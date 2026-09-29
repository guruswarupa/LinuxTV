"""Icon lookup, normalisation and dominant-colour extraction."""

import configparser
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from qt_compat import QIcon, QImage, QPainter, QPixmap, Qt
from system_controls import split_command
from theme import THEME


def resource_path(relpath: str) -> Path:
    base = Path(__file__).parent
    return (base / relpath).expanduser().resolve()


def cache_dir() -> Path:
    return Path(os.getenv("XDG_CACHE_HOME", "~/.cache")).expanduser() / "linuxtv" / "icons"


def desktop_file_locations():
    return [
        Path.home() / ".local/share/applications",
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
    ]


def icon_search_locations():
    return [
        Path.home() / ".local/share/icons",
        Path.home() / ".icons",
        Path.home() / ".local/share/flatpak/exports/share/icons",
        Path("/usr/local/share/icons"),
        Path("/usr/share/icons/hicolor"),
        Path("/var/lib/flatpak/exports/share/icons/hicolor"),
        Path("/usr/share/pixmaps"),
    ]


def _visible_content_bounds(pixmap):
    """Return ((left, top, right, bottom) fractions of the full image, 0..1,
    avg_brightness 0..255) describing an icon's actual (non-transparent)
    art. Source icons vary wildly in how much internal padding they carry --
    some are a small mark on a mostly-transparent canvas, others (Spotify,
    Twitch, Crunchyroll, ...) are "full bleed" art that touches every edge.
    avg_brightness flags icons like HBO Max's all-black wordmark, which
    would otherwise be invisible against their own extracted card color.
    Scans a small downsampled copy since exact pixel precision isn't
    needed, just enough to tell full-bleed from padded."""
    probe = 48
    img = pixmap.scaled(probe, probe, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).toImage().convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    if w == 0 or h == 0:
        return 0.0, 0.0, 1.0, 1.0, 128.0

    left, top, right, bottom = w, h, -1, -1
    threshold = 24
    brightness_total = 0
    visible_count = 0
    for y in range(h):
        for x in range(w):
            pixel = img.pixel(x, y)
            if (pixel >> 24) & 0xFF > threshold:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
                r = (pixel >> 16) & 0xFF
                g = (pixel >> 8) & 0xFF
                b = pixel & 0xFF
                brightness_total += (r + g + b) / 3
                visible_count += 1

    avg_brightness = (brightness_total / visible_count) if visible_count else 128.0
    if right < left or bottom < top:
        return 0.0, 0.0, 1.0, 1.0, avg_brightness
    return left / w, top / h, (right + 1) / w, (bottom + 1) / h, avg_brightness


