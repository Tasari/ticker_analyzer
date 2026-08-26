from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ticker_analyzer.numbers import clean_number
from ticker_analyzer.scoring import format_metric_value


@dataclass(frozen=True)
class ScoreInsight:
    tab: str
    metric: str
    value: float | None
    unit: str
    score: float | None
    weight: float

    def summary(self) -> str:
        value = format_metric_value(self.value, self.unit)
        score = "not scored" if self.score is None else f"score {self.score:.1f}/100"
        return f"{self.tab} · {self.metric}: {value} ({score})"


def analysis_insights(result: dict[str, Any], *, limit: int = 3) -> dict[str, list[str]]:
    insights = _metric_insights(result)
    scored = [item for item in insights if item.score is not None]
    strongest = sorted(
        scored,
        key=lambda item: ((item.score or 0) - 50) * item.weight,
        reverse=True,
    )[:limit]
    weakest = sorted(
        scored,
        key=lambda item: ((item.score or 0) - 50) * item.weight,
    )[:limit]
    missing = sorted(
        (item for item in insights if item.score is None),
        key=lambda item: item.weight,
        reverse=True,
    )
    improvements = [
        f"Improve {item.tab} · {item.metric}; it currently scores {item.score:.1f}/100."
        for item in weakest
        if item.score is not None and item.score < 75
    ]
    improvements.extend(rating_constraints(result))
    improvements.extend(
        f"Add reliable data for {item.tab} · {item.metric}; it is currently unscored."
        for item in missing
    )
    if not improvements:
        improvements.append("No single metric blocks the rating; further improvement requires broad-based gains.")
    return {
        "strongest": [item.summary() for item in strongest],
        "weakest": [item.summary() for item in weakest],
        "improvements": _unique(improvements)[:limit],
    }


def rating_constraints(result: dict[str, Any]) -> list[str]:
    messages = {
        "strong_buy_gate_failed": "Strong Buy gate is not met by score, Data Quality, Fundamentals, or the weakest tab.",
        "buy_gate_failed": "Buy gate is not met by score, Data Quality, Fundamentals, or the weakest tab.",
        "data_quality_cap_hold": "Low Data Quality caps the rating at Hold.",
        "data_quality_cap_buy": "Medium Data Quality caps the rating at Buy.",
        "model_applicability_cap_hold": "Low model applicability caps the rating at Hold.",
        "model_applicability_cap_buy": "Model applicability caps the rating at Buy.",
        "profile_model_cap": "The generic profile model caps the rating at Buy.",
        "insufficient_scored_tabs": "At least two scored tabs, including Fundamentals, are required.",
        "data_quality_below_40": "Data Quality below 40 prevents an overall rating.",
    }
    codes = [*result.get("rating_reason_codes", []), *result.get("rating_caps", [])]
    return _unique(messages[code] for code in codes if code in messages)


def _metric_insights(result: dict[str, Any]) -> list[ScoreInsight]:
    insights: list[ScoreInsight] = []
    for tab_name, tab in result.get("tabs", {}).items():
        for metric in tab.get("metrics", []):
            weight = clean_number(_field(metric, "weight")) or 0
            if weight <= 0:
                continue
            insights.append(
                ScoreInsight(
                    tab=tab_name,
                    metric=str(_field(metric, "name") or _field(metric, "id") or "Unknown metric"),
                    value=clean_number(_field(metric, "value")),
                    unit=str(_field(metric, "unit") or ""),
                    score=clean_number(_field(metric, "score")),
                    weight=weight,
                )
            )
    return insights


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _unique(items: Any) -> list[str]:
    return list(dict.fromkeys(items))
