import json
import re, sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize, Slot, QEvent, QRectF, QDateTime, QSignalBlocker, Signal, QObject
from PySide6.QtGui import QAction, QKeySequence, QIcon, QPainter, QPixmap, QPen, QIcon, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QTextBrowser, QMessageBox,
    QLineEdit, QLabel, QPushButton, QTabWidget, QFileDialog, QToolBar,
    QDialog, QSizePolicy, QInputDialog, QMenu, QToolButton, QHBoxLayout,
    QWidgetAction, QAbstractItemDelegate, QStyle, QApplication, QComboBox
)
from ui.widgets.theme_manager import theme_manager
from ui.widgets.ui_zoom import UiZoom
from database.db import Database
from ui.widgets.dialogs import BulkChapterImportDialog, ProjectManagerDialog, WorldImportDialog
from ui.widgets.world_detail import WorldDetailWidget
from ui.widgets.world_tree import WorldTree
from ui.widgets.characters_page import CharactersPage
from ui.widgets.character_editor import CharacterEditorDialog
from ui.widgets.character_dialog import CharacterDialog
from ui.widgets.chapter_todos import ChapterTodosWidget
from ui.widgets.chapters_tree import ChaptersTree
from ui.widgets.helpers import DropPane, PlainNoTab, chapter_display_label
from ui.widgets.common import StatusLine
from utils.icons import make_lock_icon
from utils.word_integration import DocxRoundTrip
from utils.md import docx_to_markdown, md_to_html, read_file_as_markdown
from utils.files import parse_chapter_filename
from ui.widgets.outline import OutlineWorkspace
from ui.widgets.outline import MiniOutlineTab
from ui.widgets.outline.window import OutlineWindow

