from __future__ import annotations

from functools import partial
import json

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtCore import Qt
from ui.widgets.outline.data import Chapter, ChapterVersion, chapters_from_json, chapters_to_json
from ui.widgets.outline.model import ChaptersModel
from ui.widgets.outline.commands import IndentCommand, MoveWithinCommand, MoveAcrossChaptersCommand
from ui.widgets.outline.pane import ChapterPane
from ui.widgets.outline.undo import UnifiedUndoController


class _PaneClickFilter(QtCore.QObject):
    def __init__(self, controller, editor):
        super().__init__()
        self.uc = controller
        self.ed = editor
    def eventFilter(self, obj, ev):
        if obj is self.ed and ev.type() == QtCore.QEvent.MouseButtonRelease:
            # wait until Qt moves the caret, then snapshot & force a new step
            QtCore.QTimer.singleShot(0, self._after_release)
        return False
    def _after_release(self):
        line, col = self.ed.get_line_col()
        # supply the click caret as the 'before' for the *next* T-step
        self.uc.set_nav_cursor_for(self.ed, (line, col))
        # and force a step break
        self.uc.force_break_next_text(self.ed)
        # also clear delete coalescing and one-shot local break flag
        self.ed._last_delete_kind = None
        self.ed._force_new_text_step = True


