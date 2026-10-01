from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path


def load_live_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "apps/twitch_adapter/app/live.py"
    spec = importlib.util.spec_from_file_location("streamhub_twitch_live", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eventsub_message_is_normalized_with_live_offset():
    live = load_live_module()
    origin = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
    frame = {
        "metadata": {
            "message_id": "evt-1",
            "message_type": "notification",
            "message_timestamp": "2026-10-01T02:00:05.250000000Z",
            "subscription_type": "channel.chat.message",
        },
        "payload": {
            "event": {
                "broadcaster_user_id": "10",
                "broadcaster_user_login": "channel",
                "broadcaster_user_name": "Channel",
                "chatter_user_id": "20",
                "chatter_user_login": "viewer",
                "chatter_user_name": "Viewer",
                "message_id": "msg-1",
                "message": {"text": "hello", "fragments": [{"type": "text", "text": "hello"}]},
                "badges": [],
                "color": "#112233",
                "message_type": "text",
            }
        },
    }
    message = live.eventsub_chat_message(frame, origin)
    assert message is not None
    assert message.provider_message_id == "msg-1"
    assert message.provider_event_id == "evt-1"
    assert message.source_kind == "eventsub"
    assert message.timeline_offset_ms == 5250
    assert message.message_text == "hello"


def test_irc_message_uses_same_provider_id_for_cross_source_dedup():
    live = load_live_module()
    origin = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
    line = (
        "@badges=subscriber/1;color=#112233;display-name=Viewer;id=msg-1;"
        "tmi-sent-ts=1790820005250;user-id=20 "
        ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #channel :hello"
    )
    message = live.irc_chat_message(line, origin, "10", "channel", "Channel")
    assert message is not None
    assert message.provider_message_id == "msg-1"
    assert message.source_kind == "irc"
    assert message.message_text == "hello"
    assert message.chatter_external_id == "20"


def test_irc_clearmsg_becomes_message_delete_event():
    live = load_live_module()
    origin = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
    line = (
        "@login=viewer;target-msg-id=msg-1;tmi-sent-ts=1790820005250 "
        ":tmi.twitch.tv CLEARMSG #channel :hello"
    )
    event = live.irc_chat_event(line, origin)
    assert event is not None
    assert event.event_type == "message_delete"
    assert event.payload_json["provider_message_id"] == "msg-1"
