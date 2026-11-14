# News Collection Agent

## Responsibility

The first built-in specialist reads a bounded list of explicitly approved RSS 2.0 or Atom sources, normalizes their article metadata, and returns stable article identifiers for downstream story processing.

## Configuration and safety

- Source definitions are injected by trusted runtime configuration; a job payload can select source IDs but cannot supply or override feed URLs.
- Each source must be explicitly marked approved and enabled.
- Feed URLs must use HTTPS and an exact configured hostname allowlist.
- The RSS connector rejects redirects, non-public DNS destinations, oversized responses, DTD/entity declarations, and non-XML feeds.
- Network timeouts, response size, source count, and items per source are bounded.
- Only `sources.read` and `articles.create` are required. The agent rejects execution if either grant is missing.
- Connector errors are isolated per source. Partial success is returned with source IDs and status codes only; raw exceptions and feed bodies are not exposed.
- Article IDs are deterministic hashes of source ID plus the publisher's GUID (or article URL fallback), making re-ingestion idempotent downstream.

## Runtime composition

The agent implementation is injected; manifest entrypoints are not imported dynamically:

```python
from gni.agents.news_collection import NewsCollectionAgent
from gni.connectors.rss import FeedSource, RSSFeedClient

sources = {
    "approved-outlet": FeedSource(
        source_id="approved-outlet",
        name="Approved Outlet",
        feed_url="https://feeds.example.org/rss.xml",
        allowed_hosts=frozenset({"feeds.example.org"}),
        approved=True,
        enabled=True,
    )
}
agent = NewsCollectionAgent(
    agent_id="news-collection",
    sources=sources,
    reader=RSSFeedClient(timeout_seconds=10, max_items=50),
)
```

The example host is a placeholder, not a recommended or approved news source. Real sources must be researched, reviewed, and explicitly approved before configuration.

## Current phase limitation

This implementation parses and collects feeds when invoked by a trusted runtime. It does not yet persist articles or schedule unattended collection. GitHub-hosted runners are ephemeral, and the project must not treat workflow artifacts or runner files as the durable article database. Until a durable store is funded and configured, keep this agent disabled for production collection and use fixture-based CI tests only.
