# Thử dịch song song GPT/Gemini Web — 10/11/2026

Đã thử trực tiếp trên Edge với các tài khoản đang đăng nhập. Mỗi batch có 40 câu phụ đề mẫu tiếng Anh, dịch sang tiếng Việt bằng cùng prompt của CapCap; dùng Temporary Chat, không retry, không fallback. Không ghi nhận model cụ thể hoặc hạn mức còn lại của tài khoản.

| Nền tảng | Batch đồng thời | Số câu nhận đủ | Thời gian cả đợt | Điều kiện cửa sổ |
| --- | ---: | ---: | ---: | --- |
| GPT | 2 | 80/80 | 18,469 giây | Cửa sổ riêng mặc định |
| GPT | 4 | 160/160 | 24,750 giây | Cửa sổ riêng mặc định |
| Gemini | 2 | 80/80 | 23,547 giây | Hai popup hiện rõ cạnh nhau |
| Gemini | 4 | 160/160 | 31,312 giây | Bốn popup hiện rõ theo lưới 2×2 |

Bridge ghi nhận đỉnh 2/4 tác vụ translate được dispatch đồng thời, đúng mức từng đợt. Mỗi câu trả lời đều qua kiểm tra đủ số dòng và thứ tự 1..40 của provider. File kết quả đã kiểm tra có nội dung tiếng Việt. Hai mức có tổng số câu khác nhau; bảng không phải phép so sánh cùng một khối lượng công việc.

Không quan sát lỗi giới hạn yêu cầu trong các lượt hoàn thành trên. Đây là lấy mẫu ngắn, không xác định hạn mức tối đa hay bảo đảm tài khoản/model khác hoặc chạy liên tục nhiều giờ cũng như vậy.

## Lỗi phát hiện khi thử thực tế

- GPT: bản chờ hoàn tất ban đầu chỉ tìm nút xác nhận trong khung nội dung quá hẹp. DOM thực tế đặt nút ở phần bao ngoài; dù đã có khoảng 2.700 ký tự và không còn sinh nội dung, tool vẫn chờ. Đã sửa cách tìm vùng của đúng câu trả lời, dừng trước tin nhắn khác/user prompt; vẫn yêu cầu dấu hiệu hoàn tất và không có trạng thái streaming ở phần tử cha. Thêm nhận diện `data-talvt-turn-state` và test hồi quy.
- Gemini: khi popup bị che, DOM đã có nhiều dòng dịch nhưng các span còn lớp `pending`, phần markdown còn `aria-busy="true"`, nội dung hiển thị chỉ có nhãn của Gemini. Đợt chờ 90 giây không trả kết quả. Khi bố trí các cửa sổ hiện rõ bằng thao tác Windows trong lượt thử tiếp theo, cả hai mức đều hoàn tất. Quan sát này phù hợp với việc xử lý hiển thị/animation bị trì hoãn ở cửa sổ nền; chưa có bằng chứng đây là hạn mức tài khoản.
- Bổ sung nhận diện nút dừng Gemini tiếng Việt `Ngừng tạo câu trả lời`.

Việc bố trí/đặt popup nổi ở trên chỉ dùng cho phép thử; các cửa sổ thử đã tự đóng. Chưa thêm cơ chế tự bố trí cửa sổ vào ứng dụng. Ngay sau phép thử, giới hạn được khôi phục về 2. Theo yêu cầu tiếp theo, đã mở lựa chọn **1, 2, 3, 4** trong Cài đặt và nâng giới hạn extension lên 4; mặc định vẫn là 1, không tự đổi lựa chọn đã lưu của người dùng.

## Công cụ và dữ liệu

Script [probe_browser_parallel.py](../scripts/probe_browser_parallel.py) dùng bridge thật, tạo dữ liệu mẫu và lưu báo cáo từng đợt. Task `diagnose_tasks` của extension chỉ chụp DOM những tab đã được gắn với tác vụ đang hoạt động. Slot chẩn đoán không được tính vào đỉnh tác vụ dịch. Với giới hạn hiện tại là 4, task chẩn đoán có thể phải chờ khi cả bốn tác vụ dịch đang chạy.

Lệnh đã dùng cho các lượt hoàn thành:

```powershell
.venv\Scripts\python.exe scripts/probe_browser_parallel.py --workers 2 4 --cues 40 --deadline 90 --output temp/browser-parallel-probe/run-2
.venv\Scripts\python.exe scripts/probe_browser_parallel.py --platforms gemini --workers 2 4 --cues 40 --deadline 120 --output temp/browser-parallel-probe/gemini-visible
```

Lượt Gemini thứ hai có thao tác thủ công để hiện các popup cạnh nhau. Báo cáo JSON và file câu dịch nằm trong hai thư mục output trên (bị gitignore). Các lượt chưa nhận được kết quả trước khi sửa/đổi điều kiện hiển thị được giữ trong `initial-run.json`, `diagnostic-run/` và báo cáo `run-2/`; không tính chúng là lỗi giới hạn dịch vụ.
