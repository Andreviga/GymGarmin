"""API HTTP + app web (PWA) do GymGarmin."""

from __future__ import annotations

import json
import time
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from datetime import date, timedelta

from . import (analysis, calibration, catalog, clock, db, dedupe, health, endurance, garmin_export, garmin_sync, generator, history, limitations,
               load, plans_io, progression, readiness, schedule, sports, templates)
from .muscles import EQUIPMENT_PT, HR_ZONES, MUSCLES, hr_max, zone_bpm

STATIC = Path(__file__).resolve().parent / "static"
GARMIN_CATALOG = json.loads((catalog.DATA / "garmin_catalog.json").read_text())

@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="GymGarmin", version="0.1.0", lifespan=lifespan)


def _profile() -> dict:
    """Perfil salvo; idade e peso em branco vêm do Garmin (zonas de FC e 1RM de barra dependem deles)."""
    p = db.get_profile()
    if p.get("age") and p.get("weight_kg"):
        return p
    try:
        wk = json.loads(db.kv_get("health_weekly") or "{}")
    except (TypeError, ValueError):
        return p
    filled = []
    if not p.get("age") and wk.get("chron_age"):
        p["age"] = int(wk["chron_age"])
        filled.append("age")
    if not p.get("weight_kg") and wk.get("weight_kg"):
        p["weight_kg"] = round(float(wk["weight_kg"]), 1)
        filled.append("weight_kg")
    if filled:
        p["_from_garmin"] = filled
    return p


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
        "ephemeral_storage": bool(os.environ.get("VERCEL")) and not os.environ.get("GYMGARMIN_DB") and not db.is_postgres(),
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
    body.pop("_from_garmin", None)
    if body.get("training_months") not in (None, ""):
        body["level"] = generator.TIERS[generator.tier_for(body["training_months"])]["level"]
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
    """Planilha com os dados de cada exercício (nome, tipo, limitação) — evita uma requisição por exercício no celular."""
    p = _plan_or_404(plan_id)
    lims, ovr, cache = _profile().get("limitations") or [], db.garmin_overrides(), {}
    for d in p.get("days", []):
        for it in d.get("items", []):
            eid = it.get("exercise_id")
            if eid not in cache:
                ex = catalog.get(eid)
                cache[eid] = _with_status(ex, lims, ovr) if ex else None
            if cache[eid]:
                it["_ex"] = cache[eid]
    return p


@app.put("/api/plans/{plan_id}")
def put_plan(plan_id: int, body: dict = Body(...)):
    _plan_or_404(plan_id)
    return db.update_plan(plan_id, body)


@app.delete("/api/plans/{plan_id}")
def del_plan(plan_id: int):
    db.delete_plan(plan_id)
    return {"ok": True}


def _plan_analysis(plan: dict) -> dict:
    prof = _profile()
    res = analysis.plan_report(plan, prof)
    budget = schedule.session_budget(prof, plan)
    res["times"] = [generator.time_breakdown(d.get("items", []), budget) for d in plan.get("days", [])]
    return res


@app.get("/api/plans/{plan_id}/analysis")
def plan_analysis(plan_id: int):
    return _plan_analysis(_plan_or_404(plan_id))


@app.post("/api/plans/analyze")
def plan_analysis_draft(body: dict = Body(...)):
    """Analisa uma planilha ainda não salva (editor)."""
    return _plan_analysis(body)


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
        twin = None if body.get("garmin_activity_id") else \
            dedupe.find_twin(body, db.list_sessions(since=str(body.get("date") or "")[:10] or None, limit=200))
        if twin:  # o relógio já trouxe este treino: junta (séries do app + medidas do relógio)
            saved = db.save_session(dedupe.merge(body, twin), session_id=twin["id"])
        else:
            saved = db.save_session(body)
        if body.get("schedule_id"):
            e = db.get_entry(int(body["schedule_id"]))
            if e:
                e["status"] = "feito"
                db.save_entry(e)
        return {**saved, "prs": history.session_prs(db.list_sessions(limit=5000), saved, _profile().get("weight_kg"))}
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


