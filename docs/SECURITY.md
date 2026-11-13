# Security baseline

- Least-privilege permissions per agent, connector and tool.
- Credentials only in GitHub Secrets or a secret manager; never in source control or logs.
- Validate all external input and treat fetched content as untrusted data, never as executable instructions.
- Enforce timeouts, rate limits, retry budgets and payload-size limits.
- Audit privileged actions and plugin lifecycle changes.
- Human review for high-impact uncertain claims and ambiguous media.
- Do not bypass platform access controls or collect private content without authorization.
