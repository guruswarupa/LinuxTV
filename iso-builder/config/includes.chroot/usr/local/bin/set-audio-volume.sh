#!/bin/bash
# Set audio volume to 100% and unmute at startup

# Set up environment for user session
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
export DISPLAY=:0

# Try pactl (PulseAudio) first since LinuxTV uses PulseAudio
if command -v pactl >/dev/null 2>&1; then
  pactl set-sink-volume @DEFAULT_SINK@ 100% 2>/dev/null || true
  pactl set-sink-mute @DEFAULT_SINK@ 0 2>/dev/null || true
# Fall back to wpctl (PipeWire/WirePlumber)
elif command -v wpctl >/dev/null 2>&1; then
  wpctl set-volume @DEFAULT_AUDIO_SINK@ 100% 2>/dev/null || true
  wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 2>/dev/null || true
fi

echo "Audio volume set to 100% and unmuted"
