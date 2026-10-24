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
ctx = bootstrap.get_context()


def _page(key: str, title: str):
    def run():
        importlib.import_module(f"views.{key}").render(ctx)
    run.__name__ = key
    return st.Page(run, title=title, url_path=key.replace("_", "-"), default=(key == s4["navigation"][0]["key"]))


nav = st.navigation([_page(n["key"], n["title"]) for n in s4["navigation"]])
with st.sidebar:
    st.markdown(f"**{s4['app']['title']}**")
    st.caption(s4["app"]["subtitle"])
    st.caption(s4["app"]["disclaimer_short"])
nav.run()
