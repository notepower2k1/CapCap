import os

from services import EngineRuntime, ProjectService


class VoiceWorkflow:
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

    def _synthesize_segment_wavs(self, *, segments, tmp_dir: str, voice_name: str, voice_speed: float):
        wavs = []
        for idx, seg in enumerate(segments):
            txt = (seg.get("text") or "").strip()
            if not txt:
                wavs.append("")
                continue
            seg_wav = os.path.join(tmp_dir, f"seg_{idx:04d}.wav")
            self.engine_runtime.synthesize_segment(
                text=txt,
                wav_path=seg_wav,
                voice=voice_name,
                speed=voice_speed,
                tmp_dir=tmp_dir,
            )
            wavs.append(seg_wav)
        return wavs

    def run(
        self,
        *,
        segments,
        output_dir: str,
        background_path: str = "",
        voice_name: str = "vi-VN-HoaiMyNeural",
        voice_speed: float = 1.0,
        timing_sync_mode: str = "off",
        voice_gain_db: float = 0.0,
        bg_gain_db: float = 0.0,
        project_state_path: str = "",
    ):
        state = self._load_state(project_state_path)
        self._mark_started(state, with_background=bool(background_path))

        os.makedirs(output_dir, exist_ok=True)
        tmp_dir = os.path.join(output_dir, "_tts_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        wavs = self._synthesize_segment_wavs(
            segments=segments,
            tmp_dir=tmp_dir,
            voice_name=voice_name,
            voice_speed=float(voice_speed),
        )
        wavs = self._fit_segment_wavs_to_timeline(
            segments=segments,
            wavs=wavs,
            tmp_dir=tmp_dir,
            sync_mode=timing_sync_mode,
        )

        voice_track = os.path.join(output_dir, "voice_vi.wav")
        self.engine_runtime.build_voice_track(
            segments=segments,
            tts_wav_paths=wavs,
            output_wav_path=voice_track,
            gain_db=float(voice_gain_db),
        )

        mixed = ""
        if background_path and os.path.exists(background_path):
            mixed = os.path.join(output_dir, "mixed_vi.wav")
            self.engine_runtime.mix_voice_with_background(
                background_wav_path=background_path,
                voice_wav_path=voice_track,
                output_wav_path=mixed,
                background_gain_db=float(bg_gain_db),
                voice_gain_db=0.0,
            )

        self._mark_completed(
            state,
            voice_track=voice_track,
            mixed_path=mixed,
            background_path=background_path,
        )

        return {
            "voice_track": voice_track,
            "mixed_path": mixed,
        }
