# RELEASE NOTES — V9.0.0

**Ngày cập nhật: 10/11/2026**

**Lưu ý sau cập nhật khi dùng GPT/Gemini Web:** Vào `chrome://extensions` hoặc `edge://extensions`, bấm **Reload (Tải lại)** tiện ích CapCap, rồi tải lại các tab GPT/Gemini đang mở. Nếu extension được copy sang thư mục khác, cần cập nhật các file trong thư mục đó trước khi Reload; nếu đường dẫn đã thay đổi, dùng **Load unpacked** từ thư mục mới. Cài đè cùng đường dẫn và extension đang load từ thư mục app thì không cần cài lại tiện ích từ đầu.

## 🌟 1. Tính năng mới nổi bật

- **Dịch trực tiếp bằng Gemini / ChatGPT Web**: Thêm extension Chrome/Edge để dịch bằng tài khoản đã đăng nhập, không cần API key. Hỗ trợ phân tích nhân vật, góp ý xưng hô và dịch trong cùng một cửa sổ; có thể chọn dùng tab hiện tại.
- **Lồng tiếng OmniVoice ngoại tuyến**: Tích hợp OmniVoice qua audio.cpp, hỗ trợ CUDA, CPU và Vulkan. Có bốn mức chất lượng tạo giọng và dùng lại được mẫu clone VieNeu có audio cùng lời thoại.
- **Chế độ Performance và hàng đợi video**: Thêm lựa chọn Quality / Performance trên launcher. Performance dùng khung hình tĩnh, bảng phụ đề gọn, cấu hình riêng cho từng video và xử lý hàng đợi tuần tự.
- **Công cụ xử lý media độc lập**: Bổ sung cửa sổ FFmpeg Media Tools với bốn tab: Cắt video, Ghép video, Tách âm thanh/video và Ghép âm thanh vào video.
- **Xuất / nhập project CapCap**: Xuất project thành gói di động để sao lưu hoặc chuyển máy qua **Thêm → Xuất tài nguyên → Xuất tài nguyên…**. Chọn kèm video gốc để nhập lại từ **Launcher → Nhập dự án → Nhập gói CapCap…**; giữ phụ đề, giọng đọc, timeline, layer và cấu hình chỉnh sửa đã chọn. Nhận diện project đã có để tránh nhập trùng.
- **Xuất project CapCut Desktop (thử nghiệm)**: Thêm **Thêm → Xuất tài nguyên → Xuất sang CapCut…**, tạo draft mới để dựng tiếp trong CapCut. Giữ video, phụ đề sửa được, TTS, nhạc nền, các lớp chữ/logo, mask/blur, keyframe và thời lượng Fit Voice; một số hiệu ứng được chuyển gần tương đương.
- **Xuất tài nguyên theo nhóm**: Chọn riêng transcript gốc, phụ đề đã dịch, file giọng từng câu, track âm thanh, video và dữ liệu chỉnh sửa. Hiển thị số file/dung lượng dự kiến, xuất tài nguyên đã có mà không chạy lại dịch, TTS hoặc render.
- **Tùy chỉnh làm sạch bản chép lời Whisper**: Thêm hai lựa chọn giảm câu lặp và giảm phụ đề tự sinh trong khoảng im lặng, bật/tắt độc lập; có ngưỡng tương đồng văn bản và thời gian im lặng trong phần nâng cao.
- **Lật ngang video**: Thêm tùy chọn lật ngang trong Video Inspector, áp dụng cho xem trước và xuất video.

## ⚡ 2. Cải tiến & Tối ưu hiệu năng

