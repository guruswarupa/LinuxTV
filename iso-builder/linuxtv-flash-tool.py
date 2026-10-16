#!/usr/bin/env python3
"""
LinuxTV Flash Tool - Cross-platform GUI application to flash LinuxTV ISO to USB
Supports Windows, macOS, and Linux

Windows writes the (iso-hybrid) image straight to the physical drive, the same
way `dd` does on Linux/macOS, so the result boots exactly like the Linux flash.
"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, filedialog
    from tkinter import font as tkfont
except ImportError:
    print("Error: tkinter is required. Please install python3-tk package.")
    sys.exit(1)

IS_WINDOWS = sys.platform == 'win32'
# Keep PowerShell/diskpart from flashing console windows over the GUI.
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if IS_WINDOWS else 0

CHUNK = 4 * 1024 * 1024          # multiple of 512 and 4096 (raw writes must be sector aligned)
PERSISTENCE_MIN_BYTES = 1024 ** 3

PALETTE = {
    'bg': '#070d1c',
    'header_top': '#0d1d44',
    'card': '#0e1830',
    'border': '#1c2c52',
    'field': '#13213f',
    'text': '#eaf1ff',
    'muted': '#7f92b8',
    'accent': '#2f80ff',
    'accent_hover': '#559bff',
    'accent_down': '#1f63cc',
    'cyan': '#38bdf8',
    'danger': '#ff6b81',
    'success': '#34d399',
    'warn': '#fbbf24',
}


def mix(c1, c2, t):
    """Blend two #rrggbb colors; t=0 gives c1, t=1 gives c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class Card(tk.Canvas):
    """Rounded panel; put widgets in `.body`."""

    def __init__(self, parent, ui, fill=None, border=None, pad_y=16):
        # Start tall so the body frame is mapped and can report its real size; _fit then shrinks to it.
        super().__init__(parent, bg=PALETTE['bg'], highlightthickness=0, bd=0, height=ui.s(600))
        self.ui = ui
        self.fill = fill or PALETTE['card']
        self.border = border or PALETTE['border']
        self.pad_x, self.pad_y = ui.s(18), ui.s(pad_y)
        self.body = tk.Frame(self, bg=self.fill)
        self._win = self.create_window(self.pad_x, self.pad_y, window=self.body, anchor='nw')
        self.bind('<Configure>', self._redraw)
        self.body.bind('<Configure>', self._fit)

    def _fit(self, _event=None):
        want = self.body.winfo_reqheight() + 2 * self.pad_y
        if abs(int(self['height']) - want) > 1:
            self.configure(height=want)

    def _redraw(self, _event=None):
        self.delete('card_bg')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        round_rect(self, 1, 1, w - 1, h - 1, self.ui.s(16), fill=self.fill, outline=self.border, width=1, tags='card_bg')
        self.tag_lower('card_bg')
        self.itemconfigure(self._win, width=w - 2 * self.pad_x)


