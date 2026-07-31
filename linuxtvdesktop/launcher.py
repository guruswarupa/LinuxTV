#!/usr/bin/env python3
import asyncio
import base64
import configparser
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import http.server
import importlib
import json
import logging
import os
import queue
import signal
import shlex
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import yaml
except ImportError:
    yaml = None

try:
    import websockets
except ImportError:
    websockets = None

QT_BINDING = None


def _load_qt_binding():
    preferred = os.getenv("LINUXTV_QT_BINDING")
    if preferred:
        order = [preferred]
    elif sys.platform.startswith("linux"):
        order = ["PyQt5", "PySide6"]
    else:
        order = ["PySide6", "PyQt5"]

    for binding in order:
        try:
            if binding == "PyQt5":
                qt_core = importlib.import_module("PyQt5.QtCore")
                qt_gui = importlib.import_module("PyQt5.QtGui")
                qt_widgets = importlib.import_module("PyQt5.QtWidgets")
                signal_type = qt_core.pyqtSignal
            elif binding == "PySide6":
                qt_core = importlib.import_module("PySide6.QtCore")
                qt_gui = importlib.import_module("PySide6.QtGui")
                qt_widgets = importlib.import_module("PySide6.QtWidgets")
                signal_type = qt_core.Signal
            else:
                logging.warning("Unknown Qt binding requested: %s", binding)
                continue

            return (
                binding,
                qt_core.QEvent,
                qt_core.QEasingCurve,
                qt_core.QObject,
                qt_core.QPropertyAnimation,
                qt_core.QRect,
                qt_core.Qt,
                qt_core.QSize,
                qt_core.QTimer,
                signal_type,
                qt_gui.QFont,
                qt_gui.QIcon,
                qt_gui.QKeyEvent,
                qt_gui.QPixmap,
                qt_gui.QWheelEvent,
                qt_gui.QColor,
                qt_gui.QPainter,
                qt_gui.QLinearGradient,
                qt_widgets.QApplication,
                qt_widgets.QComboBox,
                qt_widgets.QDialog,
                qt_widgets.QFrame,
                qt_widgets.QGraphicsDropShadowEffect,
                qt_widgets.QGraphicsOpacityEffect,
                qt_widgets.QGridLayout,
                qt_widgets.QHBoxLayout,
                qt_widgets.QLabel,
                qt_widgets.QLineEdit,
                qt_widgets.QMainWindow,
                qt_widgets.QMenu,
                qt_widgets.QMessageBox,
                qt_widgets.QPushButton,
                qt_widgets.QSizePolicy,
                qt_widgets.QScrollArea,
                qt_widgets.QSlider,
                qt_widgets.QToolButton,
                qt_widgets.QVBoxLayout,
                qt_widgets.QWidget,
                qt_gui.QDrag,
                qt_core.QMimeData,
            )
        except ImportError:
            continue

    raise ImportError("No supported Qt binding found. Install PyQt5 or PySide6.")


(
    QT_BINDING,
    QEvent,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRect,
    Qt,
    QSize,
    QTimer,
    Signal,
    QFont,
    QIcon,
    QKeyEvent,
    QPixmap,
    QWheelEvent,
    QColor,
    QPainter,
    QLinearGradient,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QDrag,
    QMimeData,
) = _load_qt_binding()

APP_NAME = "LinuxTV"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
REMOTE_POINTER_SPEED_MULTIPLIER = 2.5
REMOTE_POINTER_TARGET_CACHE_SECONDS = 1.0

