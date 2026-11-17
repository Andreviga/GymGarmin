"""Simulações da análise semanal (Análise → volume por músculo, o que ajustar, equilíbrio, cardio).

Uso:  python scripts/simulate_weekly.py [--n 40] [--seed 1]

Pessoas com treino CONHECIDO, durante 6 semanas, e a análise calculada todo domingo à noite (e numa
quarta-feira, no meio da semana):
  segue_plano (cada objetivo, cumprindo o plano gerado pelo app) · primeira_semana (só 1 treino até agora) ·
  deload (semana leve do ciclo) · relogio_sem_series (musculação gravada pelo Garmin sem séries) ·
  pula_perna (3 semanas sem perna) · so_empurra (só peito/ombro/tríceps) · excesso (séries em dobro) ·
  corredor (corre 40 km/semana + musculação só de tronco) · joelho (limitação 'evitar' no joelho) ·
  semana_pequena (1 treino curto na semana)

Mede: alarmes falsos (o que avisou sem motivo) × acertos (avisou o que devia), sugestões que violam a
limitação, texto (ponto decimal, "None"), e se as séries por músculo batem com a conta independente.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gymgarmin import analysis, catalog, generator  # noqa: E402
from gymgarmin.limitations import check_exercise  # noqa: E402
from gymgarmin.muscles import MUSCLES  # noqa: E402
from gymgarmin.progression import apply_cycle  # noqa: E402

MONDAY = date(2026, 10, 5)
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
HIT: dict = defaultdict(lambda: [0, 0])
STATS: dict = defaultdict(list)
LOWER = {"quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"}
PUSH = {"chest", "shoulders", "triceps"}
MAJOR = ["chest", "lats", "middle back", "shoulders", "quadriceps", "hamstrings", "glutes", "biceps", "triceps"]


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def score(k, ok):
    HIT[k][0] += int(ok)
    HIT[k][1] += 1


def session(d: date, hour: int, items, source=None):
    s = {"date": f"{d.isoformat()}T{hour:02d}:00", "items": items}
    if source:
        s["source"] = source
    return s


def logged(items, factor=1.0, keep=lambda it: True):
    out = []
    for it in items:
        if (it.get("phase") or "principal") != "principal" or not keep(it):
            continue
        ex = catalog.get(it["exercise_id"]) or {}
        if ex.get("kind") == "cardio":
            out.append({"exercise_id": it["exercise_id"], "duration_min": it.get("duration_min") or 20, "zone": it.get("zone") or 2})
        elif it.get("sets"):
            n = max(1, round(int(it["sets"]) * factor))
            out.append({"exercise_id": it["exercise_id"], "sets": [{"reps": 10, "load_kg": 40, "rpe": 8, "done": True}] * n})
    return out


def primaries(it):
    return set((catalog.get(it["exercise_id"]) or {}).get("primaryMuscles", []))


def build(rng, persona):
    goal = rng.choice(["hipertrofia", "forca", "saude", "emagrecimento", "condicionamento", "hibrido"]) if persona == "segue_plano" else "hipertrofia"
    months = rng.choice([3, 9, 18, 40])
    days = sorted(rng.sample(range(7), rng.choice([3, 4, 5])))
    prof = {"training_months": months, "goal": goal, "available_days": days, "session_minutes": rng.choice([45, 60, 75]), "age": 35,
            "level": generator.TIERS[generator.tier_for(months)]["level"]}
    if persona == "joelho":
        prof["limitations"] = [{"key": "joelho", "severity": "evitar"}]
    plan = generator.generate(prof)
    sessions = []
    for w in range(6):
        ws = MONDAY + timedelta(weeks=w)
        deload_week = persona == "deload" and w == 5
        pos = {"week": 5 if deload_week else (w % 4) + 1, "length": 5, "cycle": 1, "deload": deload_week}
        for i, d in enumerate([x for x in plan["days"]]):
            dt = ws + timedelta(days=days[i % len(days)])
            items = apply_cycle(d["items"], pos, 1)
            if persona == "primeira_semana" and (w < 5 or i > 0):
                continue
            if persona == "semana_pequena" and w == 5 and i > 0:
                continue
            if persona == "relogio_sem_series":
                if d["kind"] == "forca":
                    sessions.append(session(dt, 18, [{"exercise_id": "Garmin_musculacao", "strength_min": 55, "duration_min": 55}], "garmin"))
                continue
            if persona == "pula_perna" and w >= 3:
                items = [it for it in items if not (primaries(it) & LOWER)]
            if persona == "so_empurra":
                items = [it for it in items if primaries(it) & PUSH or (catalog.get(it["exercise_id"]) or {}).get("kind") == "cardio"]
            if persona == "corredor":
                items = [it for it in items if not (primaries(it) & LOWER)]
            factor = 2.0 if persona == "excesso" else 1.0
            its = logged(items, factor)
            if persona == "semana_pequena" and w == 5:
                its = its[:1]
            if its:
                sessions.append(session(dt, 18, its))
        if persona == "corredor":
            for k in (1, 3, 6):
                sessions.append(session(ws + timedelta(days=k), 7, [{"exercise_id": "corrida_rua", "sport": "corrida",
                                                                     "distance_km": 13, "duration_min": 70, "zone": 2}]))
    return prof, plan, sessions


def direct_sets(sessions, start, end):
    c = Counter()
    for s in sessions:
        d = datetime.fromisoformat(s["date"])
        if not (start <= d <= end):
            continue
        for it in s["items"]:
            ex = catalog.get(it.get("exercise_id")) or {}
            if ex.get("kind") != "strength":
                continue
            n = len([x for x in it.get("sets") or [] if x.get("done", True) and not x.get("warmup")])
            for m in ex.get("primaryMuscles", []):
                c[m] += n
            for m in ex.get("secondaryMuscles", []):
                c[m] += 0.5 * n
    return c


BAD = re.compile(r"None|\bnan\b|\b\d+\.\d\b")


def check(persona, prof, plan, sessions, pid):
    now = datetime.combine(MONDAY + timedelta(weeks=5, days=6), datetime.min.time()) + timedelta(hours=21)  # domingo à noite
    expected = {m["muscle"]: m["sets_week"] for m in analysis.plan_report(plan, prof)["muscles"]}
    rep = analysis.weekly_report(sessions, prof, now, expected=expected, deload=persona == "deload")
    recs = rep["recommendations"]
    warn = [r for r in recs if r["level"] in ("atencao", "alerta")]
    by = {m["muscle"]: m for m in rep["muscles"]}
    # conta independente
    truth = direct_sets(sessions, now - timedelta(days=7), now)
    for m in MUSCLES:
        if abs(truth.get(m, 0) - by[m]["sets_7d"]) > 0.11:
            issue("séries por músculo ≠ conta independente", (persona, pid, m, truth.get(m, 0), by[m]["sets_7d"]))
    for r in recs:
        if BAD.search(r["text"]):
            issue("texto com ponto decimal / None", (persona, pid, r["text"][:120]))
        for sg in r.get("suggestions") or []:
            ex = catalog.get(sg["id"])
            if ex and check_exercise(ex, prof.get("limitations") or [])["status"] == "evitar":
                issue("sugestão viola a limitação", (persona, pid, sg["id"]))
    for b in rep["balance"]:
        if b["status"] in ("baixo", "alto") and (b["a"] + b["b"]) < 8:
            issue("equilíbrio julgado com volume minúsculo", (persona, pid, b["id"], b["a"], b["b"]))
    STATS[f"avisos por semana · {persona}"].append(len(warn))
    major_warn = [r for r in warn if r.get("muscle") in MAJOR]
    if persona == "segue_plano":
        STATS[f"avisos (músculos principais) seguindo o plano · {prof['goal']}"].append(len(major_warn))
        score(f"segue_plano ({prof['goal']}): nenhum aviso de músculo principal", not major_warn)
    if persona == "primeira_semana":
        esq = [r for r in warn if "esquecido" in r["text"]]
        score("primeira_semana: não chama músculo de 'esquecido' com 1 treino", not esq)
    if persona == "deload":
        score("deload: semana leve do ciclo sem avisos de 'abaixo do mínimo'", not [r for r in warn if "mínimo" in r["text"]])
    if persona == "relogio_sem_series":
        esq = [r for r in warn if "esquecido" in r["text"] or "mínimo" in r["text"]]
        score("relógio sem séries: não acusa músculos 'esquecidos' de quem treinou", not esq)
        score("relógio sem séries: explica que a análise ficou incompleta", any("relógio" in r["text"] or "séries" in r["text"] and "Garmin" in r["text"] for r in recs))
    if persona == "pula_perna":
        legs = [r for r in warn if r.get("muscle") in ("quadriceps", "hamstrings", "glutes")] + \
               [r for r in warn if "inferiores" in r["text"]]
        score("pula_perna: avisa das pernas", bool(legs))
    if persona == "so_empurra":
        score("so_empurra: avisa puxar × empurrar", any("puxa" in r["text"].lower() for r in warn))
    if persona == "excesso":
        score("excesso: avisa volume acima do recuperável", any("acima do volume recuperável" in r["text"] for r in warn))
    if persona == "corredor":
        legs = [r for r in warn if r.get("muscle") in LOWER or "inferiores" in r["text"]]
        STATS["corredor: avisos de perna"].append(len(legs))
        score("corredor: aviso de perna menciona a corrida", (not legs) or any("corr" in r["text"].lower() for r in legs))
    if persona == "joelho":
        quad = [r for r in warn if r.get("muscle") == "quadriceps"]
        score("joelho: aviso de quadríceps respeita a limitação (só sugestões liberadas)",
              all(all(check_exercise(catalog.get(sg["id"]), prof["limitations"])["status"] != "evitar" for sg in r["suggestions"]) for r in quad))
    if persona == "semana_pequena":
        score("semana_pequena: sem avisos de equilíbrio com volume minúsculo",
              not [b for b in rep["balance"] if b["status"] in ("baixo", "alto") and (b["a"] + b["b"]) < 8])
    # quarta-feira: semana pela metade não deve virar avalanche de avisos para quem segue o plano
    if persona == "segue_plano":
        mid = analysis.weekly_report(sessions, prof, now - timedelta(days=4), expected=expected)
        STATS["avisos na quarta (seguindo o plano)"].append(len([r for r in mid["recommendations"] if r["level"] in ("atencao", "alerta")]))


PERSONAS = ["segue_plano", "primeira_semana", "deload", "relogio_sem_series", "pula_perna", "so_empurra", "excesso",
            "corredor", "joelho", "semana_pequena"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    for persona in PERSONAS:
        for pid in range(a.n * (3 if persona == "segue_plano" else 1)):
            try:
                prof, plan, sessions = build(rng, persona)
                check(persona, prof, plan, sessions, pid)
            except Exception as ex:  # noqa: BLE001
                import traceback
                issue("exceção", (persona, pid, repr(ex)[:150], traceback.format_exc(limit=3)[-300:]))
    print(f"\n=== análise semanal: {len(PERSONAS)} perfis (seed {a.seed}) ===")
    print("\n--- acerto (%)")
    for k, (ok, tot) in sorted(HIT.items()):
        pct = 100 * ok / tot if tot else 0
        print(f"{pct:6.1f}%  {k}  (n={tot}){'  ←' if pct < 85 else ''}")
    print("\n--- estatísticas")
    for k, v in sorted(STATS.items()):
        print(f"{k}: média {sum(v) / len(v):.2f} · máx {max(v)} · n {len(v)}")
    if ISSUES:
        print("\n--- problemas")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:240])


if __name__ == "__main__":
    main()
