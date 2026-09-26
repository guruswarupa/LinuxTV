#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$SCRIPT_DIR/workdir"
OUTPUT_LOG="$SCRIPT_DIR/build.log"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./build-iso.sh"
  exit 1
fi

if ! command -v lb >/dev/null 2>&1; then
  apt-get update
  apt-get install -y live-build rsync
elif ! command -v rsync >/dev/null 2>&1; then
  apt-get update
  apt-get install -y rsync
fi

# Clean up previous build - properly unmount chroot filesystems first
if [ -d "$BUILD_DIR/chroot" ]; then
  echo "Cleaning up previous build..."
  # Unmount proc, sys, and dev if still mounted
  for mount_point in "$BUILD_DIR/chroot/proc" "$BUILD_DIR/chroot/sys" "$BUILD_DIR/chroot/dev" "$BUILD_DIR/chroot/dev/pts"; do
    if mountpoint -q "$mount_point" 2>/dev/null; then
      umount "$mount_point" 2>/dev/null || umount -l "$mount_point" 2>/dev/null || true
    fi
  done
fi

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

rsync -a --delete "$SCRIPT_DIR/config/" "$BUILD_DIR/config/"

mkdir -p "$BUILD_DIR/config/includes.chroot/opt/linuxtv"
rsync -a --delete "$REPO_ROOT/linuxtvdesktop/" "$BUILD_DIR/config/includes.chroot/opt/linuxtv/linuxtvdesktop/"
if [ -d "$REPO_ROOT/linuxtvremote" ]; then
  rsync -a --delete --delete-excluded \
    --exclude '.git/' \
    --exclude 'node_modules/' \
    --exclude '.expo/' \
    --exclude '.gradle/' \
    --exclude 'android/.gradle/' \
    --exclude 'android/app/build/' \
    --exclude 'android/build/' \
    --exclude 'ios/' \
    --exclude 'dist/' \
    --exclude 'build/' \
    --exclude '.DS_Store' \
    --exclude '*.log' \
    --exclude '*.tmp' \
    --exclude 'record.json' \
    --exclude 'ecord.json' \
    "$REPO_ROOT/linuxtvremote/" "$BUILD_DIR/config/includes.chroot/opt/linuxtv/linuxtvremote/"
fi

cd "$BUILD_DIR"
lb clean --purge 2>/dev/null || true

lb config \
  --mode debian \
  --architectures amd64 \
  --distribution trixie \
  --archive-areas "main contrib non-free non-free-firmware" \
  --binary-images iso-hybrid \
  --iso-application "LinuxTV" \
  --iso-volume "LinuxTV" \
  --bootappend-live "boot=live components quiet splash loglevel=0 vt.global_cursor_default=0 persistence" \
  --linux-flavours amd64 \
  --linux-packages linux-image \
  --apt-recommends false \
  --debian-installer-gui false \
  --mirror-bootstrap http://deb.debian.org/debian/ \
  --mirror-chroot http://deb.debian.org/debian/ \
  --mirror-binary http://deb.debian.org/debian/ \
  --security false

lb build 2>&1 | tee "$OUTPUT_LOG"

# Copy and rename ISO to LinuxTV.iso
find "$BUILD_DIR" -maxdepth 1 -type f -name '*.iso' -exec cp -f {} "$SCRIPT_DIR/LinuxTV.iso" \;
find "$BUILD_DIR" -maxdepth 1 -type f -name '*.packages' -exec cp -f {} "$SCRIPT_DIR/" \; 2>/dev/null || true

echo
echo "ISO built successfully!"
ls -lh "$SCRIPT_DIR/LinuxTV.iso"