def _prefill_items(day_items: list[dict], deload: bool = False) -> list[dict]:
    items = []
    lims = _profile().get("limitations") or []
    for it in day_items:
        ex = catalog.get(it.get("exercise_id"))
        if not ex:
            continue
        last = db.last_performance(ex["id"])
        entry = {"exercise_id": ex["id"], "name": ex["name"], "kind": ex["kind"], "plan": it,
                 "phase": it.get("phase") or "principal", "last": last,
                 "limitation": limitations.check_exercise(ex, lims), "images": ex["images"][:1], "timed": ex.get("timed")}
        if ex["kind"] == "cardio":
            iv = it.get("intervals") or {}
            mins = it.get("duration_min") or (garmin_export.estimate_duration_s([it]) / 60 if iv else 10)
            entry.update(duration_min=round(mins), zone=it.get("zone") or iv.get("work_zone") or 2)
            if it.get("endurance"):
                end = it["endurance"]
                entry.update(endurance=end, sport=it.get("sport") or end.get("sport"), plan=it,
                             name=f"{sports.SPORTS[end['sport']]['name']}: {end['title']}",
                             planned_distance_km=round((end.get("dist_m") or 0) / 1000, 2) or None)
        else:
            sug = progression.suggest(it, last, deload=deload or it.get("deload", False), today=clock.today()) \
                if entry["phase"] == "principal" and not ex.get("timed") else {"load_kg": it.get("load_kg"), "note": None}
            entry["suggestion"] = sug
            reps, _ = garmin_export.parse_reps(it.get("reps"))
            entry["sets"] = [{"reps": reps if not it.get("duration_s") else None,
                              "duration_s": it.get("duration_s"), "load_kg": sug.get("load_kg"), "rpe": None, "done": False}
                             for _ in range(int(it.get("sets") or (1 if entry["phase"] != "principal" else 3)))]
        items.append(entry)
    return items


@app.get("/api/sessions/prefill/{plan_id}/{idx}")
def session_prefill(plan_id: int, idx: int):
    """Monta a sessão do dia a partir da planilha + progressão de carga do histórico."""
    plan = _plan_or_404(plan_id)
    day = _day(plan, idx)
    return {"plan_id": plan_id, "day_name": day.get("name"), "date": clock.now().isoformat(timespec="minutes"),
            "items": _prefill_items(day.get("items", []))}


# ----------------------------------------------------- evolução de cargas ----

@app.get("/api/history")
def history_overview():
    """Exercícios com histórico (tendência, recordes) e recordes dos últimos 30 dias."""
    return history.overview(db.list_sessions(limit=5000), bodyweight=_profile().get("weight_kg"))


@app.get("/api/history/{exercise_id}")
def history_exercise(exercise_id: str):
    return history.exercise_history(db.list_sessions(limit=5000), exercise_id, bodyweight=_profile().get("weight_kg"))


# ------------------------------------------------------------- análise ----

@app.get("/api/report/weekly")
def weekly(date: str | None = None):
    now = datetime.fromisoformat(date) if date else clock.now()
    prof = _profile()
    expected, deload = None, False
    plan = schedule.schedule_plan(prof)
    if plan:  # o que o plano prevê por músculo e se é semana de deload
        expected = {m["muscle"]: m["sets_week"] for m in analysis.plan_report(plan, prof)["muscles"]}
        gen = plan.get("generator") or {}
        deload = progression.cycle_position(gen.get("cycle_start") or plan.get("created_at"),
                                            schedule.week_start(now.date()), gen.get("cycle_weeks") or 5)["deload"]
    return analysis.weekly_report(db.list_sessions(limit=1000), prof, now, expected=expected, deload=deload)


# -------------------------------------------------------------- Garmin ----

@app.get("/api/garmin/status")
def g_status():
    return garmin_sync.status()


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


def _merge_garmin(session: dict) -> dict | None:
    """Atividade do relógio que já foi registrada no app no mesmo dia: vira uma sessão só."""
    day = str(session.get("date") or "")[:10]
    twin = dedupe.find_twin(session, db.list_sessions(since=day, limit=200)) if day else None
    if not twin:
        return None
    return {**db.save_session(dedupe.merge(twin, session), session_id=twin["id"]), "merged": True}


