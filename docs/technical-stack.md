# Technical Stack

| Area | Technology |
| --- | --- |
| Desktop UI | PySide6 |
| Video preview | libmpv with Qt Multimedia fallback |
| Background work | QThread workers |
| Audio transcription | Faster-Whisper / CTranslate2, SenseVoice / Sherpa-ONNX |
| OCR | RapidOCR PP-OCRv4 with OpenCV and ONNX Runtime |
| Speaker diarization | Sherpa-ONNX |
| VAD | Silero VAD via Sherpa-ONNX |
| Translation | Google Translate, Bing Translate, OpenAI, Google AI Studio, and Ollama |
| TTS | Piper, Edge TTS, CapCut TTS, and VieNeu TTS (Voice Cloning) |
| Video/audio processing | FFmpeg (NVENC GPU accelerated with CPU libx264 failover), pydub, NumPy, SciPy, librosa, soundfile |
| External integrations | CapCut Draft project generator |
| Packaging | PyInstaller |

## Processing notes

- **AI Translation & Dialogue Context Orchestration**:
  - Employs a multi-pass pipeline: Pass 1 analyzes the dialogue cues to build a Character Profile and strict Two-Way Address Rules (`learn_dialogue_context`), supporting interactive user feedback and draft re-analysis.
  - Automatically cleans LaTeX mathematical notation (converting `$\leftrightarrow$`, `$\rightarrow$` to Unicode arrows `↔`, `→`).
  - Employs `RollingContextLedger` across sequential subtitle batches to ensure pronoun continuity and maintain preceding dialogue context without batch-boundary drift.
  - Supports Google AI Studio (Gemini 2.5/1.5), OpenAI (GPT-4o), DeepSeek, Ollama (local offline models), and free web providers (Google Translate, Bing Translate). If Google Translate encounters an HTTP 429 rate limit, CapCap automatically falls back to Bing Translate.
- **Media Preview Architecture**:
  - Primary preview engine uses `libmpv` for high-framerate, hardware-accelerated playback and frame-accurate timeline seeking. Requires Microsoft Visual C++ Redistributable (x64) >= 14.20 for `libmpv-2.dll` (`installer\vc_redist.x64.exe`).
  - Seamless fallback to Qt Multimedia on systems lacking MPV libraries.
  - Fast Preview generates an on-the-fly 5-second multitrack composition (video, BGM, TTS audio, subtitles, blur regions, overlays).
- **GPU Acceleration & CPU Fallback**:
  - GPU Faster-Whisper uses CUDA when available, with standard inference as the safe path and optional batched inference controls.
  - RapidOCR uses one GPU inference worker to avoid competing CUDA sessions.
  - Video export implements an intelligent two-tier encoding strategy: dynamically selecting NVIDIA NVENC (`h264_nvenc` with p2–p5 presets) when supported, and falling back automatically to CPU `libx264` (with veryfast–slow presets and matched CRF) if NVENC or CUDA drivers are unavailable.
- **Timeline Visuals & Background Caching**:
  - Timeline waveforms and video thumbnails are generated asynchronously in a non-blocking background thread, cached per project/video, and reused instantly upon project reload.
- **Speaker Diarization**:
  - Speaker diarization runs only for audio-based transcription and is optional. Automatically assigns color-coded speaker tags to subtitle segments.

## References

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- [RapidOCR](https://github.com/RapidAI/RapidOCR)
- [Piper](https://github.com/rhasspy/piper)
- [Edge TTS](https://github.com/rany2/edge-tts)
- [FFmpeg](https://ffmpeg.org/)
- [PyInstaller](https://pyinstaller.org/)
