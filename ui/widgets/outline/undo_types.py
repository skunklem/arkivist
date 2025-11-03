from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Tuple, Any, List, Iterable, Union, Dict
import weakref

Pos = Tuple[int, int]  # (line, col)

@dataclass
class TimelineStep:
    """
    One undo/redo step. Never stores live Qt objects.
    - Text step: kind='T'
        fields: cid, before_text, before_pos, after_text, after_pos, run_id
    - Structural step: kind='S'
        fields: cid, s_kind, payload (dict); before/after text/pos empty
      (payload can hold anything you already store, e.g. for delete/restore)
    """
    # Core
    kind: str                      # "T" or "S"
    cid: int                       # chapter id
    # Text fields (only for kind == "T")
    before_text: Optional[str] = None
    before_pos: Optional[Pos] = None
    after_text: Optional[str] = None
    after_pos: Optional[Pos] = None
    run_id: Optional[int] = None
    # Structural fields (only for kind == "S")
    s_kind: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

    # weak backref to controller, set at creation or when appended
    _controller_ref: Optional["weakref.ReferenceType"] = field(default=None, repr=False, compare=False)

    # ---- factories ---------------------------------------------------------
    @classmethod
    def text(cls, cid: int, b_text: str, b_pos: Optional[Pos],
             a_text: str, a_pos: Optional[Pos], run_id: Optional[int]) -> "TimelineStep":
        return cls(kind='T', cid=cid,
                   before_text=b_text, before_pos=b_pos,
                   after_text=a_text, after_pos=a_pos,
                   run_id=run_id)

    @classmethod
    def structural(cls, cid: int, s_kind: str, payload: dict) -> "TimelineStep":
        return cls(kind='S', cid=cid, s_kind=s_kind, payload=dict(payload or {}))

    # ---- helpers -----------------------------------------------------------
    @property
    def is_text(self) -> bool:
        return self.kind == 'T'

    @property
    def is_structural(self) -> bool:
        return self.kind == 'S'

    def bind_controller(self, controller: Any) -> None:
        """Attach a weak reference to the controller so pane/editor lookup is safe."""
        self._controller_ref = weakref.ref(controller)

    @property
    def controller(self) -> Optional[Any]:
        return self._controller_ref() if self._controller_ref else None

    @property
    def pane(self):
        """
        Late-resolve the pane for this step’s chapter.
        Safe if panes were deleted; returns None if closed or unavailable.
        """
        ctl = self.controller
        if not ctl:
            return None
        # Prefer a direct helper if you have it:
        page = getattr(ctl, "page", None)
        if page and hasattr(page, "pane_for_cid"):
            try:
                return page.pane_for_cid(self.cid)
            except Exception:
                return None
        # Fallback: editor → pane
        if page and hasattr(page, "editor_for_chapter_id"):
            ed = page.editor_for_chapter_id(self.cid, ensure_open=False)
            if ed is not None:
                return getattr(ed, "parent", lambda: None)()
        return None

    @property
    def editor(self):
        """Late-resolve the editor; None if not open."""
        ctl = self.controller
        if not ctl:
            return None
        page = getattr(ctl, "page", None)
        if page and hasattr(page, "editor_for_chapter_id"):
            try:
                return page.editor_for_chapter_id(self.cid, ensure_open=False)
            except Exception:
                return None
        return None

    # --- conveniences ---
    @property
    def is_text(self) -> bool:
        return self.kind == "T"

    def can_coalesce_with(self, cid: int, run_id: int) -> bool:
        """Only allow coalescing within the same cid + run."""
        return self.is_text and self.cid == cid and self.run_id == run_id

    def update_after(self, text: str = None, pos: Pos = None, run_id: int = None) -> "TimelineStep":
        """Update 'after' side in place (used by coalescing)."""
        print("  UPDATE after:", pos)
        if text is not None:
            self.after_text = text
        if pos is not None:
            self.after_pos = pos
        if run_id is not None:
            self.run_id = run_id

    def brief(self) -> str:
        if self.is_text:
            return f"T(cid={self.cid}, run={self.run_id}, bpos={self.before_pos}, apos={self.after_pos})"
        return f"S(cid={self.cid}, kind={self.s_kind}, payload={list((self.payload or {}).keys())})"

    def __repr__(self) -> str:
        return f"<TimelineStep {self.brief()}>"


class UndoTimeline:
    """
    Small wrapper over a list of TimelineStep for safety + convenience.
    Keeps controller bound on append. Provides familiar list-ish API.
    """
    def __init__(self):
        self._items: list[TimelineStep] = []

    # basic sequence protocol
    def __len__(self): return len(self._items)
    def __getitem__(self, i): return self._items[i]
    def __iter__(self): return iter(self._items)

    @property
    def last(self) -> Optional[TimelineStep]:
        return self._items[-1] if self._items else None

    def append(self, step: TimelineStep) -> None:
        self._items.append(step)

    def truncate(self, n: int) -> None:
        del self._items[n:]

    def replace_last(self, step: TimelineStep) -> None:
        if not self._items:
            return
        self._items[-1] = step

    # coalescing convenience: returns True if the last step was updated
    def try_coalesce_text(self, cid: int, run_id: int,
                          atxt: str, apos: Pos) -> bool:
        st = self.last
        if st and st.can_coalesce_with(cid, run_id):
            st.update_after(atxt, apos)
            return True
        return False
