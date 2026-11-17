"""Saúde e sono pelo relógio: o que o treino está fazendo com o corpo.

Coleta (Garmin Connect, tudo opcional — cada relógio tem um conjunto de sensores):
  • sono da noite: nota, duração, fases (profundo/leve/REM/acordado), horário, HRV e respiração;
  • HRV noturna com a faixa normal da pessoa (baseline do Garmin);
  • FC de repouso, estresse médio, passos, minutos de intensidade, Body Battery;
  • de tempos em tempos: VO2máx, status de treino, idade física e peso.

Guarda um registro por dia na tabela kv (health:AAAA-MM-DD) e analisa:
  • a última noite (foi boa?) e a semana de sono (débito, regularidade);
  • recuperação: FC de repouso e HRV contra a linha de base da própria pessoa;
  • condicionamento: tendência do VO2máx e da FC de repouso ao longo das semanas;
  • estresse e atividade do dia a dia (meta da OMS de 150 min/semana);
  • efeitos do treino: noites depois de treino pesado × dias leves, treino tarde × cedo;
  • alertas: FC de repouso alta + HRV baixa (fadiga/início de doença), noites curtas seguidas.

Referências: sono 7–9 h (adultos), 7–8 h (65+) — National Sleep Foundation / AASM; fases típicas
profundo 13–23 %, REM 20–25 %; atividade 150–300 min/semana moderada (OMS 2020, vigorosa vale 2×);
FC de repouso ≥ 5 bpm acima da base com HRV abaixo da faixa = sinal clássico de recuperação incompleta.
"""
from __future__ import annotations

import json
import math
import statistics as st
from datetime import date, datetime, timedelta, timezone

from . import clock

KEEP_DAYS = 120
BACKFILL_PER_RUN = 3
WEEKLY_EVERY_DAYS = 3


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _hm(ms) -> str | None:
    """Timestamp 'local' do Garmin (ms desde 1970 no horário local) → HH:MM."""
    v = _num(ms)
    if not v:
        return None
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%H:%M")


# ------------------------------------------------------------------ coleta ---
def fetch_day(api, day: date) -> dict:
    """Um dia de dados do relógio. A noite de sono de `day` é a que termina na manhã de `day`."""
    d = day.isoformat()
    out: dict = {"date": d, "fetched_at": clock.now().isoformat(timespec="minutes")}
    try:
        sl = (api.get_sleep_data(d) or {})
        dto = sl.get("dailySleepDTO") or {}
        secs = _num(dto.get("sleepTimeSeconds"))
        if secs:
            sc = dto.get("sleepScores") or {}
            ov = sc.get("overall") or {}
            stages = {k: _num(dto.get(f"{k}SleepSeconds")) or 0 for k in ("deep", "light", "rem", "awake")}
            asleep = stages["deep"] + stages["light"] + stages["rem"] or secs
            out["sleep"] = {
                "score": _num(ov.get("value")), "quality": ov.get("qualifierKey"),
                "duration_h": round(secs / 3600, 2),
                "deep_pct": round(100 * stages["deep"] / asleep, 1) if asleep else None,
                "rem_pct": round(100 * stages["rem"] / asleep, 1) if asleep else None,
                "light_pct": round(100 * stages["light"] / asleep, 1) if asleep else None,
                "awake_min": round(stages["awake"] / 60),
                "awake_count": _num((sc.get("awakeCount") or {}).get("value")) or _num(dto.get("awakeCount")),
                "start": _hm(dto.get("sleepStartTimestampLocal")), "end": _hm(dto.get("sleepEndTimestampLocal")),
                "avg_hrv": _num(dto.get("avgSleepHRV")) or _num(sl.get("avgOvernightHrv")),
                "resp": _num(dto.get("averageRespirationValue") or dto.get("avgRespirationValue")),
                "spo2": _num(dto.get("avgSpO2") or dto.get("averageSpO2Value")),
                "sleep_stress": _num(dto.get("avgSleepStress")),
                "nap_h": round((_num(dto.get("napTimeSeconds")) or 0) / 3600, 2) or None,
            }
            out["sleep"] = {k: v for k, v in out["sleep"].items() if v is not None}
    except Exception:  # noqa: BLE001 - relógio sem o sensor ou dia sem dado
        pass
    try:
        h = ((api.get_hrv_data(d) or {}).get("hrvSummary")) or {}
        if h.get("lastNightAvg") or h.get("status"):
            b = h.get("baseline") or {}
            out["hrv"] = {k: v for k, v in {
                "last_night": _num(h.get("lastNightAvg")), "weekly": _num(h.get("weeklyAvg")), "status": h.get("status"),
                "low": _num(b.get("balancedLow")), "high": _num(b.get("balancedUpper"))}.items() if v is not None}
    except Exception:  # noqa: BLE001
        pass
    try:
        s = api.get_stats(d) or {}
        m = {"rhr": "restingHeartRate", "stress_avg": "averageStressLevel", "stress_max": "maxStressLevel",
             "steps": "totalSteps", "step_goal": "dailyStepGoal", "int_mod": "moderateIntensityMinutes",
             "int_vig": "vigorousIntensityMinutes", "bb_high": "bodyBatteryHighestValue", "bb_low": "bodyBatteryLowestValue",
             "bb_charged": "bodyBatteryChargedValue", "bb_drained": "bodyBatteryDrainedValue", "kcal_active": "activeKilocalories",
             "spo2_avg": "averageSpo2", "resp_awake": "avgWakingRespirationValue"}
        for k, src in m.items():
            v = _num(s.get(src))
            if v is not None and not (k.startswith("stress") and v < 0):  # -1/-2 = sem dado no Garmin
                out[k] = v
    except Exception:  # noqa: BLE001
        pass
    if out.get("rhr") is None:
        try:
            rhr = api.get_rhr_day(d) or {}
            vals = (((rhr.get("allMetrics") or {}).get("metricsMap") or {}).get("WELLNESS_RESTING_HEART_RATE") or [])
            if vals and _num(vals[0].get("value")):
                out["rhr"] = _num(vals[0]["value"])
        except Exception:  # noqa: BLE001
            pass
    return out


