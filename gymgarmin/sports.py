"""Modalidades de resistência: definições, âncoras de intensidade e zonas.

Cada modalidade tem uma "âncora" (o número que define a intensidade dela):

- corrida   → VDOT (Daniels), tirado de um tempo de prova/teste; dá ritmos em min/km
- ciclismo  → FTP (W); sem medidor de potência, cai para frequência cardíaca
- natação   → CSS (velocidade crítica, s/100 m), do teste 400 m + 200 m
- remo      → tempo dos 2 km; dá o split /500 m
- caminhada / trilha → frequência cardíaca

Todas usam as mesmas 5 zonas (Z1 recuperação … Z5 VO2máx). A zona vira o alvo
que o relógio entende em cada esporte: ritmo, watts, ritmo/100 m, split ou bpm.
Sem âncora, qualquer modalidade cai para zona de FC (LTHR ou FC máx.).
"""
from __future__ import annotations

import math
import re

ZONE_NAMES = {1: "Recuperação", 2: "Aeróbico leve", 3: "Moderado / ritmo", 4: "Limiar", 5: "VO2máx", 6: "Anaeróbico"}

# ------------------------------------------------------------------ modalidades
# env: ambiente → (exercise_id do catálogo, FIT sport/sub_sport, sport do Connect)
SPORTS: dict[str, dict] = {
    "corrida": {
        "name": "Corrida", "anchor": "vdot", "unit": "km", "impact": "alto", "legs": True,
        "envs": {
            "rua": ("corrida_rua", ("running", "generic"), "running"),
            "esteira": ("cardio_esteira_corrida", ("running", "treadmill"), "running"),
            "trilha": ("corrida_trilha", ("running", "trail"), "running"),
            "pista": ("corrida_pista", ("running", "track"), "running"),
        },
        "garmin_types": ["running", "street_running", "track_running", "trail_running", "treadmill_running",
                         "indoor_running", "virtual_run", "ultra_run", "obstacle_run"],
    },
    "ciclismo": {
        "name": "Ciclismo", "anchor": "ftp", "unit": "km", "impact": "baixo", "legs": True,
        "envs": {
            "estrada": ("ciclismo_estrada", ("cycling", "road"), "cycling"),
            "rolo": ("ciclismo_rolo", ("cycling", "indoor_cycling"), "cycling"),
            "spinning": ("cardio_spinning", ("cycling", "indoor_cycling"), "cycling"),
            "mtb": ("ciclismo_mtb", ("cycling", "mountain"), "cycling"),
        },
        "garmin_types": ["cycling", "road_biking", "indoor_cycling", "virtual_ride", "mountain_biking",
                         "gravel_cycling", "cyclocross", "track_cycling", "e_bike_fitness", "recumbent_cycling",
                         "e_bike_mountain", "enduro_mtb", "e_enduro_mtb", "downhill_biking", "bmx"],
    },
    "natacao": {
        "name": "Natação", "anchor": "css", "unit": "m", "impact": "baixo", "legs": False,
        "envs": {
            "piscina": ("natacao_piscina", ("swimming", "lap_swimming"), "swimming"),
            "aguas_abertas": ("natacao_aguas_abertas", ("swimming", "open_water"), "swimming"),
        },
        "garmin_types": ["lap_swimming", "open_water_swimming", "swimming"],
    },
    "remo": {
        "name": "Remo", "anchor": "row2k", "unit": "m", "impact": "baixo", "legs": True,
        "envs": {"indoor": ("cardio_remo", ("rowing", "indoor_rowing"), "cardio_training"),
                 "agua": ("remo_agua", ("rowing", "generic"), "other")},
        "garmin_types": ["indoor_rowing", "rowing", "rowing_v2"],
    },
    "caminhada": {
        "name": "Caminhada", "anchor": "hr", "unit": "km", "impact": "baixo", "legs": False,
        "envs": {"rua": ("caminhada_rua", ("walking", "generic"), "other"),
                 "esteira": ("cardio_esteira_caminhada", ("walking", "indoor_walking"), "other")},
        "garmin_types": ["walking", "casual_walking", "speed_walking", "treadmill_walking", "indoor_walking"],
    },
    "trilha": {
        "name": "Trilha / hiking", "anchor": "hr", "unit": "km", "impact": "medio", "legs": True,
        "envs": {"trilha": ("trilha_hiking", ("hiking", "generic"), "other")},
        "garmin_types": ["hiking", "mountaineering"],
    },
}

