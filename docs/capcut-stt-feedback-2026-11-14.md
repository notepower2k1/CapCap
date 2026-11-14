# CapCut STT timeout — feedback 14/11/2026

Log khách hàng `feedback/capcap_logs_20261113_031838.txt`: audio dài 313,93 giây, chia 2 đoạn theo cấu hình 300 giây. Kết nối cloud lúc 03:15:58, nhận kết quả 1/2 đoạn lúc 03:16:06, bước prepare thất bại lúc 03:18:02 với `CapCut STT query timed out after 120.0 seconds`.

Lỗi phát sinh trong vòng poll kết quả STT sau upload/submit; không phải lỗi tách âm thanh, GPU hoặc dịch phụ đề. Không đủ dữ liệu cũ để xác định đoạn dài hay đoạn đuôi timeout: log chỉ đếm số đoạn hoàn thành, không ghi index hoặc job ID. Có thể job cloud chậm/kẹt hoặc trả kết quả rỗng; không có bằng chứng chia đoạn quá nhỏ hay bị rate limit. Bản hiện tại trong source mặc định 600 giây nhưng thiết lập 300 giây trong log có thể do cấu hình/bản cài cũ.

Thay đổi mã nguồn:

- Thời gian chờ theo độ dài: `min(600, max(120, duration * 1.5))` giây; đoạn 300 giây được chờ tối đa 450 giây. Poll cùng job, không liên tục submit lại.
- Sau timeout hoặc lỗi mạng tạm thời trong submit/query, chờ 2 giây rồi gửi lại job nhận dạng đúng đoạn đó **một lần**, dùng audio đã upload, không tách/upload lại. Đây là job mới; job cũ có thể vẫn tiếp tục chạy phía cloud. Nếu retry vẫn timeout thì mới đi tới phục hồi chia đôi. Các phần nhỏ cũng tối đa hai lần nhận dạng nhưng không chia tiếp. Lỗi kết nối thất bại lần hai báo lỗi; lỗi xác thực/API không retry.
- Nếu job timeout, chỉ đoạn lỗi dài hơn 30 giây được chia đôi một lần, chồng lấn hai giây quanh điểm cắt. Các đoạn đã xong được giữ trong lượt xử lý; không chạy lại toàn bộ file. Timestamps của các phần được dịch về đúng vị trí gốc và merge overlap. Nếu phục hồi tiếp tục timeout, tác vụ vẫn báo lỗi; không trả transcript thiếu như thành công.
- Lỗi xác thực/API khác không được coi là job chậm để chia nhỏ.
- Log gồm index đoạn, vị trí/thời lượng, job ID, số lần poll, thời gian đã chờ, mã ret, tên trường trong response và số utterance. Không ghi token, upload URL hoặc nguyên transcript.

Kiểm tra mô phỏng: 7 test STT recovery và 3 test CapCut TTS pass (thêm ba trường hợp con timeout/network), gồm job hoàn tất sau mốc 120 giây, deadline thực, retry thành công dùng cùng audio upload, lỗi API không retry, chia đôi có giới hạn và đúng offset. Không có audio gốc của KH để tái hiện trên cloud thật; không bảo đảm loại bỏ mọi timeout của dịch vụ. Chưa build installer.
