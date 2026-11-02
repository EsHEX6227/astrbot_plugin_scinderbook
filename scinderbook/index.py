"""关键词索引：SQLite FTS5 倒排 + 少量精确命中加权。

设计约束：
- 只用关键词召回，不碰 embedding、不发任何网络请求；
- 召回给模型看的只有正文（Entry.render），检索词本身不进上下文；
- 索引是纯本地的单文件 SQLite，删掉即可重建。
"""

from __future__ import annotations

import functools
import sqlite3
import threading
from pathlib import Path

from .entry import Entry
from .semantic import pack_vector, rank_by_vector
from .tokenize import fts5_query, tokenize


def _locked(func):
    """所有碰连接的方法都串行化：重建可能在 asyncio.to_thread 里跑，
    召回却在事件循环线程里跑，同一个连接必须能跨线程用。"""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return func(self, *args, **kwargs)

    return wrapper

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id       INTEGER PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    title    TEXT NOT NULL,
    keywords TEXT NOT NULL,
    body     TEXT NOT NULL,
    always   INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
    USING fts5(tokens, content='', contentless_delete=1, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS embeddings (
    entry_id INTEGER PRIMARY KEY,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# 精确命中一个检索词加的分；bm25 的贡献通常是个位数，这个量级能主导排序
_EXACT_BONUS = 3.0


class ScinderBookIndex:
    """一个目录对应一个索引文件。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False：连接可能在 to_thread 的工作线程里建，
        # 之后由事件循环线程查询；并发由 self._lock 串行化。
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------- 构建 ----------

    @_locked
    def _reset_fts(self) -> None:
        """清空 FTS 倒排。

        contentless 表在没声明 contentless_delete=1 时不允许 DELETE；
        老库就是那种结构，所以这里兜底重建一次表。
        """
        try:
            self._conn.execute("DELETE FROM entries_fts")
        except sqlite3.OperationalError:
            self._conn.execute("DROP TABLE IF EXISTS entries_fts")
            self._conn.execute(
                "CREATE VIRTUAL TABLE entries_fts "
                "USING fts5(tokens, content='', contentless_delete=1, tokenize='unicode61')"
            )

    @_locked
    def rebuild(self, entries: list[Entry]) -> dict[str, int]:
        """全量重建索引；同时清掉旧的向量（语义兜底需要重新算）。"""
        conn = self._conn
        conn.execute("DELETE FROM entries")
        self._reset_fts()
        conn.execute("DELETE FROM embeddings")
        for entry in entries:
            cur = conn.execute(
                "INSERT INTO entries(rel_path, title, keywords, body, always) VALUES (?,?,?,?,?)",
                (
                    entry.rel_path,
                    entry.title,
                    entry.keyword_text,
                    entry.body,
                    1 if entry.always else 0,
                ),
            )
            rowid = cur.lastrowid
            search_text = " ".join(
                tokenize(f"{entry.title} {entry.keyword_text} {entry.body}")
            )
            conn.execute(
                "INSERT INTO entries_fts(rowid, tokens) VALUES (?, ?)",
                (rowid, search_text),
            )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('count', ?)",
            (str(len(entries)),),
        )
        conn.commit()
        return {"entries": len(entries)}

    # ---------- 语义兜底（可选） ----------

    @_locked
    def embedding_candidates(self, max_chars: int = 512) -> list[tuple[int, str]]:
        """返回 [(entry_id, 用于 embedding 的文本)]。

        故意只取正文：检索词行不参与向量，避免词表稀释语义。
        """
        rows = self._conn.execute(
            "SELECT id, body FROM entries ORDER BY id"
        ).fetchall()
        return [(int(rowid), str(body)[:max_chars]) for rowid, body in rows if body]

    @_locked
    def set_embeddings(self, vectors: list[tuple[int, list[float]]]) -> int:
        """写入向量，返回写入条数。"""
        conn = self._conn
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings(entry_id, dim, vec) VALUES (?,?,?)",
            [(entry_id, len(vec), pack_vector(vec)) for entry_id, vec in vectors if vec],
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('embedding_count', ?)",
            (str(len(vectors)),),
        )
        conn.commit()
        return len(vectors)

    @_locked
    def embeddings_count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        )

    @_locked
    def get_vector_dim(self) -> int:
        row = self._conn.execute("SELECT dim FROM embeddings LIMIT 1").fetchone()
        return int(row[0]) if row else 0

    @_locked
    def semantic_search(
        self,
        query_vec: list[float],
        top_n: int = 3,
        min_sim: float = 0.45,
    ) -> list[tuple[float, Entry]]:
        """向量兜底召回：只在关键词没命中或没占满时使用。"""
        if not query_vec:
            return []
        rows = self._conn.execute(
            "SELECT entry_id, vec FROM embeddings"
        ).fetchall()
        ranked = rank_by_vector(query_vec, rows, top_n, min_sim)
        if not ranked:
            return []
        ids = [entry_id for _, entry_id in ranked]
        placeholders = ",".join("?" for _ in ids)
        entry_rows = self._conn.execute(
            f"SELECT id, rel_path, title, keywords, body, always FROM entries WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {
            int(row[0]): self._row_to_entry(row[1:]) for row in entry_rows
        }
        return [
            (sim, by_id[entry_id])
            for sim, entry_id in ranked
            if entry_id in by_id
        ]

    # ---------- 查询 ----------

    @_locked
    def _always_entries(self) -> list[Entry]:
        rows = self._conn.execute(
            "SELECT rel_path, title, keywords, body, always FROM entries WHERE always=1"
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    @staticmethod
    def _row_to_entry(row: tuple) -> Entry:
        rel_path, title, keywords, body, always = row
        return Entry(
            rel_path=rel_path,
            title=title,
            keywords=[k for k in str(keywords).split() if k],
            body=body,
            always=bool(always),
        )

    @_locked
    def search(
        self,
        query: str,
        top_n: int = 3,
        min_score: float = 0.0,
        max_chars: int = 1500,
        include_always: bool = True,
    ) -> list[tuple[float, Entry]]:
        """按关键词召回词条，返回 [(score, Entry)]，按分数从高到低。"""
        matched: list[tuple[float, Entry]] = []
        match_query = fts5_query(query)
        if match_query:
            rows = self._conn.execute(
                "SELECT e.rel_path, e.title, e.keywords, e.body, e.always, bm25(entries_fts) AS rank "
                "FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid "
                "WHERE entries_fts MATCH ? ORDER BY rank LIMIT 50",
                (match_query,),
            ).fetchall()
            lowered_query = query.lower()
            for row in rows:
                entry = self._row_to_entry(row[:5])
                rank = float(row[5])
                score = -rank  # bm25 越小越相关，取反成越大越好
                hits = sum(
                    1
                    for kw in entry.keywords
                    if len(kw) >= 2 and kw.lower() in lowered_query
                )
                score += min(hits, 5) * _EXACT_BONUS
                if score < min_score:
                    continue
                matched.append((score, entry))

        matched.sort(key=lambda item: (-item[0], item[1].rel_path))

        picked: list[tuple[float, Entry]] = []
        used_chars = 0
        seen: set[str] = set()

        for score, entry in matched:
            if len(picked) >= top_n:
                break
            if entry.rel_path in seen:
                continue
            size = len(entry.body)
            if picked and used_chars + size > max_chars:
                continue
            picked.append((score, entry))
            seen.add(entry.rel_path)
            used_chars += size

        if include_always:
            for entry in self._always_entries():
                if entry.rel_path in seen:
                    continue
                if used_chars + len(entry.body) > max_chars:
                    continue
                picked.append((0.0, entry))
                seen.add(entry.rel_path)
                used_chars += len(entry.body)

        return picked

    # ---------- 统计 ----------

    @_locked
    def stats(self) -> dict[str, int]:
        count = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        always = self._conn.execute(
            "SELECT COUNT(*) FROM entries WHERE always=1"
        ).fetchone()[0]
        chars = self._conn.execute(
            "SELECT COALESCE(SUM(LENGTH(body)), 0) FROM entries"
        ).fetchone()[0]
        keywords = self._conn.execute(
            "SELECT COALESCE(SUM(LENGTH(keywords) - LENGTH(REPLACE(keywords, ' ', '')) + 1), 0) "
            "FROM entries WHERE LENGTH(keywords) > 0"
        ).fetchone()[0]
        return {
            "entries": int(count),
            "always": int(always),
            "keywords": int(keywords),
            "chars": int(chars),
        }

    @_locked
    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
