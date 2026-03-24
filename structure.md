# CapCap Structure

## 1. Muc tieu

CapCap la desktop tool local cho workflow localize video sang tieng Viet theo huong project-based:

1. import video
2. extract audio
3. transcribe transcript
4. translate sang tieng Viet
5. AI refine ban dich khi can
6. tao subtitle de preview va chinh sua
7. tao TTS tieng Viet
8. mix voice voi background
9. export video theo mode nguoi dung chon

Pipeline duoc thiet ke theo mo hinh ban tu dong:

- `Prepare` chay cac buoc nen co the tu dong hoa
- cac buoc subtitle, TTS, mix, preview, export duoc giu o che do co kiem soat de user review truoc khi xuat file

## 2. Processing Model

```text
Project
  -> Import source video
  -> Prepare
       -> Extract audio
       -> Transcribe
       -> Translate raw
       -> Refine translation (optional)
       -> Separate vocal/background (neu mode co voice)
  -> Manual workspace
       -> Edit transcript / translation
       -> Build subtitle
       -> Generate TTS
       -> Mix audio
       -> Preview
  -> Export
```

## 3. Export Modes

He thong can ho tro 4 mode nghiep vu:

- `Vietnamese Voice`
- `Vietnamese Subtitle`
- `Vietnamese Voice + Subtitle`
- `Custom`

Rule:

- neu mode khong phai `Custom`, nut `Prepare` se chay pipeline tu dong theo mode
- neu mode la `Custom`, user tu chon tung step
- `Translator AI` la tuy chon ON/OFF:
  - `OFF`: chi dung raw translation provider
  - `ON`: raw translation xong se refine them bang AI provider

## 4. Kien truc dich

Spec moi chia code thanh 3 lop ro rang.

### Engine Layer

Layer nay wrap tool / API ben ngoai va tra output chuan hoa.

Thanh phan du kien:

- `FFmpegAdapter`
- `WhisperAdapter`
- `MicrosoftTranslatorAdapter`
- `LLMRefineAdapter`
- `DemucsAdapter`
- `TTSAdapter`
- `SubtitleAdapter`
- `PreviewAdapter`
- `ExportAdapter`

Trach nhiem:

- nhan input chuan hoa
- goi engine ben ngoai
- tra ve ket qua chuan hoa
- khong chua workflow business logic

### Workflow Layer

Layer nay dieu phoi pipeline va state cua project.

Trach nhiem:

- quyet dinh step nao can chay
- skip step khong can thiet
- resume khi da co output hop le
- retry khi step loi
- cap nhat `project.json`

Workflow chinh:

- `prepare_project()`
- `extract_audio()`
- `transcribe()`
- `translate_raw()`
- `refine_translation()`
- `separate_audio()`
- `generate_tts()`
- `build_subtitle()`
- `mix_audio()`
- `export_project()`

### UI Layer

UI chi nen:

- hien thi trang thai project
- nhan input user
- goi workflow
- hien thi preview
- hien thi log / loi

UI khong nen goi truc tiep ffmpeg, demucs, whisper hay API provider.

## 5. Du lieu trung tam

Don vi du lieu trung tam cua he thong la `segment`.

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
  "status": "ready"
}
```

Y nghia:

- `original_text`: transcript goc
- `raw_translation`: ban dich truc tiep
- `refined_translation`: ban dich sau AI refine
- `final_text`: text dung cho subtitle
- `tts_text`: text dung de sinh TTS
- `voice_file`: file audio cua segment

`final_text` va `tts_text` co the khac nhau.

## 6. Project Data Layout

Moi video nen duoc quan ly nhu mot project doc lap:

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
|   |   |-- seg_0001.wav
|   |   `-- ...
|   |-- voice_merged.wav
|   `-- mixed.wav
|-- subtitle/
|   |-- subtitle.srt
|   |-- subtitle.ass
|   `-- style.json
|-- preview/
|   `-- cache/
|-- export/
|   `-- final_output.mp4
`-- project.json
```

## 7. Project State

Moi project can mot state trung tam de resume / retry:

```json
{
  "project_id": "demo_001",
  "input_video": "source/input_video.mp4",
  "input_language": "en",
  "target_language": "vi",
  "mode": "voice_subtitle",
  "translator_ai": true,
  "steps": {
    "extract_audio": "done",
    "transcribe": "done",
    "translate_raw": "done",
    "refine_translation": "done",
    "separate_audio": "done",
    "generate_tts": "pending",
    "build_subtitle": "pending",
    "mix_audio": "pending",
    "export": "pending"
  }
}
```

Status chuan cho moi step:

- `pending`
- `running`
- `done`
- `failed`
- `skipped`

## 8. Preview / Export Consistency

Day la rule bat buoc:

