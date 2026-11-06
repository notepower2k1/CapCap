# Quality, Performance và hàng đợi video

Launcher có hai chế độ, độc lập với lựa chọn CPU/GPU và chất lượng xuất video:

- **Quality — Biên tập đầy đủ:** mở project như trước với timeline, playback và layer inspector.
- **Performance — Hàng đợi video:** thêm nhiều video hoặc một thư mục, thiết lập từng video bằng Main UI gọn, rồi xử lý tuần tự.

## Sử dụng

1. Chọn **Performance — Hàng đợi video** trên launcher, bấm **Thêm video** hoặc **Thêm thư mục**.
2. Chọn một mục rồi bấm **Thiết lập** hoặc nhấp đúp. Main UI mở với preview một frame tĩnh, bảng phụ đề gọn và các thiết lập xử lý hiện có.
3. Có thể chỉnh ngôn ngữ, STT, dịch, giọng TTS, speaker, kiểu phụ đề và xuất video riêng cho mục đó. Đổi frame bằng thời điểm và nút **Đổi khung hình**. Preview thể hiện chữ tĩnh, không phát animation.
4. Chọn **Thêm → Quay về Launcher**. Thiết lập được lưu trong project tương ứng; các video khác không bị đổi theo. Cần thiết lập từng video đang chờ trước khi chạy.
5. Bấm **Chạy xử lý**. App lần lượt chạy chuẩn bị/chép lời/dịch và TTS khi cần, rồi lưu project ở trạng thái **Đã xử lý – Chưa xuất**. Mặc định chưa render MP4. Bật checkbox **Tự động xuất video sau khi xử lý** nếu muốn xử lý và xuất luôn từng video. Lựa chọn này được nhớ sau khi mở lại app.
6. Khi cần render, chọn một hoặc nhiều video đã xử lý (Ctrl/Shift để chọn nhiều) rồi bấm **Xuất video đã chọn**. App chỉ mix âm thanh/xuất video từ dữ liệu đã lưu, không chạy lại chép lời/dịch/TTS và không xử lý các mục đang chờ khác. Video lỗi được ghi nguyên nhân và app tiếp tục mục tiếp theo.
7. Có thể bấm **Mở bằng Quality** để chuyển project sang Quality và chỉnh tiếp, dùng lại phụ đề, TTS từng câu, các layer và thời gian Fit Voice đã lưu. Mục đó được bỏ khỏi hàng đợi Performance; xuất video bằng nút Xuất trong Quality.

Một video chỉ thuộc một chế độ trên launcher. Video đã có project Quality (kể cả project cũ hoặc đã xuất xong) không được thêm vào Performance. Video trong hàng đợi hoặc có project Performance không được chọn trực tiếp ở Quality; dùng **Mở bằng Quality** để chuyển chế độ. Trạng thái này được lưu trong project và giữ sau khi tắt/mở lại app, không phụ thuộc cửa sổ editor có đang mở hay không.

**Xóa dữ liệu video** đồng bộ cả lịch sử Quality và hàng đợi Performance theo các project thực sự đã xóa. Project còn bị khóa được giữ trong danh sách kèm thông báo lỗi; sau khi xóa thành công có thể thêm lại video. File log trong `temp` được giữ để tránh xung đột với phiên ứng dụng và còn thông tin chẩn đoán lỗi.

Mỗi video vẫn là project CapCap chuẩn, có `project_uuid` riêng. Hàng đợi chỉ lưu đường dẫn, ID công việc, trạng thái và kết quả; không tạo một bản project khác. Các khóa API vẫn dùng thiết lập chung của app, không được chép vào cấu hình hàng đợi. Chọn provider/model và các tùy chọn xử lý được lưu riêng theo video.

## Chạy tuần tự và dừng

