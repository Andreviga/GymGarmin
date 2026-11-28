"""Regeração de treino: atualizar sem desfazer ajustes, prontidão sem cortar em cima da falta, falta que pesa
recomenda repor (versão curta junto da corrida leve) e IMC de quem é musculoso."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import clock, db, endurance, personal, schedule

PROF = {"name": "Teste", "age": 34, "sex": "M", "weight_kg": 98, "height_cm": 180, "training_months": 72, "goal": "hipertrofia",
        "available_days": [0, 1, 2, 3, 4, 5, 6], "session_minutes": 90,
        "endurance": {"sports": {"corrida": {"enabled": True, "env": "esteira", "sessions": 4, "long_day": 6,
                                             "goal": {"type": "prova", "distance": 5, "target": "0:20:00", "date": "2027-02-28"}}}}}


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "g.sqlite3"))
    clock.freeze(datetime(2026, 11, 30, 6, 0))
    from gymgarmin.app import app
    with TestClient(app) as cl:
        yield cl
    clock.freeze(None)


def test_atualizar_so_troca_o_que_mudou_e_mantem_ajustes(c):
    c.post("/api/generator/apply", json={"profile": PROF})
    ents = [e for e in c.get("/api/schedule").json()["entries"] if e["status"] == "planejado"]
    # um treino já no relógio e igual à planilha; outro ajustado pela pessoa; outro desatualizado (regra antiga)
    same, adj, old = ents[1], ents[2], ents[3]
    raw = db.get_entry(same["id"]); raw["garmin_workout_id"] = 555; db.save_entry(raw)
    raw = db.get_entry(adj["id"]); raw["alt_backup"] = {"items": raw["items"], "day_name": raw["day_name"]}
    raw["day_name"] = "Treino que eu escolhi"; raw["items"] = raw["items"][:2]; db.save_entry(raw)
    raw = db.get_entry(old["id"]); raw["day_name"] = "Corrida: Corre-caminha 5 × (1.5 min correndo + 2 min caminhando)"
    raw["items"] = raw["items"][:1]; db.save_entry(raw)
    prev = c.get("/api/schedule").json()["rebuild"]
    assert [p["date"] for p in prev] == [old["date"]]                  # o ajustado não aparece como mudança
    c.post("/api/schedule/rebuild")
    after = {e["date"]: e for e in c.get("/api/schedule").json()["entries"] if e["status"] == "planejado"}
    assert after[same["date"]]["id"] == same["id"] and db.get_entry(same["id"])["garmin_workout_id"] == 555   # não reenviado
    assert after[adj["date"]]["day_name"] == "Treino que eu escolhi"                                         # ajuste fica
    assert after[old["date"]]["day_name"] == old["day_name"]                                                 # o velho voltou ao plano
    assert "555" not in (db.kv_get("garmin_orphans") or "")


def test_prontidao_nao_corta_series_de_musculo_que_ja_faltou(c):
    from gymgarmin import readiness
    c.post("/api/generator/apply", json={"profile": PROF})
    strength = [e for e in c.get("/api/schedule").json()["entries"] if e.get("kind") == "forca"]
    first, later = strength[0], strength[-1]
    c.post(f"/api/schedule/{first['id']}/skip", json={"strategy": "seguir"})
    from gymgarmin.app import _missed_muscles
    protect = _missed_muscles(datetime.fromisoformat(later["date"]).date())
    assert protect
    raw = db.get_entry(later["id"])
    new = readiness.adjust_entry(raw, "trocar", {}, force=True, protect=protect)
    from gymgarmin import catalog
    for a, b in zip(raw["items"], new["items"]):
        if (a.get("phase") or "principal") != "principal" or not a.get("sets"):
            continue
        prim = set((catalog.get(a["exercise_id"]) or {}).get("primaryMuscles") or [])
        if prim & protect:
            assert b["sets"] == a["sets"] and b["rpe_target"] <= 7                 # séries mantidas, só esforço baixa
        else:
            assert b["sets"] < a["sets"]
    assert any("mantiveram" in x or "manteve" in x for x in new["changes"][-1:])


def test_falta_que_pesa_recomenda_versao_curta_junto_da_corrida_leve(c):
    c.post("/api/generator/apply", json={"profile": {**PROF, "session_minutes": 120}})
    ents = c.get("/api/schedule").json()["entries"]
    tue = next(e for e in ents if e.get("kind") == "forca" and e["date"] > "2026-11-30")
    r = c.post(f"/api/schedule/{tue['id']}/skip", json={"dry_run": True}).json()
    mv = next(o for o in r["options"] if o["id"] == "mover")
    assert r["recommended"] == "mover" and "versão curta" in mv["text"] and "semana fica curta" in r["why"]
    c.post(f"/api/schedule/{tue['id']}/skip", json={"strategy": "mover", "to_date": mv["to_date"]})
    moved = next(e for e in c.get("/api/schedule").json()["entries"] if e.get("moved_from") == tue["date"])
    raw = db.get_entry(moved["id"])
    assert raw.get("compact") and len(raw["items"]) < len(db.get_entry(tue["id"])["items"])
    assert schedule._day_minutes(moved["date"], [db.get_entry(e["id"]) for e in c.get("/api/schedule").json()["entries"]
                                                 if e["status"] in ("planejado", "feito")]) <= 120 * schedule.DAY_TOL + 1


def test_falta_sem_como_repor_explica_honesto(c):
    c.post("/api/generator/apply", json={"profile": PROF})
    sat = [e for e in c.get("/api/schedule").json()["entries"] if e.get("kind") == "forca"][-1]
    clock.freeze(datetime.fromisoformat(sat["date"] + "T06:00"))
    r = c.post(f"/api/schedule/{sat['id']}/skip", json={"dry_run": True}).json()
    assert r["recommended"] == "seguir" and "não faz perder" in r["why"] and "isolada" not in r["why"]


def test_imc_alto_de_quem_e_musculoso_nao_vira_corre_caminha():
    p = {"weight_kg": 98, "height_cm": 180, "training_months": 72}
    assert not personal.derive(p)["force_run_walk"] and not personal.derive(p)["no_jumps"]
    assert personal.derive({**p, "training_months": 12})["force_run_walk"]           # pouco tempo de treino: vale a regra
    assert personal.derive({"weight_kg": 115, "height_cm": 180, "training_months": 72})["force_run_walk"]   # IMC 35+
    from datetime import date
    wp = endurance.week_plan({**PROF, "endurance": PROF["endurance"]}, date(2026, 11, 30), date(2026, 11, 30), "elite",
                             recent={"corrida": 6})
    assert not any("Corre-caminha" in s["endurance"]["title"] for s in wp["sports"]["corrida"]["sessions"])
