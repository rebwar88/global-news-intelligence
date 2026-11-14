"""Dependency-free, thread-safe Prometheus metrics with bounded label cardinality."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


@dataclass
class _Family:
    kind: str
    help: str
    samples: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    histograms: dict[tuple[tuple[str, str], ...], list[float]] = field(default_factory=dict)


class MetricsRegistry:
    """In-process metrics registry; do not put job/user/source IDs in labels."""

    def __init__(self, *, max_series_per_metric: int = 500) -> None:
        if max_series_per_metric < 1:
            raise ValueError("max_series_per_metric must be positive.")
        self._max_series = max_series_per_metric
        self._families: dict[str, _Family] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _labels(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        normalized = []
        for key, value in (labels or {}).items():
            if not _LABEL.fullmatch(key):
                raise ValueError(f"Invalid metric label name: {key!r}")
            text = str(value)
            if len(text) > 128:
                text = text[:128]
            normalized.append((key, text))
        if len(normalized) > 10:
            raise ValueError("Metrics support at most 10 labels per sample.")
        return tuple(sorted(normalized))

    def _family(self, name: str, kind: str, help_text: str) -> _Family:
        if not _NAME.fullmatch(name):
            raise ValueError(f"Invalid metric name: {name!r}")
        family = self._families.get(name)
        if family is None:
            family = _Family(kind, help_text)
            self._families[name] = family
        elif family.kind != kind:
            raise ValueError(f"Metric {name!r} was already registered as {family.kind}.")
        return family

    def _check_series(self, family: _Family, labels: tuple[tuple[str, str], ...]) -> None:
        if labels not in family.samples and labels not in family.histograms:
            if len(family.samples) + len(family.histograms) >= self._max_series:
                raise ValueError("Metric series limit reached; avoid high-cardinality labels.")

    def increment(
        self, name: str, value: float = 1, *, labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        if value < 0:
            raise ValueError("Counters cannot be incremented by a negative value.")
        key = self._labels(labels)
        with self._lock:
            family = self._family(name, "counter", help_text)
            self._check_series(family, key)
            family.samples[key] = family.samples.get(key, 0.0) + value

    def set_gauge(
        self, name: str, value: float, *, labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        key = self._labels(labels)
        with self._lock:
            family = self._family(name, "gauge", help_text)
            self._check_series(family, key)
            family.samples[key] = float(value)

    def adjust_gauge(
        self, name: str, delta: float, *, labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        key = self._labels(labels)
        with self._lock:
            family = self._family(name, "gauge", help_text)
            self._check_series(family, key)
            family.samples[key] = family.samples.get(key, 0.0) + delta

    def observe(
        self, name: str, value: float, *, labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        if value < 0:
            raise ValueError("Histogram observations cannot be negative.")
        key = self._labels(labels)
        with self._lock:
            family = self._family(name, "histogram", help_text)
            self._check_series(family, key)
            values = family.histograms.setdefault(
                key, [0.0, 0.0, *([0.0] * len(_DEFAULT_BUCKETS))]
            )
            values[0] += 1
            values[1] += value
            for index, boundary in enumerate(_DEFAULT_BUCKETS, start=2):
                if value <= boundary:
                    values[index] += 1

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    def render(self) -> str:
        """Return Prometheus text exposition (0.0.4)."""
        with self._lock:
            families = {
                name: _Family(
                    family.kind, family.help, dict(family.samples),
                    {key: list(value) for key, value in family.histograms.items()},
                )
                for name, family in self._families.items()
            }
        lines: list[str] = []
        for name in sorted(families):
            family = families[name]
            lines.append(f"# HELP {name} {family.help.replace(chr(10), ' ')}")
            lines.append(f"# TYPE {name} {family.kind}")
            for labels, value in sorted(family.samples.items()):
                suffix = self._format_labels(labels)
                lines.append(f"{name}{suffix} {value:g}")
            if family.kind == "histogram":
                for labels, values in sorted(family.histograms.items()):
                    buckets = _DEFAULT_BUCKETS
                    for index, boundary in enumerate(buckets, start=2):
                        bucket_labels = tuple(sorted((*labels, ("le", f"{boundary:g}"))))
                        lines.append(f"{name}_bucket{self._format_labels(bucket_labels)} {values[index]:g}")
                    inf_labels = tuple(sorted((*labels, ("le", "+Inf"))))
                    lines.append(f"{name}_bucket{self._format_labels(inf_labels)} {values[0]:g}")
                    lines.append(f"{name}_sum{self._format_labels(labels)} {values[1]:g}")
                    lines.append(f"{name}_count{self._format_labels(labels)} {values[0]:g}")
        return "\n".join(lines) + ("\n" if lines else "")

    @classmethod
    def _format_labels(cls, labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        rendered = ",".join(f'{key}="{cls._escape(value)}"' for key, value in labels)
        return "{" + rendered + "}"
