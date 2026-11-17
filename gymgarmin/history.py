"""Histórico de evolução de cargas por exercício.

Trabalha só com as sessões registradas (app ou Garmin). Para cada exercício de
força, cada sessão vira um ponto com: carga máxima, melhor série, 1RM estimado
(Epley), volume (carga × reps) e reps totais. Daí saem os recordes (PRs), a
tendência das últimas semanas e o aviso de estagnação.

Exercícios sem carga (peso corporal) acompanham reps máximas; isométricos
(prancha etc.) acompanham o tempo máximo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import catalog, clock

# Acima disso a fórmula de Epley superestima bastante; o ponto continua no
# gráfico, mas marcado como "estimativa fraca".
E1RM_MAX_REPS = 12


def e1rm(load: float, reps: int, rpe=None) -> float | None:
    """1RM estimado (Epley). Com RPE, soma as reps que ficaram "no tanque" (RPE 8 = 2 reps):
    uma série leve de deload não parece queda e uma série até a falha não parece igual a uma folgada."""
    if not load or not reps or reps <= 0:
        return None
    try:
        r = float(rpe)
        rir = 10 - r if 6 <= r <= 10 else 0
    except (TypeError, ValueError):
        rir = 0
    eff = reps + rir
    if eff <= 1:
        return round(load, 1)
    return round(load * (1 + eff / 30), 1)


def bracket(reps: int) -> int:
    """Faixa de reps (1–5, 6–8, 9–12, 13+): o 1RM estimado de faixas diferentes não é comparável para todo mundo."""
    return 0 if reps <= 5 else 1 if reps <= 8 else 2 if reps <= 12 else 3


# exercícios com o peso do corpo em que dá para pendurar anilha: 1RM = corpo + peso extra
LOADABLE_BODYWEIGHT = {"Pullups", "Chin-Up", "Dips_-_Triceps_Version", "Dips_-_Chest_Version", "Wide-Grip_Pulldown_Behind_The_Neck",
                       "Weighted_Pull_Ups", "Pushups", "Inverted_Row", "Parallel_Bar_Dip"}
WATCH_ONLY_IDS = {"Garmin_desconhecido", "Garmin_musculacao"}  # sem exercício real
IRREGULAR_PCT_WEEK = 15.0  # %/semana acima disso é erro de dado, não treino
DEFAULT_BW = 75.0


def _day(iso: str) -> date:
    return datetime.fromisoformat(str(iso)[:19]).date()


def _work_sets(item: dict) -> list[dict]:
    """Séries efetivas: feitas (ou sem marcação, como as do Garmin) e sem aquecimento."""
    out = []
    for s in item.get("sets") or []:
        if not s.get("done", True) or s.get("warmup"):
            continue
        reps = int(s.get("reps") or 0)
        dur = int(s.get("duration_s") or 0)
        if reps <= 0 and dur <= 0:
            continue
        out.append({"reps": reps, "load_kg": float(s.get("load_kg") or 0), "duration_s": dur,
                    "rpe": s.get("rpe")})
    return out


def _metric(sets: list[dict]) -> str:
    if any(s["load_kg"] > 0 and s["reps"] > 0 for s in sets):
        return "carga"
    if any(s["reps"] > 0 for s in sets):
        return "reps"
    return "tempo"


def session_point(session: dict, item: dict, bw: float | None = None) -> dict | None:
    """`bw` = peso do corpo quando o exercício é de peso corporal com anilha (o 1RM inclui o corpo)."""
    sets = _work_sets(item)
    if not sets:
        return None
    metric = "carga" if bw else _metric(sets)
    eff = (lambda s: s["load_kg"] + bw) if bw else (lambda s: s["load_kg"])
    loaded = [s for s in sets if eff(s) > 0 and s["reps"] > 0]
    best = None
    if loaded:
        best = max(loaded, key=lambda s: (e1rm(eff(s), s["reps"], s.get("rpe")) or 0, s["load_kg"]))
    rpes = [float(s["rpe"]) for s in sets if s.get("rpe") not in (None, "")]
    point = {
        "session_id": session.get("id"),
        "date": str(session.get("date"))[:10],
        "source": session.get("source") or "app",
        "sets": len(sets),
        "reps_total": sum(s["reps"] for s in sets),
        "top_load": max((s["load_kg"] for s in loaded), default=0.0),
        "volume": round(sum(s["load_kg"] * s["reps"] for s in sets), 1),
        "max_reps": max((s["reps"] for s in sets), default=0),
        "max_duration_s": max((s["duration_s"] for s in sets), default=0),
        "best_set": {"load_kg": best["load_kg"], "reps": best["reps"]} if best else None,
        "e1rm": e1rm(eff(best), best["reps"], best.get("rpe")) if best else None,
        "bracket": bracket(best["reps"]) if best else None,
        "bodyweight": bw,
        # carga máxima por número de reps (para o recorde "carga × reps")
        "rep_loads": {str(r): max(x["load_kg"] for x in sets if x["reps"] >= r)
                      for r in sorted({x["reps"] for x in sets if x["load_kg"] > 0})} if any(x["load_kg"] > 0 for x in sets) else {},
        "e1rm_weak": bool(best and best["reps"] > E1RM_MAX_REPS),
        "avg_rpe": round(sum(rpes) / len(rpes), 1) if rpes else None,
        "metric": metric,
        "detail": [{k: s[k] for k in ("reps", "load_kg", "duration_s")} for s in sets],
    }
    point["value"] = _value(point, metric)
    return point


def _value(p: dict, metric: str) -> float:
    """Número principal do gráfico para cada tipo de exercício."""
    if metric == "carga":
        return p["e1rm"] or p["top_load"]
    if metric == "reps":
        return p["max_reps"]
    return p["max_duration_s"]


def _points_by_exercise(sessions: list[dict], bodyweight: float | None = None) -> dict[str, list[dict]]:
    # barra fixa, paralelas…: se algum dia teve anilha, a série inteira passa a contar corpo + peso
    loaded_bw = {it.get("exercise_id") for s in sessions for it in s.get("items") or []
                 if it.get("exercise_id") in LOADABLE_BODYWEIGHT and any(float(x.get("load_kg") or 0) > 0 for x in it.get("sets") or [])}
    by_ex: dict[str, list[dict]] = {}
    for s in sorted(sessions, key=lambda x: str(x.get("date"))):
        for it in s.get("items") or []:
            eid = it.get("exercise_id")
            if not eid or not it.get("sets") or eid in WATCH_ONLY_IDS:
                continue
            p = session_point(s, it, (bodyweight or DEFAULT_BW) if eid in loaded_bw else None)
            if p:
                by_ex.setdefault(eid, []).append(p)
    # Um mesmo exercício duas vezes no dia (ex.: duas sessões) vira um ponto só
    for eid, pts in by_ex.items():
        merged: dict[str, dict] = {}
        for p in pts:
            m = merged.get(p["date"])
            merged[p["date"]] = p if m is None else _merge(m, p)
        by_ex[eid] = list(merged.values())
        _mark_suspects(by_ex[eid])
    return by_ex


def _mark_suspects(points: list[dict]) -> None:
    """Valor fora da curva que os treinos vizinhos não confirmam (600 kg digitado no lugar de 60,
    exercício trocado pelo relógio): fica no histórico marcado, mas fora de recordes e tendência.

    Também fica de fora o treino de um exercício com carga em que nenhuma série teve peso
    (o relógio grava 0 quando a carga não é digitada) — misturar reps com kg no mesmo gráfico
    dá saltos absurdos."""
    for p in points:
        p["suspect"] = False
        p.pop("suspect_why", None)
    if any(p["metric"] == "carga" for p in points):
        for p in points:
            if p["metric"] != "carga":
                p["suspect"], p["suspect_why"] = True, "sem_carga"
    valid = [p for p in points if not p["suspect"]]
    vals = [p["value"] for p in valid]
    flagged = []
    for i, p in enumerate(valid):
        nb = [v for v in vals[max(0, i - 3):i] + vals[i + 1:i + 3] if v]
        if len(nb) < 2 or not p["value"]:
            continue
        nb.sort()
        ref = nb[len(nb) // 2]
        if p["value"] > 1.4 * ref or p["value"] < 0.6 * ref:
            flagged.append(p)
    # muitos "fora da curva" = a série toda é irregular (o relógio mistura exercícios): não dá para
    # dizer qual está errado, então nenhum é descartado
    if len(flagged) * 3 > len(valid):
        return
    for p in flagged:
        p["suspect"], p["suspect_why"] = True, "fora_da_curva"


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    out["sets"] = a["sets"] + b["sets"]
    out["reps_total"] = a["reps_total"] + b["reps_total"]
    out["volume"] = round(a["volume"] + b["volume"], 1)
    out["detail"] = a["detail"] + b["detail"]
    for k in ("top_load", "max_reps", "max_duration_s"):
        out[k] = max(a[k], b[k])
    if (b["e1rm"] or 0) > (a["e1rm"] or 0):
        out.update(e1rm=b["e1rm"], best_set=b["best_set"], e1rm_weak=b["e1rm_weak"])
    if a["metric"] != b["metric"]:
        out["metric"] = "carga" if "carga" in (a["metric"], b["metric"]) else "reps"
    out["value"] = _value(out, out["metric"])
    rl = dict(a.get("rep_loads") or {})
    for k, v in (b.get("rep_loads") or {}).items():
        rl[k] = max(rl.get(k, 0), v)
    out["rep_loads"] = rl
    return out


PR_KINDS = (("e1rm", "1RM estimado"), ("top_load", "carga máxima"), ("rep_load", "carga × reps"), ("volume", "volume"),
            ("max_reps", "reps"), ("max_duration_s", "tempo"))


def _mark_prs(points: list[dict]) -> None:
    """Marca em cada ponto quais recordes ele bateu (o primeiro treino não conta)."""
    best: dict[str, float] = {}
    best_br: dict[int, float] = {}      # melhor 1RM estimado por faixa de reps
    rep_best: dict[int, float] = {}     # maior carga já feita para pelo menos N reps
    ok = [p for p in points if not p.get("suspect")]
    metric = ok[-1]["metric"] if ok else (points[-1]["metric"] if points else "carga")
    relevant = {"carga": ("e1rm", "top_load", "volume"), "reps": ("max_reps", "volume"),
                "tempo": ("max_duration_s",)}[metric]
    for i, p in enumerate(points):
        p["prs"] = []
        if p.get("suspect"):
            continue
        for key, _label in PR_KINDS:
            if key not in relevant:
                continue
            v = p.get(key) or 0
            if v <= 0:
                continue
            if i > 0 and v > best.get(key, 0) + 1e-9:
                # 1RM estimado: só vale contra a mesma faixa de reps (trocar 5 por 10 reps não é ficar mais forte)
                if key != "e1rm" or (p.get("bracket") in best_br and v > best_br[p["bracket"]] + 1e-9):
                    p["prs"].append(key)
            best[key] = max(best.get(key, 0), v)
        if p.get("e1rm") and p.get("bracket") is not None:
            best_br[p["bracket"]] = max(best_br.get(p["bracket"], 0), p["e1rm"])
        if metric == "carga" and not p.get("bodyweight"):
            beat = [int(r) for r, ld in (p.get("rep_loads") or {}).items()
                    if i > 0 and int(r) in rep_best and ld > rep_best[int(r)] + 1e-9]
            # só anuncia se não for já um recorde de carga máxima (evita repetir a mesma conquista)
            if beat and "top_load" not in p["prs"]:
                r = max(beat)
                p["prs"].append("rep_load")
                p["rep_pr"] = {"reps": r, "load_kg": p["rep_loads"][str(r)]}
            for r, ld in (p.get("rep_loads") or {}).items():
                for rr in range(1, int(r) + 1):
                    rep_best[rr] = max(rep_best.get(rr, 0), ld)


def _slope_per_week(points: list[dict]) -> float | None:
    """Inclinação (regressão linear) do valor principal, em unidades por semana."""
    if len(points) < 3:
        return None
    xs = [(_day(p["date"]) - _day(points[0]["date"])).days / 7 for p in points]
    ys = [p["value"] for p in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def _pct_per_week(points: list[dict], metric: str) -> float | None:
    """%/semana. Com troca de faixa de reps no meio (blocos de 5, 10, 3 reps), calcula a tendência dentro de
    cada faixa e combina — o 1RM estimado de 10 reps não é comparável ao de 3 reps para todo mundo."""
    groups: dict = {}
    for p in points:
        groups.setdefault(p.get("bracket") if metric == "carga" else 0, []).append(p)
    num = den = 0.0
    for g in groups.values():
        if len(g) < 2 or (_day(g[-1]["date"]) - _day(g[0]["date"])).days < 7:
            continue
        sl = _slope_per_week(g) if len(g) >= 3 else (g[-1]["value"] - g[0]["value"]) / ((_day(g[-1]["date"]) - _day(g[0]["date"])).days / 7)
        mean = sum(p["value"] for p in g) / len(g)
        if sl is None or not mean:
            continue
        w = len(g) - 1
        num += w * 100 * sl / mean
        den += w
    return round(num / den, 2) if den else None


def summary(points: list[dict], today: date | None = None) -> dict:
    today = today or clock.today()
    if not points:
        return {"sessions": 0}
    suspects = [p["date"] for p in points if p.get("suspect")]
    n_all = len(points)
    points = [p for p in points if not p.get("suspect")]
    if not points:
        return {"sessions": n_all, "status": "inicio", "metric": "carga", "suspect_dates": suspects,
                "last_date": max(suspects), "first_date": min(suspects), "last_value": None, "first_value": None,
                "best": {"e1rm": 0, "top_load": 0, "volume": 0, "max_reps": 0, "max_duration_s": 0},
                "change_total_pct": None, "change_30d_pct": None, "change_90d_pct": None,
                "message": "Nenhum treino com carga registrada ainda: digite o peso usado para acompanhar a evolução."}
    metric = points[-1]["metric"]
    first, last = points[0], points[-1]
    recent = [p for p in points if _day(p["date"]) >= today - timedelta(weeks=8)]
    base = (sum(p["value"] for p in recent) / len(recent)) if recent else last["value"]
    pct_week = _pct_per_week(recent, metric)
    slope = pct_week * base / 100 if pct_week is not None and base else None

    def best_of(key):
        return max((p.get(key) or 0 for p in points), default=0)

    def change_since(days):
        ref = [p for p in points if _day(p["date"]) <= today - timedelta(days=days)]
        if not ref or not ref[-1]["value"]:
            return None
        return round(100 * (last["value"] - ref[-1]["value"]) / ref[-1]["value"], 1)

    last_pr = next((p["date"] for p in reversed(points) if p.get("prs")), None)
    since_pr = sum(1 for _ in _after(points, last_pr)) if last_pr else len(points) - 1

    tail_pct = _pct_per_week(points[-5:], metric)

    if len(points) < 3:
        status, msg = "inicio", "Poucos registros ainda: a tendência aparece a partir de 3 treinos."
    elif pct_week is not None and abs(pct_week) > IRREGULAR_PCT_WEEK:
        # ninguém ganha (ou perde) 15% de força por semana: são cargas trocadas, faltando ou de outro exercício
        status, msg = "irregular", ("As cargas variam demais entre os treinos para indicar uma tendência "
                                    "(comum quando o relógio detecta o exercício sozinho ou o peso não foi digitado). "
                                    "Confira as séries no Garmin Connect ou registre pelo app.")
        pct_week = slope = None
    elif pct_week is not None and pct_week <= -1:
        status, msg = "caindo", "Queda nas últimas semanas: confira sono, alimentação e se não é hora de deload."
    elif pct_week is not None and pct_week >= 0.5 and (since_pr < 4 or (tail_pct is not None and tail_pct >= 0.5)):
        # subindo nas últimas semanas (inclusive voltando de uma pausa, ainda abaixo do recorde antigo)
        status, msg = "progredindo", f"Subindo cerca de {str(round(pct_week, 1)).replace('.', ',')}% por semana nas últimas 8 semanas."
    elif since_pr >= 4 and (tail_pct is None or tail_pct < 0.5):
        status, msg = "estagnado", (f"{since_pr} treinos sem recorde. Experimente mudar a faixa de reps, "
                                     "trocar por uma variação ou fazer uma semana de deload.")
    elif pct_week is not None and pct_week >= 0.5:
        status, msg = "progredindo", f"Subindo cerca de {str(round(pct_week, 1)).replace('.', ',')}% por semana nas últimas 8 semanas."
    else:
        status, msg = "estavel", "Estável nas últimas semanas."

    return {
        "sessions": len(points),
        "metric": metric,
        "first_date": first["date"],
        "last_date": last["date"],
        "first_value": first["value"],
        "last_value": last["value"],
        "best": {"e1rm": best_of("e1rm"), "top_load": best_of("top_load"), "volume": best_of("volume"),
                 "max_reps": best_of("max_reps"), "max_duration_s": best_of("max_duration_s")},
        "change_total_pct": round(100 * (last["value"] - first["value"]) / first["value"], 1)
        if first["value"] else None,
        "change_30d_pct": change_since(30),
        "change_90d_pct": change_since(90),
        "slope_per_week": round(slope, 2) if slope is not None else None,
        "pct_per_week": pct_week,
        "last_pr_date": last_pr,
        "sessions_since_pr": since_pr,
        "status": status,
        "message": msg,
        "suspect_dates": suspects,
        "bodyweight": last.get("bodyweight"),
    }


def _after(points: list[dict], iso: str | None):
    return (p for p in points if iso and p["date"] > iso)


def _ex_info(eid: str) -> dict:
    ex = catalog.get(eid) or {}
    return {"exercise_id": eid, "name": ex.get("name") or eid,
            "primary_pt": ex.get("primary_pt") or [], "images": (ex.get("images") or [])[:1]}


def exercise_history(sessions: list[dict], exercise_id: str, today: date | None = None, bodyweight: float | None = None) -> dict:
    points = _points_by_exercise(sessions, bodyweight).get(exercise_id, [])
    _mark_prs(points)
    return {**_ex_info(exercise_id), "points": points, "summary": summary(points, today)}


def overview(sessions: list[dict], today: date | None = None, bodyweight: float | None = None) -> dict:
    """Lista de exercícios com histórico, do mais recente para o mais antigo, e os recordes recentes."""
    today = today or clock.today()
    rows, recent_prs = [], []
    for eid, pts in _points_by_exercise(sessions, bodyweight).items():
        _mark_prs(pts)
        sm = summary(pts, today)
        spark = [p["value"] for p in pts if not p.get("suspect")][-12:]
        rows.append({**_ex_info(eid), "summary": sm, "spark": spark})
        for p in pts:
            if p["prs"] and _day(p["date"]) >= today - timedelta(days=30):
                recent_prs.append({"exercise_id": eid, "name": rows[-1]["name"], "date": p["date"],
                                   "prs": p["prs"], "best_set": p["best_set"], "value": p["value"],
                                   "max_reps": p["max_reps"], "max_duration_s": p["max_duration_s"],
                                   "metric": p["metric"], "rep_pr": p.get("rep_pr")})
    rows.sort(key=lambda r: r["summary"].get("last_date") or "", reverse=True)
    recent_prs.sort(key=lambda r: r["date"], reverse=True)
    return {"exercises": rows, "recent_prs": recent_prs[:20]}


def session_prs(sessions: list[dict], session: dict, bodyweight: float | None = None) -> list[dict]:
    """Recordes batidos por uma sessão recém-salva (para comemorar no fim do treino)."""
    day = str(session.get("date"))[:10]
    out = []
    ids = {it.get("exercise_id") for it in session.get("items") or []}
    by_ex = _points_by_exercise(sessions, bodyweight)
    for eid in ids:
        pts = by_ex.get(eid) or []
        _mark_prs(pts)
        for p in pts:
            if p["date"] == day and p["prs"]:
                out.append({"exercise_id": eid, "name": _ex_info(eid)["name"], "prs": p["prs"], "rep_pr": p.get("rep_pr"),
                            "best_set": p["best_set"], "e1rm": p["e1rm"], "max_reps": p["max_reps"],
                            "max_duration_s": p["max_duration_s"], "metric": p["metric"]})
    return out
