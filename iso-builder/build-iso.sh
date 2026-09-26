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

apt-get update
# debian-archive-keyring: debootstrapping Debian trixie from this Ubuntu
# host needs Debian's own archive keyring on disk to verify the Release
# file's signature -- Ubuntu only ships its own ubuntu-archive-keyring by
# default ("Cannot check Release signature; keyring file not available
# /usr/share/keyrings/debian-archive-keyring.gpg", exit 1 at debootstrap).
apt-get install -y rsync isolinux cpio debootstrap debian-archive-keyring

# The live-build in Ubuntu's own apt repos is ancient (3.0~a57, a pre-2016
# version-numbering scheme) and has accumulated a long list of mismatches
# against Debian trixie's current archive layout: wrong suite naming for
# the security archive, the old flat Contents-<arch>.gz path for firmware
# auto-detection, a syslinux theme path syslinux-common doesn't ship at
# that location anymore, sysvinit as its default init system, and a
# ubuntu/amd64 self-reported mode instead of debian. Rather than keep
# patching individual staleness bugs one at a time, just install Debian
# trixie's own current live-build directly -- it's arch-independent, has
# minimal deps (cpio, debootstrap, both installed above), and is built
# against exactly the archive layout we're actually building against.
if ! dpkg-query -W -f='${Version}' live-build 2>/dev/null | grep -q '^1:20250505'; then
  LIVE_BUILD_DEB="$(mktemp -d)/live-build.deb"
  curl -fsSL -o "$LIVE_BUILD_DEB" \
    "https://ftp.debian.org/debian/pool/main/l/live-build/live-build_20250505+deb13u1_all.deb"
  dpkg -i "$LIVE_BUILD_DEB" || apt-get install -yf
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
  --initsystem systemd \
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
  --security false \
  --firmware-chroot false

lb build 2>&1 | tee "$OUTPUT_LOG"

# Copy and rename ISO to LinuxTV.iso
find "$BUILD_DIR" -maxdepth 1 -type f -name '*.iso' -exec cp -f {} "$SCRIPT_DIR/LinuxTV.iso" \;
find "$BUILD_DIR" -maxdepth 1 -type f -name '*.packages' -exec cp -f {} "$SCRIPT_DIR/" \; 2>/dev/null || true

echo
echo "ISO built successfully!"
ls -lh "$SCRIPT_DIR/LinuxTV.iso"
