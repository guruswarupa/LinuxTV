#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_USER="${SUDO_USER:-$USER}"
if [ "$RUNTIME_USER" = "root" ]; then
  echo "❌ Run this script as your normal user with sudo access, not as root."
  exit 1
fi
RUNTIME_HOME="$(getent passwd "$RUNTIME_USER" | cut -d: -f6)"
if [ -z "$RUNTIME_HOME" ]; then
  echo "❌ Could not determine home directory for user '$RUNTIME_USER'."
  exit 1
fi
INSTALL_DIR="$RUNTIME_HOME/LinuxTV"
VENV_DIR="$RUNTIME_HOME/.linuxtv_venv"

echo "========================================="
echo "LinuxTV Desktop Launcher - Full Setup"
echo "========================================="
echo ""
echo "Runtime user: $RUNTIME_USER"
echo "Install dir: $INSTALL_DIR"
echo ""

if ! command -v apt >/dev/null 2>&1; then
  echo "❌ This setup script is Debian/Ubuntu-focused."
  exit 1
fi

# Step 1: Install system dependencies
echo "[1/8] Installing system dependencies..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv python3-pyqt5 python3-pyqt5.qtwebengine python3-pyqt5.qtquick qml-module-qtquick2 qml-module-qtquick-window2 fonts-noto-core chromium xinit xserver-xorg xauth x11-xserver-utils libxcb-cursor0 pulseaudio-utils pipewire pipewire-pulse wireplumber dbus-user-session wmctrl xdotool openbox network-manager polkitd pkexec git curl ca-certificates gnupg

echo "Installing Brave browser..."
if curl -fsS https://dl.brave.com/install.sh | sudo sh; then
  echo "✓ Brave browser installed"
else
  echo "⚠ Warning: Brave browser install failed; keeping Chromium as the browser fallback."
fi

echo "Installing Stremio..."
wget -qO - https://debian.vejeta.com/key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/stremio-debian.gpg
echo "deb [signed-by=/usr/share/keyrings/stremio-debian.gpg] https://debian.vejeta.com trixie main non-free" | sudo tee /etc/apt/sources.list.d/stremio.list
sudo apt update
if sudo apt install -y stremio stremio-server; then
  echo "✓ Stremio and Stremio Server installed"
else
  echo "⚠ Warning: Stremio installation failed"
fi

# Step 2: Prepare runtime user
echo "[2/8] Preparing runtime user '$RUNTIME_USER'..."
sudo usermod -aG video,audio,input,plugdev,netdev "$RUNTIME_USER"
echo "✓ Added $RUNTIME_USER to video/audio/input/plugdev/netdev groups"

echo "Installing NetworkManager polkit rule for local LinuxTV users..."
sudo tee /etc/polkit-1/rules.d/49-linuxtv-networkmanager.rules > /dev/null <<'EOF'
polkit.addRule(function(action, subject) {
  if (subject.local &&
      subject.active &&
      subject.isInGroup("netdev")) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 ||
        action.id.indexOf("org.freedesktop.timedate1.") === 0) {
      return polkit.Result.YES;
    }
  }
});
EOF
sudo chmod 0644 /etc/polkit-1/rules.d/49-linuxtv-networkmanager.rules
echo "✓ Installed polkit rule for NetworkManager and time sync"

sudo systemctl enable systemd-timesyncd 2>/dev/null || true
sudo systemctl restart systemd-timesyncd 2>/dev/null || true
echo "✓ Enabled automatic network time sync"

# Step 3: Create directory structure for runtime user
echo "[3/8] Setting up LinuxTV directory for $RUNTIME_USER..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp -r "$SCRIPT_DIR"/../* "$INSTALL_DIR"/ 2>/dev/null || true
sudo chown -R "$RUNTIME_USER:$RUNTIME_USER" "$INSTALL_DIR"

# Ensure icons are readable by all users
sudo chmod -R o+r "$INSTALL_DIR/linuxtvdesktop/icons/" 2>/dev/null || true

