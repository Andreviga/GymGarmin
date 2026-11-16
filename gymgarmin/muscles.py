"""Músculos, nomes em português e referências fisiológicas de volume e recuperação.

As faixas de volume semanal (séries "duras", próximas da falha, por músculo) são
referências práticas para hipertrofia em praticantes intermediários, inspiradas
nos "volume landmarks" (MEV/MAV/MRV) da literatura de treinamento e em revisões
dose-resposta (ex.: Schoenfeld et al. 2017; Pelland et al. 2024). NÃO são
prescrição médica: servem para comparar músculos entre si e apontar excessos ou
esquecimentos. Ajuste no perfil (multiplicador de volume) conforme sua resposta.

  mev  – volume mínimo efetivo (abaixo disso o músculo está "esquecido")
  mav  – faixa ótima (adaptativa)
  mrv  – volume máximo recuperável (acima disso o risco de excesso cresce)
"""

from __future__ import annotations

MUSCLES: dict[str, dict] = {
    "chest":       {"pt": "Peitoral",              "region": "superior", "push_pull": "push", "chain": "anterior", "size": "grande",  "mev": 8, "mav": (12, 20), "mrv": 22, "recovery_h": 72},
    "shoulders":   {"pt": "Ombros (deltoides)",    "region": "superior", "push_pull": "push", "chain": "anterior", "size": "medio",   "mev": 6, "mav": (12, 20), "mrv": 26, "recovery_h": 48},
    "triceps":     {"pt": "Tríceps",               "region": "superior", "push_pull": "push", "chain": "posterior", "size": "pequeno", "mev": 6, "mav": (10, 14), "mrv": 18, "recovery_h": 48},
    "lats":        {"pt": "Dorsais (latíssimo)",   "region": "superior", "push_pull": "pull", "chain": "posterior", "size": "grande",  "mev": 8, "mav": (12, 20), "mrv": 25, "recovery_h": 72},
    "middle back": {"pt": "Meio das costas (romboides)", "region": "superior", "push_pull": "pull", "chain": "posterior", "size": "medio", "mev": 6, "mav": (10, 18), "mrv": 22, "recovery_h": 60},
    "traps":       {"pt": "Trapézio",              "region": "superior", "push_pull": "pull", "chain": "posterior", "size": "medio",   "mev": 2, "mav": (8, 16), "mrv": 26, "recovery_h": 48},
    "biceps":      {"pt": "Bíceps",                "region": "superior", "push_pull": "pull", "chain": "anterior", "size": "pequeno", "mev": 6, "mav": (12, 20), "mrv": 24, "recovery_h": 48},
    "forearms":    {"pt": "Antebraços",            "region": "superior", "push_pull": "pull", "chain": "anterior", "size": "pequeno", "mev": 2, "mav": (6, 14),  "mrv": 20, "recovery_h": 36},
    "neck":        {"pt": "Pescoço",               "region": "superior", "push_pull": None,   "chain": "posterior", "size": "pequeno", "mev": 0, "mav": (4, 10),  "mrv": 14, "recovery_h": 48},
    "abdominals":  {"pt": "Abdômen",               "region": "core",     "push_pull": None,   "chain": "anterior", "size": "medio",   "mev": 4, "mav": (10, 20), "mrv": 25, "recovery_h": 36},
    "lower back":  {"pt": "Lombar (eretores)",     "region": "core",     "push_pull": None,   "chain": "posterior", "size": "medio",   "mev": 2, "mav": (4, 10),  "mrv": 14, "recovery_h": 72},
    "quadriceps":  {"pt": "Quadríceps",            "region": "inferior", "push_pull": None,   "chain": "anterior", "size": "grande",  "mev": 8, "mav": (12, 18), "mrv": 20, "recovery_h": 72},
    "hamstrings":  {"pt": "Posteriores de coxa",   "region": "inferior", "push_pull": None,   "chain": "posterior", "size": "grande",  "mev": 6, "mav": (10, 16), "mrv": 20, "recovery_h": 72},
    "glutes":      {"pt": "Glúteos",               "region": "inferior", "push_pull": None,   "chain": "posterior", "size": "grande",  "mev": 4, "mav": (8, 16),  "mrv": 20, "recovery_h": 72},
    "calves":      {"pt": "Panturrilhas",          "region": "inferior", "push_pull": None,   "chain": "posterior", "size": "pequeno", "mev": 6, "mav": (10, 16), "mrv": 20, "recovery_h": 36},
    "adductors":   {"pt": "Adutores",              "region": "inferior", "push_pull": None,   "chain": "anterior", "size": "medio",   "mev": 2, "mav": (6, 12),  "mrv": 16, "recovery_h": 48},
    "abductors":   {"pt": "Abdutores (glúteo médio)", "region": "inferior", "push_pull": None, "chain": "posterior", "size": "medio",  "mev": 2, "mav": (6, 12),  "mrv": 16, "recovery_h": 48},
}

