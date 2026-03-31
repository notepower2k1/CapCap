from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AudioChunk:
    chunk_id: str
    audio_path: str
    start_seconds: float
    end_seconds: float
    overlap_left_seconds: float = 0.0
    overlap_right_seconds: float = 0.0
    speech_start_seconds: float = 0.0
    speech_end_seconds: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, float(self.end_seconds) - float(self.start_seconds))

    @property
    def core_start_seconds(self) -> float:
        return min(float(self.end_seconds), float(self.start_seconds) + max(0.0, float(self.overlap_left_seconds)))

    @property
    def core_end_seconds(self) -> float:
        return max(float(self.start_seconds), float(self.end_seconds) - max(0.0, float(self.overlap_right_seconds)))

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "audio_path": self.audio_path,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "overlap_left_seconds": self.overlap_left_seconds,
            "overlap_right_seconds": self.overlap_right_seconds,
            "speech_start_seconds": self.speech_start_seconds,
            "speech_end_seconds": self.speech_end_seconds,
            "duration_seconds": self.duration_seconds,
            "core_start_seconds": self.core_start_seconds,
            "core_end_seconds": self.core_end_seconds,
        }

