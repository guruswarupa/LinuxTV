"""App store backend: reads the curated catalog and installs through the
root helper. The UI never passes package names to root, only catalog ids."""
import json
import logging
import re
import subprocess
from pathlib import Path

HELPER = "/usr/local/sbin/linuxtv-store"
SYSTEM_CATALOG = Path("/etc/linuxtv/store-catalog.json")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def helper_available() -> bool:
    return Path(HELPER).exists()


def load_catalog() -> list:
    for path in (SYSTEM_CATALOG,):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            apps = data.get("apps", [])
            return [a for a in apps if ID_RE.match(str(a.get("id", ""))) and a.get("packages")]
        except FileNotFoundError:
            continue
        except Exception:
            logging.exception("Could not read store catalog %s", path)
    return []


def packages_installed(packages) -> bool:
    for pkg in packages:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or "install ok installed" not in result.stdout:
            return False
    return True


def list_store_apps() -> list:
    return [
        {
            "id": a["id"],
            "name": a.get("name", a["id"]),
            "description": a.get("description", ""),
            "installed": packages_installed(a["packages"]),
        }
        for a in load_catalog()
    ]


def install_app(app_id: str):
    """Returns (success, message)."""
    if not ID_RE.match(app_id or ""):
        return False, "Invalid app."
    if not helper_available():
        return False, "App store is not available on this system."
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER, "install", app_id],
            capture_output=True, text=True, check=False, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False, "Install timed out."
    if result.returncode != 0:
        logging.error("Store install %s failed: %s", app_id, result.stderr.strip())
        tail = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
        return False, f"Install failed: {tail}"
    return True, "Installed."
