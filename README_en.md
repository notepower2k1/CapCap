# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

[English](README_en.md) | [Tiếng Việt](README.md)

| 🖥️ Landscape Layout (16:9) | 📱 Portrait Layout (9:16) |
| :---: | :---: |
| ![CapCap Landscape 16:9](assets/preview_1.png) | ![CapCap Portrait 9:16](assets/preview_2.png) |

### [🎬 Demo & Tutorial](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** is an all-in-one Windows desktop application for video localization and editing. It automates speech recognition, AI translation, voice-over generation, timed visual layer editing, and final video rendering.

---

## ✨ Highlights

* 🔄 **Guided 5-Step Pipeline:** Prepare → Transcribe (STT / OCR) → Translate → Voice-over (TTS) → Export.
* 📱 **Adaptive Workspace (16:9 & 9:16):** Automatically switches layout between landscape videos (YouTube/Cinema) and portrait videos (TikTok, Shorts, Reels).
* 🧠 **Context-Aware AI Translation & Pronoun Consistency:** Integrates Gemini 2.5, GPT-4o, DeepSeek, Ollama (offline), and Google/Bing Web Translate. Automatically infers character relationships and supports interactive natural-language feedback.
* 🎙️ **Multi-Voice TTS & BGM Separation:** Supports ElevenLabs TTS (studio-grade quality), Piper (offline), Edge TTS, CapCut TTS, and VieNeu TTS (voice cloning). Separates speech from background music (BGM) to preserve original soundtracks.
* 🎬 **Multi-Track Timeline Editor:** Visual subtitle editing, Logo, Blur, Mask, and Text overlays. Supports instant in-place voice regeneration for edited subtitle lines.
* ⚡ **High-Speed Hardware Rendering:** Accelerated by NVIDIA NVENC, supports standalone audio track export (Voice, BGM, Mix), and direct export to **CapCut Draft** projects.

---

## 📚 Documentation

* 📖 [How to Use](docs/how-to-use.md)
* 💻 [Requirements and Resources](docs/requirements.md)
* ⚙️ [Technical Stack](docs/technical-stack.md)
* 📁 [Project Structure](docs/project-structure.md)
* 📋 [Release Notes](release.md)

---

## 🛠️ Run from Source

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap

python -m venv venv
venv\Scripts\activate

pip install -r requirements-local.txt
python ui/gui.py
```

* Copy `.env_example` to `.env` only if you need custom API keys or remote server endpoints. Most settings can be configured directly in the UI.

### 💡 MPV Preview Player Troubleshooting

CapCap uses **libmpv** by default for smooth, frame-accurate preview:
* **Visual C++ Requirement:** `libmpv-2.dll` requires **Microsoft Visual C++ 2015–2022 Redistributable (x64)** (version >= 14.20).
* **If MPV fails to start:** Run the bundled installer at `installer\vc_redist.x64.exe` or download directly from [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).

---

## ❤️ Support CapCap

If CapCap is useful to you, consider supporting its continued development:

### 🇻🇳 Domestic Donations (Vietnam)
<img src="assets/qr.png" style="width: 20%; height: auto;">

### 🌍 International Donations
[![Buy Me a Coffee](assets/buymeacoffee.png)](https://buymeacoffee.com/hcaht)

---

## 📄 License

CapCap is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.
