from __future__ import annotations

from dataclasses import dataclass

from gni.agents.news_collection import NewsCollectionAgent
from gni.connectors.rss import FeedItem, FeedSource, parse_feed
from gni.contracts.agent import AgentStatus


@dataclass
class FakeReader:
    items: tuple[FeedItem, ...] = ()
    fail: bool = False

    def fetch(self, source):
        if self.fail:
            raise TimeoutError("private connector detail")
        return self.items


def source(*, approved=True, enabled=True):
    return FeedSource(
        source_id="sample-news",
        name="Sample News",
        feed_url="https://feeds.example.org/rss.xml",
        allowed_hosts=frozenset({"feeds.example.org"}),
        approved=approved,
        enabled=enabled,
    )


def context(*permissions):
    return type("Context", (), {"permissions": frozenset(permissions)})()


def test_rss_parser_extracts_and_normalizes_items():
    document = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>  Headline &amp; Update </title>
      <link>https://news.example.org/story/1</link>
      <description><![CDATA[<p>Useful <b>summary</b></p>]]></description>
      <guid>story-1</guid>
      <pubDate>Sat, 14 Nov 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""
    items = parse_feed(document)
    assert len(items) == 1
    assert items[0].title == "Headline & Update"
    assert items[0].summary == "Useful summary"
    assert items[0].external_id == "story-1"
    assert items[0].published_at == "2026-11-14T12:00:00+00:00"


def test_atom_parser_supports_alternate_links():
    document = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom story</title><id>tag:example.org,2026:1</id>
      <link href="https://example.org/story/1" rel="alternate"/>
      <updated>2026-11-14T12:00:00Z</updated><summary>Summary</summary></entry>
    </feed>"""
    items = parse_feed(document)
    assert len(items) == 1
    assert items[0].url == "https://example.org/story/1"
    assert items[0].published_at == "2026-11-14T12:00:00+00:00"


def test_feed_parser_rejects_dtd_and_oversized_document():
    import pytest

    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>')
    with pytest.raises(ValueError):
        parse_feed(b"x" * 2_000_001)


def test_source_requires_explicit_approval_and_exact_https_host():
    import pytest

    with pytest.raises(ValueError):
        FeedSource("sample-news", "Sample", "https://feeds.example.org/rss", frozenset({"feeds.example.org"}))
    with pytest.raises(ValueError):
        FeedSource(
            "sample-news", "Sample", "http://feeds.example.org/rss",
            frozenset({"feeds.example.org"}), approved=True, enabled=True,
        )
    with pytest.raises(ValueError):
        FeedSource(
            "sample-news", "Sample", "https://evil.example/rss",
            frozenset({"feeds.example.org"}), approved=True, enabled=True,
        )


def test_agent_normalizes_items_and_uses_stable_ids():
    reader = FakeReader(items=(FeedItem(
        "A headline", "https://news.example.org/1", "<b>Summary</b>", "guid-1",
        "2026-11-14T12:00:00+00:00",
    ),))
    agent = NewsCollectionAgent("news-collection", {"sample-news": source()}, reader)
    result = agent.run(context("sources.read", "articles.create"), {"source_ids": ["sample-news"]})
    assert result.status == AgentStatus.SUCCEEDED
    article = result.output["articles"][0]
    assert article["title"] == "A headline"
    assert article["source_id"] == "sample-news"
    assert len(article["id"]) == 64
    assert result.output["partial"] is False


def test_agent_refuses_unapproved_sources_and_requires_permissions():
    agent = NewsCollectionAgent("news-collection", {"sample-news": source()}, FakeReader())
    denied = agent.run(context("sources.read"), {"source_ids": ["sample-news"]})
    assert denied.status == AgentStatus.FAILED
    rejected = agent.run(
        context("sources.read", "articles.create"), {"source_ids": ["not-configured"]}
    )
    assert rejected.status == AgentStatus.FAILED
    assert rejected.error_code == "source_not_approved"


def test_agent_isolates_source_failures_and_returns_partial_results():
    good = FeedItem("Headline", "https://news.example.org/1", external_id="story-1")
    agent = NewsCollectionAgent(
        "news-collection",
        {"sample-news": source(), "other-news": FeedSource(
            "other-news", "Other News", "https://other.example.org/feed",
            frozenset({"other.example.org"}), approved=True, enabled=True,
        )},
        FakeReader(items=(good,)),
    )

    class PerSourceReader:
        def fetch(self, configured_source):
            if configured_source.source_id == "other-news":
                raise TimeoutError("must not leak")
            return (good,)

    agent.reader = PerSourceReader()
    result = agent.run(
        context("sources.read", "articles.create"),
        {"source_ids": ["sample-news", "other-news"]},
    )
    assert result.status == AgentStatus.SUCCEEDED
    assert result.output["partial"] is True
    assert result.output["failed_source_ids"] == ["other-news"]
    assert "must not leak" not in str(result.output)


def test_agent_rejects_unbounded_or_duplicate_source_lists():
    agent = NewsCollectionAgent("news-collection", {"sample-news": source()}, FakeReader())
    for source_ids in ([], ["sample-news", "sample-news"], ["x"] * 11, "sample-news"):
        result = agent.run(
            context("sources.read", "articles.create"), {"source_ids": source_ids}
        )
        assert result.status == AgentStatus.FAILED

def test_rss_http_client_fetches_allowlisted_https_feed_without_redirects(monkeypatch):
    import gni.connectors.rss as rss

    body = b'<rss version="2.0"><channel><item><title>One</title><link>https://news.example.org/1</link></item></channel></rss>'

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def geturl(self):
            return "https://feeds.example.org/rss.xml"

        def read(self, limit):
            assert limit > len(body)
            return body

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://feeds.example.org/rss.xml"
            assert timeout == 3
            return Response()

    monkeypatch.setattr(rss, "_assert_public_host", lambda hostname: None)
    client = rss.RSSFeedClient(timeout_seconds=3, max_bytes=1024)
    client._opener = Opener()
    items = client.fetch(source())
    assert len(items) == 1
    assert items[0].title == "One"


def test_rss_http_client_rejects_oversized_response(monkeypatch):
    import pytest
    import gni.connectors.rss as rss

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def geturl(self):
            return "https://feeds.example.org/rss.xml"

        def read(self, limit):
            return b"x" * limit

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(rss, "_assert_public_host", lambda hostname: None)
    client = rss.RSSFeedClient(max_bytes=32)
    client._opener = Opener()
    with pytest.raises(ValueError, match="size limit"):
        client.fetch(source())
