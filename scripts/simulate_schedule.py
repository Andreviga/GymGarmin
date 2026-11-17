"""Simulações da agenda e dos reajustes: semanas seguidas com atletas que faltam, avisam antes ou depois,
movem treinos, desfazem ajustes, têm treinos já no Garmin, dias com dois treinos e prontidão baixa.

Uso:  python scripts/simulate_schedule.py [--n 120] [--weeks 6] [--seed 1]

Confere a cada operação:
  • tempo: nenhum treino de força planejado acima de 115% do tempo da sessão;
  • no máximo 2 treinos por dia; nada mexe em treino passado ou já feito;
  • mover: data ≥ hoje, dentro da semana, sem colar em treino que repete os mesmos músculos
    (força) ou em outra sessão forte/longa de perna (resistência);
  • desfazer: a semana volta exatamente ao estado anterior;
  • Garmin: treino enviado que mudou fica marcado para reenviar;
  • status: treino registrado → feito; data passada sem registro → perdido;
  • volume: quanto das séries perdidas (por músculo) foi recuperado na semana;
  • semanas seguintes: ciclo (deload) e montagem corretos; metas sem números negativos.
"""
from __future__ import annotations

import argparse
import copy
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
os.environ["GYMGARMIN_DB"] = str(Path(TMP) / "sched.sqlite3")
os.environ.pop("DATABASE_URL", None)

from gymgarmin import catalog, db, endurance as en, generator, readiness, schedule, sports as sp  # noqa: E402

MONDAY = date(2026, 11, 16)
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
STATS: dict = defaultdict(list)


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def week_entries(ws: date) -> list[dict]:
    return db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())


def direct_sets(entries: list[dict]) -> Counter:
    c: Counter = Counter()
    for e in entries:
        if e["status"] not in ("planejado", "feito"):
            continue
        for it in schedule._strength_items(e):
            for m in catalog.get(it["exercise_id"])["primaryMuscles"]:
                c[m] += int(it.get("sets") or 0)
    return c


def random_profile(rng: random.Random) -> dict:
    n = rng.choice([2, 3, 3, 4, 4, 5, 6])
    prof = {"training_months": rng.choice([3, 9, 18, 40, 80]), "available_days": sorted(rng.sample(range(7), n)),
            "goal": rng.choice(["hipertrofia", "forca", "hibrido", "emagrecimento", "saude", "condicionamento"]),
            "session_minutes": rng.choice([40, 45, 60, 75, 90]), "age": rng.randint(22, 55)}
    if rng.random() < 0.15:
        prof["limitations"] = [{"key": rng.choice(["joelho", "ombro", "lombar"]), "severity": "evitar"}]
    if rng.random() < 0.4:
        s = rng.sample(["corrida", "ciclismo", "natacao", "caminhada"], rng.choice([1, 1, 2]))
        sp_cfg = {}
        for k in s:
            c = {"enabled": True, "sessions": rng.choice([1, 2, 3])}
            if k == "corrida" and rng.random() < 0.5:
                c["goal"] = {"type": "prova", "distance": rng.choice([5, 10, 21.1]),
                             "date": (MONDAY + timedelta(weeks=rng.randint(2, 8), days=rng.randint(0, 6))).isoformat()}
            sp_cfg[k] = c
        prof["endurance"] = {"sports": sp_cfg, "anchors": {"run_vdot": 45, "ftp_w": 220, "css_s100": 110},
                             "anchors_meta": {k: {"date": MONDAY.isoformat()} for k in ("run_vdot", "ftp_w", "css_s100")}}
    return prof


def snapshot(ws: date) -> list:
    return sorted(((e["id"], e["date"], e["status"], repr(e.get("items")), e.get("day_name")) for e in week_entries(ws)))


