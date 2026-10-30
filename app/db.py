"""SQLite 存储层：条目表 + FTS5 trigram 索引，支持中文/英文子串模糊搜索。"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,              -- text / image / video / file
    source      TEXT    NOT NULL DEFAULT 'plain', -- plain / html / bitmap / path
    content     TEXT    NOT NULL DEFAULT '',   -- 文本正文；文件类为空
    preview     TEXT    NOT NULL DEFAULT '',   -- 列表里显示的一行摘要
    html        TEXT    NOT NULL DEFAULT '',   -- 富文本片段
    file_path   TEXT    NOT NULL DEFAULT '',   -- 图片 webp 落盘路径
    thumb_path  TEXT    NOT NULL DEFAULT '',   -- 缩略图路径
    paths       TEXT    NOT NULL DEFAULT '',   -- 文件类：JSON 数组
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    hash        TEXT    NOT NULL,
    pinned      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    used_at     REAL    NOT NULL,
    use_count   INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_hash ON items(hash);
CREATE INDEX IF NOT EXISTS idx_items_used ON items(used_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind, used_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    content,
    preview,
    content='items',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS trg_items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, content, preview) VALUES (new.id, new.content, new.preview);
END;

CREATE TRIGGER IF NOT EXISTS trg_items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, content, preview)
    VALUES ('delete', old.id, old.content, old.preview);
END;

CREATE TRIGGER IF NOT EXISTS trg_items_au AFTER UPDATE OF content, preview ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, content, preview)
    VALUES ('delete', old.id, old.content, old.preview);
    INSERT INTO items_fts(rowid, content, preview) VALUES (new.id, new.content, new.preview);
END;
"""

COLUMNS = (
    "id, kind, source, content, preview, html, file_path, thumb_path, paths, "
    "width, height, size_bytes, hash, pinned, created_at, used_at, use_count"
)


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()


def item_hash(kind: str, payload: str) -> str:
    return sha1_text(f"{kind}\x1f{payload}")


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def split_terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip()) if t]


