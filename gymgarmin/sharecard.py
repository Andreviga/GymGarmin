"""Cartões para postar o treino nas redes (Instagram, WhatsApp, Strava...), como os do Strava/Hevy.

O app monta só os DADOS do cartão; a imagem é desenhada no celular (canvas), em vários modelos e formatos.
Do Garmin, quando o treino veio do relógio, dá para buscar mais coisa que o resumo: o trajeto (mapa),
as parciais por km, o tempo em cada zona de frequência cardíaca e o clima. Isso é buscado uma vez, só
quando a pessoa abre o compartilhar, e fica guardado no treino.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import catalog, db, history, partners
from .muscles import MUSCLES, PRIMARY_WEIGHT, SECONDARY_WEIGHT, muscle_pt

# regiões do desenho do corpo (frente/costas) para o cartão de músculos
REGION = {
    "chest": "peito", "shoulders": "ombros", "biceps": "biceps", "triceps": "triceps", "forearms": "antebraco",
    "abdominals": "abdomen", "quadriceps": "quadriceps", "adductors": "adutores", "abductors": "gluteos",
    "calves": "panturrilha", "lats": "dorsais", "middle back": "costas_meio", "lower back": "lombar", "traps": "trapezio",
    "glutes": "gluteos", "hamstrings": "posterior", "neck": "trapezio",
}


# ------------------------------------------------------------------ Garmin ----

def decode_polyline(s: str) -> list[tuple[float, float]]:
    """Polilinha codificada do Google (é o formato que o Garmin devolve) → [(lat, lon)]."""
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift, res = 0, 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(res >> 1) if res & 1 else res >> 1)
        lat += vals[0]
        lon += vals[1]
        pts.append((lat / 1e5, lon / 1e5))
    return pts


def _simplify(pts: list[tuple[float, float]], n: int = 240) -> list[tuple[float, float]]:
    if len(pts) <= n:
        return pts
    step = len(pts) / n
    return [pts[int(k * step)] for k in range(n)] + [pts[-1]]


def route_shape(pts: list[tuple[float, float]]) -> list[list[float]] | None:
    """Trajeto normalizado em 0..1 (mantendo a proporção), sem coordenadas reais: o cartão mostra o desenho
    do percurso, não onde a pessoa mora (o começo e o fim exatos não saem do app)."""
    import math
    pts = [p for p in pts if p and abs(p[0]) <= 90 and abs(p[1]) <= 180]
    if len(pts) < 5:
        return None
    lat0 = sum(p[0] for p in pts) / len(pts)
    xy = [(p[1] * math.cos(math.radians(lat0)), -p[0]) for p in _simplify(pts)]
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    span = max(w, h)
    if span <= 0:
        return None
    ox, oy = min(xs) + w / 2, min(ys) + h / 2
    return [[round(0.5 + (x - ox) / span, 4), round(0.5 + (y - oy) / span, 4)] for x, y in xy]


def _splits(raw: dict | None) -> list[dict]:
    """Parciais por volta/km: [{km, pace_s}] (só as voltas com distância de verdade)."""
    laps = (raw or {}).get("lapDTOs") or []
    out = []
    for k, lap in enumerate(laps):
        d, t = lap.get("distance") or 0, lap.get("duration") or lap.get("movingDuration") or 0
        if d >= 200 and t > 0:
            out.append({"n": k + 1, "km": round(d / 1000, 2), "pace_s": round(t / (d / 1000))})
    return out[:30]


def _zones(raw) -> list[int] | None:
    rows = raw if isinstance(raw, list) else (raw or {}).get("hrTimeInZones") or []
    secs = [0] * 5
    for z in rows:
        n = (z.get("zoneNumber") or 0) - 1
        if 0 <= n < 5:
            secs[n] = round(z.get("secsInZone") or 0)
    return secs if any(secs) else None


def _weather(raw: dict | None) -> dict | None:
    if not raw:
        return None
    t = raw.get("temp")
    if t is None:
        return None
    c = round((t - 32) * 5 / 9) if raw.get("unitOfMeasure", "F") != "C" and t > 45 else round(t)  # o Garmin manda em °F
    desc = ((raw.get("weatherTypeDTO") or {}).get("desc") or "").strip()
    return {"temp_c": c, "desc": desc, "humidity": raw.get("relativeHumidity")}


EXTRAS_V = 2   # versão do que é buscado: guardado com versão antiga busca só o que falta


def _gear(api, aid: str) -> dict | None:
    """Tênis (ou bike) ligado à atividade no Garmin e quantos km ele já tem."""
    rows = api.get_activity_gear_v2(aid) or []
    rows = rows if isinstance(rows, list) else [rows]
    g = next((r for r in rows if isinstance(r, dict)), None)
    if not g:
        return None
    name = (g.get("displayName") or g.get("customMakeModel")
            or " ".join(x for x in (g.get("gearMakeName"), g.get("gearModelName")) if x and x != "Other").strip())
    if not name:
        return None
    out = {"name": name[:40], "type": (g.get("gearTypeName") or "").lower()}
    uuid = g.get("uuid") or g.get("gearPk")
    try:
        st = api.get_gear_stats(str(uuid)) if uuid else None
        if st and st.get("totalDistance"):
            out["km"] = round(st["totalDistance"] / 1000)
    except Exception:  # noqa: BLE001
        pass
    return out


def _photo_url(img: dict) -> str | None:
    return img.get("mediumUrl") or img.get("largeUrl") or img.get("url") or img.get("smallUrl")


def garmin_photo_bytes(activity_id, n: int) -> tuple[bytes, str]:
    """Foto n anexada à atividade no Garmin Connect. O link do Garmin vale 24 h: é pedido na hora e a foto passa
    pelo app (o cartão só consegue usar imagem do próprio app)."""
    import urllib.request
    from . import garmin_sync
    imgs = garmin_sync.client().get_activity_images(str(activity_id)) or []
    if not 0 <= n < len(imgs) or not _photo_url(imgs[n]):
        raise LookupError("Foto não encontrada")
    with urllib.request.urlopen(_photo_url(imgs[n]), timeout=15) as r:   # noqa: S310 - link do próprio Garmin
        data = r.read(4_000_000 + 1)
        mime = r.headers.get("Content-Type") or "image/jpeg"
    if len(data) > 4_000_000:
        raise ValueError("Foto do Garmin grande demais")
    return data, mime


def garmin_extras(activity_id, have: dict | None = None) -> dict:
    """Busca no Garmin o que o resumo da atividade não traz. Cada parte é opcional (falha não derruba o resto).
    `have`: o que já foi buscado antes (versão antiga): só completa o que falta."""
    from . import garmin_sync
    api = garmin_sync.client()
    aid = str(activity_id)
    out: dict = {**(have or {}), "v": EXTRAS_V}
    for key, fn in (("gear", lambda: _gear(api, aid)), ("photos", lambda: len(api.get_activity_images(aid) or []))):
        try:
            v = fn()
            if v:
                out[key] = v
        except Exception:  # noqa: BLE001
            pass
    if have is not None:
        return out
    try:
        poly = api.get_activity_polyline(aid) or {}
        enc = poly.get("gPolyline") or poly.get("polyline") or ""
        shape = route_shape(decode_polyline(enc)) if enc else None
        if shape:
            out["route"] = shape
    except Exception:  # noqa: BLE001
        pass
    for key, fn, conv in (("splits", api.get_activity_splits, _splits), ("hr_zones", api.get_activity_hr_in_timezones, _zones),
                          ("weather", api.get_activity_weather, _weather)):
        try:
            v = conv(fn(aid))
            if v:
                out[key] = v
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------ dados ----

def _muscles(s: dict) -> list[dict]:
    vol: dict[str, float] = {}
    for it in s.get("items") or []:
        ex = catalog.get(it.get("exercise_id")) or {}
        n = len([x for x in it.get("sets") or [] if x.get("done", True)]) if isinstance(it.get("sets"), list) else 0
        if not n or ex.get("kind") != "strength":
            continue
        for m in ex.get("primaryMuscles") or []:
            vol[m] = vol.get(m, 0) + n * PRIMARY_WEIGHT
        for m in ex.get("secondaryMuscles") or []:
            vol[m] = vol.get(m, 0) + n * SECONDARY_WEIGHT
    rows = [{"key": m, "name": muscle_pt(m), "sets": round(v, 1), "region": REGION.get(m)} for m, v in vol.items() if m in MUSCLES]
    return sorted(rows, key=lambda r: -r["sets"])


def _volume(s: dict) -> tuple[float, int]:
    kg, reps = 0.0, 0
    for it in s.get("items") or []:
        for x in it.get("sets") or [] if isinstance(it.get("sets"), list) else []:
            if x.get("done", True):
                kg += (x.get("load_kg") or 0) * (x.get("reps") or 0)
                reps += x.get("reps") or 0
    return round(kg), reps


def _week(uid_sessions: list[dict], day: date) -> dict:
    ws = day - timedelta(days=day.weekday())
    days = {str(x.get("date"))[:10] for x in uid_sessions if str(x.get("source")) != "passos"}
    n_week = len([d for d in days if ws.isoformat() <= d <= (ws + timedelta(days=6)).isoformat()])
    streak, w = 0, ws
    while any(w.isoformat() <= d <= (w + timedelta(days=6)).isoformat() for d in days):   # semanas seguidas treinando
        streak += 1
        w -= timedelta(days=7)
    return {"days": n_week, "streak_weeks": streak}


def _period(iso: str) -> tuple[str, str]:
    """(feminino, masculino) do período do dia, como o Strava ("Corrida matinal", "Treino noturno")."""
    try:
        h = int(str(iso)[11:13])
    except ValueError:
        return "", ""
    return ("matinal", "matinal") if h < 12 else ("da tarde", "da tarde") if h < 18 else ("noturna", "noturno")


def _pr_txt(p: dict) -> str:
    b = p.get("best_set") or {}
    if isinstance(b, dict) and b.get("load_kg"):
        return f"{b.get('reps') or '?'} × {b['load_kg']:g} kg"
    if p.get("max_reps"):
        return f"{p['max_reps']} repetições"
    if p.get("max_duration_s"):
        return f"{p['max_duration_s']} s"
    return ""


def build(s: dict, weight_kg: float | None = None, name: str | None = None, extras: dict | None = None) -> dict:
    v = partners._session_view(s, weight_kg, detail=True)
    sessions = db.list_sessions(limit=400)
    try:
        prs = history.session_prs(sessions, s, weight_kg)
    except Exception:  # noqa: BLE001
        prs = []
    strength = [i for i in v["items"] if i["kind"] == "forca"]
    endu_items = [it for it in s.get("items") or [] if it.get("sport") or it.get("distance_km")]
    out = {"id": s.get("id"), "title": v["title"], "date": v["date"], "who": name, "minutes": v["minutes"],
           "kcal": v["kcal"], "avg_hr": None, "max_hr": None, "watch": v["watch"], "week": _week(sessions, date.fromisoformat(v["date"][:10]))}
    summ = s.get("garmin_summary") or {}
    out["avg_hr"] = summ.get("avg_hr") or next((it.get("avg_hr") for it in s.get("items") or [] if it.get("avg_hr")), None)
    out["max_hr"] = summ.get("max_hr") or next((it.get("max_hr") for it in s.get("items") or [] if it.get("max_hr")), None)
    if strength:
        kg, reps = _volume(s)
        exs = []
        for it in strength:
            best = max(it.get("sets") or [], key=lambda x: (x.get("load_kg") or 0, x.get("reps") or 0, x.get("duration_s") or 0), default=None)
            b = None
            if best:
                b = (f"{best.get('reps') or '?'} × {best['load_kg']:g} kg" if best.get("load_kg")
                     else f"{best['duration_s']} s" if best.get("duration_s") and not best.get("reps") else f"{best.get('reps') or '?'} reps")
            exs.append({"name": it["name"], "sets": it["n"], "best": b})
        out["strength"] = {"sets": v["sets"], "reps": reps, "volume_kg": kg, "exercises": exs, "muscles": _muscles(s),
                           "prs": [{"name": p["name"], "what": _pr_txt(p)} for p in prs][:4]}
    if endu_items:
        it = max(endu_items, key=lambda x: x.get("distance_km") or 0)
        e = {k: it.get(k) for k in ("sport", "distance_km", "pace_s_km", "speed_kmh", "pace_s_100", "elev_m", "te_aerobic",
                                    "cadence", "duration_min", "avg_hr", "max_hr") if it.get(k) is not None}
        e["name"] = (catalog.get(it.get("exercise_id")) or {}).get("name") or it.get("_name")
        e.update(extras or {})
        out["endurance"] = e
    if not s.get("day_name"):   # treino sem nome (relógio, treino livre): "Corrida matinal", "Musculação da tarde"
        from .sports import SPORTS
        fem, masc = _period(v["date"])
        sport = (out.get("endurance") or {}).get("sport")
        base = (SPORTS.get(sport) or {}).get("name") if sport else ("Musculação" if strength else None)
        if base:
            out["title"] = f"{base} {fem if base in ('Corrida', 'Natação', 'Caminhada', 'Musculação', 'Trilha / hiking') else masc}".strip()
    return out
