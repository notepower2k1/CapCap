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
                if "words" in seg:
                    model.metadata["words"] = list(seg.get("words") or [])
                if "manual_highlights" in seg:
                    model.metadata["manual_highlights"] = list(seg.get("manual_highlights") or [])
            elif not model.original_text:
                model.original_text = str(seg.get("text", "") or "")
                model.status = "transcribed"
            models.append(model)
        return models

    def apply_translations(self, base_models, translated_segments) -> list[Segment]:
        models: list[Segment] = []
        base_models = base_models or []
        for idx, seg in enumerate(translated_segments or [], start=1):
            model = Segment.from_dict(seg, default_id=idx)
            if idx - 1 < len(base_models):
                base_model = base_models[idx - 1]
                if not model.original_text:
                    model.original_text = base_model.original_text
                source_words = base_model.metadata.get("words")
                if source_words and "words" not in seg:
                    model.metadata["words"] = list(source_words)
                source_highlights = base_model.metadata.get("manual_highlights")
                if source_highlights and "manual_highlights" not in seg:
                    model.metadata["manual_highlights"] = list(source_highlights)
            translated_text = seg.get("text", "")
            model.apply_translation(translated_text, refined=bool(seg.get("polished")))
            model.metadata["translation_provider"] = seg.get("provider", "")
            model.metadata["source_text"] = seg.get("source_text", "")
            if "words" in seg:
                model.metadata["words"] = list(seg.get("words") or [])
            if "manual_highlights" in seg:
                model.metadata["manual_highlights"] = list(seg.get("manual_highlights") or [])
            for key in ("tts_group_id", "tts_group_start", "tts_group_end"):
                if key in seg:
                    model.metadata[key] = seg.get(key)
            models.append(model)
        return models
