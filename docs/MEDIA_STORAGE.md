# Media storage

Cloudflare R2 is the initial object-storage provider. All access must go through a provider-neutral Storage Adapter interface; agents and workflows must not call R2 APIs directly.

The database stores media IDs, provider, bucket/container, object key, checksum, size, MIME type, timestamps, provenance, rights, and verification metadata. Binary objects remain in object storage.

Provider migration requires a migration tool: copy objects, verify checksums and sizes, record destination references, support resumable batches, and switch active provider only after verification. Configuration changes alone do not migrate existing objects. Preserve stable media IDs and logical object keys independently of provider URLs.
