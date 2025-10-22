from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary
from PySide6 import QtCore, QtWidgets, QtGui

from ui.widgets.helpers import chapter_display_label

if TYPE_CHECKING:
    from .editor import OutlineEditor
    from .page import ChaptersPage

BREAK_NO_POS = object()
COALESCE_TIME_THRESHOLD = 1.5 # seconds
# Timeline tuple layout for "T" (text) steps:
# ("T", cid, editor, before_text, before_pos, after_text, after_pos, run_id)
T_KIND, T_CID, T_EDITOR, T_BTEXT, T_BPOS, T_ATEXT, T_APOS, T_RUN = range(8)
def _get_run_id(entry, default=None):
    return entry[T_RUN] if (len(entry) > T_RUN) else default

class UnifiedUndoController(QtCore.QObject):
    """
    Single timeline across outline editors:
      - 'T' entries store before/after snapshots for a specific OutlineEditor
      - 'S' entries trigger the page's QUndoStack (structural)
    """
    def __init__(self, page: "ChaptersPage"):
        super().__init__(page)
        self.page = page
        # entries: (, chap_id, editor, before_text, (before_line, before_col), after_text, (after_line, after_col))
        #       or ("S", chap_id, pane, name, meta_dict)
        self.timeline: list[tuple] = []
        self.index: int = 0
        # focus mode
        self._active_surface = "none"    # "none" | "pane" | "mini"
        self._active_chapter_id = None   # only meaningful for "mini"
        self._applying_depth = 0
        self.after_apply_cb = None  # type: Optional[Callable[[OutlineEditor], None]]
                                    # set this from the ChaptersPage to ping the mini
        self._pending_break_for: dict[int, bool] = {}  # id(editor) -> (line, col)  OR  BREAK_NO_POS
        self._last_typing_time = {}
        self._nav_override_cursor = {}   # { editor: (line, col) }

    def _after_apply_maybe(self, ed):
        cb = getattr(self, "after_apply_cb", None)
        if cb and ed is not None:
            print("AFTER_APPLY firing cb on", id(ed))
            cb(ed)

    def _truncate_future(self):
        if self.index < len(self.timeline):
            del self.timeline[self.index:]

    def on_editor_text_changed(self, editor: "OutlineEditor"):
        # ignore programmatic changes / during apply
        if self.is_applying() or getattr(editor, "_suppress_text_undo_event", False):
            return
        if not self.timeline or self.timeline[-1][0] != "T" or self.timeline[-1][2] is not editor:
            return
        after_text = editor.toPlainText()
        after_pos  = editor.get_line_col()
        # replace AFTER in-place (no index change)
        kind, cid, ed, b_text, b_pos, _, _, cur_run = self.timeline[-1]
        self.timeline[-1] = (kind, cid, ed, b_text, b_pos, after_text, after_pos, cur_run)

    def _pane_for_editor(self, ed):
        # strict identity; no guessing
        for p in self.page.panes:
            if p.editor is ed:
                return p
        raise RuntimeError("No pane found for editor")

    def _chapter_id_for_editor(self, ed) -> int:
        return self._pane_for_editor(ed).chapter.id

    def _chapter_id_for_pane(self, pane) -> int:
        return pane.chapter.id

    def set_nav_cursor_for(self, editor, pos):
        self._nav_override_cursor[editor] = tuple(pos)

    def on_nav_commit(self, editor, ln, col):
        self._pending_break_for[id(editor)] = (int(ln), int(col))
        # optional trace
        print("ARM break(pos)", (ln, col), "for", id(editor))

    def force_break_next_text(self, editor):
        """Request that the next register_text(editor) start a fresh step."""
        # used for paste/cut/enter/bulk delete; no caret pos
        self._pending_break_for[id(editor)] = BREAK_NO_POS
        print("ARM break(no-pos) for", id(editor))

    def _consume_break_flag(self, editor):
        token = self._pending_break_for.pop(id(editor), None)
        if token is BREAK_NO_POS:
            print("UUC: consumed BREAK(no-pos) for", id(editor))
            return None, True  # (pos, forced)
        if isinstance(token, tuple):
            print("UUC: consumed BREAK(pos)", token, "for", id(editor))
            return token, True
        return None, False

    def live_update_after(self, editor):
        # Don’t update during undo/redo application
        if self.is_applying():
            return
        # Only if the last timeline item is a T for this editor
        if not self.timeline or self.timeline[-1][0] != "T" or self.timeline[-1][2] is not editor:
            return
        kind, cid, ed, b_text, b_pos, _, _, _ = self.timeline[-1]
        after_text = editor.toPlainText()
        after_pos  = editor.get_line_col()
        # Write back the same tuple shape you use elsewhere (incl. run_id if present)
        _,_,_,_,_, old_after_text, old_after_pos, run_id = self.timeline[-1]
        if after_text != old_after_text or after_pos != old_after_pos:
            self.timeline[-1] = ("T", cid, ed, b_text, b_pos, after_text, after_pos, run_id)

    # called when ANY OutlineEditor adds a doc undo cmd (typing/coalesced)
    def register_text(self, editor: "OutlineEditor"):
        print("UUC register_text: pane_ed", id(editor),
            "forceBreak", bool(self._pending_break_for.get(editor, False)),
            "char", repr(getattr(editor, "_last_typed_char", "")),
            "paneFlag", bool(getattr(editor, "_force_new_text_step", False)))

        # if we've undone partway, drop the future
        self._truncate_future()

        # ignore programmatic changes from set_lines / set_text_and_cursor
        if getattr(editor, "_suppress_text_undo_event", False):
            print("suppress_text_undo_event active; ignoring register_text")
            return

        pend = getattr(editor, "_pending_nav_before", None)
        if pend is not None:
            before_text, before_pos = pend
            editor._pending_nav_before = None
            print("UUC: consumed pending BEFORE for", id(editor), before_pos)
        else:
            before_text = editor._last_text_snapshot_text
            before_pos  = editor._last_text_snapshot_cursor

        after_text = editor.toPlainText()
        after_pos  = editor.get_line_col()

        # print("REG T before", before_pos, "after", after_pos, "paneNow", editor.get_line_col())
        print("UUC reg_text:", "pane_ed", id(editor),
            "force", getattr(editor, "_force_new_text_step", False))

        # —— Hard boundary 1 (optional but typical): pause between typings breaks coalesce (e.g., 900 ms)
        now = time.monotonic()
        last_t = self._last_typing_time.get(editor, 0.0)
        paused_break = (now - last_t) > COALESCE_TIME_THRESHOLD
        # ensure dict exists
        self._last_typing_time[editor] = now

        forced = False
        if getattr(editor, "_force_new_text_step", False):
            forced = True
            editor._force_new_text_step = False
        print("REGISTER BEFORE", id(editor), before_pos, "AFTER", after_pos, "forced?", forced)

        # 1) consume nav break if any, and use its caret as the BEFORE position
        nav_before, forced = self._consume_break_flag(editor)
        print(f"UUC reg_text: nav_before={nav_before}")
        if nav_before is not None:
            # keep BEFORE text from snapshot, but fix the caret to the clicked/arrows position
            before_pos = nav_before

        # no-op (e.g., cursor-only move)
        if after_text == before_text and after_pos == before_pos:
            editor._last_text_snapshot_cursor = after_pos
            return

        # —— Hard boundary 2: editor asked to break coalescing (Enter, paste, cut, block-delete, mid-word type, nav-before-typing)
        # one-shot break (clicks, pane flag)
        force_break = (
            nav_before
            or forced
            or paused_break
        )

        print("UUC register_text: pane_ed", id(editor),
            "forceBreak", force_break,
            "char", repr(getattr(editor, "_last_typed_char", "")))

        # Use click-derived caret as the 'before' on the first char after a forced break
        if force_break:
            nav_pos = self._nav_override_cursor.pop(editor, None)
            if nav_pos is not None:
                # prefer the click-derived caret as the 'before' cursor
                before_pos = nav_pos

        # Coalesce only if:
        #   - last step is T for this editor
        #   - caret hasn’t navigated (epoch unchanged)
        #   - too much time hasn't passed

        cur_run = getattr(editor, "_typing_run_id", 0)
        last_run = self.timeline[-1] if self.timeline else None
        last_run_id = _get_run_id(self.timeline[-1], default=None) if self.timeline else None
        print("last_run", last_run)
        same_ed = last_run is not None and last_run[T_KIND] == "T" and last_run[T_EDITOR] is editor

        # Coalesce only if: same editor, same run, NOT force-broken
        can_coalesce = (same_ed and (last_run_id == cur_run) and not force_break)

        cid = self._chapter_id_for_editor(editor)
        if can_coalesce:
            _, old_cid, ed, b_text, b_pos, _, _, _ = self.timeline[-1]
            if old_cid != cid:
                raise RuntimeError("chapter_id mismatch in coalesced step")
            self.timeline[-1] = ("T", cid, ed, b_text, b_pos, after_text, after_pos, cur_run)
            # index unchanged
            print("UUC +T COALESCE idx", self.index, "cid", cid)
            print(f"coalesce BEFORE: |{b_pos}| AFTER: |{after_pos}|")
        else:
            self.timeline.append(("T", cid, editor, before_text, before_pos, after_text, after_pos, cur_run))
            self.index = len(self.timeline)
            print("UUC +T NEW idx→", self.index, "cid", cid)
            print(f"New step: before: |{before_pos}| after: |{after_pos}|")

        # refresh “after” snapshot for the next T and clear the editor-local one-shot
        editor._last_text_snapshot_text   = after_text
        editor._last_text_snapshot_cursor = after_pos

    # called right after pushing ANY structural command to QUndoStack
    def register_structural(self, pane, kind: str, meta: dict | None = None):
        self._truncate_future()
        cid = self._chapter_id_for_pane(pane)
        self.timeline.append(("S", cid, pane, kind, meta or {}))
        self.index = len(self.timeline)
        print("UUC +S idx→", self.index, "cid", cid, "pane_ed", id(pane.editor), kind, meta)

    def set_active_surface_pane(self):
        self._active_surface = "pane"
        self._active_chapter_id = None
        print("UUC (set_active_surface_pane): mode ->", self._active_surface, " mini_cid?", self._active_chapter_id)

    def set_active_surface_mini(self, chap_id: int):
        self._active_surface = "mini"
        self._active_chapter_id = int(chap_id)
        print("UUC (set_active_surface_mini): mode ->", self._active_surface, " mini_cid?", self._active_chapter_id)

    def set_active_surface_none(self):
        self._active_surface = "none"
        self._active_chapter_id = None
        print("UUC (set_active_surface_none): mode ->", self._active_surface)

    # def _after_apply(self, editor):
    #     """Invoke page callback on the next tick so the UI is ready to paint."""
    #     cb = self.after_apply_cb
    #     print("AFTER_APPLY firing cb on", id(editor))
    #     if not cb:
    #         print("after_apply_cb not set")
    #         return
    #     # invoke on next cycle so textChanged/paint queue is caught up
    #     QtCore.QTimer.singleShot(0, lambda e=editor: cb(e))

    def _apply_text(self, ed, text, pos):
        with self._applying():  # raises/lowers _applying_depth
            ed._suppress_text_undo_event = True
            ed.blockSignals(True)
            try:
                line, col = pos
                if hasattr(ed, "set_text_and_cursor"):
                    print("apply_text: set_text_and_cursor", id(ed), "to", pos)
                    ed.set_text_and_cursor(text, line, col)
                else:
                    ed.setPlainText(text); ed.set_line_col(line, col)
            finally:
                ed.blockSignals(False)
                ed._suppress_text_undo_event = False
            # sync editor snapshots to AFTER
            ed._last_text_snapshot_text   = ed.toPlainText()
            ed._last_text_snapshot_cursor = ed.get_line_col()

        self._after_apply_maybe(ed)

    def _refresh_all_snapshots(self):
        for p in self.page.panes:
            ed = p.editor
            ed._last_text_snapshot_text   = ed.toPlainText()
            ed._last_text_snapshot_cursor = ed.get_line_col()

    def _undo_one_step(self):
        step = self.timeline[self.index-1]
        kind = step[0]
        if kind == "T":
            _, cid, ed, before_text, before_pos, after_text, after_pos, _ = step
            print("applying T", ed, before_text, before_pos)
            print(f"before (_undo_one_step): |{before_pos}|")
            self._apply_text(ed, before_text, before_pos)
            self.index -= 1
            return
        if kind == "S":
            # drive QUndoStack
            self.page.undoStack.undo()
            self.index -= 1
            self._refresh_all_snapshots()
            cmd = self.page.undoStack.command(self.page.undoStack.index() - 1)  # or wherever you pull the command from
            focus_ed = getattr(cmd, "focus_editor", None)
            self._after_apply_maybe(focus_ed)
            return

    def _redo_one_step(self):
        step = self.timeline[self.index]
        kind = step[0]
        if kind == "T":
            _, cid, ed, before_text, before_pos, after_text, after_pos, _ = step
            self._apply_text(ed, after_text, after_pos)
            self.index += 1
            return
        if kind == "S":
            self.page.undoStack.redo()
            self.index += 1
            self._refresh_all_snapshots()
            cmd = self.page.undoStack.command(self.page.undoStack.index() - 1)  # or wherever you pull the command from
            focus_ed = getattr(cmd, "focus_editor", None)
            self._after_apply_maybe(focus_ed)
            return

    def _peek_prev(self):
        return self.timeline[self.index-1] if self.index > 0 else None

    def _peek_next(self):
        return self.timeline[self.index] if self.index < len(self.timeline) else None

    def _step_pane(self, step):
        kind = step[0]
        if kind == "T":
            _, ed, *_ = step
            # find pane by editor identity
            for p in getattr(self.page, "panes", []):
                if p.editor is ed:
                    return p
            return None
        elif kind == "S":
            _, pane, *_ = step
            return pane
        return None

    def undo(self):
        with self._applying():
            step = self._peek_prev()
            print("UUC: UNDO called; idx", self.index, "active_surface", getattr(self, "_active_surface", None))
            # print("UUC undo: idx", self.index, "mode", getattr(self, "_active_surface", None),
            #     "step", (step[0], step[1]) if step else None)
            if not step:
                return
            cid = step[1]

            # if self._active_surface == "mini" and cid != self._active_chapter_id:
            #     self._nudge_open_full(direction="undo", next_cid=cid)
            #     return

            return self._undo_one_step()

    def redo(self):
        with self._applying():
            step = self._peek_next()
            print("UUC: REDO called; idx", self.index, "active_surface", getattr(self, "_active_surface", None))
            # print("UUC redo: idx", self.index, "mode", getattr(self, "_active_surface", None),
            #     "step", (step[0], step[1]) if step else None)
            if not step:
                return
            cid = step[1]

            # if self._active_surface == "mini" and cid != self._active_chapter_id:
            #     self._nudge_open_full(direction="redo", next_cid=cid)
            #     return

            return self._redo_one_step()

    def _chapter_label(self, cid: int) -> str:
        # Map id → row → label using your workspace/model
        row = self.page.row_for_chapter_id(cid)
        if row < 0:
            return f"Chapter {cid}"
        ch = self.page.model._chapters[row]
        # Use your shared display helper if present
        return chapter_display_label(row, ch.title)

    def _nudge_open_full(self, direction: str, next_cid: int):
        label = self._chapter_label(next_cid)
        QtWidgets.QMessageBox.information(
            self.page,  # parent widget
            "Undo History Spans Chapters",
            f"The next {direction} step belongs to {label}.\n"
            "Open the Full Outline, or switch to that chapter to continue."
        )

    # --- applying guard helpers ---
    def is_applying(self) -> bool:
        return self._applying_depth > 0

    @contextlib.contextmanager
    def _applying(self):
        self._applying_depth += 1
        try:
            yield
        finally:
            self._applying_depth -= 1
