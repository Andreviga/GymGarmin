"""Contas do app: e-mail e senha, sessão em cookie assinado, convites.

- Senha: scrypt (biblioteca padrão) com sal por usuário.
- Sessão: cookie HttpOnly "gg_session" = "<user_id>.<expira>.<assinatura HMAC-SHA256>". Não precisa
  de tabela de sessões; trocar a senha invalida as sessões antigas (a assinatura inclui um pedaço
  do hash da senha).
- Segredo: AUTH_SECRET; sem ele, deriva de GARMIN_TOKEN_KEY (já configurada na Vercel); localmente,
  um arquivo na pasta de dados.
- Enquanto não existe nenhuma conta, o app funciona como antes (usuário 1, sem login): o dono
  cria a primeira conta e ela fica com os dados que já existem.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from contextvars import ContextVar

from . import db

_account: ContextVar[dict | None] = ContextVar("gymgarmin_account", default=None)


def set_account(acc: dict | None):
    return _account.set(acc)


def reset_account(tok) -> None:
    _account.reset(tok)


def current_account() -> dict | None:
    return _account.get()


_acting: ContextVar[int | None] = ContextVar("gymgarmin_acting_coach", default=None)


def set_acting(coach_id: int | None) -> None:
    _acting.set(coach_id)


def acting_coach() -> int | None:
    """Id do coach quando a requisição é dele agindo como o atleta."""
    return _acting.get()


COOKIE = "gg_session"
SESSION_DAYS = 90
MIN_PASSWORD = 8


def _secret() -> bytes:
    env = os.environ.get("AUTH_SECRET")
    if env:
        return env.encode()
    gk = os.environ.get("GARMIN_TOKEN_KEY")
    if gk:
        return hashlib.sha256(b"gymgarmin-auth:" + gk.encode()).digest()
    f = db.home_dir() / "auth_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip().encode()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt_b64, h_b64 = stored.split("$")
        h = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except Exception:  # noqa: BLE001
        return False


def validate_password(pw: str) -> None:
    if len(pw or "") < MIN_PASSWORD:
        raise ValueError(f"A senha precisa ter pelo menos {MIN_PASSWORD} caracteres.")


def _sig(uid: int, exp: int, pw_hash: str) -> str:
    msg = f"{uid}.{exp}.{pw_hash[-12:]}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:40]


def make_session(account: dict) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    uid = int(account["user_id"])
    return f"{uid}.{exp}.{_sig(uid, exp, account['pw_hash'])}"


def read_session(token: str | None) -> dict | None:
    """Conta do cookie, se a assinatura confere e não venceu."""
    if not token:
        return None
    try:
        uid_s, exp_s, sig = token.split(".")
        uid, exp = int(uid_s), int(exp_s)
    except ValueError:
        return None
    if exp < time.time():
        return None
    acc = db.get_account(uid)
    if not acc or not hmac.compare_digest(sig, _sig(uid, exp, acc["pw_hash"])):
        return None
    return acc


_has_accounts: set[str] = set()  # bancos que já têm conta (não volta a zero)


def accounts_exist() -> bool:
    """Depois que a primeira conta existe, o app passa a exigir login (fica em cache)."""
    key = db.database_url() or str(db.db_path())
    if key not in _has_accounts and db.count_accounts() > 0:
        _has_accounts.add(key)
    return key in _has_accounts


def reset_cache() -> None:
    _has_accounts.clear()


def new_invite_code() -> str:
    return secrets.token_urlsafe(9)


def new_ingest_token() -> tuple[str, str]:
    """Token pessoal para o Atalhos do iPhone / app Android: (token em claro, hash guardado)."""
    t = "gg_" + secrets.token_urlsafe(24)
    return t, hash_token(t)


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def public(acc: dict, name: str | None = None) -> dict:
    return {"id": acc["user_id"], "email": acc["email"], "is_admin": bool(acc.get("is_admin")),
            "name": name, "has_ingest_token": bool(acc.get("ingest_token"))}
