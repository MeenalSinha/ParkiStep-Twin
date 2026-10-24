#!/usr/bin/env python
"""Phase 6 release-hygiene audit: secret scan, env files, large files, ignore-rule coverage, local-path leakage, third-party licences.
Never prints a matched secret value, only file:line and the rule that matched. Writes reports/phase6/repo_audit.json.

    python scripts/p6_repo_audit.py [--root .] [--large-mb 5]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

SECRET_RULES = {
    "private_key_block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
    "anthropic_or_openai_key": r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b",
    "google_api_key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "bearer_literal": r"(?i)bearer\s+[A-Za-z0-9._-]{25,}",
    "assignment_of_secret": r"(?i)\b(?:api[_-]?key|secret|passwd|password|token|auth)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
    "url_with_credentials": r"[a-z]+://[^/\s:@]+:[^/\s:@]+@[^/\s]+",
}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
TEXT_EXT = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".cfg", ".ini", ".csv", ".html", ".svg", ".sh", ".log", ".xml", ".tex", ""}
LOCAL_PATH = re.compile(r"(/Users/[A-Za-z0-9._-]+|/home/(?!claude/proj)[A-Za-z0-9._-]+|C:\\Users\\[A-Za-z0-9._ -]+)")


def ignored_patterns(root: Path):
    pats = []
    gi = root / ".gitignore"
    if gi.exists():
        for ln in gi.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and not ln.startswith("!"):
                pats.append(ln)
    return pats


def is_ignored(rel: str, pats) -> bool:
    for p in pats:
        q = p.rstrip("/")
        if fnmatch.fnmatch(rel, q) or fnmatch.fnmatch(rel, q + "/*") or rel.startswith(q + "/") or any(fnmatch.fnmatch(part, q) for part in rel.split("/")):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--large-mb", type=float, default=5.0)
    ap.add_argument("--out", default="reports/phase6/repo_audit.json")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    pats = ignored_patterns(root)
    files = [p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)]
    git = (root / ".git").exists()
    secrets, local_paths, large, env_files, db_files, would_be_tracked_large = [], [], [], [], [], []
    for p in files:
        rel = p.relative_to(root).as_posix()
        ign = is_ignored(rel, pats)
        size = p.stat().st_size
        if p.name.startswith(".env") or p.suffix in {".pem", ".key", ".p12", ".pfx"} or p.name in {"id_rsa", "credentials.json"}:
            env_files.append(rel)
        if p.suffix in {".sqlite", ".db", ".sqlite3"}:
            db_files.append({"file": rel, "ignored_by_gitignore": ign})
        if size > a.large_mb * 1e6:
            large.append({"file": rel, "mb": round(size / 1e6, 1), "ignored_by_gitignore": ign})
        if not ign and size > 1e6 and p.suffix not in {".pdf", ".png", ".pptx"}:
            would_be_tracked_large.append({"file": rel, "mb": round(size / 1e6, 2)})
        if p.suffix.lower() in TEXT_EXT and size < 20e6 and not rel.startswith("data/raw/"):
            try:
                text = p.read_text(errors="ignore")
            except Exception:                                           # noqa: BLE001
                continue
            for name, rx in SECRET_RULES.items():
                for m in re.finditer(rx, text):
                    ln = text.count("\n", 0, m.start()) + 1
                    secrets.append({"file": rel, "line": ln, "rule": name, "ignored_by_gitignore": ign})
            if not rel.startswith("reports/phase6/") and p.suffix != ".log":
                for m in LOCAL_PATH.finditer(text):
                    local_paths.append({"file": rel, "line": text.count("\n", 0, m.start()) + 1, "match_prefix": m.group(0)[:12] + "..."})
    # dependency licences from installed metadata (declared by each package; not a legal determination)
    reqs = [re.split(r"[<>=!~ ]", l.split("#")[0].strip())[0] for l in (root / "requirements.txt").read_text().splitlines() if l.split("#")[0].strip()]
    lic = {}
    for r in reqs:
        try:
            md = metadata.metadata(r)
            cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
            lic[r] = {"version": md["Version"], "license_field": (md.get("License-Expression") or md.get("License") or "")[:80].replace("\n", " "), "classifiers": cls}
        except metadata.PackageNotFoundError:
            lic[r] = {"version": None, "license_field": "not installed"}
    git_info = {"is_git_repo": git}
    if git:
        git_info["history_secret_note"] = "run a history scan (e.g. gitleaks/trufflehog) before publishing; this script scans the working tree only"
    rep = {"schema": "parkistep.phase6.repo_audit/1", "root": ".", "n_files_scanned": len(files), "git": git_info,
           "license_file_present": any((root / n).exists() for n in ("LICENSE", "LICENSE.md", "LICENSE.txt")),
           "gitignore_patterns": pats, "secret_findings": secrets, "env_or_key_files": env_files, "database_files": db_files,
           "files_over_threshold": large, "unignored_files_over_1MB_non_document": would_be_tracked_large,
           "local_machine_path_mentions": local_paths[:50], "n_local_machine_path_mentions": len(local_paths),
           "dependency_licences_declared_by_packages": lic}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in rep.items() if k not in ("gitignore_patterns", "dependency_licences_declared_by_packages", "root", "schema", "git")}, indent=1))
    return 1 if secrets or env_files else 0


if __name__ == "__main__":
    sys.exit(main())
