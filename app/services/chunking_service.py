from __future__ import annotations

import os
import re
import subprocess
import wave

from core.models import AudioChunk


class ChunkingService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.ffmpeg_path = os.path.join(workspace_root, "bin", "ffmpeg", "ffmpeg.exe")

    def _subprocess_run_kwargs(self) -> dict:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
        return kwargs

    def probe_wav_duration(self, audio_path: str) -> float:
        with wave.open(audio_path, "rb") as wav_file:
            frame_rate = wav_file.getframerate() or 16000
            frame_count = wav_file.getnframes()
        return max(0.0, float(frame_count) / float(frame_rate))

    def detect_speech_regions(
        self,
        audio_path: str,
        *,
        silence_noise: str = "-35dB",
        silence_duration: float = 0.35,
        min_speech_duration: float = 0.25,
        merge_gap_seconds: float = 0.4,
    ) -> list[dict]:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio not found: {audio_path}")
        if not os.path.exists(self.ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg not found at {self.ffmpeg_path}")

        total_duration = self.probe_wav_duration(audio_path)
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-i",
            audio_path,
            "-af",
            f"silencedetect=noise={silence_noise}:d={float(silence_duration):.2f}",
            "-f",
            "null",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, **self._subprocess_run_kwargs())
        stderr = proc.stderr or ""

        silence_start_pattern = re.compile(r"silence_start:\s*([0-9.]+)")
        silence_end_pattern = re.compile(r"silence_end:\s*([0-9.]+)")
        silence_starts = [float(match.group(1)) for match in silence_start_pattern.finditer(stderr)]
        silence_ends = [float(match.group(1)) for match in silence_end_pattern.finditer(stderr)]

        silence_regions = []
        pending_start = None
        start_iter = iter(silence_starts)
        end_iter = iter(silence_ends)
        current_start = next(start_iter, None)
        current_end = next(end_iter, None)
        while current_start is not None or current_end is not None:
            if pending_start is None and current_start is not None and (current_end is None or current_start <= current_end):
                pending_start = current_start
                current_start = next(start_iter, None)
                continue
            if pending_start is not None and current_end is not None:
                silence_regions.append(
                    {
                        "start": max(0.0, pending_start),
                        "end": min(total_duration, current_end),
                    }
                )
                pending_start = None
                current_end = next(end_iter, None)
                continue
            break

        speech_regions = []
        cursor = 0.0
        for silence in silence_regions:
            silence_start = max(cursor, float(silence.get("start", 0.0)))
            if silence_start - cursor >= float(min_speech_duration):
                speech_regions.append({"start": cursor, "end": silence_start})
            cursor = max(cursor, float(silence.get("end", cursor)))
        if total_duration - cursor >= float(min_speech_duration):
            speech_regions.append({"start": cursor, "end": total_duration})

        merged_regions = []
        for region in speech_regions:
            start = float(region.get("start", 0.0))
            end = float(region.get("end", 0.0))
            if end - start < float(min_speech_duration):
                continue
            if not merged_regions:
                merged_regions.append({"start": start, "end": end})
                continue
            previous = merged_regions[-1]
            if start - float(previous["end"]) <= float(merge_gap_seconds):
                previous["end"] = max(float(previous["end"]), end)
            else:
                merged_regions.append({"start": start, "end": end})
        return merged_regions

    def extract_chunk_audio(self, source_audio_path: str, output_path: str, *, start_seconds: float, end_seconds: float) -> str:
        if not os.path.exists(source_audio_path):
            raise FileNotFoundError(f"Audio not found: {source_audio_path}")
        with wave.open(source_audio_path, "rb") as source_wav:
            frame_rate = source_wav.getframerate() or 16000
            sample_width = source_wav.getsampwidth()
            channels = source_wav.getnchannels()
            start_frame = max(0, int(float(start_seconds) * frame_rate))
            end_frame = max(start_frame, int(float(end_seconds) * frame_rate))
            source_wav.setpos(start_frame)
            frames = source_wav.readframes(max(0, end_frame - start_frame))

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with wave.open(output_path, "wb") as target_wav:
            target_wav.setnchannels(channels)
            target_wav.setsampwidth(sample_width)
            target_wav.setframerate(frame_rate)
            target_wav.writeframes(frames)
        return output_path

    def build_chunks(
        self,
        audio_path: str,
        output_dir: str,
        *,
        target_chunk_duration: float = 12.0,
        max_chunk_duration: float = 20.0,
        overlap_seconds: float = 0.5,
        silence_noise: str = "-35dB",
        silence_duration: float = 0.35,
    ) -> list[AudioChunk]:
        total_duration = self.probe_wav_duration(audio_path)
        speech_regions = self.detect_speech_regions(
            audio_path,
            silence_noise=silence_noise,
            silence_duration=silence_duration,
        )
        if not speech_regions:
            speech_regions = [{"start": 0.0, "end": total_duration}]

        os.makedirs(output_dir, exist_ok=True)
        bounded_regions = self._force_split_regions(
            speech_regions,
            target_chunk_duration=float(target_chunk_duration),
            max_chunk_duration=float(max_chunk_duration),
        )
        chunks: list[AudioChunk] = []
        for region in bounded_regions:
            chunks.append(
                self._build_chunk(
                    source_audio_path=audio_path,
                    output_dir=output_dir,
                    index=len(chunks),
                    speech_start=float(region["start"]),
                    speech_end=float(region["end"]),
                    total_duration=total_duration,
                    overlap_seconds=overlap_seconds,
                )
            )
        return chunks

    def _force_split_regions(self, speech_regions: list[dict], *, target_chunk_duration: float, max_chunk_duration: float) -> list[dict]:
        if not speech_regions:
            return []

        bounded_regions: list[dict] = []
        for region in speech_regions:
            start = float(region.get("start", 0.0))
            end = float(region.get("end", start))
            if end <= start:
                continue
            duration = end - start
            if duration <= max_chunk_duration:
                bounded_regions.append({"start": start, "end": end})
                continue

            cursor = start
            while cursor < end:
                remaining = end - cursor
                if remaining <= max_chunk_duration:
                    bounded_regions.append({"start": cursor, "end": end})
                    break

                preferred_end = min(end, cursor + target_chunk_duration)
                hard_end = min(end, cursor + max_chunk_duration)
                next_end = preferred_end
                tail_after_preferred = end - preferred_end
                if 0.0 < tail_after_preferred < 8.0 and hard_end < end:
                    next_end = hard_end
                bounded_regions.append({"start": cursor, "end": next_end})
                cursor = next_end

        return bounded_regions

    def _build_chunk(
        self,
        *,
        source_audio_path: str,
        output_dir: str,
        index: int,
        speech_start: float,
        speech_end: float,
        total_duration: float,
        overlap_seconds: float,
    ) -> AudioChunk:
        chunk_start = max(0.0, float(speech_start) - float(overlap_seconds))
        chunk_end = min(float(total_duration), float(speech_end) + float(overlap_seconds))
        overlap_left = max(0.0, float(speech_start) - chunk_start)
        overlap_right = max(0.0, chunk_end - float(speech_end))
        chunk_id = f"chunk_{index + 1:04d}"
        chunk_path = os.path.join(output_dir, f"{chunk_id}.wav")
        self.extract_chunk_audio(
            source_audio_path,
            chunk_path,
            start_seconds=chunk_start,
            end_seconds=chunk_end,
        )
        return AudioChunk(
            chunk_id=chunk_id,
            audio_path=chunk_path,
            start_seconds=chunk_start,
            end_seconds=chunk_end,
            overlap_left_seconds=overlap_left,
            overlap_right_seconds=overlap_right,
            speech_start_seconds=float(speech_start),
            speech_end_seconds=float(speech_end),
        )
