import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from services import EngineRuntime, ProjectService


class VoiceWorkflow:
    MAX_TTS_WORKERS = 6

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

    def _mark_completed(self, state, *, voice_track: str, mixed_path: str, background_path: str):
        if not state:
            return
        self.project_service.update_artifact(state, "voice_vi", voice_track, save=False)
        self.project_service.update_step(state, "generate_tts", "done", save=False)
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
            os_module.path.join(self.workspace_root, "app", "voice_preview_catalog.json"),
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

    def _provider_native_speed(self, *, voice_name: str, requested_speed: float) -> float:
        provider = self._voice_provider(voice_name)
        speed_value = float(requested_speed)
        if provider == "edge":
            return speed_value
        return 1.0

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

    def _synthesize_segment_wavs(self, *, segments, tmp_dir: str, voice_name: str, provider_speed: float = 1.0, on_progress: callable = None):
        segments = list(segments or [])
        manifest = self._load_manifest(tmp_dir)
        manifest_segments = dict(manifest.get("segments", {}) or {})
        manifest_by_cache_key = dict(manifest.get("by_cache_key", {}) or {})
        wavs = [""] * len(segments)
        pending_jobs = []
        cache_hits = 0

        for idx, seg in enumerate(segments):
            txt = (seg.get("text") or "").strip()
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
            provider = self._voice_provider(voice_name)
            if provider == "piper":
                worker_count = 1
            else:
                worker_count = max(1, min(self.MAX_TTS_WORKERS, len(pending_jobs)))
            print(
                "[Voice Workflow] TTS synth jobs: "
                f"pending={len(pending_jobs)}, cache_hits={cache_hits}, workers={worker_count}, native_speed={provider_speed:.2f}"
            )
            if on_progress:
                on_progress(f"Synthesizing {len(pending_jobs)} subtitle segments (using {worker_count} workers)...")
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
        project_state_path: str = "",
        on_progress: callable = None,
    ):
        workflow_started = time.perf_counter()
        state = self._load_state(project_state_path)
        self._mark_started(state, with_background=bool(background_path))
        audio_mode_key = str(audio_handling_mode or "fast").strip().lower()
        print(f"[Voice Workflow] Audio handling mode: {audio_mode_key}")

        os.makedirs(output_dir, exist_ok=True)
        tmp_dir = os.path.join(output_dir, "_tts_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        provider_speed = self._provider_native_speed(
            voice_name=voice_name,
            requested_speed=float(voice_speed),
        )
        residual_speed = (
            float(voice_speed) / provider_speed
            if provider_speed > 0.0
            else float(voice_speed)
        )

        synth_started = time.perf_counter()
        wavs = self._synthesize_segment_wavs(
            segments=segments,
            tmp_dir=tmp_dir,
            voice_name=voice_name,
            provider_speed=provider_speed,
            on_progress=on_progress,
        )
        wavs = self._apply_segment_speed(
            wavs=wavs,
            tmp_dir=tmp_dir,
            voice_speed=residual_speed,
        )
        wavs = self._fit_segment_wavs_to_timeline(
            segments=segments,
            wavs=wavs,
            tmp_dir=tmp_dir,
            sync_mode=timing_sync_mode,
        )
        synth_elapsed = time.perf_counter() - synth_started
        print(
            "[Voice Workflow] Speed plan: "
            f"requested={float(voice_speed):.2f}, native={provider_speed:.2f}, residual={residual_speed:.2f}"
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
        }
