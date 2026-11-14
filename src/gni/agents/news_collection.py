"""News Collection Agent: read approved feeds and normalize article records."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gni.connectors.rss import FeedReader, FeedSource


@dataclass(slots=True)
class NewsCollectionAgent:
    agent_id: str
    sources: dict[str, FeedSource]
    reader: FeedReader
    max_sources_per_job: int = 10
    max_items_per_source: int = 50

    def __post_init__(self) -> None:
        if self.agent_id != "news-collection":
            raise ValueError("NewsCollectionAgent must use the news-collection ID.")
        if self.max_sources_per_job < 1 or self.max_items_per_source < 1:
            raise ValueError("Collection limits must be positive.")
        if any(key != source.source_id for key, source in self.sources.items()):
            raise ValueError("Source mapping keys must match configured source IDs.")

    def run(self, context, payload: dict[str, Any]):
        from gni.contracts.agent import AgentResult, AgentStatus

        if "sources.read" not in context.permissions or "articles.create" not in context.permissions:
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="permission_denied",
                message="News collection permissions were not granted.",
            )
        source_ids = payload.get("source_ids")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or len(source_ids) > self.max_sources_per_job
            or any(not isinstance(item, str) for item in source_ids)
            or len(source_ids) != len(set(source_ids))
        ):
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="invalid_source_selection",
                message="Provide a bounded list of unique configured source IDs.",
            )

        articles: list[dict[str, Any]] = []
        source_results: list[dict[str, Any]] = []
        failures: list[str] = []
        rejected: list[str] = []
        fetched_at = datetime.now(UTC).isoformat()

        for source_id in source_ids:
            source = self.sources.get(source_id)
            if source is None or not source.approved or not source.enabled:
                rejected.append(source_id)
                source_results.append({"source_id": source_id, "status": "rejected"})
                continue
            try:
                items = self.reader.fetch(source)
            except Exception:  # noqa: BLE001 - connector failures are isolated per source.
                failures.append(source_id)
                source_results.append({"source_id": source_id, "status": "fetch_failed"})
                continue

            accepted = 0
            for item in items[: self.max_items_per_source]:
                if not item.title.strip() or not item.url.startswith("https://"):
                    continue
                stable_key = item.external_id.strip() or item.url
                article_id = hashlib.sha256(
                    f"{source_id}\0{stable_key}".encode("utf-8")
                ).hexdigest()
                articles.append(
                    {
                        "id": article_id,
                        "source_id": source.source_id,
                        "source_name": source.name,
                        "title": item.title[:500],
                        "url": item.url[:2048],
                        "summary": item.summary[:4000],
                        "external_id": stable_key[:2048],
                        "published_at": item.published_at,
                        "fetched_at": fetched_at,
                    }
                )
                accepted += 1
            source_results.append(
                {"source_id": source_id, "status": "collected", "items": accepted}
            )

        if failures and not articles:
            return AgentResult(
                status=AgentStatus.RETRYABLE,
                error_code="source_fetch_failed",
                message="No articles were collected; retry the failed source requests.",
            )
        if rejected and not articles and not failures:
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="source_not_approved",
                message="One or more requested sources are not configured and approved.",
            )
        return AgentResult(
            status=AgentStatus.SUCCEEDED,
            output={
                "schema_version": "1.0",
                "articles": articles,
                "source_results": source_results,
                "failed_source_ids": failures,
                "rejected_source_ids": rejected,
                "partial": bool(failures or rejected),
            },
        )
