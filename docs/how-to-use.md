# How to Use CapCap

## Basic workflow

1. Open CapCap and select CPU or GPU mode in the launcher.
2. Create or open a video project. **Prepare** becomes complete once the video is ready.
3. In **Settings**, choose a Subtitle Source: Audio (SenseVoice or Whisper) or Video (OCR). This choice is saved with the project, not globally.
4. Set source/target language and choose a translation provider.
5. Use **Generate**:
   - **Full Pipeline** runs Transcript → Translate → TTS.
   - **Step-by-Step** runs each stage in order. At TTS, choose **TTS** or **Skip**.
6. Review subtitles, speaker assignments, style, and timed layers in the editor.
7. Use **Fast Preview** to check a five-second rendered sample, then export.

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
   - It extracts character identities (standard Hán-Việt for Chinese names, Romaji/Latin for Japanese/Korean), genders, social roles (e.g. senior/junior student, boss, stalker/antagonist), and strict **Two-Way Address Rules** (quy tắc xưng hô 2 chiều: who calls who what, e.g. anh - em, mày - tao, tôi - anh).

3. **Interactive Character & Pronoun Review Dialog**:
   - When **"Review character & pronoun rules before translating"** is checked, CapCap pauses before translating and opens the review dialog.
   - **Direct manual editing**: You can freely edit character names, gender roles, and pronoun pairs in the text box. The translation engine will strictly adhere to whatever is in this box.
   - **Góp ý / Re-analyze with Feedback**: If the AI misidentified character roles or genders (e.g. characters are reversed), simply type your guidance into the feedback box (e.g. *"Ngược 2 nhân vật Triều Tịch và Lưu Giai Ngọc rồi, đảo lại"* or *"Triều Tịch là nữ sinh viên năm 1, kẻ bám đuôi xưng mày - tao"*) and click **🔄 Re-analyze with Feedback** (or press **Enter**). The AI will cross-reference the previous draft and regenerate a corrected profile according to your instructions.
   - **Skip Rules**: Click to proceed with raw translation without enforcing explicit pronoun rules.
   - **Do not show again**: Check this box to skip future confirmation popups while keeping auto-detection active in the background.

4. **Rolling Context Ledger for Long Videos**:
   - For long videos exceeding a single batch, CapCap maintains a sequential memory ledger:
     - Confirmed pronoun rules established in earlier batches are preserved and injected into subsequent batches.
     - Preceding boundary dialogue cues (tail of the previous batch) are included for speaker tone continuity.
     - Prevents pronoun flipping (lật ngôi) across scene transitions.

## Audio Track & Background Music (BGM) Management

- **Vocal and Music Separation**: CapCap automatically separates speech (Vocals) and background music (BGM/no_vocals).
- **Independent Audio Controls**: In the Audio Settings panel, adjust volume, gain, speed, or mute individual tracks for preview.
- **BGM Preservation**: When generating Vietnamese TTS voice-overs, the original background music track is retained and mixed cleanly beneath the synthesized speech.

## Media Preview (MPV Backend) & Frame Inspector

- **Native MPV Playback**: Smooth, hardware-accelerated playback of video and audio powered by `libmpv` (with seamless Qt Multimedia fallback).
  - *Troubleshooting*: If the MPV player fails to start (e.g. error 126 or missing DLLs), install Microsoft Visual C++ Redistributable x64 via `installer\vc_redist.x64.exe` or download directly from [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- **Timeline Synchronization**: Dragging or scrubbing the Timeline playhead seeks the MPV player instantly.
- **Exact Frame Preview & Large Frame Preview**: Inspect the current frame with pixel precision to review visual overlays, hardcoded subtitle positions, and blur masks.
- **Fast Preview**: Quickly render a 5-second sample of the current timeline section to check subtitle formatting, audio mix, and visual layers before performing a full export.

## Transcript editing

- Select a TS1 segment to edit its text, timing, speaker, or voice speed in the Subtitle Inspector.
- Use **+ Layer → Subtitle Segment** to add a missing subtitle at the playhead.
- Use the timeline **Selection Range** and **Alt: OCR/Whisper** to re-transcribe only a problematic section with the opposite recognition engine.
- Alt Transcribe only changes transcription for the selected range; it does not run Translate, TTS, or Export.

## Subtitle Styling and Presets

- Configure subtitle font typography, size, primary/border/shadow styling, and positioning in the Subtitle Styling panel.
- **My Presets**: Manage custom subtitle styles through the **My Presets** dropdown. Click **+ Save This Style** to save the current configuration, and use the **Rename** and **Delete** buttons to update or remove presets.
- **Keyword Highlight**: Emphasize important keywords dynamically (Auto, Manual, or Auto + Manual) with configurable accent colors.

## Timeline editing

- Use **Select Range** to create an interval on the ruler. Clear it when finished.
- Select a layer, then use **Split** or **Delete**. A range supplies split boundaries but never changes the selected target layer.
- Use the lock icon in an editable track header to prevent edits without affecting preview or export.
- **Layers** hides/shows whole tracks in the timeline only; it does not affect preview or export.

## Speaker diarization

Enable **Speaker Diarization** in Media before transcription when using Audio source. Detected speakers are colour-coded on TS1. In Voice → Detected Speakers, assign a voice per speaker; in Subtitle Inspector, correct an individual segment's speaker assignment.

## OCR Translator

OCR Translator is independent of subtitle transcription. Open it from the preview toolbar, position its region, capture visible text, then translate or copy the result. It does not modify subtitles, timeline data, or project transcript.

## Text-to-Speech and Voice Cloning

- CapCap supports multiple TTS engines: **Piper TTS** (local offline), **Edge TTS** (online Microsoft voices), **CapCut TTS** (expressive online voices), and **VieNeu TTS**.
- Open the **Voice Clone** dialog to clone voices from custom reference audio files or pick from bundled sample voices.
- Voice pitch, rate, and volume can be adjusted per speaker or per individual subtitle segment in the Subtitle Inspector.

## Layers and export

- Blur, Logo, Mask, and Text layers support direct positioning, timing fields, edge resizing, and timeline splitting. Text layers and subtitles are included in Fast Preview and final export.
- You can export directly to a **CapCut Draft** project to continue advanced video editing and styling inside CapCut.

## Video Export and Quality Profiles

When clicking **Export**, the **Export Summary** dialog displays output parameters (resolution, frame rate, audio configuration, subtitle styling, and visual layers) and lets you choose a **Video Quality / Compression** profile:

- **Medium (Recommended - Balanced)**: Balanced sharpness and encoding speed (CRF 22 / CQ 25, fast/p3 preset). Ideal for web and social media.
- **High (High Quality)**: High detail retention (CRF 18 / CQ 22, medium/p4 preset).
- **Very High (Maximum Quality)**: Near-lossless master quality with deep motion estimation (CRF 15 / CQ 18, slow/p5 preset).
- **Low (Fastest - Smallest File Size)**: Maximum compression and fastest render speed (CRF 26 / CQ 28, veryfast/p2 preset). Ideal for quick draft review.

**Hardware Acceleration & Automatic CPU Fallback**:
Export automatically leverages NVIDIA NVENC GPU acceleration when available. On systems without an NVIDIA GPU, missing CUDA drivers, or in case of encoder errors, CapCap automatically falls back to multithreaded CPU encoding (`libx264`) seamlessly.

