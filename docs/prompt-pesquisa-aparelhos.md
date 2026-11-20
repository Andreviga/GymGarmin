# Prompt: pesquisar aparelhos nos fabricantes e ampliar o catálogo do HAX

Cole o texto abaixo em uma sessão do Claude Code aberta neste repositório (ou no Claude com pesquisa na web
ligada). Troque o que está entre `<< >>`. Se tiver fotos de aparelhos que não achou no app, anexe-as na mesma
mensagem: o prompt pede para identificá-las primeiro.

---

Quero ampliar o catálogo de aparelhos de musculação do meu app de treino (HAX). Pesquise a fundo, nos sites
oficiais dos fabricantes, e monte uma lista grande e sem repetições de aparelhos de academia, no formato de
dados que o app já usa.

**1. Antes de pesquisar, leia o que já existe** (para não repetir e para copiar o formato):
- `gymgarmin/data/machine_gallery.json` (galeria de aparelhos: 78 hoje)
- `gymgarmin/data/machines.json` (exercícios de aparelho que o gerador de treino usa: 40 hoje)
- `gymgarmin/data/exercises.json` (catálogo geral; os `id` de exercício vêm daqui)
- `gymgarmin/muscles.py` (nomes válidos de músculos) e `PATTERNS` em `gymgarmin/generator.py` (padrões de movimento)

**2. Onde pesquisar** (site oficial e catálogo em PDF de cada marca; abra as páginas de produto, não fique só
no resultado da busca):
- Mais comuns no Brasil: Movement, Righetto, Total Health, Kikos, Physicus, Lion Fitness, Matrix (Johnson),
  Life Fitness / Hammer Strength, Technogym, Cybex, Precor, Star Trac / Nautilus (Core Health & Fitness).
- Linhas "premium" e de biomecânica: Panatta, Gym80, Atlantis, Prime Fitness, Arsenal Strength, Watson,
  Hoist, Newtech, BH, Booty Builder, Rogue, Eleiko.
- `<< acrescente aqui as marcas da sua academia, se souber >>`

Para cada marca, percorra as linhas com pino de carga (selectorizadas), as de anilhas (plate loaded), as
articuladas/convergentes, cabos e polias (crossover, multiestação, functional trainer), Smith e racks,
bancos e os aparelhos de glúteo, abdômen e lombar.

**3. O que eu quero de cada aparelho** (um item por TIPO de aparelho; modelos equivalentes de marcas
diferentes viram um item só, com todas as marcas listadas):
- `name` em português como se fala na academia brasileira + `name_en` (nome usado pelos fabricantes) e
  outros nomes comuns (ex.: "cadeira abdutora", "abdutor", "hip abduction").
- `category` (uma de: pernas, gluteo, costas, peito, ombros, bracos, core, funcional, cardio).
- `brands`: marca e, quando houver, linha/modelo (ex.: "Hammer Strength Plate-Loaded", "Matrix Ultra").
- `availability_br`: comum, média ou rara, com o motivo em uma frase.
- `exercises`: todos os exercícios que dá para fazer nele; para cada um: nome, como fazer em uma frase,
  músculos principais e auxiliares (com os nomes de `muscles.py`), e o `id` do exercício equivalente em
  `exercises.json`/`machines.json` se já existir (`existing_exercise_ids`).
- `settings`: quais regulagens o aparelho tem (altura do banco, encosto, apoio dos pés, braço de
  alavanca, ponto de partida do movimento, pegadas) e como ajustar para a pessoa: é o que o usuário anota
  em "Minha academia".
- `tips`: 2 a 4 dicas de execução e o erro mais comum.
- `how_to_recognize`: como reconhecer o aparelho por foto (formato, onde fica a pilha de pesos ou as
  anilhas, posição do corpo), para eu identificar a partir da foto que o usuário tira.
- `photos` e `videos`: só links oficiais do fabricante ou com licença livre (Wikimedia Commons, vídeo
  oficial no YouTube). Diga a licença/origem de cada um. Não copie fotos sem licença.
- `sources`: os links exatos das páginas de onde saiu cada informação.

**4. Regras de qualidade**
- Não invente modelo, regulagem nem disponibilidade. Se não conseguir abrir a página do fabricante, diga
  qual foi e marque o item como "não confirmado" em vez de preencher de memória.
- Não repita o que já está em `machine_gallery.json`: para aparelhos que já existem, só acrescente marcas,
  exercícios ou regulagens que faltam (liste como "complemento do item X").
- Dê prioridade ao que existe em academias brasileiras comuns (Smart Fit, Bluefit, Selfit, academias de
  bairro) antes dos aparelhos raros.
- Meta: pelo menos 120 tipos de aparelho no total (os 78 atuais + novos), cobrindo todos os grupos
  musculares. Diga quantos encontrou por categoria e o que ficou de fora.

**5. Se houver fotos anexadas**: para cada foto, diga qual aparelho é (ou os 2 mais prováveis, com o que
os diferencia), que exercícios dá para fazer nele e a qual item da lista ele corresponde. Se for um
aparelho que não está no catálogo, crie o item novo.

**6. Entrega**
- Um arquivo JSON novo, `gymgarmin/data/machine_gallery_novos.json`, com os itens novos no MESMO formato
  de `machine_gallery.json` (mais os campos `settings` e `how_to_recognize`), e um resumo em texto: quantos
  itens por categoria, quais marcas foram cobertas, quais páginas não abriram.
- Para os aparelhos que pedem um exercício que ainda não existe no app, entregue também a entrada no formato
  de `machines.json` (com `primaryMuscles`, `secondaryMuscles`, `pattern`, `instructions`, `garmin`).
- Não altere os arquivos existentes nem publique nada: eu reviso antes. Rode `python3 -m pytest -q` no fim
  só para confirmar que nada quebrou.

Pesquise com calma e em profundidade: prefiro uma lista completa e conferida a uma resposta rápida.
