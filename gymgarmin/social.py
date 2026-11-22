"""Área social: a pessoa mostra que treinou (e o quê), os outros reagem e comentam.

Quem vê: "todos" (todas as pessoas com conta no app), "parceiros" (só quem é parceiro de treino) ou "eu" (só eu).
O post guarda um resumo do treino no momento em que foi compartilhado (título, tempo, séries, exercícios,
recordes) — editar o treino depois não muda o post. Cargas só aparecem se a pessoa quiser.
"""
from __future__ import annotations

import json

from . import db, history, my_gym, partners

VIS = ("todos", "parceiros", "eu")
REACTS = ("💪", "🔥", "👏")
MAX_COMMENT = 300
MAX_CAPTION = 500


def _snapshot(s: dict, weight_kg: float | None, show_loads: bool) -> dict:
    v = partners._session_view(s, weight_kg, detail=show_loads)
    items = []
    for it in v["items"]:
        row = {"exercise_id": it.get("exercise_id"), "name": it["name"], "kind": it["kind"]}
        if it["kind"] == "forca":
            row["n"] = it["n"]
            if show_loads:
                best = max(it.get("sets") or [], key=lambda x: (x.get("load_kg") or 0, x.get("reps") or 0), default=None)
                if best and best.get("load_kg"):
                    row["best"] = f"{best.get('reps') or '?'} × {best['load_kg']:g} kg"
        else:
            row["minutes"] = it.get("minutes")
            if it.get("km"):
                row["km"] = it["km"]
        items.append(row)
    try:
        prs = history.session_prs(db.list_sessions(limit=400), s, weight_kg)
    except Exception:  # noqa: BLE001
        prs = []
    return {"title": v["title"], "date": v["date"], "minutes": v["minutes"], "kcal": v["kcal"], "sets": v["sets"],
            "items": items[:14], "prs": [p.get("name") or p.get("exercise_id") for p in prs][:4],
            "with_partner": bool(v.get("with_partner"))}


def create(uid: int, session_id: int, caption: str = "", visibility: str = "todos", photo: str | None = None,
           show_loads: bool = False) -> dict:
    s = db.get_session(int(session_id), user_id=uid)
    if not s:
        raise LookupError("Treino não encontrado")
    vis = visibility if visibility in VIS else "todos"
    photo_id = None
    if photo:
        photo_id = my_gym.save_photo(photo, uid)
        if vis != "eu":
            my_gym._set_public(photo_id, True)   # quem vê o post vê a foto
    w = db.get_profile(uid).get("weight_kg")
    data = {"caption": (caption or "").strip()[:MAX_CAPTION], "photo": photo_id, "show_loads": bool(show_loads),
            "w": _snapshot(s, w, bool(show_loads))}
    with db.conn() as c:
        cur = c.execute("INSERT INTO posts (user_id, session_id, visibility, created_at, data) VALUES (?,?,?,?,?)",
                        (uid, int(session_id), vis, db.now(), json.dumps(data, ensure_ascii=False)))
        pid = cur.lastrowid
    return get(uid, pid)


def _row(r) -> dict:
    d = dict(r)
    d["data"] = json.loads(d["data"])
    return d


def can_view(uid: int, post: dict, is_admin: bool = False) -> bool:
    if post["user_id"] == uid or is_admin and post["visibility"] != "eu":
        return True
    if post["visibility"] == "todos":
        return True
    return post["visibility"] == "parceiros" and partners.is_partner(uid, post["user_id"])


def _hidden(uid: int) -> set[int]:
    return set(json.loads(db.kv_get("social_hidden", uid) or "[]"))


def _decorate(uid: int, posts: list[dict]) -> list[dict]:
    if not posts:
        return []
    ids = [p["id"] for p in posts]
    marks = ",".join("?" * len(ids))
    with db.conn() as c:
        reacts = c.execute(f"SELECT post_id, user_id, kind FROM post_reacts WHERE post_id IN ({marks})", ids).fetchall()
        comments = c.execute(f"SELECT * FROM post_comments WHERE post_id IN ({marks}) ORDER BY id", ids).fetchall()
    names = partners._names()
    out = []
    for p in posts:
        rs = [dict(r) for r in reacts if r["post_id"] == p["id"]]
        cs = [dict(r) for r in comments if r["post_id"] == p["id"]]
        d = p["data"]
        out.append({
            "id": p["id"], "mine": p["user_id"] == uid, "author_id": p["user_id"],
            "author": names.get(p["user_id"]) or "Alguém", "visibility": p["visibility"], "created_at": p["created_at"],
            "caption": d.get("caption") or "", "photo_url": my_gym.photo_url(d.get("photo")), "workout": d.get("w") or {},
            "reacts": {k: sum(1 for r in rs if r["kind"] == k) for k in REACTS},
            "my_react": next((r["kind"] for r in rs if r["user_id"] == uid), None),
            "comments": [{"id": c["id"], "author": names.get(c["user_id"]) or "Alguém", "text": c["text"], "created_at": c["created_at"],
                          "can_delete": c["user_id"] == uid or p["user_id"] == uid} for c in cs],
        })
    return out


