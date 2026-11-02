# OmniVoice qua audio.cpp

CapCap chạy model **OmniVoice gốc của k2-fsa**, bằng audio.cpp native; không dùng bản fine-tune KhanhTTS trong `D:\CodingTime\TTS`, không cần cài Python OmniVoice/PyTorch. Bản phát hành đầu dùng GGUF Q8 theo gói đề xuất của audio.cpp.

## Sử dụng

1. Mở **Quản lý tài nguyên → tab OmniVoice**, tải **OmniVoice gốc (GGUF Q8, 1,3 GB)**. Tab này gom model, bộ chạy CUDA, CPU và Vulkan, kèm hướng dẫn chọn gói cần tải.
2. Với GPU NVIDIA, tải **OmniVoice audio.cpp CUDA GPU runtime** (444 MB). App dùng lại bộ DLL CUDA 12 đầy đủ từ **GPU Acceleration Pack**; nếu chưa có hoặc thiếu thư viện, tự tải thêm gói DLL CUDA 12.4 của audio.cpp (579 MB). Không cần cài CUDA Toolkit. Các lựa chọn khác: **CPU (29 MB)** hoặc **GPU Vulkan (78 MB)**.
3. Trong phần tạo giọng, chọn **OmniVoice (audio.cpp)**. Engine có mặt với cả 8 ngôn ngữ đầu ra hiện có: Việt, Anh, Hàn, Nhật, Thái, Nga, Pháp, Đức.
4. OmniVoice chỉ dùng **giọng clone**, mặc định chọn giọng mẫu có sẵn trong danh sách và giữ lại lựa chọn trước đó nếu còn hợp lệ. Có thể dùng ngay mẫu VieNeu hoặc chọn **Tạo bản sao giọng đọc**, đưa vào audio mẫu sạch 3–15 giây và lời nói chính xác trong mẫu. Mẫu tối đa 30 giây. App sao chép mẫu mới vào thư mục giọng riêng, nên không phụ thuộc file mẫu ban đầu sau khi lưu. Nếu không có mẫu phù hợp, cần tạo/chọn một giọng trước khi chạy.
5. Nghe thử, tạo toàn bộ hoặc tạo lại từng câu như các engine hiện có. Speed, Smart Mode và Fit Voice dùng luồng xử lý thời lượng sẵn có, tránh áp dụng tốc độ hai lần.

Khi chọn engine OmniVoice, mục **Chất lượng tạo giọng OmniVoice** có bốn mức: **Chất lượng (32 bước, mặc định)**, **Trung bình (24 bước)**, **Nhanh (16 bước)**, **Cực nhanh (8 bước)**. Ít bước giúp tạo nhanh hơn nhưng có thể giảm độ rõ và độ giống giọng mẫu; 8 bước phù hợp nghe thử. Thiết lập được nhớ cho lần mở app sau và áp dụng cho mọi giọng OmniVoice ở cả nghe thử, tạo từng câu và toàn bộ quy trình. Không đổi được mức trong lúc đang tạo giọng. Số bước nằm trong cache fingerprint, nên đổi mức sẽ tạo audio tương ứng thay vì lấy nhầm cache ở mức khác. Đây là tốc độ tính toán, không thay đổi tốc độ đọc của nhân vật.

Các giọng clone/reference đã lưu cho **VieNeu** có đủ audio và transcript tự xuất hiện trong OmniVoice, kèm nhãn **(VieNeu)**. App đọc lại mẫu gốc, tạo WAV mono 24 kHz tạm khi cần, không sao chép giọng sang thư viện khác hay sửa dữ liệu VieNeu. Không cần tải model VieNeu để dùng các mẫu này với OmniVoice. Các preset chỉ có embedding, không có audio + transcript sẽ không được dùng chung. Kết quả âm thanh có thể khác VieNeu vì model tổng hợp là OmniVoice. Nếu xóa mẫu gốc VieNeu thì lựa chọn tương ứng trong OmniVoice cũng mất; mẫu dài quá 30 giây cần rút gọn và sửa transcript cho khớp trước khi dùng.

Ghi chú VRAM trong tab: **khuyến nghị từ 6 GB**, đã kiểm thử trên RTX 2060 6 GB; chưa xác nhận mức tối thiểu. Nhu cầu thực tế tăng theo độ dài câu và audio mẫu. Chạy CPU không cần VRAM GPU. Dung lượng file model không phải mức VRAM tối thiểu.

