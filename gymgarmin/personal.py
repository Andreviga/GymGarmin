"""Perfil detalhado: saúde, fase de vida, preferências, corpo, corrida e rotina → ajustes no treino.

Tudo é opcional e fica em profile["personal"]. Cada resposta vira regras concretas (com a fonte ao lado). Quando a
resposta pede avaliação profissional, o app avisa e trava a parte arriscada — não substitui médico/fisioterapeuta.

Fontes principais (pesquisa feita em nov/2026):
- Diabetes: Kanaley et al., ACSM consensus, MSSE 2022; ADA Standards of Care 2025; Diretriz SBD 2024.
- Obesidade: Oppert et al., EASO, Obes Rev 2021; GLP-1 e massa magra (grupos de trabalho 2024–25).
- Osteoporose: Royal Osteoporosis Society "Strong, steady and straight" (BJSM 2022); LIFTMOR (Watson 2018).
- Artrose/artrite: OARSI 2019, ACR 2019, EULAR 2018; dor monitorada (Silbernagel/Thomeé).
- Remédios que mudam a FC (betabloqueador etc.): ACSM GETP 11ª ed. → intensidade por esforço percebido.
- Asma: ATS (Parsons 2013). Pós-parto: Goom, Donnelly & Brockwell 2019; Christopher et al. BJSM 2024; ACOG 804.
- Menopausa: Berin 2019; metanálise 2024 (fogachos). RED-S: Mountjoy, IOC 2023.
- Doença recente / pós-COVID: World Physiotherapy 2021; Elliott BJSM 2020. Liberação médica: Riebe, ACSM, MSSE 2015.
- Volume por músculo e proximidade da falha: Pelland 2026; Robinson 2024; Refalo 2024. Déficit calórico: Murphy &
  Koehler 2022; proteína: Morton 2018. Preferência de exercícios e adesão: ACSM 2026; Tavares 2021.
- Mobilidade e escolha de exercício: dorsiflexão × profundidade do agachamento; Maeo 2021/2023 (músculo alongado).
- Lesões de corrida: JOSPT Aquiles 2024 e calcanhar 2023; Hoenig 2023 e George 2024 (fratura por estresse);
  Heiderscheit 2011 / Lenhart 2014 (cadência +5–10%); Bertelsen 2017; Nielsen 2014 (iniciante com IMC > 30).
- Distribuição de intensidade: Rosenblat 2025 (piramidal para recreativos/50+). Combustível: Thomas 2016 (ACSM/AND).
- Trabalho físico: Coenen 2018 (paradoxo da atividade ocupacional). Estresse: Stults-Kolehmainen 2014.
- Adesão: Sperandei 2016 (63% desistem em 3 meses), Kaushal & Rhodes 2015, Milkman 2021, Spiering 2021 (dose mínima).
"""
from __future__ import annotations

from . import catalog