def feed(uid: int, before: int | None = None, limit: int = 20, only: int | None = None, is_admin: bool = False) -> list[dict]:
    hidden = _hidden(uid)
    q = "SELECT * FROM posts WHERE 1=1"
    args: list = []
    if before:
        q += " AND id < ?"
        args.append(int(before))
    if only:
        q += " AND user_id = ?"
        args.append(int(only))
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit) * 3)
    with db.conn() as c:
        rows = [_row(r) for r in c.execute(q, args).fetchall()]
    vis = [r for r in rows if r["id"] not in hidden and can_view(uid, r, is_admin)][:limit]
    return _decorate(uid, vis)


def get(uid: int, post_id: int, is_admin: bool = False) -> dict:
    with db.conn() as c:
        r = c.execute("SELECT * FROM posts WHERE id = ?", (int(post_id),)).fetchone()
    if not r or not can_view(uid, _row(r), is_admin):
        raise LookupError("Post não encontrado")
    return _decorate(uid, [_row(r)])[0]


def react(uid: int, post_id: int, kind: str | None) -> dict:
    get(uid, post_id)
    with db.conn() as c:
        c.execute("DELETE FROM post_reacts WHERE post_id = ? AND user_id = ?", (int(post_id), uid))
        if kind in REACTS:
            c.execute("INSERT INTO post_reacts (post_id, user_id, kind, created_at) VALUES (?,?,?,?)", (int(post_id), uid, kind, db.now()))
    return get(uid, post_id)


def comment(uid: int, post_id: int, text: str) -> dict:
    get(uid, post_id)
    text = (text or "").strip()[:MAX_COMMENT]
    if not text:
        raise ValueError("Comentário vazio")
    with db.conn() as c:
        c.execute("INSERT INTO post_comments (post_id, user_id, text, created_at) VALUES (?,?,?,?)", (int(post_id), uid, text, db.now()))
    return get(uid, post_id)


def delete_comment(uid: int, comment_id: int, is_admin: bool = False) -> None:
    with db.conn() as c:
        r = c.execute("SELECT c.user_id AS cu, p.user_id AS pu FROM post_comments c JOIN posts p ON p.id = c.post_id WHERE c.id = ?",
                      (int(comment_id),)).fetchone()
        if not r or not (uid in (r["cu"], r["pu"]) or is_admin):
            raise LookupError("Comentário não encontrado")
        c.execute("DELETE FROM post_comments WHERE id = ?", (int(comment_id),))


def delete(uid: int, post_id: int, is_admin: bool = False) -> None:
    with db.conn() as c:
        r = c.execute("SELECT user_id FROM posts WHERE id = ?", (int(post_id),)).fetchone()
        if not r or not (r["user_id"] == uid or is_admin):
            raise LookupError("Post não encontrado")
        c.execute("DELETE FROM post_reacts WHERE post_id = ?", (int(post_id),))
        c.execute("DELETE FROM post_comments WHERE post_id = ?", (int(post_id),))
        c.execute("DELETE FROM posts WHERE id = ?", (int(post_id),))


def hide(uid: int, post_id: int) -> None:
    """'Não quero ver': some só para mim (e o administrador fica sabendo, para avaliar)."""
    h = _hidden(uid) | {int(post_id)}
    db.kv_set("social_hidden", json.dumps(sorted(h)), uid)
    reps = json.loads(db.kv_get("social_reports", 1) or "[]")
    reps.append({"post_id": int(post_id), "by": uid, "at": db.now()})
    db.kv_set("social_reports", json.dumps(reps[-200:]), 1)


def shared_session_ids(uid: int) -> set[int]:
    with db.conn() as c:
        return {r["session_id"] for r in c.execute("SELECT session_id FROM posts WHERE user_id = ?", (uid,)).fetchall()}
