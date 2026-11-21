"""API HTTP + app web (PWA) do HAX."""

from __future__ import annotations

import json
import time
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from datetime import date, timedelta

from . import auth, coach, focus, gallery, ingest, my_gym, partners, training_rules
from . import (analysis, calibration, catalog, clock, db, dedupe, health, endurance, garmin_export, garmin_sync, generator, history, limitations,
               load, plans_io, progression, readiness, schedule, sports, steps, templates, watch_labels)
from .muscles import EQUIPMENT_PT, HR_ZONES, MUSCLES, hr_max, zone_bpm

STATIC = Path(__file__).resolve().parent / "static"
import mimetypes  # noqa: E402

mimetypes.add_type("application/vnd.android.package-archive", ".apk")
GARMIN_CATALOG = json.loads((catalog.DATA / "garmin_catalog.json").read_text())

@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="HAX", version="0.1.0", lifespan=lifespan)

# ------------------------------------------------------------------ login ----
# Rotas abertas: login/cadastro, cron (tem segredo próprio) e envio de dados por token pessoal.
OPEN_PREFIXES = ("/api/auth/", "/api/cron/", "/api/ingest", "/api/public/")


# O coach abre o app "como" o atleta (cabeçalho X-GG-Athlete). Isto ele não pode: login/senha,
# Garmin (conta do atleta), código pessoal, administração, backup.
ACT_BLOCKED = ("/api/auth/", "/api/account/", "/api/admin/", "/api/backup", "/api/ingest", "/api/coach/",
               "/api/garmin/login", "/api/garmin/logout", "/api/garmin/mfa")


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return None


def _cookie(scope, name: str) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                if "=" in part:
                    ck, cv = part.strip().split("=", 1)
                    if ck == name:
                        return cv
    return None


