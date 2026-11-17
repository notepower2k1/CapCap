from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class LayerType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    DUB_SUBTITLE = "dub_subtitle"
    TEXT = "text"
    IMAGE = "image"
    STICKER = "sticker"
    BLUR = "blur"
    MASK = "mask"
    BACKGROUND = "background"


class BlendMode(str, Enum):
    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    DARKEN = "darken"
    LIGHTEN = "lighten"
    COLOR_DODGE = "color_dodge"
    COLOR_BURN = "color_burn"
    HARD_LIGHT = "hard_light"
    SOFT_LIGHT = "soft_light"
    DIFFERENCE = "difference"
    EXCLUSION = "exclusion"


@dataclass
class BaseLayer:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    type: LayerType = LayerType.VIDEO
    name: str = ""
    start: float = 0.0
    end: float = 0.0
    z_index: int = 0
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    blend_mode: BlendMode = BlendMode.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "z_index": self.z_index,
            "visible": self.visible,
            "locked": self.locked,
            "opacity": self.opacity,
            "blend_mode": self.blend_mode.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaseLayer:
        layer = cls(
            id=str(data.get("id", uuid4().hex[:12])),
            name=str(data.get("name", "")),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            z_index=int(data.get("z_index", 0)),
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            opacity=float(data.get("opacity", 1.0)),
            blend_mode=BlendMode(data.get("blend_mode", "normal")),
            metadata=dict(data.get("metadata", {}) or {}),
        )
        type_raw = data.get("type", None)
        if type_raw is not None:
            try:
                layer.type = LayerType(type_raw)
            except (TypeError, ValueError):
                pass
        return layer
