# storyarkivist/database/schema.py
import sqlite3

LATEST_SCHEMA_VERSION = 1  # keep in sync with migrations.py

def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Create base schema if it doesn't exist (v1), but do not add new columns
    introduced by later versions. Migrations will handle upgrades.
    """
    cur = conn.cursor()

    # --- Projects & Books ---
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        import_dir TEXT,
        export_dir TEXT,
        deleted BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        position INTEGER DEFAULT 0,
        deleted INTEGER DEFAULT 0,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    """)

    # --- Chapters & Versions ---
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        book_id INTEGER,
        title TEXT NOT NULL,
        active_version_id INTEGER,
        deleted BOOLEAN DEFAULT 0,
        position INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id),
        FOREIGN KEY(book_id) REFERENCES books(id)
    );
    CREATE INDEX IF NOT EXISTS idx_chapters_active_version ON chapters(active_version_id);

    CREATE TABLE IF NOT EXISTS chapter_versions (
        id INTEGER PRIMARY KEY,
        chapter_id INTEGER NOT NULL,
        version_number INTEGER NOT NULL,
        text TEXT,
        text_hash TEXT,
        name TEXT,                     -- optional label like “Draft A”
        is_active INTEGER DEFAULT 1,   -- one active per chapter at a time
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        text_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        format_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_chver_active
        ON chapter_versions(chapter_id)
        WHERE is_active=1;

    -- Outline items for a specific chapter version
    CREATE TABLE IF NOT EXISTS outline_items (
        id INTEGER PRIMARY KEY,
        chapter_version_id INTEGER NOT NULL,
        parent_id INTEGER,                 -- null = root (per chapter version)
        order_key REAL NOT NULL,           -- fractional ordering
        text TEXT NOT NULL,
        tags TEXT,                         -- JSON array of strings (simple for v1)
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(chapter_version_id) REFERENCES chapter_versions(id),
        FOREIGN KEY(parent_id) REFERENCES outline_items(id)
    );

    CREATE INDEX IF NOT EXISTS idx_ol_items_ver_parent
        ON outline_items(chapter_version_id, parent_id, order_key);

    CREATE TABLE IF NOT EXISTS chapter_notes (
        id INTEGER PRIMARY KEY,
        chapter_id INTEGER NOT NULL,
        kind TEXT CHECK(kind IN ('todo','note')) NOT NULL,
        text TEXT NOT NULL,
        is_done BOOLEAN DEFAULT 0,
        position INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id)
    );

    -- Version-keyed metrics cache (fast aggregate views later)
    CREATE TABLE IF NOT EXISTS chapter_metrics (
        id INTEGER PRIMARY KEY,
        chapter_id INTEGER NOT NULL,
        chapter_version_id INTEGER NOT NULL,
        source_hash TEXT NOT NULL,       -- the chapter_versions.text_hash used
        word_count INTEGER, char_count INTEGER,
        paragraph_count INTEGER, sentence_count INTEGER,
        avg_sentence_len REAL, type_token_ratio REAL,
        dialogue_words INTEGER, dialogue_ratio REAL,
        reading_secs INTEGER, est_pages REAL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uq_metrics_source
        ON chapter_metrics(chapter_id, chapter_version_id, source_hash);
    """)

    # --- Worldbuilding ---
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS world_categories (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        parent_id INTEGER,
        name TEXT NOT NULL,
        type TEXT, -- types of world items that can be nested under this category: 'character','object','faction','language','custom'
        position INTEGER DEFAULT 0,
        deleted BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id),
        FOREIGN KEY(parent_id) REFERENCES world_categories(id)
    );
    CREATE TABLE IF NOT EXISTS world_items (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        category_id INTEGER,
        type TEXT,
        title TEXT NOT NULL,
        position INTEGER DEFAULT 0,
        content_md TEXT,
        content_render TEXT,
        deleted BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id),
        FOREIGN KEY(category_id) REFERENCES world_categories(id)
    );
    CREATE TABLE IF NOT EXISTS world_aliases (
        id INTEGER PRIMARY KEY,
        world_item_id INTEGER NOT NULL,
        alias TEXT NOT NULL,
        alias_type TEXT DEFAULT 'alias',
        alias_norm TEXT,
        deleted BOOLEAN DEFAULT 0,
        FOREIGN KEY(world_item_id) REFERENCES world_items(id)
    );
    CREATE TABLE IF NOT EXISTS alias_types (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        UNIQUE(project_id, name)
    );
    CREATE TABLE IF NOT EXISTS world_links (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        relationship TEXT NOT NULL,
        FOREIGN KEY(source_id) REFERENCES world_items(id),
        FOREIGN KEY(target_id) REFERENCES world_items(id)
    );
    """)

    # --- Character information ---
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS character_facets (
            id              INTEGER PRIMARY KEY,
            character_id    INTEGER NOT NULL,           -- FK to world_items.id (type='character')
            facet_type      TEXT NOT NULL,              -- 'trait','goal','belonging','affiliation','skill','alias','custom'
            label           TEXT,                       -- short label e.g. 'Eye Color', 'Desire', 'Sword'
            value           TEXT,                       -- free text value (e.g., 'green', 'escape small town', 'obsidian blade')
            note            TEXT,                       -- extra context shown as tooltip or second column
            link_world_id   INTEGER,                    -- FK to world_items (for belongings/affiliations)
            status          TEXT,                       -- for goals: 'planned','active','blocked','done' (optional)
            priority        INTEGER,                    -- for goals (optional)
            due_chapter_id  INTEGER,                    -- FK chapters.id (optional)
            position        INTEGER DEFAULT 0,          -- per-character ordering
            is_primary      INTEGER DEFAULT 0,          -- e.g., mark a primary trait or signature item
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            deleted         BOOLEAN DEFAULT 0,
            FOREIGN KEY(character_id)  REFERENCES world_items(id),
            FOREIGN KEY(link_world_id) REFERENCES world_items(id),
            FOREIGN KEY(due_chapter_id) REFERENCES chapters(id)
            );
                      
        CREATE TABLE IF NOT EXISTS facet_templates (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            kind TEXT NOT NULL,       -- 'traits_physical' | 'traits_character' | 'goals' | 'belongings' | 'affiliations' | 'skills' | 'custom'
            label TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            UNIQUE(project_id, kind, label)
        );

        
        -- Enforce uniqueness (ignoring soft-deleted rows is tricky in a UNIQUE index;
        -- we’ll enforce soft-delete in the app logic)
        CREATE UNIQUE INDEX IF NOT EXISTS idx_world_alias_unique
            ON world_aliases(world_item_id, alias_norm);

        CREATE INDEX IF NOT EXISTS idx_charfac_character_pos
            ON character_facets(character_id, position);

        CREATE INDEX IF NOT EXISTS idx_charfac_type
            ON character_facets(facet_type);

            -- optional helper view (nice for UI joins)
            -- Shows resolved link titles for belongings/affiliations, etc.
        CREATE VIEW IF NOT EXISTS character_facets_v AS
            SELECT f.*, wi.title AS linked_title
            FROM character_facets f
            LEFT JOIN world_items wi ON wi.id = f.link_world_id;
                      
        -- relationships: no duplicates
        CREATE UNIQUE INDEX IF NOT EXISTS idx_world_link_uniq ON world_links(source_id, target_id, relationship);
                      
        -- world categories: no duplicates under same parent
        CREATE UNIQUE INDEX IF NOT EXISTS idx_world_cat_parent_name ON world_categories(project_id, parent_id, lower(name));
    """)

    # --- References ---
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS chapter_world_refs (
            chapter_id INTEGER NOT NULL,
            world_item_id INTEGER NOT NULL,
            PRIMARY KEY (chapter_id, world_item_id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id),
            FOREIGN KEY(world_item_id) REFERENCES world_items(id)
        );
        -- Versioned facts with quotes & anchors
        CREATE TABLE IF NOT EXISTS world_facts (
            id INTEGER PRIMARY KEY,
            world_item_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            chapter_version_id INTEGER NOT NULL,
            start_off INTEGER NOT NULL,
            end_off INTEGER NOT NULL,
            quote_text TEXT NOT NULL,
            label TEXT,                      -- e.g., 'goal', 'appearance', 'membership'
            value TEXT,                      -- parsed value if structured
            status TEXT DEFAULT 'fresh',     -- fresh/stale/confirmed
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chapter_version_world_refs (
            chapter_version_id INTEGER NOT NULL,
            world_item_id INTEGER NOT NULL,
            PRIMARY KEY (chapter_version_id, world_item_id)
        );
    """)

    # --- Progress & UI Prefs & analaysis ---
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS progress_log (
        id INTEGER PRIMARY KEY,
        chapter_id INTEGER,
        date DATE NOT NULL,
        word_count INTEGER NOT NULL,
        delta INTEGER
    );
    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        target_words INTEGER,
        deadline DATE,
        notes TEXT,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    CREATE TABLE IF NOT EXISTS ui_prefs (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        UNIQUE(project_id, key),
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );
    -- Suggestions (pending/accepted/rejected) per chapter
    CREATE TABLE IF NOT EXISTS ingest_candidates (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL,
        chapter_version_id INTEGER NOT NULL,
        surface TEXT NOT NULL,
        kind_guess TEXT,                 -- character/place/org/object/concept
        context TEXT,                    -- nearby text snippet
        start_off INTEGER NOT NULL, end_off INTEGER NOT NULL,
        link_world_id INTEGER,           -- if user links it
        status TEXT DEFAULT 'pending',   -- pending/accepted/rejected
        confidence REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uq_ingest_key
        ON ingest_candidates(project_id, chapter_id, chapter_version_id, surface, start_off, end_off);
    CREATE INDEX IF NOT EXISTS idx_ingest_by_chapter_status
        ON ingest_candidates(chapter_id, status);
    CREATE INDEX IF NOT EXISTS idx_ingest_by_chap_ver
        ON ingest_candidates(chapter_id, chapter_version_id, status);
    CREATE INDEX IF NOT EXISTS idx_ingest_surface
        ON ingest_candidates(project_id, chapter_id, surface);
    """)

    # # --- FTS5 (search) ---
    # try:
    #     cur.executescript("""
    #      -- contentless FTS; we push data from Python
    #     CREATE VIRTUAL TABLE IF NOT EXISTS chapters_fts USING fts5(title, content_md);
    #     CREATE VIRTUAL TABLE IF NOT EXISTS world_items_fts USING fts5(title, content_md);
    #     CREATE TRIGGER IF NOT EXISTS chapters_ai AFTER INSERT ON chapters BEGIN
    #         INSERT INTO chapters_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
    #     END;
    #     CREATE TRIGGER IF NOT EXISTS chapters_au AFTER UPDATE ON chapters BEGIN
    #         INSERT INTO chapters_fts(chapters_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
    #         INSERT INTO chapters_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
    #     END;
    #     CREATE TRIGGER IF NOT EXISTS chapters_ad AFTER DELETE ON chapters BEGIN
    #         INSERT INTO chapters_fts(chapters_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
    #     END;

    #     CREATE TRIGGER IF NOT EXISTS wi_ai AFTER INSERT ON world_items BEGIN
    #         INSERT INTO world_items_fts(rowid, title, content_md) VALUES (new.id, new.title, new.content_md);
    #     END;
    #     CREATE TRIGGER IF NOT EXISTS wi_au AFTER UPDATE ON world_items BEGIN
    #         INSERT INTO world_items_fts(world_items_fts, rowid, title, content_md) VALUES('delete', old.id, old.title, old.content_md);
    #         INSERT INTO world_items_fts(rowid, title, content_md) VALUES (new.id, new.title, new.content_md);
    #     END;
    #     CREATE TRIGGER IF NOT EXISTS wi_ad AFTER DELETE ON world_items BEGIN
    #         INSERT INTO world_items_fts(world_items_fts, rowid, title, content_md) VALUES('delete', old.id, old.title, old.content_md);
    #     END;
    #     """)
    # except sqlite3.Error as e:
    #     print("[warn] FTS5 unavailable or failed to create:", e)

    conn.commit()