def fetch_weekly(api, today: date) -> dict:
    """Métricas que mudam devagar: VO2máx, status de treino, idade física, peso."""
    d = today.isoformat()
    out: dict = {"date": d}
    try:
        ts = api.get_training_status(d) or {}
        vo2 = (((ts.get("mostRecentVO2Max") or {}).get("generic")) or {})
        v = _num(vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"))
        if v:
            out["vo2max"] = v
        vo2c = (((ts.get("mostRecentVO2Max") or {}).get("cycling")) or {})
        vc = _num(vo2c.get("vo2MaxPreciseValue") or vo2c.get("vo2MaxValue"))
        if vc:
            out["vo2max_bike"] = vc
        latest = (((ts.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData")) or {})
        for dev in latest.values():
            if not isinstance(dev, dict):
                continue
            phrase = dev.get("trainingStatusFeedbackPhrase") or ""
            if phrase:
                out["training_status"] = phrase.split("_")[0].upper()
            acute = dev.get("acuteTrainingLoadDTO") or {}
            if acute.get("acwrPercent") is not None:
                out["acwr_pct"] = _num(acute.get("acwrPercent"))
                out["acwr_status"] = acute.get("acwrStatus")
            break
    except Exception:  # noqa: BLE001
        pass
    if "vo2max" not in out:
        try:
            mm = api.get_max_metrics(d) or []
            mm = mm[0] if isinstance(mm, list) and mm else mm
            v = _num(((mm or {}).get("generic") or {}).get("vo2MaxPreciseValue"))
            if v:
                out["vo2max"] = v
        except Exception:  # noqa: BLE001
            pass
    try:
        fa = api.get_fitnessage_data(d) or {}
        for k, src in (("fitness_age", "fitnessAge"), ("chron_age", "chronologicalAge"), ("achievable_age", "achievableFitnessAge")):
            v = _num(fa.get(src))
            if v:
                out[k] = round(v, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        bc = api.get_body_composition((today - timedelta(days=30)).isoformat(), d) or {}
        lst = bc.get("dateWeightList") or []
        if lst:
            last = lst[-1] if (lst[-1].get("weight") or 0) else lst[0]
            w = _num(last.get("weight"))
            if w:
                out["weight_kg"] = round(w / 1000 if w > 1000 else w, 1)
            bf = _num(last.get("bodyFat"))
            if bf:
                out["body_fat"] = bf
    except Exception:  # noqa: BLE001
        pass
    return out


def collect(api, today: date, kv_get, kv_set, backfill: int = BACKFILL_PER_RUN) -> dict:
    """Atualiza os dias que faltam (hoje, ontem e alguns para trás) e as métricas semanais."""
    now = clock.now()
    fetched = []
    todo = [today, today - timedelta(days=1)]
    missing = [today - timedelta(days=i) for i in range(2, 29) if not kv_get(f"health:{(today - timedelta(days=i)).isoformat()}")]
    todo += missing[:backfill]
    for d in todo:
        raw = kv_get(f"health:{d.isoformat()}")
        cur = json.loads(raw) if raw else None
        if cur and cur.get("final"):
            continue
        if cur and d == today:
            try:
                age = now - datetime.fromisoformat(cur["fetched_at"])
                # com o sono da noite já lido, só atualiza (passos, estresse) a cada 4 h; sem ele, a cada 45 min
                if age < (timedelta(hours=4) if cur.get("sleep") else timedelta(minutes=45)):
                    continue
            except (KeyError, ValueError):
                pass
        data = fetch_day(api, d)
        data["final"] = d < today  # o dia já acabou: os totais não mudam mais
        if cur and not data.get("sleep") and cur.get("sleep"):
            data["sleep"] = cur["sleep"]
        kv_set(f"health:{d.isoformat()}", json.dumps(data))
        fetched.append(d.isoformat())
    wk = json.loads(kv_get("health_weekly") or "{}")
    if not wk or wk.get("date", "") <= (today - timedelta(days=WEEKLY_EVERY_DAYS)).isoformat():
        w = fetch_weekly(api, today)
        hist = wk.get("history") or []
        if w.get("vo2max") or w.get("weight_kg") or w.get("fitness_age"):
            hist = [h for h in hist if h.get("date") != w["date"]] + [{k: w.get(k) for k in ("date", "vo2max", "weight_kg", "fitness_age")}]
        w["history"] = hist[-60:]
        kv_set("health_weekly", json.dumps(w))
    return {"fetched": fetched}


def load_days(kv_get, today: date, days: int = 56) -> dict[str, dict]:
    out = {}
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        raw = kv_get(f"health:{d}")
        if raw:
            try:
                out[d] = json.loads(raw)
            except ValueError:
                pass
    return out


# ----------------------------------------------------------------- análise ---
def sleep_need(age) -> tuple[float, float]:
    a = _num(age)
    if a is not None and a < 18:
        return 8.0, 10.0
    if a is not None and a >= 65:
        return 7.0, 8.0
    return 7.0, 9.0


def _f(v, d: int = 1, sign: bool = False) -> str:
    """Número em pt-BR (vírgula decimal, sem ',0')."""
    txt = f"{abs(v):.{d}f}".rstrip("0").rstrip(".") if d else f"{abs(v):.0f}"
    txt = txt.replace(".", ",")
    return ("+" if v >= 0 else "−") + txt if sign else ("−" if v < 0 else "") + txt


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def _bedtime_minutes(hhmm: str | None) -> float | None:
    """Horário de dormir em minutos relativos à meia-noite (23:30 → −30, 00:40 → 40)."""
    if not hhmm:
        return None
    h, m = map(int, hhmm.split(":"))
    v = h * 60 + m
    return v - 1440 if v >= 12 * 60 else v


def fmt_h(h: float | None) -> str:
    if h is None:
        return "—"
    tot = round(h * 60)
    return f"{tot // 60}h{tot % 60:02d}"


def night_verdict(sl: dict, need: tuple[float, float], avg_score: float | None = None, avg_dur: float | None = None) -> dict:
    """A noite foi boa? Nota do Garmin quando houver; senão duração e fases."""
    reasons, tips = [], []
    score = sl.get("score")
    dur = sl.get("duration_h")
    if score is not None:
        level = "bom" if score >= 80 else "regular" if score >= 60 else "ruim"
    elif dur is not None:
        level = "bom" if dur >= need[0] else "regular" if dur >= need[0] - 1 else "ruim"
    else:
        return {"level": "sem_dados", "reasons": [], "tips": []}
    if dur is not None:
        if dur < need[0] - 0.25:
            reasons.append(f"dormiu {fmt_h(dur)}, abaixo das {need[0]:g}–{need[1]:g} h recomendadas")
            tips.append("Tente deitar 30–45 min mais cedo hoje.")
        elif dur > need[1] + 1:
            reasons.append(f"dormiu {fmt_h(dur)}, bem acima do normal (pode ser recuperação ou sono de pouca qualidade)")
        else:
            reasons.append(f"duração boa ({fmt_h(dur)})")
    if sl.get("deep_pct") is not None:
        if sl["deep_pct"] < 10:
            reasons.append(f"pouco sono profundo ({sl['deep_pct']:.0f}%, o normal é 13–23%)")
            tips.append("Sono profundo cai com álcool, treino intenso perto da hora de dormir e quarto quente.")
        elif sl["deep_pct"] >= 13:
            reasons.append(f"sono profundo bom ({sl['deep_pct']:.0f}%)")
    if sl.get("rem_pct") is not None and sl["rem_pct"] < 15:
        reasons.append(f"pouco REM ({sl['rem_pct']:.0f}%, o normal é 20–25%)")
    if (sl.get("awake_min") or 0) >= 45:
        reasons.append(f"{sl['awake_min']} min acordado durante a noite")
    if avg_score is not None and score is not None and abs(score - avg_score) >= 8:
        reasons.append(f"nota {int(score)} — {'acima' if score > avg_score else 'abaixo'} da sua média ({avg_score:.0f})")
    elif avg_dur is not None and dur is not None and abs(dur - avg_dur) >= 0.75:
        reasons.append(f"{'mais' if dur > avg_dur else 'menos'} que sua média ({fmt_h(avg_dur)})")
    return {"level": level, "reasons": reasons, "tips": tips}


def _area(level: str, title: str, text: str, tip: str | None = None) -> dict:
    return {"level": level, "title": title, "text": text, "tip": tip}


def report(days: dict[str, dict], weekly: dict | None, daily_load: dict[date, float], profile: dict,
           today: date, session_times: dict[date, list[float]] | None = None) -> dict:
    """Painel de saúde: última noite, áreas (sono, recuperação, condicionamento, estresse, atividade),
    efeitos do treino e alertas. `daily_load` = carga (TSS) por dia; `session_times` = horas de início dos treinos."""
    weekly = weekly or {}
    need = sleep_need(profile.get("age"))
    D = lambda i: (today - timedelta(days=i)).isoformat()  # noqa: E731
    get = lambda i: days.get(D(i)) or {}  # noqa: E731
    sleep = lambda i: get(i).get("sleep") or {}  # noqa: E731
    out: dict = {"has_data": bool(days), "need": need, "areas": [], "alerts": [], "effects": [], "last_night": None}
    if not days:
        return out

    # ---- última noite (a de hoje; se ainda não chegou, a de ontem)
    idx = 0 if sleep(0) else 1 if sleep(1) else None
    s28 = [sleep(i) for i in range(1, 29) if sleep(i)]
    avg_score = _mean([x.get("score") for x in s28])
    avg_dur = _mean([x.get("duration_h") for x in s28])
    if idx is not None:
        sl = sleep(idx)
        out["last_night"] = {"date": D(idx), **sl, **night_verdict(sl, need, avg_score, avg_dur),
                             "hrv": (get(idx).get("hrv") or {}).get("last_night"), "rhr": get(idx).get("rhr")}

    # ---- sono da semana
    s7 = [sleep(i) for i in range(0, 7) if sleep(i)]
    if s7:
        d7 = _mean([x.get("duration_h") for x in s7])
        sc7 = _mean([x.get("score") for x in s7])
        short = sum(1 for x in s7 if (x.get("duration_h") or 99) < need[0] - 0.5)
        debt = sum(max(0.0, need[0] - (x.get("duration_h") or need[0])) for x in s7)
        beds = [_bedtime_minutes(sleep(i).get("start")) for i in range(0, 14) if sleep(i).get("start")]
        irregular = st.pstdev(beds) if len(beds) >= 5 else None
        lvl = "ok"
        parts = [f"média {fmt_h(d7)} por noite" + (f", nota {sc7:.0f}" if sc7 is not None else "")]
        tip = None
        if debt >= 3 or short >= 3:
            lvl = "alerta" if debt >= 5 else "atencao"
            parts.append(f"{short} noite{'s' if short != 1 else ''} curta{'s' if short != 1 else ''} — débito de ~{fmt_h(debt)} na semana")
            tip = "Sono curto atrapalha a recuperação muscular e a fome; priorize 7 h+ antes de aumentar o treino."
        if irregular is not None:
            if irregular > 75:
                lvl = "atencao" if lvl == "ok" else lvl
                parts.append(f"horário de dormir irregular (varia ±{irregular:.0f} min)")
                tip = tip or "Deitar e acordar no mesmo horário (inclusive no fim de semana) melhora a qualidade do sono."
            else:
                parts.append(f"horário regular (±{irregular:.0f} min)")
        if sc7 is not None and sc7 < 60 and lvl == "ok":
            lvl = "atencao"
        out["areas"].append(_area(lvl, "Sono", "; ".join(parts) + ".", tip))
        if short >= 3 and sum(1 for i in range(0, 5) if (sleep(i).get("duration_h") or 99) < 6) >= 3:
            out["alerts"].append("3 das últimas 5 noites com menos de 6 h de sono: reduza a intensidade até recuperar o sono.")

    # ---- recuperação: FC de repouso e HRV contra a base da própria pessoa
    rhr_recent = _mean([get(i).get("rhr") for i in range(0, 3)])
    rhr_base = _median([get(i).get("rhr") for i in range(7, 36)])
    hrv7 = _mean([(get(i).get("hrv") or {}).get("last_night") for i in range(0, 7)])
    hrv_base = [(get(i).get("hrv") or {}).get("last_night") for i in range(7, 36)]
    hrv_base = [x for x in hrv_base if x]
    lo = (get(0).get("hrv") or get(1).get("hrv") or {}).get("low")
    hi = (get(0).get("hrv") or get(1).get("hrv") or {}).get("high")
    if (lo is None or hi is None) and len(hrv_base) >= 10:
        m, sd = st.mean(hrv_base), st.pstdev(hrv_base)
        lo, hi = round(m - sd, 1), round(m + sd, 1)
    rhr_delta = round(rhr_recent - rhr_base, 1) if rhr_recent is not None and rhr_base is not None else None
    out["rhr"] = {"recent": rhr_recent and round(rhr_recent, 1), "base": rhr_base, "delta": rhr_delta}
    out["hrv"] = {"recent": hrv7 and round(hrv7, 1), "low": lo, "high": hi,
                  "status": (get(0).get("hrv") or get(1).get("hrv") or {}).get("status")}
    if rhr_recent is not None or hrv7 is not None:
        parts, lvl, tip = [], "ok", None
        hrv_low = hrv7 is not None and lo is not None and hrv7 < lo
        hrv_high = hrv7 is not None and hi is not None and hrv7 > hi
        if rhr_delta is not None:
            parts.append(f"FC de repouso {rhr_recent:.0f} bpm ({_f(rhr_delta, 1, True)} da sua base de {rhr_base:.0f})")
            if rhr_delta >= 5:
                lvl = "atencao"
        elif rhr_recent is not None:
            parts.append(f"FC de repouso {rhr_recent:.0f} bpm")
        if hrv7 is not None:
            faixa = f" (sua faixa: {lo:.0f}–{hi:.0f})" if lo is not None and hi is not None else ""
            parts.append(f"HRV média da semana {hrv7:.0f} ms{faixa}")
            if hrv_low:
                lvl = "atencao"
        if rhr_delta is not None and rhr_delta >= 5 and hrv_low:
            lvl = "alerta"
            tip = "FC de repouso alta + HRV baixa: corpo ainda se recuperando (ou começo de gripe). Faça 1–2 dias leves."
            out["alerts"].append(f"FC de repouso {_f(rhr_delta)} bpm acima do normal e HRV abaixo da sua faixa: "
                                 "sinal de recuperação incompleta, estresse ou início de doença. Dia leve ou folga.")
        elif lvl == "ok":
            tip = "Recuperação em dia: dá para manter o plano." if not hrv_high else "HRV acima da faixa: ótima recuperação."
        out["areas"].append(_area(lvl, "Recuperação", "; ".join(parts) + ".", tip))

    # ---- condicionamento: VO2máx e FC de repouso ao longo das semanas
    hist = [h for h in (weekly.get("history") or []) if h.get("vo2max")]
    parts, lvl, tip = [], "ok", None
    if weekly.get("vo2max"):
        parts.append(f"VO2máx {_f(weekly['vo2max'])}")
        old = [h for h in hist if h["date"] <= (today - timedelta(days=28)).isoformat()]
        if old:
            dv = round(weekly["vo2max"] - old[-1]["vo2max"], 1)
            parts.append(f"{_f(dv, 1, True)} em 4+ semanas")
            if dv <= -1.5:
                lvl, tip = "atencao", "VO2máx caindo: falta estímulo aeróbico de qualidade ou sobrou fadiga."
            elif dv >= 1:
                tip = "O treino aeróbico está funcionando."
    if weekly.get("fitness_age") and weekly.get("chron_age"):
        diff = round(weekly["chron_age"] - weekly["fitness_age"], 1)
        parts.append(f"idade física {_f(weekly['fitness_age'])} ({_f(-diff, 1, True)} anos da idade real)")
    rhr_old = _mean([get(i).get("rhr") for i in range(28, 56)])
    rhr_new = _mean([get(i).get("rhr") for i in range(0, 14)])
    if rhr_old is not None and rhr_new is not None:
        dr = round(rhr_new - rhr_old, 1)
        parts.append("FC de repouso estável em 1 mês" if abs(dr) < 1 else f"FC de repouso {_f(dr, 1, True)} bpm em 1 mês")
        if dr <= -2:
            tip = tip or "FC de repouso caindo ao longo das semanas: coração mais eficiente."
    if weekly.get("training_status"):
        parts.append("status do Garmin: " + STATUS_PT.get(weekly["training_status"], weekly["training_status"].lower()))
        if weekly["training_status"] in ("OVERREACHING", "STRAINED", "UNPRODUCTIVE"):
            lvl = "atencao"
    if parts:
        out["areas"].append(_area(lvl, "Condicionamento", "; ".join(parts) + ".", tip))

    # ---- estresse
    s7v = [get(i).get("stress_avg") for i in range(0, 7)]
    s28v = [get(i).get("stress_avg") for i in range(7, 35)]
    sa, sb = _mean(s7v), _mean(s28v)
    if sa is not None:
        lvl = "ok" if sa < 35 else "atencao" if sa < 50 else "alerta"
        txt = f"estresse médio {sa:.0f}/100" + (f" (sua média: {sb:.0f})" if sb is not None else "")
        tip = None
        if lvl != "ok" or (sb is not None and sa > sb + 8):
            lvl = "atencao" if lvl == "ok" else lvl
            tip = "Estresse alto rouba recuperação como um treino a mais: em semanas assim, mantenha os leves leves."
        out["areas"].append(_area(lvl, "Estresse", txt + ".", tip))

    # ---- atividade do dia a dia (meta OMS)
    steps = _mean([get(i).get("steps") for i in range(0, 7)])
    im = [(get(i).get("int_mod") or 0) + 2 * (get(i).get("int_vig") or 0) for i in range(0, 7) if get(i)]
    if steps is not None or im:
        week_im = sum(im)
        lvl = "ok" if week_im >= 150 else "atencao"
        parts = []
        if steps is not None:
            parts.append(f"{steps:,.0f} passos/dia".replace(",", "."))
        parts.append(f"{week_im:.0f} min de intensidade na semana (meta da OMS: 150–300; vigorosa vale 2×)")
        out["areas"].append(_area(lvl, "Atividade diária", "; ".join(parts) + ".",
                                  None if lvl == "ok" else "Caminhadas de 10 min depois das refeições já ajudam a bater a meta."))

    # ---- efeitos do treino: noite seguinte a dias pesados × dias leves
    nights = []
    for i in range(0, 56):
        d = today - timedelta(days=i)
        sl = (days.get(d.isoformat()) or {}).get("sleep") or {}
        if not sl:
            continue
        prev = d - timedelta(days=1)
        nights.append({"load": daily_load.get(prev, 0.0), "score": sl.get("score"), "dur": sl.get("duration_h"),
                       "hrv": ((days.get(d.isoformat()) or {}).get("hrv") or {}).get("last_night"),
                       "rhr": (days.get(d.isoformat()) or {}).get("rhr"), "deep": sl.get("deep_pct"),
                       "late": any(h >= 19.5 for h in (session_times or {}).get(prev, [])),
                       "trained": bool((session_times or {}).get(prev))})
    loads = sorted(n["load"] for n in nights if n["load"] > 0)
    if len(nights) >= 10 and loads:
        thr = max(60.0, loads[int(len(loads) * 0.75)] if len(loads) >= 4 else 60.0)
        heavy = [n for n in nights if n["load"] >= thr]
        easy = [n for n in nights if n["load"] < 15]

        def cmp(key, label, unit, better_high=True, min_diff=3.0):
            a, b = _mean([n[key] for n in heavy]), _mean([n[key] for n in easy])
            na = sum(1 for n in heavy if n[key] is not None)
            nb = sum(1 for n in easy if n[key] is not None)
            if a is None or b is None or na < 3 or nb < 3:
                return
            diff = a - b
            if abs(diff) < min_diff:
                out["effects"].append(f"{label}: praticamente igual depois de treino pesado e de dia leve "
                                      f"({a:.0f}{unit} × {b:.0f}{unit}) — você está absorvendo bem a carga.")
                return
            worse = diff < 0 if better_high else diff > 0
            out["effects"].append(f"{label}: {a:.0f}{unit} nas noites depois de treino pesado × {b:.0f}{unit} depois de dia leve"
                                  + (" — o treino forte está pesando na recuperação." if worse else "."))
        cmp("score", "Nota do sono", "", True, 4)
        cmp("hrv", "HRV", " ms", True, 4)
        cmp("rhr", "FC de repouso", " bpm", False, 2)
        late = [n for n in nights if n["late"]]
        early = [n for n in nights if n["trained"] and not n["late"]]
        a, b = _mean([n["score"] for n in late]), _mean([n["score"] for n in early])
        if a is not None and b is not None and len(late) >= 3 and len(early) >= 3:
            if b - a >= 5:
                out["effects"].append(f"Treinar à noite (depois das 19h30) derrubou seu sono: nota {a:.0f} × {b:.0f} quando treina mais cedo. "
                                      "Se der, deixe os treinos fortes para mais cedo ou termine 3 h antes de deitar.")
            else:
                out["effects"].append(f"Treinar à noite não está atrapalhando seu sono (nota {a:.0f} × {b:.0f}).")
    elif nights:
        out["effects"].append("Com mais 1–2 semanas de dados do relógio o app mostra como cada tipo de treino afeta seu sono e sua recuperação.")

    # ---- séries para gráficos (28 dias, do mais antigo ao mais novo)
    out["series"] = [{"date": D(i), "sleep_h": sleep(i).get("duration_h"), "sleep_score": sleep(i).get("score"),
                      "rhr": get(i).get("rhr"), "hrv": (get(i).get("hrv") or {}).get("last_night"),
                      "stress": get(i).get("stress_avg"), "steps": get(i).get("steps"), "load": daily_load.get(today - timedelta(days=i), 0)}
                     for i in range(27, -1, -1)]
    out["weekly"] = {k: weekly.get(k) for k in ("vo2max", "vo2max_bike", "fitness_age", "chron_age", "achievable_age", "weight_kg",
                                                 "body_fat", "training_status", "acwr_pct") if weekly.get(k) is not None}
    rank = {"alerta": 0, "atencao": 1, "ok": 2}
    out["overall"] = min((a["level"] for a in out["areas"]), key=lambda x: rank[x], default="ok")
    return out


STATUS_PT = {"PRODUCTIVE": "produtivo", "MAINTAINING": "mantendo", "RECOVERY": "recuperação", "PEAKING": "no pico",
             "OVERREACHING": "sobrecarga", "UNPRODUCTIVE": "improdutivo", "STRAINED": "esgotado", "DETRAINING": "perdendo forma",
             "NO": "sem status"}
