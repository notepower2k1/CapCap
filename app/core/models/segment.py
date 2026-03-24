from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Segment:
    id: int
    start: float
    end: float
    original_text: str = ""
    raw_translation: str = ""
    refined_translation: str = ""
    final_text: str = ""
    tts_text: str = ""
    voice_file: str = ""
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_id: int = 0) -> "Segment":
        segment_id = data.get("id", default_id)
        original_text = str(data.get("original_text", data.get("text", "")) or "")
        raw_translation = str(data.get("raw_translation", "") or "")
        refined_translation = str(data.get("refined_translation", "") or "")
        final_text = str(data.get("final_text", data.get("text", "")) or "")
        tts_text = str(data.get("tts_text", "") or "")
        return cls(
            id=int(segment_id or 0),
            start=float(data.get("start", 0.0) or 0.0),
            end=float(data.get("end", 0.0) or 0.0),
            original_text=original_text,
            raw_translation=raw_translation,
            refined_translation=refined_translation,
            final_text=final_text,
            tts_text=tts_text,
            voice_file=str(data.get("voice_file", "") or ""),
            status=str(data.get("status", "pending") or "pending"),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    @classmethod
    def from_transcript_dict(cls, data: dict[str, Any], segment_id: int) -> "Segment":
        text = str(data.get("text", "") or "").strip()
        return cls(
            id=segment_id,
            start=float(data.get("start", 0.0) or 0.0),
            end=float(data.get("end", 0.0) or 0.0),
            original_text=text,
            status="transcribed",
        )

    def apply_translation(self, translated_text: str, *, refined: bool = False) -> None:
        translated_text = str(translated_text or "").strip()
        self.raw_translation = translated_text
        if refined:
            self.refined_translation = translated_text
            self.final_text = translated_text
        elif not self.final_text:
            self.final_text = translated_text
        if not self.tts_text:
            self.tts_text = self.final_text or translated_text
        self.status = "translated"

    @property
    def subtitle_text(self) -> str:
        return (
            self.final_text
            or self.refined_translation
            or self.raw_translation
            or self.original_text
        )

    @property
    def tts_source_text(self) -> str:
        return self.tts_text or self.subtitle_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "original_text": self.original_text,
            "raw_translation": self.raw_translation,
            "refined_translation": self.refined_translation,
            "final_text": self.final_text,
            "tts_text": self.tts_text,
            "voice_file": self.voice_file,
            "status": self.status,
            "metadata": self.metadata,
        }

    def to_subtitle_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.subtitle_text,
        }

    def to_original_subtitle_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.original_text,
        }


def coerce_segments(segments: list[Any]) -> list[Segment]:
    normalized: list[Segment] = []
    for idx, segment in enumerate(segments, start=1):
        if isinstance(segment, Segment):
            normalized.append(segment)
        elif isinstance(segment, dict):
            normalized.append(Segment.from_dict(segment, default_id=idx))
        else:
            raise TypeError(f"Unsupported segment type: {type(segment)!r}")
    return normalized


def segments_to_dicts(segments: list[Any]) -> list[dict[str, Any]]:
    return [segment.to_dict() for segment in coerce_segments(segments)]

