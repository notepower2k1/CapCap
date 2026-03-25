from dataclasses import dataclass
from typing import List, Optional

from new_highlight_selector import auto_select_matches


@dataclass
class HighlightCandidate:
    text: str
    start: int
    end: int
    score: float


def find_highlights(
    text: str,
    domain: Optional[str] = None,
    max_highlights: int = 2,
) -> List[HighlightCandidate]:
    matches = auto_select_matches(text or "", domain=domain, max_keywords=max_highlights)
    return [
        HighlightCandidate(
            text=match.text,
            start=match.start,
            end=match.end,
            score=match.score,
        )
        for match in matches
    ]
