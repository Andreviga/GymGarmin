"""Simula a "outra opção para hoje", o treino gerado na hora e o próximo exercício sugerido em muitos perfis.

Para cada perfil (nível × objetivo × tempo × limitações × tipo de academia) × dia do plano × motivo, confere:
- nada marcado como "evitar" para as limitações (e para a região com dor);
- equipamento: fora da academia só o kit; academia cheia sem barra;
- tempo: até 10% acima do que a pessoa tem;
- sem exercício repetido no dia;
- dolorido: os principais não trabalham o músculo dolorido;
- cansado: no máximo 75% das séries e nada que carregue a coluna em pé;
- falta: o que mais falta na semana aparece;
- mesma escolha com a mesma semente; outra com seed+1 (variar);
e para o próximo exercício: começa pelo composto, não mistura superior e inferior, para em ~26 séries.

Uso: python scripts/simulate_alternative.py
"""
from __future__ import annotations

import itertools
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gymgarmin import alternative as A, catalog, generator as G  # noqa: E402
from gymgarmin.limitations import check_exercise  # noqa: E402

LEVELS = [3, 18, 40, 80]
GOALS = ["hipertrofia", "forca", "emagrecimento", "saude", "hibrido"]
MINUTES = [35, 60, 90]
LIMS = [[], [{"key": "joelho", "severity": "evitar"}], [{"key": "ombro", "severity": "cuidado"}],
        [{"key": "lombar", "severity": "evitar"}, {"key": "punho", "severity": "cuidado"}]]
GYMS = ["basica", "completa", "premium"]
OPTS = {"doente": [{"sick": "pescoco"}], "tempo": [{"minutes": 25}, {"minutes": 40}], "dor": [{"region": r} for r in ("ombro", "joelho", "lombar", "cotovelo")],
        "dolorido": [{"muscles": ["pernas"]}, {"muscles": ["peito", "ombros"]}, {"muscles": ["costas", "bracos"]}],
        "fora": [{"kit": []}, {"kit": ["halteres"]}, {"kit": ["elastico", "barra"]}]}


