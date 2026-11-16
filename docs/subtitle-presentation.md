# Chế độ phụ đề trên video

Trong khung **Phụ đề** riêng của tab **Ngôn ngữ**, chọn một trong ba chế độ:

| Chế độ | Preview và video xuất | Dữ liệu xử lý |
| --- | --- | --- |
| Không phụ đề | Không hiện/burn phụ đề do CapCap tạo | Vẫn giữ STT/OCR, dịch, SRT và TTS |
| Có phụ đề | Như các phiên bản trước | TS1 và giọng đọc chính |
| Song ngữ | Hiện/burn phụ đề chính và phụ | TS1 và TS2, TTS chỉ lấy từ phụ đề chính |

Project cũ mặc định **Có phụ đề**. Chuyển chế độ không xóa câu phụ đề, giọng đọc hoặc kiểu đã chỉnh. Chế độ hiển thị được lưu theo project.

## Phụ đề phụ

Khi chọn Song ngữ, chọn nguồn:

- **Phụ đề gốc:** sao chép bản STT/OCR có sẵn thành dữ liệu TS2 độc lập, không gọi dịch thêm. Nếu chưa có, nút tạo dùng luồng nhận dạng hiện tại để tạo bản gốc trước. Chọn hoặc sửa TS1 không thay bản sao này; chỉ nút tạo/cập nhật mới lấy lại bản gốc khi người dùng yêu cầu.
- **Nhập SRT:** chọn file; nội dung được chép vào dữ liệu project và lưu SRT nội bộ để không phụ thuộc file gốc sau khi nhập.
- **Dịch sang ngôn ngữ khác:** chọn ngôn ngữ rồi bấm **Tạo / Cập nhật phụ đề phụ**. Dùng cùng cơ chế/provider dịch hiện có, lấy nội dung bản gốc làm nguồn và giữ mốc thời gian nguồn. Không tạo TTS thứ hai.

### Prompt dịch phụ riêng

Nguồn **Dịch sang ngôn ngữ khác** giữ ngôn ngữ đích và nút Tạo/Cập nhật trong tab Ngôn ngữ. Bộ chọn preset và ô prompt chỉ nằm trong popup **Translation Settings & Prompt Review** mở trước khi dịch. Popup điền lại preset/prompt phụ đã lưu theo project; để prompt tùy chỉnh trống thì dùng mẫu đã chọn. Có thể dùng `{{source_lang}}`, `{{target_lang}}` và `{{context_guidance}}`.

Prompt và chỉ dẫn phong cách của phụ đề chính không tự áp dụng cho bản phụ. Provider/model vẫn dùng chung; prompt chỉ có hiệu lực với dịch AI, không thay đổi cách Google/Bing dịch web. Danh sách có đầy đủ preset giống phụ đề chính và không tự đổi preset khi chuyển ngôn ngữ. Các mẫu thể loại có quy tắc tiếng Việt áp dụng khi đích là tiếng Việt; với đích khác, bộ render prompt hiện có dùng hợp đồng dịch trung tính, hoặc có thể nhập prompt riêng.

Lựa chọn prompt phụ được lưu theo project và đi cùng cấu hình hàng đợi. Prompt được truyền theo tác vụ, kể cả các batch song song, không sửa biến môi trường/prompt chính. Khi nguồn hoặc prompt thay đổi, giao diện nhắc cập nhật bản dịch; xuất vẫn dùng bản phụ đề phụ đã lưu, và không tự gọi AI hoặc ghi đè nội dung đã có.

Khi chạy toàn bộ luồng hoặc hàng đợi Performance, việc chuẩn bị phụ đề phụ tự thực hiện sau khi đã có STT/OCR/bản dịch chính. Chỉ thêm song ngữ sau khi xử lý xong video thì dùng nút tạo/cập nhật; không phải chạy lại TTS hoặc toàn bộ luồng.

File SRT phụ không cần cùng số câu với TS1. Hai tập câu hiển thị theo timestamp riêng, có thể chồng thời gian. SRT nhập dùng thời gian video nguồn. Fit Voice ánh xạ phụ đề phụ sang timeline cùng các thay đổi chậm/giữ hình; không ghép theo chỉ số câu.

