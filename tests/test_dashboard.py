import importlib.util

from streamlit.testing.v1 import AppTest

from jobscout.demo import seed


def test_dashboard_renders_demo(data_dir, monkeypatch):
    seed(data_dir)
    monkeypatch.setenv("JOBSCOUT_DATA_DIR", str(data_dir.data_dir))
    path = importlib.util.find_spec("jobscout.dashboard").origin
    at = AppTest.from_file(path, default_timeout=60).run()
    assert not at.exception
    assert [m.value for m in at.metric][:3] == ["2", "1", "1"]
