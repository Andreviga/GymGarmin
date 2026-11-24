# Prompt para o navegador: criar o login do Google (YouTube Music) do app HAX

Cole o texto abaixo no Claude do navegador (Claude in Chrome), logado na sua conta Google e na Vercel.

---

Preciso criar as credenciais de login do Google para o meu app pessoal de treino **HAX** (https://haxfit.vercel.app),
para ele montar playlists no meu YouTube / YouTube Music. Faça tudo na minha conta, passo a passo, e me mostre antes
de clicar em qualquer botão de pagamento (não deve haver nenhum: tudo aqui é gratuito).

1. **Google Cloud Console** (https://console.cloud.google.com):
   - Crie um projeto novo chamado `HAX` (sem organização).
   - Em **APIs e serviços → Biblioteca**, ative a **YouTube Data API v3**.

2. **Tela de consentimento OAuth** (Google Auth Platform → Branding / Público-alvo):
   - Tipo de usuário: **Externo**. Nome do app: `HAX`. E-mail de suporte e de contato: o meu.
   - Página inicial do app: `https://haxfit.vercel.app`.
   - Deixe o app em **Teste** (não publique, não peça verificação).
   - Em **Usuários de teste**, adicione o meu e-mail do Google (o mesmo do YouTube).
   - Em **Acesso a dados / Escopos**, adicione o escopo `https://www.googleapis.com/auth/youtube`.

3. **Credenciais → Criar credenciais → ID do cliente OAuth**:
   - Tipo: **Aplicativo da Web**. Nome: `HAX web`.
   - Origens JavaScript autorizadas: `https://haxfit.vercel.app`
   - URIs de redirecionamento autorizados: `https://haxfit.vercel.app/google-callback` (exatamente assim, sem barra no fim).
   - Crie e anote o **ID do cliente** e a **Chave secreta do cliente**.

4. **Vercel** (https://vercel.com → time `andre-camargos-projects` → projeto `gymgarmin` → Settings → Environment Variables):
   - Adicione `GOOGLE_CLIENT_ID` = o ID do cliente (ambientes Production e Preview).
   - Adicione `GOOGLE_CLIENT_SECRET` = a chave secreta, marcada como **Sensitive** (Production e Preview).
   - **Não** faça redeploy; não mostre a chave secreta em lugar nenhum além desse campo.

5. No fim, me responda **só com o ID do cliente** (termina em `.apps.googleusercontent.com`) e a confirmação de que
   as duas variáveis foram salvas na Vercel. **Nunca escreva a chave secreta na resposta.**

---

Depois disso, mande o ID do cliente para o Claude do app: ele publica a versão nova e avisa para conectar o YouTube.
Para a Nathália usar também: em **Usuários de teste** (passo 2), adicione o e-mail do Google dela.
