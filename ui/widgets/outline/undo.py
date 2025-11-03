from __future__ import annotations

import contextlib
import json
import time
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary
from PySide6 import QtCore, QtWidgets, QtGui

from ui.widgets.helpers import chapter_display_label
from ui.widgets.outline.undo_types import TimelineStep, UndoTimeline

if TYPE_CHECKING:
    from .editor import OutlineEditor
    from .page import ChaptersPage
    from .page import OutlineWorkspace

BREAK_NO_POS = object()
COALESCE_TIME_THRESHOLD = 1.5 # seconds

class UnifiedUndoController(QtCore.QObject):
    """
    Single timeline across outline editors:
      - 'T' entries store before/after snapshots for a specific OutlineEditor
      - 'S' entries trigger the page's QUndoStack (structural)
    """
    def __init__(self, page: "ChaptersPage", workspace: "OutlineWorkspace"):
        super().__init__(page)
        self.page = page
        self.workspace = workspace
        # entries: (, chap_id, before_text, (before_line, before_col), after_text, (after_line, after_col))
        #       or ("S", chap_id, pane, name, meta_dict)
        self.index: int = 0
        self.timeline = UndoTimeline()
        # editor binding and snapshots
        self._cid_by_editor = {}        # editor -> cid
        self._ed_by_cid     = {}        # cid -> weakref(editor) or a resolver the page can supply
        # snapshots (controller-owned, per chapter id)
        self._snap_text: dict[int, str] = {}
        self._snap_pos:  dict[int, tuple[int,int]] = {}
        # coalescing/run bookkeeping per cid
        self._last_run_id_by_cid: dict[int, int] = {}
        # focus mode
        self._active_surface = "none"    # "none" | "pane" | "mini"
        self._active_chapter_id = None   # only meaningful for "mini"
        self._applying_depth = 0
        self.after_apply_cb = None  # type: Optional[Callable[[OutlineEditor], None]]
                                    # set this from the ChaptersPage to ping the mini
        # pending forced breaks: either BREAK_NO_POS or (line, col)
        self._pending_break_for: dict[int, object|tuple[int,int]] = {}
        self._last_typing_time = {}
        self._nav_override_cursor = {}   # { editor: (line, col) }

    def _cid(self, editor):  # helper
        return self._cid_by_editor.get(editor)

    def _after_apply_maybe(self, ed):
        cb = getattr(self, "after_apply_cb", None)
        if cb and ed is not None:
            print("AFTER_APPLY firing cb on", id(ed))
            cb(ed)

        # persist outline for the editor we just applied to
        cid = self._cid_for_editor(ed) if ed else None
        if cid:
            self.page.flush_outline_for_cid(cid)

    def _truncate_future(self):
        """Drop all steps after current index (standard undo-stack semantics)."""
        if self.index < len(self.timeline):
            self.timeline.truncate(self.index)

    def on_editor_text_changed(self, editor):
        # ignore during undo/redo apply or while mirroring
        if self.is_applying() or getattr(editor, "_suppress_text_undo_event", False):
            return
        print("UUC on_editor_text_changed: editor", id(editor))
        self.register_text(editor)

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

    def _cid_for_editor(self, ed) -> int:
        return self._chapter_id_for_editor(ed)

    def _editor_for_cid(self, cid: int, ensure_open: bool = False):
        # delegate to the page/workspace (see §2)
        page = self.page   # assume controller has .page set
        ed = page.editor_for_chapter_id(cid, ensure_open=ensure_open)
        return ed  # may be None if ensure_open=False and pane closed

    def set_nav_cursor_for(self, editor, pos):
        self._nav_override_cursor[editor] = tuple(pos)

    def on_nav_commit(self, editor, ln, col):
        cid = self._cid(editor)
        if cid is None:
            return
        # 1) make *next* text step break here
        self._pending_break_for[cid] = (int(ln), int(col))
        # 2) keep the caret-only snapshot current so FIRST text step’s BEFORE is right
        self._snap_pos[cid] = (int(ln), int(col))
        print(f"ARM break(pos) {(ln,col)} for cid {cid}; snap_pos updated")

    def force_break_next_text(self, editor):
        """Request that the next register_text(editor) start a fresh step."""
        # used for paste/cut/enter/bulk delete; no caret pos
        cid = self._cid(editor)
        if cid is not None:
            self._pending_break_for[cid] = BREAK_NO_POS
            print(f"ARM break(no-pos) for cid {cid}")

    def _consume_break_flag_cid(self, cid: int) -> tuple[tuple[int,int]|None, bool]:
        tok = self._pending_break_for.pop(cid, None)
        if tok is BREAK_NO_POS:
            print("consumed BREAK(no-pos) for cid", cid)
            return None, True
        if isinstance(tok, tuple):
            print("consumed BREAK(pos)", tok, "for cid", cid)
            return tok, True
        return None, False

    # def live_update_after(self, editor):
    #     # Don’t update during undo/redo application
    #     if self.is_applying():
    #         return
    #     # Only if the last timeline item is a T for this editor
    #     if not self.timeline:
    #         return
    #     kind, cid = self.timeline.last.kind, self.timeline.last.cid
    #     last_editor = self._editor_for_cid(cid) if self.timeline else None
    #     if kind != "T" or last_editor is not editor:
    #         return
    #     print("T:",self.timeline.last)
    #     b_text, b_pos, old_after_text, old_after_pos, run_id = [self.timeline.last.before_text, self.timeline.last.before_pos, self.timeline.last.after_text, self.timeline.last.after_pos, self.timeline.last.run_id]
    #     after_text = editor.toPlainText()
    #     after_pos  = editor.get_line_col()
    #     # Write back the same tuple shape you use elsewhere (incl. run_id if present)
    #     if after_text != old_after_text or after_pos != old_after_pos:
    #         self.timeline.last = ("T", cid, b_text, b_pos, after_text, after_pos, run_id)

    def live_update_after(self, cid: int | None = None,
                        text: str | None = None,
                        pos: tuple[int, int] | None = None) -> None:
        # Don’t update during undo/redo application
        if self.is_applying():
            return

        st = self.timeline.last
        if not st:
            return

        # Default to current cid snapshot if not provided
        cid = cid if cid is not None else st.cid

        # If caller didn’t provide overrides, pull fresh from editor snapshots
        if text is None:
            text = self._snap_text.get(cid)
            if text is None:
                ed = self.page.editor_for_chapter_id(cid, ensure_open=False)
                if ed:
                    text = ed.toPlainText()

        if pos is None:
            pos = self._snap_pos.get(cid)
            if pos is None:
                ed = self.page.editor_for_chapter_id(cid, ensure_open=False)
                if ed:
                    pos = ed.get_line_col()

        # Update the last step
        st.update_after(text=text, pos=pos)

        # Optional trace
        print("LIVE after:", st.kind, st.cid, st.after_pos)

    def bind_editor_cid(self, editor, cid):
            self._cid_by_editor[editor] = int(cid)
            # seed initial “before” snapshot
            self._set_snapshots_for_cid(cid, editor.toPlainText(), editor.get_line_col())

    # called when ANY OutlineEditor adds a doc undo cmd (typing/coalesced)
    def register_text(self, editor) -> bool:
        """
        Called on pane editor textChanged (not during apply/mirror).
        Builds a text step or coalesces into the last one.
        Returns True if a new step was appended, False if coalesced or nothing to do.
        """
        if self.is_applying():
            return False

        # programmatic updates (mini applying pane text) may be suppressed elsewhere;
        # here we always read the current editor state.
        cid = self._cid(editor)
        if cid is None:
            print("REGISTER - no editor")
            return
        run_id = getattr(editor, "_typing_run_id", 0)

        print("REGISTER", "cid", cid, "run", getattr(editor,"_typing_run_id",0))
        print("  BEFORE from cache:", len(self._snap_text.get(cid,"")), self._snap_pos.get(cid))

        # AFTER snapshot from the editor
        after_text = editor.toPlainText()
        after_pos  = editor.get_line_col()
        print("  AFTER from editor:", len(after_text), after_pos)

        # BEFORE comes from controller’s snapshots (seeded at bind, advanced after each register/apply)
        before_text = self._snap_text.get(cid, after_text)
        before_pos  = self._snap_pos.get(cid,  after_pos)

        # Respect any pending break (from nav/word-boundary/etc.)
        nav_pos, forced = self._consume_break_flag_cid(cid)
        if nav_pos is not None:
            # override BEFORE cursor for this NEW step
            before_pos = nav_pos

        # No-op guard (text and caret didn’t change in a meaningful way)
        if (after_text == before_text) and (after_pos == before_pos):
            # Still refresh controller snapshots so future steps have the right baseline
            self._set_snapshots_for_cid(cid, after_text, after_pos)
            return

        # Coalesce if allowed (same cid & run_id and NOT forced)
        if not forced and self.timeline.try_coalesce_text(cid, run_id, after_text, after_pos):
            # Advance controller snapshots to reflect latest state
            print("  COALESCE into last step, after:", after_pos)
            self._set_snapshots_for_cid(cid, after_text, after_pos)
            return False

        # Otherwise, start a fresh timeline step
        # drop “future” if we undid partway
        self._truncate_future()
        # coalesce: same cid + same run_id + not forced
        step = TimelineStep.text(
            cid=cid,
            b_text=before_text,
            b_pos=before_pos,
            a_text=after_text,
            a_pos=after_pos,
            run_id=run_id
        )
        self.timeline.append(step)
        self.index = len(self.timeline)

        # AFTER we’ve decided the step, advance controller snapshots to AFTER
        self._set_snapshots_for_cid(cid, after_text, after_pos)

    # called right after pushing ANY structural command to QUndoStack
    def register_structural(self, cid, kind: str, meta: dict | None = None):
        self._truncate_future()
        self.timeline.append(TimelineStep.structural(cid, kind, meta or {}))
        self.index = len(self.timeline)
        print("UUC +S idx→", self.index, "cid", cid, kind, meta)

    def _set_snapshots_for_cid(self, cid, text, pos):
        self._snap_text[cid] = text
        self._snap_pos[cid]  = pos

    def _focus_editor_for_step(self, step: TimelineStep, undo: bool) -> None:
        """
        Focus the correct editor for this step and place the caret where the user expects.
        Also sets active surface + mirrors the mini.
        """
        # 1) Decide cid + pos
        cid = step.cid
        pos = None

        if step.is_text:
            # Text steps carry precise caret before/after
            pos = step.before_pos if undo else step.after_pos
        elif step.is_structural:
            # Structural steps rely on payload for precise intent
            payload = step.payload or {}
            if undo:
                # Prefer explicit “caret_before” (with optional *_cid overrides)
                cid = payload.get("caret_before_cid", cid)
                pos = payload.get("caret_before")
            else:
                cid = payload.get("caret_after_cid", cid)
                pos = payload.get("caret_after")

        print(f"FOCUS→ cid={cid} pos={pos} undo={undo} fw_before={type(QtWidgets.QApplication.focusWidget()).__name__}")

        # 2) Focus/open + place caret
        # (Let focus_chapter handle “open if needed”, scrolling, expanding pane, etc.)
        # If pos is None, focus_chapter will just focus the editor.
        self.page._suppress_focus = False
        self.page.workspace.focus_chapter(cid, pos)  # opens pane if needed, sets caret if pos, gives focus

        # keep controller surface in sync
        self.set_active_surface_pane()

        # 4) Mirror the mini if it’s showing this chapter
        mini = self.page.single_mini
        if mini._chap_id == cid:
            mini._mirror_from_pane()

    def _apply_text_step(self, step, undo: bool):
        cid = step.cid
        ed = self._editor_for_cid(cid, ensure_open=True)  # open if needed
        if ed is None:
            # chapter genuinely missing? nothing to do
            return

        if undo:
            text, pos = step.before_text, step.before_pos
        else:
            print("APPLY text step:", step)
            text, pos = step.after_text, step.after_pos

        ed._suppress_text_undo_event = True
        ed.blockSignals(True)
        try:
            line, col = pos
            ed.set_text_and_cursor(text, line, col)
        finally:
            ed.blockSignals(False)
            ed._suppress_text_undo_event = False


        # persist outline for the editor we just applied to
        self.page.flush_outline_for_cid(cid)

        # refresh controller snapshots to what we just painted
        self._set_snapshots_for_cid(cid, text, pos)

        self._focus_editor_for_step(step, undo=undo)

        # mini mirror etc.
        self.page._after_apply_for_editor(ed)

    def _struct_unpack(self, step):
        # supports ("S", cid, name, payload) OR ("S", cid, pane_or_None, name, payload)
        if len(step) == 4:
            _, cid, name, payload = step
            pane = None
        elif len(step) == 5:
            _, cid, pane, name, payload = step
        else:
            raise RuntimeError("Bad structural step shape")
        return cid, pane, name, payload

    def _apply_structural_step(self, step: TimelineStep, undo: bool):
        """
        Apply a structural step, preserving your focus rules and post-apply hooks.
        """
        pay = step.payload or {}

        if step.s_kind == "delete_chapter":
            # 1) Perform delete / undelete via DB helper
            self._apply_delete_step(step.cid, pay, undo=undo, db=self.page.workspace.db)

            # 2) Focus target based on payload
            focus_cid = pay.get("caret_before_cid" if undo else "caret_after_cid", step.cid)
            focus_pos = pay.get("caret_before"     if undo else "caret_after",     None)

            if focus_cid is not None:
                # Ensure pane exists and place caret if provided
                self.page.workspace.focus_chapter(focus_cid, focus_pos, give_focus=True)
                focus_ed = self.page.editor_for_chapter_id(focus_cid, ensure_open=False)
            else:
                focus_ed = None

            # 3) Refresh & post-apply
            self._refresh_all_snapshots()
            self.page._after_apply_for_editor(focus_ed)
            return

        # For QUndoStack-backed structural cmds:
        if undo:
            self.page.undoStack.undo()
        else:
            self.page.undoStack.redo()

        # Prefer command-provided focus editor if present
        try:
            cmd = self.page.undoStack.command(self.page.undoStack.index() - 1)
        except Exception:
            cmd = None

        focus_ed_cmd = getattr(cmd, "focus_editor", None)

        # Fallback from payload
        focus_cid = pay.get("caret_before_cid" if undo else "caret_after_cid", step.cid)
        focus_ed_fallback = self.page.editor_for_chapter_id(focus_cid, ensure_open=False)

        self._refresh_all_snapshots()
        self.page._after_apply_for_editor(focus_ed_cmd or focus_ed_fallback)

    def _snapshot_delete_payload(self, cid: int) -> dict:
        db  = self.page.workspace.db
        pid = self.page.workspace.project_id
        bid = self.page.workspace.book_id
        pos = self.page.model.row_for_chapter_id(cid) if hasattr(self.page, "model") else 0
        caret = self._snap_pos.get(cid, (0, 0))
        active = self.page.current_outline_version_name_for(cid)
        return {
            "cid": cid,
            "project_id": pid,
            "book_id": bid,
            "position": pos,
            "caret_before": caret,
            "active_version": active,
        }

    def record_delete_chapter(self, cid: int, db):
        # capture payload to resurrect on undo (only if deleting from outline)
        payload = self._snapshot_delete_payload(cid)
        # push structural step: ('S', cid, pane, 'delete_chapter', payload)
        self.register_structural(cid, "delete_chapter", payload)
        # apply delete now
        self._apply_delete_step(cid, payload, undo=False, db=db)
        self._after_apply_maybe(None)

    def _apply_delete_step(self, cid: int, payload: dict, undo: bool, db):
        ws  = self.page.workspace
        db  = ws.db
        pid = payload["project_id"]
        bid = payload["book_id"]
        pos = payload["position"]
        ln, col = payload.get("caret_before", (0, 0))
        active_v = payload.get("active_version")

        deleted_cid = cid
        row = ws.page.row_for_chapter_id(deleted_cid)
        neighbor_row = min(row, ws.model.rowCount()-1)  # or row+1 if exists else row-1
        neighbor_cid = ws.model.chapter_id_for_row(neighbor_row) if ws.model.rowCount() else None

        # ensure all outline editors are saved
        for p in ws.page.panes:
            ws.page.flush_outline_for_cid(p.chapter.id)
        self.page.flush_all_outline_versions()

        if undo:

            # resurrect
            db.chapter_undelete(cid)
            # db.chapter_update(cid, title=payload["title"], content_md=payload["content_md"])
            db.chapter_move_to_index(pid, bid, cid, pos)
            self.page.workspace.load_from_db(db, pid, bid, focus=(cid, (ln, col)))
            print("Chapter undeleted")

            # Optionally re-assert active version
            if active_v:
                self.page.set_current_outline_version_name_for(cid, active_v)

            # # Focus and caret restore
            # ed = self._editor_for_cid(cid, ensure_open=True)
            # if ed:
            #     ed.clamp_and_place_cursor(int(ln), int(col))
            #     ed.setFocus()
            #     # refresh controller snapshots to the resurrected state
            #     self._set_snapshots_for_cid(cid, payload["text"], (ln, col))
            # self.page._after_apply_for_editor(ed)

        else:
            # delete
            # Persist active version name and flush lines before removing
            if active_v:
                self.page.set_current_outline_version_name_for(cid, active_v)
            db.chapter_soft_delete(cid)
            # renumber remaining in that book
            db.chapter_compact_positions(pid, bid)
            self.page.close_panes_for_deleted_chapter(cid)
            ws.load_from_db(db, pid, bid, focus=(neighbor_cid, (0,0)))
            self.page._after_apply_for_editor(None)  # let your hook clear mini if needed
            # choose where focus should go now

    def _purge_for_cid(self, cid: int):
        self._pending_break_for.pop(cid, None)
        self._last_run_id_by_cid.pop(cid, None)
        self._snap_text.pop(cid, None)
        self._snap_pos.pop(cid, None)

    def active_surface(self) -> str:
        return self._active_surface

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

    # def _apply_text(self, ed, text, pos):
    #     with self._applying():  # raises/lowers _applying_depth
    #         ed._suppress_text_undo_event = True
    #         ed.blockSignals(True)
    #         try:
    #             line, col = pos
    #             if hasattr(ed, "set_text_and_cursor"):
    #                 print("apply_text: set_text_and_cursor", id(ed), "to", pos)
    #                 ed.set_text_and_cursor(text, line, col)
    #             else:
    #                 ed.setPlainText(text); ed.set_line_col(line, col)
    #         finally:
    #             ed.blockSignals(False)
    #             ed._suppress_text_undo_event = False
    #         # sync editor snapshots to AFTER
    #         ed._last_text_snapshot_text   = ed.toPlainText()
    #         ed._last_text_snapshot_cursor = ed.get_line_col()

    #     self._after_apply_maybe(ed)

    def _refresh_all_snapshots(self):
        for p in self.page.panes:
            ed = p.editor
            cid = self._cid(ed)
            self._set_snapshots_for_cid(cid, ed.toPlainText(), ed.get_line_col())

    def _undo_one_step(self):
        step = self.timeline[self.index-1]
        print("in _undo_one_step, step:", step)
        if step.is_text:
            self._apply_text_step(step, undo=True)
        else:
            self._apply_structural_step(step, undo=True)

        self.index -= 1

    def _redo_one_step(self):
        step = self.timeline[self.index]
        print("REDO ONE:", step)

        if step.is_text:
            self._apply_text_step(step, undo=False)
        else:
            self._apply_structural_step(step, undo=False)

        self.index += 1

    def _peek_prev(self):
        return self.timeline[self.index-1] if self.index > 0 else None

    def _peek_next(self):
        return self.timeline[self.index] if self.index < len(self.timeline) else None

    def undo(self):
        with self._applying():
            step = self._peek_prev()
            print("UUC: UNDO called; idx", self.index, "active_surface", getattr(self, "_active_surface", None))
            if not step:
                print("UUC undo: no step")
                return

            # cid = step.cid
            # if self._active_surface == "mini" and cid != self._active_chapter_id:
            #     self._nudge_open_full(direction="undo", next_cid=cid)
            #     return

            return self._undo_one_step()

    def redo(self):
        with self._applying():
            step = self._peek_next()
            print("UUC: REDO called; idx", self.index, "active_surface", getattr(self, "_active_surface", None))
            if not step:
                return

            # cid = step.cid
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