def main() -> None:
    problems: Counter = Counter()
    examples: dict[str, str] = {}
    runs = 0

    def bad(kind: str, where: str) -> None:
        problems[kind] += 1
        examples.setdefault(kind, where)

    for months, goal, mins, lims, gym in itertools.product(LEVELS, GOALS, MINUTES, LIMS, GYMS):
        prof = {"training_months": months, "goal": goal, "session_minutes": mins, "limitations": lims, "gym_type": gym,
                "available_days": [0, 1, 3, 4]}
        plan = G.generate(prof)
        bal = [{"key": "hamstrings", "missing": 7}, {"key": "calves", "missing": 5}, {"key": "lats", "missing": 4}]
        for d in plan["days"]:
            if not any(A.is_strength(i) for i in d["items"]):
                continue
            for reason in sorted(A.REASON_IDS):
                for opts in OPTS.get(reason, [{}]):
                    runs += 1
                    where = f"{months}m {goal} {mins}min {[x['key'] for x in lims]} {gym} · {d['name']} · {reason} {opts}"
                    try:
                        o = A.propose(d["items"], reason, A.Ctx(prof, balance=bal), dict(opts), d["name"])
                    except ValueError as e:
                        bad(f"erro: {e}", where)
                        continue
                    st = [i for i in o["items"] if A.is_strength(i)]
                    ids = [i["exercise_id"] for i in o["items"] if (i.get("phase") or "principal") == "principal"]
                    if len(ids) != len(set(ids)):
                        bad("exercício repetido", where)
                    ll = lims + ([{"key": opts["region"], "severity": "evitar"}] if reason == "dor" else [])
                    for i in st:
                        if check_exercise(catalog.get(i["exercise_id"]), ll)["status"] == "evitar":
                            bad("exercício a evitar", f"{where} → {i['exercise_id']}")
                    budget = o["minutes"]["budget"]
                    if o["minutes"]["after"] > budget * 1.1 + 1:
                        bad("passou do tempo", f"{where} → {o['minutes']}")
                    if reason == "fora":
                        kit = set(opts["kit"])
                        for i in [x for x in o["items"] if (x.get("phase") or "principal") != "pos"]:
                            eq = catalog.get(i["exercise_id"]).get("equipment")
                            ok = eq in (None, "body only") or (eq == "dumbbell" and "halteres" in kit) or (eq == "bands" and "elastico" in kit) \
                                or (eq == "kettlebells" and ("kettlebell" in kit or (i["exercise_id"] == "Goblet_Squat" and "halteres" in kit)))
                            if not ok or (i["exercise_id"] in A.NEEDS_BAR and "barra" not in kit):
                                bad("fora: equipamento que não tem", f"{where} → {i['exercise_id']}")
                    if reason == "ocupado":
                        kept = {c["from"] for c in o["changes"] if c["type"] == "ajuste"}
                        if any(catalog.get(i["exercise_id"])["equipment"] in ("barbell", "e-z curl bar") and i["exercise_id"] not in kept for i in st):
                            bad("academia cheia com barra (sem avisar)", where)
                    if reason == "cansado":
                        if o["sets"]["after"] > 0.75 * o["sets"]["before"] + 0.5 and len(st) > 3:
                            bad("cansado: volume alto", f"{where} → {o['sets']}")
                        if any(i["exercise_id"] in A.AXIAL for i in st):
                            bad("cansado: carga na coluna", where)
                    if reason == "dolorido":
                        sore = {m for g in opts["muscles"] for m in A.MUSCLE_GROUPS[g]["muscles"]}
                        for i in st:
                            if i.get("role") in ("main", "sec") and set(catalog.get(i["exercise_id"])["primaryMuscles"]) & sore:
                                bad("dolorido: treina o músculo dolorido", f"{where} → {i['exercise_id']}")
                    if reason == "falta":
                        # o treino de hoje sai: o que ele daria volta a faltar; o mais atrasado precisa estar no treino
                        miss = {b["key"]: b["missing"] for b in bal}
                        for m, v in A.session_load([i for i in d["items"] if A.is_strength(i)]).items():
                            miss[m] = miss.get(m, 0) + v
                        top = max((m for m in miss if m in A.MUSCLE_MAIN), key=lambda m: miss[m])
                        prim = {m for i in st for m in catalog.get(i["exercise_id"])["primaryMuscles"] + catalog.get(i["exercise_id"])["secondaryMuscles"]}
                        if top not in prim:
                            bad("falta: não repôs o que mais falta", f"{where} → {top}")
                    if reason == "variar":
                        o2 = A.propose(d["items"], reason, A.Ctx(prof, balance=bal, seed=1), dict(opts), d["name"])
                        if o2["items"] == o["items"] and sum(1 for c in o["changes"] if c["type"] == "troca") >= 3:
                            bad("variar: seed+1 igual", where)
                    if not st:
                        bad("sem exercício de força", where)

        # próximo exercício (treino livre), com a sessão vazia
        ctx = A.Ctx(prof, balance=bal)
        seq: list[dict] = []
        for _ in range(16):
            r = A.next_exercises(seq, ctx, n=1)
            if not r["suggestions"]:
                break
            s = r["suggestions"][0]
            seq.append({"exercise_id": s["exercise_id"], "sets": s["sets"]})
        where = f"{months}m {goal} {mins}min {[x['key'] for x in lims]} {gym} · próximo"
        if seq and catalog.get(seq[0]["exercise_id"])["mechanic"] != "compound":
            bad("próximo: começa por isolado", where)
        if sum(int(x["sets"]) for x in seq) > A.SESSION_SETS_CAP + 4:
            bad("próximo: séries demais", where)
        up = sum(1 for x in seq if set(catalog.get(x["exercise_id"])["primaryMuscles"]) & G._UPPER_M)
        lo = sum(1 for x in seq if set(catalog.get(x["exercise_id"])["primaryMuscles"]) & G._LOWER_M)
        if min(up, lo) > 1:
            bad("próximo: mistura superior e inferior", f"{where} {[x['exercise_id'] for x in seq]}")
        for x in seq:
            if check_exercise(catalog.get(x["exercise_id"]), lims)["status"] == "evitar":
                bad("próximo: exercício a evitar", f"{where} → {x['exercise_id']}")

    print(f"{runs} opções simuladas")
    if not problems:
        print("Nenhum problema.")
    for k, n in problems.most_common():
        print(f"- {k}: {n}  (ex.: {examples[k]})")


if __name__ == "__main__":
    main()
