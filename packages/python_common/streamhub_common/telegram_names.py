from __future__ import annotations

import re
from pathlib import PurePath


_PART_RE = re.compile(r"(?:^|_+)part_?(\d{1,9})(?:_+|\.|$)", re.IGNORECASE)
_PROXY_RANGE_RE = re.compile(r"(?:^|_+)p(\d{1,9})[-_]+p?(\d{1,9})(?:_+|\.|$)", re.IGNORECASE)
_SEG_RE = re.compile(
    r"(?:^|_+)(?:seg_?|s)(\d{1,9})[-_]+(?:seg_?|s)?(\d{1,9})(?:_+|\.|$)",
    re.IGNORECASE,
)


def _stem(name: str) -> str:
    return PurePath(str(name).strip()).name.lower()


def canonical_part_identity(name: str) -> tuple[str, int, int, int] | None:
    """Return the stable identity encoded in a modern StreamHub part filename.

    Current StreamHub names look like::

        vp_<sessionhex>__part0001__s000001-s000011.ts

    The previous StreamHub used ``part_000001__seg_...``. Telegram Desktop may
    normalize punctuation during a manual document upload, so identity matching
    deliberately ignores separators while retaining the session UUID, part number
    and segment range. Legacy names without the ``vp_<session>`` prefix continue to
    require literal filename equality.
    """

    value = _stem(name)
    prefix = "vpx_" if value.startswith("vpx_") else "vp_" if value.startswith("vp_") else None
    if prefix is None:
        return None

    part_match = _PART_RE.search(value)
    range_match = _PROXY_RANGE_RE.search(value) if prefix == "vpx_" else _SEG_RE.search(value)
    if part_match is None or range_match is None:
        return None

    session_text = value[len(prefix) : part_match.start()]
    session_hex = re.sub(r"[^0-9a-f]", "", session_text)
    if len(session_hex) != 32:
        return None

    part_no = int(part_match.group(1))
    start_segment = int(range_match.group(1))
    end_segment = int(range_match.group(2))
    if part_no <= 0 or start_segment <= 0 or end_segment < start_segment:
        return None
    return session_hex, part_no, start_segment, end_segment


def filenames_equivalent(expected_name: str, actual_name: str) -> bool:
    expected = _stem(expected_name)
    actual = _stem(actual_name)
    if expected == actual:
        return True
    expected_identity = canonical_part_identity(expected)
    return expected_identity is not None and expected_identity == canonical_part_identity(actual)


def filename_tokens(name: str) -> set[str]:
    identity = canonical_part_identity(name)
    if identity is not None:
        session_hex, part_no, start_segment, end_segment = identity
        return {
            f"session:{session_hex}",
            f"part:{part_no:06d}",
            f"seg:{start_segment:06d}-{end_segment:06d}",
        }

    value = _stem(name)
    tokens: set[str] = set()
    part = _PART_RE.search(value)
    if part:
        tokens.add(f"part:{int(part.group(1)):06d}")
    seg = _SEG_RE.search(value)
    if seg:
        tokens.add(f"seg:{int(seg.group(1)):06d}-{int(seg.group(2)):06d}")
    return tokens
