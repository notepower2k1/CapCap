import hashlib
import json
import os
import re
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed

from runtime_paths import app_path
from services import EngineRuntime, ProjectService
from tts_processor import normalize_text_for_tts


class VoiceWorkflow:
    MAX_TTS_WORKERS = 6
    PIPER_TTS_WORKERS = 1
    AI_REWRITE_RATIO = 1.05
    SMART_RETRY_RATIO = 1.15
    HARD_RETRY_RATIO = 1.30
    HARD_OUTLIER_RATIO = 1.20
    RETRY_MIN_ACCEPT_RATIO = 0.78
    RESCUE_MIN_ACCEPT_RATIO = 0.84
    MAX_SAFE_SEGMENT_SPEED = 1.12
    MAX_STUBBORN_SEGMENT_SPEED = 1.10
    TARGET_RATIO_FLOOR = 0.84
    TARGET_RATIO_CEIL = 1.08

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.project_service = ProjectService(workspace_root)
        self.engine_runtime = EngineRuntime()

    def _load_state(self, project_state_path: str = ""):
        return self.project_service.load_project(project_state_path) if project_state_path else None

    def _mark_started(self, state, *, with_background: bool):
        if not state:
            return
        self.project_service.update_step(state, "generate_tts", "running", save=False)
        if with_background:
            self.project_service.update_step(state, "mix_audio", "running", save=False)
        self.project_service.save_project(state)

    def _mark_completed(self, state, *, voice_track: str, mixed_path: str, background_path: str, segments=None):
        if not state:
            return
        self.project_service.update_artifact(state, "voice_vi", voice_track, save=False)
        self.project_service.update_step(state, "generate_tts", "done", save=False)
        if segments:
            self.project_service.save_json_artifact(
                state,
                "voice_segments",
                os.path.join("audio", "voice_segments.json"),
                list(segments or []),
            )
        if mixed_path:
            self.project_service.update_artifact(state, "mixed_vi", mixed_path, save=False)
            self.project_service.update_step(state, "mix_audio", "done", save=False)
        elif background_path:
            self.project_service.update_step(state, "mix_audio", "skipped", save=False)
        self.project_service.save_project(state)

    def _manifest_path(self, tmp_dir: str) -> str:
        return os.path.join(tmp_dir, "tts_cache_manifest.json")

    def _load_manifest(self, tmp_dir: str) -> dict:
        manifest_path = self._manifest_path(tmp_dir)
        if not os.path.exists(manifest_path):
            return {"segments": {}, "by_cache_key": {}}
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                payload.setdefault("segments", {})
                payload.setdefault("by_cache_key", {})
                return payload
        except Exception:
            pass
        return {"segments": {}, "by_cache_key": {}}

    def _save_manifest(self, tmp_dir: str, manifest: dict) -> None:
        manifest_path = self._manifest_path(tmp_dir)
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)

    def _segment_cache_key(self, *, text: str, voice_name: str, provider_speed: float) -> str:
        payload = f"{voice_name}|{provider_speed:.3f}|{text.strip()}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _voice_provider(self, voice_name: str) -> str:
        raw = str(voice_name or "").strip()
        
        # If has ":" prefix format, parse it
        if ":" in raw:
            provider, _voice_id = raw.split(":", 1)
            return provider.strip().lower() or "edge"
        
        # Otherwise, assume it's a voice ID - load from catalog to find provider
        import json
        import os as os_module
        
        # Try multiple paths to find catalog
        catalog_paths = [
            app_path("voice_preview_catalog.json"),
            os_module.path.join(os_module.path.dirname(os_module.path.dirname(__file__)), "voice_preview_catalog.json"),
            os_module.path.join(os_module.getcwd(), "app", "voice_preview_catalog.json"),
        ]
        
        for catalog_path in catalog_paths:
            try:
                if os_module.path.exists(catalog_path):
                    with open(catalog_path, "r", encoding="utf-8") as f:
                        catalog = json.load(f)
                    for voice in catalog.get("voices", []):
                        if voice.get("id") == raw:
                            provider = voice.get("provider", "edge").strip().lower()
                            print(f"[Voice] Found voice '{raw}' with provider: {provider}")
                            return provider
            except Exception as e:
                print(f"[Voice] Error reading catalog from {catalog_path}: {e}")
        
        print(f"[Voice] Voice '{raw}' not found in catalog, defaulting to 'piper'")
        # Default to piper for local voices
        return "piper"

    def _provider_native_speed(self, *, provider: str, requested_speed: float) -> float:
        speed_value = float(requested_speed)
        if provider == "edge":
            return speed_value
        return 1.0

    def _clamp_requested_speed(self, requested_speed: float) -> float:
        speed_value = max(0.5, float(requested_speed or 1.0))
        if speed_value > 1.30:
            print(f"[Voice Workflow] Requested speed {speed_value:.2f} exceeds safe cap. Clamping to 1.30.")
            return 1.30
        return speed_value

    def _count_words(self, text: str) -> int:
        return len([token for token in re.split(r"\s+", str(text or "").strip()) if token])

    def _normalized_tts_text(self, text: str, *, voice_provider: str = "") -> str:
        return " ".join(
            str(normalize_text_for_tts(text, provider=voice_provider or "piper") or "").replace("\n", " ").split()
        ).strip()

    def _count_spoken_words(self, text: str, *, voice_provider: str = "") -> int:
        normalized = self._normalized_tts_text(text, voice_provider=voice_provider)
        return self._count_words(normalized)

    def _spoken_budget_vi(self, *, duration_sec: float, speech_cost: int) -> int:
        base_budget = self._max_words_vi(duration_sec, speech_cost)
        if speech_cost >= 5:
            return max(1, base_budget - 1)
        return base_budget

    def _estimate_speech_cost(self, text: str) -> int:
        value = str(text or "").strip()
        if not value:
            return 0
        score = 0
        if re.search(r"\d", value):
            score += 2
        if re.search(r"\b(19|20)\d{2}\b", value):
            score += 2
        if re.search(r"[A-Za-z]+\d+|\d+[A-Za-z]+", value):
            score += 2
        if re.search(r"[A-Z]", value) or re.search(r"[A-Za-z]{4,}", value) or re.search(r"[@#%&+/=_-]", value):
            score += 2
        if len(re.findall(r"[,;:]", value)) >= 2:
            score += 1
        words = [token for token in re.split(r"\s+", value) if token]
        long_words = [token for token in words if len(re.sub(r"[^\w]", "", token, flags=re.UNICODE)) >= 7]
        if len(long_words) >= 2:
            score += 1
        multi_syllable = [
            token for token in words
            if len([part for part in re.split(r"[-_./]", token) if part]) >= 2
        ]
        if len(multi_syllable) >= max(2, len(words) // 2):
            score += 1
        if re.search(r"\b(api|iphone|pro|max|ultra|beta|gpu|cpu|ai|2tb|512gb|256gb)\b", value, flags=re.IGNORECASE):
            score += 1
        return score

    def _max_words_vi(self, duration_sec: float, speech_cost: int) -> int:
        duration = max(0.0, float(duration_sec))
        words_per_sec = 4.0 if speech_cost >= 3 else 4.5
        return max(1, int(duration * words_per_sec))

    def _target_words_for_ratio(
        self,
        *,
        current_words: int,
        current_spoken_words: int,
        max_words_vi: int,
        ratio: float,
        action: str,
        attempt: int,
    ) -> int:
        ratio_value = max(1.0, float(ratio or 1.0))
        desired_from_ratio = int(current_spoken_words / ratio_value) if current_spoken_words > 0 else current_words
        if action == "compress_light":
            margin = 0 if attempt <= 1 else 1
        elif action == "compress_aggressive":
            margin = 1 + max(0, attempt - 1)
        else:
            margin = max(1, min(2, current_words - 1))
        target_words = min(max_words_vi, desired_from_ratio - margin, current_words - 1)
        return max(1, target_words)

    def _target_words_for_spoken_budget(
        self,
        *,
        text: str,
        duration_sec: float,
        speech_cost: int,
        max_words_vi: int,
        voice_provider: str = "",
    ) -> int:
        spoken_budget = self._spoken_budget_vi(duration_sec=duration_sec, speech_cost=speech_cost)
        raw_words = max(1, self._count_words(text))
        spoken_words = max(1, self._count_spoken_words(text, voice_provider=voice_provider))
        if spoken_words <= spoken_budget:
            return max_words_vi
        shrink_ratio = spoken_budget / spoken_words
        adjusted = int(raw_words * shrink_ratio)
        return max(1, min(max_words_vi, adjusted))

    def _trim_text_for_tts(self, text: str, *, duration_sec: float, max_words_vi: int) -> str:
        value = " ".join(str(text or "").replace("\n", " ").split()).strip()
        if not value:
            return ""

        tokens = value.split()
        if duration_sec < 0.8:
            return " ".join(tokens[: min(2, len(tokens))]).strip()

        if len(tokens) <= max_words_vi:
            return value

        filler_words = {
            "thì", "là", "mà", "đó", "ấy", "nha", "nhé", "à", "ờ", "ừ",
            "rất", "khá", "thực", "sự", "kiểu", "như", "vậy", "luôn",
        }
        compact_tokens = []
        for token in tokens:
            cleaned = re.sub(r"[^\wÀ-ỹ]", "", token, flags=re.UNICODE).lower()
            if cleaned in filler_words:
                continue
            compact_tokens.append(token)

        if len(compact_tokens) < max_words_vi:
            compact_tokens = tokens

        return " ".join(compact_tokens[:max_words_vi]).strip(" ,.;:!?")

    def _keyword_only_text(self, text: str) -> str:
        value = " ".join(str(text or "").replace("\n", " ").split()).strip()
        if not value:
            return ""
        tokens = value.split()
        protected = []
        for token in tokens:
            cleaned = re.sub(r"[^\wÀ-ỹ]", "", token, flags=re.UNICODE)
            if not cleaned:
                continue
            if re.search(r"\d", cleaned) or re.search(r"[A-Z]", cleaned) or len(cleaned) >= 5:
                protected.append(token)
        shortlist = protected[:2] if protected else tokens[:2]
        return " ".join(shortlist).strip(" ,.;:!?")

    def _compress_text_to_budget(self, text: str, *, duration_sec: float, max_words_vi: int, mode: str = "light") -> str:
        value = " ".join(str(text or "").replace("\n", " ").split()).strip()
        if not value:
            return ""
        if mode == "keyword_only":
            return self._keyword_only_text(value)

        compact = self._trim_text_for_tts(value, duration_sec=duration_sec, max_words_vi=max_words_vi)
        if mode != "aggressive":
            return compact

        tokens = compact.split()
        if len(tokens) <= 2:
            return compact
        keep = max(1, min(max_words_vi, len(tokens) - max(1, len(tokens) // 4)))
        protected = []
        for token in tokens:
            cleaned = re.sub(r"[^\wÀ-ỹ]", "", token, flags=re.UNICODE)
            if re.search(r"\d", cleaned) or re.search(r"[A-Z]", cleaned):
                protected.append(token)
        compressed = tokens[:keep]
        for token in protected:
            if token not in compressed and len(compressed) < max_words_vi:
                compressed.append(token)
        return " ".join(compressed[:max_words_vi]).strip(" ,.;:!?")

    def _validate_initial_dubbing_text(
        self,
        *,
        source_text: str,
        subtitle_text: str,
        dubbing_text: str,
        duration_sec: float,
        max_words_vi: int,
        speech_cost: int,
        voice_provider: str = "",
    ) -> tuple[str, str]:
        value = " ".join(str(dubbing_text or "").replace("\n", " ").split()).strip()
        if not value:
            fallback = " ".join(str(subtitle_text or source_text or "").replace("\n", " ").split()).strip()
            value = fallback
        if duration_sec < 0.8:
            return self._keyword_only_text(source_text or subtitle_text), "keyword_only"
        spoken_budget = self._spoken_budget_vi(duration_sec=duration_sec, speech_cost=speech_cost)
        spoken_over_budget = self._count_spoken_words(value, voice_provider=voice_provider) > spoken_budget
        if self._count_words(value) > max_words_vi or spoken_over_budget:
            compact = self._compress_text_to_budget(
                value,
                duration_sec=duration_sec,
                max_words_vi=min(max_words_vi, spoken_budget),
                mode="light",
            )
            if compact:
                return compact, "compress_light"
        return value, "accept"

    def _retry_cap_for_segment(self, *, duration_sec: float, speech_cost: int) -> int:
        return 3 if (duration_sec < 1.8 or speech_cost >= 4) else 2

    def _choose_retry_action(self, *, duration_sec: float, speech_cost: int, ratio: float, attempt: int, retry_cap: int) -> str:
        if duration_sec < 0.8:
            return "keyword_only"
        if ratio <= 1.05:
            return "accept"
        if duration_sec < 1.2 and ratio > 1.05:
            return "compress_aggressive" if attempt > 1 else "compress_light"
        if duration_sec < 1.8 and ratio > 1.10:
            return "compress_aggressive" if attempt > 1 else "compress_light"
        if speech_cost >= 4 and ratio > 1.08:
            return "compress_aggressive" if attempt > 1 else "compress_light"
        if ratio <= 1.15:
            return "compress_light"
        if ratio <= 1.30:
            return "compress_aggressive" if attempt > 1 else "compress_light"
        if attempt >= retry_cap:
            return "keyword_only"
        return "compress_aggressive"

    def _should_use_speedup_before_rewrite(self, *, duration_sec: float, speech_cost: int, ratio: float) -> bool:
        ratio_value = float(ratio or 0.0)
        if ratio_value <= 1.05:
            return False
        if duration_sec < 1.8 or speech_cost >= 4:
            return False
        return ratio_value <= 1.15

    def _should_allow_post_rewrite_speedup(self, *, ratio: float) -> bool:
        return 1.05 < float(ratio or 0.0) <= 1.15

    def _is_target_ratio_band(self, ratio: float) -> bool:
        ratio_value = float(ratio or 0.0)
        return self.TARGET_RATIO_FLOOR <= ratio_value <= self.TARGET_RATIO_CEIL

    def _segment_speed_ratio_for_outlier(
        self,
        *,
        duration_sec: float,
        speech_cost: int,
        ratio: float,
    ) -> float:
        ratio_value = float(ratio or 0.0)
        if ratio_value <= 1.15:
            return 1.0
        if duration_sec < 0.8:
            return 1.0
        if ratio_value <= 1.20 and speech_cost >= 4:
            return min(self.MAX_SAFE_SEGMENT_SPEED, max(1.03, ratio_value / 1.06))
        if ratio_value <= 1.28:
            base = ratio_value / 1.10
            return min(self.MAX_SAFE_SEGMENT_SPEED, max(1.05, base))
        return self.MAX_SAFE_SEGMENT_SPEED

    def _segment_speed_ratio_for_medium_overrun(self, *, ratio: float) -> float:
        ratio_value = float(ratio or 0.0)
        if ratio_value <= 1.08 or ratio_value > 1.16:
            return 1.0
        if ratio_value <= 1.11:
            return 1.03
        if ratio_value <= 1.14:
            return 1.045
        return 1.06

    def _segment_speed_ratio_for_stubborn_segment(
        self,
        *,
        duration_sec: float,
        speech_cost: int,
        ratio: float,
        attempt_count: int,
        segment_index: int,
    ) -> float:
        ratio_value = float(ratio or 0.0)
        if ratio_value <= 1.12:
            return 1.0
        if duration_sec < 0.8:
            return 1.0
        if attempt_count < 2 and speech_cost < 4 and segment_index != 0:
            return 1.0
        if ratio_value <= 1.18:
            return min(self.MAX_STUBBORN_SEGMENT_SPEED, 1.04 if segment_index != 0 else 1.06)
        if ratio_value <= 1.24:
            return min(self.MAX_STUBBORN_SEGMENT_SPEED, 1.07 if speech_cost >= 4 or segment_index == 0 else 1.05)
        return self.MAX_STUBBORN_SEGMENT_SPEED

    def _finalize_segment_result(
        self,
        *,
        seg: dict,
        wav_path: str,
        target_duration: float,
        attempt_count: int,
        action_taken: str,
    ) -> None:
        metrics = dict(seg.get("_tts_metrics") or {})
        tts_duration = self._probe_wav_duration_seconds(wav_path)
        ratio = (tts_duration / target_duration) if target_duration > 0 else 0.0
        metrics["tts_duration"] = round(tts_duration, 3)
        metrics["ratio"] = round(ratio, 3)
        metrics["attempt_count"] = int(max(1, attempt_count))
        metrics["action_taken"] = action_taken
        seg["_tts_metrics"] = metrics
        seg["subtitle_vi"] = (seg.get("subtitle_vi") or seg.get("text") or "").strip()
        seg["dubbing_vi"] = (seg.get("tts_text") or "").strip()
        seg["tts_duration"] = metrics["tts_duration"]
        seg["ratio"] = metrics["ratio"]
        seg["attempt_count"] = metrics["attempt_count"]
        seg["action_taken"] = metrics["action_taken"]

    def _rewrite_segment_with_ai(
        self,
        *,
        source_text: str,
        draft_text: str,
        duration_sec: float,
        speech_cost: int,
        max_words_vi: int,
        measured_ratio: float,
        action: str,
        attempt: int,
        target_words: int,
        draft_words: int,
        draft_spoken_words: int,
        source_language: str = "auto",
        style_instruction: str = "",
    ) -> str:
        source_line = (
            f"[mode=dubbing_rewrite][action={action}]"
            f"[duration={duration_sec:.2f}]"
            f"[max_words_vi={max_words_vi}]"
            f"[speech_cost={speech_cost}]"
            f"[measured_ratio={measured_ratio:.3f}]"
            f"[attempt={attempt}]"
            f"[draft_words={draft_words}]"
            f"[draft_spoken_words={draft_spoken_words}]"
            f"[target_words={target_words}] "
            f"{source_text}"
        )
        prompt = (
            f"[mode=dubbing_rewrite][action={action}] "
            "Rewrite for dubbing timing. Follow metadata as hard constraints. "
            "Return one shorter spoken Vietnamese line than the draft. "
            "Target target_words or fewer. "
            "Preserve names, numbers, products, and key claims exactly."
        )
        cleaned_style = " ".join(str(style_instruction or "").split()).strip()
        if cleaned_style:
            prompt += f" Extra tone/style instruction: {cleaned_style}"
        rewritten_segments = self.engine_runtime.rewrite_translation_segments(
            [{"start": 0.0, "end": duration_sec, "text": source_line, "source_text": source_line}],
            [{"start": 0.0, "end": duration_sec, "text": draft_text}],
            src_lang=str(source_language or "auto"),
            style_instruction=prompt,
        )
        if not rewritten_segments:
            return draft_text
        rewritten_text = " ".join(str(rewritten_segments[0].get("text") or "").replace("\n", " ").split()).strip()
        return rewritten_text or draft_text

    def _plan_initial_dubbing_text(
        self,
        *,
        source_text: str,
        subtitle_text: str,
        duration_sec: float,
        speech_cost: int,
        max_words_vi: int,
        enabled: bool,
        source_language: str = "auto",
        style_instruction: str = "",
    ) -> str:
        if duration_sec < 0.8:
            return self._keyword_only_text(source_text or subtitle_text)

        base_text = " ".join(str(subtitle_text or source_text or "").replace("\n", " ").split()).strip()
        if not enabled:
            return base_text

        source_line = (
            f"[mode=dubbing_rewrite][action=translate_for_dubbing]"
            f"[duration={duration_sec:.2f}]"
            f"[max_words_vi={max_words_vi}]"
            f"[speech_cost={speech_cost}] "
            f"{source_text}"
        )
        prompt = (
            "[mode=dubbing_rewrite][action=translate_for_dubbing] "
            "Create short spoken Vietnamese for dubbing. "
            "Use duration, max_words_vi, and speech_cost as hard constraints. "
            "Return one concise spoken line that is shorter than subtitle-style wording when needed. "
            "Preserve names, numbers, products, and key claims exactly."
        )
        cleaned_style = " ".join(str(style_instruction or "").split()).strip()
        if cleaned_style:
            prompt += f" Extra tone/style instruction: {cleaned_style}"
        try:
            rewritten_segments = self.engine_runtime.rewrite_translation_segments(
                [{"start": 0.0, "end": duration_sec, "text": source_line, "source_text": source_line}],
                [{"start": 0.0, "end": duration_sec, "text": base_text}],
                src_lang=str(source_language or "auto"),
                style_instruction=prompt,
            )
            if not rewritten_segments:
                return base_text
            rewritten_text = " ".join(str(rewritten_segments[0].get("text") or "").replace("\n", " ").split()).strip()
            return rewritten_text or base_text
        except Exception:
            return base_text

    def _render_segment_candidate(
        self,
        *,
        text: str,
        idx: int,
        attempt: int,
        tmp_dir: str,
        voice_name: str,
        provider_speed: float,
        target_duration: float,
        on_progress: callable = None,
    ) -> tuple[str, float]:
        base_path = os.path.join(tmp_dir, f"seg_{idx:04d}_attempt_{attempt:02d}.wav")
        self.engine_runtime.synthesize_segment(
            text=text,
            wav_path=base_path,
            voice=voice_name,
            speed=provider_speed,
            tmp_dir=tmp_dir,
            on_progress=on_progress,
        )
        actual_duration = self._probe_wav_duration_seconds(base_path)
        ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
        return base_path, ratio

    def _make_retry_tts_text(self, current_text: str, *, source_text: str, duration_sec: float, ratio: float, max_words_vi: int) -> str:
        current_value = " ".join(str(current_text or "").replace("\n", " ").split()).strip()
        source_value = " ".join(str(source_text or "").replace("\n", " ").split()).strip()
        if not source_value:
            return current_value

        if duration_sec < 0.8:
            return " ".join(source_value.split()[:1]).strip(" ,.;:!?")

        current_words = self._count_words(current_value)
        if ratio <= 1.22:
            drop_words = 1
        elif ratio <= 1.35:
            drop_words = 1
        elif ratio <= 1.50:
            drop_words = 2
        else:
            drop_words = 3
        tighter_budget = max(1, min(max_words_vi, current_words - drop_words))
        retry_text = self._trim_text_for_tts(
            source_value,
            duration_sec=duration_sec,
            max_words_vi=tighter_budget,
        )
        if retry_text and retry_text != current_value:
            return retry_text

        current_tokens = current_value.split()
        if len(current_tokens) <= 1:
            return current_value
        drop_count = 1 if ratio <= 1.50 else 2
        return " ".join(current_tokens[: max(1, len(current_tokens) - drop_count)]).strip(" ,.;:!?")

    def _rebalance_too_short_candidate(
        self,
        current_text: str,
        *,
        source_text: str,
        duration_sec: float,
        current_words: int,
        candidate_words: int,
        max_words_vi: int,
    ) -> str:
        if current_words <= 2:
            return current_text
        softened_budget = max(
            candidate_words + 1,
            min(max_words_vi, current_words - 1),
        )
        softened = self._compress_text_to_budget(
            current_text or source_text,
            duration_sec=duration_sec,
            max_words_vi=softened_budget,
            mode="light",
        )
        softened = " ".join(str(softened or "").replace("\n", " ").split()).strip()
        if softened and softened != current_text:
            return softened
        fallback_budget = max(1, min(max_words_vi, current_words - 1))
        fallback = self._trim_text_for_tts(
            source_text or current_text,
            duration_sec=duration_sec,
            max_words_vi=fallback_budget,
        )
        fallback = " ".join(str(fallback or "").replace("\n", " ").split()).strip()
        return fallback or current_text

    def _hard_outlier_candidate_text(
        self,
        *,
        current_text: str,
        source_text: str,
        duration_sec: float,
        max_words_vi: int,
        ratio: float,
    ) -> str:
        current_words = self._count_words(current_text)
        emergency_budget = max(
            1,
            min(
                max_words_vi,
                current_words - 1,
                int(max(1.0, current_words / max(1.05, float(ratio or 1.0)))) - 1,
            ),
        )
        aggressive = self._compress_text_to_budget(
            source_text or current_text,
            duration_sec=duration_sec,
            max_words_vi=emergency_budget,
            mode="aggressive",
        )
        aggressive = " ".join(str(aggressive or "").replace("\n", " ").split()).strip()
        if aggressive and aggressive != current_text:
            return aggressive
        if current_words > 2:
            compact = " ".join(current_text.split()[: max(1, current_words - 2)]).strip(" ,.;:!?")
            if compact and compact != current_text:
                return compact
        return self._keyword_only_text(source_text or current_text)

    def _prepare_segments_for_tts(
        self,
        segments,
        *,
        voice_provider: str = "",
        ai_rewrite_dubbing: bool = False,
        source_language: str = "auto",
        style_instruction: str = "",
    ):
        prepared = []
        adjusted_count = 0
        for idx, seg in enumerate(list(segments or [])):
            current = dict(seg or {})
            source_text = (current.get("source_text") or current.get("text") or "").strip()
            subtitle_text = (current.get("text") or "").strip()
            duration_sec = max(0.0, float(current.get("end", 0.0)) - float(current.get("start", 0.0)))
            speech_cost = self._estimate_speech_cost(source_text)
            max_words_vi = self._max_words_vi(duration_sec, speech_cost)
            planned_text = self._plan_initial_dubbing_text(
                source_text=source_text,
                subtitle_text=subtitle_text,
                duration_sec=duration_sec,
                speech_cost=speech_cost,
                max_words_vi=max_words_vi,
                enabled=bool(ai_rewrite_dubbing),
                source_language=source_language,
                style_instruction=style_instruction,
            )
            original_words = self._count_words(subtitle_text)
            tts_text, validation_action = self._validate_initial_dubbing_text(
                source_text=source_text,
                subtitle_text=subtitle_text,
                dubbing_text=planned_text,
                duration_sec=duration_sec,
                max_words_vi=max_words_vi,
                speech_cost=speech_cost,
                voice_provider=voice_provider,
            )
            spoken_words = self._count_spoken_words(planned_text, voice_provider=voice_provider)
            if duration_sec < 0.8:
                action_taken = "keyword_only"
            elif validation_action != "accept":
                action_taken = validation_action
            else:
                action_taken = "translate_for_dubbing" if ai_rewrite_dubbing else "accept"
            if tts_text != subtitle_text:
                adjusted_count += 1
            current["tts_text"] = tts_text
            current["dubbing_vi"] = tts_text
            current["subtitle_vi"] = subtitle_text
            final_spoken_words = self._count_spoken_words(tts_text, voice_provider=voice_provider)
            current["_tts_metrics"] = {
                "duration_sec": round(duration_sec, 3),
                "speech_cost": speech_cost,
                "max_words_vi": max_words_vi,
                "original_words": original_words,
                "spoken_words": spoken_words,
                "tts_words": self._count_words(tts_text),
                "tts_spoken_words": final_spoken_words,
                "retry_cap": self._retry_cap_for_segment(duration_sec=duration_sec, speech_cost=speech_cost),
                "action_taken": action_taken,
                "trimmed": bool(tts_text != subtitle_text),
                "subtitle_vi": subtitle_text,
                "dubbing_vi": tts_text,
            }
            prepared.append(current)
            print(
                f"[Voice Workflow] Segment {idx + 1}: "
                f"dur={duration_sec:.2f}s cost={speech_cost} budget={max_words_vi} "
                f"words={original_words}->{self._count_words(tts_text)} "
                f"spoken={spoken_words}->{final_spoken_words}"
                + (" trimmed" if tts_text != source_text else "")
            )
        print(f"[Voice Workflow] Prepared TTS text: adjusted={adjusted_count}/{len(prepared)}")
        return prepared

    def _probe_wav_duration_seconds(self, wav_path: str) -> float:
        if not wav_path or not os.path.exists(wav_path):
            return 0.0
        with wave.open(wav_path, "rb") as wav_file:
            frame_rate = wav_file.getframerate() or 16000
            frame_count = wav_file.getnframes()
        return max(0.0, float(frame_count) / float(frame_rate))

    def _log_segment_fit_metrics(self, *, segments, wavs):
        clipped = 0
        for idx, (seg, wav_path) in enumerate(zip(list(segments or []), list(wavs or []))):
            target_duration = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            actual_duration = self._probe_wav_duration_seconds(wav_path)
            ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
            if ratio > 1.05:
                clipped += 1
            self._finalize_segment_result(
                seg=seg,
                wav_path=wav_path,
                target_duration=target_duration,
                attempt_count=int((seg.get("attempt_count") or (seg.get("_tts_metrics") or {}).get("attempt_count") or 1)),
                action_taken=str((seg.get("action_taken") or (seg.get("_tts_metrics") or {}).get("action_taken") or "accept")),
            )
            print(
                f"[Voice Fit] Segment {idx + 1}: "
                f"target={target_duration:.2f}s actual={actual_duration:.2f}s ratio={ratio:.3f}"
            )
        print(f"[Voice Fit] Over target (>1.05): {clipped}/{len(list(wavs or []))}")

    def _measure_segment_ratios(self, *, segments, wavs):
        ratios = []
        for seg, wav_path in zip(list(segments or []), list(wavs or [])):
            target_duration = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            actual_duration = self._probe_wav_duration_seconds(wav_path) if wav_path and os.path.exists(wav_path) else 0.0
            ratios.append((actual_duration / target_duration) if target_duration > 0 else 0.0)
        return ratios

    def _retry_overlong_segments(
        self,
        *,
        segments,
        wavs,
        tmp_dir: str,
        voice_name: str,
        provider_speed: float,
        voice_provider: str,
        ai_rewrite_dubbing: bool = False,
        source_language: str = "auto",
        style_instruction: str = "",
        on_progress: callable = None,
    ):
        updated_wavs = list(wavs or [])
        retry_count = 0
        for idx, seg in enumerate(list(segments or [])):
            wav_path = updated_wavs[idx] if idx < len(updated_wavs) else ""
            if not wav_path or not os.path.exists(wav_path):
                continue

            target_duration = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            actual_duration = self._probe_wav_duration_seconds(wav_path)
            ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
            if ratio <= self.AI_REWRITE_RATIO:
                continue

            metrics = dict(seg.get("_tts_metrics") or {})
            max_words_vi = max(1, int(metrics.get("max_words_vi") or self._max_words_vi(target_duration, int(metrics.get("speech_cost") or 0))))
            speech_cost = int(metrics.get("speech_cost") or 0)
            retry_cap = max(1, int(metrics.get("retry_cap") or self._retry_cap_for_segment(duration_sec=target_duration, speech_cost=speech_cost)))
            source_text = (seg.get("source_text") or seg.get("text") or "").strip()
            current_text = (seg.get("tts_text") or source_text).strip()
            best_ratio = ratio
            best_text = current_text
            best_wav = wav_path
            applied_action = str(metrics.get("action_taken") or "accept")
            attempt_count = 1

            if self._should_use_speedup_before_rewrite(
                duration_sec=target_duration,
                speech_cost=speech_cost,
                ratio=best_ratio,
            ):
                applied_action = "speed_light"
                metrics["retry_applied"] = True
                metrics["retry_action"] = applied_action
                seg["_tts_metrics"] = metrics
                seg["attempt_count"] = attempt_count
                seg["action_taken"] = applied_action
                continue

            attempt = 1
            while attempt <= retry_cap and best_ratio > self.AI_REWRITE_RATIO:
                action = self._choose_retry_action(
                    duration_sec=target_duration,
                    speech_cost=speech_cost,
                    ratio=best_ratio,
                    attempt=attempt,
                    retry_cap=retry_cap,
                )
                if action == "accept":
                    break

                current_words = self._count_words(best_text)
                current_spoken_words = self._count_spoken_words(best_text, voice_provider=voice_provider)
                target_words = self._target_words_for_ratio(
                    current_words=current_words,
                    current_spoken_words=current_spoken_words,
                    max_words_vi=max_words_vi,
                    ratio=best_ratio,
                    action=action,
                    attempt=attempt,
                )
                target_words = min(
                    target_words,
                    self._target_words_for_spoken_budget(
                        text=best_text if action == "compress_light" else source_text,
                        duration_sec=target_duration,
                        speech_cost=speech_cost,
                        max_words_vi=max_words_vi,
                        voice_provider=voice_provider,
                    ),
                )

                if action == "keyword_only":
                    candidate_text = self._keyword_only_text(source_text)
                elif ai_rewrite_dubbing:
                    try:
                        candidate_text = self._rewrite_segment_with_ai(
                            source_text=source_text,
                            draft_text=best_text,
                            duration_sec=target_duration,
                            speech_cost=speech_cost,
                            max_words_vi=max_words_vi,
                            measured_ratio=best_ratio,
                            action=action,
                            attempt=attempt,
                            target_words=target_words,
                            draft_words=current_words,
                            draft_spoken_words=current_spoken_words,
                            source_language=source_language,
                            style_instruction=style_instruction,
                        )
                    except Exception:
                        candidate_text = best_text
                    if not candidate_text or candidate_text == best_text:
                        mode = "keyword_only" if action == "keyword_only" else ("aggressive" if action == "compress_aggressive" else "light")
                        candidate_text = self._compress_text_to_budget(
                            best_text if action == "compress_light" else source_text,
                            duration_sec=target_duration,
                            max_words_vi=target_words,
                            mode=mode,
                        )
                else:
                    mode = "keyword_only" if action == "keyword_only" else ("aggressive" if action == "compress_aggressive" else "light")
                    candidate_text = self._compress_text_to_budget(
                        best_text if action == "compress_light" else source_text,
                        duration_sec=target_duration,
                        max_words_vi=target_words,
                        mode=mode,
                    )

                candidate_text = " ".join(str(candidate_text or "").replace("\n", " ").split()).strip()
                candidate_words = self._count_words(candidate_text)
                candidate_spoken_words = self._count_spoken_words(candidate_text, voice_provider=voice_provider)
                if (
                    not candidate_text
                    or candidate_text == best_text
                    or candidate_words >= current_words
                    or candidate_spoken_words >= current_spoken_words
                ):
                    attempt += 1
                    continue

                retry_count += 1
                print(
                    f"[Voice Retry] Segment {idx + 1}: action={action} ratio={best_ratio:.3f} "
                    f"words={current_words}->{candidate_words}"
                )
                if on_progress:
                    on_progress(f"Retrying overlong TTS segment {idx + 1} ({action})...")

                candidate_wav, candidate_ratio = self._render_segment_candidate(
                    text=candidate_text,
                    idx=idx,
                    attempt=attempt,
                    tmp_dir=tmp_dir,
                    voice_name=voice_name,
                    provider_speed=provider_speed,
                    target_duration=target_duration,
                    on_progress=on_progress,
                )
                if candidate_ratio < self.RETRY_MIN_ACCEPT_RATIO:
                    softened_text = self._rebalance_too_short_candidate(
                        best_text,
                        source_text=source_text,
                        duration_sec=target_duration,
                        current_words=current_words,
                        candidate_words=candidate_words,
                        max_words_vi=max_words_vi,
                    )
                    softened_words = self._count_words(softened_text)
                    softened_spoken_words = self._count_spoken_words(softened_text, voice_provider=voice_provider)
                    if (
                        softened_text
                        and softened_text != best_text
                        and softened_text != candidate_text
                        and softened_words < current_words
                        and softened_spoken_words < current_spoken_words
                    ):
                        softened_wav, softened_ratio = self._render_segment_candidate(
                            text=softened_text,
                            idx=idx,
                            attempt=(attempt + 10),
                            tmp_dir=tmp_dir,
                            voice_name=voice_name,
                            provider_speed=provider_speed,
                            target_duration=target_duration,
                            on_progress=on_progress,
                        )
                        if abs(softened_ratio - 1.0) < abs(candidate_ratio - 1.0):
                            candidate_text = softened_text
                            candidate_words = softened_words
                            candidate_spoken_words = softened_spoken_words
                            candidate_wav = softened_wav
                            candidate_ratio = softened_ratio
                if self._accept_retry_candidate(old_ratio=best_ratio, new_ratio=candidate_ratio):
                    best_text = candidate_text
                    best_wav = candidate_wav
                    applied_action = action
                    attempt_count = attempt + 1
                    metrics["retry_action"] = action
                    metrics["retry_ratio_before"] = round(ratio, 3)
                    metrics["retry_ratio_after"] = round(candidate_ratio, 3)
                    metrics["retry_text_words"] = candidate_words
                    metrics["retry_spoken_words"] = candidate_spoken_words
                    metrics["retry_applied"] = True
                    seg["_tts_metrics"] = metrics
                    best_ratio = candidate_ratio
                else:
                    print(
                        f"[Voice Retry] Segment {idx + 1}: rejected {action} "
                        f"old_ratio={best_ratio:.3f} new_ratio={candidate_ratio:.3f}"
                    )
                attempt += 1

            if best_ratio > self.HARD_OUTLIER_RATIO:
                emergency_text = self._hard_outlier_candidate_text(
                    current_text=best_text,
                    source_text=source_text,
                    duration_sec=target_duration,
                    max_words_vi=max_words_vi,
                    ratio=best_ratio,
                )
                emergency_words = self._count_words(emergency_text)
                emergency_spoken_words = self._count_spoken_words(emergency_text, voice_provider=voice_provider)
                if (
                    emergency_text
                    and emergency_text != best_text
                    and emergency_words < self._count_words(best_text)
                    and emergency_spoken_words < self._count_spoken_words(best_text, voice_provider=voice_provider)
                ):
                    print(
                        f"[Voice Retry] Segment {idx + 1}: action=compress_emergency ratio={best_ratio:.3f} "
                        f"words={self._count_words(best_text)}->{emergency_words}"
                    )
                    emergency_wav, emergency_ratio = self._render_segment_candidate(
                        text=emergency_text,
                        idx=idx,
                        attempt=(retry_cap + 20),
                        tmp_dir=tmp_dir,
                        voice_name=voice_name,
                        provider_speed=provider_speed,
                        target_duration=target_duration,
                        on_progress=on_progress,
                    )
                    if (
                        emergency_ratio >= self.RESCUE_MIN_ACCEPT_RATIO
                        and abs(emergency_ratio - 1.0) < abs(best_ratio - 1.0)
                    ):
                        best_text = emergency_text
                        best_wav = emergency_wav
                        best_ratio = emergency_ratio
                        applied_action = "compress_emergency"
                        attempt_count = max(attempt_count, retry_cap + 1)
                        metrics["retry_action"] = "compress_emergency"
                        metrics["retry_ratio_after"] = round(emergency_ratio, 3)
                        metrics["retry_text_words"] = emergency_words
                        metrics["retry_spoken_words"] = emergency_spoken_words
                        metrics["retry_applied"] = True
                        seg["_tts_metrics"] = metrics

            if best_text != current_text:
                seg["tts_text"] = best_text
                seg["dubbing_vi"] = best_text
                updated_wavs[idx] = best_wav
            seg["attempt_count"] = int(max(1, attempt_count))
            seg["action_taken"] = applied_action
            metrics = dict(seg.get("_tts_metrics") or {})
            metrics["attempt_count"] = int(max(1, attempt_count))
            metrics["action_taken"] = applied_action
            metrics["dubbing_vi"] = (seg.get("tts_text") or "").strip()
            seg["_tts_metrics"] = metrics

        print(f"[Voice Retry] Retried overlong segments: {retry_count}")
        return updated_wavs

    def _accept_retry_candidate(self, *, old_ratio: float, new_ratio: float) -> bool:
        if new_ratio <= 0.0:
            return False
        if new_ratio < self.RETRY_MIN_ACCEPT_RATIO:
            return False
        if self._is_target_ratio_band(new_ratio):
            return True
        return abs(new_ratio - 1.0) < abs(old_ratio - 1.0)

    def _apply_segment_speed(self, *, wavs, tmp_dir: str, voice_speed: float):
        speed_value = float(voice_speed)
        if abs(speed_value - 1.0) < 0.02:
            return wavs

        adjusted_wavs = []
        for idx, wav_path in enumerate(wavs):
            if not wav_path or not os.path.exists(wav_path):
                adjusted_wavs.append(wav_path)
                continue
            adjusted_path = os.path.join(tmp_dir, f"seg_{idx:04d}_speed_{int(round(speed_value * 100)):03d}.wav")
            adjusted_wavs.append(
                self.engine_runtime.change_wav_speed(
                    input_wav_path=wav_path,
                    output_wav_path=adjusted_path,
                    speed_ratio=speed_value,
                )
            )
        return adjusted_wavs

    def _apply_safe_timing_polish(
        self,
        *,
        segments,
        wavs,
        tmp_dir: str,
        voice_speed: float,
        sync_mode: str,
    ):
        polished_wavs = list(wavs or [])
        for idx, (seg, wav_path) in enumerate(zip(list(segments or []), polished_wavs)):
            if not wav_path or not os.path.exists(wav_path):
                continue
            target_duration = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            actual_duration = self._probe_wav_duration_seconds(wav_path)
            ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
            speech_cost = int(((seg.get("_tts_metrics") or {}).get("speech_cost")) or 0)
            duration_sec = float(((seg.get("_tts_metrics") or {}).get("duration_sec")) or target_duration)
            attempt_count = int((seg.get("attempt_count") or (seg.get("_tts_metrics") or {}).get("attempt_count") or 1))

            if self._should_use_speedup_before_rewrite(
                duration_sec=duration_sec,
                speech_cost=speech_cost,
                ratio=ratio,
            ):
                speed_ratio = min(1.15, max(1.0, ratio))
                if abs(speed_ratio - 1.0) >= 0.02:
                    adjusted_path = os.path.join(tmp_dir, f"seg_{idx:04d}_polish_speed.wav")
                    wav_path = self.engine_runtime.change_wav_speed(
                        input_wav_path=wav_path,
                        output_wav_path=adjusted_path,
                        speed_ratio=speed_ratio,
                    )
                    polished_wavs[idx] = wav_path
                    seg["action_taken"] = "speed_light"
                    metrics = dict(seg.get("_tts_metrics") or {})
                    metrics["action_taken"] = "speed_light"
                    seg["_tts_metrics"] = metrics

            actual_duration = self._probe_wav_duration_seconds(polished_wavs[idx])
            ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
            stubborn_speed = self._segment_speed_ratio_for_stubborn_segment(
                duration_sec=duration_sec,
                speech_cost=speech_cost,
                ratio=ratio,
                attempt_count=attempt_count,
                segment_index=idx,
            )
            if stubborn_speed > 1.0:
                adjusted_path = os.path.join(tmp_dir, f"seg_{idx:04d}_stubborn_speed.wav")
                polished_wavs[idx] = self.engine_runtime.change_wav_speed(
                    input_wav_path=polished_wavs[idx],
                    output_wav_path=adjusted_path,
                    speed_ratio=stubborn_speed,
                )
                seg["action_taken"] = "speed_stubborn"
                metrics = dict(seg.get("_tts_metrics") or {})
                metrics["action_taken"] = "speed_stubborn"
                metrics["stubborn_speed_ratio"] = round(stubborn_speed, 3)
                seg["_tts_metrics"] = metrics

            actual_duration = self._probe_wav_duration_seconds(polished_wavs[idx])
            ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
            rescue_speed = self._segment_speed_ratio_for_outlier(
                duration_sec=duration_sec,
                speech_cost=speech_cost,
                ratio=ratio,
            )
            if rescue_speed > 1.0 and ratio > 1.15:
                adjusted_path = os.path.join(tmp_dir, f"seg_{idx:04d}_rescue_speed.wav")
                polished_wavs[idx] = self.engine_runtime.change_wav_speed(
                    input_wav_path=polished_wavs[idx],
                    output_wav_path=adjusted_path,
                    speed_ratio=rescue_speed,
                )
                seg["action_taken"] = "speed_rescue"
                metrics = dict(seg.get("_tts_metrics") or {})
                metrics["action_taken"] = "speed_rescue"
                metrics["rescue_speed_ratio"] = round(rescue_speed, 3)
                seg["_tts_metrics"] = metrics

            actual_duration = self._probe_wav_duration_seconds(polished_wavs[idx])
            ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
            medium_speed = self._segment_speed_ratio_for_medium_overrun(ratio=ratio)
            if medium_speed > 1.0:
                adjusted_path = os.path.join(tmp_dir, f"seg_{idx:04d}_medium_speed.wav")
                polished_wavs[idx] = self.engine_runtime.change_wav_speed(
                    input_wav_path=polished_wavs[idx],
                    output_wav_path=adjusted_path,
                    speed_ratio=medium_speed,
                )
                seg["action_taken"] = "speed_balance"
                metrics = dict(seg.get("_tts_metrics") or {})
                metrics["action_taken"] = "speed_balance"
                metrics["balance_speed_ratio"] = round(medium_speed, 3)
                seg["_tts_metrics"] = metrics

            if (sync_mode or "off").strip().lower() == "smart":
                actual_duration = self._probe_wav_duration_seconds(polished_wavs[idx])
                ratio = (actual_duration / target_duration) if target_duration > 0 else 0.0
                if self._should_allow_post_rewrite_speedup(ratio=ratio):
                    synced_path = os.path.join(tmp_dir, f"seg_{idx:04d}_smartfit.wav")
                    polished_wavs[idx] = self.engine_runtime.fit_wav_to_duration(
                        input_wav_path=polished_wavs[idx],
                        output_wav_path=synced_path,
                        target_duration_seconds=target_duration,
                        mode="smart",
                    )
        if abs(float(voice_speed) - 1.0) >= 0.02:
            polished_wavs = self._apply_segment_speed(
                wavs=polished_wavs,
                tmp_dir=tmp_dir,
                voice_speed=voice_speed,
            )
        return polished_wavs

    def _fit_segment_wavs_to_timeline(self, *, segments, wavs, tmp_dir: str, sync_mode: str):
        mode_key = (sync_mode or "off").strip().lower()
        if mode_key != "smart":
            return wavs

        synced_wavs = []
        for idx, (seg, wav_path) in enumerate(zip(segments, wavs)):
            if not wav_path or not os.path.exists(wav_path):
                synced_wavs.append(wav_path)
                continue
            target_duration = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            synced_path = os.path.join(tmp_dir, f"seg_{idx:04d}_smartfit.wav")
            fitted_path = self.engine_runtime.fit_wav_to_duration(
                input_wav_path=wav_path,
                output_wav_path=synced_path,
                target_duration_seconds=target_duration,
                mode=mode_key,
            )
            synced_wavs.append(fitted_path)
        return synced_wavs

    def _synthesize_segment_wavs(self, *, segments, tmp_dir: str, voice_name: str, provider_speed: float = 1.0, voice_provider: str = '', on_progress: callable = None):
        segments = list(segments or [])
        manifest = self._load_manifest(tmp_dir)
        manifest_segments = dict(manifest.get("segments", {}) or {})
        manifest_by_cache_key = dict(manifest.get("by_cache_key", {}) or {})
        wavs = [""] * len(segments)
        pending_jobs = []
        cache_hits = 0

        for idx, seg in enumerate(segments):
            txt = (seg.get("tts_text") or seg.get("text") or "").strip()
            if not txt:
                wavs[idx] = ""
                continue
            seg_wav = os.path.join(tmp_dir, f"seg_{idx:04d}_base.wav")
            cache_key = self._segment_cache_key(text=txt, voice_name=voice_name, provider_speed=provider_speed)
            cache_entry = manifest_segments.get(str(idx), {})
            cached_wav = str(cache_entry.get("wav_path", "")).strip()
            cached_key = str(cache_entry.get("cache_key", "")).strip()
            if not (cached_key == cache_key and cached_wav and os.path.exists(cached_wav)):
                cache_entry = dict(manifest_by_cache_key.get(cache_key, {}) or {})
                cached_wav = str(cache_entry.get("wav_path", "")).strip()
                cached_key = str(cache_entry.get("cache_key", "")).strip()
            if cached_key == cache_key and cached_wav and os.path.exists(cached_wav):
                wavs[idx] = cached_wav
                manifest_segments[str(idx)] = {
                    "cache_key": cache_key,
                    "wav_path": cached_wav,
                    "text": txt,
                    "voice_name": voice_name,
                    "provider_speed": provider_speed,
                }
                manifest_by_cache_key[cache_key] = dict(manifest_segments[str(idx)])
                cache_hits += 1
                continue
            pending_jobs.append(
                {
                    "idx": idx,
                    "text": txt,
                    "wav_path": seg_wav,
                    "cache_key": cache_key,
                }
            )

        if pending_jobs:
            if voice_provider == "piper":
                worker_count = max(1, min(self.PIPER_TTS_WORKERS, len(pending_jobs)))
                try:
                    # Warm Piper once before parallel synthesis so the UI does not appear frozen during first-load.
                    self.engine_runtime.synthesize_segment(
                        text=pending_jobs[0]["text"],
                        wav_path=pending_jobs[0]["wav_path"],
                        voice=voice_name,
                        speed=provider_speed,
                        tmp_dir=tmp_dir,
                        on_progress=on_progress,
                    )
                    manifest_segments[str(pending_jobs[0]["idx"])] = {
                        "cache_key": str(pending_jobs[0]["cache_key"]),
                        "wav_path": str(pending_jobs[0]["wav_path"]),
                        "text": str(pending_jobs[0]["text"]),
                        "voice_name": voice_name,
                        "provider_speed": provider_speed,
                    }
                    manifest_by_cache_key[str(pending_jobs[0]["cache_key"])] = dict(manifest_segments[str(pending_jobs[0]["idx"])])
                    wavs[int(pending_jobs[0]["idx"])] = str(pending_jobs[0]["wav_path"])
                    pending_jobs = pending_jobs[1:]
                    cache_hits += 1
                except Exception:
                    pass
            elif voice_provider == "edge":
                worker_count = 1
            else:
                worker_count = max(1, min(self.MAX_TTS_WORKERS, len(pending_jobs)))
            print(
                "[Voice Workflow] TTS synth jobs: "
                f"pending={len(pending_jobs)}, cache_hits={cache_hits}, workers={worker_count}, native_speed={provider_speed:.2f}"
            )
            if on_progress:
                on_progress(f"Synthesizing {len(pending_jobs)} subtitle segments (using {worker_count} workers)...")
            if not pending_jobs:
                manifest["segments"] = manifest_segments
                manifest["by_cache_key"] = manifest_by_cache_key
                self._save_manifest(tmp_dir, manifest)
                return wavs
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        self.engine_runtime.synthesize_segment,
                        text=job["text"],
                        wav_path=job["wav_path"],
                        voice=voice_name,
                        speed=provider_speed,
                        tmp_dir=tmp_dir,
                        on_progress=on_progress,
                    ): job
                    for job in pending_jobs
                }
                completed_count = 0
                for future in as_completed(future_map):
                    job = future_map[future]
                    idx = int(job["idx"])
                    txt = str(job["text"])
                    seg_wav = str(job["wav_path"])
                    try:
                        future.result()
                        completed_count += 1
                        if on_progress:
                            on_progress(f"✓ Synthesized {completed_count}/{len(pending_jobs)} segments")
                    except Exception as exc:
                        preview = " ".join(txt.split())
                        if len(preview) > 120:
                            preview = preview[:117] + "..."
                        if on_progress:
                            on_progress(f"✗ Error at segment {idx + 1}: {preview[:50]}...")
                        raise RuntimeError(
                            f"TTS failed at subtitle segment {idx + 1}: \"{preview}\". {exc}"
                        ) from exc
                    manifest_segments[str(idx)] = {
                        "cache_key": str(job["cache_key"]),
                        "wav_path": seg_wav,
                        "text": txt,
                        "voice_name": voice_name,
                        "provider_speed": provider_speed,
                    }
                    manifest_by_cache_key[str(job["cache_key"])] = dict(manifest_segments[str(idx)])
                    wavs[idx] = seg_wav
        else:
            print(f"[Voice Workflow] TTS synth jobs: pending=0, cache_hits={cache_hits}, workers=0, native_speed={provider_speed:.2f}")

        manifest["segments"] = manifest_segments
        manifest["by_cache_key"] = manifest_by_cache_key
        self._save_manifest(tmp_dir, manifest)
        return wavs

    def run(
        self,
        *,
        segments,
        output_dir: str,
        background_path: str = "",
        audio_handling_mode: str = "fast",
        voice_name: str = "vi_VN-vais1000-medium",
        voice_speed: float = 1.0,
        timing_sync_mode: str = "off",
        voice_gain_db: float = 0.0,
        bg_gain_db: float = 0.0,
        ducking_amount_db: float = -6.0,
        project_state_path: str = "",
        project_temp_dir: str = "",
        ai_rewrite_dubbing: bool = False,
        dubbing_style_instruction: str = "",
        source_language: str = "auto",
        on_progress: callable = None,
    ):
        workflow_started = time.perf_counter()
        state = self._load_state(project_state_path)
        self._mark_started(state, with_background=bool(background_path))
        audio_mode_key = str(audio_handling_mode or "fast").strip().lower()
        print(f"[Voice Workflow] Audio handling mode: {audio_mode_key}")
        os.makedirs(output_dir, exist_ok=True)
        tmp_dir = str(project_temp_dir or "").strip() or os.path.join(output_dir, "_tts_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        safe_voice_speed = self._clamp_requested_speed(float(voice_speed))
        voice_provider = self._voice_provider(voice_name)
        segments = self._prepare_segments_for_tts(
            segments,
            voice_provider=voice_provider,
            ai_rewrite_dubbing=bool(ai_rewrite_dubbing),
            source_language=source_language,
            style_instruction=dubbing_style_instruction,
        )
        provider_speed = self._provider_native_speed(
            provider=voice_provider,
            requested_speed=safe_voice_speed,
        )
        residual_speed = (
            safe_voice_speed / provider_speed
            if provider_speed > 0.0
            else safe_voice_speed
        )

        synth_started = time.perf_counter()
        wavs = self._synthesize_segment_wavs(
            segments=segments,
            tmp_dir=tmp_dir,
            voice_name=voice_name,
            provider_speed=provider_speed,
            voice_provider=voice_provider,
            on_progress=on_progress,
        )
        wavs = self._retry_overlong_segments(
            segments=segments,
            wavs=wavs,
            tmp_dir=tmp_dir,
            voice_name=voice_name,
            provider_speed=provider_speed,
            voice_provider=voice_provider,
            ai_rewrite_dubbing=bool(ai_rewrite_dubbing),
            source_language=source_language,
            style_instruction=dubbing_style_instruction,
            on_progress=on_progress,
        )
        wavs = self._apply_safe_timing_polish(
            segments=segments,
            wavs=wavs,
            tmp_dir=tmp_dir,
            voice_speed=residual_speed,
            sync_mode=timing_sync_mode,
        )
        self._log_segment_fit_metrics(segments=segments, wavs=wavs)
        synth_elapsed = time.perf_counter() - synth_started
        print(
            "[Voice Workflow] Speed plan: "
            f"requested={safe_voice_speed:.2f}, native={provider_speed:.2f}, residual={residual_speed:.2f}"
        )

        voice_track = os.path.join(output_dir, "voice_vi.wav")
        build_started = time.perf_counter()
        self.engine_runtime.build_voice_track(
            segments=segments,
            tts_wav_paths=wavs,
            output_wav_path=voice_track,
            gain_db=float(voice_gain_db),
        )
        build_elapsed = time.perf_counter() - build_started

        mixed = ""
        if background_path and os.path.exists(background_path):
            mixed = os.path.normpath(os.path.join(output_dir, "mixed_vi.wav"))
            if audio_mode_key == "fast":
                print(f"[Voice Workflow] Fast Mode mix: ducking original/extracted background source {background_path}")
            else:
                print(f"[Voice Workflow] Clean Voice mix: overlaying TTS with separated background stem {background_path}")
            mix_started = time.perf_counter()
            self.engine_runtime.mix_voice_with_background(
                background_wav_path=background_path,
                voice_wav_path=voice_track,
                output_wav_path=mixed,
                background_gain_db=float(bg_gain_db),
                voice_gain_db=0.0,
                ducking_mode="timeline" if audio_mode_key == "fast" else "off",
                ducking_segments=segments if audio_mode_key == "fast" else None,
                ducking_amount_db=float(ducking_amount_db),
            )
            mix_elapsed = time.perf_counter() - mix_started
            print(f"[Voice Workflow] Mixed output created: {mixed}")
        else:
            mix_elapsed = 0.0
            print("[Voice Workflow] No background source found. Generating voice track only.")

        self._mark_completed(
            state,
            voice_track=voice_track,
            mixed_path=mixed,
            background_path=background_path,
            segments=segments,
        )
        workflow_elapsed = time.perf_counter() - workflow_started
        print(
            "[Timing] Voice workflow: "
            f"synthesize={synth_elapsed:.2f}s, "
            f"build_track={build_elapsed:.2f}s, "
            f"mix={mix_elapsed:.2f}s, "
            f"total={workflow_elapsed:.2f}s"
        )

        return {
            "voice_track": voice_track,
            "mixed_path": mixed,
            "segments": segments,
        }
