"""Authentication helpers for the phone-remote WebSocket.

Model: a phone proves knowledge of the desktop password (or, on a box with no
password yet, of the one-time pairing code shown on the TV) once, and is handed
a random per-device token. Only the SHA-256 of each token is stored, so a leaked
config.yaml does not let anyone impersonate a paired phone.
"""
import hashlib
import hmac
import secrets
import time

PBKDF2_ITERATIONS = 200_000
LEGACY_PBKDF2_ITERATIONS = 100_000
MAX_TOKENS = 20


def hash_password(password: str, salt: str = None, iterations: int = PBKDF2_ITERATIONS) -> tuple:
    """Return (hex_hash, salt) using PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return digest, salt


def auth_enabled(config) -> bool:
    auth = config.get("auth") or {}
    return bool(str(auth.get("username", "")).strip() and str(auth.get("password_hash", "")).strip())


def verify_password(config, username: str, password: str) -> bool:
    """Constant-time check of username + password. False when no auth is configured."""
    auth = config.get("auth") or {}
    expected_user = str(auth.get("username", "")).strip()
    expected_hash = str(auth.get("password_hash", "")).strip()
    salt = str(auth.get("password_salt", "")).strip()
    if not (expected_user and expected_hash and salt):
        return False
    iterations = int(auth.get("password_iterations") or LEGACY_PBKDF2_ITERATIONS)
    computed, _ = hash_password(password, salt, iterations)
    user_ok = hmac.compare_digest(username.strip().encode(), expected_user.encode())
    hash_ok = hmac.compare_digest(computed.encode(), expected_hash.encode())
    return user_ok and hash_ok


def new_credentials(username: str, password: str) -> dict:
    """Build the config['auth'] block for a fresh password. Existing tokens are revoked."""
    password_hash, salt = hash_password(password)
    return {
        "username": username,
        "password_hash": password_hash,
        "password_salt": salt,
        "password_iterations": PBKDF2_ITERATIONS,
        "tokens": [],
    }


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(config) -> str:
    """Create a device token, store its digest in config['auth'], and return the token."""
    auth = config.setdefault("auth", {})
    token = secrets.token_urlsafe(32)
    tokens = list(auth.get("tokens") or [])
    tokens.append(_token_digest(token))
    auth["tokens"] = tokens[-MAX_TOKENS:]
    return token


def verify_token(config, token: str) -> bool:
    if not token:
        return False
    digest = _token_digest(token)
    stored = (config.get("auth") or {}).get("tokens") or []
    # Compare against every entry so timing doesn't reveal which one matched.
    matched = False
    for entry in stored:
        if hmac.compare_digest(str(entry).encode(), digest.encode()):
            matched = True
    return matched


def new_pairing_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def verify_pairing_code(expected: str, provided: str) -> bool:
    return bool(expected) and hmac.compare_digest(expected.encode(), str(provided).strip().encode())


class RateLimiter:
    """Lock a key (client IP) out after `max_failures` failures within `window` seconds."""

    def __init__(self, max_failures: int = 5, window: float = 60.0, lockout: float = 60.0, clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._clock = clock
        self._failures = {}
        self._locked_until = {}

    def blocked(self, key) -> float:
        """Seconds remaining on the lockout, or 0 when the key may try."""
        remaining = self._locked_until.get(key, 0) - self._clock()
        if remaining > 0:
            return remaining
        self._locked_until.pop(key, None)
        return 0

    def record_failure(self, key) -> None:
        now = self._clock()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        recent.append(now)
        if len(recent) >= self.max_failures:
            self._locked_until[key] = now + self.lockout
            recent = []
        self._failures[key] = recent

    def record_success(self, key) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
