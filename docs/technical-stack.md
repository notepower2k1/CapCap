# Technical Stack

| Area | Technology |
| --- | --- |
| Desktop UI | PySide6 (Qt for Python) with Adaptive 16:9 & 9:16 Layout Engine |
| Video preview | libmpv with Qt Multimedia fallback |
| Background work | QThread workers |
| Audio transcription | Faster-Whisper / CTranslate2, SenseVoice / Sherpa-ONNX |
| Vocal / BGM separation | ONNX Runtime (`CUDAExecutionProvider` GPU acceleration with CPU fallback) |
| OCR | RapidOCR PP-OCRv4 with OpenCV and ONNX Runtime |
| Speaker diarization | Sherpa-ONNX |
| VAD | Silero VAD via Sherpa-ONNX |
| Translation | Google Translate, Bing Translate, OpenAI (GPT-4o), Google AI Studio (Gemini 2.5/1.5), DeepSeek, and Ollama |
| TTS | Piper, Edge TTS, CapCut TTS, and VieNeu TTS (Voice Cloning) |
| Video/audio processing | FFmpeg (NVENC GPU accelerated with CPU libx264 failover), pydub, NumPy, SciPy, librosa, soundfile |
| External integrations | CapCut Draft project generator |
| Packaging | PyInstaller & Inno Setup |

## Processing notes

- **Adaptive Workspace Engine**:
  - Automatically switches between widescreen Landscape (16:9) and vertical Portrait (9:16) layouts without relying on disk cache.
  - In 9:16 mode, the preview video gets a dedicated full-width right column while the Subtitle Inspector controls collapse horizontally beside a compact 204px layer toolbar.
- **AI Translation & Dialogue Context Orchestration**:
  - Employs a multi-pass pipeline: Pass 1 analyzes the dialogue cues to build a Character Profile and strict Two-Way Address Rules (`learn_dialogue_context`), supporting interactive user feedback and draft re-analysis.
  - Employs `RollingContextLedger` across sequential subtitle batches to ensure pronoun continuity and maintain preceding dialogue context without batch-boundary drift.
  - Supports Google AI Studio (Gemini 2.5/1.5), OpenAI (GPT-4o), DeepSeek, Ollama (local offline models), and free web providers (Google Translate, Bing Translate) with automated HTTP 429 failover.
- **Media Preview Architecture**:
  - Primary preview engine uses `libmpv` for high-framerate, hardware-accelerated playback and frame-accurate timeline seeking. Pre-initializes GPU context before window display to eliminate main-thread freezing.
  - Requires Microsoft Visual C++ Redistributable (x64) >= 14.20 for `libmpv-2.dll` (`installer\vc_redist.x64.exe`).
- **GPU Acceleration & Memory Management**:
  - Faster-Whisper uses CUDA when available with optional batched inference controls. Chunked transcription uses one reusable GPU model for all chunks, including long videos. CPU transcription remains available when CUDA is unavailable or CPU mode is selected.
  - Vocal and BGM separation links the bundled CUDA runtime to ONNX Runtime's `CUDAExecutionProvider`, batch-sliced to 1 to prevent PCIe memory thrashing on 6GB GPUs.
  - Video export leverages NVIDIA NVENC (`h264_nvenc` with p2–p5 presets) with automatic fallback to CPU `libx264` if NVENC is unavailable.
- **Audio Pipeline & In-Place TTS**:
  - Supports dirty-segment tracking and instant in-place voice regeneration for modified subtitle segments without re-synthesizing the entire timeline.
  - Standalone audio exports for dubbed voice, clean BGM, or complete multi-track audio mix.

## References

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- [RapidOCR](https://github.com/RapidAI/RapidOCR)
- [Piper](https://github.com/rhasspy/piper)
- [Edge TTS](https://github.com/rany2/edge-tts)
- [VieNeu-TTS](https://github.com/neu-nlp/VieNeu-TTS)
- [FFmpeg](https://ffmpeg.org/)
- [PyInstaller](https://pyinstaller.org/)
