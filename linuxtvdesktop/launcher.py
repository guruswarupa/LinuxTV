#!/usr/bin/env python3
import base64
import configparser
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path

import remote_auth

from app_config import (
    DEFAULT_CONFIG,
    load_config,
    resolve_config_path,
    save_config,
)
from icons import (
    backdrop_image,
    desktop_file_locations,
    dominant_color,
    fetch_web_icon,
    find_native_icon_source,
    find_web_icon_source,
    normalized_icon_path,
    resolve_native_icon,
    resource_path,
)
from qt_compat import (
    Property,
    QApplication,
    QColor,
    QEvent,
    QFontDatabase,
    QKeyEvent,
    QMainWindow,
    QMessageBox,
    QObject,
    QQuickWidget,
    QT_BINDING,
    QTimer,
    QUrl,
    Qt,
    Signal,
    Slot,
)
from remote_server import InputDeviceGrabber, WebSocketControlServer
from system_controls import (
    control_system_brightness,
    control_system_volume,
    enforce_native_fullscreen,
    find_browser,
    find_window_ids_for_pid,
    get_current_brightness,
    is_installed,
    request_system_power_action,
    request_system_update,
    run_command,
    split_command,
    sync_system_time,
)
from theme import THEME

try:
    import yaml
except ImportError:
    yaml = None

try:
    import websockets
except ImportError:
    websockets = None






APP_NAME = "LinuxTV"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
REMOTE_POINTER_SPEED_MULTIPLIER = 2.5
REMOTE_POINTER_TARGET_CACHE_SECONDS = 1.0


LINE_COUNT = 4
COLUMN_COUNT = 3
AUTO_LAUNCH_IDLE_MS = 10_000
UPDATE_REPO_URL = "https://github.com/guruswarupa/LinuxTV"



def detect_reduced_effects_default() -> bool:
    """Best-effort guess at whether this machine is on a software/weak GPU
    path, for the 'auto' setting of ui.reduced_effects in config.yaml."""
    if os.environ.get("QT_QUICK_BACKEND", "").strip().lower() == "software":
        return True
    if os.environ.get("LIBGL_ALWAYS_SOFTWARE", "").strip().lower() in ("1", "true", "yes"):
        return True
    return False


def resolve_display_font() -> str:
    """Pick the nicest font actually installed, in preference order. Requires
    a QApplication/QGuiApplication to already exist. We ship no font files of
    our own, so this only helps on systems that already have one of these."""
    preferred = ["Inter", "Noto Sans", "Roboto", "DejaVu Sans"]
    try:
        if QT_BINDING == "PyQt5":
            available = set(QFontDatabase().families())
        else:
            available = set(QFontDatabase.families())
    except Exception:
        available = set()
    for name in preferred:
        if name in available:
            return name
    return THEME["font_fallback"]
















































































class IconUpdateBridge(QObject):
    icon_ready = Signal(int, str, str, str, str)


# pyqtProperty needs the string spellings; PySide6's Property is happy with
# the plain Python container types. Picking the right one once here keeps
# HomeBackend's property declarations binding-agnostic.
if QT_BINDING == "PyQt5":
    _VARIANT_LIST_T = "QVariantList"
    _VARIANT_MAP_T = "QVariantMap"
else:
    _VARIANT_LIST_T = list
    _VARIANT_MAP_T = dict


