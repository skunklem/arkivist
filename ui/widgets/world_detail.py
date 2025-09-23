import re
import traceback

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt, QTimer, QUrl, QDateTime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QScrollArea,
    QTextBrowser, QPlainTextEdit,QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QSizePolicy, QHBoxLayout, QApplication
)
from ui.widgets.common import StatusLine

def _safe(fn):
    def wrap(self, *a, **k):
        try:
            return fn(self, *a, **k)
        except Exception:
            print(f"[WorldDetailWidget] error in {fn.__name__}:")
            traceback.print_exc()
    return wrap


from utils.md import md_to_html
from ui.widgets.helpers import PlainNoTab

class MiniDoc(QTextBrowser):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setFrameShape(QFrame.NoFrame)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.anchorClicked.connect(self._relay_anchor)

        # inherit app palette & keep fully transparent background
        self.setPalette(QApplication.palette(self))
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet(
            "QTextBrowser { background: transparent; border:0; color: palette(window-text); }"
        )

        # no document margin (this cured the tiny indent)
        self.document().setDocumentMargin(0)  # ← remove default margin

    def _relay_anchor(self, url):
        # parent() chain to WorldDetailWidget and reuse its handler
        w = self.parent()
        while w and not hasattr(w, "_anchor_clicked"):
            w = w.parent()
        if w:
            w._anchor_clicked(url)

    def setHtmlAndFit(self, html: str):
        # Strip default margins on body/p
        html = (
            "<style>"
            "html, body { margin:0; padding:0; }"
            "p { margin:0; }"
            "</style>" + html
        )
        self.setHtml(html)
        # Fit height to document width
        doc = self.document()
        doc.setTextWidth(max(1, self.viewport().width() - 1))
        h = int(doc.size().height()) + self.frameWidth() * 2 + 1
        self.setFixedHeight(max(h, self.fontMetrics().height() + 4))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # re-fit when the panel resizes
        self.setHtmlAndFit(self.toHtml())