DEFAULT_CONFIG = {
    "native_apps": [
        {"name": "Kodi", "cmd": "kodi", "icon": "icons/kodi.png"},
        {"name": "Stremio", "cmd": "stremio", "icon": "icons/stremio.png"},
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
        "password_simple_hash": "",  # SHA-256 of raw password for challenge-response
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

LINE_COUNT = 4
COLUMN_COUNT = 3
AUTO_LAUNCH_IDLE_MS = 10_000
UPDATE_REPO_URL = "https://github.com/guruswarupa/LinuxTV"


def sync_system_time():
    timedatectl = shutil.which("timedatectl")
    if not timedatectl:
        return False, "timedatectl is not installed."

    try:
        result = subprocess.run(
            [timedatectl, "set-ntp", "true"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception as exc:
        logging.exception("Failed to enable automatic time sync")
        return False, f"Could not enable automatic time sync: {exc}"

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Unknown error").strip()
        return False, f"Could not enable automatic time sync: {message}"

    try:
        status_result = subprocess.run(
            [timedatectl, "show", "--property=NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        ntp_synced = status_result.stdout.strip().lower() == "yes"
    except Exception:
        ntp_synced = False

    if ntp_synced:
        return True, "System time synchronized."
    return True, "Automatic time sync enabled."


def dialog_metrics():
    screen = QApplication.primaryScreen()
    geometry = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
    width = geometry.width()
    height = geometry.height()
    compact = width <= 1600 or height <= 950
    if compact:
        return {
            "compact": True,
            "add_width": 420,
            "confirm_width": 380,
            "settings_width": 760,
            "dialog_margin_x": 28,
            "dialog_margin_y": 24,
            "dialog_spacing": 14,
            "title_font": 24,
            "section_font": 18,
            "subtitle_font": 15,
            "helper_font": 13,
            "input_min_height": 38,
            "nav_spacing": 12,
            "status_padding_v": 10,
            "status_padding_h": 14,
            "field_font_css": 14,
            "button_font_css": 14,
            "button_min_width": 85,
            "button_padding_v": 9,
            "button_padding_h": 18,
        }
    return {
        "compact": False,
        "add_width": 480,
        "confirm_width": 440,
        "settings_width": 840,
        "dialog_margin_x": 32,
        "dialog_margin_y": 28,
        "dialog_spacing": 16,
        "title_font": 28,
        "section_font": 20,
        "subtitle_font": 17,
        "helper_font": 14,
        "input_min_height": 42,
        "nav_spacing": 14,
        "status_padding_v": 12,
        "status_padding_h": 16,
        "field_font_css": 15,
        "button_font_css": 15,
        "button_min_width": 95,
        "button_padding_v": 11,
        "button_padding_h": 20,
    }


def dialog_stylesheet(metrics=None):
    metrics = metrics or dialog_metrics()
    stylesheet = """
        QDialog {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(30, 41, 59, 0.98), stop:1 rgba(15, 23, 42, 0.99));
            border: 1px solid rgba(100, 116, 139, 0.25);
            border-radius: 12px;
        }
        QLabel#dialogTitle {
            color: #f8fafc;
            padding-top: 0px;
            padding-bottom: 6px;
            margin-top: 0px;
            margin-bottom: 0px;
            font-size: __TITLE_FONT__px;
            font-weight: 700;
        }
        QLabel#dialogSection {
            color: #f1f5f9;
            padding-top: 6px;
            padding-bottom: 4px;
            margin-top: 0px;
            font-weight: 600;
            font-size: __SECTION_FONT__px;
        }
        QLabel#dialogSubtitle {
            color: #94a3b8;
            padding-top: 2px;
            padding-bottom: 6px;
            margin-bottom: 0px;
            line-height: 1.4;
            font-size: __SUBTITLE_FONT__px;
        }
        QLabel#dialogFieldLabel {
            color: #cbd5e1;
            font-weight: 500;
            padding-top: 6px;
            padding-bottom: 3px;
            margin-top: 0px;
            font-size: __FIELD_FONT__px;
        }
        QLabel#dialogHelper {
            color: #64748b;
            padding-top: 3px;
            font-size: __HELPER_FONT__px;
            line-height: 1.3;
        }
        QLabel#dialogStatus {
            color: #f1f5f9;
            background: rgba(51, 65, 85, 0.6);
            border: 1px solid rgba(71, 85, 105, 0.3);
            border-radius: 6px;
            padding: __STATUS_PADDING_V__px __STATUS_PADDING_H__px;
            font-size: __HELPER_FONT__px;
        }
        QLineEdit, QComboBox {
            background: rgba(15, 23, 42, 0.7);
            color: #f1f5f9;
            border: 1px solid rgba(71, 85, 105, 0.5);
            border-radius: 6px;
            padding: 10px 14px;
            min-height: 16px;
            font-size: __FIELD_FONT__px;
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
        }
        QLineEdit:focus, QComboBox:focus {
            border: 1px solid #3b82f6;
            background: rgba(15, 23, 42, 0.85);
        }
        QLineEdit:hover, QComboBox:hover {
            border: 1px solid rgba(100, 116, 139, 0.6);
            background: rgba(15, 23, 42, 0.8);
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox::down-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid #94a3b8;
            width: 0;
            height: 0;
        }
        QComboBox::down-arrow:hover {
            border-top-color: #f1f5f9;
        }
        QComboBox QAbstractItemView {
            background: rgba(15, 23, 42, 0.98);
            color: #f1f5f9;
            border: 1px solid rgba(71, 85, 105, 0.5);
            border-radius: 6px;
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
            alternate-background-color: rgba(30, 41, 59, 0.6);
            padding: 2px;
        }
        QComboBox QAbstractItemView::item {
            background: transparent;
            color: #f1f5f9;
            padding: 8px 12px;
            border-radius: 4px;
            margin: 1px;
        }
        QComboBox QAbstractItemView::item:selected {
            background: rgba(59, 130, 246, 0.3);
            color: #f8fafc;
        }
        QComboBox QAbstractItemView::item:hover {
            background: rgba(71, 85, 105, 0.5);
            color: #f1f5f9;
        }
        QComboBox QLineEdit {
            background: transparent;
            color: #f1f5f9;
            border: none;
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
        }
        QPushButton[tileVariant="dialogSecondary"] {
            background: rgba(51, 65, 85, 0.6);
            color: #e2e8f0;
            border: 1px solid rgba(71, 85, 105, 0.5);
            border-radius: 6px;
            padding: __BUTTON_PADDING_V__px __BUTTON_PADDING_H__px;
            min-width: __BUTTON_MIN_WIDTH__px;
            font-size: __BUTTON_FONT__px;
            font-weight: 500;
        }
        QPushButton[tileVariant="dialogSecondary"][sectionNav="true"] {
            padding: 8px 16px;
            min-width: 100px;
        }
        QPushButton[tileVariant="dialogSecondary"]:hover {
            background: rgba(71, 85, 105, 0.7);
            border: 1px solid rgba(100, 116, 139, 0.6);
            color: #f1f5f9;
        }
        QPushButton[tileVariant="dialogSecondary"][sectionNav="true"]:hover {
            background: rgba(59, 130, 246, 0.8);
            border: 1px solid rgba(59, 130, 246, 0.6);
            color: #f8fafc;
        }
        QPushButton[tileVariant="dialogSecondary"]:focus {
            border: 1px solid #3b82f6;
            outline: none;
            background: rgba(59, 130, 246, 0.2);
        }
        QPushButton[tileVariant="dialogSecondary"][sectionNav="true"]:focus {
            background: rgba(59, 130, 246, 0.4);
            border: 1px solid #3b82f6;
            color: #f8fafc;
        }
        QPushButton[tileVariant="dialogSecondary"]:pressed {
            background: rgba(30, 41, 59, 0.9);
        }
        QPushButton[tileVariant="accent"] {
            background: rgba(16, 185, 129, 0.8);
            color: #f8fafc;
            border: 1px solid rgba(52, 211, 153, 0.5);
            border-radius: 6px;
            padding: __BUTTON_PADDING_V__px __BUTTON_PADDING_H__px;
            min-width: __BUTTON_MIN_WIDTH__px;
            font-size: __BUTTON_FONT__px;
            font-weight: 600;
        }
        QPushButton[tileVariant="accent"]:hover {
            background: rgba(52, 211, 153, 0.9);
            border: 1px solid rgba(110, 231, 183, 0.6);
        }
        QPushButton[tileVariant="accent"]:focus {
            border: 1px solid #3b82f6;
            outline: none;
        }
        QPushButton[tileVariant="accent"]:pressed {
            background: rgba(5, 150, 105, 0.95);
        }
        QPushButton[tileVariant="danger"] {
            background: rgba(239, 68, 68, 0.8);
            color: #f8fafc;
            border: 1px solid rgba(248, 113, 113, 0.5);
            border-radius: 6px;
            padding: __BUTTON_PADDING_V__px __BUTTON_PADDING_H__px;
            min-width: __BUTTON_MIN_WIDTH__px;
            font-size: __BUTTON_FONT__px;
            font-weight: 600;
        }
        QPushButton[tileVariant="danger"]:hover {
            background: rgba(248, 113, 113, 0.9);
            border: 1px solid rgba(252, 165, 165, 0.6);
        }
        QPushButton[tileVariant="danger"]:focus {
            border: 1px solid #3b82f6;
            outline: none;
        }
        QPushButton[tileVariant="danger"]:pressed {
            background: rgba(220, 38, 38, 0.95);
        }
        QSlider::groove:horizontal {
            background: rgba(51, 65, 85, 0.7);
            border: 1px solid rgba(71, 85, 105, 0.4);
            height: 4px;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #3b82f6;
            border: 2px solid #1e293b;
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }
        QSlider::handle:horizontal:hover {
            background: #60a5fa;
        }
        QSlider::sub-page:horizontal {
            background: #3b82f6;
            border-radius: 2px;
        }
    """
    replacements = {
        "__TITLE_FONT__": str(metrics["title_font"]),
        "__SECTION_FONT__": str(metrics["section_font"]),
        "__SUBTITLE_FONT__": str(metrics["subtitle_font"]),
        "__FIELD_FONT__": str(metrics["field_font_css"]),
        "__HELPER_FONT__": str(metrics["helper_font"]),
        "__BUTTON_FONT__": str(metrics["button_font_css"]),
        "__STATUS_PADDING_V__": str(metrics["status_padding_v"]),
        "__STATUS_PADDING_H__": str(metrics["status_padding_h"]),
        "__BUTTON_PADDING_V__": str(metrics["button_padding_v"]),
        "__BUTTON_PADDING_H__": str(metrics["button_padding_h"]),
        "__BUTTON_MIN_WIDTH__": str(metrics["button_min_width"]),
    }
    for token, value in replacements.items():
        stylesheet = stylesheet.replace(token, value)
    return stylesheet


def resource_path(relpath: str) -> Path:
    base = Path(__file__).parent
    return (base / relpath).expanduser().resolve()


def cache_dir() -> Path:
    return Path(os.getenv("XDG_CACHE_HOME", "~/.cache")).expanduser() / "linuxtv" / "icons"


def desktop_file_locations():
    return [
        Path.home() / ".local/share/applications",
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
    ]


def icon_search_locations():
    return [
        Path.home() / ".local/share/icons",
        Path.home() / ".icons",
        Path.home() / ".local/share/flatpak/exports/share/icons",
        Path("/usr/local/share/icons"),
        Path("/usr/share/icons/hicolor"),
        Path("/var/lib/flatpak/exports/share/icons/hicolor"),
        Path("/usr/share/pixmaps"),
    ]


def normalized_icon_path(source_path: str, cache_key: str, size: int = 96):
    if not source_path:
        return ""

    icon_source = Path(source_path).expanduser()
    if not icon_source.exists():
        return ""

    normalized_dir = cache_dir() / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    target_path = normalized_dir / f"{hashlib.sha1(cache_key.encode('utf-8')).hexdigest()}.png"
    if target_path.exists():
        return str(target_path)

    pixmap = QPixmap(str(icon_source))
    if pixmap.isNull():
        icon = QIcon(str(icon_source))
        pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        return str(icon_source)

    scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    scaled.save(str(target_path), "PNG")
    return str(target_path)


def create_white_icon(icon_path: str, size: int = 96):
    """Create a white version of an icon by painting it with white color"""
    if not icon_path:
        return QIcon()
    
    icon_source = Path(icon_path).expanduser()
    if not icon_source.exists():
        return QIcon()
    
    pixmap = QPixmap(str(icon_source))
    if pixmap.isNull():
        return QIcon()
    
    # Create a new pixmap with the same size
    white_pixmap = QPixmap(pixmap.size())
    white_pixmap.fill(Qt.transparent)
    
    # Paint the original pixmap in white
    painter = QPainter(white_pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(white_pixmap.rect(), Qt.white)
    painter.end()
    
    return QIcon(white_pixmap)


@lru_cache(maxsize=256)
def resolve_icon_name(icon_name: str):
    if not icon_name:
        return ""

    icon_path = Path(icon_name).expanduser()
    if icon_path.exists():
        return str(icon_path)

    for suffix in ("png", "svg", "xpm"):
        for base_dir in icon_search_locations():
            if not base_dir.exists():
                continue
            direct_match = base_dir / f"{icon_name}.{suffix}"
            if direct_match.exists():
                return str(direct_match)
            for candidate in base_dir.glob(f"**/{icon_name}.{suffix}"):
                if candidate.exists():
                    return str(candidate)
    return ""


def desktop_entry_for_command(command_text: str):
    parts = split_command(command_text)
    if not parts:
        return None

    if len(parts) >= 3 and parts[0] == "flatpak" and parts[1] == "run":
        flatpak_app_id = parts[2]
        for directory in desktop_file_locations():
            direct_match = directory / f"{flatpak_app_id}.desktop"
            if direct_match.exists():
                parser = configparser.ConfigParser(interpolation=None)
                try:
                    parser.read(direct_match, encoding="utf-8")
                except Exception:
                    continue
                if "Desktop Entry" in parser:
                    return parser["Desktop Entry"]

    executable = Path(parts[0]).name
    for directory in desktop_file_locations():
        if not directory.exists():
            continue
        for desktop_file in directory.glob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(desktop_file, encoding="utf-8")
            except Exception:
                continue
            if "Desktop Entry" not in parser:
                continue
            entry = parser["Desktop Entry"]
            exec_line = entry.get("Exec", "")
            if not exec_line:
                continue
            exec_parts = split_command(exec_line.replace("%u", "").replace("%U", "").replace("%f", "").replace("%F", ""))
            if not exec_parts:
                continue
            entry_exec = Path(exec_parts[0]).name
            if executable == entry_exec:
                return entry
            if len(parts) >= 3 and parts[0] == "flatpak" and parts[1] == "run" and parts[2] in exec_parts:
                return entry
    return None


def resolve_native_icon(app):
    app_name = app.get("name", "")
    
    # Try to find icon by matching app name to filename
    if app_name:
        icon_name = app_name.lower().replace(" ", "") + ".png"
        icon_path = resource_path("icons/" + icon_name)
        if icon_path.exists():
            return normalized_icon_path(str(icon_path), f"native-name:{icon_name}")
    
    # Try configured icon as fallback
    configured_icon = app.get("icon", "")
    if configured_icon:
        path = resource_path(configured_icon)
        if path.exists():
            return normalized_icon_path(str(path), f"native-config:{configured_icon}")
    
    # Try desktop entry
    entry = desktop_entry_for_command(app.get("cmd", ""))
    if entry:
        resolved = resolve_icon_name(entry.get("Icon", ""))
        if resolved:
            return normalized_icon_path(resolved, f"native-entry:{app.get('cmd', '')}:{entry.get('Icon', '')}")
    return ""


def fetch_web_icon(app):
    app_name = app.get("name", "")
    
    # Try to find icon by matching app name to filename
    if app_name:
        icon_name = app_name.lower().replace(" ", "").replace("+", "plus") + ".png"
        icon_path = resource_path("icons/" + icon_name)
        if icon_path.exists():
            return normalized_icon_path(str(icon_path), f"web-name:{icon_name}")
    
    # Try configured icon as fallback
    configured_icon = app.get("icon", "")
    if configured_icon:
        path = resource_path(configured_icon)
        if path.exists():
            return normalized_icon_path(str(path), f"web-config:{configured_icon}")
    
    # Fallback to network icon
    network_icon = resource_path("icons/network.png")
    if network_icon.exists():
        return normalized_icon_path(str(network_icon), "web-fallback:network")
    
    return ""


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
    normalized["auto_launch"] = auto_launch if isinstance(auto_launch, dict) else dict(DEFAULT_CONFIG["auto_launch"])
    return normalized


def hash_remote_password(password: str, salt: str = None) -> tuple:
    """Hash password with PBKDF2-HMAC-SHA256 and random salt.
    Returns (password_hash, salt) tuple."""
    if salt is None:
        salt = secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000  # iterations
    ).hex()
    return password_hash, salt


def remote_auth_enabled(config) -> bool:
    auth = config.get("auth", {})
    return bool(auth.get("username", "").strip() and auth.get("password_hash", "").strip())


def verify_remote_credentials(config, username: str, password: str) -> bool:
    auth = config.get("auth", {})
    expected_user = auth.get("username", "").strip()
    expected_hash = auth.get("password_hash", "").strip()
    salt = auth.get("password_salt", "").strip()
    
    if not expected_user or not expected_hash:
        return True
    
    # If salt exists, use PBKDF2 verification
    if salt:
        computed_hash, _ = hash_remote_password(password, salt)
        return username.strip() == expected_user and computed_hash == expected_hash
    
    # Legacy fallback: old SHA-256 without salt (insecure, but allows migration)
    logging.warning("Legacy password hash detected without salt. Please update password for better security.")
    legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return username.strip() == expected_user and legacy_hash == expected_hash


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


def find_browser():
    candidates = ["brave-browser", "chromium", "chromium-browser", "google-chrome", "firefox"]
    for exe in candidates:
        if shutil.which(exe):
            return exe
    return None


def is_installed(cmd_or_path: str) -> bool:
    parts = split_command(cmd_or_path)
    if not parts:
        return False
    executable = parts[0]
    if Path(executable).is_absolute() and Path(executable).exists():
        return True
    return shutil.which(executable) is not None


def split_command(command_text: str):
    try:
        return shlex.split(command_text)
    except ValueError:
        logging.warning("Failed to parse command: %s", command_text)
        return []


def run_command(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.stdout
    except Exception as exc:
        logging.warning("Command failed %s: %s", command, exc)
        return ""


def switch_audio_to_hdmi():
    pactl = shutil.which("pactl")
    if not pactl:
        return False

    sinks_output = run_command([pactl, "list", "short", "sinks"])
    hdmi_sink = None
    for line in sinks_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "hdmi" in parts[1].lower():
            hdmi_sink = parts[1]
            break

    if not hdmi_sink:
        logging.info("No HDMI sink found; leaving audio output unchanged")
        return False

    logging.info("Switching audio to HDMI sink %s", hdmi_sink)
    subprocess.run([pactl, "set-default-sink", hdmi_sink], check=False)

    sink_inputs = run_command([pactl, "list", "short", "sink-inputs"])
    for line in sink_inputs.splitlines():
        parts = line.split()
        if parts:
            subprocess.run([pactl, "move-sink-input", parts[0], hdmi_sink], check=False)

    return True


def maintain_hdmi_audio(stop_event: threading.Event, duration_seconds: int = 20):
    deadline = time.monotonic() + duration_seconds
    while not stop_event.is_set() and time.monotonic() < deadline:
        switch_audio_to_hdmi()
        stop_event.wait(2)


def get_current_volume():
    """Get current system volume percentage"""
    import re
    
    # Try wpctl first
    wpctl = shutil.which("wpctl")
    if wpctl:
        try:
            result = subprocess.run(
                [wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Output format: "Volume: 0.50" or "Volume: 0.50 [MUTED]"
                match = re.search(r'Volume:\s+([\d.]+)', result.stdout)
                if match:
                    volume = float(match.group(1))
                    return int(volume * 100)
        except Exception:
            pass
    
    # Try pactl
    pactl = shutil.which("pactl")
    if pactl:
        try:
            result = subprocess.run(
                [pactl, "get-sink-volume", "@DEFAULT_SINK@"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Output format: "Volume: front-left: 32768 /  50% / -18.06 dB, ..."
                match = re.search(r'(\d+)%', result.stdout)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
    
    # Try amixer
    amixer = shutil.which("amixer")
    if amixer:
        try:
            result = subprocess.run(
                [amixer, "get", "Master"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Output contains: "[50%]" or "[on]"/"[off]"
                match = re.search(r'\[(\d+)%\]', result.stdout)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
    
    logging.warning("Could not get current volume")
    return 50  # Default fallback


def control_system_volume(action: str):
    action = action.upper().strip()

    wpctl = shutil.which("wpctl")
    if wpctl:
        if action == "VOLUME_UP":
            result = subprocess.run(
                [wpctl, "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", "5%+"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
        elif action == "VOLUME_DOWN":
            result = subprocess.run(
                [wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
        elif action == "MUTE":
            result = subprocess.run(
                [wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True

    pactl = shutil.which("pactl")
    if pactl:
        if action == "VOLUME_UP":
            result = subprocess.run(
                [pactl, "set-sink-volume", "@DEFAULT_SINK@", "+5%"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
        elif action == "VOLUME_DOWN":
            result = subprocess.run(
                [pactl, "set-sink-volume", "@DEFAULT_SINK@", "-5%"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
        elif action == "MUTE":
            result = subprocess.run(
                [pactl, "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True

    amixer = shutil.which("amixer")
    if amixer:
        if action == "VOLUME_UP":
            result = subprocess.run([amixer, "set", "Master", "5%+"], check=False, capture_output=True, text=True)
            if result.returncode == 0:
                return True
        elif action == "VOLUME_DOWN":
            result = subprocess.run([amixer, "set", "Master", "5%-"], check=False, capture_output=True, text=True)
            if result.returncode == 0:
                return True
        elif action == "MUTE":
            result = subprocess.run([amixer, "set", "Master", "toggle"], check=False, capture_output=True, text=True)
            if result.returncode == 0:
                return True

    logging.warning("No supported volume backend succeeded for action %s", action)
    return False


def control_system_brightness(action: str, level: int = None):
    """Control screen brightness. action can be 'BRIGHTNESS_UP', 'BRIGHTNESS_DOWN', or 'SET_BRIGHTNESS'"""
    action = action.upper().strip()
    
    # Try brightnessctl first (modern systems)
    brightnessctl = shutil.which("brightnessctl")
    if brightnessctl:
        try:
            if action == "BRIGHTNESS_UP":
                result = subprocess.run(
                    [brightnessctl, "set", "+5%"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return True
            elif action == "BRIGHTNESS_DOWN":
                result = subprocess.run(
                    [brightnessctl, "set", "5%-"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return True
            elif action == "SET_BRIGHTNESS" and level is not None:
                level = max(0, min(100, level))
                result = subprocess.run(
                    [brightnessctl, "set", f"{level}%"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return True
        except Exception:
            pass
    
    # Try xrandr as fallback
    xrandr = shutil.which("xrandr")
    if xrandr:
        try:
            # Get current brightness
            current_brightness = get_current_brightness()
            
            if action == "BRIGHTNESS_UP":
                new_brightness = min(1.0, current_brightness + 0.05)
            elif action == "BRIGHTNESS_DOWN":
                new_brightness = max(0.1, current_brightness - 0.05)
            elif action == "SET_BRIGHTNESS" and level is not None:
                level = max(0, min(100, level))
                new_brightness = level / 100.0
            else:
                return False
            
            # Get the connected display
            result = subprocess.run(
                [xrandr, "--query"],
                check=False,
                capture_output=True,
                text=True,
            )
            
            if result.returncode == 0:
                display = None
                for line in result.stdout.splitlines():
                    if " connected" in line:
                        display = line.split()[0]
                        break
                
                if display:
                    result = subprocess.run(
                        [xrandr, "--output", display, "--brightness", str(new_brightness)],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0:
                        return True
        except Exception:
            pass
    
    return False


def get_current_brightness() -> float:
    """Get current screen brightness level (0.0 to 1.0)"""
    # Try brightnessctl first
    brightnessctl = shutil.which("brightnessctl")
    if brightnessctl:
        try:
            result = subprocess.run(
                [brightnessctl, "get"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                current = int(result.stdout.strip())
                result_max = subprocess.run(
                    [brightnessctl, "max"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result_max.returncode == 0:
                    max_val = int(result_max.stdout.strip())
                    if max_val > 0:
                        return current / max_val
        except Exception:
            pass
    
    # Default to 1.0 (100%) if cannot determine
    return 1.0


def process_tree_pids(root_pid: int):
    output = run_command(["ps", "-eo", "pid=,ppid="])
    children_by_parent = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        children_by_parent.setdefault(ppid, []).append(pid)

    seen = set()
    pending = [root_pid]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(children_by_parent.get(current, []))
    return seen


def find_window_ids_for_pid(pid: int):
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return []

    tracked_pids = process_tree_pids(pid)
    output = run_command([wmctrl, "-lp"])
    window_ids = []
    for line in output.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 3:
            continue
        try:
            line_pid = int(parts[2])
        except ValueError:
            continue
        if line_pid in tracked_pids:
            window_ids.append(parts[0])
    return window_ids


def native_app_profile(command):
    if not command:
        return ""

    executable = Path(command[0]).name.lower()
    if executable == "flatpak" and len(command) >= 3 and command[1] == "run":
        app_id = command[2].lower()
        if "stremio" in app_id:
            return "stremio"
        if "vlc" in app_id:
            return "vlc"
        return app_id

    if "stremio" in executable:
        return "stremio"
    if executable == "vlc":
        return "vlc"
    return executable


def enforce_native_fullscreen(command, pid: int, geometry, stop_event: threading.Event, attempts: int = 8):
    if not command:
        return

    app_profile = native_app_profile(command)
    preferred_f11 = app_profile in {"stremio", "vlc"}
    wmctrl = shutil.which("wmctrl")
    xdotool = shutil.which("xdotool")
    target_width = geometry.width() + (2 if app_profile == "stremio" else 0)
    target_height = geometry.height() + (2 if app_profile == "stremio" else 0)
    f11_applied_windows = set()

    for _ in range(attempts):
        if stop_event.wait(1.2):
            return

        window_ids = find_window_ids_for_pid(pid)
        if not window_ids:
            continue

        for window_id in window_ids:
            if wmctrl:
                subprocess.run(
                    [
                        wmctrl,
                        "-i",
                        "-r",
                        window_id,
                        "-e",
                        f"0,{geometry.x()},{geometry.y()},{target_width},{target_height}",
                    ],
                    check=False,
                )
                subprocess.run([wmctrl, "-i", "-a", window_id], check=False)
                subprocess.run([wmctrl, "-i", "-r", window_id, "-b", "add,maximized_vert,maximized_horz"], check=False)
                subprocess.run([wmctrl, "-i", "-r", window_id, "-b", "remove,maximized_vert,maximized_horz"], check=False)
                subprocess.run([wmctrl, "-i", "-r", window_id, "-b", "add,fullscreen"], check=False)

            if xdotool:
                subprocess.run([xdotool, "windowmove", window_id, str(geometry.x()), str(geometry.y())], check=False)
                subprocess.run([xdotool, "windowsize", window_id, str(target_width), str(target_height)], check=False)

            if preferred_f11 and xdotool and window_id not in f11_applied_windows:
                subprocess.run([xdotool, "windowactivate", "--sync", window_id], check=False)
                subprocess.run([xdotool, "key", "--window", window_id, "F11"], check=False)
                f11_applied_windows.add(window_id)


def request_system_power_action(action: str):
    command_map = {
        "SHUTDOWN": ["systemctl", "poweroff"],
        "REBOOT": ["systemctl", "reboot"],
        "SLEEP": ["systemctl", "suspend"],
    }
    command = command_map.get(action.upper())
    if not command:
        logging.warning("Unknown system power action requested: %s", action)
        return False

    try:
        subprocess.Popen(command)
        logging.info("Triggered system power action: %s", action)
        return True
    except Exception:
        logging.exception("Failed to trigger system power action: %s", action)
        return False


def request_system_update():
    """Trigger system update using apt, auto-password only for linuxtv user"""
    # Check if apt is available
    apt = shutil.which("apt")
    if not apt:
        logging.warning("apt is not available on this system")
        return False, "apt is not available on this system"
    
    try:
        # Use a terminal emulator if available
        terminal_emulators = ["gnome-terminal", "x-terminal-emulator", "xterm", "konsole"]
        terminal = None
        for term in terminal_emulators:
            if shutil.which(term):
                terminal = term
                break
        
        # Check current username - only auto-password for linuxtv user
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        
        if current_user == "linuxtv":
            # Auto-fill password for linuxtv user
            update_command = "echo 'linuxtv' | sudo -S apt update && echo 'linuxtv' | sudo -S apt upgrade -y"
        else:
            # Let user enter password manually
            update_command = "sudo apt update && sudo apt upgrade -y"
        
        if terminal:
            # Run in terminal so user can see progress
            if terminal == "gnome-terminal":
                subprocess.Popen([terminal, "--", "bash", "-c", f"{update_command}; echo 'Update complete. Press Enter to close.'; read"])
            else:
                subprocess.Popen([terminal, "-e", f"bash -c '{update_command}; echo Update complete. Press Enter to close.; read'"])
            logging.info("Triggered system update in terminal (user: %s)", current_user)
            return True, "System update started in terminal"
        else:
            # No terminal available, run silently
            subprocess.Popen(["bash", "-c", update_command])
            logging.info("Triggered system update (no terminal available, user: %s)", current_user)
            return True, "System update started (check terminal for progress)"
    except Exception as e:
        logging.exception("Failed to trigger system update")
        return False, f"Failed to start update: {e}"


class InputDeviceGrabber(threading.Thread):
    """Captures input events from remote control devices system-wide using evdev."""
    
    def __init__(self, launcher_window):
        super().__init__(daemon=True)
        self.launcher_window = launcher_window
        self.running = False
        self.devices = []
        self._stop_event = threading.Event()
        
    def start_grabbing(self):
        """Start capturing input events from remote control devices."""
        try:
            import evdev
        except ImportError:
            logging.warning("evdev not installed; global input grabbing disabled")
            return False
            
        self.running = True
        self.devices = []
        
        # Find all input devices
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
                caps = device.capabilities()
                
                # Look for devices that are likely remote controls
                # Remote controls typically have navigation keys but not full keyboard
                if evdev.ecodes.EV_KEY in caps:
                    key_codes = caps[evdev.ecodes.EV_KEY]
                    
                    # Check if this looks like a remote control
                    # Remote controls usually have directional keys, OK/Enter, back, etc.
                    has_directional = any(code in key_codes for code in [
                        evdev.ecodes.KEY_UP, evdev.ecodes.KEY_DOWN,
                        evdev.ecodes.KEY_LEFT, evdev.ecodes.KEY_RIGHT
                    ])
                    has_enter = evdev.ecodes.KEY_ENTER in key_codes or evdev.ecodes.KEY_KPENTER in key_codes
                    
                    # Check if it's NOT a full keyboard (doesn't have letter keys)
                    has_letters = any(code in key_codes for code in [
                        evdev.ecodes.KEY_A, evdev.ecodes.KEY_B, evdev.ecodes.KEY_C,
                        evdev.ecodes.KEY_Q, evdev.ecodes.KEY_W, evdev.ecodes.KEY_E
                    ])
                    
                    # Grab if it looks like a remote (has directional + enter, but not full keyboard)
                    if has_directional and (has_enter or len(key_codes) < 50) and not has_letters:
                        try:
                            device.grab()  # Grab exclusive access
                            self.devices.append(device)
                            logging.info("Grabbed remote control device: %s (%s)", device.name, path)
                        except Exception as e:
                            logging.warning("Failed to grab device %s: %s", path, e)
                    elif has_directional and has_letters:
                        # It's a keyboard - don't grab it exclusively
                        logging.debug("Skipping keyboard device: %s (%s)", device.name, path)
                        
            except Exception as e:
                logging.warning("Failed to check device %s: %s", path, e)
                
        if not self.devices:
            logging.info("No remote control devices found to grab (this is OK if using keyboard)")
            return False
            
        self.start()
        return True
        
    def stop_grabbing(self):
        """Stop capturing input events."""
        self._stop_event.set()
        self.running = False
        for device in self.devices:
            try:
                device.ungrab()
            except Exception:
                pass
        self.devices.clear()
        
    def run(self):
        """Main loop to read and process input events."""
        try:
            import evdev
            from select import select
        except ImportError:
            return
            
        while self.running and not self._stop_event.is_set():
            try:
                # Wait for events from any device
                readable, _, _ = select(self.devices, [], [], 0.5)
                for device in readable:
                    if self._stop_event.is_set():
                        break
                    try:
                        for event in device.read():
                            if event.type == evdev.ecodes.EV_KEY:
                                self._handle_key_event(event)
                    except Exception as e:
                        logging.debug("Error reading from device: %s", e)
            except Exception as e:
                logging.debug("Error in input grabber loop: %s", e)
                
    def _handle_key_event(self, event):
        """Handle a key event and forward to launcher."""
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            return
            
        # Only process key press events (not release)
        if event.value != 1:  # 1 = press, 0 = release, 2 = repeat
            return
            
        key_code = event.code
        key_name = ecodes.KEY[key_code] if key_code in ecodes.KEY else None
        
        if not key_name:
            return
            
        # Map common remote control keys to actions
        action_map = {
            'KEY_UP': 'UP',
            'KEY_DOWN': 'DOWN',
            'KEY_LEFT': 'LEFT',
            'KEY_RIGHT': 'RIGHT',
            'KEY_ENTER': 'SELECT',
            'KEY_KPENTER': 'SELECT',
            'KEY_SPACE': 'SELECT',
            'KEY_BACKSPACE': 'BACK',
            'KEY_ESC': 'BACK',
            'KEY_HOME': 'HOME',
            'KEY_MENU': 'MENU',
            'KEY_INFO': 'INFO',
            'KEY_PLAYPAUSE': 'PLAY_PAUSE',
            'KEY_PLAY': 'PLAY_PAUSE',
            'KEY_PAUSE': 'PLAY_PAUSE',
            'KEY_TAB': 'TAB',
        }
        
        action = action_map.get(key_name)
        if action:
            logging.debug("Remote key pressed: %s -> %s", key_name, action)
            # Queue the action for processing by the main thread
            self.launcher_window.queue_remote_action(action)


class WebSocketControlServer(threading.Thread):
    def __init__(self, window, host=None, port=None):
        super().__init__(daemon=True)
        self.window = window
        # Default to 0.0.0.0 to allow remote connections, allow override via config
        ws_config = window.config.get("websocket", {})
        config_host = ws_config.get("host", "0.0.0.0")
        config_port = ws_config.get("port", 8765)
        self.host = host if host is not None else config_host
        self.port = port if port is not None else config_port
        
        self.loop = None
        self.server = None
        self._stop_event = threading.Event()
        self._auth_nonces = {}  # Track nonces for challenge-response auth

    async def handler(self, websocket, path=None):
        logging.info("WebSocket connection from %s", websocket.remote_address)
        authenticated = not remote_auth_enabled(self.window.config)
        
        # If no authentication required, send apps list immediately
        if authenticated:
            apps_list = self.window.get_installed_apps()
            await websocket.send(json.dumps({
                "status": "ok",
                "type": "apps_list",
                "apps": apps_list
            }))
        
        try:
            async for message in websocket:
                logging.info("Received remote action: %s", message)
                try:
                    payload = json.loads(message)
                    message_type = str(payload.get("type", "")).lower()
                except Exception:
                    payload = {}
                    message_type = ""

                if message_type == "auth":
                    # Legacy authentication (kept for backward compatibility)
                    username = str(payload.get("username", ""))
                    password = str(payload.get("password", ""))
                    if verify_remote_credentials(self.window.config, username, password):
                        authenticated = True
                        await websocket.send(json.dumps({"status": "auth_ok"}))
                        # Automatically send apps list after successful authentication
                        apps_list = self.window.get_installed_apps()
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "apps_list",
                            "apps": apps_list
                        }))
                    else:
                        await websocket.send(json.dumps({"status": "auth_error", "error": "invalid credentials"}))
                    continue

                if message_type == "auth_challenge":
                    # Send random nonce for challenge-response authentication
                    nonce = secrets.token_hex(16)
                    self._auth_nonces[websocket] = nonce
                    await websocket.send(json.dumps({
                        "type": "auth_challenge",
                        "nonce": nonce
                    }))
                    continue

                if message_type == "auth_response":
                    # Verify challenge-response using SHA-256 of raw password
                    nonce = self._auth_nonces.get(websocket)
                    if not nonce:
                        await websocket.send(json.dumps({"status": "auth_error", "error": "no challenge issued"}))
                        continue
                    
                    username = str(payload.get("username", ""))
                    response_hash = str(payload.get("response", ""))
                    
                    auth = self.window.config.get("auth", {})
                    stored_password_hash = auth.get("password_hash", "")
                    salt = auth.get("password_salt", "")
                    simple_hash = auth.get("password_simple_hash", "")
                    stored_username = auth.get("username", "")
                    
                    logging.info("Auth attempt: user=%s, has_simple_hash=%s", username, bool(simple_hash))
                    
                    # Client computes: SHA-256(SHA-256(raw_password):nonce)
                    # Server verifies using stored simple_hash (SHA-256 of raw password)
                    if simple_hash:
                        expected = hashlib.sha256(f"{simple_hash}:{nonce}".encode()).hexdigest()
                        logging.info("Challenge verification: nonce=%s, expected=%s, got=%s", 
                                   nonce[:8] + "...", expected[:16] + "...", response_hash[:16] + "...")
                    else:
                        # No simple hash stored (old config), challenge-response won't work
                        logging.warning("No password_simple_hash in config, challenge-response disabled")
                        expected = None
                    
                    if username == stored_username and expected and response_hash == expected:
                        authenticated = True
                        await websocket.send(json.dumps({"status": "auth_ok"}))
                        # Send apps list after successful authentication
                        apps_list = self.window.get_installed_apps()
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "apps_list",
                            "apps": apps_list
                        }))
                    else:
                        await websocket.send(json.dumps({"status": "auth_error", "error": "invalid credentials"}))
                    
                    # Clean up nonce
                    if websocket in self._auth_nonces:
                        del self._auth_nonces[websocket]
                    continue

                if not authenticated:
                    await websocket.send(json.dumps({"status": "auth_required"}))
                    continue

                if message_type == "text":
                    text = str(payload.get("text", ""))
                    if text:
                        self.window.queue_remote_event({"type": "text", "text": text})
                        await websocket.send(json.dumps({"status": "ok", "type": "text"}))
                    else:
                        await websocket.send(json.dumps({"status": "error", "error": "invalid text"}))
                    continue

                if message_type == "key":
                    key = str(payload.get("key", "")).upper()
                    if key:
                        self.window.queue_remote_event({"type": "key", "key": key})
                        await websocket.send(json.dumps({"status": "ok", "type": "key", "key": key}))
                    else:
                        await websocket.send(json.dumps({"status": "error", "error": "invalid key"}))
                    continue

                if message_type == "pointer":
                    event_type = str(payload.get("event", "")).lower()
                    if event_type == "move":
                        try:
                            dx = int(round(float(payload.get("dx", 0))))
                            dy = int(round(float(payload.get("dy", 0))))
                        except (TypeError, ValueError):
                            dx = 0
                            dy = 0

                        if dx or dy:
                            self.window.queue_remote_event(
                                {"type": "pointer", "event": "move", "dx": dx, "dy": dy}
                            )
                            await websocket.send(
                                json.dumps({"status": "ok", "type": "pointer", "event": "move"})
                            )
                        else:
                            await websocket.send(json.dumps({"status": "error", "error": "invalid move"}))
                    elif event_type in ("tap", "click", "right_click"):
                        self.window.queue_remote_event({"type": "pointer", "event": event_type})
                        await websocket.send(
                            json.dumps({"status": "ok", "type": "pointer", "event": event_type})
                        )
                    elif event_type == "scroll":
                        try:
                            dx = int(round(float(payload.get("dx", 0))))
                            dy = int(round(float(payload.get("dy", 0))))
                        except (TypeError, ValueError):
                            dx = 0
                            dy = 0

                        if dx or dy:
                            self.window.queue_remote_event(
                                {"type": "pointer", "event": "scroll", "dx": dx, "dy": dy}
                            )
                            await websocket.send(
                                json.dumps({"status": "ok", "type": "pointer", "event": "scroll"})
                            )
                        else:
                            await websocket.send(json.dumps({"status": "error", "error": "invalid scroll"}))
                    else:
                        await websocket.send(json.dumps({"status": "error", "error": "invalid pointer event"}))
                    continue

                # Handle app listing request
                if message_type == "get_apps" or str(payload.get("action", "")).upper() == "GET_APPS":
                    apps_list = self.window.get_installed_apps()
                    await websocket.send(json.dumps({
                        "status": "ok",
                        "type": "apps_list",
                        "apps": apps_list
                    }))
                    continue

                # Handle add app request
                if message_type == "add_app":
                    app_kind = str(payload.get("kind", ""))
                    app_name = str(payload.get("name", "")).strip()
                    
                    if not app_name:
                        await websocket.send(json.dumps({"status": "error", "error": "app name required"}))
                        continue
                    
                    if app_kind == "native":
                        app_command = str(payload.get("command", "")).strip()
                        if not app_command:
                            await websocket.send(json.dumps({"status": "error", "error": "command required for native app"}))
                            continue
                        
                        # Schedule GUI work on main thread
                        QTimer.singleShot(0, lambda name=app_name, cmd=app_command: self.window.add_native_app(name, cmd, notify=False))
                        logging.info("Added native app: %s (%s)", app_name, app_command)
                        
                    elif app_kind == "web":
                        app_url = str(payload.get("url", "")).strip()
                        if not app_url:
                            await websocket.send(json.dumps({"status": "error", "error": "url required for web app"}))
                            continue
                        
                        # Schedule GUI work on main thread
                        QTimer.singleShot(0, lambda name=app_name, url=app_url: self.window.add_web_app(name, url, notify=False))
                        logging.info("Added web app: %s (%s)", app_name, app_url)
                    
                    await websocket.send(json.dumps({"status": "ok", "type": "app_added"}))
                    continue

                # Handle remove app request
                if message_type == "remove_app":
                    app_id = str(payload.get("id", "")).strip()
                    
                    if not app_id:
                        await websocket.send(json.dumps({"status": "error", "error": "app id required"}))
                        continue
                    
                    self.window.remove_app_by_id(app_id)
                    logging.info("Removed app: %s", app_id)
                    
                    await websocket.send(json.dumps({
                        "status": "ok", 
                        "type": "app_removed",
                        "message": f"App removed successfully"
                    }))
                    continue

                # Handle app launch request
                action = str(payload.get("action", ""))
                if action.startswith("LAUNCH_APP:"):
                    app_id = action.replace("LAUNCH_APP:", "")
                    logging.info("Launching app from remote: %s", app_id)
                    self.window.launch_app_by_id(app_id)
                    await websocket.send(json.dumps({"status": "ok", "action": "launch_app", "app_id": app_id}))
                    continue

                # Handle WiFi settings request
                if message_type == "get_wifi":
                    try:
                        networks, current_wifi, message = self.window.scan_wifi_networks()
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "wifi_list",
                            "networks": networks,
                            "current_wifi": current_wifi,
                            "message": message
                        }))
                    except Exception as exc:
                        logging.exception("Failed to scan WiFi from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "wifi_list",
                            "message": f"Failed to scan WiFi: {exc}"
                        }))
                    continue

                # Handle WiFi connect request
                if message_type == "connect_wifi":
                    ssid = str(payload.get("ssid", ""))
                    password = str(payload.get("password", ""))
                    security = str(payload.get("security", ""))
                    try:
                        success, message, current_wifi = self.window.connect_to_wifi(
                            {"ssid": ssid, "security": security}, password
                        )
                        await websocket.send(json.dumps({
                            "status": "ok" if success else "error",
                            "type": "wifi_connected",
                            "success": success,
                            "message": message,
                            "current_wifi": current_wifi
                        }))
                    except Exception as exc:
                        logging.exception("Failed to connect WiFi from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "wifi_connected",
                            "message": f"Failed to connect WiFi: {exc}"
                        }))
                    continue

                # Handle Bluetooth settings request
                if message_type == "get_bluetooth":
                    try:
                        devices, current_bluetooth, message = self.window.scan_bluetooth_devices()
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "bluetooth_list",
                            "devices": devices,
                            "current_bluetooth": current_bluetooth,
                            "message": message
                        }))
                    except Exception as exc:
                        logging.exception("Failed to scan Bluetooth from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "bluetooth_list",
                            "message": f"Failed to scan Bluetooth: {exc}"
                        }))
                    continue

                # Handle Bluetooth connect request
                if message_type == "connect_bluetooth":
                    mac = str(payload.get("mac", ""))
                    name = str(payload.get("name", ""))
                    try:
                        success, message, current_bluetooth = self.window.connect_to_bluetooth(
                            {"mac": mac, "name": name}
                        )
                        await websocket.send(json.dumps({
                            "status": "ok" if success else "error",
                            "type": "bluetooth_connected",
                            "success": success,
                            "message": message,
                            "current_bluetooth": current_bluetooth
                        }))
                    except Exception as exc:
                        logging.exception("Failed to connect Bluetooth from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "bluetooth_connected",
                            "message": f"Failed to connect Bluetooth: {exc}"
                        }))
                    continue

                # Handle Bluetooth remove request
                if message_type == "remove_bluetooth":
                    mac = str(payload.get("mac", ""))
                    try:
                        success, message = self.window.remove_bluetooth_device({"mac": mac})
                        await websocket.send(json.dumps({
                            "status": "ok" if success else "error",
                            "type": "bluetooth_removed",
                            "success": success,
                            "message": message
                        }))
                    except Exception as exc:
                        logging.exception("Failed to remove Bluetooth from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "bluetooth_removed",
                            "message": f"Failed to remove Bluetooth: {exc}"
                        }))
                    continue

                # Handle Sound settings request
                if message_type == "get_sound":
                    try:
                        speakers = self.window.get_audio_sinks()
                        default_sink = self.window.get_default_audio_sink()
                        message = f"Found {len(speakers)} audio device(s)" if speakers else "No audio devices found"
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "sound_list",
                            "speakers": speakers,
                            "default_sink": default_sink,
                            "message": message
                        }))
                    except Exception as exc:
                        logging.exception("Failed to get sound devices from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "sound_list",
                            "message": f"Failed to get sound devices: {exc}"
                        }))
                    continue

                # Handle Sound default set request
                if message_type == "set_sound":
                    sink_name = str(payload.get("sink", ""))
                    try:
                        success = self.window.set_default_audio_sink(sink_name)
                        await websocket.send(json.dumps({
                            "status": "ok" if success else "error",
                            "type": "sound_set",
                            "success": success,
                            "message": "Default audio device updated" if success else "Failed to set default audio device"
                        }))
                    except Exception as exc:
                        logging.exception("Failed to set sound device from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "sound_set",
                            "message": f"Failed to set sound device: {exc}"
                        }))
                    continue

                # Handle Volume get request
                if message_type == "get_volume":
                    try:
                        current_volume = get_current_volume()
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "volume_level",
                            "volume": current_volume,
                            "message": f"Current volume: {current_volume}%"
                        }))
                    except Exception as exc:
                        logging.exception("Failed to get volume from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "volume_level",
                            "message": f"Failed to get volume: {exc}"
                        }))
                    continue

                # Handle Brightness get request
                if message_type == "get_brightness":
                    try:
                        current_brightness = int(get_current_brightness() * 100)
                        await websocket.send(json.dumps({
                            "status": "ok",
                            "type": "brightness_level",
                            "brightness": current_brightness,
                            "message": f"Current brightness: {current_brightness}%"
                        }))
                    except Exception as exc:
                        logging.exception("Failed to get brightness from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "brightness_level",
                            "message": f"Failed to get brightness: {exc}"
                        }))
                    continue

                # Handle Brightness set request
                if message_type == "set_brightness":
                    brightness_level = int(payload.get("brightness", 50))
                    try:
                        success = control_system_brightness("SET_BRIGHTNESS", brightness_level)
                        await websocket.send(json.dumps({
                            "status": "ok" if success else "error",
                            "type": "brightness_set",
                            "success": success,
                            "brightness": brightness_level,
                            "message": f"Brightness set to {brightness_level}%" if success else "Failed to set brightness"
                        }))
                    except Exception as exc:
                        logging.exception("Failed to set brightness from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "brightness_set",
                            "message": f"Failed to set brightness: {exc}"
                        }))
                    continue

                action = action.upper()
                if action:
                    self.window.queue_remote_action(action)
                    await websocket.send(json.dumps({"status": "ok", "action": action}))
                else:
                    await websocket.send(json.dumps({"status": "error", "error": "invalid action"}))
        except Exception as exc:
            logging.warning("WebSocket client disconnected: %s", exc)

    async def _run_server(self):
        if websockets is None:
            logging.error("websockets library not installed; remote control disabled")
            return
        
        self.server = await websockets.serve(
            self.handler, 
            self.host, 
            self.port
        )
        
        logging.info("WebSocket remote server started on ws://%s:%s", self.host, self.port)
        try:
            await self.server.wait_closed()
        except asyncio.CancelledError:
            pass

    def run(self):
        if websockets is None:
            return
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._run_server())
            self.loop.run_forever()
        except RuntimeError:
            # Event loop was stopped before future completed - this is expected during shutdown
            pass
        finally:
            # Ensure all tasks are cancelled before closing
            if self.loop.is_running():
                self.loop.stop()
            pending = asyncio.all_tasks(self.loop)
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def stop(self):
        self._stop_event.set()
        if self.loop and self.loop.is_running():
            # Close the server first
            if self.server:
                self.server.close()
                self.loop.call_soon_threadsafe(lambda: self.loop.create_task(self.server.wait_closed()))
            # Then stop the loop
            self.loop.call_soon_threadsafe(self.loop.stop)


