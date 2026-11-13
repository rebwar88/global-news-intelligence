# Plugin system

Every plugin is versioned and declares an ID, version, entrypoint, capabilities, dependencies, input/output contract versions, and least-privilege permissions.

Lifecycle: discover -> validate manifest -> resolve dependencies -> validate permissions -> test -> enable -> monitor -> upgrade or rollback.

Plugins are never auto-executed merely because they are discovered. Activation requires explicit administrative policy. Core interfaces remain stable and specialist logic stays outside the orchestrator.