- **Tăng tốc OCR mà giữ nguyên kết quả lấy mẫu**: Đọc thời lượng từ metadata thay cho quét hết video; đo và chọn giữa seek từng mẫu với đọc frame tiến tuần tự. Benchmark video 27 phút 31 giây bằng Windows Media OCR ở 2 FPS giảm từ 180,9 xuống 90,0 giây, giữ nguyên 3.303 ảnh crop và 824 đoạn phụ đề, SRT trùng từng byte. Chi tiết trong `docs/ocr-benchmark-2026-11-10.md`; mức tăng phụ thuộc video và backend.
- **Dịch song song bằng GPT/Gemini Web**: Thêm lựa chọn 1–4 batch đồng thời trong Cài đặt → Dịch thuật → Browser AI, đồng bộ giới hạn với extension. Mỗi batch dùng một tab/cửa sổ riêng, nhận cùng ngữ cảnh đã phân tích; ghép kết quả đúng thứ tự và cộng tiến độ theo số câu thực sự hoàn thành. Tự đóng phiên riêng khi xong, hủy đúng từng tác vụ. Mặc định vẫn dịch tuần tự; tốc độ tùy giới hạn dịch vụ/tài khoản AI. Hiển thị lưu ý giữ cửa sổ Gemini không bị che kín.
- **Tối ưu xuất video FFmpeg**: Tận dụng tăng tốc phần cứng, đa luồng và sao chép luồng media tạm khi phù hợp để giảm xử lý mã hóa lại.
- **Cải thiện độ mượt của trình xem trước và timeline**: Tối ưu lớp phủ cho cập nhật tần số cao, dùng cache timeline và cải thiện đồng bộ khi thay đổi tốc độ video.
- **Đồng bộ Fit Voice giữa xem trước và xuất project**: Thống nhất cách quy đổi thời gian khi làm chậm hoặc giữ khung hình, giữ vị trí phụ đề, âm thanh và các layer khi xuất sang CapCut.
- **Giảm khoảng lặng thừa ở đầu/cuối giọng TTS**: Cắt khoảng lặng đủ dài bằng bộ lọc bảo thủ trước khi tính tốc độ và Fit Voice, giữ khoảng nghỉ bên trong câu và file giọng gốc trong cache.
- **Tách xử lý khỏi xuất video**: Có thể xử lý rồi lưu project trước, xuất video sau bằng phụ đề và giọng đọc đã tạo. Hàng đợi hỗ trợ dừng sau video hiện tại, dừng ngay và tùy chọn tự xuất sau xử lý.
- **Mở rộng hỗ trợ ngôn ngữ**: Bổ sung lựa chọn Hàn, Nhật, Thái, Nga, Pháp và Đức; lọc engine nhận dạng/giọng đọc theo ngôn ngữ, đồng bộ ngôn ngữ nghe thử và quy tắc dịch.
- **Điều chỉnh xử lý CapCut**: Mặc định TTS xử lý theo batch 30 câu và STT chia đoạn 10 phút, đồng thời chọn engine phù hợp với ngôn ngữ đầu vào/đầu ra.
- **Tiến độ tạo giọng rõ ràng hơn**: Popup hiển thị số câu đã hoàn thành/tổng số câu và phần trăm, tính cả câu lấy từ cache; giữ bộ đếm khi OmniVoice đang tạo câu tiếp theo.
- **Thuận tiện hơn khi cài extension**: Đóng gói extension cùng ứng dụng, thêm nút cài nhanh và đồng bộ logo CapCap. Cửa sổ riêng là mặc định; phân tích lại và dịch tiếp tục dùng chung phiên.
- **Chẩn đoán GPU và nhận diện người nói**: Bổ sung hướng dẫn khắc phục lỗi NVIDIA/CUDA và hiển thị mã người nói gốc cạnh tên trên giao diện.

## 🛠️ 3. Sửa lỗi quan trọng

- **Nhận phản hồi GPT/Gemini khi AI mới tạm ngừng sinh chữ**: Bỏ điều kiện đứng yên 1,5 giây; yêu cầu dấu hiệu hoàn tất trong đúng lượt trả lời mới nhất và không còn trạng thái streaming. Gemini đọc toàn bộ khối trả lời; GPT xác nhận gửi theo cả DOM tin nhắn mới và thay đổi editor, tránh phụ thuộc duy nhất vào selector cũ. Timeout chỉ báo lỗi, không nhận bản dịch dở dang.
  - Đối chiếu trực tiếp 10/11: tìm nút hoàn tất GPT ở phần bao ngoài khung DIL mà không lấy nhầm nút của tin nhắn khác; theo dõi trạng thái lượt trả lời ở phần tử cha. Nhận diện thêm nút dừng Gemini tiếng Việt. GPT/Gemini đã hoàn tất thử nghiệm 4 batch × 40 câu, nhưng Gemini có thể bị giữ ở trạng thái hiển thị khi popup bị che; xem báo cáo thử trực tiếp trong `docs`.
