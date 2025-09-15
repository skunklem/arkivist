import re

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QPlainTextEdit,QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QSizePolicy, QHBoxLayout,
)

import traceback
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

class WorldDetailWidget(QWidget):
    """Right panel: shows a world item in View/Edit with back/forward history."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(8, 8, 8, 8)

        self._history = []
        self._hindex  = -1
        self._current_world_id = None
        self._dirty = False

        self._add_top_bar()
        self._add_views()              # creates self.view / self.edit
        # Initial blank state: nothing selected; keep both hidden
        self.view.hide()
        self.edit.hide()
        self.modeBtn.setText("Edit")

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

    def _add_views(self):
        self.views_active = True

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._anchor_clicked)
        self.vbox.addWidget(self.view)

        self.edit = PlainNoTab()
        self.edit.setPlaceholderText("Markdown content…")
        self.edit.textChanged.connect(self._mark_dirty)
        self.vbox.addWidget(self.edit)

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

    def _anchor_clicked(self, url):
        # Expected url forms: world://<id>  OR  chapter://<id>
        try:
            s = url.toString()
            if s.startswith("world://"):
                wid = int(s.split("://",1)[1])
                self.app.show_world_item(wid)   # your main-window method
            elif s.startswith("chapter://"):
                cid = int(s.split("://",1)[1])
                self.app.open_chapter(cid)      # your method to focus a chapter
            else:
                # fall back to external open if needed
                QDesktopServices.openUrl(url)
        except Exception as e:
            print("anchor click error:", e)

    def _set_mode(self, *, view_mode: bool):
        """Show/hide view vs edit for NON-character items. For characters, the button always says 'Edit'."""
        # If header not built yet, bail quietly
        if not hasattr(self, "modeBtn"):
            return

        meta = self.app.db.world_item_meta(self._current_world_id) if self._current_world_id else None
        wtype = (meta["type"] if meta else "") or ""

        # Button label shows the alternate action
        if wtype == "character":
            self.modeBtn.setText("Edit")
            # character view: we don't show the generic view/edit widgets
            if hasattr(self, "view"):
                self.view.hide()
            if hasattr(self, "edit"):
                self.edit.hide()
            return

        # Generic world item
        self.modeBtn.setText("Edit" if view_mode else "View")
        if hasattr(self, "view") and hasattr(self, "edit"):
            self.view.setVisible(view_mode)
            self.edit.setVisible(not view_mode)

    def _mark_dirty(self):
        self._dirty = True

    def toggle_mode(self):
        """Toggles View/Edit for generic world items; opens dialog for characters."""
        if not self._current_world_id:
            return
        meta = self.app.db.world_item_meta(self._current_world_id)
        wtype = (meta["type"] or "")
        if wtype == "character":
            # open character dialog and refresh panel afterwards
            self.app.open_character_dialog(self._current_world_id)
            return

        # Non-character: if leaving edit -> save first
        currently_viewing = self.view.isVisible() if getattr(self, "views_active", False) else True
        if not currently_viewing:
            self._save_current_if_dirty()

        # Flip mode
        self._set_mode(view_mode=not currently_viewing)

        # IMPORTANT: always refresh the active pane (fills edit with MD or view with HTML)
        if getattr(self, "views_active", False):
            self._refresh_render_only()

    def _save_current_if_dirty(self):
        if not self._current_world_id or not self._dirty:
            return
        md = self.edit.toPlainText()
        cur = self.app.db.conn.cursor()
        cur.execute("UPDATE world_items SET content_md=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (md, self._current_world_id))
        self.app.db.conn.commit()
        self.app.rebuild_world_item_render(self._current_world_id)
        self._dirty = False

        if getattr(self.app, "_current_world_id", None) == self.character_id:
            self.app.show_world_item(self.character_id, edit_mode=False)

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

    def _clear(self):
        """Clear the panel content without replacing the root layout."""
        self.views_active = False
        if not hasattr(self, "vbox") or self.vbox is None:
            return
        # Remove every child item from self.vbox
        while self.vbox.count():
            item = self.vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            else:
                sub = item.layout()
                if sub:
                    self._delete_layout(sub)
                    sub.setParent(None)
        # Now self.vbox exists and is empty; safe to rebuild header/body

    def _refresh_render_only(self):
        """Update the rendered HTML/text into self.view/edit (no rebuilding UI)."""
        if not getattr(self, "views_active", False):
            return
        if self._current_world_id is None:
            self.view.setHtml("<i>Select a world item</i>")
            self.edit.setPlainText("")
            return

        cur = self.app.db.conn.cursor()
        cur.execute("SELECT content_md, content_render FROM world_items WHERE id=?", (self._current_world_id,))
        row = cur.fetchone()
        if not row:
            self.view.setHtml("<i>Item not found</i>")
            self.edit.setPlainText("")
            return

        md, html = row
        if self.view.isVisible():
            self.view.setHtml(html or "<i>(empty)</i>")
        elif self.edit.isVisible():
            self.edit.blockSignals(True)
            self.edit.setPlainText(md or "")
            self.edit.blockSignals(False)
            self._dirty = False

    def show_item(self, world_item_id: int, add_to_history: bool = True, view_mode: bool = True):
        """Load a world item. Saves edit if needed, updates history, rebuilds body once."""
        if getattr(self, "_building", False):
            return
        self._building = True
        try:
            # Save if leaving Edit mode
            self._save_current_if_dirty()

            # History maintenance
            if add_to_history:
                if self._hindex < len(self._history) - 1:
                    self._history = self._history[: self._hindex + 1]
                if not self._history or self._history[self._hindex] != world_item_id:
                    self._history.append(world_item_id)
                    self._hindex = len(self._history) - 1

            # Set current
            self._current_world_id = world_item_id
            self._dirty = False

            # Fetch meta once
            meta = self.app.db.world_item_meta(world_item_id)
            if not meta:
                self._clear()
                self._add_top_bar()
                self.lblTitle.setText("World Detail (missing)")
                self._update_nav_buttons()
                return

            title = meta["title"] or ""
            wtype = (meta["type"] or "").lower()
            md    = meta["content_md"] or ""

            # Full rebuild of the panel body to avoid leftover widgets/layouts
            self._clear()
            self._add_top_bar()
            self.lblTitle.setText(title)

            if wtype == "character":
                _safe(self._render_character_summary(world_item_id, title, md))
                self.modeBtn.setText("Edit")
            else:
                self._add_views()
                self._set_mode(view_mode=view_mode)   # ← now that header & widgets exist
                self._refresh_render_only()

            self._update_nav_buttons()

            # Sync selection in the tree
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

    def _render_character_summary(self, char_id: int, title: str, md: str):
        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<h3 style='margin:4px 0'>{title}</h3>"))
        header.addStretch(1)
        btnEdit = QPushButton("Edit…")
        btnEdit.clicked.connect(lambda: self.app.open_character_dialog(char_id))
        header.addWidget(btnEdit)
        self.vbox.addLayout(header)

        # Description
        self.vbox.addWidget(QLabel("<b>Character Description</b>"))
        html = md_to_html(md)  # your existing converter
        desc = QLabel(); desc.setTextFormat(Qt.RichText); desc.setWordWrap(True)
        desc.setText(html)
        self.vbox.addWidget(desc)

        # Traits (name + value)
        self.vbox.addWidget(QLabel("<b>Traits</b>"))
        traits = self.app.db.character_facets_by_type(char_id, "trait")
        if traits:
            table = QTableWidget(len(traits), 2)
            table.setHorizontalHeaderLabels(["Trait", "Value"])
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionMode(QTableWidget.NoSelection)
            table.horizontalHeader().setStretchLastSection(True)
            for r, tr in enumerate(traits):
                table.setItem(r, 0, QTableWidgetItem(tr["label"] or ""))
                table.setItem(r, 1, QTableWidgetItem(tr["value"] or ""))
            self.vbox.addWidget(table)
        else:
            self.vbox.addWidget(QLabel("<i>No traits yet</i>"))

        # Other facet groups (optional for now; pattern identical):
        # for kind in ("goal","belonging","affiliation","skill"):
        #     rows = self.app.db.character_facets_by_type(char_id, kind)
        #     if rows: render a simple 2-col table name/value

    def refresh(self):
        if self._current_world_id is None:
            self.lblTitle.setText("World Detail")
            if self.views_active:
                self.view.setHtml("<i>Select a world item</i>")
                self.edit.setPlainText("")
            return
        cur = self.app.db.conn.cursor()
        cur.execute("SELECT title, content_md, content_render FROM world_items WHERE id=?",
                    (self._current_world_id,))
        row = cur.fetchone()
        if not row:
            self.lblTitle.setText("World Detail (missing)")
            if self.views_active:
                self.view.setHtml("<i>Item not found</i>")
                self.edit.setPlainText("")
            return
        title, md, html = row
        # self.lblTitle.setText(title or "")
        if self.views_active:
            if self.view.isVisible():
                self.view.setHtml(html or "<i>(empty)</i>")
            elif self.edit.isVisible():
                self.edit.blockSignals(True)
                self.edit.setPlainText(md or "")
                self.edit.blockSignals(False)
                self._dirty = False
            # else:
            #     html = self._render_character_summary_md(char_id=self._current_world_id, title=title, md=md)
            #     self.character_view.setHtml(html)
