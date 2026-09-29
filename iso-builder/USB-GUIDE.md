# LinuxTV Live USB - Complete Guide

## Writing the ISO to USB

### Using the LinuxTV Flash Tool

**This is the official flashing tool - works on Windows, macOS, and Linux!**

1. **Navigate to the iso-builder folder**

2. **Run the flash tool:**
   
   **Windows:**
   ```
   double-click flash-tool-windows.bat
   ```
   
   **macOS:**
   ```bash
   ./flash-tool-macos.sh
   ```
   
   **Linux:**
   ```bash
   ./flash-tool-linux.sh
   ```

3. **Use the GUI:**
   * Click "Browse..." and select the LinuxTV ISO file
   * Select your USB drive from the dropdown
   * Check "Enable persistence" if you want to save files and settings
   * Click "Flash LinuxTV to USB"
   * Wait for the process to complete (5-15 minutes)

4. **Done!** The tool will:
   * Write the ISO to your USB drive
   * Create a persistence partition (if enabled)
   * The persistence partition will be automatically formatted as ext4 on first boot

**Requirements:**
* Python 3 installed (download from https://www.python.org/downloads/)
* On Linux: `sudo apt install python3 python3-tk`

## Setting Up Persistence

**Persistence is automatically set up when you use the LinuxTV Flash Tool!**

The flash tool creates a persistence partition on your USB drive. On first boot, LinuxTV will automatically:
* Format the partition as ext4
* Create the persistence.conf file
* Enable persistence for saving your files and settings

No manual setup required!

## Troubleshooting: USB Not Showing in BIOS/UEFI

### If your USB drive doesn't appear in the boot menu:

#### 1. **Check Secure Boot Settings**
   - Enter BIOS/UEFI setup (usually F2, F12, DEL, or ESC during boot)
   - Find "Secure Boot" option
   - **Disable Secure Boot**
   - Save and exit

#### 2. **Check Boot Mode**
   - The LinuxTV ISO supports both **UEFI** and **Legacy BIOS**
   - Try switching boot modes:
     - If currently in UEFI mode, try "Legacy" or "CSM" mode
     - If currently in Legacy mode, try "UEFI" mode
   - Save and reboot

#### 3. **Verify ISO was Written Correctly**
   ```bash
   # Check if USB is bootable
   sudo fdisk -l /dev/sdX
   
   # You should see partitions listed
   ```

#### 4. **Try a Different USB Port**
   - Use USB 2.0 port instead of USB 3.0 (or vice versa)
   - Avoid USB hubs - plug directly into motherboard
   - Try ports on the back of the computer

#### 5. **Try a Different USB Drive**
   - Some USB drives have compatibility issues
   - Try a different brand or model
   - Use a quality USB drive (SanDisk, Kingston, Samsung)

#### 6. **Re-write the ISO**
   Sometimes the write process fails:
   ```bash
   # First, verify the ISO
   sha256sum LinuxTV.iso
   
   # Re-write with verification
   sudo dd if=LinuxTV.iso of=/dev/sdX bs=4M status=progress oflag=sync
   sudo sync
   ```

#### 7. **Check Fast Boot**
   - In BIOS/UEFI, disable "Fast Boot"
   - This can prevent USB detection
   - Save and reboot

#### 8. **Enable USB Boot**
   - In BIOS/UEFI, look for:
     - "USB Boot" - Enable it
     - "Boot from USB" - Enable it
     - "External Device Boot" - Enable it
   - Save and exit

## Boot Behavior

LinuxTV is configured to boot automatically with persistence enabled. The boot menu is hidden for a faster, more seamless experience.

**Default behavior:**
* The USB boots directly into LinuxTV Live with persistence
* No menu is shown, it starts in 0 seconds
* The persistence partition is automatically formatted on first boot (if not already ext4)
* Your files and settings are saved across reboots

**To access boot options:**
If you need to boot without persistence or use fail-safe mode, press `Esc` or `Shift` during boot to show the GRUB menu. You will see:

* **Boot LinuxTV Live** - Default option with persistence enabled
* **Boot LinuxTV Live (No Persistence)** - Fresh session, nothing saved
* **Advanced Options > Boot LinuxTV Live (fail-safe)** - For troubleshooting older hardware

## Default Login Credentials

- **Username:** linuxtv
- **Password:** linuxtv

## After Booting

Once LinuxTV boots:
1. The system will auto-login
2. Connect to WiFi using the network manager
3. Set up remote control credentials:
   * Open the LinuxTV settings
   * Navigate to Remote Control section
   * Set your username and password for remote access (8+ characters)
   * Or leave them empty and pair with the 6-digit pairing code shown on that screen
     (enter the code in the app's password field the first time you connect)
   * Note the IP address shown on screen
4. Install the LinuxTV Remote app:
   * Open Google Play Store on your Android phone
   * Search for "LinuxTV Remote"
   * Install the app
   * Open the app and enter the IP address and credentials you set up
5. Start using Kodi, VLC, or browse with Brave
6. Your changes will be saved across reboots (persistence is enabled by default)

## Need Help?

If you're still having issues:
1. Check the build log: `cat build.log`
2. Verify your ISO is not corrupted
3. Try the troubleshooting steps above
4. Test the USB on a different computer
