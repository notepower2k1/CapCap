# CapCap — bản installer để kiểm thử, 14/10/2026

Bản này đóng gói lại source bằng PyInstaller, sau đó tạo bộ cài Inno Setup qua `scripts/build_installer.py --build-app`.

## Các thay đổi đã đưa vào

- **Fit Voice và âm thanh cuối video:** đồng bộ độ dài timeline, âm thanh gốc và giọng đọc theo các đoạn kéo dài; cập nhật lại bộ đọc âm thanh khi file được tạo lại. Áp dụng cho cả preview và video xuất.
- **Cài đặt và tắt ứng dụng:** installer chặn cập nhật khi CapCap hoặc worker còn chạy, kiểm tra cả lúc mở installer và trước khi chép file. Tiến trình xử lý và FFmpeg được gắn với vòng đời app; bổ sung dọn worker khi đóng cửa sổ.
- **Whisper trên video dài:** bỏ chế độ chia việc giữa GPU và worker CPU khi dùng CUDA; các chunk nhận dạng đều chạy trên GPU. Bổ sung `multiprocessing.freeze_support()` để worker của bản đóng gói khởi động đúng. Máy dùng CPU vẫn có luồng CPU riêng.
- **Xuất video đứng ở 20%:** cập nhật tiến độ trong bước ghép âm thanh theo tiến độ FFmpeg; chuyển đổi FPS ở lần render cuối để tránh mã hóa thêm ở bước trung gian. Giữ tiến độ khi phải chuyển từ GPU sang CPU.
- **Chất lượng xuất:** chỉ còn **Thấp** và **Cân bằng (Khuyến nghị)**, mặc định Cân bằng. Cấu hình High/Very High cũ dùng Cân bằng, kể cả khi gọi các hàm mã hóa trực tiếp. Độ phân giải và FPS được chọn riêng.
- **Dịch và fallback:** thêm log từng batch/request, retry, thời gian chờ, số câu và kết quả trả về. UI hiển thị lý do chuyển sang Google Translate, ví dụ giới hạn quota, lỗi xác thực, provider quá tải, timeout hoặc phản hồi không đủ câu; giữ lý do trong trạng thái project.
- **Nhập transcript gốc:** thêm Auto Transcript/Re-transcript và Import Transcript File trong menu Step-by-Step. Transcript nhập từ SRT được ưu tiên khi chạy quy trình, đến khi người dùng chủ động Re-transcript; nếu chép lời lại thất bại thì giữ transcript đã nhập.

Các sửa UI bố cục 9:16, preview/timeline và xác nhận nhập SRT khác số câu đã có trong source trước đó cũng được giữ trong bản đóng gói này.

## Phạm vi còn lại

- Lỗi chép lời thiếu/lặp câu theo feedback mới chưa sửa, theo yêu cầu để xử lý sau.
- Thông báo fallback giúp xác định nguyên nhân; không thay thế việc xử lý quota hoặc lỗi của AI provider.
- Chưa xác nhận ca treo xuất video của KH thiếu log bằng chính video đó.

## Kiểm tra trước khi đóng gói

- Nhóm regression: 219 kiểm tra, 218 qua ở lần đầu. Kiểm tra đóng app có một lần crash native; chạy riêng lại bốn lần đều qua, gồm đóng bình thường và kết thúc đột ngột.
- Sau thay đổi hai mức render: 23 kiểm tra về xuất video, chất lượng, i18n và timewarp đều qua.
- Đối chiếu code trong executable với source hiện tại: 27 file đã sửa, 47 module tương ứng đều khớp.
- Worker CPU chạy thành công từ executable mới dù kế thừa chế độ server, và thoát sau khi kết thúc pool.
- Chạy lại ba kiểm tra guard installer với executable mới: đều qua, gồm tiến trình đang chạy, worker khởi động sau khi mở wizard và mutex GUI.

Log build: `logs/build_installer_20261014_234719.log`.

## Bản installer 8.0.5 đã kiểm thử

Thông tin dưới đây ghi nhận lần build 8.0.5. Đường dẫn installer được dùng lại khi build phiên bản mới.

- File: `dist_installer/CapCap_Setup.exe`.
- Hoàn tất: 15/10/2026 lúc 00:00, sau đợt build bắt đầu ngày 14/10.
- Dung lượng: 855.664.566 byte, khoảng 816 MiB.
- SHA256: `DD1E8DAEF86F08EC296C757DF0AA7623F106AD19804B50503C4443B0E722B0A7`.
- Chưa cài đè bản đang dùng hoặc chạy lại toàn bộ pipeline bằng video dài trên installer này.

## Nâng lên 8.0.6

- Người dùng đã xác nhận kiểm thử cơ bản bản installer phía trên hoạt động ổn.
- Đồng bộ version 8.0.6 ở launcher và Inno Setup, giữ các sửa đã kiểm thử.
- Đóng gói lại bằng `scripts/build_installer.py --build-app`.