class FancyButton(tk.Canvas):
    STYLES = {
        'primary': ('accent', 'accent_hover', 'accent_down', '#ffffff'),
        'secondary': ('field', 'border', 'card', 'text'),
        'ghost': ('bg', 'card', 'bg', 'muted'),
        'danger': ('danger', '#ff8a9b', '#d9506a', '#ffffff'),
    }

    def __init__(self, parent, ui, text, command, kind='secondary', height=40, width=None, size=10, bold=True, bg=None):
        self.ui, self.command, self.kind, self.label = ui, command, kind, text
        self.enabled, self.hover, self.down = True, False, False
        self.font = ui.font(size, bold)
        text_w = tkfont.Font(font=self.font).measure(text)
        w = width or text_w + ui.s(36)
        super().__init__(parent, width=w, height=ui.s(height), bg=bg or PALETTE['card'], highlightthickness=0, bd=0, cursor='hand2')
        self.bind('<Configure>', lambda e: self.render())
        self.bind('<Enter>', lambda e: self._set(hover=True))
        self.bind('<Leave>', lambda e: self._set(hover=False, down=False))
        self.bind('<ButtonPress-1>', lambda e: self._set(down=True))
        self.bind('<ButtonRelease-1>', self._release)

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.render()

    def _release(self, event):
        was_down = self.down
        self._set(down=False)
        if was_down and self.enabled and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor='hand2' if enabled else 'arrow')
        self.render()

    def set_text(self, text):
        self.label = text
        self.render()

    def render(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        base, hover, down, fg = self.STYLES[self.kind]
        pal = lambda k: PALETTE.get(k, k)
        if not self.enabled:
            fill, fg_color = mix(PALETTE['field'], PALETTE['bg'], 0.3), PALETTE['muted']
        else:
            fill = pal(down if self.down else hover if self.hover else base)
            fg_color = pal(fg)
        outline = PALETTE['border'] if self.kind == 'secondary' else fill
        radius = min(self.ui.s(12), h // 2)
        round_rect(self, 1, 1, w - 1, h - 1, radius, fill=fill, outline=outline)
        if self.kind == 'primary' and self.enabled:    # soft top highlight for a glossy, raised look
            round_rect(self, 3, 3, w - 3, h // 2, max(radius - 3, 2), fill=mix(fill, '#ffffff', 0.12), outline='')
        self.create_text(w // 2, h // 2, text=self.label, fill=fg_color, font=self.font)


class ProgressBar(tk.Canvas):
    def __init__(self, parent, ui, bg=None):
        super().__init__(parent, height=ui.s(10), bg=bg or PALETTE['card'], highlightthickness=0, bd=0)
        self.ui = ui
        self.target = self.shown = 0.0
        self.color = PALETTE['accent']
        self.busy = False
        self.phase = 0.0
        self.bind('<Configure>', lambda e: self.render())
        self._tick()

    def set(self, value, color=None, instant=False):
        self.target = max(0.0, min(100.0, float(value)))
        if color:
            self.color = color
        if instant:
            self.shown = self.target
        self.render()

    def _tick(self):
        moved = abs(self.target - self.shown) > 0.05
        if moved:
            self.shown += (self.target - self.shown) * 0.25
        if self.busy:
            self.phase = (self.phase + 0.02) % 1.0
        if moved or self.busy:
            self.render()
        self.after(33, self._tick)

    def render(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        r = h // 2
        round_rect(self, 0, 0, w, h, r, fill=PALETTE['field'], outline='')
        fill_w = int(w * self.shown / 100)
        if fill_w > h:
            round_rect(self, 0, 0, fill_w, h, r, fill=self.color, outline='')
            round_rect(self, 0, 0, fill_w, h // 2, r // 2, fill=mix(self.color, '#ffffff', 0.18), outline='')
            if self.busy and fill_w > h * 3:           # light band sweeping along the filled part
                pos = int(self.phase * (fill_w + h * 4)) - h * 2
                x1, x2 = max(0, pos - h * 2), min(fill_w, pos)
                if x2 - x1 > h:
                    round_rect(self, x1, 0, x2, h, r, fill=mix(self.color, '#ffffff', 0.35), outline='')
        elif fill_w > 0:
            self.create_oval(0, 0, h, h, fill=self.color, outline='')


class Toggle(tk.Canvas):
    def __init__(self, parent, ui, variable, bg=None):
        w, h = ui.s(44), ui.s(24)
        super().__init__(parent, width=w, height=h, bg=bg or PALETTE['bg'], highlightthickness=0, bd=0, cursor='hand2')
        self.var, self.w, self.h = variable, w, h
        self.bind('<Button-1>', lambda e: self.toggle())
        variable.trace_add('write', lambda *_: self.render())
        self.render()

    def toggle(self):
        self.var.set(not self.var.get())

    def render(self):
        self.delete('all')
        on = self.var.get()
        round_rect(self, 0, 0, self.w, self.h, self.h // 2, fill=PALETTE['accent'] if on else PALETTE['border'], outline='')
        pad = 3
        x = self.w - self.h + pad if on else pad
        self.create_oval(x, pad, x + self.h - 2 * pad, self.h - pad, fill='#ffffff', outline='')


class StepBadge(tk.Canvas):
    def __init__(self, parent, ui, number):
        d = ui.s(26)
        super().__init__(parent, width=d, height=d, bg=PALETTE['card'], highlightthickness=0, bd=0)
        self.number, self.d, self.font, self.done = number, d, ui.font(9, True), False
        self.render()

    def set_done(self, done):
        self.done = done
        self.render()

    def render(self):
        self.delete('all')
        if self.done:
            self.create_oval(1, 1, self.d - 1, self.d - 1, fill=PALETTE['success'], outline='')
            self.create_text(self.d // 2, self.d // 2, text='✓', fill='#06281c', font=self.font)
        else:
            self.create_oval(1, 1, self.d - 1, self.d - 1, fill=PALETTE['field'], outline=PALETTE['accent'], width=2)
            self.create_text(self.d // 2, self.d // 2, text=str(self.number), fill=PALETTE['cyan'], font=self.font)


class StatusIcon(tk.Canvas):
    """Small state icon: ready ring, spinner, success check, error cross, warning."""

    def __init__(self, parent, ui, bg=None):
        d = ui.s(28)
        super().__init__(parent, width=d, height=d, bg=bg or PALETTE['card'], highlightthickness=0, bd=0)
        self.d, self.font, self.state, self.angle = d, ui.font(10, True), 'idle', 0
        self.render()
        self._spin()

    def set_state(self, state):
        self.state = state
        self.render()

    def _spin(self):
        if self.state == 'busy':
            self.angle = (self.angle - 14) % 360
            self.render()
        self.after(40, self._spin)

    def render(self):
        self.delete('all')
        d, m = self.d, 3
        if self.state == 'busy':
            self.create_oval(m, m, d - m, d - m, outline=PALETTE['field'], width=3)
            self.create_arc(m, m, d - m, d - m, start=self.angle, extent=110, style=tk.ARC,
                            outline=PALETTE['cyan'], width=3)
        elif self.state in ('success', 'error', 'warn'):
            fill, glyph, ink = {'success': (PALETTE['success'], '\u2713', '#06281c'),
                                'error': (PALETTE['danger'], '\u2715', '#3a0610'),
                                'warn': (PALETTE['warn'], '!', '#3a2a02')}[self.state]
            self.create_oval(m - 1, m - 1, d - m + 1, d - m + 1, fill=fill, outline='')
            self.create_text(d // 2, d // 2, text=glyph, fill=ink, font=self.font)
        else:
            self.create_oval(m, m, d - m, d - m, outline=PALETTE['border'], width=2)
            self.create_oval(d // 2 - 3, d // 2 - 3, d // 2 + 3, d // 2 + 3, fill=PALETTE['accent'], outline='')


class Header(tk.Canvas):
    def __init__(self, parent, ui, logo):
        super().__init__(parent, height=ui.s(104), bg=PALETTE['bg'], highlightthickness=0, bd=0)
        self.ui, self.logo = ui, logo
        self.bind('<Configure>', lambda e: self.render())

    def render(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        s = self.ui.s
        for i in range(h):
            self.create_line(0, i, w, i, fill=mix(PALETTE['header_top'], PALETTE['bg'], i / h))
        cx, cy = s(70), h // 2
        for i in range(14, 0, -1):          # soft glow behind the logo
            rad = s(14) + i * s(9)
            self.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                             fill=mix(PALETTE['header_top'], '#1b4fb8', (15 - i) / 15 * 0.55), outline='')
        for i in range(h):                   # keep the lower edge fading into the page
            if i > h * 0.7:
                self.create_line(0, i, w, i, fill=mix(PALETTE['header_top'], PALETTE['bg'], i / h))
        if self.logo:
            self.create_image(s(24), h // 2, image=self.logo, anchor='w')
        tx = s(24) + (self.logo.width() if self.logo else 0) + s(18)
        self.create_text(tx, h // 2 - s(14), text='LinuxTV', anchor='w', fill=PALETTE['text'], font=self.ui.font(24, True))
        self.create_text(tx, h // 2 + s(14), text='Flash Tool  •  make a bootable USB in minutes',
                         anchor='w', fill=PALETTE['muted'], font=self.ui.font(10))
        self.create_line(0, h - 1, w, h - 1, fill=PALETTE['border'])


class FlashCancelled(Exception):
    pass


def format_size(num_bytes):
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f"{value:.0f} {unit}" if unit == 'B' else f"{value:.1f} {unit}"
        value /= 1024


def format_eta(seconds):
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds} s"


def is_admin_windows():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Re-run this script elevated (UAC prompt). Returns True if the new process started."""
    try:
        import ctypes
        if getattr(sys, 'frozen', False):
            exe, params = sys.executable, ' '.join(f'"{a}"' for a in sys.argv[1:])
        else:
            exe = sys.executable
            windowless = os.path.join(os.path.dirname(exe), 'pythonw.exe')
            if os.path.exists(windowless):
                exe = windowless   # no stray console window behind the GUI
            params = ' '.join(f'"{a}"' for a in [os.path.abspath(sys.argv[0])] + sys.argv[1:])
        return ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, None, 1) > 32
    except Exception:
        return False


def powershell_json(script, timeout=20):
    """Run a PowerShell snippet that ends in ConvertTo-Json and return the parsed result."""
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; " + script
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', full],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or 'PowerShell command failed')
    text = result.stdout.strip()
    return json.loads(text) if text else None


# --- Windows raw disk helpers ---------------------------------------------------

def _win_disk_length(fd):
    """Size in bytes of an open physical drive (IOCTL_DISK_GET_LENGTH_INFO)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
    length = ctypes.c_longlong()
    returned = wintypes.DWORD()
    ok = ctypes.windll.kernel32.DeviceIoControl(
        handle, 0x0007405C, None, 0, ctypes.byref(length), ctypes.sizeof(length),
        ctypes.byref(returned), None)
    if not ok:
        raise OSError('Could not read the size of the USB drive.')
    return length.value


def _win_allow_extended_io(fd):
    """FSCTL_ALLOW_EXTENDED_DASD_IO so writes aren't clipped to a filesystem."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        returned = wintypes.DWORD()
        ctypes.windll.kernel32.DeviceIoControl(
            wintypes.HANDLE(msvcrt.get_osfhandle(fd)), 0x00090083, None, 0, None, 0,
            ctypes.byref(returned), None)
    except Exception:
        pass


def patch_mbr_persistence(sector0, total_bytes):
    """Return a copy of an MBR sector with a Linux partition 3 filling the free tail.

    Returns None if the layout isn't a plain MBR we can safely extend (GPT, no
    free slot, not enough space). The image's own first-boot script will create
    the partition in that case, so skipping is always safe.
    """
    if len(sector0) < 512 or sector0[510:512] != b'\x55\xaa':
        return None
    mbr = bytearray(sector0)
    entries = []
    for i in range(4):
        off = 446 + i * 16
        ptype = mbr[off + 4]
        start, size = struct.unpack_from('<II', mbr, off + 8)
        entries.append((ptype, start, size))
    if any(t == 0xEE for t, _, _ in entries):
        return None                       # protective MBR: GPT disk
    if entries[2][0] != 0 or entries[3][0] != 0:
        return None                       # partition 3/4 already in use
    if all(n == 0 for _, _, n in entries):
        return None
    # Count every populated entry, whatever its type: the hybrid ISO's own
    # partition 1 is type 0x00 yet covers the whole image, so skipping "empty"
    # types would start the persistence partition inside the ISO and the
    # first-boot format would destroy it.
    last_end = max(s + n for _, s, n in entries)
    new_start = ((last_end + 2047) // 2048) * 2048
    total_sectors = min(total_bytes // 512, 0xFFFFFFFF)
    new_size = total_sectors - new_start
    if new_size * 512 < PERSISTENCE_MIN_BYTES:
        return None
    off = 446 + 2 * 16
    mbr[off:off + 16] = struct.pack('<B3sB3sII', 0x00, b'\xfe\xff\xff', 0x83, b'\xfe\xff\xff', new_start, new_size)
    return bytes(mbr)


ESP_GUID = bytes.fromhex('28732ac11ff8d211ba4b00a0c93ec93b')   # EFI System Partition type GUID (mixed-endian)


def efi_partition_ranges(iso_path):
    """Byte ranges of EFI system partitions inside the (hybrid) ISO.

    Windows mounts these FAT partitions as soon as the write finishes and may
    write to them (volume metadata), so a byte-for-byte compare must not treat
    differences there as a bad write. Everything else is still compared.
    """
    ranges = []
    try:
        with open(iso_path, 'rb') as fh:
            head = fh.read(512 * 34)
        if len(head) >= 512 and head[510:512] == b'\x55\xaa':
            for i in range(4):
                off = 446 + i * 16
                if head[off + 4] == 0xEF:
                    lba, count = struct.unpack_from('<II', head, off + 8)
                    ranges.append((lba * 512, (lba + count) * 512))
        if head[512:520] == b'EFI PART' and len(head) >= 512 * 34:
            entry_lba, n_entries, entry_size = struct.unpack_from('<QII', head, 512 + 72)
            if entry_lba == 2 and entry_size >= 128:
                for i in range(min(n_entries, (len(head) - 1024) // entry_size)):
                    e = head[1024 + i * entry_size:1024 + (i + 1) * entry_size]
                    if e[:16] == ESP_GUID:
                        first, last = struct.unpack_from('<QQ', e, 32)
                        ranges.append((first * 512, (last + 1) * 512))
    except Exception:
        pass
    return ranges


def _write_all(dst, data):
    """Raw writes can be partial; keep going until every byte is out."""
    view = memoryview(data)
    while len(view):
        n = dst.write(view)
        if not n:
            raise OSError('The USB drive stopped accepting data.')
        view = view[n:]


def _first_difference(expected, actual, base, skip):
    """Offset of the first byte that differs outside the skip ranges, else None."""
    if expected == actual:
        return None
    for i in range(len(expected)):
        if expected[i] != actual[i]:
            pos = base + i
            if not any(a <= pos < b for a, b in skip):
                return pos
    return None


def write_image(iso_path, dest_path, progress, cancelled, verify=True, fd_hook=None, log=print):
    """Write iso_path to dest_path in aligned chunks, then read back and compare.

    progress(phase, fraction, bytes_per_sec) is called as it goes. Works on a
    physical drive or an ordinary file (used by the self-test).
    Returns the number of bytes in the ISO.
    """
    iso_size = os.path.getsize(iso_path)
    start = time.time()

    with open(iso_path, 'rb') as src, open(dest_path, 'r+b', buffering=0) as dst:
        if fd_hook:
            fd_hook(dst.fileno(), iso_size)
        written = 0
        while True:
            if cancelled():
                raise FlashCancelled()
            data = src.read(CHUNK)
            if not data:
                break
            written += len(data)
            if len(data) % 4096:
                data += b'\x00' * (4096 - len(data) % 4096)
            _write_all(dst, data)
            progress('write', written / iso_size, written / max(time.time() - start, 0.001))
        try:
            os.fsync(dst.fileno())
        except OSError:
            pass

    if verify:
        skip = efi_partition_ranges(iso_path)
        if skip:
            log("Verify: ignoring EFI partition ranges Windows may touch: " +
                ", ".join(f"{a}-{b}" for a, b in skip))
        start = time.time()
        done = 0
        with open(iso_path, 'rb') as src, open(dest_path, 'rb', buffering=0) as dst:
            while done < iso_size:
                if cancelled():
                    raise FlashCancelled()
                expected = src.read(CHUNK)
                want = len(expected)
                got = dst.read(want + (-want % 4096))[:want]
                if len(got) < want:
                    raise OSError(f'The USB drive returned less data than was written (stopped at byte {done + len(got)}).')
                bad = _first_difference(expected, got, done, skip)
                if bad is not None:
                    raise OSError(
                        f'Verification failed at byte {bad:,} ({format_size(bad)} into the image): the data on the '
                        f'USB drive does not match the ISO. The drive may be faulty or counterfeit - try another '
                        f'USB port or drive.')
                done += want
                progress('verify', done / iso_size, done / max(time.time() - start, 0.001))
    return iso_size


class LinuxTVFlashTool:
    def __init__(self, root):
        self.root = root
        self.root.title("LinuxTV Flash Tool")
        self.root.configure(bg=PALETTE['bg'])
        self.scale = max(1.0, self.root.winfo_fpixels('1i') / 96.0)
        height = min(self.s(900), self.root.winfo_screenheight() - self.s(80))
        self.root.minsize(self.s(640), min(self.s(720), height))
        self.root.geometry(f"{self.s(700)}x{height}")

        self.iso_path = tk.StringVar()
        self.verify_write = tk.BooleanVar(value=True)
        self.is_flashing = False
        self.cancel_requested = False
        self.available_drives = []
        self.drive_index = -1
        self.drive_rows = []
        self.log_visible = False
        self.scanning = False

        self.font_family = 'Segoe UI' if IS_WINDOWS else tkfont.nametofont('TkDefaultFont').actual()['family']
        self.load_images()
        self.setup_ui()
        self.apply_window_chrome()
        self.iso_path.trace_add('write', lambda *_: self.update_iso_info())
        self.refresh_drives()

    # --- Helpers ---------------------------------------------------------------

    def s(self, n):
        return int(round(n * getattr(self, 'scale', 1.0)))

    def font(self, size, bold=False):
        return (self.font_family, size, 'bold' if bold else 'normal')

    def load_images(self):
        self.logo = self.icon = None
        folder = Path(__file__).resolve().parent / 'assets'
        logo_name = 'logo-80.png' if self.scale < 1.25 else 'logo-120.png' if self.scale < 1.75 else 'logo-160.png'
        try:
            self.logo = tk.PhotoImage(file=str(folder / logo_name))
            self.icon = tk.PhotoImage(file=str(folder / 'icon-256.png'))
            self.root.iconphoto(True, self.icon)
        except Exception:
            pass   # running without the assets folder just means no logo

    def apply_window_chrome(self):
        """Dark title bar on Windows 10/11 so the window frame matches the app."""
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def label(self, parent, text='', size=10, bold=False, color=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=self.font(size, bold), fg=color or PALETTE['text'],
                        bg=bg or PALETTE['card'], **kw)

    def card_title(self, card, number, title):
        row = tk.Frame(card.body, bg=card.fill)
        row.pack(fill=tk.X, pady=(0, self.s(12)))
        badge = StepBadge(row, self, number)
        badge.pack(side=tk.LEFT, padx=(0, self.s(10)))
        self.label(row, title, 12, True, bg=card.fill).pack(side=tk.LEFT)
        return row, badge

    # --- UI ----------------------------------------------------------------------

    def setup_ui(self):
        Header(self.root, self, self.logo).pack(fill=tk.X)
        page = tk.Frame(self.root, bg=PALETTE['bg'])
        page.pack(fill=tk.BOTH, expand=True, padx=self.s(24), pady=(self.s(16), self.s(16)))
        self.page = page

        if IS_WINDOWS and not is_admin_windows():
            bar = Card(page, self, fill=mix(PALETTE['card'], PALETTE['warn'], 0.10), border=mix(PALETTE['border'], PALETTE['warn'], 0.5))
            bar.pack(fill=tk.X, pady=(0, self.s(12)))
            row = tk.Frame(bar.body, bg=bar.fill)
            row.pack(fill=tk.X)
            self.label(row, "Administrator rights are needed to write to a USB drive.", 10, True,
                       PALETTE['warn'], bg=bar.fill).pack(side=tk.LEFT)
            FancyButton(row, self, "Restart as admin", self.restart_as_admin, 'secondary', height=32, size=9,
                        bg=bar.fill).pack(side=tk.RIGHT)

        # Step 1 - ISO
        iso_card = Card(page, self)
        iso_card.pack(fill=tk.X, pady=(0, self.s(12)))
        _, self.iso_badge = self.card_title(iso_card, 1, "Choose the ISO file")
        row = tk.Frame(iso_card.body, bg=iso_card.fill)
        row.pack(fill=tk.X)
        FancyButton(row, self, "Browse", self.browse_iso, 'secondary', height=40, bg=iso_card.fill).pack(side=tk.RIGHT, padx=(self.s(10), 0))
        entry = tk.Entry(row, textvariable=self.iso_path, font=self.font(10), bg=PALETTE['field'], fg=PALETTE['text'],
                         insertbackground=PALETTE['text'], relief=tk.FLAT, highlightthickness=1,
                         highlightbackground=PALETTE['border'], highlightcolor=PALETTE['accent'])
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=self.s(10))
        self.iso_info = self.label(iso_card.body, "No file selected yet", 9, color=PALETTE['muted'], bg=iso_card.fill)
        self.iso_info.pack(anchor=tk.W, pady=(self.s(10), 0))

        # Step 2 - drive
        self.drive_card = Card(page, self)
        self.drive_card.pack(fill=tk.X, pady=(0, self.s(12)))
        head, self.drive_badge = self.card_title(self.drive_card, 2, "Choose the USB drive")
        FancyButton(head, self, "Refresh", self.refresh_drives, 'ghost', height=30, size=9, bg=self.drive_card.fill).pack(side=tk.RIGHT)
        self.drive_list = tk.Frame(self.drive_card.body, bg=self.drive_card.fill)
        self.drive_list.pack(fill=tk.X)
        self.drive_info = self.label(self.drive_card.body, "", 9, color=PALETTE['muted'], bg=self.drive_card.fill,
                                     wraplength=self.s(580), justify=tk.LEFT)
        self.drive_info.pack(anchor=tk.W, pady=(self.s(8), 0))

        # Options strip (Windows only: verify switch)
        if IS_WINDOWS:
            strip = tk.Frame(page, bg=PALETTE['bg'])
            strip.pack(fill=tk.X, pady=(0, self.s(12)))
            self.label(strip, "Verify after writing", 9, color=PALETTE['muted'], bg=PALETTE['bg']).pack(side=tk.LEFT)
            self.label(strip, "  recommended - checks the stick was written correctly", 9,
                       color=mix(PALETTE['muted'], PALETTE['bg'], 0.35), bg=PALETTE['bg']).pack(side=tk.LEFT)
            Toggle(strip, self, self.verify_write).pack(side=tk.RIGHT)

        # Flash
        self.flash_button = FancyButton(page, self, "Flash LinuxTV to USB", self.start_flash, 'primary', height=54,
                                        size=13, bg=PALETTE['bg'])
        self.flash_button.pack(fill=tk.X)
        self.cancel_button = FancyButton(page, self, "Cancel", self.cancel_flash, 'secondary', height=38, bg=PALETTE['bg'])

        # Progress / status
        self.status_card = Card(page, self)
        self.status_card.pack(fill=tk.X, pady=(self.s(12), 0))
        top = tk.Frame(self.status_card.body, bg=self.status_card.fill)
        top.pack(fill=tk.X, pady=(0, self.s(10)))
        self.status_icon = StatusIcon(top, self, bg=self.status_card.fill)
        self.status_icon.pack(side=tk.LEFT, padx=(0, self.s(10)))
        self.status_label = self.label(top, "Ready", 11, True, bg=self.status_card.fill)
        self.status_label.pack(side=tk.LEFT)
        self.percent_label = self.label(top, "", 11, True, PALETTE['cyan'], bg=self.status_card.fill)
        self.percent_label.pack(side=tk.RIGHT)
        self.detail_label = self.label(top, "", 9, color=PALETTE['muted'], bg=self.status_card.fill)
        self.detail_label.pack(side=tk.RIGHT, padx=(0, self.s(12)))
        self.progress = ProgressBar(self.status_card.body, self, bg=self.status_card.fill)
        self.progress.pack(fill=tk.X)
        self.note_label = self.label(self.status_card.body, "", 9, color=PALETTE['muted'], bg=self.status_card.fill,
                                     wraplength=self.s(580), justify=tk.LEFT)

        warn = Card(page, self, fill=mix(PALETTE['card'], PALETTE['danger'], 0.10),
                    border=mix(PALETTE['border'], PALETTE['danger'], 0.45), pad_y=10)
        warn.pack(fill=tk.X, pady=(self.s(12), 0))
        self.label(warn.body, "Everything on the selected USB drive will be erased.", 9, True, PALETTE['danger'],
                   bg=warn.fill).pack(anchor=tk.W)

        self.log_toggle = FancyButton(page, self, "Show details", self.toggle_log, 'ghost', height=28, size=9, bg=PALETTE['bg'])
        self.log_toggle.pack(anchor=tk.W, pady=(self.s(6), 0))
        self.log_frame = tk.Frame(page, bg=PALETTE['bg'])
        self.log_text = tk.Text(self.log_frame, height=7, bg=PALETTE['card'], fg=PALETTE['muted'], relief=tk.FLAT,
                                wrap=tk.WORD, state=tk.DISABLED, padx=10, pady=8, highlightthickness=1,
                                highlightbackground=PALETTE['border'],
                                font=('Consolas' if IS_WINDOWS else 'monospace', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.refresh_flash_button()

    def fit_window(self):
        """Grow (never shrink) the window so everything is visible, within the screen."""
        self.root.update_idletasks()
        need = self.root.winfo_reqheight() + self.s(16)
        limit = self.root.winfo_screenheight() - self.s(60)
        height = min(max(need, self.root.winfo_height()), limit)
        if height > self.root.winfo_height():
            self.root.geometry(f"{self.root.winfo_width()}x{height}")

    def refresh_flash_button(self):
        """Only offer the big button once there is an ISO and a drive to use."""
        if self.is_flashing or not hasattr(self, 'flash_button'):
            return
        path = self.iso_path.get().strip().strip('"')
        ready = os.path.isfile(path) and self.current_drive() is not None
        self.flash_button.set_enabled(ready)
        self.flash_button.set_text("Flash LinuxTV to USB" if ready else "Choose an ISO and a USB drive to continue")

    def toggle_log(self):
        self.log_visible = not self.log_visible
        grow = self.s(170)
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if self.log_visible:
            self.log_frame.pack(fill=tk.BOTH, expand=True, pady=(self.s(6), 0))
            self.log_toggle.set_text("Hide details")
            self.root.geometry(f"{width}x{height + grow}")
        else:
            self.log_frame.pack_forget()
            self.log_toggle.set_text("Show details")
            self.root.geometry(f"{width}x{max(height - grow, self.s(720))}")

    def log(self, message):
        print(message)

        def append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, append)

    def restart_as_admin(self):
        if relaunch_as_admin():
            self.root.destroy()
        else:
            messagebox.showerror("Administrator Rights Required",
                                 "Could not restart with administrator rights.\n\n"
                                 "Right-click the tool and choose 'Run as administrator'.")

    def browse_iso(self):
        filename = filedialog.askopenfilename(
            title="Select LinuxTV ISO",
            filetypes=[("ISO files", "*.iso"), ("All files", "*.*")]
        )
        if filename:
            self.iso_path.set(filename)

    def update_iso_info(self):
        path = self.iso_path.get().strip().strip('"')
        ok = bool(path) and os.path.isfile(path)
        if not path:
            self.iso_info.config(text="No file selected yet", fg=PALETTE['muted'])
        elif ok:
            self.iso_info.config(text=f"✓  {Path(path).name}  •  {format_size(os.path.getsize(path))}", fg=PALETTE['success'])
        else:
            self.iso_info.config(text="File not found", fg=PALETTE['danger'])
        self.iso_badge.set_done(ok)
        self.refresh_flash_button()

    # --- Drives ------------------------------------------------------------------------

    def select_drive(self, index):
        self.drive_index = index
        for i, (row, dot) in enumerate(self.drive_rows):
            chosen = i == index
            row.config(highlightbackground=PALETTE['accent'] if chosen else PALETTE['border'],
                       bg=PALETTE['field'] if chosen else self.drive_card.fill)
            stack = list(row.winfo_children())
            while stack:
                child = stack.pop()
                child.config(bg=row['bg'])
                stack.extend(child.winfo_children())
            dot.config(bg=row['bg'])
            dot.delete('all')
            dot.create_oval(2, 2, self.s(18), self.s(18), outline=PALETTE['accent'] if chosen else PALETTE['muted'], width=2)
            if chosen:
                dot.create_oval(self.s(6), self.s(6), self.s(14), self.s(14), fill=PALETTE['accent'], outline='')
        self.drive_badge.set_done(index >= 0)
        self.refresh_flash_button()

    def current_drive(self):
        if 0 <= self.drive_index < len(self.available_drives):
            return self.available_drives[self.drive_index]
        return None

    def render_drives(self):
        for child in self.drive_list.winfo_children():
            child.destroy()
        self.drive_rows = []
        for i, drive in enumerate(self.available_drives[:4]):
            row = tk.Frame(self.drive_list, bg=self.drive_card.fill, highlightthickness=1,
                           highlightbackground=PALETTE['border'], cursor='hand2')
            row.pack(fill=tk.X, pady=(0, self.s(6)), ipady=self.s(6), ipadx=self.s(8))
            dot = tk.Canvas(row, width=self.s(20), height=self.s(20), bg=row['bg'], highlightthickness=0)
            dot.pack(side=tk.LEFT, padx=(self.s(8), self.s(10)))
            glyph = tk.Canvas(row, width=self.s(26), height=self.s(28), bg=row['bg'], highlightthickness=0)
            glyph.pack(side=tk.LEFT, padx=(0, self.s(10)))
            g = self.s
            glyph.create_rectangle(g(8), g(1), g(18), g(8), fill=PALETTE['muted'], outline='')       # connector
            glyph.create_rectangle(g(10), g(3), g(12), g(5), fill=PALETTE['card'], outline='')
            glyph.create_rectangle(g(14), g(3), g(16), g(5), fill=PALETTE['card'], outline='')
            round_rect(glyph, g(5), g(8), g(21), g(27), g(3), fill=PALETTE['cyan'], outline='')       # stick body
            text = tk.Frame(row, bg=row['bg'])
            text.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(text, text=drive['name'], font=self.font(10, True), fg=PALETTE['text'], bg=row['bg'], anchor='w').pack(fill=tk.X)
            tk.Label(text, text=f"{drive['device']}", font=self.font(8), fg=PALETTE['muted'], bg=row['bg'], anchor='w').pack(fill=tk.X)
            size = tk.Label(row, text=drive['size'], font=self.font(10, True), fg=PALETTE['cyan'], bg=row['bg'])
            size.pack(side=tk.RIGHT, padx=(0, self.s(8)))
            self.drive_rows.append((row, dot))
            for widget in (row, dot, glyph, text, size, *text.winfo_children()):
                widget.bind('<Button-1>', lambda e, idx=i: self.select_drive(idx))
        if self.available_drives:
            self.select_drive(0)
            self.drive_info.config(text="Double-check this is the right drive before flashing.")
        else:
            self.select_drive(-1)
            hint = "No USB drives found. Plug one in and press Refresh."
            if IS_WINDOWS and not is_admin_windows():
                hint += " (Some drives may be hidden without administrator rights.)"
            self.drive_info.config(text=hint)
        self.root.after(120, self.fit_window)

    def refresh_drives(self):
        """Scan for drives in the background so the window never freezes."""
        if self.scanning:
            return
        self.scanning = True
        self.drive_info.config(text="Looking for USB drives...")

        def work():
            drives = self.get_available_drives()

            def done():
                self.scanning = False
                self.available_drives = drives
                self.render_drives()
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def get_available_drives(self):
        """Get available USB drives based on OS"""
        drives = []

        try:
            if IS_WINDOWS:
                data = powershell_json(
                    "Get-Disk | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem "
                    "-and $_.Size -gt 0 } | Select-Object Number, FriendlyName, Size, LogicalSectorSize | "
                    "ConvertTo-Json -Compress")
                if isinstance(data, dict):
                    data = [data]
                for disk in data or []:
                    drives.append({
                        'device': f"\\\\.\\PhysicalDrive{disk['Number']}",
                        'number': int(disk['Number']),
                        'name': (disk.get('FriendlyName') or 'USB Drive').strip(),
                        'size': format_size(int(disk.get('Size', 0))),
                        'bytes': int(disk.get('Size', 0)),
                        'sector': int(disk.get('LogicalSectorSize') or 512),
                    })

            elif sys.platform == 'darwin':
                # macOS - use diskutil
                result = subprocess.run(
                    ['diskutil', 'list', '-plist'],
                    capture_output=True,
                    timeout=10
                )
                if result.returncode == 0:
                    import plistlib
                    plist_data = plistlib.loads(result.stdout)
                    for disk in plist_data.get('AllDisksAndPartitions', []):
                        if disk.get('Internal') is False and disk.get('DeviceSize', 0) > 0:
                            device = disk.get('DeviceIdentifier', '')
                            if 'disk' in device:
                                drives.append({
                                    'device': f"/dev/{device.split('s')[0]}",
                                    'name': disk.get('MediaName', 'USB Drive'),
                                    'size': format_size(disk['DeviceSize'])
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
            self.log(f"Error getting drives: {e}")

        return drives


    # --- Flash flow ------------------------------------------------------------------

    def set_busy(self, busy):
        self.is_flashing = busy
        self.progress.busy = busy
        if busy:
            self.status_icon.set_state('busy')
            self.flash_button.set_enabled(False)
            self.flash_button.set_text("Flashing...")
        else:
            self.refresh_flash_button()
        if busy and IS_WINDOWS:
            self.cancel_button.pack(fill=tk.X, pady=(self.s(8), 0), after=self.flash_button)
            self.cancel_button.set_enabled(True)
        else:
            self.cancel_button.pack_forget()

    def start_flash(self):
        """Start the flashing process"""
        if self.is_flashing:
            return

        if IS_WINDOWS and not is_admin_windows():
            if messagebox.askyesno(
                    "Administrator Rights Required",
                    "Writing to a USB drive needs administrator rights.\n\n"
                    "Restart the tool as Administrator now?"):
                self.restart_as_admin()
            return

        iso_file = self.iso_path.get().strip().strip('"')
        if not iso_file:
            messagebox.showerror("Error", "Please choose an ISO file first.")
            return
        if not os.path.isfile(iso_file):
            messagebox.showerror("Error", "ISO file not found!")
            return

        drive = self.current_drive()
        if not drive:
            messagebox.showerror("Error", "Please choose a USB drive first.")
            return

        if IS_WINDOWS and drive.get('bytes') and os.path.getsize(iso_file) > drive['bytes']:
            messagebox.showerror("Error", f"The ISO ({format_size(os.path.getsize(iso_file))}) is larger than "
                                          f"the USB drive ({drive['size']}).")
            return

        confirm = messagebox.askyesno(
            "Confirm",
            f"This will ERASE ALL DATA on:\n\n"
            f"{drive['name']}  ({drive['size']})\n"
            f"{drive['device']}\n\n"
            f"Are you sure you want to continue?",
            icon='warning'
        )
        if not confirm:
            return

        self.cancel_requested = False
        self.set_busy(True)
        self.progress.set(0, PALETTE['accent'], instant=True)
        self.percent_label.config(text="")
        self.detail_label.config(text="")
        self.note_label.pack_forget()

        threading.Thread(target=self.flash_iso, args=(iso_file, drive), daemon=True).start()

    def cancel_flash(self):
        self.cancel_requested = True
        self.cancel_button.set_enabled(False)
        self.update_status("Cancelling...")

    def show_result(self, title, color, note=""):
        def apply():
            self.status_label.config(text=title, fg=color)
            self.status_icon.set_state({PALETTE['success']: 'success', PALETTE['danger']: 'error'}.get(color, 'warn'))
            if note:
                self.note_label.config(text=note)
                self.note_label.pack(anchor=tk.W, pady=(self.s(10), 0))
            self.root.after(120, self.fit_window)
        self.root.after(0, apply)

    def flash_iso(self, iso_file, drive):
        """Flash ISO to USB drive"""
        device = drive['device']
        try:
            self.update_status("Starting flash process...")
            self.update_progress(0)

            if IS_WINDOWS:
                self.flash_windows(iso_file, drive)
            elif sys.platform == 'darwin':
                self.flash_macos(iso_file, device)
            else:
                self.flash_linux(iso_file, device)

            self.update_progress(100, PALETTE['success'])
            self.update_detail("")
            note = ("You can now boot from this USB drive.\n\n"
                    "\u2022  The first time it starts, LinuxTV restarts once by itself to set up persistence. "
                    "This is normal - leave the USB stick plugged in.\n"
                    "\u2022  Tip: set the USB drive as the first boot option in your BIOS/UEFI so LinuxTV "
                    "starts automatically every time.")
            if IS_WINDOWS:
                note += ("\n\u2022  If Windows says the drive needs formatting, click Cancel - do not format it. "
                         "Then eject it safely.")
            self.show_result("LinuxTV is ready", PALETTE['success'], note)

        except FlashCancelled:
            self.update_progress(0)
            self.update_detail("")
            self.show_result("Cancelled", PALETTE['warn'], "The USB drive is incomplete - flash again before using it.")

        except Exception as e:
            error_message = str(e)
            self.log(f"Flash failed: {error_message}")
            self.update_progress(self.progress.target, PALETTE['danger'])
            self.update_detail("")
            self.show_result("Flash failed", PALETTE['danger'], error_message[-500:])
            self.root.after(0, lambda: messagebox.showerror("Error", f"Flash failed:\n{error_message}"))

        finally:
            self.root.after(0, lambda: self.set_busy(False))

    # --- Windows -----------------------------------------------------------------------

    def run_diskpart(self, script, timeout=120):
        fd, path = tempfile.mkstemp(suffix='.txt', prefix='linuxtv_diskpart_')
        try:
            with os.fdopen(fd, 'w') as fh:
                fh.write(script)
            result = subprocess.run(['diskpart', '/s', path], capture_output=True, text=True,
                                    errors='replace', timeout=timeout, creationflags=NO_WINDOW)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        self.log(result.stdout.strip())
        if result.returncode != 0:
            raise RuntimeError("diskpart failed:\n" + (result.stdout or result.stderr).strip()[-400:])
        return result

    def flash_windows(self, iso_file, drive):
        """Raw-write the hybrid ISO to the physical drive (equivalent of `dd`)."""
        number = drive['number']

        self.update_status("Checking the drive...")
        info = powershell_json(
            f"Get-Disk -Number {number} | Select-Object BusType, IsBoot, IsSystem, Size | ConvertTo-Json -Compress")
        if not info or str(info.get('BusType')) != 'USB' or info.get('IsBoot') or info.get('IsSystem'):
            raise RuntimeError("Refusing to write: the selected disk is no longer a removable USB drive. "
                               "Press Refresh and choose the drive again.")

        self.update_status("Preparing the drive...")
        self.update_progress(2)
        self.run_diskpart(f"select disk {number}\nattributes disk clear readonly noerr\nonline disk noerr\nclean\nexit\n")
        time.sleep(2)

        iso_total = os.path.getsize(iso_file)

        def on_progress(phase, fraction, speed):
            if phase == 'write':
                self.update_progress(5 + fraction * (70 if self.verify_write.get() else 90))
                self.update_status(f"Writing... {fraction * 100:.0f}%")
            else:
                self.update_progress(75 + fraction * 20)
                self.update_status(f"Verifying... {fraction * 100:.0f}%")
            eta = f"  \u2022  {format_eta((1 - fraction) * iso_total / speed)} left" if speed > 0 and fraction < 1 else ""
            self.update_detail(f"{speed / (1024 * 1024):.1f} MB/s{eta}")

        disk_bytes = {}

        def on_open(fd, iso_size):
            _win_allow_extended_io(fd)
            disk_bytes['size'] = _win_disk_length(fd)
            if iso_size > disk_bytes['size']:
                raise RuntimeError("The ISO is larger than the USB drive.")

        self.log(f"Writing {iso_file} to {drive['device']}")
        write_image(iso_file, drive['device'], on_progress, lambda: self.cancel_requested,
                    verify=self.verify_write.get(), fd_hook=on_open, log=self.log)

        self.update_detail("")
        self.update_status("Finishing up...")
        self.update_progress(96)
        self.create_persistence_windows(drive, disk_bytes.get('size', drive.get('bytes', 0)))

        self.update_progress(100)

    def create_persistence_windows(self, drive, total_bytes):
        """Add partition 3 to the MBR in place. Best effort - first boot creates it otherwise."""
        try:
            if drive.get('sector', 512) != 512:
                self.log("Skipping persistence: drive does not use 512-byte sectors (first boot will create it).")
                return
            with open(drive['device'], 'r+b', buffering=0) as dev:
                _win_allow_extended_io(dev.fileno())
                block = dev.read(4096)
                patched = patch_mbr_persistence(block[:512], total_bytes)
                if patched is None:
                    self.log("Skipping persistence at flash time (first boot will create it).")
                    return
                dev.seek(0)
                dev.write(patched + block[512:4096])
                try:
                    os.fsync(dev.fileno())
                except OSError:
                    pass
            self.log("Persistence partition added to the partition table.")
        except Exception as exc:
            self.log(f"Persistence setup skipped: {exc} (first boot will create it).")

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
        self.update_status("Finishing up...")

        if sys.platform == 'darwin':
            self.create_persistence_macos(device)
        else:
            self.create_persistence_linux(device)

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
        self.update_status("Finishing up...")
        
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
            self.update_status("Finishing up...")
        else:
            self.update_status("Finishing up...")
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
        
        self.update_status("Finishing up...")
        
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
            if "Warning" not in process.stderr:
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
            self.update_status("Finishing up...")
            time.sleep(1)
        else:
            print(f"Warning: Partition verification failed. Partitions found: {partitions}")
            print(f"Looking for: {partition3}")
            self.update_status("Partition created (verify manually with lsblk)")
            time.sleep(1)

    # --- Thread-safe UI updates --------------------------------------------------------

    def update_status(self, message):
        """Update status label"""
        self.root.after(0, lambda: self.status_label.config(text=message, fg=PALETTE['text']))

    def update_detail(self, message):
        self.root.after(0, lambda: self.detail_label.config(text=message))

    def update_progress(self, value, color=None):
        """Update progress bar"""
        def apply():
            self.progress.set(value, color or PALETTE['accent'])
            self.percent_label.config(text=f"{value:.0f}%" if 0 < value < 100 else "")
        self.root.after(0, apply)


def enable_hidpi():
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LinuxTV.FlashTool')
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    if IS_WINDOWS and not is_admin_windows() and '--no-elevate' not in sys.argv:
        if relaunch_as_admin():
            return
    enable_hidpi()
    root = tk.Tk()
    LinuxTVFlashTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
