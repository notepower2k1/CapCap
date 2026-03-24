# Software Design Specification
## Video Localization Tool (Python)

## 1. Mục tiêu dự án

Xây dựng một tool xử lý video cho phép:

- Trích xuất transcript từ video
- Dịch sang tiếng Việt
- Tối ưu văn phong bằng AI (optional)
- Tạo voice tiếng Việt (TTS)
- Mix voice với background
- Tạo và preview subtitle
- Export video hoàn chỉnh với voice/subtitle theo lựa chọn của người dùng

Tool này hướng tới workflow bán tự động: phần chuẩn bị chạy tự động, phần tinh chỉnh nội dung, subtitle, TTS, mix audio và export được thực hiện thủ công để tiện preview và kiểm soát chất lượng.

---

## 2. Phạm vi chức năng

### 2.1 Chức năng chính

- Import video đầu vào
- Extract audio bằng FFmpeg
- Extract transcript bằng faster-whisper / whisper-faster
- Dịch bằng Microsoft Translator API
- Tối ưu văn phong bằng LLM API
- Tách voice và background bằng Demucs
- Tạo audio tiếng Việt bằng local TTS hoặc TTS API
- Mix voice vào background
- Preview:
  - voice only
  - background only
  - mixed audio
  - subtitle trực tiếp trên video
- Render subtitle bằng ASS để đảm bảo style chính xác
- Export video theo các mode khác nhau

### 2.2 Ngôn ngữ đầu vào hỗ trợ

Trong phiên bản hiện tại, đầu vào được tối ưu cho 4 ngôn ngữ:

- English
- Chinese
- Korean
- Japanese

Mục tiêu là giảm sai số ASR và tối ưu chất lượng pipeline dịch.

---

## 3. Các chế độ xuất

Khi mở tool, người dùng có thể chọn một trong các mode sau:

- **Export with Vietnamese Voice**
- **Export with Vietnamese Subtitle**
- **Export with Vietnamese Voice + Subtitle**
- **Custom**

Ngoài ra có một tùy chọn:

- **Translator AI**: ON/OFF  
  - OFF: chỉ dùng Microsoft Translator API
  - ON: dịch qua Microsoft Translator API, sau đó refine bằng LLM API

---

## 4. Nguyên tắc workflow

### 4.1 Prepare phase

Khi người dùng chọn mode và nhấn **Prepare**, hệ thống sẽ tự động chạy các bước chuẩn bị tương ứng.

### 4.2 Manual phase

Các bước sau sẽ được làm thủ công để tiện preview và chỉnh sửa:

- chỉnh transcript
- chỉnh translation
- tạo audio
- mix audio
- chỉnh subtitle style
- export

### 4.3 Custom mode

Nếu người dùng chọn **Custom**, hệ thống không tự động chạy pipeline chuẩn bị.  
Thay vào đó, người dùng được quyền chủ động chạy từng bước riêng lẻ.

---

## 5. Luồng xử lý tự động

## 5.1 Rule chung

Nếu mode không phải Custom, khi nhấn Prepare sẽ chạy các bước như sau:

1. Extract audio bằng FFmpeg
2. Extract transcript bằng whisper-faster
3. Translate bằng Microsoft Translator API
4. Refine bằng LLM API nếu Translator AI = ON
5. Tách voice/background bằng Demucs nếu mode export có voice

## 5.2 Ma trận xử lý theo mode

### Export with Vietnamese Voice
- extract audio
- transcribe
- translate raw
- refine translation nếu Translator AI bật
- separate voice/background
- manual generate TTS
- manual mix
- export video với voice tiếng Việt

### Export with Vietnamese Subtitle
- extract audio
- transcribe
- translate raw
- refine translation nếu Translator AI bật
- manual subtitle edit
- manual subtitle preview
- export video với subtitle tiếng Việt

### Export with Vietnamese Voice + Subtitle
- extract audio
- transcribe
- translate raw
- refine translation nếu Translator AI bật
- separate voice/background
- manual generate TTS
- manual subtitle edit
- manual preview
- manual mix
- export video với voice + subtitle

### Custom
Không chạy Prepare tự động.  
Người dùng tự chọn bước muốn chạy.

---

## 6. Kiến trúc phần mềm

Kiến trúc nên được tách thành 3 lớp rõ ràng.

## 6.1 Engine Layer

Layer này wrap các công cụ / API bên ngoài.

Các adapter chính:

- `FFmpegAdapter`
- `WhisperAdapter`
- `MicrosoftTranslatorAdapter`
- `LLMRefineAdapter`
- `DemucsAdapter`
- `TTSAdapter`
- `SubtitleAdapter`
- `MpvPreviewAdapter`
- `ExportAdapter`