# A função da Vercel é cortada aos 30 s sem gravar nada: cada etapa tem um prazo
SYNC_BUDGET_S = float(os.environ.get("SYNC_BUDGET_S", "17"))     # importar atividades
EXTRAS_BUDGET_S = float(os.environ.get("EXTRAS_BUDGET_S", "23"))  # saúde, prontidão, métricas


def _remap_garmin_once(plan_ids: set) -> None:
    """Importações antigas usavam o primeiro exercício do catálogo para cada categoria do relógio
    (supino → 'Alternating_Floor_Press'): corrige uma vez."""
    if db.kv_get("garmin_remap_v2"):
        return
    try:
        for s in garmin_sync.remap_imported(db.list_sessions(limit=5000), plan_ids):
            db.save_session(s, session_id=s["id"])
        db.kv_set("garmin_remap_v2", clock.now().isoformat(timespec="seconds"))
    except Exception:  # noqa: BLE001 - não impede a sincronização
        pass


def _run_sync(trigger: str) -> dict:
    t0 = time.monotonic()
    plan_ids = {it.get("exercise_id") for p in db.list_plans() for d in p.get("days", []) for it in d.get("items", [])}
    _remap_garmin_once(plan_ids)
    p = _profile()
    try:
        r = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), plan_ids,
                             hr_max(p.get("age"), p.get("hr_max")), trigger=trigger, merge=_merge_garmin,
                             until=t0 + SYNC_BUDGET_S)
    except garmin_sync.GarminUnavailable as e:
        raise HTTPException(501, str(e)) from e
    if garmin_sync._client is not None:  # mesmo se a importação parou num 429, a prontidão é outra chamada
        today = clock.today()
        until = t0 + EXTRAS_BUDGET_S
        try:  # saúde e sono (sono, HRV, FC de repouso, estresse, passos; VO2máx de tempos em tempos)
            health.collect(garmin_sync._client, today, db.kv_get, db.kv_set, until=until)
        except Exception:  # noqa: BLE001 - opcional
            pass
        if trigger != "cron" and time.monotonic() < until:  # à noite a prontidão do dia já não importa
            try:
                _refresh_readiness(today, auto_apply=True)
            except Exception:  # noqa: BLE001 - prontidão é opcional
                pass
        try:
            gm = json.loads(db.kv_get("garmin_metrics") or "{}")
            if time.monotonic() < until and (not gm or gm.get("date", "") < (today - timedelta(days=3)).isoformat()):
                db.kv_set("garmin_metrics", json.dumps(calibration.garmin_metrics(garmin_sync._client, today)))
        except Exception:  # noqa: BLE001
            pass
        garmin_sync.persist()
    r["elapsed_s"] = round(time.monotonic() - t0, 1)
    return r


@app.post("/api/garmin/sync")
def g_sync(auto: bool = False):
    """Sincroniza agora. Com auto=1 (ao abrir o app) só roda se a última tiver mais de 6 h."""
    if auto:
        st = garmin_sync.status()
        if not st["has_tokens"]:
            return {"ok": True, "ran": False, "imported": []}
        if not garmin_sync.due() and db.kv_get("garmin_remap_v2"):
            # sem sincronizar atividades, mas a prontidão de hoje pode ter chegado (o relógio sincroniza a noite ao acordar)
            today = clock.today()
            try:
                if readiness.stale(json.loads(db.kv_get(f"readiness:{today.isoformat()}") or "null")):
                    _refresh_readiness(today, auto_apply=True)
            except Exception:  # noqa: BLE001 - prontidão é opcional
                pass
            return {"ok": True, "ran": False, "imported": []}
    return {"ran": True, **_run_sync("auto" if auto else "manual")}


@app.get("/api/cron/garmin-sync")
def cron_garmin_sync(authorization: str | None = Header(None)):
    """Chamado pelo cron diário da Vercel (envia 'Authorization: Bearer $CRON_SECRET')."""
    secret = os.environ.get("CRON_SECRET")
    if (secret or os.environ.get("VERCEL")) and authorization != f"Bearer {secret}":
        raise HTTPException(401, "Não autorizado")
    garmin_sync._set_sync_state(last_cron=clock.now().isoformat(timespec="seconds"))  # prova de que o cron chega
    if not garmin_sync.status()["has_tokens"]:
        return {"ok": True, "ran": False, "reason": "Garmin não conectado"}
    return {"ran": True, **_run_sync("cron")}


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


