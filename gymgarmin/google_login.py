"""Entrar com Google (OpenID Connect) na tela de login do app.

Reaproveita o cliente OAuth e o redirect do YouTube (https://haxfit.vercel.app/google-callback é o único cadastrado
no Google): o cookie de state diz a finalidade ("p": "login" ou "youtube") e o callback decide por ela.

O id_token vem direto do endpoint de token do Google, numa conexão TLS autenticada com o client secret; por isso
(OpenID Connect Core 3.1.3.7) basta conferir emissor, público, validade e e-mail verificado, sem baixar as chaves
de assinatura.
"""
from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.parse

from . import auth, db, music, youtube

SCOPE = "openid email profile"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
GOOGLE_ONLY_KEY = "google_only_password"   # conta criada pelo Google: a senha é aleatória (ninguém sabe)


class LoginError(RuntimeError):
    pass


def available(host: str | None) -> bool:
    return youtube.oauth_available(host)


def login_url(state: str) -> str:
    return youtube.AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": youtube.client_id(), "redirect_uri": youtube.REDIRECT_URI, "response_type": "code",
        "scope": SCOPE, "prompt": "select_account", "state": state})


def _b64json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def verify_id_token(id_token: str, now: float | None = None) -> dict:
    """Confere iss, aud, exp e email_verified. Devolve {sub, email, name}."""
    try:
        _, payload, _ = (id_token or "").split(".")
        c = _b64json(payload)
    except Exception as e:  # token malformado
        raise LoginError("A resposta do Google veio incompleta. Tente de novo.") from e
    if c.get("iss") not in ISSUERS:
        raise LoginError("A resposta não veio do Google.")
    aud = c.get("aud")
    if (aud if isinstance(aud, str) else None) != youtube.client_id() and not (isinstance(aud, list) and youtube.client_id() in aud):
        raise LoginError("A resposta do Google não é para este app.")
    if float(c.get("exp") or 0) < (now or time.time()):
        raise LoginError("O login do Google venceu. Tente de novo.")
    if c.get("email_verified") not in (True, "true"):
        raise LoginError("O e-mail dessa conta Google não está verificado.")
    email = (c.get("email") or "").strip().lower()
    if not c.get("sub") or "@" not in email:
        raise LoginError("O Google não informou o e-mail da conta.")
    name = (c.get("name") or c.get("given_name") or email.split("@")[0]).strip()
    return {"sub": str(c["sub"]), "email": email, "name": name[:60]}


def exchange(code: str) -> dict:
    """Troca o código por tokens (no servidor, com o client secret) e devolve os dados verificados do id_token."""
    st, d = music._http("POST", youtube.TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"},
                        youtube._form({"client_id": youtube.client_id() or "", "client_secret": youtube._client_secret() or "",
                                       "code": code, "grant_type": "authorization_code", "redirect_uri": youtube.REDIRECT_URI}))
    if st != 200 or not isinstance(d, dict) or not d.get("id_token"):
        err = (d or {}).get("error") if isinstance(d, dict) else None
        raise LoginError(f"O Google recusou o login ({err or st}). Tente de novo.")
    return verify_id_token(d["id_token"])


def account_for(claims: dict, signup_open: bool) -> dict:
    """Conta do login com Google:
    a) já ligada a este Google (google_sub) → ela;
    b) mesmo e-mail (verificado pelo Google) → liga o Google nela;
    c) nenhuma → cria, com as mesmas regras do cadastro (aberto e dentro do limite)."""
    acc = db.get_account_by_google_sub(claims["sub"])
    if acc:
        return acc
    acc = db.get_account_by_email(claims["email"])
    if acc:
        if acc.get("google_sub") and acc["google_sub"] != claims["sub"]:
            raise LoginError("Esta conta do HAX já está ligada a outra conta Google.")
        db.set_google_sub(acc["user_id"], claims["sub"])
        return db.get_account(acc["user_id"])
    if not auth.accounts_exist():
        raise LoginError("O app ainda não tem dono: crie a primeira conta pelo Perfil.")
    if not signup_open:
        raise LoginError("O cadastro está fechado. Peça para quem te mandou o app liberar.")
    acc = db.create_account(claims["email"], auth.hash_password(secrets.token_urlsafe(48)), claims["name"])
    db.set_google_sub(acc["user_id"], claims["sub"])
    with db.as_user(acc["user_id"]):
        db.save_profile({"name": claims["name"]})
    db.kv_set(GOOGLE_ONLY_KEY, "1", user_id=acc["user_id"])
    return db.get_account(acc["user_id"])


def google_only(user_id: int) -> bool:
    """Conta criada pelo Google que ainda não definiu senha (pode criar uma sem informar a atual)."""
    return db.kv_get(GOOGLE_ONLY_KEY, user_id=user_id) == "1"


def clear_google_only(user_id: int) -> None:
    db.kv_delete(GOOGLE_ONLY_KEY, user_id=user_id)
