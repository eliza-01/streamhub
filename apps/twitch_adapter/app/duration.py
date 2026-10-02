from __future__ import annotations

import re


def parse_twitch_duration_ms(value: str | None) -> int | None:
    """Convert Twitch Helix duration strings such as ``3h12m7s`` to milliseconds."""
    if not value:
        return None
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000
