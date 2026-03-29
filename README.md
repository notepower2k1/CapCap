# CapCap

CapCap is a local desktop app for Vietnamese video localization. It combines transcription, translation, subtitle editing, AI rewrite, Vietnamese voice generation, audio mixing, preview, and export in one Windows workflow.

![CapCap Preview](assets/preview.jpg)

## Overview

CapCap is built for short-form and social video production where the user still wants control over the final script, subtitle timing, and voice output.

Current product flow:

1. load a source video
2. extract and optionally separate audio
3. transcribe with `faster-whisper`
4. translate to Vietnamese
5. rewrite Vietnamese subtitles with AI on demand
6. edit subtitles in the built-in subtitle editor
7. generate Vietnamese voice or use an existing mixed track
8. mix generated voice with user-provided background audio
9. preview subtitle and audio output
10. export the final result

## Main Features

- project-based workflow with resumable state
- subtitle-only, voice-only, or subtitle + voice output modes
- `faster-whisper` transcription with selectable `base` or `medium` model
- Microsoft Translator for raw translation
- AI rewrite with preview-first apply flow
- rewrite style presets for short videos:
  - natural short video
  - TikTok natural
  - punchy viral
  - sales voiceover
  - short storytelling
  - neutral dubbing
  - clean subtitle
- direct subtitle editing in the timeline-aware subtitle editor
- import external translated `.srt`
- subtitle styling with ASS-based rendering
- typewriter and word-highlight karaoke animations
- source-timed or Vietnamese-paced text timing for supported subtitle animations
- free and premium voice lanes
- local speed adjustment on generated voice without forcing a new TTS API request
- voice/background gain controls and audio source switching
- exact-frame preview, 5-second preview, and audio preview
- top toolbar actions for model loading, project cleanup, downloads, and export

## Voice Support

CapCap currently supports:

- Edge TTS
- Zalo TTS
- FPT.AI TTS
Voice behavior highlights:

- voice speed changes are applied locally on WAV files after base synthesis
- AI/API TTS is cached per subtitle segment

## Translation and Rewrite

Current translation stack:

- Microsoft Translator for base Vietnamese translation
- OpenRouter as the primary AI rewrite provider
- `translator-api.thach-nv` as fallback for rewrite when configured

Rewrite safety behavior:

- rewrite does not directly overwrite the subtitle editor
- users first generate a preview in a popup
- the preview is applied only when the user clicks `Apply`
- the AI output is validated by item count before it is accepted

## Project and Cleanup

CapCap now includes a `Clean Project` button on the top toolbar.

It removes intermediate data such as:

- project workspace data under `projects/`
- separated audio
- temp preview files
- TTS cache files
- generated intermediate voice/mix artifacts

It keeps:

- source video
- imported assets
- final exported video

## Requirements

- Windows 10 or Windows 11
- Python 3.11 recommended
- bundled `ffmpeg` and `ffprobe` in `bin/ffmpeg`
- bundled `libmpv` in `bin/mpv`
- model downloads enabled for:
  - `faster-whisper`
  - Demucs

## Installation

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap
pip install -r requirements.txt
```

Create `.env` from [`.env_example`](/D:/CodingTime/CapCap/.env_example) and fill in only the providers you want to use.

## Run

GUI entry points:

```bash
python ui/gui.py
```

or

```bash
python ui/main_window.py
```

CLI entry:

```bash
python app/main.py <video_path>
```

## Repository Layout

```text
CapCap/
|-- app/
|   |-- core/
|   |   |-- models/
|   |   `-- state/
|   |-- engines/
|   |-- services/
|   |-- translation/
|   |   `-- providers/
|   |-- workflows/
|   |-- audio_mixer.py
|   |-- preview_processor.py
|   |-- subtitle_builder.py
|   |-- translator.py
|   |-- tts_processor.py
|   |-- video_processor.py
|   |-- vocal_processor.py
|   |-- voice_preview_catalog.json
|   `-- whisper_processor.py
|-- assets/
|-- bin/
|-- models/
|-- output/
|-- projects/
|-- temp/
|-- ui/
|   |-- controllers/
|   |-- helpers/
|   |-- utils/
|   |-- views/
|   |-- widgets/
|   |-- worker_adapters/
|   |-- gui.py
|   |-- main_window.py
|   `-- workers.py
|-- .env
|-- .env_example
|-- README.md
|-- requirements.txt
`-- structure.md
```

## Status

The repository already has working engine, workflow, service, and UI layers, but some legacy processor modules still exist and are wrapped by adapters for compatibility.

The current direction is:

- keep workflows project-aware
- keep preview and export behavior aligned
- reduce legacy processor leakage into UI code
- make runtime cleanup and packaging safer
