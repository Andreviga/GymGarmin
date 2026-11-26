"""Treinos avulsos de cardio (corrida, bike, aparelhos da academia e HIIT) com a música seguindo as fases.

Corrida e bike usam o mesmo formato das sessões de resistência da agenda (passos com alvo de ritmo/potência/FC,
que vão para o relógio como treino de corrida ou bike). Aparelhos e HIIT usam itens de cardio do catálogo com
intervalos (vão para o relógio como treino de cardio).

`timeline()` transforma o treino em fases de música (aquecimento sobe o BPM, bloco forte em BPM alto, volta à calma
devagar); tiros curtos (menos de 4 min) viram um bloco só de música rápida, porque trocar de música a cada minuto
não funciona.
"""
from __future__ import annotations

from . import endurance as en
from . import generator
from . import sports as sp

CATS = [{"id": "corrida", "label": "Corrida"}, {"id": "bike", "label": "Bike"},
        {"id": "academia", "label": "Aparelhos"}, {"id": "hiit", "label": "HIIT"}]
MACHINES = {"cardio_eliptico": "Elíptico / transport", "cardio_escada": "Escada", "cardio_remo": "Remo ergômetro",
            "cardio_air_bike": "Air bike", "cardio_spinning": "Bike de spinning", "cardio_esteira_caminhada": "Esteira inclinada (caminhando)",
            "cardio_ski_erg": "Ski erg"}
ENVS = {"corrida": [("rua", "Rua"), ("esteira", "Esteira")], "bike": [("estrada", "Rua / estrada"), ("rolo", "Rolo"), ("spinning", "Spinning")]}
HIIT_MOVES = ["polichinelo", "agachamento com salto", "mountain climber", "burpee", "corrida parada com joelho alto",
              "afundo alternado", "prancha com toque no ombro", "skater (salto lateral)"]

# id: categoria, nome, descrição, minutos padrão, opções de minutos
TEMPLATES: dict[str, dict] = {
    "leve": {"cat": "corrida", "label": "Rodagem leve", "desc": "conversando, para base e recuperação", "min": 40, "mins": [25, 30, 40, 50, 60]},
    "progressiva": {"cat": "corrida", "label": "Progressiva", "desc": "começa leve e termina forte", "min": 40, "mins": [30, 40, 50, 60]},
    "ritmo": {"cat": "corrida", "label": "Ritmo forte", "desc": "bloco contínuo no limiar ('confortavelmente difícil')", "min": 45, "mins": [35, 45, 55]},
    "fartlek": {"cat": "corrida", "label": "Fartlek 1' forte / 2' leve", "desc": "brincando com o ritmo", "min": 40, "mins": [30, 40, 50]},
    "tiros400": {"cat": "corrida", "label": "Tiros de 400 m", "desc": "rápidos, com 1:30 trotando", "min": 45, "mins": [35, 45, 55]},
    "tiros1k": {"cat": "corrida", "label": "Tiros de 1 km", "desc": "fortes e iguais, 2 min trotando", "min": 50, "mins": [40, 50, 60]},
    "noruegues": {"cat": "corrida", "label": "Norueguês 4 × 4 min", "desc": "o que mais sobe o VO2máx", "min": 46, "mins": [39, 46]},
    "longao": {"cat": "corrida", "label": "Longão", "desc": "longo e leve", "min": 75, "mins": [60, 75, 90, 120]},
    "bike_leve": {"cat": "bike", "label": "Pedal leve", "desc": "giro solto, base aeróbia", "min": 45, "mins": [30, 45, 60, 90]},
    "bike_sweet": {"cat": "bike", "label": "Sweet spot", "desc": "blocos de 8 min firmes", "min": 50, "mins": [40, 50, 60, 75]},
    "bike_subidas": {"cat": "bike", "label": "Subidas 4 min", "desc": "força com cadência baixa", "min": 45, "mins": [35, 45, 60]},
    "bike_sprints": {"cat": "bike", "label": "Sprints 30 s", "desc": "tudo por 30 s, solto por 2:30", "min": 40, "mins": [30, 40, 50]},
    "acad_continuo": {"cat": "academia", "label": "Contínuo moderado", "desc": "ritmo constante, dá para falar frases curtas", "min": 30, "mins": [20, 30, 40, 50]},
    "acad_intervalado": {"cat": "academia", "label": "Intervalado 1' / 1'", "desc": "1 min forte, 1 min leve", "min": 30, "mins": [20, 30, 40]},
    "acad_hiit": {"cat": "academia", "label": "HIIT 30\" / 90\"", "desc": "30 s no máximo, 90 s bem leve", "min": 25, "mins": [20, 25, 30]},
    "tabata": {"cat": "hiit", "label": "Tabata", "desc": "blocos de 8 × (20 s forte / 10 s parado)", "min": 25, "mins": [20, 25, 30, 35]},
    "emom": {"cat": "hiit", "label": "EMOM 40/20", "desc": "a cada minuto: 40 s de exercício, 20 s de pausa", "min": 25, "mins": [20, 25, 30]},
    "trinta": {"cat": "hiit", "label": "30/30", "desc": "30 s forte, 30 s parado", "min": 20, "mins": [15, 20, 25, 30]},
    "circuito": {"cat": "hiit", "label": "Circuito 45/15", "desc": "8 exercícios em roda, 45 s cada", "min": 30, "mins": [20, 30, 40]},
}
RUN_SPORT = {"leve", "progressiva", "ritmo", "fartlek", "tiros400", "tiros1k", "noruegues", "longao"}
BIKE_SPORT = {"bike_leve", "bike_sweet", "bike_subidas", "bike_sprints"}


