from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.layers.base import BaseLayer, LayerType, BlendMode
from app.layers.video import VideoLayer
from app.layers.audio import AudioLayer
from app.layers.subtitle import SubtitleLayer
from app.layers.text import TextLayer
from app.layers.image import ImageLayer
from app.layers.sticker import StickerLayer
from app.layers.blur import BlurLayer
from app.layers.dub_subtitle import DubSubtitleLayer
from app.layers.mask import MaskLayer
from app.layers.background import BackgroundLayer

LAYER_CLASS_MAP: dict[LayerType, type] = {
    LayerType.VIDEO: VideoLayer,
    LayerType.AUDIO: AudioLayer,
    LayerType.SUBTITLE: SubtitleLayer,
    LayerType.DUB_SUBTITLE: DubSubtitleLayer,
    LayerType.TEXT: TextLayer,
    LayerType.IMAGE: ImageLayer,
    LayerType.STICKER: StickerLayer,
    LayerType.BLUR: BlurLayer,
    LayerType.MASK: MaskLayer,
    LayerType.BACKGROUND: BackgroundLayer,
}


@dataclass
class Clip:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    layer_id: str = ""
    track_id: str = ""
    track_start: float = 0.0
    source_start: float = 0.0
    duration: float = 0.0
    speed: float = 1.0

    @property
    def track_end(self) -> float:
        return self.track_start + self.duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer_id": self.layer_id,
            "track_id": self.track_id,
            "track_start": self.track_start,
            "source_start": self.source_start,
            "duration": self.duration,
            "speed": self.speed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Clip:
        return cls(
            id=str(data.get("id", uuid4().hex[:12])),
            layer_id=str(data.get("layer_id", "")),
            track_id=str(data.get("track_id", "")),
            track_start=float(data.get("track_start", 0.0)),
            source_start=float(data.get("source_start", 0.0)),
            duration=float(data.get("duration", 0.0)),
            speed=float(data.get("speed", 1.0)),
        )


@dataclass
class Track:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    name: str = ""
    type: LayerType = LayerType.VIDEO
    visible: bool = True
    locked: bool = False
    muted: bool = False
    solo: bool = False
    height: int = 80
    clips: list[Clip] = field(default_factory=list)
    layers: list[BaseLayer] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "visible": self.visible,
            "locked": self.locked,
            "muted": self.muted,
            "solo": self.solo,
            "height": self.height,
            "clips": [c.to_dict() for c in self.clips],
            "layers": [layer_to_dict(l) for l in self.layers],
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Track:
        track = cls(
            id=str(data.get("id", uuid4().hex[:12])),
            name=str(data.get("name", "")),
            type=LayerType(data.get("type", "video")),
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            muted=bool(data.get("muted", False)),
            solo=bool(data.get("solo", False)),
            height=int(data.get("height", 80)),
            clips=[Clip.from_dict(c) for c in data.get("clips", [])],
            layers=[layer_from_dict(l) for l in data.get("layers", [])],
        )
        meta = data.get("metadata")
        if isinstance(meta, dict):
            track.metadata = dict(meta)
        return track

    def get_clips_in_range(self, start: float, end: float) -> list[Clip]:
        return [c for c in self.clips if c.track_start < end and c.track_end > start]


@dataclass
class Timeline:
    duration: float = 0.0
    tracks: list[Track] = field(default_factory=list)
    composition_width: int = 1920
    composition_height: int = 1080
    fps: float = 30.0
    overlay_order: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "tracks": [t.to_dict() for t in self.tracks],
            "overlay_order": list(self.overlay_order),
            "composition": {
                "width": self.composition_width,
                "height": self.composition_height,
                "fps": self.fps,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Timeline:
        from app.layers.stack import overlay_order
        comp = data.get("composition", {})
        return cls(
            duration=float(data.get("duration", 0.0)),
            tracks=[Track.from_dict(t) for t in data.get("tracks", [])],
            composition_width=int(comp.get("width", 1920)),
            composition_height=int(comp.get("height", 1080)),
            fps=float(comp.get("fps", 30.0)),
            overlay_order=overlay_order(data.get("overlay_order")),
        )

    def get_track(self, track_id: str) -> Track | None:
        for t in self.tracks:
            if t.id == track_id:
                return t
        return None

    def all_layers(self) -> list[BaseLayer]:
        result = []
        for track in self.tracks:
            result.extend(track.layers)
        return sorted(result, key=lambda l: l.z_index)

    def add_track(self, name: str, track_type: LayerType) -> Track:
        track = Track(name=name, type=track_type)
        for l in self.tracks:
            if l.type == track_type:
                track.z_index = len(self.tracks)
        self.tracks.append(track)
        return track

    def remove_track(self, track_id: str) -> bool:
        for i, t in enumerate(self.tracks):
            if t.id == track_id:
                self.tracks.pop(i)
                return True
        return False


def layer_to_dict(layer: BaseLayer) -> dict[str, Any]:
    return layer.to_dict()


def layer_from_dict(data: dict[str, Any]) -> BaseLayer:
    layer_type = LayerType(data.get("type", "video"))
    cls = LAYER_CLASS_MAP.get(layer_type, BaseLayer)
    return cls.from_dict(data)
