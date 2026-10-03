# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

[English](README_en.md) | [ Tiếng Việt](README.md)

![Giao diện CapCap](assets/preview.JPG)

### [🎬 Demo & Hướng dẫn sử dụng](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** là ứng dụng biên tập và bản địa hóa video dành cho Windows, giúp đơn giản hóa toàn bộ quy trình từ nhận diện giọng nói, dịch nội dung, lồng tiếng, chỉnh sửa hình ảnh cho đến xuất video hoàn chỉnh.

CapCap hỗ trợ tạo **phụ đề tiếng Việt và tiếng Anh**, dịch nội dung video, tạo giọng đọc bằng TTS và chỉnh sửa các lớp nội dung theo thời gian trực tiếp trên timeline.

## ✨ Điểm nổi bật

* **Quy trình xử lý trực quan 5 bước:** **Chuẩn bị → Chép lời → Dịch → TTS → Xuất video**
* **Chuyển giọng nói thành văn bản (STT) siêu tốc & chính xác:** Hỗ trợ **Faster-Whisper** và **SenseVoice** chạy tăng tốc phần cứng GPU (CUDA) hoặc CPU.
* **Trích xuất phụ đề video bằng OCR:** Nhận diện và bóc tách chữ phụ đề cứng có sẵn trên video bằng **RapidOCR**.
* **Dịch thuật AI thông minh & Kiểm soát xưng hô chuẩn xác:**
  * **Tự động nhận diện ngữ cảnh & nhân vật:** Phân tích kịch bản để nhận diện giới tính, vai trò xã hội và thiết lập quy tắc xưng hô 2 chiều bắt buộc.
  * **Hộp thoại kiểm tra & can thiệp xưng hô:** Cho phép người dùng xem lại, sửa tay trực tiếp hoặc **nhập góp ý bằng lời** (ví dụ: *"Đảo lại vai 2 nhân vật A và B"*, *"Xưng mày - tao với kẻ xấu"*) để AI tự động phân tích lại theo chỉ dẫn.
  * **Sổ nhớ ngữ cảnh cuộn (Rolling Context Ledger):** Lưu vết các quy tắc xưng hô đã chốt và ngữ cảnh câu thoại liền trước, đảm bảo 100% nhất quán xuyên suốt các video dài nhiều tập.
  * **Preset dịch thuật theo thể loại:** Tích hợp sẵn prompt cho Phim ngắn/Douyin, Ngôn tình, Cổ trang/Võ hiệp/Tiên hiệp, Anime/Manga, K-Drama, Vlog/TikTok, Tài liệu...
  * **Hỗ trợ đa dạng nhà cung cấp AI & Dịch thuật web:** **Google AI Studio (Gemini 2.5/1.5)**, **OpenAI (GPT-4o)**, **DeepSeek**, **Ollama (chạy offline local)**, cùng **Google Translate** và **Bing Translate** (dịch web miễn phí không cần API key, tự động chuyển đổi dự phòng sang Bing khi Google Translate bị giới hạn lượt gọi / rate limit).
* **Tạo giọng đọc (TTS) & Nhân bản giọng nói (Voice Cloning):**
  * Hỗ trợ **Piper TTS** (offline), **Edge TTS**, **CapCut TTS** và **VieNeu TTS** (hỗ trợ nhân bản giọng đọc tùy chọn).
  * Nhận diện người nói (**Speaker Diarization**) và gán giọng đọc riêng cho từng nhân vật.
* **Quản lý âm thanh đa lớp & Tách nhạc nền (BGM / Vocals):**
  * Tách riêng giọng gốc và nhạc nền (BGM), cho phép tinh chỉnh âm lượng, gain, tốc độ hoặc tắt tiếng từng lớp khi xem trước mà không sợ bị đè mất nhạc nền khi ghép giọng TTS tiếng Việt.
* **Trình phát & xem trước Native MPV mượt mà:**
  * Xem trước video và âm thanh với hiệu năng cao bằng **libmpv**, đồng bộ tức thì theo con trỏ Timeline.
  * Tính năng **Fast Preview** kết xuất nhanh 5 giây và **Exact Frame Preview** kiểm tra khung hình chính xác.