class CardioError(ValueError):
    pass


def options() -> dict:
    return {"cats": CATS, "templates": [{"id": k, **v} for k, v in TEMPLATES.items()],
            "machines": [{"id": k, "label": v} for k, v in MACHINES.items()], "envs": {k: [{"id": a, "label": b} for a, b in v] for k, v in ENVS.items()}}


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(v))))


def _level(profile: dict) -> str:
    return en._level(generator.tier_for(profile.get("training_months") if profile.get("training_months") is not None else 12))


def _end_item(sport: str, env: str, kind: str, title: str, steps: list[dict], note: str | None, hard: bool) -> dict:
    env, ex_id = sp.sport_env(sport, env)
    flat = []
    for st in steps:
        flat += [x for _ in range(st["n"]) for x in st["steps"]] if st["kind"] == "repeat" else [st]
    dur = sum(s.get("dur_s") or 0 for s in flat)
    dist = sum(s.get("dist_m") or 0 for s in flat)
    zone = max((s.get("zone") or 1) for s in flat) if hard else 2
    end = {"sport": sport, "env": env, "type": kind, "title": title, "steps": steps, "hard": hard, "zone": zone,
           "dur_s": int(dur), "dist_m": int(dist) if dist else None, "note": note, "phase": "avulso"}
    return {"exercise_id": ex_id, "phase": "principal", "sport": sport, "endurance": end,
            "duration_min": round(dur / 60), "zone": zone, **({"distance_km": round(dist / 1000, 2)} if dist else {})}


