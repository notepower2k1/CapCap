# Tỉ lệ và kích thước video

Tùy chọn trong phần canvas:

- **Tự chỉnh layer** (mặc định bật): tính lại bố cục khi đổi tỉ lệ/Quality/Fit/Fill. Tắt để giữ nguyên thông số layer; không áp dụng bố cục đã lưu hoặc tự đổi cỡ chữ.

Tự chỉnh layer được lưu trong cấu hình project/hàng đợi/gói project. Chữ, logo và phụ đề luôn dùng toàn bộ canvas, bao gồm viền đen do Fit tạo ra. Tùy chọn Giới hạn trong video đã bỏ; giá trị cũ trong project được bỏ qua. Blur/mask luôn bám nội dung video. Layer bị khóa giữ nguyên thông số.

Tool xác định phần đệm của canvas để chọn bộ render phụ đề có thể hiển thị lên viền đen. Việc chọn này không thay đổi thông số layer và không phân tích vùng đen có sẵn trong file nguồn.

Khi cần hiển thị phụ đề trên viền đen, preview Qt nhận animation và tiến độ riêng của từng track/câu. Các lần cập nhật style dùng vị trí phát hiện tại, không khởi động lại hiệu ứng từ mốc 0. Khi khung không có phần đệm, Quality có thể dùng ASS. Animation được giữ trong file ASS xuất ra.

Trong phần xuất video, **Ratio** chọn tỉ lệ canvas. **Source** giữ đúng tỉ lệ gốc, kể cả khi giảm Quality; video 4:3 hoặc vuông không bị chuyển thành 16:9. Quality quy định cạnh ngắn tối đa và không tự phóng lớn video nhỏ khi giữ tỉ lệ Source.

Chọn **Khung hình tùy chỉnh** để nhập:

- Tỉ lệ như `3:2`, `2:3`, `21:9`: kích thước được tính cùng mức Quality. Với Quality Source, canvas nằm trong kích thước gốc.
- Kích thước pixel như `1080x1920`: dùng đúng kích thước đã nhập, ưu tiên hơn Quality và cho phép phóng lớn. Chiều rộng/cao phải là số chẵn từ 2 đến 8192 pixel để tương thích mã hóa video.

**Fit** giữ toàn bộ hình và thêm khoảng trống nếu khác tỉ lệ. **Fill** phủ kín canvas và cắt phần vượt ra ngoài. Tỉ lệ, kích thước và chế độ canvas đi cùng cấu hình project/hàng đợi, được khôi phục khi mở lại; gói project cũng giữ cấu hình này.

Preview thường, MPV, Performance và export dùng chung cách tính tỉ lệ/kích thước. Khi đổi Quality, canvas và các lớp phủ được cập nhật lại. Phần này chưa có zoom/crop tự do.

Fast Video Preview dựng ASS mới theo canvas đầu ra và style hiện tại, rồi dịch mốc thời gian về clip 5 giây. Không tái sử dụng ASS theo khung nguồn của MPV vì sẽ sai cỡ chữ sau Fit/Fill. Text được chuyển từ tọa độ nguồn sang canvas bằng cùng hàm với export trước khi tạo ảnh overlay.

Khi bật Tự chỉnh layer và đổi tỉ lệ hoặc Fit/Fill, các layer chữ và ảnh không bị khóa được bố trí lại theo vị trí tương đối trong canvas. Logo co đều hai chiều; chữ được tính lại cỡ, đo khung nội dung và thu nhỏ thêm nếu vượt canvas. Cỡ phụ đề chính/phụ, padding, bán kính nền và khoảng cách tính bằng pixel được đổi theo hệ số đồng đều của canvas; mặc định kiểu chữ đã lưu không bị ghi đè. Tọa độ và kích thước mới được lưu cùng project để export dùng đúng bố cục. Blur/mask tiếp tục co giãn và dịch chuyển cùng vùng video gốc để giữ đúng đối tượng cần che; layer khóa giữ thông số đã khóa.

Bố cục của từng kích thước canvas và Fit/Fill được lưu riêng trong dữ liệu layer/project. Khi quay về khung đã dùng, khôi phục chính xác bố cục đó thay vì nhân tiếp từ kích thước đã thu nhỏ/làm tròn. Các chỉnh tay về vị trí/kích cỡ được giữ cho khung đang chỉnh; nội dung chữ và màu không bị thay thế khi khôi phục bố cục.

Kích thước bố cục đang dùng được ghi nhận riêng, tránh bị kích thước render phụ đề tạm thời ghi đè. Bố cục theo toàn canvas dùng khóa phiên bản riêng; dữ liệu từ cách tính bám vùng hình cũ không được tự áp lại chỉ vì cùng độ phân giải. Khung chưa có bố cục theo cách tính mới sẽ được tính lại; các chỉnh sửa mới được lưu và khôi phục chính xác khi đổi qua lại. Khi đang chọn chỉnh phụ đề, preview cho thấy cả phần vượt mép khung trong vùng biên của trình xem; playback và export vẫn cắt đúng canvas đầu ra.

Text và logo được ánh xạ từ tọa độ nguồn sang canvas khi vẽ và đổi ngược khi kéo thả, cho phép tọa độ vượt vùng hình thực. Phụ đề chính/phụ căn và kéo trên canvas; export vẽ ASS sau bước Fit/Fill để phụ đề trên viền đen vẫn xuất hiện đúng vị trí.

Ô cỡ phụ đề cho nhập phần trăm tự do thay cho năm mức cố định. 100% tương ứng 60 px; cỡ chữ lưu bằng pixel nguyên và phần trăm hiển thị theo đúng giá trị đã lưu (ví dụ 18 px = 30%, 61 px ≈ 101,67%). Khi đổi khung hoặc chuyển style chính/phụ, ô được đồng bộ lại mà không ép về mức phần trăm gần nhất.

Inspector Text dùng chung ô nhập cỡ chữ phần trăm với phụ đề, hỗ trợ gõ font tùy ý và hiển thị đúng giá trị khi chọn layer hoặc tự chỉnh khung. Kích thước và font lưu riêng cho từng text layer, không liên kết với style phụ đề. Giá trị phần trăm được làm tròn về kích thước pixel nguyên thực tế; ví dụ nhập 37,3% sẽ thành 22 px và hiển thị 36,67%.
