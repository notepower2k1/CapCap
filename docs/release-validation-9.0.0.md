# Kiểm tra Version 9.0.0

Ngày cập nhật: 10/11/2026. Đây là ghi nhận kiểm tra mã nguồn; thay đổi dịch song song chưa được build vào installer. Release note không đồng nghĩa bộ cài đã được phát hành.

Cập nhật 11/11: installer từ `be93234` đã build thành công. Bản mã nguồn sau đó bổ sung giữ cửa sổ giữa các batch song song và xác nhận chat mới trước mỗi batch; thay đổi mới này chưa build lại installer. Kiểm tra mô phỏng: 53 test extension, 41 test bridge/batch và 16 test fallback/diagnostics pass. Có kiểm tra dùng lại bốn cửa sổ qua ba đợt batch trên cả hai nền tảng/chế độ hiển thị, không gửi khi lịch sử cũ chưa được xóa, dọn các cửa sổ của luồng và bỏ phản hồi muộn. Chưa chạy benchmark thực tế cho cách giữ cửa sổ.

Kiểm tra bổ sung 10/11 cho dịch song song GPT/Gemini Web:

- 44 test extension pass: gồm các phiên độc lập trên cả hai nền tảng/chế độ hiển thị, giới hạn bốn tác vụ và chờ slot cho batch thứ năm, gắn tiến độ đúng tab, hủy từng tác vụ, bỏ phản hồi muộn, đóng cửa sổ cũ khi cần tạo lại tab, nhận diện nút hoàn tất bên ngoài DIL và chẩn đoán chỉ các tab thuộc tác vụ.
- 83 test Python pass trong các bộ bridge, batch song song, cài đặt song song, partial fallback, web translators, translation diagnostics, đóng gói extension và i18n. Có kiểm tra phản hồi lệch thứ tự, giữ batch AI thành công khi fallback, hủy các batch đang chờ, đặt lại tiến độ và lưu/khôi phục tùy chọn trong dialog Qt thực.
- Đã bổ sung [thử trực tiếp GPT/Gemini ở mức 2 và 4 batch](browser-parallel-live-test-2026-11-10.md). Cả hai nhận đủ câu trong các lượt hoàn thành; Gemini cần cửa sổ hiện rõ. Sau lượt đo, đã mở mức 1–4 trong ứng dụng/extension. Kiểm tra Qt xác nhận cả mức 2, 3, 4 được lưu/khôi phục; kiểm tra orchestrator xác nhận số worker tương ứng và giá trị ngoài khoảng trở về 1. Chưa build installer cho các sửa đổi này.

Kiểm tra bổ sung OCR ngày 10/11: 27 test trong các bộ đọc video OCR, OCR enhancements, range transcription và transcript import pass. [Benchmark toàn bộ video mẫu](ocr-benchmark-2026-11-10.md) giảm thời gian từ 180,9 xuống 90,0 giây; chuỗi 3.303 ảnh crop, 824 đoạn phụ đề và SRT không đổi. Chưa build lại installer cho thay đổi này.

Ghi nhận các lượt kiểm tra trước:

- Bộ test extension: 21 test pass, gồm DOM ChatGPT mới, gửi prompt, Temporary Chat Gemini và dùng chung cửa sổ phân tích/dịch.
- Các bộ test Python cho bridge, đóng gói extension, xưng hô, OmniVoice, ngôn ngữ, i18n, lưu cấu hình, fallback dịch, chẩn đoán dịch, voice worker, Media Tools, timeline, xuất video và gói project đều pass. Test riêng cập nhật tiến độ tạo giọng cũng pass.
- Hai bộ hồi quy giao diện đầy đủ (`test_runtime_bugfixes`, `test_performance_queue`) bị treo trong lượt kiểm tra trước khi merge và đã được dừng; chưa xác nhận toàn bộ hai bộ test. Log queue có callback Qt truy cập widget đã bị hủy.
- Sau khi sửa nhãn launcher V9, 11 test i18n pass.
- Chưa build lại installer 9.0.0 và chưa chạy lại toàn bộ luồng trình duyệt thực tế sau lần cập nhật version.
- Browser bridge vẫn là công cụ cá nhân trên localhost, chưa thêm token xác thực theo phạm vi đã thống nhất.

Nội dung [release note](../release.md) đối chiếu từ commit cập nhật bản trước `74e0703` (V8.0.6.1) đến Version 9.0.0, gồm `eb0d824`, `4c1103a`, `56c9f17`, `60429db`, `8093228`, `4966f12`, `e2e43a3`, `e722fb9`, `f243158`, `95aa4d9`, `9921813`, `a0d222f` và `11c4052`. Các mô tả xuất/nhập project đối chiếu thêm với `docs/resource-packages.md`, `docs/capcut-draft-export.md`; xử lý khoảng lặng TTS theo `docs/tts-boundary-cleanup.md`.