def _run(tpl: str, M: int, env: str, a: dict, level: str) -> dict:
    S, st, rs, rp = "corrida", en._steady, en._rest, en._repeat
    if tpl == "leve":
        return _end_item(S, env, tpl, "Rodagem leve", [st(S, "warmup", 1, a, env, dur_s=5 * 60, note="comece bem leve"),
                                                      st(S, "steady", 2, a, env, dur_s=(M - 5) * 60, note="leve: dá para conversar")], None, False)
    if tpl == "longao":
        return _end_item(S, env, tpl, "Longão", [st(S, "warmup", 1, a, env, dur_s=8 * 60),
                                                 st(S, "steady", 2, a, env, dur_s=(M - 8) * 60, note="constante; beba água a cada 20 min")],
                         "Leve carboidrato se passar de 75 min: 30–60 g por hora." if M > 75 else None, False)
    if tpl == "progressiva":
        third = (M - 10) * 60 / 3
        return _end_item(S, env, tpl, "Progressiva", [st(S, "warmup", 1, a, env, dur_s=5 * 60),
                                                      st(S, "steady", 2, a, env, dur_s=third, note="leve"),
                                                      st(S, "interval", 3, a, env, dur_s=third, note="moderado"),
                                                      st(S, "interval", 4, a, env, dur_s=third, note="forte, mas controlado"),
                                                      st(S, "cooldown", 1, a, env, dur_s=5 * 60)], "Cada terço um pouco mais rápido", True)
    if tpl == "ritmo":
        return _end_item(S, env, tpl, f"Ritmo forte {M - 18} min", [st(S, "warmup", 2, a, env, dur_s=10 * 60, note="leve + 3 acelerações de 20 s"),
                                                                     st(S, "interval", 4, a, env, dur_s=(M - 18) * 60),
                                                                     st(S, "cooldown", 1, a, env, dur_s=8 * 60)], "Ritmo de limiar: 'confortavelmente difícil'", True)
    if tpl == "fartlek":
        n = _clamp((M - 15) / 3, 4, 15)
        return _end_item(S, env, tpl, f"Fartlek {n} × (1' forte + 2' leve)", [
            st(S, "warmup", 2, a, env, dur_s=10 * 60),
            rp(n, [st(S, "interval", 5, a, env, dur_s=60), st(S, "recovery", 2, a, env, dur_s=120)]),
            st(S, "cooldown", 1, a, env, dur_s=5 * 60)], "No minuto forte, rápido mas sem sprintar", True)
    if tpl in ("tiros400", "tiros1k"):
        rep, rec = (400, 90) if tpl == "tiros400" else (1000, 120)
        v5 = sp.speed_mps(S, 5, a, env)
        n = _clamp((M - 20) * 60 / (rep / v5 + rec), 4 if rep == 400 else 3, 16 if rep == 400 else 8)
        return _end_item(S, env, tpl, f"Tiros {n} × {rep} m", [
            st(S, "warmup", 2, a, env, dur_s=12 * 60, note="leve + educativos e 4 acelerações de 20 s"),
            rp(n, [st(S, "interval", 5, a, env, dist_m=rep), rs(rec, sport=S, anchors=a, env=env, note="trotando")]),
            st(S, "cooldown", 1, a, env, dur_s=8 * 60)], "Forte, mas igual do primeiro ao último", True)
    if tpl == "noruegues":
        n = 3 if M < 43 or level == "iniciante" else 4
        return _end_item(S, env, tpl, f"Norueguês {n} × 4 min", [
            st(S, "warmup", 2, a, env, dur_s=10 * 60, note="leve + educativos"),
            rp(n, [st(S, "interval", 5, a, env, dur_s=4 * 60), st(S, "recovery", 1, a, env, dur_s=3 * 60)]),
            st(S, "cooldown", 1, a, env, dur_s=8 * 60)], "4 min forte (só palavras soltas) e 3 min trotando", True)
    raise CardioError(tpl)


def _bike(tpl: str, M: int, env: str, a: dict, level: str) -> dict:
    S, st, rp = "ciclismo", en._steady, en._repeat
    ftp = a.get("ftp_w")
    if tpl == "bike_leve":
        return _end_item(S, env, tpl, "Pedal leve", [st(S, "warmup", 1, a, env, dur_s=5 * 60),
                                                     st(S, "steady", 2, a, env, dur_s=(M - 10) * 60, note="cadência 85–95 rpm"),
                                                     st(S, "cooldown", 1, a, env, dur_s=5 * 60)], None, False)
    if tpl == "bike_sweet":
        n = _clamp((M - 18) / 11, 2, 5)
        return _end_item(S, env, tpl, f"Sweet spot {n} × 8 min", [
            st(S, "warmup", 2, a, env, dur_s=10 * 60),
            rp(n, [{**st(S, "interval", 3, a, env, dur_s=8 * 60), "target": en._pct_ftp_target(ftp, 0.88, 0.93, 3, a)},
                   st(S, "recovery", 1, a, env, dur_s=3 * 60)]),
            st(S, "cooldown", 1, a, env, dur_s=8 * 60)], "88–93% do FTP: firme e sustentável", True)
    if tpl == "bike_subidas":
        n = _clamp((M - 18) / 7, 3, 6)
        return _end_item(S, env, tpl, f"Subidas {n} × 4 min", [
            st(S, "warmup", 2, a, env, dur_s=10 * 60),
            rp(n, [{**st(S, "interval", 4, a, env, dur_s=4 * 60), "note": "cadência 60–75 rpm, carga alta"},
                   st(S, "recovery", 1, a, env, dur_s=3 * 60)]),
            st(S, "cooldown", 1, a, env, dur_s=8 * 60)], "Força na pedalada: marcha pesada, tronco firme", True)
    if tpl == "bike_sprints":
        n = _clamp((M - 20) * 60 / 180, 4, 12)
        return _end_item(S, env, tpl, f"Sprints {n} × 30 s", [
            st(S, "warmup", 2, a, env, dur_s=12 * 60),
            rp(n, [{**st(S, "interval", 6, a, env, dur_s=30), "target": {"type": "open", "label": "tudo"}},
                   st(S, "recovery", 1, a, env, dur_s=150)]),
            st(S, "cooldown", 1, a, env, dur_s=8 * 60)], "30 s no máximo, 2:30 girando solto", True)
    raise CardioError(tpl)


