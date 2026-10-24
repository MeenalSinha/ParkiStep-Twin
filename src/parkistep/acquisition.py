"""Dataset acquisition: locate, extract from a local archive, or download.

Raw data is never overwritten unless `overwrite` is explicitly enabled, and nothing is
fabricated: when files are missing the caller gets precise instructions instead.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import Settings
from .issues import Issues, PipelineError

log = logging.getLogger(__name__)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def missing_instructions(settings: Settings, missing: list[str]) -> str:
    acq = settings.dataset.get("acquisition", {})
    return (
        f"Dataset '{settings.dataset['name']}' is not fully available. Missing: {', '.join(missing)}.\n"
        f"Expected location: {settings.raw_dir}\n"
        f"{acq.get('manual_instructions', 'Place the dataset files in the expected location.')}\n"
        "Or run with --download (needs network access) / --from-archive <file.zip>."
    )


def extract_from_archive(archive: Path, settings: Settings, overwrite: bool = False) -> dict:
    """Safely extract the configured members of a local ZIP into the raw directory.

    Only files whose *basename* matches `acquisition.archive_members` are extracted, and they are
    written flat under the raw directory using the basename only (no path traversal possible).
    """
    archive = Path(archive)
    if not archive.is_file():
        raise PipelineError(f"Archive not found: {archive}. Pass an existing ZIP with --from-archive.")
    patterns = settings.dataset.get("acquisition", {}).get("archive_members") or ["*"]
    raw = settings.raw_dir
    raw.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise PipelineError(f"{archive} is not a valid ZIP archive (possibly an incomplete download): {exc}") from exc
    extracted, skipped = [], []
    with zf:
        bad = zf.testzip()
        if bad:
            raise PipelineError(f"Archive {archive} is corrupt (first bad member: {bad}). Re-download it.")
        chosen: dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename).name
            if not base or not any(fnmatch.fnmatch(base, pat) for pat in patterns):
                continue
            if base in chosen:
                raise PipelineError(
                    f"Archive {archive} contains several members named {base!r}; cannot choose safely. "
                    "Narrow `acquisition.archive_members` in the dataset profile.")
            chosen[base] = info
        for base, info in chosen.items():
            target = raw / base
            if target.exists() and not overwrite:
                skipped.append(base)
                log.warning("Not overwriting existing raw file %s (use --overwrite to replace)", target)
                continue
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(base)
            log.info("Extracted %s -> %s", base, target)
    return {"method": "local_archive", "archive": archive.name, "extracted": extracted, "skipped_existing": skipped}


def _default_get_json(url: str, timeout: float):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _default_download(url: str, dest: Path, timeout: float, progress: Callable[[int, int | None], None]):
    import requests
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        done = 0
        with open(dest, "wb") as fh:
            for block in r.iter_content(1 << 20):
                fh.write(block)
                done += len(block)
                progress(done, total)


def download_dataset(settings: Settings, overwrite: bool = False,
                     get_json: Callable | None = None, download: Callable | None = None) -> dict:
    """Download the configured files from the Zenodo record (needs network access).

    `get_json` / `download` are injectable for testing. Files are fetched to `*.part`, verified
    against the record's size and MD5 checksum, then renamed.
    """
    acq_cfg = settings.project.get("acquisition", {})
    timeout = float(acq_cfg.get("timeout_seconds", 30))
    retries = int(acq_cfg.get("retries", 3))
    backoff = float(acq_cfg.get("backoff_seconds", 2.0))
    src = settings.dataset.get("source", {})
    if not src.get("zenodo_record") or not src.get("zenodo_api"):
        raise PipelineError(
            "Download is not configured for this dataset: set `source.zenodo_record` and `source.zenodo_api` "
            "in the dataset profile, or acquire the files manually.")
    get_json = get_json or _default_get_json
    download = download or _default_download
    api = src["zenodo_api"].format(record=src["zenodo_record"])
    wanted = settings.dataset.get("acquisition", {}).get("expected_files", [])
    try:
        meta = _retry(lambda: get_json(api, timeout), retries, backoff, f"fetch record metadata {api}")
    except Exception as exc:
        raise PipelineError(
            f"Could not reach {api}: {exc}. Check network access, or acquire the files manually.\n"
            + missing_instructions(settings, wanted)) from exc
    files = {Path(f.get("key") or f.get("filename", "")).name: f for f in meta.get("files", [])}
    absent = [w for w in wanted if w not in files]
    if absent:
        raise PipelineError(f"Record {src['zenodo_record']} does not list expected file(s): {', '.join(absent)}. "
                            "The dataset layout may have changed; update `acquisition.expected_files`.")
    raw = settings.raw_dir
    raw.mkdir(parents=True, exist_ok=True)
    out = {"method": "zenodo_download", "record": src["zenodo_record"],
           "record_version": (meta.get("metadata") or {}).get("version"),
           "record_doi": meta.get("doi"), "downloaded": [], "skipped_existing": []}
    for name in wanted:
        info = files[name]
        target = raw / name
        if target.exists() and not overwrite:
            out["skipped_existing"].append(name)
            log.warning("Not overwriting existing raw file %s (use --overwrite)", target)
            continue
        url = (info.get("links") or {}).get("self") or (info.get("links") or {}).get("download")
        if not url:
            raise PipelineError(f"Record metadata for {name} has no download link.")
        part = target.with_suffix(target.suffix + ".part")
        last = {"pct": -10}

        def progress(done, total, _n=name, _l=last):
            if total:
                pct = int(100 * done / total)
                if pct >= _l["pct"] + 10:
                    _l["pct"] = pct
                    log.info("Downloading %s: %d%%", _n, pct)

        _retry(lambda: download(url, part, timeout, progress), retries, backoff, f"download {name}")
        expected_size = info.get("size")
        if expected_size is not None and part.stat().st_size != int(expected_size):
            part.unlink(missing_ok=True)
            raise PipelineError(f"Incomplete download of {name}: got {part.stat().st_size if part.exists() else 0} "
                                f"bytes, record lists {expected_size}. Retry the download.")
        checksum = info.get("checksum", "")
        if checksum.startswith("md5:"):
            h = hashlib.md5()
            with open(part, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest() != checksum[4:]:
                part.unlink(missing_ok=True)
                raise PipelineError(f"Checksum mismatch for {name} (expected {checksum[4:]}, got {h.hexdigest()}). "
                                    "The file was discarded; retry the download.")
        part.replace(target)
        out["downloaded"].append(name)
    return out


def _retry(fn, retries: int, backoff: float, what: str):
    last = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            return fn()
        except Exception as exc:  # network errors vary by library
            last = exc
            log.warning("%s failed (attempt %d/%d): %s", what, attempt, retries, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last


def write_provenance(settings: Settings, acquisition: dict) -> Path:
    """Record what is actually in the raw directory (names, sizes, SHA-256) and how it got there."""
    raw = settings.raw_dir
    prov_name = settings.dataset.get("acquisition", {}).get("provenance_file", "PROVENANCE.json")
    files = {}
    for p in sorted(raw.glob("*")):
        if p.is_file() and p.name != prov_name and not p.name.endswith(".part"):
            files[p.name] = {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
    ds = settings.dataset
    prov_path = raw / prov_name
    previous = {}
    if prov_path.exists():
        try:
            previous = json.loads(prov_path.read_text())
        except json.JSONDecodeError:
            previous = {}
    record = {
        "dataset": ds["name"], "version_declared": ds["version"],
        "source_url": ds.get("source", {}).get("url"), "license": ds.get("license", {}),
        "attribution": ds.get("attribution"),
        "acquired_at_utc": previous.get("acquired_at_utc") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "acquisition": acquisition, "files": files,
        "note": "Raw files are kept unchanged; all transformations are applied to copies in data/processed.",
    }
    prov_path.write_text(json.dumps(record, indent=2))
    return prov_path
