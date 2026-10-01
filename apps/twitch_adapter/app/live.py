from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from streamhub_common.contracts import ChatEventEnvelope, ChatMessageEnvelope


def parse_twitch_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # EventSub timestamps may contain nanoseconds while Python's datetime is
    # microsecond based. Truncate only the fractional part, preserving offset.
    if "." in text:
        head, tail = text.split(".", 1)
        offset_at = max(tail.find("+"), tail.find("-"))
        if offset_at >= 0:
            fraction, offset = tail[:offset_at], tail[offset_at:]
        else:
            fraction, offset = tail, ""
        text = f"{head}.{fraction[:6]}{offset}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def timeline_offset_ms(created_at: datetime | None, origin: datetime) -> int:
    if created_at is None:
        created_at = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=UTC)
    return max(0, int((created_at - origin).total_seconds() * 1000))


def eventsub_chat_message(frame: dict[str, Any], origin: datetime) -> ChatMessageEnvelope | None:
    payload = frame.get("payload") or {}
    event = payload.get("event") or {}
    metadata = frame.get("metadata") or {}
    if not isinstance(event, dict):
        return None

    message = event.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    created_at = parse_twitch_timestamp(metadata.get("message_timestamp"))
    offset = timeline_offset_ms(created_at, origin)
    cheer = event.get("cheer") or {}
    bits = cheer.get("bits") if isinstance(cheer, dict) else None
    try:
        bits = int(bits) if bits is not None else None
    except (TypeError, ValueError):
        bits = None

    source_id = event.get("source_broadcaster_user_id") or event.get("broadcaster_user_id")
    source_login = event.get("source_broadcaster_user_login") or event.get("broadcaster_user_login")
    source_name = event.get("source_broadcaster_user_name") or event.get("broadcaster_user_name")
    message_type = str(event.get("message_type") or "message")

    return ChatMessageEnvelope(
        provider_message_id=str(event.get("message_id")) if event.get("message_id") else None,
        provider_event_id=str(metadata.get("message_id")) if metadata.get("message_id") else None,
        source_kind="eventsub",
        source_created_at_utc=created_at,
        media_offset_ms=offset,
        timeline_offset_ms=offset,
        chatter_external_id=str(event.get("chatter_user_id")) if event.get("chatter_user_id") else None,
        chatter_login=event.get("chatter_user_login"),
        chatter_name=event.get("chatter_user_name"),
        color=event.get("color"),
        badges_json=event.get("badges") or [],
        message_text=str(message.get("text") or ""),
        fragments_json=message.get("fragments") or [],
        reply_json=event.get("reply") if isinstance(event.get("reply"), dict) else None,
        bits=bits,
        is_action=message_type == "action",
        raw_payload_json=frame,
        message_type=message_type,
        channel_points_reward_id=event.get("channel_points_custom_reward_id"),
        source_broadcaster_external_id=str(source_id) if source_id else None,
        source_broadcaster_login=source_login,
        source_broadcaster_name=source_name,
    )


def eventsub_chat_delete(frame: dict[str, Any], origin: datetime) -> ChatEventEnvelope | None:
    payload = frame.get("payload") or {}
    event = payload.get("event") or {}
    metadata = frame.get("metadata") or {}
    if not isinstance(event, dict) or not event.get("message_id"):
        return None
    created_at = parse_twitch_timestamp(metadata.get("message_timestamp"))
    return ChatEventEnvelope(
        event_type="message_delete",
        timeline_offset_ms=timeline_offset_ms(created_at, origin),
        provider_event_id=str(metadata.get("message_id")) if metadata.get("message_id") else None,
        source_kind="eventsub",
        payload_json={
            "provider_message_id": str(event["message_id"]),
            "target_user_id": event.get("target_user_id"),
            "target_user_login": event.get("target_user_login"),
            "target_user_name": event.get("target_user_name"),
            "raw": frame,
        },
    )


