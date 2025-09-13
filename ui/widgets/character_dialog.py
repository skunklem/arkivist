from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QComboBox, QLineEdit, QTextEdit,
    QToolButton, QMessageBox
)

# You can swap these icons with your asset set
def _icon_edit():   return QIcon.fromTheme("document-edit")
def _icon_delete(): return QIcon.fromTheme("edit-delete")
def _icon_plus():   return QIcon.fromTheme("list-add")

# Optional facet label presets
DEFAULT_TRAIT_LABELS = [
    "Role", "Archetype", "Eye Color", "Hair", "Height", "Build",
    "Flaw", "Bond", "Ideal", "Desire"
]

class CharacterDialog(QDialog):
    """Single-page character editor: Description + Traits table (inline add/edit/delete)."""

    def __init__(self, app, db, character_id: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.app = app
        self.db = db
        self.character_id = character_id
        self.setWindowTitle("Character Details")
        self.resize(820, 640)

        # ── header line: name + Close
        name = self._character_title()
        self.titleLabel = QLabel(f"<h2 style='margin:6px 0'>{name}</h2>")
        self.btnClose   = QPushButton("Close")
        self.btnClose.clicked.connect(self.accept)

        head = QHBoxLayout(); head.addWidget(self.titleLabel); head.addStretch(1); head.addWidget(self.btnClose)

        # ── scrollable body
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        body = QWidget(); self.scroll.setWidget(body)
        b = QVBoxLayout(body); b.setContentsMargins(12,12,12,12); b.setSpacing(16)

        # Description section (Markdown source in content_md)
        b.addWidget(QLabel("<b>Description</b>"))
        self.descEdit = QPlainTextEdit()
        self.descEdit.setPlaceholderText("Character backstory, summary, or notes (Markdown)…")
        b.addWidget(self.descEdit, 0)

        # Traits section
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Traits</b>"))
        header.addStretch(1)
        b.addLayout(header)

        self.traitsTable = QTableWidget(0, 5)
        self.traitsTable.setHorizontalHeaderLabels(["Trait", "Value", "Note", "", ""])
        self.traitsTable.horizontalHeader().setStretchLastSection(False)
        self.traitsTable.horizontalHeader().setDefaultSectionSize(180)
        self.traitsTable.setColumnWidth(3, 36)
        self.traitsTable.setColumnWidth(4, 36)
        self.traitsTable.verticalHeader().setVisible(False)
        self.traitsTable.setWordWrap(False)
        self.traitsTable.setSelectionBehavior(QTableWidget.SelectRows)
        self.traitsTable.setSelectionMode(QTableWidget.SingleSelection)
        self.traitsTable.setEditTriggers(QTableWidget.NoEditTriggers)
        b.addWidget(self.traitsTable, 1)

        # New trait row (inline)
        newRow = QHBoxLayout()
        self.addLabel = QComboBox(); self.addLabel.setEditable(True)
        self.addLabel.addItems(DEFAULT_TRAIT_LABELS)
        self.addValue = QLineEdit(); self.addValue.setPlaceholderText("Value")
        self.addNote  = QLineEdit(); self.addNote.setPlaceholderText("Note (hover shows full)")
        self.btnAdd   = QToolButton(); self.btnAdd.setIcon(_icon_plus()); self.btnAdd.setToolTip("Add Trait")
        newRow.addWidget(self.addLabel, 1)
        newRow.addWidget(self.addValue, 1)
        newRow.addWidget(self.addNote, 1)
        newRow.addWidget(self.btnAdd, 0, Qt.AlignRight)
        b.addLayout(newRow)

        # Footer save
        self.btnSave = QPushButton("Save")
        self.btnSave.clicked.connect(self._save_all)
        b.addWidget(self.btnSave, 0, Qt.AlignRight)

        # ── compose dialog
        root = QVBoxLayout(self)
        root.addLayout(head)
        root.addWidget(self.scroll, 1)

        # ── events
        self.btnAdd.clicked.connect(self._add_trait_inline)

        # initial load
        self._load_description()
        self._load_traits()

    # ────────────────────────────── Loaders ──────────────────────────────
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

    # ────────────────────────────── Row helpers ──────────────────────────────
    def _append_trait_row(self, facet_id: int, label: str, value: str, note: str):
        row = self.traitsTable.rowCount()
        self.traitsTable.insertRow(row)

        # label
        it_label = QTableWidgetItem(label); it_label.setData(Qt.UserRole, int(facet_id))
        self.traitsTable.setItem(row, 0, it_label)

        # value
        it_value = QTableWidgetItem(value)
        self.traitsTable.setItem(row, 1, it_value)

        # note (single line visual; full on tooltip)
        display = self._truncate(note, 64)
        it_note = QTableWidgetItem(display)
        it_note.setToolTip(note)
        self.traitsTable.setItem(row, 2, it_note)

        # edit button
        btnEdit = QToolButton(); btnEdit.setIcon(_icon_edit()); btnEdit.setToolTip("Edit")
        btnEdit.clicked.connect(lambda: self._edit_trait_at_row(row))
        self.traitsTable.setCellWidget(row, 3, btnEdit)

        # delete button
        btnDel = QToolButton(); btnDel.setIcon(_icon_delete()); btnDel.setToolTip("Delete")
        btnDel.clicked.connect(lambda: self._delete_trait_at_row(row))
        self.traitsTable.setCellWidget(row, 4, btnDel)

    @staticmethod
    def _truncate(s: str, n: int) -> str:
        return s if len(s) <= n else s[: max(0, n-1)] + "…"

    def _facet_id_at_row(self, row: int) -> Optional[int]:
        it = self.traitsTable.item(row, 0)
        if not it: return None
        fid = it.data(Qt.UserRole)
        return int(fid) if fid is not None else None

    # ────────────────────────────── Actions ──────────────────────────────
    def _add_trait_inline(self):
        label = self.addLabel.currentText().strip()
        value = self.addValue.text().strip()
        note  = self.addNote.text().strip()
        if not (label or value):
            return
        self.db.character_facet_insert(self.character_id, "trait", label=label, value=value, note=note)
        self.addValue.clear(); self.addNote.clear()
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
        # small inline popup using the same editor as before (reuse FacetEditDialog if you like),
        # or do quick inline editors here:
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox, QLineEdit, QTextEdit, QLabel
        dlg = QDialog(self); dlg.setWindowTitle("Edit Trait")
        le = QLineEdit(r["label"] or ""); ve = QLineEdit(r["value"] or ""); ne = QTextEdit(r["note"] or "")
        lay = QVBoxLayout(dlg); lay.addWidget(QLabel("Trait")); lay.addWidget(le)
        lay.addWidget(QLabel("Value")); lay.addWidget(ve)
        lay.addWidget(QLabel("Note"));  lay.addWidget(ne)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(btns)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        if dlg.exec() == QDialog.Accepted:
            self.db.character_facet_update(fid, label=le.text().strip(), value=ve.text().strip(), note=ne.toPlainText().strip())
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

    def _save_all(self):
        # Save description
        md = self.descEdit.toPlainText()
        self.db.conn.execute(
            "UPDATE world_items SET content_md=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (md, self.character_id)
        )
        self.db.conn.commit()
        # Optionally re-render right panel if it currently shows this character
        if getattr(self.app, "_current_world_id", None) == self.character_id:
            if hasattr(self.app, "show_world_item"):
                # re-open in view mode to refresh the render
                self.app.show_world_item(self.character_id, edit_mode=False)
        self.accept()
