from __future__ import annotations
import sqlite3
import hashlib
from pathlib import Path
from typing import Iterable, Optional, Sequence

from utils.md import md_to_html
from database.schema import ensure_schema
from database.migrations import upgrade

import shutil, time, os

def _backup_db_file(path: str) -> None:
    ts = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, f"{path}.bak-{ts}")

def _normalize_alias(s: str) -> str:
    # Trim, lower, collapse newlines to space, and normalize internal spaces
    s = (s or "").strip().lower().replace("\r", "").replace("\n", " ")
    # collapse multiple spaces
    s = " ".join(s.split())
    return s

def _norm_for_hash(text: str) -> str:
    return (text or "").replace("\r\n","\n").strip()

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()

class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
    
        # schema init / migrations:
        # 1) Base schema (v1)
        ensure_schema(self.conn)
        # 2) Migrations to latest
        if self.path.exists():
            _backup_db_file(str(self.path))
        upgrade(self.conn)

    # ---- Projects
    def project_quantity(self) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM projects WHERE COALESCE(deleted,0)=0")
        return int(c.fetchone()[0] or 0)

    def project_first_active(self) -> Optional[int]:
        c = self.conn.cursor()
        c.execute("SELECT id FROM projects WHERE COALESCE(deleted,0)=0 ORDER BY created_at, id LIMIT 1")
        r = c.fetchone()
        return int(r["id"]) if r else None

    def project_meta(self, project_id: int) -> dict:
        c = self.conn.cursor()
        c.execute("""SELECT id, name, import_dir, export_dir, description
                    FROM projects WHERE id=?""", (project_id,))
        r = c.fetchone()
        return dict(r) if r else {}
    
    def project_name(self, project_id: int) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT name FROM projects WHERE id=?", (project_id,))
        r = c.fetchone()
        return r["name"] if r else None

    def project_update_meta(self, project_id: int, *, name: Optional[str]=None,
                            import_dir: Optional[str]=None, export_dir: Optional[str]=None,
                            description: Optional[str]=None) -> None:
        c = self.conn.cursor()
        c.execute("""UPDATE projects
                     SET name=COALESCE(?, name),
                         import_dir=COALESCE(?, import_dir),
                         export_dir=COALESCE(?, export_dir),
                         description=COALESCE(?, description)
                     WHERE id=?""", (name, import_dir, export_dir, description, project_id))
        self.conn.commit()

    def project_create(self, name: str="Untitled Project") -> int:
        c = self.conn.cursor()
        c.execute("INSERT INTO projects(name) VALUES (?)", (name,))
        self.conn.commit()
        return int(c.lastrowid)

    def project_soft_delete(self, project_id: int) -> None:
        self.conn.execute("UPDATE projects SET deleted=1 WHERE id=?", (project_id,))
        self.conn.commit()

    def project_deleted(self, project_id: int) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT id FROM projects WHERE id=? AND COALESCE(deleted,0)=0", (project_id,))
        return bool(c.fetchone())

    # ---- Books
    def book_list(self, project_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, name, position FROM books
                     WHERE project_id=? ORDER BY position, id""", (project_id,))
        return c.fetchall()

    def book_create(self, project_id: int, name: str="New Book", position: int=0) -> int:
        c = self.conn.cursor()
        c.execute("INSERT INTO books(project_id, name, position) VALUES (?,?,?)",
                  (project_id, name, position))
        self.conn.commit()
        return int(c.lastrowid)

    def book_rename(self, book_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE books SET name=? WHERE id=?", (new_name, book_id))
        self.conn.commit()

    # ---- Chapters
    def chapter(self, chapter_id: int) -> Optional[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("SELECT title FROM chapters WHERE id=?", (chapter_id,))
        r = c.fetchone()
        return r["title"] if r else None
    
    def chapter_meta(self, chapter_id: int) -> dict:
        """
        Returns chapters meta plus active version id/hash/length (no text).
        """
        c = self.conn.cursor()
        c.execute("""SELECT id, title, position, book_id, project_id, active_version_id
                    FROM chapters WHERE id=?""", (chapter_id,))
        ch = c.fetchone()
        if not ch:
            return {}

        ver = None
        text_hash = None
        text_len = None
        if ch["active_version_id"]:
            c.execute("SELECT id, text_hash, LENGTH(text) AS text_len FROM chapter_versions WHERE id=?",
                    (ch["active_version_id"],))
            ver = c.fetchone()
            if ver:
                text_hash = ver["text_hash"]
                text_len  = ver["text_len"]

        return {
            "id": ch["id"],
            "title": ch["title"],
            "position": ch["position"],
            "book_id": ch["book_id"],
            "project_id": ch["project_id"],
            "active_version_id": ch["active_version_id"],
            "text_hash": text_hash,
            "text_len": text_len,
        }

    def chapter_last_position_index(self, project_id: int, book_id: int) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE project_id=? AND book_id=? AND COALESCE(deleted,0)=0", (project_id, book_id))
        last_pos_idx = c.fetchone()[0]
        if last_pos_idx is None or last_pos_idx < 0:
            return -1
        return last_pos_idx

    def chapter_content(self, chapter_id: int, version_id: int | None = None) -> str | None:
        if version_id is None:
            row = self.chapter_active_version_row(chapter_id)
            return row["text"] if row else None
        else:
            return self.chapter_content_by_version(version_id)

    def chapter_project_id(self, chapter_id: int) -> Optional[int]:
        c = self.conn.cursor()
        c.execute("SELECT project_id FROM chapters WHERE id=?", (chapter_id,))
        r = c.fetchone()
        return int(r["project_id"]) if r else None

    def chapter_content_by_version(self, version_id: int) -> str | None:
        c = self.conn.cursor()
        c.execute("SELECT text FROM chapter_versions WHERE id=?", (version_id,))
        row = c.fetchone()
        return row["text"] if row else None

    def chapter_version_hash(self, version_id: int) -> str | None:
        c = self.conn.cursor()
        c.execute("SELECT text_hash FROM chapter_versions WHERE id=?", (version_id,))
        r = c.fetchone()
        return r["text_hash"] if r else None

    def chapter_list(self, project_id: int, book_id: int, fetchone: bool=False) -> list[sqlite3.Row] | sqlite3.Row | None:
        c = self.conn.cursor()
        c.execute("""SELECT id, title, position, active_version_id
                    FROM chapters
                    WHERE project_id=? AND book_id=? AND COALESCE(deleted,0)=0
                    ORDER BY position, id""", (project_id, book_id))
        return c.fetchone() if fetchone else c.fetchall()

    def chapter_list_with_vermeta(self, project_id: int, book_id: int):
        c = self.conn.cursor()
        c.execute("""
            SELECT ch.id, ch.title, ch.position, ch.active_version_id,
                cv.text_hash, LENGTH(cv.text) AS text_len
            FROM chapters ch
            LEFT JOIN chapter_versions cv ON cv.id = ch.active_version_id
            WHERE ch.project_id=? AND ch.book_id=? AND COALESCE(ch.deleted,0)=0
            ORDER BY ch.position, ch.id
        """, (project_id, book_id))
        return c.fetchall()

    def chapter_insert(self, project_id: int, book_id: int, position: int,
                    title: str, content_md: str) -> int:
        """
        Creates a chapter and immediately creates an active chapter_version carrying `content_md`.
        """
        c = self.conn.cursor()
        print("Insert chapter:", project_id, book_id, position, title)
        # Note: no 'content' here anymore; keep title/position on chapters
        c.execute("""
            INSERT INTO chapters(project_id, book_id, title, position, updated_at)
            VALUES (?,?,?,?,CURRENT_TIMESTAMP)
        """, (project_id, book_id, title, position))
        chap_id = int(c.lastrowid)

        # Seed first version and mark active
        print("insert content:", content_md)
        ver_id = self.create_chapter_version(chap_id, content_md or "", make_active=None)
        # seed FTS if present
        self.chapters_fts_upsert(chap_id, title, content_md or "")

        self.conn.commit()
        return chap_id

    def chapter_update(self, chapter_id: int, *, title: Optional[str]=None,
                    content_md: Optional[str]=None) -> None:
        """
        Back-compat updater. Title is stored on chapters; text on active chapter_version.
        Uses only DB helpers (no raw SQL here).
        """
        # title
        if title is not None:
            self.set_chapter_title(chapter_id, title)

        # text (to active version)
        if content_md is not None:
            ver_id = self.ensure_active_version(chapter_id)
            _, changed = self.set_chapter_version_text(ver_id, content_md)
            # nothing else to do here; caller can decide whether to recompute refs/metrics

    def chapter_soft_delete(self, chapter_id: int) -> None:
        self.conn.execute("UPDATE chapters SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (chapter_id,))
        self.conn.commit()

    def chapter_undelete(self, chapter_id: int) -> None:
        self.conn.execute("UPDATE chapters SET deleted=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (chapter_id,))
        self.conn.commit()

    def chapter_compact_positions(self, project_id: int, book_id: int) -> None:
        """
        Rewrite chapter positions within (project_id, book_id) to 0..N-1,
        skipping soft-deleted chapters.
        """
        rows = self.chapter_list(project_id, book_id)  # assumed to exclude deleted
        for new_pos, row in enumerate(rows):
            cid = row["id"]
            self.conn.execute("UPDATE chapters SET position=? WHERE id=?", (new_pos, cid))
        self.conn.commit()

    def _chapter_set_position_and_book(self, chapter_id: int, position: int, book_id: int) -> None:
        c = self.conn.cursor()
        c.execute("UPDATE chapters SET position=?, book_id=? WHERE id=?",
                  (position, book_id, chapter_id))
        self.conn.commit()

    def chapter_move_to_index(self, project_id: int, book_id: int,
                              chapter_id: int, insert_index: int) -> None:
        rows = [r["id"] for r in self.chapter_list(project_id, book_id)]
        if chapter_id in rows:
            rows.remove(chapter_id)
        insert_index = max(0, min(insert_index, len(rows)))
        rows.insert(insert_index, chapter_id)
        for pos, cid in enumerate(rows):
            self._chapter_set_position_and_book(cid, pos, book_id)

    def chapter_position_gap(self, N: int, project_id: int, book_id: int, last_pos_idx: int):
        # === Open a gap so inserts are contiguous (no interleaving) ===
        cur = self.conn.cursor()
        # cur.execute("SELECT MAX(position) FROM chapters WHERE project_id=? AND book_id=?", (project_id, book_id))
        # base_index = cur.fetchone()[0]
        # print("base_index", base_index)
        # base_index = cur.fetchone()[0] + 1
        cur.execute("""
            UPDATE chapters
            SET position = position + ?
            WHERE project_id=? AND book_id=? AND position >= ? AND COALESCE(deleted,0)=0
        """, (N, project_id, book_id, last_pos_idx))
        self.conn.commit()

    # ---- Chapter versions / outline ----
    # --- Active version accessors ----------------------------------------------

    def _next_version_number(self, chapter_id: int) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COALESCE(MAX(version_number), -1) AS mx FROM chapter_versions WHERE chapter_id=?",
                (chapter_id,))
        mx = c.fetchone()["mx"]
        return int(mx) + 1

    def create_chapter_version(self, chapter_id: int, text: str,
                            make_active: bool | None = None) -> int:
        # make_active: True = force; False = never; None = only if no active yet
        if make_active is None and not self.get_active_version_id(chapter_id):
            make_active = True
        vn = self._next_version_number(chapter_id)
        norm = _norm_for_hash(text); h = _sha1(norm)
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO chapter_versions
            (chapter_id, version_number, text, text_hash, text_updated_at, format_updated_at)
            VALUES (?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """, (chapter_id, vn, text, h))
        ver_id = int(c.lastrowid)
        if make_active:
            c.execute("UPDATE chapters SET active_version_id=? WHERE id=?", (ver_id, chapter_id))
        self.conn.commit()
        return ver_id

    def chapter_version_create_and_activate(self, chapter_id: int, seed_from_version_id: int | None = None) -> int:
        if seed_from_version_id:
            row = self.chapter_version_row(seed_from_version_id)
            seed = row["text"] if row else ""
        else:
            # default seed from active version
            row = self.chapter_active_version_row(chapter_id)
            seed = row["text"] if row else ""
        return self.create_chapter_version(chapter_id, seed, make_active=True)

    def list_chapter_versions(self, chapter_id: int):
        c = self.conn.cursor()
        c.execute("""
            SELECT cv.id, cv.version_number, cv.text_hash, cv.text_updated_at, cv.format_updated_at,
                CASE WHEN ch.active_version_id=cv.id THEN 1 ELSE 0 END AS is_active
            FROM chapter_versions cv
            JOIN chapters ch ON ch.id=cv.chapter_id
            WHERE cv.chapter_id=?
            ORDER BY cv.version_number ASC
        """, (chapter_id,))
        return c.fetchall()

    def set_chapter_version_world_refs(self, chapter_version_id: int, world_ids: list[int]) -> None:
        c = self.conn.cursor()
        c.execute("DELETE FROM chapter_version_world_refs WHERE chapter_version_id=?", (chapter_version_id,))
        uniq = sorted(set(int(w) for w in world_ids))
        c.executemany("""INSERT OR IGNORE INTO chapter_version_world_refs(chapter_version_id, world_item_id)
                        VALUES (?,?)""", [(chapter_version_id, wid) for wid in uniq])
        self.conn.commit()

    def copy_version_refs_to_chapter(self, chapter_id: int, chapter_version_id: int) -> None:
        c = self.conn.cursor()
        c.execute("""SELECT world_item_id FROM chapter_version_world_refs
                    WHERE chapter_version_id=?""", (chapter_version_id,))
        ids = [r["world_item_id"] for r in c.fetchall()]
        self.set_chapter_world_refs(chapter_id, ids)

    def ensure_active_version(self, chapter_id: int) -> int:
        ver_id = self.get_active_version_id(chapter_id)
        if ver_id:
            return ver_id
        # seed from legacy chapters.content if present
        c = self.conn.cursor()
        c.execute("PRAGMA table_info(chapters)")
        has_content = any(col["name"] == "content" for col in c.fetchall())
        seed = ""
        if has_content:
            c.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,))
            row = c.fetchone()
            seed = row["content"] if row and row["content"] else ""
        return self.create_chapter_version(chapter_id, seed, make_active=True)

    def set_chapter_version_text(self, chapter_version_id: int, text: str) -> tuple[str, bool]:
        """Returns (new_hash, changed:bool)."""
        norm = _norm_for_hash(text); h = _sha1(norm)
        c = self.conn.cursor()

        # early-out if unchanged
        c.execute("SELECT text_hash, chapter_id FROM chapter_versions WHERE id=?", (chapter_version_id,))
        row = c.fetchone()
        if not row:
            return h, False
        if row["text_hash"] == h:
            return h, False

        # update version text/hash
        c.execute("""UPDATE chapter_versions
                    SET text=?, text_hash=?, text_updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""", (text, h, chapter_version_id))
        self.conn.commit()

        # if this version is active, refresh FTS
        chap_id = row["chapter_id"]
        c.execute("SELECT title, active_version_id FROM chapters WHERE id=?", (chap_id,))
        crow = c.fetchone()
        if crow and int(crow["active_version_id"] or 0) == int(chapter_version_id):
            self.chapters_fts_upsert(chap_id, crow["title"], text)

        return h, True

    def touch_chapter_version_format(self, chapter_version_id: int):
        c = self.conn.cursor()
        c.execute("""UPDATE chapter_versions
                    SET format_updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""", (chapter_version_id,))
        self.conn.commit()

    def set_active_chapter_version(self, chapter_id: int, version_id: int) -> None:
        c = self.conn.cursor()
        c.execute("UPDATE chapters SET active_version_id=? WHERE id=?", (version_id, chapter_id))
        self.conn.commit()
        # keep chapter-level refs in sync with the chosen active
        self.copy_version_refs_to_chapter(chapter_id, version_id)

    def get_active_version_id(self, chapter_id: int) -> int | None:
        c = self.conn.cursor()
        c.execute("SELECT active_version_id FROM chapters WHERE id=?", (chapter_id,))
        row = c.fetchone()
        return int(row["active_version_id"]) if row and row["active_version_id"] else None

    def chapter_version_row(self, version_id: int):
        c = self.conn.cursor()
        c.execute("SELECT * FROM chapter_versions WHERE id=?", (version_id,))
        return c.fetchone()

    def chapter_active_version_row(self, chapter_id: int):
        c = self.conn.cursor()
        c.execute("""
            SELECT cv.*
            FROM chapter_versions cv
            JOIN chapters ch ON ch.id=cv.chapter_id
            WHERE cv.chapter_id=? AND cv.id = ch.active_version_id
            LIMIT 1
        """, (chapter_id,))
        return c.fetchone()


    def set_chapter_title(self, chapter_id: int, title: str):
        c = self.conn.cursor()
        c.execute("""UPDATE chapters SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (title, chapter_id))
        self.conn.commit()

    def chapter_active_text_and_hash(self, chapter_id: int) -> tuple[str, str | None, int | None]:
        row = self.chapter_active_version_row(chapter_id)
        if not row:
            return "", None, None
        text      = row["text"] if "text" in row.keys() else ""
        text_hash = row["text_hash"] if "text_hash" in row.keys() else None
        ver_id    = row["id"] if "id" in row.keys() else None
        return (text or "", text_hash, ver_id)

    def chapter_active_version_id(self, chapter_id: int) -> int:
        c = self.conn.cursor()
        c.execute("""SELECT id FROM chapter_versions
                    WHERE chapter_id=? AND is_active=1
                    ORDER BY id LIMIT 1""", (chapter_id,))
        r = c.fetchone()
        if r: return int(r["id"])
        # Create one lazily if missing
        c.execute("INSERT INTO chapter_versions(chapter_id, is_active) VALUES (?,1)", (chapter_id,))
        self.conn.commit()
        return int(c.lastrowid)

    def outline_items_for_version(self, chver_id: int) -> list:
        c = self.conn.cursor()
        c.execute("""SELECT id, parent_id, order_key, text, tags, notes
                    FROM outline_items
                    WHERE chapter_version_id=?
                    ORDER BY parent_id IS NOT NULL, order_key, id""", (chver_id,))
        return c.fetchall()

    def outline_insert_item(self, chver_id: int, parent_id: int|None, order_key: float,
                            text: str, tags_json: str="[]", notes: str="") -> int:
        c = self.conn.cursor()
        c.execute("""INSERT INTO outline_items(chapter_version_id, parent_id, order_key, text, tags, notes)
                    VALUES (?,?,?,?,?,?)""", (chver_id, parent_id, order_key, text, tags_json, notes))
        self.conn.commit()
        return int(c.lastrowid)

    def outline_update_text(self, item_id: int, text: str) -> None:
        self.conn.execute("""UPDATE outline_items
                            SET text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""", (text, item_id))
        self.conn.commit()

    def outline_delete_items(self, item_ids: list[int]) -> None:
        if not item_ids: return
        q = ",".join("?"*len(item_ids))
        self.conn.execute(f"DELETE FROM outline_items WHERE id IN ({q})", item_ids)
        self.conn.commit()

    # ---- World categories/items/aliases/links (examples)
    def world_categories(self, project_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, parent_id, name, position
                     FROM world_categories
                     WHERE project_id=? AND COALESCE(deleted,0)=0
                     ORDER BY COALESCE(position,0), name, id""", (project_id,))
        return c.fetchall()
    
    def world_categories_top_level(self, project_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, name
                     FROM world_categories
                     WHERE project_id=? AND COALESCE(deleted,0)=0 AND parent_id IS NULL
                     ORDER BY position, id""", (project_id,))
        return c.fetchall()

    def world_categories_children(self, parent_id: int, project_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, name
                     FROM world_categories
                     WHERE project_id=? AND COALESCE(deleted,0)=0 AND parent_id=?
                     ORDER BY position, id""", (project_id, parent_id))
        return c.fetchall()
    
    def world_categories_count(self, project_id: int) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM world_categories WHERE project_id=? AND COALESCE(deleted,0)=0", (project_id,))
        return int(c.fetchone()[0] or 0)
    
    def world_category_insert(self, project_id: int, parent_id: Optional[int], name: str, position: Optional[int]=0) -> int:
        c = self.conn.cursor()
        c.execute("""INSERT INTO world_categories(project_id, parent_id, name, position)
                     VALUES (?,?,?,?)""",
                  (project_id, parent_id, name, position))
        self.conn.commit()
        return int(c.lastrowid)
    
    def world_category_insert_top_level(self, project_id: int, name: str, position: Optional[int]=0) -> int:
        return self.world_category_insert(project_id, None, name, position)
    
    def world_category(self, category_id: int) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT name FROM world_categories WHERE id=?", (category_id,))
        r = c.fetchone()
        return r["name"] if r else None
    
    def world_category_meta(self, category_id: int) -> Optional[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("SELECT id, parent_id, name, position FROM world_categories WHERE id=?", (category_id,))
        return c.fetchone()
    
    def world_category_rename(self, category_id: int, new_name: str) -> None:
        self.conn.execute("UPDATE world_categories SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (new_name, category_id))
        self.conn.commit()

    def world_category_soft_delete(self, category_id: int) -> None:
        self.conn.execute("UPDATE world_categories SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (category_id,))
        self.conn.commit()

    def traits_seed(self, project_id: int, data: dict) -> None:
        rows = []
        for facet_type, traits in data.items():
            rows.extend([(project_id, facet_type, l, i) for i,l in enumerate(traits) ])
        cur = self.conn.cursor()
        cur.executemany("INSERT OR IGNORE INTO facet_templates(project_id,kind,label,position) VALUES(?,?,?,?)",
            rows
        )
        self.conn.commit()

    def notes_tree_seed(self, project_id: int) -> None:
        """
        Seed a basic Notes tree structure for a new project, if it does not
        already have any notes_nodes. This is only intended for demo /
        first-run projects.
        """
        c = self.conn.cursor()
        row = c.execute(
            "SELECT id FROM notes_nodes WHERE project_id=? LIMIT 1",
            (project_id,),
        ).fetchone()
        if row:
            return  # project already has notes nodes; do not override

        # World Overview note under root
        overview_node_id = self.notes_node_insert(
            project_id=project_id,
            title="World Overview",
            node_kind="note",
            parent_node_id=None,
        )
        self.notes_doc_insert(
            node_id=overview_node_id,
            title="Overview",
            content_md="# World Overview\n\nUse this note to summarize the big picture of your world.",
        )

        # Religion category
        religion_id = self.notes_node_insert(
            project_id=project_id,
            title="Religion",
            node_kind="category",
            parent_node_id=None,
        )
        self.notes_doc_insert(
            node_id=religion_id,
            title="Overview",
            content_md="# Religion\n\nUse this area to describe pantheons, belief systems, and rituals.",
        )

        # Deities category under Religion
        deities_id = self.notes_node_insert(
            project_id=project_id,
            title="Deities",
            node_kind="category",
            parent_node_id=religion_id,
        )
        self.notes_doc_insert(
            node_id=deities_id,
            title="Overview",
            content_md="# Deities\n\nHigh-level notes about your gods, spirits, or cosmic beings.",
        )

        # Members container for Deities (characters only)
        deities_members_id = self.notes_node_insert(
            project_id=project_id,
            title="Members",
            node_kind="members_container",
            parent_node_id=deities_id,
            allowed_item_type="character",
            relationship_label="deity",
        )
        self.notes_doc_insert(
            node_id=deities_members_id,
            title="Overview",
            content_md="# Deity Members\n\nAttach character world items here to mark them as deities.",
        )

        # Cultures category
        cultures_id = self.notes_node_insert(
            project_id=project_id,
            title="Cultures",
            node_kind="category",
            parent_node_id=None,
        )
        self.notes_doc_insert(
            node_id=cultures_id,
            title="Overview",
            content_md="# Cultures\n\nNotes on cultures, societies, and social structures.",
        )

        # Magic / Systems category
        magic_id = self.notes_node_insert(
            project_id=project_id,
            title="Magic & Systems",
            node_kind="category",
            parent_node_id=None,
        )
        self.notes_doc_insert(
            node_id=magic_id,
            title="Overview",
            content_md="# Magic & Systems\n\nOutline magical systems, technologies, or other special systems here.",
        )

        # History / Timeline category
        history_id = self.notes_node_insert(
            project_id=project_id,
            title="History & Timeline",
            node_kind="category",
            parent_node_id=None,
        )
        self.notes_doc_insert(
            node_id=history_id,
            title="Overview",
            content_md="# History & Timeline\n\nMajor eras, events, and turning points.",
        )

    def alias_types_seed(self, project_id: int, aliases: Iterable[str] = ("nickname","pseudonym","title","alias")) -> None:
        c = self.conn.cursor()
        c.executemany("INSERT OR IGNORE INTO alias_types (project_id, name) VALUES (?, ?)", ((project_id, alias) for alias in aliases))
        self.conn.commit()

    def alias_types_for_project(self, project_id:int) -> list[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT name FROM alias_types WHERE project_id=? ORDER BY name", (project_id,))
        return [r[0] for r in cur.fetchall()]

    def alias_type_upsert(self, project_id:int, name:str):
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO alias_types(project_id,name) VALUES(?,?)", (project_id, name.strip()))
        self.conn.commit()

    def aliases_for_world_item(self, world_item_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("SELECT id, alias, alias_type, alias_norm FROM world_aliases WHERE world_item_id=? AND COALESCE(deleted,0)=0", (world_item_id,))
        return c.fetchall()

    def alias_id_by_alias(self, world_item_id: int, alias: str) -> Optional[int]:
        norm = _normalize_alias(alias)
        c = self.conn.cursor()
        c.execute("""
            SELECT id FROM world_aliases
            WHERE world_item_id=? AND alias_norm=? AND COALESCE(deleted,0)=0
            LIMIT 1
        """, (world_item_id, norm))
        r = c.fetchone()
        return int(r["id"]) if r else None

    def alias_exists(self, world_item_id: int, alias: str) -> bool:
        norm = _normalize_alias(alias)
        c = self.conn.cursor()
        c.execute("""
            SELECT 1 FROM world_aliases
            WHERE world_item_id=? AND alias_norm=? AND COALESCE(deleted,0)=0
            LIMIT 1
        """, (world_item_id, norm))
        print("alias:", alias, "norm:", norm)
        print([f'norm={r["alias_norm"]}' for r in self.aliases_for_world_item(world_item_id)])
        return c.fetchone() is not None

    def alias_add(self, world_item_id: int, alias: str, alias_type: str,
                *, status: str = "active", note: str | None = None, is_primary: int = 0) -> int:
        """
        Insert an alias for a world item. Returns alias_id.
        - alias_type: preserve your semantic type/category (e.g. 'surface', 'epithet', etc.)
        - status: 'active'|'defunct' (new column you added)
        - note: optional per-alias persona notes
        - is_primary: 1/0 (new column you added)
        """
        alias = (alias or "").strip()
        if not alias:
            return
        norm = _normalize_alias(alias)
        if self.alias_exists(world_item_id, alias):
            alias_id = self.alias_id_by_alias(world_item_id, alias)
            return alias_id  # silently ignore or raise
        c = self.conn.cursor()
        # avoid dup (active ones); allow same alias if previous was defunct
        row = c.execute("""SELECT id FROM world_aliases
                    WHERE world_item_id=? AND alias_norm=? AND COALESCE(deleted,0)=0 AND status='active'""",
                (world_item_id, norm)).fetchone()
        if row:
            return row["id"]  # silently ignore

        # insert new alias
        c.execute("""
            INSERT INTO world_aliases (world_item_id, alias, alias_type, alias_norm, status, note, is_primary, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (world_item_id, alias, alias_type, norm, status, note, int(is_primary)))
        self.conn.commit()
        return int(c.lastrowid)

    def alias_add_multiple(self, world_item_id: int, aliases: dict[str, str]) -> None:
        c = self.conn.cursor()
        c.executemany(
            "INSERT INTO world_aliases (world_item_id, alias, alias_type, alias_norm) VALUES (?, ?, ?, ?)", 
            ((world_item_id, alias, alias_type, _normalize_alias(alias)) for alias, alias_type in aliases.items())
        )
        self.conn.commit()

    def alias_update(self, alias_id: int, alias: str, alias_type: str) -> None:
        c = self.conn.cursor()
        c.execute("UPDATE world_aliases SET alias=?, alias_type=? WHERE id=?", (alias, alias_type, alias_id))
        self.conn.commit()

    def alias_update_type(self, alias_id: int, alias_type: str) -> None:
        c = self.conn.cursor()
        c.execute("UPDATE world_aliases SET alias_type=? WHERE id=?", (alias_type, alias_id))
        self.conn.commit()

    def alias_set_primary(self, world_item_id: int, alias_id: int = None, alias_title: str = None) -> None:
        if alias_id is None and alias_title is not None:
            alias_id = self.alias_id_by_alias(world_item_id, alias_title)
        c = self.conn.cursor()
        # unset all others
        c.execute("UPDATE world_aliases SET is_primary=0 WHERE world_item_id=?", (world_item_id,))
        # set this one
        c.execute("UPDATE world_aliases SET is_primary=1 WHERE id=?", (alias_id,))
        self.conn.commit()

    def alias_update_alias(self, alias_id: int, alias: str) -> bool:
        alias = (alias or "").strip()
        if not alias:
            return False
        norm = _normalize_alias(alias)
        # fetch world_item_id for dupe check
        c = self.conn.cursor()
        c.execute("SELECT world_item_id FROM world_aliases WHERE id=?", (alias_id,))
        row = c.fetchone()
        if not row:
            return False
        world_item_id = row[0]
        if self.alias_exists(world_item_id, alias):
            return False  # silently ignore or raise
        # prevent dupes against other aliases
        c.execute("""
            SELECT 1 FROM world_aliases
            WHERE world_item_id=? AND alias_norm=? AND id<>? AND COALESCE(deleted,0)=0
            LIMIT 1
        """, (world_item_id, norm, alias_id))
        if c.fetchone():
            return False

        c.execute("UPDATE world_aliases SET alias=?, alias_norm=? WHERE id=?", (alias, norm, alias_id))
        self.conn.commit()
        return True

    def alias_delete(self, alias_id: int) -> None:
        """Soft delete an alias."""
        c = self.conn.cursor()
        c.execute("UPDATE world_aliases SET deleted=1 WHERE id=?", (alias_id,))
        self.conn.commit()

    # ---- Tags / classification ----

    def entity_tags_for_project(self, project_id: int) -> list[sqlite3.Row]:
        """
        Return all tags defined for this project.
        """
        c = self.conn.cursor()
        c.execute(
            """SELECT id, project_id, name, description, visibility_default
               FROM entity_tags
               WHERE project_id=?
               ORDER BY lower(name), id""",
            (project_id,),
        )
        return c.fetchall()

    def entity_tag_upsert(
        self,
        project_id: int,
        name: str,
        description: str = "",
        visibility_default: str = "public",
    ) -> int:
        """
        Create or update a tag by (project_id, name). Returns tag_id.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Tag name cannot be empty")

        c = self.conn.cursor()
        row = c.execute(
            "SELECT id FROM entity_tags WHERE project_id=? AND lower(name)=lower(?)",
            (project_id, name),
        ).fetchone()

        if row:
            tag_id = int(row["id"])
            c.execute(
                "UPDATE entity_tags "
                "SET description=?, visibility_default=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (description, visibility_default, tag_id),
            )
            self.conn.commit()
            return tag_id

        c.execute(
            "INSERT INTO entity_tags(project_id, name, description, visibility_default) "
            "VALUES (?,?,?,?)",
            (project_id, name, description, visibility_default),
        )
        self.conn.commit()
        return int(c.lastrowid)

    def world_item_tags_for_item(self, world_item_id: int) -> list[sqlite3.Row]:
        """
        Return all tags attached to a world item, with tag names/descriptions.
        """
        c = self.conn.cursor()
        c.execute(
            """SELECT wit.id,
                      wit.world_item_id,
                      wit.tag_id,
                      wit.source,
                      et.name   AS tag_name,
                      et.description
               FROM world_item_tags wit
               JOIN entity_tags et ON et.id = wit.tag_id
               WHERE wit.world_item_id=?
               ORDER BY et.name""",
            (world_item_id,),
        )
        return c.fetchall()

    def world_item_tag_add(
        self,
        world_item_id: int,
        tag_id: int,
        source: str | None = None,
    ) -> int:
        """
        Attach a tag to a world item. Returns world_item_tags.id.
        If the tag is already present, returns the existing row id.
        """
        c = self.conn.cursor()
        row = c.execute(
            "SELECT id FROM world_item_tags WHERE world_item_id=? AND tag_id=?",
            (world_item_id, tag_id),
        ).fetchone()
        if row:
            return int(row["id"])

        c.execute(
            "INSERT INTO world_item_tags(world_item_id, tag_id, source) VALUES (?,?,?)",
            (world_item_id, tag_id, source),
        )
        self.conn.commit()
        return int(c.lastrowid)

    def world_item_tag_remove(self, world_item_id: int, tag_id: int) -> None:
        """
        Remove a tag from a world item.
        """
        c = self.conn.cursor()
        c.execute(
            "DELETE FROM world_item_tags WHERE world_item_id=? AND tag_id=?",
            (world_item_id, tag_id),
        )
        self.conn.commit()

    # ---- Notes tree / world notes ----

    def notes_nodes_for_project(self, project_id: int) -> list[sqlite3.Row]:
        """
        Return all notes_nodes for a project. You can group/filter in Python.
        """
        c = self.conn.cursor()
        c.execute(
            "SELECT * FROM notes_nodes WHERE project_id=? "
            "ORDER BY parent_node_id, position, id",
            (project_id,),
        )
        return c.fetchall()

    def notes_children(self, project_id: int, parent_node_id: int | None) -> list[sqlite3.Row]:
        """
        Convenience helper: return children of a given parent (or root nodes if parent_node_id is None).
        """
        c = self.conn.cursor()
        if parent_node_id is None:
            c.execute(
                "SELECT * FROM notes_nodes "
                "WHERE project_id=? AND parent_node_id IS NULL "
                "ORDER BY position, id",
                (project_id,),
            )
        else:
            c.execute(
                "SELECT * FROM notes_nodes "
                "WHERE project_id=? AND parent_node_id=? "
                "ORDER BY position, id",
                (project_id, parent_node_id),
            )
        return c.fetchall()

    def notes_node_get(self, node_id: int) -> sqlite3.Row | None:
        c = self.conn.cursor()
        c.execute("SELECT * FROM notes_nodes WHERE id=?", (node_id,))
        return c.fetchone()

    def notes_node_insert(
        self,
        project_id: int,
        title: str,
        node_kind: str,
        parent_node_id: int | None = None,
        allowed_item_type: str | None = None,
        relationship_label: str | None = None,
        implied_tag_id: int | None = None,
        implied_facet_kind: str | None = None,
        position: int | None = None,
    ) -> int:
        """
        Insert a notes_nodes row. If position is None, append after existing siblings.
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("Notes node title cannot be empty")

        c = self.conn.cursor()
        if position is None:
            if parent_node_id is None:
                row = c.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 "
                    "FROM notes_nodes "
                    "WHERE project_id=? AND parent_node_id IS NULL",
                    (project_id,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 "
                    "FROM notes_nodes "
                    "WHERE project_id=? AND parent_node_id=?",
                    (project_id, parent_node_id),
                ).fetchone()
            position = int(row[0] if row[0] is not None else 0)

        c.execute(
            """INSERT INTO notes_nodes(
                   project_id,
                   parent_node_id,
                   title,
                   node_kind,
                   position,
                   allowed_item_type,
                   relationship_label,
                   implied_tag_id,
                   implied_facet_kind
               )
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                parent_node_id,
                title,
                node_kind,
                position,
                allowed_item_type,
                relationship_label,
                implied_tag_id,
                implied_facet_kind,
            ),
        )
        self.conn.commit()
        return int(c.lastrowid)

    def notes_docs_for_node(self, node_id: int) -> list[sqlite3.Row]:
        """
        Return all tabs (notes_docs) for a node, ordered by position.
        """
        c = self.conn.cursor()
        c.execute(
            "SELECT * FROM notes_docs WHERE node_id=? ORDER BY position, id",
            (node_id,),
        )
        return c.fetchall()

    def notes_doc_insert(
        self,
        node_id: int,
        title: str,
        content_md: str = "",
        content_render: str | None = None,
        position: int | None = None,
        visibility: str = "public",
    ) -> int:
        """
        Insert a notes_docs row. If position is None, append after existing tabs.
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("Notes doc title cannot be empty")

        c = self.conn.cursor()
        if position is None:
            row = c.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM notes_docs WHERE node_id=?",
                (node_id,),
            ).fetchone()
            position = int(row[0] if row[0] is not None else 0)

        if content_render is None:
            html = md_to_html(content_md or "", css=None, include_scaffold=False)
        else:
            html = content_render

        c.execute(
            "INSERT INTO notes_docs(node_id, title, position, content_md, content_render, visibility) "
            "VALUES (?,?,?,?,?,?)",
            (node_id, title, position, content_md, html, visibility),
        )
        self.conn.commit()
        return int(c.lastrowid)
    
    def notes_doc_get(self, doc_id: int) -> sqlite3.Row | None:
        c = self.conn.cursor()
        c.execute("SELECT * FROM notes_docs WHERE id=?", (doc_id,))
        return c.fetchone()

    def notes_doc_update_content(self, doc_id: int, content_md: str) -> None:
        html = md_to_html(content_md or "", css=None, include_scaffold=False)
        c = self.conn.cursor()
        c.execute(
            """UPDATE notes_docs
               SET content_md=?, content_render=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (content_md, html, doc_id),
        )
        self.conn.commit()

    def notes_doc_delete(self, doc_id: int) -> None:
        c = self.conn.cursor()
        c.execute("DELETE FROM notes_docs WHERE id=?", (doc_id,))
        self.conn.commit()

    def note_members_for_node(self, node_id: int) -> list[sqlite3.Row]:
        """
        Return all members for a membership node, joined with world item title/type.
        """
        c = self.conn.cursor()
        c.execute(
            """SELECT nm.id,
                      nm.node_id,
                      nm.world_item_id,
                      nm.position,
                      wi.title AS world_title,
                      wi.type  AS world_type
               FROM note_members nm
               JOIN world_items wi ON wi.id = nm.world_item_id
               WHERE nm.node_id=?
               ORDER BY nm.position, wi.title""",
            (node_id,),
        )
        return c.fetchall()

    def note_member_add(
        self,
        node_id: int,
        world_item_id: int,
        position: int | None = None,
    ) -> int:
        """
        Attach a world item to a membership node.
        - Enforces allowed_item_type (for now: block on mismatch).
        - Applies implied_tag_id if present on the node.
        """
        c = self.conn.cursor()

        node = c.execute(
            "SELECT project_id, node_kind, allowed_item_type, implied_tag_id "
            "FROM notes_nodes WHERE id=?",
            (node_id,),
        ).fetchone()
        if node is None:
            raise ValueError(f"Notes node {node_id} does not exist")

        item = c.execute(
            "SELECT project_id, type FROM world_items WHERE id=?",
            (world_item_id,),
        ).fetchone()
        if item is None:
            raise ValueError(f"World item {world_item_id} does not exist")

        allowed = node["allowed_item_type"]
        if allowed and item["type"] != allowed:
            raise ValueError(
                f"World item type {item['type']!r} does not match allowed_item_type {allowed!r}"
            )

        # Avoid duplicates; unique index will also enforce this.
        existing = c.execute(
            "SELECT id FROM note_members WHERE node_id=? AND world_item_id=?",
            (node_id, world_item_id),
        ).fetchone()
        if existing:
            return int(existing["id"])

        if position is None:
            row = c.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM note_members WHERE node_id=?",
                (node_id,),
            ).fetchone()
            position = int(row[0] if row and row[0] is not None else 0)

        c.execute(
            "INSERT INTO note_members(node_id, world_item_id, position) VALUES (?,?,?)",
            (node_id, world_item_id, position),
        )
        nm_id = int(c.lastrowid)

        # Apply implied tag if present
        tag_id = node["implied_tag_id"]
        if tag_id:
            # This will no-op if the tag is already attached
            self.world_item_tag_add(world_item_id, tag_id, source=f"notes-node:{node_id}")

        self.conn.commit()
        return nm_id

    def note_member_remove(self, node_id: int, world_item_id: int) -> None:
        """
        Remove a world item from a membership node.
        If the node has implied_tag_id and there are no other memberships
        that imply that tag for this item, remove the tag from the item.
        """
        c = self.conn.cursor()

        node = c.execute(
            "SELECT implied_tag_id FROM notes_nodes WHERE id=?",
            (node_id,),
        ).fetchone()
        implied_tag_id = node["implied_tag_id"] if node else None

        c.execute(
            "DELETE FROM note_members WHERE node_id=? AND world_item_id=?",
            (node_id, world_item_id),
        )

        if implied_tag_id:
            # Check whether any other membership still implies this tag
            row = c.execute(
                """
                SELECT 1
                FROM note_members nm
                JOIN notes_nodes nn ON nn.id = nm.node_id
                WHERE nm.world_item_id=? AND nn.implied_tag_id=?
                LIMIT 1
                """,
                (world_item_id, implied_tag_id),
            ).fetchone()
            if not row:
                # For now, remove the tag entirely. When we later add manual tag UI,
                # we can refine this to only remove tags with source='notes-node:*'.
                self.world_item_tag_remove(world_item_id, implied_tag_id)

        self.conn.commit()

    # ---- World items ----

    def world_items_by_type(self, project_id: int, item_type: str | None) -> list[sqlite3.Row]:
        """
        Return world items of a given type for a project (ignores deleted).
        If item_type is falsy, returns all types.
        """
        c = self.conn.cursor()
        if item_type:
            c.execute(
                """SELECT id, title, COALESCE(type,'') AS kind
                   FROM world_items
                   WHERE project_id=? AND COALESCE(deleted,0)=0 AND type=?
                   ORDER BY LOWER(title)""",
                (project_id, item_type),
            )
        else:
            c.execute(
                """SELECT id, title, COALESCE(type,'') AS kind
                   FROM world_items
                   WHERE project_id=? AND COALESCE(deleted,0)=0
                   ORDER BY LOWER(title)""",
                (project_id,),
            )
        return c.fetchall()

    def world_items_by_category(self, project_id: int, category_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, title
                     FROM world_items
                     WHERE project_id=? AND category_id=? AND COALESCE(deleted,0)=0
                     ORDER BY position, id""", (project_id, category_id))
        return c.fetchall()

    def world_items_grouped(self):
        q = """
        SELECT id, title, COALESCE(type,'') AS kind
        FROM world_items
        WHERE COALESCE(deleted,0)=0
        ORDER BY LOWER(title)
        """
        return self.conn.execute(q).fetchall()

    def world_item_is_character(self, world_item_id: int) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT type FROM world_items WHERE id=?", (world_item_id,))
        r = c.fetchone()
        return r["type"] == "character"

    def world_item(self, world_item_id: int) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT title FROM world_items WHERE id=?", (world_item_id,))
        r = c.fetchone()
        return r["title"] if r else None

    def world_item_meta(self, world_item_id: int) -> Optional[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("SELECT id, category_id, title, type, content_md, content_render FROM world_items WHERE id=?", (world_item_id,))
        return c.fetchone()

    def world_item_type(self, world_item_id: int) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT type FROM world_items WHERE id=?", (world_item_id,))
        r = c.fetchone()
        return r["type"] if r else None

    def world_items_list_for_kind(self, project_id: int, kind: str) -> list[int]:
        c = self.conn.cursor()
        c.execute("""SELECT id, title FROM world_items
                    WHERE project_id=? AND type=? AND COALESCE(deleted,0)=0
                    ORDER BY position, id""", (project_id, kind))
        return c.fetchall()
    
    def world_item_list_ids(self, project_id: int, category_id: int) -> list[int]:
        c = self.conn.cursor()
        c.execute("""SELECT id FROM world_items
                    WHERE project_id=? AND category_id=? AND COALESCE(deleted,0)=0
                    ORDER BY position, id""", (project_id, category_id))
        return [int(r[0]) for r in c.fetchall()]

    def world_item_insert_at_index(self, project_id: int, category_id: int,
                                title: str, insert_index: int, item_type: str) -> int:
        ids = self.world_item_list_ids(project_id, category_id)
        insert_index = max(0, min(insert_index, len(ids)))
        # temp append; we’ll rewrite positions anyway
        c = self.conn.cursor()
        c.execute("""INSERT INTO world_items(project_id, category_id, title, content_md, content_render, position, type)
                    VALUES (?,?,?,?,?,?,?)""", (project_id, category_id, title.strip(), "", "", 0, item_type))
        new_id = int(c.lastrowid)

        ids.insert(insert_index, new_id)
        for pos, wid in enumerate(ids):
            c.execute("UPDATE world_items SET position=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (pos, wid))
        self.conn.commit()
        return new_id

    # TODO: figure out what to do with category_id when none is given
    #       maybe `item_type` can determine default category?
    def world_item_insert(self,
        project_id: int,
        category_id: int | None = None,
        title: str = "",
        item_type: str = "",
        content_md: str = "",
        aliases: dict[str, str] | None = None,
    ) -> int:
        """
        Create a new world item.

        - category_id can be None (no world_category row required).
        - Sorting / grouping can be done by item_type instead of category.
        - Returns the new world_item id.
        """
        if aliases is None:
            aliases = {}

        title = (title or "").strip()
        if not title:
            raise ValueError("world_item_insert: title cannot be empty")

        content_md = content_md or ""
        html = md_to_html(content_md, css=None, include_scaffold=False)

        c = self.conn.cursor()
        c.execute(
            """
            INSERT INTO world_items
                (project_id, category_id, title, type, content_md, content_render)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, category_id, title, item_type.strip(), content_md.strip(), html),
        )
        wid = int(c.lastrowid)
        self.conn.commit()

        # add aliases
        if title not in aliases:
            aliases[title] = "alias"
        self.alias_add_multiple(wid, aliases)
        self.alias_set_primary(wid, alias_title=title)

        return wid

    def world_item_update_content(self, world_item_id: int, md: str) -> None:
        self.conn.execute("""UPDATE world_items
                             SET content_md=?, updated_at=CURRENT_TIMESTAMP
                             WHERE id=?""", (md, world_item_id))
        self.conn.commit()
    
    def world_item_update(self, world_item_id: int, title: Optional[str]=None, position: Optional[int]=None, content_md: Optional[str]=None) -> None:
        self.conn.execute("""UPDATE world_items
                             SET title=COALESCE(?, title), position=COALESCE(?, position), content_md=COALESCE(?, content_md), updated_at=CURRENT_TIMESTAMP
                             WHERE id=?""", (title, position, content_md, world_item_id))
        self.conn.commit()

    def world_item_rename(self, world_item_id: int, new_title: str) -> None:
        self.conn.execute("UPDATE world_items SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (new_title, world_item_id))
        self.conn.commit()

    def world_item_soft_delete(self, world_item_id: int) -> None:
        self.conn.execute("UPDATE world_items SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (world_item_id,))
        self.conn.commit()

    # --- World aliases/titles (phrases to world IDs)
    def world_phrases_for_project_detailed(self, project_id: int, ids: list[int] | None = None):
        """
        Return [(phrase_norm, world_item_id, alias_id)] for ACTIVE aliases in this project.
        If ids is provided, limit to those world_item ids.
        If ids is an empty list, returns no results.
        """
        c = self.conn.cursor()
        sql = """
            SELECT LOWER(TRIM(REPLACE(wa.alias, '  ', ' '))) AS phrase_norm,
                wa.world_item_id AS world_item_id,
                wa.id AS alias_id
            FROM world_aliases wa
            JOIN world_items wi ON wi.id=wa.world_item_id
            WHERE wi.project_id=? AND COALESCE(wi.deleted,0)=0
            AND COALESCE(wa.deleted,0)=0
            AND COALESCE(wa.status,'active')='active'
        """
        params = [project_id]
        if ids is not None:
            if ids == []:
                return []
            placeholders = ",".join("?" for _ in ids)
            sql += f" AND wa.world_item_id IN ({placeholders})"
            params.extend(int(x) for x in ids)
        sql += " ORDER BY LENGTH(wa.alias) DESC, wa.world_item_id"
        c.execute(sql, params)
        return [(r["phrase_norm"], int(r["world_item_id"]), int(r["alias_id"])) for r in c.fetchall()]

    def set_chapter_world_refs(self, chapter_id: int, world_ids: Sequence[int]) -> None:
        """
        Replaces all refs for `chapter_id` with the provided `world_ids` (deduped).
        """
        c = self.conn.cursor()
        c.execute("DELETE FROM chapter_world_refs WHERE chapter_id=?", (chapter_id,))
        uniq = sorted(set(int(w) for w in world_ids))
        c.executemany("""INSERT OR IGNORE INTO chapter_world_refs(chapter_id, world_item_id)
                        VALUES (?,?)""", [(chapter_id, wid) for wid in uniq])
        self.conn.commit()


    # --- Ingest candidates (basic helpers)

    def ingest_candidates_by_chapter(self, chapter_id: int, version_id: int | None = None,
                                    statuses: tuple[str,...] = ("pending",)):
        """Fetch all ingest candidates for a chapter (specify version or use active version)."""
        if isinstance(statuses, str):
            statuses = (statuses,)
        if version_id is None:
            version_id = self.get_active_version_id(chapter_id)
        project_id = self.chapter_project_id(chapter_id)
        candidates = self.candidates_for_scope(project_id=project_id, scope_type="chapter", scope_id=chapter_id,
                                        version_id=version_id, statuses=statuses, columns="*")
        print(f"ingest_candidates_by_chapter: found {len(candidates)} candidates for chapter_id={chapter_id}, version_id={version_id}")
        # print db's candidates
        print([f'id={r["id"]} cand="{r["candidate"]}"' for r in candidates])
        return candidates

    def ingest_candidate_upsert(self, *, project_id: int, scope_type: str, scope_id: int,
                                version_id: int | None, candidate: str,
                                kind_guess: str | None = None, source: str | None = None,
                                confidence: float | None = None, status: str = "pending",
                                start_off: int | None = None, end_off: int | None = None,
                                context: str | None = None) -> int:
        """
        Upsert by (project_id, scope_type, scope_id, version_id, candidate).
        Returns the candidate id. Adds debug prints so we can see what's happening.
        """
        cand = (candidate or "").strip()
        if not cand:
            return 0

        c = self.conn.cursor()

        # 1) Try find existing
        row = c.execute("""
            SELECT id, kind_guess, source, confidence, status
            FROM ingest_candidates
            WHERE project_id=? AND scope_type=? AND scope_id=? AND COALESCE(version_id,-1)=COALESCE(?, -1)
            AND candidate=?
        """, (project_id, scope_type, scope_id, version_id, cand)).fetchone()

        if row:
            cid = int(row[0])
            # 2) UPDATE only the fields we allow to evolve (no schema tokens here to avoid SQL errors)
            c.execute("""
                UPDATE ingest_candidates
                SET kind_guess=COALESCE(?, kind_guess),
                    source=COALESCE(?, source),
                    confidence=COALESCE(?, confidence),
                    status=COALESCE(?, status),
                    start_off=COALESCE(?, start_off),
                    end_off=COALESCE(?, end_off),
                    context=COALESCE(?, context),
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (kind_guess, source, confidence, status, start_off, end_off, context, cid))
            self.conn.commit()
            print(f"[ingest_candidate_upsert] UPDATE cid={cid} candidate='{cand}' "
                f"scope=({scope_type},{scope_id},{version_id}) src={source} conf={confidence} status={status}")
            return cid

        # 3) INSERT new
        c.execute("""
            INSERT INTO ingest_candidates
                (project_id, scope_type, scope_id, version_id,
                candidate, kind_guess, source, confidence, status,
                start_off, end_off, context,
                target_world_item_id, created_at, updated_at)
            VALUES (?,?,?,?, ?,?,?,?, ?,?,?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (project_id, scope_type, scope_id, version_id,
            cand, kind_guess, source, confidence, status,
            start_off, end_off, context))
        cid = int(c.lastrowid)
        self.conn.commit()
        print(f"[ingest_candidate_upsert] INSERT cid={cid} candidate='{cand}' "
            f"scope=({scope_type},{scope_id},{version_id}) src={source} conf={confidence} status={status} start_off={start_off} end_off={end_off} context={context}")
        return cid

    def ingest_candidate_mark_resolved(self, cand_id: int, *, target_world_item_id: int, status: str) -> None:
        cur = self.conn.cursor()
        cur.execute("""
            UPDATE ingest_candidates
            SET target_world_item_id=?, status=?
            WHERE id=?""", (target_world_item_id, status, cand_id))
        self.conn.commit()

    def ingest_candidate_mark_dismissed(self, cand_id: int) -> None:
        c = self.conn.cursor()
        c.execute("""
            UPDATE ingest_candidates
            SET status='dismissed', target_world_item_id=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (cand_id,))
        self.conn.commit()

    def ingest_candidate_link_world(self, candidate_id: int, world_item_id: int):
        self.conn.execute("UPDATE ingest_candidates SET link_world_id=? WHERE id=?",
                (world_item_id, candidate_id))
        self.conn.commit()

    # --- Linker support ----------------------------------------------------
    def known_world_phrases(self, project_id):
        """
        Return [(wid, alias_id_or_None, phrase_lower)] for titles + ACTIVE aliases.
        """
        cur = self.conn.cursor()
        out = []
        # Titles
        cur.execute("SELECT id, title FROM world_items WHERE project_id=? AND COALESCE(deleted,0)=0", (project_id,))
        for wid, title in cur.fetchall():
            if title:
                out.append((int(wid), None, title.strip().lower()))
        # Active aliases
        cur.execute("""
            SELECT wa.world_item_id, wa.id, wa.alias
            FROM world_aliases wa
            JOIN world_items wi ON wi.id=wa.world_item_id
            WHERE wi.project_id=? AND COALESCE(wi.deleted,0)=0
            AND COALESCE(wa.deleted,0)=0
            AND COALESCE(wa.status,'active')='active'
            AND wa.alias IS NOT NULL AND TRIM(wa.alias)!=''
        """, (project_id,))
        for wid, alias_id, alias in cur.fetchall():
            out.append((int(wid), int(alias_id), alias.strip().lower()))
        return out

    def candidates_for_scope(self, *, project_id: int,
                            scope_type: str, scope_id: int,
                            version_id: int | None = None,
                            statuses: Sequence[str]=("pending",),
                            columns: str = "*") -> list[sqlite3.Row]:
        """
        columns: SQL SELECT list. Always alias computed fields, e.g. COALESCE(source,'') AS source
        """
        # allow string
        if isinstance(statuses, str):
            statuses = (statuses,)
        c = self.conn.cursor()
        ph = ",".join("?" * len(statuses))
        if version_id is None:
            sql = f"""
                SELECT {columns}
                FROM ingest_candidates
                WHERE project_id=? AND scope_type=? AND scope_id=?
                AND COALESCE(status,'pending') IN ({ph})
            """
            params = (project_id, scope_type, scope_id, *statuses)
        else:
            sql = f"""
                SELECT {columns}
                FROM ingest_candidates
                WHERE project_id=? AND scope_type=? AND scope_id=? AND version_id=?
                AND COALESCE(status,'pending') IN ({ph})
            """
            params = (project_id, scope_type, scope_id, version_id, *statuses)
        print(f"[candidates_for_scope] {sql.strip()}  params={params}")
        rows = c.execute(sql, params).fetchall()
        print(f"[candidates_for_scope] returned {len(rows)} rows")
        return rows

    def chapter_candidates_basic(self, scope_id, statuses=("pending","linked")):
        """
        Return [(id, label, source)] for chapter-scoped ingest candidates in the given statuses.
        """
        cur = self.conn.cursor()
        placeholders = ",".join("?" for _ in statuses)
        cur.execute(f"""
            SELECT id, label, source
            FROM ingest_candidates
            WHERE scope_type='chapter' AND scope_id=? AND COALESCE(status,'pending') IN ({placeholders})
        """, (scope_id, *statuses))
        return [(int(cid), (label or "").strip(), (source or "").lower()) for cid, label, source in cur.fetchall()]

    def ingest_candidate_row(self, cand_id):
        """
        Return a tuple with (project_id, scope_type, scope_id, version_id, candidate,
                kind_guess, COALESCE(source,''), confidence, status, target_world_item_id).
        """
        cur = self.conn.cursor()
        row = cur.execute("""
            SELECT project_id, scope_type, scope_id, version_id, candidate,
                kind_guess, COALESCE(source,''), confidence, status, target_world_item_id
            FROM ingest_candidates
            WHERE id=?""", (cand_id,)).fetchone()
        return row  # tuple

    def alias_note(self, alias_id):
        cur = self.conn.cursor()
        row = cur.execute("SELECT note FROM world_aliases WHERE id=?", (alias_id,)).fetchone()
        return row[0] if row else None

    def world_item_md(self, world_item_id):
        cur = self.conn.cursor()
        row = cur.execute("SELECT content_md FROM world_items WHERE id=?", (world_item_id,)).fetchone()
        return row[0] if row else ""

    def fetch_text_for_doc(self, *, doc_type, doc_id, version_id=None):
        """
        doc_type: 'chapter', 'world_item', 'note', 'outline' (extend as you add)
        """
        if doc_type == "chapter":
            return self.chapter_content(doc_id, version_id=version_id)
        if doc_type == "world_item":
            return self.world_item_md(doc_id)
        # TODO: implement when notes/outlines have tables
        return ""

    def set_world_item_refs(self, world_item_id: int, world_ids: list[int]):
        """
        Cache 'which world items are referenced by this world_item's text' (ID list).
        Create the table on the fly (in-memory DB makes this painless).
        """
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS world_item_refs (
                world_item_id INTEGER PRIMARY KEY,
                ids_csv TEXT
            )
        """)
        csv = ",".join(str(int(w)) for w in sorted(set(world_ids or [])))
        cur.execute("""
            INSERT INTO world_item_refs(world_item_id, ids_csv)
            VALUES(?, ?)
            ON CONFLICT(world_item_id) DO UPDATE SET ids_csv=excluded.ids_csv
        """, (world_item_id, csv))
        self.conn.commit()

    def set_doc_refs(self, *, doc_type, doc_id, version_id=None, world_ids: list[int]):
        if doc_type == "chapter":
            # assumes you already have these methods
            self.set_chapter_version_world_refs(version_id, sorted(set(world_ids or [])))
            av = self.get_active_version_id(doc_id)
            if av and int(av) == int(version_id):
                self.set_chapter_world_refs(doc_id, sorted(set(world_ids or [])))
        elif doc_type == "world_item":
            self.set_world_item_refs(doc_id, world_ids or [])
        else:
            # TODO: extend when notes/outlines have a cache table
            pass

    def world_item_render_update(self, world_item_id, html_content):
        cur = self.conn.cursor()
        cur.execute("UPDATE world_items SET content_render=? WHERE id=?", (html_content, world_item_id))
        self.conn.commit()

    def chapter_version_render_update(self, version_id, html_content):
        cur = self.conn.cursor()
        cur.execute("""
            UPDATE chapter_versions
            SET content_render=?, format_updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (html_content, version_id))
        self.conn.commit()

    # --- Metrics cache ----------------------------------------------------------

    def metrics_get(self, chapter_id: int, chapter_version_id: int, source_hash: str):
        c = self.conn.cursor()
        c.execute("""SELECT * FROM chapter_metrics
                    WHERE chapter_id=? AND chapter_version_id=? AND source_hash=? LIMIT 1""",
                (chapter_id, chapter_version_id, source_hash))
        return c.fetchone()

    def metrics_upsert(self, chapter_id: int, chapter_version_id: int, source_hash: str, m: dict):
        c = self.conn.cursor()
        # try update
        c.execute("""UPDATE chapter_metrics
                    SET word_count=?, char_count=?, paragraph_count=?, sentence_count=?,
                        avg_sentence_len=?, type_token_ratio=?, dialogue_words=?, dialogue_ratio=?,
                        reading_secs=?, est_pages=?, updated_at=CURRENT_TIMESTAMP
                    WHERE chapter_id=? AND chapter_version_id=? AND source_hash=?""",
                (m["word_count"], m["char_count"], m["paragraph_count"], m["sentence_count"],
                m["avg_sentence_len"], m["type_token_ratio"], m["dialogue_words"], m["dialogue_ratio"],
                m["reading_secs"], m["est_pages"], chapter_id, chapter_version_id, source_hash))
        if c.rowcount == 0:
            c.execute("""INSERT INTO chapter_metrics
                        (chapter_id, chapter_version_id, source_hash, word_count, char_count,
                        paragraph_count, sentence_count, avg_sentence_len, type_token_ratio,
                        dialogue_words, dialogue_ratio, reading_secs, est_pages)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (chapter_id, chapter_version_id, source_hash, m["word_count"], m["char_count"],
                    m["paragraph_count"], m["sentence_count"], m["avg_sentence_len"],
                    m["type_token_ratio"], m["dialogue_words"], m["dialogue_ratio"],
                    m["reading_secs"], m["est_pages"]))
        self.conn.commit()

    # --- Characters ---
    def character_facets(self, character_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT * FROM character_facets
                    WHERE character_id=? ORDER BY position, id""", (character_id,))
        return c.fetchall()
    
    def character_facet_exists(self, character_id: int, facet_type: str, label: str) -> bool:
        c = self.conn.cursor()
        c.execute("""
            SELECT 1 FROM character_facets
            WHERE character_id=? AND facet_type=? AND lower(trim(label))=lower(trim(?))
            AND COALESCE(deleted,0)=0
            LIMIT 1
        """, (character_id, facet_type, label))
        return c.fetchone() is not None

    def character_facets_by_type(self, character_id: int, facet_type: str) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT * FROM character_facets
                    WHERE character_id=? AND facet_type=?
                    ORDER BY position, id""", (character_id, facet_type))
        return c.fetchall()

    def character_facet_insert(self, character_id: int, facet_type: str,
                            label: str = "", value: str = "", note: str = "",
                            link_world_id: int | None = None,
                            status: str | None = None, priority: int | None = None,
                            due_chapter_id: int | None = None, insert_index: int | None = None) -> int:
        # compute position
        c = self.conn.cursor()
        c.execute("""SELECT id FROM character_facets WHERE character_id=? ORDER BY position, id""",
                (character_id,))
        ids = [r[0] for r in c.fetchall()]
        if insert_index is None:
            insert_index = len(ids)
        insert_index = max(0, min(insert_index, len(ids)))

        # temp insert; rewrite positions
        c.execute("""INSERT INTO character_facets
                    (character_id, facet_type, label, value, note, link_world_id, status, priority, due_chapter_id, position)
                    VALUES (?,?,?,?,?,?,?,?,?,0)""",
                (character_id, facet_type, label.strip(), value.strip(), note.strip() if note else "",
                link_world_id, status, priority, due_chapter_id))
        new_id = int(c.lastrowid)
        ids.insert(insert_index, new_id)

        for pos, fid in enumerate(ids):
            c.execute("""UPDATE character_facets SET position=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (pos, fid))
        self.conn.commit()
        return new_id

    def character_facet_update(self, facet_id: int, **fields) -> None:
        if not fields:
            return
        cols = []
        vals = []
        for k,v in fields.items():
            cols.append(f"{k}=?")
            vals.append(v)
        cols.append("updated_at=CURRENT_TIMESTAMP")
        sql = f"UPDATE character_facets SET {', '.join(cols)} WHERE id=?"
        vals.append(facet_id)
        self.conn.execute(sql, tuple(vals))
        self.conn.commit()

    def character_facet_delete(self, facet_id: int) -> None:
        # remove and compact positions
        c = self.conn.cursor()
        c.execute("SELECT character_id FROM character_facets WHERE id=?", (facet_id,))
        row = c.fetchone()
        if not row:
            return
        char_id = int(row[0])
        c.execute("DELETE FROM character_facets WHERE id=?", (facet_id,))
        c.execute("""SELECT id FROM character_facets
                    WHERE character_id=? ORDER BY position, id""", (char_id,))
        ids = [r[0] for r in c.fetchall()]
        for pos, fid in enumerate(ids):
            c.execute("UPDATE character_facets SET position=? WHERE id=?", (pos, fid))
        self.conn.commit()

    def character_facets_reorder(self, character_id: int, new_order_ids: list[int]) -> None:
        c = self.conn.cursor()
        # ensure all belong to character_id
        q = ",".join("?" for _ in new_order_ids)
        c.execute(f"SELECT id FROM character_facets WHERE character_id=? AND id IN ({q})",
                (character_id, *new_order_ids))
        got = {int(r[0]) for r in c.fetchall()}
        if got != set(new_order_ids):
            # ignore or raise; here we ignore to be resilient
            pass
        for pos, fid in enumerate(new_order_ids):
            c.execute("UPDATE character_facets SET position=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (pos, fid))
        self.conn.commit()

    def facet_template_labels(self, project_id:int, kind:str) -> list[str]:
        cur = self.conn.cursor()
        cur.execute("""SELECT label FROM facet_templates
                    WHERE project_id=? AND kind=? ORDER BY position, id""", (project_id, kind))
        return [r[0] for r in cur.fetchall()]

    # ---- UI Preferences (per-project key-value store)
    def ui_pref_get(self, project_id: int, key: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM ui_prefs WHERE project_id=? AND key=?", (project_id, key))
        row = cur.fetchone()
        return (row["value"] if hasattr(row, "keys") and "value" in row.keys() else row[0]) if row else None

    def ui_pref_set(self, project_id: int, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO ui_prefs(project_id, key, value) VALUES(?,?,?) "
            "ON CONFLICT(project_id, key) DO UPDATE SET value=excluded.value",
            (project_id, key, value),
        )
        self.conn.commit()

    # ---- FTS (chapter & world)
    def fts_rebuild(self) -> None:
        """Rebuild FTS tables if they exist."""
        if self._has_table("fts_chapters"):
            self.conn.execute("INSERT INTO chapters_fts(chapters_fts) VALUES('rebuild')")
            self.conn.execute("INSERT INTO world_items_fts(world_items_fts) VALUES('rebuild')")
            self.conn.commit()

    def chapters_fts_upsert(self, chapter_id: int, title: str, text: str) -> None:
        """Refresh FTS row for a chapter. No-op if FTS table doesn't exist."""
        if not self._has_table("chapters_fts"):
            return
        c = self.conn.cursor()
        # delete old FTS row then insert fresh
        c.execute("INSERT INTO chapters_fts(chapters_fts, rowid, title, content_md) VALUES('delete', ?, ?, ?)",
                (chapter_id, title or "", text or ""))
        c.execute("INSERT INTO chapters_fts(rowid, title, content_md) VALUES(?, ?, ?)",
                (chapter_id, title or "", text or ""))

    # ---- Helpers
    def _has_table(self, name: str) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return c.fetchone() is not None
        self.conn.commit()

    # ---- Transactions (optional helpers)
    def begin(self): self.conn.execute("BEGIN")
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()

    # ---- Close
    def close(self): self.conn.close()
