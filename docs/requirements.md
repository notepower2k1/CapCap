# Requirements and Resources

## System requirements

- Windows 10/11 for the packaged release build
- Python 3.11 when running from source
- FFmpeg and libmpv are included in the Windows application resources
- CPU mode works on systems without an NVIDIA GPU

### Running from source on Linux and macOS

Only the packaged release is Windows-only. Running from source works on Linux
and macOS provided FFmpeg and libmpv come from the system package manager,
because `bin/` ships Windows `.exe` and `.dll` files that those systems cannot
load. CapCap looks for a bundled copy first and falls back to `PATH`.

| Platform | Install command |
| --- | --- |
| Debian/Ubuntu | `sudo apt install ffmpeg libmpv2` (`libmpv1` on older releases) |
| Fedora | `sudo dnf install ffmpeg mpv-libs` |
| Arch | `sudo pacman -S ffmpeg mpv` |
| macOS (Homebrew) | `brew install ffmpeg mpv` |

Verify the tools resolve before starting the app:

```bash
ffmpeg -version
ffprobe -version
python -c "import mpv; print('libmpv OK')"
```

Platform notes:

- GPU mode is CUDA-only, so Linux needs an NVIDIA GPU and macOS is CPU-only.
  `requirements-base.txt` installs the CPU build of `onnxruntime` automatically
  on macOS and on non-x86_64 machines.
- Qt needs a desktop session. On a headless or WSL host, install the usual
  `libxcb`/`xkbcommon` runtime packages, or run under an X/Wayland server.
- The packaged `.exe` build and the CUDA runtime pack remain Windows-only.

## GPU mode

GPU acceleration is used by Faster-Whisper and RapidOCR. It requires a supported NVIDIA GPU and a current NVIDIA driver. The CUDA runtime pack is intentionally downloaded on demand through **Manage Resources** rather than bundled into the installer.

No CUDA Toolkit installation is required when the CUDA runtime pack is installed.

## Resource Manager

Open **Manage Resources** from the launcher or Settings. It reports each resource as Ready, Partial, or Missing and provides download links.

| Resource | Target folder |
| --- | --- |
| Faster-Whisper models | `models/faster_whisper/` |
| CUDA 12 runtime pack | `bin/cuda12_fw/` |
| SenseVoice model | `models/sensevoice/` |
| Vietnamese Piper voices | `models/piper/` |
| English Piper voices | `models/piper-en/` |
| Speaker diarization models | `models/pyannote/` |

## Environment configuration

Copy `.env_example` to `.env` only for manual setup. The active variables are:

| Group | Variables |
| --- | --- |
| AI translation | `OPENAI_PROVIDER`, `AI_POLISHER_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` |
| Google AI Studio | `GOOGLE_AI_STUDIO_API_KEY`, `GOOGLE_AI_STUDIO_MODEL`, `GOOGLE_AI_STUDIO_BASE_URL` |
| OCR crop | `OCR_SUBTITLE_REGION`, `OCR_SAMPLING_FPS`, optional `OCR_CROP_RATIO`, `OCR_SUBTITLE_RECT` |
| Remote API | `CAPCAP_REMOTE_API_URL`, `CAPCAP_REMOTE_API_TOKEN`, `CAPCAP_REMOTE_API_HOST`, `CAPCAP_REMOTE_API_PORT`, `CAPCAP_REMOTE_API_TIMEOUT`, `CAPCAP_QUIET` |
| Optional Whisper tuning | `CAPCAP_WHISPER_DEVICE`, `CAPCAP_WHISPER_GPU_BATCHED`, `CAPCAP_WHISPER_GPU_BATCH_SIZE` |

Subtitle Source is project-local and intentionally is not an environment variable.