* **Timeline biên tập đa tầng chuyên nghiệp:**
  * Quản lý các lớp: Phụ đề, Vùng làm mờ (Blur), Logo, Mặt nạ (Mask), Văn bản (Text), Vùng chọn (Selection Range).
  * Hỗ trợ khóa lớp, ẩn/hiện lớp, chép lời lại vùng chọn bằng engine khác (**Alt: OCR/Whisper**).
* **Xuất video thông minh & Tối ưu hóa GPU NVENC:**
  * Tùy chọn 4 profile chất lượng (**Low, Medium, High, Very High**), tự động tận dụng mã hóa phần cứng **NVIDIA NVENC** và tự động fallback sang CPU `libx264` khi không có card rời.
  * Xuất trực tiếp sang định dạng **CapCut Draft** để tiếp tục dựng phim chuyên sâu.
* **Trình quản lý tài nguyên thông minh (Resource Manager):** Tải model và gói phụ trợ chỉ với 1 click (Whisper Turbo/Large-v3, SenseVoice, thư viện giọng đọc, gói CUDA tăng tốc) với thanh tiến độ % và dung lượng MB hiển thị trực quan theo thời gian thực.
* **Tối ưu hóa nạp video & quản lý bộ đệm:** Nạp cache waveform và video thumbnails ở chế độ nền mượt mà với thẻ trạng thái trực quan, không làm đơ ứng dụng.

## 🚀 Tính năng sắp tới

CapCap vẫn đang được phát triển tích cực, với nhiều tính năng mới và cải tiến được bổ sung theo thời gian.

👉 [Xem lộ trình phát triển](https://github.com/users/notepower2k1/projects/2)

## 📚 Tài liệu

* [Hướng dẫn sử dụng](docs/how-to-use.md)
* [Yêu cầu hệ thống và tài nguyên](docs/requirements.md)
* [Công nghệ sử dụng](docs/technical-stack.md)
* [Cấu trúc dự án](docs/project-structure.md)

## 🛠️ Chạy từ mã nguồn

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap

python -m venv venv
venv\Scripts\activate

pip install -r requirements-local.txt
python ui/gui.py
```

Bạn chỉ cần sao chép `.env_example` thành `.env` nếu muốn cấu hình thủ công các dịch vụ dịch thuật hoặc máy chủ từ xa.

Phần lớn thiết lập của CapCap có thể được cấu hình trực tiếp ngay trong ứng dụng.

### 💡 Khắc phục sự cố trình phát xem trước video (MPV)

CapCap mặc định sử dụng **libmpv** để xem trước video mượt mà và chuẩn xác từng khung hình:
* **Yêu cầu Visual C++:** `libmpv-2.dll` yêu cầu **Microsoft Visual C++ 2015–2022 Redistributable (x64)** (phiên bản >= 14.20).
* **Nếu MPV không khởi động được** (báo lỗi hoặc tự động chuyển về Qt player): Bạn có thể chạy file có sẵn tại `installer\vc_redist.x64.exe` hoặc tải trực tiếp bản mới nhất từ [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).
* **Cập nhật / Sử dụng thư viện MPV ngoài:** Nếu muốn nâng cấp hoặc dùng thư viện MPV tùy chỉnh, hãy đặt file `libmpv-2.dll` (bản 64-bit) vào thư mục `bin/mpv/`.

## ❤️ Ủng hộ CapCap

Nếu CapCap hữu ích với bạn, bạn có thể ủng hộ để giúp dự án tiếp tục được duy trì và phát triển.

### 🇻🇳 Ủng hộ tại Việt Nam

Quét mã QR bên dưới:

<img src="assets/qr.png" style="width: 25%; height: auto;">

### 🌍 Ủng hộ quốc tế

[![Buy Me a Coffee](assets/buymeacoffee.png)](https://buymeacoffee.com/hcaht)

Nhấp vào hình ảnh phía trên hoặc truy cập [Buy Me a Coffee](https://buymeacoffee.com/hcaht).

## 📄 Giấy phép

CapCap được phát hành theo **Apache License 2.0**.

Xem chi tiết tại [LICENSE](LICENSE).
