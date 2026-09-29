"""Shell-outs to the OS: audio, brightness, power, updates, window fullscreening."""

import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path



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


def sync_system_time():
    """Best-effort startup time check. This deliberately does NOT try to
    turn NTP on itself -- that's a privileged systemd-timedated operation
    that goes through polkit, which means a kiosk box with no one present
    to click "Authenticate" either hangs waiting for a prompt or gets
    denied outright, and it'd repeat on every single launch since a failed
    attempt changes nothing. Enabling NTP is a one-time system setup
    concern (setup.sh does it, with a real admin authenticating once), not
    something to keep re-attempting from inside the app. This just reports
    the current status."""
    timedatectl = shutil.which("timedatectl")
    if not timedatectl:
        return False, "timedatectl is not installed."

    try:
        status_result = subprocess.run(
            [timedatectl, "show", "--property=NTP,NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception as exc:
        logging.exception("Failed to read time sync status")
        return False, f"Could not read time sync status: {exc}"

    if status_result.returncode != 0:
        message = (status_result.stderr or status_result.stdout or "Unknown error").strip()
        return False, f"Could not read time sync status: {message}"

    ntp_enabled, _, ntp_synced = status_result.stdout.strip().partition("\n")
    if ntp_enabled.strip().lower() != "yes":
        return False, "Automatic time sync (NTP) is off. Run setup.sh (or `sudo timedatectl set-ntp true`) to enable it."
    if ntp_synced.strip().lower() == "yes":
        return True, "System time synchronized."
    return True, "Automatic time sync is enabled; waiting for the first sync."


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


def control_system_volume(action: str, level: int = None):
    action = action.upper().strip()

    wpctl = shutil.which("wpctl")
    if wpctl:
        if action == "SET_VOLUME":
            target = max(0, min(100, int(level if level is not None else 50)))
            result = subprocess.run(
                [wpctl, "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", f"{target}%"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
        elif action == "VOLUME_UP":
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
        if action == "SET_VOLUME":
            target = max(0, min(100, int(level if level is not None else 50)))
            result = subprocess.run(
                [pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True
        elif action == "VOLUME_UP":
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
        if action == "SET_VOLUME":
            target = max(0, min(100, int(level if level is not None else 50)))
            result = subprocess.run([amixer, "set", "Master", f"{target}%"], check=False, capture_output=True, text=True)
            if result.returncode == 0:
                return True
        elif action == "VOLUME_UP":
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