Nhiệm vụ:
- nhận input chuẩn hóa
- gọi engine tương ứng
- trả output chuẩn hóa
- không chứa logic workflow cấp cao

## 6.2 Workflow Layer

Layer điều phối pipeline.

Các trách nhiệm:

- xác định bước nào cần chạy
- skip bước không cần thiết
- resume nếu đã có output
- retry khi step lỗi
- cập nhật trạng thái project

Các workflow chính:

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

## 6.3 UI Layer

Layer giao diện chỉ nên làm các việc sau:

- hiển thị trạng thái project
- nhận input của người dùng
- gọi workflow
- render preview
- hiển thị log / lỗi

UI không nên chứa lệnh ffmpeg, demucs hay logic gọi API trực tiếp.

---

## 7. Cấu trúc project dữ liệu

Mỗi video là một project độc lập.

```text
project/
│
├── source/
│   ├── input_video.mp4
│   └── extracted_audio.wav
│
├── analysis/
│   ├── transcript_raw.json
│   ├── transcript_segments.json
│   └── detected_language.json
│
├── translation/
│   ├── translation_raw.json
│   ├── translation_refined.json
│   └── translation_final.json
│
├── audio/
│   ├── separated/
│   │   ├── vocal.wav
│   │   └── background.wav
│   ├── tts_segments/
│   │   ├── seg_0001.wav
│   │   ├── seg_0002.wav
│   │   └── ...
│   ├── voice_merged.wav
│   └── mixed.wav
│
├── subtitle/
│   ├── subtitle.srt
│   ├── subtitle.ass
│   └── style.json
│
├── preview/
│   └── cache/
│
├── export/
│   └── final_output.mp4
│
└── project.json
```

---

## 8. Mô hình dữ liệu trung tâm

Đơn vị dữ liệu trung tâm của toàn bộ hệ thống là **segment**.

## 8.1 Segment model

```json
{
  "id": 1,
  "start": 12.5,
  "end": 15.2,
  "original_text": "Hello everyone",
  "raw_translation": "Xin chào mọi người",
  "refined_translation": "Xin chào tất cả mọi người",
  "final_text": "Xin chào mọi người",
  "tts_text": "Xin chào mọi người",
  "voice_file": "audio/tts_segments/seg_0001.wav",
  "status": "ready"
}
```

## 8.2 Ý nghĩa các trường

- `original_text`: transcript gốc từ ASR
- `raw_translation`: bản dịch trực tiếp từ Microsoft Translator
- `refined_translation`: bản dịch sau khi optimize bằng LLM
- `final_text`: bản cuối cùng dùng cho subtitle
- `tts_text`: bản cuối cùng dùng để tạo TTS
- `voice_file`: file audio đã generate cho segment đó

Lưu ý: `final_text` và `tts_text` có thể khác nhau.  
Ví dụ subtitle cần ngắn gọn, còn câu đọc TTS cần tự nhiên hơn.

---

## 9. Project state

Mỗi project cần có một file trạng thái trung tâm: `project.json`.

