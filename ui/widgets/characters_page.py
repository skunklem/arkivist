from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                               QToolButton, QStackedWidget, QListWidget, QListWidgetItem,
                               QScrollArea, QLabel, QFrame)

class CharactersPage(QWidget):
    characterOpenRequested = Signal(int)   # emit char_id for “open editor”

    def __init__(self, app, db, parent=None):
        super().__init__(parent)
        self.app = app
        self.db = db
        self._current_view = "grid"  # or "list"
        self._project_id = getattr(app, "_current_project_id", None)

        # Top bar
        top = QWidget(); th = QHBoxLayout(top); th.setContentsMargins(0,0,0,0)
        self.searchEdit = QLineEdit(); self.searchEdit.setPlaceholderText("Search characters…")
        self.btnGrid = QToolButton(); self.btnGrid.setText("Grid")
        self.btnList = QToolButton(); self.btnList.setText("List")
        self.btnNew  = QPushButton("New Character")
        th.addWidget(self.searchEdit, 1)
        th.addWidget(self.btnGrid)
        th.addWidget(self.btnList)
        th.addWidget(self.btnNew)

        # Content
        self.stack = QStackedWidget()
        # Grid
        self.gridWrap = QScrollArea(); self.gridWrap.setWidgetResizable(True)
        self.gridInner = QWidget(); gv = QVBoxLayout(self.gridInner); gv.setContentsMargins(8,8,8,8); gv.setSpacing(8)
        self.gridWrap.setWidget(self.gridInner)
        # List
        self.listWrap = QWidget(); lw = QHBoxLayout(self.listWrap); lw.setContentsMargins(0,0,0,0)
        self.listWidget = QListWidget()
        self.detailPane = QLabel("Select a character")  # replace later with rich read-only panel
        lw.addWidget(self.listWidget, 0)
        lw.addWidget(self.detailPane, 1)

        self.stack.addWidget(self.gridWrap)  # index 0
        self.stack.addWidget(self.listWrap)  # index 1

        # Layout
        root = QVBoxLayout(self)
        root.addWidget(top)
        root.addWidget(self.stack, 1)

        # Hooks
        self.btnGrid.clicked.connect(lambda: self.set_view("grid"))
        self.btnList.clicked.connect(lambda: self.set_view("list"))
        self.btnNew.clicked.connect(self._new_character)
        self.searchEdit.textChanged.connect(self.refresh)

        self.set_view("grid")

    def set_project(self, project_id: int):
        self._project_id = project_id
        self.refresh()

    def set_view(self, mode: str):
        self._current_view = mode
        self.stack.setCurrentIndex(0 if mode == "grid" else 1)
        self.refresh()

    def refresh(self):
        if not self._project_id:
            return
        # Fetch characters
        rows = self._list_characters(self._project_id, self.searchEdit.text().strip())
        if self._current_view == "grid":
            self._render_grid(rows)
        else:
            self._render_list(rows)

    def _list_characters(self, project_id: int, q: str):
        c = self.db.conn.cursor()
        if q:
            c.execute("""SELECT id, title FROM world_items
                         WHERE project_id=? AND COALESCE(deleted,0)=0 AND type='character'
                           AND title LIKE ?
                         ORDER BY title, id""", (project_id, f"%{q}%"))
        else:
            c.execute("""SELECT id, title FROM world_items
                         WHERE project_id=? AND COALESCE(deleted,0)=0 AND type='character'
                         ORDER BY title, id""", (project_id,))
        return c.fetchall()

    # --- GRID ---
    def _render_grid(self, rows):
        # clear
        while self.gridInner.layout().count():
            it = self.gridInner.layout().takeAt(0)
            w = it.widget()
            if w: w.deleteLater()

        # minimal cards (name + 2–3 facets)
        for r in rows:
            wid = int(r["id"]); title = r["title"]
            card = self._make_card(wid, title)
            self.gridInner.layout().addWidget(card)
        self.gridInner.layout().addStretch(1)

    def _make_card(self, char_id: int, title: str):
        box = QFrame(); box.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(box); v.setContentsMargins(10,10,10,10)
        name = QLabel(f"<b>{title}</b>")
        small = QLabel(self._card_facets_summary(char_id))  # e.g. “Role: Protagonist | Goal: Escape | Affil: Guild”
        openBtn = QPushButton("Open")
        openBtn.clicked.connect(lambda: self.characterOpenRequested.emit(char_id))
        v.addWidget(name)
        v.addWidget(small)
        v.addWidget(openBtn, alignment=Qt.AlignRight)
        return box

    def _card_facets_summary(self, char_id: int) -> str:
        rows = self.db.character_facets(char_id)
        role = next((r["value"] for r in rows if r["facet_type"] == "trait" and (r["label"] or "").lower() in ("role","archetype")), None)
        goal = next((r["value"] for r in rows if r["facet_type"] == "goal"), None)
        aff  = next((r["label"] or r["value"] for r in rows if r["facet_type"] == "affiliation"), None)
        bits = []
        if role: bits.append(f"Role: {role}")
        if goal: bits.append(f"Goal: {goal}")
        if aff:  bits.append(f"Affil: {aff}")
        return " | ".join(bits) or "—"

    # --- LIST ---
    def _render_list(self, rows):
        self.listWidget.clear()
        for r in rows:
            it = QListWidgetItem(r["title"])
            it.setData(Qt.UserRole, int(r["id"]))
            self.listWidget.addItem(it)
        self.listWidget.itemClicked.connect(self._open_in_detail)

    def _open_in_detail(self, item: QListWidgetItem):
        char_id = int(item.data(Qt.UserRole))
        # For v1, keep it simple:
        self.detailPane.setText(f"<h2>{item.text()}</h2><p>(Read-only character page preview)</p>")
        # Later: swap in a rich read-only widget mirroring your dialog layout

    def _new_character(self):
        pid = self._project_id
        if not pid: return
        # simple: create empty and open editor
        c = self.db.conn.cursor()
        c.execute("""INSERT INTO world_items(project_id, type, title, content_md, content_render)
                     VALUES(?, 'character', 'New Character', '', '')""", (pid,))
        self.db.conn.commit()
        new_id = int(c.lastrowid)
        self.characterOpenRequested.emit(new_id)
        self.refresh()
