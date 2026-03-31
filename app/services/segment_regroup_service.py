from __future__ import annotations

import re


class SegmentRegroupService:
    def regroup(self, segments: list[dict], *, max_gap_seconds: float = 0.35, max_duration_seconds: float = 8.0) -> list[dict]:
        regrouped: list[dict] = []
        for segment in segments or []:
            text = str(segment.get("text", "") or "").strip()
            if not text:
                continue
            if not regrouped:
                regrouped.append(self._clone_segment(segment))
                continue

            previous = regrouped[-1]
            gap = float(segment.get("start", 0.0)) - float(previous.get("end", 0.0))
            proposed_duration = float(segment.get("end", 0.0)) - float(previous.get("start", 0.0))
            if self._should_merge(previous, segment, gap_seconds=gap, proposed_duration_seconds=proposed_duration, max_gap_seconds=max_gap_seconds, max_duration_seconds=max_duration_seconds):
                regrouped[-1] = self._merge_pair(previous, segment)
            else:
                regrouped.append(self._clone_segment(segment))

        normalized = []
        for index, segment in enumerate(regrouped, start=1):
            payload = self._clone_segment(segment)
            payload["id"] = index
            payload["text"] = self._normalize_sentence_text(payload.get("text", ""))
            normalized.append(payload)
        return normalized

    def _clone_segment(self, segment: dict) -> dict:
        payload = {
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "text": str(segment.get("text", "") or "").strip(),
        }
        if segment.get("words"):
            payload["words"] = list(segment.get("words") or [])
        if segment.get("chunk_id"):
            payload["chunk_id"] = segment.get("chunk_id")
        return payload

    def _should_merge(
        self,
        left: dict,
        right: dict,
        *,
        gap_seconds: float,
        proposed_duration_seconds: float,
        max_gap_seconds: float,
        max_duration_seconds: float,
    ) -> bool:
        if gap_seconds > max_gap_seconds:
            return False
        if proposed_duration_seconds > max_duration_seconds:
            return False
        left_text = str(left.get("text", "") or "").strip()
        right_text = str(right.get("text", "") or "").strip()
        if not left_text or not right_text:
            return False
        if left_text.endswith((".", "!", "?", "...", "…")):
            return False
        if right_text[:1].islower():
            return True
        continuation_prefixes = (
            "và",
            "nhưng",
            "rồi",
            "để",
            "khi",
            "nếu",
            "vì",
            "thì",
            "mà",
            "là",
        )
        return right_text.lower().startswith(continuation_prefixes)

    def _merge_pair(self, left: dict, right: dict) -> dict:
        merged_text = f"{str(left.get('text', '')).strip()} {str(right.get('text', '')).strip()}".strip()
        payload = {
            "start": float(left.get("start", 0.0)),
            "end": float(right.get("end", 0.0)),
            "text": merged_text,
        }
        merged_words = list(left.get("words") or []) + list(right.get("words") or [])
        if merged_words:
            payload["words"] = merged_words
        payload["chunk_id"] = right.get("chunk_id") or left.get("chunk_id", "")
        return payload

    def _normalize_sentence_text(self, text: str) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        if not value:
            return ""
        return value[:1].upper() + value[1:]