- **Một batch AI lỗi làm dịch lại toàn bộ bằng Google Translate**: Giữ các batch AI đã hoàn thành hợp lệ, chỉ fallback những câu còn thiếu và giữ đúng vị trí, provider, tiến độ. Áp dụng cho dịch tuần tự, song song và các batch phục hồi sau khi dịch toàn ngữ cảnh thất bại; hủy tác vụ không kích hoạt fallback.
- **Logo và mask biến mất khi mở menu “Thêm”**: Giữ các lớp preview hiển thị khi mở menu hoặc menu con của editor, đặt menu lên trên lớp phủ. Hộp thoại modal và chuyển sang cửa sổ khác vẫn tạm ẩn overlay như trước.
- **Ô kiểm tra xưng hô vẫn được tích khi tắt tự động phát hiện ngữ cảnh**: Tự bỏ chọn bước kiểm tra trước khi vô hiệu hóa; khi bật lại tự động phát hiện, người dùng có thể chọn lại bước kiểm tra.
- **Một nửa vùng blur bị chuyển xanh khi xuất video**: Chuẩn hóa frame NV12 từ đường giải mã phần cứng sang YUV420P trước bộ lọc blur, áp dụng cho blur layer và mask chế độ blur. Bản sửa 10/11 đã được đối chiếu bằng video thực tế và kiểm thử màu pixel bằng FFmpeg.
- **Dòng tiến độ OmniVoice nhấp nháy giữa số câu và thông báo tạo giọng**: Đưa bộ đếm câu về dòng tiến trình phía dưới, giữ hàng TTS hiển thị phần trăm. Thông báo runtime lặp lại của OmniVoice không ghi đè bộ đếm; không cập nhật lại dòng tiến trình khi nội dung không đổi.
- **Tốc độ từng câu hiển thị lại 1.0x dù audio đã được đồng bộ**: Giữ hệ số tốc độ và cờ tự điều chỉnh khi đồng bộ phụ đề qua SRT; khôi phục thông tin bị mất từ đúng file giọng đã xử lý khi mở lại project, giữ nguyên tốc độ chỉnh tay.
- **ChatGPT trả lời nhưng ứng dụng không nhận được**: Cập nhật cách đọc giao diện ChatGPT mới, lấy đủ các khối phản hồi và báo lỗi khi không đọc được nội dung.
- **Prompt ChatGPT chỉ được điền mà chưa gửi**: Chờ nút gửi sẵn sàng và xác nhận tin nhắn đã được trang tiếp nhận trước khi chờ kết quả.
- **Mở thêm cửa sổ sau khi phân tích nhân vật**: Bước dịch tiếp quản cửa sổ phân tích, tránh tạo thêm cửa sổ cho mỗi bước hoặc mỗi lần phân tích lại.
- **Chat tạm thời Gemini chưa được bật đúng**: Bật và xác nhận Temporary Chat trước khi gửi. Nếu không xác nhận được, tác vụ báo lỗi thay vì gửi vào cuộc trò chuyện thường.
  - Bản sửa 10/11: phân biệt nút mở chat tạm thời với trạng thái đang bật; giữ nhận diện sau khi có phản hồi để batch tiếp theo không bị lỗi xác nhận và chuyển sang Google Translate.
- **Phân tích xưng hô không chạy với đoạn ngắn**: Bỏ việc bỏ qua đoạn dưới ba câu và hiển thị nguyên nhân khi phân tích thủ công thất bại.
- **Vùng OCR trong Performance bị lệch hoặc làm đen khung hình**: Sửa kéo/đổi kích thước, lưu vùng chọn riêng theo video và cách hiển thị lớp OCR.
- **Dữ liệu video và hàng đợi không đồng bộ sau khi xóa**: Đối chiếu lại file còn trên đĩa, lịch sử và hàng đợi khi gặp file đang được sử dụng; ngăn thêm trùng video giữa Quality và Performance.
- **Phát xem trước không dừng đúng cuối video**: Đồng bộ lại vị trí phát và trạng thái timeline khi video kết thúc.
- **Tạo nhầm project khi nhập lại gói đã xuất**: Giữ mã nhận diện project qua các lần xuất/nhập, liên kết lại đường dẫn media và cho mở project đã có thay vì sao chép trùng dữ liệu.
- **Thông tin GPU lỗi làm mất lựa chọn tăng tốc**: Sửa đọc dung lượng VRAM và giữ khả năng chọn GPU khi chỉ phần thông tin hiển thị bị lỗi; bổ sung thông báo hướng dẫn khắc phục.
- **Nhãn phiên bản launcher còn hiển thị V8**: Cập nhật tiêu đề, hộp thông tin và dòng phiên bản sang CapCap V9 / Version 9.0.0.

Hướng dẫn: [Xuất / nhập project CapCap](docs/resource-packages.md) · [Xuất project CapCut](docs/capcut-draft-export.md) · [Dịch qua trình duyệt](browser_extension/README.md) · [OmniVoice](docs/omnivoice-audio-cpp.md) · [Quality / Performance](docs/performance-mode.md). [Ghi chú kiểm thử và tình trạng bản build](docs/release-validation-9.0.0.md).

---

# RELEASE NOTES — V8.0.6.1

**Ngày phát hành: 20/10/2026**

## 🌟 1. Tích hợp ElevenLabs TTS
- **Hỗ trợ các model mới nhất**: Tích hợp các model tiên tiến (`eleven_turbo_v2_5`, `eleven_flash_v2_5`, `eleven_v4`, `eleven_v3`), tự động tối ưu hóa khi lồng tiếng tiếng Việt.
- **Quản lý tài khoản & Quota**: Hiển thị số ký tự đã sử dụng và tự động phân loại giọng khả dụng theo gói tài khoản (Free / Paid).
- **Trải nghiệm mượt mà**: Hỗ trợ nghe thử giọng trực tiếp và đồng bộ danh sách giọng chạy ngầm không gây giật lag giao diện.

