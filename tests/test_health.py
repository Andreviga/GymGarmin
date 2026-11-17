"""Saúde e sono pelo relógio: coleta defensiva, análise e efeitos do treino."""
import json
import math
import random
from datetime import date, datetime, timedelta, timezone

from gymgarmin import clock, health, readiness

TODAY = date(2026, 11, 17)


class FakeApi:
    def __init__(self):
        self.calls = []

    def get_sleep_data(self, d):
        self.calls.append(("sleep", d))
        return {"dailySleepDTO": {"sleepTimeSeconds": 25200, "deepSleepSeconds": 4500, "lightSleepSeconds": 14400,
                                  "remSleepSeconds": 6300, "awakeSleepSeconds": 1200, "napTimeSeconds": 0,
                                  "sleepStartTimestampLocal": int(datetime(2026, 11, 16, 23, 10, tzinfo=timezone.utc).timestamp() * 1000),
                                  "sleepEndTimestampLocal": None, "avgSleepHRV": 48.0, "averageRespirationValue": 14.2,
                                  "sleepScores": {"overall": {"value": 81, "qualifierKey": "GOOD"}, "awakeCount": {"value": 2}}}}

    def get_hrv_data(self, d):
        self.calls.append(("hrv", d))
        return {"hrvSummary": {"lastNightAvg": 47, "weeklyAvg": 50, "status": "BALANCED",
                               "baseline": {"balancedLow": 44, "balancedUpper": 58}}}

    def get_stats(self, d):
        self.calls.append(("stats", d))
        return {"restingHeartRate": 52, "averageStressLevel": 31, "maxStressLevel": 90, "totalSteps": 9100,
                "moderateIntensityMinutes": 20, "vigorousIntensityMinutes": 10, "bodyBatteryHighestValue": 88}

    def get_rhr_day(self, d):
        raise RuntimeError("não usado")

    def get_training_status(self, d):
        self.calls.append(("status", d))
        return {"mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 51.4}},
                "mostRecentTrainingStatus": {"latestTrainingStatusData": {"123": {"trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
                                                                                  "acuteTrainingLoadDTO": {"acwrPercent": 64}}}}}

    def get_fitnessage_data(self, d):
        return {"chronologicalAge": 38, "fitnessAge": 31.2}

    def get_body_composition(self, a, b):
        return {"dateWeightList": [{"weight": 79800.0, "bodyFat": 17.5}]}


class BrokenApi:
    def __getattr__(self, name):
        def f(*a, **k):
            raise RuntimeError("sem sensor")
        return f


def test_fetch_parses_and_survives_missing_sensors():
    d = health.fetch_day(FakeApi(), TODAY)
    assert d["sleep"]["duration_h"] == 7.0 and d["sleep"]["score"] == 81 and round(d["sleep"]["deep_pct"]) == 18
    assert d["sleep"]["start"] == "23:10" and d["hrv"]["low"] == 44 and d["rhr"] == 52 and d["steps"] == 9100
    w = health.fetch_weekly(FakeApi(), TODAY)
    assert w["vo2max"] == 51.4 and w["training_status"] == "PRODUCTIVE" and w["fitness_age"] == 31.2 and w["weight_kg"] == 79.8
    broken = health.fetch_day(BrokenApi(), TODAY)
    assert set(broken) == {"date", "fetched_at", "errors"} and broken["errors"] == 3
    assert health.fetch_weekly(BrokenApi(), TODAY) == {"date": TODAY.isoformat()}


def test_collect_backfills_gradually_and_marks_final():
    store = {}
    api = FakeApi()
    clock.freeze(datetime(2026, 11, 17, 8, 0))
    try:
        r = health.collect(api, TODAY, store.get, store.__setitem__)
        assert len(r["fetched"]) == 2 + health.BACKFILL_PER_RUN
        assert json.loads(store["health:2026-11-16"])["final"] and not json.loads(store["health:2026-11-17"])["final"]
        assert "health_weekly" in store
        n = len(api.calls)
        health.collect(api, TODAY, store.get, store.__setitem__)  # hoje recém-buscado, ontem final: só o backfill
        assert sum(1 for c in api.calls[n:] if c[1] in ("2026-11-17", "2026-11-16")) == 0
    finally:
        clock.freeze(None)


