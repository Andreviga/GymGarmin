"""Simulações do gerador de resistência de ponta a ponta: atletas seguindo o bloco até a prova.

Uso:  python scripts/simulate_endurance_block.py [--n 80] [--seed 1]

Cada atleta tem uma forma física verdadeira (VDOT na corrida) que responde à carga de treino
(modelo de condicionamento/fadiga: CTL 42 dias, ATL 7 dias). Semana a semana o app monta o plano
(como a agenda faz), o atleta executa (cumprindo 70–100% das sessões, às vezes fica 2 semanas
doente, às vezes se machuca quando a carga sobe rápido demais) e faz os testes que o app agenda
— o resultado do teste recalibra os ritmos. No dia da prova: tempo = forma verdadeira × frescor.

Mede:
  • frescor no dia da prova (TSB: ideal +5 a +25) e ganho de condicionamento (CTL);
  • risco de lesão: semanas com carga aguda/crônica (ACWR) > 1,5 e salto de volume > 30%;
  • volta depois de doença: volume da 1ª semana de volta ÷ volume antes de adoecer;
  • ritmos desatualizados: VDOT do app × VDOT verdadeiro na véspera da prova;
  • previsão da meta: status inicial do app (no alvo / possível / ambicioso) × se a meta foi batida;
  • melhora do tempo de prova × tempo previsto no início.
"""
from __future__ import annotations

import argparse
import copy
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gymgarmin import calibration, clock, endurance as en, load, sports as sp  # noqa: E402

START = date(2026, 11, 16)  # segunda-feira
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
STATS: dict = defaultdict(list)


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


DIST = {"5k": 5, "10k": 10, "meia": 21.1, "maratona": 42.2}


def athlete(rng: random.Random):
    level = rng.choice(["iniciante", "intermediario", "avancado"])
    vdot0 = {"iniciante": rng.uniform(30, 38), "intermediario": rng.uniform(38, 47), "avancado": rng.uniform(47, 58)}[level]
    race = rng.choice(list(DIST))
    weeks = rng.choice([8, 12, 16, 20, 24])
    if race == "maratona" and level == "iniciante":
        weeks = max(weeks, 16)
    sessions = rng.choice([3, 4, 5]) if level != "iniciante" else rng.choice([3, 4])
    cur = {"iniciante": rng.uniform(5, 15), "intermediario": rng.uniform(15, 35), "avancado": rng.uniform(35, 65)}[level]
    goal_gain = rng.choice([-1, 0, 1, 2, 4, 6])  # VDOT a mais que a meta exige (negativo = meta folgada)
    target = sp.predict_time(vdot0 + goal_gain, DIST[race])
    knows_vdot = rng.random() < 0.7
    told_volume = rng.random() < 0.6
    return {
        "level": level, "vdot0": vdot0, "race": race, "weeks": weeks, "sessions": sessions, "cur": cur,
        "target": target, "knows_vdot": knows_vdot, "told_volume": told_volume,
        "compliance": rng.uniform(0.7, 1.0), "sick_week": rng.randint(2, weeks - 3) if rng.random() < 0.25 and weeks >= 8 else None,
        "responder": rng.uniform(0.6, 1.4),  # quanto a forma responde à carga
        "age": rng.randint(22, 60),
        "garmin": rng.random() < 0.7,  # treinos chegam pelo relógio (o app conhece o histórico)
    }


def profile_of(a, race_date, anchors_vdot, test_date):
    c = {"enabled": True, "env": "rua", "sessions": a["sessions"],
         "goal": {"type": "prova", "distance": DIST[a["race"]], "date": race_date.isoformat(), "target": sp.fmt_duration(a["target"])}}
    if a["told_volume"]:
        c["current_weekly"] = round(a["cur"], 1)
    prof = {"age": a["age"], "endurance": {"sports": {"corrida": c}, "anchors": {}, "anchors_meta": {}}}
    if anchors_vdot:
        prof["endurance"]["anchors"]["run_vdot"] = anchors_vdot
        prof["endurance"]["anchors_meta"]["run_vdot"] = {"date": test_date.isoformat()}
    return prof


def tier_of(level):
    return level


