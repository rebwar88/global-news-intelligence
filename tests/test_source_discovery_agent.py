from __future__ import annotations

from dataclasses import dataclass

from gni.agents.source_discovery import (
    SourceCandidate,
    SourceDiscoveryAgent,
    evaluate_candidate,
)
from gni.contracts.agent import AgentContext, AgentStatus


@dataclass
class FakeProvider:
    candidates: tuple[SourceCandidate, ...]
    fail: bool = False

    def discover(self, *, regions, topics, limit):
        if self.fail:
            raise RuntimeError("private connector detail")
        return self.candidates[:limit]


def strong_candidate(**overrides):
    values = {
        "source_id": "publisher-one",
        "name": "Publisher One",
        "website_url": "https://publisher.example.org",
        "publisher_identity_verified": True,
        "ownership_transparent": True,
        "editorial_policy_available": True,
        "corrections_policy_available": True,
        "contact_available": True,
        "original_reporting": True,
        "independent_reporting": True,
        "evidence_urls": ("https://publisher.example.org/about",),
    }
    values.update(overrides)
    return SourceCandidate(**values)


def context(*permissions):
    return AgentContext("job-1", "trace-1", frozenset(permissions))


def test_high_score_is_not_automatically_approved():
    result = evaluate_candidate(strong_candidate())
    assert result["score"] == 90
    assert result["tier"] == "B"
    assert result["approval_status"] == "awaiting_human_review"
    assert "high_score_is_not_approval" in result["reasons"]


def test_tier_a_requires_trusted_recorded_human_approval():
    result = evaluate_candidate(strong_candidate(), approval_reference="review-2026-001")
    assert result["tier"] == "A"
    assert result["approval_status"] == "human_approved"


def test_candidate_metadata_cannot_self_approve():
    result = evaluate_candidate(strong_candidate())
    assert result["tier"] == "B"
    assert "high_score_is_not_approval" in result["reasons"]


def test_unsafe_url_is_rejected():
    result = evaluate_candidate(strong_candidate(website_url="http://127.0.0.1/admin"))
    assert result["tier"] == "C"
    assert result["score"] == 0


def test_copying_network_reduces_score_and_requires_corroboration():
    result = evaluate_candidate(strong_candidate(known_copying_network=True))
    assert result["score"] == 65
    assert result["tier"] == "B"
    assert "known_copying_network_requires_independent_corroboration" in result["reasons"]


def test_agent_requires_permissions():
    agent = SourceDiscoveryAgent("source-discovery", FakeProvider((strong_candidate(),)))
    result = agent.run(context("sources.discover"), {"regions": ["Iraq"]})
    assert result.status == AgentStatus.FAILED
    assert result.error_code == "permission_denied"


def test_agent_returns_ranked_tiers_and_filters_unsafe_evidence():
    candidates = (
        strong_candidate(source_id="z-source", evidence_urls=("https://publisher.example.org/a", "http://bad.example/a")),
        strong_candidate(source_id="a-source"),
    )
    agent = SourceDiscoveryAgent(
        "source-discovery",
        FakeProvider(candidates),
        approval_references={"a-source": "human-7"},
    )
    result = agent.run(
        context("sources.discover", "sources.candidates.write"),
        {"regions": ["Iraq", "Kurdistan Region"], "topics": ["politics"], "limit": 5},
    )
    assert result.status == AgentStatus.SUCCEEDED
    assert result.output["tier_counts"] == {"A": 1, "B": 1, "C": 0}
    assert result.output["candidates"][0]["tier"] == "A"
    assert result.output["candidates"][1]["evidence_urls"] == ["https://publisher.example.org/a"]
    assert result.output["human_approval_required"] is True


def test_agent_retries_provider_failure_without_exposing_exception():
    agent = SourceDiscoveryAgent("source-discovery", FakeProvider((), fail=True))
    result = agent.run(
        context("sources.discover", "sources.candidates.write"),
        {"regions": [], "topics": []},
    )
    assert result.status == AgentStatus.RETRYABLE
    assert result.error_code == "discovery_provider_failed"
    assert "private connector detail" not in (result.message or "")


def test_agent_rejects_invalid_limits_and_duplicate_ids():
    agent = SourceDiscoveryAgent("source-discovery", FakeProvider((strong_candidate(),)))
    permissions = context("sources.discover", "sources.candidates.write")
    assert agent.run(permissions, {"limit": 201}).error_code == "invalid_discovery_request"
    duplicate = strong_candidate()
    provider = FakeProvider((duplicate, duplicate))
    result = SourceDiscoveryAgent("source-discovery", provider).run(permissions, {})
    assert result.error_code == "duplicate_candidate_ids"