def _unescape_irc_tag(value: str) -> str:
    result: list[str] = []
    i = 0
    replacements = {"s": " ", ":": ";", "r": "\r", "n": "\n", "\\": "\\"}
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            result.append(replacements.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            result.append(value[i])
            i += 1
    return "".join(result)


def parse_irc_line(line: str) -> dict[str, Any]:
    rest = line.rstrip("\r\n")
    tags: dict[str, str] = {}
    prefix: str | None = None

    if rest.startswith("@"):
        tag_blob, rest = rest[1:].split(" ", 1)
        for item in tag_blob.split(";"):
            key, sep, value = item.partition("=")
            tags[key] = _unescape_irc_tag(value) if sep else ""

    if rest.startswith(":"):
        prefix_blob, rest = rest[1:].split(" ", 1)
        prefix = prefix_blob

    trailing: str | None = None
    if " :" in rest:
        head, trailing = rest.split(" :", 1)
    else:
        head = rest
    parts = head.split()
    command = parts[0] if parts else ""
    params = parts[1:]
    return {
        "tags": tags,
        "prefix": prefix,
        "command": command,
        "params": params,
        "trailing": trailing,
        "raw": line.rstrip("\r\n"),
    }


def _irc_timestamp(tags: dict[str, str]) -> datetime | None:
    raw = tags.get("tmi-sent-ts")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _irc_badges(tags: dict[str, str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for badge in filter(None, tags.get("badges", "").split(",")):
        set_id, sep, badge_id = badge.partition("/")
        result.append({"set_id": set_id, "id": badge_id if sep else "", "info": ""})
    return result


def irc_chat_message(
    line: str,
    origin: datetime,
    broadcaster_id: str,
    broadcaster_login: str,
    broadcaster_name: str | None,
) -> ChatMessageEnvelope | None:
    parsed = parse_irc_line(line)
    if parsed["command"] != "PRIVMSG":
        return None
    tags: dict[str, str] = parsed["tags"]
    text = str(parsed.get("trailing") or "")
    is_action = text.startswith("\x01ACTION ") and text.endswith("\x01")
    if is_action:
        text = text[len("\x01ACTION ") : -1]
    created_at = _irc_timestamp(tags)
    offset = timeline_offset_ms(created_at, origin)
    prefix = str(parsed.get("prefix") or "")
    prefix_login = prefix.split("!", 1)[0] if "!" in prefix else None
    reply = {key: value for key, value in tags.items() if key.startswith("reply-") and value}

    bits: int | None = None
    if tags.get("bits"):
        try:
            bits = int(tags["bits"])
        except ValueError:
            bits = None

    source_room_id = tags.get("source-room-id")
    source_is_target = not source_room_id or source_room_id == broadcaster_id
    return ChatMessageEnvelope(
        provider_message_id=tags.get("id") or None,
        source_kind="irc",
        source_created_at_utc=created_at,
        media_offset_ms=offset,
        timeline_offset_ms=offset,
        chatter_external_id=tags.get("user-id") or None,
        chatter_login=prefix_login,
        chatter_name=tags.get("display-name") or prefix_login,
        color=tags.get("color") or None,
        badges_json=_irc_badges(tags),
        message_text=text,
        fragments_json=[{"type": "text", "text": text}],
        reply_json=reply or None,
        bits=bits,
        is_action=is_action,
        raw_payload_json={"raw": parsed["raw"], "tags": tags},
        message_type="action" if is_action else "message",
        channel_points_reward_id=tags.get("custom-reward-id") or None,
        source_broadcaster_external_id=source_room_id or broadcaster_id,
        source_broadcaster_login=broadcaster_login if source_is_target else None,
        source_broadcaster_name=broadcaster_name if source_is_target else None,
    )


def irc_chat_event(line: str, origin: datetime) -> ChatEventEnvelope | None:
    parsed = parse_irc_line(line)
    command = parsed["command"]
    tags: dict[str, str] = parsed["tags"]
    created_at = _irc_timestamp(tags)
    offset = timeline_offset_ms(created_at, origin)

    if command == "CLEARMSG" and tags.get("target-msg-id"):
        return ChatEventEnvelope(
            event_type="message_delete",
            timeline_offset_ms=offset,
            source_kind="irc",
            payload_json={
                "provider_message_id": tags["target-msg-id"],
                "target_user_login": tags.get("login"),
                "raw": parsed["raw"],
            },
        )
    if command == "CLEARCHAT":
        return ChatEventEnvelope(
            event_type="user_messages_clear" if parsed.get("trailing") else "chat_clear",
            timeline_offset_ms=offset,
            source_kind="irc",
            payload_json={
                "target_user_id": tags.get("target-user-id"),
                "target_user_login": parsed.get("trailing"),
                "ban_duration": tags.get("ban-duration"),
                "raw": parsed["raw"],
            },
        )
    return None
