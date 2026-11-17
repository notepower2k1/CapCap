from dataclasses import dataclass, field
from app.layers.base import BaseLayer, LayerType


@dataclass
class BackgroundLayer(BaseLayer):
    type: LayerType = field(default=LayerType.BACKGROUND, init=False)
    mode: str = "color"
    color: str = "#101826"
    source: str = ""
    blur: float = 24

    def to_dict(self):
        return dict(super().to_dict(), mode=self.mode, color=self.color, source=self.source, blur=self.blur)

    @classmethod
    def from_dict(cls, data):
        base = BaseLayer.from_dict(data)
        values = {key: getattr(base, key) for key in ("id", "name", "start", "end", "z_index", "visible", "locked", "opacity", "blend_mode", "metadata")}
        return cls(**values, mode=data.get("mode", "color"), color=data.get("color", "#101826"), source=data.get("source", ""), blur=float(data.get("blur", 24)))
