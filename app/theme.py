"""Single place where the visual language is applied. All colours/labels come from configs/phase4.yaml."""
from __future__ import annotations

import html

import streamlit as st

from parkistep.dashboard.config import Phase4Settings


def apply_theme(s4: Phase4Settings) -> None:
    c = s4["theme"]["colors"]
    f = s4["theme"]["font_family"]
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

/* Global Typography & Palette */
html, body, [class*="css"], .stApp {{
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    background-color: #F8FAFC !important;
    color: #0F172A !important;
    -webkit-font-smoothing: antialiased;
}}

/* Main App Container */
.main .block-container {{
    max-width: 1280px !important;
    padding-top: 1.75rem !important;
    padding-bottom: 3.5rem !important;
    padding-left: 2.25rem !important;
    padding-right: 2.25rem !important;
}}

/* Headers */
h1 {{
    font-size: 1.85rem !important;
    font-weight: 700 !important;
    color: #0F172A !important;
    letter-spacing: -0.025em !important;
    margin-bottom: 0.25rem !important;
    line-height: 1.2 !important;
}}
h2 {{
    font-size: 1.3rem !important;
    font-weight: 700 !important;
    color: #0F172A !important;
    letter-spacing: -0.015em !important;
    margin-top: 1.5rem !important;
    margin-bottom: 0.5rem !important;
}}
h3 {{
    font-size: 1.05rem !important;
    font-weight: 600 !important;
    color: #1E293B !important;
    margin-top: 1.25rem !important;
    margin-bottom: 0.4rem !important;
}}
h4, h5, h6 {{
    color: #334155 !important;
}}

/* Executive Page Header Container */
.pk-page-header {{
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 16px 22px 16px 22px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);
}}
.pk-header-meta {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
    flex-wrap: wrap;
    gap: 8px;
}}
.pk-header-category {{
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    display: flex;
    align-items: center;
    gap: 6px;
}}
.pk-category-dot {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
}}
.pk-header-context {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.8rem;
}}
.pk-header-title {{
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    color: #0F172A !important;
    letter-spacing: -0.02em !important;
    margin: 0 0 4px 0 !important;
    line-height: 1.2 !important;
}}
.pk-header-subtitle {{
    font-size: 0.865rem;
    color: #475569;
    line-height: 1.45;
}}

/* Sidebar Layout & Elements */
[data-testid="stSidebar"] {{
    background-color: #FFFFFF !important;
    border-right: 1px solid #E2E8F0 !important;
    box-shadow: 2px 0 8px 0 rgba(15, 23, 42, 0.02) !important;
}}
[data-testid="stSidebar"] > div:first-child {{
    background-color: #FFFFFF !important;
}}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
    display: flex !important;
    flex-direction: column !important;
    padding-top: 1.25rem !important;
    padding-bottom: 1.25rem !important;
}}
.pk-sidebar-brand-wrapper {{
    order: -5 !important;
    margin-bottom: 12px !important;
}}
.pk-sidebar-brand {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    background: linear-gradient(145deg, #FFFFFF 0%, #F8FAFC 100%);
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}}
.pk-brand-icon {{
    width: 38px;
    height: 38px;
    border-radius: 10px;
    background: linear-gradient(135deg, #0284C7 0%, #0369A1 100%);
    color: #FFFFFF;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.25rem;
    box-shadow: 0 2px 5px rgba(2, 132, 199, 0.28);
    flex-shrink: 0;
}}
.pk-brand-info {{
    display: flex;
    flex-direction: column;
}}
.pk-brand-title {{
    font-size: 1rem;
    font-weight: 700;
    color: #0F172A;
    line-height: 1.2;
    letter-spacing: -0.01em;
}}
.pk-brand-tag {{
    font-size: 0.65rem;
    font-weight: 700;
    color: #0284C7;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 1px;
}}
.pk-sidebar-caption {{
    order: -4 !important;
    margin-top: 4px !important;
    margin-bottom: 8px !important;
    font-size: 0.78rem !important;
    color: #64748B !important;
    line-height: 1.35 !important;
    padding-left: 2px !important;
}}
.pk-sidebar-divider {{
    order: -3 !important;
    border: 0 !important;
    border-top: 1px solid #E2E8F0 !important;
    margin: 6px 0 14px 0 !important;
}}
[data-testid="stSidebarNav"] {{
    order: 0 !important;
    flex-grow: 1 !important;
    padding-top: 0 !important;
}}
[data-testid="stSidebarNav"] ul {{
    gap: 4px !important;
}}
[data-testid="stSidebarNav"] a {{
    border-radius: 9px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    color: #475569 !important;
    padding: 8px 12px !important;
    transition: all 0.15s ease-in-out !important;
    border-left: 3px solid transparent !important;
}}
[data-testid="stSidebarNav"] a:hover {{
    background-color: #F1F5F9 !important;
    color: #0F172A !important;
}}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background-color: #F0F9FF !important;
    color: #0369A1 !important;
    font-weight: 600 !important;
    border-left: 3px solid #0284C7 !important;
    box-shadow: 0 1px 2px rgba(2, 132, 199, 0.06) !important;
}}
.pk-sidebar-footer-wrapper {{
    order: 10 !important;
    margin-top: auto !important;
    padding-top: 14px !important;
}}

