"""Bounded RSS 2.0 and Atom feed reader with an explicit source allowlist."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Protocol
from urllib import error
from urllib import request as urllib_request
from urllib.parse import urlsplit


_ATOM = "{http://www.w3.org/2005/Atom}"


def is_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class FeedSource:
    source_id: str
    name: str
    feed_url: str
    allowed_hosts: frozenset[str]
    approved: bool = False
    enabled: bool = False

    def __post_init__(self) -> None:
        if not self.source_id or len(self.source_id) > 63:
            raise ValueError("source_id must be a non-empty identifier.")
        if not self.name.strip():
            raise ValueError("Source name is required.")
        parsed = urlsplit(self.feed_url)
        if (
            not is_https_url(self.feed_url)
            or not parsed.hostname
            or parsed.hostname.lower() not in {host.lower() for host in self.allowed_hosts}
        ):
            raise ValueError("Feed URL must use HTTPS and an explicitly allowed host.")
        if not self.approved or not self.enabled:
            raise ValueError("Only approved and enabled sources may be configured.")


@dataclass(frozen=True, slots=True)
class FeedItem:
    title: str
    url: str
    summary: str = ""
    external_id: str = ""
    published_at: str | None = None


class FeedReader(Protocol):
    def fetch(self, source: FeedSource) -> tuple[FeedItem, ...]:
        """Fetch and parse one approved feed."""
        ...


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(value: str, *, limit: int = 4000) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = " ".join(parser.parts)
    except Exception:  # noqa: BLE001 - malformed publisher markup is untrusted input.
        text = value
    return " ".join(unescape(text).split())[:limit]


def normalize_published(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def parse_feed(document: bytes, *, max_items: int = 100) -> tuple[FeedItem, ...]:
    if max_items < 1:
        raise ValueError("max_items must be positive.")
    if len(document) > 2_000_000:
        raise ValueError("Feed document exceeds the configured size limit.")
    upper = document.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("Feed document contains a forbidden document declaration.")
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise ValueError("Feed document is not valid XML.") from exc

    items: list[FeedItem] = []
    if root.tag == "feed" or root.tag == f"{_ATOM}feed":
        entries = root.findall(f"{_ATOM}entry")
        for entry in entries[:max_items]:
            title = clean_text(entry.findtext(f"{_ATOM}title", ""))
            summary = clean_text(
                entry.findtext(f"{_ATOM}summary")
                or entry.findtext(f"{_ATOM}content")
                or ""
            )
            link = ""
            for candidate in entry.findall(f"{_ATOM}link"):
                if candidate.attrib.get("rel", "alternate") == "alternate":
                    link = candidate.attrib.get("href", "")
                    if link:
                        break
            external_id = entry.findtext(f"{_ATOM}id", "") or link
            published = (
                entry.findtext(f"{_ATOM}published")
                or entry.findtext(f"{_ATOM}updated")
            )
            if title and is_https_url(link):
                items.append(FeedItem(title, link, summary, external_id, normalize_published(published)))
        return tuple(items)

    channel = root.find("channel")
    if channel is None and root.tag == "channel":
        channel = root
    if channel is None:
        raise ValueError("Unsupported feed format; expected RSS 2.0 or Atom.")
    for entry in channel.findall("item")[:max_items]:
        title = clean_text(entry.findtext("title", ""))
        summary = clean_text(entry.findtext("description", ""))
        link = (entry.findtext("link") or "").strip()
        external_id = (entry.findtext("guid") or link).strip()
        published = entry.findtext("pubDate") or entry.findtext("{http://purl.org/dc/elements/1.1/}date")
        if title and link.startswith("https://"):
            items.append(FeedItem(title, link, summary, external_id, normalize_published(published)))
    return tuple(items)


def _assert_public_host(hostname: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("Feed host could not be resolved.") from exc
    if not addresses:
        raise ValueError("Feed host did not resolve to an address.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Feed host resolved to a non-public address.")


class RSSFeedClient:
    """Fetch public HTTPS feeds; redirects and non-public DNS targets are rejected."""

    def __init__(self, *, timeout_seconds: float = 10.0, max_bytes: int = 2_000_000, max_items: int = 100):
        if timeout_seconds <= 0 or max_bytes < 1 or max_items < 1:
            raise ValueError("Invalid RSS client limits.")
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._max_items = max_items
        self._opener = urllib_request.build_opener(
            urllib_request.HTTPSHandler(context=ssl.create_default_context()),
            _NoRedirect(),
        )

    def fetch(self, source: FeedSource) -> tuple[FeedItem, ...]:
        parsed = urlsplit(source.feed_url)
        if parsed.hostname is None or parsed.hostname.lower() not in {
            host.lower() for host in source.allowed_hosts
        }:
            raise ValueError("Feed host is not in the source allowlist.")
        _assert_public_host(parsed.hostname)
        http_request = urllib_request.Request(
            source.feed_url,
            headers={"User-Agent": "GlobalNewsIntelligence/0.1 (+RSS reader)", "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"},
        )
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise ValueError("Feed server returned an unsuccessful status.")
                final = urlsplit(response.geturl())
                if final.scheme != "https" or final.hostname != parsed.hostname:
                    raise ValueError("Feed response URL changed unexpectedly.")
                document = response.read(self._max_bytes + 1)
        except error.HTTPError as exc:
            raise ValueError(f"Feed request failed with HTTP status {exc.code}.") from exc
        except error.URLError as exc:
            raise ValueError("Feed request could not be completed.") from exc
        if len(document) > self._max_bytes:
            raise ValueError("Feed response exceeds the configured size limit.")
        return parse_feed(document, max_items=self._max_items)
