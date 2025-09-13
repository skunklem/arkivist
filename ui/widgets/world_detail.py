import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QPlainTextEdit,QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QSizePolicy, QHBoxLayout,
)

from utils.md import md_to_html

class WorldDetailWidget(QWidget):
    """Right panel: shows a world item in View/Edit with back/forward history."""
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(8,8,8,8)
        self.vbox_count_blank = self.vbox.count()

        # Top bar: Back / Forward / Title / View|Edit toggle
        self.top = QHBoxLayout()
        self.btnBack = QPushButton("←")
        self.btnFwd  = QPushButton("→")
        self.btnBack.setFixedWidth(32)
        self.btnFwd.setFixedWidth(32)
        self.btnBack.clicked.connect(self.go_back)
        self.btnFwd.clicked.connect(self.go_forward)

        self.lblTitle = QLabel("Side Notes")

        # Single toggle button: shows "Edit" when in View mode, and "View" when in Edit mode
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

        # Views
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)                # important: we handle anchorClicked ourselves
        self.view.anchorClicked.connect(self._anchor_clicked)
        self.vbox.addWidget(self.view)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Markdown content…")
        self.edit.textChanged.connect(self._mark_dirty)
        self.vbox.addWidget(self.edit)

        self.character_view = QTextBrowser()
        self.character_view.setOpenExternalLinks(False)
        self.character_view.setOpenLinks(False)       # important: we handle anchorClicked ourselves
        self.character_view.anchorClicked.connect(self._anchor_clicked)
        self.vbox.addWidget(self.character_view)

        self._history = []
        self._hindex  = -1
        self._current_world_id = None
        self._dirty = False           # tracks unsaved edits

        # default to View
        self._set_mode(view_mode=True)

    def _update_nav_buttons(self):
        self.btnBack.setEnabled(self._hindex > 0)
        self.btnFwd.setEnabled(self._hindex >= 0 and self._hindex < len(self._history)-1)

    def go_back(self):
        if self._hindex > 0:
            # Save current edits before navigating
            self._save_current_if_dirty()
            new_index = self._hindex - 1
            target_id = self._history[new_index]
            self._hindex = new_index
            self.show_item(target_id, add_to_history=False)

    def go_forward(self):
        if self._hindex < len(self._history) - 1:
            # Save current edits before navigating
            self._save_current_if_dirty()
            new_index = self._hindex + 1
            target_id = self._history[new_index]
            self._hindex = new_index
            self.show_item(target_id, add_to_history=False)

    def _anchor_clicked(self, url):
        # We use 'world:<id>' scheme to avoid authority parsing
        s = url.toString()
        m = re.match(r"^world:(\d+)$", s)
        if m:
            wid = int(m.group(1))
            self.show_item(wid)

    def _set_mode(self, view_mode: bool):
        # Button label shows the *other* state you can switch to
        if not self._current_world_id:
            self.character_view.setVisible(False)
            self.view.setVisible(view_mode)
            self.edit.setVisible(not view_mode)
            self.modeBtn.setText("Edit" if view_mode else "View")
            return
        item_type=self.app.db.world_item_meta(self._current_world_id)["type"]
        if item_type == "character":
            self.character_view.setVisible(view_mode)
            self.edit.setVisible(False)
            self.view.setVisible(False)
            self.modeBtn.setText("Edit")
            if not view_mode:
                # open character editor #TODO: doesn't open editor - find out why
                self.app.open_character_editor(self._current_world_id)
                
        else:
            self.character_view.setVisible(False)
            self.view.setVisible(view_mode)
            self.edit.setVisible(not view_mode)
            self.modeBtn.setText("Edit" if view_mode else "View")

    def _mark_dirty(self):
        self._dirty = True

    def toggle_mode(self):
        # If switching from Edit -> View, save first (if dirty)
        viewing_currently = self.view.isVisible()
        if not viewing_currently:
            # if currently editing, save before switching to view mode
            self._save_current_if_dirty()
        # toggle
        self._set_mode(view_mode=not viewing_currently)
        self.refresh()  # reload rendered view if we just saved

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

    def show_item(self, world_item_id: int, add_to_history: bool = True, view_mode: bool = True):
        """Load a world item. Saves current edits if needed.
        If add_to_history=False, do not push a new entry (used by back/forward)."""
        # If leaving Edit mode with unsaved changes, save first
        self._save_current_if_dirty()

        # History
        if add_to_history:
            if self._hindex < len(self._history) - 1:
                self._history = self._history[:self._hindex+1]
            if not self._history or self._history[self._hindex] != world_item_id:
                self._history.append(world_item_id)
                self._hindex = len(self._history) - 1
        else:
            # move within existing history (index is set by caller)
            pass

        # Load target
        self._current_world_id = world_item_id
        self._dirty = False

        # Always land in View mode when switching items
        self._set_mode(view_mode=view_mode)

        self.refresh()
        self._update_nav_buttons()

        item = self.app.db.world_item_meta(world_item_id)
        wtitle = item["title"]
        wtype = item["type"]
        md = item["content_md"] or ""

        # Focus the item in the world tree
        # if wtype == "character":
        #     self._render_character_summary(world_item_id, wtitle, md)
        # else:
        if hasattr(self.app, "focus_world_item_in_tree"):
            self.app.focus_world_item_in_tree(world_item_id)

    def _render_character_summary(self, char_id: int, title: str, md: str):
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

        # print("rendering character summary")
        # header = QHBoxLayout()
        # header.addWidget(QLabel(f"<h3 style='margin:4px 0'>{title}</h3>"))
        # header.addStretch(1)
        # btnEdit = QPushButton("Edit…")
        # btnEdit.clicked.connect(lambda: self.app.open_character_dialog(char_id))
        # header.addWidget(btnEdit)
        # self.vbox.addLayout(header)

        # # Description
        # self.vbox.addWidget(QLabel("<b>Character Description</b>"))
        # html = md_to_html(md)
        # desc = QLabel()
        # desc.setTextFormat(Qt.RichText)
        # desc.setWordWrap(True)
        # desc.setText(html)
        # self.vbox.addWidget(desc)

        # # Traits (name + value)
        # self.vbox.addWidget(QLabel("<b>Traits</b>"))
        # if traits:
        #     table = QTableWidget(len(traits), 2)
        #     table.setHorizontalHeaderLabels(["Trait", "Value"])
        #     table.verticalHeader().setVisible(False)
        #     table.setEditTriggers(QTableWidget.NoEditTriggers)
        #     table.setSelectionMode(QTableWidget.NoSelection)
        #     table.horizontalHeader().setStretchLastSection(True)
        #     for r, tr in enumerate(traits):
        #         table.setItem(r, 0, QTableWidgetItem(tr["label"] or ""))
        #         table.setItem(r, 1, QTableWidgetItem(tr["value"] or ""))
        #     self.vbox.addWidget(table)
        # else:
        #     self.vbox.addWidget(QLabel("<i>No traits yet</i>"))

        # Other facet groups (optional for now; pattern identical):
        # for kind in ("goal","belonging","affiliation","skill"):
        #     rows = self.app.db.character_facets_by_type(char_id, kind)
        #     if rows: render a simple 2-col table name/value

    def refresh(self):
        if self._current_world_id is None:
            self.lblTitle.setText("World Detail")
            self.view.setHtml("<i>Select a world item</i>")
            self.edit.setPlainText("")
            return
        cur = self.app.db.conn.cursor()
        cur.execute("SELECT title, content_md, content_render FROM world_items WHERE id=?",
                    (self._current_world_id,))
        row = cur.fetchone()
        if not row:
            self.lblTitle.setText("World Detail (missing)")
            self.view.setHtml("<i>Item not found</i>")
            self.edit.setPlainText("")
            return
        title, md, html = row
        self.lblTitle.setText(title or "")
        if self.view.isVisible():
            self.view.setHtml(html or "<i>(empty)</i>")
        elif self.edit.isVisible():
            self.edit.blockSignals(True)
            self.edit.setPlainText(md or "")
            self.edit.blockSignals(False)
            self._dirty = False
        else:
            html = self._render_character_summary(char_id=self._current_world_id, title=title, md=md)
            self.character_view.setHtml(html)