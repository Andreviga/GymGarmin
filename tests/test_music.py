"""Música no treino: músicas da pessoa no Spotify, BPM pelo Deezer/ReccoBeats, playlists por momento."""
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from gymgarmin import db, music

# biblioteca falsa: nome → (bpm no Deezer, isrc)
SONGS = {
    "lenta": (72, "BR001"), "media": (118, "BR002"), "treino": (132, "BR003"), "pesada": (140, "BR004"),
    "rapida": (168, "BR005"), "corrida": (162, "BR006"), "metade": (81, "BR007"), "sem_bpm": (0, "BR008"),
    "turbo": (176, "BR009"), "aquece": (108, "BR010"), "calma2": (88, "BR011"), "treino2": (145, "BR012"), "passo": (160, "BR013"),
    "novo1": (172, "BR100"), "novo2": (175, "BR101"), "novo3": (178, "BR102"),
}


def _track(name):
    return {"id": name, "name": name.title(), "uri": f"spotify:track:{name}", "type": "track", "duration_ms": 200000,
            "artists": [{"name": "Artista"}], "external_ids": {"isrc": SONGS[name][1]}}


class FakeNet:
    def __init__(self, premium=True):
        self.calls, self.playlists, self.premium = [], {}, premium

    def __call__(self, method, url, headers=None, body=None, timeout=12):
        self.calls.append((method, url))
        u = urllib.parse.urlparse(url)
        if u.netloc == "accounts.spotify.com":
            return 200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
        if u.netloc == "api.reccobeats.com":
            return 500, None                                    # fora do ar: tem de funcionar só com o Deezer
        if u.netloc == "api.deezer.com":
            isrc = u.path.split("isrc:")[-1]
            bpm = next((b for b, i in SONGS.values() if i == isrc), 0)
            return 200, {"id": 1, "bpm": bpm}
        p = u.path.replace("/v1", "")
        if p == "/me":
            return 200, {"id": "andre", "display_name": "André", "product": "premium" if self.premium else "free"}
        if p == "/me/top/tracks":
            rng = urllib.parse.parse_qs(u.query)["time_range"][0]
            own = [n for n in SONGS if not n.startswith("novo")]
            names = own[:8] if rng == "short_term" else own[4:]
            return 200, {"items": [_track(n) for n in names]}
        if p == "/me/top/artists":
            return 200, {"items": [{"name": "Banda", "genres": ["classic rock", "sertanejo universitario"]}]}
        if p == "/search":
            qs = urllib.parse.parse_qs(u.query)
            self.calls.append(("SEARCH", qs["q"][0]))
            return 200, {"tracks": {"items": [_track(n) for n in ("novo1", "novo2", "novo3")] if qs.get("offset") == ["0"] else []}}
        if p == "/me/tracks":
            return 200, {"items": [{"track": _track("calma2")}]}
        if p == "/me/player/recently-played":
            return 200, {"items": []}
        if p == "/me/playlists" and method == "POST":
            pid = f"pl{len(self.playlists) + 1}"
            self.playlists[pid] = json.loads(body)["name"]
            return 201, {"id": pid}
        if p.startswith("/playlists/") and p.endswith("/items") and method == "PUT":
            self.playlists[p.split("/")[2] + ":items"] = json.loads(body)["uris"]
            return 200, {"snapshot_id": "x"}
        if p.startswith("/me/player"):
            if not self.premium:
                return 403, {"error": {"status": 403, "message": "Premium required", "reason": "PREMIUM_REQUIRED"}}
            if p == "/me/player/currently-playing":
                return 200, {"is_playing": True, "item": _track("pesada")}
            return 204, None
        return 404, {"error": {"status": 404, "message": "not found"}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "m.sqlite3"))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid123")
    db.init()
    net = FakeNet()
    monkeypatch.setattr(music, "_http", net)
    from gymgarmin.app import app
    with TestClient(app) as c:
        yield c, net


