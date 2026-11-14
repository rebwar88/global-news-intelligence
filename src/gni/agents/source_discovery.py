"""Source Discovery Agent: assess candidate publishers without auto-approving them."""
from __future__ import annotations  # noqa: I001

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from gni.connectors.rss import is_https_url
from gni.contracts.agent import AgentContext, AgentResult, AgentStatus


_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_BOOLEAN_FIELDS = (
    "publisher_identity_verified",
    "ownership_transparent",
    "editorial_policy_available",
    "corrections_policy_available",
    "contact_available",
    "original_reporting",
    "independent_reporting",
    "known_copying_network",
)


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    source_id: str
    name: str
    website_url: str
    regions: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    publisher_identity_verified: bool = False
    ownership_transparent: bool = False
    editorial_policy_available: bool = False
    corrections_policy_available: bool = False
    contact_available: bool = False
    original_reporting: bool = False
    independent_reporting: bool = False
    known_copying_network: bool = False
    evidence_urls: tuple[str, ...] = ()


class CandidateProvider(Protocol):
    def discover(
        self, *, regions: tuple[str, ...], topics: tuple[str, ...], limit: int
    ) -> tuple[SourceCandidate, ...]:
        """Return candidate metadata gathered by a trusted discovery connector."""
        ...


def _public_https_url(value: str) -> bool:
    if not is_https_url(value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if host in {"localhost"} or host.endswith((".local", ".internal")):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "." in host and not host.endswith(".")
        return address.is_global
    except ValueError:
        return False


def evaluate_candidate(
    candidate: SourceCandidate, *, approval_reference: str | None = None
) -> dict[str, object]:
    """Score only supplied, inspectable facts; never infer truth from popularity."""
    reasons: list[str] = []
    if not isinstance(candidate.source_id, str) or not _SOURCE_ID.fullmatch(candidate.source_id):
        raise ValueError("Candidate source_id must be a short lowercase identifier.")
    if not isinstance(candidate.name, str) or not candidate.name.strip():
        raise ValueError("Candidate name is required.")
    if not isinstance(candidate.website_url, str):
        raise TypeError("Candidate website URL must be a string.")
    if any(type(getattr(candidate, field_name)) is not bool for field_name in _BOOLEAN_FIELDS):
        raise ValueError("Candidate verification flags must be booleans.")
    for field_name in ("regions", "topics", "evidence_urls"):
        values = getattr(candidate, field_name)
        if not isinstance(values, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError(f"Candidate {field_name} must be a tuple of non-empty strings.")

    safe_url = _public_https_url(candidate.website_url)
    if not safe_url:
        return {
            "source_id": candidate.source_id,
            "name": candidate.name[:160],
            "website_url": "",
            "score": 0,
            "tier": "C",
            "approval_status": "rejected",
            "reasons": ["unsafe_or_invalid_website_url"],
            "evidence_urls": [],
        }

    checks = (
        ("publisher_identity_verified", candidate.publisher_identity_verified, 20),
        ("ownership_transparent", candidate.ownership_transparent, 15),
        ("editorial_policy_available", candidate.editorial_policy_available, 10),
        ("corrections_policy_available", candidate.corrections_policy_available, 10),
        ("contact_available", candidate.contact_available, 5),
        ("original_reporting", candidate.original_reporting, 15),
        ("independent_reporting", candidate.independent_reporting, 15),
    )
    score = 0
    for reason, present, points in checks:
        if present:
            score += points
        else:
            reasons.append(f"not_verified:{reason}")
    if candidate.known_copying_network:
        score -= 25
        reasons.append("known_copying_network_requires_independent_corroboration")
    score = max(0, min(score, 100))

    if score < 30:
        tier = "C"
        approval_status = "rejected"
    elif score >= 75 and approval_reference and approval_reference.strip():
        tier = "A"
        approval_status = "human_approved"
    else:
        tier = "B"
        approval_status = "awaiting_human_review"
        if score >= 75 and not approval_reference:
            reasons.append("high_score_is_not_approval")

    evidence = [url for url in candidate.evidence_urls if _public_https_url(url)]
    return {
        "source_id": candidate.source_id,
        "name": candidate.name[:160],
        "website_url": candidate.website_url,
        "regions": list(candidate.regions[:20]),
        "topics": list(candidate.topics[:30]),
        "score": score,
        "tier": tier,
        "approval_status": approval_status,
        "reasons": reasons,
        "evidence_urls": list(dict.fromkeys(evidence))[:20],
        "known_copying_network": candidate.known_copying_network,
    }


@dataclass(slots=True)
class SourceDiscoveryAgent:
    agent_id: str
    provider: CandidateProvider
    max_candidates: int = 50
    approval_references: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.agent_id != "source-discovery":
            raise ValueError("SourceDiscoveryAgent must use the source-discovery ID.")
        if self.max_candidates < 1 or self.max_candidates > 200:
            raise ValueError("max_candidates must be between 1 and 200.")

    def run(self, context: AgentContext, payload: dict[str, object]) -> AgentResult:
        required = {"sources.discover", "sources.candidates.write"}
        if not required.issubset(context.permissions):
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="permission_denied",
                message="Source discovery permissions were not granted.",
            )
        regions = payload.get("regions", [])
        topics = payload.get("topics", [])
        limit = payload.get("limit", self.max_candidates)
        if (
            not isinstance(regions, list)
            or not isinstance(topics, list)
            or any(not isinstance(value, str) or not value.strip() for value in regions + topics)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > self.max_candidates
        ):
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="invalid_discovery_request",
                message="Provide bounded region/topic filters and a valid candidate limit.",
            )
        try:
            candidates = self.provider.discover(
                regions=tuple(regions), topics=tuple(topics), limit=limit
            )
        except Exception:  # noqa: BLE001 - isolate untrusted discovery connector errors.
            return AgentResult(
                status=AgentStatus.RETRYABLE,
                error_code="discovery_provider_failed",
                message="Candidate discovery could not be completed.",
            )
        if len(candidates) > limit:
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="provider_limit_exceeded",
                message="Discovery provider returned more candidates than requested.",
            )
        ids = [candidate.source_id for candidate in candidates]
        if len(ids) != len(set(ids)):
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="duplicate_candidate_ids",
                message="Discovery provider returned duplicate candidate identifiers.",
            )
        try:
            evaluated = [
                evaluate_candidate(
                    candidate,
                    approval_reference=self.approval_references.get(candidate.source_id),
                )
                for candidate in candidates
            ]
        except (AttributeError, TypeError, ValueError):
            return AgentResult(
                status=AgentStatus.FAILED,
                error_code="invalid_candidate_metadata",
                message="Discovery provider returned invalid candidate metadata.",
            )
        evaluated.sort(key=lambda item: (-int(item["score"]), str(item["source_id"])))
        counts = {tier: sum(item["tier"] == tier for item in evaluated) for tier in ("A", "B", "C")}
        return AgentResult(
            status=AgentStatus.SUCCEEDED,
            output={
                "schema_version": "1.0",
                "candidates": evaluated,
                "tier_counts": counts,
                "human_approval_required": True,
                "note": "Tier A requires a recorded human approval; scores never auto-approve sources.",
            },
        )
