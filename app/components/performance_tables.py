"""Model Performance helpers: every number is copied from the saved Phase 2 evaluation (see PerformanceReports)."""
from __future__ import annotations

import pandas as pd

from theme import plotly_layout


def _ci_text(v, ci, digits=2) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}" + (f" [{ci[0]:.{digits}f}, {ci[1]:.{digits}f}]" if ci else "")


def summary_table(ctx, rows: list[dict], selected_key: str | None = None) -> pd.DataFrame:
    out = []
    for r in rows:
        det = f"{r['n_events_detected']}/{r['n_events_evaluable']}" if r["n_events_detected"] is not None else "n/a"
        out.append({"": "selected" if r["key"] == f"{selected_key}" else "", "Experiment": r["experiment"], "Model": r["model"], "Horizon (s)": r["horizon_s"],
                    "Windows (positive)": f"{r['n_windows']:,} ({r['n_positive_windows']:,})" if r["n_windows"] is not None else "n/a",
                    "Avg precision [95% CI]": _ci_text(r["average_precision"], r["average_precision_ci"], 3),
                    "Event sensitivity [95% CI] (detected/evaluable)": f"{_ci_text(r['event_sensitivity'], r['event_sensitivity_ci'])} ({det})",
                    "False alerts/h [95% CI] (alerts / monitored h)": f"{_ci_text(r['false_alerts_per_hour'], r['false_alerts_per_hour_ci'], 1)} "
                                                                       f"({r['n_false_alerts']} / {r['monitored_hours']:.2f} h)" if r["monitored_hours"] is not None else "n/a",
                    "Median lead s [95% CI]": _ci_text(r["median_lead_s"], r["median_lead_s_ci"]), "Precision/recall at threshold":
                        (f"{r['precision']:.2f} / {r['recall']:.2f}" if r["precision"] is not None and r["recall"] is not None else "n/a")})
    return pd.DataFrame(out)


def fold_table(detail: dict) -> pd.DataFrame:
    return pd.DataFrame(detail["per_fold"])


def lead_table(detail: dict) -> pd.DataFrame:
    lt = detail.get("lead_time_s")
    return pd.DataFrame([lt]) if isinstance(lt, dict) else pd.DataFrame()


def participant_table(detail: dict) -> pd.DataFrame:
    df = pd.DataFrame(detail["per_participant"])
    if len(df):
        df["monitored_min"] = df["monitored_s"] / 60.0
    return df


def metric_figure(ctx, rows: list[dict], metric: str, ci_key: str, title: str):
    import plotly.graph_objects as go
    s4 = ctx.s4
    rr = [r for r in rows if r[metric] is not None]
    fig = go.Figure()
    for exp, color in zip(sorted({r["experiment"] for r in rr}), s4["theme"]["series_colors"]):
        sub = [r for r in rr if r["experiment"] == exp]
        err_hi = [(r[ci_key][1] - r[metric]) if r[ci_key] else 0 for r in sub]
        err_lo = [(r[metric] - r[ci_key][0]) if r[ci_key] else 0 for r in sub]
        fig.add_trace(go.Bar(x=[f"{r['model']} {r['horizon_s']:g}s" for r in sub], y=[r[metric] for r in sub], name=exp, marker_color=color,
                             error_y=dict(type="data", symmetric=False, array=err_hi, arrayminus=err_lo)))
    fig.update_layout(**plotly_layout(s4, height=300), barmode="group", title=dict(text=title, x=0))
    return fig
