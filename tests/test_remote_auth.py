import remote_auth as ra


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_config(username="bob", password="correct horse"):
    return {"auth": ra.new_credentials(username, password)}


def test_password_roundtrip():
    cfg = make_config()
    assert ra.verify_password(cfg, "bob", "correct horse")


def test_wrong_password_or_user_rejected():
    cfg = make_config()
    assert not ra.verify_password(cfg, "bob", "wrong")
    assert not ra.verify_password(cfg, "eve", "correct horse")


def test_no_credentials_never_verifies():
    assert not ra.verify_password({}, "", "")
    assert not ra.verify_password({"auth": {"username": "bob"}}, "bob", "x")
    assert not ra.auth_enabled({"auth": {"username": "bob"}})


def test_legacy_hash_without_iterations_field_still_verifies():
    h, salt = ra.hash_password("pw", iterations=ra.LEGACY_PBKDF2_ITERATIONS)
    cfg = {"auth": {"username": "u", "password_hash": h, "password_salt": salt}}
    assert ra.verify_password(cfg, "u", "pw")


def test_new_credentials_revoke_tokens_and_store_no_plaintext():
    cfg = make_config()
    token = ra.issue_token(cfg)
    cfg["auth"] = ra.new_credentials("bob", "another password")
    assert not ra.verify_token(cfg, token)
    assert "another password" not in str(cfg)


def test_token_stored_only_as_digest():
    cfg = make_config()
    token = ra.issue_token(cfg)
    assert token not in str(cfg)
    assert ra.verify_token(cfg, token)
    assert not ra.verify_token(cfg, token + "x")
    assert not ra.verify_token(cfg, "")


def test_token_list_is_capped():
    cfg = make_config()
    first = ra.issue_token(cfg)
    for _ in range(ra.MAX_TOKENS):
        ra.issue_token(cfg)
    assert len(cfg["auth"]["tokens"]) == ra.MAX_TOKENS
    assert not ra.verify_token(cfg, first)


def test_rate_limiter_locks_and_recovers():
    clock = Clock()
    rl = ra.RateLimiter(max_failures=3, window=60, lockout=60, clock=clock)
    for _ in range(3):
        assert rl.blocked("ip") == 0
        rl.record_failure("ip")
    assert rl.blocked("ip") > 0
    assert rl.blocked("other") == 0
    clock.now = 61
    assert rl.blocked("ip") == 0


def test_rate_limiter_forgets_old_failures():
    clock = Clock()
    rl = ra.RateLimiter(max_failures=3, window=60, lockout=60, clock=clock)
    rl.record_failure("ip")
    rl.record_failure("ip")
    clock.now = 100
    rl.record_failure("ip")
    assert rl.blocked("ip") == 0


def test_rate_limiter_success_resets():
    rl = ra.RateLimiter(max_failures=2)
    rl.record_failure("ip")
    rl.record_success("ip")
    rl.record_failure("ip")
    assert rl.blocked("ip") == 0


# --- AuthGate: the full message flows -------------------------------------

def make_gate(config):
    saved = []
    gate = ra.AuthGate(config, persist=lambda: saved.append(True))
    return gate, saved


def test_password_login_issues_token_and_persists():
    cfg = make_config()
    gate, saved = make_gate(cfg)
    reply, granted = gate.handle("1.1.1.1", "auth", {"username": "bob", "password": "correct horse"})
    assert granted and reply["status"] == "auth_ok"
    assert saved
    assert ra.verify_token(cfg, reply["token"])
    reply2, granted2 = gate.handle("1.1.1.1", "auth_token", {"token": reply["token"]})
    assert granted2 and "token" not in reply2


def test_bad_password_denied_then_locked_out():
    gate, _ = make_gate(make_config())
    for _ in range(5):
        reply, granted = gate.handle("1.1.1.1", "auth", {"username": "bob", "password": "nope"})
        assert not granted and reply["error"] == "invalid credentials"
    reply, granted = gate.handle("1.1.1.1", "auth", {"username": "bob", "password": "correct horse"})
    assert not granted and "too many attempts" in reply["error"]
    # a different client is unaffected
    _, granted = gate.handle("2.2.2.2", "auth", {"username": "bob", "password": "correct horse"})
    assert granted


def test_bad_token_denied():
    gate, _ = make_gate(make_config())
    reply, granted = gate.handle("1.1.1.1", "auth_token", {"token": "forged"})
    assert not granted and reply["status"] == "auth_error"


def test_unconfigured_box_requires_pairing_code():
    cfg = {"auth": {}}
    gate, saved = make_gate(cfg)
    assert gate.required() == {"status": "auth_required", "mode": "pairing"}
    reply, granted = gate.handle("1.1.1.1", "pair", {"code": "000000x"})
    assert not granted
    code = gate.pairing_code
    reply, granted = gate.handle("1.1.1.1", "pair", {"code": code})
    assert granted and ra.verify_token(cfg, reply["token"]) and saved
    assert gate.pairing_code != code  # single use


def test_password_login_on_unconfigured_box_is_redirected_not_counted():
    gate, _ = make_gate({"auth": {}})
    for _ in range(10):
        reply, granted = gate.handle("1.1.1.1", "auth", {"username": "", "password": ""})
        assert not granted and reply == {"status": "auth_required", "mode": "pairing"}
    assert gate.limiter.blocked("1.1.1.1") == 0


def test_pairing_code_useless_once_password_set():
    gate, _ = make_gate(make_config())
    reply, granted = gate.handle("1.1.1.1", "pair", {"code": gate.pairing_code})
    assert not granted and reply == {"status": "auth_required", "mode": "password"}


def test_required_reports_mode():
    assert make_gate(make_config())[0].required()["mode"] == "password"
