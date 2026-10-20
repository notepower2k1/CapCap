# RELEASE NOTES — V8.0.6.1

**Ngày phát hành: Tháng 10/2026**

## 🌟 1. Tính năng mới nổi bật: Tích hợp ElevenLabs TTS đỉnh cao
- **Đầy đủ model mới nhất**: Tích hợp các model ElevenLabs tiên tiến gồm `eleven_turbo_v2_5` (mặc định tối ưu, tự nhiên và ổn định nhất), `eleven_flash_v2_5` (tốc độ siêu nhanh, tiết kiệm ký tự), `eleven_v4`, `eleven_v4_turbo`, và `eleven_v3`.
- **Hỗ trợ tiếng Việt mượt mà & thông minh**: Tự động nhận diện và chuyển đổi model thông minh nhằm triệt tiêu hoàn toàn lỗi `400 unsupported_language` khi xử lý phụ đề tiếng Việt.
- **Đồng bộ mã ngôn ngữ (`language_code`)**: Hỗ trợ truyền tham số `language_code` xuyên suốt từ giao diện, voice catalog, engine cho đến pipeline render TTS.
- **Tự động nhận diện gói cước & Quota**: Tự động phát hiện bậc tài khoản (Free / Starter / Creator / Pro) và hiển thị chính xác số lượng ký tự quota đã dùng (`used / limit`).
- **Phân loại giọng trực quan**: Gắn nhãn phân loại rõ ràng trong danh sách giọng: `[Default]` (Mặc định), `[Clone]` (Bản sao), `[Design]` (Thiết kế), và biểu tượng `🔒 (Cần gói trả phí)` cho các giọng lấy từ Voice Library cộng đồng.
- **Chống lỗi quota cho tài khoản Free**: Tự động cảnh báo và ngăn chặn tài khoản Free gọi giọng Voice Library (vốn yêu cầu tài khoản trả phí) để tránh lỗi HTTP 402 lãng phí quota; tự động vô hiệu hóa (disable) các giọng bị khóa trong menu lựa chọn.
- **Nghe thử giọng tức thì (Voice Preview)**: Hỗ trợ phát âm thanh mẫu trực tiếp từ ElevenLabs (`preview_audio_url`) tức thì mà không yêu cầu tải model Piper cục bộ.
- **Kiểm soát luồng song song & Thử lại khi Rate Limit**: Tự động giới hạn luồng xử lý song song an toàn cho gói Free (tối đa 2 luồng) và mở khóa 3-4 luồng cho các gói trả phí; tích hợp cơ chế tự động thử lại (retry with backoff) khi gặp lỗi HTTP 429 rate limit.
- **Đồng bộ danh mục bất đồng bộ mượt mà**: Toàn bộ tiến trình tải danh sách giọng, thông tin model và kiểm tra quota đều chạy ngầm qua `QThread`, hoàn toàn không gây giật lag hay đơ giao diện.
- **Cơ chế Cache thông minh & an toàn**: Cache âm thanh với SHA-256 fingerprint, cơ chế ghi file nguyên tử (`atomic write`), tự động vô hiệu hóa cache khi đổi model hoặc language_code để không bị lẫn giọng cũ.
- **Kiểm tra âm thanh hợp lệ**: Tích hợp bộ kiểm tra âm thanh không im lặng (`non-silent WAV check`) trước khi ghi nhận file vào cache, đảm bảo chất lượng lồng tiếng tuyệt đối.

## ⚙️ 2. Cải tiến giao diện Cài đặt (Settings)
- **Tổ chức giao diện dạng Tab chuyên nghiệp**: Tách hộp thoại Cài đặt thành các tab độc lập, trực quan và dễ quản lý:
  - 🎙️ **Nhận dạng & Phụ đề** (`Transcription`)
  - 🌐 **Dịch thuật AI** (`AI Translation`)
  - 🔊 **ElevenLabs** (Quản lý API Key, chọn model, giọng đọc, đồng bộ quota)
  - ⚡ **CapCut TTS** (Cấu hình và kiểm tra kết nối dịch vụ giọng CapCut)
- **Hướng dẫn phân quyền API Key chi tiết**: Tích hợp sẵn khung hướng dẫn cấu hình API Key ElevenLabs với đầy đủ 4 quyền hạn bắt buộc:
  - `Text to Speech: Access`
  - `Voices: Read`
  - `Voice collection: Read`
  - `User: Access`
