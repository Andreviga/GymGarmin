"""Idade e sexo na montagem do treino: o que muda de verdade (e o que não muda).

O que a pesquisa sustenta e o app aplica:

Adolescentes (até 17 anos) — treino de força é seguro e recomendado com técnica e progressão (posição da NSCA,
Faigenbaum et al., 2009; consenso internacional, Lloyd et al., 2014). O app não prescreve séries de 1–5 repetições nem
falha: repetições a partir de 8 (6 a partir de 16 anos para quem já treina há tempo), RPE até 8, sem drop set e sem
"top set" pesado.

40–54 anos — a recuperação entre sessões pesadas fica um pouco mais lenta e tendões/articulações pedem aquecimento
mais longo: +2 min de aquecimento. Volume e intensidade seguem o nível.

55–69 anos — força e massa continuam respondendo bem (posição da NSCA para idosos, Fragala et al., 2019), mas a
potência (força rápida) cai antes da força e é a que mais protege contra quedas: o principal de cada dia vem com
"suba rápido, desça em 2–3 s". Aquecimento +3 min, um exercício de equilíbrio no preparo, até 4 dias de força,
repetições a partir de 6, RPE até 8 e, para quem ainda não é avançado, máquinas antes de pesos livres (mais estáveis).

70 anos ou mais — 2 a 3 dias por semana já dão quase todo o ganho; 8–15 repetições, RPE até 7,5, no máximo 3 séries
no principal, equilíbrio no preparo, potência nos principais, máquinas primeiro.

Mulheres — o ganho relativo de massa e força é igual ao dos homens (Roberts et al., 2020), então o treino é o mesmo
em volume e intensidade. Duas diferenças entram no plano:
- mulheres se cansam menos numa série no mesmo percentual de carga e se recuperam mais rápido entre séries (Hunter, 2014):
  o descanso dos acessórios e secundários fica ~15% mais curto (os principais ficam como estão);
- o risco de lesão de joelho (LCA) é 2–8× maior: todo dia de perna inclui glúteo médio (abdução) e posterior de coxa,
  que protegem o joelho (Hewett et al.).
Mulheres a partir de 45 anos (perimenopausa e pós-menopausa): a perda óssea acelera, e carga alta nos compostos + saltos
leves aumentam a densidade óssea com segurança (estudo LIFTMOR, Watson et al., 2018): os compostos não vão acima de
8–10 repetições (a carga precisa ser alta) e, sem limitação de joelho/tornozelo/lombar, entra um salto leve no preparo
dos dias de perna.

O que NÃO muda: o treino não segue o ciclo menstrual (o efeito da fase do ciclo no desempenho é pequeno e varia de
pessoa para pessoa; McNulty et al., 2020; Colenso-Semple et al., 2023). Nos dias de cólica ou cansaço, a "Outra opção →
estou cansado" ajusta só aquele dia. Homens: nenhuma regra própria além das de idade.
"""
from __future__ import annotations

BALANCE_DRILL = "Single_Leg_Balance"
BONE_JUMP = "Freehand_Jump_Squat"
KNEE_GUARD = "Thigh_Abductor"   # glúteo médio: protege o joelho (valgo)
NO_JUMP_LIMS = {"joelho", "tornozelo", "lombar", "quadril", "gestante", "cardiaco", "hipertensao"}


def sex_of(p: dict) -> str | None:
    s = str((p or {}).get("sex") or "").strip().upper()
    return "F" if s.startswith("F") else "M" if s.startswith("M") else None


def age_of(p: dict) -> int | None:
    try:
        a = int((p or {}).get("age"))
    except (TypeError, ValueError):
        return None
    return a if 8 <= a <= 110 else None


