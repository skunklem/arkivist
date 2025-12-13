# ui/widgets/doc_page.py

from __future__ import annotations

from typing import Optional, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QTimer, QEvent, Signal
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtCore import QUrl

from ui.widgets.rich_editor_pane import RichEditorPane
from ui.widgets.common import _HoverCardPopup, StatusLine
from ui.widgets.world_detail import _EditorClickFilter


class DocPage(QtWidgets.QWidget):
    """
    Represents a single long-form document (chapter, note, etc.).

    MVP: supports docType="chapter" and a single RichEditorPane for main text.
    """

    # Could be expanded later (e.g. dirtyChanged, titleChanged)
    saved = Signal(int)  # emit doc_id on successful hard save

    def __init__(
        self,
        app,
        doc_type: str,
        doc_id: int,
        parent: Optional[QtWidgets.QWidget] = None,
        initial_prefs: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.doc_type = doc_type
        self.doc_id = int(doc_id)

        self._dirty = False
        self._current_version_id: int = 0
        self._editor_prefs: Dict[str, Any] = dict(initial_prefs or {
            "showWikilinks": "full",
            "highlightLinksWhileCtrl": True,
            "linkFollowMode": "ctrlClick",
        })

        self._build_ui()
        self._wire_signals()
        self._wire_hovercard()

        self._load_from_db()

    # --- UI -----------------------------------------------------------------

    def _build_ui(self) -> None:
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        # Header: title + status
        header = QtWidgets.QHBoxLayout()
        self.titleLabel = QtWidgets.QLabel("(Untitled)", self)
        self.statusLabel = QtWidgets.QLabel("Viewing", self)
        self.statusLabel.setStyleSheet("color: palette(mid);")

        header.addWidget(self.titleLabel)
        header.addStretch(1)
        header.addWidget(self.statusLabel)

        vbox.addLayout(header)

        # Optional status line (like world detail)
        self.statusLine = StatusLine(self)
        vbox.addWidget(self.statusLine)
        self.statusLine.show_neutral("Loaded")

        # Main editor pane
        self.editorPane = RichEditorPane(self, initial_prefs=self._editor_prefs)
        vbox.addWidget(self.editorPane, 1)

    def _wire_signals(self) -> None:
        self.editorPane.docChanged.connect(self._on_editor_doc_changed)
        self.editorPane.requestSave.connect(self._on_editor_request_save)
        self.editorPane.prefsChanged.connect(self._on_editor_prefs_changed)
        self.editorPane.focusGained.connect(
            lambda: self.statusLine.show_neutral("Editing")
        )
        self.editorPane.focusLost.connect(self._on_editor_focus_lost)

    def _wire_hovercard(self) -> None:
        """Hovercard / link interaction plumbing (like WorldDetailWidget)"""

        # Timer that periodically checks whether the cursor left the editor/hovercard
        self._editor_hover_card = None
        self._editor_hover_poll = QTimer(self)
        self._editor_hover_poll.setInterval(150)
        self._editor_hover_poll.timeout.connect(self._hide_editor_hover_if_outside)

        # Event filter: any click inside the editor hides the hovercard immediately
        self._editor_click_filter = _EditorClickFilter(self._hide_editor_hover_immediate, self)
        if self.editorPane.editor is not None:
            self.editorPane.editor.installEventFilter(self._editor_click_filter)

        # React to wikilink hover / click events from the rich editor
        self.editorPane.linkInteraction.connect(self._on_editor_link_interaction)

    # ------------------------------------------------------------------
    # Hovercard helpers (mirrors WorldDetailWidget behavior)
    # ------------------------------------------------------------------

    def _active_editor_widget(self) -> QtWidgets.QWidget:
        """
        Root widget we treat as the 'editor area' for hovercard hit-testing.
        We want the hovercard to stay open while the cursor is inside this
        widget or one of its children.
        """
        return self.editorPane

    def _hide_editor_hover_immediate(self) -> None:
        """Hide any active hovercard right away and stop polling."""
        self._editor_hover_poll.stop()
        if self._editor_hover_card is not None:
            self._editor_hover_card.hide()
        self._editor_hover_card = None

    def _hide_editor_hover_if_outside(self) -> None:
        """
        Hide the editor hovercard when the pointer is no longer over
        the editor or the card itself.

        Mirrors WorldDetailWidget._hide_editor_hover_if_outside, but uses
        this DocPage's editorPane as the "editor root".
        """
        card = self._editor_hover_card
        if not card or not card.isVisible():
            self._editor_hover_poll.stop()
            return

        pos = QCursor.pos()

        # Keep the card if the pointer is inside the card geometry
        if card.geometry().contains(pos):
            return

        # Keep the card if the pointer is still over the editor (or a child)
        from PySide6 import QtWidgets  # or put at top of file if you prefer
        w = QtWidgets.QApplication.widgetAt(pos)

        def _is_child_of(a: QtWidgets.QWidget, b: QtWidgets.QWidget) -> bool:
            while a:
                if a is b:
                    return True
                a = a.parentWidget()
            return False

        if w is not None and _is_child_of(w, self.editorPane):
            return

        # Otherwise hide and stop polling
        card.hide()
        self._editor_hover_poll.stop()

    def _is_child_of(self, w: QtWidgets.QWidget, root: QtWidgets.QWidget) -> bool:
        """Return True if w is root or a descendant of root."""
        while w is not None:
            if w is root:
                return True
            w = w.parentWidget()
        return False

    def _on_editor_link_interaction(self, payload: dict) -> None:
        """
        Handle wikilink hover/click events from the chapter's RichEditorPane.

        Semantics for chapters:
          - Hover: show a world-item hovercard (like WorldDetail).
          - Click: open the world item in the world panel, but DO NOT:
              * save this chapter
              * change which chapter is showing
        """
        if not payload:
            return

        kind = (payload.get("kind") or "").strip()          # "wikilink", "candidate", "external"
        trigger = (payload.get("trigger") or "").strip()     # "click", "hoverStart", "hoverEnd"
        href = (payload.get("href") or "").strip()

        # World-item wikilinks
        wid = payload.get("worldItemId")
        try:
            wid_int = int(wid) if wid is not None else None
        except (TypeError, ValueError):
            wid_int = None

        # External links
        if kind == "external" or (href and href.startswith("http")):
            if trigger == "click" and href:
                QDesktopServices.openUrl(QUrl(href))
            return

        # For now, we only care about wikilinks for hover/click behavior
        if kind not in ("wikilink", "candidate"):
            return

        # ---------------- Hover start / end ----------------
        if trigger == "hoverStart":
            app = self.app
            if app is None or wid_int is None:
                return

            # Same HTML generator as WorldDetailWidget uses
            # JS normally passes a bare wid; we synthesize the QUrl used by the app.
            url = QUrl(f"world://item/{wid_int}")
            html = app._hover_card_html_for(url)
            if not html:
                return

            if self._editor_hover_card is None:
                self._editor_hover_card = _HoverCardPopup(app)

            global_pos = QCursor.pos()
            self._editor_hover_card.show_card(html, global_pos)
            self._editor_hover_poll.start()
            return

        if trigger == "hoverEnd":
            # We rely on _hide_editor_hover_if_outside + click filter; no-op here.
            return

        # ---------------- Click ----------------
        if trigger == "click" and wid_int is not None:
            # Chapter behavior:
            #   - do NOT save this DocPage here
            #   - do NOT navigate away from this chapter
            #   - DO open the linked world item in the world panel
            self._hide_editor_hover_immediate()

            app = self.app
            if app is None:
                return

            app.load_world_item(wid_int, edit_mode=False)
            return

    # --- Loading / saving ---------------------------------------------------

    def _load_from_db(self) -> None:
        """
        Load chapter/note metadata and text from the DB and feed the editor.
        """
        if self.doc_type != "chapter":
            # For now we only support chapters; notes can be added later.
            raise NotImplementedError("DocPage currently supports only doc_type='chapter'")

        db = self.app.db
        chap_id = self.doc_id

        # ---- Title ---------------------------------------------------------
        title = db.chapter(chap_id).strip() or "(Untitled)"
        self.titleLabel.setText(title)

        # ---- Active version + markdown -------------------------------------
        # Prefer the chapter's configured active version; if missing, create/resolve one.
        ver_id = db.get_active_version_id(chap_id)
        if not ver_id:
            # This helper lazily creates an active version row if needed.
            ver_id = db.chapter_active_version_id(chap_id)

        self._current_version_id = int(ver_id) if ver_id else 0

        # Use the same helper used elsewhere for chapter text
        md = db.chapter_content(chap_id, version_id=ver_id) or ""
        html_render = db.chapter_content_render(
            chap_id, version_id=self._current_version_id
        )

        # ---- World index for wikilinks -------------------------------------
        world_index = db.world_index_for_project(self.app._current_project_id)

        doc_config = {
            "docType": "chapter",
            "docId": str(chap_id),
            "versionId": self._current_version_id,
            "markdown": md,
            "html": html_render or "",
            "worldIndex": world_index,
            "prefs": dict(self._editor_prefs),
        }

        self.editorPane.load_document(doc_config)
        self._dirty = False
        self.statusLine.show_neutral("Viewing")

    def request_save_all_editors(self) -> None:
        """
        Entry point for Ctrl+S: main window should call this on the active page.
        """
        self.editorPane.request_save()

    # --- Callbacks from editor ---------------------------------------------

    def _on_editor_doc_changed(self, doc_id: str, version_id: int, dirty: bool) -> None:
        # You can optionally sanity-check doc_id here
        self._dirty = bool(dirty)
        if self._dirty:
            self.statusLine.set_dirty()
        else:
            self.statusLine.show_neutral("Viewing")

    @QtCore.Slot(str, int, str, str)
    def _on_editor_request_save(
        self,
        doc_id: str,
        version_id: int,
        markdown: str,
        html_snapshot: str,
    ) -> None:
        """
        Hard-save path for chapters, analogous to WorldDetailWidget._on_editor_request_save.
        """
        if self.doc_type != "chapter":
            # We'll extend this later for other doc types.
            return

        # Resolve chapter id
        if not doc_id:
            chap_id = self.doc_id
        else:
            chap_id = int(doc_id)

        db = self.app.db

        # Resolve version: prefer the one from the editor, otherwise fall back to our cache,
        # then to DB helpers that know how to pick/create an active version.
        ver_id = int(version_id) if version_id else 0
        if not ver_id:
            if self._current_version_id:
                ver_id = int(self._current_version_id)
            else:
                ver_id = db.get_active_version_id(chap_id) or db.chapter_active_version_id(chap_id)

        if not ver_id:
            # If this ever triggers, we have a schema/logic issue and should fix it explicitly.
            raise RuntimeError(f"Could not resolve a chapter_version_id for chapter {chap_id}")

        markdown = markdown or ""
        html_snapshot = html_snapshot or ""

        # 1) Update the version text (markdown) + hash + FTS
        _, text_changed = db.set_chapter_version_text(ver_id, markdown)

        # 2) Update the HTML snapshot for this version (if you end up using it elsewhere)
        db.chapter_version_render_update(ver_id, html_snapshot)

        # 3) Run quick-parse pipeline for this chapter/version
        #    This will recompute references, metrics, candidates, and center preview.
        self.app.cmd_quick_parse(doc_type="chapter", doc_id=chap_id, version_id=ver_id)

        # 4) Local UI bookkeeping
        self._current_version_id = ver_id
        self._dirty = False
        self.statusLine.set_saved_now()
        self.saved.emit(chap_id)

    def _on_editor_prefs_changed(self, prefs: Dict[str, Any]) -> None:
        self._editor_prefs = dict(prefs or {})

    def _on_editor_focus_lost(self) -> None:
        if self._dirty:
            # mirror world-detail: auto-save-on-blur, but via our hard-save path
            self.request_save_all_editors()
        self.statusLine.show_neutral("Viewing")
