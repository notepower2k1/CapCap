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
        metadata = dict(data.get("metadata", {}) or {})
        # Editor and diarization workflows use a flat ``speaker`` field,
        # while persisted Segment artifacts keep supplemental data in
        # metadata. Preserve it in both directions.
        speaker = str(data.get("speaker", "") or "").strip()
        if speaker and not metadata.get("speaker"):
            metadata["speaker"] = speaker
        if "words" in data and "words" not in metadata:
            metadata["words"] = list(data.get("words") or [])
        if "manual_highlights" in data and "manual_highlights" not in metadata:
            metadata["manual_highlights"] = list(data.get("manual_highlights") or [])
        if "auto_highlights" in data and "auto_highlights" not in metadata:
            metadata["auto_highlights"] = list(data.get("auto_highlights") or [])
        for key in ("tts_group_id", "tts_group_start", "tts_group_end"):
            if key in data and key not in metadata:
                metadata[key] = data.get(key)
        if "translation_provider" in data and "translation_provider" not in metadata:
            metadata["translation_provider"] = str(data.get("translation_provider") or "")
        if "provider" in data and "translation_provider" not in metadata:
            metadata["translation_provider"] = str(data.get("provider") or "")
        raw_ae = data.get("_audio_end")
        if raw_ae is not None and "_audio_end" not in metadata:
            try:
                metadata["_audio_end"] = float(raw_ae)
            except (TypeError, ValueError):
                pass
        raw_ext = data.get("extended_duration")
        if raw_ext is not None and "extended_duration" not in metadata:
            try:
                metadata["extended_duration"] = float(raw_ext)
            except (TypeError, ValueError):
                pass
        if "time_warp_id" in data and "time_warp_id" not in metadata:
            metadata["time_warp_id"] = str(data.get("time_warp_id") or "")
        if "_wav_path" in data and "_wav_path" not in metadata:
            metadata["_wav_path"] = str(data.get("_wav_path") or "")
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
            metadata=metadata,
        )

    @classmethod
    def from_transcript_dict(cls, data: dict[str, Any], segment_id: int) -> "Segment":
        text = str(data.get("text", "") or "").strip()
        metadata = {"words": list(data.get("words") or [])}
        speaker = str(data.get("speaker", "") or "").strip()
        if speaker:
            metadata["speaker"] = speaker
        raw_ext = data.get("extended_duration")
        if raw_ext is not None:
            try:
                metadata["extended_duration"] = float(raw_ext)
            except (TypeError, ValueError):
                pass
        if data.get("time_warp_id"):
            metadata["time_warp_id"] = str(data.get("time_warp_id"))
        return cls(
            id=segment_id,
            start=float(data.get("start", 0.0) or 0.0),
            end=float(data.get("end", 0.0) or 0.0),
            original_text=text,
            status="transcribed",
            metadata=metadata,
        )

    def apply_translation(self, translated_text: str, *, refined: bool = False) -> None:
        translated_text = str(translated_text or "").strip()
        self.raw_translation = translated_text
        if refined:
            self.refined_translation = translated_text
            self.final_text = translated_text
        elif not self.final_text:
            self.final_text = translated_text
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
        return self.subtitle_text

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
        payload = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.subtitle_text,
        }
        for key in ("tts_group_id", "tts_group_start", "tts_group_end"):
            if key in self.metadata:
                payload[key] = self.metadata.get(key)
        if self.metadata.get("words"):
            payload["words"] = list(self.metadata.get("words") or [])
        if self.metadata.get("manual_highlights"):
            payload["manual_highlights"] = list(self.metadata.get("manual_highlights") or [])
        if self.metadata.get("auto_highlights"):
            payload["auto_highlights"] = list(self.metadata.get("auto_highlights") or [])
        if self.metadata.get("speaker"):
            payload["speaker"] = str(self.metadata.get("speaker"))
        raw_ae = self.metadata.get("_audio_end")
        if raw_ae is not None:
            try:
                payload["_audio_end"] = float(raw_ae)
            except (TypeError, ValueError):
                pass
        raw_ext = self.metadata.get("extended_duration")
        if raw_ext is not None:
            try:
                payload["extended_duration"] = float(raw_ext)
            except (TypeError, ValueError):
                pass
        if self.metadata.get("time_warp_id"):
            payload["time_warp_id"] = str(self.metadata.get("time_warp_id"))
        if self.metadata.get("_wav_path"):
            payload["_wav_path"] = str(self.metadata.get("_wav_path"))
        if self.metadata.get("translation_provider"):
            payload["translation_provider"] = str(self.metadata.get("translation_provider"))
        if self.metadata.get("provider"):
            payload["provider"] = str(self.metadata.get("provider"))
        return payload

    def to_original_subtitle_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.original_text,
        }
        if self.metadata.get("words"):
            payload["words"] = list(self.metadata.get("words") or [])
        if self.metadata.get("speaker"):
            payload["speaker"] = str(self.metadata.get("speaker"))
        raw_ext = self.metadata.get("extended_duration")
        if raw_ext is not None:
            try:
                payload["extended_duration"] = float(raw_ext)
            except (TypeError, ValueError):
                pass
        if self.metadata.get("time_warp_id"):
            payload["time_warp_id"] = str(self.metadata.get("time_warp_id"))
        return payload


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