class WorldDetailWidget(QWidget):
    """Right panel: shows a world item in View/Edit with back/forward history."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(8, 8, 8, 8)
        self.vbox.setSpacing(6)

        self._add_top_bar()

        # Status line (kept forever)
        self.statusLine = StatusLine(self)
        self.vbox.addWidget(self.statusLine)
        self.statusLine.show_neutral("Select a world item")

        # --- SCROLLABLE content area ---
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        # make background blend with the panel (no grey)
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QTextBrowser { background: transparent; }
        """)

        self.contentWidget = QWidget(self.scroll)
        self.contentWidget.setAutoFillBackground(False)
        self.contentBox = QVBoxLayout(self.contentWidget)
        self.contentBox.setContentsMargins(0, 0, 0, 0)
        self.contentBox.setSpacing(8)

        self.scroll.setWidget(self.contentWidget)
        # IMPORTANT: give the scroll the stretch so it fills the column
        self.vbox.addWidget(self.scroll, 1)   # <-- stretch=1 makes it fill the rest

        self._history = []
        self._hindex  = -1
        self._current_world_item_id = None
        self._dirty = False
        self._views = {}  # optional: keep references

        # default to View
        self._set_mode(view_mode=True)

    def _update_nav_buttons(self):
        self.btnBack.setEnabled(self._hindex > 0)
        self.btnFwd.setEnabled(self._hindex >= 0 and self._hindex < len(self._history)-1)

    def _add_top_bar(self):
        self.top = QHBoxLayout()

        self.btnBack = QPushButton("←")
        self.btnFwd  = QPushButton("→")
        for b in (self.btnBack, self.btnFwd):
            b.setFixedWidth(32)
        self.btnBack.clicked.connect(self.go_back)
        self.btnFwd.clicked.connect(self.go_forward)

        self.lblTitle = QLabel("World Detail")
        self.modeBtn = QPushButton("Edit")
        self.modeBtn.clicked.connect(self.toggle_mode)

        for w in (self.btnBack, self.btnFwd, self.lblTitle):
            w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.top.addWidget(self.btnBack)
        self.top.addWidget(self.btnFwd)
        self.top.addWidget(self.lblTitle)
        self.top.addStretch(1)
        self.top.addWidget(self.modeBtn)
        self.vbox.addLayout(self.top)

    def _clear_content(self):
        while self.contentBox.count():
            it = self.contentBox.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            else:
                sub = it.layout()
                if sub:
                    # recursively delete sublayouts
                    while sub.count():
                        sit = sub.takeAt(0)
                        sw = sit.widget()
                        if sw:
                            sw.deleteLater()
                    sub.deleteLater()
        self._views.clear()

    def _add_views(self):
        # create view/edit widgets inside contentBox (not vbox)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._anchor_clicked)
        self.contentBox.addWidget(self.view)

        self.edit = PlainNoTab()
        self.edit.setPlaceholderText("Markdown content…")
        self.edit.textChanged.connect(self._mark_dirty)
        self.contentBox.addWidget(self.edit)

        self._views["view"] = self.view
        self._views["edit"] = self.edit

    def go_back(self):
        if self._hindex > 0:
            # Save current edits before navigating
            self._save_current_if_dirty()
            new_index = self._hindex - 1
            target_id = self._history[new_index]
            self._hindex = new_index
            _safe(self.show_item(target_id, add_to_history=False))

    def go_forward(self):
        if self._hindex < len(self._history) - 1:
            # Save current edits before navigating
            self._save_current_if_dirty()
            new_index = self._hindex + 1
            target_id = self._history[new_index]
            self._hindex = new_index
            _safe(self.show_item(target_id, add_to_history=False))

    def _set_mode(self, view_mode: bool):
        # Don't touch top bar / status line, only toggle content visibility
        if self._current_world_item_id is None:
            self.modeBtn.setText("Edit")
            v = self._views.get("view"); e = self._views.get("edit")
            if v: v.setVisible(True)     # show placeholder in view
            if e: e.setVisible(False)
            self.statusLine.show_neutral("Viewing")
            return

        item_type = self.app.db.world_item_meta(self._current_world_item_id)["type"]
        if item_type == "character":
            # summary-only here; editing characters via dialog
            self.modeBtn.setText("Edit")
            self.statusLine.show_neutral("Viewing")
        else:
            # generic world item: show/hide actual widgets
            v = self._views.get("view"); e = self._views.get("edit")
            if v: v.setVisible(view_mode)
            if e: e.setVisible(not view_mode)
            self.modeBtn.setText("Edit" if view_mode else "View")
            self.statusLine.show_neutral("Viewing" if view_mode else "Editing")

    def _mark_dirty(self):
        self._dirty = True
        if hasattr(self, "statusLine"):
            self.statusLine.set_dirty()

    def toggle_mode(self):
        """Toggles View/Edit for generic world items; opens dialog for characters."""
        if not self._current_world_item_id:
            return
        meta = self.app.db.world_item_meta(self._current_world_item_id)
        wtype = (meta["type"] or "")
        if wtype == "character":
            # open character dialog and refresh panel afterwards
            self.app.open_character_dialog(self._current_world_item_id)
            return

        v = self._views.get("view"); e = self._views.get("edit")
        # Non-character: if leaving edit -> save first
        currently_viewing = bool(v and e and v.isVisible())
        if not currently_viewing:
            self._save_current_if_dirty()

        # Flip mode
        self._set_mode(view_mode=not currently_viewing)

        # IMPORTANT: always refresh the active pane (fills edit with MD or view with HTML)
        if v:
            self._refresh_render_only()

    def _save_current_if_dirty(self):
        if not self._current_world_item_id or not self._dirty:
            return
        md = self.edit.toPlainText()
        cur = self.app.db.conn.cursor()
        cur.execute("UPDATE world_items SET content_md=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (md, self._current_world_item_id))
        self.app.db.conn.commit()
        self.app.rebuild_world_item_render(self._current_world_item_id)
        self._dirty = False
        if hasattr(self, "statusLine"):
            self.statusLine.set_saved_now()

        if getattr(self.app, "_current_world_item_id", None):
            self.app.show_world_item(self._current_world_item_id, edit_mode=False)

    def _delete_layout(self, layout):
        """Recursively delete all items (widgets or sublayouts) from a *child* layout."""
        if not layout:
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # fully detach and delete
                w.setParent(None)
                w.deleteLater()
            else:
                sub = item.layout()
                if sub:
                    self._delete_layout(sub)
                    # IMPORTANT: don't delete the *root* self.vbox; Qt owns it
                    sub.setParent(None)
        # DO NOT call layout.deleteLater() on the root container layout
        # (this function is for child layouts we took from items)
        # For child layouts, Qt will GC them once detached.

    # def _clear(self):
    #     """Clear the panel content without replacing the root layout."""
    #     self.views_active = False
    #     if not hasattr(self, "vbox") or self.vbox is None:
    #         return
    #     # Remove every child item from self.vbox
    #     while self.vbox.count():
    #         item = self.vbox.takeAt(0)
    #         w = item.widget()
    #         if w is not None:
    #             w.setParent(None)
    #             w.deleteLater()
    #         else:
    #             sub = item.layout()
    #             if sub:
    #                 self._delete_layout(sub)
    #                 sub.setParent(None)
    #     # Now self.vbox exists and is empty; safe to rebuild header/body

    def _refresh_render_only(self):
        """Update the rendered HTML/text into self.view/edit (no rebuilding UI)."""
        v = self._views.get("view"); e = self._views.get("edit")
        if not v:
            return
        if self._current_world_item_id is None:
            v.setHtml("<i>Select a world item</i>")
            e.setPlainText("")
            return

        cur = self.app.db.conn.cursor()
        cur.execute("SELECT content_md, content_render FROM world_items WHERE id=?", (self._current_world_item_id,))
        row = cur.fetchone()
        if not row:
            v.setHtml("<i>Item not found</i>")
            e.setPlainText("")
            return

        md, html = row
        if v.isVisible():
            v.setHtml(html or "<i>(empty)</i>")
        elif e.isVisible():
            e.blockSignals(True)
            e.setPlainText(md or "")
            e.blockSignals(False)
            self._dirty = False

    def show_item(self, world_item_id: int, add_to_history: bool = True, view_mode: bool = True):
        """Load a world item. Saves edit if needed, updates history, rebuilds body once."""
        if getattr(self, "_building", False):
            return
        self._building = True
        try:
            self._save_current_if_dirty()

            # history mgmt (unchanged)
            if add_to_history:
                if self._hindex < len(self._history) - 1:
                    self._history = self._history[:self._hindex+1]
                if not self._history or self._history[self._hindex] != world_item_id:
                    self._history.append(world_item_id)
                    self._hindex = len(self._history) - 1

            self._current_world_item_id = world_item_id
            self._dirty = False

            # Rebuild content area only
            self._clear_content()

            item = self.app.db.world_item_meta(world_item_id)
            if not item:
                self.lblTitle.setText("World Detail (missing)")
                self.statusLine.show_neutral("Missing")
                return

            self.lblTitle.setText(item["title"] or "World Detail")

            if item["type"] == "character":
                # build character summary in contentBox
                self._render_character_summary(world_item_id, item["title"], item["content_md"] or "")
                self.modeBtn.setText("Edit")
                self.statusLine.show_neutral("Viewing")
            else:
                # build view/edit pair in contentBox
                self._add_views()
                self._set_mode(view_mode=view_mode)
                self.refresh()  # will fill view/edit as appropriate

            self._update_nav_buttons()
            if hasattr(self.app, "focus_world_item_in_tree"):
                self.app.focus_world_item_in_tree(world_item_id)

            # ensure the layout paints this frame
            QTimer.singleShot(0, lambda: None)

        finally:
            self._building = False

    def _render_character_summary_md(self, char_id: int, title: str, md: str):
        character_summary_md = [
            f"# {title}\n",
            f"---\n",
            f"**Character Description:**\n",
            f"{md}\n",
        ]

        traits = self.app.db.character_facets_by_type(char_id, "trait")
        if traits:
            character_summary_md.append(f"**Traits:**\n")
            character_summary_md.append(f"|Trait|Value|Note|")
            character_summary_md.append(f"|---|---|---|")
            for trait in traits:
                character_summary_md.append(f"|{trait['label']}|{trait['value']}|{trait['note']}|")

        # TODO: AUTOMATE this for other facets

        return md_to_html("\n".join(character_summary_md))

    def _add_label(self, vbox: QVBoxLayout, label: str, set_size_policy: bool = True):
        lbl = QLabel(label)
        if set_size_policy:
            lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        vbox.addWidget(lbl)

    def _render_character_summary(self, char_id: int, title: str, md: str):
        # Header row
        header = QHBoxLayout()
        hdr = QLabel(f"<h3 style='margin:4px 0'>{title}</h3>")
        hdr.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        header.addWidget(hdr)
        header.addStretch(1)
        # btnEdit = QPushButton("Edit…")
        # btnEdit.clicked.connect(lambda: self.app.open_character_dialog(char_id))
        # header.addWidget(btnEdit)
        self.contentBox.addLayout(header)

        # Description
        self._add_label(self.contentBox, "<b>Character Description</b>", set_size_policy=False)
        desc = MiniDoc(self)
        desc.setHtmlAndFit(md_to_html(md or ""))
        self.contentBox.addWidget(desc)

        # Traits (two groups)
        def add_traits(kind: str, title_text: str):
            rows = self.app.db.character_facets_by_type(char_id, kind)
            if not rows:
                return
            self._add_label(self.contentBox, f"<b>{title_text}</b>", set_size_policy=False)
            t = QTableWidget(len(rows), 2)
            t.setHorizontalHeaderLabels(["Trait", "Value"])
            t.verticalHeader().setVisible(False)
            t.setEditTriggers(QTableWidget.NoEditTriggers)
            t.setSelectionMode(QTableWidget.NoSelection)
            t.horizontalHeader().setStretchLastSection(True)
            t.setFrameShape(QFrame.NoFrame)
            t.setShowGrid(False)
            t.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            for r, tr in enumerate(rows):
                name = tr["label"] or ""
                val  = tr["value"] or ""
                note = tr["note"] or ""
                t.setItem(r, 0, QTableWidgetItem(name))
                it = QTableWidgetItem(val)
                if note:
                    it.setToolTip(note)
                t.setItem(r, 1, it)
            self._fit_table_height(t, min_rows=1)
            self.contentBox.addWidget(t)

        add_traits("trait_physical", "Physical features")
        add_traits("trait_character", "Characteristics")

        # Push content to the top; consume extra space at the bottom
        self.contentBox.addStretch(1)

    def _fit_table_height(self, table: QTableWidget, min_rows=1, pad=6):
        hh = table.horizontalHeader()
        header_h = hh.height() if hh else 0
        vh = table.verticalHeader()
        row_h = vh.defaultSectionSize() or (table.fontMetrics().height() + 10)
        rows = max(min_rows, table.rowCount())
        frame = table.frameWidth() * 2
        hscroll = table.horizontalScrollBar()
        hsb_h = (hscroll.sizeHint().height() if hscroll and hscroll.isVisible() else 0)
        total_h = header_h + rows * row_h + frame + pad + hsb_h
        table.setMinimumHeight(total_h)
        table.setMaximumHeight(total_h)

    def _label_link_activated(self, url: str):
        # QLabel link -> reuse same logic
        self._anchor_clicked(QUrl(url))

    def _anchor_clicked(self, qurl):
        """Handle world:// links from QTextBrowser & QLabel."""
        print("Using this one")
        url = qurl.toString()
        # Accept several patterns:
        #   world://123
        #   world://item/123
        #   world://0.0.0.123   (old buggy pattern you saw)
        m = re.search(r'world://(?:item/)?(\d+)$', url)
        if not m:
            m = re.search(r'world://(?:[\d.]*)(\d+)$', url)
        if m:
            wid = int(m.group(1))
            # Use your centralized loader:
            if hasattr(self.app, "load_world_item"):
                self.app.load_world_item(wid, edit_mode=False)
            return

        # Fallback: ignore unknown schemes (don't hand to OS)
        # Optionally: handle http(s) internally if you have external docs
        # e.g., QDesktopServices.openUrl(qurl) for http(s) only:
        # If you also hyperlink http(s), optionally allow:
        # if url.startswith("http"):
        #     QDesktopServices.openUrl(qurl)
        # else:
        #     print("Unknown link scheme:", url)

    def refresh(self):
        """Refresh the currently shown item."""
        v = self._views.get("view")
        e = self._views.get("edit")
        if self._current_world_item_id is None:
            self.lblTitle.setText("World Detail")
            if v: v.setHtml("<i>Select a world item</i>")
            if e:
                e.blockSignals(True)
                e.setPlainText("")
                e.blockSignals(False)
            self.statusLine.show_neutral("Select an item")
            return
        cur = self.app.db.conn.cursor()
        cur.execute("SELECT title, content_md, content_render, type FROM world_items WHERE id=?",
                    (self._current_world_item_id,))
        row = cur.fetchone()
        if not row:
            self.lblTitle.setText("World Detail (missing)")
            if v: v.setHtml("<i>Item not found</i>")
            if e:
                e.blockSignals(True)
                e.setPlainText("")
                e.blockSignals(False)
            self.statusLine.show_neutral("Missing")
            return

        title, md, html, wtype = row
        self.lblTitle.setText(title or "World Detail")

        if wtype == "character":
            # Character summaries are built in show_item(); nothing to do here.
            self.statusLine.show_neutral("Viewing")
            return

        # Generic world item: update whichever subview is visible
        if v and v.isVisible():
            v.setHtml(html or "<i>(empty)</i>")
            self.statusLine.show_neutral("Viewing")
        if e and e.isVisible():
            e.blockSignals(True)
            e.setPlainText(md or "")
            e.blockSignals(False)
            self._dirty = False
            self.statusLine.show_neutral("Editing")
