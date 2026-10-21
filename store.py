"""SQLite 저장소: 후보 주제, 초안, 발행 기록."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).parent / "data.db"


@contextmanager
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS topics(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              day TEXT, keyword TEXT, angle TEXT, why_now TEXT, seed TEXT,
              sources TEXT, status TEXT DEFAULT 'candidate', created_at TEXT);
            CREATE TABLE IF NOT EXISTS drafts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              topic_id INTEGER, day TEXT, body TEXT, title_idx INTEGER,
              status TEXT DEFAULT 'pending', file_path TEXT,
              created_at TEXT, published_at TEXT);
            """
        )


def add_topic(day: str, t: dict) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO topics(day,keyword,angle,why_now,seed,sources,created_at) VALUES(?,?,?,?,?,?,?)",
            (day, t["keyword"], t.get("angle", ""), t.get("why_now", ""), t.get("seed", ""),
             json.dumps(t.get("sources", []), ensure_ascii=False), datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_topic(tid: int) -> dict | None:
    with db() as c:
        row = c.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["sources"] = json.loads(d["sources"] or "[]")
    return d


def set_topic_status(tid: int, status: str) -> None:
    with db() as c:
        c.execute("UPDATE topics SET status=? WHERE id=?", (status, tid))


def claim_topic(tid: int) -> bool:
    """candidate → picked 를 원자적으로. 버튼 중복 탭 방지."""
    with db() as c:
        cur = c.execute("UPDATE topics SET status='picked' WHERE id=? AND status='candidate'", (tid,))
        return cur.rowcount == 1


def recent_keywords(days: int = 30) -> list[str]:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with db() as c:
        rows = c.execute(
            "SELECT keyword FROM topics WHERE status IN ('picked','done') AND created_at>=?", (since,)
        ).fetchall()
    return [r["keyword"] for r in rows]


def save_draft(topic_id: int, day: str, draft: dict) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO drafts(topic_id,day,body,created_at) VALUES(?,?,?,?)",
            (topic_id, day, json.dumps(draft, ensure_ascii=False), datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_draft(did: int) -> dict | None:
    with db() as c:
        row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["body"] = json.loads(d["body"])
    return d


def update_draft(did: int, **fields) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    with db() as c:
        c.execute(f"UPDATE drafts SET {keys} WHERE id=?", (*fields.values(), did))


def count_day(day: str, statuses: tuple[str, ...]) -> int:
    q = f"SELECT COUNT(*) FROM drafts WHERE day=? AND status IN ({','.join('?' * len(statuses))})"
    with db() as c:
        return c.execute(q, (day, *statuses)).fetchone()[0]


def topics_on(day: str) -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM topics WHERE day=?", (day,)).fetchone()[0]