def check_week_state(pid, ws: date, today: date, budget: float, before: dict | None = None, op: str = "",
                     warned: bool = False):
    entries = week_entries(ws)
    per_day = Counter(e["date"] for e in entries if e["status"] in ("planejado", "feito"))
    prev = Counter(e["date"] for e in (before or {}).values() if e["status"] in ("planejado", "feito"))
    grew = {d: n for d, n in per_day.items() if n > 2 and n > prev.get(d, 0)}
    if grew and not warned:
        issue(f"mais de 2 treinos no dia ({op})", (pid, grew))
    for e in entries:
        if e["status"] == "planejado" and e.get("kind") == "forca":
            t = generator.time_breakdown(e["items"])["total"]
            if t > budget * 1.15 + 0.5:
                issue("força acima de 115% do tempo", (pid, e["day_name"], round(t), budget))
        if before and e["id"] in before:
            b = before[e["id"]]
            if (schedule._d(e["date"]) < today or b["status"] == "feito") and repr(b["items"]) != repr(e["items"]) \
                    and e["status"] != "pulado":
                issue("mexeu em treino passado/feito", (pid, e["day_name"], e["date"], str(today)))
            if b.get("garmin_workout_id") and repr(b["items"]) != repr(e["items"]) and e["status"] == "planejado" \
                    and not e.get("garmin_outdated"):
                issue("Garmin: mudou sem marcar para reenviar", (pid, e["day_name"]))
    return entries


def log_session(e: dict, rng: random.Random):
    items = []
    for it in e["items"]:
        if (it.get("phase") or "principal") != "principal":
            continue
        if it.get("endurance"):
            en_ = it["endurance"]
            items.append({"exercise_id": it["exercise_id"], "sport": it.get("sport"), "duration_min": round(en_["dur_s"] / 60),
                          "distance_km": round((en_.get("dist_m") or 0) / 1000, 2) or None, "zone": it.get("zone")})
        elif it.get("sets"):
            items.append({"exercise_id": it["exercise_id"], "sets": [{"reps": 8, "load_kg": 40, "rpe": 8}] * int(it["sets"])})
        elif it.get("duration_min"):
            items.append({"exercise_id": it["exercise_id"], "duration_min": it["duration_min"], "zone": it.get("zone")})
    db.save_session({"date": e["date"] + "T18:00", "schedule_id": e["id"], "items": items})
    e["status"] = "feito"
    db.save_entry(e)