- Trong lúc xử lý, mọi đường mở thêm Main UI đều bị khóa. Launcher vẫn cho thêm video và sắp xếp/xóa các mục chưa chạy. Những thao tác có thể tạo tải khác hoặc xóa tài nguyên đang dùng cũng được khóa đến khi worker kết thúc.
- **Dừng sau video hiện tại:** hoàn tất video đó, giữ các mục còn lại ở trạng thái chờ.
- **Dừng ngay:** dừng cây tiến trình do app tạo, chờ worker kết thúc rồi mở khóa. Project và JSON artifacts được ghi qua file tạm rồi thay thế nguyên vẹn, giảm nguy cơ hỏng dữ liệu khi dừng.
- **Xử lý lại:** chuyển mục lỗi/gián đoạn/đã hoàn thành về chờ xử lý. App dùng cache và artifacts hợp lệ; bước chưa hoàn tất có thể cần chạy lại. Muốn thử lại riêng bước render khi dữ liệu đã xử lý còn đủ, dùng **Xuất video đã chọn**. Không tự động ghi đè MP4 đã xuất: lần chạy sau có tên kết quả riêng.
- Hàng đợi được lưu ở `processing_queue.json` trong thư mục làm việc. Nếu app bị tắt đột ngột, mục đang chạy được đánh dấu gián đoạn khi mở lại. Chọn **Xử lý lại** hoặc xuất lại từ dữ liệu đã xử lý tùy bước bị gián đoạn. File hàng đợi lỗi được giữ thành bản sao `processing_queue.invalid-*.json`.

## Giảm tải trong Main UI Performance

Main UI dùng cùng workflow và mô hình layer với Quality. Timeline chỉ giữ dữ liệu project; không tạo graphics scene, không vẽ timeline. Không khởi tạo MPV/decoder, không chạy worker waveform/thumbnail, không làm audio mix cho playback và không nạp trước model TTS. Bảng phụ đề dùng model/view, không tạo một bộ widget cho từng câu. Frame chỉ được lấy khi mở video hoặc người dùng yêu cầu đổi frame; kiểu chữ dùng lại painter của preview hiện có.

Performance có nút **Layer**, danh sách chọn layer và inspector để thêm/chỉnh/xóa Text, Logo, Blur, Mask và Audio bổ sung trên frame tĩnh. Chọn thời điểm rồi bấm **Đổi khung hình** để xem layer trong khoảng thời gian tương ứng. Có thể chỉnh thời điểm bắt đầu/kết thúc trong inspector mà không cần timeline. Blur/Mask trên frame chỉ là bản xem gần đúng để giữ nhẹ UI; file xuất vẫn dùng renderer đầy đủ. Muốn xem playback hoặc animation thì mở project bằng Quality. Thiết lập chỉnh tiếp ở Quality cũng được lưu lại trong cấu hình project để dùng cho lần chạy hàng đợi sau.

Dưới preview giữ các nút thêm **Blur, Logo, Mask, Text** và nút **OCR** khi chọn nguồn OCR. Nút OCR bật/tắt vùng kéo/chỉnh trên frame tĩnh; tọa độ được lưu riêng theo video và áp dụng cho worker hàng đợi. Không hiển thị **OCR Translation** trong Performance.

Trong Performance, các nút chạy toàn bộ quy trình, xuất video và nút con mắt xem trước được ẩn. Giữ menu **Thêm** trên header để vào **Cài đặt**, xuất tài nguyên và **Quay về Launcher**; khi quay về, cấu hình được tự lưu. Nút X lưu cấu hình và thoát ứng dụng, dừng worker lấy frame và kết thúc vòng chờ của launcher; không tự mở lại launcher.

Mỗi video trong hàng đợi chạy trong một tiến trình riêng để nhả CPU/GPU và model trước video kế tiếp. Đổi lại, model phải nạp lại giữa các video. Chưa có số đo mức giảm RAM/VRAM trên video dài thực tế; không giảm chất lượng STT, dịch, TTS hoặc chất lượng encode chỉ vì chọn Performance.

## Kiểm tra

`tests/test_performance_queue.py` kiểm tra hàng đợi lưu/khôi phục, khóa xử lý, lưu JSON nguyên vẹn, bảng phụ đề nhiều câu, cấu hình độc lập, chuyển sang Quality giữ project/layer, xuất video tuần tự, tiếp tục sau lỗi và hai cách dừng. Các bài kiểm tra tự động dùng video tổng hợp và transcript nhập sẵn, không gọi dịch vụ AI trả phí.

Đã thử xuất hai video liên tiếp, một video hỏng xen giữa, và luồng local OmniVoice CUDA → mix audio → xuất video. Chưa đóng gói installer cho thay đổi này.

Kiểm tra hồi quy: 209 bài và 23 subtest đạt. Một bài cũ `test_voice_workflow_manual_speed_bypasses_smart_and_force_fit` vẫn thất bại khi chạy riêng: kỳ vọng bước timing polish gọi lại đổi tốc độ thủ công. File workflow và bài kiểm tra này không thay đổi so với HEAD; bài này được loại riêng trong lần chạy xác nhận. Chưa kiểm thử hiệu năng trên video dài thực tế.
