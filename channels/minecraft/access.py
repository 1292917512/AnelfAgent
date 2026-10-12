"""Runtime player discovery and authorization for the Minecraft channel.

Model experience: this policy gates which inbound player messages reach the model;
it adds no prompt content or token overhead and does not alter the stable prefix/cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class MinecraftAccessPolicy:
    """Track visible players without baking a world-specific name into code."""

    owner: str = ""
    grants: set[str] = field(default_factory=set)
    online: set[str] = field(default_factory=set)
    last_seen: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().casefold()

    def configure(self, *, owner: str, grants: list[str]) -> None:
        self.owner = self._key(owner)
        self.grants = {self._key(name) for name in grants if name.strip()}

    def observe(self, players: list[str], *, now: float | None = None) -> None:
        timestamp = time() if now is None else now
        self.online = {self._key(name) for name in players if name.strip()}
        for name in self.online:
            self.last_seen[name] = timestamp

    def allow(self, username: str, *, auto_authorize_online: bool) -> bool:
        key = self._key(username)
        if not self.owner and not self.grants and not auto_authorize_online:
            return bool(key)
        return bool(key and (key == self.owner or key in self.grants or
                             (auto_authorize_online and key in self.online)))

    def role(self, username: str, *, auto_authorize_online: bool) -> str:
        key = self._key(username)
        if key == self.owner and key:
            return "owner"
        if key in self.grants:
            return "grant"
        if auto_authorize_online and key in self.online:
            return "online"
        return "unknown"
