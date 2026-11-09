# Project Structure

Version 9.0.0 also includes `browser_extension/` (Manifest V3 worker, content script, popup, app-logo icons and DOM/session tests), `app/translation/providers/browser_bridge_provider.py` (localhost bridge and browser AI provider), and `app/utils/browser_extension_utils.py` (runtime file collection and frozen/source install paths). The canonical `CapCap.spec` packages only extension runtime files, excluding Node dependencies and tests.

```text
CapCap/
├── ui/
│   ├── gui.py                 # Application entry point
│   ├── main_window.py         # Main-window behavior, adaptive layout, and signal handling
│   ├── editor_factory.py      # Quality/Performance editor creation and close/return wiring
│   ├── performance_editor.py  # Still preview, virtual subtitle table, layer editing
│   ├── i18n.py                # Internationalization catalog (Vietnamese/English)
│   ├── controllers/           # Pipeline, preview, and subtitle controllers
│   │   └── subtitle_controller.py # Translation prompt & pronoun review dialogs
│   ├── views/                 # Launcher, panels, preview, timeline, inspectors
│   │   └── processing_queue.py # Queue controls, processing vs export, project transfer
│   ├── widgets/               # Native MPV preview and custom Qt widgets
│   ├── worker_adapters/       # QThread background worker adapters
│   └── utils/                 # UI, media, audio engine, and settings helpers
├── app/
│   ├── capcut/                # CapCut STT, TTS, signing, and draft integration
│   ├── workflows/             # Prepare, voice, and export workflows
│   ├── translation/           # AI translation framework
│   │   ├── context_analyzer.py # Dialogue context learning, rolling ledger, pronoun rules
│   │   ├── orchestrator.py    # Pipeline orchestration, batching, provider routing
│   │   ├── prompts.py         # Genre-specific translation presets
│   │   ├── srt_utils.py       # SRT parsing, timing, and formatting utilities
│   │   └── providers/         # Google AI Studio, OpenAI, Ollama, Google Translate, Bing Web
│   ├── engines/               # Whisper, OCR, TTS, FFmpeg adapters
│   ├── services/              # Project, resource, ASR, diarization services (model_utils.py)
│   │   ├── processing_queue.py # Persistent jobs, processing ownership, export readiness
│   │   ├── project_work_mode.py # Persistent Quality/Performance launcher ownership
│   │   ├── video_data_cleanup.py # Generated-data cleanup and surviving project entries
│   │   └── voice_edit_service.py # Shared grouped TTS and per-cue metadata merging
│   ├── queue_worker.py        # Isolated process/full/export entrypoint; no editor/player
│   ├── layers/                # Timeline track and layer domain models
│   ├── vieneu_tts.py          # VieNeu TTS and voice cloning integration
│   ├── ocr_processor.py       # OCR subtitle extraction
│   ├── whisper_processor.py   # Faster-Whisper integration
│   └── sensevoice_processor.py
├── tests/                     # Comprehensive automated test suite (350+ unit tests)
│   ├── test_portrait_workspace.py # Tests for adaptive 9:16 layout
│   ├── test_performance_queue.py # Queue, render separation, still/OCR editing, lifecycle
│   ├── test_video_data_cleanup.py # Cleanup with real Windows file locks
│   ├── test_translation_context_review.py # Tests for pronoun review & feedback
│   └── ...
├── bin/                       # FFmpeg, MPV, on-demand CUDA runtime
├── installer/                 # Inno Setup script (CapCap_Setup.iss) and vc_redist.x64.exe
├── models/                    # Downloaded ASR, Piper, and diarization models
├── assets/                    # Icons, fonts, previews, voice samples, and image assets
│   ├── preview_1.png          # 16:9 Landscape workspace screenshot
│   ├── preview_2.png          # 9:16 Portrait workspace screenshot
│   ├── voices/                # Reference voice samples for cloning and preview
│   └── capcut/                # CapCut voice metadata definitions
├── docs/                      # Focused project documentation
├── release.md                 # Release notes and changelog
├── .env_example               # Optional environment template
└── requirements-*.txt         # Python dependency sets
```

Projects and generated artifacts are stored beneath `projects/`; temporary preview and processing files are stored beneath `temp/`.

`processing_queue.json` stores launcher jobs and the automatic-export preference outside the generated-data folders. Each project keeps its `ui_work_mode`, `processing_profile` (including its OCR crop) and processing readiness in `project.json`. `ui/worker_adapters/queue_workers.py` launches one isolated worker per video, supports process-only, process-and-export, and export-only tasks, and releases the UI lock after the worker exits. `ui/widgets/performance_preview.py` shares the Qt layer editors with a still image; `ui/utils/performance_config.py` captures/restores per-video settings without copying API keys. See [Performance mode](performance-mode.md).
