"""Simulações dos esportes de resistência em muitos atletas — mede problemas e imprime um relatório.

Uso:  python scripts/simulate_endurance.py [--n 400] [--seed 1] [--json saida.json]

Para cada atleta aleatório (modalidades × nível × dias livres × minutos × metas de volume,
provas com data em horizontes variados, eventos multiesporte, com/sem referências, limitações):
  • plano (gerador): dias usados, força × resistência, perna pesada na véspera de forte/longo,
    dois treinos no dia, sessões por modalidade, tempo das sessões de força;
  • semanas do bloco inteiro (até 2 semanas depois da prova): volume construído × planejado,
    progressão, semana leve, teto, longo maior que as outras, nº de sessões fortes, testes,
    polimento, prova no dia, ritmos/potências plausíveis, duração mínima/máxima das sessões;
  • exportação de cada sessão (FIT decodificado pelo SDK oficial + JSON do Connect);
  • agenda (amostra): slots preenchidos, perda de sessão em cada treino e prontidão.
"""
from __future__ import annotations

import argparse
import json
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
os.environ["GYMGARMIN_DB"] = str(Path(TMP) / "sim.sqlite3")
os.environ.pop("DATABASE_URL", None)

from garmin_fit_sdk import Decoder, Stream  # noqa: E402

from gymgarmin import db, endurance as en, garmin_export as ge, generator, readiness, schedule, sports as sp  # noqa: E402

MONDAY = date(2026, 11, 16)
ISSUES: Counter = Counter()
EXAMPLES: dict[str, list] = defaultdict(list)
STATS: dict[str, list] = defaultdict(list)


def issue(kind: str, ex) -> None:
    ISSUES[kind] += 1
    if len(EXAMPLES[kind]) < 4:
        EXAMPLES[kind].append(ex)


def flat(steps):
    out = []
    for s in steps:
        out += [x for _ in range(s["n"]) for x in s["steps"]] if s["kind"] == "repeat" else [s]
    return out


def vol_of(it: dict) -> float:
    e = it["endurance"]
    u = en.VOLUME[e["sport"]]["unit"]
    return (e.get("dist_m") or 0) / 1000 if u == "km" else (e.get("dist_m") or 0) if u == "m" else e["dur_s"] / 3600


# ------------------------------------------------------------------ atletas
LEVELS = {"iniciante": 3, "intermediario": 12, "avancado": 36, "elite": 80}


