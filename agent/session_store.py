"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "session.db"


class SqliteSessionStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS session_context (thread_id TEXT PRIMARY KEY, context TEXT NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS investigations ("
            "run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created TEXT NOT NULL, "
            "processing_status TEXT NOT NULL, validation_status TEXT NOT NULL, result_json TEXT NOT NULL)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS investigations_thread ON investigations(thread_id)")
        self.conn.commit()

    def load(self, thread_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid",
            (thread_id,),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def append(self, thread_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?)",
            (thread_id, role, content, datetime.now().isoformat()),
        )
        self.conn.commit()

    def load_context(self, thread_id: str) -> dict:
        row = self.conn.execute(
            "SELECT context FROM session_context WHERE thread_id=?", (thread_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def append_exchange(self, thread_id: str, message: str, reply: str, context: dict | None = None,
                        investigation: dict | None = None) -> None:
        with self.conn:
            if investigation is not None:
                if investigation['thread_id'] != thread_id:
                    raise ValueError('Investigation session mismatch')
                existing = self.conn.execute("SELECT thread_id FROM investigations WHERE run_id=?", (investigation['run_id'],)).fetchone()
                if existing:
                    if existing[0] != thread_id:
                        raise ValueError('Investigation ID belongs to another session')
                    return
                self.conn.execute("INSERT INTO investigations VALUES (?,?,?,?,?,?)", (
                    investigation['run_id'], thread_id, investigation['created'],
                    investigation['processing_status'], investigation['validation_status'],
                    json.dumps(investigation, ensure_ascii=False),
                ))
            self.conn.executemany("INSERT INTO messages VALUES (?,?,?,?)", [
                (thread_id, "user", message, datetime.now().isoformat()),
                (thread_id, "assistant", reply, datetime.now().isoformat()),
            ])
            if context is not None:
                self.conn.execute(
                    "INSERT INTO session_context VALUES (?,?) ON CONFLICT(thread_id) "
                    "DO UPDATE SET context=excluded.context", (thread_id, json.dumps(context, ensure_ascii=False))
                )

    def list_investigations(self, thread_id: str, limit: int = 20) -> list[dict]:
        return self.investigation_page(thread_id, limit)['investigations']

    def investigation_page(self, thread_id: str | None = None, limit: int = 20,
                           before: str | None = None) -> dict:
        where, params = [], []
        if thread_id is not None:
            where.append('thread_id=?')
            params.append(thread_id)
        if before is not None:
            row = self.conn.execute('SELECT rowid,thread_id FROM investigations WHERE run_id=?', (before,)).fetchone()
            if row is None or (thread_id is not None and row[1] != thread_id):
                raise ValueError('Invalid investigation cursor')
            where.append('rowid < ?')
            params.append(row[0])
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        rows = self.conn.execute('SELECT result_json FROM investigations' + clause + ' ORDER BY rowid DESC LIMIT ?',
                                 [*params, limit + 1]).fetchall()
        records = [json.loads(row[0]) for row in rows[:limit]]
        keys = ('run_id', 'thread_id', 'created', 'query', 'targets', 'processing_status', 'validation_status', 'is_sample')
        return {'investigations': [{key: record.get(key) for key in keys} for record in records],
                'next_cursor': records[-1]['run_id'] if len(rows) > limit else None}

    def get_investigation(self, run_id: str, thread_id: str) -> dict | None:
        row = self.conn.execute("SELECT result_json FROM investigations WHERE run_id=? AND thread_id=?",
                                (run_id, thread_id)).fetchone()
        return json.loads(row[0]) if row else None

    def previous_observation(self, thread_id: str, system: str, before_run_id: str | None = None) -> dict | None:
        from .comparison import observation_for
        params = [thread_id]
        cutoff = ''
        if before_run_id is not None:
            row = self.conn.execute('SELECT rowid FROM investigations WHERE run_id=? AND thread_id=?',
                                    (before_run_id, thread_id)).fetchone()
            if row is None:
                return None
            cutoff = ' AND rowid < ?'
            params.append(row[0])
        rows = self.conn.execute('SELECT run_id,result_json FROM investigations WHERE thread_id=?' + cutoff + ' ORDER BY rowid DESC', params)
        for run_id, payload in rows:
            observation = observation_for(json.loads(payload), system)
            if observation is not None:
                return {'run_id': run_id, 'observation': observation}
        return None

    def close(self) -> None:
        self.conn.close()