class ChaptersPage(QtWidgets.QWidget):
    def __init__(self, model: "ChaptersModel", parent=None):
        super().__init__(parent)
        self._pending_focus_row: int | None = None
        self._pending_focus_policy: str | None = None   # "first" | "last" | None
        self._suppress_focus_on_list_change = False
        # Connect once 
        QtWidgets.QApplication.instance().focusChanged.connect(self._on_focus_changed)

        self.model = model
        self.panes: list[ChapterPane] = []
        self.single_mini = None
        self.workspace = parent
        print(f"workspace: {self.workspace}")

        # UI skeleton
        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().setContentsMargins(0,0,0,0)
        self.layout().setSpacing(0)

        self.list_view = None  # workspace may set this later

        self.undoStack = QtGui.QUndoStack(self)
        self.undoController = UnifiedUndoController(self)
        print("PAGE binds after_apply_cb to controller", id(self.undoController))
        self.undoController.after_apply_cb = self._after_apply_for_editor  # <- explicit post-apply mirror

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QtWidgets.QWidget()
        self.v = QtWidgets.QVBoxLayout(self.container)
        self.v.setContentsMargins(8,8,8,8)
        self.v.setSpacing(12)
        self._have_stretch = False

        self.scroll.setWidget(self.container)
        self.layout().addWidget(self.scroll, 1)

        # connect signals for the initial model (once)
        self._connect_model_signals(self.model)

        # build initial panes
        self._rebuild_all_panes()

    def _on_pane_text_changed(self, ed):
        uc = self.undoController
        if uc.is_applying():
            return
        # 1) keep last T step 'after' up to date while the Qt doc is still coalescing typing
        uc.live_update_after(ed)

        # 2) mirror pane → mini as you already do
        mini = getattr(self.workspace.page, "single_mini", None)
        if mini and mini._chap_id == uc._chapter_id_for_editor(ed):
            mini._mirror_from_pane()

        print("PANE textChanged → mirror mini for ed", id(ed))

    # def _mirror_minis_for_editor(self, ed):
    #     print("PAGE _mirror_minis_for_editor: ctrl", id(self.undoController), "ed", id(ed))
    #     mini = getattr(self, "single_mini", None)
    #     print("PAGE mini", mini)
    #     if not mini:
    #         return
    #     try:
    #         cid = self.undoController._chapter_id_for_editor(ed)
    #     except Exception as e:
    #         print("PAGE cid lookup failed:", e); return
    #     print("PAGE mini present?", bool(mini), "mini._chap_id", getattr(mini, "_chap_id", None))
    #     print("PAGE cid", cid, "mini._chap_id", getattr(mini, "_chap_id", None))
    #     if getattr(mini, "_chap_id", None) != cid:
    #         return
    #     mini._mirror_from_pane()
    #     print("PAGE mirrored mini for cid", cid)

    # def _mirror_minis_for_editor(self, editor_or_none):
    #     """Called by the UndoController after any apply (undo/redo/structural)."""
    #     mini = getattr(self, "single_mini", None)
    #     if not mini:
    #         return
    #     # Only mirror if the mini is looking at the same chapter as the changed pane.
    #     p = None
    #     if editor_or_none:
    #         for pane in getattr(self, "panes", []):
    #             if pane.editor is editor_or_none:
    #                 p = pane
    #                 break
    #     if p is None:
    #         # No editor given (or couldn’t find it) — safe to mirror anyway
    #         mini._mirror_from_pane()
    #         return

    #     if p and hasattr(p, "chapter") and getattr(p.chapter, "id", None) == mini._chap_id:
    #         mini._mirror_from_pane()

    def _after_apply_for_editor(self, ed):
        # 1) mirror the mini for this chapter if present
        mini = getattr(self._workspace.page, "single_mini", None) if hasattr(self, "_workspace") else None
        if mini:
            try:
                if mini._chap_id == self.undoController._chapter_id_for_editor(ed):
                    mini._mirror_from_pane()
            except Exception:
                pass  # be robust — mirroring is best-effort

        # 2) focus the pane that owns 'ed'
        pane = None
        for p in self.panes:
            if p.editor is ed:
                pane = p
                break
        if pane:
            # move Qt focus so the visible caret matches the editor we just changed
            pane.editor.setFocus(QtCore.Qt.OtherFocusReason)
            # also ensure controller surface is pane
            self.undoController.set_active_surface_pane()

    def _pane_for_widget(self, w: QtWidgets.QWidget | None):
        if not w: return None
        while w and w is not self.container and not isinstance(w, ChapterPane):
            w = w.parentWidget()
        return w if isinstance(w, ChapterPane) else None

    def _on_focus_changed(self, old, new):
        active_pane = self._pane_for_widget(new)
        for p in self.panes:
            if p is not active_pane:
                # clear editor selection & caret highlight in other panes
                p.editor.clear_selection_visual()

    def attach_list_view(self, list_view: QtWidgets.QListView):
        self.list_view = list_view
        sel = self.list_view.selectionModel()
        sel.currentChanged.connect(self._on_list_current_changed)

    # Let the list drive viewport + (optionally) focus
    def _on_list_current_changed(self, current, previous):
        row = current.row()
        give_focus = not self._suppress_focus_on_list_change
        self._suppress_focus_on_list_change = False
        # expand but only scroll if not fully visible
        if row >= 0:
            if self.panes[row].btnCollapse.isChecked():
                self.panes[row].btnCollapse.setChecked(False)
            if not self._is_pane_fully_visible(row):
                QtCore.QTimer.singleShot(0, lambda r=row: self._scroll_to_pane(r))
            if give_focus:
                fw = QtWidgets.QApplication.focusWidget()
                inside = fw and (self.panes[row] is fw or self.panes[row].isAncestorOf(fw))
                if not inside:
                    self.panes[row].editor.setFocus(QtCore.Qt.OtherFocusReason)

    # Called when user clicks inside a ChapterPane (title/desc/editor/characters)
    def _select_in_list_by_pane(self, pane: "ChapterPane"):
        if not self.list_view:
            return
        row = self._pane_index(pane)
        idx = self.model.index(row, 0)
        # Tell the list change handler NOT to give focus (we only want highlight + scroll)
        self._suppress_focus_on_list_change = True
        self.list_view.setCurrentIndex(idx)

    def _is_pane_fully_visible(self, row: int) -> bool:
        if row < 0 or row >= len(self.panes): return False
        pane = self.panes[row]
        sb = self.scroll.verticalScrollBar()
        top = sb.value()
        bottom = top + self.scroll.viewport().height()
        y = pane.mapTo(self.container, QtCore.QPoint(0, 0)).y()
        return (y >= top) and (y + pane.height() <= bottom)

    def focus_chapter(self, row: int, give_focus: bool = True):
        if row < 0 or row >= len(self.panes): return
        pane = self.panes[row]

        # expand if collapsed
        if pane.btnCollapse.isChecked():
            pane.btnCollapse.setChecked(False)

        # only scroll if not fully visible
        if not self._is_pane_fully_visible(row):
            QtCore.QTimer.singleShot(0, lambda r=row: self._scroll_to_pane(r))

        # only set focus if caller requested AND focus isn't already inside this pane
        if give_focus:
            fw = QtWidgets.QApplication.focusWidget()
            inside = fw and (pane is fw or pane.isAncestorOf(fw))
            if not inside:
                pane.editor.setFocus(QtCore.Qt.OtherFocusReason)

    def _on_rows_moved(self, src_parent, src_start, src_end, dst_parent, dst_row):
        """ Reorder panes to match chapter order shown in list."""
        # pull the moved panes
        moving = self.panes[src_start:src_end+1]
        del self.panes[src_start:src_end+1]
        insert_at = dst_row if dst_row <= len(self.panes) else len(self.panes)
        for i, p in enumerate(moving):
            self.panes.insert(insert_at + i, p)
            self.v.removeWidget(p)              # detach then reinsert to correct spot
            self.v.insertWidget(insert_at + i, p)
        # reindex pane.row so each pane talks to the correct Chapter
        for i, p in enumerate(self.panes):
            p.row = i
            p.reload_chapter_ref()              # keep self.chapter in sync with self.row
        # if a row is currently selected, re-focus it (keeps it in view)
        if self.list_view:
            idx = self.list_view.currentIndex()
            if idx.isValid():
                self.focus_chapter(idx.row())

    def _on_data_changed(self, topLeft: QtCore.QModelIndex, bottomRight: QtCore.QModelIndex, roles=None):
        """Refresh the affected panes' headers/combos after model data changes."""
        start = topLeft.row()
        end   = bottomRight.row()
        for r in range(start, end + 1):
            if 0 <= r < len(self.panes):
                p = self.panes[r]
                # keep row + chapter ref in sync
                p.row = r
                p.reload_chapter_ref()
                p.refresh_from_model()

    def _on_model_reset(self):
        """Model replaced or fully reset: rebuild all panes from scratch."""
        self._rebuild_all_panes()


    def _on_pane_version_changed(self, row: int, name: str):
        ch = self.model._chapters[row]
        if getattr(ch, "id", None) is not None:
            self.versionChanged.emit(ch.id, name)

    def _install_undo_hooks(self, ed):
        if getattr(ed, "_undo_hooks_installed", False):
            return

        # PANE editors must have the Qt doc undo enabled
        ed.setUndoRedoEnabled(True)

        # seed snapshots so first T step has a correct BEFORE
        ed._last_text_snapshot_text   = ed.toPlainText()
        ed._last_text_snapshot_cursor = ed.get_line_col()

        # SINGLE connection: Qt doc → our controller
        ed.document().undoCommandAdded.connect(
            lambda e=ed: self.undoController.register_text(e)
        )

        # Optional: debug tap so you can see the Qt signal
        ed.document().undoCommandAdded.connect(
            lambda e=ed: print("DOC undoCommandAdded", id(e))
        )

        ed._undo_hooks_installed = True

    def _wire_pane(self, row, pane: "ChapterPane"):
        ed = pane.editor
        print("WIRE pane:", row, "ed", id(ed))

        # 1) Hook doc→controller ONCE, seed snapshots
        self._install_undo_hooks(ed)                 # sets UndoRedoEnabled(True), seeds snapshots,
                                                    # connects doc.undoCommandAdded → controller.register_text
        ed._outline_undo_controller = self.undoController

        # 2) Structural commands go on the app stack
        ed.setUndoStack(self.undoStack)

        # 3) Pane↔controller: structural actions
        ed.requestMoveOutTop.connect(    lambda lines, p=pane: self.chapter_move_out(p, lines, up=True))
        ed.requestMoveOutBottom.connect( lambda lines, p=pane: self.chapter_move_out(p, lines, up=False))
        ed.requestCursorAbove.connect(   lambda p=pane: self.cursor_cross_chapter(p, up=True))
        ed.requestCursorBelow.connect(   lambda p=pane: self.cursor_cross_chapter(p, up=False))
        ed.requestIndent.connect(        lambda delta, p=pane: self._indent_outdent(p, delta))
        ed.requestMoveWithin.connect(    lambda direction, p=pane: self._move_within(p, direction))

        # 4) Pane→mini live mirror for normal typing; ignore during applying
        ed.textChanged.connect(lambda e=ed: self._on_pane_text_changed(e))

        # 5) Nav committed (keyboard nav) → controller learns the BEFORE position & force-break
        ed.navCommitted.connect(lambda ln, col, e=ed: self.undoController.on_nav_commit(e, ln, col))

        # 6) Paste/cut/enter/bulk-delete/type-over-selection → force-break
        ed.commandIssued.connect(lambda kind, e=ed: self._on_editor_command(e, kind))

        # 7) Keep left list in sync when pane is interacted with
        pane.activated.connect(lambda p=pane: self._select_in_list_by_pane(p))

        # 8) Mark “pane” active on focus
        pane.activated.connect(lambda e=ed: self.undoController.set_active_surface_pane())

        if not hasattr(ed, "_focus_filter"):
            class _EF(QtCore.QObject):
                def __init__(self, controller, pane):
                    super().__init__()
                    self.controller = controller
                    self.pane = pane
                def eventFilter(self, obj, ev):
                    if ev.type() == QtCore.QEvent.FocusIn:
                        self.controller.set_active_surface_pane()
                        print("Pane FocusIn → set_active_surface_pane() row", row, "ed", id(pane.editor))
                    return False
            ed._focus_filter = _EF(self.undoController, pane)
            ed.installEventFilter(ed._focus_filter)

        # 9) Mouse click → nav commit (sets BEFORE & arms force-break)
        flt = _PaneClickFilter(self.undoController, ed)   # calls undoController.on_nav_commit(...)
        ed.installEventFilter(flt)
        ed._pane_click_filter = flt  # keep ref

    def _on_editor_command(self, ed, kind: str):
        # These edits should be their own atomic step
        if kind in ("paste", "cut", "enter", "bulk-delete"):
            self.undoController.force_break_next_text(ed)
        # backspace/delete series is handled via ed._last_delete_kind on the editor
    
    def _indent_outdent(self, pane: "ChapterPane", delta: int):
        has_sel, sL, sC, eL, eC, active_end = pane.editor.current_selection_line_cols()
        cmd = IndentCommand(page=self, pane=pane, levels=delta, active_end=active_end)
        self.undoStack.push(cmd)
        # Track structural step with source pane + data
        self.undoController.register_structural(pane, "indent", {"delta": delta})

    def _move_within(self, pane, direction):
        ed = pane.editor
        i0,i1 = ed._selected_line_range()
        cl, cc = ed._caret_line_and_column()
        has_sel, sL, sC, eL, eC, active_end = pane.editor.current_selection_line_cols()
        cmd = MoveWithinCommand(self, pane, i0, i1, direction, cl, cc, active_end=active_end)
        if cmd.valid:
            self.undoStack.push(cmd)
            self.undoController.register_structural(pane, "move_within", {"dir": direction})
        # else: edge-cross handled in editor (Alt+Up/Down) emitting requestMoveOutTop/Bottom

    def _on_rows_inserted(self, parent, first, last):
        for r in range(first, last + 1):
            pane = ChapterPane(self.model, r, self.container)
            self._wire_pane(r, pane)
            self.panes.insert(r, pane)
            self.v.insertWidget(r, pane)

        # reindex pane.row
        for i, p in enumerate(self.panes):
            p.row = i
            p.reload_chapter_ref()

        # Default policy if not explicitly set (prevents first-time None)
        policy = self._pending_focus_policy or "first"
        self._pending_focus_policy = None

        # Which row to focus from the inserted range
        r = first if policy == "first" else last

        # Left list highlight without stealing editor focus
        if self.list_view:
            self._suppress_focus_on_list_change = True
            self.list_view.setCurrentIndex(self.model.index(r, 0))

        # Defer focus/scroll until layout settles
        QtCore.QTimer.singleShot(0, partial(self._focus_newly_inserted_row, r))

    def suppress_next_list_focus(self):
        self._suppress_focus_on_list_change = True

    def request_focus_after_insert(self, policy: str = "first"):
        # "first": focus 'first' row of newly inserted range; "last": focus 'last'
        self._pending_focus_policy = policy

    def _focus_newly_inserted_row(self, row: int):
        if row < 0 or row >= len(self.panes):
            return
        pane = self.panes[row]
        if pane.btnCollapse.isChecked():
            pane.btnCollapse.setChecked(False)
        # robust scroll
        QtCore.QTimer.singleShot(0, lambda r=row: self._scroll_to_pane(r))
        # put caret in title
        pane.titleEdit.setFocus(QtCore.Qt.ShortcutFocusReason)
        pane.titleEdit.selectAll()

    def _scroll_to_pane(self, row: int, margin: int = 24):
        if row < 0 or row >= len(self.panes):
            return
        pane = self.panes[row]
        # compute exact y and set scrollbar — avoids ensureWidgetVisible races
        y = pane.mapTo(self.container, QtCore.QPoint(0, 0)).y()
        sb = self.scroll.verticalScrollBar()
        sb.setValue(max(0, y - margin))

    def _on_rows_removed(self, parent, first, last):
        for r in range(last, first - 1, -1):
            pane = self.panes.pop(r)
            self.v.removeWidget(pane)
            pane.setParent(None)
            pane.deleteLater()
        # reindex pane.row
        for i, p in enumerate(self.panes):
            p.row = i
            p.reload_chapter_ref()

    def _pane_index(self, pane: ChapterPane) -> int:
        return self.panes.index(pane)
    
    def _connect_model_signals(self, model):
        model.rowsInserted.connect(self._on_rows_inserted)
        model.rowsRemoved.connect(self._on_rows_removed)
        model.rowsMoved.connect(self._on_rows_moved)
        model.dataChanged.connect(self._on_data_changed)
        model.modelReset.connect(self._on_model_reset)

    def _disconnect_model_signals(self, model):
        for sig, slot in (
            (model.rowsInserted, self._on_rows_inserted),
            (model.rowsRemoved, self._on_rows_removed),
            (model.rowsMoved,   self._on_rows_moved),
            (model.dataChanged, self._on_data_changed),
            (model.modelReset,  self._on_model_reset),
        ):
            try:
                sig.disconnect(slot)
            except Exception:
                pass

    def set_model(self, model: ChaptersModel):
        """Swap to a new model and rebuild panes."""
        if model is self.model:
            return
        if self.model is not None:
            self._disconnect_model_signals(self.model)
        self.model = model
        self._connect_model_signals(self.model)
        # if the left list exists, update it too (done in workspace; optional here)
        self._rebuild_all_panes()

    def _clear_panes(self):
        for p in self.panes:
            p.setParent(None)
            p.deleteLater()
        self.panes.clear()

    def _ensure_single_stretch(self):
        # ensure a single stretch exists
        if not getattr(self, "_have_stretch", False):
            self.v.addStretch(1)
            self._have_stretch = True

    def _rebuild_all_panes(self):
        self._clear_panes()
        count = self.model.rowCount()
        if count <= 0:
            self._ensure_single_stretch()
            return

        # Reuse your existing insertion logic for consistency
        self._on_rows_inserted(QtCore.QModelIndex(), 0, count - 1)

        # Make sure exactly one stretch exists at the end
        self._ensure_single_stretch()

    # --- Cross-chapter logic ---
    def chapter_move_out(self, src_pane, _lines, up: bool):
        si = self._pane_index(src_pane); di = si-1 if up else si+1
        if di < 0 or di >= len(self.panes): return
        dst = self.panes[di]

        ed = src_pane.editor
        i0, i1 = ed._selected_line_range()
        cl, cc = ed._caret_line_and_column()
        caret_rel = max(0, min(cl - i0, i1 - i0))
        # Up → end of previous; Down → top of next
        insert_at = len(dst.editor.lines()) if up else 0

        c = ed.textCursor()
        keep_sel = c.hasSelection() or (i1 > i0)
        active_end = ed.current_selection_active_end()
        self.undoStack.push(MoveAcrossChaptersCommand(
            self, src_pane, dst, i0, i1, insert_at, caret_rel, cc,
            keep_selection=keep_sel, active_end=active_end
        ))
        self.undoController.register_structural(src_pane, "move_out", {"dir": "up" if up else "down"})

    def cursor_cross_chapter(self, src_pane, up: bool):
        si = self._pane_index(src_pane)
        di = si - 1 if up else si + 1
        if di < 0 or di >= len(self.panes):
            return
        dst = self.panes[di]

        # desired column from source
        col = src_pane.editor.current_column()

        # auto-expand destination if collapsed
        if dst.btnCollapse.isChecked():
            dst.btnCollapse.setChecked(False)

        # place caret on last (up) or first (down) line, same column when possible
        target_line = (dst.editor.blockCount() - 1) if up else 0
        dst.editor.clamp_and_place_cursor(target_line, col)

        # focus after placing the caret to avoid any stray key movement
        dst.editor.setFocus()

    def collapse_all(self):
        for p in self.panes:
            if not p.btnCollapse.isChecked():
                p.btnCollapse.setChecked(True)

    def expand_all(self):
        for p in self.panes:
            if p.btnCollapse.isChecked():
                p.btnCollapse.setChecked(False)