def normalized_icon_path(source_path: str, cache_key: str, size: int = 128):
    if not source_path:
        return ""

    icon_source = Path(source_path).expanduser()
    if not icon_source.exists():
        return ""

    normalized_dir = cache_dir() / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    # The size (and a version tag for the normalization algorithm itself)
    # is part of the cache key so a change here -- or a cache left over
    # from an older build -- can never collide with, and silently serve,
    # the wrong result.
    target_path = normalized_dir / f"{hashlib.sha1(f'{cache_key}:{size}:v7'.encode('utf-8')).hexdigest()}.png"
    if target_path.exists():
        return str(target_path)

    pixmap = QPixmap(str(icon_source))
    if pixmap.isNull():
        icon = QIcon(str(icon_source))
        pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        # Don't hand QML a raw, un-normalized file -- source icons range
        # from 68px to 1280px and aren't all square, so anything we
        # couldn't scale ourselves would render inconsistently (or get
        # stretched to fill its box instead of fitting inside it). Falling
        # back to no icon (the card's letter/color placeholder) is safer.
        return ""

    # Scale the *visible content* -- not the raw canvas, which may carry
    # a lot of transparent padding or none at all -- to a consistent
    # fraction of the final square, so every icon reads as roughly the
    # same visual size regardless of how its source art was padded.
    # Every icon now sits on its own fixed white squircle badge (see
    # AppCard.qml), so a monochrome-dark mark like HBO Max's is naturally
    # high-contrast as-is -- no need to recolor it.
    left, top, right, bottom, _avg_brightness = _visible_content_bounds(pixmap)

    # left/top/right/bottom are fractions of the source; convert the
    # content's span to actual source pixels before relating it to the
    # target canvas -- scaling by a fraction of the *source's own* size
    # says nothing about how that lands relative to `size`.
    content_span_px = max((right - left) * pixmap.width(), (bottom - top) * pixmap.height(), 1)
    target_fraction = 0.78
    overall_scale = (target_fraction * size) / content_span_px

    scaled = pixmap.scaled(
        max(1, round(pixmap.width() * overall_scale)),
        max(1, round(pixmap.height() * overall_scale)),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    # left/top/right/bottom are fractions, so they still locate the visible
    # content's center correctly in the rescaled pixmap's own coordinates.
    content_cx = (left + right) / 2 * scaled.width()
    content_cy = (top + bottom) / 2 * scaled.height()

    canvas = QPixmap(size, size)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawPixmap(round(size / 2 - content_cx), round(size / 2 - content_cy), scaled)
    painter.end()

    canvas.save(str(target_path), "PNG")
    return str(target_path)


def create_white_icon(icon_path: str, size: int = 96):
    """Create a white version of an icon by painting it with white color"""
    if not icon_path:
        return QIcon()
    
    icon_source = Path(icon_path).expanduser()
    if not icon_source.exists():
        return QIcon()
    
    pixmap = QPixmap(str(icon_source))
    if pixmap.isNull():
        return QIcon()
    
    # Create a new pixmap with the same size
    white_pixmap = QPixmap(pixmap.size())
    white_pixmap.fill(Qt.transparent)
    
    # Paint the original pixmap in white
    painter = QPainter(white_pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(white_pixmap.rect(), Qt.white)
    painter.end()
    
    return QIcon(white_pixmap)


def _icon_theme_size_score(path: Path) -> int:
    """Higher is better. System icon themes (e.g. hicolor) organize icons
    under size-named directories like '48x48' or '128x128', plus a
    'scalable' (vector, sharp at any size) tier -- a glob across the theme
    can just as easily land on a 16x16 icon as a 256x256 one, and the
    former gets visibly blurry/pixelated once upscaled onto a card."""
    for part in path.parts:
        if part == "scalable":
            return 100_000
        head, _, tail = part.partition("x")
        if head.isdigit() and tail.isdigit():
            return int(head)
    return 0


@lru_cache(maxsize=256)
def resolve_icon_name(icon_name: str):
    if not icon_name:
        return ""

    icon_path = Path(icon_name).expanduser()
    if icon_path.exists():
        return str(icon_path)

    for suffix in ("svg", "png", "xpm"):
        best_path = None
        best_score = -1
        for base_dir in icon_search_locations():
            if not base_dir.exists():
                continue
            direct_match = base_dir / f"{icon_name}.{suffix}"
            if direct_match.exists():
                return str(direct_match)
            for candidate in base_dir.glob(f"**/{icon_name}.{suffix}"):
                if not candidate.exists():
                    continue
                score = _icon_theme_size_score(candidate)
                if score > best_score:
                    best_score = score
                    best_path = candidate
        if best_path is not None:
            return str(best_path)
    return ""


def desktop_entry_for_command(command_text: str):
    parts = split_command(command_text)
    if not parts:
        return None

    if len(parts) >= 3 and parts[0] == "flatpak" and parts[1] == "run":
        flatpak_app_id = parts[2]
        for directory in desktop_file_locations():
            direct_match = directory / f"{flatpak_app_id}.desktop"
            if direct_match.exists():
                parser = configparser.ConfigParser(interpolation=None)
                try:
                    parser.read(direct_match, encoding="utf-8")
                except Exception:
                    continue
                if "Desktop Entry" in parser:
                    return parser["Desktop Entry"]

    executable = Path(parts[0]).name
    for directory in desktop_file_locations():
        if not directory.exists():
            continue
        for desktop_file in directory.glob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(desktop_file, encoding="utf-8")
            except Exception:
                continue
            if "Desktop Entry" not in parser:
                continue
            entry = parser["Desktop Entry"]
            exec_line = entry.get("Exec", "")
            if not exec_line:
                continue
            exec_parts = split_command(exec_line.replace("%u", "").replace("%U", "").replace("%f", "").replace("%F", ""))
            if not exec_parts:
                continue
            entry_exec = Path(exec_parts[0]).name
            if executable == entry_exec:
                return entry
            if len(parts) >= 3 and parts[0] == "flatpak" and parts[1] == "run" and parts[2] in exec_parts:
                return entry
    return None


def find_native_icon_source(app):
    """Same lookup order as resolve_native_icon, but returns the raw,
    un-normalized (source_path, cache_key) -- for callers (color/backdrop
    extraction) that need the icon's real, original pixels rather than the
    display-normalized variant, which may have been recolored (e.g. a
    near-black mark repainted white for visibility)."""
    app_name = app.get("name", "")

    if app_name:
        icon_name = app_name.lower().replace(" ", "") + ".png"
        icon_path = resource_path("icons/" + icon_name)
        if icon_path.exists():
            return str(icon_path), f"native-name:{icon_name}"

    configured_icon = app.get("icon", "")
    if configured_icon:
        path = resource_path(configured_icon)
        if path.exists():
            return str(path), f"native-config:{configured_icon}"

    entry = desktop_entry_for_command(app.get("cmd", ""))
    if entry:
        resolved = resolve_icon_name(entry.get("Icon", ""))
        if resolved:
            return resolved, f"native-entry:{app.get('cmd', '')}:{entry.get('Icon', '')}"

    return "", ""


def resolve_native_icon(app):
    source, cache_key = find_native_icon_source(app)
    if not source:
        return ""
    return normalized_icon_path(source, cache_key)


def find_web_icon_source(app):
    """Web-app counterpart to find_native_icon_source -- see its docstring."""
    app_name = app.get("name", "")

    if app_name:
        icon_name = app_name.lower().replace(" ", "").replace("+", "plus") + ".png"
        icon_path = resource_path("icons/" + icon_name)
        if icon_path.exists():
            return str(icon_path), f"web-name:{icon_name}"

    configured_icon = app.get("icon", "")
    if configured_icon:
        path = resource_path(configured_icon)
        if path.exists():
            return str(path), f"web-config:{configured_icon}"

    network_icon = resource_path("icons/network.png")
    if network_icon.exists():
        return str(network_icon), "web-fallback:network"

    return "", ""


def fetch_web_icon(app):
    source, cache_key = find_web_icon_source(app)
    if not source:
        return ""
    return normalized_icon_path(source, cache_key)


def dominant_color(icon_path: str) -> str:
    """Return a saturated '#rrggbb' average color for an icon, for use as a
    card's brand-color background before/instead of full artwork."""
    if not icon_path:
        return THEME["surface_alt"]

    source = Path(icon_path).expanduser()
    if not source.exists():
        return THEME["surface_alt"]

    cache_file = cache_dir() / "colors" / f"{hashlib.sha1(str(source).encode('utf-8')).hexdigest()}.txt"
    if cache_file.exists():
        cached = cache_file.read_text().strip()
        if cached:
            return cached

    image = QImage(str(source))
    if image.isNull():
        return THEME["surface_alt"]

    small = image.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)

    total_r = total_g = total_b = 0
    weight = 0
    for y in range(small.height()):
        for x in range(small.width()):
            pixel = small.pixel(x, y)
            alpha = (pixel >> 24) & 0xFF
            if alpha < 32:
                continue
            r = (pixel >> 16) & 0xFF
            g = (pixel >> 8) & 0xFF
            b = pixel & 0xFF
            channel_spread = max(r, g, b) - min(r, g, b)
            # Down-weight near-gray/near-white/near-black pixels so logo
            # backgrounds don't wash out the brand color.
            brightness = (r + g + b) / 3
            if channel_spread < 18 and (brightness < 24 or brightness > 232):
                sample_weight = 1
            else:
                sample_weight = 4 + channel_spread // 8
            total_r += r * sample_weight
            total_g += g * sample_weight
            total_b += b * sample_weight
            weight += sample_weight

    if weight == 0:
        result = THEME["surface_alt"]
    else:
        r_avg, g_avg, b_avg = total_r // weight, total_g // weight, total_b // weight
        # A monochrome brand mark (e.g. HBO Max's all-black wordmark) would
        # otherwise extract as pure black -- used as the card's own
        # background, that makes the icon sitting on top of it invisible.
        # Clamp into a band that's never confusable with the app's own
        # near-black background or a stark white block, preserving hue for
        # anything that already had one.
        brightness = (r_avg + g_avg + b_avg) / 3
        min_brightness, max_brightness = 40, 210
        if brightness < min_brightness:
            if brightness <= 0:
                r_avg = g_avg = b_avg = min_brightness
            else:
                scale = min_brightness / brightness
                r_avg = min(255, round(r_avg * scale))
                g_avg = min(255, round(g_avg * scale))
                b_avg = min(255, round(b_avg * scale))
        elif brightness > max_brightness:
            scale = max_brightness / brightness
            r_avg = round(r_avg * scale)
            g_avg = round(g_avg * scale)
            b_avg = round(b_avg * scale)
        result = "#%02x%02x%02x" % (r_avg, g_avg, b_avg)

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(result)
    except OSError:
        pass
    return result


def backdrop_image(icon_path: str, size: int = 480) -> str:
    """Return a path to a cheaply-blurred backdrop derived from an icon
    (downscale then smooth-upscale), for the ambient background."""
    if not icon_path:
        return ""

    source = Path(icon_path).expanduser()
    if not source.exists():
        return ""

    cache_file = cache_dir() / "backdrops" / f"{hashlib.sha1(str(source).encode('utf-8')).hexdigest()}.png"
    if cache_file.exists():
        return str(cache_file)

    image = QImage(str(source))
    if image.isNull():
        return ""

    tiny = image.scaled(16, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    blurred = tiny.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        blurred.save(str(cache_file), "PNG")
    except OSError:
        return ""
    return str(cache_file)
