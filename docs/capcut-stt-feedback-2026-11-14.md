# CapCut STT timeout — feedback 14/11/2026

## Runtime log bổ sung cùng ngày

`temp/capcap_runtime.log` cho thấy một lỗi khác: audio 1.651,22 giây chia ba đoạn 600/600/455,22 giây. `chunk_0001` và `chunk_0002` đã nhận lần lượt 269 và 190 utterance; `chunk_0000` lỗi HTTP 504 Gateway Time-out ở **upload transfer**, trước khi submit nhận dạng. Retry cũ chỉ bao quanh submit/query nên upload không được retry.

Đã bổ sung upload retry một lần sau 2 giây cho timeout, connection error và HTTP 408/429/500/502/503/504. Retry upload tạo upload mới từ file MP3 đã tách, không tách lại và không chạy lại các đoạn khác. Submit/query cũng dùng cùng phân loại HTTP tạm thời, dùng audio đã upload. HTTP 400/401/403 và lỗi API không retry. Upload thất bại lần hai báo lỗi, không chia đôi vì chưa tới bước nhận dạng. Log ghi riêng `Uploading`, `Upload failed`, chunk, attempt và HTTP status.

15 test STT recovery/TTS pass, gồm hồi quy upload 504 thành công lần hai, lặp 504 thì dừng, upload 403 không retry, query 503 dùng lại upload và mô phỏng ba đoạn xác nhận chỉ upload lại đoạn đầu. Chưa gọi cloud thật hoặc build installer. Thay đổi `.gitignore` hiện có của người dùng được giữ nguyên.

Log khách hàng `feedback/capcap_logs_20261113_031838.txt`: audio dài 313,93 giây, chia 2 đoạn theo cấu hình 300 giây. Kết nối cloud lúc 03:15:58, nhận kết quả 1/2 đoạn lúc 03:16:06, bước prepare thất bại lúc 03:18:02 với `CapCut STT query timed out after 120.0 seconds`.

Lỗi phát sinh trong vòng poll kết quả STT sau upload/submit; không phải lỗi tách âm thanh, GPU hoặc dịch phụ đề. Không đủ dữ liệu cũ để xác định đoạn dài hay đoạn đuôi timeout: log chỉ đếm số đoạn hoàn thành, không ghi index hoặc job ID. Có thể job cloud chậm/kẹt hoặc trả kết quả rỗng; không có bằng chứng chia đoạn quá nhỏ hay bị rate limit. Bản hiện tại trong source mặc định 600 giây nhưng thiết lập 300 giây trong log có thể do cấu hình/bản cài cũ.

Thay đổi mã nguồn:

- Thời gian chờ theo độ dài: `min(600, max(120, duration * 1.5))` giây; đoạn 300 giây được chờ tối đa 450 giây. Poll cùng job, không liên tục submit lại.
- Sau timeout hoặc lỗi mạng tạm thời trong submit/query, chờ 2 giây rồi gửi lại job nhận dạng đúng đoạn đó **một lần**, dùng audio đã upload, không tách/upload lại. Đây là job mới; job cũ có thể vẫn tiếp tục chạy phía cloud. Nếu retry vẫn timeout thì mới đi tới phục hồi chia đôi. Các phần nhỏ cũng tối đa hai lần nhận dạng nhưng không chia tiếp. Lỗi kết nối thất bại lần hai báo lỗi; lỗi xác thực/API không retry.
- Nếu job timeout, chỉ đoạn lỗi dài hơn 30 giây được chia đôi một lần, chồng lấn hai giây quanh điểm cắt. Các đoạn đã xong được giữ trong lượt xử lý; không chạy lại toàn bộ file. Timestamps của các phần được dịch về đúng vị trí gốc và merge overlap. Nếu phục hồi tiếp tục timeout, tác vụ vẫn báo lỗi; không trả transcript thiếu như thành công.
- Lỗi xác thực/API khác không được coi là job chậm để chia nhỏ.
- Log gồm index đoạn, vị trí/thời lượng, job ID, số lần poll, thời gian đã chờ, mã ret, tên trường trong response và số utterance. Không ghi token, upload URL hoặc nguyên transcript.

Kiểm tra mô phỏng: 7 test STT recovery và 3 test CapCut TTS pass (thêm ba trường hợp con timeout/network), gồm job hoàn tất sau mốc 120 giây, deadline thực, retry thành công dùng cùng audio upload, lỗi API không retry, chia đôi có giới hạn và đúng offset. Không có audio gốc của KH để tái hiện trên cloud thật; không bảo đảm loại bỏ mọi timeout của dịch vụ. Chưa build installer.
