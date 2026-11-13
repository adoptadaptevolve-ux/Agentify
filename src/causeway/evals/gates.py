"""Quality gates and regression checks for an evaluation report.

Gates are limits a risk owner sets, for the whole dataset and per family::

    {
      "overall":  {"min_accuracy": 0.85, "max_error_rate": 0.0},
      "families": {"injection": {"max_miss_rate": 0.05, "max_brier": 0.1}}
    }

They are a risk decision rather than an engineering one, for the same reason
policy thresholds are (ADR 0008): moving ``max_miss_rate`` from 0.05 to 0.2
means accepting that one injection in five gets through.

A baseline comparison catches the other failure: a model that still clears
every gate but got measurably worse than the version before it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError

LIMITS: dict[str, tuple[str, str]] = {
    "min_accuracy": ("accuracy", "min"),
    "max_brier": ("brier", "max"),
    "max_calibration_error": ("calibration_error", "max"),
    "max_miss_rate": ("miss_rate", "max"),
    "max_false_alarm_rate": ("false_alarm_rate", "max"),
    "max_undecided_rate": ("undecided_rate", "max"),
    "max_mean_absolute_error": ("mean_absolute_error", "max"),
    "max_error_rate": ("error_rate", "max"),
}
"""Every limit a gates file may set: name -> (metric, direction)."""

HIGHER_IS_BETTER = frozenset({"accuracy"})
LOWER_IS_BETTER = frozenset(
    {
        "brier",
        "calibration_error",
        "miss_rate",
        "false_alarm_rate",
        "undecided_rate",
        "mean_absolute_error",
        "error_rate",
    }
)


@dataclass(frozen=True, slots=True)
class Gates:
    """Limits for the whole dataset and for named families."""

    overall: dict[str, float] = field(default_factory=dict)
    families: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GateResult:
    """One limit checked against one metric."""

    scope: str
    limit: str
    threshold: float
    actual: float | None
    passed: bool
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "scope": self.scope,
            "limit": self.limit,
            "threshold": self.threshold,
            "actual": self.actual,
            "passed": self.passed,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Regression:
    """A metric that got worse than the baseline by more than the tolerance."""

    scope: str
    metric: str
    baseline: float
    current: float

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "scope": self.scope,
            "metric": self.metric,
            "baseline": self.baseline,
            "current": self.current,
        }


def load_gates(path: str | Path) -> Gates:
    """Load a gates file, refusing anything it does not understand."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{path}: not valid JSON: {exc}") from None
    return parse_gates(raw, where=str(path))


def parse_gates(raw: Any, *, where: str = "gates") -> Gates:
    """Validate a gates object."""
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{where}: gates must be a JSON object")
    unknown = set(raw) - {"overall", "families", "description"}
    if unknown:
        raise ConfigurationError(f"{where}: unknown fields {sorted(unknown)}")
    overall = _limits(raw.get("overall", {}), where=f"{where} overall")
    families_raw = raw.get("families", {})
    if not isinstance(families_raw, dict):
        raise ConfigurationError(f"{where}: 'families' must be an object")
    families = {
        str(name): _limits(limits, where=f"{where} {name}")
        for name, limits in families_raw.items()
    }
    return Gates(overall=overall, families=families)


def apply_gates(report: dict[str, Any], gates: Gates) -> list[GateResult]:
    """Check every limit against a report's metrics.

    A limit on a family the report has no cases for fails rather than passing
    vacuously: a gate that quietly stops applying is how coverage erodes.
    """
    results = [_check("overall", report["overall"], gates.overall)]
    for family, limits in sorted(gates.families.items()):
        metrics = report["families"].get(family)
        if metrics is None:
            results.append(
                [
                    GateResult(
                        scope=family,
                        limit=limit,
                        threshold=threshold,
                        actual=None,
                        passed=False,
                        note="no cases in this family",
                    )
                    for limit, threshold in sorted(limits.items())
                ]
            )
            continue
        results.append(_check(family, metrics, limits))
    return [result for group in results for result in group]


def compare(
    report: dict[str, Any], baseline: dict[str, Any], *, tolerance: float = 0.02
) -> list[Regression]:
    """Every overall or per-family metric worse than ``baseline`` by more than ``tolerance``."""
    regressions: list[Regression] = []
    scopes: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = [
        ("overall", report["overall"], baseline.get("overall"))
    ]
    for family, metrics in sorted(report["families"].items()):
        scopes.append((family, metrics, baseline.get("families", {}).get(family)))
    for scope, current, before in scopes:
        if not before:
            continue
        for metric in sorted(HIGHER_IS_BETTER | LOWER_IS_BETTER):
            now, then = current.get(metric), before.get(metric)
            if now is None or then is None:
                continue
            worse = (then - now) if metric in HIGHER_IS_BETTER else (now - then)
            if worse > tolerance:
                regressions.append(
                    Regression(scope=scope, metric=metric, baseline=then, current=now)
                )
    return regressions


def _limits(raw: Any, *, where: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{where}: limits must be an object")
    unknown = set(raw) - set(LIMITS)
    if unknown:
        raise ConfigurationError(
            f"{where}: unknown limits {sorted(unknown)}; known limits are {sorted(LIMITS)}"
        )
    limits: dict[str, float] = {}
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigurationError(f"{where}: {name} must be a number")
        if not 0.0 <= float(value) <= 1.0 and name != "max_mean_absolute_error":
            raise ConfigurationError(f"{where}: {name} must be between 0 and 1")
        limits[name] = float(value)
    return limits


def _check(scope: str, metrics: dict[str, Any], limits: dict[str, float]) -> list[GateResult]:
    results: list[GateResult] = []
    for limit, threshold in sorted(limits.items()):
        metric, direction = LIMITS[limit]
        actual = metrics.get(metric)
        if actual is None:
            results.append(
                GateResult(
                    scope=scope,
                    limit=limit,
                    threshold=threshold,
                    actual=None,
                    passed=False,
                    note=f"{metric} could not be computed for this scope",
                )
            )
            continue
        passed = actual >= threshold if direction == "min" else actual <= threshold
        results.append(
            GateResult(
                scope=scope, limit=limit, threshold=threshold, actual=actual, passed=passed
            )
        )
    return results
