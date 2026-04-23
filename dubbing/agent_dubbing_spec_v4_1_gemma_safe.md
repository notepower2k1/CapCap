# AI Agent Spec v4.1 - Chinese -> Vietnamese Dubbing with Duration Control (Gemma-safe)

## Mục tiêu
Tạo `dubbing_vi` và `subtitle_vi` từ subtitle/audio tiếng Trung, sao cho:
- `dubbing_vi` phù hợp để TTS đọc kịp timeline gốc
- `subtitle_vi` dễ đọc trên màn hình
- hạn chế tối đa việc TTS tiếng Việt bị chậm hơn video gốc
- giữ pipeline lean, ổn định, phù hợp với model nhỏ như Gemma 4B

Bản v4.1 giữ triết lý của v4 nhưng thêm guardrail ngoài model để:
- giảm retry vô ích
- tăng tỷ lệ pass ngay từ lần TTS đầu
- xử lý tốt hơn segment ngắn và câu khó đọc

---

## Triết lý vận hành
Pipeline đúng:

```text
Duration-aware translation -> Validate -> TTS -> Measure -> Rewrite if needed -> Slight trim/speed
```

Không dùng cách:

```text
Translate freely -> TTS -> Speed up aggressively
```

---

## Đầu vào mỗi segment
- `index`
- `start_time`
- `end_time`
- `duration_sec`
- `source_text`
- `context_prev` (optional)
- `context_next` (optional)

---

## Output mong muốn mỗi segment
```json
{
  "index": 1,
  "source_text": "...",
  "subtitle_vi": "...",
  "dubbing_vi": "...",
  "duration_sec": 2.4,
  "max_words_vi": 9,
  "speech_cost": 3,
  "tts_duration": 2.2,
  "ratio": 0.92,
  "attempt_count": 1,
  "action_taken": "accept"
}
```

---

## 5 nguyên tắc chốt cho v4.1
1. **Giữ prompt ngắn** để Gemma 4B làm tốt hơn.
2. **Ưu tiên text fit trước** rồi mới tối ưu audio fit.
3. **Dùng `speech_cost` ngoài model** để giảm word budget khi câu khó đọc.
4. **Rewrite sớm hơn với segment ngắn** và dùng `keyword_only` với segment cực ngắn.
5. **Retry theo cấp độ**: lần sau phải nén mạnh hơn lần trước, không lặp compress nhẹ.

---

## Công thức cơ bản

### 1) Số từ tiếng Việt tối đa
Mặc định:
```text
base_words = floor(duration_sec * 4.5)
```

Điều chỉnh theo độ khó phát âm:
```text
if speech_cost >= 3:
    max_words_vi = floor(duration_sec * 4.0)
else:
    max_words_vi = floor(duration_sec * 4.5)
```

Gợi ý:
- chậm, rõ: `4.0`
- bình thường: `4.5`
- hơi nhanh: `5.0`

### 2) Tỷ lệ lệch sau TTS
```text
ratio = tts_duration / duration_sec
```

---

## Heuristic `speech_cost`
Mục tiêu: phát hiện các câu tuy không quá dài nhưng vẫn dễ bị TTS đọc chậm.

Khởi tạo:
```text
speech_cost = 0
```

Cộng điểm:
- `+2` nếu có số
- `+2` nếu có tên riêng dài / chữ Latin / từ viết hoa / ký hiệu đặc biệt
- `+1` nếu có nhiều dấu phẩy hoặc nhiều nhịp ngắt
- `+1` nếu có nhiều từ dài / khó đọc liên tiếp
- `+1` nếu có nhiều từ có từ 2 âm tiết trở lên
- `+1` nếu câu chứa thuật ngữ / cụm khó đọc

Chia nhanh:
- `0-1` -> thấp
- `2-3` -> trung bình
- `>=4` -> cao

Nếu `speech_cost` cao, ưu tiên rewrite sớm hơn.

---

## Rule cho segment cực ngắn
Nếu segment quá ngắn, không cố dịch đủ câu.

```text
if duration_sec < 0.8:
    dubbing_vi = keyword_only(source_text)
```

`keyword_only`:
- chỉ giữ 1-2 từ quan trọng nhất
- ưu tiên noun / verb / emphasis
- bỏ cấu trúc câu đầy đủ

Ví dụ:
```text
这个问题非常重要 -> Quan trọng
马上开始 -> Bắt đầu
快跑 -> Chạy đi
```

---

