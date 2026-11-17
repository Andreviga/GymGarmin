"""Simulações do gerador e da agenda em muitos perfis — mede problemas e imprime um relatório.

Uso:  python scripts/simulate.py [--quick] [--json saida.json]

Para cada perfil (tempo de prática × objetivo × dias × minutos × limitações):
- gera o plano e confere: tempo por sessão, volume por músculo vs alvo e máximo,
  equilíbrio (puxar/empurrar, posteriores/quadríceps), cardio vs meta, exercícios repetidos,
  exercícios proibidos/equipamento, alongamento cobrindo o que foi treinado, preparo presente,
  dias seguidos treinando o mesmo músculo, intervalado na véspera de pernas, zona máxima.
- monta a semana e simula faltar em cada treino de força: estratégia, o que não coube e
  se algum dia passou do tempo.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = tempfile.mkdtemp()
os.environ["GYMGARMIN_HOME"] = TMP
os.environ["GYMGARMIN_DB"] = str(Path(TMP) / "sim.sqlite3")

from gymgarmin import catalog, db, generator, schedule  # noqa: E402
from gymgarmin.progression import apply_cycle  # noqa: E402
from gymgarmin.analysis import balance  # noqa: E402
from gymgarmin.limitations import check_exercise, max_zone  # noqa: E402
from gymgarmin.muscles import MUSCLES  # noqa: E402

MONDAY = date(2026, 11, 16)
MONTHS = [2, 9, 18, 40, 80]
GOALS = list(generator.GOALS)
DAYSETS = {
    "2d": [1, 4], "3d": [0, 2, 4], "3d-seguidos": [0, 1, 2], "4d": [0, 1, 3, 4], "5d": [0, 1, 2, 3, 4],
    "6d": [0, 1, 2, 3, 4, 5], "fds": [5, 6], "7d": list(range(7)),
}
MINUTES = [30, 45, 60, 90]
LIMS = {
    "nenhuma": [], "joelho": [{"key": "joelho", "severity": "evitar"}], "lombar": [{"key": "lombar", "severity": "evitar"}],
    "ombro": [{"key": "ombro", "severity": "evitar"}], "joelho+lombar+ombro": [
        {"key": "joelho", "severity": "evitar"}, {"key": "lombar", "severity": "evitar"}, {"key": "ombro", "severity": "evitar"}],
    "hipertensao": [{"key": "hipertensao", "severity": "evitar"}],
}
EQUIP = {"academia": [], "so_halteres": ["dumbbell", "bands"], "sem_barra": ["dumbbell", "machine", "cable", "kettlebells", "bands"]}


def evaluate(profile: dict) -> dict:
    plan = generator.generate(profile)
    lims = profile.get("limitations") or []
    budget = profile["session_minutes"]
    issues: list[str] = []
    m: dict = {}
    days = plan["days"]
    strength = [d for d in days if d["kind"] == "forca"]
    over = [d["time"]["total"] - budget for d in days if d["time"]["total"] > budget + 0.5]
    if over:
        issues.append(f"tempo>orcamento ({max(over):.1f} min)")
    under = [budget - d["time"]["total"] for d in strength if d["time"]["total"] < budget * 0.7]
    vol_now = generator._weekly_volume(days)
    saturated = all(vol_now.get(mm, 0) >= 0.95 * t for mm, t in plan["generator"]["targets"].items() if t >= 4)
    if under and plan["generator"]["tier"] != "iniciante" and not saturated:
        issues.append(f"sessao_curta ({max(under):.0f} min sobrando)")
    targets = plan["generator"]["targets"]
    vol = generator._weekly_volume(days)
    low = [mm for mm, t in targets.items() if t >= 4 and vol.get(mm, 0) < 0.6 * t]
    zero = [mm for mm, t in targets.items() if t >= 4 and vol.get(mm, 0) == 0]
    high = [mm for mm in vol if mm in MUSCLES and vol[mm] > MUSCLES[mm]["mrv"]]
    weekly_strength_min = sum(d["time"]["total"] for d in strength)
    if strength and weekly_strength_min >= 170:  # abaixo disso é limite físico de tempo (o app avisa)
        if zero:
            issues.append("musculo_zerado:" + ",".join(sorted(zero)))
        elif low:
            issues.append("musculo_baixo:" + ",".join(sorted(low)))
        if high:
            issues.append("acima_mrv:" + ",".join(sorted(high)))
    bal = {b["id"]: b for b in balance({k: {"sets": v} for k, v in {**{x: 0 for x in MUSCLES}, **vol}.items()})}
    for k in ("push_pull", "ham_quad"):
        if bal[k]["status"] in ("baixo", "alto") and strength and len(strength) >= 2 and budget >= 45:
            issues.append(f"desequilibrio:{k}={bal[k]['ratio']}")
    planned = plan["generator"]["cardio_planned_equiv"]
    target = plan["generator"]["cardio_target"]
    m["cardio_pct"] = round(planned / target, 2) if target else 1
    share = generator.CARDIO_SHARE[plan["generator"]["goal"]]
    feasible = min(target, sum(budget * (0.85 if d["kind"] == "cardio" else share) for d in days))
    m["cardio_feasible_pct"] = round(planned / feasible, 2) if feasible else 1
    if planned < 0.6 * feasible and feasible >= 20:
        issues.append(f"cardio<60%_do_possivel ({planned}/{int(feasible)})")
    equipment = set(profile.get("equipment") or [])
    mz = max_zone(lims) or 5
    for d in days:
        ids = [i["exercise_id"] for i in d["items"] if (i.get("phase") or "principal") == "principal"]
        dup = [k for k, c in Counter(ids).items() if c > 1]
        if dup:
            issues.append("repetido_no_dia")
        phases = [i.get("phase") for i in d["items"]]
        if d["kind"] == "forca" and ("preparo" not in phases or "pos" not in phases):
            issues.append("sem_preparo_ou_pos")
        trained = set()
        for i in d["items"]:
            ex = catalog.get(i["exercise_id"])
            st = check_exercise(ex, lims)["status"]
            if st == "evitar":
                issues.append(f"proibido:{ex['id']}")
            if equipment and ex["kind"] == "strength" and ex.get("equipment") not in equipment | {None, "body only", "other"}:
                issues.append(f"equipamento:{ex['id']}")
            z = i.get("zone") or (i.get("intervals") or {}).get("work_zone")
            if z and z > mz:
                issues.append("zona_acima_limite")
            if (i.get("phase") or "principal") == "principal" and ex["kind"] == "strength":
                trained |= {mm for mm in ex["primaryMuscles"] if int(i.get("sets") or 0) >= 3}
        if d["kind"] == "forca":
            stretched = set()
            for i in d["items"]:
                if i.get("phase") == "pos":
                    stretched |= set(catalog.get(i["exercise_id"])["primaryMuscles"])
            m.setdefault("stretch_cov", []).append(len(trained & stretched) / max(1, len(trained)))
            n_main = sum(1 for i in d["items"] if i.get("phase") == "principal" and catalog.get(i["exercise_id"])["kind"] == "strength")
            if n_main < 3:
                issues.append("poucos_exercicios")
    # recuperação: dias consecutivos (inclui dom→seg) com o mesmo músculo pesado
    def direct(items):
        load = defaultdict(float)
        for i in items:
            if (i.get("phase") or "principal") == "principal" and catalog.get(i["exercise_id"])["kind"] == "strength":
                for mm in catalog.get(i["exercise_id"])["primaryMuscles"]:
                    load[mm] += int(i.get("sets") or 0)
        return load
    by_wd = {d["weekday"]: direct(d["items"]) for d in strength}
    for wd, load in by_wd.items():
        nxt = by_wd.get((wd + 1) % 7)
        if nxt:
            heavy = {k for k, v in load.items() if v >= 4} & {k for k, v in nxt.items() if v >= 4}
            if heavy:
                issues.append("dias_seguidos_mesmo_musculo")
                break
    legs = {d["weekday"] for d in days if d.get("template") in generator.HEAVY_LEGS}
    if any("HIIT" in d["name"] and (d["weekday"] + 1) % 7 in legs for d in days):
        issues.append("hiit_vespera_pernas")
    # ---- ciclo: semanas de acúmulo (séries extras) e deload
    cw = int(plan["generator"].get("cycle_weeks") or 4)
    ramp = int(plan["generator"].get("ramp_sets") or 0)
    base_sets = sum(int(i.get("sets") or 0) for d in strength for i in d["items"] if i.get("role"))
    vol0 = generator._weekly_volume(days)
    for w in range(1, cw + 1):
        pos = {"week": w, "length": cw, "cycle": 1, "deload": w == cw}
        allow = {mm: MUSCLES[mm]["mrv"] - vol0.get(mm, 0) for mm in MUSCLES}
        wk = [{**d, "items": apply_cycle(d["items"], pos, ramp, budget_min=budget, allowance=allow)} for d in days]
        for d in wk:
            if d["kind"] == "forca":
                t = generator.time_breakdown(d["items"])["total"]
                if t > budget * 1.05:
                    issues.append(f"ciclo_tempo (semana {w}: {t:.0f} de {budget} min)")
                    break
        if pos["deload"]:
            dl = sum(int(i.get("sets") or 0) for d in wk if d["kind"] == "forca" for i in d["items"] if i.get("role"))
            if base_sets and dl > 0.75 * base_sets:
                issues.append("deload_fraco")
        else:
            v = generator._weekly_volume(wk)
            hi = [mm for mm in v if mm in MUSCLES and v[mm] > MUSCLES[mm]["mrv"] * 1.05]
            if hi:
                issues.append("ciclo_acima_mrv:" + ",".join(sorted(hi)))
    # ---- frequência: músculos grandes 1×/semana com 3+ dias de força (2× rende mais para hipertrofia)
    if len(strength) >= 3 and plan["generator"]["goal"] in ("hipertrofia", "forca", "hibrido"):
        freq = Counter()
        for d in strength:
            for mm, v in direct(d["items"]).items():
                if v >= 2:
                    freq[mm] += 1
        once = [mm for mm in ("chest", "lats", "quadriceps", "hamstrings", "glutes") if targets.get(mm, 0) >= 8 and freq[mm] == 1]
        if once:
            issues.append("frequencia_1x:" + ",".join(once))
    # ---- ordem: principal depois de isolado
    for d in strength:
        roles = [i.get("role") for i in d["items"] if (i.get("phase") or "principal") == "principal" and i.get("role")]
        if "main" in roles and "acc" in roles and roles.index("acc") < max(k for k, r in enumerate(roles) if r == "main"):
            issues.append("ordem_isolado_antes_do_principal")
            break
    return {"plan": plan, "issues": sorted(set(issues)), "metrics": m}


# ------------------------------------------------------------ progressão de carga
def simulate_progression(n_sessions: int = 30) -> dict:
    """Atleta virtual (força cresce ~0,4%/treino) seguindo as cargas sugeridas pela dupla progressão.

    Reps possíveis por série = Epley invertido sobre o 1RM do dia, com fadiga entre séries.
    Mede: saltos de carga que derrubam as reps abaixo da faixa, oscilação (sobe/desce), estagnação e
    ganho de carga em relação ao ganho de força.
    """
    from gymgarmin import progression

    cases = [("Barbell_Squat", "6-10", 100.0, 70.0), ("Barbell_Bench_Press_-_Medium_Grip", "6-10", 80.0, 55.0),
             ("Dumbbell_Bicep_Curl", "10-15", 16.0, 8.0), ("Side_Lateral_Raise", "10-15", 12.0, 6.0),
             ("Leg_Extensions", "10-15", 90.0, 45.0), ("Barbell_Deadlift", "3-5", 160.0, 130.0),
             ("Cable_Rope_Overhead_Triceps_Extension", "10-15", 40.0, 20.0)]
    out = {}
    for eid, reps, one_rm, start in cases:
        if not catalog.get(eid):
            continue
        lo, hi = progression._bounds(reps)
        rm, load, last = one_rm, start, None
        loads, below, flips, stuck = [], 0, 0, 0
        for k in range(n_sessions):
            sug = progression.suggest({"exercise_id": eid, "reps": reps, "load_kg": start}, last)
            load = sug["load_kg"] or load
            sets = []
            fatigue = 0
            for _ in range(3):
                possible = max(0, int(30 * (rm * (1 - fatigue) / load - 1)))  # Epley invertido
                done = min(hi, possible)
                rpe = 10 if possible <= done else min(10, 10 - (possible - done) * 0.5)
                sets.append({"reps": done, "load_kg": load, "rpe": round(rpe, 1)})
                fatigue += 0.03
            if any(x["reps"] < lo for x in sets):
                below += 1
            if loads and len(loads) >= 2 and (loads[-1] - loads[-2]) * (load - loads[-1]) < 0:
                flips += 1
            stuck = stuck + 1 if loads and load == loads[-1] else 0
            loads.append(load)
            last = {"sets": sets}
            rm *= 1.004
        out[eid] = {"start": start, "end": loads[-1], "gain_load_pct": round(100 * (loads[-1] / start - 1), 1),
                    "gain_strength_pct": round(100 * (1.004 ** n_sessions - 1), 1), "below_range": below,
                    "flips": flips, "max_stuck": stuck, "loads": loads}
    return out


def simulate_skips(profile: dict) -> list[dict]:
    db.init()
    with db.conn() as c:
        c.execute("DELETE FROM schedule")
        c.execute("DELETE FROM plans")
        c.execute("DELETE FROM sessions")
    db.save_profile({**db.DEFAULT_PROFILE, **profile})
    prof = db.get_profile()
    plan = generator.generate(prof)
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    prof = db.get_profile()
    out = []
    week = schedule.ensure_week(prof, MONDAY, MONDAY)
    for e in [x for x in week if x["kind"] == "forca"]:
        res = schedule.handle_missed(e["id"], prof, MONDAY)
        after = schedule.ensure_week(prof, MONDAY, MONDAY)
        over = max((generator.time_breakdown(x["items"])["total"] - profile["session_minutes"] * 1.15
                    for x in after if x["status"] == "planejado"), default=0)
        big = [u for u in res["unrecovered"] if (u.startswith("cardio") and int("".join(ch for ch in u if ch.isdigit()) or 0) >= 20)
               or any(u.startswith(b) for b in ("Peitoral", "Dorsais", "Meio das costas", "Quadríceps", "Posteriores", "Glúteos"))
               and int((u.split("(")[-1].split(" ")[0]) or 0) >= 3]
        out.append({"day": e["day_name"], "strategy": res["strategy"], "unrecovered": big,
                    "over": round(over, 1)})
        schedule.undo(e["id"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    grid = list(itertools.product(MONTHS, GOALS, DAYSETS, MINUTES, LIMS, EQUIP))
    if a.quick:
        grid = grid[::7]
    counts: Counter = Counter()
    by_issue: dict[str, list] = defaultdict(list)
    cov, cardio, feas = [], [], []
    for months, goal, dk, mins, lk, ek in grid:
        prof = {"training_months": months, "goal": goal, "available_days": DAYSETS[dk], "session_minutes": mins,
                "limitations": LIMS[lk], "equipment": EQUIP[ek]}
        r = evaluate(prof)
        cov += r["metrics"].get("stretch_cov", [])
        cardio.append(r["metrics"]["cardio_pct"])
        feas.append(r["metrics"]["cardio_feasible_pct"])
        for i in r["issues"]:
            key = i.split(":")[0].split(" ")[0]
            counts[key] += 1
            if len(by_issue[key]) < 4:
                by_issue[key].append(f"{months}m {goal} {dk} {mins}min {lk} {ek} → {i}")
    n = len(grid)
    print(f"\n=== {n} perfis simulados ===")
    for k, c in counts.most_common():
        print(f"{k:32s} {c:5d}  ({c / n:5.1%})")
        for ex in by_issue[k]:
            print("     ", ex)
    print(f"cobertura média do alongamento: {sum(cov) / max(1, len(cov)):.0%}")
    print(f"cardio planejado / meta (mediana): {sorted(cardio)[len(cardio) // 2]:.0%}")
    print(f"cardio planejado / possível no tempo (mediana): {sorted(feas)[len(feas) // 2]:.0%}")

    # faltas
    skip_grid = [(m, g, dk, mins, lk) for m in (2, 18, 40) for g in ("hipertrofia", "hibrido", "emagrecimento")
                 for dk in ("3d", "4d", "5d", "6d") for mins in (45, 60) for lk in ("nenhuma", "joelho")]
    if a.quick:
        skip_grid = skip_grid[::5]
    strat: Counter = Counter()
    unrec = 0
    overs = []
    total = 0
    for m_, g, dk, mins, lk in skip_grid:
        prof = {"training_months": m_, "goal": g, "available_days": DAYSETS[dk], "session_minutes": mins,
                "limitations": LIMS[lk]}
        for r in simulate_skips(prof):
            total += 1
            strat[r["strategy"]] += 1
            unrec += bool(r["unrecovered"])
            if r["over"] > 0.5:
                overs.append((m_, g, dk, mins, lk, r))
    print(f"\n=== {total} faltas simuladas ===")
    print("estratégias:", dict(strat))
    print(f"com algo que não coube: {unrec} ({unrec / max(1, total):.0%})")
    print(f"dias acima do tempo após reajuste: {len(overs)}")
    for o in overs[:5]:
        print("     ", o)
    print("\n=== progressão de carga (atleta virtual, 30 treinos, força +12,7%) ===")
    for eid, r in simulate_progression().items():
        print(f"  {eid:40s} carga {r['start']:g}→{r['end']:g} kg ({r['gain_load_pct']:+.1f}%) · abaixo da faixa {r['below_range']}×"
              f" · sobe/desce {r['flips']}× · maior tempo parado {r['max_stuck']} treinos")
    if a.json:
        Path(a.json).write_text(json.dumps({"counts": counts, "skips": strat}, default=str, indent=1))


if __name__ == "__main__":
    main()
