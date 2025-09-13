import sqlite3
from .schema import LATEST_SCHEMA_VERSION

def get_user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version;").fetchone()[0] or 0

def set_user_version(conn: sqlite3.Connection, v: int) -> None:
    conn.execute(f"PRAGMA user_version = {v};")
def _ensure_indexes(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    # Chapter ordering lookup
    c.execute("""CREATE INDEX IF NOT EXISTS idx_chapters_proj_book_pos
                 ON chapters(project_id, book_id, position)""")
    # World trees
    c.execute("""CREATE INDEX IF NOT EXISTS idx_world_categories_proj_parent_pos
                 ON world_categories(project_id, parent_id, position)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_world_items_proj_cat
                 ON world_items(project_id, category_id)""")
    # Refs
    c.execute("""CREATE INDEX IF NOT EXISTS idx_refs_chapter ON chapter_world_refs(chapter_id)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_refs_world   ON chapter_world_refs(world_item_id)""")
    conn.commit()

def _safe_add_column(cur: sqlite3.Cursor, table: str, col: str, decl: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

def upgrade(conn: sqlite3.Connection) -> None:
    """
    Upgrade DB from current PRAGMA user_version to LATEST_SCHEMA_VERSION.
    Each migration is idempotent and runs inside a transaction.
    """
    cur_ver = get_user_version(conn)
    if cur_ver == LATEST_SCHEMA_VERSION:
        return

    # Safety: wrap the entire upgrade in a transaction
    with conn:
        # v0 → v1: initial base schema gets created by ensure_schema()
        # If your existing DBs predate user_version, treat them as v1 after ensure_schema.
        if cur_ver == 0:
            # If DB is truly empty, ensure_schema would have created it already.
            # Bump to v1 so next steps can run.
            set_user_version(conn, 1)
            cur_ver = 1

        # (add future migrations here...) (None currently needed)
        # # v1 → v2: add soft-delete columns
        # if cur_ver < 2:
        #     _migration_v2(conn)
        #     set_user_version(conn, 2)
        #     cur_ver = 2

        # # v2 → v3: add per-project metadata (import/export/description)
        # if cur_ver < 3:
        #     _migration_v3(conn)
        #     set_user_version(conn, 3)
        #     cur_ver = 3

        # # if cur_ver < 4:
        # #     _migration_v4(conn)
        # #     set_user_version(conn, 4)
        # #     cur_ver = 4

    # Optional: sanity indexes
    _ensure_indexes(conn)

## Migrations (examples) ##
def _migration_v2(conn: sqlite3.Connection) -> None:
    """Add deleted flags to projects, world, chapters."""
    c = conn.cursor()
    # add columns if missing
    _safe_add_column(c, "projects", "deleted", "INTEGER DEFAULT 0")
    _safe_add_column(c, "world_categories", "deleted", " INTEGER DEFAULT 0")
    _safe_add_column(c, "world_items",      "deleted", " INTEGER DEFAULT 0")
    _safe_add_column(c, "chapters",         "deleted", " INTEGER DEFAULT 0")
    conn.commit()

def _migration_v3(conn: sqlite3.Connection) -> None:
    """Add project meta: import_dir, export_dir, description."""
    c = conn.cursor()
    _safe_add_column(c, "projects", "import_dir",  "TEXT")
    _safe_add_column(c, "projects", "export_dir",  "TEXT")
    _safe_add_column(c, "projects", "description", "TEXT")
    conn.commit()