/* Dashboard Cards */
.pk-card {{
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 14px;
    padding: 16px 20px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px 0 rgba(15, 23, 42, 0.04), 0 1px 2px -1px rgba(15, 23, 42, 0.02);
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    position: relative;
}}
.pk-card:hover {{
    border-color: #CBD5E1;
    transform: translateY(-1px);
    box-shadow: 0 8px 16px -4px rgba(15, 23, 42, 0.05), 0 2px 4px -2px rgba(15, 23, 42, 0.02);
}}
.pk-card-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    gap: 8px;
}}
.pk-card h4 {{
    margin: 0;
    font-size: 0.72rem;
    color: #64748B;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}}
.pk-big {{
    font-size: 1.65rem;
    font-weight: 700;
    color: #0F172A;
    line-height: 1.25;
    font-feature-settings: "tnum" 1;
}}
.pk-sub {{
    color: #64748B;
    font-size: 0.825rem;
    margin-top: 6px;
    line-height: 1.4;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
}}

/* Metric Cards */
[data-testid="stMetric"] {{
    background: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 12px !important;
    padding: 14px 18px !important;
    box-shadow: 0 1px 3px 0 rgba(15, 23, 42, 0.04) !important;
    transition: all 0.15s ease-in-out !important;
}}
[data-testid="stMetric"]:hover {{
    border-color: #CBD5E1 !important;
}}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
    overflow-wrap: anywhere;
    font-size: 0.72rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
    font-weight: 600 !important;
    color: #64748B !important;
}}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
    overflow-wrap: anywhere;
    font-size: clamp(1.2rem, 2.2vw, 1.85rem) !important;
    line-height: 1.25 !important;
    font-weight: 700 !important;
    color: #0F172A !important;
    font-feature-settings: "tnum" 1;
}}

/* Badges */
.pk-badge {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2.5px 9px;
    border-radius: 9999px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    border: 1px solid;
    text-transform: uppercase;
    line-height: 1.35;
    white-space: nowrap;
}}
.pk-badge-dot {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
}}

/* Banners & Disclaimers */
.pk-banner {{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    border-left: 3px solid #0284C7;
    background: #F0F9FF;
    border-top: 1px solid #E0F2FE;
    border-right: 1px solid #E0F2FE;
    border-bottom: 1px solid #E0F2FE;
    padding: 10px 14px;
    border-radius: 8px;
    margin: 8px 0 14px 0;
    color: #0369A1;
    font-size: 0.85rem;
    line-height: 1.45;
}}
.pk-disclaimer {{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    border-left: 3px solid #D97706;
    background: #FFFBEB;
    border-top: 1px solid #FEF3C7;
    border-right: 1px solid #FEF3C7;
    border-bottom: 1px solid #FEF3C7;
    padding: 10px 14px;
    border-radius: 8px;
    margin: 8px 0 14px 0;
    color: #92400E;
    font-size: 0.85rem;
    line-height: 1.45;
}}
.pk-empty-state {{
    background: #FFFFFF;
    border: 1px dashed #CBD5E1;
    border-radius: 12px;
    padding: 24px;
    text-align: center;
    margin: 12px 0 16px 0;
}}
.pk-empty-icon {{
    font-size: 2rem;
    margin-bottom: 8px;
}}
.pk-empty-title {{
    font-size: 1.05rem;
    font-weight: 600;
    color: #0F172A;
    margin-bottom: 4px;
}}
.pk-empty-body {{
    font-size: 0.875rem;
    color: #64748B;
    max-width: 540px;
    margin: 0 auto 10px auto;
    line-height: 1.45;
}}
.pk-empty-next {{
    font-size: 0.825rem;
    color: #0369A1;
    background: #F0F9FF;
    border: 1px solid #E0F2FE;
    padding: 6px 14px;
    border-radius: 6px;
    display: inline-block;
}}