Khi ở CPU mode, app dùng bộ chạy CPU. Khi ở GPU mode, ưu tiên **CUDA**, sau đó Vulkan nếu đã tải. Thiếu bộ chạy GPU thì yêu cầu tải, không âm thầm dùng CPU. CPU chạy được nhưng chậm hơn đáng kể trên máy đã thử. Lỗi khởi động hoặc inference sẽ được báo rõ, không tự đổi backend hay tạo đoạn im lặng.

## Runtime, cache và chẩn đoán

- Pin audio.cpp `v0.9.3`; model `audio-cpp/audio.cpp-gguf`, revision `874fcb41ececda20bcbc0782d6b4ea7f7d069ea8`, file `OmniVoice-GGUF/omnivoice-q8_0.gguf`. Tải model kiểm tra kích thước và SHA-256 trước khi thay file đang dùng.
- Binaries: `bin/audio_cpp/cpu`, `bin/audio_cpp/cuda` hoặc `bin/audio_cpp/vulkan`. Model: `models/omnivoice/omnivoice-q8_0.gguf`. Giọng clone: `models/omnivoice/voices`.
- CUDA dùng một bộ thư viện đầy đủ gồm `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, `cufft64_11.dll`; không ghép DLL rời từ nhiều bộ khác nhau. Giữ nguyên Acceleration Pack, thêm đường dẫn DLL vào môi trường của riêng tiến trình audio.cpp. `ggml-cuda.dll` vẫn thuộc audio.cpp, không thay bằng thư viện của Whisper. Nếu trước đó đã cài gói CUDA riêng, DLL cạnh executable được Windows ưu tiên; tải lại runtime khi đã có Acceleration Pack sẽ chuyển sang dùng pack chung.
- Chỉ khởi động server khi tạo audio. Server nghe trên `127.0.0.1` với cổng ngẫu nhiên, không mở WebUI; giữ model cho nhiều câu, xử lý tuần tự, tự nhả model sau 3 phút không dùng. Tắt app hoặc hủy workflow sẽ đóng tiến trình do CapCap tạo.
- Không tải model hoặc khởi động inference khi chỉ mở danh sách giọng. WAV lưu vào pipeline là PCM16, mono, 16 kHz. Cache gồm ngôn ngữ, model/runtime và thông tin mẫu clone; lỗi không ghi cache im lặng.
- Log server giữ tại `logs/capcap-omnivoice-*.log`; runtime log ghi backend, PID và đường dẫn log. Timeout đóng server để câu sau không bị chờ một inference đã treo.
- Cho thử GGUF khác: `CAPCAP_OMNIVOICE_MODEL` trỏ đến GGUF OmniVoice tương thích. `CAPCAP_AUDIOCPP_SERVER` trỏ đến executable audio.cpp tương thích. Đây là override kỹ thuật; trạng thái trong Quản lý tài nguyên vẫn mô tả các gói chuẩn do app cung cấp.

## Kiểm tra

Unit tests không tải tài nguyên hoặc gọi AI: `tests/test_omnivoice.py`. Kiểm tra dispatch, ngôn ngữ ở batch/single cue/preview, cache, payload clone, tái sử dụng server, shutdown, timeout, lỗi engine không thành silence placeholder, cài resource và chặn đường dẫn ZIP vượt thư mục.

Đã chạy WAV thật bằng model gốc trên CPU, Vulkan và CUDA (RTX 2060 6 GB), clone từ audio + transcript, tạo hai câu cùng server, chạy lại lấy cache và ghép voice track qua `VoiceWorkflow.run` với speed 1.15. CUDA đã thử với DLL riêng và bộ DLL dùng chung từ Acceleration Pack. Đây là smoke test kỹ thuật; chất lượng phát âm và độ giống giọng cần nghe thử với audio mẫu thực tế của người dùng.

Nguồn: [audio.cpp](https://github.com/0xShug0/audio.cpp), [server API v0.9.3](https://github.com/0xShug0/audio.cpp/blob/v0.9.3/app/server/api.md), [OmniVoice gốc](https://huggingface.co/k2-fsa/OmniVoice).
