# Bố cục video

## Bố cục cá nhân

Sau khi chỉnh preview, nhấn **Lưu bố cục hiện tại** trong tab **Bố cục video** và đặt tên. Có thể lưu nhiều mẫu, ghi đè mẫu cùng tên hoặc xóa mẫu. Thư viện dùng chung giữa các project và vẫn còn khi mở lại app.

Mẫu lưu tỉ lệ canvas, Fit/Fill, vị trí khung video, các layer chữ/logo/nền/blur/mask và kiểu của cả hai phụ đề. Nội dung phụ đề, bản dịch, giọng đọc và âm thanh lấy từ project đang mở. Khi áp dụng sang video dài/ngắn hơn, khoảng xuất hiện và keyframe của layer được co theo thời lượng; vị trí logo/chữ được quy đổi theo canvas mới.

Logo và nền ảnh trong mẫu được thay bằng ảnh mẫu đi kèm app, không lưu đường dẫn ảnh cá nhân. Sau khi áp dụng, chọn logo trên timeline rồi nhấn **Đổi ảnh** trong inspector; giữ nguyên vị trí, kích thước, thời gian và hỗ trợ Undo/Redo. Với nền ảnh, chọn layer nền và chọn ảnh trong inspector nền.

Chọn một mẫu đã lưu chỉ nạp bản nháp. Nhấn **Áp dụng bố cục** để thay các layer chữ/logo/nền/blur/mask hiện tại; video, phụ đề và âm thanh được giữ lại. Undo/Redo khôi phục cả bố cục, canvas và kiểu phụ đề trước/sau khi áp dụng. Nếu có layer hình ảnh/chữ bị khóa, hãy mở khóa trước khi áp dụng. Các mẫu dựng sẵn vẫn giữ cơ chế bảo toàn layer tự thêm như trước.

## Mẫu dựng sẵn

Mở tab **Bố cục video / Video Layout** ở bên trái editor. Chọn tỷ lệ canvas ở Media như trước; bố cục dùng được với video ngang, dọc và kích thước tùy chỉnh.

Bạn cũng có thể thêm nền độc lập bằng nút **Nền** dưới video preview, nút **BG** trên thanh layer dọc hoặc **+ Layer → Nền**. Không cần chọn mẫu bố cục và có thể thêm ngay sau khi chọn video, trước STT/dịch. Chọn layer nền để mở inspector riêng, chọn màu/ảnh/nền mờ rồi nhấn **Áp dụng**; hỗ trợ ẩn/hiện, xóa và Undo/Redo. Nền nằm dưới video và sẽ bị che nếu video phủ kín canvas. Khi có nhiều nền, nền thêm sau nằm trên các nền trước; ẩn nó để thấy nền bên dưới.

1. Chọn **Hook + Phụ đề**, **Tiêu đề + Part** hoặc **Khung điện ảnh**.
2. Nhập nội dung chữ, chọn nền màu/ảnh/nền mờ, chỉnh lề, chiều cao và vị trí vùng video, bo góc và đổ bóng. Fit/Fill vẫn quyết định cách video nằm trong vùng này.
3. Nhấn **Áp dụng bố cục**. Trước bước này, thay đổi chỉ nằm trong bản nháp.
4. Chỉnh các lớp chữ được tạo trên track Text bằng inspector hiện có. TS1/TS2 vẫn là phụ đề chính/phụ; logo và các layer tự thêm được giữ lại.

Áp dụng lại cập nhật các layer thuộc mẫu, không nhân bản chúng. Các ô nội dung để trống sẽ không tạo layer tương ứng. Mẫu Tiêu đề + Part tạo tiêu đề phía trên, số part và nội dung part phía dưới. Chức năng này không tự chia video thành nhiều part.

**Gỡ bố cục** hoặc áp dụng **Tự do** để bỏ các layer do mẫu tạo và trả lại kích thước/vị trí phụ đề trước khi áp dụng. Kiểu chữ, màu và các layer tự thêm được giữ lại. Hãy mở khóa layer của mẫu trước khi áp dụng/gỡ. Undo/Redo dùng chung lịch sử timeline.

Track **Nền** là nền toàn video; chọn layer để mở inspector nền. Thời gian của nền cố định theo project. Blur/Mask tiếp tục bám nội dung video; chữ, logo và phụ đề có thể nằm trên toàn canvas. Không tạo thêm luồng âm thanh, STT hoặc TTS. Gỡ mẫu bố cục giữ lại các nền bạn thêm thủ công.

Bố cục đã áp dụng được lưu theo project và đưa vào cấu hình hàng đợi. Fast Preview và export dùng chung cách dựng FFmpeg. Preview tĩnh trong Performance dùng bản gần đúng nhẹ cho độ mờ/đổ bóng; dùng Fast Preview để kiểm tra kết quả render.

Khi xuất dự án CapCut, nền, bo góc và đổ bóng được dựng vào video nền. Chữ, phụ đề, logo và âm thanh vẫn là các track riêng để chỉnh tiếp. Bước dựng video nền làm tăng thời gian xuất dự án CapCut. Khi chia sẻ project bằng gói tài nguyên, chọn cả dữ liệu editor, timeline và ảnh nền.
