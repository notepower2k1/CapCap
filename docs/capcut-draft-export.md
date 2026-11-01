# Xuất project CapCut Desktop — bản thử nghiệm

Vào **Thêm → Xuất tài nguyên → Xuất sang CapCut…**. Chọn thư mục draft của CapCut
(mặc định `%LOCALAPPDATA%/CapCut/User Data/Projects/com.lveditor.draft`) hoặc một
thư mục riêng để kiểm tra gói xuất. Mỗi lần xuất tạo một project mới, không ghi
đè project cũ.

Khi chọn thư mục draft, đóng CapCut trước khi xuất. CapCap thêm project vào
`root_meta_info.json`, giữ nguyên các mục cũ và lưu bản sao danh sách trước khi
thêm vào `capcap_index_before_<draft-id>.bak` cạnh thư mục project mới. Sau đó mở CapCut
để tìm project có tên `CapCap_<tên video>_<mã>`.

## Nội dung bản cơ bản

- Video gốc, âm thanh gốc, phụ đề dạng chữ sửa được, TTS và nhạc nền.
- Các cue TTS được cắt từ track giọng đã xử lý, giữ vị trí và thời lượng thực,
  không áp dụng tốc độ giọng lần thứ hai. Nếu còn file giọng đã xử lý riêng từng
  câu, ưu tiên dùng các file đó để dễ chỉnh từng câu mà không chứa giọng của câu
  bên cạnh. Project cũ thiếu các file này dùng track giọng tổng làm fallback.
  File giọng nhập ngoài giữ thành một track riêng. Nhạc nền lặp được chia thành clip nối tiếp.
- Fit Voice: đoạn chậm giữ tỷ lệ thời lượng nguồn/đích, đoạn giữ khung được
  xuất thành ảnh riêng; âm thanh gốc có khoảng im lặng tương ứng khi giữ khung.
  Nếu hai cue chồng một phần thời gian nguồn, phần nguồn chung chỉ phát một
  lần; toàn bộ thời gian kéo dài vẫn được giữ và điều chỉnh được ghi trong báo cáo.
- Layer chữ, ảnh/logo, sticker ảnh tĩnh, video và audio phụ. Giữ vị trí, tỉ lệ,
  góc quay, độ trong suốt và âm thanh của video phụ. Clip phụ giữ thời điểm
  đích, điểm cắt nguồn và tốc độ. Lật gương video được giữ lại.
- Block BLUR đang bật được chuyển thành bản sao video nguồn có mask chữ nhật
  và Blur native, hoặc Mosaic nếu bật pixelate. Giữ vùng theo tọa độ nguồn,
  thời điểm và thời lượng; block chồng nhau có track riêng. MASK hỗ trợ che
  màu, blur và pixelate. Lớp che màu dùng ảnh PNG đóng gói cùng project.
- Các lớp vùng chia theo đoạn chậm/giữ khung Fit Voice, dùng cùng căn khung
  Fit/Fill và lật gương với video chính. Chúng tắt tiếng và nằm dưới phụ đề,
  chữ, logo. File video nguồn dùng chung, không chép lại cho từng vùng.
  Có thể chỉnh mask và hiệu ứng trong CapCut; cường độ blur/pixelate chỉ gần
  tương đương. CapCut có thể cần tải tài nguyên mask/hiệu ứng ở lần mở đầu.
- Framing Fit/Fill và điểm căn khung được chuyển thành thuộc tính clip native
  trong CapCut, áp dụng giống nhau trên video thường và ảnh giữ khung Fit Voice.
  Vị trí phụ đề tùy chỉnh được giữ. Cỡ chữ và màu được chuyển cho cả phụ đề và
  layer chữ. Giữ font nếu tìm được trên máy, đậm/nghiêng/gạch chân, căn chữ,
  viền/nền/bóng chữ cơ bản. Các kích thước trang trí được quy đổi gần tương
  đương, nền full-area dùng nền text native thay vì khung ASS chính xác.
  Phụ đề lấy kiểu chữ hiện tại từ UI (gồm màu theo speaker), không lấy giá trị
  mặc định cũ trên layer. Animation chữ được bỏ qua.
- Fade âm thanh từng layer dùng fade native khi clip nguyên vẹn. Fade toàn
  track dùng keyframe âm lượng theo thời gian project, không khởi động lại ở
  mỗi cue TTS, clip Fit Voice hoặc đoạn nhạc lặp. Pan được giữ bằng WAV stereo
  đã xử lý, không có nút pan native để chỉnh lại; file âm thanh nguồn không bị sửa.
- Keyframe vị trí, scale, xoay, opacity và âm lượng được chuyển. Easing của
  CapCap được lấy mẫu thành nhiều keyframe tuyến tính, nên gần tương đương.
  Khi cắt, các keyframe được lấy theo cửa sổ nguồn và tốc độ của clip.
- Hỗ trợ Clip trên V1/A1 tham chiếu nguồn project. `Clip.source_start` là offset
  trên timeline layer nguồn đã có Fit Voice (gồm cả đoạn giữ khung/chậm),
  `duration` là thời lượng đích; `speed` tăng tốc đồng hồ đó. Các cửa sổ được
  chia tại biên warp, giữ gap và thời điểm đích. V1/A1 không chứa clip vẫn dùng
  toàn nguồn như trước. Media khác nguồn project dùng track phụ.
- Media được sao chép vào `media/`. Có thêm `subtitles.srt`,
  `capcap_export_report.json`, `capcap_layers.json`, `capcap_package.json` và `README.txt`.
- Xuất chạy nền; Hủy sẽ dọn thư mục đang chuẩn bị. Thiếu media báo lỗi thay vì
  tạo draft mất file. Không xuất settings/API key của CapCap.

