"""Simula o gerador em todas as academias da lista × objetivos × níveis × aparelhos faltando.

Confere: nada que a pessoa marcou como 'não tem' entra; boutique/completa/básica não recebem aparelho raro;
nenhum dia fica mais curto que o mesmo treino sem nada faltando; o substituto sempre existe na academia.
"""
import random
from collections import Counter

from gymgarmin import generator as G, gyms

GOALS = ["hipertrofia", "forca", "emagrecimento", "condicionamento"]
MONTHS = [3, 14, 40]
RARE = {m for m, x in gyms.machines().items() if x.get("availability_br") == "rara"}


def ids(pl):
    return [i["exercise_id"] for d in pl["days"] for i in d["items"] if (i.get("phase") or "principal") == "principal"]


def main():
    rnd = random.Random(7)
    probs, n = Counter(), 0
    for g in gyms.all_gyms():
        kit = gyms.data()["kits"][g["kit"]]
        for goal in GOALS:
            for months in MONTHS:
                for days in ([0, 2, 4], [0, 1, 3, 4], [0, 1, 2, 3, 4, 5]):
                    base = {"training_months": months, "goal": goal, "available_days": days, "session_minutes": 60, "age": 35,
                            "sex": rnd.choice("MF"), "weight_kg": 75, "gym": {"id": g["id"]}, "gym_type": kit["gym_type"]}
                    p0 = G.generate(base, 0)
                    machines = sorted(set(ids(p0)) & (set(gyms.machines()) | {e for l in G.PREMIUM_PREF.values() for e in l}))
                    miss = rnd.sample(machines, min(len(machines), rnd.randint(1, 4))) if machines else []
                    p1 = G.generate({**base, "gym_missing": miss}, 0)
                    n += 1
                    got = set(ids(p1))
                    if got & set(miss):
                        probs["voltou aparelho que não tem"] += 1
                    if kit["level"] < 3 and got & RARE:
                        probs[f"raro em {g['kit']}"] += 1
                    for d0, d1 in zip(p0["days"], p1["days"]):
                        a = sum(1 for i in d0["items"] if (i.get("phase") or "principal") == "principal")
                        b = sum(1 for i in d1["items"] if (i.get("phase") or "principal") == "principal")
                        if b < a:
                            probs["dia ficou mais curto"] += 1
                    for e in miss:
                        if not gyms.substitutes(e, {**base, "gym_missing": miss}, list(G.PATTERNS.get(__import__("gymgarmin.alternative", fromlist=["x"]).pattern_of(e) or "", []))):
                            probs[f"sem substituto: {e}"] += 1
    print(f"{n} treinos simulados")
    print(dict(probs) or "nenhum problema")


if __name__ == "__main__":
    main()