class StoryArkivist(QMainWindow):
    chaptersOrderChanged = Signal(int, int, 'QVariantList')

    # StoryArkivist.__init__ (temporary)
    def _install_key_tracer(self):
        app = QApplication.instance()
        class _Tracer(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.KeyPress:
                    print("TRACER KeyPress", type(obj).__name__, ev.key(), ev.modifiers())
                return False
        self._tracer = _Tracer()
        app.installEventFilter(self._tracer)

    def __init__(self, dev_mode: bool = False, db_path: str = "db.sqlite3"):
        super().__init__()
        
        # self._install_key_tracer()

        self.dev_mode = dev_mode
        self.resize(1200, 700)

        self.db = Database(db_path)
        self.charactersPage = CharactersPage(self, self.db)
        self.outlineWorkspace = OutlineWorkspace()
        print("APP sees controller", id(self.outlineWorkspace.page.undoController))
        self._install_outline_undo_actions()
        QApplication.instance().installEventFilter(self)
        # self.outlineWorkspace.install_global_shortcuts(self)  # pass main window as host

        self._build_ui()
        self._wire_actions()

        # Pick or create a project immediately
        self._ensure_project_exists_or_prompt()
        # self.setWindowTitle("StoryArkivist")
        self.setWindowTitle(f"StoryArkivist — {self.db.project_name(self._current_project_id)} [*]")

        # current state
        self._current_chapter_id = None
        self._chapter_dirty = False

        # load some data (dev mode)
        if self.dev_mode:
            # populate some demo data for dev mode
            self.populate_demo_data()
            self._emit_chapter_order()

        # Now that a project is guaranteed, render left trees / center panels for it
        self.refresh_project_header()
        self.populate_all()
        self._select_startup_chapter()

    def _install_outline_undo_actions(self):
        if getattr(self, "_outline_actions_installed", False):
            return
        ctrl = self.outlineWorkspace.page.undoController

        self._actUndo = QAction("Undo Outline", self)
        self._actUndo.setShortcuts([QKeySequence.Undo])  # Ctrl+Z
        self._actUndo.setShortcutContext(Qt.ApplicationShortcut)
        self._actUndo.triggered.connect(ctrl.undo)
        self.addAction(self._actUndo)

        self._actRedo = QAction("Redo Outline", self)
        self._actRedo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")])
        self._actRedo.setShortcutContext(Qt.ApplicationShortcut)
        self._actRedo.triggered.connect(ctrl.redo)
        self.addAction(self._actRedo)

        self._outline_actions_installed = True
        print("APP: outline undo/redo actions installed; ctrl id", id(ctrl))

    # ---- Zoom state ----
    def _init_zoom_state(self):
        # base point sizes; tweak to taste
        self._zoom = 1.0
        self._base_pt = 12.0          # editor & world-edit
        self._base_title_pt = 14.0    # title field

        # QTextBrowser doesn't have absolute zoom, only relative "zoomIn/zoomOut"
        # We'll track applied step counts and reapply on change.
        self._centerView_steps = 0
        self._worldView_steps = 0

    def zoom_in(self):
        self._set_zoom(self._zoom * 1.10)

    def zoom_out(self):
        self._set_zoom(self._zoom / 1.10)

    def zoom_reset(self):
        self._set_zoom(1.0)

    def _set_zoom(self, z: float):
        z = max(0.7, min(2.0, z))  # clamp
        if abs(z - getattr(self, "_zoom", 1.0)) < 1e-3:
            return
        self._zoom = z

        # 1) Title field
        if hasattr(self, "titleEdit"):
            f = self.titleEdit.font()
            f.setPointSizeF(self._base_title_pt * z)
            self.titleEdit.setFont(f)

        # 2) Center editor (QPlainTextEdit)
        if hasattr(self, "centerEdit"):
            f = self.centerEdit.font()
            f.setPointSizeF(self._base_pt * z)
            self.centerEdit.setFont(f)

        # 3) World detail edit (QPlainTextEdit)
        if hasattr(self, "worldDetail") and hasattr(self.worldDetail, "edit"):
            f = self.worldDetail.edit.font()
            f.setPointSizeF(self._base_pt * z)
            self.worldDetail.edit.setFont(f)

        # 4) Previews (QTextBrowser) via relative zoom steps
        #    Define ~10 steps to double size (approx), so steps = round((z-1)*10)
        target_steps = int(round((z - 1.0) * 10))

        # center preview
        if hasattr(self, "centerView"):
            # undo previous steps
            if self._centerView_steps > 0:
                self.centerView.zoomOut(self._centerView_steps)
            elif self._centerView_steps < 0:
                self.centerView.zoomIn(-self._centerView_steps)
            # apply new steps
            if target_steps > 0:
                self.centerView.zoomIn(target_steps)
            elif target_steps < 0:
                self.centerView.zoomOut(-target_steps)
            self._centerView_steps = target_steps

        # world preview
        if hasattr(self, "worldDetail") and hasattr(self.worldDetail, "view"):
            if self._worldView_steps > 0:
                self.worldDetail.view.zoomOut(self._worldView_steps)
            elif self._worldView_steps < 0:
                self.worldDetail.view.zoomIn(-self._worldView_steps)
            if target_steps > 0:
                self.worldDetail.view.zoomIn(target_steps)
            elif target_steps < 0:
                self.worldDetail.view.zoomOut(-target_steps)
            self._worldView_steps = target_steps


    # ---------- DB ----------

    def move_chapter_to_index(self, chapter_id: int, dest_book_id: int, insert_index: int):
        """Move a chapter within a book to a specific index (0-based), ignoring deleted rows."""
        pid = self._current_project_id
        self.db.chapter_move_to_index(pid, dest_book_id, chapter_id, insert_index)

    def rebuild_search_indexes(self):
        self.db.fts_rebuild()

    def populate_demo_data(self):
        """Create a handy demo: a few world categories/items/aliases and 4 chapters.
        Chapter 1 mentions 1 world item, Chapter 2 mentions 2, Chapter 3 mentions 3, Chapter 4 mentions none.
        """
        pid = self._current_project_id
        bid = self._current_book_id

        # Basic world taxonomy
        characters_cid = self.db.world_category_insert_top_level(pid, "Characters", 0)
        places_cid = self.db.world_category_insert_top_level(pid, "Places", 1)

        # World items + aliases
        # People
        solara = self.db.world_item_insert(pid, characters_cid, "Solara", item_type="character", aliases={"Sun-Goddess":"title","Sol":"nickname"}, content_md="**Solara**: deity of light.")
        markus = self.db.world_item_insert(pid, characters_cid, "Markus", item_type="character", aliases={"Mark":"nickname","M.":"nickname"}, content_md="**Markus**: captain of the guard.")
        elyn   = self.db.world_item_insert(pid, characters_cid, "Elyn",   item_type="character", aliases={}, content_md="**Elyn**: novice acolyte.")

        # Places
        dawn_temple = self.db.world_item_insert(pid, places_cid, "Temple of Dawn", item_type="place", aliases={"Dawn Temple":"title",}, content_md="Ancient temple to Solara.")
        black_gate  = self.db.world_item_insert(pid, places_cid, "Black Gate", item_type="place", aliases={"Gate of Night":"title",}, content_md="Northern border fortress.")

        world = [solara, markus, elyn, dawn_temple, black_gate]

        # Render HTML for these (links)
        for wid in world:
            self.rebuild_world_item_render(wid)

        # Chapters — 4 with 1/2/3/0 mentions
        base = self.db.chapter_last_position_index(pid, bid) + 1

        # C1: 1 mention
        c1 = self.db.chapter_insert(pid, bid, base+0, "Prologue", "The sun rose over the Temple of Dawn.")
        # C2: 2 mentions
        c2 = self.db.chapter_insert(pid, bid, base+1, "Meeting", "Markus waited at the Black Gate for Solara's sign.")
        # C3: 3 mentions
        c3 = self.db.chapter_insert(pid, bid, base+2, "Acolyte’s Test", "Elyn prayed to Solara within the Temple of Dawn, near the Black Gate.")
        # C4: 0 mentions
        c4 = self.db.chapter_insert(pid, bid, base+3, "Silence", "Nothing of note occurs here.")

        # Extract references for each
        for cid in (c1, c2, c3, c4):
            txt = self.db.chapter_content(cid)
            self.recompute_chapter_references(cid, txt)

        # Example versioned outlines for demo:
        v1_c1 = [
            "Opening image",
            "Inciting incident",
            "Hero refuses the call",
        ]
        v2_c1 = [
            "Alternate opening image",
            "Incident happens off-screen",
            "Mentor foreshadowed earlier",
        ]
        v1_c2 = [
            "Enter new world",
            "Meet allies",
            "First threshold",
        ]
        v1_c3 = [
            "Trials",
            "Midpoint reversal",
            "New stakes",
        ]

        # Store version names list
        self.db.ui_pref_set(pid, f"outline_versions:{c1}", json.dumps(["v1","v2"]))
        self.db.ui_pref_set(pid, f"outline_versions:{c2}", json.dumps(["v1"]))
        self.db.ui_pref_set(pid, f"outline_versions:{c3}", json.dumps(["v1"]))
        self.db.ui_pref_set(pid, f"outline_versions:{c4}", json.dumps(["v1"]))

        # Store lines per version
        self.db.ui_pref_set(pid, f"outline:{c1}:v1", json.dumps(v1_c1))
        self.db.ui_pref_set(pid, f"outline:{c1}:v2", json.dumps(v2_c1))
        self.db.ui_pref_set(pid, f"outline:{c2}:v1", json.dumps(v1_c2))
        self.db.ui_pref_set(pid, f"outline:{c3}:v1", json.dumps(v1_c3))
        self.db.ui_pref_set(pid, f"outline:{c4}:v1", json.dumps(v1_c3))

        # refresh UI
        self.populate_chapters_tree()
        self.populate_world_tree()
        self.load_chapter(c1)

        # refresh outline workspace
        self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)
        self.tabMiniOutline.set_workspace(self.outlineWorkspace)
        self.tabMiniOutline.set_chapter(self._current_chapter_id)

        self.rebuild_search_indexes()

    def _select_startup_chapter(self):
        pid, bid = self._current_project_id, self._current_book_id
        last = self.db.ui_pref_get(pid, f"chapters:last:{bid}")
        if last:
            try:
                chap_id = int(last)
            except ValueError:
                chap_id = None
        else:
            # fallback to first chapter in this book
            rows = self.db.chapter_list(pid, bid)  # ordered
            chap_id = rows[0]["id"] if rows else None

        if chap_id:
            self.focus_chapter_in_tree(chap_id)  # your existing helper
            self.load_chapter(chap_id)           # keeps everything in sync

    def _make_fallback_book_icon(self, size: int = 16) -> QIcon:
        # create a simple book icon as fallback
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(self.palette().color(self.foregroundRole())); pen.setWidthF(1.4)
        p.setPen(pen)
        w = h = size
        # simple "open book": two rectangles with a split
        left  = QRectF(w*0.15, h*0.25, w*0.3,  h*0.5)
        right = QRectF(w*0.55, h*0.25, w*0.3,  h*0.5)
        p.drawRoundedRect(left, 2, 2)
        p.drawRoundedRect(right, 2, 2)
        # spine hint
        p.drawLine(w*0.5, h*0.2, w*0.5, h*0.8)
        p.end()
        return QIcon(pm)

    def refresh_project_header(self):
        pid = getattr(self, "_current_project_id", None)
        if not pid:
            self.projectBtn.setText("(No project)")
            return
        name = self.db.project_name(pid) or "(Unknown)"
        self.projectBtn.setText(name)
        self.setWindowTitle(f"StoryArkivist - {name} [*]")

    def _ensure_project_exists_or_prompt(self):
        """On first launch (or if all projects are deleted), create 'Untitled Project' and open the manager focused on rename."""
        if self.db.project_quantity() > 0:
            # ensure we have a current project id
            # just pick the first active project (#TODO: remember last used?)
            self._current_project_id = self.db.project_first_active()
            return

        # No project → create a default one
        self._current_project_id = self.db.project_create()

        # Make sure there is at least one book so the UI has something to show
        self._current_book_id = self.db.book_create(self._current_project_id)

        # seed some stock alias_types # TODO: make this configurable in project manager
        self.db.alias_types_seed(self._current_project_id)

        # seed some stock traits # TODO: make this configurable in project manager
        data = {
            "trait_physical": ["Eye color","Hair","Height","Build","Skin","Age"],
            "trait_character": ["Personality","Mannerisms","Goal","Fear","Flaw","Virtue"]
        }
        self.db.traits_seed(self._current_project_id, data)

        # # seed some stock world categories # TODO: make this configurable in project manager
        # self.db.world_categories_seed(self._current_project_id)

        # Open the Project Manager with rename box focused and preselected
        self.open_project_manager(focus_rename=True)

    def open_project_manager(self, focus_rename: bool = False):
        dlg = ProjectManagerDialog(self, self)
        if focus_rename:
            # focus after the dialog is shown
            QTimer.singleShot(0, lambda: (dlg.nameEdit.setFocus(), dlg.nameEdit.selectAll()))
        dlg.exec()

    def _rename_book(self, book_id: int):
        row = self.db.book_list(self._current_project_id)
        if not row: return
        old = row[0] if not isinstance(row, sqlite3.Row) else row["name"]
        new, ok = QInputDialog.getText(self, "Rename Book", "New title:", text=old or "")
        if not ok or not new.strip(): return
        self.db.book_rename(book_id, new.strip())
        self.populate_chapters_tree()

    def _center_set_mode(self, view_mode: bool):
        """Switch center pane between View (rendered) and Edit (markdown)."""
        self.centerView.setVisible(view_mode)
        self.centerEdit.setVisible(not view_mode)
        self.centerModeBtn.setText("Edit" if view_mode else "View")
        self.centerStatus.show_neutral("Viewing" if view_mode else "Editing")

        # If locked by Word (for this chapter), force View mode & disable editor
        locked = getattr(self, "_word_lock_chapter_id", None)
        is_locked = (locked is not None and locked == getattr(self, "_current_chapter_id", None))
        self.centerEdit.setReadOnly(is_locked)
        self.centerModeBtn.setEnabled(not is_locked)

        # Style read-only editor differently (greyed out)
        if self.centerEdit.isReadOnly():
            self.centerEdit.setStyleSheet("QPlainTextEdit{background: #f6f6f6;}")
        else:
            self.centerEdit.setStyleSheet("")

    def _center_mark_dirty(self):
        # Don't mark dirty if this chapter is Word-locked
        if getattr(self, "_word_lock_chapter_id", None) == getattr(self, "_current_chapter_id", None):
            return
        self._chapter_dirty = True
        self.centerStatus.set_dirty()
        self.setWindowModified(True)

    def _center_toggle_mode(self):
        """Toggle center editor view/edit. Save on leaving Edit."""
        viewing = self.centerView.isVisible()
        if not viewing:
            # leaving Edit -> save
            self.save_current_if_dirty()
            # re-render preview
            self._render_center_preview()
        # toggle
        self._center_set_mode(view_mode=not viewing)

    def _render_center_preview(self):
        """Render current chapter markdown to HTML in the preview."""
        chap_id = getattr(self, "_current_chapter_id", None)
        if chap_id is None:
            self.centerView.setHtml("<i>No chapter selected</i>")
            return
        text = self.db.chapter_content(chap_id)
        md = text if text else ""
        # Use your existing markdown→html renderer if you have one; fallback simple:
        html = md_to_html(md)
        self.centerView.setHtml(html)

    def _toggle_word_sync_clicked(self):
        if getattr(self, "_word_lock_chapter_id", None) == getattr(self, "_current_chapter_id", None):
            self.action_stop_word_sync()
        else:
            self.action_edit_in_word()

    def _update_word_sync_ui(self):
        locked_id = getattr(self, "_word_lock_chapter_id", None)
        active = (locked_id is not None and locked_id == getattr(self, "_current_chapter_id", None))
        self.wordSyncBtn.setText("Stop Word Sync" if active else "Edit in Word")
        # When locked, force View mode and disable Edit toggle
        self.centerModeBtn.setEnabled(not active)
        # (optional) visibly read-only title field when locked
        self.titleEdit.setReadOnly(active)
        # self.titleEdit.setStyleSheet("QLineEdit{background:#f6f6f6;}" if active else "")
        if active:
            self.centerStatus.show_info("Editing in Word")

    def ensure_world_roots(self):
        """Ensure the project has a basic set of world root categories."""
        pid = self._current_project_id
        if not pid:
            return
        has_roots = self.db.world_categories_count(pid) > 0
        if has_roots:
            return
        # Seed a few roots (#TODO: set default roots based on project type)
        roots = [("Characters", 0), ("Locations", 1), ("Cultures", 2), ("Misc", 3)]
        for name, pos in roots:
            self.db.world_category_insert_top_level(pid, name, pos)

    def infer_world_type_from_category(self, category_id: int) -> str:
        """Walk up to the top-level world category and map its name to a type."""
        cid = category_id
        root_name = None
        while cid is not None:
            row = self.db.world_category_meta(cid)
            if not row: break
            parent_id, name = (row["parent_id"], row["name"]) if isinstance(row, sqlite3.Row) else row
            root_name = name if parent_id is None else root_name
            cid = parent_id
        key = (root_name or "").strip().lower()
        if key.startswith("character"): return "character"
        if key.startswith("location"):  return "location"
        if key.startswith("culture"):   return "culture"
        return "misc"

    def load_world_item(self, world_item_id: int, edit_mode: bool = False):
        """Show a world item in the right panel and focus it in the tree."""
        # Prefer the widget’s own loader if present
        if hasattr(self.worldDetail, "show_item"):
            self.worldDetail.show_item(world_item_id, view_mode=not edit_mode)
        else:
            # minimal fallback
            row = self.db.world_item_meta(world_item_id)
            if not row:
                return
            title, md, html = ((row["title"], row["content_md"], row["content_render"])
                            if isinstance(row, sqlite3.Row) else row)
            # ensure right panel shows View mode with rendered HTML
            if hasattr(self.worldDetail, "set_mode"):
                self.worldDetail.set_mode("view")
            if hasattr(self.worldDetail, "view"):
                self.worldDetail.view.setHtml(html or "")
            if hasattr(self.worldDetail, "edit"):
                self.worldDetail.edit.setPlainText(md or "")
            if hasattr(self.worldDetail, "_current_world_item_id"):
                self.worldDetail._current_world_item_id = world_item_id

        # focus/select in the world tree if possible
        self.focus_world_item_in_tree(world_item_id)

    def import_world_items_from_paths(self, paths: list[str]):
        if not paths:
            return

        dlg = WorldImportDialog(self, paths, self)
        if dlg.exec() != QDialog.Accepted:
            return
        choice = dlg.chosen()
        parent_cid = choice["parent_id"]
        base_name = choice["name"]  # may be None if multiple files
        wtype = self.infer_world_type_from_category(parent_cid)

        cur = self.db.conn.cursor()
        last_world_id = None

        for p in paths:
            name = base_name if base_name else Path(p).stem

            md = ""
            ext = Path(p).suffix.lower()
            try:
                if ext == ".docx":
                    md = docx_to_markdown(p)
                else:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        md = f.read()
            except Exception as e:
                print("World import failed for", p, e)
                continue

            # insert world item with chosen category and inferred type
            self.db.world_item_insert(self._current_project_id, parent_cid, name, wtype, md)
            wid = cur.lastrowid
            last_world_id = wid

            self.rebuild_world_item_render(wid)

        self.populate_world_tree()

        if last_world_id:
            self.load_world_item(last_world_id)  # see wrapper below

    def _rename_world_object(self, kind: str, obj_id: int):
        if kind == "world_cat":
            old = self.db.world_category(obj_id)
            new, ok = QInputDialog.getText(self, "Rename Category", "New name:", text=old or "")
            if not ok or not new.strip(): return
            self.db.world_category_rename(obj_id, new.strip())
            self.populate_world_tree()
        elif kind == "world_item":
            old = self.db.world_item(obj_id)
            new, ok = QInputDialog.getText(self, "Rename World Item", "New title:", text=old or "")
            if not ok or not new.strip(): return
            self.db.world_item_rename(obj_id, new.strip())
            self.populate_world_tree()
            # refresh right panel if it's the one currently shown
            if getattr(self.worldDetail, "_current_world_item_id", None) == obj_id:
                self.load_world_item(obj_id)

    def _soft_delete_world_object(self, kind: str, obj_id: int):
        if kind == "world_cat":
            # mark the category deleted (children remain, but won’t show because parent is hidden)
            self.db.world_category_soft_delete(obj_id)
            self.populate_world_tree()
        elif kind == "world_item":
            self.db.world_item_soft_delete(obj_id)
            self.populate_world_tree()
            # clear right panel if we just hid the current item
            if getattr(self.worldDetail, "_current_world_item_id", None) == obj_id:
                if hasattr(self.worldDetail, "set_mode"): self.worldDetail.set_mode("view")
                if hasattr(self.worldDetail, "view"):     self.worldDetail.view.setHtml("")
                if hasattr(self.worldDetail, "edit"):     self.worldDetail.edit.setPlainText("")
                self.worldDetail._current_world_item_id = None

    def open_character_editor(self, char_id: int):
        dlg = CharacterEditorDialog(self, self.db, char_id, self)
        dlg.exec()
        # refresh right panel if it was showing this character
        if getattr(self, "_current_world_item_id", None) == char_id:
            self.load_world_item(char_id, edit_mode=False)

    # def open_character_dialog(self, char_id: int):
    #     dlg = CharacterDialog(self, self.db, char_id, self)
    #     dlg.exec()
    #     # refresh right panel if it was showing this character
    #     if getattr(self, "_current_world_item_id", None) == char_id:
    #         self.load_world_item(char_id, edit_mode=False)

    def open_character_dialog(self, char_id: int, refresh_world_panel_on_close=True):
        dlg = CharacterDialog(self, self.db, char_id, self)
        if refresh_world_panel_on_close:
            dlg.finished.connect(
                lambda: self.worldDetail.show_item(char_id, add_to_history=False, view_mode=True)
            )
        dlg.exec()

    def _rename_chapter(self, chap_id: int):
        title = self.db.chapter(chap_id)
        new, ok = QInputDialog.getText(self, "Rename Chapter", "New title:", text=title or "")
        if not ok or not new.strip(): return
        self.db.chapter_update(chap_id, title=new.strip())
        self.populate_chapters_tree()
        self.focus_chapter_in_tree(chap_id)

    def _soft_delete_chapter(self, chap_id: int):
        # mark deleted
        self.db.chapter_soft_delete(chap_id)
        # renumber remaining in that book
        bid = self.db.chapter_meta(chap_id)["book_id"]
        if bid is not None:
            self._compact_positions_after_insert(bid)
        self.populate_chapters_tree()
        # clear editor if we just hid the active chapter
        if getattr(self, "_current_chapter_id", None) == chap_id:
            self._current_chapter_id = None
            self.titleEdit.setText("")
            self.centerEdit.setPlainText("")
            self.centerView.setHtml("")
            self.populate_refs_tree(None)

    def _compact_positions_after_insert(self, book_id: int):
        """Rewrite chapter positions for this book to 0..N-1, skipping soft-deleted chapters."""
        pid = self._current_project_id
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT id
            FROM chapters
            WHERE project_id=? AND book_id=? AND COALESCE(deleted,0)=0
            ORDER BY position, id
        """, (pid, book_id))
        rows = cur.fetchall()
        for new_pos, (cid,) in enumerate(rows):
            cur.execute("UPDATE chapters SET position=? WHERE id=?", (new_pos, cid))

    def import_chapters_from_paths(self, paths: list[str]):
        if not paths:
            return
        # guard
        if getattr(self, "_importing_chapters_now", False):
            return
        self._importing_chapters_now = True
        try:
            pid = self._current_project_id
            bid = self._current_book_id
            cur = self.db.conn.cursor()

            # normalize & dedupe
            norm = []
            seen = set()
            for p in paths:
                q = str(Path(p).resolve())
                if q not in seen:
                    seen.add(q)
                    norm.append(q)
            paths = norm

            # Placement logic
            if len(paths) == 1:
                # simple: append to end
                cur.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE project_id=? AND book_id=?", (pid, bid))
                max_position = cur.fetchone()[0]
                insert_at = (max_position) + 1
                p = paths[0]
                order_hint, clean = parse_chapter_filename(Path(p).name, split_mode=None)
                md = read_file_as_markdown(p)
                new_id = self.db.chapter_insert(pid, bid, insert_at, clean, md)
                self.db.conn.commit()
                self.recompute_chapter_references(new_id, md)
                self.populate_chapters_tree()
                self.load_chapter(new_id)
                return

            # Multi-file: show placement + split dialog
            dlg = BulkChapterImportDialog(self, paths, self)
            if dlg.exec() != QDialog.Accepted:
                return
            choice = dlg.chosen()
            sep = choice["sep"]
            mode = choice["mode"]
            anchor_cid = choice["anchor_cid"]

            # current order map
            cur.execute("""
                SELECT id, position FROM chapters
                WHERE project_id=? AND book_id=?
                ORDER BY position, id
            """, (pid, bid))
            rows = cur.fetchall()
            pos_map = {cid: pos for (cid, pos) in rows} if rows else {}

            # compute base insert index
            if mode == "first":
                base_index = 0
            elif mode == "last":
                base_index = self.db.chapter_last_position_index(pid, bid)
            elif mode == "after" and anchor_cid in pos_map:
                base_index = pos_map[anchor_cid] + 1
            else:
                base_index = self.db.chapter_last_position_index(pid, bid)

            # parse names; preserve original order unless a leading number exists
            parsed = []
            for idx, p in enumerate(paths):
                order_hint, clean = parse_chapter_filename(Path(p).name, split_mode=sep)
                parsed.append((idx, order_hint, clean, p))

            # sort by (has_number, number, original_index) so numbered files keep their numeric order,
            # and unnumbered preserve the original list order after the numbered ones.
            parsed.sort(key=lambda t: (t[1] is None,  # False (0) first if has number
                                    999999 if t[1] is None else t[1],
                                    t[0]))

            N = len(parsed)

            # === open a gap for bulk insert (critical to avoid interleaving) ===
            cur.execute("""
                UPDATE chapters
                SET position = position + ?
                WHERE project_id=? AND book_id=? AND position >= ?
            """, (N, pid, bid, base_index))

            # === insert contiguously into the gap ===
            new_ids = []
            for i, (_, _, title, p) in enumerate(parsed):
                md = read_file_as_markdown(p)
                new_id = self.db.chapter_insert(pid, bid, base_index + i, title, md)
                new_ids.append(new_id)
                self.recompute_chapter_references(new_id, md)

            # === normalize positions to 0..M-1 (optional but nice) ===
            self._compact_positions_after_insert(bid)

            self.db.conn.commit()
            self.populate_chapters_tree()
            if new_ids:
                self.load_chapter(new_ids[0])

        finally:
            self._importing_chapters_now = False


    # ---------- UI ----------

    def _add_menu(self, name: str) -> QMenu:
        return self.menuBar().addMenu(f"&{name}")

    def _add_file_menu(self):
        self.fileMenu = self._add_menu("File")
        # File menu actions
        # actImportChapter = QAction("Import Chapter…", self)
        # actImportChapter.triggered.connect(lambda: self.import_chapters_from_paths([]))
        # self.fileMenu.addAction(actImportChapter)

        actInsertChapter = QAction("Insert Chapter…", self)
        actInsertChapter.triggered.connect(self.insert_chapters_dialog)
        self.fileMenu.addAction(actInsertChapter)

    def _add_edit_menu(self):
        self.editMenu = self._add_menu("Edit")
        # Edit menu actions
        actEditInWord = QAction("Edit in Word…", self)
        actEditInWord.triggered.connect(self.action_edit_in_word)
        self.editMenu.addAction(actEditInWord)

        self.actStopWordSync = QAction("Stop Word Sync", self)
        self.actStopWordSync.setEnabled(False)
        self.actStopWordSync.triggered.connect(self.action_stop_word_sync)
        self.editMenu.addAction(self.actStopWordSync)

    def _add_view_menu(self):
        self.viewMenu = self._add_menu("View")
        # View menu actions
        # Submenu for Theme
        theme_menu = QMenu("Theme", self)
        self.viewMenu.addMenu(theme_menu)

        # Direct select actions
        self._theme_actions = []
        for idx, t in enumerate(theme_manager._themes):
            act = QAction(t.name, self, checkable=True)
            act.setChecked(idx == theme_manager._idx)
            def make_handler(i=idx, a=act):
                def _():
                    for other in self._theme_actions:
                        other.setChecked(False)
                    a.setChecked(True)
                    theme_manager.set_index(i)
                    theme_manager.apply(QApplication.instance())
                return _
            act.triggered.connect(make_handler())
            theme_menu.addAction(act)
            self._theme_actions.append(act)

        # Next Theme action + shortcut
        next_theme_act = QAction("Next Theme", self)
        next_theme_act.setShortcut(QKeySequence("Ctrl+`"))
        def _next_theme():
            theme_manager.next()
            # sync radio checks
            for i, a in enumerate(self._theme_actions):
                a.setChecked(i == theme_manager._idx)
            theme_manager.apply(QApplication.instance())
        next_theme_act.triggered.connect(_next_theme)

        self.viewMenu.addAction(next_theme_act)

        # # --- Zoom submenu
        # zoom_in_act = QAction("Zoom In", self)
        # zoom_in_act.setShortcut(QKeySequence("Ctrl++"))  # on some keyboards use Ctrl+='
        # def _zoom_in():
        #     theme_manager.zoom_in()
        #     theme_manager.apply(QApplication.instance())
        # zoom_in_act.triggered.connect(_zoom_in)

        # zoom_out_act = QAction("Zoom Out", self)
        # zoom_out_act.setShortcut(QKeySequence("Ctrl+-"))
        # def _zoom_out():
        #     theme_manager.zoom_out()
        #     theme_manager.apply(QApplication.instance())
        # zoom_out_act.triggered.connect(_zoom_out)

        # zoom_reset_act = QAction("Reset Zoom", self)
        # zoom_reset_act.setShortcut(QKeySequence("Ctrl+0"))
        # def _zoom_reset():
        #     theme_manager.zoom_reset()
        #     theme_manager.apply(QApplication.instance())
        # zoom_reset_act.triggered.connect(_zoom_reset)

        # self.viewMenu.addActions([zoom_in_act, zoom_out_act, zoom_reset_act])

    def _add_dev_menu(self):
        self.devMenu  = self._add_menu("Dev")
        # Dev menu actions
        actReindex = QAction("Rebuild Search Indexes", self)
        actReindex.triggered.connect(self.rebuild_search_indexes)
        self.devMenu.addAction(actReindex)

        actSeed = QAction("Populate Demo Data", self)
        actSeed.triggered.connect(self.populate_demo_data)
        self.devMenu.addAction(actSeed)

    def _add_help_menu(self):
        self.helpMenu = self._add_menu("Help")
        # Help menu actions

    def _setup_menus(self):
        # Menus
        self._add_file_menu()
        self._add_edit_menu()
        self._add_view_menu()
        if self.dev_mode:
            self._add_dev_menu()
        self._add_help_menu()

    def _add_project_header(self):
        # --- Project header toolbar (left of the menu bar area) ---
        self.projectBar = QToolBar("Project")
        self.projectBar.setMovable(False)
        self.projectBar.setFloatable(False)
        self.projectBar.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.TopToolBarArea, self.projectBar)

        projBtn = QToolButton()
        projBtn.setAutoRaise(True)
        projBtn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        projBtn.setText("(No project)")
        # Open-book icon (theme → common fallbacks → drawn fallback)
        book_icon = (QIcon.fromTheme("book-open")
                    or QIcon.fromTheme("document-open")
                    or QIcon.fromTheme("help-contents")
                    or self._make_fallback_book_icon())
        projBtn.setIcon(book_icon)
        projBtn.clicked.connect(self.open_project_manager)
        self.projectBtn = projBtn  # keep a ref so refresh_project_header() can set text

        wact = QWidgetAction(self)
        wrap = QWidget()
        hl = QHBoxLayout(wrap); hl.setContentsMargins(4,2,4,2); hl.setSpacing(4)
        hl.addWidget(projBtn)
        wact.setDefaultWidget(wrap)
        self.projectBar.addAction(wact)

        # refresh label now
        self.refresh_project_header()

    def _add_search_toolbar(self):
        # Toolbar: search
        tb = QToolBar("Search")
        self.addToolBar(tb)
        tb.setIconSize(QSize(16,16))
        tb.addWidget(QLabel(" Search: "))
        self.searchEdit = QLineEdit()
        self.searchEdit.setPlaceholderText("Search chapters & world…")
        self.searchEdit.returnPressed.connect(self.run_search)
        tb.addWidget(self.searchEdit)

    def _add_left_panel(self):
        # Left column: Chapters / Worldbuilding / Referenced-in-chapter
        self.chaptersTree = ChaptersTree(self)
        self.worldTree    = WorldTree(self, db=self.db)
        self.refsTree     = QTreeWidget()
        self.refsTree.setHeaderHidden(True)
        self.refsTree.itemClicked.connect(self.on_refs_clicked)

        chapWrap = QWidget()
        v1 = QVBoxLayout(chapWrap); v1.setContentsMargins(0,0,0,0)

        # --- Chapters header row (label + new + lock) ---
        chapHeader = QWidget()
        h = QHBoxLayout(chapHeader); h.setContentsMargins(6,4,6,4); h.setSpacing(6)

        lblChapters = QLabel(" Chapters")
        # lblChapters.setStyleSheet("font-weight: 600;")  # optional

        h.addWidget(lblChapters)
        h.addStretch(1)

        # New Chapter button (plus)
        self.btnNewChapter = QToolButton()
        self.btnNewChapter.setAutoRaise(True)
        self.btnNewChapter.setToolTip("New chapter…")
        self.btnNewChapter.setIcon(
            QIcon.fromTheme("list-add") or self.style().standardIcon(QStyle.SP_FileIcon)
        )
        # quick action: blank new chapter appended at end of current book
        self.btnNewChapter.clicked.connect(lambda: self.insert_blank_chapter_after_current_last())

        # Lock toggle (reorder lock)
        self.btnChapterLock = QToolButton()
        self.btnChapterLock.setObjectName("chapterLock")
        self.btnChapterLock.setCheckable(True)
        self.btnChapterLock.setAutoRaise(True)
        self.btnChapterLock.setChecked(False)
        self.btnChapterLock.setFocusPolicy(Qt.NoFocus)  # no focus ring
        self.btnChapterLock.setToolTip("Lock reordering")
        self.btnChapterLock.setStyleSheet("""
        #chapterLock,
        #chapterLock:checked,
        #chapterLock:hover,
        #chapterLock:pressed {
            background: transparent;
            border: none;
        }
        """)
    
        # icons: unlocked/locked (fall back to stock)
        self._iconUnlocked = make_lock_icon(self, locked=False, size=16)
        self._iconLocked   = make_lock_icon(self, locked=True,  size=16)
        self.btnChapterLock.setIcon(self._iconUnlocked)


        self.btnChapterLock.toggled.connect(self._on_toggle_lock)

        h.addWidget(self.btnNewChapter)
        h.addWidget(self.btnChapterLock)

        v1.addWidget(chapHeader)
        v1.addWidget(self.chaptersTree)

        # --- Worldbuilding and referenced-in-chapter trees ---

        worldWrap = QWidget(); v2 = QVBoxLayout(worldWrap); v2.setContentsMargins(0,0,0,0)
        v2.addWidget(QLabel(" Worldbuilding")); v2.addWidget(self.worldTree)

        refsWrap = QWidget(); v3 = QVBoxLayout(refsWrap); v3.setContentsMargins(0,0,0,0)
        v3.addWidget(QLabel(" Referenced in Chapter")); v3.addWidget(self.refsTree)

        self.leftSplit = QSplitter(Qt.Vertical)
        self.leftSplit.addWidget(chapWrap)
        self.leftSplit.addWidget(worldWrap)
        self.leftSplit.addWidget(refsWrap)
        self.leftSplit.setStretchFactor(0, 1)
        self.leftSplit.setStretchFactor(1, 1)
        self.leftSplit.setStretchFactor(2, 1)

    def _on_toggle_lock(self, checked: bool):
        if checked:
            self.btnChapterLock.setToolTip("Unlock reordering")
            self.btnChapterLock.setIcon(self._iconLocked)
        else:
            self.btnChapterLock.setToolTip("Lock reordering")
            self.btnChapterLock.setIcon(self._iconUnlocked)
        # propagate to the tree so it blocks only internal drags
        if hasattr(self.chaptersTree, "set_reorder_locked"):
            self.chaptersTree.set_reorder_locked(checked)

    def _on_workspace_version_changed(self, chap_id: int, name: str):
        if chap_id == getattr(self, "_current_chapter_id", None):
            # keep header combo and mini tab aligned
            with QSignalBlocker(self.cmbChapterVersion):
                self.cmbChapterVersion.setCurrentText(name)
            self.tabMiniOutline.refresh_from_workspace()

    def _on_outline_delete_chapter(self, chap_id: int):
        # Your existing soft-delete (keeps outline in ui_prefs for potential restore)
        self._soft_delete_chapter(chap_id)

        # Remove from the outline model
        row = self.outlineWorkspace.row_for_chapter_id(chap_id)
        if row >= 0:
            self.outlineWorkspace.model.removeRow(row)
            # keep maps/panes consistent
            self.outlineWorkspace._rebuild_id_map()
        
        if self._current_chapter_id == chap_id:
            self.tabMiniOutline.set_chapter(chap_id)  # clears if gone

    def _row_for_chapter_id(self, chap_id: int) -> int:
        return self.db.chapter_meta(chap_id)["position"]

    def _on_header_version_changed(self, name: str):
        chap_id = getattr(self, "_current_chapter_id", None)
        if not chap_id or not name:
            return
        if name == "➕ New version…":
            # suggest v{n+1}
            count = len(self.outlineWorkspace.versions_for_chapter_id(chap_id))
            suggested = f"v{count+1}"
            while True:
                new_name, ok = QInputDialog.getText(self, "New version", "Version name:", text=suggested)
                if not ok:
                    # restore selection to current version
                    cur = self.outlineWorkspace.current_version_for_chapter_id(chap_id)
                    with QSignalBlocker(self.cmbChapterVersion):
                        if cur: self.cmbChapterVersion.setCurrentText(cur)
                    return
                new_name = new_name.strip()
                if new_name:
                    break
                QMessageBox.warning(self, "Name required", "Please provide a version name.")

            self.outlineWorkspace.add_version_for_chapter_id(chap_id, new_name, clone_from_current=True)
            # repopulate and select
            self._populate_header_versions(chap_id)
            with QSignalBlocker(self.cmbChapterVersion):
                self.cmbChapterVersion.setCurrentText(new_name)
            return
        # normal switch
        self.outlineWorkspace.select_version_for_chapter_id(chap_id, name)
        self.tabMiniOutline.refresh_from_workspace()

        # row = self._current_outline_row()
        # if row >= 0 and name:
        #     self.outlineWorkspace.select_version_for_row(row, name)

    def _add_center_editor(self):
        topBar = QWidget()

        # Center editor (title + Word sync toggle + view/edit toggle)
        self.titleEdit = QLineEdit()
        self.titleEdit.setMinimumWidth(200)
        self.titleEdit.setMaximumWidth(900)  # cap growth
        self.titleEdit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Status bar
        self.centerStatus = StatusLine(self)

        # Word sync toggle button
        self.wordSyncBtn = QPushButton("Edit in Word")
        self.wordSyncBtn.setToolTip("Open this chapter in Word and live-sync changes")
        self.wordSyncBtn.clicked.connect(self._toggle_word_sync_clicked)

        # View/edit toggle
        self.centerModeBtn = QPushButton("Edit")  # label shows the action you can switch to
        self.centerModeBtn.setFixedWidth(72)
        self.centerModeBtn.clicked.connect(self._center_toggle_mode)

        # Version toggle
        self.cmbChapterVersion = QComboBox(topBar)  # wherever your header widgets live
        self.cmbChapterVersion.setMinimumWidth(140)
        self.cmbChapterVersion.currentTextChanged.connect(self._on_header_version_changed)

        topLay = QHBoxLayout(topBar); topLay.setContentsMargins(0,0,0,0)
        topLay.addWidget(self.titleEdit, 1)          # stretch title to use space
        topLay.addSpacing(8)
        topLay.addWidget(self.centerStatus)
        topLay.addSpacing(8)
        topLay.addWidget(self.wordSyncBtn, 0)
        topLay.addSpacing(8)
        topLay.addWidget(self.centerModeBtn, 0)
        topLay.addSpacing(8)
        topLay.addWidget(self.cmbChapterVersion)

        # editor (markdown source) & preview (rendered HTML)
        self.centerEdit = QPlainTextEdit()
        self.centerEdit.textChanged.connect(self._center_mark_dirty)
        self.centerView = QTextBrowser()
        self.centerView.setOpenExternalLinks(True)
        self.centerView.setOpenLinks(True)

        self.centerPane = DropPane(self)
        cv = QVBoxLayout(self.centerPane)
        cv.setContentsMargins(0,0,0,0)
        cv.addWidget(topBar)
        cv.addWidget(self.centerView)   # default: start in View mode
        cv.addWidget(self.centerEdit)

        # start in view mode
        self._center_set_mode(view_mode=True)

    def _add_right_panel(self):
        # Right detail panel
        self.worldDetail = WorldDetailWidget(self)

    def _add_bottom_tabs(self):
        # Bottom tabs
        self.bottomTabs = QTabWidget()

        # Mini Outline tab
        self.tabMiniOutline = MiniOutlineTab(self)
        self.outlineWorkspace.set_single_mini(self.tabMiniOutline)
        print("MAIN: set page.single_mini →", bool(self.outlineWorkspace.page.single_mini))
        # If you later support multiple minis, change single_mini to a list and iterate. ^
        self.tabMiniOutline.set_workspace(self.outlineWorkspace)  # share workspace/undo
        self.tabMiniOutline.openFullRequested.connect(self._open_full_outline_window)
        self.bottomTabs.addTab(self.tabMiniOutline, "Outline")

        # TO-DOs tab
        self.tabTodos = ChapterTodosWidget(self)
        self.bottomTabs.addTab(self.tabTodos,   "To-Dos/Notes")

        # Progress / Timeline / Changes tabs (placeholders for now)
        self.tabProgress = PlainNoTab(); self.tabProgress.setPlaceholderText("Progress…")
        self.bottomTabs.addTab(self.tabProgress,"Progress")

        self.tabTimeline = PlainNoTab(); self.tabTimeline.setPlaceholderText("Timeline…")
        self.bottomTabs.addTab(self.tabTimeline,"Timeline")

        self.tabChanges  = PlainNoTab(); self.tabChanges.setPlaceholderText("Changes…")
        self.bottomTabs.addTab(self.tabChanges, "Changes")

        # Search results tab
        self.tabSearch   = QTreeWidget();    self.tabSearch.setHeaderLabels(["Result", "Where"])
        self.tabSearch.itemDoubleClicked.connect(self.open_search_result)
        self.bottomTabs.addTab(self.tabSearch,  "Search")

    def _add_splitters(self):
        # Splitters — final layout: Left | (Center+Right over Bottom)
        self.centerRightSplit = QSplitter(Qt.Horizontal)
        self.centerRightSplit.addWidget(self.centerPane)
        self.centerRightSplit.addWidget(self.worldDetail)
        self.centerRightSplit.setStretchFactor(0, 2)
        self.centerRightSplit.setStretchFactor(1, 1)

        self.midVertSplit = QSplitter(Qt.Vertical)
        self.midVertSplit.addWidget(self.centerRightSplit)
        self.midVertSplit.addWidget(self.bottomTabs)
        self.midVertSplit.setStretchFactor(0, 4)
        self.midVertSplit.setStretchFactor(1, 1)

        self.hSplit = QSplitter(Qt.Horizontal)
        self.hSplit.addWidget(self.leftSplit)
        self.hSplit.addWidget(self.midVertSplit)
        self.hSplit.setStretchFactor(0, 0)
        self.hSplit.setStretchFactor(1, 1)

        # Central widget: single, stable layout (no reparenting later)
        self.setCentralWidget(self.hSplit)

    def _add_keybindings(self):
        # Key bindings for world navigation
        self.addAction(self._mk_shortcut("Alt+Left", self.worldDetail.go_back))
        self.addAction(self._mk_shortcut("Alt+Right", self.worldDetail.go_forward))

        # Zoom shortcuts
        self.addAction(self._mk_shortcut("Ctrl++", self.zoom_in))
        self.addAction(self._mk_shortcut("Ctrl+=", self.zoom_in))   # many keyboards need "=" for "+"
        self.addAction(self._mk_shortcut("Ctrl+-", self.zoom_out))
        self.addAction(self._mk_shortcut("Ctrl+0", self.zoom_reset))

        # Zoom menu (see ui_zoom.py, search for "IMPORTANT:" if you want to have global zoom in a single button)
        # or see chatGPT > Writing tool > Streamlit vs. PySide6
        self.uiZoom = UiZoom(base_pt=None, parent=self)  # auto-detect baseline from app font
        self.uiZoom.attach_menu(self.viewMenu, add_shortcuts=True)

        # Ctrl+N: new blank chapter at end of current book
        self.addAction(self._mk_shortcut("Ctrl+N", self.insert_blank_chapter_after_current_last))

        self._init_zoom_state()
        self._set_zoom(1.0)

    def _build_ui(self):
        # Add menu bar
        self._setup_menus()

        # Add project header (project manager button) to the toolbar
        self._add_project_header()

        # Add search to toolbar
        self._add_search_toolbar()

        # Add left panel trees (chapters, world, refs)
        self._add_left_panel()

        # Add center editor panel
        self._add_center_editor()

        # Add right detail panel (world item detail)
        self._add_right_panel()

        # Add bottom tabs (to-dos, progress, timeline, changes, search results)
        self._add_bottom_tabs()

        # Add splitters to arrange the main layout
        self._add_splitters()

        # Add keybinding shortcuts
        self._add_keybindings()

        # initial state
        self._update_word_sync_ui()

        # Refresh text when project changes
        self.refresh_project_header()

    def _mk_shortcut(self, keyseq, slot):
        act = QAction(self)
        act.setShortcut(QKeySequence(keyseq))
        act.triggered.connect(slot)
        return act

    def _wire_actions(self):
        # Chapters tree signals
        self.chaptersTree.itemClicked.connect(self.on_chapter_clicked)
        self.chaptersTree.fileDropped.connect(self.import_chapters_from_paths, Qt.ConnectionType.UniqueConnection)

        # World tree signals
        self.worldTree.itemClicked.connect(self.on_world_clicked)
        self.titleEdit.editingFinished.connect(self.autosave_chapter_title)
        self.worldTree.fileDropped.connect(self.import_world_items_from_paths, Qt.ConnectionType.UniqueConnection)
        self.worldTree.itemChanged.connect(self._on_worldtree_item_changed_name)
        # detect Esc/accept close on inline editor to remove blank temp items
        self.worldTree.itemDelegate().closeEditor.connect(self._on_worldtree_editor_closed)

        # Center pane file drop
        self.centerPane.fileDropped.connect(self.import_chapters_from_paths, Qt.ConnectionType.UniqueConnection)

        # Center editor dirty tracking
        self.centerEdit.textChanged.connect(self._center_mark_dirty)
        # Title changes should also mark the chapter dirty (respect Word lock inside _center_mark_dirty)
        self.titleEdit.textEdited.connect(lambda _=None: self._center_mark_dirty())

        # Characters page signals
        # self.charactersPage.characterOpenRequested.connect(self.open_character_editor)
        self.charactersPage.characterOpenRequested.connect(self.open_character_dialog)

        # Outline workspace signals
        self.outlineWorkspace.versionChanged.connect(self._on_workspace_version_changed)
        self.outlineWorkspace.chapterDeleteRequested.connect(self._on_outline_delete_chapter)
        self.chaptersOrderChanged.connect(self.outlineWorkspace.apply_order_by_ids)

    def _outline_controller(self):
        return self.outlineWorkspace.page.undoController

    def _is_outline_surface_active(self) -> bool:
        w = QApplication.focusWidget()
        if not w:
            return False
        ws = getattr(self, "outlineWorkspace", None)
        if not ws or not getattr(ws, "page", None):
            return False
        page = ws.page
        # any pane editor/tree OR the mini itself
        in_pane = any(p.editor.isAncestorOf(w) or p.isAncestorOf(w) for p in page.panes)
        mini = getattr(page, "single_mini", None)
        in_mini = bool(mini and (mini.editor.isAncestorOf(w) or mini.isAncestorOf(w)))
        return in_pane or in_mini

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress and self._is_outline_surface_active():
            key = ev.key(); mods = ev.modifiers()
            if (mods & Qt.ControlModifier) and key == Qt.Key_Z and not (mods & Qt.ShiftModifier):
                print("APP-ROUTER: Ctrl+Z → OutlineController.undo()")
                self._outline_controller().undo()
                return True
            if ((mods & Qt.ControlModifier) and key == Qt.Key_Y) or \
            ((mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier) and key == Qt.Key_Z):
                print("APP-ROUTER: Redo → OutlineController.redo()")
                self._outline_controller().redo()
                return True
        return super().eventFilter(obj, ev)

    def _outline_undo(self):
        ctrl = self._outline_controller()
        print("MAIN: Ctrl+Z")
        if ctrl:
            ctrl.undo()

    def _outline_redo(self):
        ctrl = self._outline_controller()
        print("MAIN: Ctrl+Y / Ctrl+Shift+Z")
        if ctrl:
            ctrl.redo()

    def insert_new_world_item_inline(self, category_id: int, category_item: QTreeWidgetItem,
                                    mode: str = "end", ref_item: QTreeWidgetItem | None = None):
        """
        Create a temp editable world item within a category:
        - mode="end": append at bottom
        - mode="above"/"below": position relative to ref_item
        Stores planned insert index in UserRole+1 to use on commit.
        """
        # Compute visual insert position among children
        insert_row = category_item.childCount()
        if mode in ("above", "below") and ref_item is not None:
            base = category_item.indexOfChild(ref_item)
            if base >= 0:
                insert_row = base if mode == "above" else base + 1

        # Create temp node
        temp = QTreeWidgetItem([""])
        temp.setData(0, Qt.UserRole, ("world_item_temp", int(category_id)))
        temp.setData(0, Qt.UserRole + 1, insert_row)  # remember row
        temp.setFlags(temp.flags() | Qt.ItemIsEditable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)

        # Insert visually at computed row
        category_item.insertChild(insert_row, temp)
        self.worldTree.expandItem(category_item)
        self.worldTree.setCurrentItem(temp)
        self.worldTree.editItem(temp, 0)

        self._world_temp_item = temp

    def _on_worldtree_item_changed_name(self, item: QTreeWidgetItem, column: int):
        d = item.data(0, Qt.UserRole)
        if not d or d[0] != "world_item_temp" or column != 0:
            return
        title = (item.text(0) or "").strip()
        if not title:
            return  # wait for close/cancel to handle purge
        
        cat_id = int(d[1])
        planned_row = item.data(0, Qt.UserRole + 1) or 0

        # Compute world-item-only index (defensive: category might also contain subcategories later)
        # Here, we assume only world items are children; if mixed, filter by ("world_item", id)
        text_only_index = 0
        parent = item.parent()
        for i in range(min(int(planned_row), parent.childCount())):
            ch = parent.child(i)
            dd = ch.data(0, Qt.UserRole)
            if dd and dd[0].startswith("world_item"):  # temp or real
                text_only_index += 1

        # Insert in DB at index
        pid = self._current_project_id
        item_type = self.infer_world_type_from_category(parent.data(0, Qt.UserRole)[1])
        new_id = self.db.world_item_insert_at_index(pid, cat_id, title, text_only_index, item_type)

        # Convert temp node to real
        item.setData(0, Qt.UserRole, ("world_item", new_id))
        item.setData(0, Qt.UserRole + 1, None)
        item.setFlags((item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled) & ~Qt.ItemIsEditable)

        if getattr(self, "_world_temp_item", None) is item:
            self._world_temp_item = None

        # open characters in character editor
        if item_type == "character":
            # self.open_character_editor(new_id)
            self.open_character_dialog(new_id)
        # Open in right panel, EDIT mode, cursor ready
        else:
            self.load_world_item(new_id, edit_mode=True)  # use your updated method that replaces old load_world_detail


    def _on_worldtree_editor_closed(self, editor: QWidget, hint: QAbstractItemDelegate.EndEditHint):
        # If a temp is being edited and user Escaped or ended without a name -> remove it
        temp = getattr(self, "_world_temp_item", None)
        if not temp:
            return
        d = temp.data(0, Qt.UserRole)
        if not d or d[0] != "world_item_temp":
            return

        title = (temp.text(0) or "").strip()
        if title:
            # already handled in _on_worldtree_item_changed_name
            return

        # Remove the temp row
        parent = temp.parent()
        if parent:
            parent.removeChild(temp)
        else:
            idx = self.worldTree.indexOfTopLevelItem(temp)
            if idx >= 0:
                self.worldTree.takeTopLevelItem(idx)
        self._world_temp_item = None

    def insert_blank_chapter_after_current_last(self):
        """Append a blank chapter at the end of the current book and open it."""
        # figure out current book; if you already track self._current_book_id, use that.
        cur = self.db.conn.cursor()
        # pick current book or the first book
        book_id = getattr(self, "_current_book_id", None)
        if not book_id:
            cur.execute("SELECT id FROM books WHERE project_id=? AND COALESCE(deleted,0)=0 ORDER BY position, id LIMIT 1",
                        (self._current_project_id,))
            row = cur.fetchone()
            if not row: return
            book_id = int(row[0])

        # find last position
        # cur.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE book_id=? AND COALESCE(deleted,0)=0", (book_id,))
        # last = cur.fetchone()[0] or -1
        # insert_pos = last + 1
        # print("insert pos", insert_pos)
        last_pos_idx = self.db.chapter_last_position_index(self._current_project_id, self._current_book_id)
        insert_pos = last_pos_idx + 1
        new_id = self.db.chapter_insert(self._current_project_id, self._current_book_id, insert_pos, "New Chapter", "")

        # cur.execute("""
        #     INSERT INTO chapters (project_id, book_id, title, content, position)
        #     VALUES (?, ?, ?, ?, ?)
        # """, (self._current_project_id, book_id, "Untitled Chapter", "", insert_pos))
        # new_id = cur.lastrowid
        self.db.conn.commit()

        self.populate_chapters_tree()
        if hasattr(self, "focus_chapter_in_tree"):
            self.focus_chapter_in_tree(new_id)
        if hasattr(self, "load_chapter"):
            self.load_chapter(new_id)

    def insert_chapters_dialog(self):
        """Pick one or more files, then choose placement + name-splitting, then insert contiguously."""
        # 1) Pick files
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Insert Chapter(s)…",
            self._default_import_dir(),
            "Documents (*.docx *.md *.markdown *.txt);;All Files (*)"
        )
        if not paths:
            return

        # 2) De-dupe/normalize paths
        norm, seen = [], set()
        for p in paths:
            q = str(Path(p).resolve())
            if q not in seen:
                seen.add(q)
                norm.append(q)
        paths = norm

        # 3) Placement + split dialog (same as bulk drop)
        dlg = BulkChapterImportDialog(self, paths, self)
        if dlg.exec() != QDialog.Accepted:
            return
        choice = dlg.chosen()   # {"mode": "first"/"last"/"after", "anchor_cid": int|None, "sep": str|None}

        # 4) Do the contiguous insert
        self._bulk_insert_chapters(paths, choice)

    def _bulk_insert_chapters(self, paths: list[str], choice: dict):
        """Insert files contiguously with a single gap open; renumber & refresh."""
        if not paths:
            return
        # Re-entrancy guard
        if getattr(self, "_importing_chapters_now", False):
            return
        self._importing_chapters_now = True
        try:
            pid = self._current_project_id
            bid = self._current_book_id
            # cur = self.db.conn.cursor()

            # choice fields
            sep        = choice.get("sep")
            mode       = choice.get("mode")
            anchor_cid = choice.get("anchor_cid")

            # Build current position map (excluding deleted)
            # cur.execute("""
            #     SELECT id, position
            #     FROM chapters
            #     WHERE project_id=? AND book_id=? AND COALESCE(deleted,0)=0
            #     ORDER BY position, id
            # """, (pid, bid))
            # rows = cur.fetchall()
            rows = self.db.chapter_list(pid, bid)
            pos_map = { (r[0] if not isinstance(r, sqlite3.Row) else r["id"]) :
                        (r[1] if not isinstance(r, sqlite3.Row) else r["position"]) for r in rows }

            # Compute base insert index
            if mode == "first":
                base_index = 0
            elif mode == "last":
                # cur.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE project_id=? AND book_id=?", (pid, bid))
                # base_index = (cur.fetchone()[0] or -1) + 1
                base_index = self.db.chapter_last_position_index(pid, bid)
            elif mode == "after" and anchor_cid in pos_map:
                base_index = pos_map[anchor_cid] + 1
            else:
                # fallback to end
                # cur.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE project_id=? AND book_id=?", (pid, bid))
                # base_index = (cur.fetchone()[0] or -1) + 1
                base_index = self.db.chapter_last_position_index(pid, bid)

            # Parse names & compute import order
            parsed = []
            for idx, p in enumerate(paths):
                order_hint, clean = parse_chapter_filename(Path(p).name, split_mode=sep)
                parsed.append((idx, order_hint, clean, p))
            # Respect leading numbers if present; keep original order otherwise
            parsed.sort(key=lambda t: (t[1] is None,
                                    999999 if t[1] is None else t[1],
                                    t[0]))

            N = len(parsed)

            # # === Open a gap so inserts are contiguous (no interleaving) ===
            # cur.execute("""
            #     UPDATE chapters
            #     SET position = position + ?
            #     WHERE project_id=? AND book_id=? AND position >= ? AND COALESCE(deleted,0)=0
            # """, (N, pid, bid, base_index))
            self.db.chapter_position_gap(N, pid, bid, base_index)

            # === Insert files into the gap ===
            new_ids = []
            for i, (_, _, title, p) in enumerate(parsed):
                md = read_file_as_markdown(p)
                new_id = self.db.chapter_insert(pid, bid, base_index + i, title, md)
                new_ids.append(new_id)
                # detect refs per chapter
                self.recompute_chapter_references(new_id, md)

            # === Normalize 0..M-1 positions (deleted rows ignored) ===
            self._compact_positions_after_insert(bid)

            self.db.conn.commit()
            self.populate_chapters_tree()
            if new_ids:
                self.load_chapter(new_ids[0])
        finally:
            self._importing_chapters_now = False


    # ---------- Populate ----------
    def populate_all(self):
        self.populate_chapters_tree()
        self.populate_world_tree()
        self.populate_refs_tree(None)

    def populate_chapters_tree(self):
        self.chaptersTree.clear()
        pid = self._current_project_id

        # Add books
        for b in self.db.book_list(pid):
            bid, bname = int(b["id"]), b["name"]
            bitem = QTreeWidgetItem([bname])
            bitem.setData(0, Qt.UserRole, ("book", bid))
            bitem.setFlags(bitem.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled)
            self.chaptersTree.addTopLevelItem(bitem)

            # Add chapters under book
            for ch in self.db.chapter_list(pid, bid):
                label = chapter_display_label(ch["position"], ch["title"])
                citem = QTreeWidgetItem([label])
                flags = citem.flags()
                flags |= (Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                citem.setData(0, Qt.UserRole, ("chapter", int(ch["id"])))
                citem.setFlags(flags)
                bitem.addChild(citem)
        self.chaptersTree.expandAll()

        self._emit_chapter_order()

    def _emit_chapter_order(self):
        rows = self.db.chapter_list(self._current_project_id, self._current_book_id)
        ordered_ids = [r["id"] for r in rows]
        self.chaptersOrderChanged.emit(self._current_project_id, self._current_book_id, ordered_ids)

    def populate_world_tree(self):
        self.worldTree.clear()
        pid = self._current_project_id

        def add_cat_node(cat_id, name, parent=None):
            node = QTreeWidgetItem([name])
            node.setData(0, Qt.UserRole, ("world_cat", cat_id))
            node.setFlags(node.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled | Qt.ItemIsDragEnabled)
            if parent is None:
                self.worldTree.addTopLevelItem(node)
            else: parent.addChild(node)
            return node

        def add_item_node(item_id, title, parent):
            node = QTreeWidgetItem([title])
            node.setData(0, Qt.UserRole, ("world_item", item_id))
            node.setFlags(node.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            parent.addChild(node)
            return node

        def recurse(cat_node, cat_id):
            for scid, scname in self.db.world_categories_children(cat_id, pid):
                scnode = add_cat_node(scid, scname, cat_node)
                # items under this
                for iid, ititle in self.db.world_items_by_category(project_id=pid, category_id=scid):
                    add_item_node(iid, ititle, scnode)
                recurse(scnode, scid)

        # top-level categories
        for cid, cname in self.db.world_categories_top_level(pid):
            cnode = add_cat_node(cid, cname, None)
            for iid, ititle in self.db.world_items_by_category(project_id=pid, category_id=cid):
                add_item_node(iid, ititle, cnode)
            recurse(cnode, cid)

        self.worldTree.expandAll()

    def populate_refs_tree(self, chapter_id: int | None):
        t = self.refsTree
        t.clear()
        if not chapter_id:
            t.addTopLevelItem(QTreeWidgetItem(["(Open a chapter to see references)"]))
            t.expandAll()
            return
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT * from chapter_world_refs
        """)
        cwf = cur.fetchall()
        # group by category
        cur.execute("""
            SELECT wc.name, wi.id, wi.title
            FROM chapter_world_refs r
            JOIN world_items wi ON wi.id=r.world_item_id
            LEFT JOIN world_categories wc ON wc.id=wi.category_id
            WHERE r.chapter_id=?
            ORDER BY wc.name, wi.title
        """, (chapter_id,))
        rows = cur.fetchall()
        if not rows:
            t.addTopLevelItem(QTreeWidgetItem(["(No references detected)"]))
            t.expandAll()
            return
        groups = {}
        for cname, wid, wtitle in rows:
            key = cname or "(Uncategorized)"
            groups.setdefault(key, []).append((wid, wtitle))
        for gname, items in groups.items():
            gnode = QTreeWidgetItem([gname])
            gnode.setData(0, Qt.UserRole, ("refs_group", gname))
            t.addTopLevelItem(gnode)
            for wid, wtitle in items:
                inode = QTreeWidgetItem([wtitle])
                inode.setData(0, Qt.UserRole, ("world_item", wid))  # clickable like world tree
                gnode.addChild(inode)
        t.expandAll()

    def compact_and_renumber_after_tree_move(self, src_parent, dest_parent):
        """
        After a Qt reorder/move, persist 0..N-1 positions for the affected book(s)
        based on the current *visible* order in the tree (which already excludes
        soft-deleted chapters). Then relabel the on-screen items without a full reload.
        """

        def climb_to_book(node):
            """If node is a chapter, climb to its book; if already a book, return it; else None."""
            if not node:
                return None
            d = node.data(0, Qt.UserRole) or ()
            if d and d[0] == "book":
                return node
            # climb
            p = node
            while p and p.parent():
                p = p.parent()
                dd = p.data(0, Qt.UserRole) or ()
                if dd and dd[0] == "book":
                    return p
            return None

        handled = set()
        for raw in (src_parent, dest_parent):
            book_node = climb_to_book(raw)
            if not book_node or book_node in handled:
                continue
            handled.add(book_node)

            bdata = book_node.data(0, Qt.UserRole) or ()
            if not (bdata and bdata[0] == "book"):
                continue
            book_id = int(bdata[1])

            # 1) Read the visual order (chapters only, i.e., non-deleted)
            ordered_ids = []
            for i in range(book_node.childCount()):
                ch = book_node.child(i)
                cd = ch.data(0, Qt.UserRole) or ()
                if cd and cd[0] == "chapter":
                    ordered_ids.append(int(cd[1]))

            # Nothing to persist
            if not ordered_ids:
                continue

            # 2) Persist compact positions 0..N-1 into DB for these chapters
            cur = self.db.conn.cursor()
            for pos, cid in enumerate(ordered_ids):
                cur.execute("UPDATE chapters SET book_id=?, position=? WHERE id=?",
                            (book_id, pos, cid))
            self.db.conn.commit()

            # 3) Relabel visible nodes to "n. title" using fresh titles from DB (no full reload)
            qmarks = ",".join("?" for _ in ordered_ids)
            cur.execute(f"SELECT id, title FROM chapters WHERE id IN ({qmarks})", ordered_ids)
            id_to_title = {int(r[0]): (r[1] if not isinstance(r, sqlite3.Row) else r["title"])
                        for r in cur.fetchall()}

            # Keep numbering contiguous (1-based in labels)
            for i in range(book_node.childCount()):
                ch = book_node.child(i)
                cd = ch.data(0, Qt.UserRole) or ()
                if cd and cd[0] == "chapter":
                    cid = int(cd[1])
                    # Find its new 0-based pos as index in ordered_ids
                    try:
                        pos0 = ordered_ids.index(cid)
                    except ValueError:
                        # Not in ordered_ids → likely a non-chapter or deleted (shouldn't happen here)
                        continue
                    base_title = id_to_title.get(cid, ch.text(0))
                    ch.setText(0, f"{pos0 + 1}. {base_title}")

    def _relabel_book_node(self, book_node, ordered_ids, id_to_title):
        """Update tree item texts for a book node to reflect current numbering."""
        pos = 0
        # Walk all children; only chapters get numbered; keep the same items (no recreate)
        for i in range(book_node.childCount()):
            ch = book_node.child(i)
            cd = ch.data(0, Qt.UserRole)
            if cd and cd[0] == "chapter":
                cid = int(cd[1])
                title = id_to_title.get(cid, ch.text(0))  # fallback just in case
                # Strip any existing "n. " prefix before reapplying
                base = title
                # If the current text has "N. Title", prefer DB title we fetched; otherwise strip prefix from current text.
                if not id_to_title:
                    import re
                    base = re.sub(r"^\s*\d+\.\s+", "", ch.text(0)).strip()
                ch.setText(0, f"{pos+1}. {base}")
                pos += 1


    # ---------- Events ----------
    def on_chapter_clicked(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "chapter": return
        chap_id = data[1]
        self.save_current_if_dirty()
        self.load_chapter(chap_id)

    def on_world_clicked(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data: return
        if data[0] == "world_item":
            self.worldDetail.show_item(int(data[1]))

    def on_refs_clicked(self, item, col):
        data = item.data(0, Qt.UserRole)
        if data and data[0] == "world_item":
            self.worldDetail.show_item(int(data[1]))

    # ---------- Chapter load/save ----------
    def _current_outline_row(self) -> int:
        # ask the workspace’s list view
        idx = self.outlineWorkspace.list.currentIndex()
        return idx.row() if idx.isValid() else -1

    def _populate_header_versions(self, chap_id: int):
        row = self._current_outline_row()
        names = self.outlineWorkspace.versions_for_row(row) if row >= 0 else ["v1"]
        current = self.outlineWorkspace.current_version_for_row(row) or (names[0] if names else "")
        with QSignalBlocker(self.cmbChapterVersion):
            self.cmbChapterVersion.clear()
            self.cmbChapterVersion.addItems(names)
            # self.cmbChapterVersion.addItem("➕ New version…")
            self.cmbChapterVersion.setCurrentText(current)

    def load_chapter(self, chap_id: int):
        """Load a chapter into the center pane. Saves current edits first."""
        # Save current if needed
        self.save_current_if_dirty()

        self._current_chapter_id = chap_id

        # --- fetch title/content from DB (unchanged) ---
        cur = self.db.conn.cursor()
        cur.execute("SELECT title, content FROM chapters WHERE id=?", (chap_id,))
        row = cur.fetchone()
        title, md = ((row["title"], row["content"]) if isinstance(row, sqlite3.Row) else row) if row else ("", "")

        # center title/content
        self.titleEdit.blockSignals(True)
        self.titleEdit.setText(title or "")
        self.titleEdit.blockSignals(False)

        self.centerEdit.blockSignals(True)
        self.centerEdit.setPlainText(md or "")
        self.centerEdit.blockSignals(False)

        self._chapter_dirty = False

        if hasattr(self, "centerStatus"):
            self.centerStatus.show_neutral("Viewing")

        # Always show View mode on load (per your preference)
        self._render_center_preview()
        self._center_set_mode(view_mode=True)
        self._update_word_sync_ui()

        # tabs that depend on current chapter
        self.tabTodos.set_chapter(chap_id)
        self.populate_refs_tree(chap_id)
        self.focus_chapter_in_tree(chap_id)

        # --- resolve the *outline row* safely ---
        # 1) try current selection in outline list
        outline_row = self._current_outline_row()   # returns -1 if none
        # 2) if none, try workspace id→row map
        if outline_row < 0 and hasattr(self.outlineWorkspace, "row_for_chapter_id"):
            outline_row = self.outlineWorkspace.row_for_chapter_id(chap_id)
        # 3) if still none, default to 0 when the model has rows and select it
        ow_model = getattr(self.outlineWorkspace, "model", None)
        if (outline_row < 0) and ow_model and ow_model.rowCount() > 0:
            outline_row = 0
            # keep the left list visually in sync (won't steal focus)
            try:
                idx = ow_model.index(outline_row, 0)
                # outline list widget may be named `list` in your workspace
                outline_list = getattr(self.outlineWorkspace, "list", None)
                if outline_list is not None:
                    outline_list.setCurrentIndex(idx)
            except Exception:
                pass

        # --- populate the center header version combo based on the resolved row ---
        with QSignalBlocker(self.cmbChapterVersion):
            self.cmbChapterVersion.clear()
            versions = []
            current_name = ""

            if outline_row is not None and outline_row >= 0:
                try:
                    versions = self.outlineWorkspace.versions_for_row(outline_row) or []
                    current_name = self.outlineWorkspace.current_version_for_row(outline_row) or (versions[0] if versions else "")
                except Exception:
                    # guard if workspace isn’t fully initialized yet
                    versions = []
                    current_name = ""

            if not versions:
                versions = ["v1"]  # safe default if nothing is loaded yet

            self.cmbChapterVersion.addItems(versions)
            if current_name:
                self.cmbChapterVersion.setCurrentText(current_name)
            else:
                self.cmbChapterVersion.setCurrentIndex(0)

        # set most recently viewed chapter (for use on reopen)
        self.db.ui_pref_set(self._current_project_id, "outline:last_chapter_id", str(chap_id))
        self.db.ui_pref_set(self._current_project_id, f"chapters:last:{self._current_book_id}", str(chap_id))

        # keep mini pinned
        self.tabMiniOutline.set_workspace(self.outlineWorkspace)
        self.tabMiniOutline.set_chapter(self._current_chapter_id)

    def mark_chapter_dirty(self):
        self._chapter_dirty = True
        if hasattr(self, "centerStatus"):
            self.centerStatus.set_dirty()
        self.setWindowModified(True)

    def autosave_chapter_title(self):
        if self._current_chapter_id is None: return
        title = self.titleEdit.text().strip()
        cur = self.db.conn.cursor()
        cur.execute("UPDATE chapters SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (title, self._current_chapter_id))
        self.db.conn.commit()
        # update FTS row via trigger; refresh tree label
        self.populate_chapters_tree()

    def save_current_if_dirty(self):
        """Single place to persist all 'dirty' state for the current chapter + panels."""
        chap_id = getattr(self, "_current_chapter_id", None)
        if chap_id is None:
            return

        # 1) Word lock guard: skip editing content/title if this chapter is locked by Word
        word_locked_id = getattr(self, "_word_lock_chapter_id", None)
        locked = (word_locked_id is not None and word_locked_id == chap_id)

        if getattr(self, "_chapter_dirty", False) and not locked:
            title = self.titleEdit.text().strip()
            md    = self.centerEdit.toPlainText()
            cur = self.db.conn.cursor()
            cur.execute("UPDATE chapters SET title=?, content=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (title, md, chap_id))
            self.db.conn.commit()
            self._chapter_dirty = False
            if hasattr(self, "centerStatus"):
                self.centerStatus.set_saved_now()
            # mark chapter or word-cync dirty
            self.setWindowModified(bool(self._chapter_dirty or getattr(self.worldDetail, "_dirty", False)))

            # Update preview + references (only if this is the active chapter)
            self._render_center_preview()
            self.recompute_chapter_references(chap_id, md)
            self.populate_refs_tree(chap_id)
            self.populate_chapters_tree()  # renumber labels if title changed

        # 2) Persist To-Dos/Notes if your widgets track their own dirty state
        if hasattr(self.tabTodos, "save_if_dirty"):
            self.tabTodos.save_if_dirty(chap_id)

        # (Optional) if you add more bottom tabs with dirty state, call their save methods here.


    # ---------- Reference extraction on save ----------
    def recompute_chapter_references(self, chapter_id: int, text: str):
        cur = self.db.conn.cursor()
        pid = self._current_project_id
        # Build alias map
        name_map = {}
        cur.execute("SELECT id, title FROM world_items WHERE project_id=? AND COALESCE(deleted,0)=0", (pid,))
        for wid, title in cur.fetchall():
            if title:
                name_map.setdefault(title.lower(), set()).add(wid)
        cur.execute("""
            SELECT wa.world_item_id, wa.alias
            FROM world_aliases wa
            JOIN world_items wi ON wi.id=wa.world_item_id
            WHERE wi.project_id=?
        """, (pid,))
        for wid, alias in cur.fetchall():
            if alias:
                name_map.setdefault(alias.lower(), set()).add(wid)

        tokens = set(re.findall(r"\b[\w'-]+\b", (text or "").lower()))
        found = set()
        for tok in tokens:
            if tok in name_map:
                for wid in name_map[tok]:
                    found.add(wid)

        cur.execute("DELETE FROM chapter_world_refs WHERE chapter_id=?", (chapter_id,))
        cur.executemany(
            "INSERT OR IGNORE INTO chapter_world_refs (chapter_id, world_item_id) VALUES (?, ?)",
            [(chapter_id, wid) for wid in sorted(found)]
        )
        self.db.conn.commit()

    # ---------- Render world MD with auto-links ----------
    def rebuild_world_item_render(self, world_item_id: int):
        cur = self.db.conn.cursor()
        cur.execute("SELECT title, content_md FROM world_items WHERE id=?", (world_item_id,))
        row = cur.fetchone()
        if not row: 
            return
        title, md = row["title"], row["content_md"] or ""

        # Build alias->id map across project
        alias_map = {}
        cur.execute("SELECT id, title FROM world_items WHERE project_id=? AND COALESCE(deleted,0)=0", (self._current_project_id,))
        for wid, ttl in cur.fetchall():
            if ttl: alias_map[ttl.lower()] = wid
        cur.execute("""
            SELECT wa.world_item_id, wa.alias
            FROM world_aliases wa
            JOIN world_items wi ON wi.id=wa.world_item_id
            WHERE wi.project_id=?
        """, (self._current_project_id,))
        for wid, alias in cur.fetchall():
            if alias and alias.lower() not in alias_map:
                alias_map[alias.lower()] = wid

        # Linkify tokens, but never link to self
        def linkify(match):
            w = match.group(0)
            wid = alias_map.get(w.lower())
            if wid and wid != world_item_id:
                return f'<a href="world://{wid}">{w}</a>'
            # optional: style self mentions (commented out)
            # if wid == world_item_id:
            #     return f'<span style="color:#888;text-decoration:underline dotted">{w}</span>'
            return w

        md_linked = re.sub(r"\b[\w'-]+\b", linkify, md)
        html = md_to_html(md_linked)
        cur.execute("UPDATE world_items SET content_render=? WHERE id=?", (html, world_item_id))
        self.db.conn.commit()

    # ---------- DnD persistence ----------
    def sync_chapters_order_from_tree(self):
        cur = self.db.conn.cursor()
        for i in range(self.chaptersTree.topLevelItemCount()):
            bnode = self.chaptersTree.topLevelItem(i)
            bdata = bnode.data(0, Qt.UserRole)
            if not bdata or bdata[0] != "book": 
                continue
            book_id = bdata[1]
            ordered_ids = []
            for j in range(bnode.childCount()):
                cnode = bnode.child(j)
                cdata = cnode.data(0, Qt.UserRole)
                if cdata and cdata[0] == "chapter":
                    ordered_ids.append(int(cdata[1]))
            for pos, cid in enumerate(ordered_ids):
                cur.execute("UPDATE chapters SET book_id=?, position=? WHERE id=?", (book_id, pos, cid))
        self.db.conn.commit()
        # repaint on next tick to avoid flicker or half-state
        QTimer.singleShot(0, self.populate_chapters_tree)

    def sync_world_order_from_tree(self):
        cur = self.db.conn.cursor()
        # Persist categories positions/parents and items' category
        def recurse(parent_node, parent_cat_id):
            # first pass: categories positions
            cpos = 0
            for i in range(parent_node.childCount()):
                ch = parent_node.child(i)
                d = ch.data(0, Qt.UserRole)
                if d and d[0] == "world_cat":
                    cur.execute("UPDATE world_categories SET parent_id=?, position=? WHERE id=?",
                                (parent_cat_id, cpos, d[1]))
                    cpos += 1
            # second pass: items (keep alphabetical; no explicit position needed)
            for i in range(parent_node.childCount()):
                ch = parent_node.child(i)
                d = ch.data(0, Qt.UserRole)
                if d and d[0] == "world_item":
                    cur.execute("UPDATE world_items SET category_id=? WHERE id=?", (parent_cat_id, d[1]))
                elif d and d[0] == "world_cat":
                    recurse(ch, d[1])

        # top-level categories
        for i in range(self.worldTree.topLevelItemCount()):
            node = self.worldTree.topLevelItem(i)
            d = node.data(0, Qt.UserRole)
            if d and d[0] == "world_cat":
                cur.execute("UPDATE world_categories SET parent_id=NULL, position=? WHERE id=?", (i, d[1]))
                recurse(node, d[1])
        self.db.conn.commit()
        self.populate_world_tree()

    # ---------- Import ----------
    # def action_import_chapter(self):
    #     self.save_current_if_dirty()
    #     fn, _ = QFileDialog.getOpenFileName(self, "Import Chapter", self._default_import_dir(), "Documents (*.md *.txt *.docx);;All Files (*)")
    #     if not fn:
    #         print("No file name")
    #         return
    #     self.import_chapters_paths([fn])

    # def action_insert_chapter(self):
    #     self.save_current_if_dirty()
    #     fn, _ = QFileDialog.getOpenFileName(self, "Insert Chapter", self._default_import_dir(), "Documents (*.md *.txt *.docx);;All Files (*)")
    #     if not fn: return
    #     # Simple picker: First / Last / Between existing
    #     index = self._prompt_insert_position()
    #     self.import_chapters_paths([fn], insert_index=index)

    # def _prompt_insert_position(self) -> int | None:
    #     # Build a quick selector dialog of current book chapters
    #     cur = self.db.conn.cursor()
    #     cur.execute("""SELECT id, title FROM chapters
    #                    WHERE project_id=? AND book_id=?
    #                    ORDER BY position, id""", (self._current_project_id, self._current_book_id))
    #     rows = cur.fetchall()
    #     dlg = QDialog(self); dlg.setWindowTitle("Insert Position")
    #     v = QVBoxLayout(dlg)
    #     btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    #     lst = QListWidget()
    #     lst.addItem("(First)")
    #     for _, title in rows:
    #         lst.addItem(f"After: {title}")
    #     lst.addItem("(Last)")
    #     v.addWidget(QLabel("Insert the chapter…"))
    #     v.addWidget(lst)
    #     v.addWidget(btns)
    #     btns.accepted.connect(dlg.accept)
    #     btns.rejected.connect(dlg.reject)
    #     if not dlg.exec(): return None
    #     sel = lst.currentRow()
    #     if sel <= 0: return 0
    #     if sel >= lst.count()-1: return len(rows)
    #     return sel  # "After: X" -> insert after index sel-1, equals sel

    # def import_chapters_paths(self, paths: list[str], insert_index: int | None = None):
    #     # Append to end or insert at given index in current book
    #     cur = self.db.conn.cursor()
    #     pid = self._current_project_id
    #     bid = self._current_book_id
    #     # current end
    #     cur.execute("SELECT COALESCE(MAX(position), -1) FROM chapters WHERE project_id=? AND book_id=?", (pid, bid))
    #     end_pos = (cur.fetchone()[0] or -1)
    #     base_index = end_pos + 1

    #     inserted_ids = []
    #     for p in paths:
    #         pth = Path(p)
    #         title = pth.stem
    #         content = self._convert_file_to_markdown(pth)
    #         # temporary pos at end; we’ll reindex if insert_index provided
    #         cur.execute("""INSERT INTO chapters (project_id, book_id, title, content, position)
    #                        VALUES (?, ?, ?, ?, ?)""", (pid, bid, title, content, base_index))
    #         cid = cur.lastrowid
    #         inserted_ids.append(cid)
    #         base_index += 1
    #     self.db.conn.commit()

    #     # if inserting at a given index, move batch into place then re-number
    #     if insert_index is not None:
    #         # fetch list of chapter ids in order (excluding inserted first)
    #         cur.execute("""SELECT id FROM chapters
    #                        WHERE project_id=? AND book_id=?
    #                        ORDER BY position, id""", (pid, bid))
    #         ordered = [r[0] for r in cur.fetchall()]
    #         # remove the inserted ids from their appended positions
    #         for cid in inserted_ids:
    #             if cid in ordered:
    #                 ordered.remove(cid)
    #         # insert them starting at insert_index
    #         for i, cid in enumerate(inserted_ids):
    #             ordered.insert(insert_index + i, cid)
    #         # write compact positions
    #         for idx, cid in enumerate(ordered):
    #             cur.execute("UPDATE chapters SET position=? WHERE id=?", (idx, cid))
    #         self.db.conn.commit()

    #     # compute initial chapter references
    #     for cid in inserted_ids:
    #         cur.execute("SELECT content FROM chapters WHERE id=?", (cid,))
    #         txt = (cur.fetchone() or {"content": ""})["content"] or ""
    #         self.recompute_chapter_references(cid, txt)
    #     self.db.conn.commit()

    #     self.populate_chapters_tree()
    #     # select the last inserted item
    #     if inserted_ids:
    #         last = inserted_ids[-1]
    #         self.load_chapter(last)

    # def _convert_file_to_markdown(self, pth: Path) -> str:
    #     ext = pth.suffix.lower()
    #     try:
    #         if ext == ".md":
    #             return pth.read_text(encoding="utf-8", errors="replace")
    #         elif ext in (".txt",):
    #             return pth.read_text(encoding="utf-8", errors="replace")
    #         elif ext == ".docx":
    #             # minimal docx support if python-docx installed; else raw notice
    #             return docx_to_markdown(str(pth))
    #         else:
    #             return f"*Unsupported file type: {ext}*"
    #     except Exception as e:
    #         return f"*Error reading file:* {e}"
        
    # ---------- Word integration ----------
    def action_edit_in_word(self):
        """Export current chapter to DOCX, open in Word, and live-sync Markdown on save."""
        if not self._current_chapter_id:
            return
        # If already syncing another chapter, stop it
        self.action_stop_word_sync()

        rt = DocxRoundTrip(self, self._current_chapter_id)
        rt.synced.connect(self._apply_word_sync)
        try:
            rt.start()
            self._active_roundtrip = rt
            self._word_lock_chapter_id = self._current_chapter_id  # lock editing in-app for this chapter
            self._center_set_mode(view_mode=True)
        except Exception as e:
            print("Edit in Word failed:", e)
        finally:
            self._update_word_sync_ui()

    def _finish_stop_word_sync(self):
        self._active_roundtrip = None
        self._word_lock_chapter_id = None
        self._update_word_sync_ui()

    def action_stop_word_sync(self):
        """Stop watching the DOCX and release the lock."""
        rt = getattr(self, "_active_roundtrip", None)
        if rt:
            # If we have the COM doc, auto-save → sync → stop (no prompts)
            if rt.has_com() if hasattr(rt, 'has_com') else (rt._word_doc is not None):
                rt.autosave_sync_stop()
                return  # finish callback will be invoked from DocxRoundTrip
            # Fallback (no COM): just stop watcher now
            try:
                rt.stop()
            except Exception:
                pass
            self._active_roundtrip = None
            self._word_lock_chapter_id = None
            self._update_word_sync_ui()
        self._center_set_mode(view_mode=True)  # keep View by default, but editor is now writable again

    @Slot(str)
    def _apply_word_sync(self, md: str):
        """Apply Markdown coming from Word sync to the locked chapter; keep View mode."""
        chap_id = getattr(self, "_current_chapter_id", None)
        locked  = getattr(self, "_word_lock_chapter_id", None)
        if locked is None:
            return
        # Update DB for the locked chapter id, not necessarily the current one
        target_id = locked
        cur = self.db.conn.cursor()
        cur.execute("UPDATE chapters SET content=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (md, target_id))
        self.db.conn.commit()

        # If user is viewing that same chapter, update UI preview; keep View mode
        if chap_id == target_id:
            self.centerEdit.blockSignals(True)
            self.centerEdit.setPlainText(md or "")
            self.centerEdit.blockSignals(False)
            self._render_center_preview()
            self._center_set_mode(view_mode=True)

        # Update references & refs tree if the locked chapter is the current one
        if chap_id == target_id:
            self.recompute_chapter_references(target_id, md)
            self.populate_refs_tree(target_id)


    # ---------- Mini outline ----------
    def _open_full_outline_window(self):
        if not hasattr(self, "_outlineWin") or self._outlineWin is None:
            self._outlineWin = OutlineWindow(None)
            self._outlineWin.setAttribute(Qt.WA_QuitOnClose, False)
            # Adopt the *same* OutlineWorkspace instance you already use in main:
            self._outlineWin.adopt_workspace(self.outlineWorkspace)

        self._outlineWin.show()
        # focus current chapter and expand prev/current/next
        self._outlineWin.focus_chapter_id(self._current_chapter_id)
        if not self._outlineWin._first_open_done:
            self._outlineWin.workspace.expand_prev_current_next(chap_id=self._current_chapter_id)
            self._outlineWin._first_open_done = True

    # ---------- Search ----------
    def run_search(self):
        q = self.searchEdit.text().strip()
        self.tabSearch.clear()
        if not q:
            return
        # search chapters
        cur = self.db.conn.cursor()
        results_root = QTreeWidgetItem([f"Results for: {q}", ""])
        self.tabSearch.addTopLevelItem(results_root)

        try:
            cur.execute("SELECT id, title FROM chapters_fts WHERE chapters_fts MATCH ? LIMIT 50", (q,))
            chs = cur.fetchall()
            if chs:
                parent = QTreeWidgetItem(["Chapters", ""])
                results_root.addChild(parent)
                for r in chs:
                    cid, title = r["id"], r["title"]
                    it = QTreeWidgetItem([title, "Chapter"])
                    it.setData(0, Qt.UserRole, ("chapter", cid))
                    parent.addChild(it)
        except sqlite3.Error as e:
            parent = QTreeWidgetItem(["Chapters (FTS unavailable)", ""])
            results_root.addChild(parent)

        try:
            cur.execute("SELECT id, title FROM world_items_fts WHERE world_items_fts MATCH ? LIMIT 50", (q,))
            wis = cur.fetchall()
            if wis:
                parent = QTreeWidgetItem(["Worldbuilding", ""])
                results_root.addChild(parent)
                for r in wis:
                    wid, title = r["id"], r["title"]
                    it = QTreeWidgetItem([title, "World"])
                    it.setData(0, Qt.UserRole, ("world_item", wid))
                    parent.addChild(it)
        except sqlite3.Error as e:
            parent = QTreeWidgetItem(["Worldbuilding (FTS unavailable)", ""])
            results_root.addChild(parent)

        self.tabSearch.expandAll()
        self.bottomTabs.setCurrentWidget(self.tabSearch)

    def open_search_result(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data: return
        kind, oid = data
        if kind == "chapter":
            # focus in chapters
            self.focus_chapter_in_tree(oid)
            self.load_chapter(oid)
        elif kind == "world_item":
            self.worldDetail.show_item(oid)

    def focus_chapter_in_tree(self, chap_id: int):
        # expand all books and select
        self.chaptersTree.expandAll()
        for i in range(self.chaptersTree.topLevelItemCount()):
            b = self.chaptersTree.topLevelItem(i)
            for j in range(b.childCount()):
                c = b.child(j)
                d = c.data(0, Qt.UserRole)
                if d and d[0] == "chapter" and d[1] == chap_id:
                    self.chaptersTree.setCurrentItem(c)
                    self.chaptersTree.scrollToItem(c)
                    return

    def focus_world_item_in_tree(self, world_item_id: int) -> bool:
        """Expand parents, select and scroll to the world item; preserve other expand/collapse state."""
        def walk(item, chain=None):
            if chain is None:
                chain = []
            if not item:
                return None
            d = item.data(0, Qt.UserRole)
            if d and d == ("world_item", world_item_id):
                # expand only the path to this item
                for p in chain:
                    p.setExpanded(True)
                self.worldTree.setCurrentItem(item)
                self.worldTree.scrollToItem(item)
                return item
            for i in range(item.childCount()):
                got = walk(item.child(i), chain + [item])
                if got:
                    return got
            return None

        for i in range(self.worldTree.topLevelItemCount()):
            if walk(self.worldTree.topLevelItem(i)):
                return True
        return False

    def _default_import_dir(self) -> str:
        cur = self.db.conn.cursor()
        cur.execute("SELECT import_dir FROM projects WHERE id=?", (self._current_project_id,))
        row = cur.fetchone()
        return (row[0] if row and not isinstance(row, sqlite3.Row) else (row["import_dir"] if row else "")) or ""

    def _default_export_dir(self) -> str:
        cur = self.db.conn.cursor()
        cur.execute("SELECT export_dir FROM projects WHERE id=?", (self._current_project_id,))
        row = cur.fetchone()
        return (row[0] if row and not isinstance(row, sqlite3.Row) else (row["export_dir"] if row else "")) or ""


    def duplicate_project(self, src_project_id: int, new_name: str):
        cur = self.db.conn.cursor()
        # create new project
        cur.execute("INSERT INTO projects(name) VALUES (?)", (new_name,))
        new_pid = cur.lastrowid

        # duplicate books
        id_map_book = {}
        cur.execute("SELECT id, name, position FROM books WHERE project_id=?", (src_project_id,))
        for bid, name, pos in cur.fetchall():
            cur.execute("INSERT INTO books(project_id, name, position) VALUES (?,?,?)", (new_pid, name, pos))
            id_map_book[bid] = cur.lastrowid

        # duplicate chapters
        id_map_ch = {}
        cur.execute("SELECT id, book_id, title, content, position FROM chapters WHERE project_id=? AND COALESCE(deleted,0)=0",
                    (src_project_id,))
        for cid, bid, title, content, pos in cur.fetchall():
            nbid = id_map_book.get(bid)
            cur.execute("""INSERT INTO chapters(project_id, book_id, title, content, position)
                        VALUES (?,?,?,?,?)""", (new_pid, nbid, title, content, pos))
            id_map_ch[cid] = cur.lastrowid

        # duplicate world categories
        id_map_wc = {}
        cur.execute("""SELECT id, parent_id, name, position FROM world_categories 
                    WHERE project_id=? AND COALESCE(deleted,0)=0""", (src_project_id,))
        rows = cur.fetchall()
        # two-pass to preserve parent mapping
        for _ in range(2):
            for cid, parent_id, name, pos in rows:
                if cid in id_map_wc: continue
                npid = id_map_wc.get(parent_id) if parent_id else None
                if parent_id and npid is None: 
                    continue
                cur.execute("""INSERT INTO world_categories(project_id, parent_id, name, position)
                            VALUES (?,?,?,?)""", (new_pid, npid, name, pos))
                id_map_wc[cid] = cur.lastrowid

        # duplicate world items
        id_map_wi = {}
        cur.execute("""SELECT id, category_id, title, content_md, content_render 
                    FROM world_items WHERE project_id=? AND COALESCE(deleted,0)=0""", (src_project_id,))
        for wid, cat_id, title, md, html in cur.fetchall():
            ncat = id_map_wc.get(cat_id)
            cur.execute("""INSERT INTO world_items(project_id, category_id, title, content_md, content_render)
                        VALUES (?,?,?,?,?)""", (new_pid, ncat, title, md, html))
            id_map_wi[wid] = cur.lastrowid

        # duplicate aliases
        cur.execute("""SELECT world_item_id, alias FROM world_aliases 
                    WHERE world_item_id IN ({})""".format(",".join("?"*len(id_map_wi))) if id_map_wi else "SELECT 0 WHERE 0",
                    tuple(id_map_wi.keys()))
        for wid, alias in cur.fetchall():
            cur.execute("INSERT INTO world_aliases(world_item_id, alias) VALUES (?,?)",
                        (id_map_wi[wid], alias))

        # duplicate links
        if id_map_wi:
            cur.execute("""SELECT source_id, target_id, relationship FROM world_links
                        WHERE source_id IN ({}) AND target_id IN ({})"""
                        .format(",".join("?"*len(id_map_wi)), ",".join("?"*len(id_map_wi))),
                        tuple(id_map_wi.keys()) + tuple(id_map_wi.keys()))
            for s, t, rel in cur.fetchall():
                ns, nt = id_map_wi.get(s), id_map_wi.get(t)
                if ns and nt:
                    cur.execute("INSERT INTO world_links(source_id, target_id, relationship) VALUES (?,?,?)",
                                (ns, nt, rel))

        self.db.conn.commit()

    def switch_project(self, project_id: int):
        # save-all before switching
        self.save_all_dirty()
        self._current_project_id = project_id
        # pick a book if present
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM books WHERE project_id=? ORDER BY position, id LIMIT 1", (project_id,))
        row = cur.fetchone()
        self._current_book_id = (row[0] if row else None)
        self.refresh_project_header()
        # rebuild UI lists
        self.populate_chapters_tree()
        self.populate_world_tree()
        self.populate_refs_tree(None)
        # clear center/right panes
        self._current_chapter_id = None
        self.titleEdit.setText("")
        self.centerEdit.setPlainText("")
        self.centerView.setHtml("")
        if hasattr(self.worldDetail, "clear"):
            self.worldDetail.clear()


    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            # If minimized, persist everything dirty
            if self.windowState() & Qt.WindowMinimized:
                try:
                    self.save_all_dirty()
                except Exception as ex:
                    print("Save on minimize failed:", ex)
        super().changeEvent(e)

    def save_all_dirty(self):
        """
        Persist any pending edits across the app:
        - current chapter (title/content + todos/notes)
        - current world item (if in edit mode)
        - (extend here later for dashboard or other tabs)
        """
        # Save chapter-related dirty state
        self.save_current_if_dirty()

        # Save world item if its widget reports being dirty
        if hasattr(self, "worldDetail") and hasattr(self.worldDetail, "_save_current_if_dirty"):
            self.worldDetail._save_current_if_dirty()

    def closeEvent(self, e):
        try:
            # Save anything pending
            self.save_all_dirty()
            # Stop Word sync if active
            self.action_stop_word_sync()
        finally:
            super().closeEvent(e)
