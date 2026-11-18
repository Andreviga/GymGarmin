"""Prova híbrida no formato HYROX: 8 × (1 km de corrida + 1 estação).

HYROX é marca da HYROX World GmbH; aqui ela só é citada como referência do formato (sem afiliação).
Pesos da temporada 2025/26 conforme guias públicos — confirme sempre no regulamento oficial da sua prova.

O que este módulo faz:
- `stations_for(division, sex)`: as 8 estações com o padrão da categoria;
- `predict(profile, ev)`: previsão de tempo (corrida pelo VDOT com fadiga das estações + estações e roxzone pelo nível);
- `splits(target_s, ...)`: o tempo-alvo dividido em parciais de corrida, estação e roxzone;
- `adapt_run_week(...)`: troca a sessão forte de corrida por corrida comprometida (1 km + estação) e,
  a 6 e a 3 semanas da prova, por simulados (meio e completo);
- `station_block(...)`: bloco de estações para colar no fim de um treino de força.
"""

from __future__ import annotations

from . import sports as sp

EVENT_ID = "hibrida"

# id, nome, exercício do catálogo, fração do tempo total (média Open masculino ~1:30), o que se faz na prova
STATIONS = [
    ("skierg", "SkiErg", "cardio_ski_erg", 0.053, "1000 m"),
    ("sled_push", "Trenó empurra", "Sled_Push", 0.039, "50 m (4 × 12,5 m)"),
    ("sled_pull", "Trenó puxa", "Sled_Pull_Rope", 0.056, "50 m puxando a corda"),
    ("bbj", "Burpee com salto à frente", "Burpee_Broad_Jump", 0.061, "80 m"),
    ("row", "Remo", "cardio_remo", 0.054, "1000 m"),
    ("farmers", "Farmer's carry", "Farmers_Walk", 0.022, "200 m"),
    ("lunges", "Afundo com saco", "Sandbag_Walking_Lunge", 0.056, "100 m"),
    ("wallball", "Wall ball", "Wall_Ball", 0.072, "100 repetições"),
]
RUN_SHARE = 0.50      # ~metade do tempo é corrida
ROX_SHARE = 0.089     # roxzone (transições) ~8 min numa prova de 1:30
REF_TOTAL_S = 90 * 60  # referência das frações acima

# carga por categoria (temporada 2025/26; sled = peso total com o trenó)
WEIGHTS = {
    ("open", "M"): {"sled_push": "152 kg", "sled_pull": "103 kg", "farmers": "2 × 24 kg", "lunges": "20 kg", "wallball": "6 kg"},
    ("open", "F"): {"sled_push": "102 kg", "sled_pull": "78 kg", "farmers": "2 × 16 kg", "lunges": "10 kg", "wallball": "4 kg"},
    ("pro", "M"): {"sled_push": "202 kg", "sled_pull": "153 kg", "farmers": "2 × 32 kg", "lunges": "30 kg", "wallball": "9 kg"},
    ("pro", "F"): {"sled_push": "152 kg", "sled_pull": "103 kg", "farmers": "2 × 24 kg", "lunges": "20 kg", "wallball": "6 kg"},
}
DIVISIONS = [("open", "Open"), ("pro", "Pro"), ("doubles", "Duplas (Open)")]
# fator do tempo das estações e roxzone (min) por nível, em relação à média Open
LEVEL = {"iniciante": (1.3, 11.0), "intermediario": (1.05, 8.5), "avancado": (0.95, 7.5), "elite": (0.9, 6.5)}


def _div(ev: dict) -> str:
    d = (ev or {}).get("division") or "open"
    return "open" if d == "doubles" else d


def _sex(profile: dict) -> str:
    return "F" if str((profile or {}).get("sex") or "").upper().startswith("F") else "M"


def stations_for(division: str = "open", sex: str = "M") -> list[dict]:
    w = WEIGHTS.get((division if division in ("open", "pro") else "open", sex), WEIGHTS[("open", "M")])
    out = []
    for sid, name, ex, frac, std in STATIONS:
        reps = std
        if sid == "wallball" and division == "open" and sex == "F":
            reps = "75 repetições (Open feminino 25/26; confira no regulamento)"
        out.append({"id": sid, "name": name, "exercise_id": ex, "standard": reps, "load": w.get(sid), "frac": frac})
    return out


def _tier_level(profile: dict) -> str:
    from .generator import tier_for
    return {"iniciante": "iniciante", "intermediario": "intermediario", "avancado": "avancado", "elite": "elite"}.get(
        tier_for((profile or {}).get("training_months")), "intermediario")