def _cardio(eid: str, mins: float, zone: int, phase: str = "principal", notes: str | None = None, **kw) -> dict:
    it = {"exercise_id": eid, "phase": phase, "duration_min": round(mins, 1), "zone": zone, **kw}
    if notes:
        it["notes"] = notes
    return it


def _gym(tpl: str, M: int, machine: str) -> tuple[str, list[dict]]:
    m = machine if machine in MACHINES else "cardio_eliptico"
    lab = MACHINES[m]
    warm, cool = _cardio(m, 5, 1, "preparo", "bem leve, subindo aos poucos"), _cardio(m, 5, 1, "pos", "bem leve, soltando")
    if tpl == "acad_continuo":
        return f"{lab}: contínuo {M} min", [warm, _cardio(m, M - 10, 3, notes="moderado: dá para falar frases curtas"), cool]
    if tpl == "acad_intervalado":
        n = _clamp((M - 10) / 2, 5, 20)
        return f"{lab}: {n} × (1' forte + 1' leve)", [warm, _cardio(m, n * 2, 4, notes="no minuto forte: pesado, mas aguenta o minuto todo",
                                                                    intervals={"repeats": n, "work_s": 60, "rest_s": 60, "work_zone": 4, "rest_zone": 2}), cool]
    if tpl == "acad_hiit":
        n = _clamp((M - 10) / 2, 4, 10)
        return f"{lab}: HIIT {n} × 30\"", [warm, _cardio(m, n * 2, 5, notes="30 s no máximo, 90 s bem leve",
                                                         intervals={"repeats": n, "work_s": 30, "rest_s": 90, "work_zone": 5, "rest_zone": 1}), cool]
    raise CardioError(tpl)


def _hiit(tpl: str, M: int) -> tuple[str, list[dict]]:
    E = "atividade_hiit"
    warm = _cardio(E, 5, 2, "preparo", "polichinelo leve, corrida parada, mobilidade de quadril e ombro")
    cool = _cardio(E, 5, 1, "pos", "caminhando e alongando")
    moves = HIIT_MOVES
    if tpl == "tabata":
        b = _clamp((M - 10) / 5, 2, 5)
        blocks = [_cardio(E, 4, 5, notes=f"Bloco {i + 1}: {moves[i % len(moves)]}" + (" · 1 min de descanso antes" if i else ""),
                          intervals={"repeats": 8, "work_s": 20, "rest_s": 10, "work_zone": 5, "rest_zone": 2},
                          **({"warmup_min": 1} if i else {})) for i in range(b)]
        return f"Tabata · {b} blocos", [warm, *blocks, cool]
    if tpl == "emom":
        n = _clamp(M - 10, 8, 30)
        return f"EMOM {n} min (40/20)", [warm, _cardio(E, n, 4, notes="revezando: " + ", ".join(moves[:4]),
                                                      intervals={"repeats": n, "work_s": 40, "rest_s": 20, "work_zone": 4, "rest_zone": 2}), cool]
    if tpl == "trinta":
        n = _clamp(M - 10, 6, 24)
        return f"30/30 × {n}", [warm, _cardio(E, n, 5, notes="revezando: " + ", ".join(moves[:6]),
                                              intervals={"repeats": n, "work_s": 30, "rest_s": 30, "work_zone": 5, "rest_zone": 1}), cool]
    if tpl == "circuito":
        n = _clamp(M - 10, 8, 32)
        return f"Circuito {n} × 45\"", [warm, _cardio(E, n, 4, notes="na ordem: " + ", ".join(moves),
                                                      intervals={"repeats": n, "work_s": 45, "rest_s": 15, "work_zone": 4, "rest_zone": 2}), cool]
    raise CardioError(tpl)


