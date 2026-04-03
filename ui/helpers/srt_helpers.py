import re


_TIMESTAMP_PATTERN = r"\d{2}:\d{2}:\d{2},\d{3}"
_TIME_RANGE_RE = re.compile(rf"^\s*({_TIMESTAMP_PATTERN})\s*-->\s*({_TIMESTAMP_PATTERN})\s*$")


def parse_srt_to_segments(srt_text):
    is_valid, segments, _error = validate_srt_text(srt_text)
    if not is_valid:
        return []
    return segments


def validate_srt_text(srt_text, expected_len=None):
    segments = []
    normalized_text = _normalize_srt_text(srt_text)
    if not normalized_text:
        return False, segments, "SRT content is empty."

    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized_text) if block.strip()]
    if not blocks:
        return False, segments, "SRT content is empty."

    for block_index, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.split("\n")]
        if len(lines) < 3:
            return False, [], f"Subtitle block {block_index} is incomplete."
        if not lines[0].strip().isdigit():
            return False, [], f"Subtitle block {block_index} is missing a numeric index."

        time_match = _TIME_RANGE_RE.match(lines[1])
        if not time_match:
            return False, [], f"Subtitle block {block_index} has an invalid time range."

        try:
            start = _timestamp_to_seconds(time_match.group(1))
            end = _timestamp_to_seconds(time_match.group(2))
        except ValueError as exc:
            return False, [], f"Subtitle block {block_index} has an invalid timestamp: {exc}"

        if end < start:
            return False, [], f"Subtitle block {block_index} ends before it starts."

        text = "\n".join(lines[2:]).strip()
        if not text:
            return False, [], f"Subtitle block {block_index} is missing subtitle text."

        expected_index = str(block_index)
        if lines[0].strip() != expected_index:
            return False, [], f"Subtitle block {block_index} should use index {expected_index}."

        segments.append({"start": start, "end": end, "text": text})

    if expected_len is not None and len(segments) != int(expected_len):
        return False, [], f"SRT segment count mismatch. Expected {int(expected_len)}, got {len(segments)}."

    return True, segments, ""


def extract_subtitle_text_entries(srt_text):
    entries = []
    normalized_text = _normalize_srt_text(srt_text)
    if not normalized_text:
        return entries
    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized_text) if block.strip()]
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines()]
        if not lines:
            continue
        if len(lines) >= 3 and " --> " in lines[1]:
            text = "\n".join(lines[2:]).strip()
        elif len(lines) >= 2 and lines[0].strip().isdigit():
            text = "\n".join(lines[1:]).strip()
        else:
            text = "\n".join(lines).strip()
        entries.append(text)
    return entries


def format_timestamp(seconds):
    total_ms = int(seconds * 1000)
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hrs = total_minutes // 60
    return f"{hrs:02d}:{mins:02d}:{sec:02d},{ms:03d}"


def format_segments_to_srt(segments):
    lines = []
    for idx, seg in enumerate(segments):
        start = format_timestamp(seg["start"])
        end = format_timestamp(seg["end"])
        lines.append(f"{idx + 1}")
        lines.append(f"{start} --> {end}")
        lines.append(f"{seg['text'].strip()}\n")
    return "\n".join(lines)


def _timestamp_to_seconds(value):
    raw_value = str(value or "").strip()
    if not re.fullmatch(_TIMESTAMP_PATTERN, raw_value):
        raise ValueError(raw_value or "<empty>")
    value = raw_value.replace(",", ".")
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(raw_value)
    hrs, mins, secs = parts
    return int(hrs) * 3600 + int(mins) * 60 + float(secs)


def _normalize_srt_text(srt_text):
    return str(srt_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
