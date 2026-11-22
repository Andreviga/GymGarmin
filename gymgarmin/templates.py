"""Modelos de planilha prontos. Exercícios marcados como "evitar" pelas limitações do
usuário são trocados automaticamente por uma alternativa para o mesmo músculo."""

from __future__ import annotations

import copy

from . import catalog
from .analysis import suggest_for_muscle
from .limitations import check_exercise, max_zone


def S(eid, sets, reps, rest=90, **kw):
    return {"exercise_id": eid, "sets": sets, "reps": reps, "rest_s": rest, **kw}


def C(eid, minutes, zone, **kw):
    return {"exercise_id": eid, "duration_min": minutes, "zone": zone, **kw}


TEMPLATES = {
    "ppl": {
        "name": "ABC – Empurrar / Puxar / Pernas",
        "description": "Hipertrofia, 3 a 6×/semana. Volume equilibrado entre empurrar e puxar.",
        "days_per_week": 3,
        "days": [
            {"name": "A – Empurrar", "items": [
                C("cardio_esteira_caminhada", 5, 2, notes="aquecimento"),
                S("Barbell_Bench_Press_-_Medium_Grip", 4, "6-10", 150),
                S("Incline_Dumbbell_Press", 3, "8-12", 120),
                S("Dumbbell_Shoulder_Press", 3, "8-12", 120),
                S("Side_Lateral_Raise", 3, "12-15", 60),
                S("Triceps_Pushdown_-_Rope_Attachment", 3, "10-15", 60),
                S("Cable_Crunch", 3, "12-15", 60),
            ]},
            {"name": "B – Puxar", "items": [
                C("cardio_remo", 5, 2, notes="aquecimento"),
                S("Wide-Grip_Lat_Pulldown", 4, "8-12", 120),
                S("Seated_Cable_Rows", 3, "8-12", 120),
                S("One-Arm_Dumbbell_Row", 3, "10-12", 90),
                S("Face_Pull", 3, "15", 60),
                S("Barbell_Curl", 3, "8-12", 60),
                S("Hammer_Curls", 3, "10-12", 60),
            ]},
            {"name": "C – Pernas", "items": [
                C("cardio_bike_vertical", 5, 2, notes="aquecimento"),
                S("Barbell_Squat", 4, "6-10", 180),
                S("Leg_Press", 3, "10-12", 120),
                S("Romanian_Deadlift", 3, "8-10", 150),
                S("Lying_Leg_Curls", 3, "10-12", 90),
                S("Barbell_Hip_Thrust", 3, "8-12", 90),
                S("Standing_Calf_Raises", 4, "10-15", 60),
                S("Plank", 3, None, 60, duration_s=45),
            ]},
        ],
    },
    "fullbody": {
        "name": "Full body 3× + cardio",
        "description": "Corpo inteiro 3×/semana com cardio zona 2 ao final. Ótimo para iniciantes/retorno.",
        "days_per_week": 3,
        "days": [
            {"name": "Full A", "items": [
                S("Goblet_Squat", 3, "10-12", 90),
                S("Dumbbell_Bench_Press", 3, "8-12", 90),
                S("Seated_Cable_Rows", 3, "10-12", 90),
                S("Romanian_Deadlift", 3, "10", 120),
                S("Side_Lateral_Raise", 2, "12-15", 60),
                S("Plank", 3, None, 45, duration_s=40),
                C("cardio_eliptico", 20, 2),
            ]},
            {"name": "Full B", "items": [
                S("Leg_Press", 3, "10-12", 90),
                S("Wide-Grip_Lat_Pulldown", 3, "10-12", 90),
                S("Machine_Shoulder_Military_Press", 3, "10", 90),
                S("Seated_Leg_Curl", 3, "12", 60),
                S("Barbell_Hip_Thrust", 3, "10", 90),
                S("Dead_Bug", 3, "10", 45),
                C("cardio_bike_vertical", 20, 2),
            ]},
            {"name": "Full C", "items": [
                S("Dumbbell_Lunges", 3, "10", 90),
                S("Incline_Dumbbell_Press", 3, "10-12", 90),
                S("One-Arm_Dumbbell_Row", 3, "10-12", 90),
                S("Thigh_Abductor", 2, "15", 60),
                S("Face_Pull", 3, "15", 60),
                S("Standing_Calf_Raises", 3, "12-15", 60),
                C("cardio_esteira_caminhada", 20, 2, params={"inclinacao_pct": 10, "velocidade_kmh": 5.5}),
            ]},
        ],
    },
    "cardio": {
        "name": "Cardio – Zona 2 + HIIT",
        "description": "Base aeróbia (zona 2) + 1 sessão de intervalado curto. Completa os 150–300 min da OMS.",
        "days_per_week": 3,
        "days": [
            {"name": "Z2 Esteira inclinada", "items": [
                C("cardio_esteira_caminhada", 45, 2, params={"inclinacao_pct": 10, "velocidade_kmh": 5.5}),
            ]},
            {"name": "HIIT Bike", "items": [
                {"exercise_id": "cardio_air_bike", "warmup_min": 8, "cooldown_min": 5,
                 "intervals": {"repeats": 8, "work_s": 30, "work_zone": 5, "rest_s": 90, "rest_zone": 1}},
            ]},
            {"name": "Z2 Elíptico + Remo", "items": [
                C("cardio_eliptico", 25, 2),
                C("cardio_remo", 15, 2),
            ]},
        ],
    },
}