/* Buttons */
.stButton > button {{
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.875rem !important;
    padding: 0.45rem 1rem !important;
    transition: all 0.15s ease-in-out !important;
}}
.stButton > button[kind="primary"] {{
    background-color: #0284C7 !important;
    border: 1px solid #0284C7 !important;
    color: #FFFFFF !important;
    box-shadow: 0 1px 2px 0 rgba(2, 132, 199, 0.25) !important;
}}
.stButton > button[kind="primary"]:hover {{
    background-color: #0369A1 !important;
    border-color: #0369A1 !important;
    box-shadow: 0 2px 4px 0 rgba(2, 132, 199, 0.3) !important;
}}
.stButton > button[kind="secondary"] {{
    background-color: #FFFFFF !important;
    border: 1px solid #CBD5E1 !important;
    color: #1E293B !important;
}}
.stButton > button[kind="secondary"]:hover {{
    background-color: #F8FAFC !important;
    border-color: #94A3B8 !important;
    color: #0F172A !important;
}}

/* Expanders */
[data-testid="stExpander"] {{
    background: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 10px !important;
    box-shadow: 0 1px 2px 0 rgba(15, 23, 42, 0.03) !important;
    margin-bottom: 12px !important;
}}
[data-testid="stExpander"] summary {{
    font-weight: 600 !important;
    color: #1E293B !important;
}}

/* Form inputs & selectors */
div[data-baseweb="select"] > div {{
    border-radius: 8px !important;
    border-color: #CBD5E1 !important;
}}
div[data-baseweb="select"] > div:hover {{
    border-color: #94A3B8 !important;
}}

/* Tabs */
[data-testid="stTabs"] button[role="tab"] {{
    font-weight: 600 !important;
    font-size: 0.875rem !important;
    color: #64748B !important;
    padding: 10px 16px !important;
}}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
    color: #0284C7 !important;
    border-bottom-color: #0284C7 !important;
}}

/* Dataframe & Tables */
[data-testid="stDataFrame"] {{
    border-radius: 10px !important;
    border: 1px solid #E2E8F0 !important;
    overflow: hidden !important;
    background: #FFFFFF !important;
}}

/* Streamlit Built-in Alerts */
[data-testid="stAlert"] {{
    border-radius: 8px !important;
    border-width: 1px !important;
    font-size: 0.85rem !important;
}}
</style>""", unsafe_allow_html=True)


def badge_html(s4: Phase4Settings, group: str, key) -> str:
    st_ = s4.style(group, key)
    col = s4.color(st_["role"])
    # label text is always shown with the colour (colour is never the only cue)
    dot = f'<span class="pk-badge-dot" style="background:{col};"></span>'
    return (f'<span class="pk-badge" style="color:{col};border-color:{col}40;background:{col}14" title="{html.escape(st_.get("help", ""))}">'
            f'{dot}{html.escape(st_["label"])}</span>')


def plotly_layout(s4: Phase4Settings, height: int | None = None) -> dict:
    c = s4["theme"]["colors"]
    return dict(
        height=height or s4["plots"]["height_px"],
        margin=dict(l=48, r=16, t=32, b=36),
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Plus Jakarta Sans, " + s4["theme"]["font_family"], color="#1E293B", size=12),
        legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11)),
        hovermode="x unified",
        xaxis=dict(gridcolor="#F1F5F9", zerolinecolor="#E2E8F0", tickfont=dict(size=11, color="#64748B")),
        yaxis=dict(gridcolor="#F1F5F9", zerolinecolor="#E2E8F0", tickfont=dict(size=11, color="#64748B")),
    )

