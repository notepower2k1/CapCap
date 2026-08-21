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
                if "auto_highlights" in seg:
                    model.metadata["auto_highlights"] = list(seg.get("auto_highlights") or [])
            elif not model.original_text:
                model.original_text = str(seg.get("text", "") or "")
                model.status = "transcribed"
            models.append(model)
        return models

    def apply_translations(self, base_models, translated_segments) -> list[Segment]:
        models: list[Segment] = []
        base_models = base_models or []
        # An imported/edited SRT can add or remove cues.  Positional matching
        # is only safe while both lists have the same shape; otherwise use
        # the preserved cue timing to keep source text, speaker IDs, and
        # word metadata attached to the correct translated cue.
        base_by_timing = {}
        if len(base_models) != len(translated_segments or []):
            for base_model in base_models:
                key = (
                    round(float(getattr(base_model, "start", 0.0) or 0.0), 3),
                    round(float(getattr(base_model, "end", 0.0) or 0.0), 3),
                )
                base_by_timing.setdefault(key, []).append(base_model)
        for idx, seg in enumerate(translated_segments or [], start=1):
            model = Segment.from_dict(seg, default_id=idx)
            base_model = None
            if len(base_models) == len(translated_segments or []) and idx - 1 < len(base_models):
                base_model = base_models[idx - 1]
            elif base_by_timing:
                key = (
                    round(float((seg or {}).get("start", 0.0) or 0.0), 3),
                    round(float((seg or {}).get("end", 0.0) or 0.0), 3),
                )
                candidates = base_by_timing.get(key) or []
                if candidates:
                    base_model = candidates.pop(0)
            if base_model is not None:
                # Segment.from_dict treats its generic ``text`` field as an
                # original-text fallback. For translations that field is the
                # translated cue, so only an explicitly retained original
                # should win over the source model.
                explicit_original = str((seg or {}).get("original_text") or (seg or {}).get("source_text") or "").strip()
                if not explicit_original:
                    model.original_text = base_model.original_text
                source_words = base_model.metadata.get("words")
                if source_words and "words" not in seg:
                    model.metadata["words"] = list(source_words)
                source_speaker = str(base_model.metadata.get("speaker", "") or "").strip()
                if source_speaker and "speaker" not in seg:
                    model.metadata["speaker"] = source_speaker
                source_highlights = base_model.metadata.get("manual_highlights")
                if source_highlights and "manual_highlights" not in seg:
                    model.metadata["manual_highlights"] = list(source_highlights)
                source_auto_highlights = base_model.metadata.get("auto_highlights")
                if source_auto_highlights and "auto_highlights" not in seg:
                    model.metadata["auto_highlights"] = list(source_auto_highlights)
            translated_text = seg.get("text", "")
            model.apply_translation(translated_text, refined=bool(seg.get("polished")))
            model.metadata["translation_provider"] = seg.get("provider", "")
            model.metadata["source_text"] = seg.get("source_text", "")
            if "words" in seg:
                model.metadata["words"] = list(seg.get("words") or [])
            if "manual_highlights" in seg:
                model.metadata["manual_highlights"] = list(seg.get("manual_highlights") or [])
            if "auto_highlights" in seg:
                model.metadata["auto_highlights"] = list(seg.get("auto_highlights") or [])
            for key in ("tts_group_id", "tts_group_start", "tts_group_end"):
                if key in seg:
                    model.metadata[key] = seg.get(key)
            models.append(model)
        return models
