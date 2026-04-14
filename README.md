# CapCap

![CapCap Preview](assets/preview.jpg)

CapCap là ứng dụng desktop trên Windows để Việt hóa video theo workflow khép kín: tách audio, nhận diện giọng nói, dịch phụ đề, chỉnh sửa subtitle, tạo voice tiếng Việt, preview và export. Dự án hiện hỗ trợ cả `Local mode` và `Remote mode` để bạn có thể chạy toàn bộ trên một máy, hoặc dùng laptop làm client và PC mạnh hơn làm server xử lý `Whisper + AI`.

## Tính năng chính

- workflow theo project, có thể resume
- hỗ trợ `subtitle only`, `voice only`, `subtitle + voice`
- transcribe bằng `faster-whisper`
- dịch phụ đề bằng Google web fallback, Microsoft Translator hoặc AI provider khi có cấu hình
- AI polish/rewrite bằng local GGUF qua `llama-cpp-python`
- tạo voice bằng `Piper` hoặc `edge-tts`
- mix voice và background audio bằng `FFmpeg`
- preview bằng `libmpv`
- export video có subtitle/voice trực tiếp trong app
- `Remote mode` để gửi `Whisper + AI translation` sang PC server nội bộ

## Kiến trúc kỹ thuật

- `PySide6` cho desktop UI
- cấu trúc `ui -> controllers -> services -> workflows -> engines`
- `QThread` cho background workers
- `faster-whisper` cho speech-to-text local
- `onnxruntime` được dùng gián tiếp qua `faster-whisper` và `Piper`
- `Demucs` cho tách vocal/background
- `FFmpeg` cho extract, convert, mix, mux và export
- `libmpv` qua `python-mpv` cho preview video/audio
- `Piper TTS` cho local Vietnamese voice
- `edge-tts` cho cloud TTS miễn phí
- `llama-cpp-python` cho local AI translate/rewrite
- `requests` cho remote API và translator integration
- `PyInstaller` để đóng gói Windows app
- state project lưu trên đĩa để resume workflow
- subtitle styling qua `SRT -> ASS`

## Chế độ chạy

### Local mode

Toàn bộ pipeline chạy trên một máy:

- extract audio
- `Whisper`
- AI translation / rewrite
- `Piper` / `edge-tts`
- preview
- export

Phù hợp khi máy chính có đủ tài nguyên hoặc bạn muốn dùng hoàn toàn local.

### Remote mode

Laptop hoặc máy nhẹ chỉ chạy UI/client. Các tác vụ nặng được đẩy sang PC server:

- `Whisper`
- AI translation / rewrite

Hiện tại các bước sau vẫn chạy local trên client:

- extract audio
- preview
- export
- voice/TTS

Remote mode phù hợp khi bạn có một PC mạnh hơn để xử lý ASR và AI, còn laptop chỉ dùng để thao tác UI.

### PC server mode

PC chạy service HTTP nội bộ để nhận request từ client:

- `/health`
- `/v1/transcribe`
- `/v1/translate-segments`
- `/v1/translate-srt`
- `/v1/rewrite-segments`
- `/v1/rewrite-srt`

Server này dùng local runtime của PC, nên `Whisper` và local GGUF vẫn tận dụng đúng môi trường trên PC.

## Cài đặt

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap
```

Tạo `.env` từ [./.env_example](./.env_example) nếu cần cấu hình translator, AI provider hoặc remote API.

## Dependencies theo profile

### Local

```bash
pip install -r requirements-local.txt
```

Bao gồm toàn bộ stack local:

- `faster-whisper`
- `demucs`
- `llama-cpp-python`
- `Piper`
- `edge-tts`

### Remote client

```bash
pip install -r requirements-remote.txt
```

Profile này nhẹ hơn, không kéo toàn bộ local AI / Whisper stack.

### Server

```bash
pip install -r requirements-server.txt
```

Dùng cho PC server chạy `Whisper + AI translation`.

### Mặc định

```bash
pip install -r requirements.txt
```

Hiện tại `requirements.txt` trỏ tới `requirements-local.txt`.

## Chạy từ source

### Local mode

```bash
python ui/gui.py
```

### Remote client mode

```bash
python ui/gui_remote.py
```

Trong app remote, vào `Settings` để nhập:

- `PC API URL`
- `API Token` nếu có dùng token

Bạn cũng có thể bấm `Test Connection` để kiểm tra kết nối tới PC server trước khi chạy pipeline.

### Server mode trên PC

```bash
python app/remote_api_server.py
```

Biến môi trường liên quan:

- `CAPCAP_REMOTE_API_URL`
- `CAPCAP_REMOTE_API_TOKEN`
- `CAPCAP_REMOTE_API_HOST`
- `CAPCAP_REMOTE_API_PORT`
- `CAPCAP_REMOTE_API_TIMEOUT`

## Build bằng PyInstaller

### Release local

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.spec --noconfirm --clean
```

