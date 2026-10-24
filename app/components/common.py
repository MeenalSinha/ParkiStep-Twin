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
    b_markup = f'<div class="pk-card-badge">{badge_markup}</div>' if badge_markup else ""
    sub_markup = f'<div class="pk-sub">{html.escape(sub)}</div>' if sub else ""
    st.markdown(
        f'<div class="pk-card">'
        f'<div class="pk-card-header"><h4>{html.escape(title)}</h4>{b_markup}</div>'
        f'<div class="pk-big">{html.escape(big)}</div>'
        f'{sub_markup}'
        f'</div>',
        unsafe_allow_html=True,
    )


def banner(text: str) -> None:
    clean_txt = html.escape(" ".join(text.split()))
    st.markdown(
        f'<div class="pk-banner">'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#0284C7;"></span>'
        f'<span style="font-size:0.7rem;font-weight:700;color:#0369A1;letter-spacing:0.06em;text-transform:uppercase;">Information</span>'
        f'</div>'
        f'<div style="font-size:0.835rem;color:#0C4A6E;line-height:1.45;margin-top:2px;">{clean_txt}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def disclaimer(ctx) -> None:
    txt = html.escape(" ".join(ctx.s4["app"]["disclaimer_short"].split()))
    st.markdown(
        f'<div class="pk-disclaimer">'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#D97706;"></span>'
        f'<span style="font-size:0.7rem;font-weight:700;color:#B45309;letter-spacing:0.06em;text-transform:uppercase;">Research Prototype &bull; Safety Notice</span>'
        f'</div>'
        f'<div style="font-size:0.825rem;color:#78350F;line-height:1.45;margin-top:3px;">{txt}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def page_header(ctx, title: str, category: str, subtitle: str, accent_color: str = "#0284C7") -> None:
    from state import selection
    sel = selection()
    sess = ctx.session() if hasattr(ctx, "session") else None
    is_live = sess is not None and hasattr(sess, "status") and sess.status.value in ("running", "paused")
    status_pill = (
        '<span class="pk-badge" style="color:#0F766E;border-color:#99F6E4;background:#F0FDFA;"><span class="pk-badge-dot" style="background:#0D9488;"></span>STREAMING</span>'
        if is_live else
        '<span class="pk-badge" style="color:#64748B;border-color:#E2E8F0;background:#F8FAFC;"><span class="pk-badge-dot" style="background:#94A3B8;"></span>STANDBY</span>'
    )
    pid = sel.get("patient_id") or "2"
    rid = sel.get("recording_id") or "p2-s1-t4"
    st.markdown(
        f'''<div class="pk-page-header">
            <div class="pk-header-meta">
                <div class="pk-header-category" style="color:{accent_color};">
                    <span class="pk-category-dot" style="background:{accent_color};"></span>
                    {html.escape(category)}
                </div>
                <div class="pk-header-context">
                    <span style="font-weight:600;color:#0F172A;">Patient {html.escape(str(pid))}</span>
                    <span style="color:#CBD5E1;">&bull;</span>
                    <span style="color:#64748B;">{html.escape(str(rid))}</span>
                    {status_pill}
                </div>
            </div>
            <h1 class="pk-header-title">{html.escape(title)}</h1>
            <div class="pk-header-subtitle">{html.escape(subtitle)}</div>
        </div>''',
        unsafe_allow_html=True
    )
    disclaimer(ctx)



def empty_state(title: str, body: str, next_step: str | None = None) -> None:
    msg = f"**{title}**  \n{body}" + (f"  \n*Action:* {next_step}" if next_step else "")
    st.info(msg)


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
