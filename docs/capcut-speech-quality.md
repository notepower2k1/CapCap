# Cải thiện CapCut STT/TTS — 14/11/2026

Tham khảo giao thức và cấu trúc dữ liệu trong [K07VN/capcut-tts-api](https://github.com/K07VN/capcut-tts-api). Không cài SDK hoặc thêm phụ thuộc từ repo vào CapCap. VectCutAPI đã được dùng làm nguồn tham khảo cho xuất draft; thay đổi này tập trung vào speech, không sửa hiệu ứng/project xuất.

## Ngôn ngữ nhận dạng

Trước đây `transcribe_audio_capcut(language=...)` nhận ngôn ngữ từ giao diện nhưng không đưa nó vào request. Các ngôn ngữ đều đi qua profile JianyingPro.

Hiện tại:

| Ngôn ngữ đầu vào | Đường xử lý |
| --- | --- |
| Chinese | Giữ endpoint `/audio_subtitle/submit` cũ; gửi thêm `zh-CN` |
| Auto Detect | Giữ endpoint/profile cũ, không ép ngôn ngữ |
| Vietnamese, English, Japanese, Korean, Thai, Russian, French, German | Dùng `/common_task/new` với `cc_audio_subtitle_asr`, gửi locale tương ứng |

**Với audio ngoài tiếng Trung, cần chọn rõ ngôn ngữ đầu vào.** Auto Detect chưa được thay bằng bộ phát hiện đa ngôn ngữ mới; có thể nhận sai. Cài đặt CapCut STT có ghi chú này.

Luồng common-task xin credential đúng dịch vụ, upload và commit để lấy media VID/checksum trước khi gửi nhận dạng. Không bật dịch trong request STT. Cả Web và PC dùng chung logic; chỉ khác thư viện gửi request. Không tự fallback về luồng bỏ qua ngôn ngữ khi common-task báo lỗi.

Retry upload/query, timeout theo thời lượng và phục hồi chia đôi vẫn được giữ. Retry nhận dạng dùng lại audio đã upload/commit; các phần phục hồi tiếp tục dùng đúng ngôn ngữ. Không ghi credential, token hay signed URL vào log.

## Thời gian từng từ

Giữ `words` từ phản hồi cloud, cộng offset từng chunk đúng một lần, cắt theo giới hạn câu sau merge và chuyển từ mili giây sang giây theo định dạng segment hiện có. Khi thay câu ngắn bằng câu đầy đủ ở giáp ranh, thay cả dữ liệu từ để không giữ timestamp của câu cũ. Nhận dạng một vùng video cộng offset vùng cho cả câu và từ. Dữ liệu tiếp tục đi qua metadata của project.

Đây là giữ dữ liệu căn thời gian do cloud trả về; không tạo thêm lời nói hoặc thay model nhận dạng.

## Giọng đọc và trạng thái tác vụ

- Ánh xạ service voice ID, tên hiển thị hoặc resource ID tới catalog. Không đoán resource mặc định cho giọng không tìm thấy; thiếu/không hợp lệ/không rõ ánh xạ thì báo lỗi trước khi gửi.
- Loại metadata digital-human khỏi danh sách voice TTS thông thường.
- Giữ `bind_id` của task; chọn đúng task ID khi đọc phản hồi. Chấp nhận các trạng thái thành công `success/succeed/succeeded/completed/done` và phát hiện sớm thất bại/hủy/hết hạn.
- Kiểm tra mã lỗi API ở cả create/query, JSON/payload, số audio và URL từng audio. Batch thiếu audio không báo thành công. Khi fallback từng câu còn lỗi, báo số câu thất bại và giữ các file đã tạo.
- Log có job ID, số phần tử, voice ID, số lần poll và thời gian chờ. Không ghi toàn bộ body phản hồi; thông báo lỗi rút gọn, che URL và token có nhãn.

## Kiểm chứng thực tế

Mẫu tổng hợp ngắn do CapCap tạo, không sử dụng audio khách hàng:

- Web: TTS tiếng Anh và STT `en-US` thành công trên mẫu 7,08 giây.
- PC (CapCut 9.7.0.4153): TTS batch hai câu tiếng Việt xuất đủ WAV; STT `vi-VN` trả sáu đoạn, 14 từ với timestamp trên mẫu 3,43 giây.
- Web: cùng file tiếng Việt với `vi-VN` cũng trả đúng sáu đoạn/14 từ, xác nhận cải thiện không phụ thuộc DLL PC.
- So với cùng file tiếng Việt qua luồng cũ/Auto, luồng cũ trả `星穹铁道` thay cho lời tiếng Việt. Đây là bằng chứng trên một mẫu, không phải đánh giá WER toàn diện.
- PC: audio tiếng Anh lặp bốn lần, 28,32 giây, ba chunk/hai worker; hoàn tất 36 đoạn và 76 từ. Mốc cuối của từ và câu cùng khoảng 28,321 giây.
- Web: tạo giọng và nhận dạng tiếng Trung bằng endpoint direct vẫn thành công; mẫu 4,13 giây trả hai đoạn và 10 từ.

Chưa có benchmark video dài hoặc đủ dữ liệu để khẳng định tỷ lệ chính xác tăng bao nhiêu. Các phép thử tiếng Anh/Việt/Trung không xác nhận mọi ngôn ngữ và mọi tài khoản. Installer chưa build lại.

## Mã và kiểm thử

Bộ hồi quy liên quan: **110 tests và 54 subtests pass**. Gồm speech, transport Web/PC, cài đặt, nhận dạng một vùng, merge ASR, dịch giao diện và hàng đợi Performance. `compileall` và `git diff --check` cũng qua.

- `app/capcut/api.py`: kiểm tra HTTP/JSON/API, trạng thái, payload và định danh task; header/query chung.
- `app/capcut/stt_common.py`: request STT có ngôn ngữ và poll cùng task tới trạng thái cuối.
- `app/capcut/stt.py`: upload/commit, chọn luồng, retry và chuẩn hóa word timings.
- `app/capcut/tts.py`: ánh xạ voice/resource, xác nhận đủ kết quả và lỗi fallback.
- `tests/test_capcut_speech_quality.py`: ngôn ngữ, commit VID/checksum, trạng thái, timeout, lỗi API, thiếu audio, mapping voice và timestamp qua project.
- `tests/test_range_transcription.py`: timestamp từng từ khi nhận dạng một vùng.
