import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

try:
    from underthesea import word_tokenize as uts_word_tokenize
except Exception:
    uts_word_tokenize = None


@dataclass
class KeywordMatch:
    text: str
    start: int
    end: int
    score: float


STOP_WORDS = {
    "ạ", "ai", "anh", "ấy", "à", "ba", "bạn", "bị", "bởi", "cả", "các", "cái",
    "cần", "càng", "chỉ", "cho", "chưa", "có", "con", "của", "cùng", "đã", "đang",
    "đây", "đấy", "để", "đến", "đi", "đó", "được", "gần", "gồm", "hay", "hơn",
    "hôm", "ít", "kia", "khi", "không", "là", "lại", "làm", "lên", "lúc", "mà",
    "mình", "mỗi", "một", "năm", "này", "nên", "nếu", "ngay", "người", "nhất",
    "nhiều", "như", "những", "nhưng mà", "nhẹ", "nữa", "ở", "qua", "ra", "rằng",
    "rất", "rồi", "sau", "sẽ", "tại", "the", "thế", "theo", "thì", "trên", "trong",
    "từ", "từng", "và", "vẫn", "vào", "về", "vì", "với", "vừa", "vậy", "yêu",
}

POWER_WORDS = {
    "bán chạy", "bật", "bền", "bền bỉ", "bền hơn", "bí quyết", "camera", "cao cấp",
    "chất", "chất lượng", "chống ồn", "chuẩn", "cực", "cực mạnh", "dễ", "đẹp",
    "đẳng cấp", "độc quyền", "đơn giản", "game", "giảm", "giảm giá", "giá rẻ",
    "hiệu quả", "hot", "miễn phí", "mạnh", "mới", "mượt", "nổi bật", "ổn định",
    "pro", "rất tốt", "rẻ", "sạc nhanh", "sang trọng", "siêu mượt", "siêu nhanh",
    "siêu nét", "thông minh", "tiết kiệm", "tiện lợi", "trâu", "tự động", "ưu đãi",
    "vượt trội", "xu hướng", "xịn", "ấn tượng", "đáng mua", "đáng tiền", "gọn nhẹ",
}

IMPORTANT_PHRASES_COMMON = {
    "camera siêu nét",
    "chất lượng cao",
    "chống ồn",
    "cực kỳ mượt",
    "dễ làm",
    "dễ sử dụng",
    "đáng mua",
    "đáng tiền",
    "độ bền cao",
    "giảm chi phí",
    "hiệu năng mạnh mẽ",
    "hiệu quả hơn",
    "kết nối nhanh chóng",
    "màn hình sắc nét",
    "miễn phí",
    "nhanh hơn",
    "ổn định hơn",
    "pin trâu",
    "sạc nhanh",
    "siêu nhanh",
    "siêu nét",
    "sử dụng dễ dàng",
    "thiết kế sang trọng",
    "tiết kiệm thời gian",
    "tăng hiệu suất",
    "trải nghiệm mượt mà",
    "tự động",
    "vận hành ổn định",
}

IMPORTANT_PHRASES_BY_DOMAIN = {
    "education": {
        "3 bước",
        "5 bước",
        "áp dụng ngay",
        "bí quyết",
        "cách làm",
        "cần biết",
        "dễ hiểu",
        "ghi nhớ lâu",
        "học nhanh",
        "hướng dẫn chi tiết",
        "mẹo hay",
        "nguyên nhân",
        "quan trọng",
        "sai lầm",
        "thực hành",
        "trọng tâm",
    },
    "product": {
        "bền hơn",
        "camera siêu nét",
        "cao cấp",
        "chất lượng cao",
        "chơi game mượt mà",
        "chống ồn",
        "giá rẻ",
        "hiệu năng mạnh mẽ",
        "kết nối nhanh",
        "màn hình sắc nét",
        "mạnh mẽ",
        "nhẹ hơn",
        "pin trâu",
        "sạc nhanh",
        "thiết kế sang trọng",
        "tiết kiệm thời gian",
        "đáng mua",
        "đáng tiền",
    },
    "marketing": {
        "cơ hội cuối",
        "đừng bỏ lỡ",
        "giảm giá sâu",
        "miễn phí vận chuyển",
        "mua ngay",
        "quà tặng",
        "số lượng có hạn",
        "ưu đãi lớn",
    },
}

TOKEN_PATTERN = re.compile(r"\d+(?:[.,]\d+)?%?|[\w]+", re.UNICODE)
NUMERIC_PATTERN = re.compile(r"^\d+(?:[.,]\d+)?(?:%|k|tr|d|usd|vnd)?$", re.IGNORECASE)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_text(text: str) -> str:
    text = (text or "").lower().replace("_", " ")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


NORMALIZED_STOP_WORDS = {normalize_text(item) for item in STOP_WORDS}
NORMALIZED_POWER_WORDS = {normalize_text(item) for item in POWER_WORDS}


def _tokenize_fallback(text: str) -> List[str]:
    return [match.group(0) for match in TOKEN_PATTERN.finditer(text or "")]


def tokenize_vietnamese(text: str) -> List[str]:
    if uts_word_tokenize is not None:
        try:
            return [token.strip().replace("_", " ") for token in uts_word_tokenize(text or "") if token.strip()]
        except Exception:
            pass
    return _tokenize_fallback(text)


