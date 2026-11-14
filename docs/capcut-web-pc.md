# CapCut Web và CapCut PC cho STT/TTS

Trong **Cài đặt → CapCut → Kết nối CapCut**, chọn:

- **CapCut Web (mặc định)**: giữ cách gọi HTTP API hiện tại, không cần cài CapCut.
- **CapCut PC (chạy nền, thử nghiệm)**: dùng thư viện mạng `sscronet.dll` của CapCut PC đã cài trên Windows. Không khởi chạy `CapCut.exe`, không cần mở giao diện CapCut.

Lựa chọn áp dụng chung cho STT và TTS. Có thể đổi ngay tại phần **CapCut STT Settings**; hai bộ chọn đồng bộ với nhau. Chọn **Lưu** để áp dụng. Trong chế độ Performance, lựa chọn cũng được lưu vào cấu hình xử lý của từng video.

Bản PC được tìm tự động trong thư mục cài đặt phổ biến và Windows Uninstall registry. Nếu có nhiều phiên bản, ưu tiên số phiên bản mới nhất. Với bản cài ở nơi khác, chọn thư mục CapCut trong tab CapCut; có thể nhập đường dẫn thư mục phiên bản, `CapCut.exe` hoặc `sscronet.dll`. Để trống để tìm tự động.

## Cách hoạt động và giới hạn

Cả hai chế độ vẫn gửi tác vụ STT/TTS tới máy chủ CapCut và cần internet. Chế độ PC dùng transport Cronet/TTNet của bản desktop cho các API điều khiển tác vụ; upload VOD và tải audio vẫn dùng HTTP hiện tại. Đây không phải nhận dạng/tạo giọng offline, không sử dụng tài khoản đang đăng nhập trong CapCut, và không bảo đảm tránh được lỗi hoặc giới hạn của cloud.

Thư viện native chạy trong tiến trình con của CapCap, tách khỏi tiến trình giao diện. Các session đồng thời dùng chung một tiến trình theo nhóm API (STT và TTS), thay vì mở CapCut hoặc một tiến trình cho từng câu. Tiến trình được tái sử dụng và dọn khi CapCap thoát. Khi mất tiến trình, yêu cầu hiện tại nhận lỗi kết nối; lần gọi tiếp theo có thể khởi tạo lại. Cơ chế retry STT hiện có vẫn xử lý timeout/lỗi kết nối/HTTP tạm thời.

Không tìm thấy DLL hoặc DLL không tương thích sẽ báo lỗi rõ ràng, không tự đổi sang Web. Người dùng có thể chuyển về Web trong cài đặt. CapCap không đóng gói hoặc sao chép DLL của CapCut vào installer.

Cấu hình tương đương trong `.env`:

```dotenv
CAPCUT_BACKEND=pc
CAPCUT_PC_PATH=
```

## Chất lượng STT/TTS

Cả Web và PC cùng nhận các [cải thiện ngôn ngữ STT, timestamp từng từ và kiểm tra TTS](capcut-speech-quality.md). Với STT tiếng Việt/Anh hoặc các ngôn ngữ ngoài tiếng Trung, chọn rõ ngôn ngữ đầu vào. Auto Detect vẫn dùng luồng cũ.

## Log chẩn đoán

- `[CapCut STT] ... (backend=web|pc)`: đường kết nối đã chọn.
- `[CapCut PC] Native background process ready pid=... library=...`: PID và bản DLL thực tế.
- `[CapCut PC] POST /... http=... elapsed=... bytes=...`: đường API, HTTP status, thời gian và kích thước phản hồi.
- `Request failed type=...`: timeout, kết nối hoặc lỗi native; mất tiến trình có exit code. Không ghi body audio, credential hoặc URL có chữ ký.

Khi báo lỗi, gửi runtime log và phiên bản CapCut PC. Log PID/DLL giúp phân biệt lỗi thư viện với lỗi phản hồi cloud.

## Kiểm chứng mã nguồn ngày 14/11/2026

Đã thử với CapCut PC **9.7.0.4153**, Windows x64, giao diện CapCut không chạy:

- Khởi tạo thư viện và xin quyền upload STT thành công.
- Bốn yêu cầu TTS đồng thời trả về audio; các session dùng cùng tiến trình native TTS.
- Tạo WAV mono 16 kHz qua luồng TTS của CapCap.
- STT nhận về phụ đề trên audio tổng hợp; thử thêm nhiều chunk, hai worker đồng thời.
- Kiểm thử tự động bộ chọn STT/TTS đồng bộ, lưu/khôi phục Web/PC và hủy không áp dụng thay đổi; kiểm thử routing HTTP/native, HTTP error và chọn phiên bản DLL.
- Bộ hồi quy liên quan: **69 tests và 24 subtests pass**, gồm CapCut, cài đặt nhận dạng, bản dịch giao diện và hàng đợi Performance.

Đây là kiểm tra kết nối và luồng xử lý trên mẫu ngắn, chưa phải benchmark độ ổn định video dài hoặc xác nhận mọi phiên bản CapCut PC. Installer chưa được build lại cho thay đổi này.

## Tham khảo kỹ thuật

- [CapCut TTS API — hướng Cronet Windows chạy không cần giao diện](https://github.com/thonguyeen/capcut-tts-api-tn).
- [Chromium Cronet C API IDL và quy định quyền sở hữu buffer](https://github.com/chromium/chromium/blob/130.0.6723.58/components/cronet/native/cronet.idl).

Binding Python trong CapCap được viết trực tiếp cho C ABI; không sử dụng binary helper từ repo tham khảo.
