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
        assert inside == (m["availability_br"] != "rara" and m.get("rotation") is not False), m["id"]


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


# ------------------------------------------------------------------ pesquisa nos fabricantes (nov/2026) ---
CATS = {"pernas", "gluteo", "costas", "peito", "ombros", "bracos", "core", "cardio", "funcional"}


def test_gallery_items_are_consistent():
    g = json.loads((DATA / "machine_gallery.json").read_text())
    ids = [m["machine_id"] for m in g]
    assert len(ids) == len(set(ids)) and len(g) >= 120
    for m in g:
        assert m["category"] in CATS and m["name"] and m["availability_br"] in ("comum", "média", "rara"), m["machine_id"]
        strength = m["category"] != "cardio"  # os de cardio apontam para os ids antigos, unidos em "cardio_*" no catálogo
        for eid in (m.get("existing_exercise_ids") or []) if strength else []:
            assert catalog.get(eid), (m["machine_id"], eid)
        for e in m.get("exercises") or []:
            assert e["name"] and set(e.get("muscles") or []) <= MUSCLES, (m["machine_id"], e.get("name"))
            if e.get("exercise_id") and strength:
                assert catalog.get(e["exercise_id"]), (m["machine_id"], e["exercise_id"])
        for b in m.get("brand_models") or []:
            assert b["brand"] and b["url"].startswith("http"), (m["machine_id"], b)
        assert isinstance(m.get("settings") or [], list) and isinstance(m.get("how_to_recognize") or "", str)
    strength = [m for m in g if m["category"] != "cardio"]
    assert sum(1 for m in strength if m.get("settings")) >= 100 and sum(1 for m in strength if m.get("how_to_recognize")) >= 100


def test_owner_photos_are_findable_by_the_names_people_use():
    def names(q):
        return [x["id"] for x in catalog.search(q, limit=5)]
    assert names("pressão de pernas")[0] == "Horizontal_Leg_Press" and names("leg press 90")[0] == "Horizontal_Leg_Press"
    assert names("leg press 90 unilateral") == ["Horizontal_Leg_Press_Unilateral"]
    assert names("glúteo ajoelhado") == ["Kneeling_Hip_Extension_Machine"]
    from gymgarmin import gallery
    by_brand = lambda q: {x["id"] for x in gallery.search(q)}  # noqa: E731
    assert "mx_leg_press_horizontal" in by_brand("hammer strength") and "mx_leg_press_horizontal" in by_brand("leg press 90")
    assert "mx_hip_thrust_machine" in by_brand("total health") and by_brand("glutebuilder") == {"mx_kneeling_glute_isolator"}
    lp = gallery.detail(gallery.get("mx_leg_press_horizontal"))
    assert any(e.get("exercise_id") == "Horizontal_Leg_Press_Unilateral" for e in lp["exercises"]) and lp["settings"] and lp["how_to_recognize"]


def test_new_machine_exercises_only_enter_by_choice():
    ms = {m["id"]: m for m in json.loads((DATA / "machines.json").read_text())}
    for eid in ("LeverLegPress_Machine", "Horizontal_Leg_Press_Unilateral", "ReverseLunge_Machine", "StandingHipAbduction_Machine",
                "StandingHipAdduction_Machine", "RearDeltRow_Machine", "Kneeling_Hip_Extension_Machine"):
        assert ms[eid]["rotation"] is False and eid not in generator.PATTERNS[ms[eid]["pattern"]]
        assert catalog.get(eid)["kind"] == "strength"
    assert catalog.get("Gripper_Machine")["primaryMuscles"] == ["forearms"]