Tab Ngôn ngữ chỉ giữ cấu hình chế độ, nguồn, ngôn ngữ đích, nhập/tạo/cập nhật và trạng thái; không hiển thị danh sách câu phụ hoặc phần sửa prompt trùng với popup. Chọn câu TS2 trên timeline mở trang inspector phụ riêng, giữ nguyên tab bên trái. Hai inspector dùng chung thành phần sửa chữ/highlight nhưng nguồn dữ liệu, câu đang chọn và thao tác lưu tách biệt. Số thứ tự và thời lượng hiển thị dạng badge; mốc bắt đầu/kết thúc dùng cùng kiểu badge với inspector chính. Không có nút Previous/Next; chọn câu trực tiếp trên timeline. Sửa chữ/highlight trong inspector, sửa thời gian qua timeline hoặc mở **Subtitle Editor** để chỉnh cả danh sách. Inspector phụ còn có nhập SRT và tạo/cập nhật; không tạo thêm giọng đọc. Timeline hiển thị nội dung câu thay cho tên đánh số Secondary. Kéo thời gian, tách, xóa và undo/redo TS2 không chỉnh TS1 hay TTS. Ngay cả khi nguồn là Phụ đề gốc, nội dung chỉnh vẫn nằm trong bản sao TS2 và không sửa transcript gốc.

Phụ đề phụ và chữ ký nguồn được lưu trong `settings.subtitle_presentation`; bản SRT ở `subtitle/secondary.srt` và artifact `subtitle_secondary_srt`. Khi đổi project, nội dung không bị mang sang video khác.

## Kiểu hiển thị

Trong bảng **Kiểu phụ đề**, bộ chọn **Kiểu phụ đề chính / Kiểu phụ đề phụ** dùng chung các điều khiển hiện có nhưng giữ cấu hình riêng: font, cỡ chữ, màu, viền, nền, animation và vị trí.

- Chính: giữ kiểu cũ, thường lớn hơn và ở dưới.
- Phụ: mặc định khoảng 75% cỡ chữ chính, đứng trên và dùng animation Static.
- **Sao chép kiểu phụ đề chính:** chép kiểu sang phụ, giữ vị trí phụ để tránh đặt hai lớp vào cùng một chỗ.
- **Đổi vị trí hai phụ đề:** hoán đổi vị trí của hai bên.

Kiểu chỉnh được nhớ độc lập làm mặc định cho project mới. Khi mở project có kiểu riêng thì ưu tiên project, không ghi đè cache chỉ vì mở video đó. Tắt song ngữ vẫn giữ kiểu phụ. Chế độ không phụ đề vô hiệu hóa bảng kiểu, không xóa cấu hình.

Thay kiểu chính không tự thay kiểu phụ đã lưu. Vì vậy kiểu phụ có thể lớn hơn kiểu chính sau khi người dùng thu nhỏ chữ chính; đây là hai cấu hình độc lập. Có thể dùng nút sao chép kiểu chính khi muốn hai bên giống nhau.

Khi dừng video, chọn câu TS1 hoặc TS2 trên timeline rồi kéo trên preview để đổi vị trí riêng của track đó. Trong lúc kéo, lớp được chọn dùng Qt để phản hồi tức thì, lớp còn lại vẫn hiển thị; kết thúc kéo thì lưu vị trí về đúng bộ kiểu và khôi phục ASS đầy đủ. Phát video, khóa track hoặc bỏ chọn sẽ tắt kéo thả. Preview MPV, Performance và Qt đều dùng cùng cơ chế chọn nguồn; thao tác kéo không tự thay đổi cỡ chữ hay kiểu mặc định.

Timeline vẫn giữ TS1 khi chọn Không phụ đề, với nhãn **Ẩn trên video**, để chỉnh nội dung/tạo TTS. TS2 chỉ hiện ở chế độ Song ngữ. Preview trực tiếp MPV dùng một ASS chứa hai bộ style/event độc lập; preview tĩnh/Qt dùng các lớp hiển thị tương ứng. Fast Preview và xem khung hình cũng áp dụng chế độ đã chọn.

TS1 giữ màu cam mặc định trên timeline (hoặc màu người nói khi đang dùng phân vai); TS2 có màu xanh ngọc thống nhất với nhãn track và viền chọn sáng. Màu timeline không đổi màu chữ trên video. Layer bị khóa có nền gạch chéo, viền vàng và badge ổ khóa; khóa cả track áp dụng cách hiển thị này cho mọi layer trong track. Layer khóa đang chọn vẫn có viền sáng để phân biệt với các layer khóa khác. Nhãn track khóa được tô nền và ô ổ khóa rõ hơn. Đây là dấu hiệu giao diện, không đổi trạng thái xuất/ẩn nội dung.

Màu người nói trên timeline và chấm nhận diện trong inspector/danh sách người nói dùng chung bảng màu, chừa dải xanh ngọc/cyan cho TS2. Màu ổn định theo ID người nói khi đổi project hoặc mở lại ứng dụng. Quy tắc màu chữ người nói trên video/ASS/CapCut giữ nguyên, tách khỏi bảng màu của giao diện.

