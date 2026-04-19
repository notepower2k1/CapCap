# CapCap Code Structure

This file maps the main folders, entrypoints, and responsibilities in the repo.

## Top Level

```text
CapCap/
|- app/                     Core processing, services, engines, workflows
|- assets/                  Images, icons, UI assets
|- bin/                     Bundled native binaries such as ffmpeg
|- models/                  Downloaded models and voice packs
|- output/                  Generated output files
|- projects/                Persisted project data
|- temp/                    Temporary working files
|- tools/                   Extra helper scripts / utilities
|- ui/                      Desktop UI, views, widgets, controllers
|- README.md
|- structure.md
|- requirements*.txt
|- CapCap*.spec             PyInstaller specs
```

## Main Entrypoints

- `ui/gui.py`
  - Local desktop app entrypoint
- `ui/gui_remote.py`
  - Remote client entrypoint
- `app/remote_api_server.py`
  - Remote server entrypoint
- `ui/main_window.py`
  - Main `VideoTranslatorGUI` class and runtime UI behavior

## UI Layer

```text
ui/
|- controllers/
|  |- pipeline_controller.py   Run / advance the main processing flow
|  |- preview_controller.py    Preview generation and export orchestration
|  |- subtitle_controller.py   Subtitle editing, rewrite, import, preview helpers
|
|- helpers/
|  |- presentation_helpers.py  UI labels, status text, export labels
|  |- srt_helpers.py           SRT formatting / parsing helpers
|
|- utils/
|  |- display_utils.py         Dialogs, logs, processed-file helpers
|  |- file_dialog_utils.py     Browse/open helpers
|  |- icon_utils.py            Icon loading
|  |- media_backend.py         Qt/mpv backend abstraction
|  |- media_utils.py           Playback helpers, duration/position sync
|  |- settings_utils.py        Save/load UI settings
|
|- views/
|  |- main_window.py           Main layout builder
|  |- preview_panel.py         Preview, controls, timeline, inspector layout
|  |- start_panel.py           Left workflow panel layout
|  |- advanced_tabs.py         Advanced tab layout
|
|- widgets/
|  |- video_view.py            Qt video view + subtitle overlay layout
|  |- mpv_video_view.py        mpv-backed preview widget
|  |- subtitle_overlay.py      Subtitle overlay painting / blur overlay
|  |- timeline_widget.py       Timeline lanes and editing interactions
|  |- progress_dialog.py       Background progress dialog
|
|- worker_adapters/
|  |- processing_workers.py    Long-running processing workers
|  |- preview_workers.py       Preview generation workers
```

## App Layer

```text
app/
|- core/
|  |- models/
|  |  |- segment.py            Core segment model
|  |  |- chunk.py              Chunk model
|  |- state/
|     |- project_state.py      Persisted project state model
|
|- engines/
|  |- ffmpeg_adapter.py        FFmpeg operations
|  |- whisper_adapter.py       Whisper runtime adapter
|  |- translator_adapter.py    Translation adapter
|  |- tts_adapter.py           TTS adapter
|  |- preview_adapter.py       Preview rendering adapter
|  |- demucs_adapter.py        Vocal/background separation adapter
|  |- remote_*_adapter.py      Remote API adapters
|
|- services/
|  |- project_service.py       Project persistence and file layout
|  |- gui_project_bridge.py    Bridge between UI state and project state
|  |- workflow_runtime.py      Workflow runtime helpers
|  |- engine_runtime.py        Runtime selection / dependency helpers
|  |- voice_catalog_service.py Voice catalog loading
|  |- resource_download_service.py Resource/model downloads
|  |- segment_service.py       Segment operations
|  |- segment_regroup_service.py Segment regrouping
|  |- chunking_service.py      Chunk creation
|  |- asr_merge_service.py     ASR merge helpers
|
|- translation/
|  |- orchestrator.py          Translation orchestration
|  |- srt_utils.py             Subtitle translation helpers
|  |- models.py                Translation models
|  |- errors.py                Translation errors
|  |- providers/
|     |- google_web_translator.py
|     |- microsoft_translator.py
|     |- ai_polisher.py
|     |- local_polisher.py
|     |- gemini_polisher.py
|
|- workflows/
|  |- prepare_workflow.py      Project preparation
|  |- voice_workflow.py        Voice generation workflow
|  |- export_workflow.py       Export workflow
|
|- subtitle_builder.py         Build SRT/ASS from segments
|- preview_processor.py        Preview rendering helpers
|- video_processor.py          Video processing helpers
|- audio_mixer.py              Audio mix logic
|- tts_processor.py            Voice generation helpers
|- translator.py               Translation entry helpers
|- whisper_processor.py        Whisper processing helpers
|- vocal_processor.py          Separation helpers
|- remote_api.py               Remote API client helpers
|- remote_api_server.py        Remote HTTP server
|- runtime_paths.py            Path resolution
|- runtime_profile.py          Local vs remote runtime profile
```

## Important Runtime Flows

### Generate / pipeline

- UI trigger: `ui/main_window.py`
- Controller: `ui/controllers/pipeline_controller.py`
- Services/workflows: `app/services/*`, `app/workflows/*`
- Engines: `app/engines/*`

### Preview / export

- UI trigger: preview controls / export button
- Controller: `ui/controllers/preview_controller.py`
- Preview widgets: `ui/views/preview_panel.py`, `ui/widgets/*`
- Subtitle output: `app/subtitle_builder.py`

### Timeline / subtitle editor

- Timeline widget: `ui/widgets/timeline_widget.py`
- Preview panel layout: `ui/views/preview_panel.py`
- Inspector syncing and editor logic: `ui/main_window.py`

## Generated / Runtime Data

- `projects/`
  - persisted project state and artifacts
- `output/`
  - exported final results
- `temp/`
  - preview/intermediate files
- `models/`
  - downloaded ASR / AI / voice resources

## Files You Will Most Likely Edit

- `ui/main_window.py`
  - runtime UI logic, sync, state updates
- `ui/views/start_panel.py`
  - left workflow panel layout
- `ui/views/preview_panel.py`
  - preview / timeline / inspector layout
- `ui/views/advanced_tabs.py`
  - advanced controls layout
- `ui/widgets/timeline_widget.py`
  - timeline behavior and editing
- `ui/widgets/video_view.py`
  - preview area sizing and subtitle overlay placement
- `ui/widgets/mpv_video_view.py`
  - mpv-backed preview and overlay rect logic
- `ui/controllers/preview_controller.py`
  - preview/export behavior
- `app/subtitle_builder.py`
  - SRT/ASS generation

## Current Notes

- The repo contains some generated `__pycache__` and temporary files such as `_write_test.tmp`.
- UI work is concentrated in `ui/views`, `ui/widgets`, and `ui/main_window.py`.
- Project/export behavior lives mostly in `ui/controllers` and `app/workflows`.
