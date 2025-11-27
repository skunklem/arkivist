import re, sqlite3, json, html as html_mod
from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QSize, Slot, QEvent, QRectF, QDateTime, QSignalBlocker,
    Signal, QObject, QSettings, QPoint, QUrl, QUrlQuery
)
from PySide6.QtGui import (
    QAction, QKeySequence, QIcon, QPainter, QPixmap, QPen, QIcon,
    QShortcut, QDesktopServices, QCursor, QPalette, QTextCursor
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QTextBrowser, QMessageBox,
    QLineEdit, QLabel, QPushButton, QTabWidget, QFileDialog, QToolBar,
    QDialog, QSizePolicy, QInputDialog, QMenu, QToolButton, QToolTip,
    QWidgetAction, QAbstractItemDelegate, QStyle, QApplication, QComboBox
)
from ui.widgets.outline import OutlineWorkspace
from ui.widgets.outline import MiniOutlineTab
from ui.widgets.outline.window import OutlineWindow
from ui.widgets.extract import compute_metrics, drop_overlapped_shorter, find_known_spans, heuristic_candidates_spacy, spacy_candidates_strict, spacy_doc
from ui.widgets.extract_pane import ExtractPane
from ui.widgets.theme_manager import theme_manager
from ui.widgets.ui_zoom import UiZoom
from ui.widgets.dialogs import BulkChapterImportDialog, ProjectManagerDialog, WorldImportDialog
from ui.widgets.world_detail import WorldDetailWidget
from ui.widgets.world_tree import WorldTree
from ui.widgets.characters_page import CharactersPage
from ui.widgets.character_editor import CharacterEditorDialog
from ui.widgets.character_dialog import CharacterDialog
from ui.widgets.chapter_todos import ChapterTodosWidget
from ui.widgets.chapters_tree import ChaptersTree
from ui.widgets.helpers import DropPane, PlainNoTab, chapter_display_label, normalize_possessive, parse_internal_url, scrub_markdown_for_ner
from ui.widgets.common import StatusLine, _WikiHoverFilter, AliasPicker
from database.db import Database
from utils.icons import make_lock_icon
from utils.word_integration import DocxRoundTrip
from utils.md import docx_to_markdown, md_to_html, read_file_as_markdown
from utils.files import parse_chapter_filename