echo "✓ LinuxTV directory owned by $RUNTIME_USER"

# Step 4: Create Python virtual environment and install packages
echo "[4/8] Setting up Python virtual environment..."
sudo -u "$RUNTIME_USER" python3 -m venv --system-site-packages "$VENV_DIR"
sudo -u "$RUNTIME_USER" "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel

echo "Installing Python packages in venv..."
if sudo -u "$RUNTIME_USER" "$VENV_DIR/bin/pip" install pyyaml websockets PySide6 evdev 2>&1 | tee /tmp/pip_install.log; then
  echo "✓ Python packages installed successfully"
else
  echo "⚠ Warning: pip install had some issues. Check /tmp/pip_install.log"
fi

# Verify imports
echo "Verifying imports..."
if sudo -u "$RUNTIME_USER" "$VENV_DIR/bin/python3" -c "from PySide6.QtCore import Qt; print('✓ PySide6 OK')" 2>/dev/null; then
  echo "✓ PySide6 verified"
elif sudo -u "$RUNTIME_USER" "$VENV_DIR/bin/python3" -c "from PyQt5.QtCore import Qt; print('✓ PyQt5 OK')" 2>/dev/null; then
  echo "✓ PyQt5 available (fallback)"
else
  echo "⚠ WARNING: No Qt library found! This will fail."
fi

# Step 5: Create .xinitrc for runtime user
echo "[5/8] Creating .xinitrc for $RUNTIME_USER..."
sudo -u "$RUNTIME_USER" mkdir -p "$RUNTIME_HOME"
sudo tee "$RUNTIME_HOME/.xinitrc" > /dev/null <<EOF
#!/bin/sh
export PATH="$VENV_DIR/bin:\$PATH"
export LINUXTV_QT_BINDING=PyQt5
export XDG_RUNTIME_DIR="/run/user/\$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=\$XDG_RUNTIME_DIR/bus"

# Redirect all output to log file for debugging
exec > /tmp/linuxtv_startup.log 2>&1

echo "[\$(date)] LinuxTV startup starting..."

# Ensure the user session bus exists for runtime services.
if [ ! -S "\$XDG_RUNTIME_DIR/bus" ] && command -v dbus-daemon >/dev/null 2>&1; then
  dbus-daemon --session --address="\$DBUS_SESSION_BUS_ADDRESS" --fork >/tmp/linuxtv_dbus.log 2>&1 || true
fi

# Bring up per-user audio services for the runtime session if they are not already running.
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user import-environment DISPLAY XAUTHORITY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS 2>/dev/null || true
  systemctl --user start pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null || true
fi
if ! pactl info >/dev/null 2>&1; then
  if command -v pipewire >/dev/null 2>&1; then
    pipewire >/tmp/linuxtv_pipewire.log 2>&1 &
  fi
  if command -v wireplumber >/dev/null 2>&1; then
    wireplumber >/tmp/linuxtv_wireplumber.log 2>&1 &
  fi
  if command -v pipewire-pulse >/dev/null 2>&1; then
    pipewire-pulse >/tmp/linuxtv_pipewire_pulse.log 2>&1 &
  fi
  sleep 2
fi

# Prefer HDMI for the TV when available and turn off the internal panel.
if command -v xrandr >/dev/null 2>&1; then
  HDMI_OUTPUT=\$(xrandr --query | awk '/^HDMI[^ ]* connected/{print \$1; exit}')
  INTERNAL_OUTPUT=\$(xrandr --query | awk '/^(eDP|LVDS)[^ ]* connected/{print \$1; exit}')

  if [ -n "\$HDMI_OUTPUT" ]; then
    echo "[\$(date)] Using HDMI output: \$HDMI_OUTPUT"
    if [ -n "\$INTERNAL_OUTPUT" ]; then
      xrandr --output "\$HDMI_OUTPUT" --auto --primary --pos 0x0 --rotate normal --output "\$INTERNAL_OUTPUT" --off || true
    else
      xrandr --output "\$HDMI_OUTPUT" --auto --primary --pos 0x0 --rotate normal || true
    fi
  else
    echo "[\$(date)] No HDMI output detected; leaving current display layout as-is"
  fi
