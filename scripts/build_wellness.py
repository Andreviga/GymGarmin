"""Gera gymgarmin/data/wellness.json: sessões guiadas de yoga, Pilates, mobilidade, alongamento e respiração.

Cada passo: n (nome), t (segundos; por lado se s=true), s (faz dos dois lados), r (repetições, só texto),
how (como fazer), ex (exercício do catálogo, para mostrar a imagem), breath ([inspira, segura, solta, segura] s),
avoid (condições do perfil detalhado em que o passo é trocado por alt).
Doses: pesquisa em docs (ver 'sources' de cada sessão). [PB] = prática consagrada, sem estudo direto.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "gymgarmin" / "data" / "wellness.json"
FLEX = ["osteoporose", "fratura_vertebra"]   # sem flexão de coluna com carga/rolamento


def st(n, t, how, s=False, ex=None, r=None, breath=None, avoid=None, alt=None):
    d = {"n": n, "t": t, "how": how}
    if s:
        d["s"] = True
    if ex:
        d["ex"] = ex
    if r:
        d["r"] = r
    if breath:
        d["breath"] = breath
    if avoid:
        d["avoid"] = avoid
    if alt:
        d["alt"] = alt
    return d


SLOW = [4, 0, 6, 0]          # ~6 respirações por minuto, expiração mais longa
SIGH = [2, 1, 6, 0]          # suspiro cíclico: inspira, completa a inspiração, solta longo pela boca
BOX = [4, 4, 4, 4]
SLEEP = [4, 0, 7, 0]

S = {
    "Thomas2018": ("Thomas et al., 2018 — dose de alongamento (≥5 min por músculo/semana)", "https://pubmed.ncbi.nlm.nih.gov/29506306/"),
    "Afonso2021h": ("Afonso et al., 2021 — força em amplitude total melhora a amplitude tanto quanto alongar", "https://pubmed.ncbi.nlm.nih.gov/33917036/"),
    "Afonso2021r": ("Afonso et al., 2021 — alongar depois do treino não acelera a recuperação", "https://doi.org/10.3389/fphys.2021.677581"),
    "Delphi2025": ("Warneke et al., 2025 — consenso Delphi sobre alongamento", "https://pubmed.ncbi.nlm.nih.gov/40513717/"),
    "Behm2016": ("Behm et al., 2016 — alongamento estático, dinâmico e PNF antes do esforço", "https://pubmed.ncbi.nlm.nih.gov/26642915/"),
    "Kay2012": ("Kay & Blazevich, 2012 — estático ≥60 s antes reduz força/potência", "https://pubmed.ncbi.nlm.nih.gov/21659901/"),
    "Fradkin2010": ("Fradkin et al., 2010 — aquecimento melhora o desempenho", "https://pubmed.ncbi.nlm.nih.gov/19996770/"),
    "VanHooren2018": ("Van Hooren & Peake, 2018 — o que o desaquecimento faz (e não faz)", "https://doi.org/10.1007/s40279-018-0916-2"),
    "Schleinzer2024": ("Schleinzer et al., 2024 — yoga reduz o estresse percebido", "https://pubmed.ncbi.nlm.nih.gov/39553891/"),
    "Wang2020": ("Wang et al., 2020 — yoga e qualidade do sono", "https://doi.org/10.1186/s12888-020-02566-4"),
    "Laborde2022": ("Laborde et al., 2022 — respiração lenta aumenta a variabilidade da FC", "https://doi.org/10.1016/j.neubiorev.2022.104711"),
    "Zaccaro2018": ("Zaccaro et al., 2018 — respiração lenta e relaxamento", "https://pubmed.ncbi.nlm.nih.gov/30245619/"),
    "Balban2023": ("Balban et al., 2023 — suspiro cíclico x respiração quadrada x meditação", "https://doi.org/10.1016/j.xcrm.2022.100895"),
    "Lehrer2014": ("Lehrer & Gevirtz, 2014 — respiração ressonante (~6/min)", "https://doi.org/10.3389/fpsyg.2014.00756"),
    "Cramer2015": ("Cramer et al., 2015 — segurança da yoga", "https://pubmed.ncbi.nlm.nih.gov/26116216/"),
    "Lawford2024": ("Cochrane, Lawford et al., 2024 — exercício para artrose de joelho", "https://pubmed.ncbi.nlm.nih.gov/39625083/"),
    "Hayden2021": ("Cochrane, Hayden et al., 2021 — exercício para dor lombar crônica", "https://pubmed.ncbi.nlm.nih.gov/34580864/"),
    "FernandezRodriguez2022": ("Fernández-Rodríguez et al., 2022 — Pilates e força para dor lombar crônica", "https://pubmed.ncbi.nlm.nih.gov/35722759/"),
    "WHO2023": ("OMS, 2023 — diretriz para dor lombar", "https://www.who.int/publications/i/item/9789240081789"),
    "Naunton2020": ("Naunton et al., 2020 — exercício resistido para o ombro", "https://pubmed.ncbi.nlm.nih.gov/32571081/"),
    "Clifford2020": ("Clifford et al., 2020 — isometria para tendões", "https://pubmed.ncbi.nlm.nih.gov/32818059/"),
    "PilatesIdosos": ("Meta-análise — Pilates de solo em idosos (equilíbrio e força)", "https://pubmed.ncbi.nlm.nih.gov/29283899/"),
    "Youkhana2016": ("Youkhana et al., 2016 — yoga, equilíbrio e mobilidade em idosos", "https://pubmed.ncbi.nlm.nih.gov/26707903/"),
    "Albulescu2022": ("Albulescu et al., 2022 — micropausas no trabalho", "https://pubmed.ncbi.nlm.nih.gov/36044424/"),
    "Escritorio2024": ("Revisão 2024 — exercício para dor no pescoço em trabalho de escritório", "https://pubmed.ncbi.nlm.nih.gov/38219373/"),
}


def src(*keys):
    return [{"t": S[k][0], "u": S[k][1]} for k in keys]


SESSIONS = [
    # ------------------------------------------------------------------ estresse e sono
    {"id": "yoga_estresse", "title": "Yoga para baixar o estresse", "cat": "yoga", "icon": "🧘", "goal": ["estresse"],
     "when": ["descanso", "noite", "qualquer"], "level": "todos",
     "why": "Yoga suave com respiração lenta (~6 por minuto, soltando o ar mais devagar do que puxa) reduz o estresse "
            "percebido e ativa o lado 'freio' do sistema nervoso. Melhor 2–3× por semana.",
     "cautions": "Sem dor: vá até sentir alongar, nunca doer. Glaucoma ou pressão alta sem controle: evite ficar de cabeça para baixo.",
     "sources": src("Schleinzer2024", "Laborde2022", "Cramer2015"),
     "steps": [
         st("Respiração lenta sentado", 180, "Sente confortável. Puxe o ar pelo nariz em 4 s e solte em 6 s. Ombros soltos.", breath=SLOW),
         st("Gato-vaca", 60, "De quatro apoios: ao puxar o ar, peito para frente e olhar para cima; ao soltar, arredonde as costas. Bem devagar.", ex="Cat_Stretch", r="6–8 ciclos"),
         st("Postura da criança", 90, "Joelhos afastados, quadril nos calcanhares, braços à frente. Respire nas costas.", ex="Childs_Pose"),
         st("Avanço baixo", 45, "Um joelho no chão, o outro pé à frente. Quadril desce e vai para frente; sinta a frente da coxa de trás.", s=True, ex="Kneeling_Hip_Flexor"),
         st("Flexão à frente em pé", 50, "Joelhos dobrados, deixe o tronco pendurar. Cabeça e pescoço soltos.", avoid=FLEX,
            alt=st("Alongamento de posterior deitado", 50, "Deitado, puxe uma perna esticada com uma toalha no pé. Costas no chão.", s=True, ex="Hamstring_Stretch")),
         st("Ponte apoiada", 90, "Deitado, pés no chão, suba o quadril e apoie um travesseiro ou bloco embaixo do sacro. Solte o peso.", ex="Butt_Lift_Bridge"),
         st("Torção deitada", 45, "Deitado, joelhos dobrados caem para um lado, braços abertos, olhar para o outro.", s=True, ex="Lying_Crossover"),
         st("Pernas na parede", 240, "Deitado com o quadril perto da parede e as pernas apoiadas nela. Respiração lenta.", breath=SLOW),
         st("Relaxamento final", 300, "Deitado de barriga para cima. Passe a atenção pelo corpo dos pés à cabeça, soltando cada parte."),
     ]},
    {"id": "yoga_sono", "title": "Yoga e respiração para dormir", "cat": "yoga", "icon": "🌙", "goal": ["sono", "estresse"],
     "when": ["noite"], "level": "todos",
     "why": "Sequência curta e calma para a noite, terminando com expiração longa (puxa 4 s, solta 7 s). Yoga melhora um pouco a "
            "qualidade do sono, e a respiração lenta acalma o corpo antes de deitar.",
     "cautions": "Faça na cama ou no tapete, com luz baixa. Tontura: volte a respirar normal.",
     "sources": src("Wang2020", "Zaccaro2018", "Laborde2022"),
     "steps": [
         st("Gato-vaca bem lento", 60, "De quatro apoios, mova a coluna no ritmo da respiração.", ex="Cat_Stretch"),
         st("Postura da criança", 90, "Testa apoiada, braços ao lado do corpo. Solte os ombros.", ex="Childs_Pose"),
         st("Torção deitada", 45, "Joelhos caem para um lado, olhar para o outro.", s=True, ex="Lying_Crossover"),
         st("Glúteo em '4' deitado", 45, "Tornozelo sobre o joelho oposto, puxe a coxa de baixo em direção ao peito.", s=True, ex="Lying_Glute"),
         st("Pernas na parede", 180, "Pernas apoiadas na parede, braços soltos.", breath=SLEEP),
         st("Expiração longa", 300, "Deitado: puxe o ar em 4 s pelo nariz e solte em 7 s. Se dormir no meio, ótimo.", breath=SLEEP),
     ]},
    # ------------------------------------------------------------------ corrida
    {"id": "aquecimento_corrida", "title": "Aquecimento antes de correr", "cat": "mobilidade", "icon": "🏃", "goal": ["corrida", "pre"],
     "when": ["antes_corrida"], "level": "todos",
     "why": "Aquecer (subir a temperatura, ativar glúteo e panturrilha, mobilizar e acelerar) melhora o desempenho. Alongamento "
            "parado de 60 s ou mais antes de correr tira força: aqui é tudo dinâmico.",
     "cautions": "Machucado: faça as acelerações bem leves ou pule.",
     "sources": src("Fradkin2010", "Behm2016", "Kay2012"),
     "steps": [
         st("Trote leve ou caminhada rápida", 240, "Bem fácil, dá para conversar."),
         st("Ponte de glúteo", 40, "Suba o quadril apertando o glúteo, 2 s em cima.", ex="Butt_Lift_Bridge", r="10"),
         st("Caminhada lateral com elástico", 40, "Elástico acima dos joelhos, passos laterais curtos sem deixar o joelho entrar.", ex="Monster_Walk", r="10 para cada lado"),
         st("Elevação de panturrilha", 40, "Suba na ponta dos pés e desça devagar.", ex="Standing_Calf_Raises", r="15"),
         st("Balanço de perna", 30, "Apoiado numa parede, balance a perna para frente e para trás, depois para os lados.", s=True, r="10 + 10"),
         st("Avanço com rotação", 45, "Passo à frente, desça e gire o tronco para o lado da perna da frente.", ex="Bodyweight_Walking_Lunge", r="6 de cada lado"),
         st("Abertura de quadril", 30, "Em pé, leve o joelho para cima e abra para o lado, desenhando um círculo.", s=True, ex="Standing_Hip_Circles", r="6"),
         st("Lagarta", 40, "Em pé, desça as mãos até o chão, caminhe com as mãos até a prancha e volte.", ex="Inchworm", r="4"),
         st("Skipping A", 30, "Joelho alto alternando, pisando na ponta do pé, braços acompanham.", r="2 × 15 m"),
         st("Anfersen (calcanhar no glúteo)", 30, "Corrida curta levando o calcanhar ao glúteo.", r="2 × 15 m"),
         st("Acelerações progressivas", 60, "2–3 tiros de 60–80 m subindo até 70–85% do máximo, voltando andando.", r="2–3"),
     ]},
    {"id": "pos_corrida", "title": "Depois de correr: soltar e alongar", "cat": "alongamento", "icon": "🦵", "goal": ["corrida", "pos", "amplitude"],
     "when": ["depois_corrida"], "level": "todos",
     "why": "Desaquecer e alongar não acelera a recuperação nem previne lesão. Mas depois da corrida é um bom momento para "
            "somar tempo de alongamento, que melhora a amplitude com o tempo, e para o corpo desacelerar.",
     "cautions": "Alongue até sentir tensão, nunca dor.",
     "sources": src("VanHooren2018", "Afonso2021r", "Delphi2025", "Thomas2018"),
     "steps": [
         st("Caminhada leve", 180, "Deixe a respiração voltar ao normal."),
         st("Panturrilha (perna esticada)", 40, "Mãos na parede, perna de trás esticada, calcanhar no chão.", s=True, ex="Calf_Stretch_Hands_Against_Wall"),
         st("Panturrilha (joelho dobrado)", 40, "Mesma posição, dobre o joelho de trás: alonga o sóleo.", s=True, ex="Standing_Soleus_And_Achilles_Stretch"),
         st("Flexor do quadril", 45, "Um joelho no chão, aperte o glúteo dessa perna e leve o quadril para frente.", s=True, ex="Kneeling_Hip_Flexor"),
         st("Posterior de coxa deitado", 45, "Deitado, puxe a perna esticada com uma toalha.", s=True, ex="Hamstring_Stretch"),
         st("Quadríceps", 40, "Em pé (apoie numa parede) ou deitado de lado, segure o pé atrás.", s=True, ex="Quad_Stretch"),
         st("Glúteo em '4'", 45, "Deitado, tornozelo sobre o joelho oposto, puxe a coxa.", s=True, ex="Lying_Glute"),
         st("Respiração lenta", 90, "Puxe 4 s, solte 6 s.", breath=SLOW),
     ]},
    {"id": "yoga_corredores", "title": "Yoga para corredores", "cat": "yoga", "icon": "🧘", "goal": ["corrida", "amplitude"],
     "when": ["descanso", "leve", "depois_corrida"], "level": "intermediario",
     "why": "Quadril, posterior e panturrilha com posturas mantidas de 45–90 s. Bom em dia de descanso ou de treino leve "
            "(não logo antes de um treino forte: posturas longas tiram força na hora).",
     "cautions": "Joelho dói no pombo: faça o '4' deitado.",
     "sources": src("Thomas2018", "Behm2016", "Delphi2025"),
     "steps": [
         st("Saudação ao sol suave", 120, "3 voltas devagar: braços para cima, dobra à frente, prancha, cachorro olhando para baixo, volta.", r="3"),
         st("Avanço baixo", 50, "Joelho de trás no chão, quadril para frente e para baixo.", s=True, ex="Kneeling_Hip_Flexor"),
         st("Lagarto", 50, "Do avanço, as duas mãos por dentro do pé da frente, quadril baixo.", s=True, ex="Worlds_Greatest_Stretch"),
         st("Meia espacate", 50, "Do avanço, leve o quadril para trás e estique a perna da frente, ponta do pé para cima.", s=True, ex="Hamstring_Stretch"),
         st("Pombo (ou '4' deitado)", 75, "Canela da frente atravessada, perna de trás esticada. Joelho incomoda: faça deitado.", s=True, ex="Lying_Glute"),
         st("Cachorro olhando para baixo", 60, "Quadril alto, pedalando: um calcanhar desce, depois o outro.", ex="Calf_Stretch_Hands_Against_Wall"),
         st("Agachamento profundo (guirlanda)", 50, "Pés afastados, desça o quadril, cotovelos abrem os joelhos.", ex="Bodyweight_Squat"),
         st("Torção deitada", 45, "Joelhos para um lado, olhar para o outro.", s=True, ex="Lying_Crossover"),
         st("Pernas na parede", 180, "Respiração lenta.", breath=SLOW),
     ]},
    # ------------------------------------------------------------------ amplitude
    {"id": "amplitude", "title": "Mais amplitude (alongar + força em amplitude)", "cat": "alongamento", "icon": "🤸", "goal": ["amplitude"],
     "when": ["descanso", "qualquer"], "level": "todos",
     "why": "O que mais pesa é o tempo total por semana: ~5 min por músculo por semana, em sessões curtas e frequentes "
            "(5× por semana é o ideal). Força com amplitude total ganha amplitude tanto quanto alongar — por isso o fim é com exercícios profundos.",
     "cautions": "Até a tensão, não até a dor. Articulação muito flexível ou cirurgia recente: sem balançar no fim do movimento.",
     "sources": src("Thomas2018", "Afonso2021h", "Delphi2025", "Behm2016"),
     "steps": [
         st("Aquecer: polichinelo leve ou marcha", 120, "Só para esquentar."),
         st("Posterior de coxa", 45, "Deitado com toalha no pé, perna esticada.", s=True, ex="Hamstring_Stretch"),
         st("Flexor do quadril", 45, "Joelho no chão, glúteo apertado, quadril à frente.", s=True, ex="Kneeling_Hip_Flexor"),
         st("Panturrilha", 45, "Mãos na parede, calcanhar no chão.", s=True, ex="Calf_Stretch_Hands_Against_Wall"),
         st("Adutores (borboleta)", 60, "Sentado, solas dos pés juntas, joelhos caem para os lados.", ex="Groin_and_Back_Stretch"),
         st("Glúteo em '4'", 45, "Deitado, tornozelo sobre o joelho oposto.", s=True, ex="Lying_Glute"),
         st("Peitoral na porta", 45, "Antebraço no batente da porta, gire o corpo para o outro lado.", s=True, ex="Chest_And_Front_Of_Shoulder_Stretch"),
         st("PNF no posterior", 60, "Com a toalha: empurre a perna contra ela por 5 s (força leve), solte e puxe mais por 25 s. 2×.", s=True, ex="Hamstring_Stretch"),
         st("Agachamento cossaco", 60, "Pés bem afastados, desça para um lado com a outra perna esticada.", ex="Bodyweight_Squat", r="8 alternando"),
         st("Terra romeno lento", 60, "Com halteres ou elástico: desça com as costas retas até sentir o posterior, 3 s descendo.", ex="Stiff-Legged_Dumbbell_Deadlift", r="10", avoid=FLEX,
            alt=st("Ponte com uma perna", 60, "Calcanhar longe do quadril, 2 s em cima.", ex="Single_Leg_Glute_Bridge", r="10 de cada")),
         st("Agachamento profundo parado", 60, "Desça o máximo que der com o calcanhar no chão; segure numa porta se precisar.", ex="Bodyweight_Squat"),
     ]},
    {"id": "mobilidade_articular", "title": "Mobilidade das articulações (6 min)", "cat": "mobilidade", "icon": "🔄", "goal": ["articulacoes"],
     "when": ["manha", "qualquer", "antes_treino"], "level": "todos",
     "why": "Círculos lentos e no maior tamanho que der em cada articulação, sem dor. É prática de treinadores (não há estudo "
            "que meça): serve como 'despertar' rápido do corpo e antes do treino.",
     "cautions": "Movimento lento e controlado; dor = diminua o círculo.",
     "sources": src("Behm2016"),
     "steps": [
         st("Pescoço", 40, "Desenhe meio círculo com o queixo de um ombro ao outro, devagar.", ex="Side_Neck_Stretch", r="3 para cada lado"),
         st("Ombros", 45, "Braço esticado, desenhe o maior círculo possível sem girar o tronco.", s=True, ex="Arm_Circles", r="3 + 3"),
         st("Coluna (gato-vaca)", 45, "De quatro apoios, uma vértebra de cada vez.", ex="Cat_Stretch"),
         st("Quadril", 45, "De quatro apoios ou em pé, joelho desenha um círculo grande.", s=True, ex="Standing_Hip_Circles", r="3 + 3"),
         st("Joelhos", 30, "Em pé, mãos nos joelhos, círculos pequenos.", r="5 + 5"),
         st("Tornozelos", 40, "Sentado ou em pé, desenhe o maior círculo com o pé.", s=True, ex="Ankle_Circles", r="5 + 5"),
     ]},
    # ------------------------------------------------------------------ articulações
    {"id": "joelho", "title": "Joelho protegido", "cat": "articulacoes", "icon": "🦿", "goal": ["articulacoes", "joelho"],
     "when": ["qualquer"], "level": "todos",
     "why": "Exercício é o tratamento mais estudado para o joelho (inclusive artrose): reduz dor e melhora função. Base: "
            "quadríceps e glúteo fortes, 2–3× por semana, mais caminhadas.",
     "cautions": "Dor até 3–5 de 10 no exercício é aceitável se passar em 24 h. Joelho quente e inchado: pare e procure avaliação.",
     "sources": src("Lawford2024", "Clifford2020"),
     "steps": [
         st("Sentar e levantar da cadeira", 60, "Cadeira firme, levante sem usar as mãos e sente devagar (3 s).", ex="Bodyweight_Squat", r="3 × 8–12"),
         st("Elevação da perna esticada", 50, "Deitado, uma perna dobrada; suba a outra esticada até a altura do joelho dobrado.", s=True, r="3 × 10"),
         st("Extensão final do joelho com elástico", 50, "Elástico atrás do joelho preso num pé de mesa: estique o joelho até o fim e segure 2 s.", s=True, r="3 × 12"),
         st("Ponte de glúteo", 50, "Suba o quadril, 2 s em cima.", ex="Butt_Lift_Bridge", r="3 × 12"),
         st("Abdução deitado de lado", 50, "Deitado de lado, suba a perna de cima um pouco para trás.", s=True, ex="IT_Band_and_Glute_Stretch", r="3 × 12"),
         st("Subida no degrau baixo", 60, "Suba num degrau com uma perna, desça devagar.", s=True, ex="Step-up_with_Knee_Raise", r="3 × 8"),
         {**st("Agachamento isométrico na parede", 40, "Costas na parede, joelhos a ~60–90°, segure. Alivia a dor do tendão patelar na hora."), "sets": 3, "rest": 45},
     ]},
    {"id": "lombar", "title": "Lombar forte (McGill + mobilidade)", "cat": "articulacoes", "icon": "🪨", "goal": ["articulacoes", "lombar"],
     "when": ["manha", "qualquer"], "level": "todos",
     "why": "Exercício reduz a dor lombar crônica; nenhum tipo é claramente melhor que outro — o melhor é o que você faz. "
            "Os 'três grandes' do McGill treinam o tronco a segurar a coluna parada, com 10 s por repetição.",
     "cautions": "Dor que desce pela perna, formigamento, febre ou alteração para urinar: procure avaliação antes.",
     "sources": src("Hayden2021", "WHO2023", "FernandezRodriguez2022"),
     "steps": [
         st("Gato-vaca", 45, "Mova só até onde é confortável.", ex="Cat_Stretch", r="8"),
         st("Abdominal modificado de McGill", 90, "Deitado, uma perna dobrada, mãos sob a lombar. Tire só cabeça e ombros do chão, segure 10 s.", r="6-4-2 (10 s cada)"),
         st("Prancha lateral", 60, "Do joelho (fácil) ou do pé. Corpo reto, segure 10 s.", s=True, ex="Side_Bridge", r="6-4-2 (10 s cada)"),
         st("Perdigueiro (bird-dog)", 90, "De quatro apoios, estique braço e perna opostos sem girar o quadril, segure 10 s.", ex="Dead_Bug", r="6-4-2 de cada lado"),
         st("Dobradiça de quadril", 45, "Bastão nas costas (cabeça, meio das costas e sacro encostados): dobre no quadril sem perder os 3 pontos.", r="10"),
         st("Ponte de glúteo", 45, "Quadril sobe pelo glúteo, não pela lombar.", ex="Butt_Lift_Bridge", r="12"),
     ]},
    {"id": "ombro", "title": "Ombro saudável (manguito)", "cat": "articulacoes", "icon": "💪", "goal": ["articulacoes", "ombro"],
     "when": ["qualquer", "antes_treino"], "level": "todos",
     "why": "Fortalecer com carga que progride é o que tem algum efeito no ombro; exercício sem carga não mostrou benefício. "
            "Rotação externa, remada e escápula, 2–3× por semana.",
     "cautions": "Dor forte ou fraqueza súbita no braço: procure avaliação.",
     "sources": src("Naunton2020"),
     "steps": [
         st("Rotação externa deitado de lado", 60, "Cotovelo colado no corpo com uma toalha embaixo, gire o antebraço para cima com um halter leve.", s=True, ex="External_Rotation_with_Band", r="3 × 12–15"),
         st("Rotação externa com elástico", 50, "Em pé, cotovelo junto ao corpo, abra o antebraço para fora.", s=True, ex="External_Rotation_with_Band", r="3 × 12–15"),
         st("Remada com elástico", 50, "Puxe o elástico até a costela, aperte as escápulas.", r="3 × 12"),
         st("Y e T deitado de bruços", 50, "Deitado de bruços, suba os braços em Y e depois em T, polegares para cima.", ex="Superman", r="2 × 10"),
         st("Deslizamento na parede", 45, "Antebraços na parede, deslize para cima sem tirar as costas da posição.", r="2 × 10"),
         st("Abertura com elástico", 40, "Braços esticados à frente, abra o elástico até o peito.", ex="Band_Pull_Apart", r="2 × 15"),
     ]},
    {"id": "tendao_iso", "title": "Isometria para tendões (joelho e aquiles)", "cat": "articulacoes", "icon": "🦶", "goal": ["articulacoes", "corrida", "joelho"],
     "when": ["qualquer", "antes_corrida"], "level": "todos",
     "why": "Segurar a força parada por 30–45 s alivia a dor do tendão na hora e é uma boa porta de entrada; no longo prazo, "
            "o que trata o tendão é carga que progride (isometria não é melhor que o exercício normal).",
     "cautions": "Esforço forte mas sem passar de 3–4 de dor em 10.",
     "sources": src("Clifford2020"),
     "steps": [
         {**st("Agachamento na parede", 45, "Joelhos a ~70°, segure forte; no descanso, ande um pouco."), "sets": 4, "rest": 60},
         {**st("Panturrilha em uma perna, parada", 40, "Na ponta de um pé só (apoie a mão na parede), segure no meio da subida.", s=True, ex="Single_Leg_Calf_Raise_Step"), "sets": 4, "rest": 30},
         {**st("Agachamento espanhol (elástico)", 45, "Elástico grosso atrás dos joelhos preso num ponto firme; sente para trás com a canela vertical e segure."), "sets": 4, "rest": 60},
     ]},
    # ------------------------------------------------------------------ Pilates
    {"id": "pilates_iniciante", "title": "Pilates de solo — iniciante", "cat": "pilates", "icon": "🌀", "goal": ["core", "lombar", "postura"],
     "when": ["qualquer"], "level": "iniciante",
     "why": "Pilates está entre os exercícios com mais efeito para dor lombar crônica (1–2× por semana já ajuda) e melhora "
            "equilíbrio e força em idosos. Controle, respiração e coluna neutra.",
     "cautions": "Osteoporose: os passos de rolar e dobrar a coluna são trocados automaticamente. Gestante ou diástase: cabeça no chão e versões mais leves.",
     "sources": src("FernandezRodriguez2022", "PilatesIdosos", "WHO2023"),
     "steps": [
         st("Respiração e coluna neutra", 90, "Deitado, joelhos dobrados. Puxe o ar expandindo as costelas para os lados; ao soltar, umbigo para dentro."),
         st("Ponte articulada", 60, "Suba a coluna do chão vértebra por vértebra e desça do mesmo jeito.", ex="Butt_Lift_Bridge", r="8"),
         st("Cem (The Hundred)", 60, "Pernas em mesa (joelhos a 90°), cabeça no chão se o pescoço cansar; braços batem para cima e para baixo: 5 batidas puxando o ar, 5 soltando.", r="50–100 batidas"),
         st("Meio rolamento para trás", 60, "Sentado, joelhos dobrados: role para trás até a metade e volte.", r="5–6", avoid=FLEX,
            alt=st("Perdigueiro", 60, "De quatro apoios, braço e perna opostos.", ex="Dead_Bug", r="8 de cada")),
         st("Círculos com uma perna", 60, "Deitado, uma perna para cima: círculos pequenos sem mexer o quadril.", s=True, r="5 para cada sentido"),
         st("Alongamento de uma perna", 60, "Cabeça e ombros levemente fora do chão, troque as pernas puxando um joelho de cada vez.", r="8 de cada lado", avoid=FLEX,
            alt=st("Marcha deitado", 60, "Cabeça no chão, abdômen firme, tire um pé de cada vez do chão.", r="10 de cada")),
         st("Série lateral de pernas", 60, "Deitado de lado: chute à frente e atrás, levantar e baixar, círculos pequenos.", s=True, ex="IT_Band_and_Glute_Stretch", r="8–10 de cada"),
         st("Natação", 30, "De bruços, braço e perna opostos sobem e trocam rápido, pescoço longo.", ex="Superman"),
         st("Cisne preparatório", 45, "De bruços, mãos ao lado do peito, suba só o peito olhando para o chão.", r="5"),
         st("Postura da criança", 45, "Descanse.", ex="Childs_Pose"),
     ]},
    {"id": "yoga_equilibrio", "title": "Yoga com cadeira: equilíbrio e mobilidade", "cat": "yoga", "icon": "🪑", "goal": ["equilibrio", "articulacoes"],
     "when": ["qualquer"], "level": "todos",
     "why": "Yoga melhora equilíbrio e mobilidade, principalmente com mais idade. Tudo com uma cadeira ou parede para apoiar.",
     "cautions": "Sempre com apoio por perto. Levante do chão devagar (tontura quando a pressão cai).",
     "sources": src("Youkhana2016"),
     "steps": [
         st("Montanha com troca de peso", 60, "Em pé, passe o peso de um pé para o outro, depois para frente e para trás."),
         st("Árvore com apoio", 30, "Mão na cadeira, um pé apoiado na canela ou no tornozelo da outra perna.", s=True, ex="Single_Leg_Balance"),
         st("Guerreiro II com cadeira", 30, "Pernas afastadas, joelho da frente dobrado sobre o pé, braços abertos.", s=True),
         st("Cadeira (meio agachamento)", 45, "Sente para trás como se fosse sentar, segure 10–20 s.", ex="Bodyweight_Squat", r="3"),
         st("Marcha calcanhar-ponta", 45, "Ande numa linha, calcanhar encostando na ponta do outro pé, perto de uma parede.", r="2 × 10 passos"),
         st("Gato-vaca sentado", 45, "Mãos nos joelhos, arredonde e estenda a coluna.", ex="Cat_Stretch", r="8"),
         st("Torção sentado", 30, "Gire o tronco segurando o encosto da cadeira.", s=True, ex="Chair_Lower_Back_Stretch"),
         st("Flexão à frente sentado", 45, "Tronco desce entre as pernas, cabeça solta.", ex="Chair_Lower_Back_Stretch", avoid=FLEX,
            alt=st("Respiração lenta sentado", 45, "Puxe 4 s, solte 6 s.", breath=SLOW)),
         st("Relaxamento", 180, "Sentado ou deitado, respiração lenta.", breath=SLOW),
     ]},
    {"id": "pausa_escritorio", "title": "Pausa ativa para quem trabalha sentado", "cat": "mobilidade", "icon": "💻", "goal": ["postura", "pescoco"],
     "when": ["trabalho", "qualquer"], "level": "todos",
     "why": "Pausas curtas dão mais disposição e menos cansaço. Para dor no pescoço, o que funciona é fortalecer (as abertura "
            "e rotação com elástico daqui), 2–3× por semana. A cada 60–90 min sentado.",
     "cautions": "Dor ou formigamento descendo pelo braço: procure avaliação.",
     "sources": src("Albulescu2022", "Escritorio2024", "Delphi2025"),
     "steps": [
         st("Retração do queixo", 40, "Leve o queixo para trás (papada), segure 3 s.", r="10"),
         st("Alongamento lateral do pescoço", 25, "Orelha em direção ao ombro, ombro do outro lado desce.", s=True, ex="Side_Neck_Stretch"),
         st("Rotação de ombros", 20, "Círculos para trás.", ex="Shoulder_Circles", r="10"),
         st("Abertura com elástico (ou aperte as escápulas)", 40, "Braços à frente, abra até o peito.", ex="Band_Pull_Apart", r="2 × 15"),
         st("Rotação externa com elástico", 40, "Cotovelo colado, abra o antebraço.", ex="External_Rotation_with_Band", r="12"),
         st("Extensão torácica na cadeira", 30, "Mãos atrás da cabeça, incline o peito para trás sobre o encosto.", ex="Chair_Upper_Body_Stretch", r="8"),
         st("Rotação torácica sentado", 30, "Braços cruzados no peito, gire o tronco.", s=True, r="6"),
         st("Flexor do quadril em pé", 30, "Um pé atrás, aperte o glúteo e leve o quadril à frente.", s=True, ex="Kneeling_Hip_Flexor"),
         st("Sentar e levantar", 40, "Da cadeira, sem as mãos.", ex="Bodyweight_Squat", r="10"),
     ]},
    # ------------------------------------------------------------------ respiração
    {"id": "resp_suspiro", "title": "Suspiro cíclico (5 min)", "cat": "respiracao", "icon": "🌬️", "goal": ["estresse"],
     "when": ["qualquer", "noite", "trabalho"], "level": "todos",
     "why": "Puxe o ar pelo nariz, complete com mais um pouco e solte bem devagar pela boca. Num estudo comparando técnicas "
            "(5 min por dia, 1 mês), foi a que mais melhorou o humor e baixou a frequência respiratória.",
     "cautions": "Tontura ou formigamento: volte a respirar normal.",
     "sources": src("Balban2023"),
     "steps": [st("Suspiro cíclico", 300, "Puxa pelo nariz (2 s), completa com mais um pouco (1 s), solta longo pela boca (6 s).", breath=SIGH)]},
    {"id": "resp_ressonante", "title": "Respiração ressonante (10 min)", "cat": "respiracao", "icon": "🫁", "goal": ["estresse", "sono"],
     "when": ["qualquer", "noite"], "level": "todos",
     "why": "Cerca de 6 respirações por minuto (puxa 4–5 s, solta 5–6 s) é o ritmo em que a frequência cardíaca mais oscila "
            "junto com a respiração; aumenta a variabilidade da FC. 5–20 min, 1–2× por dia.",
     "cautions": "Confortável, sem forçar. Nunca dirigindo ou na água.",
     "sources": src("Lehrer2014", "Laborde2022"),
     "steps": [st("Respiração ressonante", 600, "Pelo nariz, barriga e costelas expandem: puxa 4 s, solta 6 s.", breath=SLOW)]},
    {"id": "resp_quadrada", "title": "Respiração quadrada (5 min)", "cat": "respiracao", "icon": "⬜", "goal": ["estresse", "foco"],
     "when": ["qualquer", "trabalho"], "level": "todos",
     "why": "Puxa 4, segura 4, solta 4, segura 4. Ajuda a acalmar e focar (no estudo comparativo, menos que o suspiro cíclico).",
     "cautions": "Gestante, epilepsia, doença cardíaca ou pânico: prefira o suspiro cíclico (sem prender o ar).",
     "sources": src("Balban2023"),
     "steps": [st("Respiração quadrada", 300, "Puxa 4 s, segura 4 s, solta 4 s, segura 4 s.", breath=BOX)]},
]

CATS = {"yoga": "Yoga", "pilates": "Pilates", "alongamento": "Alongamento", "mobilidade": "Mobilidade",
        "articulacoes": "Proteção das articulações", "respiracao": "Respiração"}
GOALS = {"estresse": "Menos estresse", "sono": "Dormir melhor", "corrida": "Corrida", "amplitude": "Amplitude / flexibilidade",
         "articulacoes": "Articulações", "joelho": "Joelho", "lombar": "Lombar", "ombro": "Ombro", "core": "Core",
         "postura": "Trabalho sentado", "equilibrio": "Equilíbrio", "pescoco": "Pescoço", "foco": "Foco", "pre": "Antes", "pos": "Depois"}


def step_time(x):
    """Segundos que o passo leva de verdade: lados, séries com repetições (≈3,5 s/rep + 45 s entre séries) e isometria."""
    import re
    sides = 2 if x.get("s") else 1
    if x.get("sets"):
        return sides * x["sets"] * x["t"] + (x["sets"] - 1) * x.get("rest", 45) * (1 if sides == 1 else 1)
    m = re.match(r"(\d+)\s*×\s*(\d+)", x.get("r") or "")
    if m:
        n, reps = int(m.group(1)), int(m.group(2))
        x["tap"] = True
        return sides * n * (reps * 3.5 + 5) + (n - 1) * 45
    if x.get("r") and not x.get("breath"):
        x["tap"] = True      # repetições: avança quando a pessoa toca "feito"
    return sides * x["t"]


def minutes(sess):
    return max(1, round(sum(step_time(x) for x in sess["steps"]) / 60))


for s in SESSIONS:
    s["minutes"] = minutes(s)
OUT.write_text(json.dumps({"updated": "2026-11", "cats": CATS, "goals": GOALS, "sessions": SESSIONS}, ensure_ascii=False, indent=1))
print(len(SESSIONS), "sessões:", ", ".join(f"{s['id']} {s['minutes']}min" for s in SESSIONS))