## Ngưỡng quyết định
### Mặc định
- `ratio <= 1.05` -> dùng luôn
- `1.05 < ratio <= 1.15` -> trim silence hoặc speed nhẹ
- `1.15 < ratio <= 1.30` -> rewrite rồi TTS lại
- `ratio > 1.30` -> rewrite mạnh hơn, không chỉ tăng speed

### Với segment ngắn
- nếu `duration_sec < 1.8` và `ratio > 1.10` -> rewrite sớm
- nếu `duration_sec < 1.2` và `ratio > 1.05` -> ưu tiên rewrite luôn
- nếu `duration_sec < 0.8` -> ưu tiên `keyword_only`, không cố cứu bằng speed

### Nếu `speech_cost` cao
- nếu `speech_cost >= 4` và `ratio > 1.08` -> nghiêng về rewrite thay vì speed
- nếu `speech_cost >= 3` -> dùng `max_words_vi` chặt hơn ngay từ đầu

---

## Giới hạn speed TTS
- lý tưởng: `1.00 - 1.15`
- chấp nhận: `1.15 - 1.25`
- tối đa khuyến nghị: `1.30`
- nếu cần `> 1.30`, xem như câu dịch chưa phù hợp

---

## Prompt cho Gemma 4B

### Prompt dịch chính (`dubbing_vi`)
```text
Dịch câu tiếng Trung sang tiếng Việt để lồng tiếng video.

- Giữ ý chính
- Câu ngắn, dễ đọc
- Phải đọc kịp trong {duration_sec} giây
- Tối đa {max_words_vi} từ
- Ưu tiên ngắn gọn hơn dịch sát chữ

Chỉ trả về 1 câu tiếng Việt.

Câu gốc:
{source_text}
```

### Prompt rút gọn nhẹ (`compress_light`)
```text
Rút gọn câu tiếng Việt sau để đọc nhanh hơn.

- Giữ ý chính
- Ngắn hơn rõ rệt
- Tối đa {max_words_vi} từ

Câu gốc:
{source_text}

Câu hiện tại:
{draft_vi}
```

### Prompt rút gọn mạnh (`compress_aggressive`)
```text
Rút gọn mạnh câu tiếng Việt sau để lồng tiếng kịp video.

- Chỉ giữ ý chính nhất
- Bỏ phần phụ, chủ ngữ nếu không cần
- Ưu tiên động từ và thông tin quan trọng
- Tối đa {max_words_vi} từ

Câu gốc:
{source_text}

Câu hiện tại:
{draft_vi}
```

> Không thêm prompt dài, không bắt model tự phân loại, không bắt model tự chấm điểm.

---

## Tách `subtitle_vi` và `dubbing_vi`
### `dubbing_vi`
- ưu tiên ngắn
- tối ưu để TTS đọc kịp
- có thể lược bớt phần thừa nếu ngữ cảnh đã rõ

### `subtitle_vi`
- ưu tiên dễ hiểu khi người xem đọc trên màn hình
- có thể đầy đủ hơn `dubbing_vi`

### Cách triển khai lean
- mặc định: `subtitle_vi = dubbing_vi`
- nếu segment bị nén mạnh hoặc dùng `keyword_only`, có thể tạo `subtitle_vi` đầy đủ hơn từ `source_text`
- nếu chưa muốn tăng cost, vẫn có thể giữ `subtitle_vi = dubbing_vi`

Khuyến nghị:
```text
if action_taken in ["compress_aggressive", "keyword_only"]:
    subtitle_vi = maybe_fuller_subtitle(source_text, dubbing_vi)
else:
    subtitle_vi = dubbing_vi
```

---

## Rewrite strategy
### Ưu tiên bỏ
1. trạng từ (rất, khá, thực sự...)
2. chủ ngữ nếu ngữ cảnh đã rõ
3. cụm giải thích
4. từ filler / lặp ý
5. phần lịch sự không quan trọng với timing

### Ưu tiên giữ
1. động từ chính
2. danh từ chính
3. thông tin mới
4. punchline / emphasis nếu có

---

## Quy trình xử lý cho từng segment
### Bước 1: Chuẩn bị
Tính:
- `duration_sec`
- `speech_cost`
- `max_words_vi`

### Bước 2: Sinh `dubbing_vi`
- nếu `duration_sec < 0.8` -> dùng `keyword_only`
- ngược lại dùng prompt dịch chính để tạo câu ngắn cho TTS

### Bước 3: Validate
Kiểm tra:
- có đúng 1 câu không
- có vượt `max_words_vi` không

Nếu vượt -> dùng `compress_light`.