def test_bpm_por_momento():
    cad = 162
    assert music._fit(132, "musculacao", cad) is not None and music._fit(72, "musculacao", cad) is None
    assert music._fit(81, "corrida_leve", cad) == 0 and music._fit(162, "corrida_leve", cad) == 0   # metade também serve
    assert music._fit(150, "corrida_leve", cad) is None                                             # fora do ritmo
    assert music._fit(72, "calma", cad) is not None and music._fit(140, "calma", cad) is None
    assert music._fit(176, "tiros", cad) is not None                                                 # 162 + 12 = 174
    assert music.cadence_target([133, 158, 164, 166]) == 164    # corre-caminha (133) não puxa a passada para baixo
    assert music.cadence_target([]) == music.DEFAULT_CADENCE


def test_conectar_ler_musicas_e_montar_playlists(env):
    c, net = env
    st = c.get("/api/music").json()
    assert st["configured"] and not st["connected"] and st["redirect_uri"].endswith("/spotify-callback")
    r = c.get("/spotify-callback?code=abc&state=xyz", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/#musica?") and "code=abc" in r.headers["location"]
    assert c.post("/api/music/connect", json={"code": "abc", "verifier": "v", "redirect_uri": st["redirect_uri"]}).json()["product"] == "premium"
    out = c.post("/api/music/scan", json={"refresh": True}).json()
    own = [n for n in SONGS if not n.startswith("novo")]
    assert out["built"] and out["total"] == len(own) and out["known"] == len(own) - 1     # "sem_bpm" fica de fora
    st = c.get("/api/music").json()
    modes = {m["id"]: m for m in st["modes"]}
    assert st["connected"] and st["premium"] and modes["musculacao"]["url"]
    assert "HAX · Musculação" in net.playlists.values()
    uris = {k.split(":")[0]: v for k, v in net.playlists.items() if k.endswith(":items")}
    pid = modes["musculacao"]["url"].rsplit("/", 1)[-1]
    assert set(uris[pid]) == {"spotify:track:treino", "spotify:track:pesada", "spotify:track:treino2"}
    pid_cal = modes["calma"]["url"].rsplit("/", 1)[-1]
    assert set(uris[pid_cal]) == {"spotify:track:lenta", "spotify:track:calma2", "spotify:track:metade"}
    assert modes["aquecimento"]["n"] == 2 and "url" not in modes["aquecimento"]   # menos de 3 músicas: não monta (avisa)
    # corrida leve: 162 e 81 (metade) entram; 168 não
    run = set(uris[modes["corrida_leve"]["url"].rsplit("/", 1)[-1]])
    assert {"spotify:track:corrida", "spotify:track:metade"} <= run and "spotify:track:rapida" not in run
    # tocar, mais gás e o que está tocando (com o BPM)
    assert c.post("/api/music/play", json={"mode": "musculacao"}).json()["ok"]
    g = c.post("/api/music/boost", json={}).json()
    assert g["bpm"] >= 145
    assert c.get("/api/music/now").json() == {"name": "Pesada", "artist": "Artista", "bpm": 140, "playing": True}
    # cadência escolhida pela pessoa muda a corrida
    c.put("/api/music/settings", json={"cadence": 176, "auto": False})
    st = c.get("/api/music").json()
    assert st["cadence"] == 176 and st["auto"] is False


def test_sem_premium_avisa_e_abre_a_playlist(env, monkeypatch):
    c, net = env
    net.premium = False
    c.post("/api/music/connect", json={"code": "abc", "verifier": "v", "redirect_uri": "x"})
    c.post("/api/music/scan", json={"refresh": True})
    r = c.post("/api/music/play", json={"mode": "musculacao"})
    assert r.status_code == 403 and "Premium" in r.json()["detail"]
    assert c.post("/api/music/play", json={"mode": "xx"}).status_code == 400
    c.post("/api/music/disconnect")
    assert not c.get("/api/music").json()["connected"]


def test_estilos_favoritos_completam_as_playlists(env):
    c, net = env
    c.post("/api/music/connect", json={"code": "abc", "verifier": "v", "redirect_uri": "x"})
    c.post("/api/music/scan", json={"refresh": True})
    st = c.get("/api/music").json()
    g = {x["id"]: x for x in st["genres"]}
    assert g["rock"]["seen"] and g["sertanejo"]["seen"] and not g["funk"]["seen"]      # sugestão pelo que já ouve
    tiros0 = next(m for m in st["modes"] if m["id"] == "tiros")
    assert tiros0["new"] == 0
    c.put("/api/music/settings", json={"genres": ["rock", "xx"], "mix": "completar"})
    c.post("/api/music/scan", json={})
    assert any(k == "SEARCH" and 'genre:"rock"' in q for k, q in net.calls)
    st = c.get("/api/music").json()
    tiros = next(m for m in st["modes"] if m["id"] == "tiros")
    assert tiros["new"] == 3 and tiros["n"] == tiros0["n"] + 3 and st["discovered"] == 3
    rows = music.pick("tiros", 162)
    assert [r["src"] for r in rows][: tiros0["n"]] == [r["src"] for r in rows if r["src"] != "descoberta"]   # as suas primeiro
    # "só as minhas": novidades saem
    c.put("/api/music/settings", json={"mix": "so_minhas"})
    assert next(m for m in c.get("/api/music").json()["modes"] if m["id"] == "tiros")["new"] == 0
    # nova busca do mesmo estilo só depois de 7 dias
    n = sum(1 for k, _ in net.calls if k == "SEARCH")
    c.put("/api/music/settings", json={"mix": "completar"})
    c.post("/api/music/scan", json={})
    assert sum(1 for k, _ in net.calls if k == "SEARCH") == n


def test_estilo_nichado_busca_pelo_nome_quando_o_genero_falha_e_parecidas(env, monkeypatch):
    c, net = env
    orig = net.__call__

    def fake(method, url, headers=None, body=None, timeout=12):
        u = urllib.parse.urlparse(url)
        if u.path.endswith("/search"):
            q = urllib.parse.parse_qs(u.query)["q"][0]
            net.calls.append(("SEARCH", q))
            assert "market" not in urllib.parse.parse_qs(u.query)         # pedia permissão a mais
            if q.startswith("genre:"):
                return 400, {"error": {"status": 400, "message": "bad"}}
            if q in ("hard techno", "schranz") and urllib.parse.parse_qs(u.query)["offset"] == ["0"]:
                return 200, {"tracks": {"items": [_track("novo1"), _track("novo2")]}}
            return 200, {"tracks": {"items": []}}
        if u.netloc == "api.reccobeats.com" and "recommendation" in u.path:
            return 200, {"content": [{"href": "https://open.spotify.com/track/novo3", "trackTitle": "Novo3", "artists": [{"name": "X"}], "isrc": "BR102"}]}
        return orig(method, url, headers, body, timeout)

    monkeypatch.setattr(music, "_http", fake)
    c.post("/api/music/connect", json={"code": "abc", "verifier": "v", "redirect_uri": "x"})
    c.post("/api/music/scan", json={"refresh": True})
    c.put("/api/music/settings", json={"genres": ["hardtechno", "custom:  Schranz ", "custom:x", "parecidas", "funk"], "mix": "completar"})
    st = c.get("/api/music").json()
    assert [g for g in st["genres"] if g["on"]] and any(g["id"] == "custom:schranz" and g["group"] == "livre" for g in st["genres"])
    assert not any(g["id"] == "custom:x" for g in st["genres"])                     # curto demais
    assert next(g for g in st["genres"] if g["id"] == "hardtechno")["group"] == "nicho"
    c.post("/api/music/scan", json={})
    d = c.get("/api/music").json()["disc_diag"]
    assert d["hardtechno"]["found"] == 2 and d["hardtechno"]["how"] == "texto"
    assert d["parecidas"]["found"] == 1 and d["funk"]["found"] == 0
    lib = music._lib()
    assert "funk" not in lib["disc_at"] and "hardtechno" in lib["disc_at"]          # vazio tenta de novo depois
    assert lib["tracks"]["novo3"]["bpm"] == 178
