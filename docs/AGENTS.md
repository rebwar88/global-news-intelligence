# Agent catalogue

The platform baseline is exactly 12 agents: one orchestrator and eleven specialists.

| ID | Agent | Responsibility |
|---|---|---|
| main-orchestrator | Main Orchestrator | Dispatch, scheduling, lifecycle and coordination |
| news-collection | News Collection Agent | Collect permitted news sources |
| source-discovery | Source Discovery Agent | Discover, evaluate and tier sources |
| story-intelligence | Story Intelligence Agent | Cross-language deduplication, clustering and story history |
| editorial-translation | Editorial & Translation Agent | Language processing, summaries and rewriting |
| sorani-qa | Kurdish Sorani QA Agent | Sorani language and meaning quality checks |
| social-trends | Social Trends Intelligence Agent | Detect and analyze social trends |
| social-media-discovery | Social Media Discovery Agent | Find relevant public social posts and eyewitness material |
| media-collection | Media Collection Agent | Collect media and provenance metadata |
| media-verification | Media Verification Agent | Assess context, provenance, manipulation clues and rights |
| fact-checking | Fact-Checking Agent | Evaluate claims against evidence and citations |
| delivery-sync | Delivery & Sync Agent | Secure, idempotent delivery to PayamNewsAggregator |

## Implementation status

The News Collection Agent has a tested RSS 2.0/Atom parser, an allowlisted HTTPS feed connector, bounded fetch limits, permission checks, and deterministic article IDs. Its manifest remains disabled until approved sources and durable storage are configured. The Source Discovery Agent now scores candidate publisher metadata, applies A/B/C tiers, and requires a recorded human approval before tier A. Its manifest remains disabled; live discovery connectors and durable candidate storage are not configured. Other specialist manifests remain metadata-only.

Trending status is not proof of truth. AI-only media checks are not conclusive; high-risk cases require human review.