# ------------------------------------------------- resistência / multiesporte ----

def _recent(prof: dict) -> dict:
    return endurance.recent_volumes(db.list_sessions(limit=400), prof)


def _endurance_cfg(prof: dict) -> dict:
    return dict(prof.get("endurance") or {})


@app.get("/api/endurance/options")
def endurance_options():
    return endurance.options()


@app.get("/api/endurance/overview")
def endurance_overview():
    """Âncoras, zonas, previsões, sugestões de recalibração, carga e prontidão."""
    prof = _profile()
    anchors = sports.anchors_of(prof)
    cfg = endurance.config(prof)
    sessions = db.list_sessions(limit=2000)
    gm = json.loads(db.kv_get("garmin_metrics") or "{}")
    meta = (_endurance_cfg(prof).get("anchors_meta") or {})
    anchors_out = []
    for key, label in calibration.ANCHOR_LABEL.items():
        anchors_out.append({"key": key, "label": label, "value": anchors.get(key),
                            "text": calibration.fmt_anchor(key, anchors.get(key)),
                            "date": (meta.get(key) or {}).get("date"), "source": (meta.get(key) or {}).get("source")})
    return {
        "config": _endurance_cfg(prof),
        "active": list(cfg),
        "anchors": anchors_out,
        "zones": {s: sports.zones_table(s, anchors) for s in cfg},
        "predictions": endurance.predictions(prof),
        "suggestions": calibration.suggestions(sessions, anchors, gm),
        "garmin_metrics": gm,
        "recent": endurance.recent_volumes(sessions, prof),
        "load": {k: v for k, v in load.pmc(sessions, prof).items() if k != "series"},
        "readiness": _readiness_view(clock.today()),
    }


@app.put("/api/endurance")
def endurance_save(body: dict = Body(...)):
    """Salva modalidades/evento/opções (mantém âncoras e o histórico delas)."""
    prof = _profile()
    cur = _endurance_cfg(prof)
    for k in ("sports", "event", "tests", "readiness_auto"):
        if k in body:
            cur[k] = body[k]
    db.save_profile({"endurance": cur})
    return cur


def _set_anchor(key: str, value: float, source: str) -> dict:
    if key not in calibration.ANCHOR_LABEL:
        raise HTTPException(400, "Âncora desconhecida")
    prof = _profile()
    cur = _endurance_cfg(prof)
    anchors = dict(cur.get("anchors") or {})
    meta = dict(cur.get("anchors_meta") or {})
    anchors[key] = round(float(value), 1)
    meta[key] = {"date": clock.today().isoformat(), "source": source}
    cur.update(anchors=anchors, anchors_meta=meta)
    db.save_profile({"endurance": cur})
    return {"key": key, "value": anchors[key], "text": calibration.fmt_anchor(key, anchors[key]), "source": source}


@app.post("/api/anchors")
def anchors_set(body: dict = Body(...)):
    return _set_anchor(body.get("key"), body.get("value"), body.get("source") or "manual")


@app.post("/api/anchors/test")
def anchors_test(body: dict = Body(...)):
    try:
        key, value = calibration.from_test(body.get("sport"), body.get("data") or {})
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from e
    return _set_anchor(key, value, f"teste ({clock.today().strftime('%d/%m')})")


@app.get("/api/load")
def load_chart(days: int = 90):
    return load.pmc(db.list_sessions(limit=5000), _profile(), days=days)


# ---- prontidão

