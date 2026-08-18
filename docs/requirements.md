# Requirements and Resources

## System requirements

- Windows 10/11
- Python 3.11 when running from source
- FFmpeg and libmpv are included in the application resources
- CPU mode works on systems without an NVIDIA GPU

## GPU mode

GPU acceleration is used by Faster-Whisper and RapidOCR. It requires a supported NVIDIA GPU and a current NVIDIA driver. The CUDA runtime pack is intentionally downloaded on demand through **Manage Resources** rather than bundled into the installer.

No CUDA Toolkit installation is required when the CUDA runtime pack is installed.

## Resource Manager

Open **Manage Resources** from the launcher or Settings. It reports each resource as Ready, Partial, or Missing and provides download links. Every entry shows the folder it installs into, and **Open Folder** opens that folder directly.

The SenseVoice model is required before **New Project** is enabled, in both CPU and GPU mode. The GPU Acceleration Pack is required only for GPU mode.

| Resource | Extract the archive into | Files end up in |
| --- | --- | --- |
| Faster-Whisper models | `models/faster_whisper/` | `models/faster_whisper/models--Systran--.../` |
| CUDA 12 runtime pack | `bin/` | `bin/cuda12_fw/` |
| SenseVoice model | `models/sensevoice/` | `models/sensevoice/` |
| Vietnamese Piper voices | `models/piper/` | `models/piper/` |
| English Piper voices | `models/piper-en/` | `models/piper-en/` |
| Speaker diarization models | `models/pyannote/` | `models/pyannote/` |

### Installing a pack by hand

The archives carry their own top-level folder. `cuda12_fw.zip`, for example, contains a `cuda12_fw/` directory, so it must be extracted into `bin/` and **not** into `bin/cuda12_fw/` - the latter produces `bin/cuda12_fw/cuda12_fw/`.

CapCap now also accepts a pack nested one or two levels below its target folder, so an accidental extra folder no longer leaves the resource stuck on Missing. Press **Refresh** in Manage Resources after copying files in.

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
