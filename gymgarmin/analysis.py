"""Análise fisiológica do treino.

- Volume semanal por músculo (séries; primário = 1, secundário = 0,5)
- Comparação com faixas de referência (MEV / MAV / MRV) ajustadas ao nível do usuário
- Músculos esquecidos (sem estímulo há muitos dias ou abaixo do mínimo efetivo)
- Equilíbrio entre grupos (empurrar × puxar, quadríceps × posteriores, cadeia anterior × posterior)
- Recuperação estimada por músculo (horas desde o último estímulo × fadiga da sessão)
- Cardio: minutos por intensidade, equivalência OMS (150–300 min moderado/semana), gasto calórico (MET)
- Análise da planilha (volume planejado) antes mesmo de treinar
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from . import catalog, clock
from .limitations import check_exercise, max_zone
from .muscles import HR_ZONES, LEVEL_VOLUME_FACTOR, MUSCLES, PRIMARY_WEIGHT, SECONDARY_WEIGHT, muscle_pt

STATUS_PT = {
    "esquecido": "Esquecido",
    "abaixo": "Abaixo do mínimo",
    "manutencao": "Manutenção",
    "otimo": "Faixa ótima",
    "alto": "Volume alto",
    "excesso": "Excesso (acima do recuperável)",
}


def _to_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, 12)
    s = str(v)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.fromisoformat(s[:10] + "T12:00:00")


def _landmarks(muscle: str, profile: dict | None) -> dict:
    m = MUSCLES[muscle]
    f = LEVEL_VOLUME_FACTOR.get((profile or {}).get("level") or "intermediario", 1.0)
    f *= float((profile or {}).get("volume_factor") or 1.0)
    return {
        "mev": round(m["mev"] * f, 1),
        "mav_low": round(m["mav"][0] * f, 1),
        "mav_high": round(m["mav"][1] * f, 1),
        "mrv": round(m["mrv"] * f, 1),
    }


def _classify(sets: float, lm: dict, days_since: float | None) -> str:
    if sets <= 0 and (days_since is None or days_since >= 10):
        return "esquecido"
    if sets < lm["mev"]:
        return "abaixo"
    if sets < lm["mav_low"]:
        return "manutencao"
    if sets <= lm["mav_high"]:
        return "otimo"
    if sets <= lm["mrv"]:
        return "alto"
    return "excesso"


WARMUP_SHARE = 0.60  # série com MENOS de 60% da carga mais pesada do exercício, no começo, é aquecimento
WARMUP_MAX = 3       # no máximo as 3 primeiras séries podem ser tratadas como aquecimento


def detect_warmup(session: dict) -> bool:
    """A detecção de aquecimento vale para treino livre e atividade do relógio (onde as séries de aquecimento
    são registradas junto). Treino aberto pela planilha/agenda (plan_id) já separa o aquecimento: lá toda
    série registrada é de trabalho, mesmo quando a pessoa sobe a carga de uma série para a outra."""
    return not session.get("plan_id")


def _hard_sets(item: dict, detect: bool = True) -> list[dict]:
    """Séries de trabalho de um item registrado (ignora aquecimento e séries não feitas).

    Aquecimento marcado (`warmup: true`) nunca conta; `warmup: false` força a contar. Sem marca (e com
    `detect`), as primeiras séries (até 3, seguidas, desde a primeira) com menos de 60% da carga mais pesada
    do exercício são tratadas como aquecimento. Série leve DEPOIS de uma série de trabalho (drop set,
    back-off) continua contando. Exercício com o peso do corpo não entra na detecção (a carga anotada é só
    o peso extra).
    """
    sets = [s for s in (item.get("sets") or []) if s.get("done", True)]
    loads = [float(s.get("load_kg") or 0) for s in sets]
    top = max(loads, default=0.0)
    ex = catalog.get(item.get("exercise_id")) or {}
    auto = detect and top > 0 and ex.get("equipment") != "body only"
    out, ramp = [], True
    for i, s in enumerate(sets):
        w = s.get("warmup")
        if w is None:
            w = bool(auto and ramp and i < WARMUP_MAX and 0 < loads[i] < WARMUP_SHARE * top)
            ramp = ramp and w
        if not w:
            out.append(s)
    return out


def session_muscle_load(session: dict) -> dict[str, dict]:
    """Séries ponderadas e tonelagem por músculo numa sessão."""
    out: dict[str, dict] = defaultdict(lambda: {"sets": 0.0, "direct": 0.0, "indirect": 0.0, "tonnage": 0.0, "rpe": []})
    detect = detect_warmup(session)
    for it in session.get("items", []):
        ex = catalog.get(it.get("exercise_id"))
        if not ex or ex["kind"] == "cardio" or ex["kind"] == "stretching":
            continue
        hs = _hard_sets(it, detect)
        n = len(hs)
        if not n:
            continue
        ton = sum((s.get("reps") or 0) * (s.get("load_kg") or 0) for s in hs)
        rpes = [s["rpe"] for s in hs if s.get("rpe")]
        for m in ex["primaryMuscles"]:
            if m in MUSCLES:
                out[m]["sets"] += n * PRIMARY_WEIGHT
                out[m]["direct"] += n
                out[m]["tonnage"] += ton
                out[m]["rpe"] += rpes
        for m in ex["secondaryMuscles"]:
            if m in MUSCLES:
                out[m]["sets"] += n * SECONDARY_WEIGHT
                out[m]["indirect"] += n
                out[m]["tonnage"] += ton * SECONDARY_WEIGHT
    return out


def muscle_volume(sessions: list[dict], start: datetime, end: datetime) -> dict[str, dict]:
    vol = {m: {"sets": 0.0, "direct": 0.0, "indirect": 0.0, "tonnage": 0.0, "sessions": 0} for m in MUSCLES}
    for s in sessions:
        d = _to_dt(s["date"])
        if not (start <= d <= end):
            continue
        for m, v in session_muscle_load(s).items():
            vol[m]["sets"] += v["sets"]
            vol[m]["direct"] += v["direct"]
            vol[m]["indirect"] += v["indirect"]
            vol[m]["tonnage"] += v["tonnage"]
            vol[m]["sessions"] += 1
    return vol


def last_trained(sessions: list[dict]) -> dict[str, dict]:
    """Último estímulo por músculo e a carga daquela sessão (para recuperação)."""
    out: dict[str, dict] = {}
    for s in sorted(sessions, key=lambda x: _to_dt(x["date"])):
        d = _to_dt(s["date"])
        for m, v in session_muscle_load(s).items():
            if v["direct"] > 0 or v["sets"] >= 2:
                rpe = sum(v["rpe"]) / len(v["rpe"]) if v["rpe"] else None
                out[m] = {"date": d, "sets": v["sets"], "rpe": rpe}
    return out


def recovery(sessions: list[dict], now: datetime | None = None) -> dict[str, dict]:
    now = now or clock.now()
    lt = last_trained(sessions)
    out = {}
    for m, info in MUSCLES.items():
        if m not in lt:
            out[m] = {"ready_pct": 100, "hours_since": None, "status": "pronto", "needed_h": info["recovery_h"]}
            continue
        last = lt[m]
        needed = info["recovery_h"]
        if last["sets"] > 6:
            needed *= 1.25
        elif last["sets"] < 3:
            needed *= 0.75
        if last["rpe"] and last["rpe"] >= 9:
            needed *= 1.15
        hours = max(0.0, (now - last["date"]).total_seconds() / 3600)
        pct = min(100, round(hours / needed * 100))
        status = "pronto" if pct >= 100 else ("quase" if pct >= 70 else "recuperando")
        out[m] = {"ready_pct": pct, "hours_since": round(hours), "status": status, "needed_h": round(needed),
                  "last_date": last["date"].date().isoformat()}
    return out


def _ratio(a: float, b: float) -> float | None:
    if b <= 0:
        return None if a <= 0 else float("inf")
    return round(a / b, 2)


def balance(vol: dict[str, dict]) -> list[dict]:
    s = {m: v["sets"] for m, v in vol.items()}
    push = s["chest"] + s["triceps"] + 0.5 * s["shoulders"]
    pull = s["lats"] + s["middle back"] + s["biceps"] + 0.5 * s["traps"]
    anterior = sum(v for m, v in s.items() if MUSCLES[m]["chain"] == "anterior")
    posterior = sum(v for m, v in s.items() if MUSCLES[m]["chain"] == "posterior")
    upper = sum(v for m, v in s.items() if MUSCLES[m]["region"] == "superior")
    lower = sum(v for m, v in s.items() if MUSCLES[m]["region"] == "inferior")

    checks = [
        {
            "id": "push_pull", "label": "Puxar ÷ Empurrar (membros superiores)",
            "a": round(pull, 1), "b": round(push, 1), "ratio": _ratio(pull, push), "ideal": (0.9, 1.6),
            "low_msg": "Você empurra mais do que puxa — risco para postura e saúde do ombro. Adicione remadas/puxadas.",
            "high_msg": "Volume de puxar bem acima do de empurrar. Ok se intencional; senão equilibre com supinos/desenvolvimentos.",
        },
        {
            "id": "ham_quad", "label": "Posteriores ÷ Quadríceps",
            "a": round(s["hamstrings"], 1), "b": round(s["quadriceps"], 1), "ratio": _ratio(s["hamstrings"], s["quadriceps"]),
            "ideal": (0.6, 1.6),
            "low_msg": "Posteriores de coxa pouco treinados em relação ao quadríceps (fator de risco para lesão de joelho/isquiotibiais). Inclua flexora, stiff ou RDL.",
            "high_msg": "Posteriores acima do quadríceps. Verifique se o quadríceps não está esquecido.",
        },
        {
            "id": "post_ant", "label": "Cadeia posterior ÷ anterior",
            "a": round(posterior, 1), "b": round(anterior, 1), "ratio": _ratio(posterior, anterior), "ideal": (0.9, 1.5),
            "low_msg": "Treino dominado pela cadeia anterior (peito, quadríceps, bíceps). Reforce costas, glúteos e posteriores.",
            "high_msg": "Cadeia posterior bem à frente da anterior.",
        },
        {
            "id": "lower_upper", "label": "Membros inferiores ÷ superiores",
            "a": round(lower, 1), "b": round(upper, 1), "ratio": _ratio(lower, upper), "ideal": (0.5, 1.2),
            "low_msg": "Membros inferiores negligenciados em relação aos superiores (\"não pula o dia de perna\").",
            "high_msg": "Foco grande em membros inferiores.",
        },
    ]
    for c in checks:
        r = c["ratio"]
        lo, hi = c["ideal"]
        if r is None:
            c["status"], c["message"] = "sem_dados", "Sem volume suficiente para comparar."
        elif r < lo:
            c["status"], c["message"] = "baixo", c["low_msg"]
        elif r > hi:
            c["status"], c["message"] = "alto", c["high_msg"]
        else:
            c["status"], c["message"] = "ok", "Equilibrado."
        if r == float("inf"):
            c["ratio"] = None
        del c["low_msg"], c["high_msg"]
    return checks


def cardio_summary(sessions: list[dict], start: datetime, end: datetime, profile: dict | None) -> dict:
    weight = float((profile or {}).get("weight_kg") or 75)
    by_zone = defaultdict(float)
    by_machine = defaultdict(float)
    moderate = vigorous = light = 0.0
    kcal = 0.0
    for s in sessions:
        d = _to_dt(s["date"])
        if not (start <= d <= end):
            continue
        for it in s.get("items", []):
            ex = catalog.get(it.get("exercise_id"))
            if not ex or ex["kind"] != "cardio":
                continue
            mins = float(it.get("duration_min") or 0)
            if mins <= 0:
                continue
            easy = bool(it.get("light")) or it.get("exercise_id") == "atividade_mobilidade"  # yoga/alongamento: sempre leve
            zone = 1 if easy else int(it.get("zone") or 2)
            intensity = HR_ZONES.get(zone, HR_ZONES[2])["intensity"]
            by_zone[zone] += mins
            by_machine[ex["name"]] += mins
            if intensity == "vigoroso":
                vigorous += mins
            elif intensity == "moderado":
                moderate += mins
            else:
                light += mins
            met = (ex.get("met") or {}).get(intensity, 6.0)
            kcal += met * weight * mins / 60
    who = moderate + 2 * vigorous
    if who >= 300:
        msg = "Acima de 300 min equivalentes: ótimo para saúde cardiovascular; monitore a recuperação."
    elif who >= 150:
        msg = "Dentro da recomendação da OMS (150–300 min moderados ou 75–150 vigorosos por semana)."
    else:
        msg = f"Faltam {round(150 - who)} min equivalentes para a recomendação mínima da OMS (150 min/semana)."
    return {
        "minutes_total": round(light + moderate + vigorous),
        "light": round(light), "moderate": round(moderate), "vigorous": round(vigorous),
        "who_equivalent": round(who), "who_target": (150, 300), "message": msg,
        "by_zone": {z: round(v) for z, v in sorted(by_zone.items())},
        "by_machine": {k: round(v) for k, v in by_machine.items()},
        "kcal_estimate": round(kcal),
    }


def suggest_for_muscle(muscle: str, user_limitations: list[dict], exclude: set[str] | None = None, n: int = 3) -> list[dict]:
    """Sugere exercícios para um músculo respeitando as limitações.

    Ordem: populares liberados > populares com cuidado > demais liberados.
    Exercícios marcados como "evitar" nunca são sugeridos.
    """
    buckets: dict[int, list[dict]] = {0: [], 1: [], 2: []}
    for ex in catalog.search(muscle=muscle, kind="strength", limit=200):
        if (exclude and ex["id"] in exclude) or muscle not in ex["primaryMuscles"]:
            continue
        st = check_exercise(ex, user_limitations)["status"]
        if st == "evitar":
            continue
        entry = {"id": ex["id"], "name": ex["name"], "status": st}
        if ex["popular"]:
            buckets[0 if st == "ok" else 1].append(entry)
        elif st == "ok":
            buckets[2].append(entry)
    return (buckets[0] + buckets[1] + buckets[2])[:n]


def _n(v) -> str:
    """Número em pt-BR sem casas inúteis: 7.0 → "7", 0.4 → "0,4"."""
    v = round(float(v), 1)
    return (f"{v:.0f}" if v == int(v) else f"{v:.1f}").replace(".", ",")


LEG_SPORTS = {"corrida", "ciclismo", "caminhada", "trilha", "remo"}


def weekly_report(sessions: list[dict], profile: dict | None = None, now: datetime | None = None,
                  expected: dict[str, float] | None = None, deload: bool = False) -> dict:
    """`expected` = séries/semana que o PLANO prevê por músculo (o objetivo saúde prevê menos que hipertrofia):
    quem cumpre o plano não recebe aviso de "abaixo do mínimo". `deload` = semana leve do ciclo."""
    now = now or clock.now()
    end = now
    start7 = now - timedelta(days=7)
    start28 = now - timedelta(days=28)
    lims = (profile or {}).get("limitations") or []

    vol7 = muscle_volume(sessions, start7, end)
    vol28 = muscle_volume(sessions, start28, end)
    lt = last_trained(sessions)
    rec = recovery(sessions, now)

    muscles = []
    for m in MUSCLES:
        lm = _landmarks(m, profile)
        days_since = (now - lt[m]["date"]).total_seconds() / 86400 if m in lt else None
        sets7 = round(vol7[m]["sets"], 1)
        status = _classify(sets7, lm, days_since)
        muscles.append({
            "muscle": m, "pt": muscle_pt(m), "region": MUSCLES[m]["region"],
            "sets_7d": sets7, "direct_7d": vol7[m]["direct"], "indirect_7d": vol7[m]["indirect"],
            "sets_avg_4w": round(vol28[m]["sets"] / 4, 1),
            "tonnage_7d": round(vol7[m]["tonnage"]),
            "frequency_7d": vol7[m]["sessions"],
            "days_since": None if days_since is None else round(days_since, 1),
            "landmarks": lm, "status": status, "status_pt": STATUS_PT[status],
            "recovery": rec[m],
        })

    ranked = sorted(muscles, key=lambda x: -x["sets_7d"])
    bal = balance(vol7)
    cardio = cardio_summary(sessions, start7, end, profile)

    # contexto que muda a leitura: começo de uso, treino do relógio sem séries, corrida/pedal treinando as pernas
    dated = [_to_dt(s["date"]) for s in sessions]
    first = min(dated) if dated else None
    new_user = first is not None and (now - first).days < 10
    week = [s for s in sessions if start7 <= _to_dt(s["date"]) <= end]
    no_sets = sum(1 for s in week if any(it.get("strength_min") or it.get("exercise_id") == "Garmin_musculacao"
                                         for it in s.get("items", [])) and not any(_hard_sets(it, detect_warmup(s)) for it in s.get("items", [])))
    with_sets = sum(1 for s in week if any(_hard_sets(it, detect_warmup(s)) for it in s.get("items", []) if (catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength"))
    from .load import sport_of
    leg_min = sum(float(it.get("duration_min") or 0) for s in week for it in s.get("items", []) if sport_of(it) in LEG_SPORTS)

    recs: list[dict] = []
    trained_ids = {it.get("exercise_id") for s in sessions for it in s.get("items", [])}
    for mm in muscles:
        if mm["muscle"] == "neck":
            continue
        exp = (expected or {}).get(mm["muscle"])
        # quem cumpre o que o plano prevê (≥ 85%) não está "abaixo": o objetivo pede menos volume
        on_plan = exp is not None and mm["sets_7d"] >= 0.85 * exp
        if mm["status"] in ("esquecido", "abaixo") and (deload or new_user or on_plan or (no_sets and no_sets >= with_sets)):
            continue
        if mm["status"] == "esquecido" and exp is not None and exp < 1:
            continue  # o plano não trabalha esse músculo (limitação ou objetivo)
        if mm["status"] == "esquecido":
            recs.append({"level": "alerta", "muscle": mm["muscle"],
                         "text": f"{mm['pt']} está esquecido" + (f" (último estímulo há {round(mm['days_since'])} dias)." if mm["days_since"] else " (nenhum registro)."),
                         "suggestions": suggest_for_muscle(mm["muscle"], lims, trained_ids)})
        elif mm["status"] == "abaixo" and MUSCLES[mm["muscle"]]["size"] != "pequeno":
            goal = min(mm["landmarks"]["mev"], exp) if exp else mm["landmarks"]["mev"]
            falta = max(0.5, goal - mm["sets_7d"])
            ref = "o que o seu plano prevê" if exp and exp < mm["landmarks"]["mev"] else "o mínimo efetivo"
            recs.append({"level": "atencao", "muscle": mm["muscle"],
                         "text": f"{mm['pt']}: {_n(mm['sets_7d'])} {'série' if mm['sets_7d'] == 1 else 'séries'} na semana — faltam ~{_n(falta)} para {ref}.",
                         "suggestions": suggest_for_muscle(mm["muscle"], lims, trained_ids)})
        elif mm["status"] == "excesso":
            recs.append({"level": "alerta", "muscle": mm["muscle"],
                         "text": f"{mm['pt']}: {_n(mm['sets_7d'])} séries — acima do volume recuperável (~{_n(mm['landmarks']['mrv'])}). Considere reduzir ou fazer um deload.",
                         "suggestions": []})
        if mm["frequency_7d"] == 1 and mm["sets_7d"] >= 10:
            recs.append({"level": "dica", "muscle": mm["muscle"],
                         "text": f"{mm['pt']}: todo o volume em uma só sessão. Dividir em 2× por semana tende a render mais.",
                         "suggestions": []})
    # corrida/pedal: perna treinada de forma aeróbia — o aviso de força de perna vira dica, com o contexto
    if leg_min >= 90:
        for r in recs:
            if r.get("muscle") in ("quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors") and "recuperável" not in r["text"]:
                r["level"] = "dica"
                r["text"] += f" A corrida/pedal ({round(leg_min)} min na semana) treina as pernas de forma aeróbia; 1–2 exercícios de força de perna ainda ajudam a prevenir lesão."
    if no_sets:
        recs.append({"level": "dica", "muscle": None, "suggestions": [],
                     "text": f"{no_sets} treino{'s' if no_sets > 1 else ''} de musculação veio do relógio sem as séries: a análise por músculo "
                             "fica incompleta. Registre no app (Treinar) ou deixe o relógio contar as séries para ver tudo."})
    if deload:
        recs.insert(0, {"level": "dica", "muscle": None, "suggestions": [],
                        "text": "Semana de deload: volume baixo é o plano — o corpo assimila o ciclo e volta mais forte na próxima."})
    if new_user and sessions:
        recs.append({"level": "dica", "muscle": None, "suggestions": [],
                     "text": "Primeiros dias de registro: músculos esquecidos e equilíbrio aparecem depois de ~10 dias de treinos."})
    for b in bal:
        if b["status"] in ("baixo", "alto") and (b["a"] + b["b"]) < 8:
            b["status"], b["message"] = "sem_dados", "Pouco volume na semana para comparar."
        if b["status"] == "baixo" and not new_user and not deload:
            if b["id"] == "lower_upper" and leg_min >= 90:
                recs.append({"level": "dica", "muscle": None, "suggestions": [],
                             "text": b["message"] + f" Com {round(leg_min)} min de corrida/pedal na semana as pernas não estão paradas — "
                                                    "mas 1–2 exercícios de força de perna ajudam a prevenir lesão."})
            else:
                recs.append({"level": "atencao", "muscle": None, "text": b["message"], "suggestions": []})
    if cardio["who_equivalent"] < 150:
        recs.append({"level": "dica", "muscle": None, "text": "Cardio: " + cardio["message"], "suggestions": []})
    mz = max_zone(lims)
    if mz:
        recs.append({"level": "dica", "muscle": None,
                     "text": f"Por suas limitações, mantenha o cardio até a zona {mz} ({HR_ZONES[mz]['pt']}).", "suggestions": []})

    if not sessions:
        recs = [{"level": "dica", "muscle": None, "suggestions": [],
                 "text": "Ainda não há treinos registrados. Depois do primeiro treino o app mostra volume por músculo, "
                         "músculos esquecidos e equilíbrio."}]
    return {
        "period": {"start": start7.date().isoformat(), "end": end.date().isoformat()},
        "muscles": muscles,
        "most_trained": [{"pt": x["pt"], "sets": x["sets_7d"]} for x in ranked[:3] if x["sets_7d"] > 0],
        "least_trained": [{"pt": x["pt"], "sets": x["sets_7d"]} for x in ranked[::-1] if x["muscle"] != "neck"][:3],
        "balance": bal,
        "cardio": cardio,
        "recommendations": recs,
        "sessions_7d": sum(1 for s in sessions if start7 <= _to_dt(s["date"]) <= end),
    }


def plan_report(plan: dict, profile: dict | None = None) -> dict:
    """Volume semanal PLANEJADO por músculo a partir de uma planilha.

    plan = {"days_per_week": 4, "days": [{"name": "A", "items": [{"exercise_id", "sets", ...}]}]}
    Se o usuário treina mais dias que o nº de treinos da planilha, os treinos se repetem.
    """
    days = plan.get("days") or []
    lims = (profile or {}).get("limitations") or []
    dpw = int(plan.get("days_per_week") or len(days) or 1)
    rotation = dpw / max(1, len(days))

    vol = {m: {"sets": 0.0, "direct": 0.0, "indirect": 0.0, "tonnage": 0.0, "sessions": 0} for m in MUSCLES}
    conflicts = []
    cardio_min = defaultdict(float)
    for d in days:
        hit = set()
        for it in d.get("items", []):
            ex = catalog.get(it.get("exercise_id"))
            if not ex:
                continue
            chk = check_exercise(ex, lims)
            if chk["status"] != "ok":
                conflicts.append({"day": d.get("name"), "exercise_id": ex["id"], "name": ex["name"], **chk})
            if ex["kind"] == "cardio":
                cardio_min[int(it.get("zone") or 2)] += float(it.get("duration_min") or 0) * rotation
                continue
            if ex["kind"] == "stretching" or (it.get("phase") or "principal") != "principal":
                continue  # aquecimento/mobilidade não é volume de treino (e não é registrado como série)
            n = int(it.get("sets") or 0) * rotation
            for m in ex["primaryMuscles"]:
                if m in vol:
                    vol[m]["sets"] += n
                    vol[m]["direct"] += n
                    hit.add(m)
            for m in ex["secondaryMuscles"]:
                if m in vol:
                    vol[m]["sets"] += n * SECONDARY_WEIGHT
                    vol[m]["indirect"] += n
        for m in hit:
            vol[m]["sessions"] += rotation

    muscles = []
    for m in MUSCLES:
        lm = _landmarks(m, profile)
        sets = round(vol[m]["sets"], 1)
        status = _classify(sets, lm, None if sets else 99)
        muscles.append({"muscle": m, "pt": muscle_pt(m), "sets_week": sets, "frequency": round(vol[m]["sessions"], 1),
                        "landmarks": lm, "status": status, "status_pt": STATUS_PT[status]})
    who = sum(v * (2 if HR_ZONES[z]["intensity"] == "vigoroso" else (1 if HR_ZONES[z]["intensity"] == "moderado" else 0))
              for z, v in cardio_min.items())
    recs = []
    for mm in muscles:
        if mm["muscle"] == "neck":
            continue
        if mm["status"] in ("esquecido", "abaixo") and MUSCLES[mm["muscle"]]["size"] != "pequeno":
            recs.append({"level": "atencao" if mm["status"] == "abaixo" else "alerta", "muscle": mm["muscle"],
                         "text": f"A planilha dá {mm['sets_week']} séries/semana para {mm['pt']} (mínimo ~{mm['landmarks']['mev']}).",
                         "suggestions": suggest_for_muscle(mm["muscle"], lims)})
        elif mm["status"] == "excesso":
            recs.append({"level": "alerta", "muscle": mm["muscle"],
                         "text": f"{mm['pt']}: {mm['sets_week']} séries/semana planejadas — acima do recuperável (~{mm['landmarks']['mrv']}).",
                         "suggestions": []})
    for b in balance(vol):
        if b["status"] == "baixo":
            recs.append({"level": "atencao", "muscle": None, "text": b["message"], "suggestions": []})
    return {
        "days_per_week": dpw, "muscles": muscles, "balance": balance(vol), "conflicts": conflicts,
        "cardio_minutes_week": round(sum(cardio_min.values())), "cardio_who_equivalent": round(who),
        "recommendations": recs,
    }
