"""Search results say whether an album is missing, partly in the library, or complete."""

import pytest
from fastapi.testclient import TestClient

from tuberipper import catalog, diskcache, library_index, server

client = TestClient(server.app)


def _albums(rows):
    return {"songs": [], "albums": rows}


def _search(monkeypatch, rows, counts, tracklists=None):
    """rows: catalog albums; counts: {title: songs in the library}; tracklists: {browse_id: number of songs}."""
    monkeypatch.setattr(catalog, "search", lambda q, songs_only=False: _albums([dict(r) for r in rows]))
    monkeypatch.setattr(library_index, "album_count", lambda root, artist, title: counts.get(title, 0))
    calls = []

    def tracks(browse_id, itunes_id=None):
        calls.append(browse_id)
        n = (tracklists or {}).get(browse_id)
        if n is None:
            raise RuntimeError("catalog down")
        return {"tracks": [{"title": f"t{i}"} for i in range(n)]}
    monkeypatch.setattr(catalog, "album_tracks", tracks)
    res = client.get("/api/search", params={"q": "x"}).json()
    return res["albums"], calls


def test_none_partial_and_full_from_the_itunes_count(monkeypatch):
    rows = [{"title": "Missing", "artist": "A", "track_count": 10}, {"title": "Some", "artist": "A", "track_count": 20},
            {"title": "All", "artist": "A", "track_count": 12}]
    out, calls = _search(monkeypatch, rows, {"Some": 1, "All": 12})
    assert [a["library_state"] for a in out] == ["none", "partial", "full"]
    assert [a["in_library"] for a in out] == [False, True, True]
    assert [a["library_count"] for a in out] == [0, 1, 12]
    assert calls == []          # the count was already known: no extra lookups


def test_more_songs_than_the_catalog_lists_is_complete(monkeypatch):
    out, _ = _search(monkeypatch, [{"title": "Big", "artist": "A", "track_count": 10}], {"Big": 14})
    assert out[0]["library_state"] == "full"


def test_an_album_without_a_count_is_measured_from_its_tracklist_once(monkeypatch):
    rows = [{"title": "Some", "artist": "A", "ytm_browse_id": "MPREb_some"}, {"title": "All", "artist": "A", "ytm_browse_id": "MPREb_all"},
            {"title": "Missing", "artist": "A", "ytm_browse_id": "MPREb_none"}]
    out, calls = _search(monkeypatch, rows, {"Some": 3, "All": 9}, {"MPREb_some": 20, "MPREb_all": 9, "MPREb_none": 5})
    assert [a["library_state"] for a in out] == ["partial", "full", "none"]
    assert [a.get("track_count") for a in out][:2] == [20, 9]
    assert sorted(calls) == ["MPREb_all", "MPREb_some"]    # albums with nothing in the library are not looked up
    out, calls = _search(monkeypatch, rows, {"Some": 3, "All": 9}, {"MPREb_some": 20, "MPREb_all": 9})
    assert calls == [] and [a["library_state"] for a in out] == ["partial", "full", "none"]   # remembered


def test_an_unknown_total_counts_as_partial_not_complete(monkeypatch):
    out, calls = _search(monkeypatch, [{"title": "Some", "artist": "A", "ytm_browse_id": "MPREb_x"}], {"Some": 4}, {})
    assert calls == ["MPREb_x"] and out[0]["library_state"] == "partial" and not out[0].get("track_count")
    out, _ = _search(monkeypatch, [{"title": "Some", "artist": "A"}], {"Some": 4})   # no way to look it up at all
    assert out[0]["library_state"] == "partial"


def test_only_a_few_albums_are_looked_up(monkeypatch):
    rows = [{"title": f"Al{i}", "artist": "A", "ytm_browse_id": f"B{i}"} for i in range(12)]
    out, calls = _search(monkeypatch, rows, {f"Al{i}": 1 for i in range(12)}, {f"B{i}": 10 for i in range(12)})
    assert len(calls) == server.ALBUM_TOTAL_LOOKUPS
    assert all(a["library_state"] == "partial" for a in out)


def test_library_index_counts_songs_per_album(tmp_path):
    for name in ("1 - a.m4a", "2 - b.m4a", "3 - c.m4a"):
        d = tmp_path / "Artist" / "Album"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"x")
    library_index.invalidate()
    root = str(tmp_path)
    assert library_index.album_count(root, "Artist", "Album") == 3
    assert library_index.album_count(root, "Artist", "Other") == 0
    assert library_index.has_album(root, "Artist", "Album") and not library_index.has_album(root, "Nobody", "Album")
    library_index.invalidate()
