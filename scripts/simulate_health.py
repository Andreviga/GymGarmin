"""Simulações de saúde e sono: pessoas virtuais com fisiologia conhecida → o painel acerta?

Uso:  python scripts/simulate_health.py [--n 60] [--seed 1]

Parte A — análise (health.report), N pessoas por cenário, 56 dias cada, com ruído realista:
  estavel · treino_pesa (treino forte piora o sono/HRV/FC) · absorve (treino não muda nada) ·
  doenca (últimos 3 dias: FC de repouso ↑, HRV ↓) · doenca_sem_hrv (relógio sem HRV) ·
  sobrecarga (2 semanas subindo FC e caindo HRV) · privacao (5 noites curtas) · irregular (horário bagunçado) ·
  melhorando (FC de repouso caindo, VO2máx subindo) · noturno (treino depois das 20h derruba o sono) ·
  idoso (70 anos, 7h15 de sono) · faltando (sensores ausentes, dias sem dado) · estranho (valores inválidos)
  Mede: acerto (detecta o que existe), alarme falso (acusa o que não existe), texto (sem "None",
  ponto decimal, contradições), estabilidade dia a dia (alerta que liga/desliga).

Parte B — coleta (health.collect) com um Garmin que varia o formato das respostas (None, {}, [],
  estresse −1/−2, dias sem sono, sem HRV, erros), 35 dias de sincronizações:
  dados gravados = verdade, backfill completo, dia "final" não é buscado de novo, chamadas por dia.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gymgarmin import clock, health  # noqa: E402

TODAY = date(2026, 11, 17)
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
HIT: dict = defaultdict(lambda: [0, 0])   # cenário:checagem → [acertos, total]


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


def score(key, ok: bool):
    HIT[key][0] += int(ok)
    HIT[key][1] += 1


# ------------------------------------------------------------ fisiologia ---
def person(rng: random.Random, scen: str, days: int = 56):
    age = 70 if scen == "idoso" else rng.randint(22, 58)
    rhr0 = rng.gauss(56, 6)
    hrv0 = max(18.0, rng.gauss(48, 13))
    dur0 = 7.25 if scen == "idoso" else max(7.0, rng.gauss(7.45, 0.3))
    score0 = rng.gauss(77, 5)
    bed0 = rng.gauss(-45, 40)  # minutos em relação à meia-noite
    stress0 = rng.gauss(30, 6)
    has_hrv = scen not in ("doenca_sem_hrv",) and not (scen == "faltando" and rng.random() < 0.6)
    has_score = not (scen == "faltando" and rng.random() < 0.5)
    has_baseline = has_hrv and rng.random() < 0.7
    effect = scen == "treino_pesa"
    late_effect = scen == "noturno"
    n_week = rng.choice([3, 4, 5])
    train_days = sorted(rng.sample(range(7), n_week))
    heavy_days = set(rng.sample(train_days, min(2, len(train_days))))
    late_days = set(rng.sample(train_days, max(1, len(train_days) // 2))) if scen == "noturno" else set()
    days_out, load, times = {}, {}, {}
    for i in range(days, -1, -1):
        d = TODAY - timedelta(days=i)
        wd = d.weekday()
        # treino do dia
        if wd in train_days:
            load[d] = rng.uniform(110, 160) if wd in heavy_days else rng.uniform(30, 60)
            times[d] = [rng.uniform(20.0, 21.5) if wd in late_days else rng.uniform(6.5, 18.5)]
        prev = d - timedelta(days=1)
        prev_heavy = prev in load and load[prev] >= 100
        prev_late = prev in times and times[prev][0] >= 20
        rhr = rhr0 + rng.gauss(0, 1.6)
        hrv = hrv0 * math.exp(rng.gauss(0, 0.11))
        dur = dur0 + rng.gauss(0, 0.45)
        sc = score0 + rng.gauss(0, 5)
        bed = bed0 + rng.gauss(0, 110 if scen == "irregular" else 25)
        stress = stress0 + rng.gauss(0, 5)
        if effect and prev_heavy:
            sc -= rng.uniform(9, 14); hrv *= 0.84; rhr += 2.5; dur -= 0.3
        if late_effect and prev_late:
            sc -= rng.uniform(8, 12); dur -= 0.4
        if scen in ("doenca", "doenca_sem_hrv") and i < 3:
            rhr += rng.uniform(6.5, 9); hrv *= 0.72; sc -= 10; stress += 12
        if scen == "sobrecarga" and i < 14:
            f = (14 - i) / 14
            rhr += 5.5 * f; hrv *= 1 - 0.28 * f; sc -= 6 * f
        if scen == "privacao" and i < 5:
            dur = rng.uniform(4.9, 5.7); sc = rng.uniform(42, 56)
        if scen == "melhorando":
            rhr -= 4.5 * (days - i) / days
        rec: dict = {"date": d.isoformat()}
        missing_day = scen == "faltando" and rng.random() < 0.3
        missing_night = scen == "faltando" and rng.random() < 0.2
        if not missing_day:
            rec["rhr"] = round(rhr)
            rec["stress_avg"] = round(stress)
            rec["steps"] = rng.randint(4000, 12000)
            rec["int_mod"] = rng.randint(5, 40)
            rec["int_vig"] = rng.randint(0, 20)
        if not missing_night:
            bedm = int(round(bed)) % 1440
            s = {"duration_h": round(max(2.0, dur), 2), "deep_pct": round(rng.gauss(17, 3), 1), "rem_pct": round(rng.gauss(22, 3), 1),
                 "start": f"{bedm // 60:02d}:{bedm % 60:02d}", "awake_min": rng.randint(5, 40)}
            if has_score:
                s["score"] = int(max(20, min(100, round(sc))))
            rec["sleep"] = s
            if has_hrv:
                rec["hrv"] = {"last_night": round(hrv)}
                if has_baseline:
                    rec["hrv"].update(low=round(hrv0 * 0.86), high=round(hrv0 * 1.16), status="BALANCED")
        if scen == "estranho":
            if rng.random() < 0.1:
                rec["stress_avg"] = -1
            if rng.random() < 0.05:
                rec["rhr"] = 0
            if rng.random() < 0.05 and rec.get("sleep"):
                rec["sleep"]["duration_h"] = 0
            if rng.random() < 0.05 and rec.get("hrv"):
                rec["hrv"]["last_night"] = 0
        days_out[d.isoformat()] = rec
    weekly = {}
    if scen == "melhorando":
        weekly = {"vo2max": 50.5, "history": [{"date": (TODAY - timedelta(days=49)).isoformat(), "vo2max": 48.4},
                                              {"date": TODAY.isoformat(), "vo2max": 50.5}]}
    elif scen != "faltando":
        v = round(rng.gauss(46, 5), 1)
        weekly = {"vo2max": v, "history": [{"date": (TODAY - timedelta(days=42)).isoformat(), "vo2max": v + rng.choice([-0.4, 0, 0.3])},
                                           {"date": TODAY.isoformat(), "vo2max": v}]}
    return days_out, load, times, weekly, {"age": age}


BAD_TEXT = re.compile(r"None|\bnan\b|\binf\b|\b\d+\.\d{1,2}\b|\+-|−-|--|\bundefined\b|0h00")


def text_of(rep: dict) -> list[str]:
    out = list(rep.get("alerts", [])) + list(rep.get("effects", []))
    for a in rep.get("areas", []):
        out += [a["text"], a.get("tip") or ""]
    n = rep.get("last_night") or {}
    out += n.get("reasons", []) + n.get("tips", [])
    return [t for t in out if t]


def area(rep, title):
    return next((a for a in rep["areas"] if a["title"] == title), None)


def check_report(scen: str, rep: dict, pid: int):
    k = scen
    # ---- texto
    for t in text_of(rep):
        if BAD_TEXT.search(t):
            issue("texto com valor estranho (None/nan/ponto decimal)", (scen, pid, t[:160]))
    rec = area(rep, "Recuperação")
    if rep["alerts"] and rec and rec["level"] == "ok" and any("FC de repouso" in a for a in rep["alerts"]):
        issue("contradição: alerta de recuperação com Recuperação 'bom'", (scen, pid))
    eff = " ".join(rep["effects"])
    pesando = "pesando" in eff
    illness_alert = any("FC de repouso" in a for a in rep["alerts"])
    sleep_alert = any("noites com menos de 6 h" in a for a in rep["alerts"])
    sono = area(rep, "Sono")
    cond = area(rep, "Condicionamento")
    # ---- detecção (acerto) e alarme falso, por cenário
    if scen == "treino_pesa":
        score(f"{k}: detecta que o treino pesa", pesando)
    if scen in ("estavel", "absorve", "melhorando", "idoso", "irregular"):
        score(f"{k}: sem acusar que o treino pesa (alarme falso)", not pesando)
    if scen in ("doenca", "doenca_sem_hrv"):
        score(f"{k}: alerta de recuperação/doença", illness_alert)
        score(f"{k}: Recuperação marcada (atenção/alerta)", bool(rec) and rec["level"] != "ok")
    if scen in ("estavel", "absorve", "treino_pesa", "melhorando", "idoso", "irregular", "noturno", "privacao", "faltando", "estranho"):
        score(f"{k}: sem alerta de doença (alarme falso)", not illness_alert)
    if scen == "sobrecarga":
        score(f"{k}: Recuperação marcada (atenção/alerta)", bool(rec) and rec["level"] != "ok")
    if scen == "privacao":
        score(f"{k}: alerta de sono curto", sleep_alert)
        score(f"{k}: Sono em alerta", bool(sono) and sono["level"] == "alerta")
        score(f"{k}: última noite ruim/regular", (rep["last_night"] or {}).get("level") in ("ruim", "regular"))
    if scen in ("estavel", "absorve", "melhorando", "idoso", "noturno", "treino_pesa"):
        score(f"{k}: sem alerta de sono (alarme falso)", not sleep_alert)
    if scen == "irregular":
        score(f"{k}: detecta horário irregular", bool(sono) and "irregular" in sono["text"])
    if scen in ("estavel", "absorve", "idoso", "melhorando"):
        score(f"{k}: não acusa horário irregular (alarme falso)", not (sono and "irregular" in sono["text"]))
    if scen == "melhorando":
        score(f"{k}: reconhece melhora (VO2máx/FC caindo)", bool(cond) and cond["level"] == "ok" and bool(cond.get("tip")))
    if scen == "noturno":
        score(f"{k}: detecta que treino à noite derruba o sono", "derrubou" in eff)
    if scen in ("estavel", "absorve", "treino_pesa"):
        score(f"{k}: não acusa treino noturno (alarme falso)", "derrubou" not in eff)
    if scen == "idoso":
        score(f"{k}: 7h15 aos 70 anos não é sono curto", not (sono and "curta" in sono["text"]))
    if scen == "absorve":
        score(f"{k}: diz que absorve bem a carga", "absorvendo bem" in eff)


def rolling(scen, days, load, times, weekly, prof, pid):
    """Painel calculado dia após dia nas últimas 3 semanas: alertas ligando/desligando."""
    flips, alerts = 0, 0
    prev = None
    for back in range(21, -1, -1):
        t = TODAY - timedelta(days=back)
        sub = {k: v for k, v in days.items() if k <= t.isoformat()}
        rep = health.report(sub, weekly, load, prof, t, times)
        on = any("FC de repouso" in a for a in rep["alerts"])
        alerts += on
        if prev is not None and on != prev:
            flips += 1
        prev = on
    return flips, alerts


# ------------------------------------------------------------ coleta (parte B) ---
class VariedGarmin:
    """Garmin com respostas de formato variado."""

    def __init__(self, rng, truth):
        self.rng, self.truth, self.calls = rng, truth, Counter()

    def _t(self, d):
        return self.truth.get(d)

    def get_sleep_data(self, d):
        self.calls[("sleep", d)] += 1
        t = self._t(d)
        r = self.rng.random()
        if r < 0.03:
            raise RuntimeError("503")
        if not t or not t.get("sleep") or r < 0.06:
            return self.rng.choice([None, {}, {"dailySleepDTO": {}}, {"dailySleepDTO": {"sleepTimeSeconds": None}}])
        s = t["sleep"]
        start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=(int(s["start"][:2]) * 60 + int(s["start"][3:])))
        dto = {"sleepTimeSeconds": int(s["duration_h"] * 3600), "deepSleepSeconds": int(s["duration_h"] * 3600 * s["deep_pct"] / 100),
               "remSleepSeconds": int(s["duration_h"] * 3600 * s["rem_pct"] / 100),
               "lightSleepSeconds": int(s["duration_h"] * 3600 * (100 - s["deep_pct"] - s["rem_pct"]) / 100),
               "awakeSleepSeconds": s["awake_min"] * 60, "sleepStartTimestampLocal": int(start.timestamp() * 1000)}
        if s.get("score") is not None:
            dto["sleepScores"] = {"overall": {"value": s["score"]}}
        return {"dailySleepDTO": dto}

    def get_hrv_data(self, d):
        self.calls[("hrv", d)] += 1
        t = self._t(d)
        if not t or not t.get("hrv"):
            return None
        h = t["hrv"]
        return {"hrvSummary": {"lastNightAvg": h["last_night"], "status": h.get("status"),
                               "baseline": {"balancedLow": h.get("low"), "balancedUpper": h.get("high")} if h.get("low") else None}}

    def get_stats(self, d):
        self.calls[("stats", d)] += 1
        t = self._t(d) or {}
        if self.rng.random() < 0.03:
            raise RuntimeError("timeout")
        return {"restingHeartRate": t.get("rhr"), "averageStressLevel": t.get("stress_avg", -2), "totalSteps": t.get("steps"),
                "moderateIntensityMinutes": t.get("int_mod"), "vigorousIntensityMinutes": t.get("int_vig"),
                "privacyProtected": False}

    def get_rhr_day(self, d):
        self.calls[("rhr", d)] += 1
        return {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": []}}}

    def get_training_status(self, d):
        self.calls[("weekly", d)] += 1
        return self.rng.choice([None, {}, {"mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 47.2}}},
                                {"mostRecentTrainingStatus": {"latestTrainingStatusData": {"1": None}}}])

    def get_max_metrics(self, d):
        return []

    def get_fitnessage_data(self, d):
        return self.rng.choice([None, {}, {"fitnessAge": 33.4, "chronologicalAge": 40}])

    def get_body_composition(self, a, b):
        return self.rng.choice([None, {}, {"dateWeightList": []}, {"dateWeightList": [{"weight": 81234.0}]}])


def collection_sim(rng, n=20):
    for pid in range(n):
        days, *_ = person(rng, rng.choice(["estavel", "faltando", "estranho"]), 70)
        api = VariedGarmin(rng, days)
        store: dict = {}
        start = TODAY - timedelta(days=35)
        for k in range(36):
            day = start + timedelta(days=k)
            truth_upto = {d: v for d, v in days.items() if d <= day.isoformat()}
            api.truth = truth_upto
            for hour in sorted(rng.sample([7.0, 9.5, 13.0, 18.0, 21.0, 23.2], rng.choice([1, 2, 3]))):
                clock.freeze(datetime.combine(day, datetime.min.time()) + timedelta(hours=hour))
                try:
                    health.collect(api, day, store.get, store.__setitem__)
                except Exception as ex:  # noqa: BLE001
                    issue("coleta: exceção", (pid, repr(ex)[:120], traceback.format_exc(limit=3)[-300:]))
        clock.freeze(None)
        # verdade × gravado (dias finais)
        for k in range(30):
            d = (TODAY - timedelta(days=k + 2)).isoformat()
            raw = store.get(f"health:{d}")
            t = days.get(d) or {}
            if not raw:
                if d >= (start - timedelta(days=27)).isoformat():
                    issue("coleta: dia nunca buscado", (pid, d))
                continue
            g = json.loads(raw)
            if t.get("rhr") and g.get("rhr") not in (t["rhr"], None):
                issue("coleta: FC de repouso gravada errada", (pid, d, t["rhr"], g.get("rhr")))
            if (t.get("sleep") or {}).get("duration_h") and g.get("sleep") and abs(g["sleep"]["duration_h"] - t["sleep"]["duration_h"]) > 0.02:
                issue("coleta: duração do sono errada", (pid, d, t["sleep"]["duration_h"], g["sleep"]["duration_h"]))
            if (t.get("stress_avg") or 0) < 0 and "stress_avg" in g:
                issue("coleta: estresse −1/−2 gravado como valor", (pid, d))
            if (t.get("sleep") or {}).get("duration_h", 0) >= 1 and not g.get("sleep"):
                issue("coleta: noite com dado no Garmin ficou sem sono (falha passageira não refeita)", (pid, d, g.get("tries"), g.get("final"), g.get("fetched_at"), api.calls.get(("sleep", d))))
        per_day = Counter()
        for (kind, d), c in api.calls.items():
            per_day[d] += c
            if kind == "sleep" and c > 6:
                issue("coleta: mesmo dia buscado demais", (pid, d, c))
        HIT["coleta: chamadas/dia (média)"][0] += sum(api.calls.values())
        HIT["coleta: chamadas/dia (média)"][1] += 36


SCENARIOS = ["estavel", "treino_pesa", "absorve", "doenca", "doenca_sem_hrv", "sobrecarga", "privacao", "irregular",
             "melhorando", "noturno", "idoso", "faltando", "estranho"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    flips_total, alert_days = Counter(), Counter()
    for scen in SCENARIOS:
        for pid in range(a.n):
            days, load, times, weekly, prof = person(rng, scen)
            try:
                rep = health.report(days, weekly, load, prof, TODAY, times)
                check_report(scen, rep, pid)
                if scen in ("estavel", "absorve", "treino_pesa", "doenca") and pid < 20:
                    f, al = rolling(scen, days, load, times, weekly, prof, pid)
                    flips_total[scen] += f
                    alert_days[scen] += al
            except Exception as ex:  # noqa: BLE001
                issue("exceção na análise", (scen, pid, repr(ex)[:150], traceback.format_exc(limit=3)[-300:]))
    collection_sim(rng)
    print(f"\n=== saúde e sono: {len(SCENARIOS)} cenários × {a.n} pessoas (seed {a.seed}) ===")
    print("\n--- acerto por checagem (% das pessoas)")
    for k, (ok, tot) in sorted(HIT.items()):
        if k.startswith("coleta"):
            continue
        pct = 100 * ok / tot if tot else 0
        flag = "  ←" if pct < 85 else ""
        print(f"{pct:6.1f}%  {k}{flag}")
    ok, tot = HIT["coleta: chamadas/dia (média)"]
    print(f"\ncoleta: {ok / tot:.1f} chamadas ao Garmin por dia (com backfill)")
    print("alertas de doença ligando/desligando em 3 semanas (por pessoa): "
          + ", ".join(f"{s} {flips_total[s] / 20:.1f} trocas · {alert_days[s] / 20:.1f} dias com alerta" for s in flips_total))
    if ISSUES:
        print("\n--- problemas")
        for k, n in ISSUES.most_common():
            print(f"{n:5d}  {k}")
            for ex in EX[k]:
                print("        ", str(ex)[:300])


if __name__ == "__main__":
    main()
