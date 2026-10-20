# RELEASE NOTES — V8.0.6.1

**Ngày phát hành: 20/10/2026**

## 🌟 1. Tích hợp ElevenLabs TTS
- **Hỗ trợ các model mới nhất**: Tích hợp các model tiên tiến (`eleven_turbo_v2_5`, `eleven_flash_v2_5`, `eleven_v4`, `eleven_v3`), tự động tối ưu hóa khi lồng tiếng tiếng Việt.
- **Quản lý tài khoản & Quota**: Hiển thị số ký tự đã sử dụng và tự động phân loại giọng khả dụng theo gói tài khoản (Free / Paid).
- **Trải nghiệm mượt mà**: Hỗ trợ nghe thử giọng trực tiếp và đồng bộ danh sách giọng chạy ngầm không gây giật lag giao diện.

## ⚙️ 2. Cải tiến giao diện Cài đặt (Settings)
- **Bố cục dạng Tab gọn gàng**: Chia thành 4 tab riêng biệt dễ quản lý: *Nhận dạng*, *Dịch thuật AI*, *ElevenLabs*, *CapCut TTS*.
- **Hướng dẫn tạo API Key trực quan**: Tích hợp sẵn hướng dẫn chi tiết các quyền cần cấp khi tạo API Key ElevenLabs (`Text to Speech`, `Voices`, `Voice collection`, `User`).

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