class HomeBackend(QObject):
    """QML-facing view-model for the home screen. Holds no navigation logic
    of its own -- it mirrors LauncherWindow state and forwards QML actions
    back to it, so Python stays the single source of truth."""

    rowsChanged = Signal()
    currentChanged = Signal()
    artChanged = Signal()
    networkTextChanged = Signal()
    autoLaunchChanged = Signal()
    reducedEffectsChanged = Signal()
    searchTextChanged = Signal()
    searchFocusedChanged = Signal()
    menuChanged = Signal()
    settingsOpenChanged = Signal()
    launchStarted = Signal(str, str)
    launchFinished = Signal()
    panelChanged = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._rows = []
        self._current_row = 0
        self._current_col = 0
        self._art = {}
        self._network_text = ""
        self._auto_launch_text = ""
        self._auto_launch_visible = False
        self._auto_launch_paused = False
        self._reduced_effects = False
        self._search_text = ""
        self._search_focused = False
        self._menu_open = False
        self._menu_state = {}
        self._settings_open = False
        self._active_panel = ""
        self._panel_data = {}
        self._panel_busy = False

    def _get_rows(self):
        return self._rows

    def set_rows(self, value):
        self._rows = value
        self.rowsChanged.emit()

    rows = Property(_VARIANT_LIST_T, _get_rows, notify=rowsChanged)

    def _get_current_row(self):
        return self._current_row

    currentRow = Property(int, _get_current_row, notify=currentChanged)

    def _get_current_col(self):
        return self._current_col

    currentCol = Property(int, _get_current_col, notify=currentChanged)

    def set_current(self, row, col):
        if self._current_row == row and self._current_col == col:
            return
        self._current_row = row
        self._current_col = col
        self.currentChanged.emit()

    def _get_art(self):
        return self._art

    art = Property(_VARIANT_MAP_T, _get_art, notify=artChanged)

    def merge_art(self, updates: dict):
        if not updates:
            return
        self._art.update(updates)
        self.artChanged.emit()

    def _get_network_text(self):
        return self._network_text

    def set_network_text(self, value):
        if self._network_text == value:
            return
        self._network_text = value
        self.networkTextChanged.emit()

    networkText = Property(str, _get_network_text, notify=networkTextChanged)

    def _get_auto_launch_text(self):
        return self._auto_launch_text

    autoLaunchText = Property(str, _get_auto_launch_text, notify=autoLaunchChanged)

    def _get_auto_launch_visible(self):
        return self._auto_launch_visible

    autoLaunchVisible = Property(bool, _get_auto_launch_visible, notify=autoLaunchChanged)

    def _get_auto_launch_paused(self):
        return self._auto_launch_paused

    autoLaunchPaused = Property(bool, _get_auto_launch_paused, notify=autoLaunchChanged)

    def set_auto_launch_status(self, text: str, visible: bool, paused: bool):
        self._auto_launch_text = text
        self._auto_launch_visible = visible
        self._auto_launch_paused = paused
        self.autoLaunchChanged.emit()

    def _get_reduced_effects(self):
        return self._reduced_effects

    def set_reduced_effects(self, value: bool):
        if self._reduced_effects == value:
            return
        self._reduced_effects = value
        self.reducedEffectsChanged.emit()

    reducedEffects = Property(bool, _get_reduced_effects, notify=reducedEffectsChanged)

    def _get_search_text(self):
        return self._search_text

    def _set_search_text_prop(self, value):
        if self._search_text == value:
            return
        self._search_text = value
        self.searchTextChanged.emit()

    searchText = Property(str, _get_search_text, _set_search_text_prop, notify=searchTextChanged)

    def _get_search_focused(self):
        return self._search_focused

    def set_search_focused(self, value: bool):
        if self._search_focused == value:
            return
        self._search_focused = value
        self.searchFocusedChanged.emit()

    searchFocused = Property(bool, _get_search_focused, notify=searchFocusedChanged)

    def _get_menu_open(self):
        return self._menu_open

    menuOpen = Property(bool, _get_menu_open, notify=menuChanged)

    def _get_menu_state(self):
        return self._menu_state

    menuState = Property(_VARIANT_MAP_T, _get_menu_state, notify=menuChanged)

    def _get_settings_open(self):
        return self._settings_open

    def set_settings_open(self, value: bool):
        if self._settings_open == value:
            return
        self._settings_open = value
        self.settingsOpenChanged.emit()

    settingsOpen = Property(bool, _get_settings_open, notify=settingsOpenChanged)

    def _get_active_panel(self):
        return self._active_panel

    activePanel = Property(str, _get_active_panel, notify=panelChanged)

    def _get_panel_data(self):
        return self._panel_data

    panelData = Property(_VARIANT_MAP_T, _get_panel_data, notify=panelChanged)

    def _get_panel_busy(self):
        return self._panel_busy

    panelBusy = Property(bool, _get_panel_busy, notify=panelChanged)

    def set_panel(self, name: str, data: dict, busy: bool = False):
        self._active_panel = name
        self._panel_data = data or {}
        self._panel_busy = busy
        self.panelChanged.emit()

    def update_panel_data(self, data: dict, busy: bool = False):
        """Refresh the currently-open panel's data in place (e.g. a Wi-Fi
        scan finishing) without changing which panel is open."""
        self._panel_data = data or {}
        self._panel_busy = busy
        self.panelChanged.emit()

    def set_panel_busy(self, busy: bool):
        if self._panel_busy == busy:
            return
        self._panel_busy = busy
        self.panelChanged.emit()

    # --- Slots QML calls into -------------------------------------------------

    @Slot(str)
    def navigate(self, direction):
        self.window.navigate(direction)

    @Slot(int, int)
    def setCurrent(self, row, col):
        # Mouse clicks/right-clicks on a card call this to sync the click
        # to Python's notion of "current" before acting on it -- route
        # through focus_tile_at (not set_current directly) so it also
        # clears search focus, exactly like a real D-pad move would.
        self.window.focus_tile_at(row, col)

    @Slot()
    def activate(self):
        self.window.activate_current()

    @Slot(str)
    def setSearchText(self, text):
        self.window.set_search_text(text)

    @Slot(bool)
    def setSearchFocused(self, focused):
        self.set_search_focused(focused)
        if not focused and not self._rows:
            return

    @Slot()
    def openMenu(self):
        self.window.open_context_menu()

    @Slot()
    def closeMenu(self):
        self._menu_open = False
        self.menuChanged.emit()

    @Slot(str)
    def menuAction(self, name):
        self.window.run_context_menu_action(name)

    @Slot(str)
    def openSetting(self, name):
        self.window.open_named_setting(name)

    @Slot()
    def toggleSettingsPanel(self):
        self.close_menu()
        self.set_settings_open(not self._settings_open)

    @Slot()
    def closeSettingsPanel(self):
        self.set_settings_open(False)

    @Slot()
    def cancelAutoLaunch(self):
        self.window.toggle_auto_launch_pause()

    @Slot()
    def requestExit(self):
        self.window.confirm_exit()

    @Slot(str, str)
    def reorderDrag(self, source_key, target_key):
        self.window.reorder_by_drag(source_key, target_key)

    @Slot(str, _VARIANT_MAP_T)
    def openPanel(self, name, context):
        self.window.open_app_panel(name, context)

    @Slot()
    def closePanel(self):
        self.window.close_app_panel()

    @Slot(str, _VARIANT_MAP_T)
    def panelAction(self, action, payload):
        self.window.run_panel_action(action, payload)

    # --- Called from Python, not QML ------------------------------------------

    def open_menu_with_state(self, state: dict):
        self._menu_state = state
        self._menu_open = True
        self.menuChanged.emit()

    def close_menu(self):
        if not self._menu_open:
            return
        self._menu_open = False
        self.menuChanged.emit()