def _synthetic(heavy_hurts=True):
    rng = random.Random(3)
    days, load = {}, {}
    for i in range(56, -1, -1):
        d = TODAY - timedelta(days=i)
        heavy = d.weekday() in (1, 5)  # ter e sáb pesados
        load[d] = 110.0 if heavy else (0.0 if d.weekday() in (0, 4) else 40.0)
        prev_heavy = (d - timedelta(days=1)).weekday() in (1, 5)
        score = 80 + rng.uniform(-3, 3) - (14 if prev_heavy and heavy_hurts else 0)
        days[d.isoformat()] = {"date": d.isoformat(),
                               "sleep": {"score": round(score), "duration_h": 7.4 - (0.6 if prev_heavy and heavy_hurts else 0),
                                         "deep_pct": 17, "rem_pct": 22, "start": "23:15"},
                               "hrv": {"last_night": 55 - (8 if prev_heavy and heavy_hurts else 0), "low": 46, "high": 62},
                               "rhr": 50 + (2 if prev_heavy and heavy_hurts else 0), "stress_avg": 28, "steps": 8000,
                               "int_mod": 25, "int_vig": 10}
    return days, load


def test_report_shows_training_effects_and_areas():
    days, load = _synthetic()
    rep = health.report(days, {"vo2max": 52.0, "history": [{"date": "2026-10-01", "vo2max": 50.5}]}, load, {"age": 38}, TODAY)
    titles = {a["title"]: a for a in rep["areas"]}
    assert {"Sono", "Recuperação", "Condicionamento", "Estresse", "Atividade diária"} <= set(titles)
    assert "+1,5 em 4+ semanas" in titles["Condicionamento"]["text"]
    eff = " ".join(rep["effects"])
    assert "Nota do sono" in eff and "pesando" in eff
    assert rep["last_night"]["level"] in ("bom", "regular", "ruim") and len(rep["series"]) == 28
    # quem absorve bem a carga recebe a mensagem oposta
    days2, load2 = _synthetic(heavy_hurts=False)
    eff2 = " ".join(health.report(days2, {}, load2, {"age": 38}, TODAY)["effects"])
    assert "absorvendo bem" in eff2


def test_alert_when_rhr_up_and_hrv_down_and_readiness_uses_it():
    days, load = _synthetic(heavy_hurts=False)
    for i in range(0, 3):
        d = (TODAY - timedelta(days=i)).isoformat()
        days[d]["rhr"] = 58
        days[d]["hrv"]["last_night"] = 38
    for i in range(3, 7):
        days[(TODAY - timedelta(days=i)).isoformat()]["hrv"]["last_night"] = 40
    rep = health.report(days, {}, load, {"age": 38}, TODAY)
    assert rep["alerts"] and "FC de repouso" in rep["alerts"][0]
    assert any(a["title"] == "Recuperação" and a["level"] == "alerta" for a in rep["areas"])
    sc = readiness.score({"sleep_score": 80, "rhr_delta": rep["rhr"]["delta"]})
    assert sc["score"] < 80 and any("FC de repouso" in r for r in sc["reasons"])


def test_short_sleep_week_and_night_verdict():
    days, load = _synthetic(heavy_hurts=False)
    for i in range(0, 5):
        days[(TODAY - timedelta(days=i)).isoformat()]["sleep"].update({"duration_h": 5.4, "score": 52, "deep_pct": 8})
    rep = health.report(days, {}, load, {"age": 30}, TODAY)
    assert rep["last_night"]["level"] == "ruim" and any("profundo" in r for r in rep["last_night"]["reasons"])
    assert any("6 h" in a for a in rep["alerts"])
    assert next(a for a in rep["areas"] if a["title"] == "Sono")["level"] == "alerta"
    assert health.sleep_need(70) == (7.0, 8.0)