class _ListKeyFilter(QtCore.QObject):
    def __init__(self, page):
        super().__init__()
        self.page = page
    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_F2:
            row = obj.currentIndex().row()
            if row >= 0:
                self.page.focus_chapter(row, give_focus=False)
                pane = self.page.panes[row]
                pane.titleEdit.setFocus(QtCore.Qt.ShortcutFocusReason)
                pane.titleEdit.selectAll()
            return True
        return False


class OutlineWorkspace(QtWidgets.QMainWindow):
    versionChanged = QtCore.Signal(int, str)  # (chapter_row, version_name)
    chapterDeleteRequested = QtCore.Signal(int)  # chap_id

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flat Outline Editor — Chapters")
        self.resize(1100, 700)

        self.model = ChaptersModel([])
        self._id_to_row = {}
        self._sel_model = None

        # --- Build UI ---

        # Left: chapter list + buttons (expand/collapse all)
        left = QtWidgets.QWidget(self)
        left_v = QtWidgets.QVBoxLayout(left)
        left_v.setContentsMargins(0,0,0,0)
        # Header row
        h = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("Chapters")
        btnCollapseAll = QtWidgets.QToolButton()
        btnCollapseAll.setText("Collapse all")
        btnExpandAll = QtWidgets.QToolButton()
        btnExpandAll.setText("Expand all")
        h.addWidget(lbl); h.addStretch(1); h.addWidget(btnCollapseAll); h.addWidget(btnExpandAll)
        left_v.addLayout(h)

        # Left: list of chapters (reorderable)
        self.list = QtWidgets.QListView()
        self.list.setModel(self.model)
        # Keep panes in view + expanded when the user selects in the left list
        sel = self.list.selectionModel()
        sel.currentChanged.connect(self._on_list_current_changed)
        # turn off inline editing in the list
        self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list.setDragEnabled(True); self.list.setAcceptDrops(True); self.list.setDropIndicatorShown(True)
        self.list.setDefaultDropAction(QtCore.Qt.MoveAction)
        # ensure clicking the already-selected row still brings the pane into view/focus
        self.list.activated.connect(lambda idx: self.page.focus_chapter(idx.row(), give_focus=True))
        # add context menu for add/remove
        self._in_context_menu = False
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_list_context_menu)
        self.list.viewport().setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.viewport().customContextMenuRequested.connect(self._on_list_context_menu)
        left_v.addWidget(self.list)

        # Right: multi-chapter page
        self.page = ChaptersPage(self.model, self)
        self.page.attach_list_view(self.list) # Also call this when loading outline from chapter in main app
        self.page.set_model(self.model)
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._rebuild_id_map()

        self._listKeyFilter = _ListKeyFilter(self.page)
        self.list.installEventFilter(self._listKeyFilter)

        # Export/Import buttons
        btnExport = QtWidgets.QPushButton("Export JSON")
        btnImport = QtWidgets.QPushButton("Import JSON")
        btnExport.clicked.connect(self._export_json)
        btnImport.clicked.connect(self._import_json)
        tools = QtWidgets.QHBoxLayout(); tools.addStretch(1); tools.addWidget(btnImport); tools.addWidget(btnExport)
        toolsW = QtWidgets.QWidget(); toolsW.setLayout(tools)

        # wire collapse/expand all buttons
        btnCollapseAll.clicked.connect(self.page.collapse_all)
        btnExpandAll.clicked.connect(self.page.expand_all)

        # Layout
        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left)
        right = QtWidgets.QWidget(); rv = QtWidgets.QVBoxLayout(right); rv.setContentsMargins(0,0,0,0)
        rv.addWidget(self.page, 1); rv.addWidget(toolsW, 0)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def install_global_shortcuts(self, host_widget):
        if getattr(self, "_shortcuts_installed", False):
            return
        ctrl = self.page.undoController  # already exists because ChaptersPage is built

        sc_undo = QShortcut(QKeySequence.Undo, host_widget)
        sc_undo.setContext(Qt.ApplicationShortcut)
        sc_undo.activated.connect(ctrl.undo)

        sc_redo = QShortcut(QKeySequence.Redo, host_widget)
        sc_redo.setContext(Qt.ApplicationShortcut)
        sc_redo.activated.connect(ctrl.redo)

        # Optional alias
        sc_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), host_widget)
        sc_redo2.setContext(Qt.ApplicationShortcut)
        sc_redo2.activated.connect(ctrl.redo)

        self._shortcuts_installed = True
        self._sc_undo, self._sc_redo, self._sc_redo2 = sc_undo, sc_redo, sc_redo2

    def set_single_mini(self, mini):
        self.page.single_mini = mini

    def _on_list_context_menu(self, pos: QtCore.QPoint):
        # suppress list-driven focus during menu open
        self.page.suppress_next_list_focus()
        self._in_context_menu = True

        view = self.list
        idx = view.indexAt(pos) if view.viewport() is self.sender() else view.indexAt(pos)
        if idx.isValid():
            # selecting row under cursor (don’t focus editor)
            view.setCurrentIndex(idx)

        global_pos = (view.viewport().mapToGlobal(pos)
                    if self.sender() is view.viewport()
                    else view.mapToGlobal(pos))

        menu = QtWidgets.QMenu(view)
        actAbove = menu.addAction("Insert chapter above")
        actBelow = menu.addAction("Insert chapter below")
        menu.addSeparator()
        actDelete = menu.addAction("Delete chapter")

        if not idx.isValid():
            actAbove.setEnabled(False)
            actDelete.setEnabled(False)

        chosen = menu.exec(global_pos)
        self._in_context_menu = False  # menu closed

        if chosen is None:
            return

        if chosen is actAbove:
            self._insert_chapter(idx.row())
        elif chosen is actBelow:
            self._insert_chapter(idx.row() + 1)
        elif chosen is actDelete:
            self._request_delete_row(idx.row())

    def showEvent(self, ev):
        QtWidgets.QApplication.instance().installEventFilter(self)
        super().showEvent(ev)

    def hideEvent(self, ev):
        QtWidgets.QApplication.instance().removeEventFilter(self)
        super().hideEvent(ev)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.KeyPress:
            mods = ev.modifiers(); key = ev.key()
            is_undo = (mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_Z and not (mods & QtCore.Qt.ShiftModifier)
            is_redo =((mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_Y) or \
                     ((mods & QtCore.Qt.ControlModifier) and (mods & QtCore.Qt.ShiftModifier) and key == QtCore.Qt.Key_Z)
            if is_undo or is_redo:
                fw = QtWidgets.QApplication.focusWidget()
                if isinstance(fw, QtWidgets.QLineEdit):
                    try:
                        (fw.redo() if is_redo else fw.undo())
                    except Exception:
                        pass
                    finally:
                        return True
                # Only act if focus is inside this workspace
                if self.isAncestorOf(fw):
                    (self.page.undoController.redo() if is_redo else self.page.undoController.undo())
                    return True
        return super().eventFilter(obj, ev)
    
    def _is_text_input_key(self, e: QtGui.QKeyEvent) -> bool:
        if e.text():  # printable char (includes space, punctuation, etc.)
            return True
        k = e.key()
        if k in (QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Delete, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            return True
        # treat Tab/Shift+Tab as structural (indent); we don't want text coalescing to eat those
        return False

    def _insert_chapter(self, row: int):
        # Ask for a title (optional)
        title, ok = QtWidgets.QInputDialog.getText(self, "New chapter", "Title:", text="New Chapter")
        if not ok:
            return
        # focus AFTER insert; “above/append” → first is enough, “below” also first (since we calculate row+1)
        ch = Chapter(title.strip() or "New Chapter")  # empty version stub inside
        new_row = self.model.insertChapter(row, ch)
        self.page.request_focus_after_insert("first") # the page will scroll/focus inside rowsInserted

    # def _focus_new_chapter(self, row: int):
    #     # select + scroll into view; put caret in title for quick rename
    #     idx = self.model.index(row, 0)
    #     self.list.setCurrentIndex(idx)
    #     self.page.focus_chapter(row, give_focus=False)   # scroll + ensure expanded
    #     pane = self.page.panes[row]
    #     pane.titleEdit.setFocus(QtCore.Qt.ShortcutFocusReason)
    #     pane.titleEdit.selectAll()

    # def _delete_chapter(self, chap_id: int):
    #     r = self.outlineWorkspace.row_for_chapter_id(chap_id)
    #     if row < 0 or row >= self.model.rowCount():
    #         return
    #     # confirm
    #     dlg = QtWidgets.QMessageBox(self)
    #     dlg.setIcon(QtWidgets.QMessageBox.Warning)
    #     dlg.setWindowTitle("Delete chapter")
    #     dlg.setText(f"Delete “{self.model.data(self.model.index(row, 0), QtCore.Qt.DisplayRole)}”?")
    #     dlg.setInformativeText("This will delete the chapter, its outline, any alternate chapter versions, and all associated notes/to-dos.")
    #     dlg.setStandardButtons(QtWidgets.QMessageBox.Cancel | QtWidgets.QMessageBox.Yes)
    #     dlg.setDefaultButton(QtWidgets.QMessageBox.Cancel)
    #     if dlg.exec() != QtWidgets.QMessageBox.Yes:
    #         return

    #     # Use existing logic to soft-delete chapter
    #     self._soft_delete_chapter(chap_id)
    #     # Remove from workspace model as well (if not fully reloading)
    #     if r >= 0:
    #         self.outlineWorkspace.model.removeRow(r)
    #         # optional: rebuild id map
    #         self.outlineWorkspace._rebuild_id_map()
    #     # Update mini tab if it was showing the deleted chapter
    #     if getattr(self, "_current_chapter_id", None) == chap_id and hasattr(self, "tabMiniOutline"):
    #         self.tabMiniOutline.set_chapter(chap_id)  # will clear editor because row<0

        # # choose a sensible next selection
        # next_row = min(row, self.model.rowCount() - 1)
        # if next_row >= 0:
        #     idx = self.model.index(next_row, 0)
        #     self.list.setCurrentIndex(idx)
        #     self.page.focus_chapter(next_row, give_focus=False)

    def _request_delete_row(self, row: int):
        if row < 0 or row >= self.model.rowCount():
            return
        ch = self.model._chapters[row]
        chap_id = getattr(ch, "id", None)
        if chap_id is None:
            return
        
        # confirm
        dlg = QtWidgets.QMessageBox(self)
        dlg.setIcon(QtWidgets.QMessageBox.Warning)
        dlg.setWindowTitle("Delete chapter")
        dlg.setText(f"Delete “{self.model.data(self.model.index(row, 0), QtCore.Qt.DisplayRole)}”?")
        dlg.setInformativeText("This will delete the chapter, its outline, any alternate chapter versions, and all associated notes/to-dos.")
        dlg.setStandardButtons(QtWidgets.QMessageBox.Cancel | QtWidgets.QMessageBox.Yes)
        dlg.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        if dlg.exec() != QtWidgets.QMessageBox.Yes:
            return

        # Let Main handle DB + removal policy
        self.chapterDeleteRequested.emit(chap_id)

        # choose a sensible next selection
        next_row = min(row, self.model.rowCount() - 1)
        if next_row >= 0:
            idx = self.model.index(next_row, 0)
            self.list.setCurrentIndex(idx)
            self.page.focus_chapter(next_row, give_focus=False)

    def _export_json(self):
        # ensure panes wrote back
        for p in self.page.panes: p.sync_into_model()
        s = chapters_to_json(self.model._chapters)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Outline", "outline.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f: f.write(s)

    def _import_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import Outline", "", "JSON (*.json)")
        if not path: return
        with open(path, "r", encoding="utf-8") as f: s = f.read()
        self.model.beginResetModel()
        self.model._chapters = chapters_from_json(s)
        self.model.endResetModel()
        # rebuild page
        self.page.setParent(None)
        self.page.deleteLater()
        self.page = ChaptersPage(self.model)
        self.centralWidget().widget(1).layout().insertWidget(0, self.page)  # put back into right-side vbox

    @QtCore.Slot(int, int, list)
    def apply_order_by_ids(self, project_id: int, book_id: int, ordered_ids: list[int]):
        if not self.model or not ordered_ids:
            return
        # Build current map: id -> row
        pos = {getattr(ch, "id", None): i for i, ch in enumerate(self.model._chapters)}
        parent = QtCore.QModelIndex()
        for target_row, chap_id in enumerate(ordered_ids):
            cur_row = pos.get(chap_id, None)
            if cur_row is None or cur_row == target_row:
                continue
            # moveRows adjusts indices; compute correct dst
            dst = target_row if cur_row > target_row else target_row + 1
            self.model.beginMoveRows(parent, cur_row, cur_row, parent, dst)
            ch = self.model._chapters.pop(cur_row)
            self.model._chapters.insert(target_row, ch)
            self.model.endMoveRows()
            # rebuild mapping for subsequent moves
            pos = {getattr(ch, "id", None): i for i, ch in enumerate(self.model._chapters)}
        # refresh id map afterwards
        self._rebuild_id_map()

    def focus_chapter_row(self, row: int, give_focus: bool = True):
        if not self.model or row < 0 or row >= self.model.rowCount():
            return
        # highlight in the left list (if present)
        lst = getattr(self, "list", None)  # or list_view if that's your name
        if lst is not None:
            idx = self.model.index(row, 0)
            lst.setCurrentIndex(idx)

        # ensure the pane is expanded and visible
        try:
            pane = self.page.panes[row]
        except Exception:
            return
        if getattr(pane, "btnCollapse", None) and pane.btnCollapse.isChecked():
            pane.btnCollapse.setChecked(False)

        # scroll it into view
        scroll = getattr(self.page, "scroll", None)
        if scroll is not None:
            try:
                scroll.ensureWidgetVisible(pane)
            except Exception:
                pass

        if give_focus:
            # prefer editor focus; fallback to title
            target = getattr(pane, "editor", None) or getattr(pane, "titleEdit", None)
            if target is not None:
                target.setFocus(QtCore.Qt.TabFocusReason)

    def versions_for_row(self, row: int) -> list[str]:
        if self.model is None:
            return []
        if row < 0 or row >= self.model.rowCount():
            return []
        ch = self.model._chapters[row]
        if not getattr(ch, "versions", None):
            ch.versions = [ChapterVersion(name="v1", lines=[])]
            ch.active_index = 0
            if row < len(self.page.panes):
                self.page.panes[row]._populate_versions_combo(select_name="v1", emit=False)
        return [v.name for v in ch.versions]

    def current_version_for_row(self, row: int) -> str | None:
        if row < 0 or row >= len(self.page.panes):
            return None
        return self.page.panes[row].verCombo.currentText()

    def select_version_for_row(self, row: int, name: str):
        if row < 0 or row >= len(self.page.panes):
            return
        pane = self.page.panes[row]
        i = pane.verCombo.findText(name)
        if i >= 0:
            pane.verCombo.setCurrentIndex(i)

    def add_version_for_row(self, row: int, name: str, clone_from_current=True):
        if row < 0 or row >= len(self.page.panes):
            return
        self.page.panes[row]._on_add_version_with_name(name, clone_from_current=clone_from_current)

    def _on_list_current_changed(self, cur: QtCore.QModelIndex, prev: QtCore.QModelIndex):
        row = cur.row()
        if row >= 0:
            # bring selection into view
            self.focus_chapter_row(row, give_focus=False)

    def _hook_list_selection(self):
        sm = self.list.selectionModel()
        if sm is self._sel_model:
            return
        # disconnect old (if any)
        if self._sel_model is not None:
            try: self._sel_model.currentChanged.disconnect(self._on_list_current_changed)
            except Exception: pass
        # connect new
        sm.currentChanged.connect(self._on_list_current_changed)
        self._sel_model = sm

    def _select_initial_row(self):
        # try last viewed
        last_id = None
        try:
            # expects your Database instance on self.db; if not, pass it in or call from Main
            last_id_str = self.db.ui_pref_get(self.current_project_id, "outline:last_chapter_id")
            last_id = int(last_id_str) if last_id_str else None
        except Exception:
            last_id = None

        row = -1
        if last_id is not None:
            row = self.row_for_chapter_id(last_id)
        if row < 0 and self.model.rowCount() > 0:
            row = 0

        if row >= 0:
            idx = self.model.index(row, 0)
            self.list.setCurrentIndex(idx)      # highlights in the tree
            # do not collapse/expand here; just bring into view
            self.focus_chapter_row(row, give_focus=False)

    def set_model(self, model: ChaptersModel):
        self.model = model or ChaptersModel([])
        # left list
        self.list.setModel(self.model)
        # right page
        self.page.set_model(self.model)
        # id map
        self._rebuild_id_map()
        # wire exapand-on-select logic
        self._hook_list_selection()
        self._select_initial_row() 

    # --- mapping helpers ---
    def _rebuild_id_map(self):
        self._id_to_row = {}
        if not self.model:
            return
        for r, ch in enumerate(self.model._chapters):
            cid = getattr(ch, "id", None)
            if cid is not None:
                self._id_to_row[cid] = r

    def row_for_chapter_id(self, chap_id: int) -> int:
        return self._id_to_row.get(chap_id, -1)

    # --- version helpers for Main ---
    def versions_for_chapter_id(self, chap_id: int) -> list[str]:
        row = self.row_for_chapter_id(chap_id)
        if row < 0: return []
        ch = self.model._chapters[row]
        # ensure at least one version exists
        if not getattr(ch, "versions", None):
            ch.versions = [ChapterVersion(name="v1", lines=[])]
            # also refresh the pane’s combo if already constructed
            if row < len(self.page.panes):
                self.page.panes[row]._refresh_versions_combo(select_name="v1", emit=False)
        return [v.name for v in ch.versions]

    def current_version_for_chapter_id(self, chap_id: int) -> str | None:
        row = self.row_for_chapter_id(chap_id)
        if row < 0 or row >= len(self.page.panes):
            return None
        return self.page.panes[row].versionCombo.currentText()

    def select_version_for_chapter_id(self, chap_id: int, name: str):
        row = self.row_for_chapter_id(chap_id)
        if row < 0 or row >= len(self.page.panes):
            return
        pane = self.page.panes[row]
        i = pane.versionCombo.findText(name)
        if i >= 0:
            pane.versionCombo.setCurrentIndex(i)

    def add_version_for_chapter_id(self, chap_id: int, name: str, clone_from_current=True):
        """Create a new version on that chapter; optionally clone from current."""
        row = self.row_for_chapter_id(chap_id)
        if row < 0: return
        pane = self.page.panes[row]
        # Pane already has logic to create versions; reuse it
        pane._add_version_named(name, clone_from_current=clone_from_current)  # implement this tiny helper in pane

    def expand_prev_current_next(self, current_row: int | None = None, chap_id: int | None = None):
        if current_row is None and chap_id is not None:
            current_row = self.row_for_chapter_id(chap_id)
        if current_row is None:
            # try selection or default to 0
            lst = getattr(self, "list", None)
            if lst is not None:
                idx = lst.currentIndex()
                if idx.isValid():
                    current_row = idx.row()
        if current_row is None and self.model and self.model.rowCount() > 0:
            current_row = 0
        if current_row is None:
            return

        for i, p in enumerate(self.page.panes):
            want_open = (i in (current_row - 1, current_row, current_row + 1))
            if getattr(p, "btnCollapse", None):
                p.btnCollapse.setChecked(not want_open)

    def load_from_db(self, db, project_id: int, book_id: int):
        """
        Eagerly load chapters + versioned outlines from db.ui_prefs
        Keys used:
          outline_versions:{chap_id}  -> '["v1","v2",...]'
          outline:{chap_id}:{vname}   -> '["line1","line2",...]'
        """
        # parse ordered chapter ids + titles
        chapters: list[Chapter] = []
        for ch in db.chapter_list(project_id, book_id):
            chap_id, title = ch["id"], ch["title"]
            # version names
            js_names = db.ui_pref_get(project_id, f"outline_versions:{chap_id}") or "[]"
            names = json.loads(js_names) or ["v1"]
            versions: list[ChapterVersion] = []
            for name in names:
                js_lines = db.ui_pref_get(project_id, f"outline:{chap_id}:{name}") or "[]"
                try:
                    lines = json.loads(js_lines) if js_lines else []
                except Exception:
                    lines = []
                versions.append(ChapterVersion(name=name, lines=lines))
            # ensure at least one version
            if not versions:
                versions = [ChapterVersion(name="v1", lines=[])]
            chapters.append(Chapter(title=title, versions=versions, id=chap_id))

        # 3) swap model in
        model = ChaptersModel(chapters)
        self.set_model(model)
        self._rebuild_id_map()     # keep id→row mapping fresh
