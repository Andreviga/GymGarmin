"""Simula 'aparelho ocupado' em todos os exercícios de muitos treinos gerados (perfis × academias × limitações),
com o treino em andamento (o que veio antes já feito), e confere se as trocas fazem sentido.

Uso: PYTHONPATH=. python3 scripts/simulate_swaps.py [-v]
"""
import os
import sys
import tempfile
from collections import Counter

d = tempfile.mkdtemp()
os.environ["GYMGARMIN_HOME"] = d
os.environ["GYMGARMIN_DB"] = d + "/sim.sqlite3"

from gymgarmin import catalog, db, generator as G, gyms  # noqa: E402
from gymgarmin import app as A  # noqa: E402

VERBOSE = "-v" in sys.argv
GYMS = ["ironberg", "nation_ct", "bio_ritmo", "smart_fit", "les_cinq", "casa"]
BASES = [
    {"training_months": 2, "goal": "saude", "available_days": [0, 2, 4], "session_minutes": 45, "age": 52, "sex": "F", "weight_kg": 70},
    {"training_months": 30, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 75, "age": 35, "sex": "M", "weight_kg": 85},
    {"training_months": 72, "goal": "hipertrofia", "available_days": [0, 1, 2, 3, 4, 5], "session_minutes": 90, "age": 34, "sex": "M", "weight_kg": 98},
    {"training_months": 14, "goal": "forca", "available_days": [1, 3, 5], "session_minutes": 60, "age": 28, "sex": "F", "weight_kg": 60,
     "limitations": [{"key": "ombro", "severity": "cuidado"}]},
    {"training_months": 40, "goal": "hipertrofia", "available_days": [0, 2, 4], "session_minutes": 60, "age": 45, "sex": "M", "weight_kg": 90,
     "limitations": [{"key": "lombar", "severity": "cuidado"}, {"key": "joelho", "severity": "cuidado"}]},
]

db.init()
stats = Counter()
problems: list[str] = []
empty: Counter = Counter()
for base in BASES:
    for gid in GYMS:
        prof = {**base, "gym": {"id": gid}}
        if gid == "casa":
            prof["home_kit"] = ["halteres", "elastico"]
        for seed in (0, 1):
            pl = G.generate(prof, seed)
            for day in pl["days"]:
                main = [i for i in day["items"] if (i.get("phase") or "principal") == "principal"
                        and (catalog.get(i["exercise_id"]) or {}).get("kind") == "strength"]
                ids = [i["exercise_id"] for i in main]
                for k, it in enumerate(main):
                    eid = it["exercise_id"]
                    done, sess = ids[:k], [x for x in ids if x != eid]
                    mine: set[str] = set()
                    lims = G.with_personal_lims(prof.get("limitations") or [], {})
                    opts = gyms.session_options(eid, prof, A._subs_pool(eid), my_ids=mine, lims=lims, in_session=sess, done=done, limit=12)
                    stats["casos"] += 1
                    stats["opções"] += len(opts)
                    if not opts:
                        empty[eid] += 1
                        continue
                    st = gyms.station_of(eid)
                    ok = gyms.checker(prof, home=True)
                    for o in opts:
                        c = o["id"]
                        if c in sess or c in done:
                            problems.append(f"repetido no treino: {eid} → {c}")
                        if st and st not in gyms.SOFT_STATIONS and gyms.station_of(c) == st:
                            problems.append(f"mesma estação ocupada: {eid} → {c}")
                        if not gyms.sensible(eid, c):
                            problems.append(f"sem sentido: {eid} → {c}")
                        if not ok(c):
                            problems.append(f"academia não tem ({gid}): {eid} → {c}")
                        if c in gyms.NEVER_SUB:
                            problems.append(f"arriscado: {eid} → {c}")
                    first = opts[0]
                    if first["note"]:
                        stats["1ª opção com aviso"] += 1
                    if first["fit"] != "mesmo":
                        stats["1ª opção outro movimento"] += 1
                        if VERBOSE:
                            print(f"  [{gid}] {catalog.get(eid)['name']}: 1ª = {catalog.get(first['id'])['name']} ({first['note']})")

print(f"casos: {stats['casos']} · média de opções: {stats['opções'] / max(1, stats['casos']):.1f}")
print(f"sem nenhuma troca: {sum(empty.values())} ({len(empty)} exercícios)")
for e, n in empty.most_common(12):
    print(f"   {n:3d} × {catalog.get(e)['name']} ({e})")
print(f"1ª opção com aviso: {stats['1ª opção com aviso']} · 1ª opção de outro movimento: {stats['1ª opção outro movimento']}")
print(f"problemas: {len(problems)}")
for p, n in Counter(problems).most_common(20):
    print(f"   {n:3d} × {p}")
sys.exit(1 if problems else 0)