def _readiness_view(day: date) -> dict | None:
    raw = db.kv_get(f"readiness:{day.isoformat()}")
    if not raw:
        return None
    data = json.loads(raw)
    hd = health.load_days(db.kv_get, day, 36)
    night = None
    if hd:
        hr = health.report(hd, None, {}, _profile(), day)
        rhr = hr.get("rhr") or {}
        if rhr.get("delta") is not None:
            data["rhr_delta"] = rhr["delta"]
        n = hr.get("last_night")
        if n and n["date"] == day.isoformat() and n.get("level") != "sem_dados":
            night = {"level": n["level"], "text": f"{health.fmt_h(n.get('duration_h'))} · sono {n['level']}"
                                                  + (f" ({int(n['score'])})" if n.get("score") is not None else "")}
    sc = readiness.score(data, load.pmc(db.list_sessions(limit=2000), _profile(), days=14)["form"])
    return {**data, **sc, "text": readiness.ACTION_TEXT[sc["action"]], "night": night}


def _refresh_readiness(day: date, auto_apply: bool = False) -> dict | None:
    try:
        api = garmin_sync.client()
        try:  # sono/HRV/FC de repouso vêm da coleta de saúde (uma chamada só para os dois)
            health.collect(api, day, db.kv_get, db.kv_set, backfill=0)
        except Exception:  # noqa: BLE001
            pass
        known = json.loads(db.kv_get(f"health:{day.isoformat()}") or "{}")
        data = readiness.fetch(api, day, known)
    finally:
        garmin_sync.persist()
    db.kv_set(f"readiness:{day.isoformat()}", json.dumps(data))
    view = _readiness_view(day)
    if auto_apply and view and _endurance_cfg(_profile()).get("readiness_auto"):
        _apply_readiness(day, view["action"])
    return view


def _apply_readiness(day: date, action: str, force: bool = False) -> list[dict]:
    anchors = sports.anchors_of(_profile())
    out = []
    for e in db.list_schedule(day.isoformat(), day.isoformat()):
        new = readiness.adjust_entry(e, action, anchors, force=force)
        if new:
            out.append(db.save_entry(new))
    return out


# ---- saúde e sono

def _health_report(today: date) -> dict:
    prof = _profile()
    sessions = db.list_sessions(since=(today - timedelta(days=60)).isoformat(), limit=2000)
    anchors = sports.anchors_of(prof)
    daily = {d: v["tss"] for d, v in load.daily_stress(sessions, anchors).items()}
    times: dict = {}
    for s in sessions:
        try:
            t = datetime.fromisoformat(str(s["date"])[:19])
        except ValueError:
            continue
        if len(str(s["date"])) > 10:
            times.setdefault(t.date(), []).append(t.hour + t.minute / 60)
    rep = health.report(health.load_days(db.kv_get, today, 56), json.loads(db.kv_get("health_weekly") or "{}"),
                        daily, prof, today, times)
    st = garmin_sync.status()
    rep["garmin"] = {"connected": st["connected"], "needs_reconnect": st["needs_reconnect"]}
    return rep


@app.get("/api/health")
def health_get():
    return _health_report(clock.today())


@app.post("/api/health/refresh")
def health_refresh():
    try:
        api = garmin_sync.client()
        try:
            health.collect(api, clock.today(), db.kv_get, db.kv_set, backfill=7)
        finally:
            garmin_sync.persist()
    except RuntimeError as e:
        raise HTTPException(401, str(e)) from e
    return _health_report(clock.today())


@app.get("/api/readiness")
def readiness_get():
    return _readiness_view(clock.today()) or {"score": None, "level": "sem_dados", "action": "seguir"}


@app.post("/api/readiness/refresh")
def readiness_refresh():
    try:
        return _refresh_readiness(clock.today())
    except RuntimeError as e:
        raise HTTPException(401, str(e)) from e


@app.post("/api/readiness/apply")
def readiness_apply(body: dict = Body(default={})):
    view = _readiness_view(clock.today())
    action = body.get("action") or (view or {}).get("action") or "seguir"
    if action not in readiness.ACTION_TEXT:
        raise HTTPException(400, "Ação inválida")
    return {"action": action, "changed": [e["day_name"] for e in _apply_readiness(clock.today(), action, force=True)]}


@app.post("/api/readiness/undo/{entry_id}")
def readiness_undo(entry_id: int):
    e = db.get_entry(entry_id)
    if not e:
        raise HTTPException(404, "Treino não encontrado")
    try:
        return db.save_entry(readiness.undo_entry(e))
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


