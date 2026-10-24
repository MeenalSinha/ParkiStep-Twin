"""Single place where the visual language is applied. All colours/labels come from configs/phase4.yaml."""
from __future__ import annotations

import html

import streamlit as st

from parkistep.dashboard.config import Phase4Settings


def apply_theme(s4: Phase4Settings) -> None:
    c, f = s4["theme"]["colors"], s4["theme"]["font_family"]
    st.markdown(f"""
<style>
html, body, [class*="css"] {{ font-family: {f}; }}
/* st.metric truncates long labels/values with an ellipsis on narrow screens: let them wrap and scale instead (verified by p6_visual_review) */
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{ white-space: normal !important; overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere; }}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{ white-space: normal !important; overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere; font-size: clamp(1.15rem, 2.4vw, 2.25rem) !important; line-height: 1.2; }}
.pk-card {{ background:{c['surface']}; border:1px solid {c['border']}; border-radius:10px; padding:14px 16px; margin-bottom:10px; }}
.pk-card h4 {{ margin:0 0 6px 0; font-size:0.85rem; color:{c['muted']}; font-weight:600; letter-spacing:.02em; text-transform:uppercase; }}
.pk-big {{ font-size:1.7rem; font-weight:650; color:{c['text']}; line-height:1.15; }}
.pk-sub {{ color:{c['muted']}; font-size:0.85rem; margin-top:2px; }}
.pk-badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.8rem; font-weight:600; border:1px solid; }}
.pk-banner {{ border-left:4px solid {c['caution']}; background:{c['surface']}; padding:8px 12px; border-radius:6px; margin:6px 0 12px 0; color:{c['text']}; font-size:0.88rem; }}
</style>""", unsafe_allow_html=True)


def badge_html(s4: Phase4Settings, group: str, key) -> str:
    st_ = s4.style(group, key)
    col = s4.color(st_["role"])
    # label text is always shown with the colour (colour is never the only cue)
    return (f'<span class="pk-badge" style="color:{col};border-color:{col};background:{col}14" title="{html.escape(st_.get("help", ""))}">'
            f'{html.escape(st_["label"])}</span>')


def plotly_layout(s4: Phase4Settings, height: int | None = None) -> dict:
    c = s4["theme"]["colors"]
    return dict(height=height or s4["plots"]["height_px"], margin=dict(l=48, r=16, t=30, b=36), paper_bgcolor=c["surface"],
                plot_bgcolor=c["surface"], font=dict(family=s4["theme"]["font_family"], color=c["text"], size=12),
                legend=dict(orientation="h", y=1.12, x=0), hovermode="x unified")