def random_athlete(rng: random.Random, i: int) -> dict:
    level = rng.choice(list(LEVELS))
    n_days = rng.choice([3, 4, 4, 5, 5, 6, 7, 7])
    days = sorted(rng.sample(range(7), n_days))
    prof = {"age": rng.randint(20, 60), "weight_kg": rng.randint(55, 100), "training_months": LEVELS[level],
            "goal": rng.choice(["hibrido", "hibrido", "hipertrofia", "condicionamento", "saude", "forca", "emagrecimento"]),
            "available_days": days, "session_minutes": rng.choice([40, 45, 60, 60, 75, 90])}
    if rng.random() < 0.15:
        prof["limitations"] = [{"key": rng.choice(["joelho", "lombar", "ombro", "cardiaco"]), "severity": rng.choice(["cuidado", "evitar"])}]
    end: dict = {"sports": {}, "anchors": {}, "anchors_meta": {}}
    mode = rng.random()
    if mode < 0.18:  # evento multiesporte
        ev = rng.choice(list(en.EVENTS))
        end["event"] = {"type": ev, "date": (MONDAY + timedelta(weeks=rng.randint(2, 30), days=rng.randint(0, 6))).isoformat(),
                        "target": rng.choice([None, "1:20:00", "2:45:00", "5:30:00"])}
    else:
        pool = list(sp.SPORTS)
        chosen = rng.sample(pool, rng.choice([1, 1, 2, 2, 3]))
        for s in chosen:
            c = {"enabled": True, "env": rng.choice(list(sp.SPORTS[s]["envs"])),
                 "sessions": rng.choice([1, 2, 2, 3, 3, 4, 5, 6]) if s not in ("trilha",) else rng.choice([1, 1, 2])}
            r = rng.random()
            unit = en.VOLUME[s]["unit"]
            if r < 0.35 and en.RACE_PEAK.get(s):
                dists = [d for d, _ in en.RACE_PEAK[s]]
                dist = rng.choice(dists)
                c["goal"] = {"type": "prova", "distance": dist,
                             "target": rng.choice([None, "25:00", "50:00", "1:50:00", "4:00:00", "30:00", "8:00"]),
                             "date": (MONDAY + timedelta(weeks=rng.randint(1, 26), days=rng.randint(0, 6))).isoformat()}
            elif r < 0.7:
                c["goal"] = {"type": "volume", "weekly": {"km": rng.choice([10, 20, 30, 50, 80]), "h": rng.choice([2, 4, 6, 10]),
                                                           "m": rng.choice([3000, 6000, 12000, 30000])}[unit]}
            if rng.random() < 0.4:
                c["current_weekly"] = {"km": rng.choice([5, 15, 30, 60]), "h": rng.choice([1, 3, 6]), "m": rng.choice([1000, 4000, 15000])}[unit]
            if rng.random() < 0.3:
                c["long_day"] = rng.choice(days)
            if s == "natacao":
                c["pool_m"] = rng.choice([25, 50])
            end["sports"][s] = c
    if rng.random() < 0.6:
        end["anchors"] = {"run_vdot": rng.choice([30, 38, 45, 55, 65]), "ftp_w": rng.choice([120, 200, 280, 350]),
                          "css_s100": rng.choice([80, 100, 120, 150]), "row_2k_s": rng.choice([390, 450, 520])}
        end["anchors_meta"] = {k: {"date": (MONDAY - timedelta(weeks=rng.randint(0, 12))).isoformat()} for k in end["anchors"]}
    if rng.random() < 0.3:
        end["anchors"]["lthr"] = rng.randint(150, 178)
    prof["endurance"] = end
    prof["_id"] = i
    return prof


# ------------------------------------------------------------------ checagens
def check_plan(prof: dict, plan: dict) -> None:
    pid = prof["_id"]
    days = plan["days"]
    avail = set(prof["available_days"])
    used = [d["weekday"] for d in days]
    if not set(used) <= avail:
        issue("plano: dia fora dos disponíveis", (pid, sorted(set(used) - avail)))
    per_day = Counter(used)
    if per_day and max(per_day.values()) > 2:
        issue("plano: mais de 2 treinos no dia", (pid, dict(per_day)))
    for d in days:
        if per_day[d["weekday"]] > 1 and d.get("kind") == "resistencia" and not d.get("double"):
            issue("plano: dois treinos no dia sem marcação", (pid, d["name"]))
    budget = prof["session_minutes"]
    for d in days:
        if d["kind"] == "forca" and d["time"]["total"] > budget + 0.5:
            issue("plano: força passou do tempo", (pid, d["name"], d["time"]["total"], budget))
    cfg = en.config(prof, (plan.get("endurance_plan") or {}).get("sessions"))
    got = Counter(d["endurance_slot"]["sport"] for d in days if d.get("kind") == "resistencia")
    for s, c in cfg.items():
        if got[s] != c["sessions"]:
            issue("plano: nº de sessões ≠ configurado", (pid, s, got[s], c["sessions"]))
    hard_leg = {d["weekday"] for d in days if d.get("kind") == "resistencia" and d["endurance_slot"]["sport"] in en.LEG_SPORTS
                and (d["endurance_slot"]["role"] == "qualidade" or (d["endurance_slot"]["role"] == "longo"))}
    for d in days:
        if d["kind"] == "forca" and d.get("template") in generator.HEAVY_LEGS and (d["weekday"] + 1) % 7 in hard_leg:
            issue("plano: perna pesada na véspera de forte/longo", (pid, d["name"]))
    # qualidade colada em qualidade, ou na véspera de longo (longo + longo no fim de semana é aceito)
    q_leg = {d["weekday"] for d in days if d.get("kind") == "resistencia" and d["endurance_slot"]["sport"] in en.LEG_SPORTS
             and d["endurance_slot"]["role"] == "qualidade"}
    for wd in q_leg:
        if ((wd + 1) % 7 in hard_leg or (wd - 1) % 7 in q_leg) and len(avail) >= 5:
            issue("plano: forte de perna colado em forte/longo", (pid, sorted(q_leg), sorted(hard_leg)))
            break
    n_strength = sum(1 for d in days if d["kind"] == "forca")
    STATS["força/semana"].append(n_strength)
    if n_strength == 0:
        issue("plano: sem musculação", (pid, prof["goal"]))


