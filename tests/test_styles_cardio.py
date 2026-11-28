"""Playlists por estilo parecido e cardio avulso com a música seguindo as fases do treino."""
import itertools
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from gymgarmin import cardio, clock, db, garmin_sync, music, youtube

CAD = 160


def _lib():
    """Biblioteca falsa: 15 eletrônicas pesadas e 13 de rock rápidas (tiros), 5 pop rápidas (poucas para virar
    playlist), 14 brasileiras na faixa da musculação e algumas para aquecer/acalmar."""
    t = {}

    def add(prefix, n, bpm0, fam, src="top"):
        for k in range(n):
            t[f"{prefix}{k}"] = {"name": f"{prefix.title()} {k}", "artist": "Banda", "uri": f"spotify:track:{prefix}{k}",
                                 "bpm": bpm0 + (k % 8), "energy": 0.8, "aff": 1, "src": src, "fam": fam, "ms": 180_000}
    add("pes", 15, 172, "hardcore")
    add("roc", 13, 170, "rock")
    add("pop", 5, 174, "pop")
    add("br", 14, 130, "br")
    add("aq", 10, 104, "pop")
    add("cal", 8, 70, "calma")
    for k in range(8):
        t[f"cal{k}"]["energy"] = 0.3
    for k in range(6):
        t[f"leve{k}"] = {"name": f"Leve {k}", "artist": "X", "uri": f"spotify:track:leve{k}", "bpm": CAD, "energy": 0.6, "aff": 1,
                         "src": "top", "fam": "pop", "ms": 200_000}
    return {"tracks": t, "updated": 1, "style_v": music.STYLE_V}


class FakeSpotify:
    def __init__(self):
        self.created, self.items, self.played, self.renamed = {}, {}, [], {}

    def __call__(self, method, path, body=None, params=None):
        if path == "/me/playlists" and method == "POST":
            pid = f"pl{len(self.created) + 1}"
            self.created[pid] = body["name"]
            return {"id": pid}
        if path.startswith("/playlists/") and method == "PUT" and "uris" in body:
            self.items[path.split("/")[2]] = body["uris"]
            return {}
        if path.startswith("/playlists/") and method == "PUT":      # renomear
            self.renamed[path.split("/")[2]] = body["name"]
            return {}
        if path == "/me/player/play":
            self.played.append(body["context_uri"])
            return {}
        return {}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "s.sqlite3"))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid123")
    db.init()
    db.kv_set(music.LIB_KEY, json.dumps(_lib()))
    sp = FakeSpotify()
    monkeypatch.setattr(music, "call", sp)
    monkeypatch.setattr(music, "connected", lambda: True)
    return sp


def test_familia_pelo_nome_do_genero():
    assert music.family_of("Techno/House") is None and music.family_of("Hard Rock") == "rock"   # Techno/House: decide o BPM
    assert music.family_of("hardstyle") == music.family_of("uptempo") == music.family_of("frenchcore") == "hardcore"
    assert music.family_of("Dubstep") == "bass" and music.family_of("hard techno") == "techno" and music.family_of("Hardcore") is None
    assert music.family_of("Rap/Hip Hop") == "rap" and music.family_of("Sertanejo") == "br" and music.family_of("Samba/Pagode") == "br"
    assert music.family_of("Dance") == "eletronica" and music.family_of("Pop") == "pop" and music.family_of("") is None


