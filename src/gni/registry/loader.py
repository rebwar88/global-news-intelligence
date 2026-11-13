"""Safe manifest discovery and explicit plugin activation.

This loader parses metadata only. It deliberately does not import or execute
plugin entrypoints; execution belongs in a separately permissioned worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from gni.registry.models import PluginManifest, PluginValidationError
from gni.registry.registry import PluginRegistry


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    path: str
    error: str


class PluginLoader:
    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self.registry = registry or PluginRegistry()
        self._active: set[str] = set()

    def discover(self, agents_root: Path) -> tuple[DiscoveryIssue, ...]:
        """Discover and register manifests without executing plugin code."""
        issues: list[DiscoveryIssue] = []
        if not agents_root.exists():
            return (DiscoveryIssue(str(agents_root), "Agents directory does not exist."),)

        for manifest_path in sorted(agents_root.glob("*/plugin.yaml")):
            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifest = PluginManifest.from_mapping(raw)
                self.registry.register(manifest)
            except (OSError, yaml.YAMLError, PluginValidationError, ValueError) as exc:
                issues.append(DiscoveryIssue(str(manifest_path), str(exc)))
        return tuple(issues)

    def activate(
        self,
        plugin_id: str,
        *,
        explicitly_approved: bool,
        granted_permissions: set[str] | frozenset[str],
    ) -> PluginManifest:
        """Mark a plugin active only after explicit approval and permission checks."""
        manifest = self.registry.get(plugin_id)
        if not explicitly_approved:
            raise PermissionError("Explicit administrative approval is required.")
        missing = set(manifest.permissions) - set(granted_permissions)
        if missing:
            raise PermissionError(
                f"Missing permission grants for {plugin_id}: {', '.join(sorted(missing))}"
            )
        self._active.add(plugin_id)
        return manifest

    def deactivate(self, plugin_id: str) -> None:
        self.registry.get(plugin_id)
        self._active.discard(plugin_id)

    def is_active(self, plugin_id: str) -> bool:
        self.registry.get(plugin_id)
        return plugin_id in self._active
