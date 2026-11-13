from pathlib import Path

import pytest

from gni.registry import (
    DuplicatePluginError,
    PluginLoader,
    PluginManifest,
    PluginRegistry,
    PluginValidationError,
)

VALID_MANIFEST = {
    "id": "example-agent",
    "name": "Example Agent",
    "version": "1.0.0",
    "enabled": False,
    "description": "Test plugin",
    "entrypoint": "example_agent:run",
    "capabilities": ["example"],
    "permissions": ["example.read"],
    "contract_version": "1.0",
}


def test_manifest_validation():
    manifest = PluginManifest.from_mapping(VALID_MANIFEST)
    assert manifest.id == "example-agent"
    assert manifest.enabled is False


def test_invalid_manifest_id_is_rejected():
    invalid = {**VALID_MANIFEST, "id": "../unsafe"}
    with pytest.raises(PluginValidationError):
        PluginManifest.from_mapping(invalid)


def test_registry_rejects_duplicate_ids():
    manifest = PluginManifest.from_mapping(VALID_MANIFEST)
    registry = PluginRegistry()
    registry.register(manifest)
    with pytest.raises(DuplicatePluginError):
        registry.register(manifest)


def test_discovery_loads_manifests_without_activating(tmp_path: Path):
    agent_dir = tmp_path / "example-agent"
    agent_dir.mkdir()
    (agent_dir / "plugin.yaml").write_text(
        """id: example-agent
name: Example Agent
version: 1.0.0
enabled: false
entrypoint: example_agent:run
permissions:
  - example.read
""",
        encoding="utf-8",
    )
    loader = PluginLoader()
    issues = loader.discover(tmp_path)
    assert issues == ()
    assert len(loader.registry) == 1
    assert loader.is_active("example-agent") is False


def test_activation_requires_explicit_approval_and_grants():
    loader = PluginLoader()
    loader.registry.register(PluginManifest.from_mapping(VALID_MANIFEST))

    with pytest.raises(PermissionError):
        loader.activate(
            "example-agent",
            explicitly_approved=False,
            granted_permissions={"example.read"},
        )
    with pytest.raises(PermissionError):
        loader.activate(
            "example-agent",
            explicitly_approved=True,
            granted_permissions=set(),
        )

    loader.activate(
        "example-agent",
        explicitly_approved=True,
        granted_permissions={"example.read"},
    )
    assert loader.is_active("example-agent") is True
    loader.deactivate("example-agent")
    assert loader.is_active("example-agent") is False
