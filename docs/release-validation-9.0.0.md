# Kiểm tra Version 9.0.0

Ngày cập nhật: 09/11/2026. Đây là ghi nhận kiểm tra mã nguồn; cần build lại ứng dụng/installer để tạo bộ cài 9.0.0. Release note không đồng nghĩa bộ cài đã được phát hành.

- Bộ test extension: 21 test pass, gồm DOM ChatGPT mới, gửi prompt, Temporary Chat Gemini và dùng chung cửa sổ phân tích/dịch.
- Các bộ test Python cho bridge, đóng gói extension, xưng hô, OmniVoice, ngôn ngữ, i18n, lưu cấu hình, fallback dịch, chẩn đoán dịch, voice worker, Media Tools, timeline, xuất video và gói project đều pass. Test riêng cập nhật tiến độ tạo giọng cũng pass.
- Hai bộ hồi quy giao diện đầy đủ (`test_runtime_bugfixes`, `test_performance_queue`) bị treo trong lượt kiểm tra trước khi merge và đã được dừng; chưa xác nhận toàn bộ hai bộ test. Log queue có callback Qt truy cập widget đã bị hủy.
- Sau khi sửa nhãn launcher V9, 11 test i18n pass.
- Chưa build lại installer 9.0.0 và chưa chạy lại toàn bộ luồng trình duyệt thực tế sau lần cập nhật version.
- Browser bridge vẫn là công cụ cá nhân trên localhost, chưa thêm token xác thực theo phạm vi đã thống nhất.

Nội dung [release note](../release.md) đối chiếu từ commit cập nhật bản trước `74e0703` (V8.0.6.1) đến Version 9.0.0, gồm `eb0d824`, `4c1103a`, `56c9f17`, `60429db`, `8093228`, `4966f12`, `e2e43a3`, `e722fb9`, `f243158`, `95aa4d9`, `9921813`, `a0d222f` và `11c4052`. Các mô tả xuất/nhập project đối chiếu thêm với `docs/resource-packages.md`, `docs/capcut-draft-export.md`; xử lý khoảng lặng TTS theo `docs/tts-boundary-cleanup.md`.
