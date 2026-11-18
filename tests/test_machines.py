"""Aparelhos pesquisados (data/machines.json): catálogo, busca em português de academia, gerador e mídia."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gymgarmin import catalog, generator

DATA = Path(__file__).resolve().parents[1] / "gymgarmin" / "data"
MUSCLES = {"abdominals", "abductors", "adductors", "biceps", "calves", "chest", "forearms", "glutes", "hamstrings", "lats",
           "lower back", "middle back", "neck", "quadriceps", "shoulders", "traps", "triceps"}


def test_machines_are_valid_and_in_catalog():
    ms = json.loads((DATA / "machines.json").read_text())
    garmin = {(g["category"], g["exercise"]) for g in json.loads((DATA / "garmin_catalog.json").read_text())}
    assert len(ms) >= 20
    for m in ms:
        ex = catalog.get(m["id"])
        assert ex and ex["top_machine"] and ex["evidence"]
        assert set(m["primaryMuscles"]) | set(m["secondaryMuscles"]) <= MUSCLES
        assert m["pattern"] in generator.PATTERNS
        assert (m["garmin"]["category"], m["garmin"]["exercise"]) in garmin
        # foto só de movimento equivalente, sempre identificada
        assert bool(ex["images"]) == bool(ex["image_like"])


def test_common_machines_enter_generator_rotation_rare_do_not():
    ms = json.loads((DATA / "machines.json").read_text())
    for m in ms:
        inside = m["id"] in generator.PATTERNS[m["pattern"]]
        assert inside == (m["availability_br"] != "rara"), m["id"]


def test_search_understands_gym_words():
    def names(q, eq=None):
        return [x["id"] for x in catalog.search(q, None, eq, None, None, limit=10)]
    assert "Thigh_Abductor" in names("abdução", "machine")
    assert "Pendulum_Squat" in names("pêndulo")
    assert "Assisted_Pull-Up_Machine" in names("graviton")
    assert "Lying_T-Bar_Row" in names("cavalinho")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c


def test_media_has_photo_or_pictogram_for_every_new_machine(client):
    med = client.get("/api/media").json()
    for m in json.loads((DATA / "machines.json").read_text()):
        assert m["id"] in med["f"] or m["id"] in med["p"], m["id"]
    for cid in ("cardio_treno", "cardio_versaclimber", "cardio_ergometro_braco", "cardio_esteira_curva"):
        assert med["p"].get(cid) in ("sled", "climber", "armerg", "run")
