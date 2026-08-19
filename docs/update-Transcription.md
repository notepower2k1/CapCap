# Nâng Cấp Module Transcription Tiếng Trung (SenseVoice + Silero VAD Pipeline)

Tài liệu đặc tả kiến trúc và cơ chế hoạt động của module nhận dạng giọng nói (ASR) tiếng Trung bằng **SenseVoice-Small** kết hợp **Silero VAD** chạy qua runtime **`sherpa-onnx`** trong CapCap.

---

## 1. Lý Do & Mục Tiêu Nâng Cấp

* **Khắc phục triệt để Hallucination:** Kiến trúc Non-Autoregressive (NAR) của SenseVoice loại bỏ hoàn toàn lỗi lặp từ vô tận (infinite repetition loops) khi video có nhạc nền (BGM), tiếng cười, hiệu ứng âm thanh lớn.
* **Tốc độ xử lý:** Nhanh gấp 10–15 lần so với Whisper, hoàn toàn chạy offline trên CPU đa luồng hoặc GPU CUDA với mức tiêu thụ RAM/VRAM cực thấp (~120MB model weight).
* **Độ chuẩn xác Timestamp & Ngắt câu tự nhiên:** Phân đoạn qua Silero VAD kết hợp padding (pre/post pad) và thuật toán phân tách câu theo dấu câu (，。？！) giúp phụ đề ngắn gọn (10–18 ký tự/dòng), khớp nhịp chuyển cảnh và tối ưu cho thuyết minh AI (TTS dubbing).
* **Tự động tải & Quản lý tài nguyên:** Tích hợp tải mô hình tự động từ HuggingFace qua giao diện Manage Resources.

---

## 2. Cấu Trúc File & Thành Phần

```text
CapCap/
├── app/
│   ├── vad_processor.py            # Silero VAD detection, padding & chunk merging
│   ├── sensevoice_processor.py     # SenseVoice ASR, text sanitizing & punctuation splitting
│   ├── engines/
│   │   └── sensevoice_adapter.py   # Adapter kết nối EngineRuntime với SenseVoice
│   └── services/
│       └── resource_download_service.py # Quản lý & tải model SenseVoice & Silero VAD
├── bin/
│   └── silero_vad.onnx             # Model Silero VAD (~2MB)
└── models/
    └── sensevoice/                 # Thư mục chứa SenseVoice model
        ├── model.int8.onnx         # Quantized ONNX weights (~120MB)
        └── tokens.txt              # Bảng từ vựng (vocabulary tokens)
```

---

## 3. Chi Tiết Kỹ Thuật & Tham Số

### 3.1 Silero VAD (`app/vad_processor.py`)
- **Sample Rate:** `16000 Hz`
- **Threshold:** `0.45` (cân bằng độ nhạy lời thoại và lọc tạp âm)
- **Min Speech Duration:** `0.25s` (250ms - lọc tiếng click, tiếng thở nhẹ)
- **Min Silence Duration:** `0.45s` (450ms - giữ các từ trong câu nói liền mạch)
- **Pre-Speech Padding:** `150ms` (chống mất âm đầu câu)
- **Post-Speech Padding:** `200ms` (chống cụt đuôi âm tiết cuối)
- **Max Merge Gap:** `0.35s` (ghép các đoạn ngắt nghỉ ngắn trong cùng câu)
- **Max Chunk Duration:** `12.0s` (giữ ngữ cảnh tối ưu cho SenseVoice)

### 3.2 SenseVoice Processor (`app/sensevoice_processor.py`)
- **Khởi tạo & Hardware Provider:**
  - Tự động nhận diện GPU `CUDAExecutionProvider` nếu có runtime CUDA, fallback sang CPU 4 luồng (`num_threads=4`).
  - Kích hoạt `use_itn=True` để SenseVoice tự khôi phục số tự nhiên và dấu câu tiếng Trung.
- **Tiền xử lý âm thanh:**
  - Tự động chuyển đổi âm thanh đa kênh (Stereo/Surround) thành Mono 1 kênh (`np.mean(axis=1)`).
  - Tự động resample bằng đa thức đa pha (`scipy.signal.resample_poly`) về chuẩn `16000 Hz float32 [-1.0, 1.0]`.
- **Làm sạch văn bản (Sanitization):**
  - Loại bỏ hoàn toàn các thẻ đặc biệt: `<|zh|>`, `<|HAPPY|>`, `<|Speech|>`, `<|BGM|>`, `<|withitn|>`, v.v.
  - Xóa bỏ khoảng trắng thừa giữa các ký tự chữ Hán (CJK characters).
- **Tách câu phụ đề theo dấu câu (Punctuation-based Sentence Splitting):**
  - Tách câu theo các dấu kết thúc: `。` `？` `！` `!` `?` `；` `;`
  - Nếu câu quá dài (> 18 ký tự), tự động tách tiếp theo dấu phẩy: `，` `,` `、`
  - Nội suy timestamp tỷ lệ theo độ dài ký tự thực tế để tạo các cue phụ đề chuẩn cho video ngắn.
- **Tiến trình (Progress Callback):** Cập nhật tiến trình thời gian thực (0% - 100%) cho giao diện người dùng.

---

## 4. Quản Lý Tài Nguyên & Tự Động Tải

Người dùng có thể tải mô hình thông qua menu **Manage Resources** trên thanh công cụ hoặc để phần mềm tự động tải khi bắt đầu quá trình nhận dạng:
1. **SenseVoice Model:** Tải từ repo HuggingFace `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` -> lưu vào `models/sensevoice/`.
2. **Silero VAD:** Tải từ repo Sherpa-ONNX -> lưu vào `bin/silero_vad.onnx`.