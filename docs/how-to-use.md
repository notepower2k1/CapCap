# How to Use CapCap

## 🖥️ Workspace Layouts (16:9 Landscape & 9:16 Portrait)

CapCap automatically optimizes its workspace layout based on the video's aspect ratio:

| 🖥️ Landscape Mode (16:9) | 📱 Portrait Mode (9:16) |
| :---: | :---: |
| ![CapCap 16:9 Workspace](../assets/preview_1.png) | ![CapCap 9:16 Workspace](../assets/preview_2.png) |

- **Landscape Mode (16:9)**: Designed for widescreen videos (YouTube, Movies). Video preview sits on the left, Subtitle Inspector on the right, and Timeline across the bottom.
- **Portrait Mode (9:16)**: Specially engineered for vertical videos (TikTok, Shorts, Reels). The phone preview occupies the entire right column without letterboxing. The middle column pairs the Subtitle Inspector with a compact 5-tool Layer Toolbar (`[T]`, `[Logo]`, `[Blur]`, `[Mask]`, `[OCR]`) docked at 204px height, maximizing vertical space for the Timeline below.
- **Auto-Detection**: Opening any vertical video (`height > width`) automatically defaults to the Portrait Workspace. You can also toggle layouts manually anytime from the **More (Mở rộng)** menu or top bar action.

---

## Basic Workflow

1. Open CapCap and select **CPU** or **GPU** mode in the launcher.
2. Create or open a video project. **Prepare** completes once the video is analyzed and ready.
3. In **Settings**, choose a Subtitle Source: **Audio** (SenseVoice or Faster-Whisper) or **Video** (RapidOCR). This choice is saved per-project.
4. Set source and target languages, then select a translation provider.
5. Use **Generate**:
   - **Full Pipeline**: Runs Transcript → Translate → TTS end-to-end automatically.
   - **Step-by-Step**: Runs each stage individually so you can inspect and adjust results between stages.
   - **Step-by-Step → Run to Transcript**: Choose **Auto Transcript** or **Import Transcript File…** to load an external original `.srt`. Imported cues and timestamps remain the transcript source until you explicitly choose **Re-transcript**.
6. Review subtitles, speaker assignments, typography, and timed visual layers in the editor.
7. Use **Fast Preview** to check a five-second rendered sample, or playback smoothly in real-time.
8. Click **Export** to render the final localized video or export to **CapCut Draft**.

### Whisper transcript cleanup

Open **Settings → Transcription** while **Audio (Whisper)** is selected to adjust two cleanup options. Both are enabled by default and apply to new Whisper transcriptions:

- **Reduce repeated phrases** helps remove duplicate wording where audio sections join, keeping the fuller sentence when possible. For example, “Today we are” followed by “Today we are going to school” can become the fuller sentence. Dialogue repeated at other times is kept. Turn this off to keep Whisper's recognized wording as-is; timestamps are still normalized for the timeline.
- **Reduce made-up subtitles during silence** helps reduce subtitles that were not spoken, such as an unexpected “Thanks for watching” during silence. It can slow transcription or miss very quiet speech, and it cannot guarantee that every invented phrase is removed. This option works independently of repeated phrase removal.

Open **Advanced** only if you want to adjust the filters. You can keep the defaults if you are unsure: **82%** text similarity and **3 seconds** of silence. Similarity ranges from 50% to 100%; 100% matches identical normalized text only. An informative phrase contained in a fuller cue can also match when timing supports it, unless similarity is set to 100%. The silence setting ranges from 0.5 to 10 seconds.

Saving these settings does not change an imported transcript or start a new transcription.

---

## AI Translation, Presets & Pronoun Control

CapCap includes an advanced AI dialogue analysis and pronoun consistency framework:

1. **Translation Settings & Presets**:
   - In the **Translation Settings & Prompt Review** dialog, choose from built-in genre presets:
     - *Chinese → Vietnamese*: Short Drama / Douyin / Comedy, Modern Romance / Urban, Wuxia / Xianxia / Period Drama.
     - *Japanese → Vietnamese*: Anime / School / Slice of Life, Isekai / Fantasy / Action.
     - *Korean → Vietnamese*: K-Drama / Modern / Romance, Hunter / Dungeon / Manhwa.
     - *English → Vietnamese*: Vlog / Social Media / TikTok, Movies / Casual Dialogue, Documentary / Tech / Business.
   - Choose your AI provider: **Google AI Studio (Gemini 2.5 Flash / Pro)**, **OpenAI (GPT-4o / GPT-4o-mini)**, **DeepSeek**, or local offline models via **Ollama**.
   - **Fast Web Translation**: Use **Google Translate** or **Bing Translate** for quick, keyless translations. If Google Translate encounters rate limiting (HTTP 429), CapCap automatically falls back to Bing Translate to keep batch translation running smoothly.

