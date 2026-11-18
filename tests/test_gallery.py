"""Galeria de aparelhos: leitura dos dados, busca, vídeo/foto conferidos e API."""
import json

import pytest
from fastapi.testclient import TestClient

from gymgarmin import gallery

SAMPLE = [
    {"machine_id": "mx_pendulo", "name": "Agachamento pêndulo", "name_en": "Pendulum Squat", "category": "pernas",
     "is_new": False, "availability_br": "média", "existing_exercise_ids": ["Pendulum_Squat"],
     "exercises": [{"name": "Pêndulo fundo", "how": "desça até a coxa passar da paralela", "muscles": ["quadriceps"]}],
     "tips": ["Costas no encosto"], "why": "Carrega o quadríceps alongado.",
     "videos": [{"url": "https://www.youtube.com/watch?v=abcdefghijk", "status": "ok", "channel": "Canal X"},
                {"url": "https://youtu.be/ZZZZZZZZZZZ", "status": "no_embed"},
                {"url": "https://www.youtube.com/shorts/MMMMMMMMMMM", "status": "missing"}],
     "photos": [{"url": "https://upload.wikimedia.org/x.jpg", "status": "ok", "author": "Fulano", "license": "CC BY-SA 4.0"}]},
    {"machine_id": "mx_remada", "name": "Remada articulada", "category": "costas", "availability_br": "comum",
     "exercises": [], "videos": [], "photos": []},
]


@pytest.fixture()
def sample(tmp_path, monkeypatch):
    f = tmp_path / "g.json"
    f.write_text(json.dumps(SAMPLE, ensure_ascii=False))
    monkeypatch.setattr(gallery, "DATA", f)
    gallery.load.cache_clear(); gallery.by_exercise.cache_clear()
    yield
    gallery.load.cache_clear(); gallery.by_exercise.cache_clear()


def test_yt_id_from_all_url_shapes():
    assert gallery.yt_id("https://www.youtube.com/watch?v=abcdefghijk&t=3") == "abcdefghijk"
    assert gallery.yt_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert gallery.yt_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert gallery.yt_id("https://example.com/x") is None


def test_detail_keeps_working_media_and_marks_link_only(sample):
    d = gallery.detail(gallery.get("mx_pendulo"))
    assert [v["id"] for v in d["videos"]] == ["abcdefghijk", "ZZZZZZZZZZZ"]  # o "missing" some
    assert d["videos"][0]["embed"] and not d["videos"][1]["embed"]
    assert d["photos"][0]["author"] == "Fulano"


def test_search_by_name_category_and_accents(sample):
    assert [m["id"] for m in gallery.search("pendulo")] == ["mx_pendulo"]
    assert [m["id"] for m in gallery.search(category="costas")] == ["mx_remada"]
    s = gallery.search()[0]
    assert s["id"] == "mx_pendulo" and s["has_video"] and s["thumb"].startswith("https://upload.wikimedia.org")
    assert gallery.by_exercise()["Pendulum_Squat"] == "mx_pendulo"


def test_api_machines_and_exercise_link(sample, tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    from gymgarmin.app import app
    with TestClient(app) as c:
        lst = c.get("/api/machines").json()
        assert {x["key"] for x in lst["categories"]} >= {"pernas", "costas"} and len(lst["items"]) == 2
        d = c.get("/api/machines/mx_pendulo").json()
        assert d["category_pt"] == "Pernas" and d["linked"][0]["id"] == "Pendulum_Squat"
        assert c.get("/api/machines/nao_existe").status_code == 404
        assert c.get("/api/exercises/Pendulum_Squat").json()["machine_id"] == "mx_pendulo"


def test_media_check_classifies_responses(monkeypatch):
    calls = {}

    def fake(url, method="GET", timeout=6.0):
        calls[url] = method
        if "oembed" in url:
            if "aaaaaaaaaaa" in url:
                return 200, json.dumps({"title": "Leg press", "author_name": "Canal"}).encode()
            if "bbbbbbbbbbb" in url:
                return 401, b""
            return 404, b""
        return (200, b"") if url.endswith("ok.jpg") else (404, b"")
    monkeypatch.setattr(gallery, "_fetch", fake)
    r = gallery.check_media([{"machine_id": "m", "videos": ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb",
                                                              "https://youtu.be/ccccccccccc"],
                              "photos": ["https://upload.wikimedia.org/ok.jpg", "https://upload.wikimedia.org/no.jpg"]}])["m"]
    assert [v["status"] for v in r["videos"]] == ["ok", "no_embed", "missing"]
    assert r["videos"][0]["channel"] == "Canal"
    assert [p["status"] for p in r["photos"]] == ["ok", "missing"]
