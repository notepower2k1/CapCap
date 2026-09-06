# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

[English](README_en.md) | [ Tiếng Việt](README.md)

![CapCap Editor Preview](assets/preview.JPG)

### [🎬 Demo & Tutorial](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** is a Windows desktop application for video localization, designed to simplify the entire workflow from transcription and translation to voice-over, visual editing, and final export.

It supports creating **Vietnamese and English subtitles**, translating video content, generating speech with TTS, and editing timed visual layers directly on the timeline.

## ✨ Highlights

* Guided workflow: **Prepare → Transcript → Translate → TTS → Export**
* Speech-to-text transcription with **Faster-Whisper** or **SenseVoice**
* Extract existing subtitles from video using **OCR**
* Support for multiple cloud/API translation providers, with **Google Translate** as a fallback
* Versatile Text-to-Speech engines: **Piper TTS**, **Edge TTS**, **CapCut TTS**, and **VieNeu TTS**
* **Voice Cloning** support with a rich library of voice samples
* Optional speaker diarization and per-speaker voice assignment
* Direct export to **CapCut Draft** for advanced post-production editing
* Timeline-based editor with support for:
  * Subtitles
  * Blur regions
  * Logos
  * Masks
  * Text layers
  * Selection ranges
* Layer locking to prevent accidental edits
* **Fast Preview** for quickly reviewing edits without a full export
* **Intelligent Export Quality Profiles**: Flexible export options (**Low, Medium, High, Very High**) with automatic hardware acceleration (NVIDIA NVENC / CPU libx264) to optimize file size and render speed

## 🚀 Upcoming Features

CapCap is actively being developed, with new features and improvements added over time.

👉 [View the development roadmap](https://github.com/users/notepower2k1/projects/2)

## 📚 Documentation

* [How to Use](docs/how-to-use.md)
* [Requirements and Resources](docs/requirements.md)
* [Technical Stack](docs/technical-stack.md)
* [Project Structure](docs/project-structure.md)

## 🛠️ Run from Source

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap

python -m venv venv
venv\Scripts\activate

pip install -r requirements-local.txt
python ui/gui.py
```

You only need to copy `.env_example` to `.env` if you want to manually configure translation providers or remote servers.

Most CapCap settings can be configured directly from within the application.

## ❤️ Support CapCap

If CapCap is useful to you, consider supporting its continued development and maintenance.

### 🇻🇳 Donate in Vietnam

Scan the QR code below:

<img src="assets/qr.png" style="width: 25%; height: auto;">

### 🌍 International Donations

[![Buy Me a Coffee](assets/buymeacoffee.png)](https://buymeacoffee.com/hcaht)

Click the image above or visit [Buy Me a Coffee](https://buymeacoffee.com/hcaht).

## 📄 License

CapCap is licensed under the **Apache License 2.0**.

See [LICENSE](LICENSE) for details.
