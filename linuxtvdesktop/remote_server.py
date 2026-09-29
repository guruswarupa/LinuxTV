"""Phone-remote WebSocket server and global input-device grabber."""

import asyncio
import json
import logging
import threading

try:
    import websockets
except ImportError:
    websockets = None

import remote_auth
from app_config import save_config
from system_controls import (
    control_system_brightness,
    control_system_volume,
    get_current_brightness,
    get_current_volume,
)


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
            import evdev  # noqa: F401  (availability probe; ecodes below needs it)
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
        self.gate = remote_auth.AuthGate(window.config, self._persist_config)
        logging.warning("Remote pairing code: %s (needed only until a phone is paired)", self.gate.pairing_code)

    @property
    def pairing_code(self):
        return self.gate.pairing_code

    def _persist_config(self):
        try:
            save_config(self.window.config_path, self.window.config)
        except Exception:
            logging.exception("Failed to persist remote auth config")

    async def _send_apps_list(self, websocket):
        await websocket.send(json.dumps({
            "status": "ok",
            "type": "apps_list",
            "apps": self.window.get_installed_apps(),
        }))

    async def handler(self, websocket, path=None):
        remote = websocket.remote_address
        client = remote[0] if remote else "unknown"
        logging.info("WebSocket connection from %s", remote)
        authenticated = False

        try:
            async for message in websocket:
                try:
                    payload = json.loads(message)
                    message_type = str(payload.get("type", "")).lower()
                except Exception:
                    payload = {}
                    message_type = ""
                if message_type not in ("auth", "auth_token", "pair"):
                    logging.info("Received remote action: %s", message_type or "<invalid>")

                if message_type in remote_auth.AUTH_MESSAGE_TYPES:
                    reply, granted = self.gate.handle(client, message_type, payload)
                    await websocket.send(json.dumps(reply))
                    if granted:
                        authenticated = True
                        await self._send_apps_list(websocket)
                    continue

                if not authenticated:
                    await websocket.send(json.dumps(self.gate.required()))
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
                        modifiers = payload.get("modifiers") or []
                        self.window.queue_remote_event({"type": "key", "key": key, "modifiers": modifiers})
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

                        # Actually mutating config/QML state has to happen on the
                        # Qt main thread; this handler runs on the asyncio thread,
                        # where a bare QTimer.singleShot never fires (no Qt event
                        # loop pumping it there). Go through the same thread-safe
                        # queue the D-pad/text/pointer remote events already use.
                        self.window.queue_remote_event({
                            "type": "add_app", "kind": "native", "name": app_name, "command": app_command
                        })
                        logging.info("Queued add native app: %s (%s)", app_name, app_command)

                    elif app_kind == "web":
                        app_url = str(payload.get("url", "")).strip()
                        if not app_url:
                            await websocket.send(json.dumps({"status": "error", "error": "url required for web app"}))
                            continue

                        self.window.queue_remote_event({
                            "type": "add_app", "kind": "web", "name": app_name, "url": app_url
                        })
                        logging.info("Queued add web app: %s (%s)", app_name, app_url)

                    await websocket.send(json.dumps({"status": "ok", "type": "app_added"}))
                    continue

                # Handle remove app request
                if message_type == "remove_app":
                    app_id = str(payload.get("id", "")).strip()

                    if not app_id:
                        await websocket.send(json.dumps({"status": "error", "error": "app id required"}))
                        continue

                    self.window.queue_remote_event({"type": "remove_app", "id": app_id})
                    logging.info("Queued remove app: %s", app_id)

                    await websocket.send(json.dumps({
                        "status": "ok",
                        "type": "app_removed",
                        "message": "App removed successfully"
                    }))
                    continue

                # Handle reorder app request
                if message_type == "reorder_app":
                    app_id = str(payload.get("id", "")).strip()
                    app_kind = str(payload.get("kind", ""))
                    direction = str(payload.get("direction", "")).lower()

                    if not app_id or app_kind not in ("native", "web") or direction not in ("left", "right"):
                        await websocket.send(json.dumps({"status": "error", "error": "invalid reorder request"}))
                        continue

                    self.window.queue_remote_event({
                        "type": "reorder_app", "id": app_id, "kind": app_kind, "direction": direction
                    })
                    logging.info("Queued reorder app: %s %s", app_id, direction)

                    await websocket.send(json.dumps({"status": "ok", "type": "app_reordered"}))
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

                # Handle Volume set request
                if message_type == "set_volume":
                    volume_level = int(payload.get("volume", 50))
                    try:
                        success = control_system_volume("SET_VOLUME", volume_level)
                        await websocket.send(json.dumps({
                            "status": "ok" if success else "error",
                            "type": "volume_set",
                            "success": success,
                            "volume": volume_level,
                            "message": f"Volume set to {volume_level}%" if success else "Failed to set volume"
                        }))
                    except Exception as exc:
                        logging.exception("Failed to set volume from remote")
                        await websocket.send(json.dumps({
                            "status": "error",
                            "type": "volume_set",
                            "message": f"Failed to set volume: {exc}"
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