def test_estilo_das_minhas_musicas_pelo_album_no_deezer(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "c.sqlite3"))
    db.init()
    calls = []

    def net(method, url, headers=None, body=None, timeout=12):
        u = urllib.parse.urlparse(url)
        calls.append(u.path)
        if "isrc:" in u.path:
            return 200, {"id": 9, "album": {"id": 77 if u.path.endswith("A1") else 88}}
        if u.path == "/album/77":
            return 200, {"genres": {"data": [{"name": "Rock"}, {"name": "Hard Rock"}]}}
        if u.path == "/album/88":
            return 200, {"genres": {"data": [{"name": "Electro"}, {"name": "Techno/House"}]}}
        return 404, None
    monkeypatch.setattr(music, "_http", net)
    db.kv_set(music.LIB_KEY, json.dumps({"tracks": {
        "a": {"name": "A", "artist": "X", "uri": "u:a", "bpm": 150, "isrc": "A1", "src": "top"},
        "b": {"name": "B", "artist": "Y", "uri": "u:b", "bpm": 150, "isrc": "B1", "src": "curtida"},
        "c": {"name": "C", "artist": "Z", "uri": "u:c", "bpm": 150, "isrc": "A1", "src": "top"},
        "d": {"name": "D", "artist": "W", "uri": "u:d", "bpm": 170, "src": "descoberta", "genre": "hardtechno"}}}))
    assert music.classify(budget_s=5) == 0
    t = music._lib()["tracks"]
    assert (t["a"]["fam"], t["b"]["fam"], t["c"]["fam"], t["d"]["fam"]) == ("rock", "techno", "rock", "techno")
    assert calls.count("/album/77") == 1                       # álbum repetido não é buscado de novo
    assert not any("isrc" in c for c in calls if "D" in c)      # descoberta já vem com o estilo


def test_playlist_por_estilo_so_quando_tem_musicas_suficientes(env):
    sp = env
    r = music.build_playlists(CAD)
    assert r["done"]
    tiros = {k: v for k, v in r["styles"].items() if k.startswith("tiros|")}
    assert set(tiros) == {"tiros|hardcore", "tiros|rock"}          # pop rápido (5) não vira playlist
    names = set(sp.created.values())
    assert "HAX · Tiros · Hardcore, uptempo e hardstyle" in names and "HAX · Tiros · Rock e metal" in names and "HAX · Tiros" in names
    assert "HAX · Musculação · Brasileiras" in names
    pes = sp.items[tiros["tiros|hardcore"]["id"]]
    assert pes and all(u.startswith("spotify:track:pes") for u in pes)   # só esse estilo
    # tocar um estilo: usa a playlist dele e lembra a escolha para o próximo treino
    music.play("tiros", "rock")
    assert sp.played[-1] == f"spotify:playlist:{tiros['tiros|rock']['id']}"
    assert music.settings()["style_pref"]["tiros"] == "rock"
    sm = music.summary(CAD)
    t = next(m for m in sm["modes"] if m["id"] == "tiros")
    assert {y["fam"] for y in t["styles"]} == {"hardcore", "rock"} and t["style_pref"] == "rock"
    # refazer não cria playlists de novo (reaproveita os ids)
    n = len(sp.created)
    music.build_playlists(CAD)
    assert len(sp.created) == n


def test_playlist_antiga_de_eletronica_pesada_vira_a_de_hardcore(env):
    sp = env
    s = music.settings()
    s["style_ids"] = {"tiros|pesada": "velha"}
    music.save_settings(s)
    r = music.build_playlists(CAD)
    assert r["styles"]["tiros|hardcore"]["id"] == "velha" and sp.renamed["velha"] == "HAX · Tiros · Hardcore, uptempo e hardstyle"
    assert "HAX · Tiros · Hardcore, uptempo e hardstyle" not in sp.created.values()     # não criou outra


def _row(name, artist, bpm, src="top", genre=None, isrc=None):
    return {"name": name, "artist": artist, "uri": f"u:{name}", "bpm": bpm, "src": src, "energy": 0.9,
            **({"genre": genre} if genre else {}), **({"isrc": isrc} if isrc else {})}