class TileButton(QPushButton):
    SHELL_PADDING_X = 16
    SHELL_PADDING_Y = 16

    def __init__(self, name: str, icon_path: str, tooltip: str = "", variant: str = "default", subtitle: str = "", metrics=None):
        # Show only app name, not subtitle
        button_text = name
        super().__init__(button_text)
        metrics = metrics or {}
        self.variant = variant
        self.title = name
        self.subtitle = subtitle
        tile_width = int(metrics.get("tile_width", 400))
        tile_height = int(metrics.get("tile_height", 230))
        tile_font = int(metrics.get("tile_font_size", 19))
        # Reduce icon size by 20%
        tile_icon = int(metrics.get("tile_icon_size", 130) * 0.8)
        self.base_size = QSize(tile_width, tile_height)
        self.shell_size = QSize(
            self.base_size.width() + (self.SHELL_PADDING_X * 2),
            self.base_size.height() + (self.SHELL_PADDING_Y * 2),
        )
        self._rest_rect = QRect(
            self.SHELL_PADDING_X,
            self.SHELL_PADDING_Y,
            self.base_size.width(),
            self.base_size.height(),
        )
        self._focus_rect = QRect(0, 0, self.shell_size.width(), self.shell_size.height())
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.card_shell = None
        self.row_scroll = None
        self.section_widget = None
        self.entry_kind = ""
        self.entry_item = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # Allow parent to handle mouse events for drag and drop
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setMinimumSize(self.base_size)
        self.setMaximumSize(self.shell_size)
        self.setFont(QFont("Sans Serif", tile_font, QFont.Bold))
        self.setIconSize(QSize(tile_icon, tile_icon))
        self.set_tile_icon(icon_path)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("tileVariant", variant)
        self.setProperty("hasSubtitle", "true" if subtitle else "false")
        self.setStyleSheet("")
        self.setGeometry(self._rest_rect)
        
        # Ripple effect attributes
        self._ripple_radius = 0
        self.ripple_pos = None
        self.ripple_opacity = 0
        self.ripple_timer = None

    def _get_ripple_radius(self):
        return self._ripple_radius
        
    def _set_ripple_radius(self, value):
        self._ripple_radius = value
        self.update()  # Trigger repaint
        
    ripple_radius = property(_get_ripple_radius, _set_ripple_radius)

    def mousePressEvent(self, event):
        """Start ripple animation on click"""
        self.ripple_pos = event.pos()
        self.ripple_radius = 0
        self.ripple_opacity = 0.3

        # Create ripple animation using QTimer (QPropertyAnimation doesn't work with Python properties)
        if self.ripple_timer:
            self.ripple_timer.stop()

        self.ripple_timer = QTimer(self)
        self.ripple_timer.setSingleShot(False)
        target_radius = max(self.width(), self.height()) * 1.5
        duration_ms = 400
        interval_ms = 16  # ~60 FPS
        step = target_radius / (duration_ms / interval_ms)

        def animate_ripple():
            if self.ripple_radius >= target_radius:
                self.ripple_timer.stop()
                self.ripple_timer = None
            else:
                self.ripple_radius = min(self.ripple_radius + step, target_radius)

        self.ripple_timer.timeout.connect(animate_ripple)
        self.ripple_timer.start(interval_ms)

        super().mousePressEvent(event)
        
    def paintEvent(self, event):
        """Paint button with ripple effect"""
        super().paintEvent(event)
        
        # Draw ripple if animating
        if self.ripple_pos and self.ripple_opacity > 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            ripple_color = QColor(255, 255, 255, int(self.ripple_opacity * 255))
            painter.setBrush(ripple_color)
            painter.setPen(Qt.NoPen)
            
            ripple_rect = QRect(
                int(self.ripple_pos.x() - self.ripple_radius),
                int(self.ripple_pos.y() - self.ripple_radius),
                int(self.ripple_radius * 2),
                int(self.ripple_radius * 2)
            )
            painter.drawEllipse(ripple_rect)
            painter.end()
            
            # Fade out ripple
            self.ripple_opacity *= 0.95
            if self.ripple_opacity < 0.01:
                self.ripple_opacity = 0
                self.ripple_pos = None

    def set_tile_icon(self, icon_path: str):
        if not icon_path:
            self.setIcon(QIcon())
            return

        path = Path(icon_path).expanduser()
        if not path.is_absolute():
            path = resource_path(icon_path)
        if path.exists():
            self.setIcon(QIcon(str(path)))

    def update_geometry_targets(self, shell_rect: QRect):
        # Make focus rect same as rest rect to prevent size changes
        self._focus_rect = QRect(0, 0, shell_rect.width(), shell_rect.height())
        self._rest_rect = QRect(0, 0, shell_rect.width(), shell_rect.height())
        self._anim.stop()
        self.setGeometry(self._focus_rect if self.hasFocus() else self._rest_rect)

    def animate_focus(self, focused: bool):
        """Animate focus state - now only visual, no size change"""
        # No geometry animation needed since sizes are the same
        if focused and self.parentWidget():
            self.parentWidget().raise_()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.animate_focus(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.animate_focus(False)


class DropRowWidget(QWidget):
    """Custom widget container for app cards - does not handle drops"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Do not accept drops - let children handle them
        self.setAcceptDrops(False)


class AppCard(QWidget):
    _drag_source = None  # Class variable to track current drag source
    
    def __init__(self, tile_button: TileButton, edit_callback=None, delete_callback=None, favorite_callback=None, reorder_callback=None, show_actions: bool = True, metrics=None, app_data=None, kind=None):
        super().__init__()
        metrics = metrics or {}
        action_button_size = int(metrics.get("action_button_size", 44))
        shadow_radius = int(metrics.get("card_shadow_radius", 10))
        self.setObjectName("appCardShell")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(tile_button.shell_size)
        
        # Store app data for drag-and-drop
        self.app_data = app_data
        self.kind = kind

        # Add material design drop shadow
        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(shadow_radius)
        self.shadow_effect.setXOffset(0)
        self.shadow_effect.setYOffset(4)
        self.shadow_effect.setColor(QColor(0, 0, 0, 80))
        self.setGraphicsEffect(self.shadow_effect)

        self.tile_button = tile_button
        self.tile_button.setParent(self)
        self.tile_button.update_geometry_targets(self.rect())

        self.edit_button = None
        self.delete_button = None
        self.favorite_button = None
        if show_actions:
            self.favorite_button = QToolButton(self)
            self.favorite_button.setObjectName("favoriteButton")
            self.favorite_button.setText("☆")  # Empty star by default
            self.favorite_button.setToolTip("Add to favorites")
            self.favorite_button.clicked.connect(favorite_callback)
            self.favorite_button.setCursor(Qt.PointingHandCursor)
            self.favorite_button.setFocusPolicy(Qt.NoFocus)
            self.favorite_button.setFixedSize(action_button_size, action_button_size)
            
            self.edit_button = QToolButton(self)
            self.edit_button.setObjectName("editButton")
            self.edit_button.setText("⚙")
            self.edit_button.setToolTip("Edit this app")
            self.edit_button.clicked.connect(edit_callback)
            self.edit_button.setCursor(Qt.PointingHandCursor)
            self.edit_button.setFocusPolicy(Qt.NoFocus)
            self.edit_button.setFixedSize(action_button_size, action_button_size)

            self.delete_button = QToolButton(self)
            self.delete_button.setObjectName("deleteButton")
            self.delete_button.setText("✕")
            self.delete_button.setToolTip("Delete this app")
            self.delete_button.clicked.connect(delete_callback)
            self.delete_button.setCursor(Qt.PointingHandCursor)
            self.delete_button.setFocusPolicy(Qt.NoFocus)
            self.delete_button.setFixedSize(action_button_size, action_button_size)

        # Enable drag and drop
        self.setAcceptDrops(True)
        self._is_dragging = False
        self._drag_start_pos = None
        self._drag_source = None  # Track drag source

        # Install event filter on tile_button to intercept mouse events for drag
        self.tile_button.installEventFilter(self)

    def eventFilter(self, watched, event):
        """Intercept mouse events from tile_button for drag handling"""
        if watched == self.tile_button:
            if event.type() == QEvent.MouseButtonPress:
                # Map coordinates from tile_button to app_card
                mapped_event = self._map_mouse_event(event)
                self.mousePressEvent(mapped_event)
                # Don't return True - let the button also handle it
            elif event.type() == QEvent.MouseMove:
                # Map coordinates from tile_button to app_card
                mapped_event = self._map_mouse_event(event)
                self.mouseMoveEvent(mapped_event)
                # If we're dragging, don't pass to button
                if self._is_dragging:
                    return True
            elif event.type() == QEvent.MouseButtonRelease:
                # Map coordinates from tile_button to app_card
                mapped_event = self._map_mouse_event(event)
                self.mouseReleaseEvent(mapped_event)
                if self._is_dragging:
                    return True
        return super().eventFilter(watched, event)

    def _map_mouse_event(self, event):
        """Map mouse event coordinates from tile_button to app_card"""
        # Get the tile_button's position relative to the app_card
        button_pos = self.tile_button.pos()
        # Create a new event with mapped coordinates
        mapped_pos = event.pos() + button_pos
        if QT_BINDING == "PyQt5":
            from PyQt5.QtCore import QPoint
            from PyQt5.QtGui import QMouseEvent
        else:
            from PySide6.QtCore import QPoint
            from PySide6.QtGui import QMouseEvent
        return QMouseEvent(
            event.type(),
            QPoint(mapped_pos.x(), mapped_pos.y()),
            event.globalPos(),
            event.button(),
            event.buttons(),
            event.modifiers()
        )

    def sizeHint(self):
        return self.tile_button.shell_size

    def minimumSizeHint(self):
        return self.tile_button.shell_size

    def resizeEvent(self, event):
        self.tile_button.update_geometry_targets(self.rect())
        if self.favorite_button is not None and self.edit_button is not None and self.delete_button is not None:
            # Align all buttons horizontally at the top right
            button_y = 12
            button_spacing = 6
            button_width = self.delete_button.width()
            
            # Calculate total width needed for 3 buttons
            total_buttons_width = 3 * button_width + 2 * button_spacing
            
            # Position from right to left: delete, edit, favorite
            self.delete_button.move(self.width() - button_width - 12, button_y)
            self.edit_button.move(self.width() - button_width - button_width - 12 - button_spacing, button_y)
            self.favorite_button.move(self.width() - 2*button_width - button_width - 12 - 2*button_spacing, button_y)
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        """Start drag on mouse press"""
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Reset drag state on mouse release"""
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Initiate drag operation"""
        if not (event.buttons() & Qt.LeftButton):
            return
        if not self._drag_start_pos:
            return

        # Check if mouse has moved far enough to start drag
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        # Don't start drag if clicking on buttons
        for btn in [self.favorite_button, self.edit_button, self.delete_button]:
            if btn and btn.geometry().contains(event.pos()):
                return

        # Allow drag even if clicking on tile_button (that's the main interaction area)
        
        self._is_dragging = True
        AppCard._drag_source = self  # Store as class variable
        
        # Create drag data with app info
        drag = QDrag(self)
        mime_data = QMimeData()
        # Store app info as JSON for reliable transfer
        import json
        app_info = {
            "app_id": self.get_app_id(),
            "kind": self.kind,
            "source_id": str(id(self)),
        }
        mime_data.setText(json.dumps(app_info))
        drag.setMimeData(mime_data)
        
        # Create drag pixmap (semi-transparent snapshot)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        
        # Execute drag and get result
        result = drag.exec_(Qt.MoveAction)
        
        # Reset drag state
        self._is_dragging = False
        self._drag_start_pos = None
        AppCard._drag_source = None
    
    def get_app_id(self):
        """Get unique identifier for the app in this card"""
        if self.app_data:
            if self.kind == "native":
                return self.app_data.get("cmd", "")
            else:
                return self.app_data.get("url", "")
        return ""

    def dragEnterEvent(self, event):
        """Accept drag events"""
        if event.mimeData().hasText():
            try:
                import json
                data = json.loads(event.mimeData().text())
                if "app_id" in data and "kind" in data:
                    event.accept()
            except (json.JSONDecodeError, KeyError):
                event.ignore()

    def dragMoveEvent(self, event):
        """Handle drag move"""
        if event.mimeData().hasText():
            try:
                import json
                data = json.loads(event.mimeData().text())
                if "app_id" in data and "kind" in data:
                    event.accept()
            except (json.JSONDecodeError, KeyError):
                event.ignore()

    def dropEvent(self, event):
        """Handle drop - trigger reorder"""
        import json
        import logging
        try:
            data = json.loads(event.mimeData().text())
            logging.info(f"Drop event received: {data}")

            if "app_id" in data and "kind" in data:
                # Use the tracked drag source
                source_card = AppCard._drag_source
                if not source_card:
                    logging.warning("No drag source tracked")
                    event.ignore()
                    return

                event.accept()
                logging.info(f"Dropped on card: {self.app_data}, calling handler")

                # Notify parent to handle reorder - look up the parent chain
                parent = self.parentWidget()
                while parent:
                    if hasattr(parent, 'handle_card_drop'):
                        parent.handle_card_drop(data, self.app_data, self.kind)
                        break
                    parent = parent.parentWidget()
            else:
                event.ignore()
        except (json.JSONDecodeError, KeyError) as e:
            logging.error(f"Drop event error: {e}")
            event.ignore()

    def enterEvent(self, event):
        """Enhance shadow on hover"""
        try:
            if hasattr(self, 'shadow_effect') and self.shadow_effect is not None:
                self.shadow_effect.setBlurRadius(self.shadow_effect.blurRadius() + 4)
                self.shadow_effect.setYOffset(6)
                self.shadow_effect.setColor(QColor(0, 0, 0, 100))
        except RuntimeError:
            # Shadow effect was deleted
            pass
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Restore normal shadow on leave"""
        try:
            if hasattr(self, 'shadow_effect') and self.shadow_effect is not None:
                # Safely traverse parent chain to get ui_metrics
                metrics = {}
                parent = self.tile_button.parent()
                if parent and hasattr(parent, 'parent'):
                    grandparent = parent.parent()
                    if grandparent and hasattr(grandparent, 'ui_metrics'):
                        metrics = grandparent.ui_metrics
                
                shadow_radius = metrics.get("card_shadow_radius", 10)
                self.shadow_effect.setBlurRadius(shadow_radius)
                self.shadow_effect.setYOffset(4)
                self.shadow_effect.setColor(QColor(0, 0, 0, 80))
        except RuntimeError:
            # Shadow effect was deleted
            pass
        super().leaveEvent(event)


class ParallaxBackground(QWidget):
    """Custom widget with animated parallax gradient background"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scroll_position = 0
        self.setObjectName("parallaxBackground")
        
    def set_scroll_position(self, position):
        """Update scroll position for gradient animation"""
        self.scroll_position = position
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Create animated gradient based on scroll position
        gradient = QLinearGradient(0, 0, 0, self.height())
        
        # Subtle color shifts based on scroll using dark gray slate theme
        offset = (self.scroll_position % 1000) / 1000.0
        
        gradient.setColorAt(0.0, QColor(15, 23, 42))
        gradient.setColorAt(0.3 + offset * 0.1, QColor(30, 41, 59))
        gradient.setColorAt(0.7 + offset * 0.1, QColor(51, 65, 85))
        gradient.setColorAt(1.0, QColor(15, 23, 42))
        
        painter.fillRect(self.rect(), gradient)
        painter.end()


class IconUpdateBridge(QObject):
    icon_ready = Signal(int, object, str)


class AddItemDialog(QDialog):
    def __init__(self, parent=None, title_text="Add To Apps", type_text="Application", name_text="", value_text="", allow_type_change=True):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.setFixedWidth(metrics["add_width"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(14)

        title = QLabel("Add Launcher")
        title.setObjectName("dialogSection")
        title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Create a launcher for an installed app command or a website.")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(subtitle)

        type_label = QLabel("Type")
        type_label.setObjectName("dialogFieldLabel")
        layout.addWidget(type_label)

        self.type_select = QComboBox()
        self.type_select.setMinimumHeight(metrics["input_min_height"])
        self.type_select.addItems(["Application", "Website"])
        layout.addWidget(self.type_select)

        name_label = QLabel("Name")
        name_label.setObjectName("dialogFieldLabel")
        layout.addWidget(name_label)

        self.name_input = QLineEdit()
        self.name_input.setMinimumHeight(metrics["input_min_height"])
        self.name_input.setPlaceholderText("Spotify, YouTube, Kodi...")
        layout.addWidget(self.name_input)

        self.value_label = QLabel("Launch command")
        self.value_label.setObjectName("dialogFieldLabel")
        layout.addWidget(self.value_label)

        self.value_input = QLineEdit()
        self.value_input.setMinimumHeight(metrics["input_min_height"])
        self.value_input.setPlaceholderText("flatpak run com.spotify.Client")
        layout.addWidget(self.value_input)

        self.helper_label = QLabel("Tip: Flatpak apps work too, for example `flatpak run com.spotify.Client`.")
        self.helper_label.setObjectName("dialogHelper")
        self.helper_label.setWordWrap(True)
        layout.addWidget(self.helper_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        cancel_button = QPushButton("Cancel")
        cancel_button.setProperty("tileVariant", "dialogSecondary")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        save_button = QPushButton("Save")
        save_button.setProperty("tileVariant", "accent")
        save_button.clicked.connect(self.accept)
        button_row.addWidget(save_button)

        layout.addLayout(button_row)
        self.type_select.currentTextChanged.connect(self.update_mode)
        self.type_select.setCurrentText(type_text)
        self.type_select.setEnabled(allow_type_change)
        self.name_input.setText(name_text)
        self.value_input.setText(value_text)
        self.update_mode(self.type_select.currentText())

        self.setStyleSheet(dialog_stylesheet(metrics))

    def update_mode(self, mode_text: str):
        if mode_text == "Website":
            self.value_label.setText("Website URL")
            self.value_input.setPlaceholderText("https://www.youtube.com")
            self.helper_label.setText("Add a full URL or just a domain. LinuxTV will normalize it to HTTPS.")
        else:
            self.value_label.setText("Launch command")
            self.value_input.setPlaceholderText("flatpak run com.spotify.Client")
            self.helper_label.setText("Tip: Flatpak apps work too, for example `flatpak run com.spotify.Client`.")

    def values(self):
        return {
            "type": self.type_select.currentText(),
            "name": self.name_input.text().strip(),
            "value": self.value_input.text().strip(),
        }


class ConfirmDialog(QDialog):
    def __init__(self, parent=None, title_text="Confirm Delete", body_text="Delete this app?"):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle(title_text)
        self.setModal(True)
        self.setFixedWidth(metrics["confirm_width"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(14)

        title = QLabel("Confirm")
        title.setObjectName("dialogSection")
        title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(title)

        body = QLabel(body_text)
        body.setObjectName("dialogSubtitle")
        body.setWordWrap(True)
        body.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(body)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        cancel_button = QPushButton("Cancel")
        cancel_button.setProperty("tileVariant", "dialogSecondary")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        delete_button = QPushButton("Delete")
        delete_button.setProperty("tileVariant", "danger")
        delete_button.clicked.connect(self.accept)
        button_row.addWidget(delete_button)

        layout.addLayout(button_row)

        self.setStyleSheet(dialog_stylesheet(metrics))


class NetworkDialog(QDialog):
    wifi_scan_finished = Signal(object, str, str)

    def __init__(
        self,
        wifi_networks=None,
        current_wifi="",
        wifi_refresh_callback=None,
        wifi_connect_callback=None,
        wifi_remove_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle("Network Settings")
        self.setModal(True)
        self.setFixedWidth(metrics["settings_width"])

        wifi_networks = wifi_networks or []
        self.wifi_refresh_callback = wifi_refresh_callback
        self.wifi_connect_callback = wifi_connect_callback
        self.wifi_remove_callback = wifi_remove_callback
        self._wifi_scan_in_progress = False
        self._wifi_has_loaded = bool(wifi_networks or current_wifi)
        self._wifi_last_scan_time = 0.0
        self.wifi_scan_finished.connect(self.handle_wifi_scan_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(12)

        wifi_title = QLabel("Wi-Fi")
        wifi_title.setObjectName("dialogSection")
        wifi_title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(wifi_title)

        wifi_subtitle = QLabel("Scan for nearby networks, enter a password if needed, and connect without leaving LinuxTV.")
        wifi_subtitle.setObjectName("dialogSubtitle")
        wifi_subtitle.setWordWrap(True)
        wifi_subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(wifi_subtitle)

        self.wifi_combo = QComboBox()
        self.wifi_combo.setEditable(True)
        self.wifi_combo.setMinimumHeight(metrics["input_min_height"])
        self._style_settings_combo_popup(self.wifi_combo)
        self.wifi_combo.lineEdit().setPlaceholderText("Select or type a Wi-Fi network name")
        layout.addWidget(self.wifi_combo)

        self.wifi_password_input = QLineEdit()
        self.wifi_password_input.setPlaceholderText("Wi-Fi password")
        self.wifi_password_input.setEchoMode(QLineEdit.Password)
        self.wifi_password_input.setMinimumHeight(metrics["input_min_height"])
        layout.addWidget(self.wifi_password_input)

        wifi_button_row = QHBoxLayout()
        self.refresh_wifi_button = QPushButton("Refresh Networks")
        self.refresh_wifi_button.setProperty("tileVariant", "dialogSecondary")
        self.refresh_wifi_button.clicked.connect(self.refresh_wifi_networks)
        wifi_button_row.addWidget(self.refresh_wifi_button)

        self.forget_wifi_button = QPushButton("Forget Network")
        self.forget_wifi_button.setProperty("tileVariant", "dialogSecondary")
        self.forget_wifi_button.clicked.connect(self.forget_wifi)
        wifi_button_row.addWidget(self.forget_wifi_button)

        self.connect_wifi_button = QPushButton("Connect Wi-Fi")
        self.connect_wifi_button.setProperty("tileVariant", "accent")
        self.connect_wifi_button.clicked.connect(self.connect_wifi)
        wifi_button_row.addWidget(self.connect_wifi_button)
        layout.addLayout(wifi_button_row)

        self.wifi_status_label = QLabel("")
        self.wifi_status_label.setObjectName("dialogStatus")
        self.wifi_status_label.setWordWrap(True)
        layout.addWidget(self.wifi_status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        close_button = QPushButton("Close")
        close_button.setProperty("tileVariant", "dialogSecondary")
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)
        self.set_wifi_networks(wifi_networks, current_wifi)
        self.set_wifi_loading_state(False)

        self.setStyleSheet(dialog_stylesheet(metrics))

    def _style_settings_combo_popup(self, combo: QComboBox):
        popup = combo.view()
        if popup is None:
            return
        popup.setStyleSheet(
            """
            background-color: rgba(15, 23, 42, 0.98);
            color: #f1f5f9;
            border: 1px solid rgba(71, 85, 105, 0.5);
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
            alternate-background-color: rgba(30, 41, 59, 0.6);
            font-size: 14px;
            """
        )

    def set_wifi_loading_state(self, loading: bool, message=""):
        self._wifi_scan_in_progress = loading
        self.refresh_wifi_button.setEnabled(not loading and bool(self.wifi_refresh_callback))
        self.connect_wifi_button.setEnabled(not loading and bool(self.wifi_connect_callback))
        self.forget_wifi_button.setEnabled(not loading and bool(self.wifi_remove_callback))
        if loading and message:
            self.wifi_status_label.setText(message)

    def ensure_wifi_networks_loaded(self, force=False):
        if not self.wifi_refresh_callback:
            self.set_wifi_loading_state(False)
            self.wifi_status_label.setText("Wi-Fi scanning is not available on this system.")
            return
        if self._wifi_scan_in_progress:
            return
        # Check if cache is still valid (2 minutes)
        import time
        cache_valid = self._wifi_has_loaded and (time.time() - self._wifi_last_scan_time < 120)
        if cache_valid and not force:
            return
        status_text = "Refreshing Wi-Fi networks..." if force else "Fetching nearby Wi-Fi networks..."
        self.set_wifi_loading_state(True, status_text)
        threading.Thread(
            target=self._run_wifi_scan,
            name="wifi-settings-scan",
            daemon=True,
        ).start()

    def _run_wifi_scan(self):
        try:
            networks, current_wifi, message = self.wifi_refresh_callback()
        except Exception as exc:
            logging.exception("Failed to refresh Wi-Fi networks from settings")
            networks, current_wifi, message = [], "", f"Could not scan for Wi-Fi networks: {exc}"
        self.wifi_scan_finished.emit(networks, current_wifi, message)

    def handle_wifi_scan_finished(self, wifi_networks, current_wifi, message):
        self.set_wifi_loading_state(False)
        self._wifi_has_loaded = True
        import time
        self._wifi_last_scan_time = time.time()
        self.set_wifi_networks(wifi_networks or [], current_wifi)
        if message:
            self.wifi_status_label.setText(message)

    def set_wifi_networks(self, wifi_networks, current_wifi=""):
        current_text = self.wifi_combo.currentText().strip()
        self.wifi_combo.blockSignals(True)
        self.wifi_combo.clear()
        selected_index = -1
        for index, option in enumerate(wifi_networks):
            label = option.get("label", option.get("ssid", ""))
            ssid = option.get("ssid", "")
            self.wifi_combo.addItem(label, dict(option))
            if current_wifi and ssid == current_wifi:
                selected_index = index
        self.wifi_combo.blockSignals(False)

        if selected_index >= 0:
            self.wifi_combo.setCurrentIndex(selected_index)
            self.wifi_status_label.setText(f"Connected network: {current_wifi}")
            return

        if current_text:
            self.wifi_combo.setEditText(current_text)
        elif current_wifi:
            self.wifi_combo.setEditText(current_wifi)
            self.wifi_status_label.setText(f"Connected network: {current_wifi}")
        elif wifi_networks:
            self.wifi_combo.setCurrentIndex(0)
            self.wifi_status_label.setText("Choose a network and connect from here.")
        else:
            self.wifi_combo.setEditText("")
            self.wifi_status_label.setText("No Wi-Fi networks loaded yet. Open or refresh this section to scan.")

    def refresh_wifi_networks(self):
        self.ensure_wifi_networks_loaded(force=True)

    def connect_wifi(self):
        if self._wifi_scan_in_progress:
            self.wifi_status_label.setText("Still fetching nearby Wi-Fi networks. Try again in a moment.")
            return
        if not self.wifi_connect_callback:
            self.wifi_status_label.setText("Wi-Fi connection is not available on this system.")
            return
        selected_network = self.wifi_combo.currentData()
        if not isinstance(selected_network, dict):
            selected_network = {"ssid": self.wifi_combo.currentText().strip(), "security": ""}
        password = self.wifi_password_input.text()
        success, message, current_wifi = self.wifi_connect_callback(selected_network, password)
        if current_wifi:
            self.refresh_wifi_networks()
        self.wifi_status_label.setText(message)
        if success:
            self.wifi_password_input.clear()

    def forget_wifi(self):
        # Forget/remove a Wi-Fi network.
        if self._wifi_scan_in_progress:
            self.wifi_status_label.setText("Still fetching nearby Wi-Fi networks. Try again in a moment.")
            return
        if not self.wifi_remove_callback:
            self.wifi_status_label.setText("Wi-Fi network removal is not available on this system.")
            return
        selected_network = self.wifi_combo.currentData()
        if not isinstance(selected_network, dict):
            selected_network = {"ssid": self.wifi_combo.currentText().strip(), "security": ""}
        
        ssid = selected_network.get("ssid", "").strip()
        if not ssid:
            self.wifi_status_label.setText("Select a network to forget.")
            return
        
        success, message = self.wifi_remove_callback(selected_network)
        if success:
            self.refresh_wifi_networks()
        self.wifi_status_label.setText(message)


class BluetoothDialog(QDialog):
    bluetooth_scan_finished = Signal(object, str, str)

    def __init__(
        self,
        bluetooth_devices=None,
        current_bluetooth="",
        bluetooth_refresh_callback=None,
        bluetooth_connect_callback=None,
        bluetooth_remove_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle("Bluetooth Settings")
        self.setModal(True)
        self.setFixedWidth(metrics["settings_width"])

        bluetooth_devices = bluetooth_devices or []
        self.bluetooth_refresh_callback = bluetooth_refresh_callback
        self.bluetooth_connect_callback = bluetooth_connect_callback
        self.bluetooth_remove_callback = bluetooth_remove_callback
        self._bluetooth_scan_in_progress = False
        self._bluetooth_has_loaded = bool(bluetooth_devices or current_bluetooth)
        self._bluetooth_last_scan_time = 0.0
        self.bluetooth_scan_finished.connect(self.handle_bluetooth_scan_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(12)

        bluetooth_title = QLabel("Bluetooth")
        bluetooth_title.setObjectName("dialogSection")
        bluetooth_title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(bluetooth_title)

        bluetooth_subtitle = QLabel("Scan for nearby Bluetooth devices and connect without leaving LinuxTV.")
        bluetooth_subtitle.setObjectName("dialogSubtitle")
        bluetooth_subtitle.setWordWrap(True)
        bluetooth_subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(bluetooth_subtitle)

        self.bluetooth_combo = QComboBox()
        self.bluetooth_combo.setEditable(True)
        self.bluetooth_combo.setMinimumHeight(metrics["input_min_height"])
        self._style_settings_combo_popup(self.bluetooth_combo)
        self.bluetooth_combo.lineEdit().setPlaceholderText("Select or type a Bluetooth device or MAC address")
        layout.addWidget(self.bluetooth_combo)

        bluetooth_button_row = QHBoxLayout()
        self.refresh_bluetooth_button = QPushButton("Refresh Devices")
        self.refresh_bluetooth_button.setProperty("tileVariant", "dialogSecondary")
        self.refresh_bluetooth_button.clicked.connect(self.refresh_bluetooth_devices)
        bluetooth_button_row.addWidget(self.refresh_bluetooth_button)

        self.remove_bluetooth_button = QPushButton("Remove Device")
        self.remove_bluetooth_button.setProperty("tileVariant", "dialogSecondary")
        self.remove_bluetooth_button.clicked.connect(self.remove_bluetooth)
        bluetooth_button_row.addWidget(self.remove_bluetooth_button)

        self.connect_bluetooth_button = QPushButton("Connect Device")
        self.connect_bluetooth_button.setProperty("tileVariant", "accent")
        self.connect_bluetooth_button.clicked.connect(self.connect_bluetooth)
        bluetooth_button_row.addWidget(self.connect_bluetooth_button)
        layout.addLayout(bluetooth_button_row)

        self.bluetooth_status_label = QLabel("")
        self.bluetooth_status_label.setObjectName("dialogStatus")
        self.bluetooth_status_label.setWordWrap(True)
        layout.addWidget(self.bluetooth_status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        close_button = QPushButton("Close")
        close_button.setProperty("tileVariant", "dialogSecondary")
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)
        self.set_bluetooth_devices(bluetooth_devices or [], current_bluetooth)
        self.set_bluetooth_loading_state(False)

        self.setStyleSheet(dialog_stylesheet(metrics))

    def _style_settings_combo_popup(self, combo: QComboBox):
        popup = combo.view()
        if popup is None:
            return
        popup.setStyleSheet(
            """
            background-color: rgba(15, 23, 42, 0.98);
            color: #f1f5f9;
            border: 1px solid rgba(71, 85, 105, 0.5);
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
            alternate-background-color: rgba(30, 41, 59, 0.6);
            font-size: 14px;
            """
        )

    def set_bluetooth_loading_state(self, loading: bool, message=""):
        self._bluetooth_scan_in_progress = loading
        self.refresh_bluetooth_button.setEnabled(not loading and bool(self.bluetooth_refresh_callback))
        self.connect_bluetooth_button.setEnabled(not loading and bool(self.bluetooth_connect_callback))
        self.remove_bluetooth_button.setEnabled(not loading and bool(self.bluetooth_remove_callback))
        if loading and message:
            self.bluetooth_status_label.setText(message)

    def ensure_bluetooth_devices_loaded(self, force=False):
        if not self.bluetooth_refresh_callback:
            self.set_bluetooth_loading_state(False)
            self.bluetooth_status_label.setText("Bluetooth scanning is not available on this system.")
            return
        if self._bluetooth_scan_in_progress:
            return
        # Check if cache is still valid (2 minutes)
        import time
        cache_valid = self._bluetooth_has_loaded and (time.time() - self._bluetooth_last_scan_time < 120)
        if cache_valid and not force:
            return
        status_text = "Refreshing Bluetooth devices..." if force else "Fetching nearby Bluetooth devices..."
        self.set_bluetooth_loading_state(True, status_text)
        threading.Thread(
            target=self._run_bluetooth_scan,
            name="bluetooth-settings-scan",
            daemon=True,
        ).start()

    def _run_bluetooth_scan(self):
        try:
            devices, current_bluetooth, message = self.bluetooth_refresh_callback()
        except Exception as exc:
            logging.exception("Failed to refresh Bluetooth devices from settings")
            devices, current_bluetooth, message = [], "", f"Could not scan for Bluetooth devices: {exc}"
        self.bluetooth_scan_finished.emit(devices, current_bluetooth, message)

    def handle_bluetooth_scan_finished(self, bluetooth_devices, current_bluetooth, message):
        self.set_bluetooth_loading_state(False)
        self._bluetooth_has_loaded = True
        import time
        self._bluetooth_last_scan_time = time.time()
        self.set_bluetooth_devices(bluetooth_devices or [], current_bluetooth)
        if message:
            self.bluetooth_status_label.setText(message)

    def set_bluetooth_devices(self, bluetooth_devices, current_bluetooth=""):
        current_text = self.bluetooth_combo.currentText().strip()
        self.bluetooth_combo.blockSignals(True)
        self.bluetooth_combo.clear()
        selected_index = -1
        for index, option in enumerate(bluetooth_devices):
            label = option.get("label", option.get("name", ""))
            mac = option.get("mac", "")
            self.bluetooth_combo.addItem(label, dict(option))
            if current_bluetooth and mac == current_bluetooth:
                selected_index = index
        self.bluetooth_combo.blockSignals(False)

        if selected_index >= 0:
            self.bluetooth_combo.setCurrentIndex(selected_index)
            self.bluetooth_status_label.setText(f"Connected device: {current_bluetooth}")
            return

        if current_text:
            self.bluetooth_combo.setEditText(current_text)
        elif current_bluetooth:
            self.bluetooth_combo.setEditText(current_bluetooth)
            self.bluetooth_status_label.setText(f"Connected device: {current_bluetooth}")
        elif bluetooth_devices:
            self.bluetooth_combo.setCurrentIndex(0)
            self.bluetooth_status_label.setText("Choose a device and connect from here.")
        else:
            self.bluetooth_combo.setEditText("")
            self.bluetooth_status_label.setText("No Bluetooth devices loaded yet. Open or refresh this section to scan.")

    def refresh_bluetooth_devices(self):
        self.ensure_bluetooth_devices_loaded(force=True)

    def connect_bluetooth(self):
        if self._bluetooth_scan_in_progress:
            self.bluetooth_status_label.setText("Still fetching nearby Bluetooth devices. Try again in a moment.")
            return
        if not self.bluetooth_connect_callback:
            self.bluetooth_status_label.setText("Bluetooth connection is not available on this system.")
            return
        selected_device = self.bluetooth_combo.currentData()
        if not isinstance(selected_device, dict):
            selected_device = {"mac": self.bluetooth_combo.currentText().strip()}
        success, message, current_bluetooth = self.bluetooth_connect_callback(selected_device)
        if current_bluetooth:
             self.refresh_bluetooth_devices()
        self.bluetooth_status_label.setText(message)

    def remove_bluetooth(self):
        """Remove/unpair a Bluetooth device."""
        if self._bluetooth_scan_in_progress:
            self.bluetooth_status_label.setText("Still fetching nearby Bluetooth devices. Try again in a moment.")
            return
        if not self.bluetooth_connect_callback:
            self.bluetooth_status_label.setText("Bluetooth connection is not available on this system.")
            return
        selected_device = self.bluetooth_combo.currentData()
        if not isinstance(selected_device, dict):
            selected_device = {"mac": self.bluetooth_combo.currentText().strip()}
        
        mac = selected_device.get("mac", "").strip()
        if not mac:
            self.bluetooth_status_label.setText("Select a device to remove.")
            return
        
        # Call the remove callback
        if hasattr(self, 'bluetooth_remove_callback') and self.bluetooth_remove_callback:
            success, message = self.bluetooth_remove_callback(selected_device)
            if success:
                self.refresh_bluetooth_devices()
            self.bluetooth_status_label.setText(message)
        else:
            self.bluetooth_status_label.setText("Remove function not available.")


class SoundDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle("Sound Settings")
        self.setModal(True)
        self.setFixedWidth(metrics["settings_width"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(14)

        title = QLabel("Audio Output")
        title.setObjectName("dialogSection")
        title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Select the speaker or audio output device for playing sound.")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(subtitle)

        self.speaker_combo = QComboBox()
        self.speaker_combo.setMinimumHeight(metrics["input_min_height"])
        self._style_settings_combo_popup(self.speaker_combo)
        layout.addWidget(self.speaker_combo)

        speaker_button_row = QHBoxLayout()
        self.refresh_speakers_button = QPushButton("Refresh Devices")
        self.refresh_speakers_button.setProperty("tileVariant", "dialogSecondary")
        self.refresh_speakers_button.clicked.connect(self.refresh_speakers)
        speaker_button_row.addWidget(self.refresh_speakers_button)

        self.set_default_button = QPushButton("Set as Default")
        self.set_default_button.setProperty("tileVariant", "accent")
        self.set_default_button.clicked.connect(self.set_default_speaker)
        speaker_button_row.addWidget(self.set_default_button)
        layout.addLayout(speaker_button_row)

        self.speaker_status_label = QLabel("")
        self.speaker_status_label.setObjectName("dialogStatus")
        self.speaker_status_label.setWordWrap(True)
        layout.addWidget(self.speaker_status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        close_button = QPushButton("Close")
        close_button.setProperty("tileVariant", "dialogSecondary")
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)

        self.setStyleSheet(dialog_stylesheet(metrics))
        self.load_speakers()

    def _style_settings_combo_popup(self, combo: QComboBox):
        popup = combo.view()
        if popup is None:
            return
        popup.setStyleSheet(
            """
            background-color: rgba(15, 23, 42, 0.98);
            color: #f1f5f9;
            border: 1px solid rgba(71, 85, 105, 0.5);
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
            alternate-background-color: rgba(30, 41, 59, 0.6);
            font-size: 14px;
            """
        )

    def load_speakers(self):
        """Load available audio output devices"""
        pactl = shutil.which("pactl")
        if not pactl:
            self.speaker_status_label.setText("PulseAudio is not available on this system.")
            return

        try:
            result = subprocess.run(
                [pactl, "list", "short", "sinks"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            
            self.speaker_combo.blockSignals(True)
            self.speaker_combo.clear()
            
            if result.returncode == 0 and result.stdout.strip():
                default_sink = self.get_default_sink()
                sink_index = 0
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        sink_name = parts[1]
                        # Extract a friendly name from the sink
                        friendly_name = self.get_sink_friendly_name(sink_name)
                        self.speaker_combo.addItem(friendly_name, sink_name)
                        if sink_name == default_sink:
                            self.speaker_combo.setCurrentIndex(sink_index)
                        sink_index += 1
                
                if sink_index > 0:
                    self.speaker_status_label.setText(f"Found {sink_index} audio output device(s).")
                else:
                    self.speaker_status_label.setText("No audio output devices found.")
            else:
                self.speaker_status_label.setText("No audio output devices found.")
            
            self.speaker_combo.blockSignals(False)
            
        except Exception as e:
            logging.error(f"Error loading speakers: {e}")
            self.speaker_status_label.setText(f"Error loading audio devices: {e}")

    def get_default_sink(self):
        """Get the current default audio sink"""
        pactl = shutil.which("pactl")
        if not pactl:
            return ""
        
        try:
            result = subprocess.run(
                [pactl, "get-default-sink"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def get_sink_friendly_name(self, sink_name):
        """Extract a friendly name from sink name"""
        # Try to get detailed info for better naming
        pactl = shutil.which("pactl")
        if pactl:
            try:
                result = subprocess.run(
                    [pactl, "list", "sinks"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10
                )
                if result.returncode == 0:
                    # Parse the output to find description
                    lines = result.stdout.split('\n')
                    in_sink = False
                    for line in lines:
                        if sink_name in line and 'Name:' in line:
                            in_sink = True
                        elif in_sink and 'Description:' in line:
                            desc = line.split('Description:')[1].strip()
                            return desc
                        elif in_sink and line.strip() == '':
                            break
            except Exception:
                pass
        
        # Fallback: use the sink name with better formatting
        return sink_name.replace('_', ' ').replace('.', ' ')

    def refresh_speakers(self):
        """Refresh the list of audio output devices"""
        self.speaker_status_label.setText("Refreshing audio devices...")
        self.load_speakers()

    def set_default_speaker(self):
        """Set the selected speaker as default"""
        pactl = shutil.which("pactl")
        if not pactl:
            self.speaker_status_label.setText("PulseAudio is not available on this system.")
            return

        selected_sink = self.speaker_combo.currentData()
        if not selected_sink:
            self.speaker_status_label.setText("Please select an audio output device first.")
            return

        try:
            import logging
            logging.info(f"Setting default audio sink to: {selected_sink}")
            
            result = subprocess.run(
                [pactl, "set-default-sink", selected_sink],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            
            if result.returncode == 0:
                # Move existing audio streams to new sink
                streams_moved = self.move_sink_inputs(selected_sink)
                
                friendly_name = self.speaker_combo.currentText()
                if streams_moved > 0:
                    self.speaker_status_label.setText(
                        f"Set {friendly_name} as default and moved {streams_moved} audio stream(s)."
                    )
                else:
                    self.speaker_status_label.setText(
                        f"Set {friendly_name} as default. New audio will play through this device."
                    )
                logging.info(f"Successfully set default sink to: {selected_sink}")
            else:
                error_msg = (result.stderr or result.stdout or "Unknown error").strip()
                self.speaker_status_label.setText(f"Failed to set default: {error_msg}")
                logging.error(f"Failed to set default sink: {error_msg}")
        except Exception as e:
            logging.error(f"Error setting default sink: {e}")
            self.speaker_status_label.setText(f"Error: {e}")

    def move_sink_inputs(self, sink_name):
        """Move all active audio streams to the new sink. Returns count of moved streams."""
        pactl = shutil.which("pactl")
        if not pactl:
            return 0

        moved_count = 0
        try:
            result = subprocess.run(
                [pactl, "list", "short", "sink-inputs"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if parts:
                        input_id = parts[0]
                        move_result = subprocess.run(
                            [pactl, "move-sink-input", input_id, sink_name],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=5
                        )
                        if move_result.returncode == 0:
                            moved_count += 1
                            import logging
                            logging.info(f"Moved audio stream {input_id} to {sink_name}")
        except Exception:
            logging.exception("Failed to move sink inputs")
        
        return moved_count


class BrightnessDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle("Brightness Settings")
        self.setModal(True)
        self.setFixedWidth(metrics["settings_width"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(14)

        title = QLabel("Screen Brightness")
        title.setObjectName("dialogSection")
        title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Adjust the brightness level of your display.")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(subtitle)

        # Brightness slider
        slider_row = QHBoxLayout()
        brightness_icon_label = QLabel()
        brightness_icon_path = resource_path("icons/brightness.png")
        if brightness_icon_path.exists():
            pixmap = QPixmap(str(brightness_icon_path))
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                brightness_icon_label.setPixmap(scaled_pixmap)
        brightness_icon_label.setFixedSize(24, 24)
        slider_row.addWidget(brightness_icon_label)

        self.brightness_slider = QSlider(Qt.Horizontal)
        self.brightness_slider.setMinimum(0)
        self.brightness_slider.setMaximum(100)
        self.brightness_slider.setValue(int(get_current_brightness() * 100))
        self.brightness_slider.setMinimumHeight(metrics["input_min_height"])
        self.brightness_slider.valueChanged.connect(self.on_brightness_changed)
        slider_row.addWidget(self.brightness_slider, 1)

        self.brightness_value_label = QLabel(f"{self.brightness_slider.value()}%")
        self.brightness_value_label.setObjectName("dialogStatus")
        self.brightness_value_label.setFont(QFont("Sans Serif", metrics["subtitle_font"], QFont.Bold))
        self.brightness_value_label.setFixedWidth(50)
        self.brightness_value_label.setAlignment(Qt.AlignRight)
        slider_row.addWidget(self.brightness_value_label)
        layout.addLayout(slider_row)

        # Quick preset buttons
        preset_row = QHBoxLayout()
        for preset_value in [25, 50, 75, 100]:
            preset_button = QPushButton(f"{preset_value}%")
            preset_button.setProperty("tileVariant", "dialogSecondary")
            preset_button.setFixedHeight(36)
            preset_button.clicked.connect(lambda checked, val=preset_value: self.set_brightness_preset(val))
            preset_row.addWidget(preset_button)
        
        layout.addLayout(preset_row)

        self.brightness_status_label = QLabel("")
        self.brightness_status_label.setObjectName("dialogStatus")
        self.brightness_status_label.setWordWrap(True)
        layout.addWidget(self.brightness_status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        close_button = QPushButton("Close")
        close_button.setProperty("tileVariant", "dialogSecondary")
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)

        self.setStyleSheet(dialog_stylesheet(metrics))

    def on_brightness_changed(self, value):
        """Handle brightness slider change"""
        self.brightness_value_label.setText(f"{value}%")
        success = control_system_brightness("SET_BRIGHTNESS", value)
        if success:
            self.brightness_status_label.setText(f"Brightness set to {value}%")
        else:
            self.brightness_status_label.setText("Failed to adjust brightness. Try installing brightnessctl.")

    def set_brightness_preset(self, value):
        """Set brightness to a preset value"""
        self.brightness_slider.setValue(value)


class RemoteLoginDialog(QDialog):
    def __init__(
        self,
        username_text="",
        parent=None,
    ):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle("Remote Login Settings")
        self.setModal(True)
        self.setFixedWidth(metrics["settings_width"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(14)

        title = QLabel("Remote Login")
        title.setObjectName("dialogSection")
        title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Set the phone credentials required to control LinuxTV remotely.")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(subtitle)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username")
        self.username_input.setText(username_text)
        self.username_input.setMinimumHeight(metrics["input_min_height"])
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumHeight(metrics["input_min_height"])
        layout.addWidget(self.password_input)

        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText("Confirm password")
        self.confirm_input.setEchoMode(QLineEdit.Password)
        self.confirm_input.setMinimumHeight(metrics["input_min_height"])
        layout.addWidget(self.confirm_input)

        helper = QLabel("Leave all three fields empty to disable phone authentication.")
        helper.setObjectName("dialogSubtitle")
        helper.setWordWrap(True)
        helper.setFont(QFont("Sans Serif", metrics["helper_font"]))
        layout.addWidget(helper)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.setSpacing(metrics["nav_spacing"])

        cancel_button = QPushButton("Cancel")
        cancel_button.setProperty("tileVariant", "dialogSecondary")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        save_button = QPushButton("Save")
        save_button.setProperty("tileVariant", "accent")
        save_button.clicked.connect(self.accept)
        button_row.addWidget(save_button)

        layout.addLayout(button_row)

        self.setStyleSheet(dialog_stylesheet(metrics))

    def values(self):
        return {
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "confirm_password": self.confirm_input.text(),
        }


class SettingsDialog(QDialog):
    def __init__(
        self,
        auto_launch=None,
        app_options=None,
        parent=None,
    ):
        super().__init__(parent)
        metrics = dialog_metrics()
        self.setWindowTitle("LinuxTV")
        self.setModal(True)
        self.setFixedWidth(metrics["settings_width"])

        auto_launch = auto_launch or {}
        app_options = app_options or []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics["dialog_margin_x"], metrics["dialog_margin_y"], metrics["dialog_margin_x"], metrics["dialog_margin_y"])
        layout.setSpacing(14)
        self.section_buttons = {}
        self.section_panels = {}

        title = QLabel("Auto Open")
        title.setObjectName("dialogSection")
        title.setFont(QFont("Sans Serif", metrics["section_font"], QFont.Bold))
        layout.addWidget(title)

        subtitle = QLabel("Choose which app or site opens automatically after LinuxTV sits idle.")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Sans Serif", metrics["subtitle_font"]))
        layout.addWidget(subtitle)

        self.auto_launch_combo = QComboBox()
        self.auto_launch_combo.setMinimumHeight(metrics["input_min_height"])
        self._style_settings_combo_popup(self.auto_launch_combo)
        self.auto_launch_combo.addItem("Disabled", ("", ""))
        selected_kind = str(auto_launch.get("app_kind", "")).strip()
        selected_target = str(auto_launch.get("app_target", "")).strip()
        selected_index = 0
        for index, option in enumerate(app_options, start=1):
            self.auto_launch_combo.addItem(option["label"], (option["kind"], option["target"]))
            if option["kind"] == selected_kind and option["target"] == selected_target:
                selected_index = index
        self.auto_launch_combo.setCurrentIndex(selected_index)
        layout.addWidget(self.auto_launch_combo)

        self.delay_input = QLineEdit()
        self.delay_input.setPlaceholderText("Idle delay in seconds")
        self.delay_input.setText(str(auto_launch.get("delay_seconds", AUTO_LAUNCH_IDLE_MS // 1000)))
        self.delay_input.setMinimumHeight(metrics["input_min_height"])
        layout.addWidget(self.delay_input)

        auto_helper = QLabel("Pick Disabled to turn auto open off. Enter a whole number of seconds.")
        auto_helper.setObjectName("dialogSubtitle")
        auto_helper.setWordWrap(True)
        auto_helper.setFont(QFont("Sans Serif", metrics["helper_font"]))
        layout.addWidget(auto_helper)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        cancel_button = QPushButton("Cancel")
        cancel_button.setProperty("tileVariant", "dialogSecondary")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        save_button = QPushButton("Save")
        save_button.setProperty("tileVariant", "accent")
        save_button.clicked.connect(self.accept)
        button_row.addWidget(save_button)

        layout.addLayout(button_row)

        self.setStyleSheet(dialog_stylesheet(metrics))

    def _style_settings_combo_popup(self, combo: QComboBox):
        popup = combo.view()
        if popup is None:
            return
        popup.setStyleSheet(
            """
            background-color: rgba(15, 23, 42, 0.98);
            color: #f1f5f9;
            border: 1px solid rgba(71, 85, 105, 0.5);
            selection-background-color: rgba(59, 130, 246, 0.3);
            selection-color: #f8fafc;
            alternate-background-color: rgba(30, 41, 59, 0.6);
            font-size: 14px;
            """
        )

    def values(self):
        selected_kind, selected_target = self.auto_launch_combo.currentData()
        return {
            "auto_launch_app_kind": selected_kind,
            "auto_launch_app_target": selected_target,
            "auto_launch_delay_seconds": self.delay_input.text().strip(),
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "confirm_password": self.confirm_input.text(),
        }


class LauncherWindow(QMainWindow):
    def __init__(self, config_path: Path):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.browser_exe = find_browser()
        self.config_path = config_path

        self.config = load_config(config_path)
        
        self.tiles = []
        self.tile_rows = []
        self.current_index = 0
        self.current_row = 0
        self.current_col = 0
        self.active_process = None
        self.active_process_kind = None
        self.active_process_name = None
        self.active_audio_thread = None
        self.active_audio_stop_event = None
        self.active_fullscreen_thread = None
        self.active_fullscreen_stop_event = None
        self.remote_target_window_cache = None
        self.remote_target_window_cache_at = 0.0
        self.remote_action_queue = queue.Queue()
        self._icon_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="icon-loader")
        self._icon_request_token = 0
        self._icon_bridge = IconUpdateBridge()
        self._icon_bridge.icon_ready.connect(self._apply_resolved_icon)
        self._scroll_anim = None
        self._row_scroll_anim = None
        
        # Search and filtering state
        self.search_filter = ""
        self.is_search_active = False
        
        # Loading overlay for visual feedback
        self.loading_overlay = None
        self.loading_label = None

        self.process_monitor = QTimer(self)
        self.process_monitor.setInterval(500)
        self.process_monitor.timeout.connect(self.check_active_process)

        self.remote_action_timer = QTimer(self)
        self.remote_action_timer.setInterval(8)
        self.remote_action_timer.timeout.connect(self.drain_remote_actions)
        self.remote_action_timer.start()

        self.auto_launch_timer = QTimer(self)
        self.auto_launch_timer.setSingleShot(True)
        self.auto_launch_timer.setInterval(AUTO_LAUNCH_IDLE_MS)
        self.auto_launch_timer.timeout.connect(self.auto_launch_selected_app_if_idle)

        self.auto_launch_countdown_timer = QTimer(self)
        self.auto_launch_countdown_timer.setInterval(250)
        self.auto_launch_countdown_timer.timeout.connect(self.update_auto_launch_status)
        self.auto_launch_paused = False

        self.ip_update_timer = QTimer(self)
        self.ip_update_timer.setInterval(1000)
        self.ip_update_timer.timeout.connect(self.update_ip_label)

        # Check SSL configuration and warn if not enabled
        ws_config = self.config.get("websocket", {})
        ssl_cert = ws_config.get("ssl_cert", "")
        ssl_key = ws_config.get("ssl_key", "")
        
        if not ssl_cert or not ssl_key:
            logging.warning(
                "No HTTPS for WebSocket — the WebSocket server runs on plain ws:// (port %d), not wss://; "
                "credentials traverse the local network unencrypted. "
                "Set websocket.ssl_cert and websocket.ssl_key in config to enable WSS.",
                ws_config.get("port", 8765)
            )
        
        self.ws_server = WebSocketControlServer(self)
        self.ws_server.start()

        # Initialize global input device grabber for system-wide remote control
        self.input_grabber = InputDeviceGrabber(self)
        self.input_grabber.start_grabbing()

        self.setup_ui()
        self.apply_fullscreen_to_primary_screen()
        QTimer.singleShot(1500, self.start_startup_time_sync)
        QTimer.singleShot(15000, self.start_startup_time_sync)

    def get_target_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            return screen
        screens = QApplication.screens()
        return screens[0] if screens else None

    def get_target_geometry(self):
        screen = self.get_target_screen()
        if screen:
            return screen.geometry()
        return self.geometry()

    def apply_fullscreen_to_primary_screen(self):
        geometry = self.get_target_geometry()
        self.setGeometry(geometry)
        self.move(geometry.topLeft())
        self.showFullScreen()

    def start_startup_time_sync(self):
        threading.Thread(
            target=self._run_startup_time_sync,
            name="startup-time-sync",
            daemon=True,
        ).start()

    def _run_startup_time_sync(self):
        success, message = sync_system_time()
        if success:
            logging.info("Startup time sync: %s", message)
        else:
            logging.warning("Startup time sync failed: %s", message)

    def compute_ui_metrics(self):
        geometry = self.get_target_geometry()
        width = max(geometry.width(), 1280)
        height = max(geometry.height(), 720)
        compact = width <= 1600 or height <= 950
        if compact:
            return {
                "compact": True,
                "main_margin_x": 20,
                "main_margin_top": 8,
                "main_margin_bottom": 16,
                "main_spacing": 12,
                "hero_margin_x": 20,
                "hero_margin_y": 12,
                "hero_spacing": 12,
                "hero_title_font": 28,
                "hero_title_css": 28,
                "settings_button_size": 40,
                "settings_button_font": 18,
                "section_heading_font": 20,
                "footer_font": 12,
                "status_font": 12,
                "tile_stack_spacing": 16,
                "row_label_css": 22,
                "row_scroll_height": 240,
                "row_content_margin_x": 8,
                "row_content_margin_y": 6,
                "row_content_spacing": 24,
                "tile_width": 310,
                "tile_height": 180,
                "tile_font_size": 16,
                "tile_icon_size": 90,
                "tile_font_css": 16,
                "tile_padding_lr": 20,
                "tile_padding_top": 22,
                "tile_padding_bottom": 18,
                "tile_padding_top_subtitle": 14,
                "tile_padding_bottom_subtitle": 14,
                "action_button_size": 36,
                "action_button_font_css": 15,
                "footer_padding_y": 6,
                "cancel_button_min_width": 140,
                "card_shadow_radius": 8,
                "card_focus_scale": 1.06,
            }
        return {
            "compact": False,
            "main_margin_x": 40,
            "main_margin_top": 12,
            "main_margin_bottom": 24,
            "main_spacing": 18,
            "hero_margin_x": 32,
            "hero_margin_y": 16,
            "hero_spacing": 16,
            "hero_title_font": 36,
            "hero_title_css": 36,
            "settings_button_size": 48,
            "settings_button_font": 20,
            "section_heading_font": 26,
            "footer_font": 14,
            "status_font": 14,
            "tile_stack_spacing": 20,
            "row_label_css": 28,
            "row_scroll_height": 320,
            "row_content_margin_x": 12,
            "row_content_margin_y": 8,
            "row_content_spacing": 36,
            "tile_width": 450,
            "tile_height": 260,
            "tile_font_size": 21,
            "tile_icon_size": 160,
            "tile_font_css": 20,
            "tile_padding_lr": 28,
            "tile_padding_top": 36,
            "tile_padding_bottom": 26,
            "tile_padding_top_subtitle": 18,
            "tile_padding_bottom_subtitle": 18,
            "action_button_size": 44,
            "action_button_font_css": 19,
            "footer_padding_y": 10,
            "cancel_button_min_width": 170,
            "card_shadow_radius": 12,
            "card_focus_scale": 1.06,
        }

    def showEvent(self, event):
        super().showEvent(event)
        # Delay the reset until the window is actually visible so auto-open
        # also starts on a fresh system boot.
        QTimer.singleShot(0, self.reset_auto_launch_timer)

    def get_ip_address(self):
        """Get the local IP address of the machine"""
        import socket
        try:
            # Create a socket connection to get the local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_address = s.getsockname()[0]
            s.close()
            return ip_address
        except Exception:
            return "127.0.0.1"

    def get_wifi_ssid(self):
        """Get the current WiFi SSID"""
        import subprocess
        try:
            nmcli = shutil.which("nmcli")
            if not nmcli:
                return ""
            
            # Get active WiFi connections
            result = subprocess.run(
                [nmcli, "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1]
                        return ssid
            
            return ""
        except Exception as e:
            logging.error(f"Error getting WiFi SSID: {e}")
            return ""
    
    def is_wifi_connection(self):
        """Check if current connection is via WiFi"""
        import subprocess
        try:
            nmcli = shutil.which("nmcli")
            if not nmcli:
                return False
            
            # Check if we have an active WiFi connection
            result = subprocess.run(
                [nmcli, "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            
            is_wifi = False
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    if line.startswith("yes:"):
                        is_wifi = True
                        break
            
            return is_wifi
        except Exception as e:
            logging.error(f"Error checking WiFi connection: {e}")
            return False

    def shutdown_system(self):
        """Shutdown the system"""
        try:
            request_system_power_action("SHUTDOWN")
        except Exception as e:
            logging.error(f"Failed to shutdown: {e}")
            QMessageBox.critical(self, "Error", f"Failed to shutdown: {e}")

    def restart_system(self):
        """Restart the system"""
        try:
            request_system_power_action("REBOOT")
        except Exception as e:
            logging.error(f"Failed to restart: {e}")
            QMessageBox.critical(self, "Error", f"Failed to restart: {e}")

    def update_system(self):
        """Update the system packages"""
        try:
            success, message = request_system_update()
            if success:
                QMessageBox.information(self, "System Update", message)
            else:
                QMessageBox.critical(self, "Error", message)
        except Exception as e:
            logging.error(f"Failed to update system: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update system: {e}")

    def update_app(self):
        """Update LinuxTV app from GitHub"""
        try:
            success, message = self.update_from_github()
            if success:
                QMessageBox.information(self, "App Update", message)
            else:
                QMessageBox.critical(self, "Error", message)
        except Exception as e:
            logging.error(f"Failed to update app: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update app: {e}")

    def setup_ui(self):
        self.ui_metrics = self.compute_ui_metrics()
        central = QWidget()
        central.setObjectName("centralShell")
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(
            self.ui_metrics["main_margin_x"],
            self.ui_metrics["main_margin_top"],
            self.ui_metrics["main_margin_x"],
            self.ui_metrics["main_margin_bottom"],
        )
        main_layout.setSpacing(self.ui_metrics["main_spacing"])

        hero = QWidget()
        hero.setObjectName("heroCard")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(
            self.ui_metrics["hero_margin_x"],
            self.ui_metrics["hero_margin_y"],
            self.ui_metrics["hero_margin_x"],
            self.ui_metrics["hero_margin_y"],
        )
        hero_layout.setSpacing(self.ui_metrics["hero_spacing"])

        hero_top_row = QHBoxLayout()
        hero_top_row.setContentsMargins(0, 0, 0, 0)
        hero_top_row.setSpacing(20)

        # Left-aligned title
        title = QLabel("LinuxTV")
        title.setObjectName("heroTitle")
        title.setFont(QFont("Sans Serif", self.ui_metrics["hero_title_font"], QFont.Bold))
        title.setAlignment(Qt.AlignLeft)
        hero_top_row.addWidget(title)

        hero_top_row.addStretch(1)
        
        # Search input field
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search apps...")
        self.search_input.setObjectName("searchInput")
        self.search_input.setFont(QFont("Sans Serif", max(13, self.ui_metrics["settings_button_font"] - 5)))
        self.search_input.setFixedWidth(250)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px 12px;
            }
            QLineEdit:focus {
                border: 1px solid #33c3a0;
            }
        """)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        hero_top_row.addWidget(self.search_input)

        # IP Address and WiFi SSID label
        ip_address = self.get_ip_address()
        wifi_ssid = self.get_wifi_ssid()
        is_wifi = self.is_wifi_connection()
        
        # Initialize cache for WiFi info
        self._cached_ip_address = ip_address
        self._cached_wifi_ssid = wifi_ssid
        self._cached_is_wifi = is_wifi
        self._last_wifi_check = time.time()
        
        import logging
        logging.info(f"IP: {ip_address}, WiFi SSID: '{wifi_ssid}', Is WiFi: {is_wifi}")
        
        if is_wifi:
            if wifi_ssid:
                ip_text = f"WiFi: {wifi_ssid} • {ip_address}"
            else:
                ip_text = f"WiFi • {ip_address}"
        else:
            ip_text = f"Ethernet • {ip_address}"
        
        self.ip_label = QLabel(ip_text)
        self.ip_label.setObjectName("ipLabel")
        self.ip_label.setFont(QFont("Sans Serif", max(13, self.ui_metrics["settings_button_font"] - 5)))
        self.ip_label.setStyleSheet("color: #8b949e; padding-right: 8px;")
        hero_top_row.addWidget(self.ip_label)
        
        # Start the IP update timer
        self.ip_update_timer.start()

        # Network button
        network_button = QToolButton()
        network_button.setObjectName("networkButton")
        network_icon_path = resource_path("icons/wifi.png")
        if network_icon_path.exists():
            white_icon = create_white_icon(str(network_icon_path), self.ui_metrics["settings_button_size"] - 8)
            network_button.setIcon(white_icon)
            network_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        network_button.setToolTip("Network Settings")
        network_button.setCursor(Qt.PointingHandCursor)
        network_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        network_button.clicked.connect(self.open_network_settings)
        hero_top_row.addWidget(network_button)

        # Bluetooth button
        bluetooth_button = QToolButton()
        bluetooth_button.setObjectName("bluetoothButton")
        bluetooth_icon_path = resource_path("icons/bluetooth.png")
        if bluetooth_icon_path.exists():
            white_icon = create_white_icon(str(bluetooth_icon_path), self.ui_metrics["settings_button_size"] - 8)
            bluetooth_button.setIcon(white_icon)
            bluetooth_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        bluetooth_button.setToolTip("Bluetooth Settings")
        bluetooth_button.setCursor(Qt.PointingHandCursor)
        bluetooth_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        bluetooth_button.clicked.connect(self.open_bluetooth_settings)
        hero_top_row.addWidget(bluetooth_button)

        # Sound button
        sound_button = QToolButton()
        sound_button.setObjectName("soundButton")
        sound_icon_path = resource_path("icons/sound.png")
        if sound_icon_path.exists():
            white_icon = create_white_icon(str(sound_icon_path), self.ui_metrics["settings_button_size"] - 8)
            sound_button.setIcon(white_icon)
            sound_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        sound_button.setToolTip("Sound Settings")
        sound_button.setCursor(Qt.PointingHandCursor)
        sound_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        sound_button.clicked.connect(self.open_sound_settings)
        hero_top_row.addWidget(sound_button)

        # Brightness button
        brightness_button = QToolButton()
        brightness_button.setObjectName("brightnessButton")
        brightness_icon_path = resource_path("icons/brightness.png")
        if brightness_icon_path.exists():
            white_icon = create_white_icon(str(brightness_icon_path), self.ui_metrics["settings_button_size"] - 8)
            brightness_button.setIcon(white_icon)
            brightness_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        brightness_button.setToolTip("Brightness Settings")
        brightness_button.setCursor(Qt.PointingHandCursor)
        brightness_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        brightness_button.clicked.connect(self.open_brightness_settings)
        hero_top_row.addWidget(brightness_button)

        # Shutdown button
        shutdown_button = QToolButton()
        shutdown_button.setObjectName("shutdownButton")
        power_icon_path = resource_path("icons/power.png")
        if power_icon_path.exists():
            white_icon = create_white_icon(str(power_icon_path), self.ui_metrics["settings_button_size"] - 8)
            shutdown_button.setIcon(white_icon)
            shutdown_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        shutdown_button.setToolTip("Shutdown")
        shutdown_button.setCursor(Qt.PointingHandCursor)
        shutdown_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        shutdown_button.clicked.connect(self.shutdown_system)
        hero_top_row.addWidget(shutdown_button)

        # Restart button
        restart_button = QToolButton()
        restart_button.setObjectName("restartButton")
        reboot_icon_path = resource_path("icons/reboot.png")
        if reboot_icon_path.exists():
            white_icon = create_white_icon(str(reboot_icon_path), self.ui_metrics["settings_button_size"] - 8)
            restart_button.setIcon(white_icon)
            restart_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        restart_button.setToolTip("Restart")
        restart_button.setCursor(Qt.PointingHandCursor)
        restart_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        restart_button.clicked.connect(self.restart_system)
        hero_top_row.addWidget(restart_button)

        # Update button with menu
        update_button = QToolButton()
        update_button.setObjectName("updateButton")
        update_icon_path = resource_path("icons/update.png")
        if update_icon_path.exists():
            white_icon = create_white_icon(str(update_icon_path), self.ui_metrics["settings_button_size"] - 8)
            update_button.setIcon(white_icon)
            update_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        update_button.setToolTip("Updates")
        update_button.setCursor(Qt.PointingHandCursor)
        update_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        update_button.setPopupMode(QToolButton.InstantPopup)
        
        # Create update menu
        update_menu = QMenu(self)
        update_menu.setStyleSheet("""
            QMenu {
                background-color: #1a2332;
                color: #edf2f7;
                border: 1px solid #2b3641;
                border-radius: 8px;
                padding: 8px;
                font-size: 14px;
            }
            QMenu::item {
                padding: 10px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #33c3a0;
                color: #09110f;
            }
        """)
        
        system_update_action = update_menu.addAction("🖥️  System Update")
        system_update_action.triggered.connect(self.update_system)
        
        app_update_action = update_menu.addAction("📱  App Update")
        app_update_action.triggered.connect(self.update_app)
        
        update_button.setMenu(update_menu)
        hero_top_row.addWidget(update_button)

        # Remote control button
        remote_button = QToolButton()
        remote_button.setObjectName("remoteButton")
        remote_icon_path = resource_path("icons/remote.png")
        if remote_icon_path.exists():
            white_icon = create_white_icon(str(remote_icon_path), self.ui_metrics["settings_button_size"] - 8)
            remote_button.setIcon(white_icon)
            remote_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        remote_button.setToolTip("Remote Control Settings")
        remote_button.setCursor(Qt.PointingHandCursor)
        remote_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        remote_button.clicked.connect(self.open_remote_settings)
        hero_top_row.addWidget(remote_button)

        # Auto-open button
        auto_button = QToolButton()
        auto_button.setObjectName("autoButton")
        auto_icon_path = resource_path("icons/auto.png")
        if auto_icon_path.exists():
            white_icon = create_white_icon(str(auto_icon_path), self.ui_metrics["settings_button_size"] - 8)
            auto_button.setIcon(white_icon)
            auto_button.setIconSize(QSize(self.ui_metrics["settings_button_size"] - 8, self.ui_metrics["settings_button_size"] - 8))
        auto_button.setToolTip("Auto-Open")
        auto_button.setCursor(Qt.PointingHandCursor)
        auto_button.setFixedSize(self.ui_metrics["settings_button_size"], self.ui_metrics["settings_button_size"])
        auto_button.clicked.connect(self.open_settings)
        hero_top_row.addWidget(auto_button)
        hero_layout.addLayout(hero_top_row)
        main_layout.addWidget(hero)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setObjectName("tileScroll")
        self.tile_scroll = scroll
        container = QWidget()
        container.setObjectName("tileContainer")
        self.tile_stack = QVBoxLayout(container)
        self.tile_stack.setContentsMargins(8, 8, 8, 8)
        self.tile_stack.setSpacing(self.ui_metrics["tile_stack_spacing"])
        self.tile_stack.setAlignment(Qt.AlignTop)

        self.populate_tiles()

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        footer = QLabel("↑↓←→ Navigate  •  Enter Select  •  Esc Exit")
        footer.setObjectName("footerHint")
        footer.setWordWrap(True)
        footer.setFont(QFont("Sans Serif", self.ui_metrics["footer_font"]))
        footer.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(footer)

        auto_launch_status_row = QHBoxLayout()
        auto_launch_status_row.setContentsMargins(0, 0, 0, 0)
        auto_launch_status_row.setSpacing(12)
        auto_launch_status_row.addStretch(1)

        self.auto_launch_status_label = QLabel("")
        self.auto_launch_status_label.setObjectName("autoLaunchStatus")
        self.auto_launch_status_label.setWordWrap(True)
        self.auto_launch_status_label.setFont(QFont("Sans Serif", self.ui_metrics["status_font"]))
        self.auto_launch_status_label.setAlignment(Qt.AlignCenter)
        self.auto_launch_status_label.hide()
        auto_launch_status_row.addWidget(self.auto_launch_status_label)

        self.auto_launch_cancel_button = QPushButton("Cancel Auto-Open")
        self.auto_launch_cancel_button.setObjectName("autoLaunchCancelButton")
        self.auto_launch_cancel_button.setCursor(Qt.PointingHandCursor)
        self.auto_launch_cancel_button.clicked.connect(self.toggle_auto_launch_pause)
        self.auto_launch_cancel_button.hide()
        auto_launch_status_row.addWidget(self.auto_launch_cancel_button)
        auto_launch_status_row.addStretch(1)
        main_layout.addLayout(auto_launch_status_row)

        self.setCentralWidget(central)
        # Install event filter to capture wheel events for touchpad scrolling
        central.installEventFilter(self)
        scroll.installEventFilter(self)
        scroll.viewport().installEventFilter(self)
        self.apply_theme()

        if self.tiles:
            self.focus_first_tile()
        self.reset_auto_launch_timer()

    def apply_theme(self):
        m = self.ui_metrics
        stylesheet = """
            QMainWindow {
                background: #0f172a;
            }
            QWidget#centralShell {
                background: transparent;
                color: #f1f5f9;
            }
            QWidget#heroCard {
                background: transparent;
                border: none;
                border-bottom: 1px solid rgba(71, 85, 105, 0.4);
            }
            QToolButton#remoteButton {
                background: transparent;
                color: #cbd5e1;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#remoteButton:hover {
                background: rgba(59, 130, 246, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #3b82f6;
            }
            QToolButton#remoteButton:pressed {
                background: rgba(59, 130, 246, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#autoButton {
                background: transparent;
                color: #cbd5e1;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#autoButton:hover {
                background: rgba(59, 130, 246, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #3b82f6;
            }
            QToolButton#autoButton:pressed {
                background: rgba(59, 130, 246, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#shutdownButton {
                background: transparent;
                color: #ef4444;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#shutdownButton:hover {
                background: rgba(239, 68, 68, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #f87171;
            }
            QToolButton#shutdownButton:pressed {
                background: rgba(239, 68, 68, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#restartButton {
                background: transparent;
                color: #f59e0b;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#restartButton:hover {
                background: rgba(245, 158, 11, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #fbbf24;
            }
            QToolButton#restartButton:pressed {
                background: rgba(245, 158, 11, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#updateButton {
                background: transparent;
                color: #3b82f6;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#updateButton:hover {
                background: rgba(59, 130, 246, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #60a5fa;
            }
            QToolButton#updateButton:pressed {
                background: rgba(59, 130, 246, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#networkButton {
                background: transparent;
                color: #3b82f6;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#networkButton:hover {
                background: rgba(59, 130, 246, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #60a5fa;
            }
            QToolButton#networkButton:pressed {
                background: rgba(59, 130, 246, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#bluetoothButton {
                background: transparent;
                color: #8b5cf6;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#bluetoothButton:hover {
                background: rgba(139, 92, 246, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #a78bfa;
            }
            QToolButton#bluetoothButton:pressed {
                background: rgba(139, 92, 246, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#soundButton {
                background: transparent;
                color: #10b981;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#soundButton:hover {
                background: rgba(16, 185, 129, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #34d399;
            }
            QToolButton#soundButton:pressed {
                background: rgba(16, 185, 129, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QToolButton#brightnessButton {
                background: transparent;
                color: #f59e0b;
                border: none;
                font-size: __SETTINGS_FONT__px;
                padding: 0;
            }
            QToolButton#brightnessButton:hover {
                background: rgba(245, 158, 11, 0.2);
                border-radius: __SETTINGS_RADIUS__px;
                color: #fbbf24;
            }
            QToolButton#brightnessButton:pressed {
                background: rgba(245, 158, 11, 0.3);
                border-radius: __SETTINGS_RADIUS__px;
            }
            QLabel#heroTitle {
                color: #f8fafc;
                font-weight: bold;
                letter-spacing: 0.8px;
                font-size: __HERO_TITLE__px;
            }
            QLabel#ipLabel {
                color: #94a3b8;
                font-size: 14px;
            }
            QScrollArea#tileScroll, QWidget#tileContainer {
                background: transparent;
                border: none;
            }
            QWidget[rowSection="true"] {
                background: transparent;
            }
            QLabel[rowHeading="true"] {
                color: #f1f5f9;
                font-size: __ROW_HEADING__px;
                font-weight: 700;
                padding: 20px 16px 12px 16px;
                letter-spacing: 0.5px;
            }
            QScrollArea[rowScroll="true"] {
                background: transparent;
                border: none;
            }
            QWidget[rowContent="true"] {
                background: transparent;
            }
            QScrollArea#tileScroll QScrollBar:vertical {
                background: transparent;
                width: 0px;
                margin: 0px;
            }
            QScrollArea#tileScroll QScrollBar:horizontal {
                background: transparent;
                height: 0px;
                margin: 0px;
            }
            QScrollArea#tileScroll::corner {
                background: transparent;
            }
            QPushButton[tileVariant="default"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(51, 65, 85, 0.9), stop:1 rgba(30, 41, 59, 0.95));
                color: #f1f5f9;
                border: 1px solid rgba(71, 85, 105, 0.4);
                border-radius: 16px;
                padding: __TILE_PADDING_BOTTOM__px __TILE_PADDING_LR__px;
                padding-left: __TILE_PADDING_LR__px;
                padding-right: __TILE_PADDING_LR__px;
                padding-top: __TILE_PADDING_TOP__px;
                padding-bottom: __TILE_PADDING_BOTTOM__px;
                text-align: center;
                font-size: __TILE_FONT__px;
                font-weight: 600;
            }
            QPushButton[tileVariant="default"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(71, 85, 105, 0.95), stop:1 rgba(51, 65, 85, 0.95));
                border: 1px solid rgba(100, 116, 139, 0.6);
            }
            QPushButton[tileVariant="default"]:focus {
                border: 2px solid #3b82f6;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(59, 130, 246, 0.25), stop:1 rgba(37, 99, 235, 0.3));
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton[tileVariant="accent"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #10b981, stop:1 #059669);
                color: #ffffff;
                border: 1px solid #34d399;
                border-radius: 16px;
                padding: __TILE_PADDING_BOTTOM__px __TILE_PADDING_LR__px;
                padding-left: __TILE_PADDING_LR__px;
                padding-right: __TILE_PADDING_LR__px;
                padding-top: __TILE_PADDING_TOP__px;
                padding-bottom: __TILE_PADDING_BOTTOM__px;
                text-align: center;
                font-size: __TILE_FONT__px;
                font-weight: 700;
            }
            QPushButton[tileVariant="accent"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #34d399, stop:1 #10b981);
                border: 1px solid #6ee7b7;
            }
            QPushButton[tileVariant="accent"]:focus {
                border: 2px solid #3b82f6;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(52, 211, 153, 0.3), stop:1 rgba(16, 185, 129, 0.35));
            }
            QWidget#appCardShell {
                background: transparent;
            }
            QToolButton#editButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(51, 65, 85, 0.9), stop:1 rgba(30, 41, 59, 0.95));
                color: #94a3b8;
                border: 1px solid rgba(71, 85, 105, 0.5);
                border-radius: 14px;
                font-size: 18px;
                padding-bottom: 2px;
            }
            QToolButton#editButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3b82f6, stop:1 #2563eb);
                color: #ffffff;
                border: 1px solid #3b82f6;
            }
            QToolButton#editButton:pressed {
                background-color: #1d4ed8;
                border: 1px solid #3b82f6;
            }
            QToolButton#favoriteButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(51, 65, 85, 0.9), stop:1 rgba(30, 41, 59, 0.95));
                color: #f59e0b;
                border: 1px solid rgba(245, 158, 11, 0.5);
                border-radius: 14px;
                font-size: 20px;
                padding-bottom: 2px;
            }
            QToolButton#favoriteButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f59e0b, stop:1 #d97706);
                color: #ffffff;
                border: 1px solid #f59e0b;
            }
            QToolButton#favoriteButton:pressed {
                background-color: #b45309;
                border: 1px solid #f59e0b;
            }
            QToolButton#reorderButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(51, 65, 85, 0.9), stop:1 rgba(30, 41, 59, 0.95));
                color: #94a3b8;
                border: 1px solid rgba(71, 85, 105, 0.5);
                border-radius: 14px;
                font-size: 16px;
                padding-bottom: 2px;
            }
            QToolButton#reorderButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(71, 85, 105, 0.95), stop:1 rgba(51, 65, 85, 0.95));
                color: #f1f5f9;
                border: 1px solid rgba(100, 116, 139, 0.7);
            }
            QToolButton#reorderButton:pressed {
                background-color: rgba(30, 41, 59, 0.95);
                border: 1px solid rgba(71, 85, 105, 0.7);
            }
            QToolButton#deleteButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(127, 29, 29, 0.9), stop:1 rgba(103, 15, 15, 0.95));
                color: #ef4444;
                border: 1px solid rgba(239, 68, 68, 0.5);
                border-radius: 14px;
                font-size: 18px;
            }
            QToolButton#deleteButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ef4444, stop:1 #dc2626);
                border: 1px solid #ef4444;
                color: #ffffff;
            }
            QToolButton#deleteButton:pressed {
                background-color: #991b1b;
                border: 1px solid #ef4444;
            }
            QPushButton[hasSubtitle="true"] {
                padding-top: __TILE_SUBTITLE_TOP__px;
                padding-bottom: __TILE_SUBTITLE_BOTTOM__px;
            }
            QLabel#footerHint {
                color: rgba(148, 163, 184, 0.7);
                padding: __FOOTER_PADDING_Y__px 20px;
                font-size: __FOOTER_FONT__px;
                letter-spacing: 0.3px;
                background: transparent;
            }
            QLabel#autoLaunchStatus {
                color: #3b82f6;
                padding: 0 20px 12px 20px;
                font-size: __STATUS_FONT__px;
                font-weight: 600;
            }
            QPushButton#autoLaunchCancelButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(51, 65, 85, 0.9), stop:1 rgba(30, 41, 59, 0.95));
                color: #cbd5e1;
                border: 1px solid rgba(71, 85, 105, 0.5);
                border-radius: 16px;
                padding: 12px 20px;
                min-width: __CANCEL_WIDTH__px;
                font-size: __STATUS_FONT__px;
                font-weight: 600;
            }
            QPushButton#autoLaunchCancelButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3b82f6, stop:1 #2563eb);
                border: 1px solid #3b82f6;
                color: #ffffff;
            }
            QPushButton#autoLaunchCancelButton:pressed {
                background-color: #1d4ed8;
                border: 1px solid #3b82f6;
            }
            """
        replacements = {
            "__SETTINGS_RADIUS__": str(max(14, m["settings_button_size"] // 2 - 4)),
            "__SETTINGS_FONT__": str(max(14, m["settings_button_font"])),
            "__HERO_TITLE__": str(m["hero_title_css"]),
            "__ROW_HEADING__": str(m["row_label_css"]),
            "__TILE_PADDING_BOTTOM__": str(m["tile_padding_bottom"]),
            "__TILE_PADDING_LR__": str(m["tile_padding_lr"]),
            "__TILE_PADDING_TOP__": str(m["tile_padding_top"]),
            "__TILE_FONT__": str(m["tile_font_css"]),
            "__TILE_SUBTITLE_TOP__": str(m["tile_padding_top_subtitle"]),
            "__TILE_SUBTITLE_BOTTOM__": str(m["tile_padding_bottom_subtitle"]),
            "__FOOTER_PADDING_Y__": str(m["footer_padding_y"]),
            "__FOOTER_FONT__": str(m["footer_font"]),
            "__STATUS_FONT__": str(m["status_font"]),
            "__CANCEL_WIDTH__": str(m["cancel_button_min_width"]),
        }
        for token, value in replacements.items():
            stylesheet = stylesheet.replace(token, value)
        self.setStyleSheet(stylesheet)

    def clear_tiles(self):
        while self.tile_stack.count():
            item = self.tile_stack.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        # Clear animation references
        if hasattr(self, '_fade_animations'):
            self._fade_animations.clear()

    def clear_fade_animations(self):
        """Clear fade animation references after they complete"""
        if hasattr(self, '_fade_animations'):
            self._fade_animations.clear()


    def populate_tiles(self):
        self.clear_tiles()
        self.tiles = []
        self.tile_rows = []
        self.current_index = 0
        self.current_row = 0
        self.current_col = 0
        self._icon_request_token += 1
        request_token = self._icon_request_token

        categories = self.get_categorized_entries(self.search_filter)
        for category_name, entries in categories:
            section = QWidget()
            section.setProperty("rowSection", "true")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(12)

            # Add icon based on category type
            if "Native" in category_name:
                # Native Apps - use clapperboard icon as fallback
                clapperboard_icon_path = resource_path("icons/clapperboard.png")
                if clapperboard_icon_path.exists():
                    white_icon = create_white_icon(str(clapperboard_icon_path), 32)
                    icon_label = QLabel()
                    icon_pixmap = white_icon.pixmap(32, 32)
                    if not icon_pixmap.isNull():
                        icon_label.setPixmap(icon_pixmap)
                        icon_label.setFixedSize(32, 32)
                    
                    text_label = QLabel(category_name)
                    text_label.setStyleSheet("color: #f1f5f9;")
                    text_label.setFont(QFont("Sans Serif", 20, QFont.Bold))
                    
                    header_layout = QHBoxLayout()
                    header_layout.setContentsMargins(0, 0, 0, 0)
                    header_layout.setSpacing(10)
                    header_layout.addWidget(icon_label)
                    header_layout.addWidget(text_label)
                    header_layout.addStretch()
                    
                    label = QWidget()
                    label.setLayout(header_layout)
                    label.setProperty("rowHeading", "true")
                    section_layout.addWidget(label)
                    label = None
                else:
                    icon = "🎬"
                    label = QLabel(f"{icon}  {category_name}")
            else:
                # Web Apps - use network icon
                network_icon_path = resource_path("icons/network.png")
                if network_icon_path.exists():
                    white_icon = create_white_icon(str(network_icon_path), 32)
                    icon_label = QLabel()
                    icon_pixmap = white_icon.pixmap(32, 32)
                    if not icon_pixmap.isNull():
                        icon_label.setPixmap(icon_pixmap)
                        icon_label.setFixedSize(32, 32)
                    
                    text_label = QLabel(category_name)
                    text_label.setStyleSheet("color: #f1f5f9;")
                    text_label.setFont(QFont("Sans Serif", 20, QFont.Bold))
                    
                    header_layout = QHBoxLayout()
                    header_layout.setContentsMargins(0, 0, 0, 0)
                    header_layout.setSpacing(10)
                    header_layout.addWidget(icon_label)
                    header_layout.addWidget(text_label)
                    header_layout.addStretch()
                    
                    label = QWidget()
                    label.setLayout(header_layout)
                    label.setProperty("rowHeading", "true")
                    section_layout.addWidget(label)
                    label = None
                else:
                    icon = "🌐"
                    label = QLabel(f"{icon}  {category_name}")

            if label is not None:
                label.setProperty("rowHeading", "true")
                section_layout.addWidget(label)

            row_scroll = QScrollArea()
            row_scroll.setProperty("rowScroll", "true")
            row_scroll.setFrameShape(QScrollArea.NoFrame)
            row_scroll.setWidgetResizable(False)
            row_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            row_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            row_scroll.setFixedHeight(self.ui_metrics["row_scroll_height"])

            row_widget = DropRowWidget()
            row_widget.setProperty("rowContent", "true")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(
                self.ui_metrics["row_content_margin_x"],
                self.ui_metrics["row_content_margin_y"],
                self.ui_metrics["row_content_margin_x"],
                self.ui_metrics["row_content_margin_y"],
            )
            row_layout.setSpacing(self.ui_metrics["row_content_spacing"])

            row_tiles = []
            card_index = 0
            for entry in entries:
                app = entry["item"]
                tile = TileButton(
                    app.get("name", "Untitled"),
                    "",
                    entry["tooltip"],
                    subtitle=entry["subtitle"],
                    metrics=self.ui_metrics,
                )
                tile.entry_kind = entry["kind"]
                tile.entry_item = app
                tile.clicked.connect(lambda checked=False, item=app, kind=entry["kind"]: self.launch_app(item, kind))
                
                # Create callbacks with proper closure
                current_app = app
                current_kind = entry["kind"]
                current_index = card_index
                current_entries = entries
                
                card = AppCard(
                    tile,
                    lambda checked=False, item=current_app, kind=current_kind: self.prompt_edit_entry(kind, item),
                    lambda checked=False, item=current_app, kind=current_kind: self.prompt_delete_entry(kind, item),
                    lambda checked=False, item=current_app, kind=current_kind: self.toggle_favorite(item, kind),
                    lambda direction, item=current_app, kind=current_kind, idx=current_index: self.reorder_app(item, kind, direction, idx, current_entries),
                    metrics=self.ui_metrics,
                    app_data=current_app,
                    kind=current_kind,
                )
                
                # Update favorite button state
                is_favorited = self.is_app_favorited(current_app, current_kind)
                if card.favorite_button:
                    card.favorite_button.setText("★" if is_favorited else "☆")
                    card.favorite_button.setToolTip("Remove from favorites" if is_favorited else "Add to favorites")
                
                tile.card_shell = card
                tile.row_scroll = row_scroll
                tile.section_widget = section
                row_layout.addWidget(card)
                self.tiles.append(tile)
                row_tiles.append(tile)
                card_index += 1

                future = self._icon_pool.submit(self._resolve_icon, entry)
                future.add_done_callback(
                    lambda pending, token=request_token, button=tile: self._queue_icon_result(token, button, pending)
                )

            row_layout.addStretch(1)
            row_widget.adjustSize()
            row_scroll.setWidget(row_widget)
            section_layout.addWidget(row_scroll)
            self.tile_stack.addWidget(section)
            if row_tiles:
                self.tile_rows.append(row_tiles)

        add_tile = TileButton(
            "Add App",
            "",
            "Save a new app or site to config.yaml",
            variant="accent",
            subtitle="Add a command or website",
            metrics=self.ui_metrics,
        )
        add_tile.clicked.connect(self.prompt_add_entry)
        add_section = QWidget()
        add_section.setProperty("rowSection", "true")
        add_section_layout = QVBoxLayout(add_section)
        add_section_layout.setContentsMargins(0, 0, 0, 0)
        add_section_layout.setSpacing(12)
        add_label = QLabel("Library")
        add_label.setProperty("rowHeading", "true")
        add_section_layout.addWidget(add_label)
        add_row_scroll = QScrollArea()
        add_row_scroll.setProperty("rowScroll", "true")
        add_row_scroll.setFrameShape(QScrollArea.NoFrame)
        add_row_scroll.setWidgetResizable(False)
        add_row_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        add_row_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        add_row_scroll.setFixedHeight(self.ui_metrics["row_scroll_height"])
        add_row_widget = DropRowWidget()
        add_row_widget.setProperty("rowContent", "true")
        add_row_layout = QHBoxLayout(add_row_widget)
        add_row_layout.setContentsMargins(
            self.ui_metrics["row_content_margin_x"],
            self.ui_metrics["row_content_margin_y"],
            self.ui_metrics["row_content_margin_x"],
            self.ui_metrics["row_content_margin_y"],
        )
        add_row_layout.setSpacing(self.ui_metrics["row_content_spacing"])
        add_card = AppCard(add_tile, show_actions=False, metrics=self.ui_metrics)
        add_tile.card_shell = add_card
        add_tile.row_scroll = add_row_scroll
        add_tile.section_widget = add_section
        add_row_layout.addWidget(add_card)
        add_row_layout.addStretch(1)
        add_row_widget.adjustSize()
        add_row_scroll.setWidget(add_row_widget)
        add_section_layout.addWidget(add_row_scroll)
        self.tile_stack.addWidget(add_section)
        self.tiles.append(add_tile)
        self.tile_rows.append([add_tile])

        self.tile_stack.addStretch(1)

        self.reset_auto_launch_timer()

    def show_loading_overlay(self, message: str = "Loading..."):
        """Show a loading overlay with message"""
        if self.loading_overlay is None:
            self.loading_overlay = QWidget(self)
            self.loading_overlay.setObjectName("loadingOverlay")
            self.loading_overlay.setStyleSheet("""
                QWidget#loadingOverlay {
                    background-color: rgba(15, 23, 42, 0.9);
                    border-radius: 12px;
                }
            """)
            self.loading_overlay.setFixedSize(400, 200)
            
            layout = QVBoxLayout(self.loading_overlay)
            layout.setAlignment(Qt.AlignCenter)
            
            self.loading_label = QLabel(message)
            self.loading_label.setObjectName("loadingLabel")
            self.loading_label.setStyleSheet("""
                QLabel {
                    color: #f1f5f9;
                    font-size: 18px;
                    font-weight: bold;
                    background-color: transparent;
                }
            """)
            self.loading_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(self.loading_label)
            
            # Center the overlay
            self.loading_overlay.setParent(self)
        
        self.loading_label.setText(message)
        
        # Center the overlay on the main window
        if self.loading_overlay.parent() is None:
            self.loading_overlay.setParent(self)
        
        x = (self.width() - self.loading_overlay.width()) // 2
        y = (self.height() - self.loading_overlay.height()) // 2
        self.loading_overlay.move(x, y)
        self.loading_overlay.show()
        self.loading_overlay.raise_()
    
    def hide_loading_overlay(self):
        """Hide the loading overlay"""
        if self.loading_overlay:
            self.loading_overlay.hide()

    def on_search_text_changed(self, text: str):
        """Handle search text changes"""
        self.search_filter = text
        self.is_search_active = bool(text.strip())
        
        # Save focus state
        focused_widget = QApplication.focusWidget()
        
        self.populate_tiles()
        
        # Restore focus to search input if it was focused
        if focused_widget == self.search_input:
            self.search_input.setFocus()
            # Move cursor to end
            self.search_input.setCursorPosition(len(text))
        elif self.tiles:
            self.focus_first_tile()

    def get_categorized_entries(self, filter_text: str = ""):
        native_entries = []
        web_entries = []
        favorites = self.config.get("favorites", [])
        
        # Get all categories from config
        user_categories = self.config.get("categories", {})

        for idx, app in enumerate(self.config.get("native_apps", [])):
            if not is_installed(app.get("cmd", "")):
                continue
            subtitle = app.get("cmd", "").split()[0] if app.get("cmd") else "Application"
            app_id = self.get_app_id(app)
            is_favorited = any(f.get("id") == app_id and f.get("kind") == "native" for f in favorites)
            
            # Apply search filter
            app_name = app.get("name", "").lower()
            if filter_text and filter_text.lower() not in app_name:
                continue
                
            native_entries.append({
                "kind": "native",
                "item": app,
                "subtitle": subtitle,
                "tooltip": app.get("cmd", ""),
                "favorited": is_favorited,
                "original_index": idx,
            })

        for idx, app in enumerate(self.config.get("web_apps", [])):
            url = app.get("url", "")
            subtitle = url.replace("https://", "").replace("http://", "")
            app_id = self.get_app_id(app)
            is_favorited = any(f.get("id") == app_id and f.get("kind") == "web" for f in favorites)
            
            # Apply search filter
            app_name = app.get("name", "").lower()
            if filter_text and filter_text.lower() not in app_name:
                continue
            
            web_entries.append({
                "kind": "web",
                "item": app,
                "subtitle": subtitle,
                "tooltip": url,
                "favorited": is_favorited,
                "original_index": idx,
            })
        
        # Sort entries: favorited first, then by original order
        native_entries.sort(key=lambda x: (not x.get("favorited", False), x.get("original_index", 0)))
        web_entries.sort(key=lambda x: (not x.get("favorited", False), x.get("original_index", 0)))

        categories = []
        
        # Add Favorites row if not filtering and there are favorited apps
        if not filter_text:
            favorite_entries = [e for e in native_entries + web_entries if e.get("favorited", False)]
            if favorite_entries:
                categories.append(("⭐ Favorites", favorite_entries))
        
        # Add user-defined categories if not filtering
        if not filter_text and user_categories:
            for category_name, app_names in user_categories.items():
                category_entries = []
                for entry in native_entries + web_entries:
                    if entry["item"].get("name") in app_names:
                        category_entries.append(entry)
                if category_entries:
                    categories.append((category_name, category_entries))
        
        # Add default categories if no filter or no user categories
        if not filter_text:
            if native_entries:
                categories.append(("Native Apps", native_entries))
            if web_entries:
                categories.append(("Web Apps", web_entries))
        else:
            # When filtering, show all matching apps in a single category
            all_filtered = native_entries + web_entries
            if all_filtered:
                categories.append((f"Search Results: '{filter_text}'", all_filtered))
        
        return categories

    def get_installed_apps(self):
        """Get list of installed apps for remote control app listing"""
        import base64
        apps_list = []
        categories = self.get_categorized_entries()
        
        for category_name, entries in categories:
            for entry in entries:
                item = entry["item"]
                app_id = item.get("id", item.get("name", "")).lower().replace(" ", "_")
                app_name = item.get("name", "Unknown")
                
                # Resolve actual icon path
                icon_path = ""
                if entry["kind"] == "native":
                    icon_path = resolve_native_icon(item)
                else:
                    icon_path = fetch_web_icon(item)
                
                # Convert icon to base64 data URI
                icon_data = ""
                if icon_path:
                    try:
                        icon_file = Path(icon_path)
                        if icon_file.exists():
                            with open(icon_file, "rb") as f:
                                icon_bytes = f.read()
                                icon_b64 = base64.b64encode(icon_bytes).decode('utf-8')
                                # Determine MIME type from extension
                                ext = icon_file.suffix.lower()
                                mime_types = {
                                    '.png': 'image/png',
                                    '.jpg': 'image/jpeg',
                                    '.jpeg': 'image/jpeg',
                                    '.gif': 'image/gif',
                                    '.svg': 'image/svg+xml',
                                    '.ico': 'image/x-icon',
                                    '.xpm': 'image/x-xpixmap',
                                }
                                mime_type = mime_types.get(ext, 'image/png')
                                icon_data = f"data:{mime_type};base64,{icon_b64}"
                    except Exception as e:
                        logging.warning("Failed to encode icon for %s: %s", app_name, e)
                
                apps_list.append({
                    "id": app_id,
                    "name": app_name,
                    "kind": entry["kind"],
                    "icon": icon_data,  # Base64 data URI
                    "category": category_name
                })
        
        return apps_list

    def launch_app_by_id(self, app_id):
        """Launch an app by its ID from remote control"""
        categories = self.get_categorized_entries()
        
        for category_name, entries in categories:
            for entry in entries:
                item = entry["item"]
                item_id = item.get("id", item.get("name", "")).lower().replace(" ", "_")
                
                if item_id == app_id:
                    logging.info("Launching app: %s (%s)", app_id, entry["kind"])
                    self.launch_app(item, entry["kind"])
                    return
        
        logging.warning("App not found: %s", app_id)

    def remove_app_by_id(self, app_id: str):
        """Remove an app by its ID from config"""
        app_id_normalized = app_id.lower().replace(" ", "_")
        
        # Try to remove from native apps
        native_apps = self.config.get("native_apps", [])
        for i, app in enumerate(native_apps):
            item_id = app.get("id", app.get("name", "")).lower().replace(" ", "_")
            if item_id == app_id_normalized:
                app_name = app.get("name")
                native_apps.pop(i)
                self.config["native_apps"] = native_apps
                save_config(self.config_path, self.config)
                logging.info("Removed native app: %s", app_name)
                # Refresh tiles immediately on main thread
                QTimer.singleShot(0, self.populate_tiles)
                return
        
        # Try to remove from web apps
        web_apps = self.config.get("web_apps", [])
        for i, app in enumerate(web_apps):
            item_id = app.get("id", app.get("name", "")).lower().replace(" ", "_")
            if item_id == app_id_normalized:
                app_name = app.get("name")
                web_apps.pop(i)
                self.config["web_apps"] = web_apps
                save_config(self.config_path, self.config)
                logging.info("Removed web app: %s", app_name)
                # Refresh tiles immediately on main thread
                QTimer.singleShot(0, self.populate_tiles)
                return
        
        logging.warning("App not found for removal: %s", app_id)

    def add_app_from_remote(self, app_id: str, app_name: str, app_kind: str):
        """Add an app to config from remote control request"""
        try:
            app_id_normalized = app_id.lower().replace(" ", "_")
            
            # Check if app already exists
            categories = self.get_categorized_entries()
            for category_name, entries in categories:
                for entry in entries:
                    item = entry["item"]
                    item_id = item.get("id", item.get("name", "")).lower().replace(" ", "_")
                    if item_id == app_id_normalized:
                        return False, f"{app_name} is already in your launcher"
            
            # Find the app in desktop files for native apps
            if app_kind == "native":
                from pathlib import Path
                desktop_dirs = desktop_file_locations()
                app_entry = None
                
                for desktop_dir in desktop_dirs:
                    if not desktop_dir.exists():
                        continue
                    for desktop_file in desktop_dir.glob("*.desktop"):
                        try:
                            parser = configparser.ConfigParser()
                            parser.read(desktop_file)
                            if parser.has_section("Desktop Entry"):
                                name = parser.get("Desktop Entry", "Name", fallback="")
                                if name.lower().replace(" ", "_") == app_id_normalized or \
                                   desktop_file.stem.lower().replace(" ", "_") == app_id_normalized:
                                    exec_cmd = parser.get("Desktop Entry", "Exec", fallback="")
                                    icon = parser.get("Desktop Entry", "Icon", fallback="")
                                    if exec_cmd:
                                        app_entry = {
                                            "id": app_id_normalized,
                                            "name": app_name,
                                            "cmd": exec_cmd.split()[0] if exec_cmd else "",
                                            "icon": icon
                                        }
                                        break
                        except Exception:
                            continue
                    if app_entry:
                        break
                
                if app_entry:
                    native_apps = self.config.get("native_apps", [])
                    native_apps.append(app_entry)
                    self.config["native_apps"] = native_apps
                    save_config(self.config_path, self.config)
                    logging.info("Added native app: %s", app_name)
                    # Refresh tiles on main thread
                    QTimer.singleShot(0, self.populate_tiles)
                    return True, f"{app_name} added to launcher"
                else:
                    return False, f"Could not find {app_name} on your system"
            
            # For web apps, they should already be in config
            return False, f"Web apps must be added through settings"
            
        except Exception as e:
            logging.exception("Failed to add app")
            return False, f"Error adding app: {str(e)}"

    def get_launchable_entries(self):
        entries = []
        for _, category_entries in self.get_categorized_entries():
            entries.extend(category_entries)
        return entries

    def _resolve_icon(self, entry):
        app = entry["item"]
        if entry["kind"] == "native":
            return resolve_native_icon(app)
        return fetch_web_icon(app)

    def _queue_icon_result(self, request_token: int, tile: TileButton, future):
        icon_path = ""
        try:
            icon_path = future.result() or ""
        except Exception:
            logging.exception("Failed to resolve icon for %s", getattr(tile, "title", "tile"))
        self._icon_bridge.icon_ready.emit(request_token, tile, icon_path)

    def _apply_resolved_icon(self, request_token: int, tile: TileButton, icon_path: str):
        if request_token != self._icon_request_token or tile not in self.tiles:
            return
        tile.set_tile_icon(icon_path)

    def _flat_index_for_position(self, row: int, col: int):
        index = 0
        for row_idx, row_tiles in enumerate(self.tile_rows):
            if row_idx == row:
                return index + col
            index += len(row_tiles)
        return 0

    def current_tile(self):
        if not self.tile_rows:
            return None
        row = max(0, min(self.current_row, len(self.tile_rows) - 1))
        col = max(0, min(self.current_col, len(self.tile_rows[row]) - 1))
        return self.tile_rows[row][col]

    def focus_tile_at(self, row: int, col: int):
        if not self.tile_rows:
            return
        row = max(0, min(row, len(self.tile_rows) - 1))
        col = max(0, min(col, len(self.tile_rows[row]) - 1))
        self.current_row = row
        self.current_col = col
        self.current_index = self._flat_index_for_position(row, col)
        self.tile_rows[row][col].setFocus()

    def focus_first_tile(self):
        if not self.tile_rows:
            return
        self.focus_tile_at(0, 0)

    def focus_entry_tile(self, kind: str, item):
        for row_idx, row_tiles in enumerate(self.tile_rows):
            for col_idx, tile in enumerate(row_tiles):
                if tile.entry_kind == kind and tile.entry_item is item:
                    self.focus_tile_at(row_idx, col_idx)
                    return True
        return False

    def get_auto_launch_options(self):
        options = []
        for entry in self.get_launchable_entries():
            app = entry["item"]
            target = app.get("cmd", "") if entry["kind"] == "native" else app.get("url", "")
            label = f"{app.get('name', 'Untitled')} ({'App' if entry['kind'] == 'native' else 'Site'})"
            options.append({
                "kind": entry["kind"],
                "target": target,
                "label": label,
            })
        return options

    def normalize_url(self, url: str) -> str:
        cleaned = url.strip()
        if not cleaned:
            return cleaned
        if "://" not in cleaned:
            cleaned = "https://" + cleaned
        return cleaned

    def prompt_add_entry(self):
        dialog = AddItemDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        entry_type = values["type"]
        name = values["name"]
        value = values["value"]
        if not name or not value:
            QMessageBox.information(self, "Missing Details", "Enter both a name and a command or URL.")
            return
        if entry_type == "Application":
            self.add_native_app(name, value)
            return
        self.add_web_app(name, value)

    def prompt_edit_entry(self, kind: str, app):
        type_text = "Application" if kind == "native" else "Website"
        value_text = app.get("cmd", "") if kind == "native" else app.get("url", "")
        dialog = AddItemDialog(
            self,
            title_text="Edit App",
            type_text=type_text,
            name_text=app.get("name", ""),
            value_text=value_text,
            allow_type_change=False,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        if not values["name"] or not values["value"]:
            QMessageBox.information(self, "Missing Details", "Enter both a name and a command or URL.")
            return

        app["name"] = values["name"]
        if kind == "native":
            app["cmd"] = values["value"]
        else:
            app["url"] = self.normalize_url(values["value"])

        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save config")
            QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}")
            return

        self.populate_tiles()
        if self.tiles:
            self.focus_first_tile()
        QMessageBox.information(self, "Saved", f"{values['name']} has been updated.")

    def prompt_delete_entry(self, kind: str, app):
        dialog = ConfirmDialog(
            self,
            title_text="Delete App",
            body_text=f"Remove '{app.get('name', 'this app')}' from LinuxTV?",
        )
        if dialog.exec() != QDialog.Accepted:
            return

        collection_name = "native_apps" if kind == "native" else "web_apps"
        collection = self.config.get(collection_name, [])
        try:
            collection.remove(app)
        except ValueError:
            return

        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save config")
            QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}")
            collection.append(app)
            return

        self.populate_tiles()
        if self.tiles:
            self.focus_first_tile()

    def get_app_id(self, app):
        """Get a unique identifier for an app."""
        return app.get("id", app.get("name", "")).lower().replace(" ", "_")
    
    def is_app_favorited(self, app, kind):
        """Check if an app is in the favorites list."""
        favorites = self.config.get("favorites", [])
        app_id = self.get_app_id(app)
        for fav in favorites:
            if fav.get("id") == app_id and fav.get("kind") == kind:
                return True
        return False
    
    def toggle_favorite(self, app, kind):
        """Toggle an app's favorite status."""
        favorites = self.config.get("favorites", [])
        app_id = self.get_app_id(app)
        
        # Check if already favorited and remove if found
        was_favorited = False
        for i, fav in enumerate(favorites):
            if fav.get("id") == app_id and fav.get("kind") == kind:
                # Remove from favorites
                favorites.pop(i)
                was_favorited = True
                break
        
        # Only add if it wasn't favorited before
        if not was_favorited:
            # Add to favorites
            favorites.append({
                "id": app_id,
                "kind": kind,
                "name": app.get("name", "")
            })
        
        self.config["favorites"] = favorites
        try:
            save_config(self.config_path, self.config)
            logging.info("%s %s from favorites", "Removed" if was_favorited else "Added", app.get("name", ""))
        except Exception as exc:
            logging.exception("Failed to save favorites")
            QMessageBox.critical(self, "Save Failed", f"Could not save favorites:\n{exc}")
            return
        
        # Refresh tiles to update order and button states
        QTimer.singleShot(0, self.populate_tiles)
    
    def reorder_app(self, app, kind, direction, current_index, entries):
        """Move an app left or right in the order."""
        collection_name = "native_apps" if kind == "native" else "web_apps"
        collection = self.config.get(collection_name, [])
        
        # Find the app in the config
        app_id = self.get_app_id(app)
        config_index = -1
        for i, item in enumerate(collection):
            if self.get_app_id(item) == app_id:
                config_index = i
                break
        
        if config_index == -1:
            return
        
        # Calculate new index
        if direction == "left" and config_index > 0:
            new_index = config_index - 1
        elif direction == "right" and config_index < len(collection) - 1:
            new_index = config_index + 1
        else:
            return  # Can't move further
        
        # Swap in config
        collection[config_index], collection[new_index] = collection[new_index], collection[config_index]
        self.config[collection_name] = collection
        
        try:
            save_config(self.config_path, self.config)
            logging.info("Moved %s %s", app.get("name", ""), direction)
        except Exception as exc:
            logging.exception("Failed to save config")
            QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}")
            return
        
        # Refresh tiles immediately
        QTimer.singleShot(0, self.populate_tiles)

    def handle_card_drop(self, source_data, target_app, target_kind):
        """Handle drag-and-drop reordering of cards"""
        import logging
        source_kind = source_data.get("kind")
        source_app_id = source_data.get("app_id")
        
        logging.info(f"handle_card_drop called: source={source_app_id} ({source_kind}), target={target_app} ({target_kind})")
        
        # Only allow reordering within the same collection
        if source_kind != target_kind:
            logging.info("Different kinds, skipping reorder")
            return
        
        collection_name = "native_apps" if source_kind == "native" else "web_apps"
        collection = self.config.get(collection_name, [])
        
        # Find source app by ID
        source_app = None
        for item in collection:
            if source_kind == "native":
                if item.get("cmd", "") == source_app_id:
                    source_app = item
                    break
            else:
                if item.get("url", "") == source_app_id:
                    source_app = item
                    break
        
        if not source_app:
            return
        
        # Find indices in config
        source_id = self.get_app_id(source_app)
        target_id = self.get_app_id(target_app)
        
        source_index = -1
        target_index = -1
        
        for i, item in enumerate(collection):
            if self.get_app_id(item) == source_id:
                source_index = i
            if self.get_app_id(item) == target_id:
                target_index = i
        
        if source_index == -1 or target_index == -1 or source_index == target_index:
            logging.info(f"Invalid indices: source={source_index}, target={target_index}")
            return
        
        logging.info(f"Swapping positions: {source_index} -> {target_index}")
        
        # Remove source from its position
        source_item = collection.pop(source_index)
        
        # Adjust target index if source was before it
        if source_index < target_index:
            target_index -= 1
        
        # Insert source at target position
        collection.insert(target_index, source_item)
        
        # Save config
        self.config[collection_name] = collection
        try:
            save_config(self.config_path, self.config)
            logging.info("Drag-drop reordered %s to position %d", source_app.get("name", ""), target_index)
        except Exception as exc:
            logging.exception("Failed to save config")
            QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}")
            return
        
        # Refresh tiles
        QTimer.singleShot(0, self.populate_tiles)

    def open_remote_settings(self):
        """Open remote login credentials dialog"""
        auth = self.config.get("auth", {})
        dialog = RemoteLoginDialog(
            auth.get("username", ""),
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        username = values["username"]
        password = values["password"]
        confirm_password = values["confirm_password"]

        # Validate and save remote credentials
        if username or password or confirm_password:
            if not username:
                QMessageBox.information(self, "Invalid Input", "Username is required.")
                return
            if password != confirm_password:
                QMessageBox.information(self, "Password Mismatch", "Passwords do not match.")
                return
            if len(password) < 4:
                QMessageBox.information(self, "Weak Password", "Password must be at least 4 characters.")
                return

        # Hash and save credentials
        password_hash, password_salt = hash_remote_password(password)
        # Also generate simple SHA-256 hash for challenge-response authentication
        password_simple_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        self.config["auth"] = {
            "username": username,
            "password_hash": password_hash,
            "password_salt": password_salt,
            "password_simple_hash": password_simple_hash
        }
        save_config(self.config_path, self.config)
        QMessageBox.information(self, "Saved", "Remote login credentials updated.")

    def open_settings(self):
        """Open application settings dialog"""
        auto_launch = self.config.get("auto_launch", {})
        dialog = SettingsDialog(
            auto_launch,
            self.get_auto_launch_options(),
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        auto_launch_delay_text = values["auto_launch_delay_seconds"]
        try:
            auto_launch_delay_seconds = int(auto_launch_delay_text)
        except ValueError:
            QMessageBox.information(self, "Invalid Delay", "Enter a whole number of seconds for auto open.")
            return
        if auto_launch_delay_seconds < 1:
            QMessageBox.information(self, "Invalid Delay", "Auto open delay must be at least 1 second.")
            return

        self.config["auto_launch"] = {
            "app_kind": values["auto_launch_app_kind"],
            "app_target": values["auto_launch_app_target"],
            "delay_seconds": auto_launch_delay_seconds,
        }

        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save settings")
            QMessageBox.critical(self, "Save Failed", f"Could not save settings:\n{exc}")
            return

        self.reset_auto_launch_timer()
        QMessageBox.information(self, "Saved", "Settings saved.")

    def open_network_settings(self):
        """Open the Network settings dialog"""
        dialog = NetworkDialog(
            [],
            "",
            self.scan_wifi_networks,
            self.connect_to_wifi,
            self.disconnect_from_wifi,
            self,
        )
        dialog.exec()

    def open_bluetooth_settings(self):
        """Open the Bluetooth settings dialog"""
        dialog = BluetoothDialog(
            [],
            "",
            self.scan_bluetooth_devices,
            self.connect_to_bluetooth,
            self.remove_bluetooth_device,
            self,
        )
        dialog.exec()

    def open_sound_settings(self):
        """Open the Sound settings dialog"""
        dialog = SoundDialog(self)
        dialog.exec()

    def open_brightness_settings(self):
        """Open the Brightness settings dialog"""
        dialog = BrightnessDialog(self)
        dialog.exec()

    def scan_wifi_networks(self):
        nmcli = shutil.which("nmcli")
        if not nmcli:
            return [], "", "NetworkManager tools are not installed. Install `network-manager` to manage Wi-Fi here."

        import time
        
        # Turn on WiFi if it's disabled
        try:
            # Check current WiFi status
            radio_result = subprocess.run(
                [nmcli, "radio", "wifi"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            
            # If WiFi is off, turn it on
            if "disabled" in radio_result.stdout.lower() or radio_result.returncode != 0:
                subprocess.run(
                    [nmcli, "radio", "wifi", "on"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10
                )
                time.sleep(3)  # Wait for WiFi to enable
        except Exception:
            logging.exception("Failed to enable Wi-Fi radio")
        
        # Try to enable NetworkManager if it's not running
        try:
            systemctl_path = shutil.which("systemctl")
            if systemctl_path:
                status_result = subprocess.run(
                    [systemctl_path, "is-active", "NetworkManager"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                if status_result.stdout.strip() != "active":
                    subprocess.run(
                        ["sudo", systemctl_path, "start", "NetworkManager"],
                        capture_output=True,
                        check=False,
                        timeout=10
                    )
                    time.sleep(2)
        except Exception:
            logging.exception("Failed to start NetworkManager")

        current_wifi = ""
        try:
            current_result = subprocess.run(
                [nmcli, "--colors", "no", "--terse", "--fields", "NAME,TYPE", "connection", "show", "--active"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if current_result.returncode == 0:
                for line in current_result.stdout.splitlines():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip() == "802-11-wireless":
                        current_wifi = parts[0].strip()
                        break
        except Exception:
            logging.exception("Failed to read active Wi-Fi connection")

        try:
            result = subprocess.run(
                [
                    nmcli,
                    "--colors",
                    "no",
                    "--escape",
                    "yes",
                    "--terse",
                    "--fields",
                    "IN-USE,SSID,SIGNAL,SECURITY",
                    "device",
                    "wifi",
                    "list",
                    "--rescan",
                    "yes",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except Exception as exc:
            logging.exception("Failed to scan Wi-Fi networks")
            return [], current_wifi, f"Could not scan for Wi-Fi networks: {exc}"

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Unknown error").strip()
            return [], current_wifi, f"Could not scan for Wi-Fi networks: {message}"

        networks = []
        seen_ssids = set()
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            parts = line.split(":")
            if len(parts) < 4:
                continue

            in_use = parts[0].strip()
            security = parts[-1].strip() or "Open"
            signal_strength = parts[-2].strip() or "?"
            ssid = ":".join(parts[1:-2]).replace("\\:", ":").strip()
            if not ssid or ssid in seen_ssids:
                continue

            active = in_use == "*"
            label = f"{ssid}  |  {signal_strength}%  |  {security}"
            if active:
                label = f"{label}  |  Connected"
            networks.append(
                {
                    "ssid": ssid,
                    "label": label,
                    "security": security,
                    "signal": int(signal_strength) if signal_strength.isdigit() else -1,
                    "active": active,
                }
            )
            seen_ssids.add(ssid)

        if current_wifi and current_wifi not in seen_ssids:
            networks.insert(
                0,
                {
                    "ssid": current_wifi,
                    "label": f"{current_wifi}  |  Connected",
                    "security": "",
                    "signal": 101,
                    "active": True,
                },
            )

        networks.sort(key=lambda item: (0 if item.get("active") else 1, -(item.get("signal", -1)), item.get("ssid", "").lower()))
        for item in networks:
            item.pop("signal", None)
            item.pop("active", None)

        message = f"Found {len(networks)} network(s)." if networks else "No Wi-Fi networks found. Try Refresh Networks again."
        return networks, current_wifi, message

    def connect_to_wifi(self, network_info, password: str):
        if isinstance(network_info, dict):
            ssid = str(network_info.get("ssid", "")).strip()
            security = str(network_info.get("security", "")).strip()
        else:
            ssid = str(network_info or "").strip()
            security = ""
        if not ssid:
            return False, "Enter or choose a Wi-Fi network name first.", ""

        nmcli = shutil.which("nmcli")
        if not nmcli:
            return False, "NetworkManager tools are not installed on this device.", ""

        device_name = ""
        try:
            device_result = subprocess.run(
                [nmcli, "--colors", "no", "--terse", "--fields", "DEVICE,TYPE,STATE", "device", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if device_result.returncode == 0:
                for line in device_result.stdout.splitlines():
                    parts = line.split(":")
                    if len(parts) >= 2 and parts[1].strip() == "wifi":
                        device_name = parts[0].strip()
                        break
        except Exception:
            logging.exception("Failed to inspect Wi-Fi device status")

        secure_network = bool(security and security.lower() not in ("", "--", "open"))

        # If a profile already exists, try bringing it up first.
        try:
            profile_result = subprocess.run(
                [nmcli, "--colors", "no", "--terse", "--fields", "NAME,TYPE", "connection", "show"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if profile_result.returncode == 0:
                for line in profile_result.stdout.splitlines():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[0].strip() == ssid and parts[1].strip() == "802-11-wireless":
                        up_command = [nmcli, "connection", "up", ssid]
                        if device_name:
                            up_command.extend(["ifname", device_name])
                        up_result = subprocess.run(
                            up_command,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=45,
                        )
                        if up_result.returncode == 0:
                            return True, f"Connected to {ssid}.", ssid
                        break
        except Exception:
            logging.exception("Failed to try saved Wi-Fi connection profile")

        if secure_network and not password:
            return False, f"{ssid} needs a Wi-Fi password before it can connect.", ""

        command = [nmcli, "device", "wifi", "connect", ssid]
        if secure_network and password:
            command.extend(["password", password])
        if device_name:
            command.extend(["ifname", device_name])

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
        except Exception as exc:
            logging.exception("Failed to connect to Wi-Fi")
            return False, f"Could not connect to {ssid}: {exc}", ""

        if result.returncode != 0 and not secure_network:
            try:
                hidden_fallback = [nmcli, "device", "wifi", "connect", ssid, "hidden", "yes"]
                if device_name:
                    hidden_fallback.extend(["ifname", device_name])
                result = subprocess.run(
                    hidden_fallback,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=45,
                )
            except Exception as exc:
                logging.exception("Failed to retry Wi-Fi connection")
                return False, f"Could not connect to {ssid}: {exc}", ""

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Unknown error").strip()
            return False, f"Could not connect to {ssid}: {message}", ""

        return True, f"Connected to {ssid}.", ssid

    def disconnect_from_wifi(self, network_info):
        # Disconnect and forget a Wi-Fi network.
        if isinstance(network_info, dict):
            ssid = str(network_info.get("ssid", "")).strip()
        else:
            ssid = str(network_info or "").strip()
        if not ssid:
            return False, "Enter or choose a Wi-Fi network name first."

        nmcli = shutil.which("nmcli")
        if not nmcli:
            return False, "NetworkManager tools are not installed on this device."
            
        try:
            # First disconnect if currently connected
            subprocess.run(
                [nmcli, "device", "disconnect", ssid],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            
            # Then delete the connection profile to forget it
            result = subprocess.run(
                [nmcli, "connection", "delete", ssid],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            
            if result.returncode == 0:
                return True, f"Forgot network {ssid}."
            else:
                message = (result.stderr or result.stdout or "Unknown error").strip()
                return False, f"Could not forget {ssid}: {message}"
                
        except Exception as exc:
            logging.exception("Failed to forget Wi-Fi network")
            return False, f"Could not forget {ssid}: {exc}"

    def scan_bluetooth_devices(self):
        bluetoothctl = shutil.which("bluetoothctl")
        if not bluetoothctl:
            return [], "", "bluetoothctl is not installed. Install `bluez` to manage Bluetooth here."
        
        current_bluetooth = ""
        devices = {}
        import time
        import re
        
        try:
            # Try to start Bluetooth service if it's not running
            systemctl_path = shutil.which("systemctl")
            if systemctl_path:
                # Check if bluetooth service is active
                status_result = subprocess.run(
                    [systemctl_path, "is-active", "bluetooth"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5
                )
                # If not active, try to start it
                if status_result.stdout.strip() != "active":
                    subprocess.run(
                        ["sudo", systemctl_path, "start", "bluetooth"],
                        capture_output=True,
                        check=False,
                        timeout=10
                    )
                    time.sleep(2)  # Reduced from 3s
            
            # Start interactive bluetoothctl session
            bt_proc = subprocess.Popen(
                [bluetoothctl],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            # First, try to unblock Bluetooth if rfkill is available
            rfkill_path = shutil.which("rfkill") or shutil.which("rfkill", path=os.environ.get("PATH", "") + os.pathsep + "/usr/sbin")
            if rfkill_path:
                subprocess.run([rfkill_path, "unblock", "bluetooth"], capture_output=True, check=False, timeout=5)
            
            # Check current controller state
            show_result = subprocess.run(
                [bluetoothctl, "show"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            
            bluetooth_was_off = True
            if show_result.returncode == 0:
                for line in show_result.stdout.splitlines():
                    if 'Powered:' in line:
                        if 'yes' in line.lower():
                            bluetooth_was_off = False
                        break
            
            # Power on the controller - this will turn on Bluetooth if it's off
            if bt_proc.stdin:
                bt_proc.stdin.write("power on\n")
                bt_proc.stdin.flush()
                time.sleep(2)  # Reduced from 4s
            
            # Verify power on worked by checking show output
            if bt_proc.stdin:
                bt_proc.stdin.write("show\n")
                bt_proc.stdin.flush()
                time.sleep(1)  # Reduced from 2s
            
            # List controllers to confirm
            if bt_proc.stdin:
                bt_proc.stdin.write("list\n")
                bt_proc.stdin.flush()
                time.sleep(0.5)  # Reduced from 1s
            
            # Enable the controller (in case it was disabled)
            if bt_proc.stdin:
                bt_proc.stdin.write("enable\n")
                bt_proc.stdin.flush()
                time.sleep(1)  # Reduced from 2s
            
            # Make controller discoverable and pairable
            if bt_proc.stdin:
                bt_proc.stdin.write("discoverable on\n")
                bt_proc.stdin.flush()
                time.sleep(0.5)  # Reduced from 1s
                
                bt_proc.stdin.write("pairable on\n")
                bt_proc.stdin.flush()
                time.sleep(0.5)  # Reduced from 1s
            
            # Enable agent
            if bt_proc.stdin:
                bt_proc.stdin.write("agent on\n")
                bt_proc.stdin.flush()
                time.sleep(0.5)  # Reduced from 1s
                
                bt_proc.stdin.write("default-agent\n")
                bt_proc.stdin.flush()
                time.sleep(0.5)  # Reduced from 1s
            
            # Start scanning - this will discover ALL nearby devices
            if bt_proc.stdin:
                bt_proc.stdin.write("scan on\n")
                bt_proc.stdin.flush()
            
            # Wait for scan to discover devices (reduced from 15s to 8s)
            time.sleep(8)
            
            # Stop scanning
            if bt_proc.stdin:
                bt_proc.stdin.write("scan off\n")
                bt_proc.stdin.flush()
                time.sleep(1)  # Reduced from 2s
            
            # Get all discovered devices
            if bt_proc.stdin:
                bt_proc.stdin.write("devices\n")
                bt_proc.stdin.flush()
                time.sleep(1)  # Reduced from 2s
            
            # Get the output
            bt_proc.terminate()
            try:
                stdout, stderr = bt_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                bt_proc.kill()
                stdout, stderr = bt_proc.communicate(timeout=5)
            
            # Parse discovered devices from output
            # Format: "Device XX:XX:XX:XX:XX:XX Device Name"
            device_pattern = re.compile(r'^Device\s+([\w:]+)\s+(.+)$', re.MULTILINE)
            for match in device_pattern.finditer(stdout):
                mac = match.group(1)
                name = match.group(2).strip()
                if mac not in devices:
                    devices[mac] = {
                        "mac": mac,
                        "name": name,
                        "label": f"{name} ({mac})",
                        "connected": False,
                        "paired": False
                    }
            
            # Get detailed info for each device to check connection/paired status
            for mac in list(devices.keys()):
                try:
                    info_result = subprocess.run(
                        [bluetoothctl, "info", mac],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    
                    if info_result.returncode == 0:
                        is_connected = False
                        is_paired = False
                        
                        for line in info_result.stdout.splitlines():
                            if 'Connected:' in line and 'yes' in line.lower():
                                is_connected = True
                            if 'Paired:' in line and 'yes' in line.lower():
                                is_paired = True
                        
                        # Update label with status
                        status_parts = []
                        if is_connected:
                            status_parts.append("Connected")
                        if is_paired:
                            status_parts.append("Paired")
                        
                        if status_parts:
                            devices[mac]["label"] = f"{devices[mac]['name']} ({mac}) [{' | '.join(status_parts)}]"
                            devices[mac]["connected"] = is_connected
                            devices[mac]["paired"] = is_paired
                        else:
                            devices[mac]["label"] = f"{devices[mac]['name']} ({mac}) [Discovered]"
                except Exception:
                    pass
            
            # Also check paired-devices to ensure completeness
            try:
                paired_result = subprocess.run(
                    [bluetoothctl, "paired-devices"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                
                if paired_result.returncode == 0:
                    for line in paired_result.stdout.splitlines():
                        match = re.match(r'Device\s+([\w:]+)\s+(.+)', line)
                        if match:
                            mac = match.group(1)
                            name = match.group(2).strip()
                            if mac not in devices:
                                devices[mac] = {
                                    "mac": mac,
                                    "name": name,
                                    "label": f"{name} ({mac}) [Paired]",
                                    "connected": False,
                                    "paired": True
                                }
            except Exception:
                pass
                
        except Exception as exc:
            import logging
            logging.exception("Failed to scan Bluetooth devices")
            return [], "", f"Could not scan: {exc}"
        
        # Convert to list and sort: connected first, then paired, then by name
        device_list = list(devices.values())
        device_list.sort(key=lambda d: (not d.get("connected", False), not d.get("paired", False), d.get("name", "")))
            
        if not device_list:
            message = "No Bluetooth devices found. Try: 1) sudo rfkill unblock bluetooth, 2) Ensure Bluetooth service is running (sudo systemctl status bluetooth), 3) Make devices discoverable."
        else:
            message = f"Found {len(device_list)} device(s)."
        
        return device_list, current_bluetooth, message

    def connect_to_bluetooth(self, device_info):
        if isinstance(device_info, dict):
            mac = str(device_info.get("mac", "")).strip()
        else:
            mac = str(device_info or "").strip()
        if not mac:
            return False, "Enter or choose a Bluetooth MAC address first.", ""

        bluetoothctl = shutil.which("bluetoothctl")
        if not bluetoothctl:
            return False, "bluetoothctl is not installed on this device.", ""
            
        try:
            import logging
            logging.info(f"Attempting to connect to Bluetooth device: {mac}")
            
            # Step 1: Check if device is already paired
            info_res = subprocess.run(
                [bluetoothctl, "info", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            
            is_paired = "Paired: yes" in info_res.stdout
            is_connected = "Connected: yes" in info_res.stdout
            
            if is_connected:
                return True, f"Already connected to {mac}.", mac
            
            # Step 2: If not paired, pair first with better error handling
            if not is_paired:
                logging.info(f"Device {mac} is not paired. Attempting to pair...")
                pair_result = subprocess.run(
                    [bluetoothctl, "pair", mac],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,  # Increased timeout for pairing
                )
                
                pair_output = (pair_result.stdout or "") + (pair_result.stderr or "")
                logging.info(f"Pair result: {pair_output}")
                
                # Check if pairing was successful
                if "Pairing successful" not in pair_output and pair_result.returncode != 0:
                    # Pairing failed
                    error_msg = (pair_result.stderr or pair_result.stdout or "Unknown pairing error").strip()
                    return False, f"Pairing failed for {mac}: {error_msg[-200:]}", ""
            
            # Step 3: Trust the device (important for automatic reconnection)
            logging.info(f"Trusting device {mac}...")
            trust_result = subprocess.run(
                [bluetoothctl, "trust", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            trust_output = (trust_result.stdout or "") + (trust_result.stderr or "")
            logging.info(f"Trust result: {trust_output}")
            
            # Step 4: Connect to the device
            logging.info(f"Connecting to device {mac}...")
            connect_result = subprocess.run(
                [bluetoothctl, "connect", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,  # Increased timeout for connection
            )
            
            connect_output = (connect_result.stdout or "") + (connect_result.stderr or "")
            logging.info(f"Connect result: {connect_output}")
            
            # Check if connection was successful
            if "Connection successful" in connect_output:
                return True, f"Connected to {mac}.", mac
            
            # Verify connection status
            info_res = subprocess.run(
                [bluetoothctl, "info", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            
            if "Connected: yes" in info_res.stdout:
                return True, f"Connected to {mac}.", mac
            
            # If we reach here, connection failed
            # Extract meaningful error message
            error_msg = connect_output.strip()
            if "br-connection-unknown" in error_msg:
                error_msg = "Connection failed with unknown error. Try: 1) Remove the device and pair again, 2) Make sure the device is in pairing mode, 3) Check if the device is connected to another system."
            elif "Failed to connect" in error_msg:
                # Extract the specific error
                import re
                error_match = re.search(r'Failed to connect:.*?([\w-]+)$', error_msg, re.MULTILINE)
                if error_match:
                    specific_error = error_match.group(1)
                    error_msg = f"Connection failed: {specific_error}. Make sure the device is discoverable and try again."
            
            return False, f"Could not connect to {mac}: {error_msg[-300:]}", ""
                
        except Exception as exc:
            import logging
            logging.exception("Failed to connect to Bluetooth")
            return False, f"Could not connect to {mac}: {exc}", ""

    def remove_bluetooth_device(self, device_info):
        """Remove/unpair a Bluetooth device."""
        if isinstance(device_info, dict):
            mac = str(device_info.get("mac", "")).strip()
        else:
            mac = str(device_info or "").strip()
        if not mac:
            return False, "Select a device to remove."

        bluetoothctl = shutil.which("bluetoothctl")
        if not bluetoothctl:
            return False, "bluetoothctl is not installed on this device."
            
        try:
            import logging
            logging.info(f"Attempting to remove Bluetooth device: {mac}")
            
            # First, disconnect if connected
            subprocess.run(
                [bluetoothctl, "disconnect", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            
            # Remove trust
            subprocess.run(
                [bluetoothctl, "untrust", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            
            # Remove the device
            remove_result = subprocess.run(
                [bluetoothctl, "remove", mac],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            
            remove_output = (remove_result.stdout or "") + (remove_result.stderr or "")
            logging.info(f"Remove result: {remove_output}")
            
            if "Device has been removed" in remove_output or remove_result.returncode == 0:
                return True, f"Removed device {mac}."
            else:
                error_msg = (remove_result.stderr or remove_result.stdout or "Unknown error").strip()
                return False, f"Failed to remove {mac}: {error_msg[-200:]}"
                
        except Exception as exc:
            import logging
            logging.exception("Failed to remove Bluetooth device")
            return False, f"Could not remove {mac}: {exc}"

    def get_audio_sinks(self):
        """Get list of available audio output devices"""
        pactl = shutil.which("pactl")
        if not pactl:
            return []
        
        sinks = []
        try:
            result = subprocess.run(
                [pactl, "list", "short", "sinks"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        sink_name = parts[1]
                        # Get friendly name
                        friendly_name = self.get_sink_friendly_name(sink_name)
                        sinks.append({
                            "name": sink_name,
                            "label": friendly_name
                        })
        except Exception as e:
            logging.error(f"Error getting audio sinks: {e}")
        
        return sinks

    def get_sink_friendly_name(self, sink_name):
        """Extract a friendly name from sink name"""
        pactl = shutil.which("pactl")
        if not pactl:
            return sink_name
        
        try:
            result = subprocess.run(
                [pactl, "get-sink-info", sink_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "Description:" in line:
                        return line.split("Description:", 1)[1].strip()
        except Exception:
            pass
        
        # Fallback: extract from sink name
        return sink_name.split(".")[-1] if "." in sink_name else sink_name

    def get_default_audio_sink(self):
        """Get the current default audio sink"""
        pactl = shutil.which("pactl")
        if not pactl:
            return ""
        
        try:
            result = subprocess.run(
                [pactl, "get-default-sink"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def set_default_audio_sink(self, sink_name):
        """Set the default audio sink"""
        if not sink_name:
            return False
        
        pactl = shutil.which("pactl")
        if not pactl:
            return False
        
        try:
            result = subprocess.run(
                [pactl, "set-default-sink", sink_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=10
            )
            
            if result.returncode == 0:
                logging.info(f"Set default audio sink to: {sink_name}")
                return True
            else:
                logging.error(f"Failed to set default sink: {result.stderr}")
                return False
        except Exception as e:
            logging.error(f"Error setting default sink: {e}")
            return False

    def update_from_github(self):
        git = shutil.which("git")
        if not git:
            return False, "Git is not installed. Install `git` to enable in-app updates."

        app_root = Path(__file__).resolve().parent.parent
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            if (app_root / ".git").exists():
                result = subprocess.run(
                    [git, "-C", str(app_root), "pull", "--ff-only", "origin", "main"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                if result.returncode != 0:
                    message = (result.stderr or result.stdout or "Unknown error").strip()
                    return False, f"Update failed: {message}"
                output = (result.stdout or result.stderr or "").strip()
                if "Already up to date" in output:
                    return True, "LinuxTV is already up to date."
                return True, "LinuxTV was updated from GitHub. Restart the app to load the new version."

            with tempfile.TemporaryDirectory(prefix="linuxtv-update-") as temp_dir:
                clone_result = subprocess.run(
                    [git, "clone", "--depth", "1", UPDATE_REPO_URL, temp_dir],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=180,
                )
                if clone_result.returncode != 0:
                    message = (clone_result.stderr or clone_result.stdout or "Unknown error").strip()
                    return False, f"Update failed: {message}"

                source_root = Path(temp_dir)
                excluded = {".git", ".venv", ".linuxtv_venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
                for child in source_root.iterdir():
                    if child.name in excluded:
                        continue
                    target = app_root / child.name
                    if child.is_dir():
                        if target.exists() and not target.is_dir():
                            target.unlink()
                        if not target.exists():
                            shutil.copytree(child, target)
                        else:
                            self._merge_directory(child, target, excluded)
                    else:
                        shutil.copy2(child, target)

            return True, "LinuxTV was updated from GitHub. Restart the app to load the new version."
        except Exception as exc:
            logging.exception("Failed to update LinuxTV from GitHub")
            return False, f"Update failed: {exc}"
        finally:
            QApplication.restoreOverrideCursor()

    def _merge_directory(self, source_dir: Path, target_dir: Path, excluded=None):
        excluded = excluded or set()
        target_dir.mkdir(parents=True, exist_ok=True)
        for child in source_dir.iterdir():
            if child.name in excluded:
                continue
            target = target_dir / child.name
            if child.is_dir():
                self._merge_directory(child, target, excluded)
            else:
                shutil.copy2(child, target)

    def add_native_app(self, name: str, cmd: str, notify: bool = True):
        native_apps = self.config.setdefault("native_apps", [])
        native_apps.append({"name": name.strip(), "cmd": cmd.strip(), "icon": ""})

        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save config")
            if notify:
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}"))
            native_apps.pop()
            return

        # Schedule GUI updates on main thread
        QTimer.singleShot(0, lambda: self.populate_tiles())
        QTimer.singleShot(0, lambda: self.focus_entry_tile("native", native_apps[-1]))
        if notify:
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "Added", f"{name.strip()} is now available in Apps."))

    def add_web_app(self, name: str, url: str, notify: bool = True):
        normalized_url = self.normalize_url(url)
        web_apps = self.config.setdefault("web_apps", [])
        web_apps.append({"name": name.strip(), "url": normalized_url, "icon": ""})

        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save config")
            if notify:
                QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}"))
            web_apps.pop()
            return

        # Schedule GUI updates on main thread
        QTimer.singleShot(0, lambda: self.populate_tiles())
        QTimer.singleShot(0, lambda: self.focus_entry_tile("web", web_apps[-1]))
        if notify:
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "Added", f"{name.strip()} is now available in Apps."))

    def keyPressEvent(self, event):
        if not self.tiles:
            return

        key = event.key()
        self.reset_auto_launch_timer()
        if key == Qt.Key_Escape:
            # Show confirmation dialog before closing
            reply = QMessageBox.question(
                self, 
                "Exit LinuxTV",
                "Are you sure you want to exit LinuxTV?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.close()
            return

        if key in (Qt.Key_Right, Qt.Key_Left, Qt.Key_Down, Qt.Key_Up):
            self.navigate({
                Qt.Key_Right: "RIGHT",
                Qt.Key_Left: "LEFT",
                Qt.Key_Down: "DOWN",
                Qt.Key_Up: "UP",
            }[key])
            return

        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activate_current()

    def wheelEvent(self, event):
        """Handle touchpad two-finger scrolling."""
        if not self.tiles:
            return
        
        self.reset_auto_launch_timer()
        
        # Get the angle delta - positive for up/left, negative for down/right
        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()
        
        # Vertical scrolling (up/down)
        if abs(delta_y) > abs(delta_x):
            if delta_y < 0:  # Scroll down
                self.navigate("DOWN")
            elif delta_y > 0:  # Scroll up
                self.navigate("UP")
        # Horizontal scrolling (left/right)
        else:
            if delta_x < 0:  # Scroll right
                self.navigate("RIGHT")
            elif delta_x > 0:  # Scroll left
                self.navigate("LEFT")
        
        event.accept()

    def eventFilter(self, obj, event):
        """Event filter to capture wheel events from child widgets."""
        if event.type() == QEvent.Wheel:
            # Forward wheel event to the window's wheelEvent
            self.wheelEvent(event)
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if hasattr(self, "ws_server") and self.ws_server:
            self.ws_server.stop()
        if hasattr(self, "input_grabber") and self.input_grabber:
            self.input_grabber.stop_grabbing()
        if hasattr(self, "_icon_pool") and self._icon_pool:
            self._icon_pool.shutdown(wait=False, cancel_futures=True)
        event.accept()

    def queue_remote_action(self, action):
        self.remote_action_queue.put(action)

    def queue_remote_event(self, event):
        self.remote_action_queue.put(event)

    def drain_remote_actions(self):
        while True:
            try:
                action = self.remote_action_queue.get_nowait()
            except queue.Empty:
                break
            self.process_remote_event(action)

    def process_remote_event(self, event):
        if isinstance(event, dict):
            event_type = str(event.get("type", "")).lower()
            if event_type == "text":
                self.send_remote_text_to_active_window(str(event.get("text", "")))
                return
            if event_type == "key":
                self.send_remote_special_key_to_active_window(str(event.get("key", "")))
                return
            if event_type == "pointer":
                self.process_remote_pointer_event(event)
                return

        self.process_remote_action(str(event))

    def launcher_window_ids(self):
        window_ids = set()
        for widget in QApplication.topLevelWidgets():
            if not widget.isVisible():
                continue
            try:
                window_ids.add(str(int(widget.winId())))
            except Exception:
                continue
        return window_ids

    def active_system_window(self):
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return None, None
        active_window = run_command([xdotool, "getactivewindow"]).strip()
        return xdotool, active_window or None

    def focus_remote_target_window(self, xdotool, target_window):
        if not xdotool or not target_window:
            return False
        subprocess.run([xdotool, "windowactivate", "--sync", target_window], check=False)
        subprocess.run([xdotool, "windowfocus", "--sync", target_window], check=False)
        return True

    def remember_remote_target_window(self, target_window):
        if not target_window:
            return
        self.remote_target_window_cache = target_window
        self.remote_target_window_cache_at = time.monotonic()

    def clear_remote_target_window_cache(self):
        self.remote_target_window_cache = None
        self.remote_target_window_cache_at = 0.0

    def _focus_launched_app_window(self, pid: int, attempts: int = 10, delay_seconds: float = 1.0):
        """Wait for a launched app window to appear, then activate it."""
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return

        for _ in range(attempts):
            time.sleep(delay_seconds)
            window_ids = find_window_ids_for_pid(pid)
            if not window_ids:
                continue

            target_window = window_ids[0]
            subprocess.run([xdotool, "windowactivate", "--sync", target_window], check=False)
            subprocess.run([xdotool, "windowfocus", "--sync", target_window], check=False)
            logging.info("Focused launched app window %s for pid %s", target_window, pid)
            return

        logging.warning("Could not find window for launched app pid %s after %s attempts", pid, attempts)

    def launcher_context_is_active(self):
        _, active_window = self.active_system_window()
        launcher_ids = self.launcher_window_ids()
        if active_window and active_window in launcher_ids:
            return True
        active_widget = QApplication.activeWindow()
        return bool(active_widget and active_widget.isVisible())

    def dispatch_remote_key_to_launcher(self, key, modifiers=Qt.NoModifier, text=""):
        target = QApplication.focusWidget() or QApplication.activeWindow() or self
        if not target:
            return False

        for event_type in (QEvent.KeyPress, QEvent.KeyRelease):
            event = QKeyEvent(event_type, key, modifiers, text)
            QApplication.sendEvent(target, event)
        return True

    def handle_remote_action_in_launcher(self, action: str):
        modal_widget = QApplication.activeModalWidget()
        if modal_widget:
            modal_widget.activateWindow()
            modal_widget.raise_()

        if not modal_widget and self.isVisible():
            if action in ("UP", "DOWN", "LEFT", "RIGHT"):
                self.navigate(action)
                return True

            if action in ("SELECT", "OK"):
                self.activate_current()
                return True

            if action in ("BACK", "HOME"):
                self.focus_first_tile()
                return True

        qt_action_map = {
            "UP": (Qt.Key_Up, Qt.NoModifier, ""),
            "DOWN": (Qt.Key_Down, Qt.NoModifier, ""),
            "LEFT": (Qt.Key_Left, Qt.NoModifier, ""),
            "RIGHT": (Qt.Key_Right, Qt.NoModifier, ""),
            "SELECT": (Qt.Key_Return, Qt.NoModifier, "\r"),
            "OK": (Qt.Key_Return, Qt.NoModifier, "\r"),
            "BACK": (Qt.Key_Escape, Qt.NoModifier, ""),
            "HOME": (Qt.Key_Home, Qt.NoModifier, ""),
            "TAB": (Qt.Key_Tab, Qt.NoModifier, "\t"),
            "SHIFT_TAB": (Qt.Key_Backtab, Qt.ShiftModifier, "\t"),
            "MENU": (Qt.Key_Menu, Qt.NoModifier, ""),
            "PLAY_PAUSE": (Qt.Key_Space, Qt.NoModifier, " "),
            "INFO": (Qt.Key_I, Qt.NoModifier, "i"),
        }
        key_info = qt_action_map.get(action)
        if not key_info:
            return False
        return self.dispatch_remote_key_to_launcher(*key_info)

    def process_remote_action(self, action: str):
        action = action.upper()
        launcher_active = self.launcher_context_is_active()
        if action in ("UP", "DOWN", "LEFT", "RIGHT") and launcher_active:
            self.pause_auto_launch()
        else:
            self.reset_auto_launch_timer()
        xdotool, target_window = self.remote_target_window()
        if launcher_active and self.handle_remote_action_in_launcher(action):
            return

        if xdotool and target_window and action in (
            "UP",
            "DOWN",
            "LEFT",
            "RIGHT",
            "SELECT",
            "OK",
            "BACK",
            "HOME",
            "TAB",
            "SHIFT_TAB",
            "MENU",
            "PLAY_PAUSE",
            "INFO",
            "PREVIOUS_TRACK",
            "NEXT_TRACK",
            "STOP_MEDIA",
        ):
            self.send_remote_key_to_active_window(action)
            return

        if action in ("PLAY_PAUSE",):
            self.send_remote_key_to_active_window(action)
            return

        if action in ("PREVIOUS_TRACK", "NEXT_TRACK", "STOP_MEDIA"):
            self.send_remote_key_to_active_window(action)
            return

        if action in ("CLOSE", "EXIT", "STOP", "CLOSE_APP") and self.active_process:
            self.close_active_app()
            return

        if action in ("SHUTDOWN", "REBOOT"):
            request_system_power_action(action)
            return

        if action == "UPDATE":
            request_system_update()
            return

        if action in ("VOLUME_UP", "VOLUME_DOWN", "MUTE"):
            control_system_volume(action)
            return

        if action in ("BRIGHTNESS_UP", "BRIGHTNESS_DOWN"):
            control_system_brightness(action)
            return

        if action == "TOGGLE_FULLSCREEN":
            self.toggle_fullscreen()
            return

        if action in ("UP", "DOWN", "LEFT", "RIGHT"):
            self.navigate(action)
            return

        if action in ("SELECT", "OK"):
            self.activate_current()
            return

        if action == "BACK":
            # On launcher this returns to start tile
            self.focus_first_tile()
            return

        if action == "HOME":
            self.focus_first_tile()
            return

        if action in ("CLOSE", "EXIT", "STOP", "CLOSE_APP"):
            self.close()
            return

        logging.warning("Unknown remote action: %s", action)

    def active_app_profile(self):
        name = (self.active_process_name or "").lower()
        if "kodi" in name:
            return "kodi"
        if "stremio" in name:
            return "stremio"
        if self.active_process_kind == "web":
            return "web"
        return "generic"

    def key_sequences_for_action(self, action: str):
        profile = self.active_app_profile()

        common_map = {
            "UP": ["Up"],
            "DOWN": ["Down"],
            "LEFT": ["Left"],
            "RIGHT": ["Right"],
            "SELECT": ["Return"],
            "OK": ["Return"],
            "TAB": ["Tab"],
            "SHIFT_TAB": ["shift+Tab"],
            "PLAY_PAUSE": ["space"],
            "MENU": ["m"],
            "INFO": ["i"],
            "PREVIOUS_TRACK": ["XF86AudioPrev"],
            "NEXT_TRACK": ["XF86AudioNext"],
            "STOP_MEDIA": ["XF86AudioStop"],
        }

        profile_overrides = {
            "web": {
                "BACK": ["Alt+Left", "BackSpace", "Escape"],
                "HOME": ["Alt+Home", "Home"],
                "MENU": ["Alt"],
                "PLAY_PAUSE": ["k", "space"],
            },
            "kodi": {
                "BACK": ["BackSpace", "Escape"],
                "HOME": ["h"],
                "MENU": ["c"],
                "PLAY_PAUSE": ["space"],
                "INFO": ["i"],
            },
            "stremio": {
                "BACK": ["BackSpace", "Escape"],
                "HOME": ["Home"],
                "MENU": ["m"],
                "PLAY_PAUSE": ["space", "k"],
                "INFO": ["i"],
            },
        }

        overrides = profile_overrides.get(profile, {})
        if action in overrides:
            return overrides[action]
        return common_map.get(action, [])

    def remote_target_window(self, focus: bool = True, allow_cached: bool = False):
        xdotool, active_window = self.active_system_window()
        if not xdotool:
            return None, None

        if active_window and active_window not in self.launcher_window_ids():
            self.remember_remote_target_window(active_window)
            return xdotool, active_window

        if allow_cached:
            cache_age = time.monotonic() - self.remote_target_window_cache_at
            if self.remote_target_window_cache and cache_age <= REMOTE_POINTER_TARGET_CACHE_SECONDS:
                return xdotool, self.remote_target_window_cache

        if not self.active_process or self.active_process.poll() is not None:
            self.clear_remote_target_window_cache()
            return xdotool, None

        window_ids = find_window_ids_for_pid(self.active_process.pid)
        if not window_ids:
            return xdotool, None

        target_window = window_ids[0]
        self.remember_remote_target_window(target_window)
        if focus:
            self.focus_remote_target_window(xdotool, target_window)
        return xdotool, target_window

    def send_remote_key_to_active_window(self, action: str):
        key_sequences = self.key_sequences_for_action(action)
        if not key_sequences:
            logging.warning("No key mapping for remote action %s", action)
            return

        xdotool, target_window = self.remote_target_window()
        if not xdotool:
            logging.warning("xdotool is not installed; cannot forward remote action %s", action)
            return
        if not target_window:
            logging.warning("No active target window found for remote action %s", action)
            return

        self.focus_remote_target_window(xdotool, target_window)
        # Only send the first key in the sequence (not all of them)
        key_name = key_sequences[0]
        subprocess.run([xdotool, "key", "--window", target_window, "--clearmodifiers", key_name], check=False)
        logging.info("Forwarded remote action %s to active window (key: %s)", action, key_name)

    def send_remote_text_to_active_window(self, text: str):
        if not text:
            return

        xdotool, target_window = self.remote_target_window()
        if not xdotool:
            logging.warning("xdotool is not installed; cannot type remote text")
            return
        if not target_window:
            logging.warning("No active target window found for remote text")
            return

        self.focus_remote_target_window(xdotool, target_window)
        subprocess.run(
            [xdotool, "type", "--window", target_window, "--delay", "0", text],
            check=False,
        )
        logging.info("Forwarded remote text to active window")

    def send_remote_special_key_to_active_window(self, key: str):
        if not key:
            return

        key_map = {
            "ENTER": "Return",
            "SPACE": "space",
            "BACKSPACE": "BackSpace",
            "ESCAPE": "Escape",
            "TAB": "Tab",
        }
        key_name = key_map.get(key.upper())
        if not key_name:
            logging.warning("Unknown remote special key: %s", key)
            return

        if self.launcher_context_is_active():
            qt_special_key_map = {
                "ENTER": (Qt.Key_Return, Qt.NoModifier, "\r"),
                "SPACE": (Qt.Key_Space, Qt.NoModifier, " "),
                "BACKSPACE": (Qt.Key_Backspace, Qt.NoModifier, "\b"),
                "ESCAPE": (Qt.Key_Escape, Qt.NoModifier, ""),
                "TAB": (Qt.Key_Tab, Qt.NoModifier, "\t"),
            }
            key_info = qt_special_key_map.get(key.upper())
            if key_info and self.dispatch_remote_key_to_launcher(*key_info):
                logging.info("Forwarded remote special key %s to LinuxTV", key)
                return

        xdotool, target_window = self.remote_target_window()
        if not xdotool:
            logging.warning("xdotool is not installed; cannot send remote special key %s", key)
            return
        if not target_window:
            logging.warning("No active target window found for remote special key %s", key)
            return

        self.focus_remote_target_window(xdotool, target_window)
        subprocess.run([xdotool, "key", "--window", target_window, "--clearmodifiers", key_name], check=False)
        logging.info("Forwarded remote special key %s to active window", key)

    def process_remote_pointer_event(self, event):
        event_type = str(event.get("event", "")).lower()
        
        # If launcher is visible (no active app), move mouse relative to current position
        if not self.active_process or self.active_process.poll() is not None:
            xdotool = shutil.which("xdotool")
            if not xdotool:
                logging.warning("xdotool is not installed; cannot forward remote pointer event")
                return
            
            # For launcher screen, just move the mouse relatively
            if event_type == "move":
                dx = int(round(float(event.get("dx", 0)) * REMOTE_POINTER_SPEED_MULTIPLIER))
                dy = int(round(float(event.get("dy", 0)) * REMOTE_POINTER_SPEED_MULTIPLIER))
                if dx or dy:
                    subprocess.run([xdotool, "mousemove_relative", "--", str(dx), str(dy)], check=False)
                return
            
            # For clicks on launcher, use current mouse position
            if event_type in ("tap", "click"):
                subprocess.run([xdotool, "click", "1"], check=False)
                return
            
            if event_type == "right_click":
                subprocess.run([xdotool, "click", "3"], check=False)
                return
            
            if event_type == "scroll":
                dx = int(round(float(event.get("dx", 0))))
                dy = int(round(float(event.get("dy", 0))))
                # Use xdotool to simulate mouse wheel with reduced overhead
                # Negative dy = scroll up (button 4), Positive dy = scroll down (button 5)
                commands = []
                if dy < 0:
                    commands.extend([xdotool, "click", "4"])
                elif dy > 0:
                    commands.extend([xdotool, "click", "5"])
                if dx > 0:
                    commands.extend([xdotool, "click", "6"])
                elif dx < 0:
                    commands.extend([xdotool, "click", "7"])
                
                if commands:
                    subprocess.run(commands, check=False)
                return
        
        # If an app is running, use the existing logic
        allow_cached = event_type == "move"
        focus_target = event_type != "move"
        xdotool, target_window = self.remote_target_window(focus=focus_target, allow_cached=allow_cached)
        if not xdotool:
            logging.warning("xdotool is not installed; cannot forward remote pointer event")
            return
        if not target_window:
            logging.warning("No active target window found for remote pointer event")
            return

        if event_type == "move":
            dx = int(round(float(event.get("dx", 0)) * REMOTE_POINTER_SPEED_MULTIPLIER))
            dy = int(round(float(event.get("dy", 0)) * REMOTE_POINTER_SPEED_MULTIPLIER))
            if dx or dy:
                subprocess.run([xdotool, "mousemove_relative", "--", str(dx), str(dy)], check=False)
            return

        if event_type in ("tap", "click"):
            self.focus_remote_target_window(xdotool, target_window)
            subprocess.run([xdotool, "click", "1"], check=False)
            return

        if event_type == "right_click":
            self.focus_remote_target_window(xdotool, target_window)
            subprocess.run([xdotool, "click", "3"], check=False)
            return

        if event_type == "scroll":
            dx = int(round(float(event.get("dx", 0))))
            dy = int(round(float(event.get("dy", 0))))
            # Focus the target window first
            self.focus_remote_target_window(xdotool, target_window)
            # Use xdotool to simulate mouse wheel with reduced overhead
            # Negative dy = scroll up (button 4), Positive dy = scroll down (button 5)
            commands = []
            if dy < 0:
                commands.extend([xdotool, "click", "4"])
            elif dy > 0:
                commands.extend([xdotool, "click", "5"])
            if dx > 0:
                commands.extend([xdotool, "click", "6"])
            elif dx < 0:
                commands.extend([xdotool, "click", "7"])
            
            if commands:
                subprocess.run(commands, check=False)
            return

    def close_active_app(self):
        # First try to close the tracked active process
        if self.active_process and self.active_process.poll() is None:
            logging.info("Closing tracked active app %s (pid=%s)", self.active_process_name, self.active_process.pid)
            try:
                os.killpg(self.active_process.pid, signal.SIGTERM)
                self.finish_active_process()
                return
            except Exception:
                logging.exception("Failed to terminate active app process group, trying xdotool")
                try:
                    self.active_process.terminate()
                    self.finish_active_process()
                    return
                except Exception:
                    logging.exception("Failed to terminate active app directly, trying xdotool")
        
        # If no tracked process or termination failed, try to close the active window
        logging.info("No tracked process or process already exited, trying to close active window")
        xdotool, active_window = self.active_system_window()
        
        if xdotool and active_window:
            # Don't close the launcher window itself
            if active_window in self.launcher_window_ids():
                logging.info("Active window is the launcher, showing launcher")
                self.show()
                self.raise_()
                self.activateWindow()
                return
            
            logging.info("Closing active window %s using xdotool", active_window)
            try:
                # Try to close the window gracefully
                subprocess.run([xdotool, "windowclose", active_window], check=False, timeout=3)
                
                # Also try sending Alt+F4 as fallback
                time.sleep(0.2)
                subprocess.run([xdotool, "key", "alt+F4"], check=False, timeout=3)
                
                # Clear the tracked process since we're closing via window
                self.finish_active_process()
            except Exception as e:
                logging.exception("Failed to close active window: %s", e)
        else:
            logging.warning("No active process or window found to close")

    def toggle_fullscreen(self):
        """Toggle fullscreen mode for the active window or launcher."""
        launcher_active = self.launcher_context_is_active()
        
        if launcher_active:
            # Toggle fullscreen for the launcher window itself
            if self.isFullScreen():
                logging.info("Exiting fullscreen for launcher")
                self.showNormal()
                self.apply_fullscreen_to_primary_screen()
            else:
                logging.info("Entering fullscreen for launcher")
                self.showFullScreen()
            return
        
        # Try to toggle fullscreen for the active window using xdotool
        xdotool, active_window = self.active_system_window()
        
        if xdotool and active_window:
            # Don't toggle the launcher window
            if active_window in self.launcher_window_ids():
                if self.isFullScreen():
                    self.showNormal()
                    self.apply_fullscreen_to_primary_screen()
                else:
                    self.showFullScreen()
                return
            
            logging.info("Toggling fullscreen for active window %s", active_window)
            try:
                # Check if the active window is a browser
                is_browser = self._is_browser_window(xdotool, active_window)
                
                if is_browser:
                    # Browsers use 'f' key for fullscreen toggle
                    logging.info("Browser detected, sending 'f' key for fullscreen toggle")
                    subprocess.run([xdotool, "key", "f"], check=False, timeout=3)
                else:
                    # Send F11 key to toggle fullscreen (standard for most apps)
                    subprocess.run([xdotool, "key", "F11"], check=False, timeout=3)
            except Exception as e:
                logging.exception("Failed to toggle fullscreen: %s", e)
        else:
            logging.warning("No active window found for fullscreen toggle")
    
    def _is_browser_window(self, xdotool, window_id):
        """Check if the active window is a browser."""
        try:
            # Get window class/name to detect browser
            window_class = subprocess.run(
                [xdotool, "getwindowclassname", window_id],
                capture_output=True,
                text=True,
                check=False,
                timeout=2
            ).stdout.strip().lower()
            
            # Check if it's a browser window
            is_browser = any(browser in window_class for browser in ['brave', 'chrome', 'chromium', 'firefox'])
            
            return is_browser
        except Exception as e:
            logging.debug("Failed to detect browser window: %s", e)
            return False

    def check_active_process(self):
        if not self.active_process:
            return

        if self.active_process.poll() is None:
            return

        logging.info("Active app exited with code %s", self.active_process.returncode)
        self.finish_active_process()

    def finish_active_process(self):
        if self.active_audio_stop_event:
            self.active_audio_stop_event.set()
        if self.active_fullscreen_stop_event:
            self.active_fullscreen_stop_event.set()
        if self.active_audio_thread:
            self.active_audio_thread.join(timeout=1)
        if self.active_fullscreen_thread:
            self.active_fullscreen_thread.join(timeout=1)

        self.active_process = None
        self.active_process_kind = None
        self.active_process_name = None
        self.active_audio_thread = None
        self.active_audio_stop_event = None
        self.active_fullscreen_thread = None
        self.active_fullscreen_stop_event = None
        self.clear_remote_target_window_cache()
        self.process_monitor.stop()

        self.apply_fullscreen_to_primary_screen()
        self.raise_()
        self.activateWindow()
        QApplication.restoreOverrideCursor()
        self.auto_launch_paused = False
        current_tile = self.current_tile()
        if current_tile:
            current_tile.setFocus()
        self.reset_auto_launch_timer()

    def toggle_auto_launch_pause(self):
        self.auto_launch_paused = not self.auto_launch_paused
        if self.auto_launch_paused:
            self.auto_launch_timer.stop()
            self.auto_launch_countdown_timer.stop()
        else:
            self.reset_auto_launch_timer()
            return
        self.update_auto_launch_status()

    def pause_auto_launch(self):
        if self.auto_launch_paused:
            self.update_auto_launch_status()
            return
        self.auto_launch_paused = True
        self.auto_launch_timer.stop()
        self.auto_launch_countdown_timer.stop()
        self.update_auto_launch_status()

    def reset_auto_launch_timer(self):
        if self.active_process or not self.isVisible() or QApplication.activeModalWidget():
            self.auto_launch_timer.stop()
            self.auto_launch_countdown_timer.stop()
            self.update_auto_launch_status()
            return
        auto_launch = self.config.get("auto_launch", {})
        delay_seconds = int(auto_launch.get("delay_seconds", AUTO_LAUNCH_IDLE_MS // 1000) or 0)
        app_kind = str(auto_launch.get("app_kind", "")).strip()
        app_target = str(auto_launch.get("app_target", "")).strip()
        if not app_kind or not app_target or delay_seconds < 1:
            self.auto_launch_timer.stop()
            self.auto_launch_countdown_timer.stop()
            self.update_auto_launch_status()
            return
        selected_entry = self.find_auto_launch_entry()
        if selected_entry is None:
            self.auto_launch_timer.stop()
            self.auto_launch_countdown_timer.stop()
            self.update_auto_launch_status()
            return
        if self.auto_launch_paused:
            self.auto_launch_timer.stop()
            self.auto_launch_countdown_timer.stop()
            self.update_auto_launch_status()
            return
        self.auto_launch_timer.setInterval(delay_seconds * 1000)
        self.auto_launch_timer.start()
        self.auto_launch_countdown_timer.start()
        self.update_auto_launch_status()

    def find_auto_launch_entry(self):
        auto_launch = self.config.get("auto_launch", {})
        target_kind = str(auto_launch.get("app_kind", "")).strip()
        target_value = str(auto_launch.get("app_target", "")).strip()
        if not target_kind or not target_value:
            return None

        for entry in self.get_launchable_entries():
            app = entry["item"]
            value = app.get("cmd", "") if entry["kind"] == "native" else app.get("url", "")
            if entry["kind"] == target_kind and value == target_value:
                return entry
        return None

    def auto_launch_selected_app_if_idle(self):
        self.auto_launch_countdown_timer.stop()
        self.update_auto_launch_status()
        if self.active_process or not self.isVisible() or QApplication.activeModalWidget():
            return

        selected_entry = self.find_auto_launch_entry()
        if selected_entry is None:
            return

        logging.info("Idle timeout reached; auto-launching %s", selected_entry["item"].get("name", "selected app"))
        self.launch_app(selected_entry["item"], selected_entry["kind"])

    def update_auto_launch_status(self):
        if not hasattr(self, "auto_launch_status_label"):
            return

        selected_entry = self.find_auto_launch_entry()
        if selected_entry is None:
            self.auto_launch_status_label.hide()
            self.auto_launch_status_label.setText("")
            self.auto_launch_cancel_button.hide()
            return

        if self.active_process or not self.isVisible() or QApplication.activeModalWidget():
            self.auto_launch_status_label.hide()
            self.auto_launch_status_label.setText("")
            self.auto_launch_cancel_button.hide()
            return

        app_name = selected_entry["item"].get("name", "selected app")
        if self.auto_launch_paused:
            self.auto_launch_status_label.setText(
                f"Auto-open paused for {app_name}. Resume when you're ready."
            )
            self.auto_launch_cancel_button.setText("Resume Auto-Open")
        else:
            remaining_ms = self.auto_launch_timer.remainingTime()
            if remaining_ms is None or remaining_ms < 0 or not self.auto_launch_timer.isActive():
                remaining_seconds = int(self.config.get("auto_launch", {}).get("delay_seconds", AUTO_LAUNCH_IDLE_MS // 1000) or 0)
            else:
                remaining_seconds = max(0, (remaining_ms + 999) // 1000)
            self.auto_launch_status_label.setText(
                f"Auto-open enabled: opening {app_name} in {remaining_seconds} seconds."
            )
            self.auto_launch_cancel_button.setText("Cancel Auto-Open")
        self.auto_launch_status_label.show()
        self.auto_launch_cancel_button.show()

    def update_ip_label(self):
        """Update the IP label with current network info and time."""
        if not hasattr(self, 'ip_label'):
            return
        
        # Get current time (this updates every second)
        current_time = time.strftime("%H:%M:%S")
        
        # Check if we need to refresh WiFi info (only every 30 seconds)
        current_timestamp = time.time()
        if not hasattr(self, '_last_wifi_check'):
            self._last_wifi_check = 0
        
        # Refresh WiFi info if more than 30 seconds have passed
        if current_timestamp - self._last_wifi_check > 30:
            self._cached_ip_address = self.get_ip_address()
            self._cached_wifi_ssid = self.get_wifi_ssid()
            self._cached_is_wifi = self.is_wifi_connection()
            self._last_wifi_check = current_timestamp
        
        # Use cached values
        ip_address = getattr(self, '_cached_ip_address', self.get_ip_address())
        wifi_ssid = getattr(self, '_cached_wifi_ssid', '')
        is_wifi = getattr(self, '_cached_is_wifi', False)
        
        if is_wifi:
            if wifi_ssid:
                ip_text = f"{wifi_ssid} • {ip_address} • {current_time}"
            else:
                ip_text = f"{ip_address} • {current_time}"
        else:
            ip_text = f"{ip_address} • {current_time}"
        
        self.ip_label.setText(ip_text)

    def navigate(self, direction: str):
        if not self.tile_rows:
            return

        if direction == "RIGHT":
            target_row = self.current_row
            # Check if we're at the last column BEFORE moving
            if self.current_col >= len(self.tile_rows[target_row]) - 1:
                # At last column, try to wrap to next row
                if target_row < len(self.tile_rows) - 1:
                    target_row += 1
                    target_col = 0
                else:
                    # No next row, stay at last column
                    target_col = self.current_col
            else:
                # Not at last column, move right normally
                target_col = self.current_col + 1
        elif direction == "LEFT":
            target_row = self.current_row
            # Check if we're at the first column BEFORE moving
            if self.current_col <= 0:
                # At first column, try to wrap to previous row
                if target_row > 0:
                    target_row -= 1
                    target_col = len(self.tile_rows[target_row]) - 1
                else:
                    # No previous row, stay at first column
                    target_col = 0
            else:
                # Not at first column, move left normally
                target_col = self.current_col - 1
        elif direction == "DOWN":
            target_row = min(self.current_row + 1, len(self.tile_rows) - 1)
            target_col = min(self.current_col, len(self.tile_rows[target_row]) - 1)
        elif direction == "UP":
            target_row = max(self.current_row - 1, 0)
            target_col = min(self.current_col, len(self.tile_rows[target_row]) - 1)
        else:
            return

        self.focus_tile_at(target_row, target_col)
        self.ensure_current_tile_visible()
        self.reset_auto_launch_timer()

    def ensure_current_tile_visible(self):
        if not hasattr(self, "tile_scroll") or not self.tile_rows:
            return
        current_tile = self.current_tile()
        if not current_tile or not current_tile.card_shell:
            return

        section = current_tile.section_widget
        outer_scrollbar = self.tile_scroll.verticalScrollBar()
        if section is not None:
            target_y = max(0, section.y() - (self.tile_scroll.viewport().height() // 4))
            vertical_anim = QPropertyAnimation(outer_scrollbar, b"value", self)
            vertical_anim.setDuration(220)
            vertical_anim.setStartValue(outer_scrollbar.value())
            vertical_anim.setEndValue(target_y)
            vertical_anim.setEasingCurve(QEasingCurve.OutCubic)
            vertical_anim.start()
            self._scroll_anim = vertical_anim

        row_scroll = current_tile.row_scroll
        if row_scroll is not None:
            row_scrollbar = row_scroll.horizontalScrollBar()
            card_x = current_tile.card_shell.x()
            target_x = max(0, card_x - (row_scroll.viewport().width() // 5))
            horizontal_anim = QPropertyAnimation(row_scrollbar, b"value", self)
            horizontal_anim.setDuration(180)
            horizontal_anim.setStartValue(row_scrollbar.value())
            horizontal_anim.setEndValue(target_x)
            horizontal_anim.setEasingCurve(QEasingCurve.OutCubic)
            horizontal_anim.start()
            self._row_scroll_anim = horizontal_anim

    def activate_current(self):
        widget = self.current_tile()
        if widget is None:
            return
        self.auto_launch_timer.stop()
        widget.click()

    def launch_app(self, item, kind: str):
        self.auto_launch_timer.stop()
        command = None
        if kind == "native":
            cmd = item.get("cmd")
            if not cmd:
                logging.warning("Native item missing command: %s", item)
                return
            command = split_command(cmd)
            executable_name = Path(command[0]).name.lower() if command else ""
            if executable_name == "vlc":
                command.extend(["--fullscreen", "--video-on-top"])

        if kind == "web":
            if not self.browser_exe:
                logging.error("No browser found for web app launch")
                return
            url = item.get("url")
            if not url:
                logging.warning("Web app missing URL: %s", item)
                return

            if "chromium" in self.browser_exe or "chrome" in self.browser_exe or "brave" in self.browser_exe:
                geometry = self.get_target_geometry()
                command = [
                    self.browser_exe,
                    "--kiosk",
                    "--start-fullscreen",
                    "--app=%s" % url,
                    "--no-first-run",
                    "--disable-translate",
                    "--disable-infobars",
                    "--window-position=%s,%s" % (geometry.x(), geometry.y()),
                    "--window-size=%s,%s" % (geometry.width(), geometry.height()),
                ]
            elif "firefox" in self.browser_exe:
                command = [self.browser_exe, "--kiosk", url]
            else:
                command = [self.browser_exe, url]

        if not command:
            logging.error("Cannot create command for %s item: %s", kind, item)
            return

        logging.info("Launching %s: %s", item.get("name"), command)
        
        # Show loading overlay
        self.show_loading_overlay(f"Launching {item.get('name', 'app')}...")
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # Drop out of the way so the launched app receives focus/input.
            self.hide()
            self.clear_remote_target_window_cache()
            # Respect user's default audio device - don't force HDMI
            geometry = self.get_target_geometry()
            self.active_process = subprocess.Popen(command, start_new_session=True)
            self.active_process_kind = kind
            self.active_process_name = item.get("name")
            # No longer forcing HDMI audio - user's default sink will be used
            if kind == "native":
                self.active_fullscreen_stop_event = threading.Event()
                self.active_fullscreen_thread = threading.Thread(
                    target=enforce_native_fullscreen,
                    args=(command, self.active_process.pid, geometry, self.active_fullscreen_stop_event),
                    daemon=True,
                )
                self.active_fullscreen_thread.start()
            else:
                self.active_fullscreen_stop_event = None
                self.active_fullscreen_thread = None
            self.process_monitor.start()
            threading.Thread(
                target=self._focus_launched_app_window,
                args=(self.active_process.pid,),
                daemon=True,
            ).start()
            
            # Hide loading overlay after a short delay
            QTimer.singleShot(2000, self.hide_loading_overlay)
        except Exception:
            logging.exception("App launch failed")
            self.hide_loading_overlay()
            self.finish_active_process()


def main():
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.info("Starting %s with Qt binding %s", APP_NAME, QT_BINDING)

    config_path = resolve_config_path()
    logging.info("Using config from: %s", config_path)
    
    # Log the actual file location of launcher.py for debugging
    logging.info("Launcher.py location: %s", Path(__file__).resolve())
    logging.info("Icon directory: %s", Path(__file__).parent / "icons")

    app = QApplication(sys.argv)

    window = LauncherWindow(config_path)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
