# CapCap Structure

## 1. Goal

CapCap is a local desktop tool for Vietnamese video localization with a project-based workflow:

1. import a source video
2. extract audio
3. transcribe speech
4. translate into Vietnamese
5. optionally rewrite or refine the translation
6. build and edit subtitles
7. generate Vietnamese voice audio
8. mix voice with background audio
9. preview the result
10. export the final video

The product is designed around a semi-automated model:

- preparation steps can be automated
- subtitle, TTS, mix, preview, and export remain user-controlled
- preview should match export as closely as possible

## 2. Processing Model

```text
Project
  -> Import source video
  -> Prepare
       -> Extract audio
       -> Transcribe
       -> Translate raw
       -> Rewrite / refine translation (optional)
       -> Separate source audio when needed
  -> Manual workspace
       -> Edit transcript / translation
       -> Style subtitles
       -> Generate voice
       -> Mix audio
       -> Preview
  -> Export
```

## 3. Architectural Layers

CapCap is moving toward a three-layer architecture.

### Engine layer

The engine layer wraps external tools and providers and returns normalized results.

Current examples:

- `FFmpegAdapter`
- `WhisperAdapter`
- `TranslatorAdapter`
- `TTSAdapter`
- `PreviewAdapter`
- `AudioMixAdapter`
- `DemucsAdapter`
- `SubtitleAdapter`

Responsibilities:

- accept normalized inputs
- call external tools or APIs
- return normalized outputs
- avoid workflow-specific business logic

### Workflow layer

The workflow layer coordinates processing steps and project state.

Responsibilities:

- decide which steps should run
- reuse valid outputs when possible
- skip unnecessary work
- update `project.json`
- drive export and preview consistency

Current workflows:

- `prepare_workflow.py`
- `voice_workflow.py`
- `export_workflow.py`

### UI layer

The UI layer should:

- display project state
- collect user input
- trigger workflows
- preview output
- display logs and errors

The UI should avoid talking directly to raw providers when an adapter or workflow already exists.

## 4. Core Data Model

The central unit in the system is a subtitle segment.

Example:

```json
{
  "id": 1,
  "start": 12.5,
  "end": 15.2,
  "original_text": "Hello everyone",
  "raw_translation": "Xin chao moi nguoi",
  "refined_translation": "Xin chao tat ca moi nguoi",
  "final_text": "Xin chao moi nguoi",
  "tts_text": "Xin chao moi nguoi",
  "voice_file": "audio/tts_segments/seg_0001.wav",
  "status": "ready",
  "metadata": {}
}
```

Important fields:

- `original_text`: transcript text from ASR
- `raw_translation`: direct translated text
- `refined_translation`: AI-rewritten or refined text
- `final_text`: subtitle text used for rendering
- `tts_text`: text used for voice synthesis
- `voice_file`: segment-level generated audio file
- `metadata`: extra per-segment information such as manual highlights or word timing data

`final_text` and `tts_text` may intentionally differ.

## 5. Project Data Layout

Each imported video should behave like an isolated project.

Target project layout:

```text
project/
|-- source/
|   |-- input_video.mp4
|   `-- extracted_audio.wav
|-- analysis/
|   |-- transcript_raw.json
|   |-- transcript_segments.json
|   `-- detected_language.json
|-- translation/
|   |-- translation_raw.json
|   |-- translation_refined.json
|   `-- translation_final.json
|-- audio/
|   |-- separated/
|   |   |-- vocal.wav
|   |   `-- background.wav
|   |-- tts_segments/
|   |-- voice_merged.wav
|   `-- mixed.wav
|-- subtitle/
|   |-- subtitle.srt
|   |-- subtitle.ass
|   `-- style.json
|-- preview/
|-- export/
|   `-- final_output.mp4
`-- project.json
```

## 6. Project State

Each project keeps a resumable processing state.

Example:

```json
{
  "project_id": "demo_001",
  "input_video": "source/input_video.mp4",
  "input_language": "en",
  "target_language": "vi",
  "mode": "subtitle_voice",
  "translator_ai": true,
  "steps": {
    "extract_audio": "done",
    "transcribe": "done",
    "translate_raw": "done",
    "refine_translation": "done",
    "separate_audio": "done",
    "generate_tts": "pending",
    "mix_audio": "pending",
    "export": "pending"
  }
}
```

Standard step values:

- `pending`
- `running`
- `done`
- `failed`
- `skipped`

## 7. Preview and Export Consistency

This is a hard rule for the product:

- subtitle preview and final export should use the same style configuration
- audio preview and final export should use the same selected audio source
- subtitle timing, mix settings, and output mode should be persisted in project state whenever practical

## 8. Current Code Layout

This is the current real repository structure, not the ideal future target.

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
|   |-- highlight_selector.py
|   |-- local_vie_neu_tts.py
|   |-- main.py
|   |-- preview_processor.py
|   |-- subtitle_builder.py
|   |-- translator.py
|   |-- tts_processor.py
|   |-- video_processor.py
|   |-- vocal_processor.py
|   |-- voice_preview_catalog.json
|   `-- whisper_processor.py
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
|-- assets/
|-- bin/
|-- models/
|-- output/
|-- projects/
|-- temp/
|-- README.md
|-- requirements.txt
|-- newSpec.md
`-- structure.md
```

## 9. Current Status

The repository has already moved significantly toward the target structure, but the transition is not complete.

What is already in place:

- core models and project state
- service layer for project and segment persistence
- workflow layer for prepare, voice, and export
- adapter layer for major engines
- split UI modules for views, controllers, widgets, and worker adapters

What is still transitional:

- several legacy processor modules still exist beside the adapter layer
- some UI compatibility shims still exist
- not every provider abstraction is fully isolated yet

## 10. Recommended Next Refactor Steps

1. Continue moving legacy processing modules fully behind the `engines` layer.
2. Keep translation, rewrite, TTS, and separation providers consistent behind service and adapter boundaries.
3. Reduce compatibility shims in the UI entry points.
4. Continue consolidating preview and export around one configuration source.
5. Keep documentation aligned with the real repository structure instead of the old target-only structure.
