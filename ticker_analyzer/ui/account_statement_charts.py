"""Account statement figures independent of Streamlit widget state."""

from __future__ import annotations

import plotly.graph_objects as go

from ticker_analyzer.portfolio.returns import GrowthPoint
from ticker_analyzer.portfolio.statement_models import StatementAnalysis, StatementRangeAnalysis
from ticker_analyzer.ui.formatting import money as _money


def _profit_loss_waterfall(analysis: StatementAnalysis) -> go.Figure:
    labels = [
        "Beginning equity",
        "External flows",
        "Closed P/L",
        "Dividends",
        "Fees",
        "Other performance",
        "Unrealized P/L change",
        "Ending equity",
    ]
    values = [
        analysis.beginning_unrealized_equity,
        analysis.net_external_flows,
        analysis.closed_positions_profit_loss,
        analysis.dividends,
        analysis.fees,
        analysis.other_performance,
        analysis.unrealized_profit_loss_change,
        analysis.ending_unrealized_equity,
    ]
    figure = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=[
                "absolute",
                "relative",
                "relative",
                "relative",
                "relative",
                "relative",
                "relative",
                "total",
            ],
            connector={"line": {"color": "rgba(128,128,128,0.5)"}},
            text=[_money(value, analysis.currency) for value in values],
            textposition="outside",
        )
    )
    figure.update_layout(
        yaxis_title=analysis.currency,
        showlegend=False,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


def _exposure_chart(analysis: StatementAnalysis) -> go.Figure:
    groups = analysis.exposure_by_type[:10]
    figure = go.Figure(
        go.Bar(
            x=[group.value for group in reversed(groups)],
            y=[group.name for group in reversed(groups)],
            orientation="h",
            text=[_money(group.value, analysis.currency) for group in reversed(groups)],
            textposition="auto",
        )
    )
    figure.update_layout(
        xaxis_title=f"Gross exposure ({analysis.currency})",
        yaxis_title=None,
        showlegend=False,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


def _realized_performance_chart(
    analysis: StatementRangeAnalysis,
    currency: str,
) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=[point.day for point in analysis.daily_performance],
            y=[point.estimated_cumulative_profit_loss for point in analysis.daily_performance],
            mode="lines",
            name="Estimated total P/L",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[point.day for point in analysis.daily_performance],
            y=[point.cumulative_profit_loss for point in analysis.daily_performance],
            mode="lines",
            name="Cumulative realized P/L",
        )
    )
    figure.update_layout(
        xaxis_title=None,
        yaxis_title=f"Cumulative P/L ({currency})",
        showlegend=True,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


def _growth_chart(
    points: tuple[GrowthPoint, ...],
    comparisons: dict[str, tuple[GrowthPoint, ...]] | None = None,
) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=[point.day for point in points],
            y=[point.value for point in points],
            mode="lines+markers",
            name="Account Statement",
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra></extra>",
        )
    )
    for symbol, comparison in (comparisons or {}).items():
        figure.add_trace(
            go.Scatter(
                x=[point.day for point in comparison],
                y=[point.value for point in comparison],
                mode="lines",
                name=symbol,
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra></extra>",
            )
        )
    figure.update_layout(
        xaxis_title=None,
        yaxis_title="Value of initial 10,000",
        showlegend=bool(comparisons),
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure
