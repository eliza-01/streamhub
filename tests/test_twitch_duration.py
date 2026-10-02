from __future__ import annotations

import importlib.util
from pathlib import Path


def load_duration_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "apps/twitch_adapter/app/duration.py"
    spec = importlib.util.spec_from_file_location("streamhub_twitch_duration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_twitch_duration_ms():
    duration = load_duration_module()
    assert duration.parse_twitch_duration_ms("4h17m9s") == 15_429_000
    assert duration.parse_twitch_duration_ms("58m2s") == 3_482_000
    assert duration.parse_twitch_duration_ms("41s") == 41_000
    assert duration.parse_twitch_duration_ms(None) is None
    assert duration.parse_twitch_duration_ms("unknown") is None