### Debug local

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.debug.spec --noconfirm --clean
```

### Remote client

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.remote.spec --noconfirm --clean
```

### PC server

```bash
python -m PyInstaller D:\CodingTime\CapCap\CapCap.server.spec --noconfirm --clean
```

## Resource và packaging hiện tại

- release local chỉ bundle `voice_preview_catalog.release.json` dưới tên `app/voice_preview_catalog.json`
- release local chỉ bundle 1 local voice mặc định: `vi_VN-vais1000-medium`
- các local voice khác người dùng tự tải vào `models/piper`
- `Manage Resources` hỗ trợ tải:
  - `Whisper`
  - `Local AI GGUF`
  - `Voice Pack`
  - `Whisper GPU Runtime`
- bản debug giữ console để kiểm tra runtime khi cần

## Quy trình dùng đề xuất

### Dùng một máy

1. cài `requirements-local.txt`
2. chạy `python ui/gui.py`
3. tải thêm resource nếu cần trong `Manage Resources`
4. generate và export trực tiếp

### Dùng laptop + PC

1. trên PC: cài `requirements-server.txt`
2. trên PC: chạy `python app/remote_api_server.py`
3. trên laptop: cài `requirements-remote.txt`
4. trên laptop: chạy `python ui/gui_remote.py`
5. vào `Settings` trên laptop, nhập `PC API URL`
6. bấm `Test Connection`
7. chạy workflow từ laptop, `Whisper + AI` sẽ xử lý trên PC

## Giới hạn hiện tại

- tối ưu cho Windows
- cần Internet nếu tải model/resource hoặc dùng provider online
- remote mode hiện mới remote hóa:
  - `Whisper`
  - AI translation / rewrite
- `TTS`, preview, export vẫn chạy local trên client
- local AI hiện mặc định an toàn theo hướng `CPU-safe`
- `Whisper GPU` có thể tăng tốc nếu có runtime CUDA tương thích
- release không bundle toàn bộ local Piper voices
- một số bước AI hoặc stem separation có thể chậm trên máy yếu hoặc khi chạy CPU

## Troubleshooting

### `FFmpeg not found`

Kiểm tra:

- `bin/ffmpeg/ffmpeg.exe`
- `bin/ffmpeg/ffprobe.exe`

### Local voice báo không tồn tại

Kiểm tra:

- file `.onnx`
- file `.onnx.json`

trong `models/piper`

### Whisper fallback về CPU

Thường do thiếu CUDA runtime tương thích. App vẫn có thể chạy nhưng chậm hơn.

### `Demucs preload skipped`

Đây có thể chỉ là preload warning, không phải lúc nào cũng chặn workflow.

### Remote mode không kết nối được

Kiểm tra:

- PC server đã chạy chưa
- `CAPCAP_REMOTE_API_HOST` / `PORT`
- firewall của Windows
- `PC API URL` trong app remote
- token có khớp giữa client và server không

### Cần xem runtime log

Dùng bản debug để xem console trực tiếp.

## Repo và nguồn tham khảo

- `faster-whisper`: [https://github.com/SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- `whisper`: [https://github.com/openai/whisper](https://github.com/openai/whisper)
- `demucs`: [https://github.com/facebookresearch/demucs](https://github.com/facebookresearch/demucs)
- `ffmpeg`: [https://github.com/FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg)
- `mpv`: [https://github.com/mpv-player/mpv](https://github.com/mpv-player/mpv)
- `python-mpv`: [https://github.com/jaseg/python-mpv](https://github.com/jaseg/python-mpv)
- `piper`: [https://github.com/rhasspy/piper](https://github.com/rhasspy/piper)
- `piper voices`: [https://github.com/rhasspy/piper-voices](https://github.com/rhasspy/piper-voices)
- `edge-tts`: [https://github.com/rany2/edge-tts](https://github.com/rany2/edge-tts)
- `llama.cpp`: [https://github.com/ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)
- `llama-cpp-python`: [https://github.com/abetlen/llama-cpp-python](https://github.com/abetlen/llama-cpp-python)
- `PySide`: [https://github.com/pyside/pyside-setup](https://github.com/pyside/pyside-setup)
- `PyInstaller`: [https://github.com/pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller)

## Cam kết và miễn trừ trách nhiệm

Tool này được làm với mục đích học tập, nghiên cứu và thử nghiệm kỹ thuật. Tác giả không cam kết độ chính xác tuyệt đối của subtitle, translation, voice output hoặc kết quả xử lý video, và không chịu trách nhiệm cho bất kỳ hậu quả nào phát sinh từ việc sử dụng tool. Người dùng tự chịu trách nhiệm với dữ liệu, nội dung và cách sử dụng phần mềm.