def predict(profile: dict, ev: dict) -> dict:
    """Previsão pela corrida (VDOT, com ~25% de fadiga das estações) + estações e roxzone pelo nível de treino."""
    a = sp.anchors_of(profile)
    div, sex = _div(ev), _sex(profile)
    st = stations_for(div, sex)
    fac, rox_min = LEVEL[_tier_level(profile)]
    if sex == "F":
        fac *= 1.06
    if div == "pro":
        fac *= 1.12
    station_s = [s["frac"] * REF_TOTAL_S * fac for s in st]
    run_s = sp.predict_time(a["run_vdot"], 8.0) * 1.25 if a.get("run_vdot") else None
    total = (run_s + sum(station_s) + rox_min * 60) if run_s else None
    tgt = sp.parse_duration((ev or {}).get("target"))
    out = {"name": "Prova híbrida (formato HYROX)", "date": (ev or {}).get("date"), "division": div, "sex": sex,
           "legs": [{"sport": "corrida", "distance": 8, "time": sp.fmt_duration(run_s) if run_s else None}],
           "transitions": sp.fmt_duration(rox_min * 60), "predicted": sp.fmt_duration(total) if total else None,
           "missing_anchors": [] if run_s else ["Corrida"], "target": sp.fmt_duration(tgt) if tgt else None,
           "gap_s": round(total - tgt) if tgt and total else None,
           "stations": [{**s, "time": sp.fmt_duration(t)} for s, t in zip(st, station_s)],
           "run_km": sp.fmt_pace(run_s / 8) if run_s else None}
    if tgt:
        out["splits"] = splits(tgt, div, sex)
    return out


def splits(target_s: float, division: str = "open", sex: str = "M") -> dict:
    """Tempo-alvo → parcial de cada km de corrida, de cada estação e da roxzone."""
    st = stations_for(division, sex)
    station_total = sum(s["frac"] for s in st)
    k = target_s / REF_TOTAL_S
    run_km = target_s * RUN_SHARE / 8
    rox = target_s * ROX_SHARE
    # se o alvo for muito rápido ou lento, a proporção estações/corrida se mantém (simples e honesto)
    return {"run_km": sp.fmt_pace(run_km), "run_total": sp.fmt_duration(run_km * 8), "roxzone": sp.fmt_duration(rox),
            "stations": [{"id": s["id"], "name": s["name"], "time": sp.fmt_duration(s["frac"] * REF_TOTAL_S * k)} for s in st],
            "stations_total": sp.fmt_duration(station_total * REF_TOTAL_S * k)}


# ------------------------------------------------------------------ semana de treino ----

def _race_pace(profile: dict, ev: dict, anchors: dict) -> float | None:
    """Ritmo de corrida da prova (s/km): pelo alvo se houver, senão pela previsão."""
    tgt = sp.parse_duration((ev or {}).get("target"))
    if tgt:
        return tgt * RUN_SHARE / 8
    if anchors.get("run_vdot"):
        return sp.predict_time(anchors["run_vdot"], 8.0) * 1.25 / 8
    return None


def _station_step(st: dict, dur_s: float, note: str) -> dict:
    return {"kind": "recovery", "dur_s": int(dur_s), "zone": 4, "note": f"{st['name']}: {note}"}


def compromised_session(profile: dict, ev: dict, anchors: dict, rounds: int, full: bool, title: str) -> dict:
    """Corrida comprometida: 1 km no ritmo de prova + estação logo em seguida, N vezes."""
    from .endurance import _repeat, _steady
    pace = _race_pace(profile, ev, anchors)
    st = stations_for(_div(ev), _sex(profile))
    fac = LEVEL[_tier_level(profile)][0]
    steps = [_steady("corrida", "warmup", 2, anchors, "rua", dur_s=10 * 60, note="10 min leve + 4 acelerações de 15 s")]
    order = st if full else [s for s in st if s["id"] in ("skierg", "sled_push", "row", "wallball", "lunges", "farmers")]
    for i in range(rounds):
        s = order[i % len(order)]
        run = _steady("corrida", "interval", 4, anchors, "rua", dist_m=1000, note="ritmo de prova")
        if pace:
            run["target"] = {"type": "pace", "fast_s_km": round(pace - 5), "slow_s_km": round(pace + 5),
                             "label": f"{sp.fmt_pace(pace - 5)}–{sp.fmt_pace(pace + 5)}"}
            run["dur_s"] = int(pace)
        dur = s["frac"] * REF_TOTAL_S * fac * (1 if full else 0.5)
        what = s["standard"] if full else "metade da estação (ex.: 500 m, 25 m, 50 reps)"
        steps.append(run)
        steps.append(_station_step(s, dur, f"{what}{' · ' + s['load'] if s.get('load') else ''}"))
    steps.append(_steady("corrida", "cooldown", 1, anchors, "rua", dur_s=8 * 60))
    total_s = sum(x.get("dur_s", 0) for x in steps)
    return {"exercise_id": "corrida_rua", "phase": "principal", "sport": "corrida",
            "duration_min": round(total_s / 60), "distance_km": round(rounds + 2.5, 1),
            "endurance": {"sport": "corrida", "env": "rua", "type": "comprometida", "title": title, "steps": steps,
                          "hard": True, "zone": 4, "dur_s": total_s, "dist_m": int((rounds + 2.5) * 1000),
                          "note": ("Simulado: faça as estações reais (ou a substituta da sua academia) e anote os tempos"
                                   if full else "Corra o km no ritmo de prova logo depois da estação: é a parte mais difícil da prova")}}


