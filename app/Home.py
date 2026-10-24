"""ParkiStep Twin - clinician dashboard entry point.   Launch:  streamlit run app/Home.py

Research prototype: outputs are experimental model scores, not diagnoses; not a medical device.
"""
from __future__ import annotations

import importlib

import streamlit as st

import bootstrap

s4 = bootstrap.load_settings_or_stop()                       # actionable error screen instead of a traceback if the config is invalid
st.set_page_config(page_title=s4["app"]["title"], page_icon=s4["app"]["page_icon"], layout=s4["app"]["layout"])

from theme import apply_theme                                 # noqa: E402

apply_theme(s4)
try:
    st.logo("app/assets/logo.svg", size="large")
except Exception:
    pass

ctx = bootstrap.get_context()


def _page(key: str, title: str):
    def run():
        mod = importlib.import_module(f"views.{key}")
        try:
            importlib.reload(mod)
        except Exception:
            pass
        mod.render(ctx)
    run.__name__ = key
    return st.Page(run, title=title, url_path=key.replace("_", "-"), default=(key == s4["navigation"][0]["key"]))


nav = st.navigation([_page(n["key"], n["title"]) for n in s4["navigation"]])

with st.sidebar:
    st.caption(f"{s4['app']['subtitle']}")
    st.markdown('<hr style="margin:8px 0 14px 0;border:0;border-top:1px solid #E2E8F0;"/>', unsafe_allow_html=True)

nav.run()

with st.sidebar:
    st.markdown(
        f'<div style="margin-top:24px;">'
        f'<hr style="margin:16px 0 12px 0;border:0;border-top:1px solid #E2E8F0;"/>'
        f'<div style="font-size:0.75rem;color:#64748B;background:#F8FAFC;border:1px solid #E2E8F0;padding:10px 12px;border-radius:8px;line-height:1.4;">'
        f'<strong>Notice:</strong> {s4["app"]["disclaimer_short"]}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True
    )


