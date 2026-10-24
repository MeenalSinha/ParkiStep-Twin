"""STUB-BASED smoke check of the Streamlit view code (use only when streamlit/plotly cannot be installed).

It replaces `streamlit` and `plotly` with permissive fakes and renders every view against the REAL service, data and model, in
several replay states. It catches Python errors (bad names, wrong service calls, wrong keys). It does NOT validate Streamlit API
usage or visual output - run the app (or tests/test_phase4_app.py with streamlit installed) for that.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app")]


class Any_:
    """Accepts any call/attribute/context-manager/iteration; remembers nothing."""
    def __call__(self, *a, **k): return Any_()
    def __getattr__(self, n): return Any_()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter([Any_(), Any_(), Any_(), Any_(), Any_(), Any_()])
    def __bool__(self): return False
    def __getitem__(self, i): return Any_()


def install_fakes():
    st = types.ModuleType("streamlit")
    state: dict = {}

    class SS(dict):
        __getattr__ = dict.get
        def __setattr__(self, k, v): self[k] = v
    st.session_state = SS(state)
    anyo = Any_()
    def columns(spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [Any_() for _ in range(n)]
    def tabs(labels): return [Any_() for _ in labels]
    def selectbox(label, options, index=0, key=None, **k):
        v = list(options)[index] if len(list(options)) else None
        if key: st.session_state.setdefault(key, v)
        return v
    def multiselect(label, options, default=None, key=None, **k): return list(default or [])
    def radio(label, options, key=None, **k): return list(options)[0]
    def select_slider(label, options, value=None, key=None, **k): return value
    def button(*a, **k): return False
    def fragment(run_every=None):
        return lambda f: f
    def rerun(*a, **k): raise RuntimeError("st.rerun called")
    for n, f in dict(columns=columns, tabs=tabs, selectbox=selectbox, multiselect=multiselect, radio=radio, select_slider=select_slider,
                     button=button, fragment=fragment, rerun=rerun).items():
        setattr(st, n, f)
    st.__getattr__ = lambda n: anyo                                   # every other st.* call is a no-op
    sys.modules["streamlit"] = st
    pl = types.ModuleType("plotly"); go = types.ModuleType("plotly.graph_objects"); sub = types.ModuleType("plotly.subplots")
    go.__getattr__ = lambda n: (lambda *a, **k: Any_()); sub.make_subplots = lambda *a, **k: Any_()
    pl.graph_objects, pl.subplots = go, sub
    sys.modules.update({"plotly": pl, "plotly.graph_objects": go, "plotly.subplots": sub})
    return st


def main(project_root: str | None = None) -> int:
    st = install_fakes()
    from parkistep.dashboard.config import load_phase4
    from parkistep.dashboard.service import DashboardService
    import bootstrap
    s4 = load_phase4(ROOT / "configs" / "phase4.yaml", project_root)
    svc = DashboardService(s4).warm_up()
    ctx = bootstrap.Context(s4=s4, svc=svc.fork())
    keys = [n["key"] for n in s4["navigation"]]
    fails = 0

    def run(stage):
        nonlocal fails
        for k in keys:
            try:
                importlib.import_module(f"views.{k}").render(ctx)
            except Exception as e:                                     # noqa: BLE001
                fails += 1
                print(f"FAIL [{stage}] view {k}: {type(e).__name__}: {e}")
                import traceback; traceback.print_exc()
        print(f"stage '{stage}' rendered {len(keys)} views")

    run("no replay")
    from state import selection, validate_selection
    sel = validate_selection(ctx)
    sess = ctx.svc.create_session(sel["patient_id"], sel["recording_id"], sel["model_key"])
    run("replay ready (not started)")
    sess.start()
    for _ in range(6):
        ctx.svc.advance(sess, sel["speed"])
    run("replay running")
    sess.pause(); run("replay paused")
    sess.resume()
    while not sess.is_finished:
        ctx.svc.advance(sess, "max")
    run("replay completed (retrospective)")
    print("FAILED VIEWS:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