def build(tpl: str, minutes: int | None, profile: dict, env: str | None = None, machine: str | None = None) -> dict:
    """Monta o treino avulso: {"title", "kind", "items"} (itens no formato da agenda)."""
    t = TEMPLATES.get(tpl)
    if not t:
        raise CardioError("Escolha um tipo de treino.")
    M = int(minutes or t["min"])
    M = max(min(t["mins"]), min(max(t["mins"]), M))
    a, level = sp.anchors_of(profile), _level(profile)
    if tpl in RUN_SPORT:
        item = _run(tpl, M, env if env in dict(ENVS["corrida"]) else "rua", a, level)
        return {"title": item["endurance"]["title"], "kind": "corrida", "items": [item], "minutes": round(item["endurance"]["dur_s"] / 60)}
    if tpl in BIKE_SPORT:
        item = _bike(tpl, M, env if env in dict(ENVS["bike"]) else "estrada", a, level)
        return {"title": item["endurance"]["title"], "kind": "bike", "items": [item], "minutes": round(item["endurance"]["dur_s"] / 60)}
    title, items = _gym(tpl, M, machine or "") if t["cat"] == "academia" else _hiit(tpl, M)
    mins = sum(i.get("duration_min") or 0 for i in items) + sum(i.get("warmup_min") or 0 for i in items)
    return {"title": title, "kind": t["cat"], "items": items, "minutes": round(mins)}


# ------------------------------------------------------------------ música por fase
def _mode(kind: str, zone: int, run: bool) -> str:
    if kind == "warmup":
        return "aquecimento"
    if kind == "cooldown":
        return "calma"
    if kind in ("recovery", "rest"):
        return "corrida_leve" if run else "aquecimento"
    if run:
        return "corrida_leve" if zone <= 2 else "corrida_ritmo" if zone <= 4 else "tiros"
    return "musculacao" if zone <= 3 else "gas"


SHORT_WORK_S = 240   # tiro mais curto que isso: o bloco inteiro (tiros + recuperações) é uma música rápida só
SHORT_REST_S = 150

LABEL = {"aquecimento": "Aquecimento", "musculacao": "Ritmo moderado", "gas": "Bloco forte", "corrida_leve": "Corrida leve",
         "corrida_ritmo": "Corrida forte", "tiros": "Tiros", "calma": "Volta à calma"}


def timeline(items: list[dict]) -> list[dict]:
    """Fases de música do treino: [{"mode", "dur_s", "label"}], já juntando fases vizinhas iguais."""
    seg: list[dict] = []

    def add(mode: str, dur: float, label: str | None = None) -> None:
        if dur <= 0:
            return
        seg.append({"mode": mode, "dur_s": int(dur), "label": label or LABEL[mode]})

    for it in items:
        end = it.get("endurance")
        if end:
            run = end["sport"] in ("corrida", "caminhada", "trilha")
            for st in end["steps"]:
                if st["kind"] != "repeat":
                    add(_mode(st["kind"], st.get("zone") or 2, run), st.get("dur_s") or 0)
                    continue
                inner = st["steps"]
                work = [x for x in inner if x["kind"] not in ("recovery", "rest")]
                rest = [x for x in inner if x["kind"] in ("recovery", "rest")]
                wz = max((x.get("zone") or 2) for x in work) if work else 2
                if max((x.get("dur_s") or 0) for x in work or [{}]) < SHORT_WORK_S or max((x.get("dur_s") or 0) for x in rest or [{}]) < SHORT_REST_S:
                    add(_mode("interval", wz, run), st["n"] * sum(x.get("dur_s") or 0 for x in inner), f"{st['n']} tiros")
                else:
                    for _ in range(st["n"]):
                        for x in inner:
                            add(_mode(x["kind"], x.get("zone") or 2, run), x.get("dur_s") or 0)
            continue
        ph = it.get("phase") or "principal"
        if ph == "preparo":
            add("aquecimento", (it.get("duration_min") or 5) * 60)
            continue
        if ph == "pos":
            add("calma", (it.get("duration_min") or 5) * 60)
            continue
        iv = it.get("intervals") or {}
        if it.get("warmup_min"):
            add("aquecimento", it["warmup_min"] * 60, "Pausa")
        if iv.get("repeats"):
            add("gas" if (iv.get("work_zone") or 4) >= 4 else "musculacao", iv["repeats"] * ((iv.get("work_s") or 60) + (iv.get("rest_s") or 60)))
        else:
            add(_mode("steady", it.get("zone") or 2, False), (it.get("duration_min") or 10) * 60)
        if it.get("cooldown_min"):
            add("calma", it["cooldown_min"] * 60)
    out: list[dict] = []
    for s in seg:   # fases vizinhas com a mesma música viram uma; pausa de até 2 min fica com a música de antes
        if out and (out[-1]["mode"] == s["mode"] or s["dur_s"] <= 120):
            out[-1]["dur_s"] += s["dur_s"]
        else:
            out.append(dict(s))
    t = 0
    for s in out:
        s["start_s"] = t
        t += s["dur_s"]
    return out
