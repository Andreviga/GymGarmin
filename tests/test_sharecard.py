"""Cartões para postar nas redes: dados da musculação, da corrida e o que vem do Garmin (mapa, parciais, zonas)."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, garmin_sync, sharecard


def test_polyline_e_percurso_sem_coordenadas_reais():
    # exemplo clássico da documentação do Google
    pts = sharecard.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    loop = [(-23.55 + 0.001 * k, -46.63 + 0.0005 * (k % 7)) for k in range(60)]
    shape = sharecard.route_shape(loop)
    assert shape and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in shape)
    assert not any(abs(x) > 1 or abs(y) > 1 for x, y in shape)     # nada de latitude/longitude no cartão


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "s.sqlite3"))
    clock.freeze(datetime(2026, 11, 24, 9, 0))
    db.init()
    db.save_profile({"name": "André Teste", "weight_kg": 90})
    from gymgarmin.app import app
    with TestClient(app) as cl:
        yield cl
    clock.freeze(None)


def test_cartao_de_musculacao(c):
    db.save_session({"date": "2026-11-17T07:00", "day_name": "Empurrar", "items": [
        {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "sets": [{"reps": 8, "load_kg": 80, "done": True}] * 3}]})
    s = db.save_session({"date": "2026-11-24T07:00", "day_name": "Empurrar", "items": [
        {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "sets": [{"reps": 8, "load_kg": 85, "done": True}] * 3},
        {"exercise_id": "Triceps_Pushdown", "sets": [{"reps": 12, "load_kg": 30, "done": True}] * 2}]})
    d = c.get(f"/api/share/{s['id']}").json()
    st = d["strength"]
    assert d["who"] == "André" and st["sets"] == 5 and st["volume_kg"] == 3 * 8 * 85 + 2 * 12 * 30
    assert st["exercises"][0]["best"] == "8 × 85 kg"
    m = {x["key"]: x for x in st["muscles"]}
    assert m["chest"]["sets"] == 3 and m["chest"]["region"] == "peito" and m["triceps"]["sets"] == 3.5   # 3×½ (supino) + 2
    assert st["prs"] and st["prs"][0]["name"]                   # supino passou de 80 para 85: recorde
    assert d["week"]["days"] == 1 and d["week"]["streak_weeks"] == 2
    assert "endurance" not in d


def test_corrida_do_relogio_busca_extras_uma_vez(c, monkeypatch):
    s = db.save_session({"date": "2026-11-24T06:04", "source": "garmin", "garmin_activity_id": 999, "items": [
        {"exercise_id": "cardio_esteira_corrida", "sport": "corrida", "duration_min": 25.1, "distance_km": 3.11,
         "pace_s_km": 484, "avg_hr": 126, "max_hr": 165}], "garmin_summary": {"avg_hr": 126, "max_hr": 165, "kcal": 231}})
    calls = []

    class Fake:
        def get_activity_polyline(self, aid):
            calls.append("poly")
            return {"gPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@_ulLnnqC_mqNvxq`@"}

        def get_activity_splits(self, aid):
            return {"lapDTOs": [{"distance": 1000, "duration": 470}, {"distance": 1000, "duration": 490}, {"distance": 50, "duration": 30}]}

        def get_activity_hr_in_timezones(self, aid):
            return [{"zoneNumber": 1, "secsInZone": 300}, {"zoneNumber": 2, "secsInZone": 900}]

        def get_activity_weather(self, aid):
            return {"temp": 77, "relativeHumidity": 70, "weatherTypeDTO": {"desc": "Partly Cloudy"}}

    monkeypatch.setattr(garmin_sync, "client", lambda: Fake())
    monkeypatch.setattr(garmin_sync, "status", lambda: {"has_tokens": True})
    monkeypatch.setattr(garmin_sync, "persist", lambda: None)
    d = c.get(f"/api/share/{s['id']}").json()
    e = d["endurance"]
    assert e["distance_km"] == 3.11 and e["pace_s_km"] == 484 and d["avg_hr"] == 126 and d["kcal"]
    assert e["route"] and [x["pace_s"] for x in e["splits"]] == [470, 490]     # volta de 50 m não conta
    assert e["hr_zones"] == [300, 900, 0, 0, 0] and e["weather"]["temp_c"] == 25
    c.get(f"/api/share/{s['id']}")
    assert calls == ["poly"]                                                     # guardado: não busca de novo


def test_treino_de_outra_pessoa_nao(c):
    assert c.get("/api/share/9999").status_code == 404
