# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

[English](README_en.md) | [Tiếng Việt](README.md)

| 🖥️ Giao diện Video Ngang (16:9) | 📱 Giao diện Video Dọc (9:16) |
| :---: | :---: |
| ![Giao diện CapCap 16:9](assets/preview_1.png) | ![Giao diện CapCap 9:16](assets/preview_2.png) |

### [🎬 Demo & Hướng dẫn sử dụng](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** là ứng dụng biên tập và bản địa hóa video tất-cả-trong-một dành cho Windows. Giúp tự động hóa toàn bộ quy trình: nhận diện giọng nói, dịch thuật AI, lồng tiếng tự nhiên, chỉnh sửa lớp phủ và xuất video hoàn chỉnh.

---

## ✨ Điểm nổi bật

* 🔄 **Quy trình 5 bước trực quan:** Chuẩn bị → Chép lời (STT / OCR) → Dịch AI → Lồng tiếng (TTS) → Xuất video.
* 📱 **Giao diện tự thích ứng (16:9 & 9:16):** Tự động nhận diện và tối ưu bố cục chuyên biệt cho video ngang (YouTube, Phim) hoặc video dọc (TikTok, Shorts, Reels).
* 🧠 **Dịch thuật AI chuẩn ngữ cảnh & xưng hô:** Tích hợp Gemini 2.5, GPT-4o, DeepSeek, Ollama (offline) cùng Google/Bing Dịch. Tự động phân tích quan hệ nhân vật và cho phép góp ý xưng hô bằng lời.
* 🎙️ **Lồng tiếng & Tách nhạc nền thông minh:** Hỗ trợ ElevenLabs TTS (chất lượng phòng thu), Piper (offline), Edge TTS, CapCut TTS và VieNeu TTS (nhân bản giọng). Tự động tách giọng gốc và giữ trọn nhạc nền (BGM).
* 🎬 **Biên tập Timeline đa tầng chuyên nghiệp:** Chỉnh sửa phụ đề trực quan, thêm Logo, Làm mờ (Blur), Mặt nạ (Mask), Văn bản (Text). Cho phép tạo lại giọng đọc tức thì cho từng câu vừa sửa.
* ⚡ **Xuất video siêu tốc:** Tăng tốc phần cứng GPU (NVIDIA NVENC), hỗ trợ xuất riêng file âm thanh (Voice, BGM, Mix) hoặc xuất sang dự án **CapCut Draft** để dựng tiếp.

---

## 📚 Tài liệu chi tiết

* 📖 [Hướng dẫn sử dụng](docs/how-to-use.md)
* 💻 [Yêu cầu hệ thống và tài nguyên](docs/requirements.md)
* ⚙️ [Công nghệ sử dụng](docs/technical-stack.md)
* 📁 [Cấu trúc dự án](docs/project-structure.md)
* 📋 [Ghi chú phát hành (Release Notes)](release.md)

---

## 🛠️ Chạy từ mã nguồn

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap

python -m venv venv
venv\Scripts\activate

pip install -r requirements-local.txt
python ui/gui.py
```

* Bạn chỉ cần sao chép `.env_example` thành `.env` nếu muốn cấu hình thủ công các API key hoặc máy chủ dịch từ xa. Hầu hết thiết lập có thể chỉnh trực tiếp trên giao diện.

### 💡 Khắc phục sự cố trình phát xem trước video (MPV)

CapCap mặc định sử dụng **libmpv** để xem trước video mượt mà và chuẩn xác từng khung hình:
* **Yêu cầu Visual C++:** `libmpv-2.dll` yêu cầu **Microsoft Visual C++ 2015–2022 Redistributable (x64)** (phiên bản >= 14.20).
* **Nếu MPV không khởi động được:** Chạy file cài đặt có sẵn tại `installer\vc_redist.x64.exe` hoặc tải trực tiếp từ [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).

---

## ❤️ Ủng hộ dự án

Nếu CapCap hữu ích với công việc của bạn, hãy ủng hộ để giúp dự án tiếp tục được hoàn thiện:

### 🇻🇳 Ủng hộ tại Việt Nam
Quét mã QR bên dưới:

<img src="assets/qr.png" style="width: 20%; height: auto;">

### 🌍 Ủng hộ quốc tế
[![Buy Me a Coffee](assets/buymeacoffee.png)](https://buymeacoffee.com/hcaht)

---

## 📄 Giấy phép

CapCap được phát hành theo **Apache License 2.0**. Xem chi tiết tại [LICENSE](LICENSE).
