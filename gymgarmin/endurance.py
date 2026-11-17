"""Planejador de resistência: corrida, ciclismo, natação, remo, caminhada, trilha e eventos multiesporte.

Configuração em profile["endurance"]:
    sports: {corrida: {enabled, env, sessions, long_day, current_weekly,
                       goal: {type: "volume", weekly} | {type: "prova", distance, target, date}}, ...}
    event:  {type: "tri_olimpico" | ..., date, target}      (opcional; define metas e brick)
    anchors: {run_vdot, ftp_w, css_s100, row_2k_s, lthr, hrmax, ...}
    tests: bool  (agenda testes quando a âncora falta ou tem mais de 8 semanas)

Princípios aplicados:
- progressão de volume de até ~8%/semana, semana mais leve a cada 4ª, teto na meta;
- prova/evento: base → construção → pico → polimento → prova → recuperação;
- distribuição polarizada (~80% leve): 1 sessão de qualidade por modalidade
  (2 para avançados com 5+ sessões), no máximo 3 sessões fortes na semana;
- longão no fim de semana; qualidade longe do longão e de perna pesada.

O plano guarda "slots" (dia da semana + modalidade + papel). As sessões de cada
semana são montadas na hora (volume e tipo mudam semana a semana, e as zonas
usam as âncoras atuais — recalibrou, o próximo treino já sai com os ritmos novos).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import clock, sports as sp

WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

# unidade de volume, volume inicial por nível, mínimo por sessão, sessões padrão
VOLUME = {
    "corrida": {"unit": "km", "start": {"iniciante": 8, "intermediario": 15, "avancado": 25, "elite": 40}, "min": 2.5, "max": 35, "sessions": 3},
    "ciclismo": {"unit": "h", "start": {"iniciante": 1.5, "intermediario": 3, "avancado": 5, "elite": 8}, "min": 0.5, "max": 6, "sessions": 2},
    "natacao": {"unit": "m", "start": {"iniciante": 1500, "intermediario": 3000, "avancado": 5000, "elite": 8000}, "min": 1000, "max": 6000, "sessions": 2},
    "remo": {"unit": "m", "start": {"iniciante": 8000, "intermediario": 15000, "avancado": 25000, "elite": 40000}, "min": 4000, "max": 20000, "sessions": 2},
    "caminhada": {"unit": "km", "start": {"iniciante": 10, "intermediario": 15, "avancado": 20, "elite": 25}, "min": 2, "max": 25, "sessions": 3},
    "trilha": {"unit": "h", "start": {"iniciante": 2, "intermediario": 3, "avancado": 4, "elite": 5}, "min": 1, "max": 8, "sessions": 1},
}
UNIT_PT = {"km": "km", "h": "h", "m": "m"}
MAX_SESSION_MIN = {"corrida": 210, "ciclismo": 360, "natacao": 110, "remo": 110, "caminhada": 300, "trilha": 480}
MIN_SESSION_MIN = {"corrida": 20, "ciclismo": 30, "natacao": 15, "remo": 15, "caminhada": 20, "trilha": 60}


def session_max(sport: str, anchors: dict, env: str | None = None) -> float:
    """Maior sessão possível: teto da modalidade ou o tempo máximo no ritmo leve do atleta."""
    unit = VOLUME[sport]["unit"]
    secs = MAX_SESSION_MIN[sport] * 60
    by_time = secs / 3600 if unit == "h" else secs * sp.speed_mps(sport, 2, anchors, env) / (1000 if unit == "km" else 1)
    return min(VOLUME[sport]["max"], by_time)


def min_session(sport: str, anchors: dict, env: str | None = None) -> float:
    """Volume mínimo de uma sessão: o maior entre o mínimo da modalidade e ~20 min (corredor rápido não faz 11 min)."""
    unit = VOLUME[sport]["unit"]
    secs = MIN_SESSION_MIN[sport] * 60
    by_time = secs / 3600 if unit == "h" else secs * sp.speed_mps(sport, 2, anchors, env) / (1000 if unit == "km" else 1)
    return max(VOLUME[sport]["min"], by_time)

LEVEL_FACTOR = {"iniciante": 0.6, "intermediario": 0.85, "avancado": 1.0, "elite": 1.2}
SESSIONS_FACTOR = {1: 0.45, 2: 0.65, 3: 0.82, 4: 1.0, 5: 1.12, 6: 1.22, 7: 1.3}

# pico semanal (intermediário/avançado) para provas de uma modalidade
RACE_PEAK = {
    "corrida": [(5, 30), (10, 40), (21.1, 50), (42.2, 65)],        # distância km → km/semana
    "ciclismo": [(20, 4), (40, 5.5), (90, 8), (180, 11)],           # km → h/semana
    "natacao": [(750, 6000), (1500, 8000), (3000, 10000), (3800, 12000)],  # m → m/semana
    "remo": [(2000, 30000), (5000, 35000), (21097, 50000)],
    "caminhada": [(10, 25), (21.1, 35), (42.2, 45)],
    "trilha": [(15, 5), (30, 7), (60, 9)],
}
LONG_CAP = {"corrida": [(5, 12), (10, 16), (21.1, 22), (42.2, 32)], "ciclismo": [(20, 2.0), (40, 2.75), (90, 4.0), (180, 5.5)],
            "natacao": [(750, 2000), (1500, 3000), (3000, 4000), (3800, 4500)], "remo": [(2000, 12000), (21097, 21000)],
            "caminhada": [(10, 12), (42.2, 30)], "trilha": [(15, 4), (60, 8)]}
TAPER_WEEKS = {"corrida": [(5, 1), (10, 1), (21.1, 2), (42.2, 3)], "ciclismo": [(40, 1), (180, 2)],
               "natacao": [(1500, 1), (3800, 2)], "remo": [(5000, 1), (21097, 2)], "caminhada": [(42.2, 1)],
               "trilha": [(60, 1)]}

EVENTS = {
    "tri_supersprint": {"name": "Triathlon super sprint", "legs": [("natacao", 400), ("ciclismo", 10), ("corrida", 2.5)],
                        "peak": {"natacao": 3500, "ciclismo": 2.5, "corrida": 15}, "fatigue": 1.03, "taper": 1},
    "tri_sprint": {"name": "Triathlon sprint", "legs": [("natacao", 750), ("ciclismo", 20), ("corrida", 5)],
                   "peak": {"natacao": 4500, "ciclismo": 3.5, "corrida": 18}, "fatigue": 1.04, "taper": 1},
    "tri_olimpico": {"name": "Triathlon olímpico", "legs": [("natacao", 1500), ("ciclismo", 40), ("corrida", 10)],
                     "peak": {"natacao": 6500, "ciclismo": 5.0, "corrida": 25}, "fatigue": 1.06, "taper": 1},
    "tri_meio": {"name": "Meio Ironman (70.3)", "legs": [("natacao", 1900), ("ciclismo", 90), ("corrida", 21.1)],
                 "peak": {"natacao": 8000, "ciclismo": 7.5, "corrida": 35}, "fatigue": 1.10, "taper": 2},
    "tri_ironman": {"name": "Ironman", "legs": [("natacao", 3800), ("ciclismo", 180), ("corrida", 42.2)],
                    "peak": {"natacao": 10000, "ciclismo": 11.0, "corrida": 45}, "fatigue": 1.15, "taper": 3},
    "duathlon_sprint": {"name": "Duathlon sprint", "legs": [("corrida", 5), ("ciclismo", 20), ("corrida", 2.5)],
                        "peak": {"ciclismo": 3.0, "corrida": 22}, "fatigue": 1.05, "taper": 1},
    "duathlon_padrao": {"name": "Duathlon padrão", "legs": [("corrida", 10), ("ciclismo", 40), ("corrida", 5)],
                        "peak": {"ciclismo": 4.5, "corrida": 30}, "fatigue": 1.07, "taper": 1},
    "aquathlon": {"name": "Aquathlon", "legs": [("natacao", 1000), ("corrida", 5)],
                  "peak": {"natacao": 5000, "corrida": 20}, "fatigue": 1.04, "taper": 1},
}
EVENT_SESSIONS = {"tri_supersprint": {"natacao": 2, "ciclismo": 2, "corrida": 2},
                  "tri_sprint": {"natacao": 2, "ciclismo": 2, "corrida": 3},
                  "tri_olimpico": {"natacao": 2, "ciclismo": 2, "corrida": 3},
                  "tri_meio": {"natacao": 3, "ciclismo": 3, "corrida": 3},
                  "tri_ironman": {"natacao": 3, "ciclismo": 3, "corrida": 3},
                  "duathlon_sprint": {"ciclismo": 2, "corrida": 3}, "duathlon_padrao": {"ciclismo": 2, "corrida": 3},
                  "aquathlon": {"natacao": 2, "corrida": 3}}

QUALITY_ROTATION = {"corrida": ["intervalado", "ritmo"], "ciclismo": ["sweet_spot", "limiar", "vo2"],
                    "natacao": ["css", "velocidade"], "remo": ["intervalos_1000", "intervalos_500"],
                    "caminhada": [], "trilha": []}
QUALITY_SHARE = {"corrida": 0.18, "ciclismo": 0.22, "natacao": 0.32, "remo": 0.25}
LONG_SHARE = {1: 1.0, 2: 0.55, 3: 0.4, 4: 0.33, 5: 0.28, 6: 0.25, 7: 0.22}
TEST_EVERY_WEEKS = 8
ANCHOR_KEY = {"corrida": "run_vdot", "ciclismo": "ftp_w", "natacao": "css_s100", "remo": "row_2k_s"}


def _lookup(table: list[tuple[float, float]], x: float) -> float:
    """Interpolação linear numa tabela (distância → valor)."""
    pts = sorted(table)
    if x <= pts[0][0]:
        return pts[0][1] * (x / pts[0][0]) ** 0.5
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _level(tier_key: str) -> str:
    return {"iniciante": "iniciante", "intermediario": "intermediario", "avancado": "avancado", "elite": "elite"}.get(
        tier_key, "intermediario")


def _d(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


# ------------------------------------------------------------- configuração
def config(profile: dict, sessions_override: dict | None = None) -> dict:
    """Configuração efetiva: modalidades ativas, metas e sessões (evento sobrepõe metas)."""
    out = _config(profile)
    for k, n in (sessions_override or {}).items():
        if k in out:
            out[k]["sessions"] = int(n)
    return out


def _config(profile: dict) -> dict:
    end = (profile or {}).get("endurance") or {}
    out = {}
    for s, c in (end.get("sports") or {}).items():
        if s in sp.SPORTS and c and c.get("enabled"):
            out[s] = {**c, "sessions": int(c.get("sessions") or VOLUME[s]["sessions"])}
    ev = end.get("event") or {}
    if ev.get("type") in EVENTS and _d(ev.get("date")):
        e = EVENTS[ev["type"]]
        for s, n in EVENT_SESSIONS[ev["type"]].items():
            base = out.get(s) or {"env": next(iter(sp.SPORTS[s]["envs"]))}
            dist = sum(d for ls, d in e["legs"] if ls == s)
            out[s] = {**base, "enabled": True, "sessions": max(int(base.get("sessions") or 0), n),
                      "goal": {"type": "evento", "event": ev["type"], "distance": dist, "date": ev["date"]}}
    return out


def active(profile: dict) -> bool:
    return bool(config(profile))


# ---------------------------------------------------------------- volume
def _current(sport: str, c: dict, level: str, recent: dict | None) -> float:
    if c.get("current_weekly"):
        return float(c["current_weekly"])
    if recent and recent.get(sport):
        return float(recent[sport])
    return float(VOLUME[sport]["start"][level])


def _peak(sport: str, c: dict, level: str) -> float:
    g = c.get("goal") or {}
    n = int(c.get("sessions") or VOLUME[sport]["sessions"])
    if g.get("type") == "volume" and g.get("weekly"):
        return float(g["weekly"])
    if g.get("type") == "evento":
        return EVENTS[g["event"]]["peak"].get(sport, 0) * LEVEL_FACTOR[level] / 0.85
    if g.get("type") == "prova" and g.get("distance"):
        return _lookup(RACE_PEAK[sport], float(g["distance"])) * LEVEL_FACTOR[level] * SESSIONS_FACTOR.get(min(n, 7), 1)
    return _current(sport, c, level, None) * 1.3


def _taper(sport: str, c: dict) -> int:
    g = c.get("goal") or {}
    if g.get("type") == "evento":
        return EVENTS[g["event"]]["taper"]
    if g.get("type") == "prova" and g.get("distance"):
        return int(round(_lookup(TAPER_WEEKS[sport], float(g["distance"]))))
    return 0


def weekly_cap(sport: str, n_sessions: int) -> float:
    """Teto semanal que cabe no número de sessões (o longo é o maior)."""
    mx = VOLUME[sport]["max"]
    return mx if n_sessions <= 1 else mx * (0.55 + 0.45 * (n_sessions - 1)) * 0.9


def _trend(start: float, peak: float, n: int) -> float:
    build_weeks = max(0, n) - max(0, n) // 4  # semanas leves não sobem
    return min(peak, start * (1.08 ** build_weeks)) if peak >= start else peak


def done_ceiling(done: dict | None, sport: str, week_idx: int, planned_avg3: float | None = None) -> float | None:
    """Teto pelo que o atleta REALMENTE fez (registros/Garmin), só quando ficou bem atrás do plano
    (fez < 70% do planejado nas últimas 3 semanas: doença, lesão, viagem): volta a +15% da melhor semana recente.
    Voltando de pausa (3 semanas sem nada, mas treinava antes), recomeça em ~60% do volume de antes.
    Faltas normais (cumprir 80% do plano) não freiam a progressão. Só vale para quem registra os treinos."""
    d = (done or {}).get(sport)
    if not d or week_idx < 2 or not d.get("tracked"):
        return None
    avg3, mx3, before = float(d.get("avg3") or 0), float(d.get("max3") or 0), float(d.get("before") or 0)
    wk = list(d.get("weeks") or [])  # [semana passada, retrasada, antes]
    if avg3 <= 0:
        return before * 0.6 if before > 0 else None
    if planned_avg3 and avg3 >= 0.7 * planned_avg3:
        return None
    ref = max([before] + wk) if (before or wk) else mx3
    low = lambda v: v < 0.25 * (planned_avg3 or ref or 1)  # noqa: E731
    if len(wk) >= 2 and low(wk[0]) and low(wk[1]):
        return ref * 0.65   # 2+ semanas paradas (doença, lesão): recomeça em ~65% do que fazia
    if wk and low(wk[0]):
        return mx3          # 1 semana falhada: repete o volume, sem subir
    return max(mx3 * 1.15, avg3 * 1.3)


def week_volume(sport: str, c: dict, level: str, week_idx: int, ws: date, recent: dict | None = None,
                cap: float | None = None, done: dict | None = None) -> dict:
    """Volume da semana e fase. week_idx = semanas desde o início do plano (0 = primeira).

    `cap` = teto semanal (sessões × tempo máximo no ritmo do atleta); entra antes da semana leve
    e do polimento, para que eles continuem reduzindo de verdade.
    """
    cap = min(cap or 1e12, weekly_cap(sport, int(c.get("sessions") or VOLUME[sport]["sessions"])))
    out = _week_volume(sport, c, level, week_idx, ws, recent, cap)
    planned3 = None
    if done and week_idx >= 2:
        prev = [_week_volume(sport, c, level, week_idx - k, ws - timedelta(weeks=k), recent, cap)["volume"]
                for k in (1, 2, 3) if week_idx - k >= 0]
        planned3 = sum(prev) / len(prev) if prev else None
    ceil = done_ceiling(done, sport, week_idx, planned3)
    if ceil is not None and out["phase"] not in ("prova", "recuperacao") and out["volume"] > ceil:
        out = {**out, "planned_volume": out["volume"], "volume": max(VOLUME[sport]["min"], ceil), "adjusted": True}
    return out


def _week_volume(sport: str, c: dict, level: str, week_idx: int, ws: date, recent: dict | None, cap: float) -> dict:
    start = min(_current(sport, c, level, recent), cap)
    # corrida: o pico nunca fica abaixo do que a pessoa já faz (meta de 5 km não é motivo para cortar o volume pela metade)
    floor = sport == "corrida" and int(c.get("sessions") or VOLUME[sport]["sessions"]) >= 3  # com 2 sessões não cabe
    peak = min(max(_peak(sport, c, level), start) if floor else _peak(sport, c, level), cap)
    g = c.get("goal") or {}
    race = _d(g.get("date")) if g.get("type") in ("prova", "evento") else None
    n = max(0, week_idx)
    if race:
        weeks_to = (race - ws).days // 7
        taper = max(1, _taper(sport, c))
        # polimento/prova/recuperação partem do volume realmente atingido antes do polimento
        top = _trend(start, peak, n + weeks_to - taper - 1)
        if weeks_to < 0:
            rec = -weeks_to
            if rec <= 2:
                return {"volume": top * (0.45 if rec == 1 else 0.6), "phase": "recuperacao", "weeks_to_race": weeks_to}
            return {"volume": top * 0.75, "phase": "manutencao", "weeks_to_race": weeks_to, "after_race": True}
        if weeks_to == 0:
            return {"volume": top * 0.4, "phase": "prova", "weeks_to_race": 0}
        if weeks_to <= taper:
            frac = {1: 0.55, 2: 0.7, 3: 0.8}.get(weeks_to, 0.8) if taper > 1 else 0.6
            return {"volume": top * frac, "phase": "polimento", "weeks_to_race": weeks_to}
    # progressão: +8%/semana a partir do atual, semana leve a cada 4ª, teto no pico
    trend = _trend(start, peak, n)
    deload = n % 4 == 3
    vol = trend * (0.75 if deload else 1.0)
    if race:
        weeks_to = (race - ws).days // 7
        build_left = weeks_to - max(1, _taper(sport, c))
        phase = "pico" if build_left <= 3 else ("construcao" if trend >= start * 1.15 or build_left <= 8 else "base")
    else:
        phase = "manutencao" if trend >= peak * 0.98 else ("base" if n < 4 else "construcao")
    if deload:
        phase = "semana_leve"
    return {"volume": vol, "phase": phase, "weeks_to_race": (race - ws).days // 7 if race else None}


PHASE_PT = {"base": "Base", "construcao": "Construção", "pico": "Pico", "polimento": "Polimento", "prova": "Semana da prova",
            "recuperacao": "Recuperação pós-prova", "manutencao": "Manutenção (meta atingida)", "semana_leve": "Semana mais leve"}


# ------------------------------------------------------------- passos
def _steady(sport, kind, zone, anchors, env, dist_m=None, dur_s=None, note=None):
    step = {"kind": kind, "zone": zone, "target": sp.target_for(sport, zone, anchors)}
    v = sp.speed_mps(sport, zone, anchors, env)
    if dist_m:
        step["dist_m"] = int(round(dist_m))
        step["dur_s"] = int(round(dist_m / v))
    else:
        step["dur_s"] = int(round(dur_s))
        step["dist_m"] = int(round(dur_s * v)) if sport not in ("ciclismo",) or anchors.get("ftp_w") is None else None
    if note:
        step["note"] = note
    return step


def _rest(dur_s, kind="recovery", zone=1, sport=None, anchors=None, env=None, note=None):
    st = {"kind": kind, "dur_s": int(dur_s), "zone": zone}
    if sport and kind == "recovery":
        st["target"] = sp.target_for(sport, zone, anchors or {})
        st["dist_m"] = int(dur_s * sp.speed_mps(sport, zone, anchors or {}, env)) if sport != "natacao" else 0
    if note:
        st["note"] = note
    return st


def _repeat(n, steps):
    return {"kind": "repeat", "n": int(n), "steps": steps}


def _custom_pace_target(sport: str, sec: float, spread: float = 5) -> dict:
    if sport == "corrida":
        return {"type": "pace", "fast_s_km": round(sec - spread), "slow_s_km": round(sec + spread),
                "label": f"{sp.fmt_pace(sec - spread)}–{sp.fmt_pace(sec + spread)}"}
    if sport == "natacao":
        return {"type": "swim_pace", "fast_s_100": round(sec - 2), "slow_s_100": round(sec + 2),
                "label": f"{sp.fmt_pace(sec - 2, '/100m')}–{sp.fmt_pace(sec + 2, '/100m')}"}
    return {}


def _pct_ftp_target(ftp: float | None, lo: float, hi: float, zone: int, anchors: dict) -> dict:
    if ftp:
        return {"type": "power", "low_w": round(ftp * lo), "high_w": round(ftp * hi),
                "label": f"{round(ftp * lo)}–{round(ftp * hi)} W ({int(lo * 100)}–{int(hi * 100)}% FTP)"}
    return sp.target_for("ciclismo", zone, anchors)


def _goal_pace(sport: str, c: dict) -> float | None:
    """Ritmo alvo da prova (s/km na corrida, s/100 m na natação)."""
    g = c.get("goal") or {}
    t = sp.parse_duration(g.get("target"))
    if not t or not g.get("distance"):
        return None
    if sport == "corrida":
        vd = sp.vdot(float(g["distance"]), t)
        return t / float(g["distance"]) if vd and 20 <= vd <= 85 else None  # meta impossível é ignorada
    if sport == "natacao":
        pace = t / (float(g["distance"]) / 100)
        return pace if 55 <= pace <= 240 else None
    return None


def goal_warning(sport: str, c: dict) -> str | None:
    g = c.get("goal") or {}
    if g.get("type") == "prova" and g.get("target") and sport in ("corrida", "natacao") and _goal_pace(sport, c) is None:
        return (f"Tempo alvo de {sp.SPORTS[sport]['name'].lower()} ({g['target']} em {g.get('distance')}) fora do possível "
                "para humanos ou mal digitado — ignorado; os treinos usam suas zonas.")
    return None


# -------------------------------------------------- sessões por modalidade
def _run(kind, km, c, anchors, env, level, week_idx, phase):
    S = "corrida"
    note = None
    if kind == "longo":
        steps = [_steady(S, "steady", 2, anchors, env, dist_m=km * 1000)]
        gp = _goal_pace(S, c)
        dist = float((c.get("goal") or {}).get("distance") or 0)
        if gp and dist >= 21 and phase in ("construcao", "pico") and week_idx % 2 == 1:
            fin = round(km * 0.25, 1)
            steps = [_steady(S, "steady", 2, anchors, env, dist_m=(km - fin) * 1000),
                     {**_steady(S, "interval", 3, anchors, env, dist_m=fin * 1000), "target": _custom_pace_target(S, gp),
                      "note": "final no ritmo de prova"}]
            note = f"Longão com os últimos {fin:g} km no ritmo da prova"
        return "Longão", steps, note
    if kind == "leve":
        strides = level == "iniciante" or week_idx % 2 == 0
        return "Rodagem leve", [_steady(S, "steady", 2, anchors, env, dist_m=km * 1000,
                                        note="6 × 20 s soltos (educativos) no final" if strides else None)], None
    if kind == "regenerativo":
        return "Regenerativo", [_steady(S, "steady", 1, anchors, env, dist_m=km * 1000)], None
    warm, cool = 2.0, 1.5
    work = max(3.0 if kind == "ritmo" else 1.5, km - warm - cool)
    if kind == "intervalado":
        rep = 800 if level == "iniciante" else 1000
        n = max(3, min(8, round(work * 1000 / rep / 1.6)))  # ~60% do miolo é tiro
        return f"Intervalado {n} × {rep} m", [
            _steady(S, "warmup", 2, anchors, env, dist_m=warm * 1000),
            _repeat(n, [_steady(S, "interval", 5, anchors, env, dist_m=rep), _rest(120 if rep >= 1000 else 90, sport=S, anchors=anchors, env=env)]),
            _steady(S, "cooldown", 1, anchors, env, dist_m=cool * 1000)], "Tiros no ritmo I: forte, mas igual do primeiro ao último"
    if kind == "ritmo":
        if work <= 4.5:
            return f"Ritmo contínuo {work:g} km", [_steady(S, "warmup", 2, anchors, env, dist_m=warm * 1000),
                                                    _steady(S, "interval", 4, anchors, env, dist_m=work * 1000),
                                                    _steady(S, "cooldown", 1, anchors, env, dist_m=cool * 1000)], "Ritmo de limiar: 'confortavelmente difícil'"
        n = max(2, round(work / 2))
        return f"Ritmo {n} × 2 km", [_steady(S, "warmup", 2, anchors, env, dist_m=warm * 1000),
                                       _repeat(n, [_steady(S, "interval", 4, anchors, env, dist_m=2000), _rest(60, sport=S, anchors=anchors, env=env)]),
                                       _steady(S, "cooldown", 1, anchors, env, dist_m=cool * 1000)], "Ritmo de limiar: 'confortavelmente difícil'"
    if kind == "ritmo_prova":
        gp = _goal_pace(S, c) or sp.pace_at(anchors.get("run_vdot") or 40, 0.88)
        dist = float((c.get("goal") or {}).get("distance") or 10)
        rep, n, rec = ((1000, 5, 120) if dist <= 5 else (2000, 3, 90) if dist <= 10 else (3000, 3, 120) if dist <= 21.1 else (5000, 2, 180))
        n = max(2, min(n, int(work * 1000 // (rep * 1.15))))  # cabe no volume da semana (polimento encurta)
        return f"Ritmo de prova {n} × {rep // 1000 if rep >= 1000 else rep} {'km' if rep >= 1000 else 'm'}", [
            _steady(S, "warmup", 2, anchors, env, dist_m=warm * 1000),
            _repeat(n, [{**_steady(S, "interval", 4, anchors, env, dist_m=rep), "target": _custom_pace_target(S, gp)},
                        _rest(rec, sport=S, anchors=anchors, env=env)]),
            _steady(S, "cooldown", 1, anchors, env, dist_m=cool * 1000)], f"No ritmo alvo da prova ({sp.fmt_pace(gp)})"
    if kind == "ativacao":
        gp = _goal_pace(S, c) or sp.pace_at(anchors.get("run_vdot") or 40, 0.95)
        return "Ativação pré-prova", [_steady(S, "warmup", 2, anchors, env, dist_m=2000),
                                      _repeat(4, [{**_steady(S, "interval", 4, anchors, env, dist_m=200), "target": _custom_pace_target(S, gp)},
                                                  _rest(60, sport=S, anchors=anchors, env=env)]),
                                      _steady(S, "cooldown", 1, anchors, env, dist_m=1000)], "Curto e solto: só para lembrar o ritmo da prova"
    if kind == "prova":
        gp = _goal_pace(S, c)
        dist = float((c.get("goal") or {}).get("distance") or km)
        step = _steady(S, "interval", 4, anchors, env, dist_m=dist * 1000)
        if gp:
            step["target"] = _custom_pace_target(S, gp, 3)
            step["dur_s"] = int(gp * dist)
        return f"PROVA {dist:g} km", [_steady(S, "warmup", 1, anchors, env, dist_m=1500, note="aquecimento leve + 3 acelerações"),
                                      step], f"Dia da prova! Ritmo alvo {sp.fmt_pace(gp)}" if gp else "Dia da prova!"
    if kind == "teste":
        return "Teste: 5 km no máximo sustentável", [
            _steady(S, "warmup", 2, anchors, env, dist_m=2000, note="inclua 3 acelerações de 20 s"),
            {**_steady(S, "interval", 5, anchors, env, dist_m=5000), "target": {"type": "open", "label": "máximo sustentável"},
             "note": "registre o tempo: o app recalcula seus ritmos"},
            _steady(S, "cooldown", 1, anchors, env, dist_m=1500)], "Teste de 5 km (recalibra o VDOT)"
    raise ValueError(kind)


def _bike(kind, hours, c, anchors, env, level, week_idx, phase):
    S = "ciclismo"
    ftp = anchors.get("ftp_w")
    mins = hours * 60
    if kind in ("longo", "leve", "regenerativo"):
        z = {"longo": 2, "leve": 2, "regenerativo": 1}[kind]
        title = {"longo": "Pedal longo", "leve": "Pedal de endurance", "regenerativo": "Giro regenerativo"}[kind]
        return title, [_steady(S, "steady", z, anchors, env, dur_s=mins * 60,
                               note="cadência 85–95 rpm" if kind != "longo" else "coma e beba a cada 20–30 min")], None
    warm, cool = 15, 10
    if kind == "teste":
        return "Teste de FTP (20 min)", [
            _steady(S, "warmup", 2, anchors, env, dur_s=20 * 60),
            {**_steady(S, "interval", 5, anchors, env, dur_s=5 * 60), "note": "5 min forte para 'abrir'"},
            _steady(S, "recovery", 1, anchors, env, dur_s=10 * 60),
            {**_steady(S, "interval", 5, anchors, env, dur_s=20 * 60), "target": {"type": "open", "label": "máximo constante"},
             "note": "registre a potência média: FTP = 95% dela"},
            _steady(S, "cooldown", 1, anchors, env, dur_s=10 * 60)], "Teste de FTP de 20 min"
    spec = {"sweet_spot": ("Sweet spot", 20, 0.88, 0.93, 3, 5 * 60),
            "limiar": ("Limiar", 10, 0.95, 1.05, 4, 5 * 60),
            "vo2": ("VO2máx", 4, 1.06, 1.20, 5, 4 * 60)}[kind]
    title, rep_min, lo, hi, zone, rec = spec
    avail = max(rep_min, mins - warm - cool)
    n = max(2, min(6 if kind == "vo2" else 4, int(avail // (rep_min + rec / 60))))
    if level == "iniciante" and kind != "sweet_spot":
        n = max(2, n - 1)
    return f"{title} {n} × {rep_min} min", [
        _steady(S, "warmup", 2, anchors, env, dur_s=warm * 60),
        _repeat(n, [{**_steady(S, "interval", zone, anchors, env, dur_s=rep_min * 60), "target": _pct_ftp_target(ftp, lo, hi, zone, anchors)},
                    _steady(S, "recovery", 1, anchors, env, dur_s=rec)]),
        _steady(S, "cooldown", 1, anchors, env, dur_s=cool * 60)], f"{int(lo * 100)}–{int(hi * 100)}% do FTP"


def _swim(kind, meters, c, anchors, env, level, week_idx, phase):
    S = "natacao"
    m = max(600, int(round(meters / 50) * 50))
    if kind == "teste":
        return "Teste CSS (400 m + 200 m)", [
            _steady(S, "warmup", 2, anchors, env, dist_m=400),
            {**_steady(S, "interval", 5, anchors, env, dist_m=400), "target": {"type": "open", "label": "máximo constante"}},
            _rest(300, kind="rest", note="5 min de descanso"),
            {**_steady(S, "interval", 5, anchors, env, dist_m=200), "target": {"type": "open", "label": "máximo"}},
            _steady(S, "cooldown", 1, anchors, env, dist_m=200)], "Registre os tempos de 400 e 200: o app calcula o CSS"
    if env == "aguas_abertas" or kind == "longo":
        return "Nado contínuo", [_steady(S, "steady", 2, anchors, env, dist_m=m, note="respire bilateral; olhe à frente a cada 6–10 braçadas")], None
    warm, cool = 300, 200
    main = max(200, m - warm - cool)
    if kind in ("leve", "regenerativo"):
        n = max(2, main // 200)
        return "Aeróbico + técnica", [
            _steady(S, "warmup", 2, anchors, env, dist_m=warm, note="inclua 4 × 25 m de educativo"),
            _repeat(n, [_steady(S, "interval", 2, anchors, env, dist_m=200), _rest(20, kind="rest")]),
            _steady(S, "cooldown", 1, anchors, env, dist_m=cool)], "Aeróbico: braçada longa, contando as braçadas"
    if kind == "css":
        n = max(4, min(16, main // 100))
        return f"CSS {n} × 100 m", [
            _steady(S, "warmup", 2, anchors, env, dist_m=warm),
            _repeat(n, [_steady(S, "interval", 4, anchors, env, dist_m=100), _rest(15, kind="rest")]),
            _steady(S, "cooldown", 1, anchors, env, dist_m=cool)], "Todos os 100 m no CSS, saída a cada ~15 s de pausa"
    if kind == "velocidade":
        n = max(6, min(16, (main // 2) // 50))
        rest_m = max(200, main - n * 50)
        return f"Velocidade {n} × 50 m", [
            _steady(S, "warmup", 2, anchors, env, dist_m=warm),
            _repeat(n, [_steady(S, "interval", 5, anchors, env, dist_m=50), _rest(20, kind="rest")]),
            _steady(S, "steady", 2, anchors, env, dist_m=rest_m, note="pull buoy, técnica"),
            _steady(S, "cooldown", 1, anchors, env, dist_m=cool)], "50 m rápidos com técnica limpa"
    raise ValueError(kind)


def _row(kind, meters, c, anchors, env, level, week_idx, phase):
    S = "remo"
    m = max(2000, int(round(meters / 500) * 500))
    if kind == "teste":
        return "Teste de 2 km", [_steady(S, "warmup", 2, anchors, env, dist_m=2000),
                                 {**_steady(S, "interval", 5, anchors, env, dist_m=2000), "target": {"type": "open", "label": "máximo"}},
                                 _steady(S, "cooldown", 1, anchors, env, dist_m=1000)], "Registre o tempo: o app recalcula os splits"
    if kind in ("leve", "longo", "regenerativo"):
        return "Remo contínuo (UT2)", [_steady(S, "steady", 2 if kind != "regenerativo" else 1, anchors, env, dist_m=m,
                                               note="18–22 remadas/min")], None
    warm, cool = 2000, 1000
    if kind == "intervalos_1000":
        n = max(3, min(5, (m - warm - cool) // 1500))
        return f"Intervalos {n} × 1000 m", [_steady(S, "warmup", 2, anchors, env, dist_m=warm),
                                             _repeat(n, [_steady(S, "interval", 4, anchors, env, dist_m=1000), _rest(180, kind="rest")]),
                                             _steady(S, "cooldown", 1, anchors, env, dist_m=cool)], "Split do limiar, 24–28 remadas/min"
    n = max(4, min(8, (m - warm - cool) // 750))
    return f"Intervalos {n} × 500 m", [_steady(S, "warmup", 2, anchors, env, dist_m=warm),
                                        _repeat(n, [_steady(S, "interval", 5, anchors, env, dist_m=500), _rest(120, kind="rest")]),
                                        _steady(S, "cooldown", 1, anchors, env, dist_m=cool)], "Ritmo de prova de 2 km"


def _walk(kind, vol, c, anchors, env, level, week_idx, phase, sport):
    if sport == "trilha":
        return "Trilha", [_steady(sport, "steady", 2, anchors, env, dur_s=vol * 3600,
                                  note="some ~10 min para cada 100 m de subida; bastões ajudam na descida")], None
    title = "Caminhada longa" if kind == "longo" else "Caminhada"
    return title, [_steady(sport, "steady", 2, anchors, env, dist_m=vol * 1000, note="passo firme, ~100+ passos/min")], None


def build_session(sport: str, kind: str, volume: float, c: dict, anchors: dict, level: str,
                  week_idx: int = 0, phase: str = "base") -> dict:
    """Uma sessão de resistência pronta (passos, alvos, duração e distância estimadas)."""
    env, ex_id = sp.sport_env(sport, c.get("env"))
    if kind in ("ativacao", "prova") and sport != "corrida":
        kind = "leve"
    if sport == "corrida":
        title, steps, note = _run(kind, volume, c, anchors, env, level, week_idx, phase)
    elif sport == "ciclismo":
        title, steps, note = _bike(kind, volume, c, anchors, env, level, week_idx, phase)
    elif sport == "natacao":
        title, steps, note = _swim(kind, volume, c, anchors, env, level, week_idx, phase)
    elif sport == "remo":
        title, steps, note = _row(kind, volume, c, anchors, env, level, week_idx, phase)
    else:
        title, steps, note = _walk(kind, volume, c, anchors, env, level, week_idx, phase, sport)
    flat = []
    for st in steps:
        flat += [x for _ in range(st["n"]) for x in st["steps"]] if st["kind"] == "repeat" else [st]
    dur = sum(s.get("dur_s") or 0 for s in flat)
    dist = sum(s.get("dist_m") or 0 for s in flat)
    hard = kind in ("intervalado", "ritmo", "ritmo_prova", "sweet_spot", "limiar", "vo2", "css", "velocidade",
                    "intervalos_1000", "intervalos_500", "teste", "prova")
    zone = max((s.get("zone") or 1) for s in flat) if hard else (2 if kind != "regenerativo" else 1)
    end = {"sport": sport, "env": env, "type": kind, "title": title, "steps": steps, "hard": hard, "zone": zone,
           "dur_s": int(dur), "dist_m": int(dist) if dist else None, "note": note, "phase": phase}
    if sport == "natacao":
        end["pool_m"] = int(c.get("pool_m") or 25) if env == "piscina" else None
    return {"exercise_id": ex_id, "phase": "principal", "sport": sport, "endurance": end,
            "duration_min": round(dur / 60), "zone": zone, **({"distance_km": round(dist / 1000, 2)} if dist else {})}


# ------------------------------------------------------- semana de uma modalidade
def _week_roles(sport: str, c: dict, level: str, week_idx: int, phase: str, n: int, test_due: bool) -> list[str]:
    """Papéis das sessões da semana: longo, qualidade(s) e leves."""
    if n <= 0:
        return []
    g = c.get("goal") or {}
    if phase == "prova":
        # a prova entra na agenda no próprio dia; aqui só ativação e rodagens curtas
        return (["ativacao"] if sport == "corrida" else ["leve"]) + ["regenerativo"] * (n - 2 if n > 2 else 0)
    if phase == "recuperacao":
        return ["regenerativo"] * n
    if n == 1:  # uma sessão só na semana: contínua (sem teste nem tiros)
        return ["longo"] if sport in LEG_SPORTS or sport == "caminhada" else ["leve"]
    roles = ["longo"] if n >= 2 or sport in ("trilha", "caminhada") else []
    rot = QUALITY_ROTATION.get(sport) or []
    n_quality = 0
    if rot and phase not in ("recuperacao",):
        n_quality = 1 if (n >= 3 or (n >= 2 and sport in ("natacao", "ciclismo", "remo"))) else 0
        if n >= 5 and level in ("avancado", "elite") and phase not in ("polimento", "semana_leve", "prova"):
            n_quality = 2
        if sport == "corrida" and level == "iniciante" and week_idx < 4:
            n_quality = 0  # iniciante: primeiro constrói base (educativos na rodagem)
    q = []
    for i in range(n_quality):
        kind = rot[(week_idx + i) % len(rot)]
        if sport == "corrida" and g.get("type") in ("prova", "evento") and phase in ("pico", "polimento") and i == 0:
            kind = "ritmo_prova" if _goal_pace(sport, c) else kind
        if sport == "ciclismo" and level == "iniciante":
            kind = "sweet_spot"
        q.append(kind)
    if test_due and rot and phase in ("base", "construcao", "manutencao", "semana_leve"):
        if q:
            q[0] = "teste"
        else:
            q.append("teste")
    if sport == "natacao" and (c.get("env") == "aguas_abertas"):
        q = []
    rest = n - len(roles) - len(q)
    easy = ["leve"] * max(0, rest)
    if phase == "recuperacao":
        easy = ["regenerativo"] * len(easy)
    out = roles + q + easy
    return out[:n] if len(out) > n else out


def _vol(it: dict) -> float:
    e = it["endurance"]
    u = VOLUME[e["sport"]]["unit"]
    return (e.get("dist_m") or 0) / 1000 if u == "km" else (e.get("dist_m") or 0) if u == "m" else e["dur_s"] / 3600


def _compose(sport: str, roles: list[str], vol: float, c: dict, anchors: dict, level: str, week_idx: int, phase: str) -> list[dict]:
    """Monta as sessões da semana somando o volume planejado.

    1) sessões fortes/teste com o tamanho real delas (aquecimento + trabalho + volta);
       se a semana não comporta (forte + longo maior que ela + leves no mínimo), vira leve;
    2) longo: fatia do volume, nunca menor que a sessão forte (pernas) e dentro dos tetos;
    3) o que sobra vai para as leves (no mínimo da modalidade).
    """
    mn, mx = min_session(sport, anchors, c.get("env")), VOLUME[sport]["max"]
    q_share = QUALITY_SHARE.get(sport, 0.2)
    g = c.get("goal") or {}
    special = ("ativacao", "prova")
    roles = list(roles)
    while True:
        quality_roles = [r for r in roles if r not in ("longo", "leve", "regenerativo") + special]
        q_target = {"corrida": 5.0 if level == "iniciante" else 6.5, "ciclismo": 1.0, "natacao": 1500, "remo": 6000}.get(sport, mn)
        q_target = max(q_target, vol * q_share)
        q_items = [build_session(sport, r, _round_vol(sport, q_target), c, anchors, level, week_idx, phase) for r in quality_roles]
        q_built = sum(_vol(x) for x in q_items)
        n_long = roles.count("longo")
        n_easy = sum(1 for r in roles if r in ("leve", "regenerativo") + special)
        long_floor = max([mn] + ([max(_vol(x) for x in q_items)] if q_items and sport in LEG_SPORTS else []))
        need = q_built + n_long * long_floor + n_easy * mn
        if quality_roles and need > vol * 1.15:
            # não cabe: a forte (ou o teste) vira leve nesta semana
            i = max(i for i, r in enumerate(roles) if r in quality_roles)
            roles[i] = "leve"
            continue
        break
    long_v = 0.0
    if n_long:
        long_v = vol * LONG_SHARE.get(min(len(roles), 7), 0.22)
        dist = float(g.get("distance") or 0)
        if sport in LONG_CAP and g.get("type") in ("prova", "evento") and dist:
            cap = _lookup(LONG_CAP[sport], dist)
            long_v = min(long_v, max(cap, dist * 1.2) if sport == "natacao" else cap)
        long_v = min(mx, max(long_floor, long_v))
        long_v = min(long_v, max(long_floor, vol - q_built - n_easy * mn))
        if not n_easy:  # sem sessões leves: o longo leva o que sobra
            long_v = min(mx, max(long_floor, vol - q_built))
    # teto por tempo (ritmo do atleta): natação lenta não vira 3 h de piscina
    unit = VOLUME[sport]["unit"]
    v2 = sp.speed_mps(sport, 2, anchors, c.get("env"))
    t_cap = MAX_SESSION_MIN[sport] * 60
    time_cap = t_cap / 3600 if unit == "h" else t_cap * v2 / (1000 if unit == "km" else 1)
    mx = min(mx, time_cap)
    long_cap_bind = False
    if n_long:
        before = long_v
        long_v = min(long_v, mx)
        long_cap_bind = long_v < before or (g.get("type") in ("prova", "evento") and g.get("distance"))
    easy_v = max(mn, (vol - q_built - long_v) / n_easy) if n_easy else 0
    if n_easy and easy_v > long_v and n_long and sport in LEG_SPORTS:
        tot = vol - q_built
        new_long = min(mx, max(long_v, tot / (n_easy + 1) * 1.15))
        if long_cap_bind and new_long > long_v:
            easy_v = max(mn, long_v * 0.9)  # o longo está no teto da prova: as leves não passam dele
        else:
            long_v = new_long
            easy_v = max(mn, (tot - long_v) / n_easy)
    easy_v = min(easy_v, mx, long_v if (n_long and sport in LEG_SPORTS) else mx)
    out, qi = [], iter(q_items)
    for r in roles:
        if r == "longo":
            out.append(build_session(sport, "longo", _round_vol(sport, long_v), c, anchors, level, week_idx, phase))
        elif r in ("leve", "regenerativo") + special:
            out.append(build_session(sport, r, _round_vol(sport, easy_v), c, anchors, level, week_idx, phase))
        else:
            out.append(next(qi))
    return out


def _round_vol(sport: str, v: float) -> float:
    unit = VOLUME[sport]["unit"]
    if unit == "km":
        return round(v * 2) / 2 if v >= 10 else round(v * 4) / 4
    if unit == "h":
        return round(v * 12) / 12  # 5 min
    return int(round(v / 100) * 100)


def week_plan(profile: dict, ws: date, start: date, tier_key: str = "intermediario",
              recent: dict | None = None, today: date | None = None, sessions_override: dict | None = None,
              full_slots: bool = False, max_hard: int | None = None, done: dict | None = None) -> dict:
    """Todas as sessões de resistência da semana, por modalidade (sem dia definido)."""
    cfg = config(profile, sessions_override)
    level = _level(tier_key)
    anchors = sp.anchors_of(profile)
    week_idx = max(0, (ws - start).days // 7)
    end_cfg = (profile or {}).get("endurance") or {}
    out = {}
    test_slot_used = False
    for sport, c in cfg.items():
        n_cfg = int(c["sessions"])
        t_cap = session_max(sport, anchors, c.get("env")) * (1 if n_cfg <= 1 else (0.55 + 0.45 * (n_cfg - 1)) * 0.9)
        wv = week_volume(sport, c, level, week_idx, ws, recent, cap=t_cap, done=done)
        n = int(c["sessions"])
        test_due = False
        if end_cfg.get("tests", True) and sport in ANCHOR_KEY and not test_slot_used:
            meta = (end_cfg.get("anchors_meta") or {}).get(ANCHOR_KEY[sport]) or {}
            last = _d(meta.get("date"))
            missing = not anchors.get(ANCHOR_KEY[sport])
            stale = last is not None and (ws - last).days >= TEST_EVERY_WEEKS * 7
            # teste só fora de semana de prova/polimento e no máximo um por semana
            test_due = (missing and week_idx in (0, 1)) or stale
            if test_due:
                test_slot_used = True
        mn = min_session(sport, anchors, c.get("env"))
        n_week = n if full_slots else min(n, max(1, int(wv["volume"] // (mn * 1.1)) or 1))
        roles = _week_roles(sport, c, level, week_idx, wv["phase"], n_week, test_due)
        sessions = _compose(sport, roles, wv["volume"], c, anchors, level, week_idx, wv["phase"])
        built = sum(_vol(x) for x in sessions)
        if built and built < wv["volume"] * 0.85:
            # poucas sessões para o volume (o longo tem teto pela distância da prova): a semana mostra o que cabe
            wv = {**wv, "volume": built}
        for r, s in zip(roles, sessions):
            s["endurance"]["role"] = "longo" if s["endurance"]["type"] == "longo" else ("qualidade" if s["endurance"]["hard"] else "leve")
        if wv.get("adjusted"):
            unit = VOLUME[sport]["unit"]
            fmt = (lambda v: f"{v:g} km") if unit == "km" else (lambda v: f"{int(v)} m") if unit == "m" else (lambda v: f"{v:.1f} h".replace(".", ","))
            msg = (f"Volume ajustado ao que você fez nas últimas semanas: {fmt(_round_vol(sport, wv['volume']))} "
                   f"em vez de {fmt(_round_vol(sport, wv['planned_volume']))} — volta a subir aos poucos, sem salto.")
            for s in sessions:
                s["endurance"]["note"] = (s["endurance"].get("note") + " · " if s["endurance"].get("note") else "") + msg
        out[sport] = {"volume": _round_vol(sport, wv["volume"]), "unit": VOLUME[sport]["unit"], "phase": wv["phase"],
                      **({"adjusted": True, "planned_volume": _round_vol(sport, wv["planned_volume"])} if wv.get("adjusted") else {}),
                      "phase_pt": ("Manutenção pós-prova — defina a próxima meta em Esportes" if wv.get("after_race")
                                   else PHASE_PT.get(wv["phase"], wv["phase"])), "weeks_to_race": wv.get("weeks_to_race"),
                      "sessions": sessions}
    _cap_hard(out, level, anchors, max_hard)
    return {"week_idx": week_idx, "sports": out, "bricks": _bricks(profile, out, week_idx)}


def _cap_hard(out: dict, level: str, anchors: dict, max_hard: int | None = None) -> None:
    """No máximo 3 sessões fortes na semana (2 para iniciante, menos com poucos dias livres): o resto vira leve."""
    cap = 2 if level == "iniciante" else 3
    if max_hard is not None:
        cap = max(0, min(cap, max_hard))
    hard = [(s, i) for s, d in out.items() for i, x in enumerate(d["sessions"]) if x["endurance"]["hard"]
            and x["endurance"]["type"] != "teste"]
    goal_first = sorted(hard, key=lambda t: 0 if t[0] == "corrida" else 1)
    for s, i in goal_first[cap:]:
        old = out[s]["sessions"][i]
        vol = (old.get("distance_km") or 0) if VOLUME[s]["unit"] == "km" else (
            old["duration_min"] / 60 if VOLUME[s]["unit"] == "h" else (old["endurance"].get("dist_m") or 1000))
        c = {"env": old["endurance"]["env"]}
        new = build_session(s, "leve", _round_vol(s, vol), c, anchors, level)
        new["endurance"]["role"] = "leve"
        new["endurance"]["note"] = "Era sessão forte: virou leve para não acumular sessões intensas demais na semana"
        out[s]["sessions"][i] = new


def _bricks(profile: dict, out: dict, week_idx: int) -> list[dict]:
    """Brick (bike + corrida em seguida) 1×/semana na construção e no pico de triathlon/duathlon."""
    ev = ((profile or {}).get("endurance") or {}).get("event") or {}
    if ev.get("type") not in EVENTS or "corrida" not in out or "ciclismo" not in out:
        return []
    phase = out["corrida"]["phase"]
    if phase not in ("construcao", "pico") or not ev["type"].startswith(("tri", "duathlon")):
        return []
    anchors = sp.anchors_of(profile)
    run_c = {"env": "rua"}
    run = build_session("corrida", "leve", 3.0 if "sprint" in ev["type"] else 5.0, run_c, anchors, "intermediario")
    run["endurance"].update(title="Transição: corrida logo após o pedal", role="brick",
                            note="Saia da bike e corra em até 5 min; os primeiros 1–2 km parecem pesados — é o treino")
    run["endurance"]["steps"][0]["zone"] = 3
    run["endurance"]["steps"][0]["target"] = sp.target_for("corrida", 3, anchors)
    return [{"sport": "multi", "items": [run], "attach_to": "ciclismo:longo"}]


# ------------------------------------------------------------- dias da semana
LEG_SPORTS = {s for s, d in sp.SPORTS.items() if d["legs"]}


def allocate(slots: list[dict], avail: list[int], n_strength: int, long_day_pref: dict[str, int] | None = None,
             combine: bool = False) -> dict:
    """Escolhe o dia de cada sessão de resistência e os dias de força.

    combine=True (musculação e esporte no mesmo treino): as sessões leves vão de preferência para
    depois da musculação (dia de força sem perna pesada), deixando dias inteiros de descanso.

    slots: [{key, sport, role: longo|qualidade|leve, minutes}]
    Retorna {key: weekday, ..., "_strength": [weekdays], "_doubles": [keys em dia de força]}.
    """
    avail = sorted(set(avail))
    long_day_pref = long_day_pref or {}
    day_load: dict[int, list[dict]] = {d: [] for d in avail}
    placed: dict[str, int] = {}

    def dist(a, b):
        return min((a - b) % 7, (b - a) % 7)

    weekend = [d for d in (5, 6) if d in avail] or avail[-1:]

    def is_long(x):  # longo que pesa (pernas, inclusive caminhada longa e trilha); nado contínuo entra como sessão leve
        return x["role"] == "longo" and (x["sport"] in LEG_SPORTS or x["sport"] in ("caminhada", "trilha"))

    # 1) longos: dia preferido → fim de semana → último dia; um longo por dia
    for s in sorted([x for x in slots if is_long(x)], key=lambda x: -x["minutes"]):
        prefs = ([long_day_pref[s["sport"]]] if s["sport"] in long_day_pref else []) + weekend + avail[::-1]
        day = next((d for d in prefs if d in day_load and not any(o["role"] == "longo" for o in day_load[d])), None)
        if day is None:
            day = min(avail, key=lambda d: len(day_load[d]))
        day_load[day].append(s)
        placed[s["key"]] = day
    longs = [placed[s["key"]] for s in slots if is_long(s)]
    # 2) qualidade: longe dos longos e de outras qualidades
    for s in [x for x in slots if x["role"] == "qualidade"]:
        def score(d):
            load = day_load[d]
            if any(o["role"] in ("longo", "qualidade") for o in load) or len(load) >= 2:
                return -100 - 10 * len(load) - sum(5 for o in load if o["role"] == "qualidade")
            near_long = min((dist(d, l) for l in longs), default=7)
            near_q = min((dist(d, placed[k]) for k in placed if any(x["key"] == k and x["role"] == "qualidade" for x in slots)), default=7)
            before_long = any((d + 1) % 7 == l for l in longs)
            after_long = any((d - 1) % 7 == l for l in longs)
            return (min(near_long, 3) * 2 + min(near_q, 3) * 2 - (4 if near_q == 1 else 0) - (3 if before_long else 0)
                    - (2 if after_long else 0) - len(load))
        day = max(avail, key=score)
        day_load[day].append(s)
        placed[s["key"]] = day
    # 3) dias de força: dias sem longo/qualidade, espalhados; longo e qualidade só em último caso
    hard_leg_days = {placed[s["key"]] for s in slots if (is_long(s) or s["role"] == "qualidade") and s["sport"] in LEG_SPORTS}
    strength: list[int] = []

    def s_score(d):
        load = day_load[d]
        pen = 0
        if any(is_long(o) for o in load):
            pen += 100
        if any(o["role"] == "qualidade" for o in load):
            pen += 50 if any(o["sport"] in LEG_SPORTS for o in load) else 20
        pen += 10 * sum(1 for x in strength if dist(d, x) <= 1)  # evita força em dias seguidos
        pen += 5 if any((d - 1) % 7 == l for l in longs) else 0  # dia seguinte ao longão: pernas cansadas
        return pen
    while len(strength) < n_strength and len(strength) < len(avail):
        d = min((x for x in avail if x not in strength), key=s_score)
        strength.append(d)
    strength.sort()
    # 4) leves: dia livre (sem força nem resistência) → dia de força sem perna pesada → menos carregado
    doubles = []
    def n_on(d):
        return len(day_load[d]) + (1 if d in strength else 0)

    for s in [x for x in slots if not is_long(x) and x["role"] != "qualidade"]:
        empty = [d for d in avail if not day_load[d] and d not in strength]
        if combine:
            pair = [d for d in strength if not day_load[d] and d not in hard_leg_days]
            if pair:
                empty = []
                day = pair[0] if s["sport"] not in LEG_SPORTS else max(
                    pair, key=lambda d: min((dist(d, l) for l in hard_leg_days), default=7))
                day_load[day].append(s)
                placed[s["key"]] = day
                doubles.append(s["key"])
                continue
        if empty:
            day = max(empty, key=lambda d: min((dist(d, l) for l in hard_leg_days), default=7))
        else:
            def sc(d):
                return (100 * max(0, n_on(d) - 1) + len(day_load[d]) + (1 if d in strength else 0)
                        + (1 if any(o["role"] == "qualidade" or is_long(o) for o in day_load[d]) else 0)
                        + (2 if s["sport"] in LEG_SPORTS and d in hard_leg_days else 0)
                        + (1 if any(o["sport"] == s["sport"] for o in day_load[d]) else 0))
            day = min(avail, key=sc)
        day_load[day].append(s)
        placed[s["key"]] = day
        if day in strength:
            doubles.append(s["key"])
    for s in slots:
        d = placed[s["key"]]
        if d in strength or len(day_load[d]) > 1:
            doubles.append(s["key"])
    doubles = sorted(set(doubles))
    return {**placed, "_strength": strength, "_doubles": doubles, "_hard_leg_days": sorted(hard_leg_days)}


# ------------------------------------------------------------ previsões
def bike_speed_kmh(power_w: float, weight_kg: float = 75, cda: float = 0.32, crr: float = 0.004) -> float:
    """Velocidade no plano para uma potência (modelo físico simples, sem vento)."""
    m, g, rho, eta = weight_kg + 9, 9.81, 1.2, 0.97
    lo, hi = 0.5, 25.0
    for _ in range(60):
        v = (lo + hi) / 2
        p = (crr * m * g + 0.5 * rho * cda * v * v) * v / eta
        lo, hi = (v, hi) if p < power_w else (lo, v)
    return round(v * 3.6, 1)


def predictions(profile: dict) -> dict:
    """Previsões por modalidade e do evento a partir das âncoras atuais."""
    a = sp.anchors_of(profile)
    cfg = config(profile)
    out: dict = {"sports": {}}
    for s, c in cfg.items():
        g = c.get("goal") or {}
        if s == "corrida" and a.get("run_vdot"):
            dists = [5, 10, 21.1, 42.2]
            out["sports"][s] = {"vdot": a["run_vdot"], "times": {str(d): sp.fmt_duration(sp.predict_time(a["run_vdot"], d)) for d in dists}}
            if g.get("type") == "prova" and g.get("distance"):
                pred = sp.predict_time(a["run_vdot"], float(g["distance"]))
                tgt = sp.parse_duration(g.get("target"))
                out["sports"][s]["goal"] = _goal_check(pred, tgt, g, a["run_vdot"], float(g["distance"]))
        elif s == "natacao" and a.get("css_s100"):
            out["sports"][s] = {"css": sp.fmt_pace(a["css_s100"], "/100m"),
                                "times": {str(d): sp.fmt_duration((a["css_s100"] + (2 if d <= 400 else 5)) * d / 100) for d in (400, 750, 1500, 1900, 3800)}}
        elif s == "ciclismo" and a.get("ftp_w"):
            w = float((profile or {}).get("weight_kg") or 75)
            out["sports"][s] = {"ftp": a["ftp_w"], "w_kg": round(a["ftp_w"] / w, 2),
                                "speed_kmh": {"1h (100%)": bike_speed_kmh(a["ftp_w"], w), "olímpico (88%)": bike_speed_kmh(a["ftp_w"] * 0.88, w),
                                              "70.3 (78%)": bike_speed_kmh(a["ftp_w"] * 0.78, w), "Ironman (70%)": bike_speed_kmh(a["ftp_w"] * 0.70, w)}}
        elif s == "remo" and a.get("row_2k_s"):
            out["sports"][s] = {"2k": sp.fmt_duration(a["row_2k_s"]), "split": sp.fmt_pace(sp.row_split(a["row_2k_s"]), "/500m")}
    ev = ((profile or {}).get("endurance") or {}).get("event") or {}
    if ev.get("type") in EVENTS:
        out["event"] = event_prediction(profile, ev)
    return out


def _goal_check(pred: float, tgt: float | None, g: dict, vd: float, dist: float) -> dict:
    res = {"predicted": sp.fmt_duration(pred), "target": sp.fmt_duration(tgt) if tgt else None}
    if tgt:
        need = sp.vdot(dist, tgt) or vd
        race = _d(g.get("date"))
        weeks = max(0, (race - clock.today()).days // 7) if race else 12
        # ganho realista de VDOT a cada 4 semanas de treino consistente: quem já é rápido melhora mais devagar
        per4 = 1.0 if vd < 35 else 0.7 if vd < 45 else 0.45 if vd < 55 else 0.3
        realistic_gain = min(6.0, weeks / 4 * per4)
        res.update(gap_s=round(pred - tgt), vdot_needed=need,
                   status="no_alvo" if pred <= tgt else ("possivel" if need - vd <= realistic_gain else "ambicioso"))
        if res["status"] == "ambicioso":
            res["realistic"] = sp.fmt_duration(sp.predict_time(vd + realistic_gain, dist))
    return res


def event_prediction(profile: dict, ev: dict) -> dict:
    e = EVENTS[ev["type"]]
    a = sp.anchors_of(profile)
    w = float((profile or {}).get("weight_kg") or 75)
    parts, total, missing = [], 0.0, []
    bike_frac = {"tri_supersprint": 0.92, "tri_sprint": 0.9, "tri_olimpico": 0.85, "tri_meio": 0.78, "tri_ironman": 0.70,
                 "duathlon_sprint": 0.88, "duathlon_padrao": 0.84, "aquathlon": 0.8}[ev["type"]]
    for s, d in e["legs"]:
        t = None
        if s == "natacao" and a.get("css_s100"):
            t = (a["css_s100"] + 4) * d / 100
        elif s == "ciclismo" and a.get("ftp_w"):
            t = d / bike_speed_kmh(a["ftp_w"] * bike_frac, w) * 3600
        elif s == "corrida" and a.get("run_vdot"):
            t = sp.predict_time(a["run_vdot"], d) * e["fatigue"]
        if t is None:
            missing.append(sp.SPORTS[s]["name"])
        else:
            total += t
        parts.append({"sport": s, "distance": d, "time": sp.fmt_duration(t) if t else None})
    trans = 120 if "supersprint" in ev["type"] or "sprint" in ev["type"] else 240 if "olimpico" in ev["type"] or "padrao" in ev["type"] else 480
    total += trans if not missing else 0
    tgt = sp.parse_duration(ev.get("target"))
    return {"name": e["name"], "date": ev.get("date"), "legs": parts, "transitions": sp.fmt_duration(trans),
            "predicted": sp.fmt_duration(total) if not missing else None, "missing_anchors": missing,
            "target": sp.fmt_duration(tgt) if tgt else None,
            "gap_s": round(total - tgt) if tgt and not missing else None}


def recent_volumes(sessions: list[dict], profile: dict, today: date | None = None, weeks: int = 4,
                   fixed_span: bool = False) -> dict:
    """Volume semanal médio das últimas semanas por modalidade (na unidade de cada uma)."""
    from . import load

    today = today or clock.today()
    since = today - timedelta(weeks=weeks)
    anchors = sp.anchors_of(profile)
    tot: dict[str, float] = {}
    first = None
    for s in sessions:
        try:
            d = datetime.fromisoformat(str(s.get("date"))[:19]).date()
        except ValueError:
            continue
        if d < since or d > today:
            continue
        first = d if first is None or d < first else first
        st = load.session_stress(s, anchors)
        for sport, km in st["km"].items():
            if sport in VOLUME and VOLUME[sport]["unit"] in ("km", "m"):
                tot[sport] = tot.get(sport, 0) + (km if VOLUME[sport]["unit"] == "km" else km * 1000)
        for sport, mins in st["minutes"].items():
            if sport in VOLUME and VOLUME[sport]["unit"] == "h":
                tot[sport] = tot.get(sport, 0) + mins / 60
    if not tot:
        return {}
    span = weeks if fixed_span else (max(1.0, min(weeks, ((today - first).days + 1) / 7)) if first else weeks)
    return {k: round(v / span, 2) for k, v in tot.items()}


def done_volumes(sessions: list[dict], profile: dict, ws: date) -> dict:
    """Por modalidade: média semanal das 3 semanas antes de `ws`, média das 3 anteriores a essas e se há registro."""
    avg3 = recent_volumes(sessions, profile, today=ws - timedelta(days=1), weeks=3, fixed_span=True)
    weeks = [recent_volumes(sessions, profile, today=ws - timedelta(days=1 + 7 * k), weeks=1, fixed_span=True) for k in range(3)]
    before = recent_volumes(sessions, profile, today=ws - timedelta(days=22), weeks=3, fixed_span=True)
    ever = recent_volumes(sessions, profile, today=ws - timedelta(days=1), weeks=8)
    out = {}
    for sport in set(avg3) | set(before) | set(ever):
        out[sport] = {"avg3": avg3.get(sport, 0.0), "max3": max(w.get(sport, 0.0) for w in weeks),
                      "weeks": [w.get(sport, 0.0) for w in weeks],
                      "before": before.get(sport, 0.0), "tracked": sport in ever}
    return out


def options() -> dict:
    env_pt = {"rua": "Rua", "esteira": "Esteira", "trilha": "Trilha", "pista": "Pista", "estrada": "Estrada",
              "rolo": "Rolo / smart trainer", "spinning": "Spinning (academia)", "mtb": "Mountain bike",
              "piscina": "Piscina", "aguas_abertas": "Águas abertas", "indoor": "Remo ergômetro", "agua": "Na água"}
    return {
        "sports": [{"id": k, "name": d["name"], "unit": VOLUME[k]["unit"], "sessions": VOLUME[k]["sessions"],
                    "anchor": d["anchor"], "envs": [{"id": e, "name": env_pt.get(e, e)} for e in d["envs"]],
                    "race_distances": [x for x, _ in RACE_PEAK.get(k, [])]}
                   for k, d in sp.SPORTS.items()],
        "events": [{"id": k, "name": v["name"], "legs": v["legs"]} for k, v in EVENTS.items()],
        "phases": PHASE_PT,
    }