class Store:
    """单连接 + 锁。写入量很小，UI 线程直接查询也足够快。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ 写

    def add(
        self,
        *,
        kind: str,
        source: str,
        content: str = "",
        preview: str = "",
        html: str = "",
        file_path: str = "",
        thumb_path: str = "",
        paths: Iterable[str] = (),
        width: int = 0,
        height: int = 0,
        size_bytes: int = 0,
        hash: str,
    ) -> tuple[int, bool]:
        """插入或刷新已有条目。返回 (id, 是否新插入)。"""
        now = time.time()
        paths_json = ""
        if paths:
            import json

            paths_json = json.dumps(list(paths), ensure_ascii=False)
        with self._lock:
            row = self._conn.execute("SELECT id FROM items WHERE hash = ?", (hash,)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE items SET used_at = ?, use_count = use_count + 1, "
                    "html = CASE WHEN ? <> '' THEN ? ELSE html END, "
                    "file_path = CASE WHEN ? <> '' THEN ? ELSE file_path END, "
                    "thumb_path = CASE WHEN ? <> '' THEN ? ELSE thumb_path END, "
                    "width = CASE WHEN ? > 0 THEN ? ELSE width END, "
                    "height = CASE WHEN ? > 0 THEN ? ELSE height END "
                    "WHERE id = ?",
                    (
                        now,
                        html, html,
                        file_path, file_path,
                        thumb_path, thumb_path,
                        width, width,
                        height, height,
                        row["id"],
                    ),
                )
                self._conn.commit()
                return int(row["id"]), False
            cur = self._conn.execute(
                "INSERT INTO items (kind, source, content, preview, html, file_path, thumb_path, "
                "paths, width, height, size_bytes, hash, pinned, created_at, used_at, use_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,1)",
                (
                    kind, source, content, preview, html, file_path, thumb_path,
                    paths_json, width, height, size_bytes, hash, now, now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid), True

    def touch(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE items SET used_at = ?, use_count = use_count + 1 WHERE id = ?",
                (time.time(), item_id),
            )
            self._conn.commit()

    def toggle_pin(self, item_id: int) -> int:
        with self._lock:
            row = self._conn.execute("SELECT pinned FROM items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return 0
            pinned = 0 if row["pinned"] else 1
            self._conn.execute("UPDATE items SET pinned = ? WHERE id = ?", (pinned, item_id))
            self._conn.commit()
            return pinned

    def delete(self, item_id: int) -> list[str]:
        """删除一条，返回需要清理的磁盘文件路径。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT file_path, thumb_path FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if not row:
                return []
            self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            self._conn.commit()
            return [p for p in (row["file_path"], row["thumb_path"]) if p]

    def clear(self, kind: str | None = None) -> list[str]:
        with self._lock:
            if kind:
                rows = self._conn.execute(
                    "SELECT id, file_path, thumb_path FROM items WHERE kind = ? AND pinned = 0",
                    (kind,),
                ).fetchall()
                self._conn.execute("DELETE FROM items WHERE kind = ? AND pinned = 0", (kind,))
            else:
                rows = self._conn.execute(
                    "SELECT id, file_path, thumb_path FROM items WHERE pinned = 0"
                ).fetchall()
                self._conn.execute("DELETE FROM items WHERE pinned = 0")
            self._conn.commit()
        files: list[str] = []
        for r in rows:
            files.extend([p for p in (r["file_path"], r["thumb_path"]) if p])
        return files

    def wipe(self) -> list[str]:
        """删除所有条目（含置顶）并回收数据库空间，返回需要清理的磁盘文件路径。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path, thumb_path FROM items"
            ).fetchall()
            self._conn.execute("DELETE FROM items")
            self._conn.commit()
            try:
                self._conn.execute("VACUUM")
            except sqlite3.OperationalError:
                pass
        files: list[str] = []
        for r in rows:
            files.extend([p for p in (r["file_path"], r["thumb_path"]) if p])
        return files

    def purge(self, max_items: int) -> list[str]:
        """超出上限时，按最久未用清理未置顶条目。"""
        if max_items <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, file_path, thumb_path FROM items WHERE pinned = 0 "
                "ORDER BY used_at DESC LIMIT -1 OFFSET ?",
                (max_items,),
            ).fetchall()
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            self._conn.executemany("DELETE FROM items WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()
        files: list[str] = []
        for r in rows:
            files.extend([p for p in (r["file_path"], r["thumb_path"]) if p])
        return files

    # ------------------------------------------------------------ 读

    def get(self, item_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                f"SELECT {COLUMNS} FROM items WHERE id = ?", (item_id,)
            ).fetchone()

    def query(self, *, kind: str | None = None, search: str = "", limit: int = 400) -> list[sqlite3.Row]:
        terms = split_terms(search)
        with self._lock:
            if terms and all(len(t) >= 3 for t in terms):
                rows = self._fts_query(terms, kind, limit)
                if rows is not None:
                    return rows
            return self._like_query(terms, kind, limit)

    def _fts_query(self, terms: list[str], kind: str | None, limit: int) -> list[sqlite3.Row] | None:
        clauses = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in terms]
        match = " AND ".join(clauses)
        sql = (
            f"SELECT i.{COLUMNS.replace(', ', ', i.')} FROM items_fts f "
            "JOIN items i ON i.id = f.rowid WHERE items_fts MATCH ?"
        )
        params: list[Any] = [match]
        if kind:
            sql += " AND i.kind = ?"
            params.append(kind)
        sql += " ORDER BY i.pinned DESC, i.used_at DESC LIMIT ?"
        params.append(limit)
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return None

    def _like_query(self, terms: list[str], kind: str | None, limit: int) -> list[sqlite3.Row]:
        sql = f"SELECT {COLUMNS} FROM items WHERE 1=1"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            sql += " AND (content LIKE ? ESCAPE '\\' OR preview LIKE ? ESCAPE '\\')"
            params.extend([pattern, pattern])
        sql += " ORDER BY pinned DESC, used_at DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchall()

    def count(self, kind: str | None = None) -> int:
        with self._lock:
            if kind:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM items WHERE kind = ?", (kind,)).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
            return int(row["n"])

    def kind_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM items GROUP BY kind"
            ).fetchall()
            return {r["kind"]: int(r["n"]) for r in rows}

    def total_size(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM items").fetchone()
            return int(row["n"])

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
