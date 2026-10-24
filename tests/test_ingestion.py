from helpers import make_workspace
from parkistep.ingestion import get_adapter


def test_discovery_and_load(tmp_path):
    s = make_workspace(tmp_path)
    a = get_adapter(s)
    d = a.discover()
    assert d.complete and set(d.found) == {"clinical", "sensor"}
    tbl, issues = a.load("sensor", d.found["sensor"])
    assert tbl is not None and not issues.has_errors and len(tbl.df) == 600 and len(tbl.sha256) == 64


def test_missing_files_give_useful_errors(tmp_path):
    s = make_workspace(tmp_path, write_files=False)
    d = get_adapter(s).discover()
    assert not d.complete and {i.code for i in d.issues} == {"FILE_MISSING"}
    assert "pattern" in d.issues[0].location and d.issues[0].action


def test_invalid_format_handled(tmp_path):
    s = make_workspace(tmp_path)
    a = get_adapter(s)
    (s.raw_dir / "sensor_data.csv").write_bytes(b"")
    _, iss = a.load("sensor", s.raw_dir / "sensor_data.csv")
    assert iss[0].code == "FILE_EMPTY"
    (s.raw_dir / "sensor_data.csv").write_bytes(b"\xff\xfe\x00bad\xff")
    _, iss = a.load("sensor", s.raw_dir / "sensor_data.csv")
    assert iss.has_errors
    (s.raw_dir / "sensor_data.csv").write_text("a,b\n")
    _, iss = a.load("sensor", s.raw_dir / "sensor_data.csv")
    assert iss[0].code == "NO_ROWS"


def test_unsupported_format_config(tmp_path):
    s = make_workspace(tmp_path, profile_edit=lambda ds: ds["files"]["sensor"].update(format="xlsx"))
    a = get_adapter(s)
    _, iss = a.load("sensor", s.raw_dir / "sensor_data.csv")
    assert iss[0].code == "FORMAT_UNSUPPORTED"
