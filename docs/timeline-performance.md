# Rà soát cập nhật timeline cho project lớn

## Các lượt cập nhật thừa đã xử lý

- Một lần `apply_segments_to_timeline` trước đây dựng lại bố cục riêng cho phụ đề chính, TS2, time warp và TTS. Các bước đồng bộ model được gom trong `EditorTimeline.batch_update`; bố cục được tính sau khi dữ liệu đã sẵn sàng. Nested batch và trường hợp phát sinh exception vẫn giải phóng bước cập nhật cuối.
- Bố cục hàng chồng nhau từng được tính lúc đo chiều cao track rồi tính lại ở lần paint đầu tiên. Hai bước dùng chung cache gồm hàng, danh sách câu theo thời gian và chỉ mục đoạn nằm trong viewport. TTS có `_audio_end` tràn sang câu kế vẫn buộc làm mới hàng khi dữ liệu thay đổi.
- Sửa chữ của một câu TS1 không còn chạy toàn bộ `apply_segments_to_timeline`, sync TTS và yêu cầu worker waveform/thumbnail. Layer hiện tại được cập nhật chữ, nội dung đọc và trạng thái cần tạo lại TTS; identity, thời gian và cache bố cục được giữ. Nếu thời gian đã khác hoặc đang bật tách dòng đơn, luồng đầy đủ vẫn chạy.
- TS2 giữ nguyên layer khi số câu và thời gian không đổi. Sửa chữ chỉ repaint; đổi prompt không dựng lại track. Khi thời gian/số câu/khả năng hiển thị thay đổi, chỉ cache TS2 cần cập nhật.
- Time warp giống dữ liệu đã nhận không làm mất cache. Khi warp đổi, waveform/thumbnail được đánh dấu làm mới; hình học câu được đồng bộ qua `set_segments`, không tính lại chỉ vì nhận lại danh sách warp.
- Đổi thời gian của Blur/Logo/Mask/Text trong inspector chỉ làm mới bố cục track đó. Khóa layer/track, bật/tắt preview chữ và đổi tên chữ chỉ cập nhật phần hiển thị, không bỏ cache hàng TS1/TS2.
- Paint bỏ qua track nằm ngoài vùng cuộn dọc. Track phụ đề đã có type rõ ràng không còn bị quét toàn bộ câu để kiểm tra xem có phải track Blur/Mask/Text hay không.
- Cùng một cue đang phát không bị quét lại toàn bộ danh sách index ở mỗi tick. Cờ chọn thủ công và thay đổi model vẫn làm mới kết quả. Playhead không repaint khi tọa độ pixel không thay đổi, kể cả khi dừng; tín hiệu thời gian vẫn được phát cho các phần UI khác.

## Đo trên mẫu lớn

Benchmark tại máy phát triển dùng 3.000 câu mỗi track TS1/TS2, có đường TTS; đo ba lần đồng bộ model và vẽ các track phụ đề, có profiler. Không bao gồm giải mã video, ASR, gọi AI hay render toàn bộ editor.

| Chỉ số | Trước | Sau |
| --- | ---: | ---: |
| Trung bình mỗi lượt đồng bộ + paint | 367,6 ms | 126,6 ms |
| Số lần tính bố cục trong ba lượt | 12 | 3 |
| Số lần tính hàng chồng nhau trong ba lượt | 30 | 6 |

Script và kết quả kiểm tra tại máy phát triển nằm trong `output/probe_timeline_rebuild.py`, `output/timeline-rebuild-before.txt`, `output/timeline-rebuild-after.txt` (không đưa vào bản phát hành).

## Các bước vẫn cần làm mới

Nhập/thay toàn bộ phụ đề, tách/xóa/thêm câu, thay đổi thời gian, Auto Fit và cập nhật độ dài audio TTS vẫn có thể thay đổi hàng hoặc kích thước timeline. Những thay đổi này tiếp tục tính lại bố cục. Cache không được dùng để bỏ qua cập nhật hình học thực sự.

Ghi project/SRT khi thực sự sửa dữ liệu và tạo ASS khi đổi nội dung/kiểu vẫn là chi phí ngoài phần bố cục timeline. Các số đo trên không đại diện cho tổng thời gian của những bước đó.

## Kiểm chứng

- Bộ hồi quy rộng timeline, phụ đề, Auto Fit, preview, CapCut, hàng đợi, Audio Mix và i18n: 142 tests + 17 subtests pass.
- Lượt kiểm tra bổ sung sau khi thu hẹp cập nhật theo track: kiểm tra cache của track không bị sửa, cập nhật chữ một layer, đánh dấu TTS cũ, exception trong batch, TTS tràn, viewport dọc và playhead/cue không đổi.
- Preview/overlay Windows chạy trong tiến trình riêng để không trộn với Qt offscreen.

Chưa commit hoặc build cho thay đổi này.
