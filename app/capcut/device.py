"""Local device profile used by the independent CapCut client."""

from dataclasses import asdict, dataclass
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

DEFAULT_DEVICE = {
    "aid": "359289", "app_name": "CapCut", "appvr": "8.7.0",
    "version_name": "8.7.0", "version_code": "8.7.0",
    "channel": "capcutpc_google", "device_platform": "mac",
    "device_type": "MacBookPro17,4", "device_brand": "MacBookPro17,4",
    "os_version": "15.7.4", "device_id": "76471456455646328721",
    "iid": "76471456455646328721", "region": "VN", "loc": "VN",
    "lan": "vi-VN", "pf": "3", "tdid": "76471456455646328721",
}

@dataclass
class DeviceConfig:
    aid: str = DEFAULT_DEVICE["aid"]
    app_name: str = DEFAULT_DEVICE["app_name"]
    appvr: str = DEFAULT_DEVICE["appvr"]
    version_name: str = DEFAULT_DEVICE["version_name"]
    version_code: str = DEFAULT_DEVICE["version_code"]
    channel: str = DEFAULT_DEVICE["channel"]
    device_platform: str = DEFAULT_DEVICE["device_platform"]
    device_type: str = DEFAULT_DEVICE["device_type"]
    device_brand: str = DEFAULT_DEVICE["device_brand"]
    os_version: str = DEFAULT_DEVICE["os_version"]
    device_id: str = DEFAULT_DEVICE["device_id"]
    iid: str = DEFAULT_DEVICE["iid"]
    region: str = DEFAULT_DEVICE["region"]
    loc: str = DEFAULT_DEVICE["loc"]
    lan: str = DEFAULT_DEVICE["lan"]
    pf: str = DEFAULT_DEVICE["pf"]
    tdid: str = DEFAULT_DEVICE["tdid"]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceConfig":
        merged = deepcopy(DEFAULT_DEVICE)
        merged.update(data)
        return cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json_file(cls, path: str | Path) -> "DeviceConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
