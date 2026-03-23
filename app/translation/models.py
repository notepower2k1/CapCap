from dataclasses import dataclass, field


@dataclass
class TranslationResult:
    success: bool
    segments: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stage: str = ""
    primary_provider: str = ""
    polish_provider: str = ""
    used_fallback: bool = False

    @property
    def text(self) -> str:
        return "\n".join(seg.get("text", "") for seg in self.segments)
