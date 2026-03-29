# CapCap Structure

## 1. Product Goal

CapCap is a local Windows desktop app for Vietnamese video localization and short-form video production.

It is designed around a practical hybrid workflow:

- preparation can be automated
- editing and approval remain user-controlled
- preview should stay close to final export

Typical flow:

1. import a source video
2. extract audio
3. transcribe speech
4. translate to Vietnamese
5. optionally rewrite the Vietnamese subtitles with AI
6. edit subtitles
7. generate Vietnamese voice
8. mix voice with background audio
9. preview output
10. export the final video

## 2. Runtime Model

```text
Video
  -> Project
      -> Extract audio
      -> Optional source separation
      -> Transcribe
      -> Translate
      -> Optional AI rewrite preview
      -> Subtitle editing
      -> Voice generation
      -> Audio mixing
      -> Preview
      -> Export
      -> Clean project intermediates
```

## 3. Main Layers

CapCap is structured around four practical layers.

### UI layer

The UI layer is responsible for:

- collecting user input
- presenting project state
- driving preview and export actions
- exposing rewrite and cleanup actions

Current UI modules:

- `ui/views`
- `ui/controllers`
- `ui/widgets`
- `ui/worker_adapters`
- `ui/utils`

### Workflow layer

The workflow layer coordinates multi-step operations and project lifecycle.

Responsibilities:

- decide which steps run
- reuse valid outputs when possible
- manage project state transitions
- keep preview/export behavior consistent

Current workflows:

- `prepare_workflow.py`
- `voice_workflow.py`
- `export_workflow.py`

### Service layer

The service layer owns project persistence and shared app-level logic.

Responsibilities:

- load and save project state
- persist transcript/translation artifacts
- manage voice catalog loading
- provide a normalized bridge between UI and project data

Current examples:

- `ProjectService`
- `GUIProjectBridge`
- `VoiceCatalogService`
- `EngineRuntime`

### Engine / provider layer

This layer wraps external tools and provider-specific integrations behind normalized interfaces.

Current examples:

- `FFmpegAdapter`
- `WhisperAdapter`
- `TranslatorAdapter`
- `TTSAdapter`
- `AudioMixAdapter`
- `PreviewAdapter`
- `DemucsAdapter`
- `SubtitleAdapter`

Provider-level modules still used under adapters:

- `whisper_processor.py`
- `translator.py`
- `tts_processor.py`
- `audio_mixer.py`
- `preview_processor.py`
- `video_processor.py`
- `vocal_processor.py`

## 4. Core Data Model

The main unit of processing is a subtitle segment.

Example:

```json
{
  "id": 1,
  "start": 12.5,
  "end": 15.2,
  "original_text": "Hello everyone",
  "raw_translation": "Xin chào mọi người",
  "refined_translation": "Xin chào cả nhà",
  "final_text": "Xin chào mọi người",
  "tts_text": "Xin chào mọi người",
  "voice_file": "audio/tts_segments/seg_0001.wav",
  "status": "ready",
  "metadata": {
    "words": [],
    "manual_highlights": []
  }
}
```

Important segment fields:

- `original_text`
- `raw_translation`
- `refined_translation`
- `final_text`
- `tts_text`
- `voice_file`
- `metadata.words`
- `metadata.manual_highlights`

`final_text` and `tts_text` are allowed to diverge when subtitle readability and TTS readability need different phrasing.

## 5. Project Layout

Each video is treated as an isolated project with resumable state.

Current project structure:

```text
projects/<project_id>/
|-- source/
|-- analysis/
|-- translation/
|-- audio/
|   |-- separated/
|   `-- tts_segments/
|-- subtitle/
|-- preview/
|   `-- cache/
|-- export/
|-- logs/
`-- project.json
```

Current stored artifacts may include:

- extracted audio
- transcript JSON
- translation JSON
- original and translated SRT
- separated vocals and background audio
- final workflow state in `project.json`

## 6. Voice Architecture

CapCap currently has two user-facing voice lanes:

- free voice
- premium voice

### Free and premium voices

Current providers:

- Edge
- Zalo
- FPT.AI

Catalog source:

- built-in voices come from `app/voice_preview_catalog.json`

## 7. Translation and Rewrite Architecture

### Translation

Base translation pipeline:

- ASR output
- Microsoft Translator
- Vietnamese subtitle state

### Rewrite

Rewrite is now a user-triggered preview flow:

1. user opens `Rewrite with AI`
2. user selects a rewrite preset or custom instruction
3. AI generates a preview
4. preview is applied only after explicit confirmation

Current rewrite presets include:

- natural short video
- TikTok natural
- punchy viral
- sales voiceover
- short storytelling
- neutral dubbing
- clean subtitle
- custom

Providers:

- OpenRouter primary
- `translator-api.thach-nv` fallback

Output validation:

- numbered-line parsing
- exact item-count validation
- invalid AI output is rejected before it can be applied

## 8. Subtitle Rendering

Rendering pipeline:

```text
Subtitle segments
  -> SRT
  -> ASS styling
  -> FFmpeg render
```

Current subtitle features:

- editable subtitle styles
- saved subtitle style presets
- keyword highlighting
- typewriter animation
- word-highlight karaoke animation
- source speech timing
- Vietnamese pacing timing
- exact frame preview
- 5-second preview rendering

## 9. Audio Pipeline

Voice generation pipeline:

```text
Subtitle segments
  -> per-segment TTS
  -> optional local speed adjustment
  -> optional smart timing fit
  -> merged voice track
  -> optional background mix
```

Important behavior:

- base TTS is cached per segment
- speed changes do not force a new TTS API call
- timing sync happens after base synthesis
- preview/export can use either generated mixed audio or an existing mixed file

## 10. Cleanup Model

The top toolbar now includes `Clean Project`.

Cleanup behavior:

- removes project intermediates
- removes temp preview data
- removes TTS cache folders
- removes generated voice/mix artifacts that belong to the current project
- keeps source video
- keeps imported user assets
- keeps final exported video

This cleanup model is important for packaging because the app produces a large amount of transient audio data during normal use.

## 11. Current Repository Layout

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
|   |-- main.py
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
|-- structure.md
`-- newSpec.md
```

## 12. Current Status

What is already established:

- project-aware workflows
- service and adapter layers
- subtitle editor with timeline sync
- rewrite preview/apply flow
- toolbar actions for model loading and project cleanup

What is still transitional:

- some legacy processor modules still sit beside adapter wrappers
- runtime artifact cleanup can be expanded further
- packaging still benefits from pruning temp/test leftovers before release

## 13. Near-Term Refactor Targets

1. Continue pushing legacy processor logic fully behind adapters.
2. Keep temp/cache directories fully project-scoped wherever practical.
3. Add stronger packaging cleanup around test files and runtime leftovers.
4. Keep docs synchronized with the actual repo and product behavior.
