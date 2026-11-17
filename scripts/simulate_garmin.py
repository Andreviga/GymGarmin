"""Simulações do Garmin (sincronização automática) e da prontidão diária.

Uso:  python scripts/simulate_garmin.py [--n 80] [--days 28] [--seed 1]

Cada atleta tem um plano (musculação + resistência) e vive N dias com um Garmin falso:
  • atividades gravadas no relógio, aparecendo no Garmin Connect com atraso (às vezes dias,
    quando o relógio fica sem sincronizar), musculação com e sem séries detectadas,
    exercícios reconhecidos errado, caminhadas curtas do dia a dia, yoga, tênis etc.;
  • Garmin fora do ar por algumas horas, limite de requisições (429), token que se renova
    (o antigo deixa de valer) e, raramente, login revogado de verdade;
  • app aberto 0–3 vezes por dia em horários aleatórios (sincroniza ao abrir) e cron às 23h13;
  • às vezes o atleta também registra o treino no app (duplicidade com o relógio);
  • prontidão do relógio (Training Readiness, sono, HRV, Body Battery), com relógios que não
    têm Training Readiness; o atleta às vezes desfaz o ajuste.

Confere: tudo que é importável chega uma única vez e rápido; sem treino contado em dobro;
tokens não somem por falha passageira; quantidade de chamadas à API; status do treino
(feito só quando foi feito); prontidão presente no dia certo; ajuste aplicado, coerente,
respeitando o "desfazer" e agravando quando a prontidão piora.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["GYMGARMIN_HOME"] = TMP
os.environ["GYMGARMIN_DB"] = str(Path(TMP) / "garmin.sqlite3")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("VERCEL", None)
os.environ.pop("CRON_SECRET", None)
os.environ["GARMIN_TOKEN_KEY"] = "simulacao-chave-local-0123456789abcdef"

from fastapi import HTTPException  # noqa: E402
from garminconnect import (GarminConnectAuthenticationError, GarminConnectConnectionError,  # noqa: E402
                           GarminConnectTooManyRequestsError)

from gymgarmin import app as A, catalog, clock, db, garmin_sync, generator, load, readiness, schedule  # noqa: E402

MONDAY = date(2026, 11, 16)
ISSUES: Counter = Counter()
EX: dict = defaultdict(list)
STATS: dict = defaultdict(list)
SEV = {"seguir": 0, "moderar": 1, "trocar": 2, "descanso": 3}


def issue(k, ex):
    ISSUES[k] += 1
    if len(EX[k]) < 4:
        EX[k].append(ex)


# ----------------------------------------------------------------- mundo Garmin ---
class World:
    """Estado do lado da Garmin para um atleta."""

    def __init__(self, rng: random.Random, days: int):
        self.rng = rng
        self.acts: list[dict] = []          # {"id", "start", "visible", "type", "summary", "sets", "importable", ...}
        self.refresh = "r0"                 # refresh token válido
        self.gen = 0
        self.revoked_at: datetime | None = None
        self.outages: list[tuple[datetime, datetime]] = []
        for d in range(days):
            if rng.random() < 0.06:
                st = datetime.combine(MONDAY + timedelta(days=d), datetime.min.time()) + timedelta(hours=rng.uniform(0, 24))
                self.outages.append((st, st + timedelta(hours=rng.uniform(1, 6))))
        if rng.random() < 0.05:
            self.revoked_at = datetime.combine(MONDAY, datetime.min.time()) + timedelta(days=rng.uniform(3, days - 3))
        self.readiness: dict[str, dict] = {}
        self.has_tr = rng.random() < 0.7    # relógio com Training Readiness
        self.calls = Counter()
        self.detail_calls = Counter()       # get_activity por id

    def down(self) -> bool:
        t = clock.now()
        return any(a <= t < b for a, b in self.outages)

    def revoked(self) -> bool:
        return self.revoked_at is not None and clock.now() >= self.revoked_at and self.refresh.startswith("r")


WORLD: World | None = None


class FakeTokens:
    def __init__(self):
        self.tokens = None

    def dumps(self):
        return json.dumps(self.tokens)


class FakeGarmin:
    def __init__(self, email=None, password=None, return_on_mfa=False, **_):
        self.email, self.password = email, password
        self.client = FakeTokens()
        self.display_name = "atleta"

    # -- autenticação: imita o garminconnect 0.3.x (falha no refresh vira "Username and password are required")
    def login(self, tokenstore=None):
        w = WORLD
        w.calls["login"] += 1
        if not tokenstore:
            if not self.email:
                raise GarminConnectAuthenticationError("Username and password are required")
            w.gen += 1
            w.refresh = f"n{w.gen}"
            w.revoked_at = None
            self.client.tokens = {"di_token": f"a{w.gen}", "di_refresh_token": w.refresh}
            return None, None
        data = json.loads(tokenstore)
        expiring = w.rng.random() < 0.35
        if w.revoked() or data.get("di_refresh_token") != w.refresh:
            raise GarminConnectAuthenticationError("Username and password are required")
        if w.down():
            if expiring:
                raise GarminConnectAuthenticationError("Username and password are required")
            raise GarminConnectConnectionError("Connection error: 503")
        if expiring:
            self._rotate()
        else:
            self.client.tokens = data
        return None, None

    def _rotate(self):
        w = WORLD
        w.gen += 1
        w.refresh = f"r{w.gen}" if not w.refresh.startswith("n") else f"n{w.gen}"
        self.client.tokens = {"di_token": f"a{w.gen}", "di_refresh_token": w.refresh}

    def _api(self, name):
        w = WORLD
        w.calls[name] += 1
        if w.revoked() or (self.client.tokens or {}).get("di_refresh_token") != w.refresh:
            raise GarminConnectAuthenticationError("401 Unauthorized")
        if w.down():
            raise GarminConnectConnectionError("Connection error: 503")
        if w.rng.random() < 0.015:
            self._rotate()  # o token pode se renovar no meio das chamadas

    def get_activities_by_date(self, start, end):
        self._api("list")
        now = clock.now()
        return [{"activityId": a["id"], "activityName": a["name"], "activityType": {"typeKey": a["type"]},
                 "startTimeLocal": a["start"].strftime("%Y-%m-%d %H:%M:%S"), "duration": a["summary"]["duration"],
                 "distance": a["summary"].get("distance")}
                for a in WORLD.acts if a["visible"] <= now and start <= a["start"].date().isoformat() <= end]

    def get_activity(self, aid):
        self._api("detail")
        WORLD.detail_calls[str(aid)] += 1
        if WORLD.rng.random() < 0.02:
            raise GarminConnectTooManyRequestsError("429 Too Many Requests")
        a = next(x for x in WORLD.acts if str(x["id"]) == str(aid))
        return {"activityId": a["id"], "activityName": a["name"], "activityTypeDTO": {"typeKey": a["type"]},
                "summaryDTO": {"startTimeLocal": a["start"].strftime("%Y-%m-%dT%H:%M:%S.0"), **a["summary"]}}

    def get_activity_exercise_sets(self, aid):
        self._api("sets")
        a = next(x for x in WORLD.acts if str(x["id"]) == str(aid))
        return {"exerciseSets": a.get("sets") or []}

    # -- prontidão / métricas
    def _ready(self, d):
        self._api("readiness")
        r = WORLD.readiness.get(d)
        if not r or clock.now() < r["available"]:
            return None
        return r

    def get_training_readiness(self, d):
        r = self._ready(d)
        return [{"score": r["tr"], "level": "X"}] if r and r.get("tr") is not None else []

    def get_hrv_data(self, d):
        r = self._ready(d)
        return {"hrvSummary": {"status": r["hrv"]}} if r and r.get("hrv") else None

    def get_sleep_data(self, d):
        r = self._ready(d)
        return {"dailySleepDTO": {"sleepScores": {"overall": {"value": r["sleep"]}}, "sleepTimeSeconds": 25000}} if r else {}

    def get_body_battery(self, a, b):
        r = self._ready(a)
        return [{"bodyBatteryValuesArray": [[1, r["bb"] - 10], [2, r["bb"]]]}] if r else []

    def get_rhr_day(self, d):
        raise RuntimeError("sem dado")

    def get_race_predictions(self):
        self._api("metrics")
        return {"time5K": 1500}

    def get_cycling_ftp(self):
        return []

    def get_lactate_threshold(self, latest=True):
        return {}

    def get_max_metrics(self, d):
        return []


garmin_sync._lib = lambda: FakeGarmin


# ------------------------------------------------------------- atividades ---
SPORT_TYPES = {"corrida": {"rua": "running", "esteira": "treadmill_running", "trilha": "trail_running", "pista": "track_running"},
               "ciclismo": {"estrada": "road_biking", "rolo": "indoor_cycling", "mtb": "mountain_biking"},
               "natacao": {"piscina": "lap_swimming", "aguas_abertas": "open_water_swimming"},
               "remo": {"indoor": "indoor_rowing", "agua": "rowing"},
               "caminhada": {"rua": "walking", "esteira": "treadmill_walking"},
               "trilha": {"trilha": "hiking"}}
NOT_IMPORTABLE = ["yoga", "tennis", "breathwork", "pilates", "golf", "soccer"]


def new_id(w: World) -> int:
    return 7_000_000 + len(w.acts) + 1


def add_activity(w: World, rng, start: datetime, typ: str, minutes: float, *, dist_m=None, sets=None, entry=None,
                 name=None, kind=None, delay_h=None):
    summary = {"duration": round(minutes * 60), "averageHR": rng.randint(110, 165), "calories": rng.randint(150, 700)}
    if dist_m:
        summary["distance"] = float(dist_m)
    if typ == "lap_swimming":
        summary["poolLength"] = rng.choice([25, 50])
    if typ in ("road_biking", "indoor_cycling") and rng.random() < 0.5:
        summary["averagePower"] = rng.randint(150, 260)
    if delay_h is None:
        delay_h = rng.uniform(0.1, 3)
    end = start + timedelta(minutes=minutes)
    imp = typ in load_types() or typ == "strength_training"
    w.acts.append({"id": new_id(w), "start": start, "visible": end + timedelta(hours=delay_h), "type": typ,
                   "summary": summary, "sets": sets, "importable": imp, "entry_id": entry, "kind": kind,
                   "name": name or typ})


def load_types():
    from gymgarmin import sports as sp

    return set(sp.GARMIN_TYPE_TO_SPORT) | set(garmin_sync.GYM_MACHINES)


def strength_sets(rng, e: dict) -> list[dict]:
    out = []
    for it in e["items"]:
        if not it.get("sets") or (it.get("phase") or "principal") != "principal":
            continue
        g = (catalog.get(it["exercise_id"]) or {}).get("garmin") or {}
        for _ in range(int(it["sets"])):
            cat, name = g.get("category"), g.get("exercise")
            if rng.random() < 0.12:
                cat, name = rng.choice([("UNKNOWN", None), ("CURL", "BICEPS_CURL"), ("SQUAT", "SQUAT")])
            out.append({"setType": "ACTIVE", "exercises": [{"category": cat, "name": name, "probability": 80}],
                        "repetitionCount": rng.randint(6, 12), "weight": rng.choice([0, 20000, 40000, 60.0]), "duration": 40})
            out.append({"setType": "REST", "duration": 90})
    return out


# ------------------------------------------------------------- atleta ---
def random_profile(rng):
    n = rng.choice([3, 4, 5, 6])
    days = sorted(rng.sample(range(7), n))
    sports = rng.sample(["corrida", "ciclismo", "natacao", "caminhada", "remo"], rng.choice([1, 1, 2]))
    cfg = {s: {"enabled": True, "sessions": rng.choice([1, 2, 3]),
               "env": rng.choice(list(SPORT_TYPES[s]))} for s in sports}
    return {"training_months": rng.choice([6, 18, 40]), "available_days": days, "age": rng.randint(22, 55),
            "goal": rng.choice(["hibrido", "hipertrofia", "condicionamento"]), "session_minutes": rng.choice([45, 60, 75]),
            "endurance": {"sports": cfg, "anchors": {"run_vdot": 44, "ftp_w": 210, "css_s100": 115, "row_2k_s": 470},
                          "readiness_auto": rng.random() < 0.8}}


def at(day: date, hours: float) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=hours)


def call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except HTTPException as e:
        return {"http_error": e.status_code, "detail": e.detail}


def run_athlete(pid: int, rng: random.Random, days: int):
    global WORLD
    with db.conn() as c:
        for t in ("schedule", "plans", "sessions", "kv"):
            c.execute(f"DELETE FROM {t}")
    garmin_sync._client = None
    garmin_sync._pending_mfa.clear()
    w = WORLD = World(rng, days)
    clock.freeze(at(MONDAY, 7))
    db.save_profile({**db.DEFAULT_PROFILE, **random_profile(rng)})
    prof = db.get_profile()
    plan = generator.generate(prof, today=MONDAY)
    plan["generator"]["cycle_start"] = MONDAY.isoformat()
    saved = db.create_plan(plan)
    db.save_profile({"schedule_plan_id": saved["id"]})
    prof = db.get_profile()
    garmin_sync.login("atleta@x", "senha")  # conectado no início
    garmin_sync._client = None
    behavior = rng.choice(["assiduo", "assiduo", "falta_pouco", "falta_muito"])
    p_miss = {"assiduo": 0.07, "falta_pouco": 0.2, "falta_muito": 0.4}[behavior]
    logs_in_app = rng.random() < 0.5         # também registra o treino no app
    lazy_watch = rng.random() < 0.25         # relógio fica dias sem sincronizar com o celular
    undo_rate = rng.choice([0.0, 0.2, 0.5])
    truth: dict[int, bool] = {}              # entry_id → fez de verdade
    fatigue = 0.0
    applied: dict[str, dict] = {}            # dia → {"action", "undone"}

    # prontidão do relógio por dia
    for d in range(days + 1):
        day = MONDAY + timedelta(days=d)
        base = rng.gauss(62, 18)
        r = {"available": at(day, rng.uniform(5, 9)), "sleep": max(10, min(100, int(base + rng.gauss(0, 8)))),
             "hrv": rng.choice(["BALANCED", "BALANCED", "UNBALANCED", "LOW", None]),
             "bb": max(5, min(100, int(base + rng.gauss(0, 10))))}
        if w.has_tr:
            r["tr"] = max(1, min(100, int(base)))
        w.readiness[day.isoformat()] = r

    events = []
    for d in range(days):
        day = MONDAY + timedelta(days=d)
        n_open = rng.choice([0, 1, 1, 2, 2, 3])
        for _ in range(n_open):
            events.append((at(day, rng.uniform(5.5, 23.9)), "open", day))
        events.append((at(day, 23 + 13 / 60), "cron", day))
        events.append((at(day, 4), "plan_day", day))
    events.sort(key=lambda x: x[0])

    for when, kind, day in events:
        clock.freeze(when)
        if kind == "plan_day":
            # gera as atividades do dia (o atleta decide treinar ou não)
            try:
                entries = schedule.ensure_week(prof, day, day)
            except Exception as ex:  # noqa: BLE001
                issue("exceção ao montar a semana", (pid, repr(ex)[:150]))
                return
            todays = [e for e in entries if e["date"] == day.isoformat()]
            hour = rng.choice([6.0, 7.0, 12.0, 18.0, 19.5])
            delay = rng.uniform(24, 96) if lazy_watch and rng.random() < 0.3 else None
            for e in todays:
                if e["status"] in ("pulado",):
                    continue
                did = rng.random() > p_miss
                truth[e["id"]] = did
                if not did:
                    continue
                if e.get("kind") == "resistencia":
                    it = next(i for i in e["items"] if i.get("endurance"))
                    en_ = it["endurance"]
                    sport = en_["sport"]
                    typ = SPORT_TYPES.get(sport, {}).get(en_.get("env") or "", None) or next(iter(SPORT_TYPES[sport].values()))
                    add_activity(w, rng, at(day, hour), typ, en_["dur_s"] / 60 * rng.uniform(0.9, 1.1),
                                 dist_m=(en_.get("dist_m") or None), entry=e["id"], kind="resistencia", delay_h=delay)
                elif e.get("kind") in ("forca", "cardio"):
                    detect = rng.random() < 0.75
                    sets = strength_sets(rng, e) if detect and e.get("kind") == "forca" else None
                    typ = "strength_training" if e.get("kind") == "forca" else rng.choice(["indoor_cardio", "treadmill_running"])
                    add_activity(w, rng, at(day, hour), typ, rng.uniform(40, 70), sets=sets, entry=e["id"],
                                 kind=e.get("kind"), delay_h=delay)
                    if logs_in_app and e.get("kind") == "forca":
                        items = [{"exercise_id": i["exercise_id"], "sets": [{"reps": 8, "load_kg": 40, "rpe": 8}] * int(i["sets"])}
                                 for i in e["items"] if i.get("sets") and (i.get("phase") or "principal") == "principal"]
                        db.save_session({"date": f"{day.isoformat()}T{int(hour) + 1:02d}:10", "schedule_id": e["id"],
                                         "day_name": e["day_name"], "items": items})
                hour += 1.5
            # vida real: caminhadas curtas detectadas e outros esportes que o app não planeja
            if rng.random() < 0.35:
                add_activity(w, rng, at(day, rng.uniform(8, 20)), "walking", rng.uniform(12, 30), dist_m=rng.uniform(1000, 2500),
                             kind="extra", delay_h=delay)
            if rng.random() < 0.12:
                add_activity(w, rng, at(day, rng.uniform(8, 20)), rng.choice(NOT_IMPORTABLE), rng.uniform(30, 80),
                             kind="extra", delay_h=delay)
            continue

        before_calls = sum(w.detail_calls.values())
        if kind == "cron":
            r = call(A.cron_garmin_sync, None)
        else:
            if rng.random() < 0.6:
                garmin_sync._client = None  # nova instância (cold start)
            st = garmin_sync.status()
            if st["needs_reconnect"] and rng.random() < 0.7:
                garmin_sync.login("atleta@x", "senha")
                STATS["reconectou"].append(1)
            r = call(A.g_sync, True)
        if isinstance(r, dict) and r.get("http_error") and r["http_error"] >= 500 and r["http_error"] != 501:
            issue("erro 5xx na sincronização", (pid, kind, r))
        STATS["chamadas de detalhe por execução"].append(sum(w.detail_calls.values()) - before_calls)
        st = garmin_sync.status()
        if st["needs_reconnect"] and not w.revoked():
            issue("pediu para reconectar sem o login ter sido revogado", (pid, when.isoformat(), st["sync"].get("last_error")))
        if kind == "open":
            entries = schedule.ensure_week(prof, day, day)
            rv = A.readiness_get()
            ready_now = w.readiness[day.isoformat()]["available"] <= when
            if ready_now and rv.get("score") is None and not w.down():
                issue("prontidão do dia faltando com o app aberto", (pid, when.isoformat(), garmin_sync.sync_state().get("last_run")))
            if rv.get("date") and rv["date"] != day.isoformat():
                issue("prontidão de outro dia", (pid, rv.get("date"), day.isoformat()))
            if rv.get("score") is not None:
                if prof.get("endurance", {}).get("readiness_auto"):
                    check_applied(pid, day, entries, rv, applied)
                    # às vezes o atleta desfaz o ajuste
                    for e in entries:
                        if e["date"] == day.isoformat() and e.get("readiness_backup") and rng.random() < undo_rate:
                            call(A.readiness_undo, e["id"])
                            applied.setdefault(day.isoformat(), {}).setdefault("undone_ids", set()).add(e["id"])
                STATS["ação da prontidão"].append(rv["action"])
    # fim: uma sincronização final sem falhas para conferir que tudo chega
    final_check(pid, w, truth, prof, days)


class FakeGarminProxy(FakeGarmin):
    """Lê a prontidão sem contar chamada nem falhar (para o valor esperado)."""

    def __init__(self):
        super().__init__()

    def _api(self, name):
        return None


def check_applied(pid, day: date, entries: list[dict], rv: dict, applied: dict):
    today = [e for e in entries if e["date"] == day.isoformat()]
    undone = applied.setdefault(day.isoformat(), {}).setdefault("undone_ids", set())
    for e in today:
        b = e.get("readiness_backup")
        if e["id"] in undone:
            if b:
                issue("ajuste reaplicado depois de desfeito", (pid, day.isoformat(), e["day_name"]))
            continue
        if not b:
            if rv["action"] != "seguir" and e["status"] == "planejado" and adjustable(e, rv["action"]):
                issue("prontidão baixa e treino não ajustado", (pid, day.isoformat(), rv["action"], e["day_name"]))
            continue
        orig = b["items"]
        t0 = sum(float(i.get("duration_min") or 0) for i in orig if i.get("endurance"))
        t1 = sum(float(i.get("duration_min") or 0) for i in e["items"] if i.get("endurance"))
        if t1 > t0 + 0.5:
            issue("ajuste aumentou a duração", (pid, e["day_name"], t0, t1))
        s0 = sum(int(i.get("sets") or 0) for i in orig if not i.get("endurance"))
        s1 = sum(int(i.get("sets") or 0) for i in e["items"] if not i.get("endurance"))
        if s1 > s0:
            issue("ajuste aumentou as séries", (pid, e["day_name"], s0, s1))
        if any(i.get("sets") is not None and int(i["sets"]) < 1 for i in e["items"]):
            issue("ajuste deixou exercício com 0 séries", (pid, e["day_name"]))
        if e.get("garmin_workout_id") and not e.get("garmin_outdated"):
            issue("ajuste sem marcar reenvio ao Garmin", (pid, e["day_name"]))
        done_sev = SEV.get(e.get("readiness_action") or "moderar", 1)
        if SEV[rv["action"]] > done_sev and e["status"] == "planejado":
            issue("prontidão piorou e o ajuste não acompanhou", (pid, day.isoformat(), e.get("readiness_action"), rv["action"]))


def adjustable(e: dict, action: str) -> bool:
    for it in e["items"]:
        end = it.get("endurance")
        if end and end.get("type") != "prova" and (end.get("hard") or (action != "moderar" and end.get("role") == "longo")):
            return True
        if not end and it.get("sets") and (it.get("phase") or "principal") == "principal":
            if action != "moderar" or it.get("role") in ("main", "sec"):
                return True
    return False


def final_check(pid, w: World, truth: dict, prof: dict, days: int):
    end = at(MONDAY + timedelta(days=days + 5), 12)
    clock.freeze(end)
    w.outages.clear()
    w.revoked_at = None
    for _ in range(4):  # até 4 execuções (limite de 15 por execução)
        if garmin_sync.status()["needs_reconnect"]:
            garmin_sync.login("atleta@x", "senha")
        garmin_sync._set_sync_state(last_run=None)
        A._run_sync("manual")
        clock.freeze(clock.now() + timedelta(hours=7))
    sessions = db.list_sessions(limit=5000)
    by_gid = Counter(str(s.get("garmin_activity_id")) for s in sessions if s.get("garmin_activity_id"))
    for gid, n in by_gid.items():
        if n > 1:
            issue("atividade importada em dobro", (pid, gid, n))
    window_start = MONDAY
    for a in w.acts:
        if a["start"].date() < window_start:
            continue
        got = by_gid.get(str(a["id"]), 0)
        if a["importable"] and not got:
            issue(f"atividade importável não chegou ({a['type']}{', sem séries' if a['type'] == 'strength_training' and not a['sets'] else ''})",
                  (pid, a["id"], a["start"].isoformat(), a["visible"].isoformat()))
        if not a["importable"]:
            STATS["buscas de atividade não importável"].append(w.detail_calls.get(str(a["id"]), 0))
        else:
            STATS["buscas de atividade importável"].append(w.detail_calls.get(str(a["id"]), 0))
    # latência: momento da importação (aproximado pela primeira execução depois de visível)
    # treino contado em dobro (registrado no app e importado do relógio no mesmo dia)
    per_day = defaultdict(list)
    for s in sessions:
        per_day[s["date"][:10]].append(s)
    for d, ss in per_day.items():
        app_strength = [s for s in ss if s.get("schedule_id") and any(i.get("sets") for i in s["items"])]
        gar_strength = [s for s in ss if s.get("garmin_activity_id") and s.get("source") == "garmin"
                        and any(i.get("sets") for i in s["items"]) and not s.get("merged_into")]
        if app_strength and gar_strength:
            issue("musculação contada em dobro (app + relógio)", (pid, d, len(app_strength), len(gar_strength)))
    # status do treino
    ws_seen = sorted({schedule.week_start(MONDAY + timedelta(days=d)) for d in range(days)})
    for ws in ws_seen:
        for e in schedule.ensure_week(prof, ws, clock.today()):
            if e["id"] not in truth or e["status"] in ("pulado",):
                continue
            did = truth[e["id"]]
            if did and e["status"] != "feito":
                issue(f"treino feito não marcado ({e.get('kind')})", (pid, e["date"], e["day_name"], e["status"]))
            if not did and e["status"] == "feito" and e.get("sport") != "caminhada":  # caminhou de verdade no dia
                issue(f"treino marcado como feito sem ter sido feito ({e.get('kind')})", (pid, e["date"], e["day_name"]))
    STATS["chamadas login/lista/detalhe por atleta"].append((w.calls["login"], w.calls["list"], w.calls["detail"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    db.init()
    for pid in range(a.n):
        try:
            run_athlete(pid, rng, a.days)
        except Exception as ex:  # noqa: BLE001
            issue("exceção", (pid, repr(ex)[:160], traceback.format_exc(limit=4)[-500:]))
    clock.freeze(None)
    print(f"\n=== {a.n} atletas × {a.days} dias (seed {a.seed}) ===")
    for k, n in ISSUES.most_common():
        print(f"{n:5d}  {k}")
        for ex in EX[k]:
            print("        ", str(ex)[:300])
    print("\n--- estatísticas")
    for k, v in STATS.items():
        if v and isinstance(v[0], (int, float)):
            s = sorted(v)
            print(f"{k}: média {sum(s) / len(s):.2f} · mediana {s[len(s) // 2]} · p90 {s[int(len(s) * 0.9)]} · máx {s[-1]} · n {len(s)}")
        else:
            print(f"{k}: {Counter(map(str, v)).most_common(6)}")


if __name__ == "__main__":
    main()