def test_estilos_e_bpm_dos_casos_reais(tmp_path, monkeypatch):
    """Os erros que apareceram: uptempo lido pela metade caindo na musculação, hardstyle como 'eletrônica e dance',
    rawstyle como rock e banda de hardcore punk (da busca 'hardcore') como eletrônica."""
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "r.sqlite3"))
    db.init()
    albums = {"PUNK": ["Rock", "Punk"], "HS": ["Dance"], "RAW": ["Hardcore"], "HOUSE": ["Dance", "Techno/House"], "DUB": ["Electro"]}

    def net(method, url, headers=None, body=None, timeout=12):
        u = urllib.parse.urlparse(url)
        if "isrc:" in u.path:
            return 200, {"album": {"id": u.path.split("isrc:")[1]}}
        if u.path.startswith("/album/"):
            return 200, {"genres": {"data": [{"name": g} for g in albums[u.path.split("/")[-1]]]}}
        return 404, None
    monkeypatch.setattr(music, "_http", net)
    db.kv_set(music.LIB_KEY, json.dumps({"tracks": {
        "rem": _row("Uptempo Mo Distortion", "R.E.M.", 141.3, "descoberta", "custom:uptempo"),
        "purge": _row("UPTEMPO (240 BPM!) - Extended Version", "DJ HARDCORE PURGE", 119.1, "descoberta", "custom:uptempo"),
        "punk": _row("Jobs", "Capitalist Casualties", 165.7, "descoberta", "hardcore_el", "PUNK"),
        "hsd": _row("Rave Into Space", "Sub Zero Project", 150.1, "descoberta", "hardstyle"),
        "hs": _row("Destiny", "Sub Zero Project", 149.6, "curtida", None, "HS"),             # mesmo artista da busca hardstyle
        "atmoz": _row("Breathe", "Atmozfears, Devin Wild", 75.3, "curtida", None, "HS"),     # meio tempo, Deezer genérico
        "raw": _row("LOVE THE WAY", "Rooler, Sickmode", 160.0, "curtida", None, "RAW"),
        "lake": _row("Turn off the Lights", "Chris Lake", 125.0, "curtida", None, "HOUSE"),
        "dub": _row("Bubzstep", "12th Planet", 75.5, "curtida", None, "DUB")}}))
    music.save_settings({"genres": ["custom:uptempo", "hardcore_el", "hardstyle"]})   # os estilos que você marcou
    assert music.classify(budget_s=5) == 0
    t = music._lib()["tracks"]
    assert t["rem"]["fam"] == "hardcore" and t["rem"]["bpm"] > 250                    # uptempo de verdade: 282, não 141
    assert t["purge"]["bpm"] == 240 and t["purge"]["bpm_src"] == 119.1                  # BPM escrito no nome vale
    assert t["punk"]["fam"] == "rock"                                                   # punk da busca "hardcore" é rock
    assert t["hs"]["fam"] == "hardcore" and t["atmoz"]["fam"] == "hardcore" and t["atmoz"]["bpm"] == 150.6
    assert t["raw"]["fam"] == "hardcore" and t["lake"]["fam"] == "eletronica"
    lib = music._lib()
    assert music._fit(t["rem"]["bpm"], "musculacao", CAD, sure=True) is None and music._fit(t["purge"]["bpm"], "gas", CAD) is not None
    assert "rem" not in [r["id"] for r in music.pick("musculacao", CAD, lib)]           # sai da musculação...
    assert "purge" in [r["id"] for r in music.pick("gas", CAD, lib)]                    # ...e entra no mais gás
    # reclassificar (versão nova) parte do BPM original, sem dobrar duas vezes
    lib["style_v"] = 0
    music._save_lib(lib)
    music.classify(budget_s=5)
    assert music._lib()["tracks"]["purge"]["bpm"] == 240 and music._lib()["tracks"]["atmoz"]["bpm"] == 150.6


def test_sem_tempo_as_de_estilo_ficam_para_a_proxima(env):
    r = music.build_playlists(CAD, budget_s=-1)
    assert not r["done"] and not r["styles"] and music.settings().get("rules_v") != music.RULES_V
    assert music.build_playlists(CAD)["done"] and music.settings()["rules_v"] == music.RULES_V


def test_youtube_estilos_so_depois_das_misturas(env, monkeypatch):
    st = {"playlists": {}, "added": {}}
    monkeypatch.setattr(youtube, "_cached", lambda q: (False, None))
    w = youtube.wanted_lists(st, CAD)
    assert all("|" not in k for k in w)                        # misturas ainda incompletas
    for k, rows in list(w.items()):
        st["added"][k] = [r["id"] for r in rows]
    w = youtube.wanted_lists(st, CAD)
    assert "tiros|hardcore" in w and youtube._label("tiros|hardcore") == "Tiros · Hardcore, uptempo e hardstyle"


# ------------------------------------------------------------------ cardio avulso
PROF = {"age": 38, "training_months": 40, "endurance": {"anchors": {"run_vdot": 50, "ftp_w": 250}}}