Nhãn track chỉ hiện tên ngắn: Video, Âm thanh gốc, Nhạc nền, Phụ đề chính/phụ, Blur, Logo, Mask, Text (theo ngôn ngữ giao diện). Các tiền tố V1/A1/TS1/TS2 và ký hiệu lặp trước tên đã bỏ khỏi cột nhãn; ID nội bộ, nút mute/ẩn/khóa và cơ chế export giữ nguyên.

Khi Play, inspector tự theo câu đang phát của track đang xem: phụ đề chính hoặc phụ đề phụ. TS2 dùng mốc thời gian riêng, không phụ thuộc ranh giới câu TS1. Khi Pause hoặc playhead nằm giữa các câu, giữ câu đang chọn. Chỉ đổi nội dung inspector khi sang câu khác; không lưu project hay dựng lại timeline theo từng nhịp phát.

Khi bấm **Tạo / Cập nhật phụ đề phụ** với nguồn dịch, ứng dụng mở lại màn **Translation Settings & Prompt Review** như phụ đề chính: dịch vụ theo Settings, ngôn ngữ đích/preset/prompt lấy từ TS2. Xác nhận nhận dạng nhân vật/xưng hô dùng transcript gốc và ngôn ngữ đích của TS2. Preset và prompt đã duyệt chỉ lưu vào cấu hình phụ đề phụ, không thay preset chính.

Popup dịch chính và phụ dùng chung `TranslationProgressDialog`, hiện dịch vụ, số câu hoàn thành/tổng số, phần trăm, thời gian và lý do fallback. Tiến độ đếm các câu đã nhận, không cộng trùng khi retry hoặc batch song song. Hủy gửi yêu cầu dừng, bỏ kết quả về muộn và giữ nguyên TS2 cũ; chỉ cho chạy lần mới sau khi worker cũ kết thúc. Lỗi được ghi log kèm nguồn, ngôn ngữ và dịch vụ; kết quả về sau khi đổi project/cấu hình bị bỏ qua. Không chạy dịch chính và dịch phụ thủ công đồng thời.

## Khôi phục thời gian sau khi chạy luồng

Popup dịch có thư viện preset cá nhân: chỉnh prompt rồi chọn **Lưu preset**, đặt tên; chọn preset đã lưu để dùng lại hoặc **Xóa preset** khi không cần. Thư viện dùng chung cho phụ đề chính/phụ và lưu trong cài đặt ứng dụng; áp dụng vào project khi xác nhận dịch. Prompt mẫu chưa chỉnh được lưu dưới dạng lựa chọn preset để tự dựng lại theo ngôn ngữ mới, custom prompt giữ nguyên nội dung và hỗ trợ placeholder ngôn ngữ. Mỗi yêu cầu AI đều được bổ sung ngôn ngữ đích, định dạng dòng đánh số và yêu cầu giữ đủ câu, kể cả khi custom prompt không viết các phần này.

Hai inspector có nút **Khôi phục thời gian**. Chọn câu rồi bấm để trả lại **cả bắt đầu và kết thúc** như sau lần chạy luồng thành công gần nhất, giữ nội dung và kiểu chữ đã chỉnh. Mốc được cập nhật khi luồng hoàn tất thành công và lưu cùng project; chỉnh tay, Undo/Redo hoặc chạy lỗi không ghi đè mốc đó. Hàng đợi Performance cũng lưu mốc sau khi xử lý xong; chỉ export lại không đổi mốc.

Khôi phục câu chính không đổi TS2; khôi phục TS2 không đổi câu chính. Thao tác có Undo/Redo, không chạy khi đang phát video hoặc khi layer/track bị khóa. Khi video còn cùng cấu hình Auto Fit thì khôi phục đúng timestamp đã lưu; nếu sau đó đổi time warp, mốc nguồn được ánh xạ sang timeline hiện tại, không tự hủy các chỉnh sửa video. Câu mới sinh ra do tách thủ công không nhận nhầm toàn bộ thời lượng của câu cha.

Với project cũ chưa có mốc này, chỉ phục hồi từ artifact TTS khi xác định được đúng một bản ghi theo đường dẫn WAV và nội dung. Nếu thiếu dữ liệu hoặc có nhiều bản ghi dùng chung WAV, nút sẽ vô hiệu hóa và tooltip thông báo chưa có mốc, thay vì đoán thời gian cũ. Tạo/xử lý lại phụ đề thành công sẽ ghi mốc mới.

## Sẵn sàng xuất và cập nhật

