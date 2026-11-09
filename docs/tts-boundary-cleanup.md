# Cắt khoảng lặng thừa ở hai đầu giọng TTS

Khi tạo giọng toàn bộ hoặc tạo lại từng câu, CapCap kiểm tra audio trước khi
tính tốc độ và Fit Voice. Chỉ cắt phần đầu/cuối im lặng ít nhất 350 ms, giữ
120 ms đệm cạnh vùng có âm. Khoảng nghỉ bên trong câu không bị thay đổi.

Bộ lọc dùng đỉnh tín hiệu từng kênh với ngưỡng thấp -60 dBFS, tránh xem âm nhỏ
hoặc hai kênh ngược pha là khoảng lặng. File rất nhỏ tiếng, chỉ có một tín hiệu
quá ngắn, không đọc được hoặc không chắc chắn sẽ giữ nguyên. Đây là xử lý
bảo thủ, không phải bộ nhận diện lời nói và không bảo đảm mọi câu sẽ ngắn hơn.

File TTS gốc trong cache được giữ nguyên. File xử lý riêng được dùng để ghép
track giọng, cập nhật `_audio_end`, playback và export. Nếu câu hết bị tràn,
thời điểm kết thúc audio cũ cũng được cập nhật. Không tự sửa project chỉ vì
mở project đó; tính năng áp dụng ở lần tạo/tạo lại giọng tiếp theo.

Phiên bản xử lý nằm trong chữ ký track giọng để không dùng nhầm track cũ.
Cache tổng hợp giọng từ provider không đổi; audio có cache hợp lệ được tái
sử dụng để xử lý cục bộ. Kiểm tra này không gọi AI, không yêu cầu tải model.

Smart mode dùng cùng bộ lọc bảo thủ; không chạy thêm bộ cắt đuôi -40 dB cũ.
Chạy kiểm tra bằng `test_tts_boundary_cleanup.py`.
