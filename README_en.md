# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

[English](README_en.md) | [ Tiếng Việt](README.md)

![CapCap Editor Preview](assets/preview.JPG)

### [🎬 Demo & Tutorial](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** is a Windows desktop application for video localization, designed to simplify the entire workflow from transcription and translation to voice-over, visual editing, and final export.

It supports creating **Vietnamese and English subtitles**, translating video content, generating speech with TTS, and editing timed visual layers directly on the timeline.

## ✨ Highlights

* **Guided 5-Step Pipeline:** **Prepare → Transcript → Translate → TTS → Export**
* **Fast & Accurate Speech-to-Text (STT):** Powered by **Faster-Whisper** and **SenseVoice** with GPU (CUDA) and CPU hardware acceleration.
* **Hardcoded Subtitle Extraction via OCR:** Extract subtitles directly from video frames using **RapidOCR**.
* **Intelligent AI Translation & Pronoun Consistency Control:**
  * **Auto-Detect Dialogue Context & Character Profiles:** Automatically analyzes the transcript to identify character genders, social roles, and mandatory two-way address rules (quy tắc xưng hô 2 chiều).
  * **Interactive Character & Pronoun Review Dialog:** Allows reviewing and editing character rules before translating, or providing **natural-language feedback** (e.g. *"Swap character A and B roles"*, *"Use formal pronouns"*) for AI to re-analyze accurately.
  * **Rolling Context Ledger:** Maintains confirmed character address rules and preceding dialogue boundary cues across sequential batches for 100% address continuity in long videos.
  * **Genre-Specific Translation Presets:** Built-in optimized prompts for Short Dramas/Douyin, Romance, Wuxia/Xianxia, Anime/Manga, K-Drama, Vlogs/TikTok, Documentaries, etc.
  * **Multi-Provider AI & Web Translation Support:** **Google AI Studio (Gemini 2.5/1.5)**, **OpenAI (GPT-4o)**, **DeepSeek**, **Ollama (local offline models)**, alongside **Google Translate** and **Bing Translate** (free web translation engines without API keys, with automatic fallback to Bing when Google Translate hits rate limits).
* **Versatile TTS & Voice Cloning:**
  * Supports **Piper TTS** (offline), **Edge TTS**, **CapCut TTS**, and **VieNeu TTS** (voice cloning).
  * Optional **Speaker Diarization** to detect unique speakers and assign distinct voices per character.
* **Multi-Layer Audio Management & Vocal/BGM Separation:**
  * Split original vocals from background music (BGM). Adjust volume, gain, speed, or mute individual audio tracks for preview without losing the music layer when applying TTS voices.
* **Smooth Native MPV Media Preview:**
  * High-performance video and audio playback using **libmpv**, instantly synchronized with the Timeline playhead.
  * Includes **Fast Preview** (5-second quick sample with all layers rendered) and **Exact Frame Preview**.
* **Multi-Track Visual Timeline Editor:**
  * Manage multiple timed layers: Subtitles, Blur regions, Logos, Masks, Text overlays, and Selection ranges.
  * Supports layer locking, visibility toggling, and range re-transcription (**Alt: OCR/Whisper**).
* **Intelligent NVENC Hardware Encoding & Quality Profiles:**
  * 4 export profiles (**Low, Medium, High, Very High**) leveraging **NVIDIA NVENC** GPU acceleration with seamless automatic fallback to CPU `libx264`.
  * Direct export to **CapCut Draft** projects for advanced post-production.
* **Smart Resource Manager:** Convenient 1-click downloads for models and dependencies (Whisper Turbo/Large-v3, SenseVoice, TTS voices, CUDA acceleration pack) with real-time download progress tracking in MB and percentage.
* **Optimized Project Loading & Visual Caching:** Asynchronous timeline waveform and thumbnail extraction with interactive loading cards, ensuring a fast and freeze-free experience.

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

### 💡 MPV Video Preview Troubleshooting & Setup

CapCap uses **libmpv** by default for smooth, frame-accurate video playback and preview:
* **Visual C++ Requirement:** `libmpv-2.dll` requires **Microsoft Visual C++ 2015–2022 Redistributable (x64)** (version >= 14.20).
* **If MPV fails to load** (shows an error or falls back to Qt player): Run the bundled installer at `installer\vc_redist.x64.exe` or download the latest official installer from [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).
* **Updating / Using External MPV Library:** If you need to update or provide a custom 64-bit MPV library, place `libmpv-2.dll` into the `bin/mpv/` directory.

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
