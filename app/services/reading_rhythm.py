"""Plan video extensions around the measured duration of generated speech."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

from .time_warp_service import TimeWarpService


READING_RHYTHM = "Reading Rhythm Priority"
MIN_VIDEO_SPEED = 0.75
MAX_VIDEO_EXTENSION = 0.10
MIN_OVERFLOW = 0.05


def prefers_reading_rhythm(mode):
    return str(mode or "").strip().lower() == READING_RHYTHM.lower()


@dataclass
class ReadingRhythmFit:
    subtitles: list
    voice_segments: list
    warps: list
    shifts: list
    unresolved: int

    @property
    def added_seconds(self):
        return sum(delta for _, delta in self.shifts)

    def rebuild_voice(self, path):
        if not self.shifts:
            return path
        from app.audio_mixer import build_voice_track_from_srt_segments

        paths = [voice.get("_wav_path", "") for voice in self.voice_segments]
        if not any(paths) or any(path and not Path(path).is_file() for path in paths):
            raise RuntimeError("Could not find the generated voice clips to extend the video.")
        source = Path(path)
        output = source.with_name(f"{source.stem}_reading_rhythm.wav")
        return build_voice_track_from_srt_segments(
            segments=self.voice_segments,
            tts_wav_paths=paths,
            output_wav_path=str(output),
        )


def fit_reading_rhythm(subtitles, voices, source_duration, warps, *, auto_extend=False):
    """Use free space first, then optionally slow down part of the source video."""
    subtitles = [dict(row) for row in subtitles]
    voices = [dict(row) for row in voices]
    warps = list(warps or [])
    plan = TimeWarpService.compile_plan(warps)
    warp_ends = [span.source_end for span in plan]
    existing_duration = sum(span.duration for span in plan)
    timeline_end = source_duration + existing_duration
    budget = max(0.0, source_duration * MAX_VIDEO_EXTENSION - existing_duration) if auto_extend else 0.0
    shifts = []
    total_added = 0.0
    unresolved = 0
    first = 0
    for index, voice in enumerate(voices):
        if first >= len(subtitles):
            break
        last = first
        group = voice.get("tts_group_id")
        while group and last + 1 < len(subtitles) and subtitles[last + 1].get("tts_group_id") == group:
            last += 1
        start = float(voice["start"])
        audio_end = float(voice.get("_audio_end") or start)
        end = float(subtitles[last]["end"])
        next_start = float(voices[index + 1]["start"]) if index + 1 < len(voices) else max(end, timeline_end)
        free_end = max(end, next_start)
        shortage = audio_end - next_start if audio_end > start else 0.0
        delta = 0.0
        if budget > 0 and shortage > MIN_OVERFLOW and next_start >= end:
            media_start = TimeWarpService.timeline_to_media_time(float(subtitles[last]["start"]), plan)
            media_end = TimeWarpService.timeline_to_media_time(end, plan)
            first_warp = bisect_right(warp_ends, media_start)
            overlaps_warp = first_warp < len(plan) and plan[first_warp].source_start < media_end
            if not overlaps_warp and media_start < media_end <= source_duration and not subtitles[last].get("extended_duration"):
                source_span = media_end - media_start
                extra = min(shortage, budget, source_span * (1 / MIN_VIDEO_SPEED - 1))
                delta = int((extra + 1e-9) * 1000) / 1000
                if delta >= MIN_OVERFLOW:
                    warp = TimeWarpService.create_time_warp(
                        media_end, delta, "slow", last, source_span / (source_span + delta),
                        media_start, media_end, note=READING_RHYTHM,
                    )
                    warps.append(warp)
                    shifts.append((end + total_added, delta))
                    budget -= delta
                    subtitles[last].update(extended_duration=delta, time_warp_id=warp["id"],
                                           warp_type="slow", warp_speed=warp["speed"])
                else:
                    delta = 0.0

        # Keep the subtitle visible through available silence, without covering the next line.
        covered_end = max(end + delta, min(audio_end, free_end + delta))
        for row in subtitles[first:last + 1]:
            _offset_row(row, total_added)
        last_row = subtitles[last]
        last_row["end"] = round(covered_end + total_added, 3)
        if "tts_group_end" in last_row:
            last_row["tts_group_end"] = last_row["end"]
        if audio_end > start:
            last_row["_audio_end"] = round(audio_end + total_added, 3)
            for row in subtitles[first:last]:
                row["_audio_end"] = min(last_row["_audio_end"], float(row["end"]))
        voice["start"] = round(start + total_added, 3)
        voice["end"] = last_row["end"]
        if audio_end > start:
            voice["_audio_end"] = last_row["_audio_end"]
        if shortage - delta > MIN_OVERFLOW:
            unresolved += 1
        total_added += delta
        first = last + 1

    for row in subtitles[first:]:
        _offset_row(row, total_added)
    return ReadingRhythmFit(subtitles, voices, warps, shifts, unresolved)


def _offset_row(row, delta):
    if not delta:
        return
    for key in ("start", "end", "tts_group_start", "tts_group_end", "_audio_end", "_original_end"):
        if row.get(key) is not None:
            row[key] = round(float(row[key]) + delta, 3)
    if row.get("words"):
        row["words"] = [dict(word) for word in row["words"]]
        for word in row["words"]:
            _offset_row(word, delta)


def shift_source_subtitles(rows, fit):
    """Move original subtitle timestamps after the same extension points."""
    if len(rows) == len(fit.subtitles):
        shift = 0.0
        added_warps = fit.warps[-len(fit.shifts):] if fit.shifts else []
        by_index = {warp["segment_index"]: warp for warp in added_warps}
        for index, row in enumerate(rows):
            _offset_row(row, shift)
            warp = by_index.get(index)
            if warp is not None:
                delta = warp["duration"]
                row["end"] = round(float(row["end"]) + delta, 3)
                row.update(extended_duration=delta, time_warp_id=warp["id"],
                           warp_type="slow", warp_speed=warp["speed"])
                shift += delta
        return
    points = []
    offsets = [0.0]
    for point, delta in fit.shifts:
        points.append(round(point - offsets[-1], 3))
        offsets.append(offsets[-1] + delta)
    for row in rows:
        for item in (row, *(row.get("words") or ())):
            item["start"] = round(float(item["start"]) + offsets[bisect_right(points, float(item["start"]))], 3)
            item["end"] = round(float(item["end"]) + offsets[bisect_left(points, float(item["end"]))], 3)
