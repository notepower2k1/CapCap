"""Pure cue grouping and TTS result merging shared by GUI and queue."""

def resolve_voice_text(segment: dict) -> str:
    current = dict(segment or {})
    subtitle_text = " ".join(str(current.get("text", "") or "").split()).strip()
    if bool(current.get("voice_edited")):
        edited_text = " ".join(str(current.get("tts_text") or current.get("dubbing_vi") or "").split()).strip()
        if edited_text:
            return edited_text
    return subtitle_text


def group_voice_segments(segments):
    source_segments = list(segments or [])
    grouped_segments = []
    idx = 0
    while idx < len(source_segments):
        segment = dict(source_segments[idx])
        group_id = str(segment.get('tts_group_id', '') or '').strip()
        tts_text = resolve_voice_text(segment)
        if not group_id:
            segment['text'] = tts_text
            segment['tts_text'] = str(segment.get('tts_text') or '').strip() if bool(segment.get('voice_edited')) else ''
            grouped_segments.append(segment)
            idx += 1
            continue

        group_items = [segment]
        cursor = idx + 1
        while cursor < len(source_segments):
            candidate = source_segments[cursor]
            if str(candidate.get('tts_group_id', '') or '').strip() != group_id:
                break
            group_items.append(dict(candidate))
            cursor += 1

        voice_text = ""
        voice_edited = False
        for item in group_items:
            if bool(item.get('voice_edited')):
                candidate_text = " ".join(str(item.get('tts_text') or item.get('dubbing_vi') or '').split()).strip()
                if candidate_text:
                    voice_text = candidate_text
                    voice_edited = True
                    break
        if not voice_text:
            voice_text = ' '.join(
                ' '.join(str(item.get('text') or '').split()).strip()
                for item in group_items
            ).strip()

        grouped_segments.append({
            'start': float(group_items[0].get('tts_group_start', group_items[0].get('start', 0.0)) or group_items[0].get('start', 0.0)),
            'end': float(group_items[-1].get('tts_group_end', group_items[-1].get('end', 0.0)) or group_items[-1].get('end', 0.0)),
            'text': voice_text,
            'tts_text': voice_text if voice_edited else '',
            'tts_group_id': group_id,
            'voice_edited': voice_edited,
            'source_text': ' '.join(
                ' '.join(str(item.get('source_text') or item.get('text') or '').split()).strip()
                for item in group_items
            ).strip(),
            'speaker': str(group_items[0].get('speaker', '') or ''),
            'voice_speed': group_items[0].get('voice_speed', 1.0),
            '_auto_fit_applied': bool(group_items[0].get('_auto_fit_applied', False)),
        })
        idx = cursor
    return grouped_segments


def merge_generated_voice_segments(source_segments, voice_segments):
    if not source_segments or not voice_segments:
        return False

    updated = False
    grouped_updates = {}
    positional_updates = []
    for seg in list(voice_segments or []):
        tts_text = ' '.join(str((seg or {}).get("tts_text") or (seg or {}).get("text") or "").split()).strip()
        subtitle_vi = ' '.join(str((seg or {}).get("subtitle_vi") or (seg or {}).get("text") or "").split()).strip()
        dubbing_vi = ' '.join(str((seg or {}).get("dubbing_vi") or tts_text).split()).strip()
        action_taken = str((seg or {}).get("action_taken") or "").strip().lower()
        ratio = float((seg or {}).get("ratio") or 0.0)
        group_id = str((seg or {}).get("tts_group_id") or "").strip()
        try:
            new_start = float((seg or {}).get("start", 0.0))
            new_end = float((seg or {}).get("end", 0.0))
        except (TypeError, ValueError):
            new_start = new_end = None
        try:
            new_original_end = float((seg or {}).get("_original_end")) if (seg or {}).get("_original_end") is not None else None
        except (TypeError, ValueError):
            new_original_end = None
        try:
            new_audio_end = float((seg or {}).get("_audio_end")) if (seg or {}).get("_audio_end") is not None else None
        except (TypeError, ValueError):
            new_audio_end = None
        try:
            seg_speed = float((seg or {}).get("voice_speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            seg_speed = 1.0
        payload = {
            "tts_text": tts_text,
            "subtitle_vi": subtitle_vi,
            "dubbing_vi": dubbing_vi,
            "action_taken": action_taken,
            "ratio": ratio,
            "attempt_count": int((seg or {}).get("attempt_count") or 1),
            "voice_speed": seg_speed,
            "_auto_fit_applied": bool((seg or {}).get("_auto_fit_applied", False)),
            "start": new_start,
            "end": new_end,
            "_original_end": new_original_end,
            "_audio_end": new_audio_end,
            "_wav_path": str((seg or {}).get("_wav_path") or ""),
        }
        if group_id:
            grouped_updates[group_id] = payload
        else:
            positional_updates.append(payload)

    positional_index = 0
    for seg in source_segments:
        group_id = str((seg or {}).get("tts_group_id") or "").strip()
        if group_id and group_id in grouped_updates:
            next_payload = grouped_updates[group_id]
        elif positional_index < len(positional_updates):
            next_payload = positional_updates[positional_index]
            positional_index += 1
        else:
            continue

        next_tts_text = next_payload["tts_text"]
        current_tts_text = ' '.join(str(seg.get("tts_text") or "").split()).strip()
        if current_tts_text != next_tts_text:
            seg["tts_text"] = next_tts_text
            updated = True
        seg["subtitle_vi"] = next_payload["subtitle_vi"]
        seg["dubbing_vi"] = next_payload["dubbing_vi"]
        seg["action_taken"] = next_payload["action_taken"]
        seg["ratio"] = next_payload["ratio"]
        seg["attempt_count"] = next_payload["attempt_count"]
        seg["_tts_dirty"] = False
        if next_payload.get("_wav_path"):
            seg["_wav_path"] = next_payload["_wav_path"]
        try:
            new_speed = float(next_payload.get("voice_speed") or 1.0)
        except (TypeError, ValueError):
            new_speed = 1.0
        try:
            old_speed = float(seg.get("voice_speed") or 1.0)
        except (TypeError, ValueError):
            old_speed = 1.0
        if abs(new_speed - old_speed) >= 0.0005:
            seg["voice_speed"] = round(new_speed, 3)
            updated = True
        auto_fit = bool(next_payload.get("_auto_fit_applied", False))
        if bool(seg.get("_auto_fit_applied", False)) != auto_fit:
            updated = True
        seg["_auto_fit_applied"] = auto_fit
        # When group_id is present, multiple sub-segments share the same group_id.
        # Do NOT overwrite their individual start/end times with the entire group's start/end,
        # which would collapse them onto the same time slice!
        if not group_id:
            new_start = next_payload.get("start")
            new_end = next_payload.get("end")
            if new_start is not None and new_end is not None and new_end > new_start:
                try:
                    old_start = float(seg.get("start", 0.0))
                    old_end = float(seg.get("end", 0.0))
                except (TypeError, ValueError):
                    old_start = old_end = None
                if old_start is not None and old_end is not None:
                    if abs(new_start - old_start) > 0.01 or abs(new_end - old_end) > 0.01:
                        seg["start"] = new_start
                        seg["end"] = new_end
                        updated = True
        new_original_end = next_payload.get("_original_end")
        if new_original_end is not None:
            seg["_original_end"] = new_original_end
        new_audio_end = next_payload.get("_audio_end")
        if new_audio_end is not None:
            seg["_audio_end"] = new_audio_end
        else:
            seg.pop("_audio_end", None)
    return updated
