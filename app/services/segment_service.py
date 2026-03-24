from __future__ import annotations

from core.models import Segment


class SegmentService:
    def transcript_dicts_to_models(self, raw_segments) -> list[Segment]:
        return [
            Segment.from_transcript_dict(raw_segment, segment_id=index)
            for index, raw_segment in enumerate(raw_segments or [], start=1)
        ]

    def segment_dicts_to_models(self, segments, *, translated: bool = False) -> list[Segment]:
        models: list[Segment] = []
        for idx, seg in enumerate(segments or [], start=1):
            model = Segment.from_dict(seg, default_id=idx)
            if translated:
                translated_text = seg.get("text", "")
                model.apply_translation(translated_text, refined=bool(seg.get("polished")))
            elif not model.original_text:
                model.original_text = str(seg.get("text", "") or "")
                model.status = "transcribed"
            models.append(model)
        return models

    def apply_translations(self, base_models, translated_segments) -> list[Segment]:
        models: list[Segment] = []
        base_models = base_models or []
        for idx, seg in enumerate(translated_segments or [], start=1):
            if idx - 1 < len(base_models):
                model = Segment.from_dict(base_models[idx - 1].to_dict(), default_id=idx)
            else:
                model = Segment.from_dict(seg, default_id=idx)
            translated_text = seg.get("text", "")
            model.apply_translation(translated_text, refined=bool(seg.get("polished")))
            model.metadata["translation_provider"] = seg.get("provider", "")
            model.metadata["source_text"] = seg.get("source_text", "")
            models.append(model)
        return models