def run(rng, a, adaptive: bool):
    weeks = a["weeks"]
    race_date = START + timedelta(weeks=weeks, days=6)  # domingo da última semana
    vdot_true = a["vdot0"]
    app_vdot = round(a["vdot0"] + rng.uniform(-2, 2), 1) if a["knows_vdot"] else None
    test_date = START - timedelta(days=rng.randint(0, 50))
    # ponto de partida: a carga que o atleta já faz ("cur" km/semana em rodagens leves), na mesma conta do app
    probe = en.build_session("corrida", "leve", 10.0, {"env": "rua"}, {"run_vdot": a["vdot0"]}, tier_of(a["level"]))
    tss_km = load.planned_stress(probe, {"run_vdot": a["vdot0"]}) / 10
    ctl = atl = a["cur"] * tss_km / 7
    ctl0 = ctl
    tier = {"iniciante": "iniciante", "intermediario": "intermediario", "avancado": "avancado"}[a["level"]]
    prof0 = profile_of(a, race_date, app_vdot, test_date)
    clock.freeze(None)
    # previsão inicial do app
    with_vdot = profile_of(a, race_date, app_vdot or a["vdot0"], test_date)
    clock.freeze(__import__("datetime").datetime.combine(START, __import__("datetime").time(8)))
    g = en.predictions(with_vdot)["sports"]["corrida"].get("goal") or {}
    status0 = g.get("status")
    sessions_log = []
    if a["garmin"]:  # 4 semanas de histórico antes do plano (o que o relógio já registrou)
        for k in range(28, 0, -2):
            dd = START - timedelta(days=k)
            sessions_log.append({"date": dd.isoformat() + "T07:00", "items": [
                {"exercise_id": "corrida_rua", "sport": "corrida", "distance_km": round(a["cur"] / 3.5, 2),
                 "duration_min": round(a["cur"] / 3.5 * 6), "zone": 2}]})
    start_recent = en.recent_volumes(sessions_log, prof0, today=START - timedelta(days=1)) if a["garmin"] else None
    asked_over = 0
    week_vol, week_tss, acwr_hi, jumps, injuries = [], [], 0, 0, 0
    injured_until = -1
    sick = a["sick_week"]
    back_ratio = None
    daily = []
    for w in range(weeks + 1):
        ws = START + timedelta(weeks=w)
        clock.freeze(__import__("datetime").datetime.combine(ws, __import__("datetime").time(8)))
        prof = profile_of(a, race_date, app_vdot, test_date)
        if adaptive:  # app novo: começa pelo histórico e limita pelo que foi feito
            done = en.done_volumes(sessions_log if a["garmin"] else [], prof, ws)
            wp = en.week_plan(prof, ws, START, tier, recent=start_recent, done=done)
        else:         # app antigo: nem histórico inicial na agenda, nem o que foi feito
            wp = en.week_plan(prof, ws, START, tier)
        d = wp["sports"].get("corrida")
        if not d:
            issue("semana sem plano", (w,))
            continue
        off = (sick is not None and sick <= w < sick + 2) or w <= injured_until
        last3 = week_vol[-3:]
        if len(last3) == 3 and d["phase"] not in ("polimento", "prova", "recuperacao") and d["volume"] > 1.5 * (sum(last3) / 3) + 2:
            asked_over += 1  # o plano pede bem mais do que a pessoa fez nas últimas 3 semanas
        vol_done, tss_week = 0.0, 0.0
        day_tss = [0.0] * 7
        for k, it in enumerate(d["sessions"]):
            end = it["endurance"]
            if end.get("type") == "prova":
                continue
            if off or rng.random() > a["compliance"]:
                continue
            tss = load.planned_stress(it, sp.anchors_of(prof) or {"run_vdot": vdot_true})
            km = (end.get("dist_m") or 0) / 1000 or (end["dur_s"] / 60) / 6
            vol_done += km
            tss_week += tss
            day_tss[min(6, k * 2)] += tss
            sessions_log.append({"date": (ws + timedelta(days=min(6, k * 2))).isoformat() + "T07:00", "items": [
                {"exercise_id": "corrida_rua", "sport": "corrida", "distance_km": round(km, 2), "duration_min": round(end["dur_s"] / 60),
                 "zone": 2}]})
            if end.get("type") == "teste":
                # teste de 5 km: o resultado recalibra os ritmos do app
                t5 = sp.predict_time(vdot_true * (1 + rng.gauss(0, 0.01)), 5)
                _, app_vdot = calibration.from_test("corrida", {"distance_km": 5, "time": sp.fmt_duration(t5)})
                app_vdot = round(app_vdot, 1)
                test_date = ws
                STATS["testes por atleta"].append(1)
        # dinâmica diária de condicionamento/fadiga
        for x in day_tss:
            ctl += (x - ctl) / 42
            atl += (x - atl) / 7
            daily.append((ctl, atl))
        acwr = atl / max(1e-6, ctl)
        if acwr > 1.5:
            acwr_hi += 1
        ref = max(week_vol[-3:]) if week_vol else 0
        if ref > 3 and vol_done > ref * 1.3 and not off:
            jumps += 1  # acima do maior volume das 3 semanas anteriores (voltar de semana leve não conta)
        if sick is not None and w == sick + 2 and week_vol:
            pre = [v for v in week_vol[max(0, sick - 3):sick] if v > 0]
            if pre and vol_done > 0:
                back_ratio = vol_done / (sum(pre) / len(pre))
        # lesão: risco sobe com ACWR alto e salto de volume
        risk = 0.004 + 0.06 * max(0.0, acwr - 1.3) + (0.03 if ref > 3 and vol_done > ref * 1.3 else 0)
        if not off and rng.random() < risk:
            injuries += 1
            injured_until = w + rng.choice([1, 2, 3])
        week_vol.append(vol_done)
        week_tss.append(tss_week)
        # forma verdadeira responde ao condicionamento acumulado
        vdot_true = a["vdot0"] + 0.12 * a["responder"] * (ctl - ctl0)
    tsb = ctl - atl
    form = 1 + 0.0015 * max(-30, min(25, tsb)) - (0.002 * (tsb - 25) if tsb > 25 else 0)
    t_race = sp.predict_time(vdot_true * form, DIST[a["race"]])
    t_base = sp.predict_time(a["vdot0"], DIST[a["race"]])
    clock.freeze(None)
    return {"tsb": tsb, "ctl_gain": ctl / max(1e-6, ctl0), "acwr_hi": acwr_hi, "jumps": jumps, "injuries": injuries,
            "back_ratio": back_ratio, "vdot_gap": (app_vdot - vdot_true) if app_vdot else None, "status0": status0,
            "hit": t_race <= a["target"], "improve": 100 * (t_base / t_race - 1), "weeks": weeks, "race": a["race"],
            "level": a["level"], "had_test": any(True for _ in []), "peak_vol": max(week_vol) if week_vol else 0,
            "start_vol": a["cur"], "first_vol": week_vol[0] if week_vol else 0, "told": a["told_volume"] or a["garmin"],
            "asked_over": asked_over, "garmin": a["garmin"]}


