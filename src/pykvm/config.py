"""
Runtime configuration (CLI + defaults).
"""

from dataclasses import dataclass, field


DEFAULT_PORT = 5900
DEFAULT_HOST = "0.0.0.0"
# Modifier keys held while pressing a digit to switch slots.
# Default: KEY_LEFTCTRL (29) + KEY_LEFTMETA (125)
DEFAULT_SWITCH_MODS: frozenset[int] = frozenset({29, 125})


@dataclass
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    switch_mods: frozenset[int] = field(default_factory=lambda: DEFAULT_SWITCH_MODS)
    # Patterns matched against device names (case-insensitive substring) or
    # exact /dev/input/eventN paths (when the pattern starts with '/').
    ignore_devices: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ClientConfig:
    server_host: str = "127.0.0.1"
    server_port: int = DEFAULT_PORT
