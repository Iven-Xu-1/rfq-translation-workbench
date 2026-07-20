from __future__ import annotations

import os


PDF2ZH_NEXT_VERSION = "2.8.2"
BABELDOC_VERSION = "0.5.24"


def env_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(
    name: str,
    default: int,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    raw = env_text(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if value < minimum:
        raise ValueError(f"环境变量 {name} 必须不小于 {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"环境变量 {name} 不能大于 {maximum}")
    return value


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = env_text(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是数字") from exc
    if value < minimum:
        raise ValueError(f"环境变量 {name} 必须不小于 {minimum}")
    return value


def env_enabled(name: str, default: bool = False) -> bool:
    raw = env_text(name)
    if not raw:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}