def run_athlete(pid: int, rng: random.Random, weeks: int):
    with db.conn() as c:
        for t in ("schedule", "plans", "sessions"):
            c.execute(f"DELETE FROM {t}")
    prof0 = random_profile(rng)
    db.save_profile({**db.DEFAULT_PROFILE, **prof0})
    prof = db.get_profile()
    plan = generator.generate(prof, today=MONDAY)
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    prof = db.get_profile()
    budget = schedule.session_budget(prof, saved)
    behavior = rng.choice(["assiduo", "falta_pouco", "falta_muito", "avisa_antes", "esquece"])
    p_miss = {"assiduo": 0.05, "falta_pouco": 0.15, "falta_muito": 0.4, "avisa_antes": 0.25, "esquece": 0.25}[behavior]
    for w in range(weeks):
        ws = MONDAY + timedelta(weeks=w)
        try:
            entries = schedule.ensure_week(prof, ws, ws)
        except Exception as ex:  # noqa: BLE001
            issue("exceção ao montar a semana", (pid, w, repr(ex)[:120], traceback.format_exc(limit=2)[-250:]))
            return
        if not entries and generator.WEEKDAYS:
            issue("semana vazia", (pid, w))
            continue
        # semana do ciclo
        cyc = next((e.get("cycle") for e in entries if e.get("cycle")), None)
        if cyc and cyc.get("deload"):
            sets = sum(int(i.get("sets") or 0) for e in entries for i in schedule._strength_items(e))
            base = sum(int(i.get("sets") or 0) for d in saved["days"] if d["kind"] == "forca" for i in d["items"] if i.get("role"))
            if base and sets > 0.75 * base:
                issue("deload sem reduzir na agenda", (pid, w, sets, base))
        # parte dos treinos já enviados ao Garmin
        for e in entries:
            if rng.random() < 0.3 and e["status"] == "planejado":
                e["garmin_workout_id"] = rng.randint(1000, 9999)
                db.save_entry(e)
        for day in range(7):
            today = ws + timedelta(days=day)
            todays = [e for e in week_entries(ws) if e["date"] == today.isoformat() and e["status"] == "planejado"]
            for e in todays:
                before = {x["id"]: copy.deepcopy(x) for x in week_entries(ws)}
                pre_direct = direct_sets(week_entries(ws))
                r = rng.random()
                if rng.random() < 0.08:  # prontidão baixa no dia
                    adj = readiness.adjust_entry(e, rng.choice(["moderar", "trocar"]), sp.anchors_of(prof))
                    if adj:
                        db.save_entry(adj)
                        e = db.get_entry(e["id"])
                if r < p_miss:
                    when = today - timedelta(days=rng.choice([0, 1, 2])) if behavior == "avisa_antes" else (
                        today + timedelta(days=1) if behavior == "esquece" else today)
                    when = max(when, ws)
                    snap = snapshot(ws)
                    try:
                        res = schedule.handle_missed(e["id"], prof, today=when)
                    except ValueError:
                        continue
                    except Exception as ex:  # noqa: BLE001
                        issue("exceção no reajuste", (pid, e["day_name"], repr(ex)[:120], traceback.format_exc(limit=3)[-300:]))
                        continue
                    STATS["estratégia"].append(f"{e.get('kind')}:{res['strategy']}")
                    after = check_week_state(pid, ws, when, budget, before, f"reajuste {e.get('kind')}:{res['strategy']}")
                    # mover: regras
                    for x in after:
                        if x.get("moved_from") == e["date"] and x["id"] not in before:
                            if schedule._d(x["date"]) < when:
                                issue("moveu para o passado", (pid, x["day_name"], x["date"], str(when)))
                            if x.get("kind") == "forca":
                                bad = [y for y in after if y["id"] != x["id"] and y.get("kind") == "forca"
                                       and y["status"] in ("planejado", "feito") and abs((schedule._d(y["date"]) - schedule._d(x["date"])).days) == 1
                                       and schedule._conflict(x, y)]
                                if bad:
                                    issue("moveu colando nos mesmos músculos", (pid, x["day_name"], x["date"], bad[0]["day_name"]))
                            elif x.get("kind") == "resistencia" and schedule._is_heavy_end(x):
                                bad = [y for y in after if y["id"] != x["id"] and y["status"] in ("planejado", "feito")
                                       and abs((schedule._d(y["date"]) - schedule._d(x["date"])).days) == 1 and schedule._is_heavy_end(y)]
                                if bad:
                                    issue("moveu forte/longo colado em outro forte", (pid, x["day_name"], x["date"]))
                    # recuperação de volume (força)
                    future_slack = sum(max(0.0, budget * 1.15 - schedule._minutes(x)) for x in before.values()
                                       if x.get("kind") == "forca" and x["status"] == "planejado" and x["id"] != e["id"]
                                       and schedule._d(x["date"]) > max(when, schedule._d(e["date"]) if schedule._d(e["date"]) == when else when))
                    if e.get("kind") == "forca":
                        lost = direct_sets([e])
                        post = direct_sets(after)
                        tot_lost = sum(lost.values())
                        if tot_lost:
                            gained = sum(max(0, post[m] - (pre_direct[m] - lost[m])) for m in lost)
                            pct = round(100 * min(1, gained / tot_lost))
                            STATS["recuperado (% séries perdidas)"].append(pct)
                            if future_slack >= 30:
                                STATS["recuperado quando havia ≥30 min livres"].append(pct)
                            if pct < 30 and res["strategy"] == "redistribuir" and future_slack >= 30:
                                issue("reajuste recuperou < 30% (havia tempo livre)", (pid, e["day_name"], when.isoformat(), round(future_slack),
                                                                                      res["changes"][:3], res["unrecovered"], tot_lost))
                    # desfazer de vez em quando: estado idêntico
                    if rng.random() < 0.25:
                        schedule.undo(e["id"])
                        if snapshot(ws) != snap:
                            diff = set(snapshot(ws)) ^ set(snap)
                            issue("desfazer não restaurou a semana", (pid, e["day_name"], list(diff)[:2]))
                elif r < p_miss + 0.05 and day < 6:  # move manualmente para outro dia livre da semana
                    free = [ws + timedelta(days=d) for d in range(day + 1, 7)]
                    if free:
                        to = rng.choice(free)
                        res = None
                        try:
                            res = schedule.move(e["id"], to.isoformat())
                            STATS["mover manual com aviso"].append(bool(res.get("warning")))
                        except Exception as ex:  # noqa: BLE001
                            issue("exceção ao mover", (pid, repr(ex)[:120]))
                        check_week_state(pid, ws, today, budget, before, "mover manual",
                                         warned="treinos" in str((res or {}).get("warning")))
                else:
                    if behavior != "esquece" or rng.random() < 0.85:
                        log_session(e, rng)
        # fim da semana: status e metas
        final = schedule.sync_status(week_entries(ws), schedule._sessions_in_week(ws), ws + timedelta(days=7))
        for x in final:
            if x["status"] == "planejado":
                issue("ficou 'planejado' depois da data", (pid, w, x["day_name"], x["date"]))
        logged_ids = {s.get("schedule_id") for s in schedule._sessions_in_week(ws)}
        for x in final:
            if x["id"] in logged_ids and x["status"] != "feito":
                issue("registrado mas não ficou 'feito'", (pid, w, x["day_name"], x["status"]))
        try:
            g = schedule.week_goals(final, prof, ws)
            for k in ("sessions", "cardio", "sets", "minutes"):
                if any(v < 0 for v in g[k].values()):
                    issue("meta negativa", (pid, k, g[k]))
            STATS["treinos feitos/planejados"].append(round(g["sessions"]["done"] / max(1, g["sessions"]["planned"]), 2))
        except Exception as ex:  # noqa: BLE001
            issue("exceção nas metas", (pid, repr(ex)[:120]))
    # reconstruir semana atual preserva feitos
    ws = MONDAY + timedelta(weeks=weeks)
    entries = schedule.ensure_week(prof, ws, ws)
    if entries:
        e0 = entries[0]
        log_session(e0, rng)
        schedule.rebuild_week(prof, today=ws + timedelta(days=1))
        if not any(x["id"] == e0["id"] and x["status"] == "feito" for x in week_entries(ws)):
            issue("refazer semana apagou treino feito", (pid,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    db.init()
    for i in range(a.n):
        try:
            run_athlete(i, rng, a.weeks)
        except Exception as ex:  # noqa: BLE001
            issue("exceção geral", (i, repr(ex)[:150], traceback.format_exc(limit=3)[-300:]))
    print(f"\n=== {a.n} atletas × {a.weeks} semanas (seed {a.seed}) ===")
    for k, v in ISSUES.most_common():
        print(f"{v:5d}  {k}")
        for ex in EX[k]:
            print("        ", str(ex)[:300])
    print("\n--- estatísticas")
    for k, v in STATS.items():
        if v and isinstance(v[0], (int, float)) and not isinstance(v[0], bool):
            v2 = sorted(v)
            print(f"{k}: média {sum(v2) / len(v2):.1f} · mediana {v2[len(v2) // 2]} · p25 {v2[len(v2) // 4]} · n {len(v2)}")
        else:
            print(f"{k}: {Counter(v).most_common()}")


if __name__ == "__main__":
    main()
