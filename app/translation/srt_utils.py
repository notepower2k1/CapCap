import re


def parse_srt(srt_text: str) -> list[dict]:
    segments = []
    if not srt_text or not srt_text.strip():
        return segments

    blocks = [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines()]
        if len(lines) < 3:
            continue
        time_line = lines[1]
        if " --> " not in time_line:
            continue
        start_raw, end_raw = time_line.split(" --> ", 1)
        segments.append(
            {
                "start": _to_seconds(start_raw),
                "end": _to_seconds(end_raw),
                "text": "\n".join(lines[2:]).strip(),
            }
        )
    return segments


def to_srt(segments: list[dict]) -> str:
    lines = []
    for idx, seg in enumerate(segments, 1):
        lines.append(str(idx))
        lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
        lines.append((seg.get("text") or "").strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def clone_with_texts(segments: list[dict], texts: list[str], provider: str, polished: bool = False) -> list[dict]:
    cloned = []
    for seg, text in zip(segments, texts):
        cloned.append(
            {
                "start": seg["start"],
                "end": seg["end"],
                "text": (text or "").strip(),
                "source_text": seg.get("source_text") or seg.get("text", ""),
                "provider": provider,
                "polished": polished,
            }
        )
    return cloned


def format_timestamp(seconds: float) -> str:
    total_ms = int(round(float(seconds) * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    mins = total_minutes % 60
    hrs = total_minutes // 60
    return f"{hrs:02d}:{mins:02d}:{sec:02d},{ms:03d}"


def _to_seconds(raw: str) -> float:
    raw = raw.strip().replace(",", ".")
    parts = raw.split(":")
    if len(parts) != 3:
        return 0.0
    hrs, mins, secs = parts
    return int(hrs) * 3600 + int(mins) * 60 + float(secs)


def split_text_batches(texts: list[str], batch_size: int) -> list[list[str]]:
    return [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]


def validate_texts(texts: list[str], expected_len: int) -> bool:
    if len(texts) != expected_len:
        return False
    return all(isinstance(text, str) and text.strip() for text in texts)


def parse_numbered_lines(raw: str) -> list[str]:
    items = []
    for line in raw.splitlines():
        match = re.match(r"^\s*\d+\.\s*(.+?)\s*$", line)
        if match:
            items.append(match.group(1).strip())
    return items