# Peso de contribuição para o volume: músculo primário conta 1 série, secundário 0,5
# (contagem fracionada, prática usada em meta-análises de volume).
PRIMARY_WEIGHT = 1.0
SECONDARY_WEIGHT = 0.5

EQUIPMENT_PT = {
    "barbell": "Barra", "dumbbell": "Halteres", "cable": "Polia/Cabo", "machine": "Máquina",
    "body only": "Peso corporal", "kettlebells": "Kettlebell", "bands": "Elástico",
    "medicine ball": "Medicine ball", "exercise ball": "Bola suíça", "foam roll": "Rolo de liberação",
    "e-z curl bar": "Barra W", "other": "Outro", None: "—", "cardio machine": "Aparelho de cardio",
}

CATEGORY_PT = {
    "strength": "Força/Musculação", "stretching": "Alongamento", "plyometrics": "Pliometria",
    "powerlifting": "Powerlifting", "olympic weightlifting": "Levantamento olímpico",
    "strongman": "Strongman", "cardio": "Cardio",
}

LEVEL_PT = {"beginner": "Iniciante", "intermediate": "Intermediário", "expert": "Avançado"}
FORCE_PT = {"push": "Empurrar", "pull": "Puxar", "static": "Isométrico", None: "—"}
MECHANIC_PT = {"compound": "Multiarticular", "isolation": "Isolado", None: "—"}

# Multiplicador de volume por nível de experiência (iniciantes respondem com menos)
LEVEL_VOLUME_FACTOR = {"iniciante": 0.7, "intermediario": 1.0, "avancado": 1.15}

# Zonas de frequência cardíaca (% da FC máx.)
HR_ZONES = {
    1: {"pt": "Z1 – Recuperação", "low": 0.50, "high": 0.60, "intensity": "leve"},
    2: {"pt": "Z2 – Base aeróbia", "low": 0.60, "high": 0.70, "intensity": "moderado"},
    3: {"pt": "Z3 – Tempo", "low": 0.70, "high": 0.80, "intensity": "moderado"},
    4: {"pt": "Z4 – Limiar", "low": 0.80, "high": 0.90, "intensity": "vigoroso"},
    5: {"pt": "Z5 – VO2máx", "low": 0.90, "high": 1.00, "intensity": "vigoroso"},
}


def muscle_pt(key: str) -> str:
    return MUSCLES.get(key, {}).get("pt", key)


def hr_max(age: int | None, measured: int | None = None) -> int | None:
    """FC máxima: medida, se houver; senão Tanaka (208 − 0,7 × idade)."""
    if measured:
        return int(measured)
    if not age:
        return None
    return round(208 - 0.7 * age)


def zone_bpm(zone: int, fc_max: int | None) -> tuple[int, int] | None:
    if not fc_max or zone not in HR_ZONES:
        return None
    z = HR_ZONES[zone]
    return round(z["low"] * fc_max), round(z["high"] * fc_max)
