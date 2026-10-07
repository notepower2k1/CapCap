import re


_TIMESTAMP_TOKEN = r"\d{1,2}:\d{2}(?::\d{2})?[,\.]\d{1,3}"
_TIMESTAMP_PATTERN = _TIMESTAMP_TOKEN
_TIME_RANGE_RE = re.compile(rf"^\s*({_TIMESTAMP_TOKEN})\s*(?:-->|->|—>|- >)\s*({_TIMESTAMP_TOKEN})\s*$")


def parse_srt_to_segments(srt_text):
    is_valid, segments, _error = validate_srt_text(srt_text)
    if not is_valid:
        return []
    return segments


def validate_srt_text(srt_text, expected_len=None, strict_indexing: bool = False):
    segments = []
    normalized_text = _normalize_srt_text(srt_text)
    if not normalized_text:
        return False, segments, "SRT content is empty."

    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized_text) if block.strip()]
    if not blocks:
        return False, segments, "SRT content is empty."

    for block_index, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.split("\n")]
        if not lines:
            return False, [], f"Subtitle block {block_index} is incomplete."

        if _TIME_RANGE_RE.match(lines[0]):
            timestamp_line = lines[0]
            text = "\n".join(lines[1:]).strip()
        else:
            if len(lines) < 2:
                return False, [], f"Subtitle block {block_index} is incomplete."
            raw_idx = lines[0].strip()
            clean_idx = raw_idx.lstrip("#").rstrip(".")
            if not clean_idx.isdigit():
                return False, [], f"Subtitle block {block_index} is missing a numeric index (line 1: '{lines[0]}')."
            if strict_indexing:
                expected_index = str(block_index)
                if raw_idx != expected_index:
                    return False, [], f"Subtitle block {block_index} should use index {expected_index}."
            timestamp_line = lines[1]
            text = "\n".join(lines[2:]).strip()

        time_match = _TIME_RANGE_RE.match(timestamp_line)
        if not time_match:
            return False, [], f"Subtitle block {block_index} has an invalid time range: '{timestamp_line}'. Expected format: '00:00:00,000 --> 00:00:00,000'."

        try:
            start = _timestamp_to_seconds(time_match.group(1))
            end = _timestamp_to_seconds(time_match.group(2))
        except ValueError as exc:
            return False, [], f"Subtitle block {block_index} has an invalid timestamp: {exc}"

        if end < start:
            return False, [], f"Subtitle block {block_index} ends before it starts ({format_timestamp(start)} --> {format_timestamp(end)})."

        if not text:
            return False, [], f"Subtitle block {block_index} is missing subtitle text."

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


def format_segments_to_srt(segments, max_gap_ms: float = 100.0):
    lines = []
    max_gap_s = max_gap_ms / 1000.0
    for idx, seg in enumerate(segments):
        start = format_timestamp(seg["start"])
        end_s = float(seg.get("end", 0.0) or 0.0)
        if idx + 1 < len(segments):
            next_start = float(segments[idx + 1].get("start", 0.0))
            gap = next_start - end_s
            if 0 < gap <= max_gap_s:
                end_s = next_start
        end = format_timestamp(end_s)
        lines.append(f"{idx + 1}")
        lines.append(f"{start} --> {end}")
        lines.append(f"{seg['text'].strip()}\n")
    return "\n".join(lines)


def _timestamp_to_seconds(value):
    raw_value = str(value or "").strip().replace(",", ".")
    parts = raw_value.split(":")
    if len(parts) == 3:
        hrs, mins, secs = parts
    elif len(parts) == 2:
        hrs = 0
        mins, secs = parts
    else:
        raise ValueError(raw_value or "<empty>")
    try:
        return int(hrs) * 3600 + int(mins) * 60 + float(secs)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid timestamp '{raw_value}': {exc}")


