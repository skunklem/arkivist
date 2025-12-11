from __future__ import annotations

from typing import Optional, Dict, Any

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSizePolicy

from ui.widgets.rich_text_editor import RichTextEditor


class RichEditorToolbar(QtWidgets.QFrame):
    """
    Small toolbar controlling link display/behavior for RichTextEditor.

    Exposes a simple prefs dict:

        {
            "showWikilinks": "full" | "ctrlReveal" | "minimal" (future),
            "linkFollowMode": "ctrlClick" | "click",
            "highlightLinksWhileCtrl": bool,
        }
    """

    prefsChanged = Signal(dict)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Raised)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # --- Link visibility mode ---
        # lbl_links = QtWidgets.QLabel("Links:", self)
        self.cmbLinkMode = QtWidgets.QComboBox(self)
        self.cmbLinkMode.addItem("Show links", "full")
        self.cmbLinkMode.addItem("Reveal on Ctrl", "ctrlReveal")

        # --- Follow mode ---
        # lbl_click = QtWidgets.QLabel("Follow:", self)
        self.cmbFollowMode = QtWidgets.QComboBox(self)
        self.cmbFollowMode.addItem("Ctrl+Click", "ctrlClick")
        self.cmbFollowMode.addItem("Click", "click")

        # --- Highlight while Ctrl is held ---
        self.chkHighlight = QtWidgets.QCheckBox("Highlight while Ctrl", self)

        # Layout
        # layout.addWidget(lbl_links)
        layout.addWidget(self.cmbLinkMode)
        layout.addSpacing(16)
        # layout.addWidget(lbl_click)
        layout.addWidget(self.cmbFollowMode)
        layout.addSpacing(16)
        layout.addWidget(self.chkHighlight)
        layout.addStretch(1)

        # Wire
        self.cmbLinkMode.currentIndexChanged.connect(self._emit_prefs)
        self.cmbFollowMode.currentIndexChanged.connect(self._emit_prefs)
        self.chkHighlight.toggled.connect(self._emit_prefs)

    # --- Public API ---------------------------------------------------------

    def set_prefs(self, prefs: Dict[str, Any]) -> None:
        """
        Programmatically apply prefs. This *will* emit prefsChanged once with
        the normalized dict (which is fine – callers can just store it).
        """
        prefs = dict(prefs or {})

        show_mode = prefs.get("showWikilinks", "full")
        idx = self.cmbLinkMode.findData(show_mode)
        if idx < 0:
            idx = self.cmbLinkMode.findData("full")
        if idx >= 0:
            self.cmbLinkMode.setCurrentIndex(idx)

        follow_mode = prefs.get("linkFollowMode", "ctrlClick")
        idx = self.cmbFollowMode.findData(follow_mode)
        if idx < 0:
            idx = self.cmbFollowMode.findData("ctrlClick")
        if idx >= 0:
            self.cmbFollowMode.setCurrentIndex(idx)

        self.chkHighlight.setChecked(bool(prefs.get("highlightLinksWhileCtrl", True)))
        # Let the normal signal path advertise the resulting prefs
        self._emit_prefs()

    def prefs(self) -> Dict[str, Any]:
        """Return the current prefs as a normalized dict."""
        show_data = self.cmbLinkMode.currentData() or "full"
        follow_data = self.cmbFollowMode.currentData() or "ctrlClick"
        return {
            "showWikilinks": show_data,
            "linkFollowMode": follow_data,
            "highlightLinksWhileCtrl": self.chkHighlight.isChecked(),
        }

    # --- Internals ----------------------------------------------------------

    @QtCore.Slot()
    def _emit_prefs(self) -> None:
        self.prefsChanged.emit(self.prefs())


