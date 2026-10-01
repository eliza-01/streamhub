from __future__ import annotations

from pathlib import Path


def build_streamlink_command(
    *,
    media_type: str,
    source_url: str,
    quality: str,
    resume_source_offset_ms: int | None,
    stream_timeout_seconds: int,
) -> list[str]:
    command = [
        "streamlink",
        "--stdout",
        "--retry-streams",
        "5",
        "--retry-max",
        "0",
        "--retry-open",
        "10",
        "--stream-segment-attempts",
        "10",
        "--stream-segment-threads",
        "3",
        "--stream-timeout",
        str(stream_timeout_seconds),
    ]
    if media_type == "vod" and resume_source_offset_ms:
        command.extend(["--hls-start-offset", f"{resume_source_offset_ms / 1000:.3f}"])
    command.extend([source_url, quality])
    return command


def build_ffmpeg_command(
    *,
    segments_dir: Path,
    runs_dir: Path,
    run_no: int,
    start_number: int,
    segment_seconds: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-fflags",
        "+genpts",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(segment_seconds),
        "-hls_list_size",
        "0",
        "-start_number",
        str(start_number),
        "-hls_flags",
        "temp_file+program_date_time+independent_segments+discont_start",
        "-hls_segment_filename",
        str(segments_dir / "seg_%06d.ts"),
        str(runs_dir / f"run_{run_no:06d}.m3u8"),
    ]


def segment_no_from_name(name: str) -> int | None:
    value = name
    if value.endswith(".tmp"):
        value = value[:-4]
    if not value.startswith("seg_") or not value.endswith(".ts"):
        return None
    try:
        number = int(value[4:-3])
    except ValueError:
        return None
    return number if number >= 0 else None