def summarize(label, res):
    def pct(x):
        return f"{100 * sum(x) / max(1, len(x)):5.1f}%"
    tsb = [r["tsb"] for r in res]
    print(f"\n### {label} ({len(res)} atletas)")
    print(f"  frescor na prova (TSB +5 a +25): {pct([5 <= t <= 25 for t in tsb])} · cansado (< −10): {pct([t < -10 for t in tsb])}"
          f" · destreinado (> +30): {pct([t > 30 for t in tsb])} · mediana TSB {sorted(tsb)[len(tsb) // 2]:.1f}")
    cg = sorted(r["ctl_gain"] for r in res)
    print(f"  ganho de condicionamento (CTL final/inicial): mediana {cg[len(cg) // 2]:.2f} · p10 {cg[len(cg) // 10]:.2f}")
    print(f"  semanas em que o plano pede >50% acima do que a pessoa fez (quem usa o relógio): "
          f"{sum(r['asked_over'] for r in res if r['garmin']) / max(1, sum(1 for r in res if r['garmin'])):.2f} por atleta")
    print(f"  atletas com semana de ACWR > 1,5: {pct([r['acwr_hi'] > 0 for r in res])} · com salto de volume > 30%: {pct([r['jumps'] > 0 for r in res])}"
          f" · lesões por atleta: {sum(r['injuries'] for r in res) / len(res):.2f}")
    br = [r["back_ratio"] for r in res if r["back_ratio"]]
    if br:
        br.sort()
        print(f"  volta da doença (1ª semana ÷ antes): mediana {br[len(br) // 2]:.2f} · p90 {br[int(len(br) * 0.9)]:.2f} · n {len(br)}")
    gaps = [abs(r["vdot_gap"]) for r in res if r["vdot_gap"] is not None]
    if gaps:
        gaps.sort()
        print(f"  ritmos do app × forma real (|VDOT app − verdadeiro|): mediana {gaps[len(gaps) // 2]:.1f} · p90 {gaps[int(len(gaps) * 0.9)]:.1f}")
    by = defaultdict(list)
    for r in res:
        by[r["status0"]].append(r["hit"])
    print("  previsão da meta × resultado: " + " · ".join(f"{k}: bateu {pct(v)} (n {len(v)})" for k, v in sorted(by.items(), key=lambda x: str(x[0]))))
    for told in (True, False):
        rr = [r["first_vol"] / max(1, r["start_vol"]) for r in res if r["told"] == told and r["first_vol"]]
        if rr:
            rr.sort()
            print(f"  1ª semana ÷ volume que já fazia ({'informou/relógio' if told else 'sem informar e sem relógio'}): mediana {rr[len(rr) // 2]:.2f} · p10 {rr[len(rr) // 10]:.2f} · p90 {rr[int(len(rr) * 0.9)]:.2f}")
    imp = sorted(r["improve"] for r in res)
    print(f"  melhora do tempo de prova: mediana {imp[len(imp) // 2]:.1f}% · p10 {imp[len(imp) // 10]:.1f}% · p90 {imp[int(len(imp) * 0.9)]:.1f}%")
    by_lv = defaultdict(list)
    for r in res:
        by_lv[r["level"]].append(r["improve"])
    print("  melhora por nível: " + " · ".join(f"{k} {sorted(v)[len(v) // 2]:.1f}%" for k, v in by_lv.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--adaptive", action="store_true", help="passa o volume realmente feito (recent) ao gerador")
    a_ = ap.parse_args()
    rng = random.Random(a_.seed)
    athletes = [athlete(rng) for _ in range(a_.n)]
    res = []
    for i, a in enumerate(athletes):
        try:
            res.append(run(random.Random(a_.seed * 1000 + i), a, a_.adaptive))
        except Exception as ex:  # noqa: BLE001
            import traceback
            issue("exceção", (i, repr(ex)[:150], traceback.format_exc(limit=3)[-300:]))
    summarize("app como está (agenda sem o volume realmente feito)" if not a_.adaptive else "com volume realmente feito", res)
    if ISSUES:
        print("\n--- problemas")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:300])


if __name__ == "__main__":
    main()
