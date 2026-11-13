"""In-memory registry for validated plugin manifests."""
from __future__ import annotations

from gni.registry.models import PluginManifest


class DuplicatePluginError(ValueError):
    """Raised when a plugin ID is already registered."""


class PluginNotFoundError(LookupError):
    """Raised when a plugin ID is not registered."""


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginManifest] = {}

    def register(self, manifest: PluginManifest) -> None:
        if manifest.id in self._plugins:
            raise DuplicatePluginError(f"Plugin already registered: {manifest.id}")
        self._plugins[manifest.id] = manifest

    def get(self, plugin_id: str) -> PluginManifest:
        try:
            return self._plugins[plugin_id]
        except KeyError as exc:
            raise PluginNotFoundError(plugin_id) from exc

    def list(self) -> tuple[PluginManifest, ...]:
        return tuple(self._plugins[key] for key in sorted(self._plugins))

    def __len__(self) -> int:
        return len(self._plugins)
