"""路径、设置与秘密文件的集中管理。

规则：
* 所有运行期数据都在 ``data/`` 下（已被 .gitignore 排除）；
* 设置文件只存非敏感项；API Key 单独存 ``data/secrets.json``，接口永不回显；
* 环境变量优先于文件，便于临时覆盖。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("RADAR_DATA_DIR")
    path = Path(override) if override else ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    override = os.environ.get("RADAR_DB_PATH")
    return Path(override) if override else data_dir() / "radar.sqlite3"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def secrets_path() -> Path:
    return data_dir() / "secrets.json"


DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "site_title": "MindOS Radar",
    "bind_host": "127.0.0.1",
    "port": 8770,
    "collector": {
        "timeout_seconds": 20,
        "max_bytes": 4 * 1024 * 1024,
        "max_items_per_run": 50,
        "host_min_interval_seconds": 1.0,
        "retry_count": 2,
        "user_agent": "MindOS-Radar/0.1 (+local personal research tool)",
    },
    "schedule": {"enabled": False, "interval_minutes": 360},
    "ai": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "prompt_version": "v1",
        "enabled": False,
        "max_items_per_job": 10,
        "max_input_chars": 6000,
        "max_output_tokens": 800,
        "daily_request_limit": 50,
        "daily_budget_cny": "1.00",
        "price_input_cny_per_mtok": "2.00",
        "price_output_cny_per_mtok": "8.00",
        "price_checked_at": "2026-11-16",
    },
    "export": {"default_format": "json"},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return _merge(DEFAULT_SETTINGS, {})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _merge(DEFAULT_SETTINGS, {})
    if not isinstance(raw, dict):
        return _merge(DEFAULT_SETTINGS, {})
    return _merge(DEFAULT_SETTINGS, raw)


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """合并并持久化非敏感设置；返回最新完整设置。"""
    current = load_settings()
    updated = _merge(current, patch)
    settings_path().write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return updated


def load_secrets() -> dict[str, str]:
    path = secrets_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def set_secret(name: str, value: str | None) -> None:
    data = load_secrets()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    path = secrets_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_secret(name: str, env_var: str | None = None) -> str:
    """环境变量优先，其次本地 secrets.json。"""
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            return env_value
    return load_secrets().get(name, "")


def secret_status() -> dict[str, bool]:
    """只返回"是否已配置"，绝不返回值。"""
    data = load_secrets()
    env = {
        "deepseek": bool(os.environ.get("RADAR_DEEPSEEK_API_KEY")),
        "github": bool(os.environ.get("RADAR_GITHUB_TOKEN")),
    }
    return {
        "deepseek": bool(data.get("deepseek_api_key")) or env["deepseek"],
        "github": bool(data.get("github_token")) or env["github"],
    }