def build_token_spans(original_text: str, tokens: List[str]) -> List[tuple[int, int]]:
    spans = []
    cursor = 0

    for token in tokens:
        idx = original_text.find(token, cursor)
        if idx == -1:
            pattern = re.escape(token).replace(r"\ ", r"\s+")
            match = re.search(pattern, original_text[cursor:])
            if not match:
                continue
            idx = cursor + match.start()

        start = idx
        end = idx + len(token)
        spans.append((start, end))
        cursor = end

    return spans


def _phrase_library(domain: Optional[str] = None) -> set[str]:
    phrases = set(IMPORTANT_PHRASES_COMMON)
    if domain and domain in IMPORTANT_PHRASES_BY_DOMAIN:
        phrases.update(IMPORTANT_PHRASES_BY_DOMAIN[domain])
    phrases.update(word for word in POWER_WORDS if " " in word)
    return {normalize_text(item) for item in phrases}


def _score_phrase(normalized_phrase: str, raw_phrase: str, domain: Optional[str] = None) -> float:
    tokens = normalized_phrase.split()
    if not tokens:
        return -10.0
    if tokens[0] in NORMALIZED_STOP_WORDS or tokens[-1] in NORMALIZED_STOP_WORDS:
        return -5.0

    score = 0.0
    phrase_library = _phrase_library(domain)
    if normalized_phrase in phrase_library:
        score += 7.0

    if NUMERIC_PATTERN.fullmatch(tokens[0]) or any(NUMERIC_PATTERN.fullmatch(token) for token in tokens):
        score += 2.5

    joined = " ".join(tokens)
    compact = joined.replace(" ", "")
    if joined in NORMALIZED_POWER_WORDS or compact in {item.replace(" ", "") for item in NORMALIZED_POWER_WORDS}:
        score += 4.0

    meaningful = [token for token in tokens if token not in NORMALIZED_STOP_WORDS]
    score += len(meaningful) * 1.1
    score += min(len(normalized_phrase), 24) * 0.05

    if len(tokens) == 3:
        score += 1.8
    elif len(tokens) == 2:
        score += 1.2
    elif len(tokens) == 1 and len(tokens[0]) >= 5:
        score += 0.8

    if raw_phrase.isupper() and len(raw_phrase) > 1:
        score += 0.7

    return score


def _generate_candidate_keywords(text: str, domain: Optional[str] = None, max_keywords: int = 2) -> List[str]:
    tokens = tokenize_vietnamese(text)
    spans = build_token_spans(text, tokens)
    if not tokens or len(spans) != len(tokens):
        return []

    candidates: list[tuple[float, int, int, str]] = []
    for size in (3, 2, 1):
        for start_idx in range(len(tokens) - size + 1):
            phrase_tokens = tokens[start_idx:start_idx + size]
            normalized_phrase = normalize_text(" ".join(phrase_tokens))
            raw_phrase = text[spans[start_idx][0]:spans[start_idx + size - 1][1]]
            score = _score_phrase(normalized_phrase, raw_phrase, domain=domain)
            if score > 0:
                candidates.append((score, start_idx, start_idx + size - 1, raw_phrase))

    candidates.sort(key=lambda item: (item[0], len(item[3])), reverse=True)
    selected: list[tuple[int, int, str]] = []
    occupied = set()
    for _, start_idx, end_idx, raw_phrase in candidates:
        token_range = set(range(start_idx, end_idx + 1))
        if occupied.intersection(token_range):
            continue
        selected.append((start_idx, end_idx, raw_phrase))
        occupied.update(token_range)
        if len(selected) >= max_keywords:
            break

    return [item[2] for item in sorted(selected, key=lambda row: row[0])]


def find_keyword_token_matches(text_tokens: List[str], keywords: List[str]) -> List[tuple[int, int, str]]:
    normalized_text_tokens = [normalize_text(token) for token in text_tokens]
    matches = []

    for keyword in keywords:
        kw_tokens = tokenize_vietnamese(keyword)
        normalized_kw_tokens = [normalize_text(token) for token in kw_tokens]
        if not normalized_kw_tokens:
            continue

        kw_len = len(normalized_kw_tokens)
        for index in range(len(normalized_text_tokens) - kw_len + 1):
            if normalized_text_tokens[index:index + kw_len] == normalized_kw_tokens:
                matches.append((index, index + kw_len - 1, keyword))

    return matches


def merge_overlapping_matches(matches: List[tuple[int, int, str]]) -> List[tuple[int, int, str]]:
    matches = sorted(matches, key=lambda item: (item[0], -(item[1] - item[0])))
    merged = []
    occupied = set()

    for start_idx, end_idx, keyword in matches:
        token_range = set(range(start_idx, end_idx + 1))
        if occupied.intersection(token_range):
            continue
        merged.append((start_idx, end_idx, keyword))
        occupied.update(token_range)

    return merged


def auto_select_matches(text: str, domain: Optional[str] = None, max_keywords: int = 2) -> List[KeywordMatch]:
    text_tokens = tokenize_vietnamese(text)
    token_spans = build_token_spans(text, text_tokens)
    if not text_tokens or len(token_spans) != len(text_tokens):
        return []

    keywords = _generate_candidate_keywords(text, domain=domain, max_keywords=max_keywords)
    raw_matches = find_keyword_token_matches(text_tokens, keywords)
    matches = merge_overlapping_matches(raw_matches)

    output: List[KeywordMatch] = []
    for start_token, end_token, _keyword in matches:
        char_start = token_spans[start_token][0]
        char_end = token_spans[end_token][1]
        snippet = text[char_start:char_end]
        output.append(
            KeywordMatch(
                text=snippet,
                start=char_start,
                end=char_end,
                score=float(len(snippet)),
            )
        )

    return output