class LauncherWindow(QMainWindow):
    # Emitted from a background scan thread, connected to a main-thread
    # handler -- Qt/QML state must only ever be touched from the main
    # thread, so these are how the scan results cross back over safely.
    wifiScanFinished = Signal(object, str, str)
    bluetoothScanFinished = Signal(object, str, str)

    def __init__(self, config_path: Path):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.browser_exe = find_browser()
        self.config_path = config_path

        self.config = load_config(config_path)

        self.home_backend = HomeBackend(self)
        self._panel_context = {}
        self.wifiScanFinished.connect(self._on_wifi_scan_finished)
        self.bluetoothScanFinished.connect(self._on_bluetooth_scan_finished)
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
        self._icon_bridge.icon_ready.connect(self._apply_resolved_art)
        self._pending_art = {}
        self._art_flush_timer = QTimer(self)
        self._art_flush_timer.setInterval(50)
        self._art_flush_timer.setSingleShot(True)
        self._art_flush_timer.timeout.connect(self._flush_pending_art)

        # Search and filtering state
        self.search_filter = ""
        self.is_search_active = False

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
        self.quick_widget = QQuickWidget(self)
        self.quick_widget.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.quick_widget.setClearColor(QColor(THEME["bg"]))

        # Parent the widget into its final place *before* loading QML or
        # setting context properties. setCentralWidget() reparents the
        # widget, which on some windowing systems (Wayland in particular)
        # forces the widget's native window surface to be recreated --  if
        # that happens after the QML has already loaded and bindings have
        # already evaluated against backend/theme, every one of those
        # bindings gets re-evaluated against a context that's momentarily
        # torn down, producing a "Cannot read property of null" for every
        # file in the scene right at startup.
        self.setCentralWidget(self.quick_widget)

        context = self.quick_widget.rootContext()
        context.setContextProperty("backend", self.home_backend)
        context.setContextProperty("theme", THEME)
        qml_path = resource_path("qml/Home.qml")
        self.quick_widget.setSource(QUrl.fromLocalFile(str(qml_path)))
        for error in self.quick_widget.errors():
            logging.error("QML error loading %s: %s", qml_path, error.toString())
        if self.quick_widget.status() == QQuickWidget.Error:
            raise RuntimeError(f"Failed to load {qml_path}; see the log above for QML errors.")

        self.quick_widget.setFocusPolicy(Qt.StrongFocus)
        self.quick_widget.setFocus()
        # Touchpad two-finger scroll should navigate like the D-pad.
        self.quick_widget.installEventFilter(self)

        ip_address = self.get_ip_address()
        wifi_ssid = self.get_wifi_ssid()
        is_wifi = self.is_wifi_connection()
        self._cached_ip_address = ip_address
        self._cached_wifi_ssid = wifi_ssid
        self._cached_is_wifi = is_wifi
        self._last_wifi_check = time.time()
        logging.info("IP: %s, WiFi SSID: '%s', Is WiFi: %s", ip_address, wifi_ssid, is_wifi)

        self.build_home_model()
        self.update_ip_label()
        self.ip_update_timer.start()

        if self.tiles:
            self.focus_first_tile()
        self.reset_auto_launch_timer()

    def clear_tiles(self):
        self.tiles = []
        self.tile_rows = []
        self._pending_art = {}
        self._art_flush_timer.stop()

    CATEGORY_DISPLAY_NAMES = {
        "Native Apps": "Apps",
        "Web Apps": "Streaming",
    }

    def entry_key(self, entry) -> str:
        return f"{entry['kind']}:{self.get_app_id(entry['item'])}"

    def _resolve_banner_path(self, app) -> str:
        banner = app.get("banner", "")
        if not banner:
            return ""
        path = Path(banner).expanduser()
        if not path.is_absolute():
            path = resource_path(banner)
        return str(path) if path.exists() else ""

    def apply_reduced_effects_setting(self):
        mode = str(self.config.get("ui", {}).get("reduced_effects", "auto")).strip().lower()
        if mode == "true":
            reduced = True
        elif mode == "false":
            reduced = False
        else:
            reduced = detect_reduced_effects_default()
        self.home_backend.set_reduced_effects(reduced)

    def toggle_reduced_effects(self):
        ui_config = self.config.setdefault("ui", {})
        currently_reduced = self.home_backend._get_reduced_effects()
        ui_config["reduced_effects"] = "false" if currently_reduced else "true"
        try:
            save_config(self.config_path, self.config)
        except Exception:
            logging.exception("Failed to save ui.reduced_effects setting")
        self.apply_reduced_effects_setting()

    def build_home_model(self):
        self.clear_tiles()
        self.apply_reduced_effects_setting()
        self._icon_request_token += 1
        request_token = self._icon_request_token

        categories = self.get_categorized_entries(self.search_filter)
        rows_data = []
        for category_name, entries in categories:
            title = self.CATEGORY_DISPLAY_NAMES.get(category_name, category_name)
            row_apps = []
            for entry in entries:
                app = entry["item"]
                key = self.entry_key(entry)
                row_apps.append({
                    "key": key,
                    "name": app.get("name", "Untitled"),
                    "subtitle": entry["subtitle"],
                    "kind": entry["kind"],
                    "favorited": entry.get("favorited", False),
                    "isAdd": False,
                    "banner": self._resolve_banner_path(app),
                })
                self.tiles.append(entry)
            rows_data.append({"title": title, "apps": row_apps})
            self.tile_rows.append(entries)

        add_entry_app = {"key": "add:app", "name": "Add App", "subtitle": "Save a command or website", "kind": "add", "favorited": False, "isAdd": True}
        rows_data.append({"title": "Library", "apps": [add_entry_app]})
        # The add-tile is a real, navigable position, so tile_rows needs a
        # matching synthetic entry to keep row/col math consistent. It
        # always gets its own row, mirroring rows_data above.
        add_row_entry = {"kind": "add", "item": None, "subtitle": "", "tooltip": "", "favorited": False}
        self.tile_rows.append([add_row_entry])
        self.tiles.append(add_row_entry)

        self.home_backend.set_rows(rows_data)

        self.clamp_current_position(self.current_row, self.current_col)

        for row_entries in self.tile_rows:
            for entry in row_entries:
                if entry["kind"] == "add":
                    continue
                future = self._icon_pool.submit(self._resolve_art, entry)
                key = self.entry_key(entry)
                future.add_done_callback(
                    lambda pending, token=request_token, key=key: self._queue_art_result(token, key, pending)
                )

        self.reset_auto_launch_timer()

    def _resolve_art(self, entry):
        app = entry["item"]
        if entry["kind"] == "native":
            source, cache_key = find_native_icon_source(app)
        else:
            source, cache_key = find_web_icon_source(app)
        icon_path = normalized_icon_path(source, cache_key) if source else ""
        # Color/backdrop come from the *original* source pixels, not the
        # display-normalized icon -- normalization can repaint a near-black
        # mark white for visibility, and extracting color from that would
        # just recreate the same invisible-icon problem as white-on-white.
        color = dominant_color(source) if source else THEME["surface_alt"]
        backdrop = backdrop_image(source) if source else ""
        return icon_path or "", color, backdrop

    def _queue_art_result(self, request_token: int, key: str, future):
        try:
            icon_path, color, backdrop = future.result()
        except Exception:
            logging.exception("Failed to resolve art for %s", key)
            icon_path, color, backdrop = "", THEME["surface_alt"], ""
        self._icon_bridge.icon_ready.emit(request_token, key, icon_path, color, backdrop)

    def _apply_resolved_art(self, request_token: int, key: str, icon_path: str, color: str, backdrop: str):
        if request_token != self._icon_request_token:
            return
        self._pending_art[key] = {"icon": icon_path, "color": color, "backdrop": backdrop}
        if not self._art_flush_timer.isActive():
            self._art_flush_timer.start()

    def _flush_pending_art(self):
        if not self._pending_art:
            return
        pending, self._pending_art = self._pending_art, {}
        self.home_backend.merge_art(pending)

    def set_search_text(self, text: str):
        self.search_filter = text
        self.is_search_active = bool(text.strip())
        self.home_backend._set_search_text_prop(text)
        self.build_home_model()

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
                    "category": self.CATEGORY_DISPLAY_NAMES.get(category_name, category_name)
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
                QTimer.singleShot(0, self.build_home_model)
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
                QTimer.singleShot(0, self.build_home_model)
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
                    QTimer.singleShot(0, self.build_home_model)
                    return True, f"{app_name} added to launcher"
                else:
                    return False, f"Could not find {app_name} on your system"
            
            # For web apps, they should already be in config
            return False, "Web apps must be added through settings"
            
        except Exception as e:
            logging.exception("Failed to add app")
            return False, f"Error adding app: {str(e)}"

    def get_launchable_entries(self):
        entries = []
        for _, category_entries in self.get_categorized_entries():
            entries.extend(category_entries)
        return entries

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

    def clamp_current_position(self, row: int, col: int):
        """Update current_row/col and the backend's selection without
        touching search focus -- used when rebuilding the model (e.g. on
        every keystroke while typing a search query), where stealing focus
        away from the search box would be a bug, not a navigation."""
        if not self.tile_rows:
            return
        row = max(0, min(row, len(self.tile_rows) - 1))
        col = max(0, min(col, len(self.tile_rows[row]) - 1))
        self.current_row = row
        self.current_col = col
        self.current_index = self._flat_index_for_position(row, col)
        self.home_backend.set_current(row, col)

    def focus_tile_at(self, row: int, col: int):
        self.clamp_current_position(row, col)
        if self.tile_rows:
            self.home_backend.set_search_focused(False)

    def focus_first_tile(self):
        if not self.tile_rows:
            return
        self.focus_tile_at(0, 0)

    def focus_entry_tile(self, kind: str, item):
        for row_idx, row_tiles in enumerate(self.tile_rows):
            for col_idx, entry in enumerate(row_tiles):
                if entry["kind"] == kind and entry["item"] is item:
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
        self.open_app_panel("addApp", {})

    def prompt_edit_entry(self, kind: str, app):
        self.open_app_panel("editApp", {"key": self.entry_key({"kind": kind, "item": app})})

    def prompt_delete_entry(self, kind: str, app):
        self.open_app_panel("confirmDelete", {"key": self.entry_key({"kind": kind, "item": app})})

    # --- In-scene panel system --------------------------------------------
    # Every "popup window" that used to be a separate QDialog (Add/Edit App,
    # Delete confirmation, Network, Bluetooth, Sound, Brightness, Remote
    # Login, Auto-Open) now renders as a QML overlay driven by
    # HomeBackend.activePanel/panelData instead, matching the Settings
    # panel. Python still owns all the actual logic (Wi-Fi/Bluetooth
    # scanning, config writes, ...); the panel is just a view over it.

    def open_app_panel(self, name: str, context: dict):
        context = context or {}
        self.home_backend.set_settings_open(False)
        self.home_backend.close_menu()
        self._panel_context = context

        if name == "addApp":
            self.home_backend.set_panel(name, {
                "mode": "add", "title": "Add App", "type": "Application",
                "name": "", "value": "", "allowTypeChange": True, "status": "",
            })

        elif name == "editApp":
            entry = self._find_entry_by_key(context.get("key", ""))
            if not entry:
                return
            app = entry["item"]
            kind = entry["kind"]
            self.home_backend.set_panel(name, {
                "mode": "edit", "title": "Edit App",
                "type": "Application" if kind == "native" else "Website",
                "name": app.get("name", ""),
                "value": app.get("cmd", "") if kind == "native" else app.get("url", ""),
                "allowTypeChange": False, "status": "",
            })

        elif name == "confirmDelete":
            entry = self._find_entry_by_key(context.get("key", ""))
            if not entry:
                return
            self.home_backend.set_panel(name, {"appName": entry["item"].get("name", "this app")})

        elif name == "network":
            self.home_backend.set_panel(name, {"networks": [], "currentWifi": "", "status": "Loading Wi-Fi networks..."}, busy=True)
            self._start_wifi_scan()

        elif name == "bluetooth":
            self.home_backend.set_panel(name, {"devices": [], "currentBluetooth": "", "status": "Loading Bluetooth devices..."}, busy=True)
            self._start_bluetooth_scan()

        elif name == "sound":
            self.home_backend.set_panel(name, {"sinks": [], "currentSink": "", "status": "Loading audio devices..."})
            self._refresh_sound_panel()

        elif name == "brightness":
            self.home_backend.set_panel(name, {"value": int(get_current_brightness() * 100), "status": ""})

        elif name == "remoteLogin":
            auth = self.config.get("auth", {})
            self.home_backend.set_panel(name, {
                "username": auth.get("username", ""),
                "pairingCode": self.ws_server.pairing_code,
                "status": "",
            })

        elif name == "autoOpen":
            auto_launch = self.config.get("auto_launch", {})
            self.home_backend.set_panel(name, {
                "options": self.get_auto_launch_options(),
                "selectedKind": str(auto_launch.get("app_kind", "")),
                "selectedTarget": str(auto_launch.get("app_target", "")),
                "delaySeconds": str(auto_launch.get("delay_seconds", AUTO_LAUNCH_IDLE_MS // 1000)),
                "status": "",
            })

    def close_app_panel(self):
        self.home_backend.set_panel("", {})
        self._panel_context = {}
        if hasattr(self, "quick_widget"):
            self.quick_widget.setFocus()

    def _panel_status(self, message: str, **extra):
        """Patch the open panel's data with a new status message (and any
        other field updates), keeping the rest of what's already there."""
        data = dict(self.home_backend.panelData)
        data["status"] = message
        data.update(extra)
        self.home_backend.update_panel_data(data)

    def run_panel_action(self, action: str, payload: dict):
        payload = payload or {}
        panel = self.home_backend.activePanel

        if action == "cancel":
            self.close_app_panel()
            return

        if panel in ("addApp", "editApp") and action == "save":
            self._save_app_panel(payload)
        elif panel == "confirmDelete" and action == "confirm":
            self._confirm_delete_panel()
        elif panel == "network":
            if action == "refresh":
                self._start_wifi_scan(force=True)
            elif action == "connect":
                self._wifi_connect_action(payload)
            elif action == "forget":
                self._wifi_forget_action(payload)
        elif panel == "bluetooth":
            if action == "refresh":
                self._start_bluetooth_scan(force=True)
            elif action == "connect":
                self._bluetooth_connect_action(payload)
            elif action == "remove":
                self._bluetooth_remove_action(payload)
        elif panel == "sound":
            if action == "refresh":
                self._refresh_sound_panel()
            elif action == "setDefault":
                self._sound_set_default_action(payload)
        elif panel == "brightness" and action == "set":
            self._brightness_set_action(payload)
        elif panel == "remoteLogin" and action == "save":
            self._remote_login_save_action(payload)
        elif panel == "autoOpen" and action == "save":
            self._auto_open_save_action(payload)

    def _save_app_panel(self, payload):
        entry_type = payload.get("type", "Application")
        name = str(payload.get("name", "")).strip()
        value = str(payload.get("value", "")).strip()
        if not name or not value:
            self._panel_status("Enter both a name and a command or URL.")
            return

        mode = self.home_backend.panelData.get("mode", "add")
        if mode == "edit":
            entry = self._find_entry_by_key(self._panel_context.get("key", ""))
            if not entry:
                self.close_app_panel()
                return
            app = entry["item"]
            kind = entry["kind"]
            app["name"] = name
            if kind == "native":
                app["cmd"] = value
            else:
                app["url"] = self.normalize_url(value)
            try:
                save_config(self.config_path, self.config)
            except Exception as exc:
                logging.exception("Failed to save config")
                self._panel_status(f"Could not save: {exc}")
                return
            self.close_app_panel()
            self.build_home_model()
            self.focus_entry_tile(kind, app)
            return

        if entry_type == "Application":
            self.add_native_app(name, value, notify=False)
        else:
            self.add_web_app(name, value, notify=False)
        self.close_app_panel()

    def _confirm_delete_panel(self):
        entry = self._find_entry_by_key(self._panel_context.get("key", ""))
        self.close_app_panel()
        if not entry:
            return
        kind = entry["kind"]
        app = entry["item"]
        collection_name = "native_apps" if kind == "native" else "web_apps"
        collection = self.config.get(collection_name, [])
        try:
            collection.remove(app)
        except ValueError:
            return
        try:
            save_config(self.config_path, self.config)
        except Exception:
            logging.exception("Failed to save config")
            collection.append(app)
            return
        self.build_home_model()
        if self.tiles:
            self.focus_first_tile()

    def _start_wifi_scan(self, force=False):
        threading.Thread(target=self._run_wifi_scan_thread, name="wifi-panel-scan", daemon=True).start()

    def _run_wifi_scan_thread(self):
        try:
            networks, current_wifi, message = self.scan_wifi_networks()
        except Exception as exc:
            logging.exception("Failed to scan Wi-Fi networks")
            networks, current_wifi, message = [], "", f"Could not scan for Wi-Fi networks: {exc}"
        self.wifiScanFinished.emit(networks, current_wifi, message)

    def _on_wifi_scan_finished(self, networks, current_wifi, message):
        if self.home_backend.activePanel != "network":
            return
        self.home_backend.update_panel_data({
            "networks": networks or [],
            "currentWifi": current_wifi or "",
            "status": message or "",
        })

    def _wifi_connect_action(self, payload):
        if self.home_backend.panelBusy:
            self._panel_status("Still fetching nearby Wi-Fi networks. Try again in a moment.")
            return
        network_info = {"ssid": payload.get("ssid", ""), "security": payload.get("security", "")}
        success, message, current_wifi = self.connect_to_wifi(network_info, payload.get("password", ""))
        self._panel_status(message)
        if current_wifi:
            self._start_wifi_scan(force=True)

    def _wifi_forget_action(self, payload):
        if self.home_backend.panelBusy:
            self._panel_status("Still fetching nearby Wi-Fi networks. Try again in a moment.")
            return
        ssid = str(payload.get("ssid", "")).strip()
        if not ssid:
            self._panel_status("Select a network to forget.")
            return
        success, message = self.disconnect_from_wifi({"ssid": ssid})
        self._panel_status(message)
        if success:
            self._start_wifi_scan(force=True)

    def _start_bluetooth_scan(self, force=False):
        threading.Thread(target=self._run_bluetooth_scan_thread, name="bluetooth-panel-scan", daemon=True).start()

    def _run_bluetooth_scan_thread(self):
        try:
            devices, current_bluetooth, message = self.scan_bluetooth_devices()
        except Exception as exc:
            logging.exception("Failed to scan Bluetooth devices")
            devices, current_bluetooth, message = [], "", f"Could not scan for Bluetooth devices: {exc}"
        self.bluetoothScanFinished.emit(devices, current_bluetooth, message)

    def _on_bluetooth_scan_finished(self, devices, current_bluetooth, message):
        if self.home_backend.activePanel != "bluetooth":
            return
        self.home_backend.update_panel_data({
            "devices": devices or [],
            "currentBluetooth": current_bluetooth or "",
            "status": message or "",
        })

    def _bluetooth_connect_action(self, payload):
        if self.home_backend.panelBusy:
            self._panel_status("Still fetching nearby Bluetooth devices. Try again in a moment.")
            return
        mac = str(payload.get("mac", "")).strip()
        if not mac:
            self._panel_status("Select a device to connect.")
            return
        success, message, current_bt = self.connect_to_bluetooth({"mac": mac})
        self._panel_status(message)
        if current_bt:
            self._start_bluetooth_scan(force=True)

    def _bluetooth_remove_action(self, payload):
        if self.home_backend.panelBusy:
            self._panel_status("Still fetching nearby Bluetooth devices. Try again in a moment.")
            return
        mac = str(payload.get("mac", "")).strip()
        if not mac:
            self._panel_status("Select a device to remove.")
            return
        success, message = self.remove_bluetooth_device({"mac": mac})
        self._panel_status(message)
        if success:
            self._start_bluetooth_scan(force=True)

    def _refresh_sound_panel(self):
        sinks = self.get_audio_sinks()
        current_sink = self.get_default_audio_sink()
        message = f"Found {len(sinks)} audio output device(s)." if sinks else "No audio output devices found."
        self.home_backend.update_panel_data({"sinks": sinks, "currentSink": current_sink, "status": message})

    def _sound_set_default_action(self, payload):
        sink = str(payload.get("sink", "")).strip()
        if not sink:
            self._panel_status("Select an audio output device first.")
            return
        success, message = self.set_default_sink_and_move_streams(sink)
        extra = {"currentSink": sink} if success else {}
        self._panel_status(message, **extra)

    def _brightness_set_action(self, payload):
        try:
            value = int(payload.get("value", 0))
        except (TypeError, ValueError):
            return
        success = control_system_brightness("SET_BRIGHTNESS", value)
        message = f"Brightness set to {value}%" if success else "Failed to adjust brightness. Try installing brightnessctl."
        self._panel_status(message, value=value)

    def _remote_login_save_action(self, payload):
        username = str(payload.get("username", "")).strip()
        password = payload.get("password", "")
        confirm_password = payload.get("confirmPassword", "")

        if username or password or confirm_password:
            if not username:
                self._panel_status("Username is required.")
                return
            if password != confirm_password:
                self._panel_status("Passwords do not match.")
                return
            if len(password) < 8:
                self._panel_status("Password must be at least 8 characters.")
                return

        if username and password:
            self.config["auth"] = remote_auth.new_credentials(username, password)
        else:
            self.config["auth"] = dict(DEFAULT_CONFIG["auth"])
        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save remote login settings")
            self._panel_status(f"Could not save: {exc}")
            return
        self.close_app_panel()

    def _auto_open_save_action(self, payload):
        kind = payload.get("kind", "")
        target = payload.get("target", "")
        try:
            delay_seconds = int(str(payload.get("delaySeconds", "")).strip())
        except ValueError:
            self._panel_status("Enter a whole number of seconds for auto open.")
            return
        if delay_seconds < 1:
            self._panel_status("Auto open delay must be at least 1 second.")
            return

        self.config["auto_launch"] = {
            "app_kind": kind,
            "app_target": target,
            "delay_seconds": delay_seconds,
        }
        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save settings")
            self._panel_status(f"Could not save: {exc}")
            return
        self.reset_auto_launch_timer()
        self.close_app_panel()

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
        QTimer.singleShot(0, self.build_home_model)
    
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
        QTimer.singleShot(0, self.build_home_model)

    def open_remote_settings(self):
        self.open_app_panel("remoteLogin", {})

    def open_settings(self):
        self.open_app_panel("autoOpen", {})

    def open_network_settings(self):
        self.open_app_panel("network", {})

    def open_bluetooth_settings(self):
        self.open_app_panel("bluetooth", {})

    def open_sound_settings(self):
        self.open_app_panel("sound", {})

    def open_brightness_settings(self):
        self.open_app_panel("brightness", {})

    def set_default_sink_and_move_streams(self, sink_name: str):
        """Panel-facing wrapper around the existing set_default_audio_sink
        (also used by the WebSocket remote, which expects its plain bool
        return -- this doesn't change that) that additionally moves
        already-playing streams onto the new sink and reports a message
        for the panel's status line. Returns (success, message)."""
        if not self.set_default_audio_sink(sink_name):
            return False, "Failed to set default audio device."
        moved = self._move_sink_inputs(sink_name)
        if moved > 0:
            return True, f"Set as default and moved {moved} audio stream(s)."
        return True, "Set as default. New audio will play through this device."

    def _move_sink_inputs(self, sink_name: str) -> int:
        pactl = shutil.which("pactl")
        if not pactl:
            return 0
        moved_count = 0
        try:
            result = subprocess.run(
                [pactl, "list", "short", "sink-inputs"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    move_result = subprocess.run(
                        [pactl, "move-sink-input", parts[0], sink_name],
                        capture_output=True, text=True, check=False, timeout=5,
                    )
                    if move_result.returncode == 0:
                        moved_count += 1
        except Exception:
            logging.exception("Failed to move sink inputs")
        return moved_count

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
                return True, (
                    "LinuxTV was updated from GitHub. Restart the app to load the new version. "
                    "If it fails to start after restarting, re-run setup.sh -- an update can "
                    "bring in new system package requirements that a code pull alone won't install."
                )

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

            return True, (
                "LinuxTV was updated from GitHub. Restart the app to load the new version. "
                "If it fails to start after restarting, re-run setup.sh -- an update can bring "
                "in new system package requirements that a code pull alone won't install."
            )
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
                QTimer.singleShot(0, lambda msg=str(exc): QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{msg}"))
            native_apps.pop()
            return

        # Schedule GUI updates on main thread
        QTimer.singleShot(0, lambda: self.build_home_model())
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
                QTimer.singleShot(0, lambda msg=str(exc): QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{msg}"))
            web_apps.pop()
            return

        # Schedule GUI updates on main thread
        QTimer.singleShot(0, lambda: self.build_home_model())
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
                self.send_remote_special_key_to_active_window(
                    str(event.get("key", "")), event.get("modifiers")
                )
                return
            if event_type == "pointer":
                self.process_remote_pointer_event(event)
                return
            if event_type == "add_app":
                kind = str(event.get("kind", ""))
                name = str(event.get("name", ""))
                if kind == "native":
                    self.add_native_app(name, str(event.get("command", "")), notify=False)
                elif kind == "web":
                    self.add_web_app(name, str(event.get("url", "")), notify=False)
                return
            if event_type == "remove_app":
                self.remove_app_by_id(str(event.get("id", "")))
                return
            if event_type == "reorder_app":
                self.reorder_app(
                    {"id": str(event.get("id", ""))},
                    str(event.get("kind", "")),
                    str(event.get("direction", "")),
                    0,
                    [],
                )
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

            if action == "MENU":
                self.open_context_menu()
                return True

            if action in ("BACK", "HOME"):
                if self.home_backend.menuOpen:
                    self.home_backend.close_menu()
                elif self.home_backend.settingsOpen:
                    self.home_backend.set_settings_open(False)
                else:
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

    def send_remote_special_key_to_active_window(self, key: str, modifiers: list = None):
        if not key:
            return

        key_map = {
            "ENTER": "Return",
            "SPACE": "space",
            "BACKSPACE": "BackSpace",
            "ESCAPE": "Escape",
            "TAB": "Tab",
            "DELETE": "Delete",
            "END": "End",
            "PAGE_UP": "Prior",
            "PAGE_DOWN": "Next",
            "F5": "F5",
            "A": "a",
            "C": "c",
            "V": "v",
            "X": "x",
            "Z": "z",
        }
        key_name = key_map.get(key.upper())
        if not key_name:
            logging.warning("Unknown remote special key: %s", key)
            return

        modifiers = [str(m).lower() for m in (modifiers or []) if str(m).lower() in ("ctrl", "alt", "shift")]

        if not modifiers and self.launcher_context_is_active():
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
        if modifiers:
            combo = "+".join(modifiers + [key_name])
            subprocess.run([xdotool, "key", "--window", target_window, combo], check=False)
            logging.info("Forwarded remote key combo %s to active window", combo)
        else:
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

            # Ask the window manager to toggle the EWMH fullscreen hint
            # directly instead of guessing an app-specific keybinding.
            # Sending 'f' only fullscreens a YouTube-style <video> player
            # via its own page JS -- it does nothing (or types "f" into
            # whatever has focus) on Netflix, Prime Video, and most other
            # sites, and plenty of native apps don't bind F11 either. A
            # wmctrl fullscreen toggle works uniformly for any window --
            # native app or browser -- and browsers already react to their
            # own window going fullscreen by hiding their chrome, the same
            # as if F11 had been pressed inside them.
            wmctrl = shutil.which("wmctrl")
            if wmctrl:
                try:
                    subprocess.run(
                        [wmctrl, "-i", "-r", active_window, "-b", "toggle,fullscreen"],
                        check=False,
                        timeout=3,
                    )
                    return
                except Exception as e:
                    logging.exception("Failed to toggle fullscreen via wmctrl: %s", e)

            # Fall back to F11 if wmctrl is unavailable for some reason.
            try:
                subprocess.run([xdotool, "key", "F11"], check=False, timeout=3)
            except Exception as e:
                logging.exception("Failed to toggle fullscreen: %s", e)
        else:
            logging.warning("No active window found for fullscreen toggle")

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
        self.home_backend.launchFinished.emit()
        if hasattr(self, "quick_widget"):
            self.quick_widget.setFocus()
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
        if not hasattr(self, "home_backend"):
            return

        selected_entry = self.find_auto_launch_entry()
        if selected_entry is None or self.active_process or not self.isVisible() or QApplication.activeModalWidget():
            self.home_backend.set_auto_launch_status("", False, False)
            return

        app_name = selected_entry["item"].get("name", "selected app")
        if self.auto_launch_paused:
            text = f"Auto-open paused for {app_name}. Resume when you're ready."
        else:
            remaining_ms = self.auto_launch_timer.remainingTime()
            if remaining_ms is None or remaining_ms < 0 or not self.auto_launch_timer.isActive():
                remaining_seconds = int(self.config.get("auto_launch", {}).get("delay_seconds", AUTO_LAUNCH_IDLE_MS // 1000) or 0)
            else:
                remaining_seconds = max(0, (remaining_ms + 999) // 1000)
            text = f"Opening {app_name} in {remaining_seconds}s"
        self.home_backend.set_auto_launch_status(text, True, self.auto_launch_paused)

    def update_ip_label(self):
        """Update the network status text shown in the QML top bar."""
        if not hasattr(self, "home_backend"):
            return

        current_timestamp = time.time()
        if not hasattr(self, "_last_wifi_check"):
            self._last_wifi_check = 0

        # Refresh WiFi info if more than 30 seconds have passed
        if current_timestamp - self._last_wifi_check > 30:
            self._cached_ip_address = self.get_ip_address()
            self._cached_wifi_ssid = self.get_wifi_ssid()
            self._cached_is_wifi = self.is_wifi_connection()
            self._last_wifi_check = current_timestamp

        ip_address = getattr(self, "_cached_ip_address", self.get_ip_address())
        wifi_ssid = getattr(self, "_cached_wifi_ssid", "")
        is_wifi = getattr(self, "_cached_is_wifi", False)

        if is_wifi and wifi_ssid:
            network_text = f"{wifi_ssid} • {ip_address}"
        else:
            network_text = ip_address

        self.home_backend.set_network_text(network_text)

    def navigate(self, direction: str):
        if not self.tile_rows:
            return

        if direction == "UP" and self.current_row == 0:
            self.home_backend.set_search_focused(True)
            self.reset_auto_launch_timer()
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
        self.reset_auto_launch_timer()

    def activate_current(self):
        entry = self.current_tile()
        if entry is None:
            return
        self.auto_launch_timer.stop()
        if entry["kind"] == "add":
            self.prompt_add_entry()
            return
        self.launch_app(entry["item"], entry["kind"])

    def _collection_for_kind(self, kind: str):
        return self.config.get("native_apps" if kind == "native" else "web_apps", [])

    def _can_move_entry(self, app, kind: str, direction: str) -> bool:
        collection = self._collection_for_kind(kind)
        app_id = self.get_app_id(app)
        index = next((i for i, item in enumerate(collection) if self.get_app_id(item) == app_id), -1)
        if index == -1:
            return False
        return index > 0 if direction == "left" else index < len(collection) - 1

    def _find_entry_by_key(self, key: str):
        for row_entries in self.tile_rows:
            for entry in row_entries:
                if entry["kind"] != "add" and self.entry_key(entry) == key:
                    return entry
        return None

    def reorder_by_drag(self, source_key: str, target_key: str):
        """Move a dragged card to sit at another card's position, within the
        same collection (native_apps or web_apps) -- the mouse-drag
        counterpart to the context menu's Move Left/Right."""
        if source_key == target_key:
            return
        source_entry = self._find_entry_by_key(source_key)
        target_entry = self._find_entry_by_key(target_key)
        if not source_entry or not target_entry:
            return
        if source_entry["kind"] != target_entry["kind"]:
            return
        kind = source_entry["kind"]
        collection = self._collection_for_kind(kind)
        source_app = source_entry["item"]
        source_id = self.get_app_id(source_app)
        target_id = self.get_app_id(target_entry["item"])
        source_index = next((i for i, item in enumerate(collection) if self.get_app_id(item) == source_id), -1)
        target_index = next((i for i, item in enumerate(collection) if self.get_app_id(item) == target_id), -1)
        if source_index == -1 or target_index == -1 or source_index == target_index:
            return

        moved_item = collection.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        collection.insert(target_index, moved_item)

        try:
            save_config(self.config_path, self.config)
        except Exception as exc:
            logging.exception("Failed to save reordered config")
            QMessageBox.critical(self, "Save Failed", f"Could not save config:\n{exc}")
            return

        self.build_home_model()
        self.focus_entry_tile(kind, source_app)

    def open_context_menu(self):
        entry = self.current_tile()
        if entry is None or entry["kind"] == "add":
            return
        app = entry["item"]
        kind = entry["kind"]
        self.home_backend.set_settings_open(False)
        self.home_backend.open_menu_with_state({
            "key": self.entry_key(entry),
            "name": app.get("name", ""),
            "favorited": self.is_app_favorited(app, kind),
            "canMoveLeft": self._can_move_entry(app, kind, "left"),
            "canMoveRight": self._can_move_entry(app, kind, "right"),
        })

    def run_context_menu_action(self, name: str):
        entry = self.current_tile()
        self.home_backend.close_menu()
        if entry is None or entry["kind"] == "add":
            return
        app = entry["item"]
        kind = entry["kind"]
        if name == "favorite":
            self.toggle_favorite(app, kind)
        elif name == "moveLeft":
            self.reorder_app(app, kind, "left", 0, [])
        elif name == "moveRight":
            self.reorder_app(app, kind, "right", 0, [])
        elif name == "edit":
            self.prompt_edit_entry(kind, app)
        elif name == "remove":
            self.prompt_delete_entry(kind, app)

    def open_named_setting(self, name: str):
        if name == "reducedfx":
            self.toggle_reduced_effects()
            return

        handlers = {
            "network": self.open_network_settings,
            "bluetooth": self.open_bluetooth_settings,
            "sound": self.open_sound_settings,
            "brightness": self.open_brightness_settings,
            "remote": self.open_remote_settings,
            "autoopen": self.open_settings,
            "systemupdate": self.update_system,
            "appupdate": self.update_app,
            "restart": self.restart_system,
            "shutdown": self.shutdown_system,
        }
        handler = handlers.get(name)
        if handler:
            self.home_backend.set_settings_open(False)
            handler()

    def confirm_exit(self):
        reply = QMessageBox.question(
            self,
            "Exit LinuxTV",
            "Are you sure you want to exit LinuxTV?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.close()

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

        key = f"{kind}:{self.get_app_id(item)}"
        art = self.home_backend.art.get(key, {})
        self.home_backend.launchStarted.emit(item.get("name", "App"), art.get("color", THEME["surface_alt"]))

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
            
            # Hide the launch splash after a short delay
            QTimer.singleShot(1200, self.home_backend.launchFinished.emit)
        except Exception:
            logging.exception("App launch failed")
            self.home_backend.launchFinished.emit()
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
    THEME["font_family"] = resolve_display_font()

    window = LauncherWindow(config_path)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
