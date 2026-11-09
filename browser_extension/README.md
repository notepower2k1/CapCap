# 🚀 CapCap Web AI Translator Bridge (Extension)

**Version 9.0.0.** Logo thanh trình duyệt và popup dùng logo app trong `assets/capcap.ico`.

Trong bản cài, dùng **Cài đặt → Dịch thuật → Quick Install Extension** để lấy đúng thư mục được đóng gói (có thể nằm trong `_internal/browser_extension`). Đường dẫn `browser_extension/` bên dưới là thư mục tương đối khi chạy từ source. Sau khi cập nhật, Reload extension rồi tải lại các tab AI đã mở.

Tiện ích mở rộng trình duyệt (Chrome & Edge) kết nối trực tiếp phần mềm **CapCap Video Translator** với giao diện web của **Google Gemini** và **ChatGPT**. Dịch phụ đề video hoàn toàn tự động, chất lượng cao, giữ ngữ cảnh và không lưu lại lịch sử chat rác.

Browser extension for Google Chrome & Microsoft Edge connecting **CapCap Video Translator** directly to **Google Gemini** & **ChatGPT Web**. Translates video subtitles with full context, high quality, and zero chat history clutter.

---

## 🇻🇳 Hướng Dẫn Cài Đặt (2 Bước Đơn Giản)

### Bước 1: Mở trang Quản lý tiện ích mở rộng
- **Google Chrome**: Truy cập `chrome://extensions/`
- **Microsoft Edge**: Truy cập `edge://extensions/`
- **Brave / Cốc Cốc**: Truy cập `brave://extensions/` hoặc `coccoc://extensions/`

### Bước 2: Tải tiện ích đã giải nén
1. Bật công tắc **"Chế độ dành cho nhà phát triển" (Developer mode)** ở góc trên bên phải màn hình.
2. Nhấn nút **"Tải tiện ích đã giải nén" (Load unpacked)** ở góc trên bên trái.
3. Chọn thư mục:
   ```
   browser_extension/
   ```
4. Tiện ích **CapCap Web AI Translator Bridge** sẽ xuất hiện trên thanh tiện ích của trình duyệt!

---

## 🇬🇧 Installation Guide (2 Simple Steps)

### Step 1: Open Extension Management
- **Google Chrome**: Go to `chrome://extensions/`
- **Microsoft Edge**: Go to `edge://extensions/`
- **Brave / Chromium**: Go to `brave://extensions/`

### Step 2: Load Unpacked Extension
1. Toggle on **"Developer mode"** in the top-right corner.
2. Click **"Load unpacked"** in the top-left corner.
3. Select the folder:
   ```
   browser_extension/
   ```
4. **CapCap Web AI Translator Bridge** is now ready in your browser toolbar!

---

## 🌟 Tính Năng Nổi Bật (Key Features)

1. **🛡️ Tự động Không lưu lịch sử (Temporary Chat / No History)**:
   - **ChatGPT**: Tự động mở và duy trì chế độ `?temporary-chat=true`. Các đoạn hội thoại dịch không bị lưu vào danh sách chat của tài khoản OpenAI.
   - **Gemini**: Bật và xác nhận chế độ Temporary Chat trước khi gửi prompt, giữ chung phiên cho phân tích và dịch. Nếu không xác nhận được, báo lỗi và không gửi prompt vào chat thường. Việc đóng cửa sổ/tạo chat mới chỉ là dọn giao diện, không phải xóa lịch sử. Xem [hướng dẫn Google](https://support.google.com/gemini/answer/13275745?co=GENIE.Platform%3DDesktop&hl=en).

2. **🧠 Giữ ngữ cảnh thông minh (Session Context Preservation)**:
   - Các batch phụ đề thuộc cùng một video/phiên dịch được tự động đưa vào **cùng một tab và cuộc trò chuyện**.
   - AI ghi nhớ ngữ cảnh các câu trước, đảm bảo đại từ xưng hô, thuật ngữ đồng nhất xuyên suốt video.

3. **🪟 Chạy trong cửa sổ riêng (Mặc định)**:
   - Tự mở một cửa sổ Gemini/ChatGPT riêng và dùng chung cho phân tích nhân vật, phân tích lại và dịch. Không cần mở sẵn tab AI hoặc giữ popup extension mở.
   - Dịch tự động chạy trong một cửa sổ riêng biệt (`type: popup`, không chiếm tiêu điểm `focused: false`), hoàn toàn không làm gián đoạn hay nhảy tab khi bạn đang lướt web hoặc làm việc.
   - **Tùy chọn hiển thị linh hoạt trong Popup Extension**:
     - **Chạy trong cửa sổ riêng (mặc định)**: Tự động mở cửa sổ độc lập để dịch, tự đóng cửa sổ khi hoàn thành phiên dịch.
     - **Dùng tab hiện tại**: Tái sử dụng tab ChatGPT/Gemini có sẵn và kích hoạt hiện tab lên mỗi batch.
   - 💡 **Mẹo quan trọng**: **Không thu nhỏ (minimize)** cửa sổ dịch xuống taskbar; bạn có thể kéo cửa sổ sang một góc màn hình hoặc sang màn hình thứ hai (trình duyệt Chrome sẽ tạm ngưng / throttle bộ hẹn giờ của các cửa sổ bị thu nhỏ).

4. **📊 Bảng điều khiển đồng bộ thời gian thực (Popup Dashboard)**:
   - Hiển thị trạng thái kết nối với phần mềm CapCap qua cổng nội bộ `127.0.0.1:18765`.
   - Xem trực tiếp AI Engine đang dùng (`Google Gemini Web` / `ChatGPT Web`).
   - Theo dõi tiến trình dịch: Phần trăm hoàn thành, số Batch hiện tại, số câu phụ đề đã dịch.
   - Nút mở nhanh tab Gemini hoặc ChatGPT Temporary Chat chỉ với 1 click.

5. **⚡ Tự động hóa DOM bền bỉ (Resilient Automation)**:
   - Hỗ trợ cả `contenteditable` (ProseMirror, Quill) và `textarea`.
   - Cơ chế phát hiện hoàn tất thông minh theo thời gian thực (kết hợp MutationObserver và kiểm tra độ ổn định văn bản trong 1500ms khi nút Stop biến mất, không lo timer bị throttle ở chế độ chạy nền).

---

## 🛠️ Cấu Trúc Thư Mục (Folder Structure)

```
browser_extension/
├── manifest.json       # Cấu hình Manifest V3 của Extension
├── background.js       # Background Service Worker (Polling, quản lý Tab và phiên dịch)
├── content.js          # DOM Automation cho ChatGPT & Gemini
├── popup.html          # Giao diện Popup Dashboard đồng bộ
├── popup.js            # Logic hiển thị đồng bộ tiến trình thời gian thực
├── icons/              # Bộ icon 16x16, 48x48, 128x128
└── README.md           # Hướng dẫn cài đặt và sử dụng
```