def stage(p: dict, tier_key: str | None = None) -> dict:
    """Ajustes do treino para a idade e o sexo da pessoa (vazio = nada muda)."""
    age, sex = age_of(p), sex_of(p)
    lims = {lim.get("key") for lim in (p or {}).get("limitations") or [] if lim.get("severity") in ("evitar", "cuidado")}
    adv = tier_key in ("avancado", "elite")
    a = {"age": age, "sex": sex, "group": None, "max_strength_days": None, "reps_min": None, "rpe_cap": None,
         "main_sets_cap": None, "acc_sets_cap": None, "warm_extra": 0, "power": False, "balance": False,
         "machines_first": False, "no_intensifiers": False, "rest_mult_acc": 1.0, "knee_guard": False,
         "bone": False, "bone_jump": False, "reps_max_compound": None, "notes": []}
    if age is not None:
        if age < 18:
            a.update(group="adolescente", reps_min=6 if (age >= 16 and adv) else 8, rpe_cap=8, no_intensifiers=True)
            a["notes"].append(f"{age} anos: técnica antes da carga — repetições a partir de {a['reps_min']}, sem falha e sem "
                              "séries máximas (posição da NSCA para jovens). Treinar com supervisão.")
        elif age >= 70:
            a.update(group="70+", max_strength_days=3, reps_min=8, rpe_cap=7.5, main_sets_cap=3, acc_sets_cap=2, warm_extra=3,
                     power=True, balance=True, machines_first=True, no_intensifiers=True)
            a["notes"].append(f"{age} anos: 2–3 dias de força já dão quase todo o ganho; 8–15 repetições, RPE até 7,5, "
                              "máquinas primeiro, equilíbrio no aquecimento e subida rápida nos principais (potência protege "
                              "contra quedas — NSCA, Fragala et al., 2019).")
        elif age >= 55:
            a.update(group="55+", max_strength_days=4, reps_min=6, rpe_cap=8, warm_extra=3, power=True, balance=True,
                     machines_first=not adv, main_sets_cap=None if adv else 3)
            a["notes"].append(f"{age} anos: aquecimento mais longo, um exercício de equilíbrio, subida rápida e descida "
                              "controlada nos principais (a potência cai antes da força) e RPE até 8. Força e massa "
                              "continuam respondendo bem nessa idade.")
        elif age >= 40:
            a.update(group="40+", warm_extra=2)
            a["notes"].append(f"{age} anos: +2 min de aquecimento (tendões e articulações pedem mais tempo); o resto segue o seu nível.")
    if sex == "F":
        a.update(rest_mult_acc=0.85, knee_guard=True)
        a["notes"].append("Mulher: mesmo volume e intensidade (o ganho relativo é igual ao dos homens); descanso ~15% mais "
                          "curto nos acessórios (menos fadiga entre séries) e glúteo médio + posterior em todo dia de perna "
                          "para proteger o joelho. O treino não muda pelo ciclo menstrual: nos dias de cólica ou cansaço, "
                          "use “Outra opção → estou cansado”.")
        if age is not None and age >= 45 and a["group"] != "adolescente":
            a["bone"] = True
            a["reps_max_compound"] = 12 if age >= 70 else 10
            a["bone_jump"] = age < 70 and not (lims & NO_JUMP_LIMS)
            a["notes"].append("A partir dos 45: carga alta nos compostos (até "
                              f"{a['reps_max_compound']} repetições){' e um salto leve no preparo dos dias de perna' if a['bone_jump'] else ''}, "
                              "que fortalecem os ossos com segurança (estudo LIFTMOR).")
    return a


def _bounds(reps: str | None) -> tuple[int, int] | None:
    try:
        lo, hi = str(reps).split("-")
        return int(lo), int(hi)
    except (ValueError, AttributeError):
        try:
            v = int(reps)
            return v, v
        except (TypeError, ValueError):
            return None


def clamp_reps(reps, adj: dict, compound: bool):
    """Faixa de repetições dentro do que a idade/sexo pede (mantém a largura da faixa quando sobe)."""
    b = _bounds(reps)
    if not b:
        return reps
    lo, hi = b
    rmin = adj.get("reps_min")
    if rmin and lo < rmin:
        lo, hi = rmin, max(hi, rmin + 4)
    rmax = adj.get("reps_max_compound") if compound else None
    if rmax and hi > rmax and lo < rmax:
        hi = rmax
    elif rmax and lo >= rmax:
        lo, hi = max(6, rmax - 4), rmax
    return f"{lo}-{hi}" if lo != hi else str(lo)


def apply_item(it: dict, adj: dict, compound: bool, rest_floor: int = 45, rest: bool = True) -> dict:
    """Ajusta um exercício principal do treino (reps, RPE, séries, descanso, nota de potência)."""
    if not adj or (it.get("phase") or "principal") != "principal" or not it.get("sets"):
        return it
    role = it.get("role")
    if it.get("reps"):
        it["reps"] = clamp_reps(it["reps"], adj, compound)
    if adj.get("rpe_cap") and it.get("rpe_target"):
        it["rpe_target"] = min(float(it["rpe_target"]), adj["rpe_cap"])
    cap = adj.get("main_sets_cap") if role == "main" else adj.get("acc_sets_cap") if role in ("acc", "sec") else None
    if cap and int(it["sets"]) > cap:
        it["sets"] = cap
    if rest and adj.get("rest_mult_acc", 1.0) != 1.0 and role in ("acc", "sec") and (it.get("rest_s") or 0) > rest_floor \
            and not it.get("superset"):
        it["rest_s"] = int(max(rest_floor, round(it["rest_s"] * adj["rest_mult_acc"] / 15) * 15))
    if adj.get("no_intensifiers") and it.get("notes"):
        parts = [x for x in str(it["notes"]).split(" · ") if not x.startswith(("top set", "última série: drop set"))]
        it["notes"] = " · ".join(parts) or None
    if adj.get("power") and role == "main" and not it.get("duration_s"):
        note = "suba rápido (com controle) e desça em 2–3 s: potência"
        if note not in (it.get("notes") or ""):
            it["notes"] = ((it.get("notes") or "") + " · " + note).strip(" ·")
    return it
