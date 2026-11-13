# Plugin system

Every plugin is versioned and declares an ID, version, entrypoint, capabilities, dependencies, input/output contract versions, and least-privilege permissions.

Lifecycle: discover -> validate manifest -> resolve dependencies -> validate permissions -> test -> enable -> monitor -> upgrade or rollback.

## Implemented foundation

- YAML manifests are parsed with `yaml.safe_load`; arbitrary YAML object construction is not allowed.
- IDs, semantic versions, entrypoint syntax, required fields, and permission/capability lists are validated.
- Duplicate plugin IDs are rejected.
- Discovery registers metadata only; it never imports or executes plugin code.
- Activation requires explicit approval and grants for every declared permission.
- Deactivation is supported by the in-memory lifecycle controller.

## Security boundary

The current registry is an in-process foundation, not yet a production authorization system. Active state is not persisted across runs. Plugin code execution is intentionally not implemented. Before executing third-party or independently developed plugins, add isolated workers, signed/version-pinned artifacts, resource limits, audit events, and an administrator-controlled permission policy.

Plugins are never auto-executed merely because they are discovered. Core interfaces remain stable and specialist logic stays outside the orchestrator.
