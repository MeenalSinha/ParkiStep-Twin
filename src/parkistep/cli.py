"""Command-line interface: acquire | audit | prepare."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import acquisition
from .config import ConfigError, load_settings
from .issues import PipelineError
from .pipeline import FAIL, NOT_RUN, prepare_outputs, run_audit
from .reporting import write_reports


def _parser(command: str | None = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="parkistep", description="ParkiStep Twin - Phase 1 data foundation")
    p.add_argument("--config", default="configs/datasets.yaml", help="dataset profiles YAML (default: %(default)s)")
    p.add_argument("--project-config", default=None, help="project YAML (default: project.yaml next to --config)")
    p.add_argument("--dataset", default=None, help="dataset profile key (default: active_dataset in project config)")
    p.add_argument("--project-root", default=None, help="root for relative paths (default: parent of the configs directory)")
    p.add_argument("--data-root", default=None, help="override paths.data_root")
    p.add_argument("--processed-dir", default=None, help="override paths.processed_root")
    p.add_argument("--reports-dir", default=None, help="override paths.reports_dir")
    p.add_argument("--figures-dir", default=None, help="override paths.figures_dir")
    p.add_argument("--log-level", default=None, help="override logging.level")
    p.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                   help="override any setting, e.g. --set datasets.fogstar.sampling.expected_hz=100 (repeatable)")
    p.add_argument("--download", action="store_true", help="allow network download of the dataset")
    p.add_argument("--overwrite", action="store_true", help="allow overwriting existing raw files")
    p.add_argument("--from-archive", default=None, help="extract the configured files from a local ZIP")
    p.add_argument("--figures", default=None, help="comma-separated figure names to generate, or 'none'")
    p.add_argument("--no-figures", action="store_true", help="skip figure generation")
    p.add_argument("--no-reports", action="store_true", help="do not write report files")
    p.add_argument("--fail-on-warnings", action="store_true", help="exit non-zero for PASS_WITH_WARNINGS too")
    return p


def main(argv: list[str] | None = None, command: str | None = None) -> int:
    parser = _parser()
    if command is None:
        parser.add_argument("command", choices=["acquire", "audit", "prepare"])
    args = parser.parse_args(argv)
    command = command or args.command
    ov = list(args.overrides)
    for flag, key in (("data_root", "paths.data_root"), ("processed_dir", "paths.processed_root"),
                      ("reports_dir", "paths.reports_dir"), ("figures_dir", "paths.figures_dir"),
                      ("log_level", "logging.level")):
        if getattr(args, flag):
            ov.append(f"{key}={getattr(args, flag)}")
    if args.download:
        ov.append("acquisition.allow_download=true")
    if args.overwrite:
        ov.append("acquisition.overwrite=true")
    if args.no_figures or args.figures == "none":
        ov.append("visualization.enabled=false")
    elif args.figures:
        ov.append("visualization.figures=[" + ",".join(f.strip() for f in args.figures.split(",")) + "]")
    try:
        settings = load_settings(args.config, args.project_config, args.dataset, args.project_root, ov)
        lg = settings.project["logging"]
        logging.basicConfig(level=getattr(logging, str(lg.get("level", "INFO")).upper(), logging.INFO),
                            format=lg.get("format"), stream=sys.stderr, force=True)
        if lg.get("file"):
            fh = logging.FileHandler(settings.resolve(lg["file"]))
            fh.setFormatter(logging.Formatter(lg.get("format")))
            logging.getLogger().addHandler(fh)
        return {"acquire": _acquire, "audit": _audit, "prepare": _prepare}[command](settings, args)
    except (ConfigError, PipelineError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _acquire(settings, args) -> int:
    acq = settings.project.get("acquisition", {})
    overwrite = bool(acq.get("overwrite", False))
    if args.from_archive:
        info = acquisition.extract_from_archive(Path(args.from_archive), settings, overwrite)
    elif acq.get("allow_download", False):
        info = acquisition.download_dataset(settings, overwrite)
    else:
        from .ingestion import get_adapter
        disc = get_adapter(settings).discover()
        if disc.complete:
            print(f"Dataset already present in {settings.raw_dir}: {[p.name for p in disc.found.values()]}")
            return 0
        print(acquisition.missing_instructions(settings, [f"{k} ({v})" for k, v in disc.missing.items()]), file=sys.stderr)
        print("\nNo action taken: downloading is disabled. Use --download or --from-archive.", file=sys.stderr)
        return 1
    prov = acquisition.write_provenance(settings, info)
    print(json.dumps(info, indent=2))
    print(f"Provenance written to {prov}")
    return 0


def _audit(settings, args, prepare: bool = False) -> int:
    result = run_audit(settings)
    paths = {}
    if not args.no_reports:
        paths = write_reports(settings, result.report, result.join_audit)
    figs = None
    if not args.no_reports and result.sensor is not None:
        from .visualization import generate_figures
        figs = generate_figures(settings, result)
        result.report["figures"] = figs
        write_reports(settings, result.report, result.join_audit)
    print(f"Audit status: {result.status}\n{result.status_reason}")
    for i in result.issues.errors:
        print(f"  {i}", file=sys.stderr)
    if paths:
        print("Reports: " + ", ".join(paths.values()))
    if figs:
        print(f"Figures generated: {len(figs['generated'])}; skipped: {figs['skipped'] or 'none'}")
    if prepare:
        out = prepare_outputs(settings, result)
        print("Processed outputs: " + json.dumps(out, indent=2))
    if result.status in (FAIL, NOT_RUN):
        return 1
    if args.fail_on_warnings and result.status != "PASS":
        return 1
    return 0


def _prepare(settings, args) -> int:
    return _audit(settings, args, prepare=True)


if __name__ == "__main__":
    sys.exit(main())
