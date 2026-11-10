# 🚀 CapCap Web AI Translator Bridge (Extension)

**Version 9.0.0.** Logo thanh trình duyệt và popup dùng logo app trong `assets/capcap.ico`.

Nếu một batch dịch lỗi, CapCap giữ các batch AI đã hoàn thành hợp lệ và chỉ dùng Google Translate cho những câu chưa có kết quả. Nội dung không đủ/sai số câu của batch lỗi không được ghép vào bản dịch. Hủy tác vụ không tự chuyển provider.

Để dịch song song: trong **Cài đặt → Dịch thuật → Browser AI → Số batch chạy đồng thời**, chọn **2, 3 hoặc 4 — Song song, cuộc trò chuyện riêng**. CapCap xử lý tối đa bốn batch cùng lúc theo lựa chọn. Mỗi luồng giữ một tab/cửa sổ để nhận các batch tiếp theo; trước mỗi batch tạo và xác nhận cuộc trò chuyện mới, bật Temporary Chat theo lựa chọn. Chỉ đóng các cửa sổ của luồng khi cả lượt dịch kết thúc hoặc bị hủy. Kết quả được ghép theo thứ tự phụ đề gốc; tiến độ đếm tổng số câu đã hoàn thành dù batch trả lời lệch thứ tự. Mặc định vẫn là **1 — Tuần tự**. Cần cập nhật cả ứng dụng và reload extension để dùng cách mới.

Nếu nút tạo chat mới không hoạt động, tool điều hướng về chat mới trong chính tab đó rồi xác nhận lại trước khi gửi. Không gửi prompt vào hội thoại cũ khi bước xác nhận thất bại. Chưa benchmark tốc độ thực tế cho cách giữ cửa sổ và chưa xác nhận ảnh hưởng tới yêu cầu xác minh robot; nếu trang yêu cầu xác minh thì người dùng cần xử lý thủ công.

Các batch song song nhận cùng hướng dẫn ngữ cảnh/nhân vật đã phân tích; không truyền nối tiếp lịch sử của batch trước như chế độ tuần tự. Chạy hai batch có thể giảm thời gian chờ, nhưng tốc độ còn tùy dịch vụ và giới hạn tài khoản AI. Nếu bị giới hạn yêu cầu, chuyển về một batch.

Thử trực tiếp ngày 10/11: GPT và Gemini đều hoàn tất mức 4 batch × 40 câu bằng bản thử nghiệm, chưa gặp giới hạn yêu cầu trong lượt đo. Gemini cần các cửa sổ hiện rõ: popup bị che có thể giữ trạng thái animation/đang trả lời dù dữ liệu đã có. Không thu nhỏ hoặc che kín cửa sổ dịch khi thử song song. Xem [báo cáo và giới hạn của phép thử](../docs/browser-parallel-live-test-2026-11-10.md). Sau lượt thử, đã mở lựa chọn 1–4 trong ứng dụng và nâng giới hạn extension lên 4; chưa tự bố trí cửa sổ, mặc định vẫn chạy tuần tự.

Trong bản cài, dùng **Cài đặt → Dịch thuật → Quick Install Extension** để lấy đúng thư mục được đóng gói (có thể nằm trong `_internal/browser_extension`). Đường dẫn `browser_extension/` bên dưới là thư mục tương đối khi chạy từ source. **Sau khi cập nhật CapCap, vào `chrome://extensions` hoặc `edge://extensions`, bấm Reload tiện ích CapCap rồi tải lại các tab AI đã mở.** Nếu đã copy extension sang thư mục khác, cập nhật các file trong bản sao đó trước khi Reload. Nếu đường dẫn thư mục extension thay đổi, dùng Load unpacked từ thư mục mới; Reload thư mục chứa bản cũ sẽ vẫn chạy code cũ.

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
   - Ở chế độ tuần tự mặc định, các batch phụ đề thuộc cùng một video/phiên dịch được tự động đưa vào **cùng một tab và cuộc trò chuyện**. Khi chọn 2–4 batch đồng thời, mỗi luồng giữ một tab/cửa sổ và tạo cuộc trò chuyện mới cho từng batch.
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
   - Chỉ nhận câu trả lời mới khi khối trả lời đó có dấu hiệu hoàn tất (nút sao chép/đánh giá hiện ra) và không còn trạng thái đang sinh nội dung. Theo dõi cả thay đổi nội dung và thuộc tính DOM; khoảng dừng sinh chữ không được coi là hoàn tất. Timeout chỉ báo lỗi, không trả nội dung dở dang.

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
