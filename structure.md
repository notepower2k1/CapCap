# CapCap Structure

## 1. Goal

CapCap is a local desktop application designed to:

1. Extract audio from video
2. Run speech-to-text with Whisper
3. Translate subtitles into Vietnamese
4. Let users review and edit subtitles with timeline and preview tools
5. Generate Vietnamese voice and optionally mix it with background audio
6. Export subtitle-only, voice-only, or subtitle + voice videos

## 2. High-Level Processing Flow

```text
Video
  -> Audio extraction
  -> Transcription
  -> Translation / subtitle editing
  -> Subtitle preview + exact frame preview
  -> Optional voice generation / background mix
  -> Final export
```

## 3. Code Architecture

### UI Layer

- `ui/gui.py`
  - Main PySide6 window
  - Guided `START HERE` workflow
  - `Live preview` panel
  - Collapsible `ADVANCED` section
  - Timeline seek + subtitle overlay
  - Auto-refresh exact frame preview
  - `QSettings` persistence

### Core Modules

- `app/video_processor.py`
  - ffmpeg / ffprobe helpers
  - subtitle, voice, and final video export

- `app/preview_processor.py`
  - 5-second preview clip generation
  - exact frame preview rendering

- `app/whisper_processor.py`
  - speech-to-text through whisper-cli

- `app/translator.py`
  - translation orchestration

- `app/subtitle_builder.py`
  - SRT generation and saving

- `app/tts_processor.py`
  - text-to-speech generation

- `app/audio_mixer.py`
  - voice/background mixing

- `app/vocal_processor.py`
  - vocal separation

- `app/translation/`
  - translation providers and orchestration

## 4. Current UX / State Rules

- Subtitle source is selected automatically:
  - edited / translated Vietnamese subtitles are preferred
  - original subtitles are used as fallback
- Audio source for preview/export is explicit:
  - generated voice/mix
  - existing mixed audio
- Exact frame preview:
  - auto-refreshes while editing subtitle text/style
  - auto-refreshes when seeking on the timeline
  - still supports opening a larger preview dialog manually
- Temporary preview files:
  - only the latest preview is kept
  - preview artifacts are cleaned up after successful export

## 5. Directory Layout

```text
CapCap/
|-- app/
|   |-- audio_mixer.py
|   |-- main.py
|   |-- preview_processor.py
|   |-- subtitle_builder.py
|   |-- translator.py
|   |-- tts_processor.py
|   |-- video_processor.py
|   |-- vocal_processor.py
|   |-- whisper_processor.py
|   `-- translation/
|       |-- __init__.py
|       |-- errors.py
|       |-- models.py
|       |-- orchestrator.py
|       |-- srt_utils.py
|       `-- providers/
|           |-- __init__.py
|           |-- ai_polisher.py
|           `-- microsoft_translator.py
|-- bin/
|   |-- ffmpeg/
|   `-- whisper/
|-- models/
|-- output/
|-- temp/
|-- ui/
|   `-- gui.py
|-- .env
|-- .env_example
|-- README.md
|-- requirements.txt
`-- structure.md
```

## 6. Technology Stack

- UI: PySide6
- Speech-to-text: Whisper.cpp
- Multimedia: FFmpeg / FFprobe
- Translation: provider layer under `app/translation/`
- Settings persistence: `QSettings`
