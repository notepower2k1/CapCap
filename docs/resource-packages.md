# Xuất tài nguyên và nhập gói CapCap

Trong **Thêm → Xuất tài nguyên**:

- **Xuất sang CapCut…** tạo draft CapCut như trước.
- **Xuất tài nguyên…** mở danh sách chia theo nhóm. Tích tất cả, bỏ tích
  tất cả, tích nguyên nhóm hoặc chọn từng file. Nút Xuất chỉ bật khi đã chọn.
  Danh sách hiển thị số file và dung lượng dự kiến; mục chưa có dữ liệu bị vô hiệu.

Các nhóm tài nguyên:

| Nhóm | File có thể xuất |
| --- | --- |
| Transcript gốc | Toàn bộ SRT, TXT, JSON từng câu và speaker |
| Phụ đề đã dịch | Toàn bộ SRT, TXT, JSON từng câu và speaker |
| Giọng đọc từng câu | File TTS đã xử lý của từng cue; tên theo số thứ tự |
| Âm thanh và video đã xuất | Track giọng đọc, bản mix, giọng/nhạc nền đã tách, audio gốc, video render đã có |
| Video và media | Video gốc, logo và các file được layer tham chiếu |
| Dữ liệu chỉnh sửa | Timeline/layer/clip, kiểu phụ đề, Fit Voice, lật gương, ẩn/hiện, tỉ lệ khung, Fit/Fill, điểm căn khung, độ phân giải và FPS |

Đây là xuất tài nguyên đã có, không tự chạy dịch, TTS hoặc render lại. Video
gốc và video render không được tích sẵn vì thường có dung lượng lớn. Popup
ghi rõ: phải chọn video gốc nếu muốn nhập lại package vào CapCap. Nhóm có
nhiều file TTS được thu gọn để các nhóm khác vẫn dễ tìm.

Chọn thư mục đích; mỗi lần tạo thư mục `CapCap_resources_<video>_<mã>` riêng,
không ghi đè. File bên trong dùng được trực tiếp trong phần mềm khác. Nếu có
TTS từng câu, `audio/tts/index.csv` ghi số cue, thời điểm, nội dung và tên file.
`capcap_package.json` lưu ID gói (UUID), danh sách đã chọn, phiên bản và SHA-256 từng file.
Giữ toàn bộ thư mục này nếu muốn nhập lại. Không đóng gói API key, tài khoản,
model, cache preview hoặc cài đặt provider.

Ngay trên **launcher → Nhập dự án** (không cần mở video trước):

- **Nhập gói project vào CapCut…** dành cho draft đã xuất sang CapCut.
- **Nhập gói CapCap…** dành cho thư mục tài nguyên do chức năng trên tạo ra.
  Có thể chọn lại nhóm/file cần nhập. Gói phải chứa video gốc; thiếu video sẽ
  báo lỗi và yêu cầu xuất lại kèm video, không cho chọn video bên ngoài.
  Video gốc trong danh sách nhập luôn được giữ chọn.

Sau khi nhập gói CapCap, launcher thêm video vào dự án gần đây và mở bằng
luồng tải project bình thường. Gói CapCut được đưa sang CapCut Desktop.
Chức năng nhập nằm trên launcher, không hiển thị trong editor.

Gói CapCap chưa từng nhập tạo project mới, không ghi đè project đang mở. Video và
các tài nguyên được chép vào `imported_packages/<mã>` của workspace; project
chuẩn được tạo dưới `projects/` với ID tính từ video đã chép. Vì vậy mở lại
video nhập sẽ tìm đúng project đó. Đường dẫn timeline, artifact và file TTS
được liên kết lại, không dùng đường dẫn từ máy cũ. Transcript nhập có cờ dùng
lại transcript, không tự chạy nhận diện lại.

Project có `project_uuid` ngay khi tạo, tách biệt tên thư mục/path-based
`project_id`. UUID này được giữ qua mọi lần xuất/nhập và ghi trong manifest,
kể cả khi không chọn phần dữ liệu chỉnh sửa. Project cũ được cấp mã ổn định
khi đọc và lưu mã ở lần mở/lưu tiếp theo; không đổi tên thư mục hay đường dẫn.

ID gói cũng được lưu trong project đã nhập. Nhập lại cùng ID gói hoặc cùng
UUID project sẽ thông báo và cho chọn **Mở dự án có sẵn / Hủy**, không sao chép
thêm video/audio. Điều này nhận ra cả project gốc trên máy xuất, dù chưa từng
nhập gói nào. Đổi tên hoặc di chuyển thư mục gói không làm đổi nhận diện.
Mỗi lần xuất mới tạo ID gói mới nhưng giữ UUID project; vì vậy vẫn nhận ra bản
đã có nếu gói vừa được xuất lại. Không tự ghi đè hoặc gộp bản mới vào project
đang có; popup ghi rõ điều này. Nếu project cũ hoặc video
nhập đã bị xóa, có thể nhập lại để khôi phục. Có khóa liên tiến trình để tránh
hai cửa sổ ứng dụng đồng thời nhập trùng.

Gói cũ thiếu ID được tính mã nhận diện từ danh sách file/checksum, không sửa
nội dung gói. Mã này được lưu khi nhập bằng phiên bản có kiểm tra trùng; các
project đã nhập từ trước khi có cơ chế này chưa có ID để đối chiếu.

Chỉ các tài nguyên đã chọn mới được khôi phục. Layer media thiếu file được bỏ
và thông báo. TTS từng câu giữ liên kết với cue khi có dữ liệu phụ đề; chọn
thêm track giọng đọc tổng để nghe ngay mà không phải dựng lại track. Để mang
project đầy đủ sang máy khác, chọn tất cả, gồm video gốc và dữ liệu chỉnh sửa.
Font hệ thống và model cần sẵn có trên máy đích.

Sao chép/xác minh chạy trong worker; Hủy đợi worker dọn thư mục tạm. Gói sai
định dạng, thiếu file, checksum sai hoặc đường dẫn ra ngoài thư mục gói đều bị
từ chối. Các tác vụ thay đổi dữ liệu project phải hoàn tất trước khi xuất/nhập.
Trên Windows, lỗi khóa file tạm thời được thử lại tối đa 4 giây cho mỗi thao
tác sao chép/đổi tên. Dọn thư mục tạm có ngân sách thử lại riêng 4 giây; nếu
vẫn không dọn được thì ghi đường dẫn và giữ nguyên lỗi gốc trong log, không
che lỗi hoàn tất gói bằng lỗi xóa một file WAV. Worker ghi đầy đủ traceback.

Triển khai: `app/services/resource_package.py`,
`ui/controllers/resource_export_controller.py`. Kiểm tra tại
`tests/test_resource_package.py` và bài GUI của `test_capcut_draft_export.py`.
