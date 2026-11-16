"""API HTTP + app web (PWA) do GymGarmin."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import analysis, catalog, db, garmin_export, garmin_sync, limitations, plans_io, templates
from .muscles import EQUIPMENT_PT, HR_ZONES, MUSCLES, hr_max, zone_bpm

STATIC = Path(__file__).resolve().parent / "static"
GARMIN_CATALOG = json.loads((catalog.DATA / "garmin_catalog.json").read_text())

@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="GymGarmin", version="0.1.0", lifespan=lifespan)


def _profile() -> dict:
    return db.get_profile()


def _with_status(ex: dict, lims: list[dict], overrides: dict | None = None) -> dict:
    out = catalog.public(ex)
    out["limitation"] = limitations.check_exercise(ex, lims)
    if overrides and ex["id"] in overrides:
        out["garmin"] = {**ex["garmin"], **overrides[ex["id"]], "confidence": "manual"}
    return out


# ---------------------------------------------------------------- meta -----

@app.get("/api/meta")
def meta():
    p = _profile()
    fc = hr_max(p.get("age"), p.get("hr_max"))
    return {
        "muscles": [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "mav"}, "mav": list(v["mav"])}
                    for k, v in MUSCLES.items()],
        "limitations": limitations.catalog(),
        "zones": [{"zone": z, **v, "bpm": zone_bpm(z, fc)} for z, v in HR_ZONES.items()],
        "hr_max": fc,
        "equipment": [{"key": k, "pt": v} for k, v in EQUIPMENT_PT.items() if k],
        "templates": templates.list_templates(),
    }


# ----------------------------------------------------------- exercícios ----

@app.get("/api/exercises")
def exercises(q: str = "", muscle: str | None = None, equipment: str | None = None,
              kind: str | None = None, level: str | None = None, limit: int = Query(60, le=1000),
              hide_avoid: bool = False):
    lims = _profile().get("limitations") or []
    ov = db.garmin_overrides()
    res = [_with_status(e, lims, ov) for e in catalog.search(q, muscle, equipment, kind, level, limit=limit * 2)]
    if hide_avoid:
        res = [r for r in res if r["limitation"]["status"] != "evitar"]
    for r in res:
        r.pop("instructions", None)
    return res[:limit]


@app.get("/api/exercises/{exercise_id}")
def exercise_detail(exercise_id: str):
    ex = catalog.get(exercise_id)
    if not ex:
        raise HTTPException(404, "Exercício não encontrado")
    out = _with_status(ex, _profile().get("limitations") or [], db.garmin_overrides())
    out["last"] = db.last_performance(exercise_id)
    return out


@app.put("/api/exercises/{exercise_id}/garmin")
def set_exercise_garmin(exercise_id: str, body: dict = Body(...)):
    if not catalog.get(exercise_id):
        raise HTTPException(404, "Exercício não encontrado")
    cat, exn = body.get("category"), body.get("exercise")
    if not any(g["category"] == cat and g["exercise"] == exn for g in GARMIN_CATALOG):
        raise HTTPException(400, "Par categoria/exercício não existe no catálogo do Garmin")
    db.set_garmin_override(exercise_id, cat, exn)
    return {"ok": True}


@app.get("/api/garmin/catalog")
def garmin_catalog(q: str = "", limit: int = 40):
    qn = catalog.strip_accents(q)
    words = qn.split()
    res = [g for g in GARMIN_CATALOG if all(w in g["name"].lower() for w in words)] if words else GARMIN_CATALOG
    return res[:limit]


# --------------------------------------------------------------- perfil ----

@app.get("/api/profile")
def get_profile():
    p = _profile()
    p["hr_max_calc"] = hr_max(p.get("age"), p.get("hr_max"))
    return p


@app.put("/api/profile")
def put_profile(body: dict = Body(...)):
    body.pop("hr_max_calc", None)
    return db.save_profile(body)


# ------------------------------------------------------------ planilhas ----

@app.get("/api/plans")
def plans():
    return db.list_plans()


@app.post("/api/plans")
def create_plan(body: dict = Body(...)):
    if body.get("template"):
        tpl = templates.build(body["template"], _profile())
        if not tpl:
            raise HTTPException(404, "Modelo não encontrado")
        body = tpl
    return db.create_plan(body)


def _plan_or_404(plan_id: int) -> dict:
    p = db.get_plan(plan_id)
    if not p:
        raise HTTPException(404, "Planilha não encontrada")
    return p


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int):
    return _plan_or_404(plan_id)


@app.put("/api/plans/{plan_id}")
def put_plan(plan_id: int, body: dict = Body(...)):
    _plan_or_404(plan_id)
    return db.update_plan(plan_id, body)


@app.delete("/api/plans/{plan_id}")
def del_plan(plan_id: int):
    db.delete_plan(plan_id)
    return {"ok": True}


@app.get("/api/plans/{plan_id}/analysis")
def plan_analysis(plan_id: int):
    return analysis.plan_report(_plan_or_404(plan_id), _profile())


@app.post("/api/plans/analyze")
def plan_analysis_draft(body: dict = Body(...)):
    """Analisa uma planilha ainda não salva (editor)."""
    return analysis.plan_report(body, _profile())


def _day(plan: dict, idx: int) -> dict:
    days = plan.get("days") or []
    if not 0 <= idx < len(days):
        raise HTTPException(404, "Treino (dia) não encontrado")
    return days[idx]


@app.get("/api/plans/{plan_id}/days/{idx}/fit")
def day_fit(plan_id: int, idx: int):
    plan = _plan_or_404(plan_id)
    day = _day(plan, idx)
    try:
        data = garmin_export.to_fit(day, plan["name"], db.garmin_overrides())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    fname = garmin_export.fit_filename(plan["name"], day.get("name", str(idx)))
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/plans/{plan_id}/days/{idx}/connect.json")
def day_connect_json(plan_id: int, idx: int, download: bool = False):
    plan = _plan_or_404(plan_id)
    try:
        payload = garmin_export.to_connect_json(_day(plan, idx), plan["name"], db.garmin_overrides())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    headers = {}
    if download:
        fname = garmin_export.fit_filename(plan["name"], _day(plan, idx).get("name", "")).replace(".fit", ".json")
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return JSONResponse(payload, headers=headers)


@app.post("/api/plans/{plan_id}/days/{idx}/push")
def day_push(plan_id: int, idx: int, body: dict = Body(default={})):
    plan = _plan_or_404(plan_id)
    day = _day(plan, idx)
    payload = garmin_export.to_connect_json(day, plan["name"], db.garmin_overrides())
    try:
        res = garmin_sync.push_workout(payload, body.get("date"))
    except garmin_sync.GarminUnavailable as e:
        raise HTTPException(501, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(401, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Garmin Connect recusou o envio: {e}") from e
    db.log_push(plan_id, day.get("name"), res.get("workout_id"), res.get("scheduled"))
    return res


@app.get("/api/plans/{plan_id}/csv")
def plan_csv(plan_id: int):
    plan = _plan_or_404(plan_id)
    fname = garmin_export.fit_filename(plan["name"], "planilha").replace(".fit", ".csv")
    return Response("﻿" + plans_io.export_csv(plan), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/plans/import-csv")
def plan_import_csv(body: dict = Body(...)):
    res = plans_io.import_csv(body.get("csv") or "", body.get("name") or "Planilha importada")
    if body.get("save") and res["plan"]["days"]:
        res["saved"] = db.create_plan(res["plan"])
    return res


# --------------------------------------------------------- sessões/log ----

@app.get("/api/sessions")
def sessions(since: str | None = None, limit: int = 100):
    return db.list_sessions(since=since, limit=limit)


@app.post("/api/sessions")
def create_session(body: dict = Body(...)):
    try:
        return db.save_session(body)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    s = db.get_session(session_id)
    if not s:
        raise HTTPException(404, "Sessão não encontrada")
    return s


@app.put("/api/sessions/{session_id}")
def put_session(session_id: int, body: dict = Body(...)):
    return db.save_session(body, session_id=session_id)


@app.delete("/api/sessions/{session_id}")
def del_session(session_id: int):
    db.delete_session(session_id)
    return {"ok": True}


@app.get("/api/sessions/prefill/{plan_id}/{idx}")
def session_prefill(plan_id: int, idx: int):
    """Monta a sessão do dia a partir da planilha + última carga usada em cada exercício."""
    plan = _plan_or_404(plan_id)
    day = _day(plan, idx)
    items = []
    lims = _profile().get("limitations") or []
    for it in day.get("items", []):
        ex = catalog.get(it.get("exercise_id"))
        if not ex:
            continue
        last = db.last_performance(ex["id"])
        entry = {"exercise_id": ex["id"], "name": ex["name"], "kind": ex["kind"], "plan": it,
                 "last": last, "limitation": limitations.check_exercise(ex, lims),
                 "images": ex["images"][:1], "timed": ex.get("timed")}
        if ex["kind"] == "cardio":
            entry.update(duration_min=it.get("duration_min") or 10, zone=it.get("zone") or 2)
        else:
            reps, _ = garmin_export.parse_reps(it.get("reps"))
            last_sets = (last or {}).get("sets") or []
            load = it.get("load_kg") or (max((s.get("load_kg") or 0) for s in last_sets) if last_sets else None)
            entry["sets"] = [{"reps": reps if not it.get("duration_s") else None,
                              "duration_s": it.get("duration_s"), "load_kg": load, "rpe": None, "done": False}
                             for _ in range(int(it.get("sets") or 3))]
        items.append(entry)
    return {"plan_id": plan_id, "day_name": day.get("name"), "date": datetime.now().isoformat(timespec="minutes"),
            "items": items}


# ------------------------------------------------------------- análise ----

@app.get("/api/report/weekly")
def weekly(date: str | None = None):
    now = datetime.fromisoformat(date) if date else datetime.now()
    return analysis.weekly_report(db.list_sessions(limit=1000), _profile(), now)


# -------------------------------------------------------------- Garmin ----

@app.get("/api/garmin/status")
def g_status():
    st = garmin_sync.status()
    if st["has_tokens"] and not st["connected"] and st["library_installed"]:
        try:
            garmin_sync.connect_with_tokens()
            st = garmin_sync.status()
        except Exception as e:  # noqa: BLE001
            st["error"] = str(e)
    return st


@app.post("/api/garmin/login")
def g_login(body: dict = Body(...)):
    try:
        return garmin_sync.login(body.get("email", ""), body.get("password", ""))
    except garmin_sync.GarminUnavailable as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(401, f"Falha no login do Garmin: {e}") from e


@app.post("/api/garmin/mfa")
def g_mfa(body: dict = Body(...)):
    try:
        return garmin_sync.submit_mfa(body.get("code", ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(401, f"Código MFA recusado: {e}") from e


@app.post("/api/garmin/logout")
def g_logout():
    garmin_sync.logout()
    return {"ok": True}


@app.get("/api/garmin/activities")
def g_activities(days: int = 14):
    try:
        return garmin_sync.recent_activities(days)
    except RuntimeError as e:
        raise HTTPException(401, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@app.post("/api/garmin/import/{activity_id}")
def g_import(activity_id: int):
    plan_ids = {it.get("exercise_id") for p in db.list_plans() for d in p.get("days", []) for it in d.get("items", [])}
    p = _profile()
    try:
        session = garmin_sync.import_activity(activity_id, plan_ids, hr_max(p.get("age"), p.get("hr_max")))
        return db.save_session(session)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(401, str(e)) from e


# ------------------------------------------------------------- estático ----

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")
