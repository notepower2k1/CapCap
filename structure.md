# System Structure - Video Subtitle Translator (CapCap)

## 1. Mục tiêu hệ thống
**Project name**: CapCap - Video Subtitle Translator
**Goal**: Tạo tool local giúp dịch video nhanh chóng với giao diện hiện đại.
1. Trích xuất âm thanh từ video.
2. Chuyển đổi giọng nói thành văn bản (Speech-to-Text).
3. Dịch phụ đề sang tiếng Việt (Sử dụng Cloudflare Workers AI).
4. Xem trước và chỉnh sửa phụ đề trên Timeline phong cách chuyên nghiệp.
5. Nhúng phụ đề cứng (Hardsub) vào video.

## 2. Kiến trúc tổng thể
Video → Audio Extraction (FFmpeg) → Speech Recognition (Whisper) → Translation (Cloudflare AI) → Timeline Preview → Video Embedding (FFmpeg)

## 3. Các module chính
### 1. Video Processing Module (`video_processor.py`)
- **Nhiệm vụ**: Trích xuất âm thanh, lấy thông tin video (ffprobe), nhúng phụ đề.
- **Công nghệ**: FFmpeg, FFprobe.

### 2. Speech Recognition Module (`whisper_processor.py`)
- **Nhiệm vụ**: Chuyển giọng nói sang text kèm timestamp.
- **Công nghệ**: `whisper-cli.exe` (Whisper.cpp).

### 3. Translation Module (`translator.py`)
- **Nhiệm vụ**: Dịch văn bản SRT sang tiếng Việt.
- **Công nghệ**: REST API calls đến Cloudflare Workers AI (Llama 3.1 model).

### 4. UI Module (`gui.py`)
- **Nhiệm vụ**: Giao diện người dùng, trình phát video, thanh Timeline điều chỉnh phụ đề.
- **Công nghệ**: PySide6 (Qt for Python).

## 4. Thư mục Project
```text
CapCap/
├── app/                  # Logic xử lý backend
│   ├── video_processor.py
│   ├── whisper_processor.py
│   ├── translator.py
│   └── subtitle_builder.py
├── bin/                  # Các công cụ thực thi
│   ├── ffmpeg/           # ffmpeg.exe, ffprobe.exe
│   └── whisper/          # whisper-cli.exe và các dll
├── models/               # Model AI (Whisper ggml bin)
├── output/               # Kết quả xuất bản
├── temp/                 # File tạm (audio wav, ass)
├── ui/
│   └── gui.py            # Giao diện chính
└── .env                  # Cấu hình API Key
```

## 5. Phân công Công nghệ
- **GUI**: PySide6.
- **Backend API**: Cloudflare Workers AI.
- **Multimedia**: FFmpeg.
- **STT**: Whisper.cpp.