Nếu phụ đề phụ đang tạo, còn thiếu hoặc bản dịch không khớp chữ ký nguồn hiện tại, thao tác xuất báo rõ để cập nhật trước. Không âm thầm xuất song ngữ thiếu một bên. Sửa phụ đề chính không ghi đè bản phụ; giao diện có thông báo cần kiểm tra/cập nhật. Kết quả dịch sai số câu hoặc rỗng là lỗi, không coi là thành công.

Thông báo lỗi tạo phụ đề phụ hiện trong giao diện và log `[Subtitles]`; luồng tự động ghi trạng thái bước `secondary_subtitle` khi chạy/thành công/thất bại. Không tự thay provider ngoài cơ chế fallback sẵn có của translator.

## Xuất project CapCut

- Không phụ đề: không tạo clip chữ phụ đề trong draft; vẫn giữ âm thanh/TTS và các layer khác.
- Phụ đề chính: như trước.
- Song ngữ: hai track chữ chỉnh sửa được, giữ thời gian và kiểu từng bên. Phụ đề không bị burn vào video nguồn trong draft.

## Kiểm chứng

- Bảng màu người nói dành riêng tông TS2: kiểm tra 1.000 ID số và các ID tùy chỉnh, màu bar/chấm nhận diện nhất quán, màu video cũ giữ nguyên. Bộ UI/timeline/preview/CapCut **57 tests pass**; preview/overlay Windows **19 tests pass** riêng.
- Inspector badge và tối ưu chọn câu: **102 tests + 17 subtests pass**, cùng **19 tests preview/overlay Windows pass** trong tiến trình riêng; đã kiểm tra ảnh render inspector phụ.
- Chỉ đổi lựa chọn TS1/TS2 không ghi project/SRT và không dựng lại track. Benchmark cùng mẫu 300 câu, 20 lượt chọn, có profiler: khoảng **98 → 13–14 ms/lượt**, số lần lưu/dựng track **20 → 0**. Cache thời gian được làm mới khi dữ liệu hoặc time warp thay đổi; bản sao dùng để chỉnh sửa không làm đổi cache. Sửa kiểu thực sự vẫn lưu khi rời bộ kiểu đó.
- Khôi phục kéo thả song ngữ: **33 tests phụ đề/UI + 19 tests preview/overlay Windows pass**. Kiểm tra chọn đúng bộ kiểu, giữ lớp còn lại khi kéo, chỉ nạp ASS ở đầu/cuối thao tác, khóa khi phát video/khóa track và tín hiệu kéo trên Qt fallback. Giữ nguyên cơ chế kiểu/cache độc lập.
- Sửa tách biệt TS1/TS2 và inspector phụ: **123 tests + 17 subtests pass**, cùng **19 tests preview/overlay Windows pass** trong tiến trình riêng. Các ca mới kiểm tra sự kiện chuột timeline không gửi nhầm index TS1, dữ liệu gốc chụp riêng, sửa chữ/highlight/thời gian TS2, preset đầy đủ, tách/xóa/undo/redo và giữ bản sao khi export.
- Lượt kiểm thử cuối gồm phụ đề, UI, prompt theo tác vụ, dịch song song/fallback, chẩn đoán, Fit Voice, xuất MP4/CapCut và hàng đợi: **139 tests và 38 subtests pass**.
- Bộ hồi quy UI/timeline/Fit Voice/MP4/CapCut/Audio Mix/Performance: **85 tests và 26 subtests pass**; sau đó thêm và chạy lại kiểm thử worker dịch, cache và cấu hình phụ đề. Bộ ASR wiring/resource packages: **35 tests pass**. Bộ phụ đề mới cùng i18n: **28 tests và 17 subtests pass**.
- Kiểm thử cấu hình mặc định/project cũ, nội dung/kiểu riêng, đổi project, cache, source signature, dịch bằng worker, SRT nội bộ và SRT khác số câu.
- Kiểm thử time mapping Fit Voice và ASS ghép hai bộ style; Fast Preview không dùng lại chữ cũ khi chọn Không phụ đề.
- Xuất MP4 thực tế bằng FFmpeg/NVENC với ba chế độ trên video tổng hợp 640×360: bản không phụ đề không có glyph; bản một ngôn ngữ chỉ có chữ chính ở dưới; bản song ngữ có thêm chữ phụ phía trên. Kiểm tra frame ảnh tại cùng thời điểm.
- Xuất draft CapCut thực tế: hai track chữ khi song ngữ, không có track chữ phụ đề khi chọn Không phụ đề.
- Hàng đợi chạy Prepare và export song ngữ từ transcript có sẵn, không gọi dịch phụ khi chọn nguồn bản gốc.

Chưa build installer cho thay đổi này. Các mẫu MP4/ảnh kiểm tra nằm trong `output/subtitle-presentation-smoke` tại máy phát triển.
