# Requirements and Resources

## System requirements

- Windows 10/11
- Python 3.11 when running from source
- FFmpeg and libmpv are included in the application resources
- Microsoft Visual C++ 2015-2022 Redistributable (x64) (version >= 14.20) for `libmpv-2.dll` (installer available at `installer\vc_redist.x64.exe`)
- CPU mode works on systems without an NVIDIA GPU

### Media Preview & MPV Troubleshooting

If MPV fails to load (error 126 / missing DLLs), run `installer\vc_redist.x64.exe` to install the Microsoft Visual C++ runtime, or place a compatible 64-bit `libmpv-2.dll` into `bin/mpv/`.

## GPU mode

GPU acceleration is used by Faster-Whisper, RapidOCR, and video export (NVIDIA NVENC). It requires a supported NVIDIA GPU and a current NVIDIA driver. The CUDA runtime pack is intentionally downloaded on demand through **Manage Resources** rather than bundled into the installer.

No CUDA Toolkit installation is required when the CUDA runtime pack is installed.
Faster-Whisper GPU execution requires CTranslate2 4.6.3 or newer for the CUDA 12.8 runtime pack.

Video export leverages NVENC hardware encoding when available. On systems without an NVIDIA GPU, without CUDA drivers, or running AMD/Intel GPUs, export automatically falls back to CPU `libx264` encoding without requiring any additional installation.

## Resource Manager

Open **Manage Resources** from the launcher or Settings. It reports each resource as Ready, Partial, or Missing and provides download links.

| Resource | Target folder |
| --- | --- |
| Faster-Whisper models (Base, Small, Medium, Turbo, Large-v3) | `models/faster_whisper/` |
| CUDA 12.8 runtime pack | `bin/cuda12_fw/` |
| SenseVoice model | `models/sensevoice/` |
| VieNeu-TTS models (v3 Turbo ONNX & MOSS tokenizer) | `models/vieneu/` |
| Vietnamese Piper voices (`piper-new`, shared config) | `models/piper/` (`config.json` + `voices.json` + `.onnx`) |
| English Piper voices | `models/piper-en/` |
| Speaker diarization models | `models/pyannote/` |
| Voice samples and voice catalogs | `assets/voices/`, `assets/capcut/` |

## Environment configuration

Copy `.env_example` to `.env` only for manual setup. The active variables are:

| Group | Variables |
| --- | --- |
| AI translation providers | `OPENAI_PROVIDER` (`google_ai_studio`, `openai`, `deepseek`, `ollama`, `google`, `bing`), `AI_POLISHER_PROVIDER` |
| Google AI Studio | `GOOGLE_AI_STUDIO_API_KEY`, `GOOGLE_AI_STUDIO_MODEL`, `GOOGLE_AI_STUDIO_BASE_URL` |
| OpenAI / DeepSeek | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` |
| Ollama (Offline Local) | `OLLAMA_BASE_URL` (default: `http://localhost:11434/v1`), `OLLAMA_MODEL` |
| Translation timeouts | `CAPCAP_OLLAMA_TIMEOUT` (default: `3600`s), `CAPCAP_AI_TIMEOUT` (default: `300`s) |
| Dialogue Context & Pronouns | `CAPCAP_AUTO_TRANSLATION_CONTEXT` (`1`/`0`), `CAPCAP_REVIEW_TRANSLATION_CONTEXT` (`1`/`0`) |
| OCR crop | `OCR_SUBTITLE_REGION`, `OCR_SAMPLING_FPS`, optional `OCR_CROP_RATIO`, `OCR_SUBTITLE_RECT` |
| Remote API | `CAPCAP_REMOTE_API_URL`, `CAPCAP_REMOTE_API_TOKEN`, `CAPCAP_REMOTE_API_HOST`, `CAPCAP_REMOTE_API_PORT`, `CAPCAP_REMOTE_API_TIMEOUT`, `CAPCAP_QUIET` |
| Optional Whisper tuning | `CAPCAP_WHISPER_DEVICE`, `CAPCAP_WHISPER_GPU_BATCHED`, `CAPCAP_WHISPER_GPU_BATCH_SIZE` |

Subtitle Source and Project Preset are project-local and intentionally not environment variables.
