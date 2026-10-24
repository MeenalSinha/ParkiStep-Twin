#!/usr/bin/env python
"""Builds docs/phase6/ParkiStep_Twin_Presentation.pptx (editable) from generated project files only.

Every number comes from: reports/phase2/phase2_metrics.json + phase2_results_report.md (results, CIs), reports/phase6/*.json and
junit XML (verification), configs/*.yaml, and the Phase 1 recheck. Screenshots are the real ones captured by
scripts/p6_visual_review.py. Team/repository details stay as PENDING placeholders unless supplied via --team-json.
    python docs/phase6/presentation_src/build_presentation.py [--out docs/phase6/ParkiStep_Twin_Presentation.pptx]
Then export the PDF:  soffice --headless --convert-to pdf --outdir docs/phase6 docs/phase6/ParkiStep_Twin_Presentation.pptx
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[3]
FIG = ROOT / "docs/phase6/presentation_src/figs"
NAVY, TEAL, GREY, LIGHT, RED, AMBER, WHITE = (RGBColor(0x1F, 0x29, 0x37), RGBColor(0x0F, 0x76, 0x6E), RGBColor(0x6B, 0x72, 0x80),
                                              RGBColor(0xF3, 0xF4, 0xF6), RGBColor(0xB9, 0x1C, 0x1C), RGBColor(0xB4, 0x53, 0x09), RGBColor(255, 255, 255))
FONT = "Calibri"
FOOT = "Research prototype - not a medical device. Outputs are experimental model scores."


def J(p):
    return json.loads((ROOT / p).read_text())


# ---------------------------------------------------------------------------------------------------------------- data
def parse_report():
    """horizon -> {(experiment, model): dict} parsed from the generated Phase 2 markdown tables (keeps the CIs)."""
    txt = (ROOT / "reports/phase2/phase2_results_report.md").read_text()
    out, h = {}, None
    for ln in txt.splitlines():
        m = re.match(r"### Horizon ([\d.]+) s", ln)
        if m:
            h = float(m.group(1)); out[h] = {}; continue
        if ln.startswith("#"):
            h = None; continue                      # any other heading (e.g. lead-time distributions) ends the horizon tables
        if h is not None and ln.startswith("| ") and "|" in ln[2:] and not ln.startswith("| Experiment") and not ln.startswith("|---"):
            c = [x.strip() for x in ln.strip().strip("|").split("|")]
            if len(c) >= 10 and c[0] in ("sensor_only", "fusion"):
                sens = re.match(r"([\d.]+) \[([\d.]+), ([\d.]+)\] \((\d+)/(\d+)\)", c[7])
                fa = re.match(r"([\d.]+) \[([\d.]+), ([\d.]+)\]", c[8])
                ap = re.match(r"([\d.]+) \[([\d.]+), ([\d.]+)\]", c[4])
                lead = re.match(r"([\d.]+)", c[9])
                out[h][(c[0], c[1])] = {"ap": float(ap.group(1)), "auc": float(c[5]), "sens": float(sens.group(1)), "sens_ci": (float(sens.group(2)), float(sens.group(3))),
                                        "det": int(sens.group(4)), "evaluable": int(sens.group(5)), "fa": float(fa.group(1)), "fa_ci": (float(fa.group(2)), float(fa.group(3))),
                                        "lead": float(lead.group(1)) if lead else None}
    return out


def make_charts(res):
    models = [("sensor_only", "logistic", "sensor logistic"), ("sensor_only", "random_forest", "sensor RF"),
              ("fusion", "logistic", "fusion logistic"), ("fusion", "random_forest", "fusion RF")]
    cols = ["#0f766e", "#14b8a6", "#9a3412", "#f59e0b"]
    hs = sorted(res)
    for key, ylabel, fname, budget in (("sens", "event sensitivity (detected / evaluable events)", "sensitivity.png", None),
                                       ("fa", "false alerts per hour", "false_alerts.png", 30.0)):
        fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=200)
        w = 0.19
        for i, (e, m, lab) in enumerate(models):
            xs = [j + (i - 1.5) * w for j in range(len(hs))]
            vals = [res[h][(e, m)][key] for h in hs]
            lo = [res[h][(e, m)][key] - res[h][(e, m)][key + "_ci"][0] for h in hs]
            hi = [res[h][(e, m)][key + "_ci"][1] - res[h][(e, m)][key] for h in hs]
            ax.bar(xs, vals, w, yerr=[lo, hi], capsize=2, color=cols[i], label=lab, error_kw={"elinewidth": 0.8})
        if budget:
            ax.axhline(budget, color="#b91c1c", ls="--", lw=1.2); ax.text(len(hs) - 0.5, budget + 2, "30/h budget used to pick thresholds", color="#b91c1c", ha="right", fontsize=7)
        ax.set_xticks(range(len(hs))); ax.set_xticklabels([f"{h:g} s horizon" for h in hs]); ax.set_ylabel(ylabel, fontsize=9)
        ax.spines[["top", "right"]].set_visible(False); ax.legend(fontsize=7, frameon=False, ncol=2); ax.tick_params(labelsize=8)
        fig.tight_layout(); fig.savefig(FIG / fname); plt.close(fig)


def crop_top(src, dst, w=1440, h=900):
    im = Image.open(src); im.crop((0, 0, min(w, im.width), min(h, im.height))).save(dst)


# ---------------------------------------------------------------------------------------------------------------- pptx
class Deck:
    def __init__(self):
        self.p = Presentation(); self.p.slide_width, self.p.slide_height = Inches(13.333), Inches(7.5); self.n = 0
        self.blank = self.p.slide_layouts[6]

    def slide(self, title, notes=None):
        s = self.p.slides.add_slide(self.blank); self.n += 1
        bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, self.p.slide_width, Inches(0.12)); bar.fill.solid(); bar.fill.fore_color.rgb = TEAL; bar.line.fill.background()
        self.text(s, title, 0.6, 0.3, 12.1, 0.9, 30, True, NAVY)
        self.text(s, FOOT, 0.6, 7.05, 10.5, 0.3, 10, False, GREY)
        self.text(s, str(self.n), 12.2, 7.05, 0.6, 0.3, 10, False, GREY, PP_ALIGN.RIGHT)
        if notes: s.notes_slide.notes_text_frame.text = notes
        return s

    def text(self, s, t, x, y, w, h, size=18, bold=False, color=NAVY, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame; tf.word_wrap = True; tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = Inches(0.05)
        lines = t if isinstance(t, list) else [t]
        for i, ln in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph(); p.alignment = align
            r = p.add_run(); r.text = ln; r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = color; r.font.name = FONT
        return tb

    def bullets(self, s, items, x=0.6, y=1.4, w=12.1, h=5.4, size=20, gap=8):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame; tf.word_wrap = True
        for i, it in enumerate(items):
            lvl = 1 if isinstance(it, tuple) else 0; txt = it[0] if isinstance(it, tuple) else it
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph(); p.space_after = Pt(gap)
            r = p.add_run(); r.text = ("-  " if lvl else "") + txt if lvl else "\u2022  " + txt
            r.font.size = Pt(size - 3 if lvl else size); r.font.name = FONT; r.font.color.rgb = GREY if lvl else NAVY
            if lvl: p.level = 1
        return tb

    def card(self, s, x, y, w, h, head, body, fill=LIGHT, size=15):
        sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)); sh.fill.solid(); sh.fill.fore_color.rgb = fill; sh.line.color.rgb = RGBColor(0xD1, 0xD5, 0xDB)
        sh.adjustments[0] = 0.06
        self.text(s, head, x + 0.15, y + 0.1, w - 0.3, 0.5, size + 2, True, TEAL)
        self.text(s, body, x + 0.15, y + 0.6, w - 0.3, h - 0.7, size, False, NAVY)

    def image(self, s, path, x, y, w=None, h=None):
        im = Image.open(path); ar = im.width / im.height
        if w and h:
            if w / h > ar: w = h * ar
            else: h = w / ar
        elif w: h = w / ar
        else: w = h * ar
        return s.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))

    def table(self, s, rows, x, y, w, col_w, size=13, h_row=0.4):
        t = s.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(h_row * len(rows))).table
        for j, cw in enumerate(col_w): t.columns[j].width = Inches(cw)
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                c = t.cell(i, j); c.text = str(v); p = c.text_frame.paragraphs[0]; p.font.size = Pt(size); p.font.name = FONT; p.font.bold = (i == 0)
                p.font.color.rgb = WHITE if i == 0 else NAVY; c.fill.solid(); c.fill.fore_color.rgb = TEAL if i == 0 else (LIGHT if i % 2 == 0 else WHITE)
        return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/phase6/ParkiStep_Twin_Presentation.pptx")
    ap.add_argument("--team-json", default=None, help="optional JSON with team_name, members, college, repo_url, video_url")
    a = ap.parse_args()
    team = json.loads(Path(a.team_json).read_text()) if a.team_json else {}
    PEND = "[PENDING - to be supplied]"

    m = J("reports/phase2/phase2_metrics.json"); exp_id = m["experiment_id"]
    res = parse_report(); make_charts(res)
    ws = m["window_summary"]; fs = ws["feature_spec"]
    ds = yaml.safe_load((ROOT / "configs/datasets.yaml").read_text())["datasets"]["fogstar"]
    p2 = yaml.safe_load((ROOT / "configs/phase2.yaml").read_text())
    rc = J("reports/phase6/phase1_warning_recheck.json")
    defk = ("sensor_only", "random_forest"); d3 = res[3.0][defk]
    # verification inputs (generated by the Phase 6 scripts)
    ju = ET.parse(ROOT / "reports/phase6/junit_full_regression_final.xml").getroot(); ju = ju if ju.tag == "testsuite" else ju[0]
    art = J("reports/phase6/artifact_verification.json"); bfp = J("reports/phase6/browser_failure_paths.json"); mp = J("reports/phase6/browser_multi_patient.json")
    soak = J("reports/phase6/soak_report.json"); rec = J("reports/phase6/retrospective_recompute.json")
    vis = [J(f"reports/phase6/{f}") for f in ("visual_review_after_fix.json", "visual_review_after_fix_b.json")]
    vis_n = sum(len(v["results"]) for v in vis); vis_ok = sum(1 for v in vis for r in v["results"] if r["passed"])
    n_checks = sum(len(s["checks"]) for s in bfp["scenarios"])

    D = Deck()
    # 1 title
    s = D.p.slides.add_slide(D.blank); D.n += 1
    bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, D.p.slide_width, D.p.slide_height); bg.fill.solid(); bg.fill.fore_color.rgb = NAVY; bg.line.fill.background()
    D.text(s, "ParkiStep Twin", 0.8, 2.0, 11.5, 1.2, 60, True, WHITE)
    D.text(s, "A patient-specific digital twin for forecasting freezing-of-gait episodes in Parkinson's disease", 0.8, 3.3, 11.5, 1.2, 26, False, RGBColor(0x99, 0xF6, 0xE4))
    D.text(s, ["Research prototype - not a medical device", f"Team: {team.get('team_name', PEND)}   |   {team.get('college', PEND)}"], 0.8, 5.4, 11.5, 1.0, 16, False, RGBColor(0xD1, 0xD5, 0xDB))
    # 2 problem
    s = D.slide("The healthcare problem: freezing of gait")
    D.bullets(s, ["Freezing of gait (FoG): a sudden, brief inability to step forward in Parkinson's disease - a common cause of falls",
                  "Episodes are short, irregular and vary strongly between people and tasks",
                  "Detecting a freeze that has already started is easier than forecasting its onset",
                  "An advance warning could, in principle, give a person or a cueing system time to react - whether it helps is not shown here",
                  "ParkiStep asks a narrower, testable question: how much onset information is in a short window of wearable data?"])
    # 3 solution
    s = D.slide("Proposed solution: a continuously updating patient digital twin")
    for i, (h, b) in enumerate([("Historical context", "Clinical record per participant\n(age, disease duration, Hoehn & Yahr, UPDRS-III, FoG-Q, MoCA, FES-I, PDQ-8; missing stays missing)"),
                                ("Dynamic stream", "Wearable accelerometer + gyroscope observations replayed one sample at a time with a rolling buffer"),
                                ("Forecast + policy", "Trained model scores the next-H-seconds onset risk; a configurable warning policy turns scores into warnings"),
                                ("Inspectable history", "State, predictions and warnings are kept and shown in a dashboard, then compared with the annotations afterwards")]):
        D.card(s, 0.6 + (i % 2) * 6.2, 1.5 + (i // 2) * 2.6, 5.9, 2.4, h, b, size=15)
    # 4 why twin
    s = D.slide("Why a digital twin? Combining slow context with fast signals")
    D.bullets(s, ["The sensor window describes the last 2 seconds of movement; the clinical record describes the person",
                  "A twin keeps both in one patient-specific state and updates it as data arrive",
                  "Experiment: does adding the clinical record improve forecasts?",
                  (f"Answer so far: the paired participant bootstrap cannot distinguish clinical+sensor from sensor-only (every model and horizon)",),
                  (f"Only {rc['participants_clinical']} clinical profiles exist to learn from - a null result is expected, and not evidence that clinical data are useless",),
                  "The twin architecture is therefore demonstrated; a benefit from fusion is not"], size=20)
    # 5 data
    s = D.slide("Data sources: FoG-STAR (CC BY 4.0)")
    rows = [["Stream", "What it contains (from the dataset audit)"],
            ["Wearable sensors", f"{rc['rows_sensor']:,} samples at {rc['timestamp']['rows_per_s']:.0f} Hz; {len(fs['raw_channels'])} channels = {len(fs['positions'])} body positions ({', '.join(fs['positions'])}) x accelerometer + gyroscope"],
            ["Recordings", f"{rc['recordings']} recordings from {rc['participants_sensor']} participants; structured laboratory tasks"],
            ["Annotations", f"{rc['annotations']['fog_events']} FoG episodes from video (10 fps) in {rc['annotations']['participants_with_events']} participants; {rc['annotations']['events_running_to_recording_end']} run to the end of a recording"],
            ["Clinical record", f"{len(ds['clinical']['fields']) - 1} fields per participant; {len(rc['clinical_missing'])} fields have missing values and {len(rc['clinical_out_of_range'])} have out-of-documented-range values (kept, flagged)"],
            ["Audit status", "PASS_WITH_WARNINGS - every warning re-derived independently from the raw files (Phase 6)"]]
    D.table(s, rows, 0.6, 1.5, 12.1, [2.4, 9.7], size=14, h_row=0.62)
    D.text(s, "Source: Borzì, L. et al. (2026) Sci Data 13, 305, doi:10.1038/s41597-026-06645-1 - zenodo.org/records/17838806. The dataset is not redistributed in the repository.", 0.6, 5.9, 12.1, 0.8, 13, False, GREY)
    # 6 architecture
    s = D.slide("Architecture (as implemented)")
    D.image(s, ROOT / "docs/phase6/architecture_src/architecture_slide.png", 0.9, 1.15, w=11.5, h=5.25)
    for i, (t, col) in enumerate([("blue: live sensor data (past only)", RGBColor(0x25, 0x63, 0xEB)), ("green: clinical context", RGBColor(0x16, 0xA3, 0x4A)),
                                  ("orange: model artifacts / outputs", RGBColor(0xEA, 0x58, 0x0C)), ("red dashed: training-only labels", RGBColor(0xDC, 0x26, 0x26)),
                                  ("purple dashed: retrospective evaluation", RGBColor(0x7C, 0x3A, 0xED))]):
        D.text(s, t, 0.6 + i * 2.45, 6.45, 2.45, 0.35, 11.5, True, col)
    D.text(s, "Simplified from the complete diagram (docs/phase6/architecture.pdf, vector). Future labels never enter live prediction.", 0.6, 6.75, 12.1, 0.3, 11, False, GREY)
    # 7 ML methodology
    s = D.slide("AI/ML methodology")
    hz = ", ".join(f"{h:g}" for h in p2["windowing"]["horizons_s"]) if "windowing" in p2 and "horizons_s" in p2["windowing"] else "1, 3, 5"
    D.bullets(s, ["Target: does an annotated FoG episode START within the next H seconds? (H = %s s evaluated)" % hz,
                  "Input: only the trailing 2.0 s of sensor data, anchored every 0.2 s - never the future",
                  f"Windows that contain or follow an ongoing episode are excluded; futures not fully observed are censored",
                  f"Features: {ws['n_features']} sensor statistics (time-domain, frequency bands, cross-sensor); clinical fields added for the fusion experiment",
                  "Models: logistic regression, random forest, and two dummy baselines (prior, random)",
                  f"Eligible windows: {ws['horizons']['h3']['n_eligible']:,} at 3 s, of which {ws['horizons']['h3']['positive_fraction'] * 100:.1f}% are positive - a heavily imbalanced problem"], size=19)
    # 8 fusion
    s = D.slide("Data fusion: clinical + sensor vs sensor only (3 s horizon)")
    rows = [["Model", "Inputs", "Avg. precision", "Event sensitivity [95% CI]", "False alerts / h [95% CI]"]]
    for mdl in ("logistic", "random_forest"):
        for e, lab in (("sensor_only", "sensor only"), ("fusion", "clinical + sensor")):
            r = res[3.0][(e, mdl)]
            rows.append([mdl.replace("_", " "), lab, f"{r['ap']:.3f}", f"{r['sens']:.2f} [{r['sens_ci'][0]:.2f}, {r['sens_ci'][1]:.2f}] ({r['det']}/{r['evaluable']})", f"{r['fa']:.1f} [{r['fa_ci'][0]:.1f}, {r['fa_ci'][1]:.1f}]"])
    D.table(s, rows, 0.6, 1.5, 12.1, [2.0, 2.2, 1.8, 3.5, 2.6], size=14, h_row=0.55)
    D.bullets(s, ["Prevalence (chance level for average precision) at 3 s: %.3f" % m["results"]["sensor_only|dummy_prior|h3"]["window"]["average_precision"],
                  "Paired participant bootstrap (Phase 2 report, section 4): no distinguishable difference for any model or horizon"], x=0.6, y=4.9, h=1.8, size=17)
    # 9 engine
    s = D.slide("The digital-twin engine")
    for i, (h, b) in enumerate([("State + rolling buffer", "Per-patient state with a bounded buffer; observations are validated (timestamps, duplicates, order, missing channels) before use"),
                                ("Model gateway", "Loads an artifact only after SHA-256, schema, horizon and threshold checks; never substitutes another model"),
                                ("Warning manager", "Threshold, persistence and merge rules; explicit READY / STALE / unavailable states - a missing forecast is never shown as zero risk"),
                                ("History + persistence", "Predictions, warnings and events kept in memory; optional SQLite store with rollback on failure")]):
        D.card(s, 0.6 + (i % 2) * 6.2, 1.5 + (i // 2) * 2.6, 5.9, 2.4, h, b, size=15)
    # 10 dashboard (two slides, two large main-area crops each; sidebar cropped away)
    def crop_main(src, dst, h=940):
        im = Image.open(src); im.crop((255, 40, im.width, min(im.height, h))).save(dst)
    pairs = [("Dashboard (1/2): Overview and Early Warning", [("overview.png", "Overview: patient, twin state, forecast, warning, data quality"), ("early_warning.png", "Early Warning: forecast history, threshold and warning periods")]),
             ("Dashboard (2/2): Movement Monitor and Replay Analysis", [("movement_monitor.png", "Movement Monitor: observed channels with warning periods"), ("replay_analysis.png", "Replay Analysis: replay controls and retrospective analysis")])]
    for title, items in pairs:
        s = D.slide(title)
        for i, (f, cap) in enumerate(items):
            dst = FIG / f"main_{f}"; crop_main(ROOT / "reports/phase6/screenshots/viewports/1440x900" / f, dst)
            D.image(s, dst, 0.55 + i * 6.3, 1.25, w=6.0, h=4.9); D.text(s, cap, 0.55 + i * 6.3, 6.2, 6.0, 0.5, 12.5, True, TEAL, PP_ALIGN.CENTER)
        D.text(s, "Real screenshots from scripts/p6_visual_review.py: Patient 2, recording p2-s1-t4, replay stopped after a few seconds (state after Stop). Seven pages in total.", 0.6, 6.7, 12.1, 0.35, 11, False, GREY)
    # 11 workflow
    s = D.slide("Demonstration workflow: replaying a real recording")
    D.bullets(s, ["1. Launch:  streamlit run app/Home.py", "2. Replay Analysis: choose patient, one of that patient's recordings, model and speed", "3. Start: the recording is fed to the twin sample by sample - annotations withheld",
                  "4. Watch signals, scores and warnings update; Pause / Resume / Stop at any time, other pages follow live",
                  "5. When the replay ends the annotations appear: detections, false warnings, lead times - labelled in-sample"], size=20)
    D.image(s, FIG / "shot_early_warning.png", 8.6, 4.4, h=2.5) if False else None
    # 12 eval method
    s = D.slide("Evaluation methodology and leakage prevention")
    D.bullets(s, ["Participant-grouped 5-fold cross-validation: no person is in both training and test folds",
                  "Operating threshold chosen only on the training participants (false-alert budget 30 / hour)",
                  "Events matched strictly: a warning counts only if it fires BEFORE the annotated onset and within the horizon; alerts after onset never count",
                  "Annotations are withheld from the live twin and used only for labels (offline) and for scoring after a replay ends",
                  "Uncertainty: 300 participant bootstrap resamples, 95% percentile intervals",
                  "Saved final models were fitted on all participants -> dashboard replays are in-sample demonstrations"], size=19)
    # 13 results
    s = D.slide("Results: out-of-sample, participant-grouped")
    D.image(s, FIG / "sensitivity.png", 0.5, 1.25, w=6.2); D.image(s, FIG / "false_alerts.png", 6.75, 1.25, w=6.2)
    D.text(s, f"Default dashboard model (sensor-only random forest, 3 s): sensitivity {d3['sens']:.2f} ({d3['det']}/{d3['evaluable']}; CI {d3['sens_ci'][0]:.2f}-{d3['sens_ci'][1]:.2f}), "
              f"{d3['fa']:.1f} false alerts/h (CI {d3['fa_ci'][0]:.1f}-{d3['fa_ci'][1]:.1f}), median lead {d3['lead']:.2f} s, ROC-AUC {d3['auc']:.3f}. Error bars: 95% participant-bootstrap intervals.",
           0.6, 5.65, 12.1, 1.2, 15, False, NAVY)
    # 14 failures
    s = D.slide("Failure cases and lessons")
    D.bullets(s, ["Event sensitivity is low (at most %.2f) and the intervals are wide - the model finds few onsets" % max(v["sens"] for h in res.values() for v in h.values()),
                  f"Where it alerts, it alerts often: {d3['fa']:.0f} false alerts/h for the default model - far above the 30/h budget used for threshold selection, so the threshold did not transfer to new participants",
                  "Average precision is above chance but modest; probability scores are uncalibrated",
                  "Clinical fusion showed no measurable gain with 22 profiles",
                  "Some recordings give no forecast at all (sensor-quality gate); the UI says why instead of showing a score",
                  "Lesson: 22 people and ~1 hour of scored time cannot support a clinical claim; the value of the project is the transparent pipeline"], size=18)
    # 15 engineering
    s = D.slide("Reliability and verification (Phase 6 re-verification)")
    rows = [["Check", "Result (generated report in reports/phase6/)"],
            ["Full regression, real data + real model", f"{ju.attrib['tests']} tests: {int(ju.attrib['tests']) - int(ju.attrib['failures']) - int(ju.attrib['errors']) - int(ju.attrib['skipped'])} passed, {ju.attrib['failures']} failed, {ju.attrib['skipped']} skipped"],
            ["Model artifacts", f"{art['n_passed']}/{art['n_manifests']} pass SHA-256 + schema + real inference (tamper control detected)"],
            ["Replay metrics, recomputed independently", f"{rec['n_discrepancies']} discrepancies vs saved analysis ({len(rec['comparison'])} metrics)"],
            ["Browser failure paths (real Chromium)", f"{sum(1 for x in bfp['scenarios'] if x['passed'])}/{len(bfp['scenarios'])} scenarios, {n_checks} checks"],
            ["Multi-patient browser test", f"{len({c['patient_id'] for c in mp['discovered']['combinations']})} patients, {len(mp['discovered']['combinations'])} recordings, {sum(1 for v in mp['checks'].values() if v['pass'])}/{len(mp['checks'])} checks"],
            ["Soak test", f"{soak['cycles_run']} cycles in {soak['duration_s']:.0f} s; memory slope {soak['rss_slope_mb_per_cycle_after_warmup']:.2f} MB/cycle - short run, not proof of no leak"],
            ["Viewport review", f"{vis_ok}/{vis_n} page-viewport automated checks; a sample of 7 screenshots reviewed by hand (listed in PHASE5_FINAL_VERIFICATION.md)"]]
    D.table(s, rows, 0.6, 1.4, 12.1, [4.4, 7.7], size=13, h_row=0.58)
    # 16 privacy
    s = D.slide("Privacy, licensing and responsible use")
    D.bullets(s, ["Only a published, permitted open dataset (CC BY 4.0) is used; it is not redistributed in the repository and no secrets are committed",
                  "Replays are playback of recorded data - no live patient is monitored",
                  "Source code: MIT (copyright holder to be confirmed by the owner); dataset and libraries keep their own licences (THIRD_PARTY_NOTICES.md)",
                  "Trained models derive from the dataset: their redistribution terms must be confirmed before publication",
                  "Outputs are research scores - not diagnoses, not validated warnings, not for treatment or safety-critical use"], size=19)
    # 17 future
    s = D.slide("Future work (not implemented)")
    D.bullets(s, ["More participants and free-living recordings to test generalisation beyond laboratory tasks",
                  "Probability calibration and thresholds evaluated per participant, to bring false alerts under control",
                  "Personalisation: patient-specific baselines inside the twin",
                  "Prospective clinical evaluation with clinician input before any claim about outcomes",
                  "Live wearable ingestion (the engine already consumes observations one at a time)"], size=20)
    # 18 conclusion
    s = D.slide("Conclusion")
    D.bullets(s, ["Demonstrated: an end-to-end, reproducible, inspectable pipeline - data audit, leakage-safe forecasting, a checksum-gated digital twin, and a dashboard that shows failures honestly",
                  "Not demonstrated: clinically useful forecasting - sensitivity is low, false alerts are frequent, and fusion adds nothing measurable",
                  "The honest outcome is a transparent research baseline others can reproduce and improve"], size=22)
    # 19 resources
    s = D.slide("Repository and resources")
    rows = [["Item", "Value"], ["Public repository", team.get("repo_url", "[PENDING - available after publication]")], ["Demonstration video", team.get("video_url", "[PENDING - not yet recorded]")],
            ["Team", team.get("team_name", PEND)], ["Members", ", ".join(team["members"]) if team.get("members") else PEND],
            ["Dataset", "FoG-STAR, zenodo.org/records/17838806 (CC BY 4.0)"], ["Citation", "Borzì, L. et al. (2026) Sci Data 13, 305, doi:10.1038/s41597-026-06645-1"]]
    D.table(s, rows, 0.6, 1.5, 12.1, [3.0, 9.1], size=15, h_row=0.6)
    out = ROOT / a.out; out.parent.mkdir(parents=True, exist_ok=True); D.p.save(out); print("saved", out, D.n, "slides")


if __name__ == "__main__":
    main()