# --------------------------------------------------------------- questionário ---
# type: multi (lista), single (um valor), bool. show_if: {"campo": valores} (perfil: sex, running) para mostrar
QUESTIONS = [
    {"section": "Saúde", "intro": "Respostas ficam só no seu perfil e ajustam o treino. Na dúvida, marque o que algum médico já disse.",
     "items": [
        {"id": "conditions", "type": "multi", "label": "Algum médico já disse que você tem:", "options": [
            ["pre_diabetes", "Pré-diabetes"], ["diabetes", "Diabetes (sem insulina)"], ["diabetes_insulina", "Diabetes com insulina ou glibenclamida/gliclazida"],
            ["osteopenia", "Osteopenia"], ["osteoporose", "Osteoporose"], ["fratura_vertebra", "Já fraturei vértebra ou várias fraturas leves"],
            ["artrose", "Artrose no joelho ou quadril"], ["artrite", "Artrite reumatoide / outra artrite"],
            ["asma", "Asma ou chiado no esforço"], ["renal", "Doença renal"], ["cancer", "Câncer (tratando ou terminou há < 2 anos)"],
            ["hipermobilidade", "Articulações muito flexíveis (já 'saíram do lugar')"]]},
        {"id": "meds", "type": "multi", "label": "Usa algum destes remédios?", "options": [
            ["fc", "Betabloqueador ou outro para coração/pressão que baixa a FC (atenolol, propranolol, carvedilol, metoprolol, bisoprolol, verapamil, diltiazem)"],
            ["glp1", "Injeção para emagrecer (Ozempic, Wegovy, Mounjaro)"], ["anticoagulante", "Anticoagulante (varfarina, rivaroxabana, apixabana)"],
            ["corticoide", "Corticoide todo dia"], ["diuretico", "Diurético ou dapagliflozina/empagliflozina"]]},
        {"id": "chest", "type": "single", "label": "Ao se esforçar, já sentiu dor no peito, desmaio, palpitação forte ou falta de ar fora do normal?",
         "options": [["nao", "Não"], ["sim", "Sim"], ["liberado", "Sim, mas fui avaliado e liberado"]]},
        {"id": "smoker", "type": "single", "label": "Fuma ou usa vape?", "options": [["nao", "Não"], ["sim", "Sim"]]},
    ]},
    {"section": "Fase de vida", "show_if": {"sex": ["F"]}, "items": [
        {"id": "phase", "type": "single", "label": "Momento atual:", "options": [
            ["nenhuma", "Nenhum destes"], ["pos_parto", "Tive bebê nos últimos 12 meses"], ["menopausa", "Perimenopausa ou menopausa"]]},
        {"id": "postpartum_weeks", "type": "number", "label": "Há quantas semanas foi o parto?", "show_if": {"phase": ["pos_parto"]}, "min": 0, "max": 60},
        {"id": "cesarea", "type": "single", "label": "Foi cesárea?", "show_if": {"phase": ["pos_parto"]}, "options": [["nao", "Não"], ["sim", "Sim"]]},
        {"id": "pelvic", "type": "single", "label": "Perde urina, sente peso na vagina ou a barriga 'estufa' no meio ao fazer força?",
         "show_if": {"phase": ["pos_parto", "menopausa"]}, "options": [["nao", "Não"], ["sim", "Sim"]]},
        {"id": "amenorrhea", "type": "single", "label": "Sua menstruação falhou 3 meses ou mais (sem gravidez, sem ser menopausa)?",
         "show_if": {"phase": ["nenhuma", None]}, "options": [["nao", "Não"], ["sim", "Sim"], ["anticoncepcional", "Uso anticoncepcional contínuo"]]},
    ]},
    {"section": "Musculação", "items": [
        {"id": "diet", "type": "single", "label": "Está fazendo dieta para perder peso agora?", "options": [
            ["nao", "Não"], ["leve", "Sim, déficit leve"], ["agressiva", "Sim, déficit grande / perdendo rápido"]]},
        {"id": "protein", "type": "single", "label": "Quantas refeições por dia têm uma porção de proteína (palma da mão de carne, frango, peixe, ovos, whey)?",
         "options": [["1", "0–1"], ["2", "2"], ["3", "3"], ["4", "4 ou mais"]]},
        {"id": "ankle", "type": "single", "label": "Teste do tornozelo: pé a 10 cm da parede, calcanhar no chão — o joelho encosta na parede?",
         "options": [["sim", "Sim"], ["nao", "Não"], ["nao_sei", "Não testei"]]},
        {"id": "overhead", "type": "single", "label": "Encostado na parede, leva os braços esticados até encostar acima da cabeça sem arquear a lombar?",
         "options": [["sim", "Sim"], ["nao", "Não"], ["doi", "Dói"], ["nao_sei", "Não testei"]]},
        {"id": "past_injury", "type": "multi", "label": "Lesão séria nos últimos 2 anos (cirurgia, ruptura, luxação, hérnia), já liberada e sem dor:",
         "options": [["ombro", "Ombro"], ["joelho", "Joelho"], ["lombar", "Coluna lombar"], ["quadril", "Quadril"],
                     ["tornozelo", "Tornozelo"], ["cotovelo", "Cotovelo"], ["punho", "Punho"]]},
        {"id": "dislikes", "type": "exercises", "label": "Exercícios que você não gosta ou não quer fazer (o app troca por outro do mesmo movimento):"},
        {"id": "variety", "type": "single", "label": "Prefere:", "options": [
            ["meio", "Meio-termo"], ["rotina", "Rotina fixa (mesmos exercícios)"], ["variar", "Variar sempre"]]},
    ]},
    {"section": "Corrida", "show_if": {"running": [True]}, "items": [
        {"id": "run_injuries", "type": "multi", "label": "Teve alguma destas lesões nos últimos 12 meses?", "options": [
            ["aquiles", "Tendão de Aquiles"], ["fascite", "Fascite plantar / dor no calcanhar"], ["canelite", "Canelite"],
            ["fratura_estresse", "Fratura por estresse"], ["joelho_frente", "Dor na frente do joelho"], ["banda_it", "Dor na lateral do joelho (banda iliotibial)"]]},
        {"id": "run_pain", "type": "single", "label": "Ainda sente dor ao correr?", "show_if": {"run_injuries": "any"}, "options": [
            ["nao", "Não"], ["leve", "Leve (até 3 de 10)"], ["moderada", "Moderada (4–6)"], ["forte", "Forte (7+)"]]},
        {"id": "shoes", "type": "single", "label": "Tênis de corrida:", "options": [
            ["amortecido", "Amortecido comum"], ["carbono", "Placa de carbono"], ["minimalista", "Minimalista"], ["nao_sei", "Não sei"]]},
        {"id": "run_time", "type": "single", "label": "Horário em que costuma correr:", "options": [
            ["cedo", "Antes das 7h"], ["manha", "7–10h"], ["meio_dia", "10–16h"], ["fim_tarde", "16–19h"], ["noite", "Depois das 19h"]]},
    ]},
    {"section": "Rotina", "items": [
        {"id": "job", "type": "single", "label": "Seu trabalho é:", "options": [
            ["sentado", "Sentado a maior parte"], ["em_pe", "Em pé a maior parte"], ["fisico", "Carrega peso / esforço físico"], ["turnos", "Turnos ou noturno"]]},
        {"id": "stress", "type": "single", "label": "Nas últimas 2 semanas, quanto se sentiu sobrecarregado(a)?", "options": [
            ["nada", "Nada"], ["pouco", "Pouco"], ["moderado", "Moderado"], ["muito", "Muito"]]},
        {"id": "caregiver", "type": "single", "label": "Cuida de criança pequena ou de familiar?", "options": [["nao", "Não"], ["sim", "Sim"]]},
    ]},
]