def test_readiness_reuses_health_data_without_extra_calls():
    api = FakeApi()
    known = health.fetch_day(api, TODAY)
    n = len(api.calls)

    class Counting(FakeApi):
        def get_training_readiness(self, d):
            self.calls.append(("tr", d))
            return [{"score": 70}]

        def get_body_battery(self, a, b):
            self.calls.append(("bb", a))
            return []

    c = Counting()
    data = readiness.fetch(c, TODAY, known)
    assert [x[0] for x in c.calls] == ["tr"]  # sono, HRV e FC de repouso vieram da saúde
    assert data["sleep_score"] == 81 and data["hrv_status"] == "BALANCED" and data["rhr"] == 52 and n == 3


# ------------------------------------------------- regressões da simulação de saúde ---
def _stable(rng, days=56, hrv=True):
    out = {}
    for i in range(days, -1, -1):
        d = (TODAY - timedelta(days=i)).isoformat()
        rec = {"date": d, "rhr": round(55 + rng.gauss(0, 1.6)),
               "sleep": {"duration_h": round(7.4 + rng.gauss(0, 0.4), 2), "score": round(77 + rng.gauss(0, 5)), "start": "23:20"}}
        if hrv:
            rec["hrv"] = {"last_night": round(50 * math.exp(rng.gauss(0, 0.11)))}
        out[d] = rec
    return out


def test_illness_detected_from_short_window_even_without_hrv():
    rng = random.Random(5)
    for hrv in (True, False):
        days = _stable(rng, hrv=hrv)
        assert not health.report(days, {}, {}, {"age": 35}, TODAY)["alerts"]
        for i in range(0, 3):  # gripe começando: FC de repouso +7 e HRV −28% nos últimos 3 dias
            r = days[(TODAY - timedelta(days=i)).isoformat()]
            r["rhr"] += 7
            if hrv:
                r["hrv"]["last_night"] = round(r["hrv"]["last_night"] * 0.72)
        rep = health.report(days, {}, {}, {"age": 35}, TODAY)
        assert any("FC de repouso" in a for a in rep["alerts"]), hrv


def test_noise_does_not_claim_training_hurts_and_invalid_readings_ignored():
    rng = random.Random(9)
    claims = 0
    for _ in range(40):
        days = _stable(rng)
        load = {TODAY - timedelta(days=i): (130.0 if i % 3 == 0 else 0.0) for i in range(60)}  # treino sem efeito real
        claims += "pesando" in " ".join(health.report(days, {}, load, {"age": 35}, TODAY)["effects"])
    assert claims <= 2  # antes: ~10% de alarme falso por acaso
    days = _stable(rng)
    days[TODAY.isoformat()]["sleep"]["duration_h"] = 0  # leitura falha do relógio
    days[TODAY.isoformat()]["rhr"] = 0
    rep = health.report(days, {}, {}, {"age": 35}, TODAY)
    assert rep["last_night"]["date"] != TODAY.isoformat() and not rep["alerts"]
    assert "0h00" not in " ".join(rep["last_night"]["reasons"])


def test_failed_night_is_retried_before_final():
    class Flaky(FakeApi):
        fails = 2

        def get_sleep_data(self, d):
            if Flaky.fails > 0:
                Flaky.fails -= 1
                raise RuntimeError("503")
            return super().get_sleep_data(d)

    store = {}
    api = Flaky()
    clock.freeze(datetime(2026, 11, 17, 8, 0))
    try:
        health.collect(api, TODAY, store.get, store.__setitem__, backfill=1)
        d = json.loads(store["health:2026-11-16"])
        assert not d.get("sleep") and not d["final"]  # falhou: ainda não é final
        for h in (9, 10, 11):
            clock.freeze(datetime(2026, 11, 17, h, 0))
            health.collect(api, TODAY, store.get, store.__setitem__, backfill=1)
        assert json.loads(store["health:2026-11-16"]).get("sleep")
    finally:
        clock.freeze(None)
