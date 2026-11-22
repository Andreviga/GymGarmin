"""Simula o perfil detalhado: combinações aleatórias de respostas (saúde, fase, corpo, corrida, rotina) × perfis.

Confere, para cada plano gerado:
- nada que a resposta proíbe (exercícios evitados/não gostados, saltos sem impacto, flexão de coluna com osso frágil);
- tetos de esforço (RPE) respeitados; liberação médica pendente = nada forte na corrida;
- lesão de Aquiles/fratura por estresse = sem subidas e tiros; dor forte ao correr = corrida vira bike;
- nenhum erro e nenhum dia de força vazio.

Uso: python scripts/simulate_personal.py [n]
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gymgarmin import catalog, endurance as en, generator as G, personal  # noqa: E402
from gymgarmin.limitations import check_exercise  # noqa: E402

MONDAY = date(2026, 11, 16)


def rand_answers(rnd: random.Random, sex: str) -> dict:
    a = {}
    for sec in personal.QUESTIONS:
        for q in sec["items"]:
            if rnd.random() < 0.55:
                continue
            if q["type"] == "multi":
                a[q["id"]] = rnd.sample([k for k, _ in q["options"]], k=rnd.randint(1, min(3, len(q["options"]))))
            elif q["type"] == "single":
                a[q["id"]] = rnd.choice(q["options"])[0]
            elif q["type"] == "number":
                a[q["id"]] = rnd.randint(1, 40)
            elif q["type"] == "exercises":
                a[q["id"]] = rnd.sample(sorted({e for v in G.PATTERNS.values() for e in v}), k=3)
    if sex != "F":
        for k in ("phase", "postpartum_weeks", "cesarea", "pelvic", "amenorrhea"):
            a.pop(k, None)
    return a


def main(n: int = 600) -> None:
    rnd = random.Random(7)
    problems: Counter = Counter()
    ex: dict[str, str] = {}

    def bad(k, w):
        problems[k] += 1
        ex.setdefault(k, w)

    for i in range(n):
        sex = rnd.choice(["M", "F"])
        prof = {"training_months": rnd.choice([3, 12, 30, 80]), "goal": rnd.choice(list(G.GOALS)), "age": rnd.randint(16, 78),
                "sex": sex, "weight_kg": rnd.randint(48, 130), "height_cm": rnd.randint(150, 198),
                "available_days": sorted(rnd.sample(range(7), rnd.randint(2, 6))), "session_minutes": rnd.choice([30, 45, 60, 90]),
                "gym_type": rnd.choice(["basica", "completa", "premium"]),
                "limitations": rnd.choice([[], [{"key": "joelho", "severity": "cuidado"}], [{"key": "lombar", "severity": "evitar"}]])}
        prof["personal"] = rand_answers(rnd, sex)
        where = f"#{i} {prof['age']}a {sex} {prof['personal']}"
        try:
            pl = G.generate(prof)
        except Exception as e:  # noqa: BLE001
            bad(f"erro no gerador: {type(e).__name__}", f"{where} {e}")
            continue
        d = personal.derive(prof)
        lims = G.with_personal_lims(prof["limitations"], d)
        strength = [x for x in pl["days"] if x["kind"] == "forca"]
        if any(not [i for i in x["items"] if i.get("phase") == "principal"] for x in strength):
            bad("dia de força vazio", where)
        for day in strength:
            for it in day["items"]:
                eid = it["exercise_id"]
                if (it.get("phase") or "principal") == "principal" and eid in d["avoid"]:
                    bad("exercício evitado no plano", f"{where} → {eid}")
                exx = catalog.get(eid)
                if exx and exx.get("kind") == "strength" and check_exercise(exx, lims)["status"] == "evitar":
                    bad("exercício 'evitar' no plano", f"{where} → {eid}")
                if d["no_jumps"] and eid in personal.JUMP_IDS:
                    bad("salto sem impacto permitido", f"{where} → {eid}")
                if d["rpe_cap"] and it.get("rpe_target") and float(it["rpe_target"]) > d["rpe_cap"] + 1e-9:
                    bad("RPE acima do teto", f"{where} → {eid} {it['rpe_target']}")
        # corrida
        rp = {**prof, "endurance": {"sports": {"corrida": {"enabled": True, "env": "rua", "sessions": rnd.randint(2, 5),
                                                            "current_weekly": rnd.choice([5, 15, 30, 50])}},
                                    "anchors": {"run_vdot": rnd.choice([35, 45, 55])}}}
        for w in (0, 2, 5):
            try:
                wp = en.week_plan(rp, MONDAY + timedelta(weeks=w), MONDAY, rnd.choice(["iniciante", "intermediario", "avancado"]))
            except Exception as e:  # noqa: BLE001
                bad(f"erro na corrida: {type(e).__name__}", f"{where} {e}")
                break
            sess = wp["sports"]["corrida"]["sessions"]
            types = {s["endurance"]["type"] for s in sess}
            if d["no_running"] and any(s["sport"] == "corrida" for s in sess):
                bad("corrida com dor forte/pós-parto", where)
            if d["no_hills_speed"] and types & {"subidas", "intervalado", "noruegues"}:
                bad("tiros/subidas com Aquiles ou fratura", f"{where} {types}")
            if d["clearance"] and any(s["endurance"]["hard"] for s in sess):
                bad("treino forte sem liberação médica", where)
    print(f"{n} perfis simulados")
    if not problems:
        print("Nenhum problema.")
    for k, c in problems.most_common():
        print(f"- {k}: {c}  (ex.: {ex[k][:400]})")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 600)