- **Tối ưu thanh điều khiển chung**: Đưa nút **Quản lý tài nguyên** (`Manage Resources`) và **Mở thư mục giọng** (`Open Voices Folder`) ra thanh điều khiển chung bên dưới hộp thoại để truy cập nhanh chóng từ bất kỳ tab nào.
- **Tinh gọn**: Ẩn tab Remote API lỗi thời để đơn giản hóa trải nghiệm người dùng.

---

# RELEASE NOTES — V8.3

**Ngày phát hành: 11/10/2026**

## 🌟 1. Tính năng mới nổi bật
- **Giao diện tối ưu cho Video dọc (9:16)**: Tự động nhận diện video dạng dọc (TikTok, Shorts, Reels) để chuyển đổi không gian làm việc tối ưu; dàn ngang hàng nút Subtitle Inspector và thu gọn thanh công cụ Layer, nhường tối đa diện tích cho màn hình xem trước và Timeline.
- **Xuất file âm thanh độc lập (Export Audio)**: Bổ sung tùy chọn xuất riêng file giọng lồng tiếng (Voice), nhạc nền (BGM) hoặc bản phối tổng hợp trong menu Mở rộng.
- **Tạo lại giọng đọc từng câu tức thì**: Đánh dấu các câu phụ đề vừa sửa và cho phép tạo lại giọng đọc ngay tại câu đó mà không cần render lại toàn bộ dự án.
- **Mở khóa công cụ Layer độc lập**: Cho phép chèn và xuất video kèm các lớp Chữ, Logo, Làm mờ (Blur), Mặt nạ (Mask) ngay cả khi không có phụ đề.
- **Kéo thả phụ đề đa tầng trên Timeline**: Hỗ trợ di chuyển tự do các khối phụ đề giữa các dòng trên Timeline với cơ chế chống đè thông minh.
- **Nhập phụ đề SRT linh hoạt**: Hỗ trợ đọc các định dạng SRT phong phú và hiển thị thông báo trực quan nếu file phụ đề bị lỗi cú pháp thời gian.

## ⚡ 2. Cải tiến & Tối ưu hiệu năng
- **Tăng tốc tách giọng / tách nhạc bằng GPU**: Hỗ trợ tận dụng card đồ họa NVIDIA (CUDA) để tách giọng nói nhanh hơn, tối ưu bộ nhớ VRAM cho các dòng card phổ thông (6GB).
- **Mở ứng dụng và tải dự án mượt mà**: Khởi tạo trình phát ngầm thông minh, loại bỏ hoàn toàn hiện tượng đơ trắng màn hình hoặc thông báo "Not Responding" lúc mở dự án.
- **Tối ưu hiển thị Timeline & Sóng âm**: Tăng tốc độ vẽ sóng âm thanh, giúp thao tác cuộn chuột và phóng to/thu nhỏ dòng thời gian mượt mà, không giật lag.
- **Tinh gọn giao diện người dùng**: Gom nhóm các tính năng xuất phụ đề vào menu Mở rộng; thêm nút gập/mở thanh công cụ ở mép màn hình giúp mở rộng tối đa vùng làm việc khi cần.

## 🛠️ 3. Sửa lỗi quan trọng
- **Sửa triệt để lỗi mất âm thanh xem trước**: Khắc phục tình trạng video xem trước bị im lặng sau khi hoàn tất quy trình dịch thuật, hoặc âm thanh chạy tràn khi video kết thúc.
- **Khắc phục lỗi dịch thuật bị treo ở 99%**: Cập nhật tiến độ dịch thuật theo thời gian thực và sửa lỗi ngắt luồng đột ngột khi xử lý văn bản dài.
- **Đồng bộ chuẩn xác tốc độ giọng đọc**: Khắc phục lỗi lệch tốc độ giọng lồng tiếng giữa bảng chỉnh sửa câu và thanh Timeline.
- **Sửa lỗi xuất video có hiệu ứng**: Khắc phục lỗi xuất video khi áp dụng hiệu ứng làm mờ nền (Blur) hoặc các đoạn video có thay đổi tốc độ (Timewarp).
- **Chặn đè khối phụ đề trên Timeline**: Ngăn chặn việc kéo phụ đề vào khoảng trống nhỏ hơn độ dài câu gây đè nát câu bên cạnh.
- **Tắt hoàn toàn tác vụ ngầm khi đóng app**: Đảm bảo dọn dẹp sạch sẽ toàn bộ các tiến trình xử lý nền khi tắt phần mềm, tránh chiếm dụng tài nguyên máy tính.