FLEXION_IDS = {"Crunches", "Sit-Up", "Cable_Crunch", "Ab_Crunch_Machine", "AbCoaster_Machine", "Reverse_Crunch",
               "Decline_Crunch", "Russian_Twist", "Toe_Touchers", "Jackknife_Sit-Up", "Hanging_Leg_Raise",
               "Captains_Chair_Knee_Raise", "RotaryTorso_Machine", "Air_Bike", "Cross-Body_Crunch", "Oblique_Crunches",
               "Butt-Ups", "Weighted_Crunches"}
JUMP_IDS = {"Freehand_Jump_Squat", "Box_Jump_Multiple_Response", "Burpee_Broad_Jump", "Rocket_Jump", "Knee_Tuck_Jump",
            "Split_Jump", "Star_Jump", "Medicine_Ball_Chest_Pass"}
OVERHEAD_BARBELL = {"Standing_Military_Press", "Seated_Barbell_Military_Press", "Barbell_Shoulder_Press",
                    "Standing_Barbell_Press_Behind_Neck", "Seated_Barbell_Press_Behind_Neck"}
DEEP_BARBELL_SQUAT = {"Barbell_Squat", "Front_Barbell_Squat"}
CORE_FLEX_PATTERN = "core"

RPE_LABEL = {1: "esforço 1–2 de 10 (bem leve)", 2: "esforço 3–4 de 10 (conversa fácil)", 3: "esforço 5–6 de 10 (frases curtas)",
             4: "esforço 7 de 10 (poucas palavras)", 5: "esforço 8–9 de 10 (forte)", 6: "esforço 9–10 de 10 (máximo)"}