## ⚙️ 2. Cải tiến giao diện Cài đặt (Settings)
- **Bố cục dạng Tab gọn gàng**: Chia thành 4 tab riêng biệt dễ quản lý: *Nhận dạng*, *Dịch thuật AI*, *ElevenLabs*, *CapCut TTS*.
- **Hướng dẫn tạo API Key trực quan**: Tích hợp sẵn hướng dẫn chi tiết các quyền cần cấp khi tạo API Key ElevenLabs (`Text to Speech`, `Voices`, `Voice collection`, `User`).

---

# RELEASE NOTES — V8.3

**Ngày phát hành: 11/10/2026**

## 🌟 1. Tính năng mới nổi bật
- **Giao diện tối ưu cho Video dọc (9:16)**: Tự động nhận diện video dạng dọc (TikTok, Shorts, Reels) để chuyển đổi không gian làm việc tối ưu; dàn ngang hàng nút Subtitle Inspector và thu gọn thanh công cụ Layer, nhường tối đa diện tích cho màn hình xem trước và Timeline.
- **Xuất file âm thanh độc lập (Export Audio)**: Bổ sung tùy chọn xuất riêng file giọng lồng tiếng (Voice), nhạc nền (BGM) hoặc bản phối tổng hợp trong menu Mở rộng.
- **Tạo lại giọng đọc từng câu tức thì**: Đánh dấu các câu phụ đề vừa sửa và cho phép tạo lại giọng đọc ngay tại câu đó mà không cần render lại toàn bộ dự án.
- **Mở khóa công cụ Layer độc lập**: Cho phép chèn và xuất video kèm các lớp Chữ, Logo, Làm mờ (Blur), Mặt nạ (Mask) ngay cả khi không có phụ đề.
- **Kéo thả phụ đề đa tầng trên Timeline**: Hỗ trợ di chuyển tự do các khối phụ đề giữa các dòng trên Timeline với cơ chế chống đè thông minh.
- **Nhập phụ đề SRT linh hoạt**: Hỗ trợ đọc các định dạng SRT phong phú và hiển thị thông báo trực quan nếu file phụ đề bị lỗi cú pháp thời gian.

## ⚡ 2. Cải tiến & Tối ưu hiệu năng
- **Tăng tốc tách giọng / tách nhạc bằng GPU**: Hỗ trợ tận dụng card đồ họa NVIDIA (CUDA) để tách giọng nói nhanh hơn, tối ưu bộ nhớ VRAM cho các dòng card phổ thông (6GB).
- **Mở ứng dụng và tải dự án mượt mà**: Khởi tạo trình phát ngầm thông minh, loại bỏ hoàn toàn hiện tượng đơ trắng màn hình hoặc thông báo "Not Responding" lúc mở dự án.
- **Tối ưu hiển thị Timeline & Sóng âm**: Tăng tốc độ vẽ sóng âm thanh, giúp thao tác cuộn chuột và phóng to/thu nhỏ dòng thời gian mượt mà, không giật lag.
- **Tinh gọn giao diện người dùng**: Gom nhóm các tính năng xuất phụ đề vào menu Mở rộng; thêm nút gập/mở thanh công cụ ở mép màn hình giúp mở rộng tối đa vùng làm việc khi cần.

## 🛠️ 3. Sửa lỗi quan trọng
- **Sửa triệt để lỗi mất âm thanh xem trước**: Khắc phục tình trạng video xem trước bị im lặng sau khi hoàn tất quy trình dịch thuật, hoặc âm thanh chạy tràn khi video kết thúc.
- **Khắc phục lỗi dịch thuật bị treo ở 99%**: Cập nhật tiến độ dịch thuật theo thời gian thực và sửa lỗi ngắt luồng đột ngột khi xử lý văn bản dài.
- **Đồng bộ chuẩn xác tốc độ giọng đọc**: Khắc phục lỗi lệch tốc độ giọng lồng tiếng giữa bảng chỉnh sửa câu và thanh Timeline.
- **Sửa lỗi xuất video có hiệu ứng**: Khắc phục lỗi xuất video khi áp dụng hiệu ứng làm mờ nền (Blur) hoặc các đoạn video có thay đổi tốc độ (Timewarp).
- **Chặn đè khối phụ đề trên Timeline**: Ngăn chặn việc kéo phụ đề vào khoảng trống nhỏ hơn độ dài câu gây đè nát câu bên cạnh.
- **Tắt hoàn toàn tác vụ ngầm khi đóng app**: Đảm bảo dọn dẹp sạch sẽ toàn bộ các tiến trình xử lý nền khi tắt phần mềm, tránh chiếm dụng tài nguyên máy tính.