class AuthMiddleware:
    """Define o usuário da requisição pelo cookie; sem login válido, /api/* responde 401
    (enquanto não existe nenhuma conta, tudo funciona como usuário 1, como antes)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.inner(scope, receive, send)
        acc = auth.read_session(_cookie(scope, auth.COOKIE))
        tok = auth.set_account(acc)
        utok = db.set_current_user(acc["user_id"]) if acc else None
        atok = None
        try:
            if not acc and not scope["path"].startswith(OPEN_PREFIXES) and auth.accounts_exist():
                resp = JSONResponse({"detail": "Entre na sua conta para continuar.", "login": True}, status_code=401)
                return await resp(scope, receive, send)
            act = _header(scope, b"x-gg-athlete")
            if acc and act and not scope["path"].startswith(ACT_BLOCKED):
                try:
                    aid = int(act)
                except ValueError:
                    aid = 0
                if aid != acc["user_id"]:
                    if not db.is_linked(acc["user_id"], aid):
                        resp = JSONResponse({"detail": "Você não é coach deste atleta (ou ele removeu o acesso)."}, status_code=403)
                        return await resp(scope, receive, send)
                    atok = db.set_current_user(aid)
                    auth.set_acting(acc["user_id"])
                    if scope["path"].startswith("/api/partners"):
                        resp = JSONResponse({"detail": "Parceria de treino é da própria pessoa: o coach não acessa por ela."}, status_code=403)
                        return await resp(scope, receive, send)
                    if scope["path"].startswith(("/api/health", "/api/checkin", "/api/steps")) and \
                            db.kv_get(f"coach_share_health:{acc['user_id']}") == "0":
                        resp = JSONResponse({"detail": "Este atleta não compartilha saúde e sono com o coach."}, status_code=403)
                        return await resp(scope, receive, send)
                    if scope["method"] in ("POST", "PUT", "DELETE"):
                        _coach_log(aid, acc, scope["method"], scope["path"])
            return await self.inner(scope, receive, send)
        finally:
            if atok is not None:
                db._current_user.reset(atok)
                auth.set_acting(None)
            auth.reset_account(tok)
            if utok is not None:
                db._current_user.reset(utok)


app.add_middleware(AuthMiddleware)

COACH_ACTION_PT = [("/api/plans", "mudou a planilha"), ("/api/generator/apply", "gerou uma planilha nova"),
                   ("/api/schedule", "ajustou a agenda"), ("/api/profile", "ajustou o perfil"),
                   ("/api/endurance", "ajustou os esportes"), ("/api/readiness", "ajustou o treino do dia"),
                   ("/api/sessions", "mexeu nos treinos registrados"), ("/api/anchors", "ajustou as zonas")]


def _coach_name(acc: dict) -> str:
    n = (db.get_profile(acc["user_id"]).get("name") or "").strip()
    return n if n and n.lower() != "eu" else acc["email"].split("@")[0]


def _coach_log(athlete_id: int, coach_acc: dict, method: str, path: str) -> None:
    """Registro do que o coach mudou (o atleta vê em Hoje)."""
    what = next((t for prefix, t in COACH_ACTION_PT if path.startswith(prefix)), "fez uma alteração")
    name = _coach_name(coach_acc)
    log = json.loads(db.kv_get("coach_log", user_id=athlete_id) or "[]")
    if log and log[-1].get("text") == f"{name} {what}" and log[-1].get("at", "")[:13] == clock.now().isoformat()[:13]:
        return  # mesma ação na mesma hora: uma linha só
    log.append({"at": clock.now().isoformat(timespec="minutes"), "coach": coach_acc["user_id"], "text": f"{name} {what}",
                "path": path, "method": method})
    db.kv_set("coach_log", json.dumps(log[-40:], ensure_ascii=False), user_id=athlete_id)


def _with_cookie(payload: dict, acc: dict) -> JSONResponse:
    r = JSONResponse(payload)
    r.set_cookie(auth.COOKIE, auth.make_session(acc), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                 secure=bool(os.environ.get("VERCEL")), samesite="lax", path="/")
    return r


def _me_payload(acc: dict | None) -> dict:
    if not acc:
        return {"logged_in": False, "setup_needed": not auth.accounts_exist()}
    prof = db.get_profile(acc["user_id"])
    return {"logged_in": True, "setup_needed": False,
            "user": {**auth.public(acc, prof.get("name")), "is_coach": db.is_coach(acc["user_id"])}}


def _require_admin() -> dict:
    acc = auth.current_account()
    if not auth.accounts_exist():
        return {"user_id": 1, "is_admin": 1}
    if not acc or not acc.get("is_admin"):
        raise HTTPException(403, "Só o administrador pode fazer isso.")
    return acc


@app.get("/api/auth/me")
def auth_me():
    acc = auth.current_account()
    if acc:
        # "última vez que abriu o app" (no máx. 1 gravação a cada 10 min): ajuda a conferir se o app abriu e sincronizou
        try:
            now = clock.now()
            last = db.kv_get("last_seen")
            if not last or (now - datetime.fromisoformat(last)).total_seconds() > 600:
                db.kv_set("last_seen", now.isoformat(timespec="seconds"))
        except Exception:  # noqa: BLE001 - nunca atrapalha o login
            pass
    return _me_payload(acc)


@app.post("/api/auth/setup")
def auth_setup(body: dict = Body(...)):
    """Primeira conta (o dono): fica com os dados que já estão no app."""
    if auth.accounts_exist():
        raise HTTPException(409, "O app já tem dono. Entre com sua conta ou peça um convite.")
    email, pw = (body.get("email") or "").strip().lower(), body.get("password") or ""
    if "@" not in email:
        raise HTTPException(400, "E-mail inválido.")
    try:
        auth.validate_password(pw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    name = (body.get("name") or "").strip() or db.get_profile(1).get("name") or "Eu"
    acc = db.create_account(email, auth.hash_password(pw), name, is_admin=True, user_id=1)
    if body.get("name"):
        db.save_profile({"name": name}, user_id=1)
    return _with_cookie(_me_payload(acc), acc)


@app.post("/api/auth/login")
def auth_login(body: dict = Body(...)):
    acc = db.get_account_by_email(body.get("email") or "")
    if not acc or not auth.check_password(body.get("password") or "", acc["pw_hash"]):
        time.sleep(0.6)  # freia tentativa de senha por força bruta
        raise HTTPException(401, "E-mail ou senha incorretos.")
    return _with_cookie(_me_payload(acc), acc)


@app.post("/api/auth/logout")
def auth_logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@app.get("/api/auth/invite/{code}")
def auth_invite(code: str):
    inv = db.get_invite(code)
    return {"valid": bool(inv and not inv.get("used_by")), "note": (inv or {}).get("note")}


@app.post("/api/auth/register")
def auth_register(body: dict = Body(...)):
    """Cadastro com convite: cada pessoa tem dados, planilhas e Garmin separados."""
    code = body.get("code") or ""
    inv = db.get_invite(code)
    if not inv or inv.get("used_by"):
        raise HTTPException(400, "Convite inválido ou já usado. Peça um novo a quem te convidou.")
    email, pw = (body.get("email") or "").strip().lower(), body.get("password") or ""
    if "@" not in email:
        raise HTTPException(400, "E-mail inválido.")
    if db.get_account_by_email(email):
        raise HTTPException(409, "Já existe uma conta com esse e-mail. Entre com ela.")
    try:
        auth.validate_password(pw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    acc = db.create_account(email, auth.hash_password(pw), (body.get("name") or "").strip() or email.split("@")[0])
    db.use_invite(code, acc["user_id"])
    return _with_cookie(_me_payload(acc), acc)


# cadastro aberto: a pessoa cria a conta sozinha na tela de entrada (sem convite). O administrador
# pode fechar e há um limite de contas, para o link não virar porta aberta se vazar.
SIGNUP_DEFAULT = {"open": True, "max": 30}


def _signup_cfg() -> dict:
    return {**SIGNUP_DEFAULT, **json.loads(db.kv_get("signup", user_id=-1) or "{}")}


@app.get("/api/public/signup")
def signup_status():
    c = _signup_cfg()
    return {"open": bool(c["open"]) and db.count_accounts() < int(c["max"]) and auth.accounts_exist()}


@app.post("/api/auth/signup")
def auth_signup(body: dict = Body(...)):
    c = _signup_cfg()
    if not auth.accounts_exist():
        raise HTTPException(400, "O app ainda não tem dono: crie a primeira conta pelo Perfil.")
    if not c["open"] or db.count_accounts() >= int(c["max"]):
        raise HTTPException(403, "O cadastro está fechado. Peça para quem te mandou o app liberar.")
    name = (body.get("name") or "").strip()
    email, pw = (body.get("email") or "").strip().lower(), body.get("password") or ""
    if not name:
        raise HTTPException(400, "Diga seu nome.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "E-mail inválido.")
    if db.get_account_by_email(email):
        raise HTTPException(409, "Já existe uma conta com esse e-mail. Toque em Entrar.")
    try:
        auth.validate_password(pw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    acc = db.create_account(email, auth.hash_password(pw), name[:60])
    with db.as_user(acc["user_id"]):
        db.save_profile({"name": name[:60]})
    return _with_cookie(_me_payload(acc), acc)


@app.get("/api/admin/signup")
def admin_signup_get():
    _require_admin()
    c = _signup_cfg()
    return {**c, "accounts": db.count_accounts()}


@app.put("/api/admin/signup")
def admin_signup_put(body: dict = Body(...)):
    _require_admin()
    c = _signup_cfg()
    if "open" in body:
        c["open"] = bool(body["open"])
    if body.get("max"):
        c["max"] = max(1, min(500, int(body["max"])))
    db.kv_set("signup", json.dumps(c), user_id=-1)
    return {**c, "accounts": db.count_accounts()}


@app.post("/api/account/password")
def account_password(body: dict = Body(...)):
    acc = auth.current_account()
    if not acc:
        raise HTTPException(401, "Entre na sua conta.")
    if not auth.check_password(body.get("current") or "", acc["pw_hash"]):
        raise HTTPException(400, "Senha atual incorreta.")
    try:
        auth.validate_password(body.get("new") or "")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.set_password(acc["user_id"], auth.hash_password(body["new"]))
    return _with_cookie({"ok": True}, db.get_account(acc["user_id"]))


# ------------------------------------------- outros relógios (Apple / Android)

@app.post("/api/account/ingest-token")
def ingest_token_new():
    """Gera (ou troca) o código pessoal usado pelo Atalhos do iPhone e pelo app Android."""
    acc = auth.current_account()
    if not acc:
        raise HTTPException(400, "Crie sua conta (Perfil → Sua conta) antes de ligar outro relógio.")
    token, h = auth.new_ingest_token()
    db.set_ingest_token(acc["user_id"], h)
    return {"token": token}


@app.delete("/api/account/ingest-token")
def ingest_token_delete():
    acc = auth.current_account()
    if acc:
        db.set_ingest_token(acc["user_id"], None)
    return {"ok": True}


@app.get("/api/account/ingest")
def ingest_status():
    acc = auth.current_account()
    last = json.loads(db.kv_get("ingest_last") or "null")
    return {"has_token": bool(acc and acc.get("ingest_token")), "last": last}


@app.post("/api/ingest")
async def ingest_data(request: Request, authorization: str | None = Header(None), token: str | None = Query(None),
                      x_gymgarmin_token: str | None = Header(None)):
    """Recebe treinos e saúde do Atalhos do iPhone (Apple Saúde) ou do app Android (Health Connect)."""
    raw = (authorization or "").removeprefix("Bearer ").strip() or (x_gymgarmin_token or "").strip() or (token or "").strip()
    acc = db.account_by_ingest_token(auth.hash_token(raw)) if raw else None
    if not acc:
        raise HTTPException(401, "Código pessoal inválido. Gere um novo em Perfil → Outros relógios.")
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "Envie os dados em JSON.") from e
    tok = db.set_current_user(acc["user_id"])
    try:
        return await run_in_threadpool(_ingest_sync, body)
    finally:
        db._current_user.reset(tok)


def _ingest_sync(body) -> dict:
    p = _profile()
    try:
        r = ingest.ingest(body, hr_max(p.get("age"), p.get("hr_max")), merge=_merge_garmin)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    today = clock.today()
    if r.get("imported"):  # chegou treino de verdade: confere os dias de muitos passos que a pessoa já explicou
        try:
            _reconcile_step_events(today)
        except Exception:  # noqa: BLE001 - opcional
            pass
    if today.isoformat() in r["days"]:  # sono da noite chegou: prontidão do dia sem Garmin
        try:
            known = json.loads(db.kv_get(f"health:{today.isoformat()}") or "{}")
            db.kv_set(f"readiness:{today.isoformat()}", json.dumps(readiness.fetch(None, today, known)))
            view = _readiness_view(today)
            if view and _endurance_cfg(_profile()).get("readiness_auto"):
                _apply_readiness(today, view["action"])
        except Exception:  # noqa: BLE001 - prontidão é opcional
            pass
    return r


# --------------------------------------------------------------------- coach ----

def _coach() -> dict:
    acc = auth.current_account()
    if not acc or not db.is_coach(acc["user_id"]):
        raise HTTPException(403, "Área do coach: peça ao administrador para ativar seu acesso de coach.")
    return acc


def _athlete_or_403(coach_acc: dict, athlete_id: int) -> None:
    if not db.is_linked(coach_acc["user_id"], athlete_id):
        raise HTTPException(403, "Este atleta não te deu acesso de coach.")


def _athlete_snapshot(today: date, coach_id: int) -> dict:
    """Retrato do atleta atual (db.current_user) para a triagem do coach."""
    prof = _profile()
    since = (today - timedelta(days=60)).isoformat()
    sessions = db.list_sessions(since=since, limit=2000)
    snap: dict = {"name": prof.get("name") or "Atleta", "has_plan": bool(prof.get("schedule_plan_id")),
                  "limitations": prof.get("limitations") or []}
    last = max((s["date"][:10] for s in sessions), default=None)
    snap["last_training"] = last
    snap["days_inactive"] = (today - date.fromisoformat(last)).days if last else None
    ents = db.list_schedule((today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat())
    if ents:
        ents = schedule.sync_status(ents, [x for x in sessions if x["date"][:10] >= (today - timedelta(days=7)).isoformat()], today)
        planned = [e for e in ents if not e.get("race")]
        done = sum(1 for e in planned if e.get("status") == "feito")
        snap["planned_7d"], snap["done_7d"] = len(planned), done
        miss = 0
        for e in sorted(planned, key=lambda x: x["date"], reverse=True):
            if e.get("status") == "feito":
                break
            if e.get("status") in ("perdido", "planejado", "pulado"):
                miss += 1
        snap["missed_row"] = miss
        snap["compliance_7d"] = round(100 * done / len(planned)) if planned else None
    rv = _readiness_view(today) or _readiness_view(today - timedelta(days=1))
    if rv and rv.get("score") is not None:
        snap["readiness"] = rv["score"]
        snap["readiness_level"] = rv.get("level")
    share_health = db.kv_get(f"coach_share_health:{coach_id}") != "0"  # o atleta pode não compartilhar
    hd = _health_days(today, 36) if share_health else {}
    snap["health_shared"] = share_health
    if hd:
        rep = health.report(hd, None, {}, prof, today)
        snap["health_alerts"] = rep.get("alerts") or []
        snap["rhr_delta"] = (rep.get("rhr") or {}).get("delta")
        s7 = [(hd.get((today - timedelta(days=i)).isoformat()) or {}).get("sleep") or {} for i in range(7)]
        snap["sleep_7d"] = coach._mean([x.get("duration_h") for x in s7 if (x.get("duration_h") or 0) >= 1])
        snap["short_nights"] = sum(1 for x in s7 if 1 <= (x.get("duration_h") or 0) < 6)
        snap["hrv_low"] = coach.hrv_low(hd, today)
        ln = rep.get("last_night") or {}
        snap["last_night_h"] = ln.get("duration_h")
    pmc = load.pmc(sessions, prof, today=today, days=35)
    snap["form"] = pmc.get("form")
    snap.update(coach.load_numbers(pmc.get("series") or []))
    cks = coach.checkins(today, 29) if share_health else []
    if cks and cks[0]["date"] >= (today - timedelta(days=2)).isoformat():
        snap["wellness"] = {k: cks[0][k] for k in coach.WELLNESS if k in cks[0]}
        snap["wellness_z"] = coach.wellness_z(cks)
        snap["pain"] = cks[0].get("pain") or {}
        snap["pain_prev"] = next((c.get("pain") or {} for c in cks[1:8] if c.get("pain") is not None), {})
        snap["checkin_date"] = cks[0]["date"]
        if cks[0].get("note"):
            snap["checkin_note"] = cks[0]["note"]
    snap["falling"] = sum(1 for r in history.overview(sessions, today, prof.get("weight_kg"))["exercises"]
                          if r["summary"].get("status") == "caindo")
    snap["muted"] = json.loads(db.kv_get(f"coach_muted:{db.current_user()}", user_id=coach_id) or "[]")
    snap["suggestions"] = coach.pain_suggestions(cks, snap["limitations"], coach.settings(coach_id))
    return snap


@app.get("/api/coach/athletes")
def coach_athletes():
    acc = _coach()
    s = coach.settings(acc["user_id"])
    today = clock.today()
    out = []
    for aid in db.athletes_of(acc["user_id"]):
        with db.as_user(aid):
            try:
                snap = _athlete_snapshot(today, acc["user_id"])
            except Exception as e:  # noqa: BLE001 - um atleta com dado estranho não derruba o painel
                snap = {"name": db.get_profile(aid).get("name"), "error": f"{type(e).__name__}"}
        fl = coach.flags(snap, s) if "error" not in snap else []
        out.append({"id": aid, **snap, "flags": fl, "priority": coach.priority(fl)})
    out.sort(key=lambda a: (-a["priority"], a.get("name") or ""))
    return {"athletes": out, "settings": s, "regions": coach.REGIONS,
            "counts": {"agir": sum(1 for a in out if any(f["level"] == "agir" for f in a["flags"])),
                       "atencao": sum(1 for a in out if a["flags"] and not any(f["level"] == "agir" for f in a["flags"])),
                       "ok": sum(1 for a in out if not a["flags"])}}


@app.get("/api/coach/settings")
def coach_settings_get():
    return {"settings": coach.settings(_coach()["user_id"]), "defaults": coach.DEFAULTS}


@app.put("/api/coach/settings")
def coach_settings_put(body: dict = Body(...)):
    return {"settings": coach.save_settings(_coach()["user_id"], body)}


@app.post("/api/coach/athletes/{athlete_id}/note")
def coach_note(athlete_id: int, body: dict = Body(...)):
    acc = _coach()
    _athlete_or_403(acc, athlete_id)
    text = (body.get("text") or "").strip()[:500]
    if not text:
        raise HTTPException(400, "Escreva o recado.")
    name = _coach_name(acc)
    notes = json.loads(db.kv_get("coach_notes", user_id=athlete_id) or "[]")
    notes.append({"at": clock.now().isoformat(timespec="minutes"), "coach": name, "text": text, "read": False})
    db.kv_set("coach_notes", json.dumps(notes[-30:], ensure_ascii=False), user_id=athlete_id)
    return {"ok": True}


@app.post("/api/coach/athletes/{athlete_id}/mute")
def coach_mute(athlete_id: int, body: dict = Body(...)):
    acc = _coach()
    _athlete_or_403(acc, athlete_id)
    key = f"coach_muted:{athlete_id}"
    muted = set(json.loads(db.kv_get(key) or "[]"))
    (muted.add if body.get("on", True) else muted.discard)(body.get("code"))
    db.kv_set(key, json.dumps(sorted(m for m in muted if m)))
    return {"muted": sorted(muted)}


@app.post("/api/coach/athletes/{athlete_id}/tension")
def coach_tension(athlete_id: int, body: dict = Body(...)):
    """Área de tensão: marca a região (cuidado/evitar/nenhuma) e já troca os exercícios do plano."""
    acc = _coach()
    _athlete_or_403(acc, athlete_id)
    region, sev = body.get("region"), body.get("severity")
    if region not in coach.REGIONS or sev not in ("cuidado", "evitar", None, "", "nenhuma"):
        raise HTTPException(400, "Região ou nível inválido")
    with db.as_user(athlete_id):
        prof = _profile()
        lims = [l for l in prof.get("limitations") or [] if l.get("key") != region]
        if sev in ("cuidado", "evitar"):
            lims.append({"key": region, "severity": sev})
        db.save_profile({"limitations": lims})
        changes = []
        if body.get("apply", True):
            plan = schedule.schedule_plan(prof)
            if plan:
                plan, changes = coach.adapt_plan(plan, lims, _profile())
                if changes:
                    db.update_plan(plan["id"], plan)
                    schedule.rebuild_week(_profile())
        _coach_log(athlete_id, acc, "POST", "/api/profile")
    return {"limitations": lims, "changes": changes}


@app.post("/api/coach/athletes/{athlete_id}/adjust")
def coach_adjust(athlete_id: int, body: dict = Body(...)):
    """Ação rápida: hoje ou o resto da semana mais leve / descanso / trocar por regenerativo."""
    acc = _coach()
    _athlete_or_403(acc, athlete_id)
    action = body.get("action") or "moderar"
    if action not in readiness.ACTION_TEXT:
        raise HTTPException(400, "Ação inválida")
    today = clock.today()
    days = [today] if body.get("scope", "hoje") == "hoje" else [today + timedelta(days=i) for i in range(7 - today.weekday())]
    changed = []
    with db.as_user(athlete_id):
        schedule.ensure_week(_profile(), today)
        for d in days:
            changed += [e["day_name"] for e in _apply_readiness(d, action, force=True)]
        _coach_log(athlete_id, acc, "POST", "/api/readiness")
    return {"changed": changed}


@app.get("/api/coach/mine")
def coach_mine():
    """Atleta: quem pode ser meu coach e quem já tem acesso."""
    acc = auth.current_account()
    if not acc:
        return {"coaches": [], "linked": []}
    linked = set(db.coaches_of(acc["user_id"]))
    out = []
    for a in db.coaches():
        if a["user_id"] == acc["user_id"]:
            continue
        out.append({"id": a["user_id"], "name": _coach_name(a), "linked": a["user_id"] in linked,
                    "share_health": db.kv_get(f"coach_share_health:{a['user_id']}") != "0"})
    return {"coaches": out, "is_coach": db.is_coach(acc["user_id"])}


@app.post("/api/coach/mine/{coach_id}")
def coach_mine_add(coach_id: int):
    acc = auth.current_account()
    if not acc or not db.is_coach(coach_id) or coach_id == acc["user_id"]:
        raise HTTPException(400, "Coach inválido")
    db.link_coach(coach_id, acc["user_id"])
    return coach_mine()


@app.post("/api/coach/mine/{coach_id}/share")
def coach_mine_share(coach_id: int, body: dict = Body(...)):
    """O atleta escolhe se o coach vê saúde, sono, VFC e check-in (o treino ele sempre vê)."""
    db.kv_set(f"coach_share_health:{coach_id}", "1" if body.get("health", True) else "0")
    return coach_mine()


@app.delete("/api/coach/mine/{coach_id}")
def coach_mine_remove(coach_id: int):
    acc = auth.current_account()
    if acc:
        db.unlink_coach(coach_id, acc["user_id"])
    return coach_mine()


@app.get("/api/coach/inbox")
def coach_inbox():
    """Atleta: recados do coach e o que ele mudou (para aparecer em Hoje)."""
    notes = json.loads(db.kv_get("coach_notes") or "[]")
    log = json.loads(db.kv_get("coach_log") or "[]")
    seen = db.kv_get("coach_log_seen") or ""
    return {"notes": notes[-5:][::-1], "unread": sum(1 for n in notes if not n.get("read")),
            "changes": [x for x in log if x["at"] > seen][-5:][::-1]}


@app.post("/api/coach/inbox/read")
def coach_inbox_read():
    notes = json.loads(db.kv_get("coach_notes") or "[]")
    for n in notes:
        n["read"] = True
    db.kv_set("coach_notes", json.dumps(notes, ensure_ascii=False))
    db.kv_set("coach_log_seen", clock.now().isoformat(timespec="minutes"))
    return {"ok": True}


@app.get("/api/checkin")
def checkin_get():
    today = clock.today()
    cks = coach.checkins(today, 14)
    return {"today": next((c for c in cks if c["date"] == today.isoformat()), None), "recent": cks[:7],
            "regions": coach.REGIONS, "wellness": coach.WELLNESS}


@app.post("/api/checkin")
def checkin_post(body: dict = Body(...)):
    rec = coach.save_checkin(clock.today(), body)
    # dor alta: sugere a área de tensão ao próprio atleta (o coach também vê)
    sugg = coach.pain_suggestions(coach.checkins(clock.today(), 7), _profile().get("limitations") or [],
                                  coach.DEFAULTS)
    return {"checkin": rec, "suggestions": sugg}


@app.post("/api/admin/users/{user_id}/coach")
def admin_set_coach(user_id: int, body: dict = Body(default={})):
    _require_admin()
    if not db.get_account(user_id):
        raise HTTPException(404, "Usuário não encontrado")
    db.set_coach(user_id, bool(body.get("on", True)))
    return {"ok": True, "is_coach": db.is_coach(user_id)}


@app.get("/api/admin/users")
def admin_users():
    _require_admin()
    return {"users": db.list_accounts(), "invites": [i for i in db.list_invites() if not i.get("used_by")]}


@app.post("/api/admin/invites")
def admin_invite(body: dict = Body(default={})):
    acc = _require_admin()
    return db.create_invite(auth.new_invite_code(), acc["user_id"], (body.get("note") or "").strip() or None)


@app.delete("/api/admin/invites/{code}")
def admin_invite_delete(code: str):
    _require_admin()
    db.delete_invite(code)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int):
    """Senha provisória para quem esqueceu (o admin repassa; a pessoa troca no Perfil)."""
    _require_admin()
    if not db.get_account(user_id):
        raise HTTPException(404, "Usuário não encontrado")
    temp = auth.new_invite_code()
    db.set_password(user_id, auth.hash_password(temp))
    return {"temporary_password": temp}


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
              hide_avoid: bool = False, train: bool = False):
    lims = _profile().get("limitations") or []
    ov = db.garmin_overrides()
    found = catalog.search(q, muscle, equipment, kind, level, limit=1000 if train and muscle and not q else limit * 2)
    if kind == "cardio" and not q.strip():  # lista de aparelhos de cardio: futebol, yoga etc. só aparecem quando a pessoa busca pelo nome
        found = [e for e in found if not e.get("activity")]
    if train and muscle and not q:  # "quero treinar este músculo": os exercícios certos primeiro
        mkey = catalog.MUSCLE_SEARCH_PT.get(catalog.strip_accents(muscle), muscle)
        found = my_gym.rank_for_muscle(found, mkey)[:limit * 2]
    res = [_with_status(e, lims, ov) for e in found]
    if hide_avoid:
        res = [r for r in res if r["limitation"]["status"] != "evitar"]
    for r in res:
        r.pop("instructions", None)
    return res[:limit]


PICTO = [(("corrida", "esteira_corrida"), "run"), (("ciclismo", "bike", "spinning"), "bike"), (("natacao",), "swim"),
         (("remo",), "row"), (("trilha",), "hike"), (("caminhada",), "walk"), (("ski",), "ski"), (("eliptico",), "elliptical"),
         (("escada",), "stairs"), (("corda",), "rope"), (("kettlebell",), "kettlebell"), (("esteira_curva",), "run"),
         (("treno",), "sled"), (("ergometro_braco",), "armerg"), (("versaclimber",), "climber"),
         (("atividade_mobilidade",), "yoga"), (("atividade_coletivo",), "ball"), (("atividade_raquete",), "racket"),
         (("atividade_luta",), "glove"), (("atividade_escalada",), "climber"), (("atividade_aquatica",), "swim"),
         (("atividade_neve",), "ski"), (("atividade_danca",), "rope"), (("atividade_",), "pulse")]


@app.get("/api/media")
def media_index():
    """Imagens de todos os exercícios (2 quadros: início e fim do movimento) e pictograma para quem não tem foto.
    Vai em cache no aparelho: as telas mostram a foto animada de qualquer exercício da agenda/planilha."""
    base = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"
    out: dict = {"base": base, "f": {}, "p": {}}
    for ex in catalog.all_exercises():
        im = ex.get("images") or []
        if len(im) >= 2 and im[0].startswith(base):
            folder = im[0][len(base):].rsplit("/", 1)[0]
            out["f"][ex["id"]] = "" if folder == ex["id"] else folder  # "" = mesma pasta do id
        lid = ex["id"].lower()
        pic = ex.get("picto") or next((k for keys, k in PICTO if any(x in lid for x in keys)), None)
        if pic and not im:
            out["p"][ex["id"]] = pic
        elif not im:
            out["p"][ex["id"]] = "dumbbell"
    return JSONResponse(out, headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------ aparelhos ----

@app.get("/api/machines")
def machines(q: str = "", category: str | None = None):
    return {"categories": [{"key": k, "pt": t} for k, t in gallery.CATEGORIES], "items": gallery.search(q, category)}


@app.get("/api/machines/{machine_id}")
def machine_detail(machine_id: str):
    m = gallery.get(machine_id)
    if not m:
        raise HTTPException(404, "Aparelho não encontrado")
    out = gallery.detail(m)
    out["category_pt"] = gallery.category_name(m.get("category") or "")
    out["linked"] = [{"id": eid, "name": (catalog.get(eid) or {}).get("name", eid)} for eid in m.get("existing_exercise_ids") or []
                     if catalog.get(eid)]
    return out


@app.get("/api/public/version")
def app_version():
    """Versão dos arquivos do app no ar: o app aberto compara com a dele e se atualiza sozinho."""
    return JSONResponse({"v": ASSET_V}, headers={"Cache-Control": "no-store"})


_MEDIA_CHECK: dict = {}


@app.get("/api/public/media-check")
def media_check(start: int = 0, count: int = Query(25, le=40)):
    """Confere no ar os vídeos (YouTube oEmbed) e fotos (Commons) da galeria. Só as URLs do nosso arquivo; resultado
    guardado por 1 h para não virar fonte de tráfego."""
    import time as _t
    key = (start, count)
    hit = _MEDIA_CHECK.get(key)
    if hit and _t.time() - hit[0] < 3600:
        return hit[1]
    cands = [{"machine_id": m["machine_id"],
              "videos": list(dict.fromkeys([v.get("url") for v in m.get("videos") or [] if v.get("url")]
                                           + [(e.get("video") or {}).get("url") for e in m.get("exercises") or [] if (e.get("video") or {}).get("url")])),
              "photos": [p.get("url") for p in m.get("photos") or [] if p.get("url")]} for m in gallery.load()[start:start + count]]
    res = {"start": start, "count": len(cands), "total": len(gallery.load()), "results": gallery.check_media(cands)}
    res["saved"] = gallery.save_status(res["results"])
    res = {k: v for k, v in res.items() if k != "results"} | {"summary": {mid: {"videos": [v["status"] for v in r["videos"]], "photos": [p["status"] for p in r["photos"]]}
                                                              for mid, r in res["results"].items()}}
    _MEDIA_CHECK[key] = (_t.time(), res)
    return res


@app.get("/api/exercises/{exercise_id}")
def exercise_detail(exercise_id: str):
    ex = catalog.get(exercise_id)
    if not ex:
        raise HTTPException(404, "Exercício não encontrado")
    out = _with_status(ex, _profile().get("limitations") or [], db.garmin_overrides())
    out["last"] = db.last_performance(exercise_id)
    out["machine_id"] = gallery.by_exercise().get(exercise_id)
    out["my_machine"] = my_gym.for_exercise(exercise_id)
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
    if "focus" in body:
        body["focus"] = focus.normalize(body.get("focus"))
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
    prof = _profile()
    lims, ovr, cache = prof.get("limitations") or [], db.garmin_overrides(), {}
    for d in p.get("days", []):
        d["items"] = training_rules.apply(d.get("items", []), prof, warmups=False)  # mostra o descanso que vale hoje
        for it in d.get("items", []):
            eid = it.get("exercise_id")
            if eid not in cache:
                ex = catalog.get(eid)
                cache[eid] = _with_status(ex, lims, ovr) if ex else None
            if cache[eid]:
                it["_ex"] = cache[eid]
    return p


def _me_ids() -> tuple[int, str | None, bool]:
    acc = auth.current_account()
    uid = db.current_user()
    is_admin = (not auth.accounts_exist()) or bool(acc and acc.get("is_admin"))
    return uid, (db.get_profile(uid).get("name") or (acc or {}).get("name")), is_admin


# ------------------------------------------------------- minha academia ----

@app.get("/api/focus/options")
def focus_options():
    return focus.options()


@app.get("/api/focus/progress")
def focus_progress():
    prof = _profile()
    f = focus.active(prof)
    if not f:
        return {"focus": None}
    plan = next((p for p in db.list_plans() if p.get("id") == prof.get("schedule_plan_id")), None) or {}
    targets = (plan.get("generator") or {}).get("targets")
    applied = (plan.get("generator") or {}).get("focus")
    out = focus.progress(f, db.list_sessions(limit=400), targets)
    out["applied"] = bool(applied and applied.get("muscles") == f["muscles"] and applied.get("skills") == f["skills"])
    return out


@app.get("/api/my-gym/machines")
def my_gym_list():
    return my_gym.list_mine()


@app.post("/api/my-gym/machines")
def my_gym_add(body: dict = Body(...)):
    uid, name, _ = _me_ids()
    try:
        return my_gym.add_mine(uid, name, body.get("image"), body.get("exercise_id") or "", body.get("name") or "",
                               body.get("settings"), bool(body.get("suggest")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/my-gym/machines/{mid}")
def my_gym_update(mid: str, body: dict = Body(...)):
    out = my_gym.update_mine(db.current_user(), mid, body)
    if not out:
        raise HTTPException(404, "Aparelho não encontrado")
    return out


@app.delete("/api/my-gym/machines/{mid}")
def my_gym_delete(mid: str):
    if not my_gym.delete_mine(db.current_user(), mid):
        raise HTTPException(404, "Aparelho não encontrado")
    return {"ok": True}


@app.get("/api/photos/{pid}")
def photo_get(pid: str):
    p = my_gym.photo(pid)
    uid, _, is_admin = _me_ids()
    if not p or not my_gym.can_see(p, uid, is_admin):
        raise HTTPException(404, "Foto não encontrada")
    import base64 as _b64
    return Response(_b64.b64decode(p["b64"]), media_type=p["mime"],
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.get("/api/exercises/{exercise_id}/alternatives")
def exercise_alternatives(exercise_id: str):
    lims, ovr = _profile().get("limitations") or [], db.garmin_overrides()
    out = []
    for eid in my_gym.alternatives(exercise_id):
        ex = catalog.get(eid)
        v = _with_status(ex, lims, ovr)
        out.append({"id": eid, "name": v["name"], "equipment_pt": v.get("equipment_pt"), "limitation": v.get("limitation"),
                    "kind": ex["kind"], "timed": ex.get("timed"), "my_machine": my_gym.for_exercise(eid)})
    out.sort(key=lambda x: ((x["limitation"] or {}).get("status") == "evitar", not x["my_machine"]))
    return out


@app.post("/api/sessions/item")
def session_item(body: dict = Body(...)):
    """Item de treino pronto (sugestão de carga, última vez, aquecimento) para trocar o exercício no meio do treino."""
    ex = catalog.get(body.get("exercise_id"))
    if ex and ex.get("timed") and not body.get("duration_s"):
        body = {**body, "duration_s": 40}
        body.pop("reps", None)
    elif ex and not ex.get("timed") and body.get("duration_s") and not body.get("reps"):
        body = {**body, "reps": "10"}
        body.pop("duration_s", None)
    items = _prefill_items([body])
    if not items:
        raise HTTPException(404, "Exercício desconhecido")
    return items[0]


@app.post("/api/plans/{plan_id}/swap")
def plan_swap(plan_id: int, body: dict = Body(...)):
    """Troca o exercício em toda a planilha e nos próximos treinos da agenda (a academia não tem / prefere outro)."""
    src, dst = body.get("from"), body.get("to")
    if not catalog.get(dst):
        raise HTTPException(400, "Exercício desconhecido")
    p = _plan_or_404(plan_id)
    n = 0
    for d in p.get("days", []):
        for it in d.get("items", []):
            if it.get("exercise_id") == src:
                it["exercise_id"] = dst
                if catalog.get(dst).get("timed") and not it.get("duration_s"):
                    it.pop("reps", None)
                    it["duration_s"] = 40
                n += 1
    db.update_plan(plan_id, p)
    prefs = json.loads(db.kv_get("swap_prefs") or "{}")  # lembra a troca: ao regerar o treino ela continua valendo
    prefs = {k: v for k, v in prefs.items() if k != dst and v != src}
    prefs[src] = dst
    db.kv_set("swap_prefs", json.dumps(prefs))
    today = clock.today()
    for e in db.list_schedule(today.isoformat(), (today + timedelta(days=21)).isoformat()):
        if e.get("status") != "planejado":
            continue
        hit = False
        for it in e.get("items", []):
            if it.get("exercise_id") == src:
                it["exercise_id"] = dst
                hit = True
        if hit:
            if e.get("garmin_workout_id"):
                e["garmin_outdated"] = True
            db.save_entry(e)
    return {"changed": n}


@app.post("/api/feedback")
def send_feedback(body: dict = Body(...)):
    uid, name, _ = _me_ids()
    try:
        my_gym.add_feedback(uid, name, body.get("text") or "", body.get("where"), body.get("device"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.post("/api/dev/feedback/{fid}/done")
def dev_feedback_done(fid: str, body: dict = Body(default={})):
    _dev()
    if not my_gym.feedback_done(fid, bool(body.get("done", True))):
        raise HTTPException(404, "Não encontrado")
    return {"ok": True}


# ------------------------------------------------------- área do desenvolvedor ----

def _dev() -> None:
    if not _me_ids()[2]:
        raise HTTPException(403, "Área do desenvolvedor: só o administrador.")


@app.get("/api/dev/queue")
def dev_queue():
    _dev()
    return {"pending": my_gym.queue("pendente"), "approved": my_gym.queue("aprovado")[:20], "feedback": my_gym.feedback(),
            "custom": [gallery.summary(m) for m in my_gym.custom_machines()],
            "categories": [{"id": k, "name": v} for k, v in gallery.CATEGORIES],
            "machines": [{"id": m["machine_id"], "name": m["name"]} for m in gallery.load()]}


@app.post("/api/dev/queue/{qid}/approve")
def dev_approve(qid: str, body: dict = Body(...)):
    _dev()
    try:
        m = my_gym.approve(qid, body.get("name") or "", body.get("category") or "", list(body.get("exercise_ids") or []),
                           body.get("what"), body.get("merge_into") or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"machine_id": m["machine_id"]}


@app.post("/api/dev/queue/{qid}/discard")
def dev_discard(qid: str):
    _dev()
    if not my_gym.discard(qid):
        raise HTTPException(404, "Item não encontrado")
    return {"ok": True}


@app.post("/api/dev/machines")
def dev_add_machine(body: dict = Body(...)):
    """Administrador fotografa e publica direto (vai pela fila já aprovada, para ficar registrado)."""
    _dev()
    uid, name, _ = _me_ids()
    try:
        pid = my_gym.save_photo(body.get("image") or "", uid)
        q = my_gym.submit(pid, uid, name, (body.get("exercise_ids") or [None])[0], body.get("name") or "", None)
        m = my_gym.approve(q["qid"], body.get("name") or "", body.get("category") or "", list(body.get("exercise_ids") or []),
                           body.get("what"), body.get("merge_into") or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"machine_id": m["machine_id"]}


@app.delete("/api/dev/machines/{machine_id}")
def dev_delete_machine(machine_id: str):
    _dev()
    if not my_gym.delete_custom(machine_id):
        raise HTTPException(404, "Aparelho não encontrado")
    return {"ok": True}


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
        data = garmin_export.to_fit(day, plan["name"], db.garmin_overrides(), prefs=_profile())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    fname = garmin_export.fit_filename(plan["name"], day.get("name", str(idx)))
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/plans/{plan_id}/days/{idx}/connect.json")
def day_connect_json(plan_id: int, idx: int, download: bool = False):
    plan = _plan_or_404(plan_id)
    try:
        payload = garmin_export.to_connect_json(_day(plan, idx), plan["name"], db.garmin_overrides(), prefs=_profile())
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
    payload = garmin_export.to_connect_json(day, plan["name"], db.garmin_overrides(), prefs=_profile())
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
        if body.get("with_partner") is not None:  # só vale parceiro de verdade (o campo vem da tela)
            acc = auth.current_account()
            pid = body.get("with_partner")
            if not (acc and isinstance(pid, int) and not isinstance(pid, bool) and partners.is_partner(acc["user_id"], pid)):
                body.pop("with_partner", None)
        if "duration_min" in body:  # tempo do treino vindo da tela: número plausível ou nada
            dm = body.get("duration_min")
            if isinstance(dm, (int, float)) and not isinstance(dm, bool) and 1 <= dm <= 600:
                body["duration_min"] = round(float(dm), 1)
            else:
                body.pop("duration_min")
        target = body.pop("merge_into", None)
        watch = db.get_session(target) if isinstance(target, int) and not isinstance(target, bool) else None
        if watch and dedupe.is_time_only(watch) and str(watch.get("date"))[:10] == str(body.get("date") or "")[:10]:
            # a pessoa apontou a atividade do relógio (que só marcou o tempo) como sendo este treino
            saved = db.save_session(dedupe.merge_into_watch(body, watch), session_id=watch["id"])
            credit = schedule.credit_session(saved, _profile())
            saved = db.get_session(saved["id"]) or saved
            return {**saved, "prs": history.session_prs(db.list_sessions(limit=5000), saved, _profile().get("weight_kg")),
                    "credit": credit}
        twin = None if body.get("garmin_activity_id") else \
            dedupe.find_twin(body, db.list_sessions(since=str(body.get("date") or "")[:10] or None, limit=200))
        if twin:  # o relógio já trouxe este treino: junta (séries do app + medidas do relógio)
            saved = db.save_session(dedupe.merge(body, twin), session_id=twin["id"])
        else:
            saved = db.save_session(body)
        # que treino da agenda esta sessão fecha (pelo conteúdo) e o que ainda falta na semana
        credit = schedule.credit_session(saved, _profile())
        saved = db.get_session(saved["id"]) or saved
        return {**saved, "prs": history.session_prs(db.list_sessions(limit=5000), saved, _profile().get("weight_kg")),
                "credit": credit}
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
    old = db.get_session(session_id)
    if not old:
        raise HTTPException(404, "Sessão não encontrada")
    # a edição não apaga o vínculo com a agenda nem a decisão "foi o treino X / é um extra"
    keep = {k: old[k] for k in ("schedule_id", "client_id", "extra", "match_pending") if k in old and k not in body}
    saved = db.save_session({**keep, **body}, session_id=session_id)
    from .credit import COVER_ASK
    schedule.reopen_for_session(saved, min_cover=COVER_ASK)  # editou até não cobrir mais o treino: ele volta à agenda
    return saved


@app.delete("/api/sessions/{session_id}")
def del_session(session_id: int):
    s = db.get_session(session_id)
    if s:
        schedule.reopen_for_session(s)  # o treino que esta sessão tinha fechado volta para a agenda
        try:  # veio do relógio/celular: fica marcado como apagado, senão a próxima sincronização traria de volta
            garmin_sync.forget_activity(s.get("garmin_activity_id"))
            ingest.forget_workout(s.get("ext_id"))
        except Exception:  # noqa: BLE001
            pass
        if steps.is_event(s):  # caminhada estimada pelos passos: a marca do dia fica (não pergunta de novo), sem a sessão
            tags = steps.load_tags(db.kv_get(STEP_TAGS_KEY))
            t = tags.get(str(s.get("date"))[:10])
            if t and t.get("session_id") == session_id:
                t.pop("session_id", None)
                t.pop("minutes", None)
                db.kv_set(STEP_TAGS_KEY, json.dumps(tags))
    db.delete_session(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/match")
def match_session(session_id: int, body: dict = Body(...)):
    """Resposta a "esse treino livre foi o treino X?": {schedule_id} = foi esse; {extra: true} = é um extra."""
    extra, sid = body.get("extra") is True, body.get("schedule_id")
    if not extra and not (isinstance(sid, int) and not isinstance(sid, bool) and sid > 0):
        raise HTTPException(400, "Diga se foi o treino da agenda (schedule_id) ou um treino extra (extra: true)")
    try:
        return schedule.match_session(session_id, _profile(), None if extra else sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _prefill_items(day_items: list[dict], deload: bool = False) -> list[dict]:
    items = []
    prof = _profile()
    lims = prof.get("limitations") or []
    for it in training_rules.apply(day_items, prof):
        ex = catalog.get(it.get("exercise_id"))
        if not ex:
            continue
        last = db.last_performance(ex["id"])
        entry = {"exercise_id": ex["id"], "name": ex["name"], "kind": ex["kind"], "plan": it,
                 "my_machine": my_gym.for_exercise(ex["id"]),
                 "phase": it.get("phase") or "principal", "last": last,
                 "limitation": limitations.check_exercise(ex, lims), "images": ex["images"][:2], "timed": ex.get("timed")}
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
            try:
                sug = progression.suggest(it, last, deload=deload or it.get("deload", False), today=clock.today()) \
                    if entry["phase"] == "principal" and not ex.get("timed") else {"load_kg": it.get("load_kg"), "note": None}
            except Exception:  # noqa: BLE001 — um histórico estranho de UM exercício nunca pode impedir o treino de abrir
                sug = {"load_kg": it.get("load_kg"), "note": None}
            entry["suggestion"] = sug
            reps, _ = garmin_export.parse_reps(it.get("reps"))
            entry["sets"] = [{"reps": reps if not it.get("duration_s") else None,
                              "duration_s": it.get("duration_s"), "load_kg": sug.get("load_kg"), "rpe": None, "done": False}
                             for _ in range(int(it.get("sets") or (1 if entry["phase"] != "principal" else 3)))]
            # séries de aquecimento do próprio exercício (não contam como séries válidas)
            entry["warmup"] = [{"pct": w["pct"], "reps": w["reps"], "done": False,
                                "load_kg": training_rules.warm_load(sug.get("load_kg"), w["pct"], ex.get("equipment"))}
                               for w in it.get("warmup_sets") or []]
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

# ---- treinar com o companheiro: parceria, puxar o treino do outro, placar

def _me_id() -> int:
    acc = auth.current_account()
    if not acc:
        raise HTTPException(400, "Treinar com alguém precisa de conta: crie a sua em Perfil.")
    return int(acc["user_id"])


def _partner_or_403(pid: int) -> int:
    uid = _me_id()
    if not partners.is_partner(uid, pid):
        raise HTTPException(403, "Vocês ainda não são parceiros de treino.")
    return uid


def _partner_day(v: str | None) -> date:
    try:
        d = date.fromisoformat(str(v)[:10]) if v else clock.today()
    except ValueError as e:
        raise HTTPException(400, "Data inválida") from e
    if d > clock.today() or (clock.today() - d).days > 60:
        raise HTTPException(400, "Só dá para ver os últimos 60 dias")
    return d


@app.get("/api/partners")
def partners_get():
    """Com quem treino, quem me convidou e quem posso convidar; para cada parceiro, o dia de hoje e o placar da semana."""
    acc = auth.current_account()
    if not acc:
        return {"enabled": False, "people": [], "partners": [], "requests": []}
    uid, today = int(acc["user_id"]), clock.today()
    out = partners.overview(uid)
    for p in out["partners"]:
        d = partners.day(uid, p["id"], today)
        p["today"] = {"planned": [{"id": e["id"], "title": e["title"], "has_sets": e["has_sets"]} for e in d["planned"]],
                      "done": [{"id": s["id"], "title": s["title"], "minutes": s["minutes"], "kcal": s["kcal"], "has_sets": s["has_sets"]} for s in d["done"]]}
        du = partners.duel(uid, p["id"], today, today)
        p["duel"] = {"metrics": du["metrics"], "score": du["score"], "kcal_estimated": du["kcal_estimated"],
                     "est": {"me": du["me"]["kcal_estimated"], "partner": du["partner"]["kcal_estimated"]}}
        # treinaram juntos ontem/anteontem e eu ainda não registrei nada com séries nesse dia: o cartão lembra de puxar
        p["recent"] = None
        for back in (1, 2):
            prev = partners.day(uid, p["id"], today - timedelta(days=back))
            theirs = [s for s in prev["done"] if s["has_sets"]]
            if theirs and not any(m.get("has_sets") for m in prev["mine"]):
                p["recent"] = {"date": prev["date"], "days_ago": back, "id": theirs[-1]["id"], "title": theirs[-1]["title"],
                               "minutes": theirs[-1]["minutes"]}
                break
    return {"enabled": True, **out}


@app.post("/api/partners/{pid}/invite")
def partners_invite(pid: int):
    try:
        status = partners.invite(_me_id(), pid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": status, **partners_get()}


@app.post("/api/partners/{pid}/accept")
def partners_accept(pid: int):
    try:
        partners.accept(_me_id(), pid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return partners_get()


@app.delete("/api/partners/{pid}")
def partners_remove(pid: int):
    partners.remove(_me_id(), pid)
    return partners_get()


@app.get("/api/partners/{pid}/day")
def partners_day(pid: int, date_: str | None = Query(None, alias="date")):
    uid = _partner_or_403(pid)
    return partners.day(uid, pid, _partner_day(date_))


@app.get("/api/partners/{pid}/duel")
def partners_duel(pid: int, date_: str | None = Query(None, alias="date")):
    uid = _partner_or_403(pid)
    return partners.duel(uid, pid, _partner_day(date_), clock.today())


@app.post("/api/partners/{pid}/draft")
def partners_draft(pid: int, body: dict = Body(...)):
    """Monta o MEU treino a partir do treino do parceiro: {session_id} = o que ele já fez (as séries vêm marcadas, para
    quem treinou só com o relógio); {entry_id} = o planejado dele (para treinar junto agora). As cargas são as minhas."""
    uid = _partner_or_403(pid)
    names = partners._names()
    who = partners.first_name(names.get(pid, ""))
    if body.get("session_id") is not None:
        s = db.get_session(int(body["session_id"]), user_id=pid)
        if not s or str(s.get("source")) == "passos":
            raise HTTPException(404, "Treino do parceiro não encontrado")
        plan_items, done = partners.plan_items_from_session(s)
        if not plan_items:
            raise HTTPException(400, f"Esse treino de {who} não tem exercícios com séries para puxar (só tempo).")
        items = _prefill_items(plan_items)
        for it in items:  # é um treino já feito: mesmas séries e repetições dele, marcadas; a carga é a minha (ou em branco)
            theirs = done.get(it["exercise_id"]) or []
            sets = []
            for k, x in enumerate(theirs):
                mine = it["sets"][k] if k < len(it.get("sets") or []) else (it["sets"][-1] if it.get("sets") else {})
                sets.append({"reps": x.get("reps"), "duration_s": x.get("duration_s"), "load_kg": mine.get("load_kg"), "rpe": None,
                             "done": True, "partner_load": x.get("load_kg")})
            it["sets"] = sets or it.get("sets")
            it["warmup"] = []
            it["from_partner"] = True
        day_iso = str(s.get("date"))[:10]
        mine = [x for x in db.list_sessions(uid, since=day_iso, limit=60) if str(x.get("date"))[:10] == day_iso]
        watch = [x for x in mine if dedupe.is_time_only(x)]
        # o tempo MEDIDO do treino dele (relógio ou anotado) serve de ponto de partida para o meu: treinamos juntos
        dur = round(float(s.get("duration_min") or (s.get("garmin_summary") or {}).get("duration_min") or 0)) or None
        return {"date": str(s.get("date"))[:16], "day_name": f"{s.get('day_name') or 'Treino'} (com {who})", "view": "lista",
                "with_partner": pid, "partner_name": who, "pulled": True, "items": items, "duration_min": dur,
                "merge_candidates": [{"id": x["id"], "time": str(x.get("date"))[11:16], "minutes": round(partners.session_minutes(x)),
                                      "kcal": round(partners.session_kcal(x, None)[0]) if not partners.session_kcal(x, None)[1] else None,
                                      "title": x.get("notes") or x.get("day_name") or "Atividade do relógio"} for x in watch],
                "already": [x["id"] for x in mine if x.get("with_partner") == pid and any(i.get("sets") for i in x.get("items") or [])]}
    if body.get("entry_id") is not None:
        e = db.get_entry(int(body["entry_id"]), user_id=pid)
        if not e:
            raise HTTPException(404, "Treino do parceiro não encontrado")
        plan_items = partners.plan_items_from_entry(e)
        if not plan_items:
            raise HTTPException(400, f"O treino de {who} nesse dia não tem musculação para fazer junto.")
        return {"date": clock.now().isoformat(timespec="minutes"), "day_name": f"{e.get('day_name') or 'Treino'} (com {who})",
                "with_partner": pid, "partner_name": who, "items": _prefill_items(plan_items)}
    raise HTTPException(400, "Diga qual treino do parceiro (session_id ou entry_id)")


# ---- séries do relógio sem nome: conferir categorias e dar nome aos blocos

def _watch_prefs() -> dict:
    return watch_labels.load_prefs(db.kv_get(watch_labels.PREFS_KEY))


def _watch_review() -> dict:
    today = clock.today()
    lo = (today - timedelta(days=watch_labels.REVIEW_DAYS + 7)).isoformat()
    plan_ids = [it.get("exercise_id") for p in db.list_plans() for d in p.get("days", []) for it in d.get("items", [])]
    return watch_labels.review(db.list_sessions(limit=5000), db.list_schedule(lo, today.isoformat()), _watch_prefs(), today, plan_ids)


def _strength_exercise(exercise_id) -> dict:
    ex = catalog.get(str(exercise_id or ""))
    if not ex or ex.get("kind") != "strength" or ex["id"] in garmin_sync.WATCH_ONLY_IDS:
        raise HTTPException(400, "Escolha um exercício de força do catálogo")
    return ex


@app.get("/api/watch/review")
def watch_review():
    return _watch_review()


@app.post("/api/watch/category")
def watch_category(body: dict = Body(...)):
    """"Quando o relógio diz só 'rosca', é este exercício": vale para os treinos já importados e para os próximos."""
    cat = str(body.get("category") or "").upper()
    if not cat or cat == "UNKNOWN":
        raise HTTPException(400, "Categoria inválida")
    ex = _strength_exercise(body.get("exercise_id"))
    changed = watch_labels.apply_category(db.list_sessions(limit=5000), cat, ex["id"])
    for s in changed:
        db.save_session(s, session_id=s["id"])
    prefs = _watch_prefs()
    prefs.setdefault("categories", {})[cat] = ex["id"]
    db.kv_set(watch_labels.PREFS_KEY, json.dumps(prefs))
    return {"changed": len(changed), "exercise": {"id": ex["id"], "name": ex["name"]}, **_watch_review()}


@app.post("/api/watch/label")
def watch_label(body: dict = Body(...)):
    """Dá nome a um bloco de séries que o relógio gravou sem exercício: {session_id, item, from, to, exercise_id}."""
    ex = _strength_exercise(body.get("exercise_id"))
    try:
        sid, item, a, b = (int(body.get(k)) for k in ("session_id", "item", "from", "to"))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "Bloco de séries inválido") from e
    s = db.get_session(sid)
    if not s:
        raise HTTPException(404, "Treino não encontrado")
    try:
        watch_labels.apply_label(s, item, a, b, ex["id"], expect=body.get("expect"))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    db.save_session(s, session_id=s["id"])
    return {"exercise": {"id": ex["id"], "name": ex["name"]}, **_watch_review()}


@app.post("/api/watch/unlabel")
def watch_unlabel(body: dict = Body(...)):
    """Desfaz o nome dado a um bloco de séries: {session_id, item}."""
    try:
        sid, item = int(body.get("session_id")), int(body.get("item"))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "Bloco inválido") from e
    s = db.get_session(sid)
    if not s:
        raise HTTPException(404, "Treino não encontrado")
    try:
        watch_labels.unlabel(s, item)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    db.save_session(s, session_id=s["id"])
    return _watch_review()


@app.post("/api/watch/dismiss")
def watch_dismiss(body: dict = Body(...)):
    """"Não lembro": para de perguntar sobre esse treino (as séries continuam contando no volume)."""
    try:
        sid = int(body.get("session_id"))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "Treino inválido") from e
    prefs = _watch_prefs()
    rest = [x for x in prefs.get("dismissed") or [] if x != sid]
    prefs["dismissed"] = rest if body.get("undo") else (rest + [sid])[-200:]  # undo = "perguntar de novo"
    db.kv_set(watch_labels.PREFS_KEY, json.dumps(prefs))
    return _watch_review()


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


def _fill_activity_steps(by_activity: dict) -> int:
    """Corridas e caminhadas importadas sem os passos da atividade: completa com o que o relógio informou."""
    if not by_activity:
        return 0
    n = 0
    since = (clock.today() - timedelta(days=45)).isoformat()
    for s in db.list_sessions(since=since, limit=2000):
        got = by_activity.get(str(s.get("garmin_activity_id") or ""))
        if not got or not got.get("steps"):
            continue
        its = [it for it in s.get("items") or [] if load.sport_of(it) in ("corrida", "caminhada", "trilha")
               or it.get("exercise_id") in garmin_sync.GYM_MACHINES.values()]
        if len(its) != 1 or its[0].get("steps"):
            continue
        try:
            its[0]["steps"] = int(float(got["steps"]))
            if got.get("cadence") and 40 <= float(got["cadence"]) <= 260:
                its[0]["cadence"] = round(float(got["cadence"]))
        except (TypeError, ValueError):
            continue
        db.save_session(s, session_id=s["id"])
        n += 1
    return n


def _run_sync(trigger: str) -> dict:
    t0 = time.monotonic()
    plan_ids = {it.get("exercise_id") for p in db.list_plans() for d in p.get("days", []) for it in d.get("items", [])}
    _remap_garmin_once(plan_ids)
    p = _profile()
    try:
        r = garmin_sync.sync(db.save_session, db.garmin_imported_ids(), plan_ids,
                             hr_max(p.get("age"), p.get("hr_max")), trigger=trigger, merge=_merge_garmin,
                             until=t0 + SYNC_BUDGET_S, cat_prefs=watch_labels.category_prefs(_watch_prefs()))
    except garmin_sync.GarminUnavailable as e:
        raise HTTPException(501, str(e)) from e
    try:
        _fill_activity_steps(r.pop("activity_steps", None) or {})
        if r.get("imported"):
            _reconcile_step_events()
    except Exception:  # noqa: BLE001 - opcional
        pass
    if garmin_sync.current() is not None:  # mesmo se a importação parou num 429, a prontidão é outra chamada
        today = clock.today()
        until = t0 + EXTRAS_BUDGET_S
        try:  # saúde e sono (sono, HRV, FC de repouso, estresse, passos; VO2máx de tempos em tempos)
            health.collect(garmin_sync.current(), today, db.kv_get, db.kv_set, until=until)
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
                db.kv_set("garmin_metrics", json.dumps(calibration.garmin_metrics(garmin_sync.current(), today)))
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
    users = db.users_with_key(garmin_sync.TOKENS_KEY) or [1]
    # quem rodou por último vai para o fim da fila (no plano grátis a função tem 30 s)
    last = int(db.kv_get("cron_last_user", user_id=1) or 0)
    users = sorted(users, key=lambda u: (u <= last, u))
    t0, results = time.monotonic(), {}
    for uid in users:
        if time.monotonic() - t0 > 6:
            break  # o resto sincroniza quando abrir o app (ou no cron de amanhã)
        with db.as_user(uid):
            garmin_sync._set_sync_state(last_cron=clock.now().isoformat(timespec="seconds"))  # prova de que o cron chega
            if not garmin_sync.status()["has_tokens"]:
                results[uid] = {"ran": False, "reason": "Garmin não conectado"}
                continue
            results[uid] = {"ran": True, **_run_sync("cron")}
        db.kv_set("cron_last_user", str(uid), user_id=1)
    if list(results) == [1]:
        return {**results[1], "ok": results[1].get("ok", True)}
    return {"ok": True, "users": results}


@app.post("/api/garmin/import/{activity_id}")
def g_import(activity_id: int):
    plan_ids = {it.get("exercise_id") for p in db.list_plans() for d in p.get("days", []) for it in d.get("items", [])}
    p = _profile()
    try:
        session = garmin_sync.import_activity(activity_id, plan_ids, hr_max(p.get("age"), p.get("hr_max")),
                                               cat_prefs=watch_labels.category_prefs(_watch_prefs()))
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
    hd = _health_days(day, 36)
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
    days = _health_days(today, 56)
    try:  # o cartão de passos é um extra: se algo nele falhar, o resto do painel de saúde continua
        sv = steps.summary(days, sessions, steps.load_tags(db.kv_get(STEP_TAGS_KEY)), today, ask=_steps_can_ask())
        txt = steps.activity_text(sv)
    except Exception:  # noqa: BLE001
        sv, txt = None, None
    rep = health.report(days, json.loads(db.kv_get("health_weekly") or "{}"), daily, prof, today, times, steps_text=txt)
    rep["steps"] = sv
    st = garmin_sync.status()
    rep["garmin"] = {"connected": st["connected"], "needs_reconnect": st["needs_reconnect"]}
    return rep


@app.get("/api/health")
def health_get():
    return _health_report(clock.today())


# ---- passos: treino × dia a dia, dias fora do costume

STEP_TAGS_KEY = "day_tags"
STEP_WINDOW = 70


def _health_days(today: date, days: int = 56) -> dict[str, dict]:
    """Os registros diários do relógio (uma consulta só, em vez de uma por dia)."""
    lo, hi = (today - timedelta(days=days - 1)).isoformat(), today.isoformat()
    out = {}
    for k, v in db.kv_prefix("health:").items():
        d = k[7:]
        if lo <= d <= hi:
            try:
                out[d] = json.loads(v)
            except ValueError:
                pass
    return out


def _steps_can_ask() -> bool:
    """Só pergunta "o que foi?" quando os treinos do relógio já chegaram: com a importação pela metade (primeira
    sincronização, fila pendente, última tentativa com erro) a corrida de ontem ainda não está aqui e pareceria dia a dia."""
    try:
        st = garmin_sync.sync_state()
    except Exception:  # noqa: BLE001
        return True
    if not st.get("last_run"):
        return True  # sem Garmin (Apple / Health Connect mandam treino e passos juntos)
    return bool(st.get("last_ok")) and not st.get("pending") and bool(st.get("backfill_done"))


def _steps_view(today: date) -> dict:
    since = (today - timedelta(days=STEP_WINDOW)).isoformat()
    return steps.summary(_health_days(today, STEP_WINDOW), db.list_sessions(since=since, limit=2000),
                         steps.load_tags(db.kv_get(STEP_TAGS_KEY)), today, ask=_steps_can_ask())


def _drop_step_event(s: dict) -> None:
    """Apaga a caminhada estimada de um dia e devolve à agenda o treino que ela tinha fechado."""
    try:
        schedule.reopen_for_session(s)
    except Exception:  # noqa: BLE001 - a agenda é um extra aqui
        pass
    db.delete_session(s["id"])


def _reconcile_step_events(today: date | None = None) -> int:
    """Chegou do relógio um treino de verdade num dia que a pessoa já tinha explicado (festival, caminhada…):
    a caminhada estimada é recalculada com o que sobra do dia a dia, ou sai se o treino real já explica os passos."""
    today = today or clock.today()
    tags = steps.load_tags(db.kv_get(STEP_TAGS_KEY))
    if not any(t.get("session_id") for t in tags.values()):
        return 0
    days = _health_days(today, STEP_WINDOW)
    sessions = db.list_sessions(since=(today - timedelta(days=STEP_WINDOW)).isoformat(), limit=2000)
    changed = 0
    for day, t in list(tags.items()):
        ev = next((s for s in sessions if steps.is_event(s) and str(s.get("date"))[:10] == day), None)
        if not t.get("session_id") or not ev or day not in days:
            continue
        others = [s for s in sessions if s is not ev]
        rows = steps.split(days, others)
        row = next((r for r in rows if r["date"] == day), None)
        if not row:
            continue
        base = steps.baseline(rows, before=day, tags=tags)
        if base is None:
            base = steps.baseline(rows, tags=tags) or 0
        extra = max(0, row["daily"] - base)
        had = float((ev.get("items") or [{}])[0].get("steps") or 0)
        if abs(extra - had) <= max(1000, 0.15 * had):
            continue  # nada de novo nesse dia
        new = steps.event_session(day, extra, t.get("tag"))
        _drop_step_event(ev)
        sessions = others
        if new:
            saved = db.save_session(new)
            sessions = others + [saved]
            t.update(session_id=saved["id"], minutes=new["duration_min"], extra=round(extra), steps=row["daily"])
        else:  # o treino que chegou já explica o dia: a marca fica (não pergunta de novo), sem caminhada estimada
            t.pop("session_id", None)
            t.pop("minutes", None)
            t.update(extra=round(extra), steps=row["daily"], explained=True)
        changed += 1
    if changed:
        db.kv_set(STEP_TAGS_KEY, json.dumps(tags))
    return changed


@app.get("/api/steps")
def steps_get():
    return _steps_view(clock.today())


@app.post("/api/steps/tag")
def steps_tag(body: dict = Body(...)):
    """Resposta a "o que foi esse dia de muitos passos?": {date, tag}; tag nula desfaz a resposta."""
    today = clock.today()
    day, tag = str(body.get("date") or "")[:10], body.get("tag")
    try:
        d = date.fromisoformat(day)
    except ValueError as e:
        raise HTTPException(400, "Data inválida") from e
    if tag is not None and tag not in steps.TAGS:
        raise HTTPException(400, "Opção desconhecida")
    tags = steps.load_tags(db.kv_get(STEP_TAGS_KEY))
    if d > today or (tag is not None and (today - d).days > STEP_WINDOW - steps.BASE_DAYS):
        raise HTTPException(400, "Só dá para marcar dias das últimas semanas")
    days = _health_days(today, STEP_WINDOW)
    sessions = db.list_sessions(since=(today - timedelta(days=STEP_WINDOW)).isoformat(), limit=2000)
    old = [s for s in sessions if steps.is_event(s) and str(s.get("date"))[:10] == day]
    prev_id = (tags.get(day) or {}).get("session_id")
    if prev_id and not any(s.get("id") == prev_id for s in old):  # marca antiga, fora da janela: busca pela ligação guardada
        s_old = db.get_session(prev_id)
        if s_old and steps.is_event(s_old):
            old.append(s_old)
    for s in old:
        _drop_step_event(s)  # a caminhada estimada da resposta anterior sai; a nova (se houver) entra abaixo
        if s in sessions:
            sessions.remove(s)
    tags.pop(day, None)
    created = None
    if tag:
        rows = steps.split(days, sessions)
        row = next((r for r in rows if r["date"] == day), None)
        if not row:
            raise HTTPException(404, "O relógio não mandou os passos desse dia")
        base = steps.baseline(rows, before=day, tags=tags)
        if base is None:
            base = steps.baseline(rows, tags=tags) or 0
        extra = max(0, row["daily"] - base)
        tags[day] = {"tag": tag, "steps": row["daily"], "extra": round(extra), "at": clock.now().isoformat(timespec="minutes")}
        sess = steps.event_session(day, extra, tag)
        if sess:
            created = db.save_session(sess)
            tags[day]["session_id"] = created["id"]
            tags[day]["minutes"] = sess["duration_min"]
    cut = (today - timedelta(days=180)).isoformat()
    db.kv_set(STEP_TAGS_KEY, json.dumps({k: v for k, v in tags.items() if k >= cut}))
    return {"ok": True, "tag": tags.get(day), "session_id": created["id"] if created else None, **_steps_view(today)}


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


def _apply_swaps(plan: dict) -> dict:
    """Aplica as trocas de aparelho que a pessoa marcou como 'usar sempre' num treino recém-gerado."""
    prefs = json.loads(db.kv_get("swap_prefs") or "{}")
    if not prefs:
        return plan
    for d in plan.get("days", []):
        used = {i.get("exercise_id") for i in d.get("items", [])}
        for it in d.get("items", []):
            dst = prefs.get(it.get("exercise_id"))
            if dst and dst not in used and catalog.get(dst):
                used.add(dst)
                it["exercise_id"] = dst
                if catalog.get(dst).get("timed") and not it.get("duration_s"):
                    it.pop("reps", None)
                    it["duration_s"] = 40
    return plan


@app.post("/api/generator/apply")
def generator_apply(body: dict = Body(default={})):
    """Gera, salva a planilha, define como planilha da agenda e (re)monta a semana."""
    overrides = body.get("profile") or {}
    if overrides:
        db.save_profile(overrides)
    prof = _profile()
    seed = int(body.get("seed") or 0)
    plan = _apply_swaps(generator.generate(prof, seed, recent=_recent(prof)))
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

def _entry_view(e: dict, budget: float, fmt: str | None = None) -> dict:
    e = dict(e)
    if fmt and e.get("status") == "planejado" and _garmin_outdated(e, fmt):
        e["garmin_outdated"] = True
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
    today = clock.today()
    day = date.fromisoformat(date_) if date_ else today
    ws = schedule.week_start(day)
    current = ws == schedule.week_start(today)
    pending = schedule.free_workout_state(prof, today) if current else []
    entries = schedule.ensure_week(prof, day)
    plan = schedule.schedule_plan(prof)
    budget = schedule.session_budget(prof, plan)
    cycle = next((e.get("cycle") for e in entries if e.get("cycle")), None)
    return {
        "week_start": ws.isoformat(), "today": clock.today().isoformat(),
        "plan": {"id": plan["id"], "name": plan["name"], "generator": plan.get("generator")} if plan else None,
        "cycle": cycle, "budget": budget,
        "entries": (views := [_entry_view(e, budget, _garmin_fmt(prof)) for e in entries]),
        "goals": schedule.week_goals(entries, prof, ws, today),
        "pending": pending,  # treinos livres esperando a resposta "foi o treino X?"
        "legacy": schedule.legacy_compensation(entries, today) if current else None,  # reforço da regra antiga
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
def schedule_skip(entry_id: int, body: dict | None = Body(None)):
    """"Não vou treinar". {dry_run: true} devolve as opções com a prévia de cada uma (nada é gravado).
    Sem `strategy` só pula (não mexe nos outros dias)."""
    b = body or {}
    try:
        return schedule.handle_missed(entry_id, _profile(), strategy=b.get("strategy"), to_date=b.get("to_date"),
                                      dry_run=bool(b.get("dry_run")), skip_endurance=bool(b.get("skip_endurance")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/schedule/legacy")
def schedule_legacy(body: dict | None = Body(None)):
    """Reforço espalhado pela regra antiga de treino pulado: volta ao plano original, ou {keep: true} mantém."""
    today = clock.today()
    return schedule.remove_compensation(schedule.week_start(today), today, keep=bool((body or {}).get("keep")))


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
        return schedule.move(entry_id, body["date"], clock.today())
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
    data = garmin_export.to_fit(_entry_day(e), "", db.garmin_overrides(), prefs=_profile())
    fname = garmin_export.fit_filename(e["date"], e.get("day_name") or "treino")
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


GARMIN_FORMAT = 2  # 2: aquecimento do exercício, descanso pela evidência e passos por tempo manuais


def _garmin_fmt(prof: dict | None = None) -> str:
    """Muda quando o formato do envio ou as preferências do relógio mudam: o treino já enviado fica desatualizado."""
    p = prof if prof is not None else _profile()
    return f"{GARMIN_FORMAT}:{p.get('watch_timed') or 'manual'}:{p.get('rest_pref') or 'padrao'}:{p.get('warmup_sets') or 'auto'}"


def _garmin_outdated(e: dict, fmt: str) -> bool:
    return bool(e.get("garmin_outdated") or (e.get("garmin_workout_id") and e.get("garmin_fmt") != fmt))


def _push_entry(e: dict) -> dict:
    if e.get("garmin_workout_id"):
        try:
            garmin_sync.delete_workout(int(e["garmin_workout_id"]))
        except Exception:  # noqa: BLE001 - versão antiga pode já ter sido apagada
            pass
    payload = garmin_export.to_connect_json(_entry_day(e), "", db.garmin_overrides(), prefs=_profile())
    res = garmin_sync.push_workout(payload, e["date"])
    e["garmin_workout_id"] = res.get("workout_id")
    e["garmin_outdated"] = False
    e["garmin_fmt"] = _garmin_fmt()
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
    fmt = _garmin_fmt()
    today = clock.today()
    ws = schedule.week_start(today)
    entries = [e for e in db.list_schedule(today.isoformat(), (ws + timedelta(days=6)).isoformat())
               if e["status"] == "planejado" and (not only_outdated or _garmin_outdated(e, fmt) or not e.get("garmin_workout_id"))]

    def run():
        _clear_garmin_orphans()
        return [{"date": e["date"], **_push_entry(e)} for e in entries]
    return _garmin_call(run)


def _clear_garmin_orphans() -> int:
    """Apaga do Garmin os treinos de uma semana que foi remontada (regerar/recriar), para não duplicar no relógio."""
    ids = json.loads(db.kv_get("garmin_orphans") or "[]")
    if not ids:
        return 0
    left = []
    for wid in ids:
        try:
            garmin_sync.delete_workout(int(wid))
        except Exception as e:  # noqa: BLE001 - já apagado lá (404) conta como feito; falha de rede tenta de novo depois
            if "404" not in str(e) and "not found" not in str(e).lower():
                left.append(wid)
    db.kv_set("garmin_orphans", json.dumps(left))
    return len(ids) - len(left)


# --------------------------------------------------------------- backup ----

@app.get("/api/backup")
def backup():
    _require_admin()
    fname = f"gymgarmin-backup-{clock.today().isoformat()}.json"
    return JSONResponse(db.export_all(), headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/backup")
def restore(body: dict = Body(...)):
    _require_admin()
    if body.get("version") != 1:
        raise HTTPException(400, "Arquivo de backup inválido")
    return db.import_all(body)


# ------------------------------------------------------------- estático ----

app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _asset_version() -> str:
    """Versão dos arquivos do app = hash do conteúdo: cada publicação nova troca o ?v= e o celular baixa a versão nova."""
    import hashlib
    h = hashlib.sha1()
    for name in ("app.js", "style.css", "index.html"):
        h.update((STATIC / name).read_bytes())
    return h.hexdigest()[:10]


ASSET_V = _asset_version()


@app.get("/")
def index():
    html = (STATIC / "index.html").read_text().replace("?v=hl6", f"?v={ASSET_V}")
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    js = (STATIC / "sw.js").read_text().replace("?v=hl6", f"?v={ASSET_V}").replace('"hybridlab-v6"', f'"hybridlab-{ASSET_V}"')
    return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-cache"})
