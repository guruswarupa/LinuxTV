#!/bin/bash
# Set up the persistence partition on first boot -- creating it if it
# doesn't exist yet, then formatting it if needed.
#
# The flash tool also tries to create this partition at flash time (via
# interactive fdisk), but that depends on the fdisk version and USB
# controller behavior of whatever machine someone flashes from, and can
# silently fail there. This is the guaranteed path: it runs inside the
# image we control, so it doesn't depend on the flashing machine at all.

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

# Create the partition if the flash step didn't already leave one in place.
if [ ! -b "$PERSIST_PARTITION" ]; then
    echo "Persistence partition ($PERSIST_PARTITION) not found; creating it."

    if ! command -v parted >/dev/null 2>&1; then
        echo "parted not available; cannot create persistence partition. Exiting."
        exit 0
    fi

    # Same "start right after the last existing partition" layout the flash
    # tool uses, but computed here and created non-interactively with
    # parted instead of scripted fdisk keystrokes.
    LAST_END_SECTOR=$(lsblk -nplb -o TYPE,START,SIZE "$BASE_DEVICE" 2>/dev/null | \
        awk '$1 == "part" { end = $2 + int($3 / 512); if (end > max) max = end } END { print max + 0 }')

    if [ -z "$LAST_END_SECTOR" ] || [ "$LAST_END_SECTOR" -eq 0 ]; then
        echo "Could not determine existing partition layout; not creating persistence partition."
        exit 0
    fi

    NEW_START_MIB=$(( (LAST_END_SECTOR * 512 / 1024 / 1024) + 1 ))
    echo "Creating persistence partition on $BASE_DEVICE starting at ${NEW_START_MIB}MiB..."

    if parted --script "$BASE_DEVICE" mkpart primary ext4 "${NEW_START_MIB}MiB" 100%; then
        udevadm settle 2>/dev/null || true
        partprobe "$BASE_DEVICE" 2>/dev/null || true
        sleep 2
    else
        echo "Failed to create persistence partition. Exiting."
        exit 0
    fi
fi

if [ ! -b "$PERSIST_PARTITION" ]; then
    echo "Persistence partition ($PERSIST_PARTITION) still not found after creation attempt, exiting."
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
