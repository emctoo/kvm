"""Unit tests for configuration dataclasses."""

import pytest
from evdev import ecodes

from pykvm.config import (
    DEFAULT_HOST,
    DEFAULT_HOTKEY,
    DEFAULT_PORT,
    ClientConfig,
    ServerConfig,
)


def test_server_defaults():
    cfg = ServerConfig()
    assert cfg.host == DEFAULT_HOST
    assert cfg.port == DEFAULT_PORT
    assert cfg.hotkey == DEFAULT_HOTKEY


def test_client_defaults():
    cfg = ClientConfig()
    assert cfg.server_port == DEFAULT_PORT
    assert cfg.server_host == "127.0.0.1"


def test_default_port_is_5900():
    assert DEFAULT_PORT == 5900


def test_default_host_is_all_interfaces():
    assert DEFAULT_HOST == "0.0.0.0"


def test_default_hotkey_is_frozenset():
    assert isinstance(DEFAULT_HOTKEY, frozenset)


def test_default_hotkey_contains_lctrl_lalt_tab():
    assert ecodes.KEY_LEFTCTRL in DEFAULT_HOTKEY
    assert ecodes.KEY_LEFTALT in DEFAULT_HOTKEY
    assert ecodes.KEY_TAB in DEFAULT_HOTKEY


def test_default_hotkey_has_three_keys():
    assert len(DEFAULT_HOTKEY) == 3


def test_custom_server_config():
    hotkey = frozenset({ecodes.KEY_F1, ecodes.KEY_F2})
    cfg = ServerConfig(host="127.0.0.1", port=5901, hotkey=hotkey)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 5901
    assert cfg.hotkey == hotkey


def test_custom_client_config():
    cfg = ClientConfig(server_host="192.168.1.10", server_port=5901)
    assert cfg.server_host == "192.168.1.10"
    assert cfg.server_port == 5901


def test_hotkey_immutability():
    """DEFAULT_HOTKEY must be a frozenset (not mutable set)."""
    with pytest.raises(AttributeError):
        DEFAULT_HOTKEY.add(0)  # type: ignore[attr-defined]