fi

# Initialize audio output - respect user's default device selection
if command -v pactl >/dev/null 2>&1; then
  echo "[\$(date)] Initializing audio system..."
  
  # Check if a default sink is already set
  CURRENT_DEFAULT=\$(pactl get-default-sink 2>/dev/null || echo "")
  
  if [ -z "\$CURRENT_DEFAULT" ]; then
    # No default set, try to use HDMI if available
    HDMI_SINK=\$(pactl list short sinks 2>/dev/null | awk '/hdmi|HDMI/ {print \$2; exit}')
    if [ -n "\$HDMI_SINK" ]; then
      echo "[\$(date)] No default audio device set. Setting HDMI as default: \$HDMI_SINK"
      pactl set-default-sink "\$HDMI_SINK" || true
    else
      # Use first available sink
      FIRST_SINK=\$(pactl list short sinks 2>/dev/null | awk '{print \$2; exit}')
      if [ -n "\$FIRST_SINK" ]; then
        echo "[\$(date)] Setting first available audio device as default: \$FIRST_SINK"
        pactl set-default-sink "\$FIRST_SINK" || true
      fi
    fi
  else
    echo "[\$(date)] Audio default already set to: \$CURRENT_DEFAULT - preserving user preference"
  fi
  
  # Set HDMI card profile if available (but don't override default sink)
  HDMI_CARD=\$(pactl list cards short 2>/dev/null | awk '/hdmi|HDMI/ {print \$2; exit}')
  if [ -n "\$HDMI_CARD" ]; then
    echo "[\$(date)] Configuring HDMI audio card profile: \$HDMI_CARD"
    pactl set-card-profile "\$HDMI_CARD" output:hdmi-stereo 2>/dev/null || \
    pactl set-card-profile "\$HDMI_CARD" output:hdmi-stereo-extra1 2>/dev/null || true
  fi
fi

# Test Python and imports
echo "[\$(date)] Testing Python and imports..."
"$VENV_DIR/bin/python3" -c "import sys; print('Python path:', sys.path)" || echo "Python failed"
"$VENV_DIR/bin/python3" -c "from PySide6.QtCore import Qt; print('PySide6 OK')" 2>/dev/null || \
"$VENV_DIR/bin/python3" -c "from PyQt5.QtCore import Qt; print('PyQt5 OK')" 2>/dev/null || \
echo "ERROR: No Qt library found"

# Change to app directory
cd "$INSTALL_DIR/linuxtvdesktop"
echo "[\$(date)] Changed to: \$(pwd)"

# Start a lightweight window manager so launched apps receive focus correctly.
if command -v openbox >/dev/null 2>&1; then
  openbox >/tmp/linuxtv_openbox.log 2>&1 &
  sleep 0.5
fi

# Run launcher with full error output
echo "[\$(date)] Launching LinuxTV..."
exec "$VENV_DIR/bin/python3" "$INSTALL_DIR/linuxtvdesktop/launcher.py"
EOF
sudo chmod +x "$RUNTIME_HOME/.xinitrc"
sudo chown "$RUNTIME_USER:$RUNTIME_USER" "$RUNTIME_HOME/.xinitrc"
echo "✓ .xinitrc created (debug logs to /tmp/linuxtv_startup.log)"

# Step 5b: Add auto-start X to .profile
echo "[5b/8] Configuring auto-start of X on tty1..."
sudo tee -a "$RUNTIME_HOME/.profile" > /dev/null <<EOF

