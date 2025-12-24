# ui/widgets/rich_text_editor.py

from __future__ import annotations

import json
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebChannel import QWebChannel


class _DebugPage(QWebEnginePage):
    """
    QWebEnginePage subclass that prints all JS console messages to stdout.
    """

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        # level is a QWebEnginePage.JavaScriptConsoleMessageLevel enum
        print(f"[RichTextEditor JS][{level}] {source_id}:{line_number}: {message}")
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class RichTextBridge(QtCore.QObject):
    """
    QObject exposed to JS as `richTextBridge` via QWebChannel.

    JS calls these slots via callBridge("methodName", payload):

        richTextBridge.onDocChanged({ docId, versionId, dirty })
        richTextBridge.requestSave({ docId, versionId, markdown, htmlSnapshot })
        richTextBridge.onLinkInteraction({ ... })
        richTextBridge.contextAction({ ... })
        richTextBridge.activityPing("typing" | "scrolling" | ...)
        richTextBridge.focusGained()
        richTextBridge.focusLost()

    We translate them into Qt signals that the host RichTextEditor listens to.
    """

    # Signals that RichTextEditor will connect to its own public signals
    docChangedSignal = QtCore.Signal(str, int, bool)          # doc_id, version_id, dirty
    requestSaveSignal = QtCore.Signal(str, int, str, str)     # doc_id, version_id, md, html
    linkInteractionSignal = QtCore.Signal(dict)
    contextActionSignal = QtCore.Signal(dict)
    activityPingSignal = QtCore.Signal(str)                   # "typing"/"scrolling"/...
    focusGainedSignal = QtCore.Signal()
    focusLostSignal = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)

    # ---- helpers ---------------------------------------------------------

    def _as_dict(self, payload) -> dict:
        if payload is None:
            return {}
        if isinstance(payload, dict):
            return payload
        # QVariantMap / QJsonObject etc.
        try:
            return dict(payload)
        except Exception:
            raise TypeError(f"Unexpected payload type for bridge: {type(payload)!r}")

    # ---- slots called from JS --------------------------------------------

    @QtCore.Slot("QVariant")
    def onDocChanged(self, payload) -> None:
        """
        JS: callBridge("onDocChanged", { docId, versionId, dirty })
        """
        data = self._as_dict(payload)
        doc_id = str(data.get("docId") or "")
        version_id = int(data.get("versionId") or 0)
        dirty = bool(data.get("dirty", True))
        print("[RichTextBridge] onDocChanged docId=", doc_id, "ver=", version_id, "dirty=", dirty)
        self.docChangedSignal.emit(doc_id, version_id, dirty)

    @QtCore.Slot("QVariant")
    def requestSave(self, payload) -> None:
        """
        JS: callBridge("requestSave", { docId, versionId, markdown, htmlSnapshot })
        """
        data = self._as_dict(payload)
        doc_id = str(data.get("docId") or "")
        version_id = int(data.get("versionId") or 0)
        markdown = data.get("markdown") or ""
        # allow either htmlSnapshot or html, just in case
        html_snapshot = data.get("htmlSnapshot") or data.get("html") or ""
        print(
            "[RichTextBridge] requestSave docId=",
            doc_id,
            "ver=",
            version_id,
            "md_len=",
            len(markdown),
            "html_len=",
            len(html_snapshot),
        )
        self.requestSaveSignal.emit(doc_id, version_id, markdown, html_snapshot)

    @QtCore.Slot("QVariant")
    def onLinkInteraction(self, payload) -> None:
        """
        JS: callBridge("onLinkInteraction", { ... })
        """
        data = self._as_dict(payload)
        print("[RichTextBridge] onLinkInteraction", data)
        self.linkInteractionSignal.emit(data)

    @QtCore.Slot("QVariant")
    def contextAction(self, payload) -> None:
        """
        JS: callBridge("contextAction", { ... })
        """
        data = self._as_dict(payload)
        print("[RichTextBridge] contextAction", data)
        self.contextActionSignal.emit(data)

    @QtCore.Slot(str)
    def activityPing(self, kind: str) -> None:
        """
        JS: callBridge("activityPing", "typing" | "scrolling" | ...)
        """
        print("[RichTextBridge] activityPing kind=", kind)
        self.activityPingSignal.emit(kind)

    @QtCore.Slot()
    def focusGained(self) -> None:
        """
        JS: callBridge("focusGained")
        """
        print("[RichTextBridge] focusGained")
        self.focusGainedSignal.emit()

    @QtCore.Slot()
    def focusLost(self) -> None:
        """
        JS: callBridge("focusLost")
        """
        print("[RichTextBridge] focusLost")
        self.focusLostSignal.emit()