class RichEditorPane(QtWidgets.QWidget):
    """
    Composite widget: [toolbar] + [RichTextEditor].

    It is agnostic about doc type (world_item vs chapter vs note). It just
    receives a doc_config dict compatible with RichTextEditor.

    Public API (MVP):

        load_document(doc_config: dict) -> None
        set_prefs(prefs: dict) -> None
        prefs() -> dict
        request_save() -> None

    Signals (bubbled from inner editor / toolbar):

        docChanged(str docId, int versionId, bool dirty)
        requestSave(str docId, int versionId, str markdown, str html_snapshot)
        linkInteraction(dict payload)
        contextAction(dict payload)
        activityPing(str kind)
        focusGained()
        focusLost()
        prefsChanged(dict prefs)
    """

    # Bubble the RichTextEditor signals
    docChanged = Signal(str, int, bool)
    requestSave = Signal(str, int, str, str)
    linkInteraction = Signal(dict)
    contextAction = Signal(dict)
    activityPing = Signal(str)
    focusGained = Signal()
    focusLost = Signal()

    # Bubble toolbar prefs
    prefsChanged = Signal(dict)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        initial_prefs: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)

        self._prefs: Dict[str, Any] = dict(initial_prefs or {})
        self._current_doc_config: Optional[Dict[str, Any]] = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Toolbar
        self.toolbar = RichEditorToolbar(self)
        if self._prefs:
            self.toolbar.set_prefs(self._prefs)
        layout.addWidget(self.toolbar)

        # Editor
        self.editor = RichTextEditor(self)
        layout.addWidget(self.editor)

        # --- size policies & stretch: toolbar fixed, editor eats space ---
        # Pane itself should just expand with its container
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Toolbar: horizontally expand, but vertically fixed so it doesn't grab height
        tb_policy = self.toolbar.sizePolicy()
        tb_policy.setHorizontalPolicy(QSizePolicy.Expanding)
        tb_policy.setVerticalPolicy(QSizePolicy.Fixed)
        self.toolbar.setSizePolicy(tb_policy)

        # Editor: expand both ways and get the stretch
        self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.setStretch(0, 0)  # toolbar
        layout.setStretch(1, 1)  # editor

        # Wire toolbar → pane
        self.toolbar.prefsChanged.connect(self._on_toolbar_prefs_changed)

        # Wire editor → pane (bubble signals up)
        self.editor.docChanged.connect(self.docChanged)
        self.editor.requestSave.connect(self.requestSave)
        self.editor.linkInteraction.connect(self.linkInteraction)
        self.editor.contextAction.connect(self.contextAction)
        self.editor.activityPing.connect(self.activityPing)
        self.editor.focusGained.connect(self.focusGained)
        self.editor.focusLost.connect(self.focusLost)

    # --- Public API ---------------------------------------------------------

    def load_document(self, doc_config: Dict[str, Any]) -> None:
        """
        Load a document into the inner editor.

        We merge any prefs bundled in doc_config["prefs"] with our own _prefs,
        preferencing values explicitly provided in doc_config.
        """
        if not isinstance(doc_config, dict):
            raise TypeError("doc_config must be a dict")

        incoming_prefs = dict(doc_config.get("prefs") or {})
        combined = dict(self._prefs)
        combined.update(incoming_prefs)

        self._prefs = combined
        self.toolbar.set_prefs(combined)

        cfg = dict(doc_config)
        cfg["prefs"] = combined

        self._current_doc_config = cfg
        self.editor.load_document(cfg)

    def set_prefs(self, prefs: Dict[str, Any]) -> None:
        """
        Update pane-level prefs and apply them to the toolbar.

        These prefs will be included on the next load_document call. For now
        we *do not* re-send the doc to JS immediately to avoid caret jumps.
        """
        self._prefs = dict(prefs or {})
        self.toolbar.set_prefs(self._prefs)
        # Toolbar will emit prefsChanged → _on_toolbar_prefs_changed

    def prefs(self) -> Dict[str, Any]:
        return dict(self._prefs)

    def request_save(self) -> None:
        """Hard save: ask the inner editor to emit requestSave."""
        self.editor.request_save()

    # Convenience: some hosts may want to patch worldIndex in-place
    def update_world_index(self, world_index: list[dict]) -> None:
        """
        Push an updated worldIndex into the current doc without reloading.
        This keeps the editor's DOM and caret position intact on save.
        """
        # Keep our cached config in sync for any future loads from _current_doc_config
        if self._current_doc_config is not None:
            self._current_doc_config["worldIndex"] = list(world_index)

        # If the inner editor knows how to handle incremental world-index
        # updates, delegate to it (RichTextEditor does).
        if hasattr(self.editor, "update_world_index"):
            self.editor.update_world_index(world_index)

    # --- Internals ----------------------------------------------------------

    @QtCore.Slot(dict)
    def _on_toolbar_prefs_changed(self, prefs: Dict[str, Any]) -> None:
        # Keep local copy; host widgets can persist this
        self._prefs = dict(prefs or {})
        self.prefsChanged.emit(self._prefs)