# Auto-start X and launcher on tty1
if [ -z "\$DISPLAY" ] && [ "\$(tty)" = "/dev/tty1" ]; then
  # Clear screen immediately to hide any login text
  clear 2>/dev/null || true
  printf '\033[3J' 2>/dev/null || true
  
  # Track startup attempts
  STARTUP_COUNT=\${STARTUP_COUNT:-0}
  export STARTUP_COUNT=\$((STARTUP_COUNT + 1))
  
  if [ \$STARTUP_COUNT -gt 3 ]; then
    echo ""
    echo "❌ LinuxTV startup failed multiple times."
    echo "Debug log: cat /tmp/linuxtv_startup.log"
    echo "To troubleshoot, try: $VENV_DIR/bin/python3 $INSTALL_DIR/linuxtvdesktop/launcher.py"
    echo ""
  else
    exec startx >/dev/null 2>&1
  fi
fi
EOF
sudo chown "$RUNTIME_USER:$RUNTIME_USER" "$RUNTIME_HOME/.profile"
echo "✓ Auto-start X configured with fallback safety"

# Step 6: Add auto-login on tty1
echo "[6/8] Configuring tty1 auto-login..."
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/override.conf > /dev/null <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $RUNTIME_USER --noclear --noissue --nohostname %I \$TERM
StandardInput=tty
StandardOutput=tty
StandardError=tty
TTYVTDisallocate=no
EOF
sudo systemctl daemon-reload
echo "✓ Auto-login configured"

# Suppress MOTD and login messages system-wide
sudo touch /etc/hushlogin
sudo chmod 644 /etc/hushlogin
echo "✓ Login messages suppressed"

# Step 7: Disable display manager
echo "[7/8] Disabling display manager..."
for dm in gdm3 lightdm sddm; do
  if systemctl is-enabled $dm &>/dev/null 2>&1; then
    sudo systemctl disable --now $dm 2>/dev/null || true
    echo "✓ Disabled $dm"
  fi
done

# Optional: Install systemd service
read -p "Install systemd service? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  echo "[8/8] Installing systemd service..."
  sudo tee /etc/systemd/system/linuxtv.service > /dev/null <<EOF
[Unit]
Description=LinuxTV Fullscreen Media Launcher
After=network.target
Wants=graphical.target

[Service]
Type=simple
User=$RUNTIME_USER
Environment=DISPLAY=:0
Environment=LINUXTV_QT_BINDING=PyQt5
Environment=XAUTHORITY=$RUNTIME_HOME/.Xauthority
ExecStart=/usr/bin/xinit $VENV_DIR/bin/python3 $INSTALL_DIR/linuxtvdesktop/launcher.py -- :0 vt1 -nolisten tcp
Restart=on-failure

[Install]
WantedBy=graphical.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable linuxtv.service
  echo "✓ Systemd service installed"
fi

# Config file setup
echo ""
echo "[Setup] Preparing config directory..."
sudo -u "$RUNTIME_USER" mkdir -p "$RUNTIME_HOME/.config/linuxtv"
sudo cp -n "$SCRIPT_DIR/config.yaml" "$RUNTIME_HOME/.config/linuxtv/config.yaml" 2>/dev/null || true
sudo chown "$RUNTIME_USER:$RUNTIME_USER" "$RUNTIME_HOME/.config/linuxtv/config.yaml"
echo "✓ Config file ready at ~/.config/linuxtv/config.yaml"

echo ""
echo "========================================="
echo "✅ LinuxTV Desktop Setup Complete!"
echo "========================================="
echo ""
echo "System is now ready. Next steps:"
echo "1) Reboot your system:"
echo "   sudo reboot"
echo ""
echo "2) After reboot, LinuxTV should start automatically on tty1"
echo ""
echo "3) Test WebSocket remote control (optional):"
echo "   websocat ws://<TV-IP>:8765"
echo "   {\"action\":\"RIGHT\"}"
echo ""
echo "4) Install mobile remote app:"
echo "   cd $INSTALL_DIR/linuxtvremote"
echo "   npm install && npm start"
echo ""
echo "5) Configure apps in ~/.config/linuxtv/config.yaml"
echo ""
echo "Questions? Check $INSTALL_DIR/linuxtvdesktop/README.md"
echo "========================================="
