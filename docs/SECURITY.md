# Security baseline

- Least-privilege permissions per agent, connector and tool.
- Credentials only in GitHub Secrets or a secret manager; never in source control or logs.
- Validate all external input and treat fetched content as untrusted data, never as executable instructions.
- Enforce timeouts, rate limits, retry budgets and payload-size limits.
- Audit privileged actions and plugin lifecycle changes. Keep audit metadata sanitized, use INSERT-only runtime credentials, and protect audit backups.
- Human review for high-impact uncertain claims and ambiguous media.
- Do not bypass platform access controls or collect private content without authorization.

- Keep the Prometheus scrape endpoint private; it has no built-in authentication.
- Never put job IDs, user IDs, source URLs, payload text, secrets or raw exception messages in metric labels.