def adapt_run_week(sessions: list[dict], wv: dict, anchors: dict, profile: dict, ev: dict) -> list[dict]:
    """Construção e pico: a sessão forte vira corrida comprometida; a 6 e 3 semanas da prova, simulados."""
    phase, wtr = wv.get("phase"), wv.get("weeks_to_race")
    if phase not in ("construcao", "pico") or not sessions:
        return sessions
    out = list(sessions)
    if wtr in (6, 3):
        full = wtr == 3
        idx = next((i for i, s in enumerate(out) if s["endurance"]["type"] == "longo"), None)
        if idx is None:
            idx = next((i for i, s in enumerate(out) if s["endurance"]["hard"]), 0)
        out[idx] = compromised_session(profile, ev, anchors, 8 if full else 4, full,
                                       "Simulado completo (8 × 1 km + 8 estações)" if full else "Meio simulado (4 × 1 km + 4 estações)")
        out[idx]["endurance"]["role"] = "longo"
        # semana de simulado: o resto da corrida fica leve (o simulado já é o estímulo forte da semana)
        from .endurance import build_session
        for i, s in enumerate(out):
            if i != idx and s["endurance"]["hard"]:
                easy = build_session("corrida", "leve", max(4.0, round(s.get("distance_km") or 5)), {"env": s["endurance"].get("env") or "rua"},
                                     anchors, "intermediario")
                easy["endurance"]["note"] = "Semana de simulado: corrida leve para chegar descansado"
                out[i] = easy
        return out
    idx = next((i for i, s in enumerate(out) if s["endurance"]["hard"] and s["endurance"]["type"] != "teste"), None)
    if idx is not None:
        n = 4 if phase == "construcao" else 6
        out[idx] = compromised_session(profile, ev, anchors, n, False, f"Corrida comprometida {n} × (1 km + estação)")
    return out


# exercícios de estação para o treino de força (rodízio de 3 por semana)
BLOCKS = [
    [("Sled_Push", 4, "25 m", 120, "carga de prova ou um pouco acima; passos curtos, braços estendidos"),
     ("Wall_Ball", 4, "20", 60, "agache fundo e use a perna para lançar; respire a cada repetição"),
     ("Farmers_Walk", 4, "50 m", 60, "carga de prova; ombros baixos, passos rápidos")],
    [("Sled_Pull_Rope", 4, "25 m", 120, "puxe mão a mão com o quadril baixo; pés firmes"),
     ("Burpee_Broad_Jump", 4, "10", 60, "salto longo e constante; não pare entre as repetições"),
     ("Sandbag_Walking_Lunge", 3, "20 m", 75, "joelho de trás encosta no chão; saco nos ombros")],
    [("Wall_Ball", 5, "15", 45, "ritmo de prova: séries curtas sem parar"),
     ("Sled_Push", 3, "25 m", 120, "carga acima da prova para ganhar força"),
     ("Sandbag_Walking_Lunge", 3, "20 m", 75, "passos iguais do começo ao fim")],
]


def station_block(week_idx: int, profile: dict, ev: dict) -> list[dict]:
    st = {s["exercise_id"]: s for s in stations_for(_div(ev), _sex(profile))}
    out = []
    for ex, sets, reps, rest, note in BLOCKS[week_idx % len(BLOCKS)]:
        load = (st.get(ex) or {}).get("load")
        out.append({"exercise_id": ex, "sets": sets, "reps": reps, "rest_s": rest, "phase": "principal", "role": "acc",
                    "station": True, "notes": f"Estação da prova híbrida: {note}" + (f" · prova: {load}" if load else "")})
    return out