GARMIN_TYPE_TO_SPORT = {t: s for s, d in SPORTS.items() for t in d["garmin_types"]}


def sport_env(sport: str, env: str | None) -> tuple[str, str]:
    """(env válido, exercise_id) para a modalidade."""
    envs = SPORTS[sport]["envs"]
    env = env if env in envs else next(iter(envs))
    return env, envs[env][0]


def fit_sport(sport: str, env: str | None) -> tuple[str, str]:
    env, _ = sport_env(sport, env)
    return SPORTS[sport]["envs"][env][1]


def connect_sport(sport: str, env: str | None) -> str:
    env, _ = sport_env(sport, env)
    return SPORTS[sport]["envs"][env][2]


# ------------------------------------------------------------------ utilidades
def parse_duration(v) -> float | None:
    """'25:30' → 1530 s; '1:45:00' → 6300 s; 1530 → 1530. Minutos com 'min' também."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*min", s)
    if m:
        return float(m.group(1)) * 60
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_pace(sec_per_unit: float | None, unit: str = "/km") -> str:
    if not sec_per_unit:
        return "—"
    s = int(round(sec_per_unit))
    return f"{s // 60}:{s % 60:02d}{unit}"


# ----------------------------------------------------------- corrida: VDOT
def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distance_km: float, time_s: float) -> float | None:
    """VDOT de Daniels-Gilbert a partir de uma prova/teste."""
    if not distance_km or not time_s or time_s < 180:
        return None
    t = time_s / 60
    return round(_vo2(distance_km * 1000 / t) / _pct_max(t), 1)


def _speed_for_vo2(vo2: float) -> float:
    """Velocidade (m/min) correspondente a um VO2 (inverso da equação de custo)."""
    a, b, c = 0.000104, 0.182258, -(4.60 + vo2)
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def pace_at(vd: float, frac: float) -> float:
    """Ritmo (s/km) correndo a uma fração do VDOT."""
    return 60_000 / _speed_for_vo2(vd * frac)


def predict_time(vd: float, distance_km: float) -> float:
    """Tempo previsto (s) numa distância para um VDOT (busca binária)."""
    lo, hi = 60.0, 60 * 60 * 12.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if (vdot(distance_km, mid) or 0) > vd:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# Daniels: E 62–70 %, M ~81 %, T ~88 %, I ~97,5 %, R ~105 % do VDOT
RUN_ZONES_FRAC = {1: (0.55, 0.62), 2: (0.62, 0.70), 3: (0.78, 0.83), 4: (0.86, 0.90), 5: (0.95, 1.0), 6: (1.03, 1.07)}
RUN_ZONE_LABEL = {1: "regenerativo", 2: "leve (E)", 3: "maratona (M)", 4: "limiar (T)", 5: "intervalado (I)", 6: "repetição (R)"}


def run_paces(vd: float) -> dict[int, tuple[float, float]]:
    """Faixas de ritmo (s/km, rápido→lento) por zona."""
    return {z: (pace_at(vd, hi), pace_at(vd, lo)) for z, (lo, hi) in RUN_ZONES_FRAC.items()}


# ----------------------------------------------------------- ciclismo: FTP
BIKE_ZONES_PCT = {1: (0.40, 0.55), 2: (0.56, 0.75), 3: (0.76, 0.90), 4: (0.91, 1.05), 5: (1.06, 1.20), 6: (1.21, 1.50)}


def ftp_from_20min(avg_power_20: float) -> float:
    return round(avg_power_20 * 0.95)


def bike_power(ftp: float) -> dict[int, tuple[float, float]]:
    return {z: (round(ftp * lo), round(ftp * hi)) for z, (lo, hi) in BIKE_ZONES_PCT.items()}


# ----------------------------------------------------------- natação: CSS
# offsets em s/100 m em relação ao CSS (positivo = mais lento)
SWIM_ZONES_OFFSET = {1: (12, 20), 2: (6, 12), 3: (2, 6), 4: (-2, 2), 5: (-6, -2), 6: (-12, -6)}


def css_from_tests(t400_s: float, t200_s: float) -> float | None:
    """CSS em s/100 m a partir dos tempos de 400 m e 200 m."""
    if not t400_s or not t200_s or t400_s <= t200_s:
        return None
    speed = 200 / (t400_s - t200_s)  # m/s
    return round(100 / speed, 1)


def swim_paces(css_s100: float) -> dict[int, tuple[float, float]]:
    return {z: (css_s100 + lo, css_s100 + hi) for z, (lo, hi) in SWIM_ZONES_OFFSET.items()}


# ------------------------------------------------------------- remo: 2 km
ROW_ZONES_OFFSET = {1: (20, 28), 2: (12, 20), 3: (7, 12), 4: (3, 7), 5: (-1, 3), 6: (-6, -1)}


def row_split(t2k_s: float) -> float:
    """Split médio (s/500 m) do teste de 2 km."""
    return t2k_s / 4


def row_splits(t2k_s: float) -> dict[int, tuple[float, float]]:
    base = row_split(t2k_s)
    return {z: (base + lo, base + hi) for z, (lo, hi) in ROW_ZONES_OFFSET.items()}


# ---------------------------------------------------- frequência cardíaca
LTHR_ZONES_PCT = {1: (0.70, 0.85), 2: (0.85, 0.89), 3: (0.90, 0.94), 4: (0.95, 0.99), 5: (1.00, 1.06), 6: (1.06, 1.10)}
HRMAX_ZONES_PCT = {1: (0.50, 0.60), 2: (0.60, 0.70), 3: (0.70, 0.80), 4: (0.80, 0.90), 5: (0.90, 1.0), 6: (0.95, 1.0)}


def hr_zones(lthr: float | None = None, hrmax: float | None = None) -> dict[int, tuple[int, int]] | None:
    if lthr:
        return {z: (round(lthr * lo), round(lthr * hi)) for z, (lo, hi) in LTHR_ZONES_PCT.items()}
    if hrmax:
        return {z: (round(hrmax * lo), round(hrmax * hi)) for z, (lo, hi) in HRMAX_ZONES_PCT.items()}
    return None


# ------------------------------------------------------------------ âncoras
def anchors_of(profile: dict) -> dict:
    """Âncoras salvas no perfil (com FC máx. estimada pela idade se faltar)."""
    end = (profile or {}).get("endurance") or {}
    a = dict(end.get("anchors") or {})
    from . import personal
    if personal.rpe_mode(profile or {}):
        # remédio que baixa a FC: zonas por FC enganam — alvo por esforço percebido (ritmo/potência continuam valendo)
        a.pop("lthr", None)
        a.pop("hrmax", None)
        a["rpe_mode"] = True
        return a
    if not a.get("hrmax"):
        age = (profile or {}).get("age")
        a["hrmax"] = (profile or {}).get("hr_max") or (round(208 - 0.7 * age) if age else None)
    return a


def anchor_value(sport: str, anchors: dict):
    kind = SPORTS[sport]["anchor"]
    return {"vdot": anchors.get("run_vdot"), "ftp": anchors.get("ftp_w"), "css": anchors.get("css_s100"),
            "row2k": anchors.get("row_2k_s"), "hr": None}[kind]


def target_for(sport: str, zone: int, anchors: dict) -> dict:
    """Alvo do passo para o relógio. Usa a âncora do esporte; sem ela, FC; sem FC, zona."""
    zone = max(1, min(6, int(zone)))
    kind = SPORTS[sport]["anchor"]
    val = anchor_value(sport, anchors)
    if kind == "vdot" and val:
        fast, slow = run_paces(val)[zone]
        return {"type": "pace", "fast_s_km": round(fast), "slow_s_km": round(slow),
                "label": f"{fmt_pace(fast)}–{fmt_pace(slow)}"}
    if kind == "ftp" and val:
        lo, hi = bike_power(val)[zone]
        return {"type": "power", "low_w": lo, "high_w": hi, "label": f"{lo}–{hi} W"}
    if kind == "css" and val:
        fast, slow = swim_paces(val)[zone]
        return {"type": "swim_pace", "fast_s_100": round(fast), "slow_s_100": round(slow),
                "css_offset": round((fast + slow) / 2 - val), "label": f"{fmt_pace(fast, '/100m')}–{fmt_pace(slow, '/100m')}"}
    if kind == "row2k" and val:
        fast, slow = row_splits(val)[zone]
        return {"type": "split", "fast_s_500": round(fast), "slow_s_500": round(slow),
                "label": f"{fmt_pace(fast, '/500m')}–{fmt_pace(slow, '/500m')}"}
    if anchors.get("rpe_mode"):
        from .personal import RPE_LABEL
        return {"type": "rpe", "zone": min(zone, 6), "label": RPE_LABEL.get(zone, f"Z{zone}")}
    hz = hr_zones(anchors.get("lthr"), anchors.get("hrmax"))
    if hz:
        lo, hi = hz[min(zone, 5)]
        return {"type": "hr", "low_bpm": lo, "high_bpm": hi, "zone": min(zone, 5), "label": f"{lo}–{hi} bpm"}
    return {"type": "hr_zone", "zone": min(zone, 5), "label": f"Z{min(zone, 5)}"}


def zones_table(sport: str, anchors: dict) -> list[dict]:
    return [{"zone": z, "name": ZONE_NAMES[z], **target_for(sport, z, anchors)} for z in range(1, 6 if sport != "corrida" else 7)]


# ------------------------------------------------- velocidade média planejada
def speed_mps(sport: str, zone: int, anchors: dict, env: str | None = None) -> float:
    """Velocidade média esperada numa zona (para estimar duração de passos por distância)."""
    t = target_for(sport, zone, anchors)
    if t["type"] == "pace":
        return 1000 / ((t["fast_s_km"] + t["slow_s_km"]) / 2)
    if t["type"] == "swim_pace":
        return 100 / ((t["fast_s_100"] + t["slow_s_100"]) / 2)
    if t["type"] == "split":
        return 500 / ((t["fast_s_500"] + t["slow_s_500"]) / 2)
    defaults = {  # sem âncora: velocidades típicas de recreativo por zona (m/s)
        "corrida": {1: 2.4, 2: 2.7, 3: 3.0, 4: 3.3, 5: 3.6, 6: 4.0},
        "ciclismo": {1: 5.5, 2: 7.0, 3: 8.0, 4: 8.8, 5: 9.5, 6: 10.0},
        "natacao": {1: 0.75, 2: 0.85, 3: 0.95, 4: 1.0, 5: 1.08, 6: 1.15},
        "remo": {1: 3.4, 2: 3.7, 3: 3.9, 4: 4.1, 5: 4.3, 6: 4.5},
        "caminhada": {1: 1.2, 2: 1.4, 3: 1.6, 4: 1.75, 5: 1.85, 6: 1.9},
        "trilha": {1: 0.9, 2: 1.1, 3: 1.25, 4: 1.35, 5: 1.4, 6: 1.45},
    }
    if sport == "ciclismo" and env in ("rolo", "spinning"):
        return defaults["ciclismo"][zone] * 0.9
    return defaults[sport][max(1, min(6, zone))]