2. **Auto-Detect Dialogue Context & Pronouns**:
   - When enabled, CapCap performs an initial dialogue analysis pass over the transcript cues.
   - It extracts character identities (standard Hán-Việt for Chinese names, Romaji/Latin for Japanese/Korean), genders, social roles (e.g. senior/junior student, boss, antagonist), and strict **Two-Way Address Rules** (quy tắc xưng hô 2 chiều: who calls who what, e.g. anh - em, mày - tao, tôi - anh).

3. **Interactive Character & Pronoun Review Dialog**:
   - When **"Review character & pronoun rules before translating"** is checked, CapCap pauses before translating and opens the review dialog.
   - **Direct manual editing**: You can freely edit character names, gender roles, and pronoun pairs in the text box. The translation engine strictly adheres to these rules.
   - **Feedback Guidance**: If the AI misidentified character roles or genders, simply type your guidance into the feedback box (e.g. *"Ngược 2 nhân vật A và B rồi, đảo lại"* or *"Nhân vật X là nữ sinh, xưng mày - tao với kẻ xấu"*) and click **🔄 Re-analyze with Feedback** (or press **Enter**). The AI will regenerate the profile based on your instructions.
   - **Skip Rules**: Click to proceed with raw translation without enforcing explicit pronoun rules.

4. **Rolling Context Ledger for Long Videos**:
   - For long videos exceeding a single batch, CapCap maintains a sequential memory ledger:
     - Confirmed pronoun rules established in earlier batches are preserved and injected into subsequent batches.
     - Preceding boundary dialogue cues are included for speaker tone continuity.
     - Prevents pronoun flipping (lật ngôi) across scene transitions.

---

## Audio Track & Background Music (BGM) Management

- **Vocal and Music Separation**: CapCap automatically separates speech (Vocals) and background music (BGM/no_vocals). Accelerated by GPU (CUDA) when available.
- **Independent Audio Controls**: In the Audio Settings panel, adjust volume, gain, speed, or mute individual tracks for preview.
- **BGM Preservation**: When generating Vietnamese TTS voice-overs, the original background music track is retained and mixed cleanly beneath the synthesized speech.
- **Export Standalone Audio Tracks**: Under **More (Mở rộng) → Export Audio**, you can export:
  - **Export Dubbed Voice**: Saves the synthesized TTS voice track as a separate audio file.
  - **Export Background Music (BGM)**: Saves the clean extracted background music track.
  - **Export Mixed Audio**: Saves the complete multi-track audio mix (voice + BGM).

---

## Media Preview (MPV Backend) & Frame Inspector