Ví dụ:

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
  },
  "settings": {
    "tts_provider": "local",
    "subtitle_format": "ass",
    "mix_mode": "ducking",
    "use_original_audio": false
  }
}
```

---

## 10. Trạng thái step

Mỗi step trong workflow nên có status chuẩn hóa:

- `pending`
- `running`
- `done`
- `failed`
- `skipped`

Điều này giúp:

- resume pipeline
- retry step lỗi
- hiển thị tiến độ rõ ràng trong UI
- debug dễ hơn

---

## 11. Thiết kế pipeline chi tiết

## 11.1 Extract audio

### Mục tiêu
Trích xuất audio từ video đầu vào.

### Công cụ
- FFmpeg

### Input
- `input_video.mp4`

### Output
- `source/extracted_audio.wav`

### Ghi chú
Đây là bước mặc định trong Prepare.

---

## 11.2 Transcribe

### Mục tiêu
Sinh transcript từ audio.

### Công cụ
- faster-whisper / whisper-faster

### Input
- `source/extracted_audio.wav`

### Output
- `analysis/transcript_raw.json`
- `analysis/transcript_segments.json`

### Ghi chú
- đầu vào chỉ tối ưu cho 4 ngôn ngữ: en, zh, ko, ja
- segment là output chuẩn để dùng tiếp cho translation, subtitle và TTS

---

## 11.3 Translate raw

### Mục tiêu
Dịch transcript sang tiếng Việt theo dạng raw.

### Công cụ
- Microsoft Translator API

### Input
- `analysis/transcript_segments.json`

### Output
- `translation/translation_raw.json`

### Ghi chú
Đây là bước mặc định.

---

## 11.4 Refine translation

### Mục tiêu
Tối ưu văn phong tiếng Việt bằng LLM API.

### Điều kiện chạy
Chỉ chạy nếu **Translator AI = ON**

### Input
- `translation/translation_raw.json`

### Output
- `translation/translation_refined.json`

### Ghi chú
Bước này không thay thế raw translation mà là hậu biên tập.

---

## 11.5 Separate audio

### Mục tiêu
Tách phần voice và background music.

### Công cụ
- Demucs

### Điều kiện chạy
Chỉ chạy nếu mode export có voice.

### Input
- `source/extracted_audio.wav`

### Output
- `audio/separated/vocal.wav`
- `audio/separated/background.wav`

---

## 11.6 Generate TTS

### Mục tiêu
Tạo giọng đọc tiếng Việt từ `tts_text`.

### Input
- danh sách segment có `tts_text`

### Output
- `audio/tts_segments/*.wav`
- `audio/voice_merged.wav`

### Provider hỗ trợ
- local TTS
- API TTS

### Yêu cầu
- generate theo từng segment
- có thể regenerate từng segment riêng lẻ
- có thể chỉnh speech rate
- nên hỗ trợ trim silence đầu/cuối

---

## 11.7 Subtitle generation

### Mục tiêu
Sinh subtitle từ dữ liệu dịch.

### Format
- nội bộ dùng **ASS** làm format chính
- SRT dùng để import/export phổ thông

### Output
- `subtitle/subtitle.ass`
- `subtitle/subtitle.srt`

### Ghi chú
ASS là format chính vì cần:
- font family
- font size
- màu chữ
- outline
- shadow
- vị trí
- hiệu ứng

---

## 11.8 Audio mixing

### Mục tiêu
Mix voice tiếng Việt với background.

### Input
- `audio/voice_merged.wav`
- `audio/separated/background.wav`

### Output
- `audio/mixed.wav`

### Tùy chọn
- normal mix
- ducking background khi có voice
- gain control
- loudness adjustment

---

## 11.9 Export

### Mục tiêu
Xuất video cuối cùng.

### Các mode export
- dùng âm thanh gốc
- dùng âm thanh mix
- subtitle only
- voice only
- voice + subtitle

### Yêu cầu
- style subtitle trong export phải giống preview
- audio mix trong export phải giống preview
- cho phép hard-sub hoặc soft-sub tùy thiết kế hiện tại

---

## 12. Preview system

Preview là phần bắt buộc để kiểm soát chất lượng trước khi export.

## 12.1 Audio preview

Hệ thống cần cho phép nghe thử:

- voice only
- background only
- mixed audio

## 12.2 Subtitle preview

Subtitle phải được preview trực tiếp bằng:

- `mpv`
- `libmpv`
- `ASS`

Điều này giúp đảm bảo:
- style nhìn đúng như export
- font hiển thị đúng
- effect hiển thị đúng
- user có thể chỉnh subtitle trực tiếp và thấy thay đổi ngay

---

## 13. Chỉnh sửa nội dung thủ công

Tool cần có màn hình/editor cho phép chỉnh từng segment.

Các cột đề xuất:

- start
- end
- original text
- raw translation
- refined translation
- final subtitle text
- final TTS text
- voice status

Lợi ích:
- user sửa transcript sai
- user sửa bản dịch chưa tự nhiên
- user tách riêng subtitle text và TTS text
- user regenerate lại TTS cho từng segment

---

## 14. Subtitle style system

Nên lưu style subtitle thành cấu hình riêng, ví dụ `subtitle/style.json`.

Các thuộc tính nên hỗ trợ:

- font family
- font size
- primary color
- outline color
- shadow
- alignment
- margin
- line spacing
- special effects
- preset name

ASS sẽ được generate từ:
- segment text
- style config

---

## 15. Audio control system

Để tăng tính thực dụng, nên có các control sau:

- global TTS speech rate
- per-segment regenerate
- trim silence
- voice gain
- background gain
- ducking strength
- mix preview

Nếu làm được, có thể bổ sung waveform/timeline để hỗ trợ căn chỉnh tốt hơn.

---

## 16. Provider abstraction

Để dễ mở rộng và thay engine, nên có interface/provider abstraction.

Ví dụ:

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

Ví dụ mapping implementation:

- `WhisperProvider` implements `ASRProvider`
- `MicrosoftTranslatorProvider` implements `TranslationProvider`
- `OpenAIRefineProvider` implements `RefinementProvider`
- `LocalTTSProvider` implements `TTSProvider`
- `ApiTTSProvider` implements `TTSProvider`
- `DemucsProvider` implements `SeparationProvider`

---

## 17. Job system và khả năng resume

Vì pipeline multimedia thường chạy lâu, hệ thống cần hỗ trợ:

- retry step bị lỗi
- resume từ step lỗi
- skip nếu output đã tồn tại
- cancel task đang chạy

Ví dụ:
- extract_audio done
- transcribe done
- translate done
- refine failed

Sau khi user sửa API key, hệ thống chỉ cần chạy lại `refine_translation`, không chạy lại từ đầu.

---

## 18. Cache và tối ưu hiệu năng

### 18.1 Cache step output
Không chạy lại nếu output hợp lệ đã tồn tại.

### 18.2 Cache TTS per segment
Nếu segment không thay đổi thì không cần generate lại.

### 18.3 Hash input
Có thể dùng hash để kiểm tra:
- transcript có thay đổi không
- translation có thay đổi không
- tts_text có thay đổi không

---

## 19. Logging và debug

Mỗi step nên có log riêng:

- command đã chạy
- stdout
- stderr
- thời gian bắt đầu / kết thúc
- input / output file

Nên có thư mục log hoặc file log trong project để dễ kiểm tra khi lỗi.

---

## 20. Đề xuất module Python

Cấu trúc code đề xuất:

```text
app/
│
├── core/
│   ├── models/
│   ├── enums/
│   ├── state/
│   └── utils/
│
├── engines/
│   ├── ffmpeg_adapter.py
│   ├── whisper_adapter.py
│   ├── translator_adapter.py
│   ├── llm_adapter.py
│   ├── demucs_adapter.py
│   ├── tts_adapter.py
│   ├── subtitle_adapter.py
│   ├── mpv_adapter.py
│   └── export_adapter.py
│
├── providers/
│   ├── asr/
│   ├── translator/
│   ├── refine/
│   ├── tts/
│   └── separation/
│
├── workflows/
│   ├── prepare_workflow.py
│   ├── tts_workflow.py
│   ├── subtitle_workflow.py
│   ├── mix_workflow.py
│   └── export_workflow.py
│
├── services/
│   ├── project_service.py
│   ├── segment_service.py
│   ├── preview_service.py
│   └── cache_service.py
│
├── ui/
│   ├── views/
│   ├── controllers/
│   └── widgets/
│
└── main.py
```

---

## 21. Đề xuất màn hình UI

Nên chia tool theo step thay vì nhồi tất cả vào một màn hình.

### Workspace đề xuất

1. Import
2. Prepare
3. Transcript
4. Translation
5. Voice
6. Subtitle
7. Preview
8. Export

### Mỗi màn hình nên có

- dữ liệu đầu vào
- kết quả đầu ra
- action chính
- log
- preview nếu có

---

## 22. Yêu cầu nhất quán preview và export

Đây là yêu cầu bắt buộc.

### Audio
- preview mixed audio thế nào thì export phải ra đúng như vậy

### Subtitle
- preview ASS thế nào thì export phải ra đúng style đó

### Cấu hình cần được lưu
- subtitle style
- selected audio source
- mix params
- export mode
- hard-sub / soft-sub nếu có

---

## 23. Phạm vi MVP đề xuất

## MVP 1
- import video
- extract audio
- transcribe
- raw translate
- edit translation
- generate subtitle
- preview subtitle
- export subtitle video

## MVP 2
- demucs separation
- TTS tiếng Việt
- audio preview
- audio mix
- export voice

## MVP 3
- AI refine
- advanced subtitle style
- per-segment regenerate
- advanced mix control

---

## 24. Rủi ro kỹ thuật

Các rủi ro chính:

- segment ASR sai hoặc chia câu không đẹp
- translation raw cứng, thiếu ngữ cảnh
- refined translation dài hơn timing subtitle
- TTS không khớp duration segment
- mix audio không tự nhiên
- font subtitle hiển thị khác giữa preview và export
- xử lý file lớn gây chậm hoặc ngốn RAM

---

## 25. Kết luận

Spec này giữ nguyên tinh thần workflow ban đầu nhưng được tổ chức lại để phù hợp với phát triển phần mềm bằng Python:

- pipeline rõ ràng
- dữ liệu trung tâm là segment
- tách engine / workflow / UI
- dễ maintain
- dễ thay provider
- dễ debug
- thuận lợi cho mở rộng về sau

Hướng triển khai được khuyến nghị là:
- dùng `project.json` làm state trung tâm
- dùng ASS làm format subtitle nội bộ
- giữ Prepare là pipeline tự động có thể resume
- để các bước TTS / subtitle / mix / export ở chế độ bán thủ công để đảm bảo preview và chất lượng đầu ra
