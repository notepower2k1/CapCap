# CapCap

![CapCap Preview](assets/preview.jpg)

CapCap is a Windows desktop app for Vietnamese video localization. It supports subtitle creation, translation, subtitle editing, Vietnamese voice generation, preview, and export in a single project-based workflow.

## Core Features

- Project-based workflow with resume support
- Output modes:
  - `subtitle only`
  - `voice only`
  - `subtitle + voice`
- Speech-to-text with `faster-whisper`
- Subtitle translation with web / AI providers
- AI polish / rewrite for subtitle cleanup
- Vietnamese voice generation with `Piper` or `edge-tts`
- Audio mix and export with `FFmpeg`
- Video preview with subtitle overlay
- Remote mode for offloading heavy `Whisper + AI` work to another PC

## Editor UI / Tools

The current desktop editor includes:

- Top header actions:
  - `Generate`
  - `Export`
  - `More`
- Left workflow panel:
  - `Media`
  - `Language`
  - `Voice`
  - `Style`
  - `Advanced`
- Always-visible `Status` summary card
- Video preview area with subtitle overlay
- Playback controls between preview and timeline
- Multi-lane timeline:
  - `Video`
  - `Audio`
  - `Subtitle`
- Subtitle Inspector for the selected subtitle block

Current editor interactions:

- Click audio/subtitle blocks on the timeline to select a segment
- Resize audio blocks to adjust timing
- Subtitle timing in the inspector follows timeline edits
- Export uses the latest in-memory subtitle timing

`More` menu actions:

- `Subtitle`
- `Original Script`
- `Clean`
- `Hide Controls`
- `Settings`

## Technical Stack

- `PySide6` for desktop UI
- `libmpv` / media backend for preview
- `QThread` workers for background processing
- `faster-whisper` for local ASR
- `FFmpeg` for extract, mix, mux, export
- `Demucs` for vocal/background separation
- `Piper TTS` and `edge-tts`
- `llama-cpp-python` for local AI rewrite / polish
- `requests` for remote integrations
- `PyInstaller` for packaging

## Run From Source

Clone the repo:

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap
```

Install a profile:

### Local

```bash
pip install -r requirements-local.txt
python ui/gui.py
```

### Remote Client

```bash
pip install -r requirements-remote.txt
python ui/gui_remote.py
```

### Remote Server

```bash
pip install -r requirements-server.txt
python app/remote_api_server.py
```

## Packaging

### Local release

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.spec --noconfirm --clean
```

### Local debug

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.debug.spec --noconfirm --clean
```

### Remote client

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.remote.spec --noconfirm --clean
```

### Server

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.server.spec --noconfirm --clean
```

## Repo Guide

See [structure.md](./structure.md) for a codebase map and important entrypoints.

## Notes

- The app is currently optimized for Windows.
- Some AI / ASR / separation steps can be slow on weaker machines.
- Remote mode currently focuses on `Whisper + AI translation/rewrite`; preview/export still run locally on the client.
