"""Login do Google (state em cookie, troca no servidor, tokens criptografados) e playlists no YouTube."""
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from gymgarmin import db, music, youtube

PROD = "https://haxfit.vercel.app"


def _lib():
    tracks = {}
    for k in range(12):          # musculação 125–150
        tracks[f"m{k}"] = {"name": f"Musc {k}", "artist": "A", "uri": f"spotify:track:m{k}", "bpm": 130 + k, "aff": 2, "src": "top"}
    for k in range(5):           # calma
        tracks[f"c{k}"] = {"name": f"Calma {k}", "artist": "B", "uri": f"spotify:track:c{k}", "bpm": 70 + k, "aff": 2, "src": "top"}
    tracks["sem"] = {"name": "Sem video", "artist": "C", "uri": "spotify:track:sem", "bpm": 131.5, "aff": 2, "src": "top"}
    return {"tracks": tracks, "updated": 1}


class FakeGoogle:
    def __init__(self):
        self.calls, self.items, self.quota_after, self.refresh_status = [], {}, None, 200
        self.searches = 0

    def __call__(self, method, url, headers=None, body=None, timeout=12):
        u = urllib.parse.urlparse(url)
        self.calls.append((method, u.netloc + u.path))
        if u.netloc == "oauth2.googleapis.com" and u.path == "/token":
            form = urllib.parse.parse_qs(body.decode())
            assert form["client_secret"] == ["segredo"] and form["client_id"] == ["cid.apps.googleusercontent.com"]
            if form["grant_type"] == ["authorization_code"]:
                assert form["redirect_uri"] == [f"{PROD}/google-callback"] and form["code"] == ["abc"]
                return 200, {"access_token": "AT1", "refresh_token": "RT-segredo", "expires_in": 3600, "scope": youtube.SCOPE}
            assert form["refresh_token"] == ["RT-segredo"]
            if self.refresh_status != 200:
                return 400, {"error": "invalid_grant"}
            return 200, {"access_token": "AT2", "expires_in": 3600}
        if u.netloc == "oauth2.googleapis.com" and u.path == "/revoke":
            assert urllib.parse.parse_qs(body.decode())["token"] == ["RT-segredo"]
            return 200, {}
        p = u.path.replace("/youtube/v3", "")
        assert headers["Authorization"].startswith("Bearer AT")
        if p == "/search":
            self.searches += 1
            if self.quota_after is not None and self.searches > self.quota_after:
                return 403, {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
            q = urllib.parse.parse_qs(u.query)
            assert q["part"] == ["snippet"] and q["type"] == ["video"] and q["maxResults"] == ["1"]
            text = q["q"][0]
            return 200, {"items": [] if text.lower().startswith("sem video") else [{"id": {"videoId": "v" + str(abs(hash(text)) % 10000)}}]}
        if p == "/playlists":
            assert urllib.parse.parse_qs(u.query)["part"] == ["snippet,status"]
            pid = f"PL{len(self.items) + 1}"
            self.items[pid] = []
            assert json.loads(body)["status"]["privacyStatus"] == "private"
            return 200, {"id": pid}
        if p == "/playlistItems":
            sn = json.loads(body)["snippet"]
            self.items[sn["playlistId"]].append(sn["resourceId"]["videoId"])
            return 200, {"id": "x"}
        return 404, None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "y.sqlite3"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo")
    db.init()
    db.kv_set(music.LIB_KEY, json.dumps(_lib()))
    net = FakeGoogle()
    monkeypatch.setattr(music, "_http", net)
    from gymgarmin.app import app
    with TestClient(app, base_url=PROD) as c:
        yield c, net


def _connect(c):
    r = c.get("/api/google/login", follow_redirects=False)
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "accounts.google.com" and q["redirect_uri"] == [f"{PROD}/google-callback"]
    assert q["scope"] == [youtube.SCOPE] and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["include_granted_scopes"] == ["true"] and q["response_type"] == ["code"]
    ck = r.headers["set-cookie"]
    assert "HttpOnly" in ck and "Max-Age=600" in ck and "samesite=lax" in ck.lower()
    assert "segredo" not in r.headers["location"] and "segredo" not in ck
    cb = c.get("/google-callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False)
    assert cb.status_code == 302 and cb.headers["location"] == "/#musica?youtube=conectado"
    return cb


def test_login_completo_e_tokens_criptografados(env):
    c, net = env
    st = c.get("/api/youtube").json()
    assert st["configured"] and st["available"] and not st["connected"]
    assert "segredo" not in json.dumps(st)
    _connect(c)
    assert c.get("/api/youtube").json()["connected"]
    with db.conn() as k:
        row = dict(k.execute("SELECT * FROM google_tokens WHERE id = 1").fetchone())
    assert row["access_token"] == "AT1" and row["scope"] == youtube.SCOPE and row["expires_at"] > time.time() + 3000
    assert "RT-segredo" not in row["refresh_token_enc"]                       # criptografado
    assert youtube.access_token() == "AT1"                                    # ainda válido: não renova
    assert sum(1 for m, x in net.calls if x.endswith("/token")) == 1


def test_state_errado_ou_sem_cookie_da_erro_e_apaga_cookie(env):
    c, net = env
    r = c.get("/api/google/login", follow_redirects=False)
    bad = c.get("/google-callback", params={"code": "abc", "state": "outro"}, follow_redirects=False)
    assert bad.status_code == 400 and "gg_google_state=" in bad.headers.get("set-cookie", "") and 'Max-Age=0' in bad.headers["set-cookie"]
    c.cookies.clear()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert c.get("/google-callback", params={"code": "abc", "state": q["state"][0]}, follow_redirects=False).status_code == 400
    assert not youtube.connected()
    assert not any(x.endswith("/token") for _, x in net.calls)               # nada foi trocado


def test_fora_da_producao_nao_oferece_login(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "p.sqlite3"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo")
    from gymgarmin.app import app
    with TestClient(app, base_url="https://gymgarmin-git-x.vercel.app") as c:
        assert c.get("/api/youtube").json()["available"] is False
        r = c.get("/api/google/login", follow_redirects=False)
        assert r.headers["location"] == "/#musica?youtube=indisponivel"


def test_renova_e_invalid_grant_desconecta(env):
    c, net = env
    _connect(c)
    with db.conn() as k:
        k.execute("UPDATE google_tokens SET expires_at = ? WHERE id = 1", (time.time() + 30,))   # faltam 30 s
    assert youtube.access_token() == "AT2"
    with db.conn() as k:
        k.execute("UPDATE google_tokens SET expires_at = ? WHERE id = 1", (time.time() - 5,))
    net.refresh_status = 400
    with pytest.raises(youtube.YTError):
        youtube.access_token()
    st = c.get("/api/youtube").json()
    assert not st["connected"] and "desconectado" in st["note"]


def test_desconectar_revoga_e_apaga(env):
    c, net = env
    _connect(c)
    assert c.post("/api/youtube/disconnect").json()["ok"]
    assert ("POST", "oauth2.googleapis.com/revoke") in net.calls and not youtube.connected()


def test_playlist_pela_api_com_cache_e_nao_encontradas(env):
    c, net = env
    _connect(c)
    body = {"nome": "Treino de perna", "musicas": [{"artista": "A", "titulo": "Um"}, {"artista": "C", "titulo": "Sem video"},
                                                   {"artista": "B", "titulo": "Dois"}]}
    r = c.post("/api/youtube/playlist", json=body).json()
    assert r["url"].startswith("https://music.youtube.com/playlist?list=PL") and r["adicionadas"] == 2
    assert r["nao_encontradas"] == [{"artista": "C", "titulo": "Sem video"}]
    assert net.items[r["playlist_id"]] == [youtube.find_video_id("Um A"), youtube.find_video_id("Dois B")]   # na ordem
    n = net.searches
    c.post("/api/youtube/playlist", json=body)
    assert net.searches == n                                                  # tudo do cache (inclusive o não achado)
    assert c.post("/api/youtube/playlist", json={"nome": "x", "musicas": []}).status_code == 400


def test_cota_acaba_mensagem_clara_e_parcial(env, monkeypatch):
    c, net = env
    _connect(c)
    # cota do Google acaba na busca: erro claro; o que já foi achado fica no cache
    net.quota_after = 1
    r = c.post("/api/youtube/playlist", json={"nome": "P", "musicas": [{"artista": "A", "titulo": "Um"}, {"artista": "A", "titulo": "Dois"}]})
    assert r.status_code == 429 and "limite diário" in r.json()["detail"]
    assert youtube._cached("Um A")[0]
    # limite próprio do app acaba no meio dos inserts: playlist criada com o que deu + pendentes
    monkeypatch.setattr(youtube, "_quota_day", lambda: "2099-01-01")
    net.quota_after = None
    monkeypatch.setattr(youtube, "DAILY_UNITS", 2 * 100 + 50 + 50)            # 2 buscas + playlist + 1 vídeo
    r = c.post("/api/youtube/playlist", json={"nome": "Q", "musicas": [{"artista": "B", "titulo": "Tres"}, {"artista": "B", "titulo": "Quatro"}]}).json()
    assert r["adicionadas"] == 1 and r["pendentes"] == [{"artista": "B", "titulo": "Quatro"}] and "limite diário" in r["aviso"]


def test_playlists_hax_automaticas_enchem_revezando(env, monkeypatch):
    c, net = env
    _connect(c)
    monkeypatch.setattr(music, "connected", lambda: True)
    out = c.post("/api/youtube/sync", json={}).json()
    pl = {p["id"]: p for p in out["playlists"]}
    assert pl["musculacao"]["done"] == 12 and pl["musculacao"]["total"] == 13 and pl["calma"]["done"] == 5
    assert out["pending"] == 0 and pl["musculacao"]["url"].startswith("https://music.youtube.com/playlist?list=")
    n = net.searches
    c.post("/api/youtube/sync", json={})
    assert net.searches == n


def test_pagina_de_privacidade_publica(env):
    c, _ = env
    r = c.get("/privacidade")
    assert r.status_code == 200 and "Política de Dados do Usuário" in r.text and "HAX" in r.text
