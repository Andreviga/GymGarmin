# Pendente

## Teste no app publicado (combinado com o André em 22/11/2026)

Gatilho: quando o André disser **"estou no computador"**.

1. Propor o ambiente pessoal **"HAX (app no ar)"**: mesmo acesso do Default + domínio permitido `haxfit.vercel.app`
   (create_environment com switch_session network_only). O app do celular não aprova esse pedido; no computador ele aprova.
   Se ele preferir, ele mesmo adiciona `haxfit.vercel.app` em Acesso à rede → Domínios permitidos do ambiente.
2. Criar a conta **"Teste (Claude)"** com um convite de uso único (sem usar a senha dele). Não colocar a senha da conta
   de teste no chat nem no repositório.
3. Percorrer o app publicado como alguém novo (Playwright, vários tamanhos de celular, tema claro e escuro):
   boas-vindas → Hoje → treino → salvar → nota de esforço → compartilhar → Bem-estar → Famosos → academia do dia → Perfil.
4. Posts de teste sempre como **"Só eu"** (não aparecem na Comunidade de ninguém).
5. No fim: relatar o que achou, corrigir, publicar e perguntar se apaga a conta de teste.

## Spotify (música no treino) — fazer no computador

O código está pronto; falta só criar o app no Spotify (5 minutos):

1. Entrar em https://developer.spotify.com/dashboard com a conta Spotify do André (precisa ser **Premium**:
   regra do Spotify desde fev/2026 para apps em modo de desenvolvimento).
2. **Create app**: nome "HAX", descrição "música no treino", **Redirect URI**: `https://haxfit.vercel.app/spotify-callback`,
   marcar **Web API**. Salvar.
3. Copiar o **Client ID** (não é segredo; o Client Secret NÃO é usado — login PKCE) e mandar no chat:
   eu coloco como `SPOTIFY_CLIENT_ID` na Vercel e publico.
4. Em **User Management**, adicionar o e-mail do Spotify da Nathália (até 5 pessoas por app).
5. No app: Perfil → 🎵 Música no treino → Conectar o Spotify.
