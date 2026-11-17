"""Simulações do gerador de musculação ao longo de meses: o que cada músculo recebe de verdade.

Uso:  python scripts/simulate_strength_block.py [--n 120] [--weeks 16] [--seed 1]

Cada pessoa gera o treino (tempo de prática × objetivo × dias × minutos × limitações × equipamento)
e vive N semanas com a agenda do app: ciclos com séries a mais e deload, faltas (com e sem
reajuste pelo app), treinos registrados. A cada semana conta as séries EFETIVAS por músculo
(principal = 1, secundário = 0,5) do que foi feito e compara com as faixas de volume:
abaixo do mínimo (MEV) · útil (MEV até o máximo recuperável) · acima do máximo (MRV).

Modelo de hipertrofia (dose-resposta): ganho semanal ∝ log(1 + séries/4) até o MRV, com +10% se o
músculo é treinado 2×+ na semana e perda de ganho acima do MRV. Compara com o ideal possível no
mesmo tempo (cada músculo no meio da faixa útil).

Mede também: tempo real das sessões × tempo informado, limitações respeitadas depois dos reajustes,
exercícios repetidos por meses (variedade), músculos que ficam sempre abaixo do mínimo.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import tempfile
import traceback
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["GYMGARMIN_HOME"] = TMP
os.environ["GYMGARMIN_DB"] = str(Path(TMP) / "sb.sqlite3")
os.environ.pop("DATABASE_URL", None)

from gymgarmin import catalog, db, generator, schedule  # noqa: E402
from gymgarmin.limitations import check_exercise  # noqa: E402
from gymgarmin.muscles import MUSCLES  # noqa: E402

MONDAY = date(2026, 11, 16)
ADJUST = "misto"
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
STATS: dict = defaultdict(list)
HOME = ["dumbbell", "body only", "bands", "kettlebells"]
TRACKED = [m for m in MUSCLES if m not in ("neck",)]
MAJOR = ["chest", "lats", "middle back", "shoulders", "quadriceps", "hamstrings", "glutes", "biceps", "triceps"]


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def profile(rng):
    n = rng.choice([2, 3, 3, 4, 4, 5, 6])
    p = {"training_months": rng.choice([3, 9, 18, 40, 80]), "available_days": sorted(rng.sample(range(7), n)),
         "goal": rng.choice(["hipertrofia", "forca", "hibrido", "emagrecimento", "saude", "condicionamento"]),
         "session_minutes": rng.choice([40, 45, 60, 75, 90]), "age": rng.randint(20, 65)}
    if rng.random() < 0.2:
        p["limitations"] = [{"key": rng.choice(["joelho", "ombro", "lombar", "punho"]), "severity": rng.choice(["evitar", "cuidado"])}]
    if rng.random() < 0.2:
        p["equipment"] = HOME
    return p


def eff_sets(items) -> Counter:
    c: Counter = Counter()
    for it in items:
        if not it.get("sets") or (it.get("phase") or "principal") != "principal":
            continue
        ex = catalog.get(it["exercise_id"]) or {}
        if ex.get("kind") != "strength":
            continue
        n = int(it["sets"]) if isinstance(it["sets"], (int, float)) else len(it["sets"])
        for m in ex.get("primaryMuscles", []):
            c[m] += n
        for m in ex.get("secondaryMuscles", []):
            c[m] += 0.5 * n
    return c


def growth(sets: float, mrv: float, freq2: bool) -> float:
    if sets <= 0:
        return 0.0
    g = math.log1p(min(sets, mrv) / 4)
    if sets > mrv:
        g -= 0.15 * (sets - mrv)  # passou do recuperável: fadiga come o ganho
    return max(0.0, g) * (1.1 if freq2 else 1.0)


def run(pid, rng, weeks):
    with db.conn() as c:
        for t in ("schedule", "plans", "sessions"):
            c.execute(f"DELETE FROM {t}")
    p0 = profile(rng)
    db.save_profile({**db.DEFAULT_PROFILE, **p0, "limitations": p0.get("limitations") or [], "equipment": p0.get("equipment")})
    prof = db.get_profile()
    plan = generator.generate(prof, today=MONDAY)
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    prof = db.get_profile()
    budget = schedule.session_budget(prof, saved)
    compliance = rng.uniform(0.7, 1.0)
    uses_adjust = {"sempre": True, "nunca": False}.get(ADJUST, rng.random() < 0.6)
    lims = prof.get("limitations") or []
    per_muscle_weeks = defaultdict(list)
    ex_seen = Counter()
    g_real = g_ideal = 0.0
    for w in range(weeks):
        ws = MONDAY + timedelta(weeks=w)
        entries = schedule.ensure_week(prof, ws, ws)
        done_items, days_trained = [], defaultdict(set)
        deload = any((e.get("cycle") or {}).get("deload") for e in entries)
        for e in sorted(entries, key=lambda x: x["date"]):
            cur = db.get_entry(e["id"])
            if not cur or cur["status"] != "planejado":
                continue
            if cur.get("kind") != "forca":
                continue
            # tempo real da sessão
            t = generator.time_breakdown(cur["items"])["total"]
            STATS["sessão ÷ tempo informado"].append(round(t / budget, 2))
            if t > budget * 1.15 + 0.5:
                issue("sessão de força passou do tempo (+15%)", (pid, cur["day_name"], round(t), budget))
            for it in cur["items"]:
                ex = catalog.get(it.get("exercise_id")) or {}
                if ex and lims and check_exercise(ex, lims)["status"] == "evitar":
                    issue("exercício 'evitar' na agenda (limitação)", (pid, it["exercise_id"], [l["key"] for l in lims], cur.get("changes", [])[-1:] ))
                if (it.get("phase") or "principal") == "principal" and ex.get("kind") == "strength":
                    ex_seen[it["exercise_id"]] += 1
                if prof.get("equipment") and ex and ex.get("equipment") not in set(prof["equipment"]) | {None, "body only", "other", "cardio machine"}:
                    issue("exercício com equipamento que a pessoa não tem", (pid, it["exercise_id"], ex.get("equipment")))
            if rng.random() > compliance:
                if uses_adjust and _d(cur["date"]) < ws + timedelta(days=6):
                    try:
                        schedule.handle_missed(cur["id"], prof, _d(cur["date"]))
                    except Exception as ex:  # noqa: BLE001
                        issue("exceção no reajuste", (pid, repr(ex)[:120]))
                continue
            done_items += cur["items"]
            for m in eff_sets(cur["items"]):
                days_trained[m].add(cur["date"])
            cur["status"] = "feito"
            db.save_entry(cur)
        sets = eff_sets(done_items)
        planned = eff_sets([it for e in entries if e.get("kind") == "forca" for it in e["items"]])
        if not deload:
            for m in TRACKED:
                L = MUSCLES[m]
                for lab, val in (("plan", planned.get(m, 0.0)), ("feito", sets.get(m, 0.0))):
                    z = "abaixo" if val < L["mev"] else "acima" if val > L["mrv"] else "util"
                    STATS[f"obj {p0['goal']} {lab} {z}"].append(1)
                    if m in MAJOR:
                        STATS[f"maior {p0['goal']} {lab} {z}"].append(1)
        for m in TRACKED:
            L = MUSCLES[m]
            v = sets.get(m, 0.0)
            per_muscle_weeks[m].append(v)
            if not deload:
                g_real += growth(v, L["mrv"], len(days_trained[m]) >= 2)
                g_ideal += growth((L["mav"][0] + L["mav"][1]) / 2, L["mrv"], True)
                zone = "abaixo" if v < L["mev"] else "acima" if v > L["mrv"] else "util"
                STATS[f"zona {zone}"].append(1)
                STATS[f"zona-musculo {m} {zone}"].append(1)
                if p0.get("equipment"):
                    STATS[f"casa zona {zone}"].append(1)
                STATS[f"tempo {p0['session_minutes']} {zone}"].append(1)
    # variedade: exercícios principais que aparecem em > 80% das semanas
    if weeks >= 12:
        same = sum(1 for k, v in ex_seen.items() if v >= weeks * 0.8 * 1)
        STATS["exercícios iguais o bloco inteiro"].append(same / max(1, len(ex_seen)))
    STATS["eficiência (ganho ÷ ideal)"].append(g_real / max(1e-9, g_ideal))
    STATS[f"eficiência {p0['session_minutes']} min"].append(g_real / max(1e-9, g_ideal))
    STATS[f"eficiência {len(p0['available_days'])} dias"].append(g_real / max(1e-9, g_ideal))
    for m, vals in per_muscle_weeks.items():
        if vals and max(vals) < MUSCLES[m]["mev"] and MUSCLES[m]["mev"] > 0:
            STATS[f"sempre abaixo do mínimo: {m}"].append(1)


def _d(s):
    return date.fromisoformat(str(s)[:10])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--weeks", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--adjust", choices=["misto", "sempre", "nunca"], default="misto",
                    help="se a pessoa usa 'Ajustar a semana' quando falta")
    a = ap.parse_args()
    global ADJUST
    ADJUST = a.adjust
    rng = random.Random(a.seed)
    db.init()
    for pid in range(a.n):
        try:
            run(pid, rng, a.weeks)
        except Exception as ex:  # noqa: BLE001
            issue("exceção", (pid, repr(ex)[:150], traceback.format_exc(limit=3)[-300:]))
    print(f"\n=== musculação ao longo de {a.weeks} semanas: {a.n} pessoas (seed {a.seed}) ===")
    tot = sum(len(STATS[f"zona {z}"]) for z in ("abaixo", "util", "acima")) or 1
    print("músculo-semanas (fora de deload): " + " · ".join(f"{z} {100 * len(STATS[f'zona {z}']) / tot:.1f}%" for z in ("abaixo", "util", "acima")))
    for grp, lab in (("casa", "treino em casa (halteres/peso do corpo)"),):
        t2 = sum(len(STATS[f"{grp} zona {z}"]) for z in ("abaixo", "util", "acima")) or 1
        print(f"  {lab}: " + " · ".join(f"{z} {100 * len(STATS[f'{grp} zona {z}']) / t2:.1f}%" for z in ("abaixo", "util", "acima")))
    for mins in (40, 45, 60, 75, 90):
        t2 = sum(len(STATS[f"tempo {mins} {z}"]) for z in ("abaixo", "util", "acima")) or 1
        print(f"  sessões de {mins} min: " + " · ".join(f"{z} {100 * len(STATS[f'tempo {mins} {z}']) / t2:.1f}%" for z in ("abaixo", "util", "acima")))
    print("\npor objetivo — músculos principais (peito, costas, ombro, coxas, glúteo, braços): % abaixo do mínimo, plano × feito")
    for goal in ("hipertrofia", "forca", "hibrido", "emagrecimento", "saude", "condicionamento"):
        out = []
        for lab in ("plan", "feito"):
            t4 = sum(len(STATS[f"maior {goal} {lab} {z}"]) for z in ("abaixo", "util", "acima")) or 1
            out.append(f"{lab} {100 * len(STATS[f'maior {goal} {lab} abaixo']) / t4:5.1f}%")
        print(f"  {goal:15s} " + " · ".join(out))
    print("\npor músculo (% das semanas abaixo do mínimo / acima do máximo):")
    rows = []
    for m in TRACKED:
        ab, ut, ac = (len(STATS[f"zona-musculo {m} {z}"]) for z in ("abaixo", "util", "acima"))
        t3 = ab + ut + ac or 1
        rows.append((100 * ab / t3, 100 * ac / t3, m))
    for ab, ac, m in sorted(rows, reverse=True):
        print(f"  {MUSCLES[m]['pt']:28s} abaixo {ab:5.1f}% · acima {ac:5.1f}%")
    def mean(v):
        return sum(v) / len(v) if v else float("nan")
    print(f"\neficiência hipertrofia (ganho ÷ ideal no mesmo bloco): {mean(STATS['eficiência (ganho ÷ ideal)']):.2f}")
    for k in sorted(k for k in STATS if k.startswith("eficiência ") and k != "eficiência (ganho ÷ ideal)"):
        print(f"  {k[11:]}: {mean(STATS[k]):.2f}")
    s = sorted(STATS["sessão ÷ tempo informado"])
    if s:
        print(f"sessão de força ÷ tempo informado: mediana {s[len(s) // 2]} · p90 {s[int(len(s) * 0.9)]} · máx {s[-1]}")
    if STATS["exercícios iguais o bloco inteiro"]:
        print(f"exercícios principais iguais em 80%+ das semanas: {100 * mean(STATS['exercícios iguais o bloco inteiro']):.0f}%")
    always = sorted(((len(v), k[len("sempre abaixo do mínimo: "):]) for k, v in STATS.items() if k.startswith("sempre abaixo do mínimo: ")), reverse=True)
    if always:
        print("músculos que ficaram TODAS as semanas abaixo do mínimo (nº de pessoas): " + ", ".join(f"{MUSCLES[m]['pt']} {n}" for n, m in always[:10]))
    if ISSUES:
        print("\n--- problemas")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:260])


if __name__ == "__main__":
    main()