# ----------------------------------------------------- gerador automático ----

@app.get("/api/generator/options")
def generator_options():
    return {
        "tiers": [{"id": k, "label": v["label"], "months": v["months"]} for k, v in generator.TIERS.items()],
        "goals": [{"id": k, "label": v["label"], "cardio_target": v["cardio_target"]} for k, v in generator.GOALS.items()],
        "weekdays": generator.WEEKDAYS,
        "cardio_machines": [{"id": e["id"], "name": e["name"]} for e in catalog.all_exercises() if e.get("machine")],
    }


@app.post("/api/generator/preview")
def generator_preview(body: dict = Body(default={})):
    prof = {**_profile(), **(body.get("profile") or {})}
    plan = generator.generate(prof, int(body.get("seed") or 0), recent=_recent(prof))
    plan["analysis"] = analysis.plan_report(plan, prof)
    return plan


@app.post("/api/generator/apply")
def generator_apply(body: dict = Body(default={})):
    """Gera, salva a planilha, define como planilha da agenda e (re)monta a semana."""
    overrides = body.get("profile") or {}
    if overrides:
        db.save_profile(overrides)
    prof = _profile()
    seed = int(body.get("seed") or 0)
    plan = generator.generate(prof, seed, recent=_recent(prof))
    plan["generator"]["cycle_start"] = schedule.week_start(clock.today()).isoformat()
    for p in db.list_plans():
        if p.get("generator") and p.get("active"):
            p["active"] = False
            db.update_plan(p["id"], p)
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    schedule.rebuild_week(_profile())
    return saved


# --------------------------------------------------------------- agenda ----

def _entry_view(e: dict, budget: float) -> dict:
    e = dict(e)
    e["can_undo"] = bool(e.pop("undo", None))
    e["time"] = generator.time_breakdown(e.get("items", []), budget)
    over = round(e["time"]["total"] - budget)
    if over > 5 and e.get("status") in ("planejado", "perdido"):
        e["over_budget_min"] = over  # o treino pode passar do tempo (longo, pedal); o app só avisa
    e["weekday"] = generator.WEEKDAYS[date.fromisoformat(e["date"]).weekday()]
    for it in e.get("items", []):
        ex = catalog.get(it.get("exercise_id"))
        it["_name"] = ex["name"] if ex else it.get("exercise_id")
    return e


@app.get("/api/schedule")
def get_schedule(date_: str | None = Query(None, alias="date")):
    prof = _profile()
    day = date.fromisoformat(date_) if date_ else clock.today()
    entries = schedule.ensure_week(prof, day)
    ws = schedule.week_start(day)
    plan = schedule.schedule_plan(prof)
    budget = schedule.session_budget(prof, plan)
    cycle = next((e.get("cycle") for e in entries if e.get("cycle")), None)
    return {
        "week_start": ws.isoformat(), "today": clock.today().isoformat(),
        "plan": {"id": plan["id"], "name": plan["name"], "generator": plan.get("generator")} if plan else None,
        "cycle": cycle, "budget": budget,
        "entries": (views := [_entry_view(e, budget) for e in entries]),
        "goals": schedule.week_goals(entries, prof, ws),
        "time_warnings": _time_warnings(views, budget),
    }


def _time_warnings(views: list[dict], budget: float) -> list[str]:
    """Avisos da semana: treinos acima do tempo de sessão e dias com dois treinos que somam demais."""
    out = []
    for e in views:
        if e.get("over_budget_min"):
            role = next((it["endurance"].get("role") for it in e.get("items", []) if it.get("endurance")), None)
            out.append(f"{e['weekday']} {e['date'][8:10]}/{e['date'][5:7]} – {e['day_name']}: ~{round(e['time']['total'])} min, "
                       f"{e['over_budget_min']} min além dos seus {int(budget)}"
                       + (" (longo: reserve o tempo, não corte)" if role == "longo" else ""))
    by_day: dict = {}
    for e in views:
        if e.get("status") in ("planejado", "perdido"):
            by_day.setdefault(e["date"], []).append(e)
    for d, es in sorted(by_day.items()):
        if len(es) >= 2:
            tot = round(sum(x["time"]["total"] for x in es))
            if tot > budget + 10:
                out.append(f"{es[0]['weekday']} {d[8:10]}/{d[5:7]}: dois treinos somam ~{tot} min — "
                           "faça em dois horários (força primeiro, ou 6 h+ de intervalo).")
    return out


