from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Sequence

from utils.md import md_to_html
from database.schema import ensure_schema
from database.migrations import upgrade

import shutil, time, os
def _backup_db_file(path: str) -> None:
    ts = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, f"{path}.bak-{ts}")

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

    def project_create(self, name: str="New Project") -> int:
        c = self.conn.cursor()
        c.execute("INSERT INTO projects(name) VALUES (?)", (name,))
        self.conn.commit()
        return int(c.lastrowid)

    def project_soft_delete(self, project_id: int) -> None:
        self.conn.execute("UPDATE projects SET deleted=1 WHERE id=?", (project_id,))
        self.conn.commit()

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
        c = self.conn.cursor()
        c.execute("""SELECT id, title, content, position, book_id
                    FROM chapters WHERE id=?""", (chapter_id,))
        return c.fetchone()

    def chapter_base_index(self, project_id: int, book_id: int) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE project_id=? AND book_id=? AND COALESCE(deleted,0)=0", (project_id, book_id))
        base_index = c.fetchone()[0]
        print("base_index - here", base_index)
        if base_index is None or base_index < 0:
            return -1
        return base_index
        return (c.fetchone()[0] or -1) + 1
    
    def chapter_content(self, chapter_id: int) -> Optional[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,))
        r = c.fetchone()["content"]
        return r if r else None

    def chapter_list(self, project_id: int, book_id: int, fetchone=False) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, title, content, position
                     FROM chapters
                     WHERE project_id=? AND book_id=? AND COALESCE(deleted,0)=0
                     ORDER BY position, id""", (project_id, book_id))
        if fetchone:
            return c.fetchone()
        else:
            return c.fetchall()

    def chapter_insert(self, project_id: int, book_id: int, position: int,
                       title: str, content_md: str) -> int:
        c = self.conn.cursor()
        c.execute("""INSERT INTO chapters(project_id, book_id, title, content, position)
                     VALUES (?,?,?,?,?)""",
                  (project_id, book_id, title, content_md, position))
        self.conn.commit()
        return int(c.lastrowid)

    def chapter_update(self, chapter_id: int, *, title: Optional[str]=None,
                       content_md: Optional[str]=None) -> None:
        self.conn.execute("""UPDATE chapters
                             SET title=COALESCE(?, title),
                                 content=COALESCE(?, content),
                                 updated_at=CURRENT_TIMESTAMP
                             WHERE id=?""", (title, content_md, chapter_id))
        self.conn.commit()

    def chapter_soft_delete(self, chapter_id: int) -> None:
        self.conn.execute("UPDATE chapters SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (chapter_id,))
        self.conn.commit()

    def chapter_compact_positions(self, project_id: int, book_id: int) -> None:
        rows = self.chapter_list(project_id, book_id)
        c = self.conn.cursor()
        for pos, r in enumerate(rows):
            c.execute("UPDATE chapters SET position=? WHERE id=?", (pos, r["id"]))
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

    def chapter_position_gap(self, N: int, project_id: int, book_id: int, base_index: int):
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
        """, (N, project_id, book_id, base_index))
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

    def world_aliases_add(self, world_item_id: int, alias: str) -> None:
        c = self.conn.cursor()
        c.execute("INSERT INTO world_aliases (world_item_id, alias) VALUES (?, ?)", (world_item_id, alias))
        self.conn.commit()

    def world_aliases_add_multiple(self, world_item_id: int, aliases: list[str]) -> None:
        c = self.conn.cursor()
        c.executemany("INSERT INTO world_aliases (world_item_id, alias) VALUES (?, ?)", ((world_item_id, a) for a in aliases))
        self.conn.commit()

    def world_items_by_category(self, project_id: int, category_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT id, title
                     FROM world_items
                     WHERE project_id=? AND category_id=? AND COALESCE(deleted,0)=0
                     ORDER BY position, id""", (project_id, category_id))
        return c.fetchall()
    
    def world_item_is_character(self, world_id: int) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT type FROM world_items WHERE id=?", (world_id,))
        r = c.fetchone()
        return r["type"] == "character"

    def world_item(self, world_id: int) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT title FROM world_items WHERE id=?", (world_id,))
        r = c.fetchone()
        return r["title"] if r else None

    def world_item_meta(self, world_id: int) -> Optional[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("SELECT id, category_id, title, type, content_md, content_render FROM world_items WHERE id=?", (world_id,))
        return c.fetchone()

    def world_item_type(self, world_id: int) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("SELECT type FROM world_items WHERE id=?", (world_id,))
        r = c.fetchone()
        return r["type"] if r else None
    
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

    def world_item_insert(self, project_id: int, category_id: int, title: str = "", content_md: str = "", item_type: str = "", aliases=()) -> int:
        """
        Create a new world item under a category.
        Returns the new world_item id.
        """
        html = md_to_html(content_md)
        c = self.conn.cursor()
        c.execute(
            """INSERT INTO world_items (project_id, category_id, title, type, content_md, content_render)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, category_id, title.strip(), item_type.strip(), content_md.strip(), html),
        )
        wid = c.lastrowid
        self.conn.commit()
        self.world_aliases_add_multiple(wid, aliases)
        return int(wid)

    def world_item_update_content(self, world_id: int, md: str) -> None:
        self.conn.execute("""UPDATE world_items
                             SET content_md=?, updated_at=CURRENT_TIMESTAMP
                             WHERE id=?""", (md, world_id))
        self.conn.commit()

    def world_item_rename(self, world_id: int, new_title: str) -> None:
        self.conn.execute("UPDATE world_items SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (new_title, world_id))
        self.conn.commit()

    def world_item_soft_delete(self, world_id: int) -> None:
        self.conn.execute("UPDATE world_items SET deleted=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (world_id,))
        self.conn.commit()

    # --- Characters ---
    def character_facets(self, character_id: int) -> list[sqlite3.Row]:
        c = self.conn.cursor()
        c.execute("""SELECT * FROM character_facets
                    WHERE character_id=? ORDER BY position, id""", (character_id,))
        return c.fetchall()

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


    # ---- FTS (chapter & world)
    def fts_rebuild(self) -> None:
        self.conn.execute("INSERT INTO chapters_fts(chapters_fts) VALUES('rebuild')")
        self.conn.execute("INSERT INTO world_items_fts(world_items_fts) VALUES('rebuild')")
        self.conn.commit()

    # ---- References (chapter ↔ world)
    def set_chapter_world_refs(self, chapter_id: int, world_ids: Sequence[int]) -> None:
        c = self.conn.cursor()
        c.execute("DELETE FROM chapter_world_refs WHERE chapter_id=?", (chapter_id,))
        c.executemany("""INSERT OR IGNORE INTO chapter_world_refs(chapter_id, world_item_id)
                         VALUES (?,?)""", [(chapter_id, wid) for wid in world_ids])
        self.conn.commit()

    # ---- Transactions (optional helpers)
    def begin(self): self.conn.execute("BEGIN")
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()

    # ---- Close
    def close(self): self.conn.close()