@pytest.mark.parametrize("tpl", list(cardio.TEMPLATES))
def test_cada_modelo_vira_treino_e_fases_de_musica(tpl):
    w = cardio.build(tpl, None, PROF, machine="cardio_escada")
    tl = cardio.timeline(w["items"])
    assert w["items"] and tl and tl[0]["mode"] == "aquecimento"
    assert tl[-1]["mode"] in ("calma", "corrida_leve")         # termina acalmando (longão/leve terminam leve)
    total = sum(s["dur_s"] for s in tl)
    assert abs(total / 60 - w["minutes"]) <= 1.5               # as fases cobrem o treino todo
    assert all(a["mode"] != b["mode"] for a, b in itertools.pairwise(tl))   # fases iguais vizinhas viram uma


def test_tiros_curtos_viram_um_bloco_so_e_tiros_longos_alternam():
    tl = cardio.timeline(cardio.build("tiros400", 45, PROF)["items"])
    assert [s["mode"] for s in tl] == ["aquecimento", "tiros", "calma"]
    tl = cardio.timeline(cardio.build("noruegues", 46, PROF)["items"])
    assert [s["mode"] for s in tl].count("tiros") == 4 and "corrida_leve" in [s["mode"] for s in tl]
    tl = cardio.timeline(cardio.build("tabata", 25, PROF)["items"])
    assert [s["mode"] for s in tl] == ["aquecimento", "gas", "calma"]   # pausa de 1 min não troca a música


def test_sequencia_de_musicas_segue_as_fases(env):
    tl = cardio.timeline(cardio.build("tiros400", 45, PROF)["items"])
    seq = music.sequence(tl, CAD)
    by = {i: [x for x in seq if x["seg"] == i] for i in range(len(tl))}
    assert all(by[i] for i in by)                              # toda fase tem música
    assert all(x["bpm"] >= CAD + 6 for x in by[1])            # bloco de tiros: rápidas
    assert [x["start_s"] for x in seq] == sorted(x["start_s"] for x in seq) and len({x["id"] for x in seq}) == len(seq)
    pes = music.sequence(tl, CAD, fam="hardcore")
    assert all(x["fam"] == "hardcore" for x in pes if x["seg"] == 1)


def test_api_cardio_rascunho_relogio_e_musica(env, monkeypatch):
    sp = env
    pushed = []
    monkeypatch.setattr(garmin_sync, "push_workout", lambda payload, d: pushed.append((payload, d)) or {"workout_id": 5, "scheduled": d})
    from gymgarmin.app import app
    with TestClient(app) as c:
        o = c.get("/api/cardio/options").json()
        assert {x["id"] for x in o["cats"]} == {"corrida", "bike", "academia", "hiit"} and o["spotify"]
        assert {y["id"] for y in o["styles"]} >= {"hardcore", "rock", "br"}
        body = {"tpl": "tiros400", "minutes": 45, "env": "esteira", "fam": "hardcore"}
        pv = c.post("/api/cardio/preview", json=body).json()
        assert pv["title"].startswith("Tiros") and pv["music"]["tracks"] and pv["segments"][1]["mode"] == "tiros"
        dr = c.post("/api/cardio/draft", json=body).json()
        assert dr["generated"] and dr["items"][0]["endurance"]["env"] == "esteira" and dr["cardio"]["tpl"] == "tiros400"
        r = c.post("/api/cardio/music", json=body).json()
        assert r["played"] and sp.created[r["url"].rsplit("/", 1)[1]] == "HAX · Treino avulso"
        r2 = c.post("/api/cardio/music", json={**body, "tpl": "leve"}).json()
        assert r2["url"] == r["url"]                            # a mesma playlist, refeita
        assert c.post("/api/cardio/push", json={"tpl": "tabata", "minutes": 25}).status_code == 200
        assert pushed[-1][1] == clock.today().isoformat() and pushed[-1][0]["workoutName"].startswith("Tabata")
        f = c.post("/api/cardio/fit", json={"tpl": "bike_sweet", "minutes": 50})
        assert f.status_code == 200 and len(f.content) > 100 and ".fit" in f.headers["content-disposition"].lower()
        assert c.post("/api/cardio/preview", json={"tpl": "nada"}).status_code == 400
