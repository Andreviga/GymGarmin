"""Carga de treino combinada: todas as modalidades + musculação na mesma régua.

Cada sessão vira um "estresse" no estilo TSS (100 = 1 h no limiar):
  IF (intensidade relativa à âncora do esporte) → TSS = horas × IF² × 100
  (natação usa IF³, como o sTSS, porque o arrasto cresce mais rápido).
Ordem de preferência por item: TSS do próprio Garmin → potência/FTP (bike) →
ritmo/âncora (corrida, natação, remo) → FC média/limiar → zona planejada.
Musculação: tempo estimado × RPE (sRPE convertido para a mesma escala).

Com o estresse diário calcula o gráfico de desempenho:
  condicionamento (CTL, média de 42 dias), fadiga (ATL, 7 dias) e forma (TSB = CTL − ATL).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from . import catalog, clock
from .sports import (SPORTS, anchors_of, pace_at, row_split, speed_mps)

ZONE_IF = {1: 0.55, 2: 0.68, 3: 0.80, 4: 0.92, 5: 1.03, 6: 1.12}
CTL_DAYS, ATL_DAYS = 42, 7

# máquinas de academia que também contam como volume da modalidade
MACHINE_SPORT = {"cardio_esteira_corrida": "corrida", "cardio_esteira_caminhada": "caminhada", "cardio_remo": "remo",
                 "cardio_spinning": "ciclismo", "cardio_bike_vertical": "ciclismo", "cardio_bike_horizontal": "ciclismo",
                 "cardio_air_bike": "ciclismo"}
EX_SPORT = {**MACHINE_SPORT, **{env[0]: s for s, d in SPORTS.items() for env in d["envs"].values()}}


def sport_of(item: dict) -> str | None:
    return item.get("sport") or EX_SPORT.get(item.get("exercise_id"))


def _tss(hours: float, intensity: float, power: float = 2.0) -> float:
    return round(max(0.0, hours) * max(0.0, min(intensity, 1.5)) ** power * 100, 1)


def _hr_if(avg_hr: float, anchors: dict) -> float | None:
    lthr = anchors.get("lthr") or (anchors.get("hrmax") and anchors["hrmax"] * 0.9)
    if not avg_hr or not lthr:
        return None
    return avg_hr / lthr


def endurance_stress(item: dict, anchors: dict) -> dict:
    """Estresse de um item de cardio/modalidade já feito (ou planejado)."""
    sport = sport_of(item)
    minutes = float(item.get("duration_min") or 0)
    if not minutes and item.get("intervals"):
        iv = item["intervals"]
        minutes = (int(iv.get("repeats") or 0) * (int(iv.get("work_s") or 0) + int(iv.get("rest_s") or 0)) / 60
                   + float(item.get("warmup_min") or 0) + float(item.get("cooldown_min") or 0))
    hours = minutes / 60
    dist_m = float(item.get("distance_km") or 0) * 1000
    out = {"sport": sport, "minutes": round(minutes, 1), "km": round(dist_m / 1000, 2)}
    if item.get("tss"):
        return {**out, "tss": round(float(item["tss"]), 1), "method": "garmin"}
    if not hours:
        return {**out, "tss": 0.0, "method": "sem_tempo"}
    speed = dist_m / (minutes * 60) if dist_m and minutes else None
    if sport == "ciclismo" and anchors.get("ftp_w") and (item.get("np") or item.get("avg_power")):
        return {**out, "tss": _tss(hours, float(item.get("np") or item["avg_power"]) / anchors["ftp_w"]), "method": "potencia"}
    env_trail = item.get("exercise_id") in ("corrida_trilha", "trilha_hiking", "ciclismo_mtb")
    if speed and not env_trail:
        if sport == "corrida" and anchors.get("run_vdot"):
            thr = 1000 / pace_at(anchors["run_vdot"], 0.88)
            return {**out, "tss": _tss(hours, speed / thr), "method": "ritmo"}
        if sport == "natacao" and anchors.get("css_s100"):
            return {**out, "tss": _tss(hours, speed / (100 / anchors["css_s100"]), 3), "method": "ritmo"}
        if sport == "remo" and anchors.get("row_2k_s"):
            thr = 500 / (row_split(anchors["row_2k_s"]) + 5)
            return {**out, "tss": _tss(hours, speed / thr), "method": "ritmo"}
    hr_if = _hr_if(item.get("avg_hr"), anchors)
    if hr_if:
        return {**out, "tss": _tss(hours, hr_if), "method": "fc"}
    zone = int(item.get("zone") or (item.get("intervals") or {}).get("work_zone") or 2)
    if item.get("intervals"):
        iv = item["intervals"]
        work_h = int(iv.get("repeats") or 0) * int(iv.get("work_s") or 0) / 3600
        easy_h = max(0.0, hours - work_h)
        return {**out, "tss": round(_tss(work_h, ZONE_IF[min(6, zone)]) + _tss(easy_h, ZONE_IF[1]), 1), "method": "zona"}
    return {**out, "tss": _tss(hours, ZONE_IF[max(1, min(6, zone))]), "method": "zona"}


def strength_stress(items: list[dict], minutes: float | None = None) -> float:
    """sRPE → escala TSS. 1 h de musculação a RPE 8 ≈ 40–45."""
    sets, rpes = 0, []
    for it in items:
        for s in it.get("sets") or []:
            if not s.get("done", True):
                continue
            sets += 1
            if s.get("rpe") not in (None, ""):
                rpes.append(float(s["rpe"]))
    if not sets:
        return 0.0
    rpe = sum(rpes) / len(rpes) if rpes else 7.5
    mins = minutes or sets * 2.6  # ~2,6 min por série (execução + descanso), quando não há tempo medido
    return _tss(mins / 60, 0.55 + 0.03 * (rpe - 5))


def session_stress(session: dict, anchors: dict) -> dict:
    by_sport: dict[str, float] = defaultdict(float)
    km: dict[str, float] = defaultdict(float)
    minutes: dict[str, float] = defaultdict(float)
    strength_items = []
    total = 0.0
    for it in session.get("items") or []:
        ex = catalog.get(it.get("exercise_id")) or {}
        if ex.get("kind") == "cardio" or it.get("sport"):
            st = endurance_stress(it, anchors)
            key = st["sport"] or "cardio"
            by_sport[key] += st["tss"]
            km[key] += st["km"]
            minutes[key] += st["minutes"]
            total += st["tss"]
        elif it.get("sets"):
            strength_items.append(it)
        elif it.get("strength_min"):  # musculação do relógio sem séries detectadas: sRPE pelo tempo (RPE ~7,5)
            s = _tss(float(it["strength_min"]) / 60, 0.55 + 0.03 * 2.5)
            by_sport["musculacao"] += s
            total += s
    if strength_items:
        s = strength_stress(strength_items, session.get("duration_min"))
        by_sport["musculacao"] += s
        total += s
    return {"tss": round(total, 1), "by_sport": {k: round(v, 1) for k, v in by_sport.items()},
            "km": {k: round(v, 2) for k, v in km.items() if v}, "minutes": {k: round(v) for k, v in minutes.items() if v}}


def _day(v) -> date:
    return datetime.fromisoformat(str(v)[:19]).date() if not isinstance(v, date) else v


def daily_stress(sessions: list[dict], anchors: dict) -> dict[date, dict]:
    out: dict[date, dict] = {}
    for s in sessions:
        d = _day(s.get("date"))
        st = session_stress(s, anchors)
        cur = out.setdefault(d, {"tss": 0.0, "by_sport": defaultdict(float), "km": defaultdict(float),
                                 "minutes": defaultdict(float)})
        cur["tss"] += st["tss"]
        for k in ("by_sport", "km", "minutes"):
            for sp, v in st[k].items():
                cur[k][sp] += v
    return out


def pmc(sessions: list[dict], profile: dict, today: date | None = None, days: int = 90) -> dict:
    """Condicionamento (CTL), fadiga (ATL) e forma (TSB) com a série dos últimos `days` dias."""
    today = today or clock.today()
    anchors = anchors_of(profile)
    daily = daily_stress(sessions, anchors)
    if daily:
        start = min(min(daily), today - timedelta(days=days))
    else:
        start = today - timedelta(days=days)
    ctl = atl = 0.0
    seeded = False
    first = min(daily) if daily else None
    hist_days = (today - first).days + 1 if first else 0
    # O app só conhece o treino a partir do primeiro registro. Começar o condicionamento do zero
    # faz quem já treinava parecer em "risco de overtraining" nas primeiras semanas (a fadiga sobe em
    # 7 dias, o condicionamento leva 42). Quem não é iniciante já vinha treinando: parte da média
    # das 2 primeiras semanas registradas.
    if first and hist_days < CTL_DAYS and not _beginner(profile):
        span = [daily.get(first + timedelta(days=i), {}).get("tss", 0.0) for i in range(min(14, hist_days))]
        if len(span) >= 7:
            ctl = atl = sum(span) / len(span)
            seeded = True
    series = []
    d = start
    while d <= today:
        tss = daily.get(d, {}).get("tss", 0.0)
        tsb = ctl - atl  # forma do dia = antes do treino do dia
        ctl += (tss - ctl) / CTL_DAYS
        atl += (tss - atl) / ATL_DAYS
        if d > today - timedelta(days=days):
            series.append({"date": d.isoformat(), "tss": round(tss, 1), "ctl": round(ctl, 1), "atl": round(atl, 1),
                           "tsb": round(tsb, 1)})
        d += timedelta(days=1)
    cur = series[-1] if series else {"ctl": 0, "atl": 0, "tsb": 0}
    week_ago = series[-8] if len(series) >= 8 else (series[0] if series else cur)
    ramp = round(cur["ctl"] - week_ago["ctl"], 1)
    form = round(cur["ctl"] - cur["atl"], 1)
    status, msg = form_status(form, ramp, cur["ctl"])
    if seeded:
        msg += (f" (Só {hist_days} dias de treinos no app: o condicionamento foi estimado pela média do início; "
                "fica mais preciso com o tempo.)")
    week = [v for k, v in daily.items() if k > today - timedelta(days=7)]
    by_sport_week: dict[str, dict] = defaultdict(lambda: {"tss": 0.0, "km": 0.0, "minutes": 0.0})
    for v in week:
        for sp, t in v["by_sport"].items():
            by_sport_week[sp]["tss"] += t
        for sp, k in v["km"].items():
            by_sport_week[sp]["km"] += k
        for sp, m in v["minutes"].items():
            by_sport_week[sp]["minutes"] += m
    return {
        "ctl": cur["ctl"], "atl": cur["atl"], "form": form, "ramp_week": ramp,
        "status": status, "message": msg, "series": series, "history_days": hist_days, "seeded": seeded,
        "week": {"tss": round(sum(v["tss"] for v in week), 1),
                 "by_sport": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in by_sport_week.items()}},
    }


def _beginner(profile: dict) -> bool:
    tm = (profile or {}).get("training_months")
    if tm not in (None, ""):
        try:
            return float(tm) < 3
        except (TypeError, ValueError):
            pass
    return (profile or {}).get("level") == "iniciante"


def form_status(form: float, ramp: float, ctl: float) -> tuple[str, str]:
    if ctl < 5:
        return "inicio", "Pouca carga registrada ainda: o gráfico ganha sentido depois de 2–3 semanas de treinos."
    if form < -30:
        return "risco", ("Fadiga bem acima do condicionamento: risco de overtraining. Faça 2–3 dias leves ou de descanso "
                         "antes do próximo treino forte.")
    if ramp > 8:
        return "subida_rapida", (f"A carga subiu {ramp:g} pontos em 7 dias (ideal até ~5–7). Segure o aumento na "
                                 "próxima semana.")
    if form < -10:
        return "produtivo", "Fadiga controlada: é a faixa em que o condicionamento sobe."
    if form <= 15:
        return "equilibrado", "Equilibrado: bom momento para um treino de qualidade ou um teste."
    return "descansado", "Bem descansado. Ótimo para prova/teste; se durar muito, o condicionamento começa a cair."


def planned_stress(item: dict, anchors: dict) -> float:
    """Estresse previsto de um item planejado (para a carga da semana que vem)."""
    end = item.get("endurance")
    if end:
        return round(sum(_tss(st["dur_s"] / 3600, ZONE_IF[max(1, min(6, st.get("zone") or 2))],
                              3 if end.get("sport") == "natacao" else 2) for st in flat_steps(end)), 1)
    ex = catalog.get(item.get("exercise_id")) or {}
    if ex.get("kind") == "cardio":
        return endurance_stress(item, anchors)["tss"]
    return 0.0


def flat_steps(end: dict) -> list[dict]:
    """Passos de uma sessão de resistência com repetições expandidas e duração estimada."""
    out = []
    for st in end.get("steps") or []:
        if st.get("kind") == "repeat":
            for _ in range(int(st.get("n") or 1)):
                out += [dict(x) for x in st.get("steps") or []]
        else:
            out.append(dict(st))
    return out


__all__ = ["pmc", "session_stress", "endurance_stress", "strength_stress", "planned_stress", "flat_steps",
           "sport_of", "speed_mps", "ZONE_IF"]