class StoryArkivist(QMainWindow):
    chaptersOrderChanged = Signal(int, int, 'QVariantList')
    outlineVersionChanged = Signal(int, str)  # (cid, vname)

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
        self._squelch_anchor_until_ms = 0

        self.db = Database(db_path)
        self.charactersPage = CharactersPage(self, self.db)
        self.outlineWorkspace = OutlineWorkspace()
        print("APP sees controller", id(self.outlineWorkspace.page.undoController))
        self._install_outline_undo_actions()
        QApplication.instance().installEventFilter(self)
        # self.outlineWorkspace.install_global_shortcuts(self)  # pass main window as host

        self._build_ui()

        # Pick or create a project immediately
        # self._ensure_project_exists_or_prompt()
        self._startup_pick_project()

        self._wire_actions()
        # self.setWindowTitle("StoryArkivist")
        self.setWindowTitle(f"StoryArkivist — {self.db.project_name(self._current_project_id)} [*]")

        # current state
        self._current_chapter_id = None
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
        self._select_startup_world_item()

        # If it’s the first-ever run, show the PM after the window is visible
        if getattr(self, "_show_pm_overlay_on_first_show", False):
            QTimer.singleShot(0, self._open_project_manager_overlay)

    def _startup_pick_project(self):
        """Pick MRU project or create 'Untitled Project' on first-ever run."""
        settings = QSettings("Arkivist", "StoryArkivist")
        mru = settings.value("mru_project_id", type=int)

        count = self.db.project_quantity()
        if count == 0:
            # First-ever run: create a project immediately so UI can render
            self._current_project_id = self.db.project_create("Untitled Project")
            settings.setValue("mru_project_id", int(self._current_project_id))
            self._current_book_id = self.db.book_create(self._current_project_id)
            print("Created book id:", self._current_book_id)
            # mark that we should show PM as overlay after show()
            self._show_pm_overlay_on_first_show = True
        else:
            # Prefer MRU if still active; else first active
            if self.db.project_deleted(mru) == False:
                # Validate the MRU still exists and isn't soft-deleted
                self._current_project_id = mru
            else:
                self._current_project_id = self.db.project_first_active()

            self._current_project_id = self._current_project_id or self.db.project_first_active()
            # TODO: save most recent book and chapter ids too
            self._current_book_id = self.db.book_list(self._current_project_id)[0]["id"]
            settings.setValue("mru_project_id", int(self._current_project_id))

        # Ensure world roots exist now that we have a project id
        self.ensure_world_roots()

    def _open_project_manager_overlay(self):
        """Open Project Manager non-modally, hovering above the main window for first-run onboarding."""
        dlg = ProjectManagerDialog(self, self)

        # Make it hover without blocking
        dlg.setModal(False)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._projectManager = dlg  # keep a ref if needed

    def _select_startup_world_item(self):
        pid = getattr(self, "_current_project_id", None)
        if not pid: 
            return
        wid = self.db.ui_pref_get(pid, "world:last_item_id")
        if wid:
            try:
                self.load_world_item(int(wid))
                return
            except Exception:
                pass

    def _install_outline_undo_actions(self):
        if getattr(self, "_outline_actions_installed", False):
            return
        ctrl = self.outlineWorkspace.page.undoController

        self._actUndo = QAction("Undo Outline", self)
        self._actUndo.setShortcuts([QKeySequence.Undo])  # Ctrl+Z
        self._actUndo.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self._actUndo.triggered.connect(ctrl.undo)
        self.addAction(self._actUndo)

        self._actRedo = QAction("Redo Outline", self)
        self._actRedo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")])
        self._actRedo.setShortcutContext(Qt.WidgetWithChildrenShortcut)
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
        c1 = self.db.chapter_insert(pid, bid, base+0, "Prologue", "The sun rose over the Temple of Dawn. John watched it from the Temple of Dusk. The Temple of Dawn was beautiful.")
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
        self.rebuild_search_indexes()

    def populate_outline_workspace(self):
        # refresh outline workspace
        self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)
        self.tabMiniOutline.set_workspace(self.outlineWorkspace)
        self.tabMiniOutline.set_chapter(self._current_chapter_id)

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
        # if not pid:
        #     self.projectBtn.setText("(No project)")
        #     return
        name = self.db.project_name(pid) or "(Unknown)"
        # self.projectBtn.setText(name)
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

    def _selected_text_or_word(self) -> str:
        cur = self.centerEdit.textCursor()
        text = cur.selectedText()
        if not text:
            cur.select(cur.WordUnderCursor)
            text = cur.selectedText()
        # QTextEdit uses U+2029 etc. Replace with spaces.
        return (text or "").replace("\u2029"," ").replace("\u2028"," ").strip()

    def _ctx_add_world_item_create(self, text: str):
        title = (text or "").strip()
        if not title:
            return
        # Ask for kind if unknown
        kind = self._prompt_kind_for_new_item()
        if not kind:
            return
        wid = self.find_or_create_world_item(title, kind)
        # Optional: open detail
        if wid and hasattr(self, "worldDetail"):
            self.worldDetail.open_world_item(wid)

    def _ctx_add_world_item_alias(self, text: str):
        alias = (text or "").strip()
        if not alias:
            return
        # pick target item (prefer characters, but let user choose)
        wid = None
        if hasattr(self.tabExtract, "_prompt_pick_world_item"):
            wid = self.tabExtract._prompt_pick_world_item(prefill=alias)  # can pass restrict_kind="character" if you like
        if not wid:
            return
        # alias type
        if hasattr(self.tabExtract, "_prompt_alias_type"):
            alias_type = self.tabExtract._prompt_alias_type()
        else:
            alias_type = "alias"
        if not alias_type:
            return
        self.db.alias_add(wid, alias, alias_type)

    def _ctx_link_dialog(self, text: str):
        """
        Open a generic link dialog: link selection to owner/setting/etc.
        If you already have a link UI, call it here.
        """
        if hasattr(self, "open_link_dialog"):
            self.open_link_dialog(initial_text=text)

    def _ctx_search_in_chapter(self, text: str):
        if not text:
            return
        # Reuse your editor's find panel if you have one
        if hasattr(self, "open_find_panel_in_editor"):
            self.open_find_panel_in_editor(self.centerEdit, text)
        else:
            self.centerEdit.find(text)

    def _ctx_search_all_chapters(self, text: str):
        if not text:
            return
        if hasattr(self, "open_global_search"):
            self.open_global_search(scope="chapters", query=text)

    def _ctx_search_world(self, text: str):
        if not text:
            return
        if hasattr(self, "open_global_search"):
            self.open_global_search(scope="world", query=text)

    def _ctx_search_notes(self, text: str):
        if not text:
            return
        if hasattr(self, "open_global_search"):
            self.open_global_search(scope="notes", query=text)

    def _on_center_context_menu(self, pos: QPoint):
        sel = self._selected_text_or_word()
        menu = self.centerEdit.createStandardContextMenu()
        menu.addSeparator()

        actCreate = menu.addAction(f"Add “{sel or '…'}” as new world item…")
        actAlias  = menu.addAction(f"Add “{sel or '…'}” as alias to existing…")
        actLink   = menu.addAction("Link…")  # owner/setting/etc.
        menu.addSeparator()
        actFindHere  = menu.addAction(f"Search here for “{sel or '…'}”")
        actFindAll   = menu.addAction(f"Search all chapters for “{sel or '…'}”")
        actFindWorld = menu.addAction(f"Search world items for “{sel or '…'}”")
        actFindNotes = menu.addAction(f"Search notes for “{sel or '…'}”")

        chosen = menu.exec_(self.centerEdit.mapToGlobal(pos))
        if not chosen:
            return

        if chosen == actCreate:
            self._ctx_add_world_item_create(sel)
        elif chosen == actAlias:
            self._ctx_add_world_item_alias(sel)
        elif chosen == actLink:
            self._ctx_link_dialog(sel)
        elif chosen == actFindHere:
            self._ctx_search_in_chapter(sel)
        elif chosen == actFindAll:
            self._ctx_search_all_chapters(sel)
        elif chosen == actFindWorld:
            self._ctx_search_world(sel)
        elif chosen == actFindNotes:
            self._ctx_search_notes(sel)

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

    def _theme_link_colors(self):
        pal = QApplication.palette(self)
        link = pal.color(QPalette.Link)        # theme link color
        hi   = pal.color(QPalette.Highlight)   # good accent in both themes
        txt  = pal.color(QPalette.Text)        # fallback
        # Ensure contrast by nudging toward text color if too close to base
        def hex_(q): return q.name()
        return {
            "known": hex_(link),         # known world items
            "cand_qp": hex_(txt),        # quick-parse candidates (visible in both themes)
            "cand_ai": hex_(hi),         # AI candidates
        }

    def _doc_css(self) -> str:
        c = self._theme_link_colors()
        return f"""
    a.wlKnown {{ color:{c['known']};    text-decoration: underline; }}
    a.wlCandQP{{ color:{c['cand_qp']}; text-decoration: underline; }}
    a.wlCandAI{{ color:{c['cand_ai']}; text-decoration: underline; }}
    """.strip()

    def _apply_doc_styles(self, tb: QTextBrowser):
        tb.document().setDefaultStyleSheet(self._doc_css())

    def _render_center_preview(self, version_id: int | None = None):
        """Render current chapter markdown to HTML with wikilinks in the preview."""
        chap_id = self._current_chapter_id
        if chap_id is None:
            self.centerView.setHtml("<i>No chapter selected</i>")
            return

        # 1) Resolve which version we're viewing
        ver_id = int(version_id) if version_id is not None else (self.db.get_active_version_id(chap_id) or 0)

        # 2) Source text for that version
        md = self.db.chapter_content(chap_id, version_id=ver_id) or ""

        # 3) Linkify in Markdown space, then render
        html_content = self._render_html_from_md(md, known_only=False, scope=("chapter", chap_id, ver_id))

        # 4) Persist to chapter_versions.content_render (cache)
        print("Caching rendered HTML for chapter", chap_id, "version", ver_id)
        print(html_content[:100])
        self.db.chapter_version_render_update(ver_id, html_content)

        # 5) Show it
        self.centerView.document().setDefaultStyleSheet(self._doc_css())
        self.centerView.setHtml(html_content)

    # --- Wikilink helpers -------------------------------------------------
    def route_anchor_click(self, qurl):
        """Central handler for all app-specific anchors."""
        now = QDateTime.currentMSecsSinceEpoch()
        last = getattr(self, "_last_anchor_route_ms", 0)
        if now - last < 300:                 # debounce
            return
        self._last_anchor_route_ms = now
        
        info = parse_internal_url(qurl)
        if not info:
            try: QDesktopServices.openUrl(qurl)
            except Exception: pass
            return

        if info["kind"]=="world":
            edit = QUrlQuery(qurl).queryItemValue("edit")
            print("load_world_item from route_anchor_click", info["id"], "edit:", edit)
            self.load_world_item(info["id"], edit_mode=(edit=="1"))
            return

        if info["kind"] == "suggest":
            self.show_extract_tab_and_focus(info["id"])
            # self._open_candidate_editor(info["id"], edit_mode=True)
            return

    def _on_anchor_hovered(self, href: str):
        """Connected to QTextBrowser.anchorHovered; href=='' means leaving."""
        w = self.sender()
        if not href:
            QToolTip.hideText()
            return
        qurl = QUrl(href)
        info = parse_internal_url(qurl)
        if not info:
            print("No hover card info for", href)
            QToolTip.hideText()
            return
        html_content = self._hover_card_html_for(qurl)
        print(f"html_content for hover card:\n{html_content}")
        if not html_content:
            print("No hover card content for", href)
            QToolTip.hideText()
            return
        QToolTip.showText(QCursor.pos(), html_content, w)

    def _on_popup_anchor_clicked(self, qurl):
        # prevent underlying view from reacting to the same physical click
        try:
            self.parent()._squelch_anchor_until_ms = QDateTime.currentMSecsSinceEpoch() + 400
        except Exception:
            pass
        self.hide()
        self.parent().route_anchor_click(qurl)

    def _attach_link_handlers(self, tb: QTextBrowser):
        if not tb:
            return
        tb.setOpenExternalLinks(False)
        tb.setOpenLinks(False)
        # Ensure any existing hover card hides when a link is followed from the main view
        tb.anchorClicked.connect(lambda qurl: getattr(self, "_wl_hover", None) and self._wl_hover.card.hide())
        def _route_if_not_squelched(qurl):
            now = QDateTime.currentMSecsSinceEpoch()
            if now < getattr(self, "_squelch_anchor_until_ms", 0):
                return
            self.route_anchor_click(qurl)
        tb.anchorClicked.connect(_route_if_not_squelched)
        vp = tb.viewport()
        vp.setMouseTracking(True)
        if not getattr(vp, "_wl_hover_installed", False):
            vf = _WikiHoverFilter(self, tb)
            vp.installEventFilter(vf)
            vp._wl_hover_installed = True
            # Keep a ref so the lambda above can find it
            self._wl_hover = vf

    def show_extract_tab(self):
        idx = self.bottomTabs.indexOf(self.tabExtract)
        if idx != -1:
            self.bottomTabs.setCurrentIndex(idx)

    def show_extract_tab_and_focus(self, cand_id: int):
        # Ensure Extract tab is visible/active, then delegate:
        self.show_extract_tab()
        self.tabExtract.focus_candidate(cand_id)

    def _build_link_index(self) -> dict[str, int]:
        """lowercased phrase -> world_item_id across titles+aliases (active only)."""
        pid = getattr(self, "_current_project_id", None)
        if not pid:
            return {}
        # # returns [(wid, phrase_lower), ...]
        # pairs = self.db.world_phrases_for_project(pid)  # titles + aliases
        # idx = {}
        # for wid, phrase in pairs:
        #     p = (phrase or "").strip().lower()
        #     if p:
        #         idx[p] = int(wid)
        idx = {phrase_norm: wid for (phrase_norm, wid, alias_id) in self.db.world_phrases_for_project_detailed(pid)}
        return idx

    def _compile_phrase_regex(self, phrases: list[str]):
        """Build one big alternation regex of phrases, longest-first, case-insensitive."""
        if not phrases:
            return None
        # Sort longest-first to prefer longest multi-word matches
        phrases_sorted = sorted({p for p in phrases if p}, key=len, reverse=True)
        # Escape for regex; use word boundary semantics (no letter/number on either side)
        escaped = [re.escape(p) for p in phrases_sorted]
        pat = r"(?i)(?<!\w)(" + "|".join(escaped) + r")(?!\w)"
        return re.compile(pat)

    def _linkify_candidates(self, md: str, chapter_id: int) -> str:
        cur = self.db.conn.cursor()
        cands = cur.execute("""
            SELECT id, candidate, status
            FROM ingest_candidates
            WHERE chapter_id=? AND COALESCE(status,'pending') IN ('pending','linked')
        """, (chapter_id,)).fetchall()
        if not cands: return md

        # Longest-first phrase match
        phrases = sorted({(cid, (lbl or "").strip()) for cid,lbl,_ in cands if lbl}, key=lambda x: len(x[1]), reverse=True)

        parts = []
        for cid, phrase in phrases:
            parts.append(re.escape(phrase))
        rx = re.compile(r"(?i)(?<!\w)(" + "|".join(parts) + r")(?!\w)")

        def sub(m):
            text = m.group(0)
            # you can also encode the source type in the URL if you track it
            return f'<a href="suggest://quick/{cid}" class="wl wl-suggest">{text}</a>'

        return rx.sub(sub, md)

    # def _linkify_md(self, md: str, *, exclude_world_id: int | None = None, link_candidates: bool = True) -> str:
    #     """Insert <a href='world://item/<id>' ...> into Markdown prior to md_to_html()."""
    #     idx = self._build_link_index()
    #     if not idx or not md:
    #         return md
    #     if exclude_world_id is not None:
    #         idx = {k: v for k, v in idx.items() if v != exclude_world_id}
    #     rx = self._compile_phrase_regex(list(idx.keys()))
    #     if not rx:
    #         return md

    #     def _sub(m: re.Match):
    #         candidate = m.group(0)
    #         wid = idx.get(candidate.lower())
    #         if not wid:
    #             return candidate
    #         return f'<a href="world://item/{wid}" data-wid="{wid}" class="wl wl-known">{candidate}</a>'

    #     md_linked = rx.sub(_sub, md)
    #     if link_candidates:
    #         md_linked = self._linkify_candidates(md_linked, self._current_chapter_id)
    #     return md_linked

    def _linkify_md(self, md: str, *,
                    known_only: bool = True,
                    scope: tuple[str, int, int | None] | None = None,  # ('chapter'|'world_item'|..., id, version_id?)
                    exclude_world_id: int | None = None) -> str:
        """
        known_only=True: only titles/active aliases.
        known_only=False: include candidates for the given scope (must pass scope).
        scope expands beyond chapters now: ('chapter', chap_id, ver_id), ('world_item', wid, None), etc.
        """
        if known_only:
            return self._linkify_md_known_only(md, exclude_world_id=exclude_world_id)
        print(f"Linkifying MD with candidates for scope {scope}")
        return self._linkify_md_including_candidates(md, scope=scope, exclude_world_id=exclude_world_id)

    def _linkify_md_including_candidates(self, md: str, *,
                                        scope: tuple[str, int, int | None] | None,
                                        exclude_world_id: int | None = None,
                                        prefilter_with_refs: bool = True) -> str:
        """
        scope = (scope_type, scope_id, version_id_or_None)
        Pull candidates only for that scope (fast) and never override a known match.
        """
        if not md:
            print("No MD to linkify")
            return md
        pid = self._current_project_id

        # scope → (doc_type, doc_id, version_id)
        doc_type, doc_id, version_id = (scope or (None, None, None))

        # 1) Optional prefilter: which WIDs are actually present in this text?
        ids_subset = None
        if prefilter_with_refs and doc_type and doc_id:
            ids_subset = list(self.seek_references(md))  # in-memory WID set for this text
            print(f"Prefiltering known phrases to WIDs present in text: {ids_subset}")

        # 2) known map (phrase_norm -> (wid, alias_id)) (possibly limited)
        known_pairs = self.db.world_phrases_for_project_detailed(pid, ids_subset)
        known = {}
        for phrase, wid, alias_id in known_pairs:
            if exclude_world_id is not None and int(wid) == int(exclude_world_id):
                continue
            known[phrase] = (wid, alias_id)

        # 3) candidates for this exact scope+version (only wrap phrases not already ‘known’)
        cand = {}
        if scope:
            print(f"Fetching candidates for scope {scope}")
            rows = self.db.candidates_for_scope(
                project_id=pid, scope_type=doc_type, scope_id=doc_id,
                version_id=version_id, statuses=("pending",), columns="id, candidate, COALESCE(source,'') AS source"
                )
            print(f"[linkify] scope={scope} rows={[(r['id'], r['candidate'], r['source']) for r in rows]}")
            for cid, txt, src in rows:
                key = (txt or "").strip().lower()
                if key and key not in known:
                    cand[key] = (cid, "ai" if src == "ai" else "quick")

        # 4) compile union (longest-first)
        print(f"[linkify] scope={scope} prefilter={'yes' if prefilter_with_refs else 'no'}")
        print(f"[linkify] known={len(known)} keys, cand={len(cand)} keys")
        print(f"[Linkify] {set(cand.keys())}")
        print(f"[Linkify] Known phrases: {known}")
        keys = sorted(set(known.keys()) | set(cand.keys()), key=len, reverse=True)
        if not keys:
            return md

        rx = re.compile(r"(?i)(?<![\w'-])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w'-])")

        def sub(m):
            surf = m.group(0)
            key = surf.strip().lower()
            if key in known:
                wid, alias_id = known[key]
                href = f"world://item/{wid}"
                if alias_id is not None:
                    href += f"?alias={alias_id}"
                return f'<a class="wlKnown" href="{href}">{surf}</a>'
            if key in cand:
                cid, src = cand[key]
                cls = "wlCandAI" if src == "ai" else "wlCandQP"
                return f'<a class="{cls}" href="suggest://{src}/{cid}">{surf}</a>'
            return surf

        return rx.sub(sub, md)

    def _linkify_md_known_only(self, md: str, *, exclude_world_id: int | None = None,
                                prefilter_with_refs: bool = True, doc_type: str | None = None,
                                doc_id: int | None = None) -> str:
        if not md:
            return md
        pid = self._current_project_id

        # 1) Optional prefilter: which WIDs are actually present in this text?
        ids_subset = None
        if prefilter_with_refs and doc_type and doc_id:
             ids_subset = list(self.seek_references(md))  # fast in-memory; no persistence

        # 2) Build known phrases (optionally limited to subset of WIDs)
        known_pairs = self.db.world_phrases_for_project_detailed(pid, ids_subset)
        known = {}
        for phrase, wid, alias_id in known_pairs:
            if exclude_world_id is not None and int(wid) == int(exclude_world_id):
                continue
            known[phrase] = (wid, alias_id)

        if not known:
            return md

        keys = sorted(known.keys(), key=len, reverse=True)
        rx = re.compile(r"(?i)(?<![\w'-])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w'-])")

        def sub(m):
            surf = m.group(0)
            wid, alias_id = known[surf.strip().lower()]
            href = f"world://item/{wid}"
            if alias_id is not None:
                href += f"?alias={alias_id}"
            return f'<a class="wlKnown" href="{href}">{surf}</a>'

        return rx.sub(sub, md)

    def _open_candidate_editor(self, candidate_id: int, edit_mode: bool = False):
        row = self.db.ingest_candidate_row(candidate_id)
        if not row: 
            return
        surf = (row["candidate"] or "").strip()
        kind = (row["kind_guess"] or "").strip().lower()  # guesses: character/place/org/object/concept

        if not edit_mode:
            # View mode but world item doesn't exist yet
            # TODO: replace this with a dialog to choose Create/Alias/Reject
            # Simply focus in Extract tab for now
            cid = row["id"]
            self.tabExtract.focus_candidate(candidate_id)
            return

        # edit_mode True
        canonical = {"person":"character", "char":"character"}
        k = canonical.get(kind, kind or "misc")

        if k == "character":
            # Create/find, open CharacterDialog
            wid = row["target_world_item_id"] or self.ensure_world_item_from_candidate(row)
            if wid:
                print("Opening character editor from _open_candidate_editor")
                self.open_character_dialog(wid)
            return

        # Other kinds: open right panel in edit mode
        wid = row["target_world_item_id"] or self.ensure_world_item_from_candidate(row)
        if wid:
            # Optional: seed starter MD if brand-new and none exists
            meta = self.db.world_item_meta(wid)
            md   = (meta["content_md"] or "").strip()
            if not md:
                scaffold = f"# {surf}\n\n"
                self.db.world_item_update_content(wid, scaffold)
                self.rebuild_world_item_render(wid)  # keep render in sync
            print("load_world_item from _open_candidate_editor", wid, "edit:", True)
            self.load_world_item(wid, edit_mode=True)

    def resolve_candidate_chapter_and_version(self, cand_id: int) -> tuple[int | None, int | None]:
        cand_row = self.db.ingest_candidate_row(cand_id)
        if cand_row:
            chap_id = cand_row["scope_id"]
            ver_id = cand_row["version_id"]
            return chap_id, ver_id
        print(f"Warning: candidate id {cand_id} not found when resolving chapter/version.")
        return None, None

    def resolve_candidate_to_existing(self, cand_id: int, wid: int, also_alias: bool):
        cur = self.db.conn.cursor()
        # 1) mark candidate as linked
        cur.execute("UPDATE ingest_candidates SET status='linked', target_world_item_id=? WHERE id=?",
                    (wid, cand_id))
        # 2) optional alias
        if also_alias:
            # Pull candidate
            row = cur.execute("SELECT label FROM ingest_candidates WHERE id=?", (cand_id,)).fetchone()
            if row and row[0]:
                alias = row[0].strip()
                if alias:
                    cur.execute("INSERT INTO world_aliases(world_item_id, alias, status, deleted) VALUES(?, ?, 'active', 0)",
                                (wid, alias))
        self.db.conn.commit()
        # 3) refresh
        # self.recompute_chapter_references(self._current_chapter_id)
        chap_id, ver_id = self.resolve_candidate_chapter_and_version(cand_id)
        self.recompute_chapter_references(chapter_id=chap_id, chapter_version_id=ver_id, text=None)
        self._render_center_preview(ver_id)

    def accept_candidate_create(self, cand_id: int):
        """
        Convenience wrapper: accept as NEW world item (no alias-of picker).
        """
        return self.accept_candidate(cand_id, as_alias_of=None)

    def pick_alias_target(self) -> int | None:
        dlg = AliasPicker(self.db.conn, self._current_project_id, self)
        return dlg.sel_wid if dlg.exec() == dlg.Accepted else None

    def accept_candidate(self, cand_id: int, *, as_alias_of: int | None = None, add_alias: bool = True, alias_type: str = "alias") -> None:
        """
        Accept a candidate:
        - if as_alias_of is given: add candidate text as an active alias to that item and mark candidate 'linked'
        - else: create a new world item, add alias == candidate text, mark candidate 'accepted'
        Always recompute+rerender current chapter version afterwards.
        """
        row = self.db.ingest_candidate_row(cand_id)
        if not row:
            return
        (project_id, scope_type, scope_id, version_id,
        candidate_text, kind_guess, source, confidence, status, target_wid) = row

        title = (candidate_text or "").strip()
        if not title:
            return

        if as_alias_of:
            # Resolve to existing item
            if add_alias:
                self.db.alias_add(as_alias_of, title, alias_type=alias_type, status="active", is_primary=0)
            self.db.ingest_candidate_mark_resolved(cand_id, target_world_item_id=as_alias_of, status="linked")
        else:
            # Create a brand-new item titled by the candidate
            kind = (kind_guess or "item")
            wid = self.db.world_item_insert(project_id=project_id, item_type=kind, title=title, content_md="")
            # world_item_insert already adds + primary-sets the title alias; no extra alias call needed.
            self.db.ingest_candidate_mark_resolved(cand_id, target_world_item_id=wid, status="accepted")
            # seed starter MD so there's something to render
            scaffold = f"# {title}\n\nNo content yet.\n"
            self.db.world_item_update_content(wid, scaffold)
            self.rebuild_world_item_render(wid)  # keep render in sync

        # Recompute references in current chapter version if applicable
        if scope_type == "chapter":
            # Recompute/refresh the chapter we are *currently showing*
            chap_id = int(scope_id) if scope_id else getattr(self, "_current_chapter_id", None)
            ver_id = (version_id if version_id else getattr(self, "_view_version_id", None)) or (self.db.get_active_version_id(chap_id) if chap_id else None)
            if chap_id and ver_id:
                self.recompute_references(doc_type="chapter", doc_id=chap_id, version_id=ver_id, text=None)
                self._render_center_preview(ver_id)

    def link_candidate_to_existing(self, cand_id: int, wid: int):
        cur = self.db.conn.cursor()
        cur.execute("""
            UPDATE ingest_candidates
            SET status='linked', target_world_item_id=?
            WHERE id=?""", (wid, cand_id))
        self.db.conn.commit()
        self._render_center_preview()  # relink now that a known item exists

    def reject_candidate(self, cand_id: int, rerender: bool = True) -> None:
        self.db.ingest_candidate_mark_dismissed(cand_id)
        # Optionally re-render if you overlay candidates everywhere
        if rerender:
            self.rerender_center_and_extract()

    def rerender_center_and_extract(self):
        chap_id = getattr(self, "_current_chapter_id", None)
        if chap_id:
            ver_id = self._view_version_id or self.db.get_active_version_id(chap_id)
            if ver_id:
                self._render_center_preview(ver_id)
        if hasattr(self, "tabExtract"):
            self.tabExtract.refresh()

    def _hover_card_html_for(self, qurl: QUrl) -> str | None:
        info = parse_internal_url(qurl)
        if not info:
            return None
        cur = self.db.conn.cursor()

        if info["kind"] == "world":
            wid = info["id"]
            alias_id = info.get("alias_id")
            row = cur.execute("""
                SELECT type, title, content_render, content_md
                FROM world_items WHERE id=?""", (wid,)).fetchone()
            if not row:
                return None
            kind, title, render, md = row
            title = html_mod.escape(title or "")
            kind  = html_mod.escape(kind or "")
            text  = ""
            if render:
                print("HoverCard: using rendered HTML")
                render = render or ""
                render = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", "", render or "")  # drop style/script blocks fully
                text = re.sub(r"(?s)<[^>]+>", "", render).strip()
            elif md:
                print("HoverCard: using markdown snippet")
                text = (md or "")
            text = html_mod.escape(text.strip())[:240]
            print(f"HoverCard: {title} {kind}\n{text}")

            alias_note_html = ""
            if alias_id:
                arow = cur.execute("SELECT note FROM world_aliases WHERE id=?", (alias_id,)).fetchone()
                if arow and arow[0]:
                    alias_note_html = f'<div style="margin-top:4px; font-style:italic; opacity:.85;">{html_mod.escape(arow[0])}</div>'

            return f"""
            <div style="max-width:420px;">
            <div style="font-weight:600; margin-bottom:4px;">{title}</div>
            <div style="opacity:.7; font-size:90%; margin-bottom:4px;">{kind}</div>
            <div style="margin-bottom:6px;">{text}</div>
            {alias_note_html}
            <div style="margin-top:6px;"><a href="world://item/{wid}?edit=1">Edit</a></div>
            </div>"""

        if info["kind"] == "suggest":
            cid = info["id"]
            row = cur.execute("""
                SELECT candidate, kind_guess, confidence
                FROM ingest_candidates WHERE id=?""", (cid,)).fetchone()
            if not row:
                return None
            label, s_type, conf = row
            label = html_mod.escape(label or "")
            s_type = html_mod.escape(s_type or "item")
            conf_txt = f"{int((conf or 0)*100)}%"
            return f"""
            <div style="max-width:420px;">
            <div style="font-weight:600; margin-bottom:4px;">{label}</div>
            <div style="opacity:.7; font-size:90%; margin-bottom:4px;">suggested {s_type} · confidence {conf_txt}</div>
            <div style="margin-top:6px;"><a href="suggest://quick/{cid}">Open in Extract</a></div>
            </div>"""

        return None

    # --- Word Sync --------------------------------------------------------
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

    def load_world_item(self, world_item_id: int, edit_mode: bool = False, item_type: str = None):
        """Show a world item in the right panel and focus it in the tree."""
        if not item_type:
            item_type = self.db.world_item_type(world_item_id)
        if item_type == "character" and edit_mode:
            print("Opening character editor from load_world_item")
            self.open_character_dialog(world_item_id)
            edit_mode = False # always view mode for characters in world panel
        self.worldDetail.show_item(world_item_id, view_mode=not edit_mode)

        # place the caret at the end when opening edit mode for non-characters:
        if edit_mode and getattr(self.worldDetail, "editor", None):
            ed = self.worldDetail.editor
            ed.moveCursor(QTextCursor.End)
            ed.setFocus(Qt.OtherFocusReason)

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
        import traceback, time
        print(f"[char-dialog] open wid={char_id} t={time.time()} stack:")
        traceback.print_stack(limit=6)
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
        if title == new.strip(): return
        self.db.chapter_update(chap_id, title=new.strip())
        self.populate_chapters_tree()
        self.focus_chapter_in_tree(chap_id)
        self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)

    def _soft_delete_chapter(self, chap_id: int, origin: str = "tree"):
        if origin == "outline":
            # mark deleted (undoable via outline undo action)
            self.outlineWorkspace.page.undoController.record_delete_chapter(chap_id, self.db)
        else:
            # immediate delete (not recorded on outline stack)
            self.outlineWorkspace.page.undoController._apply_delete_step(
                chap_id, undo=False, db=self.db,
                payload={"project_id": self._current_project_id, "book_id": self._current_book_id, "position": None}
            )

        # 2) Refresh the tree UI
        self.populate_chapters_tree()
        # clear editor if we just hid the active chapter
        if getattr(self, "_current_chapter_id", None) == chap_id:
            self._current_chapter_id = None
            self._current_chapter_id = None
            self.titleEdit.setText("")
            self.centerEdit.setPlainText("")
            self.centerView.setHtml("")
            self.populate_refs_tree(None)

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
                self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)
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
            self.db.chapter_compact_positions(pid, bid)

            self.db.conn.commit()
            self.populate_chapters_tree()
            self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)
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
        """
        Sets up the menus for the main window.

        This method is called from the constructor and is responsible for
        setting up the menus, including the file menu, edit menu, view menu,
        and help menu. If the application is running in dev mode, the
        dev menu is also added.
        """
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
        projBtn.setText("Project Manager")
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

    def _sync_visible_pane_combo_for(self, chap_id: int, vname: str) -> None:
        p = self.outlineWorkspace.page.pane_for_cid(chap_id)
        if not p: 
            return
        if p.verCombo.currentText() != vname:
            with QSignalBlocker(p.verCombo):
                p.verCombo.setCurrentText(vname)

    def _on_workspace_version_changed(self, chap_id: int, vname: str):
        # persist “workspace’s current version” if you want (ui_prefs) — optional
        # self.set_current_outline_version_name_for(chap_id, vname)

        # keep the pane’s combo in sync with workspace choice
        self._sync_visible_pane_combo_for(chap_id, vname)

        # we do NOT touch the mini here — the mini follows the app header (centerView)

    def _refresh_outline_and_tree(self, book_id: int, focus_cid: int | None = None):
        pid = self._current_project_id
        self.populate_chapters_tree()          # tree refresh (no center focus changes)
        self.outlineWorkspace.load_from_db(self.db, pid, book_id)
        if focus_cid:
            self.outlineWorkspace.focus_chapter_id(focus_cid)

    def _on_outline_insert_requested(self, book_id: int, insert_at: int, title: str):
        pid = self._current_project_id
        new_cid = self.db.chapter_insert(pid, book_id, insert_at, title, content_md="")
        self.db.chapter_compact_positions(pid, book_id)
        self.db.conn.commit()
        self._refresh_outline_and_tree(book_id, focus_cid=new_cid)

    def _on_outline_rename_requested(self, chap_id: int, new_title: str):
        self.db.chapter_update(chap_id, title=new_title)
        meta = self.db.chapter_meta(chap_id)
        self.db.conn.commit()
        self._refresh_outline_and_tree(meta["book_id"], focus_cid=chap_id)

    def _on_outline_move_requested(self, chap_id: int, to_book_id: int, to_index: int):
        meta = self.db.chapter_meta(chap_id)
        pid, from_book = meta["project_id"], meta["book_id"]
        # Move to target index (your API):
        self.db.chapter_move_to_index(pid, to_book_id, chap_id, to_index)
        # Compact both books (source & dest) in case of inter-book move
        self.db.chapter_compact_positions(pid, from_book)
        if to_book_id != from_book:
            self.db.chapter_compact_positions(pid, to_book_id)
        self.db.conn.commit()
        self._refresh_outline_and_tree(to_book_id, focus_cid=chap_id)

    def _on_outline_delete_requested(self, chap_id: int):
        # keep your existing delete logic; this shows how to notify workspace afterwards
        self._soft_delete_chapter(chap_id)   # you already have this

    def _on_outline_delete_chapter(self, chap_id: int):
        # Your existing soft-delete (keeps outline in ui_prefs for potential restore)
        self._soft_delete_chapter(chap_id, "outline")

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

    def _label_to_version_number(self, label: str) -> int | None:
        """Parse 'vN' labels to N; returns None if it doesn't match."""
        m = re.fullmatch(r"v(\d+)(?:\s*\(active\))?", label.strip())
        return int(m.group(1)) if m else None

    def _resolve_version_id_from_label(self, chap_id: int, label: str) -> int | None:
        """
        Try to map a UI label to a DB chapter_versions.id, using (in order):
        1) outlineWorkspace.version_id_for_label (if present),
        2) the version_number parsed from 'vN' labels,
        3) fallback to active_version_id.
        """
        # 1) workspace-provided mapping
        if hasattr(self.outlineWorkspace, "version_id_for_label"):
            try:
                vid = self.outlineWorkspace.version_id_for_label(chap_id, label)
                if vid:
                    return int(vid)
            except Exception:
                pass

        # 2) 'vN' label → look up by version_number
        vn = self._label_to_version_number(label)
        if vn is not None:
            c = self.db.conn.cursor()
            c.execute("SELECT id FROM chapter_versions WHERE chapter_id=? AND version_number=?", (chap_id, vn))
            row = c.fetchone()
            if row:
                return int(row["id"])

        # 3) fallback: active
        return self.db.get_active_version_id(chap_id)

    def _create_new_version_from_current_view(self, chap_id: int, ui_label: str) -> int | None:
        """
        Clone current *viewed* text into a new DB version (NOT set active).
        Returns new chapter_version_id. UI naming remains in outlineWorkspace.
        """
        seed_ver = self._view_version_id or self.db.get_active_version_id(chap_id)
        md = self.db.chapter_content(chap_id, version_id=seed_ver) or ""
        # create DB version (make_active=False so we don't flip the book's active)
        new_ver_id = self.db.create_chapter_version(chap_id, md, make_active=False)
        # Optional: if your outlineWorkspace can store the DB id for a label, attach it now:
        if hasattr(self.outlineWorkspace, "bind_version_id_for_label"):
            try:
                self.outlineWorkspace.bind_version_id_for_label(chap_id, ui_label, new_ver_id)
            except Exception:
                pass
        return new_ver_id

    def _apply_view_version(self, chap_id: int, version_id: int | None):
        """
        Update editor + preview + Extract to reflect the 'viewed' version (does NOT change active).
        """
        self._view_version_id = version_id if version_id is not None else None

        # Editor & preview
        md = self.db.chapter_content(chap_id, version_id=self._view_version_id) or ""
        self.centerEdit.blockSignals(True)
        self.centerEdit.setPlainText(md)
        self.centerEdit.blockSignals(False)
        self._render_center_preview(version_id=self._view_version_id)

        # keep mini outline in sync with center-editor version
        mini = self.tabMiniOutline
        if mini and mini._chap_id == chap_id:
            mini.set_version_by_id(self._view_version_id)
            mini.refresh_from_workspace(reason="header-version", cid=chap_id)

        # Extract aligns to viewed version (no auto-parse here)
        if hasattr(self, "tabExtract"):
            self.tabExtract.set_chapter_version(chap_id, self._view_version_id)

        # Optional: auto-parse the viewed version (toggle-able)
        if getattr(self, "auto_parse_on_view_change", True):
            self.cmd_quick_parse_chapter(chap_id, version_id=self._view_version_id)

    def select_version_name(self, suggested: str=None, prompt: bool=True, chap_id: int=None) -> str:
        if not prompt:
            return suggested
        # prompt
        while True:
            new_name, ok = QInputDialog.getText(self, "New version", "Version name:", text=suggested)
            if not ok:
                # restore selection to current version
                cur = None
                try:
                    cur = self.outlineWorkspace.current_version_for_chapter_id(chap_id)
                except Exception:
                    pass
                with QSignalBlocker(self.cmbChapterVersion):
                    if cur:
                        self.cmbChapterVersion.setCurrentText(cur)
                return
            new_name = new_name.strip()
            if new_name:
                break
            QMessageBox.warning(self, "Name required", "Please provide a version name.")
        return new_name

    # def view_version_name_for(self, chap_id: int) -> str:
    #     pid = self._current_project_id
    #     return self.db.ui_pref_get(pid, f"view_version:{chap_id}") or "v1"

    def view_version_name_for(self, chap_id: int) -> str | None:
        # whatever your centerView/header considers the current label:
        return self._view_version_name_by_cid.get(chap_id, "v1")

    def set_view_version_name_for(self, chap_id: int, vname: str) -> None:
        pid = self._current_project_id
        self.db.ui_pref_set(pid, f"view_version:{chap_id}", vname)
        prev = getattr(self, "_view_version_name_by_cid", {}).get(chap_id)
        self._view_version_name_by_cid[chap_id] = vname
        print(f"[VIEW set] cid={chap_id} '{prev}' → '{vname}'")

        mini = self.tabMiniOutline
        if mini and mini._chap_id == chap_id:
            mini.set_version_name(vname)
            mini.refresh_from_workspace(reason="view-version-set")

    def create_new_chapter_version(self, chap_id: int):
        # suggest v{n+1} based on outlineWorkspace count (kept from your code)
        count = len(self.outlineWorkspace.versions_for_chapter_id(chap_id))
        name = self.select_version_name(suggested = f"v{count+1}", prompt=False, chap_id=chap_id)

        # 1) Create the version in the WS model (labels/ordering)
        try:
            self.outlineWorkspace.add_version_for_chapter_id(
                chap_id, name, clone_from_current=True)
        except Exception:
            pass

        # 2) Create the version in DB for the *center view* (clone current view)
        ver_id = self._create_new_version_from_current_view(chap_id, name)

        # 3) Select in header (block signals to avoid recursion)
        with QSignalBlocker(self.cmbChapterVersion):
            self.cmbChapterVersion.setCurrentText(name)

        # 4) Apply in center view + remember “view version”
        self._apply_view_version(chap_id, ver_id)
        self.set_view_version_name_for(chap_id, name)
        return name, ver_id

    def _on_header_version_changed(self, name: str):
        chap_id = getattr(self, "_current_chapter_id", None)
        if not chap_id or not name:
            return

        # --- "New version…" flow ---
        if name == "➕ New version…":
            name, ver_id = self.create_new_chapter_version(chap_id)
        # --- Normal switch to an existing version label ---
        else:
            ver_id = self._resolve_version_id_from_label(chap_id, name)

        # Apply in center view + remember “view version”
        self._apply_view_version(chap_id, ver_id)

        # 1) remember per-chapter header version for the session
        self._view_version_name_by_cid[chap_id] = name
        # 2) update the mini to *display* the header’s version
        self.set_view_version_name_for(chap_id, name)

        # (independent modes) do NOT change WS panes here
        # if you ever want “linking”, check a flag and call:
        # if getattr(self, "_link_view_and_outline_versions", False):
        #     self.outlineWorkspace.select_version_for_chapter_id(chap_id, name)

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
        # self.cmbChapterVersion.setMinimumWidth(140)
        self.cmbChapterVersion.setObjectName("cmbChapterVersion")
        self.cmbChapterVersion.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmbChapterVersion.setToolTip("View a different version of this chapter")
        self.cmbChapterVersion.currentTextChanged.connect(self._on_header_version_changed)
        self._view_version_name_by_cid = {}

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
        self._attach_link_handlers(self.centerView)
        self._apply_doc_styles(self.centerView)

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
        vb = getattr(self.worldDetail, "get_view_browser", lambda: None)()
        if vb:
            self.app._attach_link_handlers(vb)   # uses squelch-aware router + hides card
            self._install_wikilink_hover(vb)     # hover behavior

    def _install_wikilink_hover(self, tb):
        if not tb: return
        tb.viewport().setMouseTracking(True)
        if not getattr(tb, "_wl_hover_installed", False):
            self._wl_hover = getattr(self, "_wl_hover", None) or _WikiHoverFilter(self)
            tb.viewport().installEventFilter(self._wl_hover)
            tb._wl_hover_installed = True

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

        # Extract tab
        self.tabExtract = ExtractPane(self)
        self.bottomTabs.addTab(self.tabExtract, "Extract")

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


    def _wire_outline_version_bridge(self):
        # Workspace → Page listens for app-level version changes
        self.outlineVersionChanged.connect(
            self.outlineWorkspace.page.on_app_outline_version_changed
        )

        # If the workspace emits pane version changes, rebroadcast up
        self.outlineWorkspace.versionChanged.connect(self._on_workspace_version_changed)
        # (and page already hooked to call self.set_current_outline_version_name_for)

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

        # Center editor context menu
        self.centerEdit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.centerEdit.customContextMenuRequested.connect(self._on_center_context_menu)

        # Characters page signals
        # self.charactersPage.characterOpenRequested.connect(self.open_character_editor)
        self.charactersPage.characterOpenRequested.connect(self.open_character_dialog)

        # Outline workspace signals
        self.outlineWorkspace.adopt_db_and_scope(self.db, self._current_project_id, self._current_book_id)
        self.outlineWorkspace.chapterInsertRequested.connect(self._on_outline_insert_requested)
        self.outlineWorkspace.chapterRenameRequested.connect(self._on_outline_rename_requested)
        self.outlineWorkspace.chapterMoveRequested.connect(self._on_outline_move_requested)
        self.outlineWorkspace.chapterDeleteRequested.connect(self._on_outline_delete_chapter)
        self.chaptersOrderChanged.connect(self.outlineWorkspace.apply_order_by_ids)
        self._wire_outline_version_bridge()

    def _outline_controller(self):
        return self.outlineWorkspace.page.undoController

    def _is_outline_surface_active(self) -> bool:
        uc   = self._outline_controller()
        if not uc or uc.active_surface() == "none":
            return False
        fw = QApplication.focusWidget()
        if not fw:
            return False
        ws = getattr(self, "outlineWorkspace", None)
        if not ws or not getattr(ws, "page", None):
            return False
        # focus must be inside any pane editor or the mini editor
        if any(p.editor.isAncestorOf(fw) for p in ws.page.panes):
            return True
        mini = getattr(ws.page, "single_mini", None)
        return bool(mini and mini.editor.isAncestorOf(fw))

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
            print("Opening character editor from _on_worldtree_item_changed_name")
            self.open_character_dialog(new_id)
        # Open in right panel, EDIT mode, cursor ready
        else:
            print("load_world_item from _on_worldtree_item_changed_name")
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
        last_pos_idx = self.db.chapter_last_position_index(self._current_project_id, self._current_book_id)
        insert_pos = last_pos_idx + 1
        new_id = self.db.chapter_insert(self._current_project_id, self._current_book_id, insert_pos, "New Chapter", "")

        self.db.conn.commit()

        self.populate_chapters_tree()
        self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)
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
            self.db.chapter_compact_positions(pid, bid)

            self.populate_chapters_tree()
            self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)
            if new_ids:
                self.load_chapter(new_ids[0])
        finally:
            self._importing_chapters_now = False


    # ---------- Populate ----------
    def populate_all(self):
        self.populate_chapters_tree()
        self.populate_world_tree()
        self.populate_refs_tree(None)
        self.populate_outline_workspace()

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

    # TODO: make this version-spcific
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

    # def compact_and_renumber_after_tree_move(self, src_parent, dest_parent):
    #     """
    #     After a Qt reorder/move, persist 0..N-1 positions for the affected book(s)
    #     based on the current *visible* order in the tree (which already excludes
    #     soft-deleted chapters). Then relabel the on-screen items without a full reload.
    #     """

    #     def climb_to_book(node):
    #         """If node is a chapter, climb to its book; if already a book, return it; else None."""
    #         if not node:
    #             return None
    #         d = node.data(0, Qt.UserRole) or ()
    #         if d and d[0] == "book":
    #             return node
    #         # climb
    #         p = node
    #         while p and p.parent():
    #             p = p.parent()
    #             dd = p.data(0, Qt.UserRole) or ()
    #             if dd and dd[0] == "book":
    #                 return p
    #         return None

    #     handled = set()
    #     for raw in (src_parent, dest_parent):
    #         book_node = climb_to_book(raw)
    #         if not book_node or book_node in handled:
    #             continue
    #         handled.add(book_node)

    #         bdata = book_node.data(0, Qt.UserRole) or ()
    #         if not (bdata and bdata[0] == "book"):
    #             continue
    #         book_id = int(bdata[1])

    #         # 1) Read the visual order (chapters only, i.e., non-deleted)
    #         ordered_ids = []
    #         for i in range(book_node.childCount()):
    #             ch = book_node.child(i)
    #             cd = ch.data(0, Qt.UserRole) or ()
    #             if cd and cd[0] == "chapter":
    #                 ordered_ids.append(int(cd[1]))

    #         # Nothing to persist
    #         if not ordered_ids:
    #             continue

    #         # 2) Persist compact positions 0..N-1 into DB for these chapters
    #         cur = self.db.conn.cursor()
    #         for pos, cid in enumerate(ordered_ids):
    #             cur.execute("UPDATE chapters SET book_id=?, position=? WHERE id=?",
    #                         (book_id, pos, cid))
    #         self.db.conn.commit()

    #         # 3) Relabel visible nodes to "n. title" using fresh titles from DB (no full reload)
    #         qmarks = ",".join("?" for _ in ordered_ids)
    #         cur.execute(f"SELECT id, title FROM chapters WHERE id IN ({qmarks})", ordered_ids)
    #         id_to_title = {int(r[0]): (r[1] if not isinstance(r, sqlite3.Row) else r["title"])
    #                     for r in cur.fetchall()}

    #         # Keep numbering contiguous (1-based in labels)
    #         for i in range(book_node.childCount()):
    #             ch = book_node.child(i)
    #             cd = ch.data(0, Qt.UserRole) or ()
    #             if cd and cd[0] == "chapter":
    #                 cid = int(cd[1])
    #                 # Find its new 0-based pos as index in ordered_ids
    #                 try:
    #                     pos0 = ordered_ids.index(cid)
    #                 except ValueError:
    #                     # Not in ordered_ids → likely a non-chapter or deleted (shouldn't happen here)
    #                     continue
    #                 base_title = id_to_title.get(cid, ch.text(0))
    #                 ch.setText(0, chapter_display_label(pos0, base_title))

    def compact_and_renumber_after_tree_move(self, src_book_item, dest_book_item):
        pid = self._current_project_id

        def ordered_chapter_ids(book_item):
            out = []
            for i in range(book_item.childCount()):
                d = book_item.child(i).data(0, Qt.UserRole)
                if d and d[0] == "chapter":
                    out.append(int(d[1]))
            return out

        touched = [x for x in {src_book_item, dest_book_item} if x]

        for book_item in touched:
            bdata = book_item.data(0, Qt.UserRole)
            if not (bdata and bdata[0] == "book"):
                continue
            bid = int(bdata[1])

            ids = ordered_chapter_ids(book_item)
            # persist the visual order via db helpers
            for pos, cid in enumerate(ids):
                self.db.chapter_move_to_index(pid, bid, cid, pos)

            # normalize positions to 0..N-1, skipping deleted
            self.db.chapter_compact_positions(pid, bid)

        # refresh both UIs
        self.populate_chapters_tree()
        if hasattr(self, "outlineWorkspace"):
            self.outlineWorkspace.load_from_db(self.db, pid, self._current_book_id)

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
                    base = re.sub(r"^\s*\d+\.\s+", "", ch.text(0)).strip()
                ch.setText(0, chapter_display_label(pos, base))
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
            self.cmbChapterVersion.addItem("➕ New version…")
            self.cmbChapterVersion.setCurrentText(current)

    def load_chapter(self, chap_id: int):
        """Load a chapter into the center pane. Saves current edits first."""
        self._prepare_to_switch_chapter()

        self._current_chapter_id = chap_id
        self._view_version_id = None  # None => view active version

        self.db.ensure_active_version(chap_id)

        # DEBUG (kept)
        txt, h, vid = self.db.chapter_active_text_and_hash(chap_id)
        print("Active ver:", vid, "hash:", h, "len:", len(txt))

        # Fetch title + active-version text
        title, md = self._load_title_and_text(chap_id, version_id=None)

        # Update center widgets (title + editor) & preview
        self._apply_title_and_text(title, md)
        self._show_view_mode()

        # Tabs + trees that depend on current chapter
        self._bind_bottom_tabs(chap_id)
        self.populate_refs_tree(chap_id)
        self.focus_chapter_in_tree(chap_id)

        # Resolve outline row (kept exactly as your logic)
        outline_row = self._resolve_outline_row(chap_id)

        # Populate the version combo UI
        self._populate_version_combo(outline_row, chap_id)

        # Persist MRU
        self._store_recent_chapter(chap_id)

        # Keep mini pinned
        self.tabMiniOutline.set_workspace(self.outlineWorkspace)
        self.tabMiniOutline.set_chapter(self._current_chapter_id)

    def _prepare_to_switch_chapter(self):
        # Save current if needed
        self.save_current_if_dirty()

    def _load_title_and_text(self, chap_id: int, version_id: int | None):
        # Title from chapter meta
        meta = self.db.chapter_meta(chap_id)
        title = meta.get("title") if isinstance(meta, dict) else meta["title"]
        # Text for active or a specific version
        md = self.db.chapter_content(chap_id, version_id=version_id) or ""
        return title or "", md

    def _apply_title_and_text(self, title: str, md: str):
        self.titleEdit.blockSignals(True)
        self.titleEdit.setText(title)
        self.titleEdit.blockSignals(False)

        self.centerEdit.blockSignals(True)
        self.centerEdit.setPlainText(md)
        self.centerEdit.blockSignals(False)

        self._chapter_dirty = False

    def _show_view_mode(self):
        if hasattr(self, "centerStatus"):
            self.centerStatus.show_neutral("Viewing")
        self._render_center_preview(version_id=self._view_version_id)  # None => active
        self._center_set_mode(view_mode=True)
        self._update_word_sync_ui()

    def _bind_bottom_tabs(self, chap_id: int):
        self.tabTodos.set_chapter(chap_id)
        # Extract should NOT auto-parse here to avoid double inserts
        self.tabExtract.set_chapter(chap_id)
        if not hasattr(self, "extract_panes"):
            self.extract_panes = {}
        self.extract_panes[chap_id] = self.tabExtract

    def _resolve_outline_row(self, chap_id: int) -> int:
        outline_row = self._current_outline_row()   # returns -1 if none
        if outline_row < 0 and hasattr(self.outlineWorkspace, "row_for_chapter_id"):
            outline_row = self.outlineWorkspace.row_for_chapter_id(chap_id)
        ow_model = getattr(self.outlineWorkspace, "model", None)
        if (outline_row < 0) and ow_model and ow_model.rowCount() > 0:
            outline_row = 0
            try:
                idx = ow_model.index(outline_row, 0)
                outline_list = getattr(self.outlineWorkspace, "list", None)
                if outline_list is not None:
                    outline_list.setCurrentIndex(idx)
            except Exception:
                pass
        return outline_row

    def append_new_version_option(self, combo_box: QComboBox):
        combo_box.addItem("➕ New version…")

    def _populate_version_combo(self, outline_row: int, chap_id: int):
        with QSignalBlocker(self.cmbChapterVersion):
            self.cmbChapterVersion.clear()

            # Prefer your workspace-provided names if available
            versions = []
            current_name = ""
            if outline_row is not None and outline_row >= 0:
                try:
                    versions = self.outlineWorkspace.versions_for_row(outline_row) or []
                    current_name = self.outlineWorkspace.current_version_for_row(outline_row) or (versions[0] if versions else "")
                except Exception:
                    versions = []
                    current_name = ""

            if not versions:
                # Fallback to DB list (vN labels)
                rows = self.db.list_chapter_versions(chap_id)  # [(id, version_number, ..., is_active)]
                for r in rows:
                    label = f"v{r['version_number']}" + ("  (active)" if r["is_active"] else "")
                    self.cmbChapterVersion.addItem(label, userData=int(r["id"]))
                self.append_new_version_option(self.cmbChapterVersion)
                # Select active
                act = self.db.get_active_version_id(chap_id)
                if act is not None:
                    for i in range(self.cmbChapterVersion.count()):
                        if self.cmbChapterVersion.itemData(i) == act:
                            self.cmbChapterVersion.setCurrentIndex(i)
                            break
                return

            # Workspace provided names (string list) path:
            self.cmbChapterVersion.addItems(versions)
            self.append_new_version_option(self.cmbChapterVersion)
            if current_name:
                self.cmbChapterVersion.setCurrentText(current_name)
            else:
                self.cmbChapterVersion.setCurrentIndex(0)

    def _store_recent_chapter(self, chap_id: int):
        self.db.ui_pref_set(self._current_project_id, "outline:last_chapter_id", str(chap_id))
        self.db.ui_pref_set(self._current_project_id, f"chapters:last:{self._current_book_id}", str(chap_id))

    def mark_chapter_dirty(self):
        self._chapter_dirty = True
        if hasattr(self, "centerStatus"):
            self.centerStatus.set_dirty()
        self.setWindowModified(True)

    def autosave_chapter_title(self):
        if self._current_chapter_id is None: return
        old_title = self.db.chapter(self._current_chapter_id)
        title = self.titleEdit.text().strip()
        cur = self.db.conn.cursor()
        cur.execute("UPDATE chapters SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (title, self._current_chapter_id))
        self.db.conn.commit()
        # update FTS row via trigger; refresh tree label
        if not old_title == title:
            self.populate_chapters_tree()
            self.outlineWorkspace.load_from_db(self.db, self._current_project_id, self._current_book_id)

    def save_current_if_dirty(self):
        """Persist dirty state for the current chapter + panels (no raw SQL here)."""
        chap_id = getattr(self, "_current_chapter_id", None)
        if chap_id is None:
            return
        ver_id = self._view_version_id or self.db.get_active_version_id(chap_id)

        # 0) Word lock guard
        word_locked_id = getattr(self, "_word_lock_chapter_id", None)
        locked = (word_locked_id is not None and word_locked_id == chap_id)

        text_changed = False

        # 1) Chapter text -> active version
        if getattr(self, "_chapter_dirty", False) and not locked:
            md = self.centerEdit.toPlainText()
            ver_id = self.db.ensure_active_version(chap_id)
            _, text_changed = self.db.set_chapter_version_text(ver_id, md)
            self._chapter_dirty = False
            if hasattr(self, "centerStatus"):
                self.centerStatus.set_saved_now()
            # Update window modified indicator based on other panes
            self.setWindowModified(bool(getattr(self.worldDetail, "_dirty", False)))

            if text_changed:
                # Update preview + references
                self.cmd_quick_parse(doc_type="chapter", doc_id=chap_id, version_id=ver_id)
                # (cmd_quick_parse already recomputes refs and refreshes the preview)
                # self.recompute_chapter_references(chapter_id=chap_id, chapter_version_id=ver_id, text=None)
                # self.render_center_preview(version_id=ver_id)
                self.populate_refs_tree(chap_id)

        # 2) Persist To-Dos/Notes #TODO: make this version-specific
        if hasattr(self.tabTodos, "save_if_dirty"):
            self.tabTodos.save_if_dirty(chap_id)

        # 3) If title autosaves elsewhere, optionally refresh chapter tree if its display depends on title
        #    (Do not update title here anymore.)
        self.populate_chapters_tree()  # harmless; keeps labels/ordering fresh


    # ---------- Reference extraction on save ----------
    def seek_references(self, text: str) -> set[int]:
        """Lists longest-phrase matching across project's known titles + aliases found within text."""
        # pairs = [(p, wid) for (wid, p) in self.db.world_phrases_for_project(self._current_project_id)]
        pairs = [(phrase_norm, wid) for (phrase_norm, wid, alias_id) in self.db.world_phrases_for_project_detailed(self._current_project_id)]
        pairs.sort(key=lambda t: len(t[0]), reverse=True)
        print(f"Recomputing refs: {len(pairs)} phrases to match.")

        # Normalize haystack: lowercase, collapse whitespace
        hay = re.sub(r"\s+", " ", (text or "").lower()).strip()
        found_ids, used = set(), []
        def overlaps(s,e): return any(not (e<=ps or s>=pe) for ps,pe in used)
        for phrase, wid in pairs:
            if not phrase: continue
            for m in re.finditer(r"(?<!\w)"+re.escape(phrase)+r"(?!\w)", hay):
                s,e = m.span()
                if overlaps(s,e): continue
                found_ids.add(int(wid)); used.append((s,e))
        return found_ids

    DOC_SCHEMA = {
        "chapter":   {"text_field": "content_md", "id_field": "id", "versioned": True},
        "world_item":{"text_field": "content_md", "id_field": "id", "versioned": False},
        # "note": {"text_field":"...", ...}
        # NOTE: when updating this, also update recompute_references and db.fetch_text_for_doc
    }

    def recompute_references(self, *, doc_type, doc_id, version_id=None, text=None):
        if text is None:
            text = self.db.fetch_text_for_doc(doc_type=doc_type, doc_id=doc_id, version_id=version_id) or ""
        # compute occurrences/refs based on doc_type rules
        found_ids = self.seek_references(text)
        # persist based on doc_type
        if doc_type == "chapter":
            self.persist_chapter_references(doc_id, version_id, found_ids)
        elif doc_type == "world_item":
            self.persist_world_item_references(doc_id, found_ids)
        else:
            self.persist_references(doc_type=doc_type, doc_id=doc_id, version_id=version_id, found_ids=found_ids)

    def persist_chapter_references(self, chapter_id: int, chapter_version_id: int, found_ids: list[int]):
        # write per-version
        self.db.set_chapter_version_world_refs(chapter_version_id, sorted(found_ids))
        # if this is the active version, mirror to chapter-level for legacy UI
        av = self.db.get_active_version_id(chapter_id)
        if av and int(av) == int(chapter_version_id):
            self.db.set_chapter_world_refs(chapter_id, sorted(found_ids))

    def persist_references(self, *, doc_type: str, doc_id: int, version_id: int | None, found_ids: list[int]):
        """Generic persister for recompute_references."""
        self.db.set_doc_refs(doc_type=doc_type, doc_id=doc_id, version_id=version_id, world_ids=found_ids or [])

    def persist_world_item_references(self, world_item_id: int, found_ids: list[int]):
        self.db.set_world_item_refs(world_item_id, found_ids or [])

    def  recompute_chapter_references(self, chapter_id: int = None, text: str = None,
                                    chapter_version_id: int | None = None) -> None:
        """
        Fetch the version text (if not provided) and recompute refs
        Longest-phrase matching across titles + aliases.
        """
        if chapter_version_id is None:
            vr = self.db.chapter_active_version_row(chapter_id)
            if not vr: return
            chapter_version_id = vr["id"]
        if text is None:
            text = self.db.chapter_content(chapter_id, version_id=chapter_version_id) or ""

        found_ids = self.seek_references(text)

        print(f"Found {len(found_ids)} referenced world items in chapter {chapter_id}.")
        self.persist_chapter_references(chapter_id, chapter_version_id, found_ids)

    def dedup_candidates(self, candidates: list[dict], known_phrases: set[str]) -> list[dict]:
        """Deduplicate candidate list by (candidate.lower(), start_off, end_off)."""
        seen = set()
        uniq = []
        for c in candidates:
            key = (c["candidate"].strip().lower(), c.get("start_off") or -1, c.get("end_off") or -1)
            if key in seen:
                continue
            seen.add(key)
            if c["candidate"].strip().lower() not in known_phrases:
                uniq.append(c)
        return uniq

    def quick_parse_text(self, text: str, *, doc_type: str, doc_id: int | None,
                        version_id: int | None) -> list[dict]:
        """
        Generic candidate generator for any doc_type.
        Uses existing spaCy/heuristics functions and project-known phrases.
        For 'chapter', keeps metrics cache; for others, skips metrics.
        Returns: [{'surface': str, 'kind_guess': str|None, 'confidence': float|None,
                'start_off': int|None, 'end_off': int|None}]
        """
        pid = self._current_project_id
        text = text or ""

        # (A) metrics (chapters only)
        if doc_type == "chapter" and doc_id and version_id is not None:
            text_hash = self.db.chapter_version_hash(version_id)
            mrow = self.db.metrics_get(doc_id, version_id, text_hash or "")
            if not mrow:
                metrics = compute_metrics(text)
                self.db.metrics_upsert(doc_id, version_id, text_hash or "", metrics)

        # (B) known phrases (aliases only; already normalized)
        known = {phrase_norm for (phrase_norm, wid, alias_id)
                in self.db.world_phrases_for_project_detailed(pid)}

        # (C) baseline candidates (spaCy ents only by default)
        plain = scrub_markdown_for_ner(text)
        cand = spacy_candidates_strict(plain, known_phrases=known)
        print("sorted spacy cands:\n", sorted([c["surface"] for c in cand]))
        # optionally normalize individual surfaces
        normed = []
        for c in cand:
            base, is_poss = normalize_possessive(c["surface"])
            # prefer base if both forms end up present later (UI de-dupe handles)
            c2 = c.copy(); c2["surface"] = base if is_poss else c["surface"]; c2["is_possessive"] = 1 if is_poss else 0
            normed.append(c2)
        cand = drop_overlapped_shorter(normed)

        # (D) optional heuristics (same knob used for chapters)
        if getattr(self, "extract_use_heuristics", False):
            doc = spacy_doc(plain)
            supplement = heuristic_candidates_spacy(doc, find_known_spans(plain, known)) if doc else []
            cand = drop_overlapped_shorter(cand + supplement)

        # (E) De-dupe “simple vs possessive” candidates, preferring base form
        seen = {}
        for c in cand:
            key = c["surface"].lower()
            prev = seen.get(key)
            if not prev:
                seen[key] = c
            else:
                # prefer non-possessive
                if prev.get("is_possessive",0) and not c.get("is_possessive",0):
                    seen[key] = c
        cand = list(seen.values())
        print("sorted final cands:\n", sorted([c["surface"] for c in cand]))

        # print(f"candidates: {cand}")
        return cand

    def cmd_quick_parse(self, *, doc_type: str, doc_id: int, version_id: int | None = None):
        """Run quick-parse on any text-bearing doc and upsert scope-specific candidates.
        1) get active text/hash
        2) compute metrics (cache keyed by version+hash)
        3) build ingest candidates (heuristic + optional spaCy)
        """
        pid = self._current_project_id

        # Resolve effective version for chapters
        if doc_type == "chapter":
            ver_id = version_id or (self._view_version_id or self.db.get_active_version_id(doc_id))
            if not ver_id:
                print(f"[quick-parse] No version resolved for chapter:{doc_id}")
                return
        else:
            ver_id = None  # world_item, notes, etc.

        # Fetch the exact text we’ll parse (so recompute+render see the same text)
        md = self.db.fetch_text_for_doc(doc_type=doc_type, doc_id=doc_id, version_id=ver_id) or ""
        if not md.strip():
            print(f"[quick-parse] Empty text for {doc_type}:{doc_id} ver={ver_id}")
            return
        print(f"[quick-parse] {doc_type}:{doc_id} ver={version_id} …")
        # Extract suggestions (spaCy + optional heuristics)
        suggestions = self.quick_parse_text(md, doc_type=doc_type, doc_id=doc_id, version_id=ver_id)

        # Upsert candidates *with the same resolved scope+version*
        for s in suggestions:
            self.db.ingest_candidate_upsert(
                project_id=pid, scope_type=doc_type, scope_id=doc_id, version_id=ver_id,
                candidate=s["surface"], kind_guess=s.get("kind_guess"), source="quick",
                confidence=s.get("confidence"), status="pending",
                start_off=s.get("start_off"), end_off=s.get("end_off"), context=s.get("context")
            )
        # Refresh the viewer that shows links for this scope
        if doc_type == "chapter":
            # Recompute refs using the *same* md we just parsed
            self.recompute_chapter_references(chapter_id=doc_id, text=md, chapter_version_id=ver_id)
            self._render_center_preview(ver_id)
        elif doc_type == "world_item":
            self.rebuild_world_item_render(doc_id)
            self.worldDetail.refresh_if_showing(doc_id)  # small helper, no-op if not visible

    def cmd_quick_parse_chapter(self, chapter_id: int, version_id: int | None = None):
        self.cmd_quick_parse(doc_type="chapter", doc_id=chapter_id, version_id=version_id)

    # # TODO: remove old version
    # def cmd_quick_parse_chapter(self, chapter_id: int, version_id: int | None = None):
    #     """
    #     1) get active text/hash
    #     2) compute metrics (cache keyed by version+hash)
    #     3) build ingest candidates (heuristic + optional spaCy)
    #     """
    #     pid = self._current_project_id
    #     if version_id is None:
    #         text, text_hash, ver_id = self.db.chapter_active_text_and_hash(chapter_id)
    #     else:
    #         text = self.db.chapter_content(chapter_id, version_id=version_id) or ""
    #         text_hash = self.db.chapter_version_hash(version_id)
    #         ver_id = version_id
    #     if ver_id is None:
    #         return

    #     # (2) metrics cache
    #     mrow = self.db.metrics_get(chapter_id, ver_id, text_hash or "")
    #     if not mrow:
    #         metrics = compute_metrics(text)
    #         self.db.metrics_upsert(chapter_id, ver_id, text_hash or "", metrics)

    #     # (3) build candidates (Always add spaCy ents)
    #     # known = {p for _, p in self.db.world_phrases_for_project(pid)}  # lowercased
    #     known = {phrase_norm for (phrase_norm, wid, alias_id) in self.db.world_phrases_for_project_detailed(pid)}
    #     cand = spacy_candidates_strict(text, known_phrases=known) # default: spaCy only
    #     # cand = build_candidates(text, known, super_lenient=getattr(self, "extract_lenient", False))

    #     # Optionally add a stricter heuristic supplement *inside* sentences:
    #     if getattr(self, "extract_use_heuristics", False):
    #         doc = spacy_doc(text)
    #         supplement = heuristic_candidates_spacy(doc, find_known_spans(text, known)) if doc else []
    #         cand = drop_overlapped_shorter(cand + supplement)
    #     print(f"candidates: {cand}")

    #     print("Quick parse found", len(cand), "candidates to upsert for chapter", chapter_id, "version", ver_id)
    #     # persist (upserts; version-aware)
    #     for c in cand:
    #         self.db.ingest_candidate_upsert(
    #             project_id=pid, scope_type="chapter", scope_id=chapter_id, version_id=ver_id,
    #             candidate=c["surface"], kind_guess=c.get("kind_guess"), context=None,
    #             start_off=c.get("start_off"), end_off=c.get("end_off"),
    #             confidence=c.get("confidence"), status="pending"
    #         )
    #     # refresh the viewer that shows links for this scope
    #     # recompute refs for the viewed version only
    #     self.recompute_chapter_references(chapter_id, text, chapter_version_id=version_id)
    #     # after all upserts/commits, refresh center:
    #     ver_id = version_id or (self._view_version_id or self.db.get_active_version_id(chapter_id))
    #     if ver_id:
    #         self._render_center_preview(ver_id)

    def cmd_quick_parse_world_item(self, world_item_id: int):
        self.cmd_quick_parse(doc_type="world_item", doc_id=world_item_id)

    def on_center_version_changed(self, version_id: int | None):
        """User changed the viewed version (does NOT change the book's active version)."""
        chap_id = getattr(self, "_current_chapter_id", None)
        if chap_id is None: return
        self._view_version_id = version_id  # remember selection if you like

        # Render preview + refresh Extract against the viewed version
        self._render_center_preview(version_id=version_id)
        if hasattr(self, "tabExtract"):
            self.tabExtract.set_chapter_version(chap_id, version_id)

        # Optional: auto-parse on view change (toggle-able)
        auto = getattr(self, "auto_parse_on_view_change", True)
        if auto:
            self.cmd_quick_parse_chapter(chap_id, version_id=version_id)

    def find_or_create_world_item(self, title_or_alias: str, kind: str) -> int | None:
        """
        Try to resolve an existing world item by title or alias (case-insensitive).
        If none, create a minimal new one and return its id.
        """
        c = self.db.conn.cursor()
        pid = self._current_project_id
        t = title_or_alias.strip()
        # resolve by title
        c.execute("""SELECT id FROM world_items
                    WHERE project_id=? AND LOWER(title)=LOWER(?) AND COALESCE(deleted,0)=0 LIMIT 1""",
                (pid, t))
        r = c.fetchone()
        if r:
            return int(r["id"])
        # resolve by alias
        c.execute("""SELECT wa.world_item_id AS id
                    FROM world_aliases wa JOIN world_items wi ON wi.id=wa.world_item_id
                    WHERE wi.project_id=? AND LOWER(wa.alias)=LOWER(?) AND COALESCE(wa.deleted,0)=0
                    LIMIT 1""", (pid, t))
        r = c.fetchone()
        if r:
            return int(r["id"])
        # create minimal
        c.execute("""INSERT INTO world_items (project_id, type, title)
                    VALUES (?,?,?)""", (pid, kind, t))
        wid = int(c.lastrowid)
        self.db.conn.commit()
        return wid

    def ensure_world_item_from_candidate(self, cand_row) -> int | None:
        title = (cand_row["candidate"] or "").strip()
        kind = (cand_row["kind_guess"] or "").strip()
        if not kind:
            kind = self._prompt_kind_for_new_item()
            if not kind:
                return None
        return self.find_or_create_world_item(title, kind)

    def _prompt_kind_for_new_item(self) -> str | None:
        kinds = ["character","place","organization","object","concept"]
        kind, ok = QInputDialog.getItem(self, "Categorize item", "Choose a type:", kinds, 0, False)
        return kind if ok else None

    # ---------- Render world MD with auto-links ----------
    def _render_html_from_md(self, md: str, *,
                            known_only: bool,
                            scope: tuple[str, int, int | None] | None = None,
                            exclude_world_id: int | None = None) -> str:
        md_linked = self._linkify_md(md or "",
                                    known_only=known_only,
                                    scope=scope,
                                    exclude_world_id=exclude_world_id)
        print("MD linked, known_only:", known_only, "|", md_linked)
        return md_to_html(md_linked)

    def rebuild_world_item_render(self, world_item_id: int):
        md = self.db.world_item_md(world_item_id)
        html_raw  = self._render_html_from_md(
            md, known_only=False,
            scope=("world_item", world_item_id, None),
            exclude_world_id=world_item_id
        )
        self.db.world_item_render_update(world_item_id, html_raw )

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

        rt = DocxRoundTrip(self, self._current_chapter_id, self._view_version_id)
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
        self.db.set_chapter_version_text(self._view_version_id, md)

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
            print("adopting outline workspace from main window")
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
        for wid, cat_id, title, md, html_content in cur.fetchall():
            ncat = id_map_wc.get(cat_id)
            cur.execute("""INSERT INTO world_items(project_id, category_id, title, content_md, content_render)
                        VALUES (?,?,?,?,?)""", (new_pid, ncat, title, md, html_content))
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
        self.autosave_chapter_title()  # already wired via editingFinished; still nice on Ctrl+S

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
