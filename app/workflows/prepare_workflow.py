import os
import time

from runtime_profile import is_remote_profile
from services import AsrMergeService, ChunkingService, EngineRuntime, ProjectService, SegmentRegroupService, SegmentService


class PrepareWorkflow:
    CHUNKED_ASR_MIN_DURATION_SECONDS = 90.0
    CHUNK_TARGET_DURATION_SECONDS = 12.0
    CHUNK_MAX_DURATION_SECONDS = 20.0
    CHUNK_OVERLAP_SECONDS = 0.5
    CHUNK_SILENCE_NOISE = "-35dB"
    CHUNK_SILENCE_DURATION_SECONDS = 0.35

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.project_service = ProjectService(workspace_root)
        self.segment_service = SegmentService()
        self.chunking_service = ChunkingService(workspace_root)
        self.asr_merge_service = AsrMergeService()
        self.segment_regroup_service = SegmentRegroupService()
        self.engine_runtime = EngineRuntime()

    def _transcribe_long_audio_chunked(self, *, audio_path: str, project_state, model_path: str, language: str):
        overall_started = time.perf_counter()
        chunk_dir = self.project_service.build_path(project_state, "audio", "chunks")
        chunk_cache_dir = self.project_service.build_path(project_state, "analysis", "chunk_results")
        transcription_config = {
            "vad_mode": "silencedetect",
            "target_chunk_duration_seconds": self.CHUNK_TARGET_DURATION_SECONDS,
            "max_chunk_duration_seconds": self.CHUNK_MAX_DURATION_SECONDS,
            "overlap_seconds": self.CHUNK_OVERLAP_SECONDS,
            "silence_noise": self.CHUNK_SILENCE_NOISE,
            "silence_duration_seconds": self.CHUNK_SILENCE_DURATION_SECONDS,
        }
        self.project_service.save_json_artifact(
            project_state,
            "transcription_chunking_config",
            os.path.join("analysis", "chunking_config.json"),
            transcription_config,
        )
        chunk_started = time.perf_counter()
        chunks = self.chunking_service.build_chunks(
            audio_path,
            chunk_dir,
            target_chunk_duration=self.CHUNK_TARGET_DURATION_SECONDS,
            max_chunk_duration=self.CHUNK_MAX_DURATION_SECONDS,
            overlap_seconds=self.CHUNK_OVERLAP_SECONDS,
            silence_noise=self.CHUNK_SILENCE_NOISE,
            silence_duration=self.CHUNK_SILENCE_DURATION_SECONDS,
        )
        chunk_elapsed = time.perf_counter() - chunk_started
        self.project_service.save_json_artifact(
            project_state,
            "transcription_chunks",
            os.path.join("analysis", "chunks.json"),
            [chunk.to_dict() for chunk in chunks],
        )
        asr_started = time.perf_counter()
        chunk_results = self.asr_merge_service.transcribe_chunks(
            chunks,
            whisper_adapter=self.engine_runtime.whisper,
            model_path=model_path,
            language=language,
            cache_dir=chunk_cache_dir,
            transcription_config=transcription_config,
        )
        asr_elapsed = time.perf_counter() - asr_started
        cache_hits = sum(1 for result in chunk_results if result.get("from_cache"))
        print(
            f"[ASR] Chunk cache: {cache_hits}/{len(chunk_results)} chunks reused from cache."
        )
        self.project_service.save_json_artifact(
            project_state,
            "transcript_chunk_raw",
            os.path.join("analysis", "transcript_chunk_raw.json"),
            [
                {
                    "chunk": result["chunk"].to_dict(),
                    "segments": result["segments"],
                }
                for result in chunk_results
            ],
        )
        merge_started = time.perf_counter()
        merged_segments = self.asr_merge_service.merge_chunk_results(chunk_results)
        merge_elapsed = time.perf_counter() - merge_started
        self.project_service.save_json_artifact(
            project_state,
            "transcript_merged",
            os.path.join("analysis", "transcript_merged.json"),
            merged_segments,
        )
        regroup_started = time.perf_counter()
        regrouped_segments = self.segment_regroup_service.regroup(merged_segments)
        regroup_elapsed = time.perf_counter() - regroup_started
        self.project_service.save_json_artifact(
            project_state,
            "transcript_regrouped",
            os.path.join("analysis", "transcript_regrouped.json"),
            regrouped_segments,
        )
        overall_elapsed = time.perf_counter() - overall_started
        print(
            "[ASR] Chunked transcription enabled: "
            f"{len(chunks)} chunks generated from long audio, "
            f"{len(merged_segments)} merged segments, "
            f"{len(regrouped_segments)} regrouped segments."
        )
        print(
            "[Timing] Chunked ASR: "
            f"chunking={chunk_elapsed:.2f}s, "
            f"asr={asr_elapsed:.2f}s, "
            f"merge={merge_elapsed:.2f}s, "
            f"regroup={regroup_elapsed:.2f}s, "
            f"total={overall_elapsed:.2f}s"
        )
        return regrouped_segments

    def run(
        self,
        video_path: str,
        *,
        source_language: str = "auto",
        target_language: str = "vi",
        mode: str = "subtitle",
        audio_handling_mode: str = "fast",
        translator_ai: bool = True,
        optimize_subtitles: bool = False,
        translator_style: str = "",
        whisper_model_name: str = "ggml-base.bin",
        step_callback=None,
    ) -> str:
        if step_callback: step_callback("prepare")
        workflow_started = time.perf_counter()
        whisper_model = os.path.join(self.workspace_root, "models", whisper_model_name)
        project_state = self.project_service.ensure_project(
            video_path,
            mode=mode,
            translator_ai=translator_ai,
            translator_style=translator_style,
            input_language=source_language,
            target_language=target_language,
        )
        project_state.set_setting("whisper_model", whisper_model)
        project_state.set_setting("audio_handling_mode", audio_handling_mode)
        self.project_service.save_project(project_state)

        audio_output_path = self.project_service.build_path(project_state, "source", "extracted_audio.wav")
        srt_original_path = self.project_service.build_path(project_state, "subtitle", "original.srt")
        srt_translated_path = self.project_service.build_path(project_state, "subtitle", "subtitle.srt")
        extraction_signature = self.project_service.build_extraction_signature(video_path)
        cached_extraction_signature = str(project_state.settings.get("extraction_signature", "") or "").strip()
        cached_extracted_audio = project_state.artifacts.get("extracted_audio", "")

        print("--- Step 1: Extracting audio ---")
        if step_callback: step_callback("extraction")
        extract_started = time.perf_counter()
        project_state.set_step_status("extract_audio", "running")
        self.project_service.save_project(project_state)
        reused_extraction = (
            cached_extraction_signature == extraction_signature
            and cached_extracted_audio
            and os.path.exists(cached_extracted_audio)
        )
        if reused_extraction:
            audio_output_path = cached_extracted_audio
            print(f"[Prepare Workflow] Reusing cached extracted audio: {audio_output_path}")
        else:
            if not self.engine_runtime.extract_audio(video_path, audio_output_path):
                project_state.set_step_status("extract_audio", "failed")
                self.project_service.save_project(project_state)
                raise RuntimeError("Audio extraction failed.")
            project_state.set_setting("extraction_signature", extraction_signature)
        extract_elapsed = time.perf_counter() - extract_started
        print(f"Success: Audio saved to {audio_output_path}")
        print(f"[Timing] Extract audio: {extract_elapsed:.2f}s")
        project_state.set_step_status("extract_audio", "done")
        project_state.set_artifact("extracted_audio", audio_output_path)
        self.project_service.save_project(project_state)

        working_audio_path = audio_output_path
        audio_mode_key = str(audio_handling_mode or "fast").strip().lower()
        print(f"[Audio Handling] Selected mode: {audio_mode_key}")
        if mode in ("voice", "both") and audio_mode_key == "clean":
            print("\n--- Step 1.5: Separating vocals/background ---")
            if step_callback: step_callback("separation")
            print("[Audio Handling] Clean Voice enabled: running Demucs stem separation before transcription.")
            separation_started = time.perf_counter()
            project_state.set_step_status("separate_audio", "running")
            self.project_service.save_project(project_state)
            separated_root = self.project_service.build_path(project_state, "audio", "separated")
            separation_signature = self.project_service.build_separation_signature(
                audio_output_path,
                audio_handling_mode=audio_mode_key,
            )
            cached_separation_signature = str(project_state.settings.get("separation_signature", "") or "").strip()
            cached_vocal_path = project_state.artifacts.get("vocals", "")
            cached_music_path = project_state.artifacts.get("music", "")
            if (
                cached_separation_signature == separation_signature
                and cached_vocal_path
                and cached_music_path
                and os.path.exists(cached_vocal_path)
                and os.path.exists(cached_music_path)
            ):
                vocal_path, music_path = cached_vocal_path, cached_music_path
                print("[Prepare Workflow] Reusing cached separated stems.")
            else:
                try:
                    vocal_path, music_path = self.engine_runtime.separate_vocals(audio_output_path, separated_root)
                except Exception as exc:
                    project_state.set_step_status("separate_audio", "failed")
                    self.project_service.save_project(project_state)
                    raise RuntimeError(f"Audio separation failed: {exc}") from exc
                if not vocal_path or not music_path:
                    project_state.set_step_status("separate_audio", "failed")
                    self.project_service.save_project(project_state)
                    raise RuntimeError("Audio separation failed: Demucs did not return vocals/background stems.")
                project_state.set_setting("separation_signature", separation_signature)
            separation_elapsed = time.perf_counter() - separation_started
            working_audio_path = vocal_path
            print(f"[Audio Handling] Using separated vocals for Whisper: {working_audio_path}")
            print(f"[Audio Handling] Background music stem ready: {music_path}")
            print(f"[Timing] Demucs separation: {separation_elapsed:.2f}s")
            project_state.set_step_status("separate_audio", "done")
            project_state.set_artifact("vocals", vocal_path)
            project_state.set_artifact("music", music_path)
            self.project_service.save_project(project_state)
        else:
            if mode in ("voice", "both"):
                print("[Audio Handling] Fast Mode enabled: skipping Demucs and transcribing directly from extracted audio.")
            else:
                print("[Audio Handling] Subtitle mode: Demucs is not needed.")
            print(f"[Audio Handling] Using extracted audio for Whisper: {working_audio_path}")
            project_state.set_step_status("separate_audio", "skipped")
            self.project_service.save_project(project_state)

        print("\n--- Step 2: Transcribing audio (Whisper) ---")
        if step_callback: step_callback("transcription")
        transcribe_started = time.perf_counter()
        project_state.set_step_status("transcribe", "running")
        self.project_service.save_project(project_state)
        transcription_signature = self.project_service.build_transcription_signature(
            working_audio_path,
            whisper_model=whisper_model,
            source_language=source_language,
            audio_handling_mode=audio_mode_key,
        )
        cached_transcription_signature = str(project_state.settings.get("transcription_signature", "") or "").strip()
        cached_transcript_path = project_state.artifacts.get("transcript_segments", "")
        reused_transcript = (
            cached_transcription_signature == transcription_signature
            and cached_transcript_path
            and os.path.exists(cached_transcript_path)
        )
        if reused_transcript:
            segment_models = self.project_service.load_segment_artifact(project_state, "transcript_segments")
            raw_segments = [segment.to_original_subtitle_dict() for segment in segment_models]
            print("[Prepare Workflow] Reusing cached Whisper transcript. Generate did not transcribe again.")
        else:
            audio_duration = self.chunking_service.probe_wav_duration(working_audio_path)
            print(f"[ASR] Working audio duration: {audio_duration:.2f}s")
            if is_remote_profile():
                print("[ASR] Remote API mode: using single-pass transcription and sending full working audio to the PC server.")
                raw_segments = self.engine_runtime.transcribe_audio(
                    working_audio_path,
                    whisper_model,
                    language=source_language,
                )
            elif audio_duration >= self.CHUNKED_ASR_MIN_DURATION_SECONDS:
                raw_segments = self._transcribe_long_audio_chunked(
                    audio_path=working_audio_path,
                    project_state=project_state,
                    model_path=whisper_model,
                    language=source_language,
                )
            else:
                print("[ASR] Using standard single-pass transcription for short audio.")
                raw_segments = self.engine_runtime.transcribe_audio(
                    working_audio_path,
                    whisper_model,
                    language=source_language,
                )
            if not raw_segments:
                project_state.set_step_status("transcribe", "failed")
                self.project_service.save_project(project_state)
                raise RuntimeError("Transcription failed.")
            segment_models = self.segment_service.transcript_dicts_to_models(raw_segments)
            project_state.set_setting("transcription_signature", transcription_signature)
        transcribe_elapsed = time.perf_counter() - transcribe_started
        print(f"Success: Generated {len(segment_models)} segments.")
        print(f"[Timing] Transcribe step: {transcribe_elapsed:.2f}s")
        self.project_service.save_json_artifact(
            project_state,
            "transcript_raw",
            os.path.join("analysis", "transcript_raw.json"),
            raw_segments,
        )
        self.project_service.save_segment_artifact(
            project_state,
            "transcript_segments",
            os.path.join("analysis", "transcript_segments.json"),
            segment_models,
        )
        project_state.set_step_status("transcribe", "done")
        self.project_service.save_project(project_state)

        print("\n--- Step 3: Generating Original Subtitle ---")
        subtitle_started = time.perf_counter()
        project_state.set_step_status("build_subtitle", "running")
        self.project_service.save_project(project_state)
        self.engine_runtime.generate_srt(
            [segment.to_original_subtitle_dict() for segment in segment_models],
            srt_original_path,
        )
        subtitle_elapsed = time.perf_counter() - subtitle_started
        print(f"[Timing] Build original subtitle: {subtitle_elapsed:.2f}s")
        project_state.set_step_status("build_subtitle", "done")
        project_state.set_artifact("subtitle_original_srt", srt_original_path)
        self.project_service.save_project(project_state)

        print("\n--- Step 4: Translating to Vietnamese ---")
        if step_callback: step_callback("translation")
        translate_started = time.perf_counter()
        project_state.set_step_status("translate_raw", "running")
        self.project_service.save_project(project_state)
        translation_signature = self.project_service.build_translation_signature(
            [segment.to_original_subtitle_dict() for segment in segment_models],
            src_lang=source_language,
            target_lang=target_language,
            enable_polish=translator_ai,
            optimize_subtitles=optimize_subtitles,
            style_instruction=project_state.translator_style,
        )
        cached_translation_path = project_state.artifacts.get("translation_final", "")
        cached_translation_signature = str(project_state.settings.get("translation_signature", "") or "").strip()
        try:
            if cached_translation_signature == translation_signature and cached_translation_path and os.path.exists(cached_translation_path):
                cached_models = self.project_service.load_segment_artifact(project_state, "translation_final")
                if cached_models:
                    segment_models = cached_models
                    print("[Prepare Workflow] Reusing cached Vietnamese subtitles. Generate did not call AI again.")
                else:
                    translated_segments = self.engine_runtime.translate_segments(
                        raw_segments,
                        src_lang=source_language,
                        enable_polish=translator_ai,
                        optimize_subtitles=optimize_subtitles,
                        style_instruction=project_state.translator_style,
                    )
                    segment_models = self.segment_service.apply_translations(segment_models, translated_segments)
                    self.project_service.save_segment_artifact(
                        project_state,
                        "translation_final",
                        os.path.join("translation", "translation_final.json"),
                        segment_models,
                    )
            else:
                translated_segments = self.engine_runtime.translate_segments(
                    raw_segments,
                    src_lang=source_language,
                    enable_polish=translator_ai,
                    optimize_subtitles=optimize_subtitles,
                    style_instruction=project_state.translator_style,
                )
                segment_models = self.segment_service.apply_translations(segment_models, translated_segments)
                self.project_service.save_segment_artifact(
                    project_state,
                    "translation_final",
                    os.path.join("translation", "translation_final.json"),
                    segment_models,
                )
            project_state.set_setting("translation_signature", translation_signature)
            project_state.set_step_status("translate_raw", "done")
            project_state.set_step_status("refine_translation", "done" if optimize_subtitles else "skipped")
            self.project_service.save_project(project_state)
        except Exception as e:
            print(f"[AI Translation] Error: {e}")
            project_state.set_step_status("translate_raw", "failed")
            project_state.set_step_status("refine_translation", "skipped")
            self.project_service.save_project(project_state)
            raise
        translate_elapsed = time.perf_counter() - translate_started
        print(f"[Timing] Translate/refine: {translate_elapsed:.2f}s")

        print("\n--- Step 5: Generating Vietnamese Subtitle ---")
        translated_subtitle_started = time.perf_counter()
        self.engine_runtime.generate_srt(segment_models, srt_translated_path)
        translated_subtitle_elapsed = time.perf_counter() - translated_subtitle_started
        project_state.set_artifact("subtitle_translated_srt", srt_translated_path)
        self.project_service.save_project(project_state)
        workflow_elapsed = time.perf_counter() - workflow_started
        print(f"[Timing] Build translated subtitle: {translated_subtitle_elapsed:.2f}s")
        print(f"[Timing] Prepare workflow total: {workflow_elapsed:.2f}s")
        print(f"\nCOMPLETED! Project saved at: {project_state.project_root}")
        return project_state
