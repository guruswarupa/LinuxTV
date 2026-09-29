#!/usr/bin/env python3
"""
LinuxTV Flash Tool - Cross-platform GUI application to flash LinuxTV ISO to USB
Supports Windows, macOS, and Linux
"""

import os
import sys
import subprocess
import threading
import time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    print("Error: tkinter is required. Please install python3-tk package.")
    sys.exit(1)


class LinuxTVFlashTool:
    def __init__(self, root):
        self.root = root
        self.root.title("LinuxTV Flash Tool")
        self.root.geometry("600x500")
        self.root.resizable(False, False)
        
        self.iso_path = tk.StringVar()
        self.selected_drive = tk.StringVar()
        self.enable_persistence = tk.BooleanVar(value=True)
        self.is_flashing = False
        
        self.available_drives = []
        
        self.setup_ui()
        self.refresh_drives()
    
    def setup_ui(self):
        """Setup the user interface"""
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = ttk.Label(
            main_frame,
            text="LinuxTV Flash Tool",
            font=("Helvetica", 18, "bold")
        )
        title_label.pack(pady=(0, 10))
        
        # Admin status warning
        if sys.platform == 'win32' and not self.check_admin_windows():
            admin_warning = ttk.Label(
                main_frame,
                text="⚠ Not running as Administrator - Flash will fail!",
                foreground="red",
                font=("Helvetica", 10, "bold")
            )
            admin_warning.pack(pady=(0, 10))
        
        # ISO Selection
        iso_frame = ttk.LabelFrame(main_frame, text="Step 1: Select ISO File", padding="10")
        iso_frame.pack(fill=tk.X, pady=(0, 10))
        
        iso_entry = ttk.Entry(iso_frame, textvariable=self.iso_path, width=50)
        iso_entry.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)
        
        iso_button = ttk.Button(iso_frame, text="Browse...", command=self.browse_iso)
        iso_button.pack(side=tk.RIGHT)
        
        # Drive Selection
        drive_frame = ttk.LabelFrame(main_frame, text="Step 2: Select USB Drive", padding="10")
        drive_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.drive_combo = ttk.Combobox(
            drive_frame,
            textvariable=self.selected_drive,
            width=47,
            state="readonly"
        )
        self.drive_combo.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)
        
        refresh_button = ttk.Button(
            drive_frame,
            text="Refresh",
            command=self.refresh_drives
        )
        refresh_button.pack(side=tk.RIGHT)
        
        # Options
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))
        
        persistence_check = ttk.Checkbutton(
            options_frame,
            text="Enable persistence (save your files and settings)",
            variable=self.enable_persistence
        )
        persistence_check.pack(anchor=tk.W)
        
        persistence_info = ttk.Label(
            options_frame,
            text="A partition will be created. On first boot, LinuxTV will automatically format it as ext4.",
            foreground="gray",
            font=("Helvetica", 9)
        )
        persistence_info.pack(anchor=tk.W, pady=(5, 0))
        
        # Flash Button
        self.flash_button = ttk.Button(
            main_frame,
            text="Flash LinuxTV to USB",
            command=self.start_flash,
            style="Accent.TButton"
        )
        self.flash_button.pack(fill=tk.X, pady=(0, 10))
        
        # Progress
        self.progress = ttk.Progressbar(main_frame, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # Status
        self.status_label = ttk.Label(
            main_frame,
            text="Ready",
            font=("Helvetica", 10)
        )
        self.status_label.pack()
        
        # Warning
        warning_label = ttk.Label(
            main_frame,
            text="WARNING: This will erase all data on the selected USB drive!",
            foreground="red",
            font=("Helvetica", 9, "bold")
        )
        warning_label.pack(pady=(10, 0))
    
    def browse_iso(self):
        """Browse for ISO file"""
        filename = filedialog.askopenfilename(
            title="Select LinuxTV ISO",
            filetypes=[("ISO files", "*.iso"), ("All files", "*.*")]
        )
        if filename:
            self.iso_path.set(filename)
    
    def refresh_drives(self):
        """Refresh available drives"""
        self.available_drives = self.get_available_drives()
        
        if self.available_drives:
            drive_list = [f"{d['device']} - {d['name']} ({d['size']})" for d in self.available_drives]
            self.drive_combo['values'] = drive_list
            if drive_list:
                self.drive_combo.current(0)
        else:
            self.drive_combo['values'] = ["No USB drives found"]
            self.selected_drive.set("No USB drives found")
    
    def get_available_drives(self):
        """Get available USB drives based on OS"""
        drives = []
        
        try:
            if sys.platform == 'win32':
                # Windows - use PowerShell
                result = subprocess.run(
                    ['powershell', '-Command',
                     'Get-PhysicalDisk | Where-Object {$_.BusType -eq "USB"} | '
                     'Select-Object DeviceId, FriendlyName, Size | ConvertTo-Json'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    import json
                    disk_data = json.loads(result.stdout)
                    if isinstance(disk_data, dict):
                        disk_data = [disk_data]
                    for disk in disk_data:
                        size_gb = int(disk.get('Size', 0)) / (1024**3)
                        drives.append({
                            'device': f"\\\\.\\PhysicalDrive{disk['DeviceId']}",
                            'name': disk.get('FriendlyName', 'USB Drive'),
                            'size': f"{size_gb:.1f} GB"
                        })
            
            elif sys.platform == 'darwin':
                # macOS - use diskutil
                result = subprocess.run(
                    ['diskutil', 'list', '-plist'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    import plistlib
                    plist_data = plistlib.loads(result.stdout.encode())
                    for disk in plist_data.get('AllDisksAndPartitions', []):
                        if disk.get('Internal') is False and disk.get('DeviceSize', 0) > 0:
                            size_gb = disk['DeviceSize'] / (1024**3)
                            device = disk.get('DeviceIdentifier', '')
                            if 'disk' in device:
                                drives.append({
                                    'device': f"/dev/{device.split('s')[0]}",
                                    'name': disk.get('MediaName', 'USB Drive'),
                                    'size': f"{size_gb:.1f} GB"
                                })
            
            else:
                # Linux - use lsblk
                result = subprocess.run(
                    ['lsblk', '-d', '-n', '-o', 'NAME,SIZE,MODEL,TRAN'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        parts = line.split()
                        if len(parts) >= 2:
                            name = parts[0]
                            size = parts[1] if len(parts) > 1 else ""
                            model = ' '.join(parts[2:]) if len(parts) > 2 else "USB Drive"
                            if name.startswith(('sd', 'vd', 'nvme')):
                                drives.append({
                                    'device': f"/dev/{name}",
                                    'name': model.strip(),
                                    'size': size
                                })
        except Exception as e:
            print(f"Error getting drives: {e}")
        
        return drives
    
    def check_admin_windows(self):
        """Check if running as Administrator on Windows"""
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False
    
    def start_flash(self):
        """Start the flashing process"""
        if self.is_flashing:
            messagebox.showwarning("Warning", "Flash operation already in progress!")
            return
        
        # Check for admin rights on Windows
        if sys.platform == 'win32' and not self.check_admin_windows():
            messagebox.showerror(
                "Administrator Rights Required",
                "This tool must be run as Administrator to flash USB drives.\n\n"
                "Please close this tool and run it again by:\n"
                "1. Right-clicking on the Python script or shortcut\n"
                "2. Selecting 'Run as administrator'\n"
                "3. Clicking 'Yes' when prompted by UAC"
            )
            return
        
        # Validate inputs
        if not self.iso_path.get():
            messagebox.showerror("Error", "Please select an ISO file!")
            return
        
        if not os.path.exists(self.iso_path.get()):
            messagebox.showerror("Error", "ISO file not found!")
            return
        
        if not self.selected_drive.get() or "No USB drives found" in self.selected_drive.get():
            messagebox.showerror("Error", "Please select a USB drive!")
            return
        
        # Confirm
        drive_info = self.selected_drive.get()
        confirm = messagebox.askyesno(
            "Confirm",
            f"This will ERASE ALL DATA on the selected USB drive:\n\n"
            f"{drive_info}\n\n"
            f"Are you sure you want to continue?"
        )
        
        if not confirm:
            return
        
        # Start flashing in background thread
        self.is_flashing = True
        self.flash_button.config(state=tk.DISABLED)
        self.progress['value'] = 0
        
        thread = threading.Thread(target=self.flash_iso, daemon=True)
        thread.start()
    
    def flash_iso(self):
        """Flash ISO to USB drive"""
        try:
            iso_file = self.iso_path.get()
            drive_info = self.selected_drive.get()
            device = drive_info.split(' - ')[0]
            
            self.update_status("Starting flash process...")
            self.update_progress(10)
            
            if sys.platform == 'win32':
                self.flash_windows(iso_file, device)
            elif sys.platform == 'darwin':
                self.flash_macos(iso_file, device)
            else:
                self.flash_linux(iso_file, device)
            
            self.update_progress(100)
            self.update_status("Flash complete!")
            
            self.root.after(0, lambda: messagebox.showinfo(
                "Success",
                "LinuxTV has been successfully flashed to USB!\n\n"
                "Partition layout:\n"
                "• Partition 1: EFI (100MB)\n"
                "• Partition 2: LinuxTV ISO (~2GB)\n"
                "• Partition 3: Persistence (remaining space)\n\n"
                "You can now boot from this USB drive.\n"
                "On first boot, LinuxTV will automatically format\n"
                "the persistence partition as ext4."
            ))
        
        except Exception as e:
            error_message = str(e)
            self.update_status(f"Error: {error_message}")
            self.root.after(0, lambda: messagebox.showerror("Error", f"Flash failed:\n{error_message}"))
        
        finally:
            self.is_flashing = False
            self.root.after(0, lambda: self.flash_button.config(state=tk.NORMAL))
    
    def flash_windows(self, iso_file, device):
        """Flash ISO on Windows using PowerShell raw write (dd is unreliable for hybrid ISOs)"""
        self.update_status("Writing ISO to USB (this may take 5-15 minutes)...")
        
        # Always use PowerShell method - dd for Windows doesn't handle hybrid ISOs properly
        print(f"Using PowerShell raw write to {device}")
        print(f"ISO file: {iso_file}")
        
        # Check ISO file size
        iso_size = os.path.getsize(iso_file)
        print(f"ISO file size: {iso_size / (1024*1024):.0f} MB ({iso_size} bytes)")
        
        self._flash_windows_powershell(iso_file, device)
        
        # Verify the ISO was written correctly
        print("Verifying disk after flash...")
        time.sleep(5)
        
        disk_number = device.replace("\\\\.\\PhysicalDrive", "")
        verify_script = f"select disk {disk_number}\nlist partition\ndetail disk\nexit\n"
        verify_file = Path(os.environ['TEMP']) / "linuxtv_post_flash_verify.txt"
        verify_file.write_text(verify_script)
        
        verify_result = subprocess.run(
            ['diskpart', '/s', str(verify_file)],
            capture_output=True,
            text=True,
            timeout=30
        )
        verify_file.unlink()
        
        print(f"Post-flash disk layout:\n{verify_result.stdout}")
        if verify_result.stderr:
            print(f"Post-flash errors: {verify_result.stderr}")
    
    def _flash_windows_powershell(self, iso_file, device):
        """Flash ISO on Windows using diskpart apply command (most reliable for hybrid ISOs)"""
        self.update_status("Writing ISO to USB (this may take 5-15 minutes)...")
        
        disk_number = device.replace("\\\\.\\PhysicalDrive", "")
        
        print(f"Using diskpart to apply ISO to disk {disk_number}")
        
        # Method 1: Try using diskpart's apply command (Windows 10/11)
        # First, we need to mount the ISO, then apply it
        ps_script = """
            $ErrorActionPreference = "Stop"
            $isoPath = "{}"
            $diskNumber = {}
            $drivePath = "{}"
            
            try {{
                Write-Host "Step 1: Mounting ISO..."
                $mountedISO = Mount-DiskImage -ImagePath $isoPath -PassThru
                $driveLetter = ($mountedISO | Get-Volume).DriveLetter
                Write-Host "ISO mounted as drive $driveLetter`:"
                
                Write-Host "Step 2: Preparing USB disk..."
                # Get ISO size to calculate how much space we need for the main partition
                $isoSize = (Get-Item $isoPath).Length
                $isoSizeMB = [math]::Ceiling($isoSize / 1MB) + 100  # Add 100MB buffer
                
                Write-Host "ISO size: $isoSizeMB MB"
                
                $diskpartScript = @"
select disk $diskNumber
attributes disk clear readonly
online disk noerr
clean
convert mbr
create partition primary size=100
format quick fs=fat32 label="EFI"
active
create partition primary size=$isoSizeMB
format quick fs=fat32 label="LinuxTV"
create partition primary
exit
"@
                $tempFile = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllText($tempFile, $diskpartScript)
                $result = Start-Process "diskpart.exe" -ArgumentList "/s `"$tempFile`"" -Wait -NoNewWindow -PassThru
                [System.IO.File]::Delete($tempFile)
                
                if ($result.ExitCode -ne 0) {{
                    throw "DiskPart failed with exit code $($result.ExitCode)"
                }}
                
                Write-Host "Step 3: Getting USB partitions..."
                Start-Sleep -Seconds 3
                $usbPartitions = Get-Partition -DiskNumber $diskNumber
                
                # For MBR, get partitions by size and order
                $sortedPartitions = $usbPartitions | Where-Object {{ $_.Type -ne 'Reserved' }} | Sort-Object PartitionNumber
                
                # First partition (smallest, ~100MB) is EFI
                $efiPartition = $sortedPartitions | Select-Object -First 1
                
                # Second partition (~2GB) is main LinuxTV
                $mainPartition = $sortedPartitions | Select-Object -Skip 1 -First 1
                
                if (-not $efiPartition) {{
                    throw "EFI partition not found"
                }}
                if (-not $mainPartition) {{
                    throw "Main partition not found"
                }}
                
                Write-Host "EFI Partition: $($efiPartition.PartitionNumber) (Size: $([math]::Round($efiPartition.Size/1MB)) MB)"
                Write-Host "Main Partition: $($mainPartition.PartitionNumber) (Size: $([math]::Round($mainPartition.Size/1MB)) MB)"
                
                Write-Host "Step 4: Assigning temporary drive letters..."
                
                # Assign drive letters using diskpart for reliability
                $efiPartNum = $efiPartition.PartitionNumber
                $mainPartNum = $mainPartition.PartitionNumber
                
                $assignEFIScript = @"
select disk $diskNumber
select partition $efiPartNum
assign letter=Z
exit
"@
                $tempFileEFI = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllText($tempFileEFI, $assignEFIScript)
                Start-Process "diskpart.exe" -ArgumentList "/s `"$tempFileEFI`"" -Wait -NoNewWindow
                [System.IO.File]::Delete($tempFileEFI)
                
                $assignMainScript = @"
select disk $diskNumber
select partition $mainPartNum
assign letter=Y
exit
"@
                $tempFileMain = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllText($tempFileMain, $assignMainScript)
                Start-Process "diskpart.exe" -ArgumentList "/s `"$tempFileMain`"" -Wait -NoNewWindow
                [System.IO.File]::Delete($tempFileMain)
                
                Start-Sleep -Seconds 2
                
                $efiDrive = "Z"
                $mainDrive = "Y"
                Write-Host "EFI drive: $efiDrive`:"
                Write-Host "Main drive: $mainDrive`:"
                
                Write-Host "Step 5: Copying ISO contents to main partition..."
                $isoContents = Get-ChildItem -Path "${{driveLetter}}:\" -Force | Where-Object {{ $_.Name -ne 'EFI' -and $_.Name -ne 'boot' }}
                $totalFiles = ($isoContents | Measure-Object).Count
                $copiedFiles = 0
                
                foreach ($item in $isoContents) {{
                    Copy-Item -Path $item.FullName -Destination "${{mainDrive}}:\" -Recurse -Force -ErrorAction SilentlyContinue
                    $copiedFiles++
                    $progress = [math]::Round(($copiedFiles / $totalFiles) * 100)
                    if ($progress % 10 -eq 0) {{
                        Write-Host "PROGRESS:$progress"
                    }}
                }}
                
                Write-Host "Step 6: Setting up GRUB bootloader on EFI partition..."
                $grubDir = "${{efiDrive}}:\\EFI\\BOOT"
                if (-not (Test-Path $grubDir)) {{
                    New-Item -Path $grubDir -ItemType Directory -Force
                }}
                
                # Copy GRUB EFI bootloader
                $sourceGRUB = "${{driveLetter}}:\\EFI\\BOOT"
                if (Test-Path $sourceGRUB) {{
                    Copy-Item -Path "$sourceGRUB\\*" -Destination $grubDir -Recurse -Force
                    Write-Host "GRUB bootloader copied to EFI partition"
                }} else {{
                    Write-Host "WARNING: GRUB EFI files not found in ISO"
                }}
                
                # Copy grub.cfg
                $sourceGrubCfg = "${{driveLetter}}:\\boot\\grub\\grub.cfg"
                $destGrubCfg = "${{efiDrive}}:\\EFI\\BOOT\\grub.cfg"
                if (Test-Path $sourceGrubCfg) {{
                    Copy-Item -Path $sourceGrubCfg -Destination $destGrubCfg -Force
                    Write-Host "GRUB configuration copied"
                }} else {{
                    Write-Host "WARNING: grub.cfg not found"
                }}
                
                Write-Host "Step 7: Copying remaining ISO files..."
                # Copy boot folder if it exists
                $sourceBoot = "${{driveLetter}}:\\boot"
                if (Test-Path $sourceBoot) {{
                    Copy-Item -Path $sourceBoot -Destination "${{mainDrive}}:\\" -Recurse -Force -ErrorAction SilentlyContinue
                    Write-Host "Boot files copied"
                }}
                
                Write-Host "PROGRESS:100"
                Write-Host "Step 7: Cleaning up..."
                
                # Remove temporary drive letters
                $removeLetters = @"
select disk $diskNumber
select partition $efiPartNum
remove letter=Z noerr
select partition $mainPartNum
remove letter=Y noerr
exit
"@
                $tempFileRemove = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllText($tempFileRemove, $removeLetters)
                Start-Process "diskpart.exe" -ArgumentList "/s `"$tempFileRemove`"" -Wait -NoNewWindow
                [System.IO.File]::Delete($tempFileRemove)
                
                # Unmount ISO
                Dismount-DiskImage -ImagePath $isoPath
                
                Write-Host "Flash completed successfully!"
            }}
            catch {{
                Write-Host "`nERROR: $_" -ForegroundColor Red
                Write-Host "Stack trace: $($_.ScriptStackTrace)" -ForegroundColor Red
                exit 1
            }}
        """.format(iso_file, disk_number, device)
        
        # Run PowerShell and monitor progress
        process = subprocess.Popen(
            ['powershell', '-Command', ps_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Monitor output for progress
        import re
        output_lines = []
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                line = line.strip()
                output_lines.append(line)
                print(line)
                
                # Check for progress line
                match = re.search(r'PROGRESS:(\d+)', line)
                if match:
                    progress = int(match.group(1))
                    self.update_progress(progress)
                    self.update_status(f"Writing ISO... {progress}%")
        
        process.wait()
        
        if process.returncode != 0:
            error_msg = "PowerShell flash failed. Output:\n" + "\n".join(output_lines[-20:])
            raise Exception(error_msg)
    
    def flash_macos(self, iso_file, device):
        """Flash ISO on macOS using dd"""
        self.update_status("Writing ISO to USB (this may take 5-15 minutes)...")
        
        disk_id = device.replace("/dev/", "")
        subprocess.run(['diskutil', 'unmountDisk', f"/dev/{disk_id}"], capture_output=True)
        
        raw_device = device.replace("/dev/disk", "/dev/rdisk")
        cmd = ['sudo', 'dd', f'if={iso_file}', f'of={raw_device}', 'bs=1m']
        
        process = subprocess.run(cmd, capture_output=True, timeout=3600)
        if process.returncode != 0:
            raise Exception(f"dd failed: {process.stderr.decode()}")
        
        subprocess.run(['sync'])
        
        # Create persistence partition automatically (like Windows and Linux do)
        self.create_persistence_partition(device)
    
    def flash_linux(self, iso_file, device):
        """Flash ISO on Linux using dd"""
        self.update_status("Writing ISO to USB (this may take 5-15 minutes)...")
        
        cmd = ['sudo', 'dd', f'if={iso_file}', f'of={device}', 'bs=4M', 'status=progress', 'oflag=sync']
        
        process = subprocess.run(cmd, capture_output=True, timeout=3600)
        if process.returncode != 0:
            raise Exception(f"dd failed: {process.stderr.decode()}")
        
        # Create persistence partition automatically (like Windows does)
        self.create_persistence_partition(device)
    
    def create_persistence_partition(self, device):
        """Create persistence partition (without formatting)"""
        self.update_status("Creating persistence partition...")
        
        if sys.platform == 'win32':
            self.create_persistence_windows(device)
        elif sys.platform == 'darwin':
            self.create_persistence_macos(device)
        else:
            self.create_persistence_linux(device)
    
    def create_persistence_windows(self, device):
        """Create persistence partition on Windows"""
        import time
        import json
        time.sleep(5)
        
        disk_number = device.replace("\\\\.\\PhysicalDrive", "")
        
        # Wait for disk to settle after flash
        self.update_status("Analyzing disk layout...")
        time.sleep(3)
        
        # Get disk size
        disk_size_result = subprocess.run(
            ['powershell', '-Command', 
             f'(Get-PhysicalDisk -DeviceNumber {disk_number}).Size'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if disk_size_result.returncode != 0:
            print(f"Failed to get disk size: {disk_size_result.stderr}")
            self.update_status("Warning: Could not get disk size")
            return
        
        try:
            disk_size_bytes = int(disk_size_result.stdout.strip())
            disk_size_mb = disk_size_bytes / (1024 * 1024)
            print(f"Disk size: {disk_size_mb:.0f} MB ({disk_size_bytes} bytes)")
        except:
            print(f"Failed to parse disk size: {disk_size_result.stdout}")
            self.update_status("Warning: Could not parse disk size")
            return
        
        # Get partitions using PowerShell
        partitions_result = subprocess.run(
            ['powershell', '-Command', f'Get-Partition -DiskNumber {disk_number} | Select-Object PartitionNumber,Size,Type,DriveLetter | ConvertTo-Json'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if partitions_result.returncode != 0:
            print(f"Failed to get partition info: {partitions_result.stderr}")
            self.update_status("Warning: Could not get partition information")
            return
        
        partitions = json.loads(partitions_result.stdout)
        if isinstance(partitions, dict):
            partitions = [partitions]
        
        print(f"Found {len(partitions)} partition(s)")
        total_partition_size = 0
        for p in partitions:
            p_size = p.get('Size', 0)
            p_size_mb = p_size / (1024*1024)
            total_partition_size += p_size
            print(f"  Partition {p.get('PartitionNumber')}: {p.get('Type')} - {p_size_mb:.0f} MB")
        
        # Calculate space needed for persistence (reserve 2GB for persistence)
        persistence_size_mb = 2048  # 2GB
        persistence_size_bytes = persistence_size_mb * 1024 * 1024
        
        # Check if there's enough unallocated space
        used_space = total_partition_size
        unallocated_space = disk_size_bytes - used_space
        unallocated_mb = unallocated_space / (1024 * 1024)
        
        print(f"Total partition space: {total_partition_size / (1024*1024):.0f} MB")
        print(f"Unallocated space: {unallocated_mb:.0f} MB")
        
        if unallocated_mb >= persistence_size_mb:
            # Enough space already exists, just create the partition
            print(f"Sufficient unallocated space ({unallocated_mb:.0f} MB), creating partition...")
        else:
            # Need to shrink the largest partition (usually the ISO partition)
            print(f"Insufficient space. Need to shrink ISO partition by {persistence_size_mb - unallocated_mb:.0f} MB")
            self.update_status("Resizing ISO partition to make room...")
            
            # Find the largest partition (usually partition 2 - the ISO9660)
            largest_partition = max(partitions, key=lambda p: p.get('Size', 0))
            largest_part_num = largest_partition.get('PartitionNumber')
            largest_part_size = largest_partition.get('Size', 0)
            
            # Calculate new size (leave room for persistence + 100MB buffer)
            shrink_amount = persistence_size_bytes + (100 * 1024 * 1024)  # 2GB + 100MB buffer
            new_size = largest_part_size - shrink_amount
            new_size_mb = new_size / (1024 * 1024)
            
            print(f"Shrinking partition {largest_part_num} from {largest_part_size / (1024*1024):.0f} MB to {new_size_mb:.0f} MB")
            
            # Shrink the partition
            shrink_script = f"select disk {disk_number}\nselect partition {largest_part_num}\nshrink desired={int(persistence_size_mb + 100)}\nexit\n"
            shrink_file = Path(os.environ['TEMP']) / "linuxtv_shrink.txt"
            shrink_file.write_text(shrink_script)
            
            shrink_result = subprocess.run(
                ['diskpart', '/s', str(shrink_file)],
                capture_output=True,
                text=True,
                timeout=120
            )
            shrink_file.unlink()
            
            print(f"Shrink output: {shrink_result.stdout}")
            if shrink_result.returncode != 0:
                print(f"Shrink errors: {shrink_result.stderr}")
                self.update_status("Warning: Failed to resize partition")
                print("NOTE: The ISO partition may not support shrinking.")
                print("Solution: The setup-persistence.sh script on LinuxTV will handle this on first boot.")
                return
            
            time.sleep(3)
        
        # Create persistence partition in the freed space
        self.update_status("Creating persistence partition...")
        print("Creating persistence partition in unallocated space...")
        
        persist_script = f"select disk {disk_number}\ncreate partition primary size={persistence_size_mb}\nexit\n"
        persist_file = Path(os.environ['TEMP']) / "linuxtv_persist.txt"
        persist_file.write_text(persist_script)
        
        result = subprocess.run(
            ['diskpart', '/s', str(persist_file)],
            capture_output=True,
            text=True,
            timeout=60
        )
        persist_file.unlink()
        
        print(f"Create persistence output: {result.stdout}")
        print(f"Create persistence errors: {result.stderr}")
        
        if result.returncode != 0:
            self.update_status("Warning: Persistence partition creation failed")
            print("NOTE: The ISO partition may be using all disk space.")
            print("Solution: The setup-persistence.sh script on LinuxTV will handle this on first boot.")
        else:
            self.update_status("Persistence partition created!")
            time.sleep(2)
            
            # Verify partition was created
            verify_script = f"select disk {disk_number}\nlist partition\nexit\n"
            verify_file = Path(os.environ['TEMP']) / "linuxtv_verify.txt"
            verify_file.write_text(verify_script)
            verify_result = subprocess.run(
                ['diskpart', '/s', str(verify_file)],
                capture_output=True,
                text=True,
                timeout=30
            )
            verify_file.unlink()
            print(f"Final partitions: {verify_result.stdout}")
    
    def create_persistence_macos(self, device):
        """Create persistence partition on macOS"""
        self.update_status("Waiting for disk to be recognized...")
        time.sleep(3)
        
        disk_id = device.replace("/dev/", "")
        
        # Get disk information to find the ISO partition
        result = subprocess.run(
            ['diskutil', 'list', f"/dev/{disk_id}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            print(f"Failed to get disk info: {result.stderr}")
            raise Exception("Could not detect disk partitions. Try unplugging and replugging the USB.")
        
        print(f"Disk layout before partitioning:\n{result.stdout}")
        
        # Resize the second partition (ISO partition) and create persistence partition
        # Using 'R' to resize to minimum needed, leaving remaining space for persistence
        self.update_status("Creating persistence partition...")
        
        cmd = ['sudo', 'diskutil', 'resizeVolume', f"/dev/{disk_id}s2", 'R',
               'Free Space', 'free',
               'LinuxTVPersistence', '0b']
        
        print(f"Running: {' '.join(cmd)}")
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        print(f"diskutil output: {process.stdout}")
        if process.stderr:
            print(f"diskutil errors: {process.stderr}")
        
        if process.returncode != 0:
            print("WARNING: macOS partition creation failed")
            print("The ISO may have an unsupported filesystem for resizing.")
            print("Solution: The setup-persistence.sh script on LinuxTV will handle this on first boot.")
            self.update_status("Warning: Persistence partition creation failed")
        else:
            self.update_status("Persistence partition created successfully!")
            time.sleep(2)
            
            # Verify partition was created
            verify_result = subprocess.run(
                ['diskutil', 'list', f"/dev/{disk_id}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            print(f"Final disk layout:\n{verify_result.stdout}")
    
    def create_persistence_linux(self, device):
        """Create persistence partition on Linux"""
        self.update_status("Waiting for disk to be recognized...")
        time.sleep(3)
        
        subprocess.run(['sudo', 'partprobe', device], capture_output=True)
        time.sleep(2)
        
        result = subprocess.run(
            ['lsblk', '-nplb', '-o', 'TYPE,START,SIZE', device],
            capture_output=True,
            text=True
        )
        
        last_sector = 0
        partition_count = 0
        for line in result.stdout.strip().split('\n'):
            parts = line.split()
            if parts[0] == 'part' and len(parts) >= 3:
                start = int(parts[1])
                size = int(parts[2])
                end = start + (size // 512)
                if end > last_sector:
                    last_sector = end
                    partition_count += 1
        
        if partition_count == 0:
            self.update_status("Using alternative partition detection...")
            disk_result = subprocess.run(
                ['sudo', 'blockdev', '--getsz', device],
                capture_output=True,
                text=True
            )
            if disk_result.returncode == 0:
                last_sector = 8388608
            else:
                raise Exception("Could not detect disk partitions. Try unplugging and replugging the USB.")
        
        new_start = ((last_sector // 2048) + 1) * 2048
        
        self.update_status("Creating persistence partition...")
        
        fdisk_cmd = f"n\np\n3\n{new_start}\n\nw\n"
        process = subprocess.run(
            ['sudo', 'fdisk', device],
            input=fdisk_cmd,
            text=True,
            capture_output=True,
            timeout=60
        )
        
        if process.returncode != 0:
            print(f"fdisk output: {process.stdout}")
            print(f"fdisk stderr: {process.stderr}")
            if "Warning" not in process.stderr.decode():
                raise Exception(f"fdisk failed: {process.stderr}")
        
        self.update_status("Refreshing partition table...")
        subprocess.run(['sudo', 'partprobe', device], capture_output=True)
        subprocess.run(['sudo', 'blockdev', '--rereadpt', device], capture_output=True)
        time.sleep(3)
        
        # Verify partition was created
        verify_result = subprocess.run(
            ['lsblk', '-n', '-o', 'NAME', device],
            capture_output=True,
            text=True
        )
        
        partitions = [p.strip().lstrip('├─└│ ') for p in verify_result.stdout.strip().split('\n')]
        base_name = device.split('/')[-1]
        partition3 = f"{base_name}3"
        
        if partition3 in partitions:
            self.update_status("Persistence partition created successfully!")
            time.sleep(1)
        else:
            print(f"Warning: Partition verification failed. Partitions found: {partitions}")
            print(f"Looking for: {partition3}")
            self.update_status("Partition created (verify manually with lsblk)")
            time.sleep(1)
    
    def find_dd_windows(self):
        """Find dd executable on Windows"""
        # Check for dd.exe in the same directory as the flash tool
        dd_local = Path(__file__).parent / "dd.exe"
        if dd_local.exists():
            return str(dd_local)
        
        possible_paths = [
            r"C:\Program Files\GnuWin32\bin\dd.exe",
            r"C:\Program Files (x86)\GnuWin32\bin\dd.exe",
            r"C:\cygwin64\bin\dd.exe",
            r"C:\cygwin\bin\dd.exe",
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def update_status(self, message):
        """Update status label"""
        self.root.after(0, lambda: self.status_label.config(text=message))
    
    def update_progress(self, value):
        """Update progress bar"""
        self.root.after(0, lambda: self.progress.config(value=value))


def main():
    root = tk.Tk()
    LinuxTVFlashTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
