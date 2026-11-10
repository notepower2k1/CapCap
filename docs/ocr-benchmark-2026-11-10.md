# Tối ưu OCR và benchmark video — 10/11/2026

Đã chạy toàn bộ video `D:\CodingTime\media\SaveTik.io_7644198938047745299.mp4` trước và sau thay đổi. Cấu hình lấy từ thiết lập hiện tại: **Windows Media OCR**, tiếng Trung (`zh` → `zh-Hans-CN`), **2 FPS**, vùng dưới 25% khung hình, không đặt crop tùy chỉnh. Video dài **1.651,2 giây (27 phút 31,2 giây)**, 1024×576, 25 FPS, 41.280 frame.

| Chỉ số | Trước | Sau |
| --- | ---: | ---: |
| Tổng thời gian OCR | 180,882 giây | 89,971 giây |
| Lấy/giải mã frame | 96,84 giây | 14,32 giây |
| Nhận dạng chữ | 50,31 giây | 50,81 giây |
| Khung hình lấy mẫu | 3.303 | 3.303 |
| Lần gọi engine OCR | 2.643 | 2.643 |
| Đoạn phụ đề | 824 | 824 |

**Nhanh hơn 2,01 lần, giảm 50,26% thời gian**, tiết kiệm khoảng 90,9 giây trên video này. Bản sau chọn đọc tiến tuần tự sau khi đo chi phí hai cách đọc. Đọc metadata bằng FFprobe mất khoảng 0,05 giây. Log cũ gọi mục nhận dạng là `RapidOCR inference` ngay cả khi backend là Windows Media OCR; bảng trên ghi theo backend thực tế đã dùng.

## Kiểm tra kết quả

- Chuỗi CRC32 của **toàn bộ 3.303 ảnh crop đưa vào xử lý trùng khớp** trước/sau.
- **824 đoạn phụ đề trùng hoàn toàn**, gồm nội dung, thời điểm bắt đầu/kết thúc và dữ liệu `words`.
- Hai file SRT **giống nhau từng byte**.
- Giữ nguyên FPS, vùng crop, bộ lọc ảnh, ngưỡng bỏ qua frame và quy tắc ghép phụ đề. Không tăng tốc bằng cách bỏ thêm mẫu hoặc thay model.

Đây là một cặp lượt đo trên cùng máy, engine được nạp và làm nóng trước mỗi lượt; thời gian nạp engine khoảng 0,04 giây, không tính vào tổng. Cả hai lượt cùng tính chi phí CRC32 để đối chiếu ảnh. Mức tăng trên video/backend khác có thể khác; không suy ra RapidOCR cũng tăng đúng 2 lần. Kết quả giống bản trước không đồng nghĩa đã đối chiếu độ chính xác với transcript chuẩn.

## Thay đổi

1. Thay lệnh FFmpeg giải mã hết video chỉ để đọc thời lượng bằng FFprobe đọc metadata, có timeout. Nếu thiếu FFprobe hoặc metadata không hợp lệ, đọc FPS/số frame qua OpenCV để tính thời lượng, không quét hết video.
2. Giữ nguyên chỉ số frame lấy mẫu. So sánh ngắn chi phí seek với cách `grab()` đi qua frame trung gian mà không chuyển từng frame đó sang ảnh BGR. Chỉ chuyển sang BGR ở mẫu cần nhận dạng. Chọn đọc tuần tự khi rẻ hơn rõ ràng; vẫn seek cho bước nhảy lớn, quay lại frame cũ và video mà seek có lợi hơn.
3. Thêm thời gian đọc metadata và cách đọc frame được chọn vào log profiling. Không chạy nhiều lệnh nhận dạng đồng thời trên cùng engine/bộ nhớ dùng chung.

27 test pass trong các bộ `test_ocr_video_reading`, `test_ocr_enhancements`, `test_range_transcription`, `test_transcript_import`. Có kiểm tra fallback metadata, các kiểu lấy mẫu/seek, EOF, chọn cách đọc theo chi phí và đối chiếu pixel trên video thử nghiệm thật.

## Tái chạy

Script: [benchmark_ocr.py](../scripts/benchmark_ocr.py). Bản mã trước thay đổi đã lưu tại `temp/ocr-benchmark/ocr_processor_before.py`, SHA-256 `5b9865a8c3fe1c773692d2e1aaee6156bfbe3f18bf3967bb59c22a6dd84cf374`.

```powershell
.venv\Scripts\python.exe scripts/benchmark_ocr.py "D:\CodingTime\media\SaveTik.io_7644198938047745299.mp4" --implementation temp/ocr-benchmark/ocr_processor_before.py --output temp/ocr-benchmark/before --backend winocr --sampling-fps 2 --language zh
.venv\Scripts\python.exe scripts/benchmark_ocr.py "D:\CodingTime\media\SaveTik.io_7644198938047745299.mp4" --output temp/ocr-benchmark/after --backend winocr --sampling-fps 2 --language zh --compare-to temp/ocr-benchmark/before
```

Các thư mục `before/`, `after/` có log profiling, báo cáo JSON, CRC32 của ảnh mẫu, phụ đề JSON và SRT. So sánh của lượt đo đã lưu tại `temp/ocr-benchmark/comparison.json`. Đây là dữ liệu cục bộ bị gitignore. Chưa build lại installer cho thay đổi này.
