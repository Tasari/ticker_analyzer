from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONFIG_PATH = Path("metrics_config.json")


class MetricsConfig:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "MetricsConfig":
        return cls(load_config(path))

    def save(self, path: Path = CONFIG_PATH) -> None:
        save_config(self.data, path)

    @property
    def metrics(self) -> dict[str, list[dict[str, Any]]]:
        return self.data.get("metrics", {})

    @property
    def tab_weights(self) -> dict[str, Any]:
        return self.data.get("tab_weights", {})

    @property
    def rating_thresholds(self) -> dict[str, Any]:
        return self.data.get("rating_thresholds", {})


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
