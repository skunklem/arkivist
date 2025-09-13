# ui/widgets/character_dialog.py
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QComboBox, QLineEdit,
    QToolButton, QMessageBox, QDialogButtonBox
)

from ui.widgets.helpers import PlainNoTab

def _icon_edit():
    icon = QIcon.fromTheme("document-edit")
    if icon.isNull():
        icon = QIcon.fromTheme("edit")
    return icon
def _icon_delete():
    icon =QIcon.fromTheme("edit-delete")
    if icon.isNull():
        icon = QIcon.fromTheme("delete")
    return icon
def _icon_plus():
    icon = QIcon.fromTheme("list-add")
    if icon.isNull():
        icon = QIcon.fromTheme("add")
    return icon

DEFAULT_TRAIT_LABELS = [
    "Role", "Archetype", "Eye Color", "Hair", "Height", "Build",
    "Flaw", "Bond", "Ideal", "Desire"
]

class CharacterDialog(QDialog):
    """
    Single-page character editor: Name, Description (MD), Traits table (inline add/edit/delete).
    - Pressing Enter in any "new trait" field commits the row.
    - Save/Cancel at bottom; Save only enabled when dirty.
    - On save/close, right panel (world detail) is refreshed if it shows this character.
    """
    def __init__(self, app, db, character_id: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.app = app
        self.db = db
        self.character_id = character_id
        self._dirty = False
        self.setWindowTitle("Character Details")
        self.resize(860, 640)

        # ── header: editable name
        name = self._character_title()
        self.nameEdit = QLineEdit(name)
        self.nameEdit.setPlaceholderText("Character name")
        self.nameEdit.textEdited.connect(self._mark_dirty)

        # Close/Cancel/Save buttons (Save enabled iff dirty)
        self.btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        # self.btnSave   = self.btns.button(QDialogButtonBox.Save)
        # self.btnCancel = self.btns.button(QDialogButtonBox.Cancel)
        # self.btnSave.setEnabled(False)
        self.btns.accepted.connect(self._save_all_and_close)
        self.btns.rejected.connect(self.reject)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Name</b>"))
        head.addWidget(self.nameEdit, 1)
        head.addStretch(1)
        head.addWidget(self.btns)

        # ── scrollable body
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)

        body = QWidget(); self.scroll.setWidget(body)
        b = QVBoxLayout(body); b.setContentsMargins(12,12,12,12); b.setSpacing(16)

        # Description section (Markdown source in content_md)
        b.addWidget(QLabel("<b>Description</b>"))
        self.descEdit = PlainNoTab()
        self.descEdit.setPlaceholderText("Character backstory, summary, or notes (Markdown)…")
        self.descEdit.textChanged.connect(self._mark_dirty)
        b.addWidget(self.descEdit, 1)

        # Traits section
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<b>Traits</b>"))
        hdr.addStretch(1)
        b.addLayout(hdr)

        self.traitsTable = QTableWidget(0, 5)
        self.traitsTable.setHorizontalHeaderLabels(["Trait", "Value", "Note", "", ""])
        self.traitsTable.verticalHeader().setVisible(False)
        self.traitsTable.setEditTriggers(QTableWidget.NoEditTriggers)
        self.traitsTable.setSelectionBehavior(QTableWidget.SelectRows)
        self.traitsTable.setSelectionMode(QTableWidget.SingleSelection)
        self.traitsTable.horizontalHeader().setStretchLastSection(False)
        self.traitsTable.horizontalHeader().setDefaultSectionSize(200)
        self.traitsTable.setColumnWidth(3, 36)
        self.traitsTable.setColumnWidth(4, 36)
        b.addWidget(self.traitsTable, 2)

        # New trait inline row (auto-commit on Enter)
        rowAdd = QHBoxLayout()
        self.addLabel = QComboBox(); self.addLabel.setEditable(True)
        self.addLabel.addItems(DEFAULT_TRAIT_LABELS)
        self.addValue = QLineEdit(); self.addValue.setPlaceholderText("Value")
        self.addNote  = QLineEdit(); self.addNote.setPlaceholderText("Note (hover shows full)")
        self.btnAdd   = QToolButton(); self.btnAdd.setIcon(_icon_plus()); self.btnAdd.setToolTip("Add trait")
        rowAdd.addWidget(self.addLabel, 1)
        rowAdd.addWidget(self.addValue, 1)
        rowAdd.addWidget(self.addNote, 2)
        rowAdd.addWidget(self.btnAdd, 0, Qt.AlignRight)
        b.addLayout(rowAdd)

        # Save/Cancel at bottom (also in header; this duplicates but keeps an easy reach)
        self.btnSaveBottom = QPushButton("Save")
        self.btnSaveBottom.setEnabled(False)
        self.btnSaveBottom.clicked.connect(self._save_all_and_close)
        b.addWidget(self.btnSaveBottom, 0, Qt.AlignRight)

        # Prevent QScrollArea internals from taking focus
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.viewport().setFocusPolicy(Qt.NoFocus)
        if self.scroll.horizontalScrollBar():
            self.scroll.horizontalScrollBar().setFocusPolicy(Qt.NoFocus)
        if self.scroll.verticalScrollBar():
            self.scroll.verticalScrollBar().setFocusPolicy(Qt.NoFocus)

        # Defer tab order
        QTimer.singleShot(0, self._apply_tab_order)

        # Compose dialog
        root = QVBoxLayout(self)
        root.addLayout(head)
        root.addWidget(self.scroll, 1)

        # Hooks
        self.btnAdd.clicked.connect(self._add_trait_inline)
        # Enter to add from any of the three fields
        self.addLabel.lineEdit().returnPressed.connect(self._add_trait_inline)
        self.addValue.returnPressed.connect(self._add_trait_inline)
        self.addNote.returnPressed.connect(self._add_trait_inline)

        # Initial data
        self._load_description()
        self._load_traits()

    def _apply_tab_order(self):
        # name -> description -> new-trait row -> buttons
        try:
            self.setTabOrder(self.nameEdit, self.descEdit)
            # self.setTabOrder(self.descEdit, self.addLabel.lineEdit())
            # self.setTabOrder(self.addLabel.lineEdit(), self.addValue)
            self.setTabOrder(self.descEdit, self.addLabel)
            self.setTabOrder(self.addLabel, self.addValue)
            self.setTabOrder(self.addValue, self.addNote)
            self.setTabOrder(self.addNote, self.btnAdd)
            # use the BOTTOM buttons to avoid mixing top header/buttonbox
            self.setTabOrder(self.btnAdd, self.btnSaveBottom)
            # if you also have a bottom cancel, put it here; if not, remove this line
            if hasattr(self, "btnCancelBottom"):
                self.setTabOrder(self.btnSaveBottom, self.btnCancelBottom)
        except Exception:
            # silent guard: in case any widget got renamed/removed
            pass

    # ── data loads
    def _character_title(self) -> str:
        c = self.db.conn.cursor()
        c.execute("SELECT title FROM world_items WHERE id=?", (self.character_id,))
        row = c.fetchone()
        return row["title"] if row else "Character"

    def _load_description(self):
        c = self.db.conn.cursor()
        c.execute("SELECT content_md FROM world_items WHERE id=?", (self.character_id,))
        row = c.fetchone()
        self.descEdit.setPlainText(row["content_md"] or "" if row else "")

    def _load_traits(self):
        self.traitsTable.setRowCount(0)
        rows = self.db.character_facets_by_type(self.character_id, "trait")
        for r in rows:
            self._append_trait_row(r["id"], r["label"] or "", r["value"] or "", r["note"] or "")

    # ── table helpers
    def _append_trait_row(self, facet_id: int, label: str, value: str, note: str):
        r = self.traitsTable.rowCount()
        self.traitsTable.insertRow(r)

        itL = QTableWidgetItem(label); itL.setData(Qt.UserRole, int(facet_id))
        itV = QTableWidgetItem(value)
        itN = QTableWidgetItem(self._truncate(note, 64)); itN.setToolTip(note)

        self.traitsTable.setItem(r, 0, itL)
        self.traitsTable.setItem(r, 1, itV)
        self.traitsTable.setItem(r, 2, itN)

        btnE = QToolButton(); btnE.setIcon(_icon_edit()); btnE.setToolTip("Edit")
        btnD = QToolButton(); btnD.setIcon(_icon_delete()); btnD.setToolTip("Delete")
        btnE.clicked.connect(lambda: self._edit_trait_at_row(r))
        btnD.clicked.connect(lambda: self._delete_trait_at_row(r))
        self.traitsTable.setCellWidget(r, 3, btnE)
        self.traitsTable.setCellWidget(r, 4, btnD)

    @staticmethod
    def _truncate(s: str, n: int) -> str:
        return s if len(s) <= n else s[: max(0, n-1)] + "…"

    def _facet_id_at_row(self, row: int) -> Optional[int]:
        it = self.traitsTable.item(row, 0)
        if not it: return None
        v = it.data(Qt.UserRole)
        return int(v) if v is not None else None

    # ── actions
    def _add_trait_inline(self):
        label = self.addLabel.currentText().strip()
        value = self.addValue.text().strip()
        note  = self.addNote.text().strip()
        if not (label or value):
            return
        self.db.character_facet_insert(self.character_id, "trait", label=label, value=value, note=note)
        self.addValue.clear(); self.addNote.clear()
        self._mark_dirty()
        self._load_traits()

    def _edit_trait_at_row(self, row: int):
        fid = self._facet_id_at_row(row)
        if not fid:
            return
        cur = self.db.conn.cursor()
        cur.execute("SELECT label, value, note FROM character_facets WHERE id=?", (fid,))
        r = cur.fetchone()
        if not r:
            return
        # inline dialog
        dlg = QDialog(self); dlg.setWindowTitle("Edit Trait")
        v = QVBoxLayout(dlg)
        le = QLineEdit(r["label"] or ""); ve = QLineEdit(r["value"] or ""); ne = PlainNoTab(r["note"] or "")
        for w,label in ((le,"Trait"), (ve,"Value"), (ne,"Note")):
            v.addWidget(QLabel(label)); v.addWidget(w)
        db = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(db)
        db.accepted.connect(dlg.accept); db.rejected.connect(dlg.reject)
        # enter triggers OK for line edits
        for w in (le, ve): w.returnPressed.connect(dlg.accept)
        if dlg.exec() == QDialog.Accepted:
            self.db.character_facet_update(fid,
                label=le.text().strip(), value=ve.text().strip(), note=ne.toPlainText().strip())
            self._mark_dirty()
            self._load_traits()

    def _delete_trait_at_row(self, row: int):
        fid = self._facet_id_at_row(row)
        if not fid:
            return
        if QMessageBox.question(self, "Delete Trait", "Delete this trait?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.db.character_facet_delete(fid)
        self.traitsTable.removeRow(row)
        self._mark_dirty()

    # ── save/close
    def _save_all_and_close(self):
        self._save_all()
        self.accept()

    def _save_all(self):
        if not self._dirty:
            return
        # update name + description
        name = self.nameEdit.text().strip()
        md   = self.descEdit.toPlainText()
        self.db.conn.execute(
            "UPDATE world_items SET title=?, content_md=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, md, self.character_id)
        )
        self.db.conn.commit()
        # refresh right panel if it's showing this character
        if getattr(self.app, "_current_world_id", None) == self.character_id:
            if hasattr(self.app, "show_world_item"):
                self.app.show_world_item(self.character_id, edit_mode=False)
        # keep tree label in sync if you show titles there
        if hasattr(self.app, "reload_world_tree"):
            self.app.reload_world_tree(keep_expansion=True, focus_id=self.character_id)
        self._dirty = False
        # self.btnSave.setEnabled(False)
        self.btnSaveBottom.setEnabled(False)

    def _mark_dirty(self):
        self._dirty = True
        # self.btnSave.setEnabled(True)
        self.btnSaveBottom.setEnabled(True)
