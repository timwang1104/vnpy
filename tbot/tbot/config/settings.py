"""ConfigManager — 环境感知的 YAML 配置加载。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class ConfigManager:
    """分层配置管理器。

    加载顺序：
        1. base.yaml（默认配置）
        2. {env}.yaml（环境覆盖，由 QUANTBOT_ENV 决定）
        3. logging.yaml（日志配置）

    用法:
        config = ConfigManager()
        config.get("database.data_dir")        # → "data"
        config.get("server.port", default=8765)
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        if config_dir is None:
            config_dir = Path(__file__).resolve().parent
        self.config_dir = Path(config_dir)

        env = os.getenv("QUANTBOT_ENV", "development")
        self._data: dict[str, Any] = {}

        # 1. base.yaml
        base_path = self.config_dir / "base.yaml"
        if base_path.exists():
            with open(base_path) as f:
                self._data = yaml.safe_load(f) or {}

        # 2. {env}.yaml
        env_path = self.config_dir / f"{env}.yaml"
        if env_path.exists():
            with open(env_path) as f:
                env_data = yaml.safe_load(f) or {}
            self._deep_merge(self._data, env_data)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """递归合并字典。"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigManager._deep_merge(base[key], value)
            else:
                base[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """点号分隔的键路径取值。

        例: config.get("database.data_dir")  # → "data"
        """
        parts = key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return default
            else:
                return default
        return current

    def raw(self) -> dict:
        """返回完整配置字典。"""
        return dict(self._data)
