"""Entrar com Google: state de login × YouTube, id_token conferido, vínculo por e-mail e regras do cadastro."""
import base64
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from gymgarmin import auth, db, google_login, music, youtube

PROD = "https://haxfit.vercel.app"
CID = "cid.apps.googleusercontent.com"


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.assinatura"


def _claims(**kw):
    base = {"iss": "https://accounts.google.com", "aud": CID, "exp": time.time() + 600, "sub": "g-123",
            "email": "nova@gmail.com", "email_verified": True, "name": "Nova Pessoa"}
    return {**base, **kw}


class FakeToken:
    def __init__(self):
        self.claims, self.calls = _claims(), []

    def __call__(self, method, url, headers=None, body=None, timeout=12):
        u = urllib.parse.urlparse(url)
        self.calls.append(u.netloc + u.path)
        assert u.netloc == "oauth2.googleapis.com" and u.path == "/token"
        form = urllib.parse.parse_qs(body.decode())
        assert form["client_secret"] == ["segredo"] and form["redirect_uri"] == [f"{PROD}/google-callback"]
        return 200, {"access_token": "AT", "id_token": _jwt(self.claims), "expires_in": 3600}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "g.sqlite3"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo")
    monkeypatch.delenv("VERCEL", raising=False)
    auth.reset_cache()
    net = FakeToken()
    monkeypatch.setattr(music, "_http", net)
    from gymgarmin.app import app
    with TestClient(app, base_url=PROD) as c:
        assert c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"}).status_code == 200
        c.cookies.clear()
        yield c, net
    auth.reset_cache()


def _google(c):
    r = c.get("/api/auth/google", follow_redirects=False)
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "accounts.google.com" and q["scope"] == ["openid email profile"] and q["prompt"] == ["select_account"]
    assert "access_type" not in q and q["redirect_uri"] == [f"{PROD}/google-callback"] and "segredo" not in r.headers["location"]
    return c.get("/google-callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)


def test_conta_nova_pelo_google_e_depois_entra_de_novo(env):
    c, _ = env
    assert c.get("/api/public/signup").json() == {"open": True, "google": True}
    r = _google(c)
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = c.get("/api/auth/me").json()
    assert me["logged_in"] and me["user"]["email"] == "nova@gmail.com" and me["user"]["google"] and me["user"]["google_only"]
    acc = db.get_account_by_google_sub("g-123")
    assert acc and acc["pw_hash"].startswith("scrypt$")
    # cria uma senha sem informar a atual (ninguém sabe a aleatória); a sessão é renovada
    assert c.post("/api/account/password", json={"new": "minha-senha-1"}).json()["ok"]
    assert not google_login.google_only(acc["user_id"])
    assert c.post("/api/account/password", json={"new": "outra-senha-2"}).status_code == 400   # agora pede a atual
    # sai e entra de novo pelo Google: mesma conta (google_sub)
    c.cookies.clear()
    _google(c)
    assert c.get("/api/auth/me").json()["user"]["id"] == acc["user_id"] and db.count_accounts() == 2


def test_vincula_pelo_email_verificado(env):
    c, net = env
    net.claims = _claims(email="DONO@x.com", sub="g-dono")
    _google(c)
    me = c.get("/api/auth/me").json()
    assert me["user"]["email"] == "dono@x.com" and me["user"]["is_admin"] and not me["user"]["google_only"]
    assert db.get_account_by_email("dono@x.com")["google_sub"] == "g-dono" and db.count_accounts() == 1
    # outra conta Google com o mesmo e-mail não sequestra a conta já ligada
    c.cookies.clear()
    net.claims = _claims(email="dono@x.com", sub="g-outro")
    r = _google(c)
    assert r.status_code == 400 and "outra conta Google" in r.text and not c.get("/api/auth/me").json()["logged_in"]


@pytest.mark.parametrize("bad, msg", [
    ({"aud": "outro-app.apps.googleusercontent.com"}, "não é para este app"),
    ({"email_verified": False}, "não está verificado"),
    ({"exp": time.time() - 10}, "venceu"),
    ({"iss": "https://evil.example"}, "não veio do Google"),
])
def test_id_token_invalido_e_recusado(env, bad, msg):
    c, net = env
    net.claims = _claims(**bad)
    r = _google(c)
    assert r.status_code == 400 and msg in r.text and "gg_google_state=" in r.headers.get("set-cookie", "")
    assert not c.get("/api/auth/me").json()["logged_in"] and db.count_accounts() == 1


def test_cadastro_fechado_bloqueia_conta_nova_mas_nao_quem_ja_tem(env):
    c, net = env
    db.kv_set("signup", json.dumps({"open": False, "max": 30}), user_id=-1)
    r = _google(c)
    assert r.status_code == 400 and "cadastro está fechado" in r.text and db.count_accounts() == 1
    net.claims = _claims(email="dono@x.com", sub="g-dono")             # já tem conta: entra mesmo fechado
    assert _google(c).headers["location"] == "/"
    # limite máximo também fecha
    db.kv_set("signup", json.dumps({"open": True, "max": 1}), user_id=-1)
    c.cookies.clear()
    net.claims = _claims(email="nova2@gmail.com", sub="g-999")
    assert "cadastro está fechado" in _google(c).text


def test_state_de_login_e_de_youtube_nao_se_misturam(env):
    c, _ = env
    # o state do YouTube não serve para login e o de login não conecta o YouTube
    st_yt, ck_yt = youtube.new_state(1, purpose="youtube")
    st_lg, ck_lg = youtube.new_state(0, purpose="login")
    assert youtube.check_state(ck_yt, st_yt, purpose="youtube") == 1 and youtube.check_state(ck_yt, st_yt, purpose="login") is None
    assert youtube.check_state(ck_lg, st_lg, purpose="youtube") is None and youtube.read_state(ck_lg, st_lg)["p"] == "login"
    assert youtube.check_state(ck_lg, st_yt, purpose="login") is None                       # state de outro cookie
    # callback com state de login: entra (não grava tokens do YouTube)
    _google(c)
    with db.conn() as k:
        assert not k.execute("SELECT * FROM google_tokens").fetchall()


def test_fora_da_producao_sem_botao(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "p.sqlite3"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo")
    auth.reset_cache()
    from gymgarmin.app import app
    with TestClient(app, base_url="https://gymgarmin-git-x.vercel.app") as c:
        c.post("/api/auth/setup", json={"email": "dono@x.com", "password": "senha-forte"})
        assert c.get("/api/public/signup").json()["google"] is False
        assert c.get("/api/auth/google", follow_redirects=False).headers["location"] == "/"
    auth.reset_cache()


def test_migracao_roda_de_novo_sem_erro(env):
    db.init()
    db.init()
    with db.conn() as k:
        cols = [r[1] for r in k.execute("PRAGMA table_info(accounts)").fetchall()]
    assert cols.count("google_sub") == 1
