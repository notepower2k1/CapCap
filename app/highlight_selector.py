import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class HighlightCandidate:
    text: str
    start: int
    end: int
    score: float


STOP_WORDS = {
    "la",
    "va",
    "cua",
    "mot",
    "nhung",
    "cac",
    "da",
    "dang",
    "thi",
    "ma",
    "o",
    "trong",
    "cho",
    "voi",
    "ve",
    "nay",
    "kia",
    "do",
    "duoc",
    "co",
    "bi",
    "tu",
    "khi",
    "nen",
    "rat",
    "hay",
    "di",
    "den",
    "ra",
    "vao",
    "theo",
    "nhu",
    "lai",
    "chi",
    "van",
    "con",
    "nua",
    "de",
}

POWER_WORDS = {
    "nhanh",
    "de",
    "mien",
    "phi",
    "tot",
    "nhat",
    "cuc",
    "ngay",
    "don",
    "gian",
    "hieu",
    "qua",
    "manh",
    "xin",
    "nhe",
    "ben",
    "tien",
    "muot",
    "hot",
    "chuan",
    "re",
}

IMPORTANT_PHRASES_COMMON = [
    "tiet kiem thoi gian",
    "chong on",
    "mien phi",
    "de su dung",
    "tu dong",
    "hieu qua hon",
    "sac nhanh",
    "pin lau",
    "giam chi phi",
    "tang hieu suat",
]

IMPORTANT_PHRASES_BY_DOMAIN = {
    "education": [
        "3 buoc",
        "5 buoc",
        "sai lam",
        "bi quyet",
        "nguyen nhan",
        "cach lam",
        "meo hay",
        "quan trong",
    ],
    "product": [
        "chong on",
        "pin lau",
        "sac nhanh",
        "nhe hon",
        "ben hon",
        "gia re",
        "cao cap",
        "tiet kiem thoi gian",
    ],
}

TOKEN_PATTERN = re.compile(r"\d+(?:[.,]\d+)?%?|\w+", flags=re.UNICODE)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    return _strip_accents(text)


def _normalized_with_mapping(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    mapping: list[int] = []
    last_was_space = False

    for index, char in enumerate(text):
        folded = _strip_accents(char.lower())
        for folded_char in folded:
            if folded_char.isspace():
                if normalized_chars and not last_was_space:
                    normalized_chars.append(" ")
                    mapping.append(index)
                last_was_space = True
                continue

            normalized_chars.append(folded_char)
            mapping.append(index)
            last_was_space = False

    while normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        mapping.pop()

    return "".join(normalized_chars), mapping


def score_word(word: str) -> float:
    if word in STOP_WORDS:
        return -10.0

    if re.fullmatch(r"\d+([.,]\d+)?(%|k|tr|d|usd|vnd)?", word, flags=re.IGNORECASE):
        return 5.0

    if word in POWER_WORDS:
        return 4.0

    if len(word) >= 8:
        return 2.0
    if len(word) >= 6:
        return 1.0

    return 0.0


def get_phrases(domain: Optional[str] = None) -> List[str]:
    phrases = list(IMPORTANT_PHRASES_COMMON)
    if domain and domain in IMPORTANT_PHRASES_BY_DOMAIN:
        phrases.extend(IMPORTANT_PHRASES_BY_DOMAIN[domain])
    return sorted(set(phrases), key=len, reverse=True)


def _candidate_from_normalized_span(
    original_text: str,
    normalized_mapping: list[int],
    start: int,
    end: int,
    score: float,
) -> HighlightCandidate:
    original_start = normalized_mapping[start]
    original_end = normalized_mapping[end - 1] + 1
    snippet = original_text[original_start:original_end]
    return HighlightCandidate(
        text=snippet,
        start=original_start,
        end=original_end,
        score=score,
    )


def find_phrase_candidates(text: str, domain: Optional[str] = None) -> List[HighlightCandidate]:
    normalized, mapping = _normalized_with_mapping(text)
    if not normalized:
        return []

    phrases = [normalize_text(phrase) for phrase in get_phrases(domain)]
    candidates: List[HighlightCandidate] = []

    for phrase in phrases:
        start = 0
        while True:
            idx = normalized.find(phrase, start)
            if idx == -1:
                break
            candidates.append(
                _candidate_from_normalized_span(
                    text,
                    mapping,
                    idx,
                    idx + len(phrase),
                    score=6.0 + len(phrase) * 0.01,
                )
            )
            start = idx + 1

    return candidates


def find_word_candidates(text: str) -> List[HighlightCandidate]:
    candidates: List[HighlightCandidate] = []

    for match in TOKEN_PATTERN.finditer(text):
        word = match.group(0).strip()
        if not word:
            continue
        score = score_word(normalize_text(word))
        if score > 0:
            candidates.append(
                HighlightCandidate(
                    text=word,
                    start=match.start(),
                    end=match.end(),
                    score=score,
                )
            )

    return candidates


def overlaps(a: HighlightCandidate, b: HighlightCandidate) -> bool:
    return not (a.end <= b.start or a.start >= b.end)


def select_best_candidates(
    candidates: List[HighlightCandidate],
    max_highlights: int = 2,
) -> List[HighlightCandidate]:
    candidates = sorted(
        candidates,
        key=lambda c: (c.score, c.end - c.start),
        reverse=True,
    )

    selected: List[HighlightCandidate] = []
    for candidate in candidates:
        if any(overlaps(candidate, existing) for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= max_highlights:
            break

    return sorted(selected, key=lambda c: c.start)


def find_highlights(
    text: str,
    domain: Optional[str] = None,
    max_highlights: int = 2,
) -> List[HighlightCandidate]:
    phrase_candidates = find_phrase_candidates(text, domain=domain)
    word_candidates = find_word_candidates(text)
    all_candidates = phrase_candidates + word_candidates
    return select_best_candidates(all_candidates, max_highlights=max_highlights)
