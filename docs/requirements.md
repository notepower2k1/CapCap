# Requirements and Resources

## System requirements

Version **9.0.0** includes the browser extension runtime files in the installer. Browser AI translation needs Chrome/Edge running with the unpacked extension enabled and an authenticated Gemini/ChatGPT session; no API key is required for this provider. Node.js is needed only to run extension development tests, not to use the extension. See [setup](../browser_extension/README.md).

- Windows 10 / Windows 11 (64-bit)
- Python 3.11 when running from source
- FFmpeg and libmpv are included in the application resources
- Microsoft Visual C++ 2015-2022 Redistributable (x64) (version >= 14.20) for `libmpv-2.dll` (installer available at `installer\vc_redist.x64.exe` or download from [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe))
- CPU mode works reliably on systems without a dedicated NVIDIA GPU

### Media Preview & MPV Troubleshooting

If MPV fails to load (error 126 / missing DLLs), run `installer\vc_redist.x64.exe` or download from [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe) to install the Microsoft Visual C++ runtime, or place a compatible 64-bit `libmpv-2.dll` into `bin/mpv/`.

## GPU mode

GPU acceleration is utilized by:
- **Faster-Whisper**: Speech-to-text recognition via CTranslate2 CUDA.
- **Vocal & BGM Separation**: Deep learning audio stem separation via ONNX Runtime `CUDAExecutionProvider`.
- **RapidOCR**: Subtitle text recognition on video frames.
- **Video Export**: Hardware-accelerated encoding via **NVIDIA NVENC**.

Requirements:
- A supported NVIDIA GPU (GTX 1060+, RTX series recommended).
- Current NVIDIA drivers.
- The CUDA 12.8 runtime pack can be downloaded on-demand with 1 click in **Manage Resources** (no CUDA Toolkit installation required).
- On systems without an NVIDIA GPU (e.g. AMD, Intel GPUs, or CPU-only), CapCap automatically falls back to CPU processing and `libx264` encoding.

## Resource Manager

Open **Manage Resources** from the launcher or Settings. It reports each resource as Ready, Partial, or Missing and provides automatic 1-click downloads with live progress tracking:

| Resource | Target folder | Description |
| --- | --- | --- |
| Faster-Whisper models (Base, Small, Medium, Turbo, Large-v3) | `models/faster_whisper/` | Speech-to-text recognition models |
| CUDA 12.8 runtime pack | `bin/cuda12_fw/` | GPU acceleration libraries |
| SenseVoice model | `models/sensevoice/` | Ultra-fast multi-language ASR |
| VieNeu-TTS models (v3 Turbo ONNX & MOSS tokenizer) | `models/vieneu/` | Vietnamese TTS and voice cloning |
| Vietnamese Piper voices (`piper-new`, shared config) | `models/piper/` | Offline local Vietnamese speech synthesis |
| English Piper voices | `models/piper-en/` | Offline local English speech synthesis |
| Speaker diarization models | `models/pyannote/` | Multi-speaker segmentation models |
| Voice samples and voice catalogs | `assets/voices/`, `assets/capcut/` | Reference voice audio and presets |

## Environment configuration

Copy `.env_example` to `.env` only for manual setup. The active variables are:

| Group | Variables |
| --- | --- |
| AI translation providers | `OPENAI_PROVIDER` (`google_ai_studio`, `openai`, `ollama`, `browser_ai`, `google`, `bing`), `AI_POLISHER_PROVIDER` |
| Browser AI bridge | `CAPCAP_BROWSER_AI_PLATFORM` (`gemini` / `chatgpt`), `CAPCAP_BROWSER_AI_TEMP_CHAT` (`1` / `0`), `CAPCAP_BROWSER_AI_WORKERS` (`1` sequential, default / `2`, `3`, `4` parallel isolated chats), `CAPCAP_BROWSER_BRIDGE_PORT` (default `18765`) |
| Google AI Studio | `GOOGLE_AI_STUDIO_API_KEY`, `GOOGLE_AI_STUDIO_MODEL`, `GOOGLE_AI_STUDIO_BASE_URL` |
| OpenAI / DeepSeek | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` |
| Ollama (Offline Local) | `OLLAMA_BASE_URL` (default: `http://localhost:11434/v1`), `OLLAMA_MODEL` |
| Translation timeouts | `CAPCAP_OLLAMA_TIMEOUT` (default: `3600`s), `CAPCAP_AI_TIMEOUT` (default: `300`s) |
| Dialogue Context & Pronouns | `CAPCAP_AUTO_TRANSLATION_CONTEXT` (`1`/`0`), `CAPCAP_REVIEW_TRANSLATION_CONTEXT` (`1`/`0`) |
| OCR crop | `OCR_SUBTITLE_REGION`, `OCR_SAMPLING_FPS`, optional `OCR_CROP_RATIO`, `OCR_SUBTITLE_RECT` |
| Remote API | `CAPCAP_REMOTE_API_URL`, `CAPCAP_REMOTE_API_TOKEN`, `CAPCAP_REMOTE_API_HOST`, `CAPCAP_REMOTE_API_PORT`, `CAPCAP_REMOTE_API_TIMEOUT`, `CAPCAP_QUIET` |
| Optional Whisper tuning | `CAPCAP_WHISPER_DEVICE`, `CAPCAP_WHISPER_GPU_BATCHED`, `CAPCAP_WHISPER_GPU_BATCH_SIZE` |

Subtitle Source and Project Preset are saved per project and intentionally not global environment variables.
