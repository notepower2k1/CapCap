import os
import concurrent.futures
import hashlib
import re
import subprocess
import time

from runtime_paths import bin_path, models_path, subprocess_hidden_kwargs, subprocess_text_kwargs
from runtime_profile import is_remote_profile
from services import ChunkingService, EngineRuntime, ProjectService, SegmentRegroupService, SegmentService
from services.resource_download_service import ResourceDownloadService


class PrepareWorkflow:
    CHUNKED_ASR_MIN_DURATION_SECONDS = 90.0
    CHUNK_TARGET_DURATION_SECONDS = 12.0
    CHUNK_MAX_DURATION_SECONDS = 20.0
    CHUNK_OVERLAP_SECONDS = 0.5
    CHUNK_SILENCE_NOISE = "-35dB"
    CHUNK_SILENCE_DURATION_SECONDS = 0.35
    # ASR-only gain control: quiet speech is easy for VAD to misclassify as
    # silence.  Keep this deliberately conservative so normal recordings are
    # untouched and background noise is not amplified excessively.
    ASR_NORMALIZE_TRIGGER_DB = -35.0
    ASR_NORMALIZE_TARGET_DB = -25.0
    ASR_NORMALIZE_MAX_GAIN_DB = 12.0
    ASR_NORMALIZE_PEAK_DB = -2.0

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.project_service = ProjectService(workspace_root)
        self.segment_service = SegmentService()
        self.chunking_service = ChunkingService(workspace_root)
        self.segment_regroup_service = SegmentRegroupService()
        self.engine_runtime = EngineRuntime()

    def _prepare_asr_working_audio(self, audio_path: str, project_state) -> str:
        """Return a cached, gain-adjusted copy only when ASR input is quiet.

        The source/extracted audio is never modified.  This happens before
        both chunk silence detection and model VAD, which is where soft
        dialogue could otherwise be discarded.
        """
        source = str(audio_path or "").strip()
        if not source or not os.path.exists(source):
            return source
        try:
            stat = os.stat(source)
            fingerprint = hashlib.sha256(
                f"v1|{os.path.abspath(source)}|{stat.st_size}|{stat.st_mtime_ns}|"
                f"{self.ASR_NORMALIZE_TRIGGER_DB}|{self.ASR_NORMALIZE_TARGET_DB}|"
                f"{self.ASR_NORMALIZE_MAX_GAIN_DB}|{self.ASR_NORMALIZE_PEAK_DB}".encode("utf-8")
            ).hexdigest()
        except OSError:
            return source

        cached = project_state.settings.get("asr_audio_normalization", {}) or {}
        if isinstance(cached, dict) and cached.get("signature") == fingerprint:
            cached_path = str(cached.get("path") or "")
            if bool(cached.get("applied")) and cached_path and os.path.exists(cached_path):
                print(
                    "[ASR Audio] Reusing cached normalized audio "
                    f"(gain={float(cached.get('gain_db', 0.0)):+.1f} dB)."
                )
                return cached_path
            if not bool(cached.get("applied")):
                print("[ASR Audio] Reusing level analysis: normalization not needed.")
                return source

        ffmpeg = str(bin_path("ffmpeg", "ffmpeg.exe"))
        if not os.path.exists(ffmpeg):
            print("[ASR Audio] FFmpeg unavailable; skipping level normalization.")
            return source
        try:
            probe = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", source, "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True,
                check=False, **subprocess_text_kwargs(),
            )
            output = f"{probe.stdout}\n{probe.stderr}"
            mean_match = re.search(r"mean_volume:\s*([-+]?\d+(?:\.\d+)?)\s*dB", output)
            max_match = re.search(r"max_volume:\s*([-+]?\d+(?:\.\d+)?)\s*dB", output)
            if not mean_match:
                print("[ASR Audio] Could not measure audio level; using source audio.")
                return source
            mean_db = float(mean_match.group(1))
            peak_db = float(max_match.group(1)) if max_match else -99.0
        except Exception as exc:
            print(f"[ASR Audio] Level analysis failed; using source audio: {exc}")
            return source

        gain_db = 0.0
        if mean_db < self.ASR_NORMALIZE_TRIGGER_DB:
            target_gain = self.ASR_NORMALIZE_TARGET_DB - mean_db
            peak_limited_gain = self.ASR_NORMALIZE_PEAK_DB - peak_db
            gain_db = max(0.0, min(target_gain, peak_limited_gain, self.ASR_NORMALIZE_MAX_GAIN_DB))

        profile = {
            "signature": fingerprint,
            "source": source,
            "mean_db": round(mean_db, 2),
            "peak_db": round(peak_db, 2),
            "gain_db": round(gain_db, 2),
            "applied": gain_db >= 0.5,
            "path": "",
        }
        if gain_db < 0.5:
            print(
                f"[ASR Audio] Level: mean={mean_db:.1f} dB, peak={peak_db:.1f} dB; "
                "normalization not needed."
            )
        else:
            normalized_path = self.project_service.build_path(project_state, "audio", "asr_normalized.wav")
            try:
                subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-y", "-i", source,
                        "-af", f"volume={gain_db:.2f}dB",
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", normalized_path,
                    ],
                    capture_output=True,
                    check=True, **subprocess_text_kwargs(),
                )
                profile["path"] = normalized_path
                print(
                    f"[ASR Audio] Quiet input detected: mean={mean_db:.1f} dB, peak={peak_db:.1f} dB; "
                    f"applying +{gain_db:.1f} dB before ASR."
                )
            except Exception as exc:
                profile["applied"] = False
                profile["gain_db"] = 0.0
                print(f"[ASR Audio] Normalization failed; using source audio: {exc}")

        project_state.set_setting("asr_audio_normalization", profile)
        self.project_service.save_json_artifact(
            project_state,
            "asr_audio_profile",
            os.path.join("analysis", "asr_audio_profile.json"),
            profile,
        )
        self.project_service.save_project(project_state)
        return str(profile["path"] or source)

    def _transcribe_long_audio_chunked(self, *, audio_path: str, project_state, model_path: str, language: str, on_chunk_ready=None):
        overall_started = time.perf_counter()
        chunk_dir = self.project_service.build_path(project_state, "audio", "chunks")
        chunk_cache_dir = self.project_service.build_path(project_state, "analysis", "chunk_results")
        transcription_config = {
            "vad_mode": "silencedetect",
            # Each item is already a short, speech-focused chunk.  Batched
            # inference only has one short item to work on here and can alter
            # Faster-Whisper's VAD segmentation, so use its stable standard
            # path instead.  This also invalidates any cache produced by the
            # old experimental batched-per-chunk behavior.
            "inference_mode": "standard_per_chunk_v2",
            "target_chunk_duration_seconds": self.CHUNK_TARGET_DURATION_SECONDS,
            "max_chunk_duration_seconds": self.CHUNK_MAX_DURATION_SECONDS,
            "overlap_seconds": self.CHUNK_OVERLAP_SECONDS,
            "silence_noise": self.CHUNK_SILENCE_NOISE,
            "silence_duration_seconds": self.CHUNK_SILENCE_DURATION_SECONDS,
            "min_speech_duration_seconds": 0.05,
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
            min_speech_duration=0.05,
        )
        chunk_elapsed = time.perf_counter() - chunk_started
        self.project_service.save_json_artifact(
            project_state,
            "transcription_chunks",
            os.path.join("analysis", "chunks.json"),
            [chunk.to_dict() for chunk in chunks],
        )
        asr_started = time.perf_counter()
        from services import AsrMergeService
        asr_merge_service = AsrMergeService()
        chunk_results = asr_merge_service.transcribe_chunks(
            chunks,
            whisper_adapter=self.engine_runtime.whisper,
            model_path=model_path,
            language=language,
            cache_dir=chunk_cache_dir,
            transcription_config=transcription_config,
            ordered_callback=on_chunk_ready,
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
        merged_segments = asr_merge_service.merge_chunk_results(chunk_results)
        merge_elapsed = time.perf_counter() - merge_started
        self.project_service.save_json_artifact(
            project_state,
            "transcript_merged",
            os.path.join("analysis", "transcript_merged.json"),
            merged_segments,
        )
        regroup_started = time.perf_counter()
        regrouped_segments = self.segment_regroup_service.regroup(
            merged_segments,
            max_gap_seconds=0.25,
            max_duration_seconds=5.0,
        )
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

    def _should_enable_asr_translate_streaming(self, *, translator_ai: bool) -> bool:
        if not bool(translator_ai):
            return True
        provider = str(os.getenv("OPENAI_PROVIDER") or "google").strip().lower()
        return provider == "openai"

    @staticmethod
    def _speaker_diarization_signature(audio_path: str, diarization_key: str = "") -> str:
        try:
            stat = os.stat(audio_path)
            source = f"{os.path.abspath(audio_path)}|{stat.st_size}|{stat.st_mtime_ns}|{diarization_key}"
        except OSError:
            source = f"{os.path.abspath(audio_path)}|{diarization_key}"
        return hashlib.sha1(source.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _should_run_diarization_parallel(*, is_sensevoice: bool) -> bool:
        """Only overlap CPU diarization with ASR that does not consume CPU."""
        if is_sensevoice:
            return False
        if is_remote_profile():
            return True
        return str(os.getenv("CAPCAP_DEVICE", "") or "").strip().lower() == "cuda"

    @staticmethod
    def _apply_speaker_labels(segments: list[dict], speaker_turns: list[dict]) -> list[dict]:
        """Assign each transcript cue to the speaker with the most overlap."""
        if not speaker_turns:
            return [dict(segment) for segment in (segments or [])]
        labeled = []
        for segment in segments or []:
            item = dict(segment)
            try:
                start = float(item.get("start", 0.0))
                end = max(start, float(item.get("end", start)))
            except (TypeError, ValueError):
                labeled.append(item)
                continue
            best_speaker = ""
            best_overlap = 0.0
            for turn in speaker_turns:
                try:
                    overlap = max(0.0, min(end, float(turn.get("end", 0.0))) - max(start, float(turn.get("start", 0.0))))
                except (TypeError, ValueError):
                    continue
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = str(turn.get("speaker", "") or "").strip()
            if best_speaker:
                item["speaker"] = best_speaker
            labeled.append(item)
        return labeled

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
        whisper_model_name: str = "ggml-medium.bin",
        transcription_engine: str = "whisper",
        speaker_diarization: bool = False,
        speaker_diarization_num_speakers: int = -1,
        skip_translation: bool = False,
        prefetch_voice_name: str = "",
        prefetch_voice_speed: float = 1.0,
        step_callback=None,
    ) -> str:
        optimize_subtitles = False
        if step_callback: step_callback("prepare")
        workflow_started = time.perf_counter()
        is_ocr = transcription_engine == "ocr"
        speaker_diarization = bool(speaker_diarization and not is_ocr)
        try:
            speaker_diarization_num_speakers = int(speaker_diarization_num_speakers)
        except (TypeError, ValueError):
            speaker_diarization_num_speakers = -1
        if speaker_diarization_num_speakers < 2:
            speaker_diarization_num_speakers = -1
        is_sensevoice = transcription_engine == "sensevoice"

        # The GUI runs the same checks before launching the worker.  Repeat
        # them here because a frozen worker can have a different import or
        # resource-path failure.  This turns an otherwise opaque HTTP 500
        # into the component/folder that actually needs attention.
        if not is_remote_profile():
            resource_service = ResourceDownloadService(self.workspace_root)
            readiness_issues = resource_service.validate_pipeline_runtime()
            if is_ocr:
                readiness_issues.extend(resource_service.validate_ocr_runtime())
            elif is_sensevoice:
                readiness_issues.extend(resource_service.validate_sensevoice_runtime())
            if prefetch_voice_name:
                readiness_issues.extend(
                    resource_service.validate_piper_voice_runtime(prefetch_voice_name)
                )
            if readiness_issues:
                details = "\n".join(f"- {detail}" for _code, detail in readiness_issues)
                raise RuntimeError(
                    "CapCap startup readiness check failed. Resolve the following before retrying:\n"
                    f"{details}"
                )

        sensevoice_model_dir = ""
        if is_sensevoice:
            # Development resources live beside the project; PyInstaller
            # bundles the ready-to-use SenseVoice model under _internal.
            # Never construct workspace_root/models directly here or the
            # frozen worker will miss its bundled model at Transcript time.
            sensevoice_model_dir = models_path("sensevoice")
        # Faster-Whisper accepts a model name (for example ``base``) or a
        # resolved local model directory. Do not prepend ``models/`` here:
        # doing so turns a selected Base/Small model into an invalid path and
        # makes the loader fall back to the legacy Medium alias.
        whisper_model = str(whisper_model_name or "medium").strip() if not is_sensevoice else ""
        raw_segments = []
        segment_models = []
        streamed_translation_executor = None
        streamed_translation_futures = []
        streamed_translation_enabled = False
        project_state = self.project_service.ensure_project(
            video_path,
            mode=mode,
            translator_ai=translator_ai,
            translator_style=translator_style,
            input_language=source_language,
            target_language=target_language,
        )
        if is_sensevoice:
            project_state.set_setting("sensevoice_model", sensevoice_model_dir)
        else:
            project_state.set_setting("whisper_model", whisper_model)
        project_state.set_setting("audio_handling_mode", audio_handling_mode)
        project_state.set_setting("transcription_engine", transcription_engine)
        project_state.set_setting("speaker_diarization_enabled", speaker_diarization)
        project_state.set_setting("speaker_diarization_num_speakers", speaker_diarization_num_speakers)
        self.project_service.save_project(project_state)

        if is_ocr:
            print("--- Step 1: Extracting background audio ---")
            if step_callback: step_callback("extract_audio")
            project_state.set_step_status("extract_audio", "running")
            self.project_service.save_project(project_state)

            audio_output_path = self.project_service.build_path(project_state, "source", "extracted_audio.wav")
            os.makedirs(os.path.dirname(audio_output_path), exist_ok=True)
            ffmpeg_bin = os.path.join(bin_path(), "ffmpeg", "ffmpeg.exe")
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", audio_output_path],
                capture_output=True, **subprocess_hidden_kwargs(),
            )
            project_state.set_artifact("extracted_audio", audio_output_path)
            project_state.set_step_status("extract_audio", "completed")
            self.project_service.save_project(project_state)

            print("--- Step 2: Extracting subtitles via OCR ---")
            if step_callback: step_callback("transcription")
            transcribe_started = time.perf_counter()
            project_state.set_step_status("extract_audio", "skipped")
            project_state.set_step_status("transcribe", "running")
            self.project_service.save_project(project_state)
            ocr_region = (os.getenv("OCR_SUBTITLE_REGION") or "bottom").strip().lower()
            ocr_signature = self.project_service.build_ocr_transcription_signature(
                video_path, region=ocr_region,
            )
            cached_ocr_signature = str(project_state.settings.get("ocr_transcription_signature", "") or "")
            cached_raw_path = project_state.artifacts.get("transcript_raw", "")
            cached_segment_path = project_state.artifacts.get("transcript_segments", "")
            reused_ocr = (
                cached_ocr_signature == ocr_signature
                and cached_raw_path and cached_segment_path
                and os.path.exists(cached_raw_path)
                and os.path.exists(cached_segment_path)
            )
            if reused_ocr:
                raw_segments = self.project_service.load_json_artifact(project_state, "transcript_raw", default=[])
                segment_models = self.project_service.load_segment_artifact(project_state, "transcript_segments")
                if not raw_segments and segment_models:
                    raw_segments = [segment.to_original_subtitle_dict() for segment in segment_models]
                print("[Prepare Workflow] Reusing cached OCR transcript. Generate did not scan the video again.")
            else:
                raw_segments = self.engine_runtime.transcribe_video_ocr(video_path, region=ocr_region)
                if not raw_segments:
                    project_state.set_step_status("transcribe", "failed")
                    self.project_service.save_project(project_state)
                    message = (
                        f"No readable text was detected in the OCR subtitle region ({ocr_region}). "
                        "This video may have text outside that region. In Settings, change Subtitle position "
                        "to Full frame or Top, then run Transcript again."
                    )
                    print(f"[OCR] {message}")
                    raise RuntimeError(message)
                segment_models = self.segment_service.transcript_dicts_to_models(raw_segments)
                project_state.set_setting("ocr_transcription_signature", ocr_signature)
            transcribe_elapsed = time.perf_counter() - transcribe_started
            print(f"Success: Generated {len(segment_models)} segments via OCR.")
            print(f"[Timing] OCR step: {transcribe_elapsed:.2f}s")
            if not reused_ocr:
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
            project_state.set_step_status("transcribe", "completed")
            project_state.set_setting("transcription_engine", "ocr")
            self.project_service.save_project(project_state)

        srt_original_path = self.project_service.build_path(project_state, "subtitle", "original.srt")
        srt_translated_path = self.project_service.build_path(project_state, "subtitle", "subtitle.srt")

        if not is_ocr:
            audio_output_path = self.project_service.build_path(project_state, "source", "extracted_audio.wav")

            # srt paths defined above
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
                # Post-process vocals: denoise + loudness normalize for better transcription
                processed_vocal_path = os.path.join(
                    os.path.dirname(vocal_path), "vocals_enhanced.wav"
                )
                try:
                    ffmpeg_cmd = [
                        str(bin_path("ffmpeg", "ffmpeg.exe")),
                        "-i", vocal_path,
                        "-af", "afftdn,loudnorm=I=-16:LRA=11:TP=-1.5",
                        "-ar", "16000",
                        "-ac", "1",
                        "-y",
                        processed_vocal_path,
                    ]
                    subprocess.run(
                        ffmpeg_cmd, check=True, capture_output=True,
                        **subprocess_hidden_kwargs(),
                    )
                    working_audio_path = processed_vocal_path
                    print(f"[Audio Handling] Enhanced vocals (denoise + loudnorm): {processed_vocal_path}")
                except Exception as e:
                    print(f"[Audio Handling] Enhancement skipped: {e}. Using raw vocals.")
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

            # Run level analysis once and only create a separate ASR input
            # when the working audio is genuinely quiet.  The resulting path
            # participates in the transcription signature below, so cached
            # transcript/chunk results remain correct.
            working_audio_path = self._prepare_asr_working_audio(
                working_audio_path, project_state
            )

            speaker_turns: list[dict] = []
            diarization_future = None
            diarization_executor = None
            diarization_signature = ""
            diarization_started = 0.0
            if speaker_diarization:
                print("\n--- Step 1.75: Detecting speakers (Sherpa-ONNX) ---")
                if step_callback:
                    step_callback("diarization")
                project_state.set_step_status("diarize", "running")
                self.project_service.save_project(project_state)
                from services import SpeakerDiarizationService
                diarization_signature = self._speaker_diarization_signature(
                    audio_output_path,
                    SpeakerDiarizationService.cache_key(
                        num_speakers=speaker_diarization_num_speakers
                    ),
                )
                cached_signature = str(project_state.settings.get("speaker_diarization_signature", "") or "")
                cached_turns_path = project_state.artifacts.get("speaker_diarization", "")
                if (
                    cached_signature == diarization_signature
                    and cached_turns_path
                    and os.path.exists(cached_turns_path)
                ):
                    speaker_turns = self.project_service.load_json_artifact(
                        project_state, "speaker_diarization", default=[]
                    ) or []
                    print(f"[Diarization] Reusing cached speaker turns: {len(speaker_turns)}")
                    project_state.set_step_status("diarize", "done")
                    self.project_service.save_project(project_state)
                else:
                    diarization_started = time.perf_counter()
                    # GPU Whisper and remote ASR do not compete for the CPU
                    # used by Sherpa diarization.  Keep CPU Whisper and
                    # SenseVoice sequential so they remain responsive.
                    use_parallel_diarization = self._should_run_diarization_parallel(
                        is_sensevoice=is_sensevoice
                    )
                    if use_parallel_diarization:
                        execution_mode = "remote ASR" if is_remote_profile() else "GPU Whisper"
                        cluster_label = (
                            f"fixed at {speaker_diarization_num_speakers} speaker(s)"
                            if speaker_diarization_num_speakers >= 2 else "auto clustering"
                        )
                        print(f"[Diarization] Running in parallel with {execution_mode} ({cluster_label}).")
                        diarization_executor = concurrent.futures.ThreadPoolExecutor(
                            max_workers=1,
                            thread_name_prefix="speaker-diarization",
                        )
                        diarization_future = diarization_executor.submit(
                            SpeakerDiarizationService().diarize,
                            audio_output_path,
                            num_speakers=speaker_diarization_num_speakers,
                        )
                    else:
                        cluster_label = (
                            f"fixed at {speaker_diarization_num_speakers} speaker(s)"
                            if speaker_diarization_num_speakers >= 2 else "auto clustering"
                        )
                        print(
                            "[Diarization] Running sequentially to avoid CPU contention with ASR "
                            f"({cluster_label})."
                        )
                        speaker_turns = SpeakerDiarizationService().diarize(
                            audio_output_path,
                            num_speakers=speaker_diarization_num_speakers,
                        )
                        self.project_service.save_json_artifact(
                            project_state,
                            "speaker_diarization",
                            os.path.join("analysis", "speaker_diarization.json"),
                            speaker_turns,
                        )
                        project_state.set_setting("speaker_diarization_signature", diarization_signature)
                        print(
                            f"[Diarization] Detected {len(speaker_turns)} speaker turn(s) "
                            f"in {time.perf_counter() - diarization_started:.2f}s"
                        )
                        project_state.set_step_status("diarize", "done")
                        self.project_service.save_project(project_state)
            else:
                project_state.set_step_status("diarize", "skipped")
                self.project_service.save_project(project_state)

            engine_name = "SenseVoice" if is_sensevoice else "Whisper"
            print(f"\n--- Step 2: Transcribing audio ({engine_name}) ---")
            if not is_sensevoice:
                print(f"[Whisper] Requested model: {whisper_model}")
            if step_callback: step_callback("transcription")
            transcribe_started = time.perf_counter()
            project_state.set_step_status("transcribe", "running")
            self.project_service.save_project(project_state)
            transcription_signature = self.project_service.build_transcription_signature(
                working_audio_path,
                whisper_model=whisper_model,
                source_language=source_language,
                # Bump the signature when the chunk merge algorithm changes
                # so a project cannot reuse an aggressively regrouped
                # transcript. Raw per-chunk ASR cache entries remain valid.
                audio_handling_mode=f"{audio_mode_key}|asr-merge-v4",
            )
            cached_transcription_signature = str(project_state.settings.get("transcription_signature", "") or "").strip()
            cached_transcript_path = project_state.artifacts.get("transcript_segments", "")
            reused_transcript = (
                cached_transcription_signature == transcription_signature
                and cached_transcript_path
                and os.path.exists(cached_transcript_path)
            )
            has_imported_transcript = (
                not cached_transcription_signature
                and cached_transcript_path
                and os.path.exists(cached_transcript_path)
            )
            if reused_transcript or has_imported_transcript:
                segment_models = self.project_service.load_segment_artifact(project_state, "transcript_segments")
                raw_segments = self.project_service.load_json_artifact(project_state, "transcript_raw", default=[])
                if not raw_segments and segment_models:
                    raw_segments = [segment.to_original_subtitle_dict() for segment in segment_models]
                if has_imported_transcript:
                    print(f"[Prepare Workflow] Using imported transcript segments. Skipping transcription.")
                else:
                    print(f"[Prepare Workflow] Reusing cached {engine_name} transcript. Generate did not transcribe again.")
            else:
                audio_duration = self.chunking_service.probe_wav_duration(working_audio_path)
                print(f"[ASR] Working audio duration: {audio_duration:.2f}s")
                if is_sensevoice:
                    print("[ASR] Using SenseVoice single-pass transcription with Silero VAD.")
                    try:
                        import sherpa_onnx
                    except ImportError:
                        raise RuntimeError("sherpa-onnx is not installed. Run: pip install sherpa-onnx")
                    raw_segments = self.engine_runtime.transcribe_audio_sensevoice(
                        working_audio_path,
                        sensevoice_model_dir,
                        language=source_language,
                    )
                elif is_remote_profile():
                    print("[ASR] Remote API mode: using single-pass transcription and sending full working audio to the PC server.")
                    raw_segments = self.engine_runtime.transcribe_audio(
                        working_audio_path,
                        whisper_model,
                        language=source_language,
                    )
                elif audio_duration >= self.CHUNKED_ASR_MIN_DURATION_SECONDS or (audio_mode_key == "clean" and audio_duration >= 30.0):
                    print("[ASR] Using chunked transcription (VAD silence detection).")
                    if (
                        not skip_translation
                        and self._should_enable_asr_translate_streaming(translator_ai=translator_ai)
                    ):
                        streamed_translation_enabled = True
                        print(
                            "[Prepare Workflow] ASR->Translate overlap enabled for chunked Whisper "
                            f"(translator_ai={bool(translator_ai)}, provider={str(os.getenv('OPENAI_PROVIDER') or 'google').strip().lower()})."
                        )
                        from services import AsrMergeService
                        asr_merge_service = AsrMergeService()
                        streamed_translation_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                        pending_stream_segments = []
                        emitted_stream_count = 0

                        def _submit_stream_batch(batch_start: int, batch_segments: list[dict]) -> None:
                            if not batch_segments:
                                return
                            print(
                                "[Prepare Workflow] Queue ASR->Translate batch: "
                                f"start={batch_start}, count={len(batch_segments)}"
                            )
                            future = streamed_translation_executor.submit(
                                self.engine_runtime.translate_segments,
                                list(batch_segments),
                                src_lang=source_language,
                                target_lang=target_language,
                                enable_polish=translator_ai,
                                optimize_subtitles=optimize_subtitles,
                                style_instruction=project_state.translator_style,
                            )
                            streamed_translation_futures.append((int(batch_start), future))

                        def _flush_pending_stream_segments(force: bool = False) -> None:
                            nonlocal pending_stream_segments, emitted_stream_count
                            batch_size = 12
                            while len(pending_stream_segments) >= batch_size or (force and pending_stream_segments):
                                count = batch_size if len(pending_stream_segments) >= batch_size and not force else min(batch_size, len(pending_stream_segments))
                                batch = [dict(seg) for seg in pending_stream_segments[:count]]
                                pending_stream_segments = pending_stream_segments[count:]
                                _submit_stream_batch(emitted_stream_count, batch)
                                emitted_stream_count += len(batch)

                        seen_chunk_results = []
                        emitted_stable_count = 0

                        def _on_chunk_ready(chunk_result: dict) -> None:
                            nonlocal emitted_stable_count, pending_stream_segments
                            seen_chunk_results.append(chunk_result)
                            merged_partial = asr_merge_service.merge_chunk_results(seen_chunk_results)
                            regrouped_partial = self.segment_regroup_service.regroup(
                                merged_partial,
                                max_gap_seconds=0.25,
                                max_duration_seconds=5.0,
                            )
                            stable_limit = max(0, len(regrouped_partial) - 2)
                            if stable_limit <= emitted_stable_count:
                                return
                            new_stable = regrouped_partial[emitted_stable_count:stable_limit]
                            pending_stream_segments.extend([dict(seg) for seg in new_stable])
                            emitted_stable_count = stable_limit
                            _flush_pending_stream_segments(force=False)
                    else:
                        _on_chunk_ready = None
                    raw_segments = self._transcribe_long_audio_chunked(
                        audio_path=working_audio_path,
                        project_state=project_state,
                        model_path=whisper_model,
                        language=source_language,
                        on_chunk_ready=_on_chunk_ready if streamed_translation_enabled else None,
                    )
                    if streamed_translation_enabled:
                        remaining_segments = raw_segments[emitted_stable_count:]
                        if remaining_segments:
                            pending_stream_segments.extend([dict(seg) for seg in remaining_segments])
                        _flush_pending_stream_segments(force=True)
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
            if diarization_future is not None:
                try:
                    print("[Diarization] Waiting for parallel speaker detection to finish...")
                    speaker_turns = diarization_future.result()
                    self.project_service.save_json_artifact(
                        project_state,
                        "speaker_diarization",
                        os.path.join("analysis", "speaker_diarization.json"),
                        speaker_turns,
                    )
                    project_state.set_setting("speaker_diarization_signature", diarization_signature)
                    print(
                        f"[Diarization] Detected {len(speaker_turns)} speaker turn(s) "
                        f"in {time.perf_counter() - diarization_started:.2f}s (parallel)"
                    )
                    project_state.set_step_status("diarize", "done")
                    self.project_service.save_project(project_state)
                except Exception:
                    project_state.set_step_status("diarize", "failed")
                    self.project_service.save_project(project_state)
                    raise
                finally:
                    diarization_executor.shutdown(wait=True)
            if speaker_turns:
                raw_segments = self._apply_speaker_labels(raw_segments, speaker_turns)
                segment_models = self.segment_service.transcript_dicts_to_models(raw_segments)
                labeled_count = sum(
                    1 for segment in raw_segments if str(segment.get("speaker", "") or "").strip()
                )
                speaker_count = len({
                    str(segment.get("speaker", "") or "").strip()
                    for segment in raw_segments
                    if str(segment.get("speaker", "") or "").strip()
                })
                print(
                    f"[Diarization] Applied speaker labels to {labeled_count}/{len(raw_segments)} "
                    f"transcript segment(s) across {speaker_count} detected speaker(s)."
                )
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


        if skip_translation:
            print("\n--- Step 4: Translation skipped (keep original text) ---")
            project_state.set_step_status("translate_raw", "skipped")
            project_state.set_step_status("refine_translation", "skipped")
            self.project_service.save_project(project_state)
        else:
            print(f"\n--- Step 4: Translating to {target_language} ---")

            if step_callback: step_callback("translation")
            translate_started = time.perf_counter()
            project_state.set_step_status("translate_raw", "running")
            self.project_service.save_project(project_state)
            tts_prefetch_executor = None
            tts_prefetch_futures = []
            tts_prefetch_enabled = bool(
                prefetch_voice_name
                and mode in ("voice", "both")
                and not is_remote_profile()
            )
            voice_prefetch = None
            if tts_prefetch_enabled:
                from workflows.voice_workflow import VoiceWorkflow
                voice_prefetch = VoiceWorkflow(self.workspace_root)
                tts_prefetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                tts_prefetch_tmp_dir = os.path.join(
                    self.workspace_root,
                    "temp",
                    "projects",
                    str(project_state.project_id or "global"),
                    "tts",
                )
                print(
                    "[Prepare Workflow] TTS cache prefetch enabled: "
                    f"voice={prefetch_voice_name}, speed={float(prefetch_voice_speed or 1.0):.2f}, "
                    f"tmp_dir={tts_prefetch_tmp_dir}"
                )

                def _prefetch_batch(start_idx: int, batch_segments: list[dict]) -> None:
                    if not batch_segments:
                        return
                    future = tts_prefetch_executor.submit(
                        voice_prefetch.prime_tts_cache,
                        segments=list(batch_segments),
                        tmp_dir=tts_prefetch_tmp_dir,
                        voice_name=prefetch_voice_name,
                        voice_speed=float(prefetch_voice_speed or 1.0),
                        index_offset=int(start_idx),
                        quiet=True,
                    )
                    tts_prefetch_futures.append(future)

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
                streamed_translated_segments = None
                if streamed_translation_enabled and streamed_translation_futures:
                    try:
                        ordered_futures = sorted(
                            [(int(start_idx), future) for start_idx, future in list(streamed_translation_futures)],
                            key=lambda item: item[0],
                        )
                        assembled = []
                        expected_start = 0
                        for start_idx, future in ordered_futures:
                            batch_segments = list(future.result() or [])
                            if tts_prefetch_enabled and batch_segments:
                                print(
                                    "[Prepare Workflow] Streamed translate batch ready, queue TTS prefetch: "
                                    f"start={start_idx}, count={len(batch_segments)}"
                                )
                                _prefetch_batch(start_idx, batch_segments)
                            if start_idx != expected_start:
                                assembled = []
                                break
                            assembled.extend(batch_segments)
                            expected_start = len(assembled)
                        if len(assembled) == len(segment_models):
                            streamed_translated_segments = assembled
                            print(
                                "[Prepare Workflow] Reusing streamed translation batches from chunked ASR: "
                                f"segments={len(streamed_translated_segments)}"
                            )
                    except Exception as exc:
                        print(f"[Prepare Workflow] Streaming translation overlap discarded: {exc}")
                    finally:
                        if streamed_translation_executor is not None:
                            streamed_translation_executor.shutdown(wait=True)
                            streamed_translation_executor = None

                if cached_translation_signature == translation_signature and cached_translation_path and os.path.exists(cached_translation_path):
                    cached_models = self.project_service.load_segment_artifact(project_state, "translation_final")
                    if cached_models:
                        # A cached translation can predate diarization. Keep
                        # speaker metadata from the freshly labeled transcript
                        # so TS1 remains a visualization-only speaker map.
                        for index, cached_model in enumerate(cached_models):
                            if index >= len(segment_models):
                                break
                            speaker = str(segment_models[index].metadata.get("speaker", "") or "").strip()
                            if speaker:
                                cached_model.metadata["speaker"] = speaker
                        segment_models = cached_models
                        print("[Prepare Workflow] Reusing cached Vietnamese subtitles. Generate did not call AI again.")
                    else:
                        translated_segments = streamed_translated_segments or self.engine_runtime.translate_segments(
                            raw_segments,
                            src_lang=source_language,
                            target_lang=target_language,
                            enable_polish=translator_ai,
                            optimize_subtitles=optimize_subtitles,
                            style_instruction=project_state.translator_style,
                            batch_callback=_prefetch_batch if tts_prefetch_enabled else None,
                        )
                        segment_models = self.segment_service.apply_translations(segment_models, translated_segments)
                        self.project_service.save_segment_artifact(
                            project_state,
                            "translation_final",
                            os.path.join("translation", "translation_final.json"),
                            segment_models,
                        )
                else:
                    translated_segments = streamed_translated_segments or self.engine_runtime.translate_segments(
                        raw_segments,
                        src_lang=source_language,
                        target_lang=target_language,
                        enable_polish=translator_ai,
                        optimize_subtitles=optimize_subtitles,
                        style_instruction=project_state.translator_style,
                        batch_callback=_prefetch_batch if tts_prefetch_enabled else None,
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
            finally:
                if streamed_translation_executor is not None:
                    streamed_translation_executor.shutdown(wait=True)
                if tts_prefetch_executor is not None:
                    try:
                        for future in list(tts_prefetch_futures):
                            future.result()
                        print(
                            "[Prepare Workflow] TTS cache prefetch complete: "
                            f"batches={len(tts_prefetch_futures)}"
                        )
                    finally:
                        tts_prefetch_executor.shutdown(wait=True)
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
