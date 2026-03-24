def parse_srt_to_segments(srt_text):
    segments = []
    if not srt_text:
        return segments

    blocks = [block.strip() for block in srt_text.strip().split("\n\n") if block.strip()]
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        time_line = lines[1]
        if " --> " not in time_line:
            continue
        start_raw, end_raw = time_line.split(" --> ", 1)
        try:
            start = _timestamp_to_seconds(start_raw)
            end = _timestamp_to_seconds(end_raw)
            text = "\n".join(lines[2:])
            segments.append({"start": start, "end": end, "text": text})
        except Exception:
            continue
    return segments


def extract_subtitle_text_entries(srt_text):
    entries = []
    if not srt_text:
        return entries
    blocks = [block.strip() for block in srt_text.strip().split("\n\n") if block.strip()]
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
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) != 3:
        return 0.0
    hrs, mins, secs = parts
    return int(hrs) * 3600 + int(mins) * 60 + float(secs)
