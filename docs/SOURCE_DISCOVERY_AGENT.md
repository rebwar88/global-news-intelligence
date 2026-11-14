# Source Discovery Agent

The Source Discovery Agent evaluates candidate publisher metadata supplied by a trusted discovery connector. It does not scrape search engines itself, fetch arbitrary websites, or activate sources.

## Tiers and approval

- **A — approved:** a strong evidence score plus a recorded human approval reference.
- **B — candidate:** potentially useful; awaiting editorial review or missing an approval record.
- **C — rejected:** unsafe/invalid website URL or a low evidence score.

The deterministic score uses only supplied, inspectable facts: verified publisher identity, transparent ownership, editorial and corrections policies, contact information, original reporting, and independent reporting. A known copying network reduces the score and is explicitly flagged. Evidence URLs are retained only when they are public HTTPS URLs.

A high score is not proof of accuracy, impartiality, or trustworthiness. Popularity, reach, and social engagement are not scoring inputs. Candidate-provider metadata cannot self-approve a source: tier A requires a matching approval reference supplied separately through the agent's trusted approval registry/configuration. Human reviewers must inspect evidence and record that approval before a source can enter tier A or the News Collection Agent's allowlist.

## Execution and safety

The agent requires `sources.discover` and `sources.candidates.write`, accepts bounded region/topic filters, and caps candidate counts. Candidate discovery is injected through a trusted provider interface; job payloads cannot provide feed URLs or executable connector details. Provider exceptions are sanitized. This first implementation is a scoring and review queue foundation, not a live web crawler or an unattended source approval service.

## Initial coverage priorities

Use region filters to prioritize Kurdistan Region and Iraq, then the wider Middle East, while keeping global discovery available. Topic coverage should be configured by workflow rather than hard-coded into this agent.
