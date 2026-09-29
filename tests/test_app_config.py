import app_config


def test_legacy_simple_hash_is_dropped_on_load():
    cfg = app_config.normalize_config(
        {"auth": {"username": "u", "password_hash": "h", "password_simple_hash": "unsalted"}}
    )
    assert "password_simple_hash" not in cfg["auth"]
    assert cfg["auth"]["username"] == "u"


def test_nested_defaults_are_preserved_when_partially_overridden():
    cfg = app_config.normalize_config({"websocket": {"port": 9999}})
    assert cfg["websocket"] == {**app_config.DEFAULT_CONFIG["websocket"], "port": 9999}


def test_wrong_types_fall_back_to_defaults():
    cfg = app_config.normalize_config({"native_apps": "oops", "auth": None})
    assert isinstance(cfg["native_apps"], list)
    assert isinstance(cfg["auth"], dict)


def test_normalize_does_not_mutate_defaults():
    app_config.normalize_config({"auth": {"username": "u"}})
    assert app_config.DEFAULT_CONFIG["auth"]["username"] == ""


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = app_config.normalize_config({"web_apps": [{"name": "X", "url": "https://x.test"}]})
    app_config.save_config(path, cfg)
    assert app_config.load_config(path)["web_apps"] == [{"name": "X", "url": "https://x.test"}]


def test_load_missing_file_gives_defaults(tmp_path):
    cfg = app_config.load_config(tmp_path / "nope.yaml")
    assert cfg["websocket"]["port"] == 8765
