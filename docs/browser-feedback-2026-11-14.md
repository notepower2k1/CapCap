# Phân tích feedback trình duyệt 14/11/2026

Đã đọc hai log trong `feedback/` và trích khung hình từ video `2026-11-13 17-17-55.mp4` (233,6 giây). Đây là dữ liệu khách hàng cục bộ, không đóng gói cùng ứng dụng.

## Điều xác nhận được

- Lần quay dùng **ChatGPT Web, Temporary Chat, một luồng**, 691 câu chia 12 batch. Phân tích xưng hô hoàn tất và đã được duyệt.
- Batch 1–7 hoàn tất, tổng cộng 441 câu. Batch 8 (câu 442–501) trả kết quả không vượt qua kiểm tra ID vào **17:21:25**, sau **23,81 giây**. Tool giữ 441 câu AI, fallback 250 câu còn lại. Người dùng hủy lúc 17:21:32.
- Video có thao tác thay đổi vị trí và giảm mạnh chiều cao cửa sổ dịch; không đủ bằng chứng chỉ từ các khung hình để kết luận minimize xuống taskbar. Sau đó phần hiển thị chủ yếu là prompt, nút cuộn xuống và editor; không nhìn thấy toàn bộ phản hồi cuối để đếm số dòng thực tế.
- Lượt trước đó cũng lỗi đúng batch 8 ở 17:03:04. Log chỉ ghi tám ID đầu `[1..8]`; không ghi số ID thực tế, các ID thiếu hay phần cuối. Chưa phân biệt được AI thực sự trả thiếu với DOM bị trích thiếu/lấy nhầm lượt.
- Các lần chạy song song khác lỗi `send button is unavailable or disabled`. Có một lần tuần tự timeout 300 giây ở batch 9. Các biểu hiện này không chứng minh CAPTCHA hay hạn mức tài khoản.

## Sửa và bổ sung chẩn đoán trong mã nguồn

- Thay `document.execCommand('selectAll')` bằng Range chỉ bao editor; đặt lại focus/selection ngay trước lệnh chèn, kiểm tra nội dung đã chèn đủ trước khi coi thao tác thành công. Đây là rủi ro xác định được trong code khi cửa sổ mất focus giữa các bước bất đồng bộ; chưa khẳng định là nguyên nhân duy nhất của video.
- Khi nút gửi không sẵn sàng, lỗi kèm số ký tự trong editor, trạng thái đang sinh nội dung và visibility của trang.
- Khi phản hồi sai số câu, log kèm số dòng đã parse, số ký tự, ID thiếu/thừa và tám ID cuối. Không nhận kết quả thiếu là thành công.
- Trước khi kiểm tra định dạng thất bại, background đọc lại đúng node phản hồi của cùng task một lần, yêu cầu đã hoàn tất và không streaming. Không gửi lại prompt trong bước đọc lại. Nếu node thay đổi hoặc chưa hoàn tất thì báo lỗi provider để fallback.
- Nếu nội dung vẫn sai định dạng: retry batch một lần trong chat mới, dùng nguyên source và bộ quy tắc đã duyệt. Nếu vẫn sai, chia batch thành hai phần một lần, không chia đệ quy; mỗi phần gửi một lần trong chat mới. Chỉ cộng tiến độ batch khi cả hai phần hợp lệ. Nếu một phần lỗi, batch chưa hoàn thành sẽ fallback; các batch hợp lệ trước đó vẫn được giữ.
- Lỗi không phản hồi, rỗng, timeout, block, mất tab/kết nối không retry gửi bản dịch; user bấm Hủy thì dừng và không fallback. Đã bỏ cơ chế background tự mở lại tab và gửi lại khi mất liên lạc. Injection content script chỉ là khôi phục kết nối, không vượt qua xác minh của dịch vụ.
- Log CapCap kèm task/session/platform, thời gian chờ, fresh chat, số ký tự, trạng thái visibility/focus lúc nhận kết quả, dấu hiệu đã đọc lại, ID thiếu/thừa. Không ghi nguyên lời thoại vào log chẩn đoán này.

Thu nhỏ/minimize có thể ảnh hưởng tới cập nhật giao diện/timer của trình duyệt; riêng lỗi trong video là validation thất bại chứ không phải hết timeout. Cần thử đối chứng cửa sổ giữ nguyên kích thước và lần lặp có thu nhỏ, hoặc thu thập DOM phản hồi lỗi, trước khi kết luận quan hệ nhân quả. Bản sửa chưa build vào installer.