# ---- modelos especiais: inspirados em treinos que pessoas famosas divulgaram (data/famous.json)
def _load_famous() -> dict:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent / "data" / "famous.json"
    return json.loads(p.read_text()) if p.exists() else {"templates": []}


FAMOUS = _load_famous()
for _f in FAMOUS["templates"]:
    TEMPLATES["famoso_" + _f["id"]] = {
        "name": f"{_f['person']}: {_f['title']}", "description": _f["about"], "days_per_week": _f["days_per_week"],
        "special": True, "days": _f["days"],
        "notes": f"Inspirado em {_f['person']} ({_f['confidence']}). {FAMOUS.get('disclaimer', '')}",
    }


def famous() -> dict:
    out = []
    for f in FAMOUS["templates"]:
        n_ex = sum(1 for d in f["days"] for i in d["items"] if not (catalog.get(i["exercise_id"]) or {}).get("kind") == "cardio")
        out.append({**f, "template_id": "famoso_" + f["id"], "n_exercises": n_ex,
                    "days": [{"name": d["name"], "items": [{**i, "name": (catalog.get(i["exercise_id"]) or {}).get("name", i["exercise_id"])}
                                                            for i in d["items"]]} for d in f["days"]]})
    return {"disclaimer": FAMOUS.get("disclaimer"), "updated": FAMOUS.get("updated"), "templates": out}


def list_templates() -> list[dict]:
    return [{"id": k, "name": v["name"], "description": v["description"]} for k, v in TEMPLATES.items() if not v.get("special")]


def build(template_id: str, profile: dict | None) -> dict | None:
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        return None
    plan = copy.deepcopy({k: v for k, v in tpl.items() if k not in ("description", "special")})
    base_notes = plan.pop("notes", "")
    # o mesmo treino repetido na semana (ex.: corpo inteiro 3×): a agenda precisa de um dia da planilha por sessão
    dpw = int(plan.get("days_per_week") or len(plan["days"]))
    if 0 < len(plan["days"]) < dpw:
        base = plan["days"]
        plan["days"] = []
        for i in range(dpw):
            d = copy.deepcopy(base[i % len(base)])
            if len(base) == 1:
                d["name"] = f"{d['name'].split(' (')[0]} ({i + 1}/{dpw})"
            plan["days"].append(d)
    lims = (profile or {}).get("limitations") or []
    mz = max_zone(lims)
    swaps = []
    for day in plan["days"]:
        used = {i["exercise_id"] for i in day["items"]}
        for it in day["items"]:
            ex = catalog.get(it["exercise_id"])
            if not ex:
                continue
            if mz:
                if it.get("zone") and it["zone"] > mz:
                    it["zone"] = mz
                iv = it.get("intervals")
                if iv and iv.get("work_zone", 0) > mz:
                    iv["work_zone"] = mz
            if check_exercise(ex, lims)["status"] != "evitar":
                continue
            if ex["kind"] == "cardio":
                alt = next((a for a in ("cardio_bike_horizontal", "cardio_eliptico", "cardio_bike_vertical")
                            if check_exercise(catalog.get(a), lims)["status"] != "evitar"), None)
            else:
                prim = ex["primaryMuscles"][0] if ex["primaryMuscles"] else None
                sug = suggest_for_muscle(prim, lims, used) if prim else []
                alt = sug[0]["id"] if sug else None
            if alt:
                swaps.append({"day": day["name"], "from": ex["name"], "to": catalog.get(alt)["name"]})
                it["exercise_id"] = alt
                it["notes"] = (it.get("notes", "") + " (substituído pelas suas limitações)").strip()
                used.add(alt)
    plan["notes"] = "Trocas automáticas: " + "; ".join(f"{s['from']} → {s['to']}" for s in swaps) if swaps else ""
    if base_notes:
        plan["notes"] = (base_notes + (" " + plan["notes"] if plan["notes"] else "")).strip()
    plan["days_per_week"] = (profile or {}).get("days_per_week") or plan["days_per_week"]
    return plan
