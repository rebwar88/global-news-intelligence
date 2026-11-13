"""Validated plugin metadata models."""
from __future__ import annotations

import re
from dataclasses import dataclass

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
_ENTRYPOINT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$")


class PluginValidationError(ValueError):
    """Raised when a plugin manifest is invalid."""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    name: str
    version: str
    enabled: bool
    description: str
    entrypoint: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    contract_version: str

    @classmethod
    def from_mapping(cls, data: object) -> PluginManifest:
        if not isinstance(data, dict):
            raise PluginValidationError("Manifest root must be a mapping.")

        required = ("id", "name", "version", "enabled", "entrypoint")
        missing = [key for key in required if key not in data]
        if missing:
            raise PluginValidationError(f"Missing required fields: {', '.join(missing)}")

        plugin_id = data["id"]
        name = data["name"]
        version = data["version"]
        enabled = data["enabled"]
        entrypoint = data["entrypoint"]

        if not isinstance(plugin_id, str) or not _PLUGIN_ID.fullmatch(plugin_id):
            raise PluginValidationError("Invalid plugin id.")
        if not isinstance(name, str) or not name.strip():
            raise PluginValidationError("Plugin name must be a non-empty string.")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise PluginValidationError("Plugin version must use semantic versioning.")
        if not isinstance(enabled, bool):
            raise PluginValidationError("Plugin enabled must be a boolean.")
        if not isinstance(entrypoint, str) or not _ENTRYPOINT.fullmatch(entrypoint):
            raise PluginValidationError("Entrypoint must use 'module:callable' syntax.")

        description = data.get("description", "")
        contract_version = data.get("contract_version", "1.0")
        if not isinstance(description, str) or not isinstance(contract_version, str):
            raise PluginValidationError("Description and contract_version must be strings.")

        capabilities = cls._string_tuple(data.get("capabilities", []), "capabilities")
        permissions = cls._string_tuple(data.get("permissions", []), "permissions")

        return cls(
            id=plugin_id,
            name=name.strip(),
            version=version,
            enabled=enabled,
            description=description,
            entrypoint=entrypoint,
            capabilities=capabilities,
            permissions=permissions,
            contract_version=contract_version,
        )

    @staticmethod
    def _string_tuple(value: object, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise PluginValidationError(f"{field} must be a list of non-empty strings.")
        if len(value) != len(set(value)):
            raise PluginValidationError(f"{field} must not contain duplicates.")
        return tuple(value)