def answers(p: dict) -> dict:
    a = (p or {}).get("personal")
    return a if isinstance(a, dict) else {}


def bmi(p: dict) -> float | None:
    try:
        w, h = float(p.get("weight_kg")), float(p.get("height_cm")) / 100
        return round(w / (h * h), 1) if w > 0 and h > 0.5 else None
    except (TypeError, ValueError):
        return None


def rpe_mode(p: dict) -> bool:
    return "fc" in (answers(p).get("meds") or [])


def derive(p: dict) -> dict:
    """Todas as regras que as respostas (e o IMC) disparam. Vazio = nada muda."""
    a = answers(p)
    cond = set(a.get("conditions") or [])
    meds = set(a.get("meds") or [])
    sex = str((p or {}).get("sex") or "").upper()[:1]
    b = bmi(p)
    out = {"avoid": set(), "demote": {}, "prefer": {}, "rpe_cap": None, "reps_min": None, "no_jumps": False,
           "machines_first": False, "warm_extra": 0, "rir_plus": 0, "sets_minus": 0, "lower_sets_minus": 0,
           "extra_lims": [], "notes": [], "session_notes": [], "stop_signs": False, "clearance": False, "hold_progress": False,
           "no_core_flexion": False, "rpe_mode": "fc" in meds, "bone_impact": False, "rest_day": False, "variety": a.get("variety") or "meio",
           # resistência
           "end_rate": None, "spike": None, "force_run_walk": False, "no_hills_speed": False, "pyramidal": False,
           "run_notes": [], "no_running": False, "low_impact_cardio": False, "zone_cap": None}

    # ---- liberação médica e sinais de alerta (ACSM 2015)
    if a.get("chest") == "sim":
        out.update(clearance=True, stop_signs=True, rpe_cap=6.0, zone_cap=3)
        out["notes"].append("Você marcou dor no peito, desmaio ou palpitação ao se esforçar: até ser avaliado por um médico, o "
                            "treino fica em intensidade moderada (esforço até 6 de 10, sem tiros). Depois da avaliação, marque "
                            "“fui avaliado e liberado”.")
    if cond & {"diabetes_insulina", "renal", "cancer"} or meds & {"fc", "anticoagulante"} or a.get("chest") == "liberado":
        out["stop_signs"] = True

    # ---- diabetes
    if cond & {"pre_diabetes", "diabetes", "diabetes_insulina"}:
        out["notes"].append("Diabetes/pré-diabetes: o melhor horário é 30–90 min depois de uma refeição; uma caminhada de 10–15 min "
                            "depois das refeições baixa o pico de glicose. Não fique mais de 2 dias seguidos sem se mexer.")
    if "diabetes_insulina" in cond:
        out["session_notes"].append("Insulina/glibenclamida: meça a glicose antes. Abaixo de 100 mg/dL coma 15–30 g de carboidrato "
                                    "antes; acima de 250 com cetona (ou 300 com sintomas), não treine forte. Leve 15 g de "
                                    "carboidrato rápido; a hipoglicemia pode vir até 12–24 h depois.")
    # ---- obesidade e GLP-1
    if b and b >= 30:
        out["no_jumps"] = True
        out["avoid"] |= {"Burpee_Broad_Jump", "Burpee", "Mountain_Climbers"}
        out["machines_first"] = True
        out["notes"].append(f"IMC {b:g}: sem saltos nem burpee, máquinas e apoio primeiro (o peso do corpo pesa nas articulações); "
                            "cardio de baixo impacto vale tanto quanto correr.")
        out["force_run_walk"] = True
        out["end_rate"] = 1.05
        if b >= 35:
            out["low_impact_cardio"] = True
            out["run_notes"].append("IMC acima de 35: comece pela caminhada inclinada, bike ou elíptico; a corrida entra como "
                                    "corre-caminha, com dias de bike entre eles.")
    if "glp1" in meds:
        out["notes"].append("Remédio para emagrecer (GLP-1): cerca de 1/4 do peso perdido é músculo — a musculação 2+ vezes por "
                            "semana é obrigatória, e a proteína deve ficar em 1,2–1,6 g/kg por dia. Evite HIIT em jejum nos dias de enjoo.")
    # ---- osso
    if cond & {"osteopenia", "osteoporose", "fratura_vertebra"} or "corticoide" in meds:
        out["no_core_flexion"] = True
        out["avoid"] |= FLEXION_IDS
        if "fratura_vertebra" in cond:
            out.update(no_jumps=True)
            out["reps_min"] = 8
            out["notes"].append("Fratura de vértebra: carga moderada, coluna neutra, sem saltos e sem abdominal de flexão "
                                "(prancha, dead bug e extensão de costas no lugar).")
        else:
            out["bone_impact"] = "osteoporose" not in cond or True
            out["notes"].append("Osso (osteopenia/osteoporose/corticoide): força progressiva até carga alta, impacto leve e "
                                "extensão de costas; sem abdominal de flexão com carga (crunch, abdominal supra, rotação de tronco).")
    # ---- artrose / artrite / hipermobilidade
    if "artrose" in cond:
        out["no_jumps"] = True
        out["machines_first"] = True
        out["low_impact_cardio"] = True
        out["session_notes"].append("Artrose: dor de até 5 de 10 durante o treino é aceitável se voltar ao normal na manhã seguinte; "
                                    "se não voltar, tire 20–30% da carga na próxima vez.")
    if "artrite" in cond:
        out["machines_first"] = True
        out["session_notes"].append("Artrite: em dia de crise (articulação quente e inchada), só mobilidade e isometria — use “Outra "
                                    "opção → dor”. Treine depois que a rigidez da manhã passar.")
        out["avoid"] |= {"Barbell_Shrug", "Farmers_Walk"}
    if "hipermobilidade" in cond:
        out["machines_first"] = True
        out["rpe_cap"] = min(out["rpe_cap"] or 10, 8)
        out["session_notes"].append("Hipermobilidade: descida lenta (3 s), amplitude controlada, sem alongamento forçado no fim do "
                                    "movimento.")
    # ---- asma
    if "asma" in cond:
        out["warm_extra"] = max(out["warm_extra"], 5)
        out["session_notes"].append("Asma: aquecimento de 10–15 min com pequenos tiros (protege por 1–3 h); se o médico indicou, a "
                                    "bombinha 15 min antes. Em ar frio, seco ou poluído, prefira treinar dentro.")
    if "renal" in cond:
        out["rpe_cap"] = min(out["rpe_cap"] or 10, 7)
        out["notes"].append("Doença renal: intensidade por esforço (até 7 de 10), sem carga no braço da fístula e respeite o "
                            "limite de líquidos.")
    if "cancer" in cond:
        out["rpe_cap"] = min(out["rpe_cap"] or 10, 7.5)
        out["notes"].append("Câncer: musculação 2×/semana (8–15 repetições) e aeróbio 3×/semana, ajustando dia a dia pelo "
                            "cansaço; com febre durante a quimioterapia, não treine e fale com a equipe.")
    # ---- remédios
    if "fc" in meds:
        out["notes"].append("Remédio que baixa a frequência cardíaca: as zonas por FC não valem para você. O cardio passa a ser "
                            "por esforço percebido (de 0 a 10) e pelo teste da fala.")
    if "diuretico" in meds:
        out["session_notes"].append("Diurético: hidrate-se bem, cuidado redobrado no calor e volte à calma devagar.")
    if "anticoagulante" in meds:
        out["avoid"] |= JUMP_IDS
        out["no_jumps"] = True
        out["session_notes"].append("Anticoagulante: nada de saltos ou risco de queda; se cair e bater a cabeça, procure atendimento.")
    if a.get("smoker") == "sim":
        out["end_rate"] = min(out["end_rate"] or 1.08, 1.05)
        out["notes"].append("Fumante: progressão mais lenta nas primeiras semanas; evite fumar 1–2 h antes e depois do treino — o "
                            "exercício ajuda a diminuir a vontade de fumar.")

    # ---- fase de vida (mulheres)
    if sex == "F":
        ph = a.get("phase")
        if ph == "pos_parto":
            wk = _num(a.get("postpartum_weeks"))
            pelvic = a.get("pelvic") == "sim"
            if wk is not None and wk < 6:
                out.update(rest_day=True, no_jumps=True, no_running=True, no_core_flexion=True, rpe_cap=5.0)
                out["notes"].append("Menos de 6 semanas do parto: caminhada, respiração e contração do assoalho pélvico. O treino "
                                    "completo volta depois da consulta de revisão pós-parto.")
            elif wk is not None and wk < 12:
                out.update(no_jumps=True, no_running=True, rpe_cap=min(out["rpe_cap"] or 10, 6.5))
                if a.get("cesarea") == "sim":
                    out["no_core_flexion"] = True
                out["notes"].append("6–12 semanas do parto: força progressiva e cardio de baixo impacto (esforço até 6–7 de 10). "
                                    "Corrida e saltos só depois de 12 semanas e dos testes de retorno sem perda de urina, peso ou dor.")
            elif pelvic:
                out.update(no_jumps=True, no_running=True)
                out["notes"].append("Perda de urina, peso na vagina ou barriga estufando: sem corrida e saltos por enquanto, e "
                                    "procure fisioterapia pélvica.")
            if pelvic:
                out["no_core_flexion"] = True
        if ph == "menopausa":
            out["notes"].append("Menopausa: força perto da falha (1–3 repetições sobrando) e potência ajudam nos fogachos, no "
                                "osso e na gordura visceral; treine em lugar fresco e evite treino forte à noite se atrapalhar o sono.")
            if a.get("pelvic") == "sim":
                out["no_jumps"] = True
                out["session_notes"].append("Perda de urina: troque impacto por versões sem salto e faça exercícios de assoalho pélvico.")
        if a.get("amenorrhea") == "sim" or (b and b < 18.5) or "fratura_estresse" in (a.get("run_injuries") or []):
            out["hold_progress"] = True
            out["notes"].append("Sinal de pouca energia disponível (menstruação ausente, IMC baixo ou fratura por estresse): o "
                                "volume não sobe até uma avaliação com médico e nutricionista (RED-S, consenso do COI 2023).")

    # ---- musculação
    if a.get("diet") == "agressiva":
        out["rir_plus"] = 1
        out["notes"].append("Dieta com déficit grande: cargas mantidas, 1 repetição a mais de sobra nos compostos e volume sem "
                            "subir. Perder mais de 1% do peso por semana tira músculo — 0,5 a 1% é o ideal.")
    elif a.get("diet") == "leve":
        out["notes"].append("Em dieta: mantenha as cargas (é o que segura o músculo) e a proteína alta.")
    prot = a.get("protein")
    if prot in ("1", "2") or (prot and a.get("diet") in ("leve", "agressiva")):
        tgt = "2,0–2,4" if a.get("diet") in ("leve", "agressiva") else "1,6–2,2"
        w = _num((p or {}).get("weight_kg"))
        g = f" (≈ {round(w * (2.2 if a.get('diet') in ('leve', 'agressiva') else 1.8))} g/dia para você)" if w else ""
        out["notes"].append(f"Proteína: o ganho de músculo pede {tgt} g por kg por dia{g}, em 3–5 refeições.")
    if a.get("ankle") == "nao":
        out["demote"]["squat"] = DEEP_BARBELL_SQUAT
        out["prefer"]["squat"] = ["Goblet_Squat", "Hack_Squat", "Leg_Press", "Pendulum_Squat"]
        out["notes"].append("Tornozelo pouco móvel: agachamento com calcanhar elevado, goblet, hack ou leg press no lugar do "
                            "agachamento livre profundo.")
    if a.get("overhead") in ("nao", "doi"):
        out["avoid"] |= OVERHEAD_BARBELL
        out["prefer"]["v_push"] = ["LandmineStation", "Machine_Shoulder_Military_Press", "Seated_Dumbbell_Press"]
        out["notes"].append("Ombro sem amplitude total acima da cabeça: desenvolvimento na máquina, landmine ou com halteres "
                            "sentado; nada de barra atrás da nuca.")
    h = _num((p or {}).get("height_cm"))
    if h and h >= 185:
        out["demote"]["hinge"] = {"Barbell_Deadlift"}
        out["notes"].append("Altura de 1,85 m ou mais: terra romeno e pull-through no lugar do terra convencional (alavanca longa).")
    for region in a.get("past_injury") or []:
        out["extra_lims"].append({"key": region, "severity": "cuidado", "history": True})
    if a.get("past_injury"):
        out["notes"].append("Lesão antiga já liberada: a região entra no treino com cuidado (versões com apoio primeiro). Dor de "
                            "até 3 de 10 que some na manhã seguinte é aceitável; se não sumir, volte um passo.")
    dislikes = [d for d in (a.get("dislikes") or []) if catalog.get(d)]
    out["avoid"] |= set(dislikes)

    # ---- corrida
    inj = set(a.get("run_injuries") or [])
    if inj:
        out["end_rate"] = min(out["end_rate"] or 1.08, 1.05)
        out["spike"] = 1.05
        out["run_notes"].append("Lesão de corrida no último ano: volume sobe no máximo 5% por semana e nenhum treino passa 5% do mais "
                                "longo dos últimos 30 dias.")
    if inj & {"aquiles", "fratura_estresse"}:
        out["no_hills_speed"] = True
    if "aquiles" in inj:
        out["run_notes"].append("Aquiles: pode correr com dor até 5 de 10 se a manhã seguinte não piorar. Sem subidas e tiros nas "
                                "primeiras 4 semanas. Panturrilha 3×/semana: 3×15 joelho dobrado + 3×15 esticado, depois com carga.")
    if "fascite" in inj:
        out["run_notes"].append("Fascite: elevação de calcanhar com carga e toalha sob os dedos, dia sim dia não (12 → 8 repetições). "
                                "Tênis amortecido; corte 20–30% do volume enquanto o primeiro passo da manhã doer 4+ de 10.")
    if "canelite" in inj:
        out["run_notes"].append("Canelite: sem dias seguidos de corrida por 4 semanas, panturrilha/sóleo forte e passos ~5% mais curtos e rápidos.")
    if "fratura_estresse" in inj:
        out["run_notes"].append("Fratura por estresse: só volte a correr sem dor no osso e caminhando 30–45 min sem dor; sem tiros e "
                                "subidas por 8–12 semanas. Vale avaliar energia/alimentação com um profissional.")
    if inj & {"joelho_frente", "banda_it", "canelite", "fratura_estresse"}:
        out["run_notes"].append("Cadência: passos 5–10% mais rápidos e curtos que o seu normal diminuem a carga no joelho e na canela "
                                "(Heiderscheit 2011). Evite descidas longas.")
    if inj & {"joelho_frente", "banda_it"}:
        out["prefer"]["glute"] = ["Thigh_Abductor", "Cable_Standing_Hip_Abduction"]
    pain = a.get("run_pain")
    if pain == "forte":
        out["no_running"] = True
        out["run_notes"].append("Dor forte ao correr: a corrida para por enquanto — bike, elíptico ou corrida na piscina no lugar. "
                                "Procure avaliação.")
    elif pain == "moderada":
        out["force_run_walk"] = True
        out["no_hills_speed"] = True
    if a.get("shoes") == "carbono":
        out["run_notes"].append("Placa de carbono: só em prova e em no máximo 1 treino forte por semana; nos outros, tênis comum.")
    elif a.get("shoes") == "minimalista":
        out["run_notes"].append("Minimalista: aumente aos poucos (10% do volume, +10% a cada 2 semanas) e fortaleça a panturrilha.")
    if a.get("run_time") == "meio_dia":
        out["run_notes"].append("Correr entre 10–16h no calor: nos dias quentes, corra pelo esforço (não pelo ritmo) e, nos tiros, "
                                "prefira o começo da manhã ou a noite.")
    age = _num((p or {}).get("age"))
    if age and age >= 50:
        out["pyramidal"] = True

    # ---- rotina
    if a.get("job") == "fisico":
        out["lower_sets_minus"] = 1
        out["notes"].append("Trabalho físico não conta como treino (paradoxo da atividade ocupacional), mas cansa: pernas e costas "
                            "com 1 série a menos nos acessórios; terra e treino longo em dia de folga.")
    if a.get("job") == "turnos":
        out["notes"].append("Turnos/noturno: treine pela hora em que acorda, não pelo relógio; nada forte nas 2 h antes de dormir de dia, "
                            "e depois da 1ª noite do turno, só leve.")
    if a.get("stress") == "muito":
        out["sets_minus"] = 1
        out["notes"].append("Estresse alto recupera mais devagar (Stults-Kolehmainen 2014): 1 série a menos nos secundários e "
                            "acessórios, intensidade mantida. Treinar continua ajudando.")
    if a.get("caregiver") == "sim":
        out["notes"].append("Cuidando de alguém: nos dias apertados, use “Outra opção → pouco tempo” — 1 série por exercício com "
                            "intensidade mantém força e músculo (Spiering 2021).")
    if out["no_core_flexion"]:
        out["avoid"] |= FLEXION_IDS
    if out["no_jumps"]:
        out["avoid"] |= JUMP_IDS
    return out


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply_item(it: dict, d: dict) -> dict:
    """Ajustes de um exercício principal (RPE, repetições, séries, sobra de repetições)."""
    if not d or (it.get("phase") or "principal") != "principal" or not it.get("sets"):
        return it
    if d.get("rpe_cap") and it.get("rpe_target"):
        it["rpe_target"] = min(float(it["rpe_target"]), d["rpe_cap"])
    if d.get("reps_min") and it.get("reps"):
        try:
            lo, hi = [int(x) for x in str(it["reps"]).split("-")]
        except ValueError:
            lo = hi = None
        if lo is not None and lo < d["reps_min"]:
            it["reps"] = f"{d['reps_min']}-{max(hi, d['reps_min'] + 4)}"
    role = it.get("role")
    minus = (d.get("sets_minus") or 0) if role in ("sec", "acc") else 0
    ex = catalog.get(it.get("exercise_id")) or {}
    lower = set(ex.get("primaryMuscles") or []) & {"quadriceps", "hamstrings", "glutes", "lower back", "adductors", "abductors"}
    if lower and role in ("sec", "acc"):
        minus += d.get("lower_sets_minus") or 0
    if minus and not it.get("_personal_sets"):
        it["sets"] = max(2, int(it["sets"]) - minus)
        it["_personal_sets"] = True
    if d.get("rir_plus") and ex.get("mechanic") == "compound" and it.get("rir") and not it.get("_personal_rir"):
        it["rir"] = _rir_plus(str(it["rir"]), d["rir_plus"])
        it["_personal_rir"] = True
    return it


def _rir_plus(rir: str, k: int) -> str:
    import re
    nums = [int(x) for x in re.findall(r"\d+", rir)[:2]]
    if not nums:
        return rir
    return "–".join(str(n + k) for n in nums) + (rir[rir.find(" "):] if " " in rir else "")


def stop_signs_text() -> str:
    return ("Pare e procure ajuda se sentir: dor ou aperto no peito, desmaio ou quase desmaio, falta de ar desproporcional, "
            "coração disparado ou irregular, sintoma neurológico novo, ou dor na panturrilha com inchaço.")
