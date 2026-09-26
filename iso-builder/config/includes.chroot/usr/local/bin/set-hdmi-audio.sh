#!/bin/bash
# Set an HDMI-named PulseAudio sink as the default output, if one exists.
#
# Runs once at startup (see set-hdmi-audio.service), not on every app
# launch: when LinuxTV boots connected to a TV over HDMI, TV speakers
# should be the default without the OS re-forcing HDMI later and fighting
# a user who's since picked a different output via Settings > Sound.
# Mirrors switch_audio_to_hdmi() in linuxtvdesktop/launcher.py.

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

command -v pactl >/dev/null 2>&1 || exit 0

hdmi_sink=""
while read -r _ name _; do
  case "${name,,}" in
    *hdmi*) hdmi_sink="$name"; break ;;
  esac
done < <(pactl list short sinks)

if [ -z "$hdmi_sink" ]; then
  echo "No HDMI sink found; leaving audio output unchanged"
  exit 0
fi

echo "Switching audio to HDMI sink $hdmi_sink"
pactl set-default-sink "$hdmi_sink"

pactl list short sink-inputs | while read -r input_id _; do
  [ -n "$input_id" ] && pactl move-sink-input "$input_id" "$hdmi_sink"
done