PACE_RANGE = {"pace": ("fast_s_km", "slow_s_km", 150, 600), "swim_pace": ("fast_s_100", "slow_s_100", 55, 240),
              "split": ("fast_s_500", "slow_s_500", 80, 200)}
MIN_MIN = {"corrida": 15, "ciclismo": 25, "natacao": 14, "remo": 10, "caminhada": 15, "trilha": 45}
MAX_MIN = {"corrida": 240, "ciclismo": 420, "natacao": 120, "remo": 120, "caminhada": 300, "trilha": 600}


def check_session(pid, ws, s, it, anchors):
    end = it["endurance"]
    fl = flat(end["steps"])
    if not fl or any((x.get("dur_s") or 0) <= 0 for x in fl):
        issue("sessão: passo sem duração", (pid, s, end["title"]))
    mins = end["dur_s"] / 60
    if end["type"] not in ("prova",) and mins < MIN_MIN[s]:
        issue("sessão: curta demais", (pid, s, end["title"], round(mins)))
    if mins > MAX_MIN[s]:
        issue("sessão: longa demais", (pid, s, end["title"], round(mins)))
    for x in fl:
        t = x.get("target") or {}
        if x["kind"] != "rest" and not t:
            issue("sessão: passo sem alvo", (pid, s, end["title"], x["kind"]))
        if t.get("type") in PACE_RANGE:
            f, sl, lo, hi = PACE_RANGE[t["type"]]
            if not (lo <= t[f] <= t[sl] <= hi):
                issue(f"sessão: ritmo implausível ({t['type']})", (pid, s, end["title"], t.get("label")))
        if t.get("type") == "power" and anchors.get("ftp_w") and t["high_w"] > anchors["ftp_w"] * 1.6:
            issue("sessão: potência implausível", (pid, end["title"], t.get("label")))
    if end.get("hard") and end["type"] not in ("teste", "prova") and s in ("corrida", "ciclismo", "remo"):
        work = sum(x["dur_s"] for x in fl if x["kind"] == "interval")
        if work < 6 * 60:
            issue("sessão: forte com pouco trabalho (<6 min)", (pid, s, end["title"], work // 60))


def check_weeks(prof: dict) -> None:
    pid = prof["_id"]
    tier = generator.tier_for(prof["training_months"])
    level = en._level(tier)
    cfg = en.config(prof)
    anchors = sp.anchors_of(prof)
    race_dates = []
    for s, c in cfg.items():
        g = c.get("goal") or {}
        if g.get("date"):
            race_dates.append(en._d(g["date"]))
    last = max(race_dates) if race_dates else MONDAY + timedelta(weeks=20)
    n_weeks = max(4, (last - MONDAY).days // 7 + 3)
    prev: dict = {}
    vols: dict[str, list] = defaultdict(list)
    for w in range(min(n_weeks, 40)):
        ws = MONDAY + timedelta(weeks=w)
        try:
            wp = en.week_plan(prof, ws, MONDAY, tier)
        except Exception as e:  # noqa: BLE001
            issue("semana: exceção", (pid, w, repr(e)[:120]))
            continue
        hard = tests = 0
        for s, d in wp["sports"].items():
            built = sum(vol_of(x) for x in d["sessions"])
            planned = d["volume"]
            vols[s].append((w, planned, d["phase"]))
            if planned and d["phase"] not in ("prova",) and planned >= en.VOLUME[s]["min"]:
                ratio = built / planned
                STATS[f"construído/planejado {s}"].append(round(ratio, 2))
                if ratio > 1.35 or ratio < 0.7:
                    issue("semana: volume construído ≠ planejado", (pid, s, w, d["phase"], round(planned, 2), round(built, 2),
                                                                    [x["endurance"]["title"] for x in d["sessions"]]))
            longs = [x for x in d["sessions"] if x["endurance"]["role"] == "longo"]
            others = [x for x in d["sessions"] if x["endurance"]["role"] != "longo"]
            if longs and others and vol_of(longs[0]) < max(vol_of(o) for o in others) * 0.95 and s in en.LEG_SPORTS:
                issue("semana: longo menor que outra sessão", (pid, s, w, round(vol_of(longs[0]), 2),
                                                               [(o["endurance"]["title"], round(vol_of(o), 2)) for o in others]))
            for x in d["sessions"]:
                check_session(pid, ws, s, x, anchors)
                hard += 1 if x["endurance"]["hard"] and x["endurance"]["type"] not in ("teste", "prova") else 0
                tests += 1 if x["endurance"]["type"] == "teste" else 0
                if x["endurance"]["type"] == "teste" and d["phase"] in ("polimento", "prova", "pico"):
                    issue("semana: teste em pico/polimento/prova", (pid, s, w, d["phase"]))
            if d["phase"] == "recuperacao" and any(x["endurance"]["hard"] for x in d["sessions"]):
                issue("semana: sessão forte na recuperação", (pid, s, w))
            if s in prev and d["phase"] not in ("semana_leve", "polimento", "prova", "recuperacao") \
                    and prev[s][1] not in ("semana_leve", "polimento", "prova", "recuperacao") and prev[s][0] > 0:
                jump = planned / prev[s][0]
                gran = {"km": 0.5, "h": 1 / 12, "m": 100}[en.VOLUME[s]["unit"]]
                if jump > 1.10 and planned - prev[s][0] > gran + 1e-9 and planned > prev[s][0] * 1.10 + gran:
                    issue("semana: aumento > 10%", (pid, s, w, round(prev[s][0], 2), round(planned, 2)))
            prev[s] = (planned, d["phase"])
        cap = 2 if level == "iniciante" else 3
        if hard > cap:
            issue("semana: fortes acima do teto", (pid, w, hard, cap))
        if tests > 1:
            issue("semana: mais de 1 teste", (pid, w, tests))
    # polimento abaixo do pico
    for s, lst in vols.items():
        first_taper = min((w for w, _, ph in lst if ph == "polimento"), default=None)
        peak = max((v for w, v, ph in lst if ph in ("pico", "construcao", "base", "manutencao")
                    and (first_taper is None or w < first_taper)), default=0)
        for w, v, ph in lst:
            if ph == "polimento" and peak and v > peak * 0.9:
                issue("bloco: polimento sem redução", (pid, s, w, round(v, 2), round(peak, 2)))
        goal = (cfg[s].get("goal") or {})
        if goal.get("type") == "volume" and goal.get("weekly"):
            reached = max(v for _, v, _ in lst)
            STATS["meta de volume atingida (%)"].append(round(100 * reached / goal["weekly"]))


def check_exports(prof: dict) -> None:
    pid = prof["_id"]
    tier = generator.tier_for(prof["training_months"])
    wp = en.week_plan(prof, MONDAY + timedelta(weeks=2), MONDAY, tier)
    items = [x for d in wp["sports"].values() for x in d["sessions"]]
    for it in items:
        day = {"name": it["endurance"]["title"], "items": [it]}
        try:
            data = ge.to_fit(day, "Sim")
            msgs, errs = Decoder(Stream.from_byte_array(bytearray(data))).read()
            if errs:
                issue("export: FIT com erro", (pid, it["endurance"]["title"], errs[:1]))
            n = len(msgs.get("workout_step_mesgs", []))
            if n == 0 or n > 50:
                issue("export: FIT com nº de passos estranho", (pid, it["endurance"]["title"], n))
            cj = ge.to_connect_json(day, "Sim")
            json.dumps(cj)
            if not cj["workoutSegments"][0]["workoutSteps"]:
                issue("export: Connect sem passos", (pid, it["endurance"]["title"]))
        except Exception as e:  # noqa: BLE001
            issue("export: exceção", (pid, it["endurance"]["title"], repr(e)[:150]))


def check_schedule(prof: dict) -> None:
    pid = prof["_id"]
    db.init()
    with db.conn() as c:
        for t in ("schedule", "plans", "sessions"):
            c.execute(f"DELETE FROM {t}")
    clean = {k: v for k, v in prof.items() if k != "_id"}
    db.save_profile(clean)
    plan = generator.generate(db.get_profile(), today=MONDAY)
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    p = db.get_profile()
    n_slots = sum(1 for d in saved["days"] if d.get("endurance_slot"))
    for w in (0, 1, 3):
        entries = schedule.build_week(saved, p, MONDAY + timedelta(weeks=w))
        got = sum(1 for e in entries if e.get("kind") == "resistencia" and not e.get("race"))
        week_ph = {ph for d in en.week_plan(p, MONDAY + timedelta(weeks=w), MONDAY, generator.tier_for(p["training_months"]))["sports"].values()
                   for ph in [d["phase"]]}
        n_week = sum(len(d["sessions"]) for d in en.week_plan(p, MONDAY + timedelta(weeks=w), MONDAY,
                     generator.tier_for(p["training_months"]), sessions_override=(saved.get("endurance_plan") or {}).get("sessions"))["sports"].values())
        if got < min(n_slots, n_week) and not (week_ph & {"prova", "recuperacao"}):
            issue("agenda: slot de resistência vazio", (pid, w, got, n_slots, week_ph))
        dates = Counter(e["date"] for e in entries)
        if dates and max(dates.values()) > 2:
            issue("agenda: mais de 2 treinos no dia", (pid, w, dict(dates)))
    entries = [db.save_entry(e) for e in schedule.build_week(saved, p, MONDAY)]
    for e in entries:
        try:
            r = schedule.handle_missed(e["id"], p, today=MONDAY)
            STATS["reajuste"].append(r["strategy"])
            schedule.undo(e["id"])
        except ValueError:
            pass
        except Exception as ex:  # noqa: BLE001
            issue("agenda: exceção no reajuste", (pid, e["day_name"], repr(ex)[:150], traceback.format_exc(limit=2)[-300:]))
    for e in db.list_schedule(MONDAY.isoformat(), (MONDAY + timedelta(days=6)).isoformat()):
        for act in ("moderar", "trocar", "descanso"):
            try:
                readiness.adjust_entry(e, act, sp.anchors_of(p))
            except Exception as ex:  # noqa: BLE001
                issue("prontidão: exceção", (pid, e["day_name"], act, repr(ex)[:150]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--schedule", type=int, default=60, help="quantos atletas passam pela agenda")
    ap.add_argument("--json")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    for i in range(args.n):
        prof = random_athlete(rng, i)
        try:
            plan = generator.generate(prof, today=MONDAY)
            check_plan(prof, plan)
        except Exception as e:  # noqa: BLE001
            issue("plano: exceção", (i, repr(e)[:150], traceback.format_exc(limit=3)[-400:]))
            continue
        check_weeks(prof)
        if i % 4 == 0:
            check_exports(prof)
        if i < args.schedule:
            check_schedule(prof)
    print(f"\n=== {args.n} atletas simulados (seed {args.seed}) ===")
    for k, v in ISSUES.most_common():
        print(f"{v:5d}  {k}")
        for ex in EXAMPLES[k]:
            print("        ", str(ex)[:260])
    print("\n--- estatísticas")
    for k, v in sorted(STATS.items()):
        if v and isinstance(v[0], (int, float)):
            v2 = sorted(v)
            print(f"{k}: média {sum(v2) / len(v2):.2f} · mín {v2[0]} · mediana {v2[len(v2) // 2]} · máx {v2[-1]}")
        else:
            print(f"{k}: {Counter(v).most_common()}")
    if args.json:
        Path(args.json).write_text(json.dumps({"issues": ISSUES, "examples": EXAMPLES}, ensure_ascii=False, default=str, indent=1))


if __name__ == "__main__":
    main()
