"""Dados de outros relógios: Apple Saúde (pelo app Atalhos do iPhone) e Health Connect (app Android).

Os dois mandam o mesmo JSON para /api/ingest, com o token pessoal da conta:

    {
      "source": "apple" | "android",
      "days": [{"date": "2026-11-17", "sleep_h": 6.4, "deep_min": 55, "rem_min": 80, "light_min": 230,
                "awake_min": 20, "sleep_start": "22:40", "sleep_end": "05:10", "rhr": 48, "hrv": 52,
                "steps": 9300, "resp": 15, "spo2": 96, "vo2max": 41.5, "weight_kg": 98}],
      "workouts": [{"id": "abc", "type": "Corrida", "start": "2026-11-17T06:00:00-03:00",
                    "duration_min": 32, "distance_km": 5.1, "kcal": 380, "avg_hr": 141, "max_hr": 168}]
    }

Campos faltando são ignorados; números podem vir como texto ("6,4", "48 bpm", "32 min") porque o
Atalhos converte tudo em texto. Também aceita um dia só no topo ({"date": ..., "sleep_h": ...}).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from . import clock, db, garmin_sync

MAX_WORKOUTS = 60


# --------------------------------------------------------------- números
def num(v, integer: bool = False) -> float | None:
    """'6,4 h' → 6.4; '48 bpm' → 48; '1:32:10' → minutos; None/'' → None.
    `integer` (passos, calorias): '9.312' é nove mil (separador de milhar do Atalhos em português)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if m:  # h:mm(:ss) → minutos
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        return h * 60 + mi + se / 60 if m.group(3) is not None else h * 60 + mi
    s = s.replace(" ", " ")
    m = re.search(r"-?\d[\d.,]*", s)
    if not m:
        return None
    t = m.group(0)
    if integer and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):
        return float(t.replace(".", ""))
    if "," in t and "." in t:  # 1.234,5 ou 1,234.5
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def minutes(v) -> float | None:
    """Duração em minutos: aceita '45 min', '1 h 5 min', '0:45:00', segundos grandes ('2700 s')."""
    if v is None or v == "":
        return None
    s = str(v).lower()
    h = re.search(r"(\d+[.,]?\d*)\s*h", s)
    mi = re.search(r"(\d+[.,]?\d*)\s*min", s)
    if h or mi:
        tot = 0.0
        if h:
            tot += (num(h.group(1)) or 0) * 60
        if mi:
            tot += num(mi.group(1)) or 0
        return tot
    if re.search(r"\d\s*s(eg)?\b", s):
        x = num(s)
        return x / 60 if x is not None else None
    return num(v)


def parse_dt(v) -> datetime | None:
    if not v:
        return None
    s = str(v).strip().replace(" ", "T", 1) if re.match(r"\d{4}-\d{2}-\d{2} \d", str(v)) else str(v).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:  # horário local do app (America/Sao_Paulo)
        dt = dt.astimezone(clock.now().tzinfo).replace(tzinfo=None)
    return dt


def _hm(v) -> str | None:
    if not v:
        return None
    dt = parse_dt(v)
    if dt:
        return dt.strftime("%H:%M")
    m = re.search(r"(\d{1,2}):(\d{2})", str(v))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


# --------------------------------------------------------------- tipos de treino
# palavra-chave (pt/en, Apple e Health Connect) → tipo do Garmin (reaproveita a importação do Garmin)
TYPE_RULES = [
    (("treadmill", "esteira", "running_treadmill"), "treadmill_running"),
    (("trail",), "trail_running"),
    (("run", "corrid", "corrend"), "running"),
    (("stationary", "indoor cycl", "indoor_cycl", "spinning", "ergom", "biking_stationary", "ciclismo indoor",
      "ciclismo em local fechado"), "indoor_cycling"),
    (("mountain", "mtb"), "mountain_biking"),
    (("cycl", "bik", "cicl", "pedal", "bicic"), "cycling"),
    (("hik", "trilha", "montanha"), "hiking"),
    (("walk", "caminh"), "walking"),
    (("open water", "open_water", "águas abertas", "aguas abertas"), "open_water_swimming"),
    (("swim", "nata", "nado", "piscina"), "lap_swimming"),
    (("rowing_machine", "rowing machine", "remo ergom", "indoor row"), "indoor_rowing"),
    (("row", "remo"), "rowing"),
    (("ellip", "elípt", "elipt", "transport"), "elliptical"),
    (("stair", "escada"), "stair_climbing"),
    (("strength", "força", "forca", "muscula", "weight", "peso", "funcional", "functional", "crossfit",
      "cross training", "core"), "strength_training"),
]


def garmin_type(t) -> str | None:
    s = str(t or "").lower().replace("hkworkoutactivitytype", "").replace("exercise_type_", "")
    for keys, gtype in TYPE_RULES:
        if any(k in s for k in keys):
            return gtype
    from . import garmin_sync

    key = re.sub(r"[^a-z0-9à-ú]+", "_", s).strip("_")
    return key if garmin_sync.activity_kind(key) else None  # futebol, ioga, tênis…: atividade genérica