- audio preview the nao thi export phai ra dung nhu vay
- subtitle preview the nao thi export phai dung cung style do
- subtitle style, audio source, mix params va export mode phai duoc luu vao project state

## 9. Provider Abstraction

Spec moi uu tien abstraction de thay engine de dang hon:

```python
class ASRProvider:
    def transcribe(self, audio_path: str) -> dict:
        raise NotImplementedError


class TranslationProvider:
    def translate(self, segments: list[dict]) -> list[dict]:
        raise NotImplementedError


class RefinementProvider:
    def refine(self, segments: list[dict]) -> list[dict]:
        raise NotImplementedError


class TTSProvider:
    def synthesize(self, text: str, output_path: str, **kwargs) -> str:
        raise NotImplementedError


class SeparationProvider:
    def separate(self, audio_path: str, output_dir: str) -> dict:
        raise NotImplementedError
```

Mapping du kien:

- `WhisperProvider` -> `ASRProvider`
- `MicrosoftTranslatorProvider` -> `TranslationProvider`
- `OpenAIRefineProvider` -> `RefinementProvider`
- `LocalTTSProvider` / `ApiTTSProvider` -> `TTSProvider`
- `DemucsProvider` -> `SeparationProvider`

## 10. Target Code Layout

Day la cau truc dich sau khi refactor:

```text
CapCap/
|-- app/
|   |-- core/
|   |   |-- models/
|   |   |-- enums/
|   |   |-- state/
|   |   `-- utils/
|   |-- engines/
|   |   |-- ffmpeg_adapter.py
|   |   |-- whisper_adapter.py
|   |   |-- translator_adapter.py
|   |   |-- llm_adapter.py
|   |   |-- demucs_adapter.py
|   |   |-- tts_adapter.py
|   |   |-- subtitle_adapter.py
|   |   |-- preview_adapter.py
|   |   `-- export_adapter.py
|   |-- providers/
|   |   |-- asr/
|   |   |-- translator/
|   |   |-- refine/
|   |   |-- tts/
|   |   `-- separation/
|   |-- workflows/
|   |   |-- prepare_workflow.py
|   |   |-- tts_workflow.py
|   |   |-- subtitle_workflow.py
|   |   |-- mix_workflow.py
|   |   `-- export_workflow.py
|   |-- services/
|   |   |-- project_service.py
|   |   |-- segment_service.py
|   |   |-- preview_service.py
|   |   `-- cache_service.py
|   `-- main.py
|-- ui/
|   |-- views/
|   |-- controllers/
|   `-- widgets/
|-- bin/
|-- models/
|-- output/
|-- temp/
|-- README.md
|-- newSpec.md
`-- structure.md
```

## 11. Trang thai hien tai cua repo

Repo hien tai DA refactor mot phan lon theo target layout tren, nhung CHUA hoan tat toan bo.

Cau truc dang ton tai trong code:

```text
CapCap/
|-- app/
|   |-- core/
|   |   |-- models/
|   |   `-- state/
|   |-- services/
|   |   |-- engine_runtime.py
|   |   |-- gui_project_bridge.py
|   |   |-- project_service.py
|   |   |-- segment_service.py
|   |   `-- workflow_runtime.py
|   |-- workflows/
|   |   |-- prepare_workflow.py
|   |   |-- voice_workflow.py
|   |   `-- export_workflow.py
|   `-- translation/
|       |-- errors.py
|       |-- models.py
|       |-- orchestrator.py
|       |-- srt_utils.py
|       `-- providers/
|           |-- ai_polisher.py
|           `-- microsoft_translator.py
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
|-- bin/
|-- models/
|-- output/
|-- temp/
|-- README.md
|-- newSpec.md
`-- structure.md
```

Dieu nay co nghia:

- `structure.md` mo ta target architecture de team refactor theo
- implementation hien tai da co `project.json`, `segment model`, workflow layer, runtime facade va UI modules tach nho
- engine layer van chua tach het thanh package `engines/` rieng; mot so adapter van dang o cac module processor cu
- UI da gan target structure nhung van con mot so shim compatibility nhu `ui/gui.py`, `ui/workers.py`
- khi refactor tiep, uu tien tach engine/provider abstraction va workflow phu con thieu truoc

## 12. Uu tien refactor de xuat

1. Tach `engine` layer thanh package ro rang thay cho viec goi truc tiep `video_processor`, `whisper_processor`, `vocal_processor`, `tts_processor`.
2. Chuan hoa provider interface cho ASR, translation, refine, TTS va separation.
3. Bo sung cac workflow phu con thieu neu can, vi du subtitle/mix workflow rieng neu team muon chia sau hon.
4. Chuyen preview/export sang mo hinh config-driven sau hon de dam bao preview = export tuyet doi.
5. Giam bot cac shim compatibility khi toan bo import path moi da on dinh.
