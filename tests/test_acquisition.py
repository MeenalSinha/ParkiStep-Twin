import hashlib
import zipfile

import pytest

from helpers import make_workspace
from parkistep import acquisition
from parkistep.issues import PipelineError


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)


def test_extract_from_archive_is_flat_safe_and_no_overwrite(tmp_path):
    s = make_workspace(tmp_path, write_files=False)
    z = tmp_path / "bundle.zip"
    _zip(z, {"x/sensor_data.csv": "a\n1\n", "clinical_data.csv": "b\n2\n", "../evil.csv": "no", "other.bin": "zz"})
    info = acquisition.extract_from_archive(z, s)
    assert sorted(info["extracted"]) == ["clinical_data.csv", "sensor_data.csv"]
    assert not (tmp_path / "evil.csv").exists() and not (s.raw_dir / "other.bin").exists()
    (s.raw_dir / "sensor_data.csv").write_text("KEEP")
    info2 = acquisition.extract_from_archive(z, s)
    assert "sensor_data.csv" in info2["skipped_existing"] and (s.raw_dir / "sensor_data.csv").read_text() == "KEEP"
    acquisition.extract_from_archive(z, s, overwrite=True)
    assert (s.raw_dir / "sensor_data.csv").read_text() == "a\n1\n"
    prov = acquisition.write_provenance(s, info)
    assert "sha256" in prov.read_text()


def test_bad_archive_reported(tmp_path):
    s = make_workspace(tmp_path, write_files=False)
    bad = tmp_path / "bad.zip"; bad.write_bytes(b"not a zip")
    with pytest.raises(PipelineError, match="not a valid ZIP"):
        acquisition.extract_from_archive(bad, s)
    with pytest.raises(PipelineError, match="not found"):
        acquisition.extract_from_archive(tmp_path / "none.zip", s)


def _fake_record(content: bytes, md5=None, size=None):
    md5 = md5 or hashlib.md5(content).hexdigest()
    meta = {"metadata": {"version": "T"}, "files": [
        {"key": n, "size": size if size is not None else len(content), "checksum": f"md5:{md5}", "links": {"self": f"http://x/{n}"}}
        for n in ("sensor_data.csv", "clinical_data.csv")]}
    def dl(url, dest, timeout, progress):
        dest.write_bytes(content); progress(len(content), len(content))
    return (lambda url, timeout: meta), dl


def test_download_verified_with_injected_network(tmp_path):
    s = make_workspace(tmp_path, write_files=False)
    gj, dl = _fake_record(b"hello")
    info = acquisition.download_dataset(s, get_json=gj, download=dl)
    assert sorted(info["downloaded"]) == ["clinical_data.csv", "sensor_data.csv"]


def test_download_checksum_and_size_failures(tmp_path):
    s = make_workspace(tmp_path, write_files=False)
    gj, dl = _fake_record(b"hello", md5="0" * 32)
    with pytest.raises(PipelineError, match="Checksum mismatch"):
        acquisition.download_dataset(s, get_json=gj, download=dl)
    assert not list(s.raw_dir.glob("*"))        # discarded, nothing left behind
    gj, dl = _fake_record(b"hello", size=999)
    with pytest.raises(PipelineError, match="Incomplete"):
        acquisition.download_dataset(s, get_json=gj, download=dl)


def test_download_network_failure_is_actionable(tmp_path):
    s = make_workspace(tmp_path, write_files=False, project_edit=lambda p: p["acquisition"].update(retries=2, backoff_seconds=0))
    def boom(url, timeout): raise ConnectionError("offline")
    with pytest.raises(PipelineError, match="manually"):
        acquisition.download_dataset(s, get_json=boom)