# --------------------------------------------------------------- saúde do dia
def _day_record(d: dict) -> tuple[str, dict, dict] | None:
    day = str(d.get("date") or "")[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    rec: dict = {}
    sleep: dict = {}
    sh = num(d.get("sleep_h"))
    if sh is None and d.get("sleep_min") is not None:
        sh = (minutes(d.get("sleep_min")) or 0) / 60 or None
    if sh is not None and sh > 24:  # veio em minutos
        sh = sh / 60
    if sh:
        sleep["duration_h"] = round(sh, 2)
        stages = {k: minutes(d.get(f"{k}_min")) for k in ("deep", "rem", "light", "awake")}
        asleep = sum(v for k, v in stages.items() if k != "awake" and v) or sh * 60
        for k in ("deep", "rem", "light"):
            if stages[k] is not None and asleep:
                sleep[f"{k}_pct"] = round(100 * stages[k] / asleep, 1)
        if stages["awake"] is not None:
            sleep["awake_min"] = round(stages["awake"])
        for k in ("start", "end"):
            hm = _hm(d.get(f"sleep_{k}"))
            if hm:
                sleep[k] = hm
        if num(d.get("sleep_score")) is not None:
            sleep["score"] = num(d.get("sleep_score"))
    if sleep:
        rec["sleep"] = sleep
    rhr = num(d.get("rhr"))
    if rhr and 25 <= rhr <= 130:
        rec["rhr"] = round(rhr)
    hrv = num(d.get("hrv"))
    if hrv and 5 <= hrv <= 300:
        # Apple mede SDNN; Garmin e Health Connect, RMSSD: não comparáveis entre si, só com o próprio histórico
        rec["hrv"] = {"last_night": round(hrv, 1), "kind": "sdnn" if d.get("hrv_kind") == "sdnn" else d.get("hrv_kind") or None}
        rec["hrv"] = {k: v for k, v in rec["hrv"].items() if v is not None}
    for k, lim in (("steps", (0, 150000)), ("resp", (5, 40)), ("spo2", (70, 100)), ("kcal_active", (0, 10000)),
                   ("int_mod", (0, 1440)), ("int_vig", (0, 1440))):
        v = num(d.get(k), integer=k in ("steps", "kcal_active", "int_mod", "int_vig"))
        if v is not None and lim[0] <= v <= lim[1]:
            rec[k] = round(v) if k in ("steps", "kcal_active", "int_mod", "int_vig") else round(v, 1)
    weekly = {}
    for k, lim in (("vo2max", (15, 90)), ("weight_kg", (25, 300))):
        v = num(d.get(k))
        if v is not None and lim[0] <= v <= lim[1]:
            weekly[k] = round(v, 1)
    return day, rec, weekly


def save_days(days: list[dict], source: str) -> list[str]:
    today = clock.today()
    saved = []
    wk_new: dict = {}
    for d in days:
        r = _day_record(d)
        if not r:
            continue
        day, rec, weekly = r
        if weekly:
            wk_new = {**wk_new, **weekly, "date": day}
        if not rec:
            continue
        cur = json.loads(db.kv_get(f"health:{day}") or "{}")
        merged = {**cur, **rec, "date": day, "source": source, "fetched_at": clock.now().isoformat(timespec="minutes")}
        if cur.get("sleep") and rec.get("sleep"):
            merged["sleep"] = {**cur["sleep"], **rec["sleep"]}
        merged["final"] = date.fromisoformat(day) < today - timedelta(days=1)
        db.kv_set(f"health:{day}", json.dumps(merged))
        saved.append(day)
    if wk_new:
        wk = json.loads(db.kv_get("health_weekly") or "{}")
        hist = [h for h in wk.get("history") or [] if h.get("date") != wk_new["date"]]
        hist.append({k: wk_new.get(k) for k in ("date", "vo2max", "weight_kg")})
        db.kv_set("health_weekly", json.dumps({**wk, **wk_new, "history": hist[-60:], "source": source}))
    if saved:
        idx = json.loads(db.kv_get("health_index") or "{}")
        for day in saved:
            idx[day] = "final"
        db.kv_set("health_index", json.dumps(idx))
    return saved


# --------------------------------------------------------------- treinos
def workout_session(w: dict, source: str, hr_max: int | None) -> dict | None:
    start = parse_dt(w.get("start") or w.get("start_date") or w.get("date"))
    dur = minutes(w.get("duration_min") if w.get("duration_min") is not None else w.get("duration"))
    if dur is None and start and w.get("end"):
        end = parse_dt(w.get("end"))
        dur = (end - start).total_seconds() / 60 if end else None
    if not start or not dur or dur < 3 or dur > 24 * 60:
        return None
    gtype = garmin_type(w.get("type") or w.get("name"))
    if not gtype:
        return None
    dist = num(w.get("distance_km"))
    if dist is None and w.get("distance_m") is not None:
        dist = (num(w.get("distance_m")) or 0) / 1000
    act = {"activityType": {"typeKey": gtype}, "duration": dur * 60, "averageHR": num(w.get("avg_hr")),
           "maxHR": num(w.get("max_hr")), "calories": num(w.get("kcal"), integer=True)}
    if dist:
        act["distance"] = dist * 1000
    act = {k: v for k, v in act.items() if v not in (None, 0)}
    item = garmin_sync.endurance_item(act, hr_max)
    if not item and gtype == "strength_training":
        item = {"exercise_id": garmin_sync.STRENGTH_GENERIC, "_name": "Musculação (relógio)", "strength_min": round(dur, 1),
                "duration_min": round(dur, 1), "zone": garmin_sync._zone_from_hr(act.get("averageHR"), hr_max)}
        for k_src, k in (("averageHR", "avg_hr"), ("maxHR", "max_hr"), ("calories", "kcal")):
            if act.get(k_src):
                item[k] = act[k_src]
    if not item:
        return None
    ext = str(w.get("id") or "") or f"{start.isoformat(timespec='minutes')}|{gtype}"
    summ = {k: act.get(src) for src, k in (("averageHR", "avg_hr"), ("maxHR", "max_hr"), ("calories", "kcal")) if act.get(src)}
    summ["duration_min"] = round(dur, 1)
    return {"date": start.isoformat(timespec="seconds"), "source": source, "ext_id": f"{source}:{ext}",
            "notes": str(w.get("name") or w.get("type") or "")[:60] or None, "items": [item],
            "duration_min": round(dur, 1), "garmin_summary": summ}


DELETED_KEY = "ingest_deleted"


def deleted_ids() -> list[str]:
    try:
        d = json.loads(db.kv_get(DELETED_KEY) or "[]")
        return d if isinstance(d, list) else []
    except ValueError:
        return []


def forget_workout(ext_id: str | None) -> None:
    if not ext_id or str(ext_id).startswith("passos:"):
        return
    db.kv_set(DELETED_KEY, json.dumps(([x for x in deleted_ids() if x != ext_id] + [ext_id])[-300:]))


def save_workouts(workouts: list[dict], source: str, hr_max: int | None, merge=None) -> tuple[list[dict], int]:
    since = (clock.today() - timedelta(days=60)).isoformat()
    known = {s.get("ext_id") for s in db.list_sessions(since=since, limit=2000) if s.get("ext_id")}
    known |= set(deleted_ids())  # treino do celular que a pessoa apagou no app não volta no próximo envio
    imported, skipped = [], 0
    for w in workouts[:MAX_WORKOUTS]:
        s = workout_session(w, source, hr_max)
        if not s:
            skipped += 1
            continue
        if s["ext_id"] in known:
            continue
        saved = (merge(s) if merge else None) or db.save_session(s)
        known.add(s["ext_id"])
        first = s["items"][0]
        kind = first.get("sport") or ("atividade" if str(first.get("exercise_id")).startswith("atividade_") else "musculacao")
        imported.append({"session_id": saved.get("id"), "date": s["date"][:10], "type": kind,
                         "merged": bool(saved.get("merged"))})
    return imported, skipped


def _diag(d) -> dict | None:
    """O que o app do celular disse que encontrou (só números e nomes curtos: nada disso é confiável para decidir, só para explicar)."""
    if not isinstance(d, dict):
        return None
    out: dict = {}
    for k in ("app",):
        if isinstance(d.get(k), str):
            out[k] = d[k][:12]
    for k in ("window_days", "health_days", "sleep_days", "steps_days", "exercise_total"):
        v = num(d.get(k), integer=True)
        if v is not None:
            out[k] = int(v)
    if isinstance(d.get("missing"), list):
        out["missing"] = [str(x)[:40] for x in d["missing"][:12]]
    if isinstance(d.get("exercise_types"), dict):
        out["exercise_types"] = {str(k)[:40]: int(num(v, integer=True) or 0) for k, v in list(d["exercise_types"].items())[:20]}
    if isinstance(d.get("exercise_apps"), list):
        out["exercise_apps"] = [str(x)[:60] for x in d["exercise_apps"][:6]]
    return out


def ingest(payload: dict, hr_max: int | None, merge=None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Formato inválido: envie um objeto JSON.")
    source = str(payload.get("source") or "apple").lower()
    source = source if source in ("apple", "android") else "apple"
    days = payload.get("days")
    if days is None and payload.get("date"):
        days = [payload]
    days = [d for d in (days or []) if isinstance(d, dict)][:60]
    workouts = payload.get("workouts") or []
    if isinstance(workouts, dict):
        workouts = [workouts]
    workouts = [w for w in workouts if isinstance(w, dict)]
    saved_days = save_days(days, source)
    imported, skipped = save_workouts(workouts, source, hr_max, merge)
    last = {"at": clock.now().isoformat(timespec="seconds"), "source": source, "days": saved_days,
            "workouts": len(imported), "skipped": skipped, "sent": len(workouts), "diag": _diag(payload.get("diag")),
            "sample": json.dumps({k: v for k, v in payload.items() if k != "diag"}, ensure_ascii=False)[:600]}
    db.kv_set("ingest_last", json.dumps(last, ensure_ascii=False))
    return {"ok": True, "days": saved_days, "imported": imported, "skipped": skipped}
