"""Plotly figures built from presentation data prepared by `parkistep.dashboard` (observations the engine accepted)."""
from __future__ import annotations

import numpy as np

from theme import plotly_layout


def _go():
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    return go, make_subplots


def movement_figure(ctx, traces: list[dict], warnings: list[dict] | None = None, events: list[dict] | None = None, title: str | None = None):
    """One row per sensor position (from the dataset channel schema); gaps are NaN breaks. `events` (annotated episodes) are
    passed ONLY by the retrospective view."""
    go, make_subplots = _go()
    s4 = ctx.s4
    positions = list(dict.fromkeys(t["position"] for t in traces))
    fig = make_subplots(rows=max(1, len(positions)), cols=1, shared_xaxes=True, vertical_spacing=0.04, subplot_titles=positions or None)
    colors = s4["theme"]["series_colors"]
    for r, pos in enumerate(positions, start=1):
        for i, t in enumerate([x for x in traces if x["position"] == pos]):
            unit = f" ({t['unit']})" if t.get("unit") else ""
            fig.add_trace(go.Scatter(x=t["x"], y=t["y"], mode="lines", connectgaps=False, name=f"{t['signal']} {t['axis']}",
                                     legendgroup=f"{t['signal']}{t['axis']}", showlegend=(r == 1), line=dict(width=1, color=colors[i % len(colors)]),
                                     hovertemplate=f"{t['channel']}: %{{y:.3f}}{unit}<extra></extra>"), row=r, col=1)
        units = sorted({x["unit"] for x in traces if x["position"] == pos and x.get("unit")})
        fig.update_yaxes(title_text=", ".join(units) or None, row=r, col=1)
    for w in warnings or []:
        end = w.get("resolved_at") or w["activated_at"]
        fig.add_vrect(x0=w["activated_at"], x1=end if end > w["activated_at"] else w["activated_at"] + 1e-3,
                      fillcolor=s4.color("alert"), opacity=0.15, line_width=0)
    for e in events or []:
        fig.add_vrect(x0=e["onset_t"], x1=e["end_t"], fillcolor=s4.color("annotation"), opacity=0.25, line_width=0)
    fig.update_layout(**plotly_layout(s4, height=max(s4["plots"]["height_px"], 150 * max(1, len(positions)))))
    fig.update_xaxes(title_text="Observation time (s)", row=max(1, len(positions)), col=1)
    if title:
        fig.update_layout(title=dict(text=title, x=0))
    return fig


def risk_figure(ctx, frame, threshold: float | None, warnings: list[dict] | None = None, events: list[dict] | None = None):
    go, _ = _go()
    s4 = ctx.s4
    fig = go.Figure()
    if len(frame):
        fig.add_trace(go.Scatter(x=frame["prediction_time"], y=frame["probability"], mode="lines", name="Model score",
                                 line=dict(color=s4.color("primary"), width=1.5),
                                 hovertemplate="t=%{x:.2f} s<br>score %{y:.3f}<extra></extra>"))
        up = frame[frame["above_threshold"] == True]                      # noqa: E712
        if len(up):
            fig.add_trace(go.Scatter(x=up["prediction_time"], y=up["probability"], mode="markers", name="At or above threshold",
                                     marker=dict(color=s4.color("alert"), size=5)))
    if threshold is not None:
        fig.add_hline(y=threshold, line_dash="dash", line_color=s4.color("caution"), annotation_text="operating threshold", annotation_position="top left")
    for w in warnings or []:
        end = w.get("resolved_at") or w["activated_at"]
        fig.add_vrect(x0=w["activated_at"], x1=end if end > w["activated_at"] else w["activated_at"] + 1e-3, fillcolor=s4.color("alert"), opacity=0.12, line_width=0)
    for e in events or []:
        fig.add_vrect(x0=e["onset_t"], x1=e["end_t"], fillcolor=s4.color("annotation"), opacity=0.25, line_width=0)
    fig.update_yaxes(title_text="score (uncalibrated)", range=[0, 1])
    fig.update_xaxes(title_text="Observation time (s)")
    fig.update_layout(**plotly_layout(s4))
    return fig


def nan_safe(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x