class RichTextEditor(QtWidgets.QWidget):
    """
    Wrapper widget that hosts the web editor and exposes nice Qt signals.

    Public signals:
      - docChanged(docId: str, versionId: int, dirty: bool)
      - requestSave(docId: str, versionId: int, markdown: str, htmlSnapshot: str)
      - linkInteraction(payload: dict)
      - contextAction(payload: dict)
      - activityPing(kind: str)
      - focusGained()
      - focusLost()

    Public methods:
      - load_document(doc_config: dict)
      - request_save()   # ask JS to send back markdown/html via requestSave signal
    """

    docChanged = QtCore.Signal(str, int, bool)
    requestSave = QtCore.Signal(str, int, str, str)
    linkInteraction = QtCore.Signal(dict)
    contextAction = QtCore.Signal(dict)
    activityPing = QtCore.Signal(str)
    focusGained = QtCore.Signal()
    focusLost = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        # --- view/page -----------------------------------------------------
        self._view = QWebEngineView(self)
        self._page = _DebugPage(self._view)
        self._view.setPage(self._page)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        # --- bridge + channel ---------------------------------------------
        self._bridge = RichTextBridge(self)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("richTextBridge", self._bridge)
        self._page.setWebChannel(self._channel)

        # Track load state of the HTML shell
        self._page_is_loaded: bool = False
        self._pending_doc_config: dict | None = None

        # Current doc identity / state
        self._current_doc_id: str = ""
        self._current_version_id: int = 0
        self._current_dirty: bool = False

        # Bridge → public signals
        self._bridge.docChangedSignal.connect(self._on_bridge_doc_changed)
        self._bridge.requestSaveSignal.connect(self._on_bridge_request_save)
        self._bridge.linkInteractionSignal.connect(self.linkInteraction)
        self._bridge.contextActionSignal.connect(self.contextAction)
        self._bridge.activityPingSignal.connect(self.activityPing)
        self._bridge.focusGainedSignal.connect(self.focusGained)
        self._bridge.focusLostSignal.connect(self.focusLost)

        self._page.loadFinished.connect(self._on_page_load_finished)

        # Load the HTML shell that hosts editor_bundle.js
        self._load_html_shell()

    # ------------------------------------------------------------------ #
    #   HTML shell + page load
    # ------------------------------------------------------------------ #

    def _load_html_shell(self) -> None:
        bundle_path = Path(__file__).with_name("editor_bundle.js").resolve()
        if not bundle_path.exists():
            raise FileNotFoundError(f"editor_bundle.js not found at {bundle_path}")

        bundle_url = bundle_path.as_uri()

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>StoryArkivist Rich Editor</title>
<style>
html, body {{
  margin: 0;
  padding: 0;
  height: 100%;
  overflow: hidden;
  box-sizing: border-box;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
}}
#wrapper {{
  height: 100%;
  width: 100%;
  box-sizing: border-box;
}}
#editorRoot {{
  height: 100%;
  width: 100%;
  box-sizing: border-box;
  padding: 8px;
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-y: auto;
}}

/* Keep Shift+Enter vs Enter visually distinct:
   - Shift+Enter stays within the same <p> (just <br>)
   - Enter creates a new <p>/<div> and gets spacing */
#editorRoot > p,
#editorRoot > div {{
  margin-top: 0;
  margin-bottom: 0.75em; /* tweak to taste */
}}

/* Avoid extra whitespace at the very end */
#editorRoot > p:last-child,
#editorRoot > div:last-child {{
  margin-bottom: 0;
}}

/* Base: distraction-friendly, links visually blend into text */
#editorRoot a,
#editorRoot .wikilink {{
  color: inherit;
  text-decoration: none;
  cursor: text;
}}

/* "full" mode: always show link chrome */
#editorRoot.links-full a,
#editorRoot.links-full .wikilink {{
  color: #3b82f6;            /* tweak to theme */
  text-decoration: underline;
  cursor: text;
}}
#editorRoot.links-full .wikilink-candidate,
#editorRoot.links-minimal.ctrl-links-active .wikilink-candidate {{
  color: #10b981;             /* e.g., green for candidates */
  text-decoration: underline;
  cursor: text;
  border-bottom: 1px dashed currentColor;
}}

/* "ctrlReveal" / "minimal" mode: show link chrome only while Ctrl is held */
#editorRoot.links-minimal.ctrl-links-active a,
#editorRoot.links-minimal.ctrl-links-active .wikilink {{
  color: #3b82f6;
  text-decoration: underline;
  cursor: text;
}}

/* Optional: a bit of extra emphasis while Ctrl is down */
#editorRoot.ctrl-links-active .wikilink {{
  background-color: rgba(59, 130, 246, 0.15);
}}

#editorRoot .wikilink:hover {{
  text-decoration-thickness: 2px;
}}

/* Show pointer cursor when follow mode is click-to-follow */
#editorRoot.follow-click.links-full a,
#editorRoot.follow-click.links-full .wikilink,
#editorRoot.follow-click.links-full .wikilink-candidate,
#editorRoot.follow-click.links-minimal.ctrl-links-active a,
#editorRoot.follow-click.links-minimal.ctrl-links-active .wikilink,
#editorRoot.follow-click.links-minimal.ctrl-links-active .wikilink-candidate {{
  cursor: pointer;
}}

