"""YouTube Music: login do Google, as playlists HAX enchendo aos poucos (cota diária) e o vídeo achado guardado."""
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from gymgarmin import db, music, youtube


def _lib():
    tracks = {}
    for k in range(12):          # musculação 125–150
        tracks[f"m{k}"] = {"name": f"Musc {k}", "artist": "A", "uri": f"spotify:track:m{k}", "bpm": 130 + k, "aff": 2, "src": "top"}
    for k in range(5):           # calma
        tracks[f"c{k}"] = {"name": f"Calma {k}", "artist": "B", "uri": f"spotify:track:c{k}", "bpm": 70 + k, "aff": 2, "src": "top"}
    tracks["sem"] = {"name": "Sem video", "artist": "C", "uri": "spotify:track:sem", "bpm": 131.5, "aff": 2, "src": "top"}
    return {"tracks": tracks, "updated": 1}


class FakeYT:
    def __init__(self):
        self.calls, self.items, self.quota_fail_after = [], {}, None

    def __call__(self, method, url, headers=None, body=None, timeout=12):
        u = urllib.parse.urlparse(url)
        self.calls.append((method, u.path))
        if u.netloc == "oauth2.googleapis.com":
            form = urllib.parse.parse_qs(body.decode())
            assert form["client_secret"] == ["segredo"]                    # o segredo só vai para o Google
            return 200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
        p = u.path.replace("/youtube/v3", "")
        if self.quota_fail_after is not None and sum(1 for m, x in self.calls if x.endswith("/search")) > self.quota_fail_after:
            return 403, {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
        if p == "/playlists":
            pid = f"PL{len(self.items) + 1}"
            self.items[pid] = []
            return 200, {"id": pid}
        if p == "/search":
            q = urllib.parse.parse_qs(u.query)["q"][0]
            assert urllib.parse.parse_qs(u.query)["videoCategoryId"] == ["10"]
            return 200, {"items": [] if q.startswith("Sem video") else [{"id": {"videoId": "v_" + q.split()[0] + q.split()[1]}}]}
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
    net = FakeYT()
    monkeypatch.setattr(music, "_http", net)
    from gymgarmin.app import app
    with TestClient(app) as c:
        yield c, net


def test_login_e_playlists_enchem_revezando(env):
    c, net = env
    st = c.get("/api/youtube").json()
    assert st["configured"] and not st["connected"] and st["redirect_uri"].endswith("/google-callback")
    assert "segredo" not in json.dumps(st)
    r = c.get("/google-callback?code=abc&state=s1", follow_redirects=False)
    assert r.headers["location"].startswith("/#musica?") and "yt_code=abc" in r.headers["location"]
    assert c.post("/api/youtube/connect", json={"code": "abc", "verifier": "v", "redirect_uri": "x"}).json()["ok"]
    out = c.post("/api/youtube/sync", json={}).json()
    pl = {p["id"]: p for p in out["playlists"]}
    assert pl["musculacao"]["done"] == 12 and pl["musculacao"]["total"] == 13          # "Sem video" não achou
    assert pl["calma"]["done"] == 5 and pl["musculacao"]["url"].startswith("https://music.youtube.com/playlist?list=")
    assert out["pending"] == 0
    n_search = sum(1 for m, x in net.calls if x.endswith("/search"))
    c.post("/api/youtube/sync", json={})                                                 # nada novo: não busca de novo
    assert sum(1 for m, x in net.calls if x.endswith("/search")) == n_search


def test_cota_do_dia_para_e_continua_depois(env, monkeypatch):
    c, net = env
    c.post("/api/youtube/connect", json={"code": "abc", "verifier": "v", "redirect_uri": "x"})
    net.quota_fail_after = 4
    out = c.post("/api/youtube/sync", json={}).json()
    assert out["stopped"] == "cota" and out["today_left"] == 0 and out["pending"] > 0
    done1 = sum(p["done"] for p in out["playlists"])
    assert 1 <= done1 <= 4
    # revezou: as duas playlists ganharam músicas antes da cota acabar
    assert all(p["done"] >= 1 for p in out["playlists"] if p["id"] in ("musculacao", "calma"))
    # dia seguinte (cota zerada): continua de onde parou
    net.quota_fail_after = None
    monkeypatch.setattr(youtube, "_quota_day", lambda: "2099-01-01")
    out2 = c.post("/api/youtube/sync", json={}).json()
    assert out2["pending"] == 0 and sum(p["done"] for p in out2["playlists"]) == 17


def test_limite_proprio_de_unidades(env, monkeypatch):
    c, net = env
    c.post("/api/youtube/connect", json={"code": "abc", "verifier": "v", "redirect_uri": "x"})
    monkeypatch.setattr(youtube, "DAILY_UNITS", 100 + 2 * 50 + 2 * 150)   # 2 playlists + 2 músicas
    out = c.post("/api/youtube/sync", json={}).json()
    assert out["stopped"] == "cota" and sum(p["done"] for p in out["playlists"]) == 2
