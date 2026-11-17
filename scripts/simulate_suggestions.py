"""Simulações da progressão de carga sugerida: levantadores virtuais seguindo as sugestões do app.

Uso:  python scripts/simulate_suggestions.py [--n 60] [--weeks 24] [--seed 1]

Cada levantador tem, por exercício, uma força real que RESPONDE ao treino:
  • série produtiva (RPE 7–9,5, dentro da faixa de reps) → ganha força conforme o nível;
  • treino fácil demais (RPE ≤ 6,5) → quase não ganha; falha seguida (abaixo da faixa, RPE 10) → acumula fadiga;
  • semanas paradas → perde força depois de ~2 semanas.
No treino ele usa a carga sugerida (arredondada ao equipamento: anilhas 2,5 kg, halteres de 2 kg, pilha de 5 kg),
faz as séries mirando o topo da faixa, cansa de uma série para a outra, e anota as reps e (às vezes) o RPE —
com viés pessoal (quem sempre anota mais pesado ou mais leve). Dias bons e ruins.

Cenários: normal · sem_rpe (nunca anota RPE) · chute_pesado / chute_leve (carga inicial 35% errada) ·
viés_+1 / viés_-1 (anota RPE 1 ponto acima/abaixo) · pausa (3 ou 6 semanas paradas no meio) ·
deload (semana leve a cada 5ª) · faixas (força 3–5, hipertrofia 6–10, acessório 10–15).

Mede: % de treinos na zona produtiva × fácil × falha; ganho de força × o máximo possível;
quantos treinos até entrar na zona a partir de um chute errado; carga final × carga ideal;
quedas de carga fora de deload/pausa (vai e volta); carga logo depois do deload e da pausa.
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gymgarmin import clock, progression as pg  # noqa: E402

START = date(2026, 1, 5)
EXS = [("Barbell_Squat", 2.5, "3-5"), ("Barbell_Bench_Press_-_Medium_Grip", 2.5, "6-10"), ("Leg_Press", 5.0, "8-12"),
       ("Dumbbell_Bicep_Curl", 2.0, "8-12"), ("Side_Lateral_Raise", 2.0, "10-15"), ("Triceps_Pushdown", 5.0, "10-15"),
       ("Seated_Cable_Rows", 5.0, "8-12")]
BASE_1RM = {"Barbell_Squat": 100, "Barbell_Bench_Press_-_Medium_Grip": 75, "Leg_Press": 180, "Dumbbell_Bicep_Curl": 18,
            "Side_Lateral_Raise": 13, "Triceps_Pushdown": 45, "Seated_Cable_Rows": 75}
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
STATS: dict = defaultdict(list)


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def avail(load, step):
    """Carga que existe no equipamento (o atleta arredonda para baixo se não existir)."""
    return max(step, int(load / step + 1e-6) * step)


def run(rng: random.Random, scen: str, weeks: int):
    level = rng.choice(["iniciante", "intermediario", "avancado"])
    rate = {"iniciante": 0.02, "intermediario": 0.008, "avancado": 0.003}[level]  # ganho semanal com treino produtivo
    k_rep = rng.uniform(0.85, 1.15)
    bias = {"vies_+1": 1.0, "vies_-1": -1.0}.get(scen, 0.0)
    logs_rpe = scen != "sem_rpe" and rng.random() < 0.8
    per_week = 2
    pause = None
    if scen == "pausa":
        a = weeks // 2
        pause = (a, a + rng.choice([3, 6]))
    out = {}
    for eid, step, reps in EXS:
        if scen == "faixas":
            reps = rng.choice(["3-5", "6-10", "10-15"])
        lo, hi = pg._bounds(reps)
        T = BASE_1RM[eid] * rng.uniform(0.7, 1.3) * (0.8 if level == "iniciante" else 1.2 if level == "avancado" else 1)
        T0 = T
        fatigue = 0.0
        ideal0 = T / (1 + (hi + 2) / (30 * k_rep))      # carga ideal: topo da faixa com 2 reps no tanque
        guess = ideal0 * {"chute_pesado": 1.35, "chute_leve": 0.65}.get(scen, rng.uniform(0.85, 1.15))
        perfs, history, loads = [], [], []
        prod = easy = fail = 0
        sessions = 0
        to_zone = None
        drops = 0
        post_deload, post_pause = [], []
        prev_load = None
        max_gain_T = T
        weeks_off = 0
        for w in range(weeks):
            if pause and pause[0] <= w < pause[1]:
                weeks_off += 1
                if weeks_off > 2:
                    T *= 0.985
                continue
            returned = weeks_off >= 3
            weeks_off = 0
            deload = scen in ("deload",) and w % 5 == 4
            for di in range(per_week):
                d = START + timedelta(weeks=w, days=di * 3)
                clock.freeze(datetime.combine(d, datetime.min.time()) + timedelta(hours=18))
                item = {"exercise_id": eid, "reps": reps, "sets": 3, "load_kg": round(guess, 1)}
                sug = pg.suggest(item, pg.pick_last(perfs), deload=deload, today=d)
                L = avail(sug["load_kg"] or guess, step)
                C = T * (1 + rng.gauss(0, 0.03)) * (1 - fatigue)
                sets = []
                target_hi = hi if not deload else max(lo, hi - 2)
                for j in range(2 if deload else 3):
                    rmax = 30 * k_rep * (C / L - 1) - j * rng.uniform(0.3, 1.2)  # cansa de série para série
                    rmax_i = max(0, int(rmax))
                    if deload:
                        done = min(target_hi, max(1, rmax_i - 4))
                    else:
                        stop = int(rmax - rng.choice([0.5, 1.0, 1.0, 1.5]))  # para com ~1 rep no tanque
                        done = min(target_hi, stop) if stop >= lo else min(lo, max(1, rmax_i))  # abaixo da faixa: vai até a falha
                    true_rpe = max(5.0, min(10.0, 10 - (rmax - done)))
                    s = {"reps": done, "load_kg": L, "done": True}
                    if logs_rpe:
                        s["rpe"] = max(5.0, min(10.0, round((true_rpe + bias + rng.choice([-0.5, 0, 0, 0.5])) * 2) / 2))
                    sets.append((s, true_rpe))
                sessions += 1
                last_rpe = sets[-1][1]
                reps_done = [s["reps"] for s, _ in sets]
                if not deload:
                    in_zone = last_rpe >= 7 and min(reps_done) >= lo
                    if in_zone:
                        prod += 1
                        to_zone = to_zone if to_zone is not None else sessions
                    elif last_rpe <= 6.5:
                        easy += 1
                    elif min(reps_done) < lo:
                        fail += 1
                    # adaptação: produtivo ganha; fácil quase nada; falha acumula fadiga
                    g = rate / per_week
                    if in_zone:
                        T *= 1 + g
                    elif last_rpe <= 6.5:
                        T *= 1 + 0.25 * g
                    else:
                        T *= 1 + 0.5 * g
                    if min(reps_done) < lo and last_rpe >= 9.5:
                        fatigue = min(0.12, fatigue + 0.02)
                    else:
                        fatigue = max(0.0, fatigue - 0.01)
                else:
                    fatigue = max(0.0, fatigue - 0.04)
                max_gain_T *= 1 + rate / per_week if not deload else 1
                if prev_load is not None and L < prev_load - 1e-6 and not deload and not returned \
                        and not (history and history[-1].get("deload")):
                    drops += 1
                if history and history[-1].get("deload") and not deload:
                    before = next((h["load"] for h in reversed(history) if not h.get("deload")), None)
                    if before:
                        post_deload.append(L / before)
                if returned and di == 0:
                    post_pause.append((reps_done, last_rpe, sug.get("note", "")))
                history.append({"load": L, "deload": deload})
                loads.append(L)
                prev_load = L
                perf = {"date": d.isoformat(), "sets": [s for s, _ in sets]}
                if deload:
                    perf["deload"] = True
                perfs.insert(0, perf)  # mais recente primeiro, como o app
                returned = False
        clock.freeze(None)
        n_eff = max(1, prod + easy + fail)
        ideal_end = T / (1 + (hi + 2) / (30 * k_rep))
        out[eid] = {"prod": prod / n_eff, "easy": easy / n_eff, "fail": fail / n_eff, "gain": T / T0 - 1,
                    "gain_max": max_gain_T / T0 - 1, "to_zone": to_zone, "end_ratio": loads[-1] / ideal_end if loads else None,
                    "drops": drops, "post_deload": post_deload, "post_pause": post_pause, "reps": reps, "level": level}
    return out


SCEN = ["normal", "sem_rpe", "chute_pesado", "chute_leve", "vies_+1", "vies_-1", "pausa", "deload", "faixas"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--weeks", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    agg: dict = defaultdict(lambda: defaultdict(list))
    for scen in SCEN:
        for _ in range(a.n):
            res = run(rng, scen, a.weeks)
            for eid, r in res.items():
                A = agg[scen]
                for k in ("prod", "easy", "fail", "drops"):
                    A[k].append(r[k])
                A["eff"].append(r["gain"] / r["gain_max"] if r["gain_max"] > 0 else 1)
                A["to_zone"].append(r["to_zone"] if r["to_zone"] is not None else 99)
                if r["end_ratio"]:
                    A["end_ratio"].append(r["end_ratio"])
                A["post_deload"] += r["post_deload"]
                for reps_done, rpe, note in r["post_pause"]:
                    lo = pg._bounds(r["reps"])[0]
                    A["pause_fail"].append(int(min(reps_done) < lo or rpe >= 9.9))
                A[f"prod {r['reps']}"].append(r["prod"])
                A[f"eff {r['level']}"].append(r["gain"] / r["gain_max"] if r["gain_max"] > 0 else 1)
                if r["to_zone"] is None:
                    issue(f"nunca entrou na zona produtiva ({scen})", (eid, r["reps"], r["level"], round(r["end_ratio"] or 0, 2)))
                if r["drops"] >= 4:
                    issue(f"carga vai e volta (≥4 quedas fora de deload/pausa) ({scen})", (eid, r["reps"], r["drops"]))

    def m(v):
        v = sorted(v)
        return sum(v) / len(v) if v else float("nan")

    def pct(v):
        return f"{100 * m(v):5.1f}%"

    print(f"\n=== progressão sugerida: {len(SCEN)} cenários × {a.n} levantadores × {len(EXS)} exercícios × {a.weeks} semanas ===")
    print(f"{'cenário':14s} {'zona':>7s} {'fácil':>7s} {'falha':>7s} {'ganho/máx':>10s} {'até zona':>9s} {'carga/ideal':>12s} {'quedas':>7s}")
    for scen in SCEN:
        A = agg[scen]
        tz = sorted(A["to_zone"])
        print(f"{scen:14s} {pct(A['prod']):>7s} {pct(A['easy']):>7s} {pct(A['fail']):>7s} {pct(A['eff']):>10s} "
              f"{tz[len(tz) // 2]:>5} (p90 {tz[int(len(tz) * 0.9)]})" if tz else "", end="")
        print(f" {m(A['end_ratio']):10.2f} {m(A['drops']):7.2f}")
    print("\n--- por faixa de reps (zona produtiva)")
    for k in sorted({k for A in agg.values() for k in A if k.startswith("prod ")}):
        vals = [x for A in agg.values() for x in A.get(k, [])]
        print(f"  {k[5:]:>6s}: {pct(vals)}")
    print("--- eficiência por nível (ganho de força / máximo possível)")
    for k in ("eff iniciante", "eff intermediario", "eff avancado"):
        vals = [x for A in agg.values() for x in A.get(k, [])]
        print(f"  {k[4:]:>13s}: {pct(vals)}")
    pdl = agg["deload"]["post_deload"]
    print(f"--- carga no 1º treino depois do deload ÷ carga antes do deload: média {m(pdl):.3f} (1,00 = volta onde estava)")
    pf = agg["pausa"]["pause_fail"]
    print(f"--- 1º treino depois da pausa abaixo da faixa ou até a falha: {pct(pf)}")
    if ISSUES:
        print("\n--- problemas")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:220])


if __name__ == "__main__":
    main()
