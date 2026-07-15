#!/bin/bash
# Auto-format persistence partition if it exists but is not ext4
# This script runs during boot to ensure Windows and macOS users
# who created the partition but couldn't format it get a working persistence setup

set -e

# Find the USB boot device (the one with the live system)
BOOT_DEVICE=$(findmnt -n -o SOURCE /run/live/medium 2>/dev/null || \
              findmnt -n -o SOURCE /lib/live/mount/medium 2>/dev/null || echo "")

if [ -z "$BOOT_DEVICE" ]; then
    # Not booted from USB, exit
    exit 0
fi

# Extract the base device (e.g., /dev/sda1 -> /dev/sda)
BASE_DEVICE=$(echo "$BOOT_DEVICE" | sed 's/[0-9]*$//')

echo "Boot device: $BOOT_DEVICE"
echo "Base device: $BASE_DEVICE"

# Find the third partition on this device (persistence partition)
PERSIST_PARTITION="${BASE_DEVICE}3"

# Check if the third partition exists
if [ ! -b "$PERSIST_PARTITION" ]; then
    echo "Persistence partition ($PERSIST_PARTITION) not found, exiting."
    exit 0
fi

echo "Found persistence partition: $PERSIST_PARTITION"

# Check filesystem type
FS_TYPE=$(blkid -s TYPE -o value "$PERSIST_PARTITION" 2>/dev/null || echo "")

# Track whether we need to reboot after setup
NEEDS_REBOOT=false

# If it has no filesystem type or is not ext4, format it
if [ -z "$FS_TYPE" ] || [ "$FS_TYPE" != "ext4" ]; then
    echo "Partition is not ext4 (current type: ${FS_TYPE:-none})."
    echo "Formatting as ext4..."
    
    # Unmount if mounted
    umount "$PERSIST_PARTITION" 2>/dev/null || true
    
    # Format as ext4 with label "persistence"
    mkfs.ext4 -F -L persistence "$PERSIST_PARTITION"
    
    echo "Partition formatted as ext4."
    NEEDS_REBOOT=true
else
    echo "Partition is already ext4."
fi

# Check if persistence.conf exists, create if needed
MOUNT_POINT=$(mktemp -d)
if mount "$PERSIST_PARTITION" "$MOUNT_POINT" 2>/dev/null; then
    if [ ! -f "$MOUNT_POINT/persistence.conf" ]; then
        echo "Creating persistence.conf..."
        echo "/ union" > "$MOUNT_POINT/persistence.conf"
        echo "Persistence configuration created."
    else
        echo "persistence.conf already exists."
    fi
    umount "$MOUNT_POINT" 2>/dev/null || true
    rmdir "$MOUNT_POINT" 2>/dev/null || true
    
    echo "Persistence setup complete!"
    
    # If we just formatted the partition, reboot so live-boot initramfs
    # picks up the new persistence partition on next boot
    if [ "$NEEDS_REBOOT" = true ]; then
        echo "Rebooting to activate persistence..."
        systemctl reboot
    fi
fi

exit 0