def _entry_or_404(entry_id: int) -> dict:
    e = db.get_entry(entry_id)
    if not e:
        raise HTTPException(404, "Treino da agenda não encontrado")
    return e


@app.post("/api/schedule/{entry_id}/skip")
def schedule_skip(entry_id: int):
    try:
        return schedule.handle_missed(entry_id, _profile())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/schedule/{entry_id}/undo")
def schedule_undo(entry_id: int):
    try:
        schedule.undo(entry_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.post("/api/schedule/{entry_id}/move")
def schedule_move(entry_id: int, body: dict = Body(...)):
    try:
        return schedule.move(entry_id, body["date"])
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/schedule/rebuild")
def schedule_rebuild():
    schedule.rebuild_week(_profile())
    return {"ok": True}


@app.get("/api/schedule/{entry_id}/prefill")
def schedule_prefill(entry_id: int):
    e = _entry_or_404(entry_id)
    deload = bool((e.get("cycle") or {}).get("deload"))
    return {"schedule_id": e["id"], "plan_id": e.get("plan_id"), "day_name": e.get("day_name"),
            "date": e["date"] + clock.now().strftime("T%H:%M"), "deload": deload,
            "items": _prefill_items(e.get("items", []), deload)}


def _entry_day(e: dict) -> dict:
    d = date.fromisoformat(e["date"])
    return {"name": f"{e.get('day_name')} {d.day:02d}/{d.month:02d}", "items": e.get("items", []),
            "notes": "; ".join(e.get("changes") or []) or None}


@app.get("/api/schedule/{entry_id}/fit")
def schedule_fit(entry_id: int):
    e = _entry_or_404(entry_id)
    data = garmin_export.to_fit(_entry_day(e), "", db.garmin_overrides())
    fname = garmin_export.fit_filename(e["date"], e.get("day_name") or "treino")
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


def _push_entry(e: dict) -> dict:
    if e.get("garmin_workout_id"):
        try:
            garmin_sync.delete_workout(int(e["garmin_workout_id"]))
        except Exception:  # noqa: BLE001 - versão antiga pode já ter sido apagada
            pass
    payload = garmin_export.to_connect_json(_entry_day(e), "", db.garmin_overrides())
    res = garmin_sync.push_workout(payload, e["date"])
    e["garmin_workout_id"] = res.get("workout_id")
    e["garmin_outdated"] = False
    db.save_entry(e)
    db.log_push(e.get("plan_id"), e.get("day_name"), res.get("workout_id"), e["date"])
    return res


def _garmin_call(fn):
    try:
        return fn()
    except garmin_sync.GarminUnavailable as e:
        raise HTTPException(501, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(401, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Garmin Connect recusou o envio: {e}") from e


@app.post("/api/schedule/{entry_id}/push")
def schedule_push(entry_id: int):
    e = _entry_or_404(entry_id)
    return _garmin_call(lambda: _push_entry(e))


@app.post("/api/schedule/push-week")
def schedule_push_week(body: dict = Body(default={})):
    only_outdated = bool(body.get("only_outdated"))
    today = clock.today()
    ws = schedule.week_start(today)
    entries = [e for e in db.list_schedule(today.isoformat(), (ws + timedelta(days=6)).isoformat())
               if e["status"] == "planejado" and (not only_outdated or e.get("garmin_outdated") or not e.get("garmin_workout_id"))]
    return _garmin_call(lambda: [{"date": e["date"], **_push_entry(e)} for e in entries])


# --------------------------------------------------------------- backup ----

@app.get("/api/backup")
def backup():
    fname = f"gymgarmin-backup-{clock.today().isoformat()}.json"
    return JSONResponse(db.export_all(), headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/backup")
def restore(body: dict = Body(...)):
    if body.get("version") != 1:
        raise HTTPException(400, "Arquivo de backup inválido")
    return db.import_all(body)


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
