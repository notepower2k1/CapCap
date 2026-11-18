from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.layers.base import BaseLayer, LayerType


class MaskMode(str, Enum):
    SOLID = "solid"
    PIXELATE = "pixelate"
    BLUR = "blur"
    IMAGE = "image"


@dataclass
class MaskLayer(BaseLayer):
    """Rectangular mask region that hides or transforms part of the video
    without blurring. Position/size are normalized 0..1 (same convention
    as BlurLayer) so the layer can be applied to any source resolution.
    """

    type: LayerType = field(default=LayerType.MASK, init=False)
    position_x: float = 0.3
    position_y: float = 0.4
    width: float = 0.4
    height: float = 0.2
    color: str = "#000000"
    mode: str = "solid"
    source: str = ""
    pixelate_size: int = 12
    blur_strength: int = 20

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "position_x": self.position_x,
            "position_y": self.position_y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "mode": self.mode,
            "source": self.source,
            "pixelate_size": self.pixelate_size,
            "blur_strength": self.blur_strength,
        })
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaskLayer:
        base = super().from_dict(data)
        return cls(
            id=base.id, name=base.name,
            start=base.start, end=base.end,
            z_index=base.z_index,
            visible=base.visible, locked=base.locked,
            opacity=base.opacity, blend_mode=base.blend_mode,
            metadata=base.metadata,
            position_x=float(data.get("position_x", 0.3)),
            position_y=float(data.get("position_y", 0.4)),
            width=float(data.get("width", 0.4)),
            height=float(data.get("height", 0.2)),
            color=str(data.get("color", "#000000")),
            mode=str(data.get("mode", "solid")),
            source=str(data.get("source", "")),
            pixelate_size=int(data.get("pixelate_size", 12)),
            blur_strength=int(data.get("blur_strength", 20)),
        )
