"""Markdown results report generated from computed metrics only (no hand-entered numbers)."""
from __future__ import annotations

from pathlib import Path

from .windows import horizon_key


def _f(v, d=3):
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(v, ci, d=3):
    return _f(v, d) if not ci else f"{_f(v, d)} [{_f(ci[0], d)}, {_f(ci[1], d)}]"


def write_report(s, payload: dict, path: Path) -> None:
    cfg = s.cfg; R = payload["results"]; summ = payload["window_summary"]
    L = []
    a = L.append
    a(f"# ParkiStep Twin - Phase 2 results (experiment `{payload['experiment_id']}`)\n")
    a("All numbers below are computed from the saved out-of-fold predictions of this run; nothing is hand-entered. "
      "Retrospective results on 22 participants are **not** evidence of clinical effectiveness.\n")
    ds = s.base.dataset
    a(f"## 1. Data and design\n\n- Dataset: {ds['name']}, version {ds['version']}; {ds['source']['url']}; license {ds['license']['name']}.")
    nmax = max(r["n_participants"] for r in R.values()); nev = max(r["event"]["n_events_evaluable"] for r in R.values())
    n_with = max(v["n_events_with_positive_anchor"] for v in summ["horizons"].values())
    a(f"- Participants contributing eligible windows: {nmax}; annotated events: {summ['n_events_total']} ({summ.get('events_touching_recording_end')} run to a recording end); "
      f"events with at least one eligible positive window before the sensor-quality filter: {n_with}; evaluable events after the filter (all four sensors valid): {nev}.")
    w = cfg["windowing"]
    a(f"- Input window {w['input_window_s']} s, anchor stride {w['stride_s']} s, horizons {w['horizons_s']} s; "
      f"windows containing or following an ongoing FoG sample are excluded; futures that are not fully observed are censored.")
    a(f"- Split: `{cfg['split']['strategy']}`, {cfg['split']['n_splits']} participant-grouped folds (seed {cfg['split']['seed']}). "
      f"Operating threshold policy `{cfg['threshold']['policy']}` (budget {cfg['threshold'].get('target_false_alerts_per_hour')} false alerts/h) chosen from inner grouped out-of-fold predictions of the training participants only.")
    a(f"- Alert policy: persistence {cfg['alerts']['persistence_anchors']} anchor(s), merge gap {cfg['alerts']['merge_gap_s']} s, cooldown {cfg['alerts']['cooldown_s']} s, minimum lead {cfg['alerts']['min_lead_s']} s. "
      "Event sensitivity = detected evaluable events / evaluable events; false alerts/h = alerts not valid for any annotated onset / (eligible anchors x stride) in hours.")
    a(f"- Bootstrap: {cfg['evaluation']['bootstrap']['n_resamples']} participant resamples, {int(cfg['evaluation']['bootstrap']['ci']*100)}% percentile intervals (resampling unit = participant).\n")
    a("## 2. Target construction per horizon\n")
    a("| Horizon (s) | eligible windows | positive | negative | positive % | events with a positive window | events without | participants with positives | monitored h |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, v in summ["horizons"].items():
        a(f"| {v['horizon_s']} | {v['n_eligible']} | {v['n_positive']} | {v['n_negative']} | {100*v['positive_fraction']:.2f} | {v['n_events_with_positive_anchor']} | {v['n_events_without_positive_anchor']} | {v['n_participants_with_positive']} | {v['monitored_time_s_eligible']/3600:.2f} |")
    a("\nBase exclusions (all anchors): " + ", ".join(f"{k}: {v}" for k, v in summ["base_status_counts"].items()) + ".")
    for k, v in summ["horizons"].items():
        a(f"\n- {v['horizon_s']} s: status counts {v['status_counts']}; events without a positive window: {v['event_exclusion_reasons'] or 'none'}.")
    a("\nThese counts are before the per-experiment sensor-quality filter; the number of windows actually used is shown in section 3.\n")
    a("## 3. Results by horizon\n")
    exps = [e["name"] for e in cfg["experiments"]]
    for H in cfg["windowing"]["horizons_s"]:
        hk = horizon_key(float(H))
        a(f"### Horizon {H} s\n")
        a("| Experiment | Model | windows (pos) | evaluable events | AP | ROC-AUC | Brier (prev. ref.) | event sensitivity [CI] | false alerts/h [CI] | median lead s [CI] | P/R/F1 at selected thr. |")
        a("|---|---|---:|---:|---|---:|---|---|---|---|---|")
        for e in exps:
            for m in cfg["models"]:
                r = R.get(f"{e}|{m}|{hk}")
                if not r:
                    a(f"| {e} | {m} | not run / failed | | | | | | | | |"); continue
                wm, em, bs = r["window"], r["event"], r["bootstrap"]
                t = wm["at_selected_thresholds"]
                lead = em["lead_time_s"]["median"] if em["lead_time_s"] else None
                a(f"| {e} | {m} | {wm['n_windows']} ({wm['n_positive']}) | {em['n_events_evaluable']} | {_ci(wm['average_precision'], bs['average_precision']['ci'])} | {_f(wm['roc_auc'])} | {_f(wm['brier'],4)} ({_f(wm.get('brier_prevalence_reference'),4)}) | "
                  f"{_ci(em['event_sensitivity'], bs['event_sensitivity']['ci'], 2)} ({em['n_events_detected']}/{em['n_events_evaluable']}) | {_ci(em['false_alerts_per_hour'], bs['false_alerts_per_hour']['ci'], 1)} | {_ci(lead, bs['median_lead_s']['ci'], 2)} | {_f(t['precision'],2)}/{_f(t['recall'],2)}/{_f(t['f1'],2)} |")
        a("")
    a("### Warning lead-time distributions (detected events, seconds before annotated onset)\n")
    a("| Experiment | Model | Horizon | n | min | p10 | p25 | median | p75 | p90 | max |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, r in R.items():
        lt = r["event"]["lead_time_s"]
        if lt and r["model"] in cfg["evaluation"]["selection"]["among"] + ["dummy_random"]:
            a(f"| {r['experiment']} | {r['model']} | {r['horizon_s']} | {r['event']['n_leads']} | {_f(lt['min'],2)} | {_f(lt['p10'],2)} | {_f(lt['p25'],2)} | {_f(lt['median'],2)} | {_f(lt['p75'],2)} | {_f(lt['p90'],2)} | {_f(lt['max'],2)} |")
    a("\n## 4. Clinical + sensor fusion vs sensor-only (paired participant bootstrap)\n")
    a("Delta = fusion - sensor-only; interval from the same participant resamples. An interval containing 0 means the data do not distinguish the two.\n")
    a("| Model | Horizon | dAP [CI] | dBrier [CI] | dEvent sens. [CI] | dFalse alerts/h [CI] | dMedian lead s [CI] | verdict (AP) |")
    a("|---|---:|---|---|---|---|---|---|")
    for row in payload["comparison"]:
        def cell(m, d=3):
            x = row[m]; return _ci(x["delta"], x["delta_ci"], d)
        ap = row["average_precision"]; ci = ap["delta_ci"]
        verdict = "n/a" if ap["delta"] is None or not ci else ("fusion better" if ci[0] > 0 else "fusion worse" if ci[1] < 0 else "no distinguishable difference")
        a(f"| {row['model']} | {row['horizon_s']} | {cell('average_precision')} | {cell('brier',4)} | {cell('event_sensitivity',2)} | {cell('false_alerts_per_hour',1)} | {cell('median_lead_s',2)} | {verdict} |")
    a("\n## 5. Preferred model per horizon (selection metric, not a claim of success)\n")
    for k, v in payload["selection"].items():
        a(f"- {k}: " + (f"{v['experiment']} / {v['model']} ({v['metric']} = {_f(v['value'])})" if v else "none"))
    a("\n## 6. Variability across folds and participants\n")
    for k, r in R.items():
        if r["model"] in cfg["evaluation"]["selection"]["among"]:
            pf = ", ".join(f"f{x['fold']}: AP {_f(x['average_precision'],2)}, sens {_f(x['event_sensitivity'],2)} ({x['n_events_evaluable']} ev)" for x in r["per_fold"])
            a(f"- `{k}` thresholds per fold {[round(t,3) for t in r['thresholds']]}; folds: {pf}; participants with evaluable events: {r['n_participants_with_evaluable_events']}/{r['n_participants']}.")
    a("\n## 7. Limitations\n")
    for t in ["Only a small number of participants contributes independent information; windows from one person are strongly correlated, so intervals are wide and fold estimates unstable.",
              "Clinical fusion has few independent clinical profiles to learn from; a lack of improvement is expected and a seeming improvement should be treated cautiously.",
              "Threshold and alert policy were fixed in configuration; event sensitivity depends strongly on the false-alert budget and on the monitored time (a few hours in total).",
              "Annotations come from 10 fps video; onset times are accurate only to that resolution. Recordings are structured lab tasks, not free-living monitoring.",
              "Windows that overlap or follow an ongoing episode are excluded by design, so events that follow another event within the input window cannot be evaluated; very short recordings contribute few windows.",
              "Sensor-quality filtering removes windows with missing sensors; results apply to the retained windows only.",
              "Scores are model outputs for research, not diagnoses; no claim of clinical effectiveness follows from retrospective data."]:
        a(f"- {t}")
    a("\n## 8. Citation\n\n" + ds["attribution"].replace("\n", " "))
    path.write_text("\n".join(L) + "\n")
