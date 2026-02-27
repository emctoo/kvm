"""
Runtime configuration (CLI + defaults).
"""

from dataclasses import dataclass, field


DEFAULT_PORT = 5900
DEFAULT_HOST = "0.0.0.0"
# Hotkey to toggle between local and remote mode.
# Expressed as a frozenset of evdev key codes (EV_KEY codes).
# Default: Left-Ctrl + Left-Alt + Tab  (codes 29, 56, 15)
DEFAULT_HOTKEY: frozenset[int] = frozenset({29, 56, 15})


@dataclass
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    hotkey: frozenset[int] = field(default_factory=lambda: DEFAULT_HOTKEY)


@dataclass
class ClientConfig:
    server_host: str = "127.0.0.1"
    server_port: int = DEFAULT_PORT
