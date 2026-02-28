"""Unit tests for configuration dataclasses."""

import pytest
from evdev import ecodes

from pykvm.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SWITCH_MODS,
    ClientConfig,
    ServerConfig,
)


def test_server_defaults():
    cfg = ServerConfig()
    assert cfg.host == DEFAULT_HOST
    assert cfg.port == DEFAULT_PORT
    assert cfg.switch_mods == DEFAULT_SWITCH_MODS


def test_client_defaults():
    cfg = ClientConfig()
    assert cfg.server_port == DEFAULT_PORT
    assert cfg.server_host == "127.0.0.1"


def test_default_port_is_5900():
    assert DEFAULT_PORT == 5900


def test_default_host_is_all_interfaces():
    assert DEFAULT_HOST == "0.0.0.0"


def test_default_switch_mods_is_frozenset():
    assert isinstance(DEFAULT_SWITCH_MODS, frozenset)


def test_default_switch_mods_contains_lctrl_lmeta():
    assert ecodes.KEY_LEFTCTRL in DEFAULT_SWITCH_MODS
    assert ecodes.KEY_LEFTMETA in DEFAULT_SWITCH_MODS


def test_default_switch_mods_has_two_keys():
    assert len(DEFAULT_SWITCH_MODS) == 2


def test_custom_server_config():
    mods = frozenset({ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT})
    cfg = ServerConfig(host="127.0.0.1", port=5901, switch_mods=mods)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 5901
    assert cfg.switch_mods == mods


def test_custom_client_config():
    cfg = ClientConfig(server_host="192.168.1.10", server_port=5901)
    assert cfg.server_host == "192.168.1.10"
    assert cfg.server_port == 5901


def test_switch_mods_immutability():
    """DEFAULT_SWITCH_MODS must be a frozenset (not mutable set)."""
    with pytest.raises(AttributeError):
        DEFAULT_SWITCH_MODS.add(0)  # type: ignore[attr-defined]
