"""Shared UI helpers: formatting, banners, empty states, the live-page wrapper."""
from __future__ import annotations

import html

import streamlit as st

from parkistep.dashboard.replay_session import SessionStatus
from theme import badge_html


def fmt_score(s4, x) -> str:
    return "n/a" if x is None else f"{x:.{s4['display']['score_decimals']}f}"


def fmt_time(s4, x) -> str:
    return "n/a" if x is None else f"{x:.{s4['display']['time_decimals']}f} s"


def fmt_pct(s4, x) -> str:
    return "n/a" if x is None else f"{100 * x:.{s4['display']['percent_decimals']}f}%"


def badge(s4, group, key) -> None:
    st.markdown(badge_html(s4, group, key), unsafe_allow_html=True)


def card(title: str, big: str, sub: str = "", badge_markup: str = "") -> None:
    st.markdown(f'<div class="pk-card"><h4>{html.escape(title)}</h4><div class="pk-big">{html.escape(big)}</div>'
                f'<div class="pk-sub">{badge_markup}{(" " if badge_markup and sub else "")}{html.escape(sub)}</div></div>', unsafe_allow_html=True)


def banner(text: str) -> None:
    st.markdown(f'<div class="pk-banner">{html.escape(" ".join(text.split()))}</div>', unsafe_allow_html=True)


def disclaimer(ctx) -> None:
    banner(ctx.s4["app"]["disclaimer_short"])


def empty_state(title: str, body: str, next_step: str | None = None) -> None:
    st.info(f"**{title}**  \n{body}" + (f"  \n*Next:* {next_step}" if next_step else ""))


def dataset_guard(ctx) -> bool:
    """Show an actionable state and return False when the dataset is unavailable."""
    ds = ctx.svc.dataset_status()
    if ds["available"]:
        return True
    st.error("Dataset unavailable")
    st.write(ds["problem"])
    st.caption(ds["next_step"])
    return False


def no_session(ctx, what: str) -> None:
    empty_state(f"No replay yet - {what} is empty", "Nothing has been fed to a digital twin in this browser session, so there is nothing to show. "
                "An empty view is not low risk.", "Open **Replay Analysis**, pick a patient and recording, and press Start.")


def live_page(ctx, render) -> None:
    """Run `render(ctx)` inside a fragment. While a replay is RUNNING the fragment re-runs at the configured refresh interval and
    advances the replay by one bounded slice first; otherwise it does not re-run. The engine decides everything shown."""
    sess = ctx.session()
    running = sess is not None and sess.status == SessionStatus.RUNNING
    interval = ctx.s4["replay"]["refresh_interval_s"] if running else None

    @st.fragment(run_every=interval)
    def _frag():
        s = ctx.session()
        was_running = s is not None and s.status == SessionStatus.RUNNING
        if was_running:
            from state import selection
            ctx.svc.advance(s, selection()["speed"])
        render(ctx)
        if was_running and s.is_finished:
            st.rerun()                           # full rerun: stops the timer and lets every panel show the final state

    _frag()