def _normalize_srt_text(srt_text):
    return str(srt_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def diagnose_srt_timeline(imported_segments, base_segments, time_diff_threshold: float = 1.5) -> dict:
    base = list(base_segments or [])
    imported = list(imported_segments or [])
    base_count = len(base)
    imported_count = len(imported)
    count_match = (base_count == imported_count)
    count_diff = imported_count - base_count

    if base_count == 0 or imported_count == 0:
        return {
            "count_match": count_match,
            "base_count": base_count,
            "imported_count": imported_count,
            "count_diff": count_diff,
            "has_desync": False,
            "first_desync": None,
            "desync_cues": [],
            "summary": "No segments to compare." if not base_count and not imported_count else "One set of segments is empty.",
        }

    desync_cues = []
    first_desync = None
    common_len = min(base_count, imported_count)

    for i in range(common_len):
        b = base[i]
        imp = imported[i]
        b_start = float(b.get("start", 0.0) or 0.0)
        b_end = float(b.get("end", b_start) or b_start)
        imp_start = float(imp.get("start", 0.0) or 0.0)
        imp_end = float(imp.get("end", imp_start) or imp_start)

        diff = imp_start - b_start
        abs_diff = abs(diff)

        if abs_diff >= time_diff_threshold:
            # Diagnose reason
            reason = "timestamp_drift"
            hint = f"Thời gian bị lệch {abs_diff:.2f}s tại câu #{i + 1}."

            # Check if likely dropped cue in base (imported[i] matches base[i+1])
            if i + 1 < base_count:
                next_b_start = float(base[i + 1].get("start", 0.0) or 0.0)
                if abs(imp_start - next_b_start) <= 1.0:
                    reason = "dropped_cue"
                    hint = f"Có thể AI đã bỏ qua hoặc gộp câu #{i + 1} của bản gốc (bản dịch bị thiếu câu)."

            # Check if likely split cue (imported[i+1] matches within base[i] window)
            if reason != "dropped_cue" and i + 1 < imported_count:
                next_imp_start = float(imported[i + 1].get("start", 0.0) or 0.0)
                if next_imp_start < b_end + 0.5:
                    reason = "split_cue"
                    hint = f"Có thể AI đã tách câu #{i + 1} thành nhiều câu nhỏ (bản dịch bị thừa câu)."

            cue_info = {
                "index": i,
                "cue_number": i + 1,
                "base_start": b_start,
                "base_end": b_end,
                "base_timestamp": f"{format_timestamp(b_start)} --> {format_timestamp(b_end)}",
                "base_text": str(b.get("text", "") or "").strip(),
                "imported_start": imp_start,
                "imported_end": imp_end,
                "imported_timestamp": f"{format_timestamp(imp_start)} --> {format_timestamp(imp_end)}",
                "imported_text": str(imp.get("text", "") or "").strip(),
                "time_diff_seconds": round(diff, 3),
                "reason": reason,
                "hint": hint,
            }
            if first_desync is None:
                first_desync = cue_info
            if len(desync_cues) < 10:
                desync_cues.append(cue_info)

    if first_desync is None and not count_match:
        if imported_count < base_count:
            missing_idx = imported_count
            b = base[missing_idx]
            b_start = float(b.get("start", 0.0) or 0.0)
            b_end = float(b.get("end", b_start) or b_start)
            first_desync = {
                "index": missing_idx,
                "cue_number": missing_idx + 1,
                "base_start": b_start,
                "base_end": b_end,
                "base_timestamp": f"{format_timestamp(b_start)} --> {format_timestamp(b_end)}",
                "base_text": str(b.get("text", "") or "").strip(),
                "imported_start": 0.0,
                "imported_end": 0.0,
                "imported_timestamp": "--",
                "imported_text": "",
                "time_diff_seconds": 0.0,
                "reason": "truncated_end",
                "hint": f"File dịch bị thiếu {base_count - imported_count} câu ở phần cuối (dừng lại sau câu #{imported_count}).",
            }
            desync_cues.append(first_desync)
        else:
            extra_idx = base_count
            imp = imported[extra_idx]
            imp_start = float(imp.get("start", 0.0) or 0.0)
            imp_end = float(imp.get("end", imp_start) or imp_start)
            first_desync = {
                "index": extra_idx,
                "cue_number": extra_idx + 1,
                "base_start": 0.0,
                "base_end": 0.0,
                "base_timestamp": "--",
                "base_text": "",
                "imported_start": imp_start,
                "imported_end": imp_end,
                "imported_timestamp": f"{format_timestamp(imp_start)} --> {format_timestamp(imp_end)}",
                "imported_text": str(imp.get("text", "") or "").strip(),
                "time_diff_seconds": 0.0,
                "reason": "extra_cues_at_end",
                "hint": f"File dịch có thêm {imported_count - base_count} câu ở cuối video.",
            }
            desync_cues.append(first_desync)

    has_desync = (not count_match) or (first_desync is not None)

    if not has_desync:
        summary = f"Đồng bộ hoàn toàn ({base_count} câu)."
    elif not count_match:
        fd_text = f" Điểm lệch đầu tiên tại câu #{first_desync['cue_number']}." if first_desync else ""
        hint_text = f" Gợi ý: {first_desync['hint']}" if first_desync else ""
        summary = f"Lệch số lượng: Bản gốc {base_count} câu, file nhập {imported_count} câu ({count_diff:+d}).{fd_text}{hint_text}"
    else:
        summary = f"Số lượng khớp ({base_count} câu) nhưng lệch thời gian từ câu #{first_desync['cue_number']} ({first_desync['time_diff_seconds']:+.2f}s). {first_desync['hint']}"

    return {
        "count_match": count_match,
        "base_count": base_count,
        "imported_count": imported_count,
        "count_diff": count_diff,
        "has_desync": has_desync,
        "first_desync": first_desync,
        "desync_cues": desync_cues,
        "summary": summary,
    }