## Giới hạn cần kiểm tra

Đây là draft thử nghiệm, đối chiếu cấu trúc Windows CapCut 9.2. Mỗi phiên bản
có thể thay đổi định dạng. Cần kiểm tra phát từ đầu đến cuối, framing, vị trí
layer, sửa một cue, lưu, đóng rồi mở lại trên bản CapCut đang sử dụng.

Màu/filter/LUT và animation preset không chuyển. Keyframe chỉ hỗ trợ các thuộc
tính nêu trên; dữ liệu không hợp lệ/thuộc tính lạ báo lỗi thay vì âm thầm bỏ mất.
Font hệ thống không được chép vào gói. Máy đích cần cài cùng font; nếu thiếu,
báo cáo ghi cảnh báo để chọn lại trong CapCut. Không bảo đảm chữ và trang trí
khớp từng pixel do hai engine render khác nhau.

## Chuyển project sang thư mục hoặc máy khác

1. Chép toàn bộ thư mục đã xuất, gồm `media/` và `capcap_package.json`.
2. Trên launcher CapCap, chọn **Nhập dự án → Nhập gói project vào CapCut…**.
3. Chọn thư mục gói, rồi thư mục draft CapCut trên máy đích. Đóng CapCut trước
   khi nhập thẳng vào thư mục draft của ứng dụng.
4. CapCap chép media, tạo project mới với ID riêng, cập nhật đường dẫn trong
   draft gốc và các bản timeline. Gói nguồn và project cũ được giữ nguyên.

Không chỉ kéo thư mục vào vị trí mới rồi mở trực tiếp: draft dùng đường dẫn
tuyệt đối nên cần bước nhập để liên kết lại. Gói từ bản xuất cũ thiếu manifest
cần được xuất lại một lần. Nếu đã thêm media hoặc nhiều timeline trong CapCut,
bộ nhập sẽ báo không hỗ trợ thay vì âm thầm bỏ mất nội dung. Không phải chức
năng nhập project CapCut tùy ý về timeline CapCap.

Cỡ chữ được quy đổi từ pixel canvas CapCap sang đơn vị tương đối theo chiều
rộng canvas CapCut, dùng hệ quy chiếu thử nghiệm 200 đơn vị và hệ số preview
0,85 có sẵn của CapCap. Chưa phải phép hiệu chuẩn pixel chính xác với font
của từng bản CapCut: cần đối chiếu một lần trong CapCut Desktop.
`capcap_layers.json` ghi cỡ chữ nguồn, cỡ đã quy đổi và màu để tiện kiểm tra.
Các giá trị này được giữ nguyên khi nhập lại gói ở máy khác.
Manifest ghi tên family của các font hệ thống. Bộ nhập tìm lại font ở máy
đích và cập nhật cả đường dẫn material lẫn font bên trong JSON của text;
không giữ đường dẫn font tuyệt đối của máy xuất.

Mask chữ nhật và hiệu ứng Blur/Mosaic cũng được giữ khi nhập lại gói. Bộ nhập
bỏ đường dẫn cache mask/hiệu ứng của máy cũ để CapCut tự tìm hoặc tải tài nguyên
native trên máy đích. `capcap_layers.json` ghi vùng nguồn và số clip đã chuyển.
Phép quy đổi dùng tọa độ vật liệu nguồn, không dùng kích thước widget preview.
Cần kiểm tra trực quan trên CapCut Desktop, nhất là vùng lệch tâm khi Fill/lật
gương; unit test xác nhận dữ liệu và phép tính, chưa xác nhận renderer CapCut.

Bản sao index nằm ngoài gói project để không đưa thông tin các project khác
vào gói chia sẻ.

Không render MP4, không gọi API trả phí và không đồng bộ hai chiều.

## Cấu trúc triển khai

`ui/controllers/capcut_export_controller.py` chụp timeline hiện tại trên UI
thread rồi chuyển snapshot cho worker. `app/capcut/draft_export.py` xử lý media,
ghi draft gốc và bản timeline lồng bên trong, đóng gói bằng thư mục tạm trước
khi đổi tên sang đích. Không thêm dependency hoặc tải server VectCutAPI.

Fit Voice dùng chung `app/services/time_warp_plan.py` với preview, audio và
render MP4. Các đoạn mở rộng được sắp theo điểm kết thúc nguồn; phần nguồn
chung chỉ đọc một lần, vẫn giữ tổng thời gian mở rộng. Tốc độ được tính từ
thời lượng chính xác thay vì tin giá trị `speed` đã làm tròn trong project cũ.
Việc chuẩn hóa thực hiện trong bộ nhớ, không sửa phụ đề hoặc file giọng cũ.
MP4 tính số khung theo mốc timeline tổng để tránh cộng dồn sai số từng cue.

Tham khảo cấu trúc:
[VectCutAPI](https://github.com/sun-guannan/VectCutAPI), đặc biệt
`pyJianYingDraft/segment.py`, `local_materials.py`, `text_segment.py`, cùng
draft Windows CapCut có sẵn trên máy. Bộ ghi là implementation riêng, không
chép server, SDK hoặc thư viện từ repo tham khảo.

Tham khảo thêm cách ghép clip nguồn + mask chữ nhật + Blur của
[capcut-mcp](https://github.com/spectrika/capcut-mcp/blob/main/timeline_edit.py)
và quy ước mask của
[pyCapCut](https://github.com/GuanYixuan/pyCapCut/blob/main/pycapcut/video_segment.py).

Chạy kiểm tra: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_capcut_draft_export.py -v`.
