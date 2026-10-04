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


def to_srt(segments: list[dict], max_gap_ms: float = 100.0) -> str:
    lines = []
    max_gap_s = max_gap_ms / 1000.0
    for idx, seg in enumerate(segments, 1):
        lines.append(str(idx))
        end_time = seg['end']
        
        # Close small gaps to next segment
        if idx < len(segments):
            next_seg = segments[idx]
            gap = next_seg['start'] - end_time
            if 0 < gap <= max_gap_s:
                end_time = next_seg['start']
        
        lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(end_time)}")
        lines.append((seg.get("text") or "").strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def clone_with_texts(segments: list[dict], texts: list[str], provider: str, polished: bool = False) -> list[dict]:
    cloned = []
    for seg, text in zip(segments, texts):
        cloned.append(
            {
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
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


def _deduplicate_numbered_items(items: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Deduplicate numbered line items by ID keeping the last occurrence."""
    if not items:
        return items
    deduped = {}
    for number, text in items:
        deduped[number] = text
    n = len(items) - len(deduped)
    if n > 0:
        print(f"[Parser] Deduplicated {n} duplicate IDs in AI response")
    return sorted(deduped.items(), key=lambda x: x[0])


def parse_numbered_line_items(raw: str) -> list[tuple[int, str]]:
    """Parse numbered model output while retaining the original cue IDs."""
    # Strip Gemma chain-of-thought tags
    cleaned = re.sub(r"<thought>.*?</thought>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    # Strip everything before first numbered line (thinking prefix)
    first_num = re.search(r"^\s*\d+\.", cleaned, re.MULTILINE)
    if first_num:
        cleaned = cleaned[first_num.start():]
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

    items = []
    pattern = re.compile(r"^\s*(\d+)\.\s*(.*?)(?=^\s*\d+\.\s*|\Z)", re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(cleaned):
        body = str(match.group(2) or "").strip()
        if not body:
            continue
        body_lines = []
        for raw_line in body.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            body_lines.append(stripped)
        normalized = " ".join(body_lines).strip()
        while True:
            nested = re.match(r"^\s*\d+\.\s*(.+?)\s*$", normalized)
            if not nested:
                break
            candidate = nested.group(1).strip()
            if not candidate or candidate == normalized:
                break
            normalized = candidate
        if normalized:
            items.append((int(match.group(1)), normalized))

    items = _deduplicate_numbered_items(items)
    if items:
        return items

    fallback_items = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\s*\d+\.\s*(.+?)\s*$", line)
        if match:
            candidate = match.group(1).strip()
            while True:
                nested = re.match(r"^\s*\d+\.\s*(.+?)\s*$", candidate)
                if not nested:
                    break
                inner = nested.group(1).strip()
                if not inner or inner == candidate:
                    break
                candidate = inner
            fallback_items.append((int(match.group(1)), candidate))
    return _deduplicate_numbered_items(fallback_items)


def parse_numbered_lines(raw: str) -> list[str]:
    """Backward-compatible text-only parser for numbered model output."""
    return [text for _number, text in parse_numbered_line_items(raw)]
