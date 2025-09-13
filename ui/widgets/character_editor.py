# ui/widgets/character_editor.py
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QListWidget, QListWidgetItem, QAbstractItemView, QWidget, QLineEdit,
    QTextEdit, QDialogButtonBox, QMessageBox
)

class FacetEditDialog(QDialog):
    """Small popup for adding/editing a single facet (Traits for v1)."""
    def __init__(self, parent: QWidget, *, title: str = "Edit Trait",
                 label: str = "", value: str = "", note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 260)

        self.labelEdit = QLineEdit(label)
        self.valueEdit = QLineEdit(value)
        self.noteEdit  = QTextEdit(note)

        form = QVBoxLayout(self)
        form.addWidget(QLabel("Label"))
        form.addWidget(self.labelEdit)
        form.addWidget(QLabel("Value"))
        form.addWidget(self.valueEdit)
        form.addWidget(QLabel("Note (optional)"))
        form.addWidget(self.noteEdit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addWidget(btns)

    def get_data(self) -> tuple[str, str, str]:
        return (
            self.labelEdit.text().strip(),
            self.valueEdit.text().strip(),
            self.noteEdit.toPlainText().strip(),
        )


class ReorderableList(QListWidget):
    """QListWidget that supports drag-to-reorder, and emits a callback on drop."""
    def __init__(self, on_reordered, parent=None):
        super().__init__(parent)
        self.on_reordered = on_reordered
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setUniformItemSizes(True)
        self.setAlternatingRowColors(True)

    def dropEvent(self, event):
        super().dropEvent(event)
        # After the internal move, collect ids and call back
        ids = []
        for i in range(self.count()):
            it = self.item(i)
            fid = it.data(Qt.UserRole)
            if fid is not None:
                ids.append(int(fid))
        if callable(self.on_reordered):
            self.on_reordered(ids)


class CharacterEditorDialog(QDialog):
    """
    Minimal Character Editor:
      - Title bar shows character name
      - Traits tab: list + Add/Edit/Delete + drag reorder
      - Hooks into Database.*character_facets* API
    """
    def __init__(self, app, db, character_id: int, parent: Optional[QWidget]=None):
        super().__init__(parent)
        self.app = app
        self.db = db
        self.character_id = character_id
        self.setWindowTitle("Edit Character")
        self.resize(720, 520)

        # Title / header
        self.nameLabel = QLabel(self._character_title_html())
        self.nameLabel.setTextFormat(Qt.RichText)

        # Tabs
        self.tabs = QTabWidget()
        self._tab_traits = QWidget(); self._build_traits_tab(self._tab_traits)
        self.tabs.addTab(self._tab_traits, "Traits")
        # (Later: Goals, Belongings, Affiliations, Skills, Bio …)

        # Footer
        self.btnClose = QPushButton("Close")
        self.btnClose.clicked.connect(self.accept)

        root = QVBoxLayout(self)
        root.addWidget(self.nameLabel)
        root.addWidget(self.tabs, 1)
        root.addWidget(self.btnClose, alignment=Qt.AlignRight)

        # Initial load
        self._reload_traits()

    # ---------- header ----------
    def _character_title_html(self) -> str:
        c = self.db.conn.cursor()
        c.execute("SELECT title FROM world_items WHERE id=?", (self.character_id,))
        row = c.fetchone()
        title = row["title"] if row else "Character"
        return f"<h2 style='margin:6px 0'>{title}</h2>"

    # ---------- Traits tab ----------
    def _build_traits_tab(self, tab: QWidget):
        v = QVBoxLayout(tab); v.setContentsMargins(8,8,8,8)

        # toolbar
        bar = QHBoxLayout(); bar.setContentsMargins(0,0,0,0)
        self.btnAddTrait = QPushButton("Add Trait")
        self.btnEditTrait = QPushButton("Edit")
        self.btnDelTrait = QPushButton("Delete")
        bar.addWidget(self.btnAddTrait)
        bar.addWidget(self.btnEditTrait)
        bar.addWidget(self.btnDelTrait)
        bar.addStretch(1)

        # list
        self.traitsList = ReorderableList(on_reordered=self._reorder_traits)

        v.addLayout(bar)
        v.addWidget(self.traitsList, 1)

        # hooks
        self.btnAddTrait.clicked.connect(self._add_trait)
        self.btnEditTrait.clicked.connect(self._edit_selected_trait)
        self.btnDelTrait.clicked.connect(self._delete_selected_trait)
        self.traitsList.itemDoubleClicked.connect(lambda _: self._edit_selected_trait())

    def _reload_traits(self):
        self.traitsList.clear()
        rows = self.db.character_facets_by_type(self.character_id, "trait")
        for r in rows:
            label = (r["label"] or "").strip()
            value = (r["value"] or "").strip()
            note  = (r["note"]  or "").strip()
            text  = f"{label}: {value}" if label and value else (label or value or "—")
            if note:
                text += f"  —  {note}"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, int(r["id"]))
            self.traitsList.addItem(it)

    # --- CRUD handlers ---
    def _add_trait(self):
        dlg = FacetEditDialog(self, title="Add Trait")
        if dlg.exec() != QDialog.Accepted:
            return
        label, value, note = dlg.get_data()
        if not (label or value):
            return
        self.db.character_facet_insert(
            self.character_id, facet_type="trait",
            label=label, value=value, note=note, insert_index=None
        )
        self._reload_traits()

    def _current_facet_id(self) -> Optional[int]:
        it = self.traitsList.currentItem()
        return int(it.data(Qt.UserRole)) if it else None

    def _edit_selected_trait(self):
        fid = self._current_facet_id()
        if not fid:
            return
        # fetch current
        c = self.db.conn.cursor()
        c.execute("SELECT label, value, note FROM character_facets WHERE id=?", (fid,))
        row = c.fetchone()
        if not row:
            return
        dlg = FacetEditDialog(self, title="Edit Trait", label=row["label"] or "",
                              value=row["value"] or "", note=row["note"] or "")
        if dlg.exec() != QDialog.Accepted:
            return
        label, value, note = dlg.get_data()
        if not (label or value):
            # either allow blank or delete; here, allow blank
            pass
        self.db.character_facet_update(fid, label=label, value=value, note=note)
        self._reload_traits()

    def _delete_selected_trait(self):
        fid = self._current_facet_id()
        if not fid:
            return
        if QMessageBox.question(self, "Delete Trait",
                                "Delete this trait?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.db.character_facet_delete(fid)
        self._reload_traits()

    def _reorder_traits(self, new_order_facet_ids: list[int]):
        # Persist new order for this character
        self.db.character_facets_reorder(self.character_id, new_order_facet_ids)
        # no need to reload; QListWidget already reflects order, but safe to sync:
        # self._reload_traits()
