"""Config defaults, loading, normalizing and saving (no Qt needed)."""

import json
import logging
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_CONFIG = {
    "native_apps": [
        {"name": "Kodi", "cmd": "kodi", "icon": "icons/kodi.png"},
        {"name": "Stremio", "cmd": "stremio-qt5", "icon": "icons/stremio.png"},
        {"name": "VLC", "cmd": "vlc", "icon": "icons/vlc.png"},
    ],
    "web_apps": [
        {"name": "YouTube", "url": "https://www.youtube.com", "icon": "icons/youtube.png"},
    ],
    "categories": {},  # User-defined categories: {"category_name": ["app_name", ...]}
    "auth": {
        "username": "",
        "password_hash": "",  # PBKDF2 hash for storage
        "password_salt": "",  # Salt for PBKDF2
        "password_iterations": 0,
        "tokens": [],  # SHA-256 digests of paired-device tokens
    },
    "websocket": {
        "host": "0.0.0.0",
        "port": 8765,
    },
    "auto_launch": {
        "app_kind": "",
        "app_target": "",
        "delay_seconds": 10,
    },
}


def load_config(path: Path):
    if not path.exists():
        logging.warning("Config not found at %s, using built-in defaults", path)
        return normalize_config(DEFAULT_CONFIG)

    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yml", ".yaml"):
            if yaml is None:
                raise RuntimeError("pyyaml is required to load YAML config")
            return normalize_config(yaml.safe_load(text))
        if path.suffix.lower() == ".json":
            return normalize_config(json.loads(text))

        # fallback by heuristic
        if text.strip().startswith("{"):
            return normalize_config(json.loads(text))
        if yaml is None:
            raise RuntimeError("pyyaml is required to load YAML config")
        return normalize_config(yaml.safe_load(text))
    except Exception as e:
        logging.exception("Failed to load config '%s': %s", path, e)
        return normalize_config(DEFAULT_CONFIG)


def normalize_config(config):
    normalized = dict(DEFAULT_CONFIG)
    if isinstance(config, dict):
        # Deep merge nested dicts instead of shallow replace
        for key, value in config.items():
            if key in normalized and isinstance(normalized[key], dict) and isinstance(value, dict):
                # Merge nested dicts, preserving defaults
                normalized[key] = {**normalized[key], **value}
            else:
                normalized[key] = value

    native_apps = normalized.get("native_apps")
    web_apps = normalized.get("web_apps")
    categories = normalized.get("categories")
    auth = normalized.get("auth")
    auto_launch = normalized.get("auto_launch")
    normalized["native_apps"] = native_apps if isinstance(native_apps, list) else list(DEFAULT_CONFIG["native_apps"])
    normalized["web_apps"] = web_apps if isinstance(web_apps, list) else list(DEFAULT_CONFIG["web_apps"])
    normalized["categories"] = categories if isinstance(categories, dict) else dict(DEFAULT_CONFIG["categories"])
    normalized["auth"] = auth if isinstance(auth, dict) else dict(DEFAULT_CONFIG["auth"])
    normalized["auth"].pop("password_simple_hash", None)  # unsalted secret from older versions
    normalized["auto_launch"] = auto_launch if isinstance(auto_launch, dict) else dict(DEFAULT_CONFIG["auto_launch"])
    return normalized


def save_config(path: Path, config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".yml", ".yaml"):
        if yaml is None:
            raise RuntimeError("pyyaml is required to save YAML config")
        path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=False), encoding="utf-8")
    elif path.suffix.lower() == ".json":
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    elif yaml is not None:
        path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def resolve_config_path() -> Path:
    config_path = Path(os.getenv("LINUXTV_CONFIG", "~/.config/linuxtv/config.yaml")).expanduser()
    bundled_path = Path(__file__).parent / "config.yaml"

    if config_path.exists():
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if bundled_path.exists():
        try:
            config_path.write_text(bundled_path.read_text(encoding="utf-8"), encoding="utf-8")
            logging.info("Seeded LinuxTV config at %s from %s", config_path, bundled_path)
        except Exception:
            logging.exception("Failed to seed LinuxTV config at %s", config_path)

    return config_path
