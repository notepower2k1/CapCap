from __future__ import annotations

import uuid
from typing import Any


class TimeWarpService:
    """Service to manage non-destructive video time-warps (freeze frame / slow motion / extension)
    and timeline ripple-shifting.
    """

    @staticmethod
    def create_time_warp(
        time: float,
        duration: float,
        warp_type: str = "freeze",
        segment_index: int | None = None,
        speed: float = 1.0,
        media_start: float | None = None,
        media_end: float | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        m_start = round(float(media_start if media_start is not None else time), 3)
        m_end = round(float(media_end if media_end is not None else time), 3)
        speed_val = float(speed or 1.0)
        if warp_type == "freeze":
            speed_val = 1.0
        return {
            "id": f"warp_{uuid.uuid4().hex[:8]}",
            "time": round(float(time), 3),
            "duration": round(float(duration), 3),
            "type": warp_type,
            "segment_index": segment_index,
            "speed": speed_val,
            "media_start": m_start,
            "media_end": m_end,
            "note": note,
        }

    @staticmethod
    def apply_segment_extension(
        segments: list[dict],
        segment_index: int,
        added_duration: float,
        translated_segments: list[dict] | None = None,
        warp_type: str = "freeze",
    ) -> tuple[dict, list[dict], list[dict] | None]:
        """Extend a segment by added_duration (+Δt) and ripple shift all subsequent segments by +Δt.
        warp_type can be 'freeze' (hold frame at end) or 'slow' (stretch video duration smoothly).
        Returns (new_warp, updated_segments, updated_translated_segments).
        """
        if not segments and translated_segments:
            segments = translated_segments
            translated_segments = None

        if segment_index < 0 or segment_index >= len(segments):
            raise IndexError(f"Segment index {segment_index} out of range [0, {len(segments)})")

        target_seg = segments[segment_index]
        orig_start = float(target_seg.get("start", 0.0))
        orig_end = float(target_seg.get("end", 0.0))
        delta = round(float(added_duration), 3)
        if delta <= 0:
            raise ValueError(f"Added duration must be positive, got {delta}")

        prev_ext = float(target_seg.get("extended_duration", 0.0) or 0.0)
        total_ext = round(prev_ext + delta, 3)
        prior_shift = sum(
            float(segments[i].get("extended_duration", 0.0) or 0.0)
            for i in range(segment_index)
        ) + prev_ext

        # Media coordinates on original file (accounting for prior extensions)
        media_start = round(max(0.0, orig_start - (prior_shift - prev_ext)), 3)
        warp_time = round(max(0.0, orig_end - prior_shift), 3)
        media_end = warp_time

        orig_dur = max(0.001, media_end - media_start)
        new_dur = orig_dur + total_ext
        speed = orig_dur / new_dur if warp_type == "slow" else 1.0

        warp = TimeWarpService.create_time_warp(
            time=warp_time,
            duration=total_ext,
            warp_type=warp_type,
            segment_index=segment_index,
            speed=speed,
            media_start=media_start,
            media_end=media_end,
        )

        target_seg["extended_duration"] = total_ext
        target_seg["end"] = round(float(target_seg["end"]) + delta, 3)
        target_seg["time_warp_id"] = warp["id"]
        target_seg["warp_type"] = warp_type
        target_seg["warp_speed"] = speed

        for i in range(segment_index + 1, len(segments)):
            segments[i]["start"] = round(float(segments[i]["start"]) + delta, 3)
            segments[i]["end"] = round(float(segments[i]["end"]) + delta, 3)
            if "_audio_end" in segments[i] and segments[i]["_audio_end"] is not None:
                segments[i]["_audio_end"] = round(float(segments[i]["_audio_end"]) + delta, 3)
            if isinstance(segments[i].get("metadata"), dict) and "_audio_end" in segments[i]["metadata"]:
                try:
                    segments[i]["metadata"]["_audio_end"] = round(float(segments[i]["metadata"]["_audio_end"]) + delta, 3)
                except (TypeError, ValueError):
                    pass

        if translated_segments and 0 <= segment_index < len(translated_segments):
            t_seg = translated_segments[segment_index]
            t_prev_ext = float(t_seg.get("extended_duration", 0.0) or 0.0)
            t_seg["extended_duration"] = round(t_prev_ext + delta, 3)
            t_seg["end"] = round(float(t_seg["end"]) + delta, 3)
            t_seg["time_warp_id"] = warp["id"]
            t_seg["warp_type"] = warp_type
            t_seg["warp_speed"] = speed

            for i in range(segment_index + 1, len(translated_segments)):
                translated_segments[i]["start"] = round(float(translated_segments[i]["start"]) + delta, 3)
                translated_segments[i]["end"] = round(float(translated_segments[i]["end"]) + delta, 3)
                if "_audio_end" in translated_segments[i] and translated_segments[i]["_audio_end"] is not None:
                    translated_segments[i]["_audio_end"] = round(float(translated_segments[i]["_audio_end"]) + delta, 3)
                if isinstance(translated_segments[i].get("metadata"), dict) and "_audio_end" in translated_segments[i]["metadata"]:
                    try:
                        translated_segments[i]["metadata"]["_audio_end"] = round(float(translated_segments[i]["metadata"]["_audio_end"]) + delta, 3)
                    except (TypeError, ValueError):
                        pass

        return warp, segments, translated_segments

    @staticmethod
    def remove_segment_extension(
        segments: list[dict],
        segment_index: int,
        translated_segments: list[dict] | None = None,
        warps: list[dict] | None = None,
    ) -> tuple[float, list[dict], list[dict] | None, list[dict]]:
        """Revert the extension on segment_index, subtracting extended_duration (-Δt)
        and ripple shifting all subsequent segments by -Δt.
        Returns (removed_delta, updated_segments, updated_translated_segments, updated_warps).
        """
        if not segments and translated_segments:
            segments = translated_segments
            translated_segments = None

        if segment_index < 0 or segment_index >= len(segments):
            raise IndexError(f"Segment index {segment_index} out of range [0, {len(segments)})")

        target_seg = segments[segment_index] if (segments and 0 <= segment_index < len(segments)) else {}
        delta = float(target_seg.get("extended_duration", 0.0) or 0.0)
        if delta <= 0 and translated_segments and 0 <= segment_index < len(translated_segments):
            delta = float(translated_segments[segment_index].get("extended_duration", 0.0) or 0.0)
        if delta <= 0:
            for w in (warps or []):
                if w.get("segment_index") == segment_index:
                    delta = float(w.get("duration", 0.0) or 0.0)
                    break
        if delta <= 0:
            return 0.0, segments, translated_segments, warps or []

        warp_id = target_seg.get("time_warp_id", "")
        if not warp_id and translated_segments and 0 <= segment_index < len(translated_segments):
            warp_id = translated_segments[segment_index].get("time_warp_id", "")

        if segments and 0 <= segment_index < len(segments):
            segments[segment_index]["end"] = round(float(segments[segment_index]["end"]) - delta, 3)
            segments[segment_index]["extended_duration"] = 0.0
            segments[segment_index].pop("time_warp_id", None)
            segments[segment_index].pop("warp_type", None)
            segments[segment_index].pop("warp_speed", None)

            for i in range(segment_index + 1, len(segments)):
                segments[i]["start"] = round(max(0.0, float(segments[i]["start"]) - delta), 3)
                segments[i]["end"] = round(max(0.0, float(segments[i]["end"]) - delta), 3)
                if "_audio_end" in segments[i] and segments[i]["_audio_end"] is not None:
                    segments[i]["_audio_end"] = round(max(0.0, float(segments[i]["_audio_end"]) - delta), 3)
                if isinstance(segments[i].get("metadata"), dict) and "_audio_end" in segments[i]["metadata"]:
                    try:
                        segments[i]["metadata"]["_audio_end"] = round(max(0.0, float(segments[i]["metadata"]["_audio_end"]) - delta), 3)
                    except (TypeError, ValueError):
                        pass

        if translated_segments and 0 <= segment_index < len(translated_segments):
            t_seg = translated_segments[segment_index]
            t_seg["end"] = round(float(t_seg["end"]) - delta, 3)
            t_seg["extended_duration"] = 0.0
            t_seg.pop("time_warp_id", None)
            t_seg.pop("warp_type", None)
            t_seg.pop("warp_speed", None)

            for i in range(segment_index + 1, len(translated_segments)):
                translated_segments[i]["start"] = round(max(0.0, float(translated_segments[i]["start"]) - delta), 3)
                translated_segments[i]["end"] = round(max(0.0, float(translated_segments[i]["end"]) - delta), 3)
                if "_audio_end" in translated_segments[i] and translated_segments[i]["_audio_end"] is not None:
                    translated_segments[i]["_audio_end"] = round(max(0.0, float(translated_segments[i]["_audio_end"]) - delta), 3)
                if isinstance(translated_segments[i].get("metadata"), dict) and "_audio_end" in translated_segments[i]["metadata"]:
                    try:
                        translated_segments[i]["metadata"]["_audio_end"] = round(max(0.0, float(translated_segments[i]["metadata"]["_audio_end"]) - delta), 3)
                    except (TypeError, ValueError):
                        pass

        updated_warps = [
            w for w in (warps or [])
            if (not warp_id or w.get("id") != warp_id) and w.get("segment_index") != segment_index
        ]

        return delta, segments, translated_segments, updated_warps

    @staticmethod
    def normalize_warps(warps: list[dict]) -> list[dict]:
        from app.services.time_warp_plan import normalized_warps
        return normalized_warps(warps)

    @staticmethod
    def compile_plan(warps: list[dict]):
        from app.services.time_warp_plan import compile_plan
        return compile_plan(warps)

    @staticmethod
    def build_video_parts(duration: float, warps: list[dict], on_adjustment=None):
        from app.services.time_warp_plan import video_parts
        return video_parts(duration, warps, on_adjustment)

    @staticmethod
    def speed_at_media_time(media_time: float, warps: list[dict]) -> float:
        from app.services.time_warp_plan import speed_at_media
        return speed_at_media(media_time, warps)

    @staticmethod
    def timeline_to_media_time(timeline_time: float, warps: list[dict]) -> float:
        from app.services.time_warp_plan import timeline_to_media
        return timeline_to_media(float(timeline_time), warps)

    @staticmethod
    def media_to_timeline_time(media_time: float, warps: list[dict]) -> float:
        from app.services.time_warp_plan import media_to_timeline
        return media_to_timeline(float(media_time), warps)

    @staticmethod
    def map_segments_to_media_time(segments: list[dict], warps: list[dict]) -> list[dict]:
        """Convert a list of timeline-coordinate segments to media-coordinate segments.
        Used for live player subtitle overlays when playing raw (unwarped) media.
        """
        if not warps or not segments:
            return list(segments or [])
        mapped = []
        for s in segments:
            new_s = dict(s)
            try:
                tl_start = float(s.get("start", 0.0))
                tl_end = float(s.get("end", 0.0))
                new_s["start"] = round(TimeWarpService.timeline_to_media_time(tl_start, warps), 3)
                new_s["end"] = round(TimeWarpService.timeline_to_media_time(tl_end, warps), 3)
            except (TypeError, ValueError):
                pass
            if "words" in s and isinstance(s["words"], list):
                new_words = []
                for w in s["words"]:
                    new_w = dict(w)
                    try:
                        w_start = float(w.get("start", 0.0))
                        w_end = float(w.get("end", 0.0))
                        new_w["start"] = round(TimeWarpService.timeline_to_media_time(w_start, warps), 3)
                        new_w["end"] = round(TimeWarpService.timeline_to_media_time(w_end, warps), 3)
                    except (TypeError, ValueError):
                        pass
                    new_words.append(new_w)
                new_s["words"] = new_words
            mapped.append(new_s)
        return mapped

    @staticmethod
    def _format_atempo_chain(speed: float) -> str:
        s = float(speed)
        if s <= 0:
            return "atempo=1.0"
        parts = []
        while s < 0.5:
            parts.append("atempo=0.5")
            s /= 0.5
        while s > 2.0:
            parts.append("atempo=2.0")
            s /= 2.0
        parts.append(f"atempo={s:.12g}")
        return ",".join(parts)

    @staticmethod
    def slice_time_warps_for_window(
        warps: list[dict],
        start_timeline_s: float,
        duration_s: float,
    ) -> tuple[float, float, list[dict]]:
        """Slice project time warps for a specific timeline window [T_start, T_start + duration].

        Returns:
            (media_start_s, media_duration_s, local_warps)
        where:
            - media_start_s: start time to slice from the source media file.
            - media_duration_s: duration of source media to slice.
            - local_warps: list of warps with coordinates relative to media_start_s,
              ready for build_ffmpeg_timewarp_filtergraph.
        """
        t0 = max(0.0, float(start_timeline_s))
        dur = max(0.1, float(duration_s))
        t1 = t0 + dur

        if not warps:
            return t0, dur, []

        m0 = TimeWarpService.timeline_to_media_time(t0, warps)
        m1 = TimeWarpService.timeline_to_media_time(t1, warps)

        local_warps: list[dict] = []
        for span in TimeWarpService.compile_plan(warps):
            start, end = max(t0, span.timeline_start), min(t1, span.timeline_end)
            if end <= start:
                continue
            original = dict(warps[span.indices[0]])
            if span.type == "slow":
                source_start = span.source_start + (start - span.timeline_start) * span.speed
                source_end = span.source_start + (end - span.timeline_start) * span.speed
                original.update(type="slow", media_start=max(0., source_start - m0),
                                media_end=max(0., source_end - m0), time=max(0., source_end - m0),
                                duration=(end - start) - (source_end - source_start), speed=span.speed)
            else:
                anchor = max(0., span.source_start - m0)
                original.update(type="freeze", media_start=anchor, media_end=anchor,
                                time=anchor, duration=end - start, speed=1.)
            local_warps.append(original)
        # Zero source duration means a window entirely inside a held frame.
        return m0, max(0., m1 - m0), local_warps

    @staticmethod
    def build_ffmpeg_timewarp_filtergraph(
        video_stream: str,
        warps: list[dict],
        total_media_duration: float,
        fps: float = 30.0,
        audio_stream: str | None = None,
    ) -> tuple[str, str] | tuple[str, str, str | None]:
        """Build an FFmpeg complex filter chain that slices the video at each warp point,
        freezes the frame using 'loop' or slows it down using 'setpts'/'atempo',
        and concats them back together.
        If audio_stream is provided (e.g. '0:a'), inserts matching silence pads / atempo
        so audio never desyncs.
        Returns (filter_string, output_video_pad) if audio_stream is None,
        or (filter_string, output_video_pad, output_audio_pad) if audio_stream is given.
        """
        clean_v_stream = video_stream.strip("[]")
        if not warps:
            return ("", clean_v_stream, audio_stream.strip("[]") if audio_stream else None) if audio_stream else ("", clean_v_stream)

        parts = TimeWarpService.build_video_parts(total_media_duration, warps)
        filter_parts, video_inputs, audio_inputs = [], [], []
        rate = max(1., float(fps))
        frame_window = 1. / rate
        clean_a_stream = audio_stream.strip("[]") if audio_stream else ""
        for i, (source, target, duration, speed, frozen) in enumerate(parts):
            # Quantize GLOBAL boundaries, not each duration independently.
            # Hundreds of fractional cues otherwise accumulate frame drift.
            frames = round((target + duration) * rate) - round(target * rate)
            source_end = source + (0. if frozen else duration * speed)
            frame_start = max(0., min(source, total_media_duration - frame_window))
            if frames > 0:
                video_pad = f"v_{'freeze' if frozen else 'slow' if speed != 1. else 'norm'}_{i}"
                if frozen:
                    graph = (f"[{clean_v_stream}]trim=start={frame_start:.12g},"
                             f"trim=end_frame=1,loop=loop={frames - 1}:size=1:start=0")
                else:
                    # Read at least one source frame for sub-frame intervals.
                    read_end = max(source_end, frame_start + frame_window)
                    graph = (f"[{clean_v_stream}]trim=start={frame_start:.12g}:end={read_end:.12g},"
                             f"setpts={1. / speed:.12g}*(PTS-STARTPTS),fps={rate:.12g},"
                             f"tpad=stop_mode=clone:stop_duration={duration + frame_window:.12g}")
                filter_parts.append(graph + f",trim=end_frame={frames},setpts=N/({rate:.12g}*TB)[{video_pad}]")
                video_inputs.append(f"[{video_pad}]")
            if audio_stream:
                audio_pad = f"a_{i}"
                if frozen:
                    graph = "anullsrc=r=44100:cl=stereo"
                else:
                    graph = (f"[{clean_a_stream}]atrim=start={source:.12g}:end={source_end:.12g},"
                             "asetpts=PTS-STARTPTS")
                    if speed != 1.:
                        graph += "," + TimeWarpService._format_atempo_chain(speed)
                    graph += f",apad=pad_dur={duration:.12g}"
                filter_parts.append(graph + f",atrim=duration={duration:.12g},asetpts=PTS-STARTPTS[{audio_pad}]")
                audio_inputs.append(f"[{audio_pad}]")
        if not video_inputs:
            raise ValueError("Fit Voice output is shorter than one frame")
        filter_parts.append(f"{''.join(video_inputs)}concat=n={len(video_inputs)}:v=1:a=0[v_warped]")
        if audio_stream:
            filter_parts.append(f"{''.join(audio_inputs)}concat=n={len(audio_inputs)}:v=0:a=1[a_warped]")
            return "; ".join(filter_parts), "v_warped", "a_warped"
        return "; ".join(filter_parts), "v_warped"

    # Backward-compatible alias
    build_ffmpeg_freeze_filtergraph = build_ffmpeg_timewarp_filtergraph

    @staticmethod
    def ripple_shift_timeline_layers(timeline: Any, split_time: float, delta: float) -> None:
        """Non-destructively shift all timeline layers (on visual/effect tracks like Logo, Mask, Blur, Text)
        that occur at or after split_time by delta.
        """
        if timeline is None:
            return
        tracks = getattr(timeline, "tracks", None)
        if tracks is None:
            return
        split_s = round(float(split_time), 3)
        shift_s = round(float(delta), 3)
        if abs(shift_s) < 0.001:
            return

        for track in tracks:
            # Skip TS1 / subtitle tracks because their cues are synced from self.current_segments
            track_name = str(getattr(track, "name", "") or "").strip()
            track_type = str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower()
            if track_name in ("TS1", "S1") or track_type in ("subtitle", "dub_subtitle", "video", "audio"):
                continue

            for layer in getattr(track, "layers", []):
                start = float(getattr(layer, "start", 0.0) or 0.0)
                end = float(getattr(layer, "end", 0.0) or 0.0)
                if start >= split_s - 0.005:
                    layer.start = max(0.0, round(start + shift_s, 3))
                    layer.end = max(layer.start + 0.1, round(end + shift_s, 3))
                elif start < split_s < end:
                    layer.end = max(layer.start + 0.1, round(end + shift_s, 3))