- **Native MPV Playback**: Smooth, hardware-accelerated playback of video and audio powered by `libmpv` (with seamless Qt Multimedia fallback).
  - *Troubleshooting*: If the MPV player fails to start (error 126 or missing DLLs), install Microsoft Visual C++ Redistributable x64 via `installer\vc_redist.x64.exe` or download directly from [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- **Timeline Synchronization**: Dragging or scrubbing the Timeline playhead seeks the MPV player instantly.
- **Exact Frame Preview & Large Frame Preview**: Inspect the current frame with pixel precision to review visual overlays, hardcoded subtitle positions, and blur masks.
- **Fast Preview**: Quickly render a 5-second sample of the current timeline section to check subtitle formatting, audio mix, and visual layers before performing a full export.

---

## Transcript & Subtitle Editing

- Select a TS1 segment to edit its text, timing, speaker, or voice speed in the Subtitle Inspector.
- **In-Place Voice Regeneration (Tạo lại TTS tức thì)**: If you edit a subtitle line or modify its voice/speed, the segment is flagged with an indicator. Click the regenerate button (`Tạo lại TTS`) on that segment or inspector to re-synthesize speech immediately without re-rendering the rest of the timeline.
- Use **+ Layer → Subtitle Segment** to add a missing subtitle at the playhead.
- Use the timeline **Selection Range** and **Alt: OCR/Whisper** to re-transcribe only a problematic section with the opposite recognition engine.
- **Multi-Row Vertical Drag-and-Drop**: Easily drag and organize subtitle segments across timeline rows with collision avoidance.

---

## Subtitle Styling and Presets

- Configure subtitle font typography, size, primary/border/shadow styling, and positioning in the Subtitle Styling panel.
- **My Presets**: Manage custom subtitle styles through the **My Presets** dropdown. Click **+ Save This Style** to save the current configuration, and use the **Rename** and **Delete** buttons to update or remove presets.
- **Keyword Highlight**: Emphasize important keywords dynamically (Auto, Manual, or Auto + Manual) with configurable accent colors.

---

## Timeline Editing & Visual Layers

- **Visual Layers**: Add Blur, Logo, Mask, and Text layers to the timeline. Each layer supports custom coordinates, edge resizing, duration trimming, and timeline splitting.
- **Export Without Subtitles**: You can add and position visual layers (e.g. logo watermark, blur regions) and export the video directly even if no subtitles or audio tracks exist.
- Use **Select Range** to create an interval on the ruler. Clear it when finished.
- Use the lock icon in an editable track header to prevent accidental edits.

---

## Speaker Diarization

Enable **Speaker Diarization** in Media before transcription when using Audio source. Detected speakers are colour-coded on TS1. In Voice → Detected Speakers, assign a voice per speaker; in Subtitle Inspector, correct an individual segment's speaker assignment.

---

## OCR Translator

OCR Translator is independent of subtitle transcription. Open it from the preview toolbar, position its region, capture visible text, then translate or copy the result. It does not modify subtitles, timeline data, or project transcript.

---

## Text-to-Speech and Voice Cloning

- CapCap supports multiple TTS engines: **ElevenLabs TTS** (high-fidelity studio quality), **Piper TTS** (local offline), **Edge TTS** (online Microsoft voices), **CapCut TTS** (expressive online voices), and **VieNeu TTS** (Vietnamese voice cloning).
- Open the **Voice Clone** dialog to clone voices from custom reference audio files or pick from bundled sample voices.
- Voice pitch, rate, and volume can be adjusted per speaker or per individual subtitle segment in the Subtitle Inspector.

### ElevenLabs TTS Setup & Permissions

To use ElevenLabs high-quality voices in CapCap:

1. **Obtain an API Key**: Sign up or log in at [ElevenLabs](https://elevenlabs.io) and create an API key from your profile settings.
2. **Required Permissions**: Ensure the API key has the following 4 permissions enabled:
   - `Text to Speech: Access` — Allows CapCap to synthesize speech from subtitles.
   - `Voices: Read` — Allows CapCap to list your available default, cloned, and generated voices.
   - `Voice collection: Read` — Allows CapCap to browse shared voices and voice library catalogs.
   - `User: Access` — Allows CapCap to inspect your account tier (Free / Starter / Creator / Pro) and remaining character quota.
3. **Configure in CapCap**:
   - Open **Settings → ElevenLabs** tab.
   - Enter your API Key and click **Save Key**. CapCap will validate the key, retrieve your account tier and quota, and populate your voice catalog asynchronously in the background.
   - Select your preferred model: `eleven_turbo_v2_5` (recommended default for optimal stability and natural quality), `eleven_flash_v2_5` (ultra-fast, economical), `eleven_v4`, `eleven_v4_turbo`, or `eleven_v3`.
4. **Smart Tier & Quota Safeguards**:
   - Free accounts are automatically capped at 2 parallel worker threads, while paid subscriptions unlock 3–4 workers for faster batch generation.
   - Community Voice Library voices requiring paid subscriptions are badged with `🔒 (Cần gói trả phí)` and disabled on Free tier to prevent unintended HTTP 402 errors and quota loss.
   - Built-in preview allows testing voice samples directly from ElevenLabs without downloading offline models.

---

## Video Export and Quality Profiles

When clicking **Export**, the **Export Summary** dialog displays output parameters (resolution, frame rate, audio configuration, subtitle styling, and visual layers) and lets you choose a **Video Quality / Compression** profile:

- **Balanced (Recommended)**: The default profile, balancing detail retention and encoding speed (CRF 22 / CQ 25, fast/p3 preset).
- **Low (Fastest - Smallest File Size)**: Faster export and smaller files, with some loss of fine detail (CRF 26 / CQ 28, veryfast/p2 preset).

Existing High or Very High settings use Balanced. These compression profiles do not change the separately selected output resolution or restore detail missing from the source video.

**Hardware Acceleration & Automatic CPU Fallback**:
Export automatically leverages NVIDIA NVENC GPU acceleration when available. On systems without an NVIDIA GPU or missing CUDA drivers, CapCap automatically falls back to multithreaded CPU encoding (`libx264`) seamlessly.
