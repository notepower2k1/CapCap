# CapCap

CapCap is a local desktop application for Vietnamese video localization. It supports a project-based workflow for transcription, translation, subtitle editing, Vietnamese voice generation, audio mixing, preview, and final export.

This repository already contains a significant part of the target architecture described in [structure.md](/D:/CodingTime/CapCap/structure.md), but the codebase is still in transition. The README describes the current product behavior and the current implementation layout.

## What CapCap Does

CapCap is designed for a semi-automated workflow:

- automate the preparation steps that are safe to run in sequence
- keep subtitle editing, TTS, mixing, preview, and export under user control
- keep preview output consistent with final export output

Core capabilities:

- extract audio from video
- transcribe speech with `faster-whisper`
- translate subtitles into Vietnamese
- rewrite translated subtitles with AI on demand
- edit subtitles directly in the built-in subtitle editor
- import external translated `.srt` files
- generate Vietnamese voice tracks
- mix generated voice with user-provided background audio
- preview subtitle and audio output before export
- export subtitle-only, voice-only, or combined output

## Current Workflow

### Preparation phase

For non-custom workflows, CapCap can automatically run:

1. audio extraction
2. transcription
3. raw translation
4. optional translation rewrite or refinement
5. optional source audio separation when the selected output requires voice work

### Review and production phase

The user can then control:

1. transcript and subtitle editing
2. subtitle styling
3. AI rewrite of the translated script
4. voice generation
5. audio mixing
6. frame and clip preview
7. final export

## Output Modes

The UI currently supports these output modes:

- `Vietnamese subtitles only`
- `Vietnamese voice only`
- `Vietnamese subtitles + voice`

## Translation and AI Rewrite

Current translation behavior:

- raw subtitle translation is handled by Microsoft Translator
- AI rewriting is a separate user action from the subtitle editor
- OpenRouter is used as the primary rewrite provider
- `translator-api.thach-nv` can be used as a fallback rewrite provider

## Voice Providers

Current voice support includes:

- Edge TTS
- Zalo TTS
- FPT.AI TTS
- VieNeu local TTS

Important implementation detail:

- API TTS is cached per segment
- voice speed changes are applied locally on WAV files instead of forcing a new TTS request
- timing sync and force-fit operations are applied after the base audio is generated

## Subtitle Rendering

CapCap renders styled subtitles through an `SRT -> ASS -> FFmpeg` pipeline.

Current subtitle features include:

- editable subtitle styles
- keyword highlighting
- exact frame preview
- 5-second preview clips
- karaoke-style word highlighting
- typewriter animation
- source-timed or Vietnamese-paced text timing for supported animations

## Environment Requirements

- Windows 10 or Windows 11
- Python 3.11 recommended
- bundled `FFmpeg` / `FFprobe` in `bin/ffmpeg`
- bundled `libmpv` in `bin/mpv`
- model downloads enabled for:
  - `faster-whisper`
  - Demucs
  - VieNeu if local TTS is used

## Installation

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap
pip install -r requirements.txt
```

Create `.env` from [`.env_example`](/D:/CodingTime/CapCap/.env_example) and fill in the providers you want to use.

## Run

Primary GUI entry points:

```bash
python ui/gui.py
```

or

```bash
python ui/main_window.py
```

CLI workflow entry:

```bash
python app/main.py <video_path>
```

## Repository Layout

This is the current practical layout of the repository:

```text
CapCap/
|-- app/
|   |-- core/
|   |   |-- models/
|   |   `-- state/
|   |-- engines/
|   |-- services/
|   |-- translation/
|   |-- workflows/
|   |-- audio_mixer.py
|   |-- local_vie_neu_tts.py
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

## Current Architecture Status

The codebase is partially aligned with the target architecture:

- `core`, `services`, `workflows`, and `engines` already exist
- project state and segment models are already used in real workflows
- the UI is split into views, controllers, widgets, and worker adapters
- several legacy processor modules still exist and are wrapped by adapter layers
- compatibility shims still exist in parts of the UI and worker entry points

## Next Practical Refactor Targets

1. Continue moving legacy processor logic fully behind the `engines` layer.
2. Reduce compatibility shims in `ui/gui.py` and `ui/workers.py`.
3. Keep provider abstractions consistent for ASR, translation, rewrite, TTS, and separation.
4. Continue aligning preview/export behavior around a single config-driven subtitle and audio pipeline.
