"""Séries do relógio sem nome: blocos, categorias confirmadas e uso na evolução de cargas."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, garmin_sync, history, watch_labels as wl

TODAY = date(2026, 11, 16)


def _sets(txt: str) -> list[dict]:
    """'12x54 9x75' → séries (reps x carga)."""
    return [{"reps": int(a), "load_kg": float(b), "duration_s": 25} for a, b in (p.split("x") for p in txt.split())]


# um treino real do relógio (14/11): vários exercícios seguidos num item só, sem nome
REAL = ("25x20 25x40 12x54 10x75 10x75 10x75 20x45 20x61 10x75 15x89 9x103 27x96 15x125 10x138 14x138 "
        "21x89 20x103 11x110 10x110 10x50 10x100 10x140 8x140 11x40 8x40 11x40 11x40")


def test_blocks_cut_where_the_load_restarts():
    sets = _sets(REAL)
    got = [[int(s["load_kg"]) for s in sets[a:b]] for a, b in wl.blocks(sets)]
    assert got == [[20, 40, 54, 75, 75, 75], [45, 61, 75, 89, 103], [96, 125, 138, 138], [89, 103, 110, 110], [50, 100, 140, 140], [40, 40, 40, 40]]
    # série sem carga digitada fica no bloco; subida de aquecimento (50 → 100) não corta
    assert wl.blocks(_sets("12x86 12x104 8x122 7x0 15x27 14x27")) == [(0, 4), (4, 6)]
    assert wl.blocks([]) == [] and wl.blocks(_sets("10x40")) == [(0, 1)]


def _unknown(txt: str) -> dict:
    return {"exercise_id": wl.UNKNOWN_ID, "_name": "Exercício não identificado pelo relógio", "sets": _sets(txt),
            "garmin": {"category": "UNKNOWN", "name": None}}


def _guess(eid: str, cat: str, txt: str) -> dict:
    return {"exercise_id": eid, "sets": _sets(txt), "garmin": {"category": cat, "name": None}, "guess": True}


def _session(sid: int, day: str, items: list[dict]) -> dict:
    return {"id": sid, "date": f"{day}T06:00", "source": "garmin", "garmin_activity_id": 1000 + sid, "notes": "Força", "items": items}


def test_review_groups_categories_and_asks_only_recent_unknown_sessions():
    sessions = [
        _session(1, "2026-11-14", [_guess("Dumbbell_Bicep_Curl", "CURL", "10x12 10x12 8x14"), _unknown("12x54 9x75 8x82 20x30 15x40")]),
        _session(2, "2026-11-10", [_guess("Dumbbell_Bicep_Curl", "CURL", "10x12"), _guess("Barbell_Squat", "SQUAT", "10x60 8x70")]),
        _session(3, "2026-09-01", [_unknown("10x40 10x40")]),  # antigo: conta no total, mas ninguém lembra
    ]
    entry = {"id": 9, "date": "2026-11-14", "kind": "forca", "day_name": "Pernas",
             "items": [{"exercise_id": "Leg_Press", "sets": 3, "phase": "principal"}, {"exercise_id": "Leg_Extensions", "sets": 3, "phase": "principal"}]}
    r = wl.review(sessions, [entry], {}, TODAY)
    cats = {c["category"]: c for c in r["categories"]}
    assert cats["CURL"]["sets"] == 4 and cats["CURL"]["sessions"] == 2 and cats["CURL"]["label"] == "Rosca"
    assert cats["CURL"]["current"]["id"] == "Dumbbell_Bicep_Curl" and cats["CURL"]["options"][0]["id"] == "Dumbbell_Bicep_Curl"
    assert r["categories"][0]["category"] == "CURL" and r["guess_sets"] == 6 and r["unknown_sets"] == 7
    assert [s["session_id"] for s in r["sessions"]] == [1]
    blks = r["sessions"][0]["blocks"]
    assert [(b["from"], b["to"]) for b in blks] == [(0, 3), (3, 5)] and r["sessions"][0]["planned"] == "Pernas"
    assert [b["suggestion"]["id"] for b in blks] == ["Leg_Press", "Leg_Extensions"]  # pela ordem do treino planejado
    assert wl.review(sessions, [entry], {"dismissed": [1]}, TODAY)["sessions"] == []


def test_category_answer_fixes_past_sessions_and_merges_neighbors():
    s = _session(1, "2026-11-14", [_guess("Dumbbell_Bicep_Curl", "CURL", "10x12"), _guess("Dumbbell_Bicep_Curl", "CURL", "9x14"),
                                   _guess("Barbell_Squat", "SQUAT", "10x60")])
    exact = {"exercise_id": "Dumbbell_Bicep_Curl", "sets": _sets("10x10"), "garmin": {"category": "CURL", "name": "DUMBBELL_BICEPS_CURL"}}
    s2 = _session(2, "2026-11-12", [exact])
    changed = wl.apply_category([s, s2], "CURL", "Hammer_Curls")
    assert changed == [s] and [i["exercise_id"] for i in s["items"]] == ["Hammer_Curls", "Barbell_Squat"]
    assert len(s["items"][0]["sets"]) == 2 and "guess" not in s["items"][0] and s["items"][0]["labeled"] == "categoria"
    assert s["items"][1]["guess"] and s2["items"][0]["exercise_id"] == "Dumbbell_Bicep_Curl"  # nome exato do relógio não muda


def test_label_a_block_keeps_the_rest_unknown():
    s = _session(1, "2026-11-14", [_unknown("12x54 9x75 8x82 20x30 15x40 12x50")])
    wl.apply_label(s, 0, 3, 5, "Leg_Extensions")
    ids = [i["exercise_id"] for i in s["items"]]
    assert ids == [wl.UNKNOWN_ID, "Leg_Extensions", wl.UNKNOWN_ID]
    assert [len(i["sets"]) for i in s["items"]] == [3, 2, 1] and s["items"][1]["labeled"] == "bloco" and "_name" not in s["items"][1]
    with pytest.raises(ValueError):
        wl.apply_label(s, 1, 0, 1, "Leg_Press")  # já tem nome
    with pytest.raises(ValueError):
        wl.apply_label(s, 0, 2, 9, "Leg_Press")  # fora do item
    # rotulado entra na evolução de cargas; sem nome continua fora
    pts = history._points_by_exercise([s])
    assert set(pts) == {"Leg_Extensions"} and pts["Leg_Extensions"][0]["top_load"] == 40


def test_next_import_uses_the_confirmed_category():
    sets = {"exerciseSets": [
        {"setType": "ACTIVE", "exercises": [{"category": "CURL", "name": None, "probability": 90}], "repetitionCount": 10, "weight": 14000, "duration": 30},
        {"setType": "ACTIVE", "exercises": [{"category": "CURL", "name": "DUMBBELL_BICEPS_CURL", "probability": 90}], "repetitionCount": 10, "weight": 12000, "duration": 30},
        {"setType": "ACTIVE", "exercises": [{"category": "SQUAT", "name": None, "probability": 90}], "repetitionCount": 8, "weight": 60000, "duration": 30},
    ]}
    act = {"activityId": 1, "activityType": {"typeKey": "strength_training"}, "startTimeLocal": "2026-11-16 06:00:00", "duration": 1800}
    plain = garmin_sync.activity_to_session(act, sets)["items"]
    assert plain[0]["guess"] and plain[0]["exercise_id"] == "Dumbbell_Bicep_Curl"
    items = garmin_sync.activity_to_session(act, sets, cat_prefs={"CURL": "Hammer_Curls"})["items"]
    assert items[0]["exercise_id"] == "Hammer_Curls" and "guess" not in items[0] and items[0]["labeled"] == "categoria"
    assert items[1]["exercise_id"] != "Hammer_Curls" and "guess" not in items[1]  # nome exato vence a preferência
    assert items[2]["guess"]  # categoria ainda não confirmada continua palpite


# ------------------------------------------------------------------------------- API ---
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "a.sqlite3"))
    from gymgarmin.app import app

    clock.freeze(datetime(2026, 11, 16, 12, 0))
    try:
        with TestClient(app) as c:
            yield c
    finally:
        clock.freeze(None)


def test_api_confirm_category_and_label_block(client):
    a = db.save_session({k: v for k, v in _session(1, "2026-11-14", [_guess("Dumbbell_Bicep_Curl", "CURL", "10x12 10x12"),
                                                                    _unknown("12x54 9x75 8x82 20x30 15x40")]).items() if k != "id"})
    b = db.save_session({k: v for k, v in _session(2, "2026-11-10", [_guess("Dumbbell_Bicep_Curl", "CURL", "10x14")]).items() if k != "id"})
    r = client.get("/api/watch/review").json()
    assert r["categories"][0]["category"] == "CURL" and r["sessions"][0]["session_id"] == a["id"]
    assert client.get("/api/history").json()["exercises"][0]["watch_guess"]

    # "é isso mesmo" também responde: o palpite vira confirmado e para de perguntar
    r = client.post("/api/watch/category", json={"category": "CURL", "exercise_id": "Hammer_Curls"}).json()
    assert r["changed"] == 2 and r["categories"] == [] and r["confirmed"][0]["exercise"]["id"] == "Hammer_Curls"
    assert db.get_session(b["id"])["items"][0]["exercise_id"] == "Hammer_Curls"
    h = client.get("/api/history").json()["exercises"]
    assert [x["exercise_id"] for x in h] == ["Hammer_Curls"] and not h[0]["watch_guess"]

    blk = r["sessions"][0]["blocks"][0]
    r = client.post("/api/watch/label", json={"session_id": a["id"], "item": blk["item"], "from": blk["from"], "to": blk["to"],
                                               "exercise_id": "Leg_Press"}).json()
    assert r["exercise"]["id"] == "Leg_Press" and len(r["sessions"][0]["blocks"]) == 1 and r["unknown_sets"] == 2
    assert "Leg_Press" in [x["exercise_id"] for x in client.get("/api/history").json()["exercises"]]
    assert client.post("/api/watch/dismiss", json={"session_id": a["id"]}).json()["sessions"] == []

    # validação
    assert client.post("/api/watch/category", json={"category": "UNKNOWN", "exercise_id": "Leg_Press"}).status_code == 400
    assert client.post("/api/watch/category", json={"category": "CURL", "exercise_id": "corrida_rua"}).status_code == 400
    assert client.post("/api/watch/label", json={"session_id": 999, "item": 0, "from": 0, "to": 1, "exercise_id": "Leg_Press"}).status_code == 404
    assert client.post("/api/watch/label", json={"session_id": a["id"], "item": 0, "from": 0, "to": 1, "exercise_id": "Leg_Press"}).status_code == 409


def test_loose_sets_between_the_same_exercise_suggest_that_exercise():
    s = _session(1, "2026-11-14", [_guess("Barbell_Squat", "SQUAT", "10x68"), _unknown("11x0"), _guess("Barbell_Squat", "SQUAT", "11x0"),
                                   _guess("Barbell_Deadlift", "DEADLIFT", "7x68"), _unknown("8x0"), _guess("Side_Lateral_Raise", "LATERAL_RAISE", "8x10")])
    blks = wl.review([s], [], {}, TODAY)["sessions"][0]["blocks"]
    assert blks[0]["suggestion"]["id"] == "Barbell_Squat"  # entre duas séries de agachamento
    assert blks[1]["suggestion"] is None                    # vizinhos diferentes: não chuta
    wl.apply_label(s, 1, 0, 1, "Barbell_Squat")
    assert s["items"][1]["exercise_id"] == "Barbell_Squat" and s["items"][1]["labeled"] == "bloco"


# ------------------------------------------------------------------ achados da revisão ---
def test_blocks_also_cut_on_big_load_jumps_and_bodyweight_runs():
    def cut(txt):
        sets = _sets(txt)
        return [[int(s["load_kg"]) for s in sets[a:b]] for a, b in wl.blocks(sets)]
    # sem subida de carga em cada exercício: elevação lateral 8 kg → leg press 120 → supino máquina 40…
    assert cut("12x8 12x8 12x8 10x120 10x120 10x120 10x40 10x40 10x45 10x45") == [[8, 8, 8], [120, 120, 120], [40, 40, 45, 45]]
    # peso do corpo (0 kg) depois de supino: é outro exercício; uma série isolada a 0 (carga não digitada) continua no bloco
    assert cut("8x60 8x60 8x60 10x0 9x0 8x0") == [[60, 60, 60], [0, 0, 0]]
    assert cut("8x60 8x0 8x60") == [[60, 0, 60]]
    assert cut("10x0 10x0 10x0 12x40 12x40") == [[0, 0, 0], [40, 40]]
    assert cut("25x20 25x40 12x54 10x75") == [[20, 40, 54, 75]] and cut("10x50 10x100 10x140 8x140") == [[50, 100, 140, 140]]  # aquecimento em escada: um bloco só


def test_stale_screen_cannot_name_the_wrong_sets():
    s = _session(1, "2026-11-14", [_unknown("12x54 9x75 20x30 15x40 12x80 12x80 30x20 28x20")])
    A, B, C = wl.review([s], [], {}, TODAY)["sessions"][0]["blocks"]
    assert [(x["from"], x["to"]) for x in (A, B, C)] == [(0, 2), (2, 6), (6, 8)]
    sig = lambda b: [[x["reps"], x["load_kg"]] for x in b["sets"]]  # noqa: E731
    wl.apply_label(s, B["item"], B["from"], B["to"], "Leg_Press", expect=sig(B))   # o item vira: [sem nome A] [Leg press] [sem nome C]
    with pytest.raises(ValueError):                                                # a tela antiga ainda aponta C na posição velha
        wl.apply_label(s, C["item"], C["from"], C["to"], "Leg_Extensions", expect=sig(C))
    wl.apply_label(s, A["item"], A["from"], A["to"], "Barbell_Squat", expect=sig(A))  # A não mudou de lugar nem de conteúdo: vale
    fresh = wl.review([s], [], {}, TODAY)["sessions"][0]["blocks"][0]
    wl.apply_label(s, fresh["item"], fresh["from"], fresh["to"], "Leg_Extensions", expect=sig(fresh))
    assert [(i["exercise_id"], len(i["sets"])) for i in s["items"]] == [("Barbell_Squat", 2), ("Leg_Press", 4), ("Leg_Extensions", 2)]
    with pytest.raises(ValueError):   # mesma posição, séries diferentes das que a tela mostrou
        wl.apply_label(_session(2, "2026-11-14", [_unknown("10x40 10x40")]), 0, 0, 2, "Leg_Press", expect=[[10, 40], [9, 40]])


def test_answers_can_be_fixed_category_block_and_dont_remember():
    s = _session(1, "2026-11-14", [_guess("Dumbbell_Bicep_Curl", "CURL", "10x12 10x12"), _unknown("12x54 9x75")])
    wl.apply_category([s], "CURL", "Hammer_Curls")
    assert wl.apply_category([s], "CURL", "Barbell_Curl") == [s] and s["items"][0]["exercise_id"] == "Barbell_Curl"   # troca vale de novo
    assert wl.apply_category([s], "CURL", "Barbell_Curl") == []                                                       # nada a mudar
    wl.apply_label(s, 1, 0, 2, "Leg_Press")
    r = wl.review([s], [], {"categories": {"CURL": "Barbell_Curl"}}, TODAY)
    assert r["confirmed"][0]["exercise"]["id"] == "Barbell_Curl" and r["labeled"][0]["exercise"]["id"] == "Leg_Press" and r["sessions"] == []
    wl.unlabel(s, r["labeled"][0]["item"])
    r = wl.review([s], [], {"dismissed": [1]}, TODAY)
    assert r["sessions"] == [] and r["dismissed"][0]["session_id"] == 1 and r["labeled"] == []
    assert wl.review([s], [], {}, TODAY)["sessions"][0]["blocks"][0]["sets"][0]["i"] == 0
    with pytest.raises(ValueError):
        wl.unlabel(s, 0)   # item de categoria não é "bloco"


def test_cardio_and_warmup_guesses_are_not_asked_and_sessions_are_counted_once():
    warm = {"exercise_id": "cardio_ski_erg", "sets": _sets("10x0"), "garmin": {"category": "CARDIO", "name": None}, "guess": True}
    biset = [_guess("Dumbbell_Bicep_Curl", "CURL", "10x12"), _guess("Triceps_Pushdown", "TRICEPS_EXTENSION", "10x30"),
             _guess("Dumbbell_Bicep_Curl", "CURL", "10x12"), _guess("Triceps_Pushdown", "TRICEPS_EXTENSION", "10x30"), warm]
    r = wl.review([_session(1, "2026-11-14", biset)], [], {}, TODAY)
    cats = {c["category"]: c for c in r["categories"]}
    assert set(cats) == {"CURL", "TRICEPS_EXTENSION"} and cats["CURL"]["sessions"] == 1 and cats["CURL"]["sets"] == 2


def test_api_unlabel_ask_again_and_manual_import_uses_confirmed_category(client):
    a = db.save_session({k: v for k, v in _session(1, "2026-11-14", [_unknown("12x54 9x75 8x82")]).items() if k != "id"})
    blk = client.get("/api/watch/review").json()["sessions"][0]["blocks"][0]
    body = {"session_id": a["id"], "item": blk["item"], "from": blk["from"], "to": blk["to"], "exercise_id": "Leg_Press"}
    assert client.post("/api/watch/label", json={**body, "expect": [[12, 54], [9, 75], [8, 99]]}).status_code == 409   # não é o que a tela mostrou
    r = client.post("/api/watch/label", json={**body, "expect": [[x["reps"], x["load_kg"]] for x in blk["sets"]]}).json()
    assert r["labeled"][0]["exercise"]["id"] == "Leg_Press"
    r = client.post("/api/watch/unlabel", json={"session_id": a["id"], "item": r["labeled"][0]["item"]}).json()
    assert r["labeled"] == [] and r["sessions"][0]["session_id"] == a["id"]
    assert client.post("/api/watch/dismiss", json={"session_id": a["id"]}).json()["dismissed"][0]["session_id"] == a["id"]
    r = client.post("/api/watch/dismiss", json={"session_id": a["id"], "undo": True}).json()
    assert r["dismissed"] == [] and r["sessions"][0]["session_id"] == a["id"]