/* Ctrl/Cmd down: links are "actionable", show pointer on hover */
#editorRoot.modifier-down a,
#editorRoot.modifier-down .wikilink,
#editorRoot.modifier-down .wikilink-candidate {{
  cursor: pointer;
}}

/* Find highlight (if Custom Highlight API is supported) */
::highlight(sa-find-current) {{
  background: rgba(250, 204, 21, 0.35);
}}

</style>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script src="{bundle_url}"></script>
</head>
<body>
  <div id="wrapper">
    <div id="editorRoot"></div>
  </div>
</body>
</html>
"""
        base_url = QtCore.QUrl.fromLocalFile(str(bundle_path.parent))
        self._page.setHtml(html, base_url)

    @QtCore.Slot(bool)
    def _on_page_load_finished(self, ok: bool) -> None:
        print("[RichTextEditor] HTML shell loadFinished ok=", ok)
        self._page_is_loaded = ok
        if ok and self._pending_doc_config is not None:
            cfg = self._pending_doc_config
            self._pending_doc_config = None
            self._send_doc_config_to_js(cfg)

    # ------------------------------------------------------------------ #
    #   Public API: load_document / request_save
    # ------------------------------------------------------------------ #

    def load_document(self, doc_config: dict) -> None:
        """
        doc_config keys (minimum):

            {
              "docType":   "world" | "chapter" | "scratch" | ...,
              "docId":     str(...),
              "versionId": int,
              "markdown":  str,
              "worldIndex": [],   # optional, for auto-linking
              "prefs": {},        # optional
            }
        """
        if not isinstance(doc_config, dict):
            raise TypeError("doc_config must be a dict")

        self._current_doc_id = str(doc_config.get("docId") or "")
        self._current_version_id = int(doc_config.get("versionId") or 0)
        self._current_dirty = False

        if self._page_is_loaded:
            self._send_doc_config_to_js(doc_config)
        else:
            # queue until the shell is ready
            self._pending_doc_config = dict(doc_config)

    def _send_doc_config_to_js(self, doc_config: dict) -> None:
        js_cfg = json.dumps(doc_config)
        js = f"""
            if (window.RichEditor && typeof window.RichEditor.loadDocument === 'function') {{
                window.RichEditor.loadDocument({js_cfg});
            }} else {{
                console.error("[RichEditor] loadDocument not available yet");
            }}
        """
        print(
            "[RichTextEditor] sending doc config to JS:",
            doc_config.get("docId"),
            doc_config.get("versionId"),
        )
        self._page.runJavaScript(js)

    def request_save(self) -> None:
        """
        Ask JS to gather markdown/html and send it back through the requestSave signal.
        """
        js = """
            if (window.RichEditor && typeof window.RichEditor.requestSaveFromHost === 'function') {
                window.RichEditor.requestSaveFromHost();
            } else {
                console.error("[RichEditor] requestSaveFromHost not available");
            }
        """
        print("[RichTextEditor] request_save → JS")
        self._page.runJavaScript(js)

    # ------------------------------------------------------------------ #
    #   Bridge handlers (internal)
    # ------------------------------------------------------------------ #

    @QtCore.Slot(str, int, bool)
    def _on_bridge_doc_changed(self, doc_id: str, version_id: int, dirty: bool) -> None:
        if doc_id:
            self._current_doc_id = doc_id
        if version_id:
            self._current_version_id = version_id
        self._current_dirty = bool(dirty)
        self.docChanged.emit(self._current_doc_id, self._current_version_id, self._current_dirty)

    @QtCore.Slot(str, int, str, str)
    def _on_bridge_request_save(
        self,
        doc_id: str,
        version_id: int,
        markdown: str,
        html_snapshot: str,
    ) -> None:
        if doc_id:
            self._current_doc_id = doc_id
        if version_id:
            self._current_version_id = version_id
        # after a successful save, we treat doc as clean
        self._current_dirty = False
        self.requestSave.emit(
            self._current_doc_id,
            self._current_version_id,
            markdown,
            html_snapshot,
        )

    def update_world_index(self, world_index: list[dict]) -> None:
        """
        Push a new worldIndex into the JS editor without reloading the whole doc.
        """
        if not self._page_is_loaded:
            # If the page isn't ready yet, just stash it in the pending doc config
            if self._pending_doc_config is None:
                self._pending_doc_config = {}
            self._pending_doc_config["worldIndex"] = world_index
            return

        payload = {"worldIndex": world_index}
        cfg_json = json.dumps(payload)
        js = (
            "if (window.RichEditor && typeof window.RichEditor.updateWorldIndex === 'function') {"
            f"  window.RichEditor.updateWorldIndex({cfg_json});"
            "} else {"
            "  console.warn('[RichEditor] updateWorldIndex not available in JS');"
            "}"
        )
        self._page.runJavaScript(js)
