# arkivist
A tool for managing notes, characters, and chapters while writing something, all the way from head to tale.

## Main features/goals of StoryArkivist
* Store details about your world its people, items, locations, and so much more
* Navigate your world details (via intuitive linking) to refresh yourself while writing
* Easily edit your chapters in StoryArkivist or Word
* Create different versions of chapters and decide which ones work best
* Monitor your time spent on each project to analyze how you work best
* Analyze your writing with plots

## Directory structure
<!-- ├─ app.py                       # thin bootstrap; constructs StoryArkivist(db=Database(...))
├─ main.py                      # (optional) entrypoint; CLI args, single-instance logic, etc.
 -->
```
storyarkivist/
├── app.py                # Entry point (starts the app)
├── config.py             # App-wide config (paths, constants)
├─ database/
│  ├─ __init__.py
│  ├─ db.py                     # Database class (connections, schema, CRUD)
│  ├─ schema.py                 # DDL strings & migrations (versioned)
│  ├── seed.sql                 # Example data (optional)
│  ├─ models.py                 # typed Row/DTOs (dataclasses) for chapters/items/etc.
│  └─ migrations.py             # optional: per-version migration functions
├─ ui/
│  ├─ __init__.py
│  ├─ main_window.py            # StoryArkivist (QMainWindow) – largely UI logic
│  ├─ widgets/
│  │  ├─ chapters_tree.py
│  │  ├─ world_tree.py
│  │  ├─ world_detail.py
│  │  ├─ chapters_todos.py
│  │  └─ dialogs.py             # import/insert/project manager dialogs
│  └── resources/        # Icons, styles, etc.
│      ├── qss/              # Qt stylesheets
│      ├── icons/            # App icons
│      └── fonts/
├─ utils/
│  ├─ __init__.py
│  ├─ md.py                     # md_to_html / html_to_md, pandoc adapters, fallback
│  ├─ files.py                  # file reading, docx→md, path helpers
│  ├─ parsing.py                # parse_chapter_filename, entity extraction
│  ├─ word_integration.py       # COM automation, locks, callbacks
│  └─ qt.py                     # common Qt helpers (shortcuts, palettes, message wrappers)
│
├── tests/                # Unit + integration tests
│   ├── __init__.py
│   ├── test_db.py
│   ├── test_ui.py
│   └── test_controllers.py
│
├── requirements.txt      # Python deps (PySide6, etc.)
├── pyproject.toml        # (Optional) Poetry / build system
├── README.md
└── LICENSE
```

## How to add a future database migration

1. Pick a new version number (e.g., v4).
2. Write a function _migration_v4(conn) in migrations.py.
3. Add a clause:
    ```python
    if cur_ver < 4:
        _migration_v4(conn)
        set_user_version(conn, 4)
        cur_ver = 4
    ```
4. Update LATEST_SCHEMA_VERSION = 4 in schema.py.
5. Guidelines:
   * Keep each migration idempotent (safe to run once).
   * Wrap the whole upgrade in a with conn: transaction (already in the code).
   * For column additions, use _safe_add_column.
   * For backfills/data transforms, write guarded updates (e.g., WHERE new_col IS NULL).
   * For table rewrites (rare), create new_table, copy, drop old_table, rename — but only if absolutely necessary.