### Bước 4: TTS
Sinh audio từ `dubbing_vi`.

### Bước 5: Đo thời lượng
Tính:
```text
ratio = tts_duration / duration_sec
```

### Bước 6: Quyết định xử lý
#### Case A: `ratio <= 1.05`
- accept

#### Case B: `1.05 < ratio <= 1.15`
- nếu segment ngắn hoặc `speech_cost` cao -> rewrite
- ngược lại -> trim silence, speed nhẹ nếu cần

#### Case C: `1.15 < ratio <= 1.30`
- rewrite
- TTS lại
- chỉ speed nhẹ nếu rewrite xong vẫn hơi dài

#### Case D: `ratio > 1.30`
- rewrite mạnh hơn
- không dùng speed là giải pháp chính

---

## Retry policy
- tối đa `2` lần TTS cho segment thường
- tối đa `3` lần nếu segment ngắn hoặc `speech_cost` cao
- mỗi lần retry sau phải nén mạnh hơn lần trước
- không để loop không giới hạn

Chiến lược:
- `attempt 1` -> dịch bình thường / compress nhẹ
- `attempt 2` -> `compress_aggressive`
- `attempt 3` -> `keyword_only` hoặc cực ngắn nếu thật sự cần

---

## Pseudocode
```text
for each segment:
    duration_sec = end_time - start_time
    speech_cost = estimate_speech_cost(source_text)

    if duration_sec < 0.8:
        dubbing_vi = keyword_only(source_text)
        retry_cap = 2
    else:
        if speech_cost >= 3:
            max_words_vi = floor(duration_sec * 4.0)
        else:
            max_words_vi = floor(duration_sec * 4.5)

        dubbing_vi = translate_for_dubbing(source_text, duration_sec, max_words_vi)

        if word_count(dubbing_vi) > max_words_vi:
            dubbing_vi = compress_light(source_text, dubbing_vi, max_words_vi)

        retry_cap = 2
        if duration_sec < 1.8 or speech_cost >= 4:
            retry_cap = 3

    attempt = 1
    while attempt <= retry_cap:
        audio = tts(dubbing_vi)
        tts_duration = measure(audio)
        ratio = tts_duration / duration_sec

        if ratio <= 1.05:
            action_taken = "accept"
            break

        if duration_sec < 0.8:
            action_taken = "keyword_only"
            break

        if attempt == 1:
            dubbing_vi = compress_light(source_text, dubbing_vi, max_words_vi)
            action_taken = "compress_light"
            attempt += 1
            continue

        if attempt == 2:
            dubbing_vi = compress_aggressive(source_text, dubbing_vi, max_words_vi)
            action_taken = "compress_aggressive"
            attempt += 1
            continue

        dubbing_vi = keyword_only(source_text)
        action_taken = "keyword_only"
        attempt += 1

    if action_taken in ["compress_aggressive", "keyword_only"]:
        subtitle_vi = maybe_fuller_subtitle(source_text, dubbing_vi)
    else:
        subtitle_vi = dubbing_vi
```

---

## Trách nhiệm của AI Agent
AI Agent phải:
1. Luôn dùng `duration_sec` để giới hạn độ dài câu.
2. Dùng prompt ngắn, không nhồi nhiều rule vào prompt.
3. Dùng `speech_cost` như guardrail ngoài model.
4. Giảm `max_words_vi` khi câu khó đọc.
5. Rewrite sớm hơn với segment ngắn.
6. Dùng `keyword_only` cho segment cực ngắn khi cần.
7. Retry theo cấp độ, không lặp compress nhẹ.
8. Không dùng speed > `1.30` như cách cứu mặc định.
9. Hỗ trợ tách `subtitle_vi` và `dubbing_vi`.
10. Log lại:
   - `duration_sec`
   - `max_words_vi`
   - `speech_cost`
   - `dubbing_vi`
   - `subtitle_vi`
   - `tts_duration`
   - `ratio`
   - `attempt_count`
   - `action_taken`

---

## Kết luận vận hành
v4.1 là bản lean hơn nhưng ổn định hơn cho Gemma 4B:
- prompt ngắn
- logic ngoài model nhiều hơn
- ít retry vô ích hơn
- xử lý tốt hơn vấn đề TTS chậm hơn video
- giữ được pipeline đơn giản để triển khai production

Pipeline khuyến nghị:

```text
Short prompt -> TTS -> Measure -> Early rewrite for short/hard segments -> Aggressive rewrite if needed -> Slight speed only when safe
```
