import pytest

from tuberipper import diskcache


@pytest.fixture(autouse=True)
def _isolated_disk_cache(tmp_path, monkeypatch):
    """Every test gets an empty MusicBrainz disk cache of its own, never the
    real library's."""
    monkeypatch.setenv("TUBERIPPER_CACHE_DIR", str(tmp_path / "cache"))
    diskcache.reset()
    yield
    diskcache.reset()
