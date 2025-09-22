# ui/widgets/character_dialog.py
from __future__ import annotations
import sqlite3
from typing import List, Optional, Union
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QComboBox, QLineEdit,
    QToolButton, QMessageBox, QDialogButtonBox, QHeaderView, QFrame,
    QAbstractScrollArea, QSizePolicy, QMenu
)

from ui.widgets.helpers import PlainNoTab, auto_grow_plaintext, fit_table_height_to_rows
from ui.widgets.inline_alias_table import InlineAliasTable
from ui.widgets.inline_facet_table import InlineFacetTable
from ui.widgets.delegates import AliasTypeDelegate
from ui.widgets.inline_alias_table import TYPE_COL, ALIAS_COL
from ui.widgets.common import StatusLine

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
        # self.setWindowTitle("Character Details")
        self.setWindowTitle(f"Character Details — {self.db.world_item(character_id)} [*]")
        self.resize(860, 640)

        # ── header: editable name
        self.nameEdit = QLineEdit()
        self.nameEdit.setPlaceholderText("Character name")
        self.nameEdit.textEdited.connect(self._mark_dirty)
        self.nameEdit.textEdited.connect(lambda _: self._mark_dirty_status())

        # Description section (Markdown source in content_md)
        self.descEdit = PlainNoTab()
        self.descEdit.setPlaceholderText("Character backstory, summary, or notes (Markdown)…")
        auto_grow_plaintext(self.descEdit)
        self.descEdit.textChanged.connect(lambda: self._mark_dirty_status())

        # aliases (with types)
        self.aliasTable = InlineAliasTable(lambda: self.db.alias_types_for_project(self.app._current_project_id))
        self.setup_alias_table(self.aliasTable)

        # facet tables
        labels_physical  = self.db.facet_template_labels(self.app._current_project_id, "trait_physical")
        self.tblPhysical   = InlineFacetTable(self.app, labels_physical)
        self.setup_facet_table(self.tblPhysical, "trait_physical")

        labels_character = self.db.facet_template_labels(self.app._current_project_id, "trait_character")
        self.tblCharacter  = InlineFacetTable(self.app, labels_character)
        self.setup_facet_table(self.tblCharacter, "trait_character")

        # Ensure table changes impact staus line
        self.setup_tables_mark_dirty([self.aliasTable, self.tblPhysical, self.tblCharacter])

        # footer buttons
        self.btnSave  = QPushButton("Save")
        self.btnClose = QPushButton("Cancel")
        self.btnSave.clicked.connect(self._save_and_close)
        self.btnClose.clicked.connect(self._cancel_dialog)
        footer_row = QHBoxLayout(); footer_row.addStretch(1); footer_row.addWidget(self.btnSave); footer_row.addWidget(self.btnClose)

        # --- Dialog layout ---

        head = QHBoxLayout()
        self._add_label(head, "Name")
        head.addWidget(self.nameEdit, 1)
        head.addStretch(1)

        self.status = StatusLine(self)
        self.status.show_neutral("Viewing")

        # ── scrollable body
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)

        body = QWidget(); self.scroll.setWidget(body)
        grid = QVBoxLayout(body); grid.setContentsMargins(12,12,12,12); grid.setSpacing(8)

        # layout sections
        self._add_label(grid, "Background")
        grid.addWidget(self.descEdit)

        self._add_label(grid, "Aliases")
        grid.addWidget(self.aliasTable)

        self._add_label(grid, "Physical features")
        grid.addWidget(self.tblPhysical)

        self._add_label(grid, "Characteristics")
        grid.addWidget(self.tblCharacter)

        grid.addLayout(footer_row)

        # Prevent QScrollArea internals from taking focus
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.viewport().setFocusPolicy(Qt.NoFocus)
        if self.scroll.horizontalScrollBar():
            self.scroll.horizontalScrollBar().setFocusPolicy(Qt.NoFocus)
        if self.scroll.verticalScrollBar():
            self.scroll.verticalScrollBar().setFocusPolicy(Qt.NoFocus)

        # # Defer tab order
        # QTimer.singleShot(0, self._apply_tab_order)

        # Compose dialog
        root = QVBoxLayout(self)
        root.addLayout(head)
        root.addWidget(self.status) 
        root.addWidget(self.scroll, 1)

        # Initial data
        self.load_character(self.character_id)

    def setup_facet_table(self, table: InlineFacetTable, facet_type: str):
        """
        table: InlineFacetTable or QTableWidget
        """
        # connect signals
        table.rowAdded.connect(lambda l,v,n: self._add_facet(facet_type, l, v, n))
        table.rowEdited.connect(self._edit_facet)
        table.rowDeleted.connect(self._delete_facet)

        # unwrap if it's the wrapper widget
        t: QTableWidget = getattr(table, "table", table)

        # Make facet tables clean and let the dialog scroll, not the tables
        t.setFrameShape(QFrame.NoFrame)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setStyleSheet("QTableView { border: none; }")
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        t.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        t.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        hh = t.horizontalHeader()
        hh.setStretchLastSection(True)
        # (optional, if you want trait column wider)
        # hh.setSectionResizeMode(0, QHeaderView.Stretch)
        # hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        # hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        # keep table height compact (dialog scrolls, not the table)
        fit_table_height_to_rows(t, min_rows=1)

    def _mark_dirty_status(self):
        self._dirty = True
        if hasattr(self, "status"):
            self.status.set_dirty()
        self.setWindowModified(True)

    def setup_tables_mark_dirty(self, tables: List[Union[InlineAliasTable, InlineFacetTable]]):
        for table in tables:
            table.rowAdded.connect(lambda *_: self._mark_dirty_status())
            table.rowEdited.connect(lambda *_: self._mark_dirty_status())
            table.rowDeleted.connect(lambda *_: self._mark_dirty_status())

    def setup_alias_table(self, table: InlineAliasTable):
        # keep margins tight
        self.aliasTable.setContentsMargins(0,0,0,0)

        # connect signals
        table.rowAdded.connect(lambda alias_type, alias: self._alias_added(alias_type, alias))
        table.rowEdited.connect(self._alias_edited)
        table.rowDeleted.connect(self._alias_deleted)

        # unwrap if it's the wrapper widget
        t: QTableWidget = getattr(table, "table", table)

        t.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # # keep table height compact (dialog scrolls, not the table)
        # fit_table_height_to_rows(t, min_rows=1)

    def _commit_alias_addrow_if_filled(self):
        """If the add-row has data but user didn't press Enter, insert it once."""
        t = self.aliasTable.table
        if t.rowCount() == 0:
            return
        # add-row is index 0
        cb = t.cellWidget(0, TYPE_COL)
        a_type = cb.currentText().strip() if cb else ""
        alias_item = t.item(0, ALIAS_COL)
        alias = (alias_item.text() if alias_item else "").strip()

        if a_type and alias and a_type != "Add alias…":
            # Same order used everywhere: (alias, alias_type)
            self.db.alias_type_upsert(self.app._current_project_id, a_type)
            self.db.alias_add(self.character_id, alias, a_type)

            # reset UI add-row
            if alias_item:
                alias_item.setText("")
            if cb:
                cb.blockSignals(True)
                cb.setCurrentIndex(0)
                cb.blockSignals(False)

    def _reload_alias_table(self):
        rows = self.db.aliases_for_world_item(self.character_id)
        self.aliasTable.set_rows(rows)

    def _on_alias_cell_changed(self, it: QTableWidgetItem):
        r, c = it.row(), it.column()
        rid = self.aliasTable.item(r,0).data(Qt.UserRole) if self.aliasTable.item(r,0) else None
        alias = (self.aliasTable.item(r,ALIAS_COL).text() if self.aliasTable.item(r,ALIAS_COL) else "").strip()
        a_type = (self.aliasTable.item(r,TYPE_COL).text() if self.aliasTable.item(r,TYPE_COL) else "alias").strip() or "alias"

        if c == 1 and a_type:  # ensure new type gets learned for this project
            self.db.alias_type_upsert(self.app._current_project_id, a_type)

        if not alias:  # ignore incomplete rows
            return
        if rid:
            self.db.alias_update(int(rid), alias, a_type)
        else:
            new_id = self.db.alias_add(self.character_id, alias, a_type)
            self.aliasTable.item(r,0).setData(Qt.UserRole, new_id)

    @staticmethod
    def _add_label(grid: QVBoxLayout, label: str):
        grid.addWidget(QLabel(f"<b>{label}</b>"))

    def _begin_populating(self):
        # block change signals while we load data
        self.nameEdit.blockSignals(True)
        self.descEdit.blockSignals(True)
        # If your tables emit rowEdited when you set rows, temporarily disconnect or block:
        self.tblPhysical.table.blockSignals(True)
        self.tblCharacter.table.blockSignals(True)
        self.aliasTable.table.blockSignals(True)

    def _end_populating(self):
        self.nameEdit.blockSignals(False)
        self.descEdit.blockSignals(False)
        self.tblPhysical.table.blockSignals(False)
        self.tblCharacter.table.blockSignals(False)
        self.aliasTable.table.blockSignals(False)

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
            self.setTabOrder(self.btnAdd, self.btnSave)
            # if you also have a bottom cancel, put it here; if not, remove this line
            if hasattr(self, "btnCancelBottom"):
                self.setTabOrder(self.btnSave, self.btnCancelBottom)
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

    def load_character(self, character_id: int):
        self._begin_populating()
        try:
            self._id = character_id
            meta = self.app.db.world_item_meta(character_id)  # {title, content_md, ...}
            self.nameEdit.setText(meta["title"])
            self.descEdit.setPlainText(meta["content_md"])
            QTimer.singleShot(0, lambda: auto_grow_plaintext(self.descEdit))

            # aliases
            self._reload_alias_table()

            # facets
            phys = self.app.db.character_facets_by_type(character_id, "trait_physical")   # [{id,label,value,note}]
            char = self.app.db.character_facets_by_type(character_id, "trait_character")
            self.tblPhysical.set_rows(phys) #;  fit_table_height_to_rows(self.tblPhysical._table, min_rows=1)
            self.tblCharacter.set_rows(char) #; fit_table_height_to_rows(self.tblCharacter._table, min_rows=1)
        finally:
            self._end_populating()

        # reset dirty and status/modified dot
        self._dirty = False
        self.setWindowModified(False)
        if hasattr(self, "status"):
            self.status.show_neutral("Unchanged")

    # def _populate_alias_table(self, character_id: int):
    #     aliases = self.db.aliases_for_world_item(character_id)  # return [{id,alias,alias_type}]
    #     self.aliasTable.set_rows(aliases)

    def _alias_context_menu(self, pos):
        r = self.aliasTable.rowAt(pos.y())
        m = QMenu(self)
        aAdd = m.addAction("Add alias")
        aDel = m.addAction("Delete")
        chosen = m.exec(self.aliasTable.viewport().mapToGlobal(pos))
        if not chosen: return
        if chosen == aAdd:
            self.aliasTable.insertRow(self.aliasTable.table.rowCount())
            # focus alias cell
            self.aliasTable.editItem(QTableWidgetItem(""))
            # you can detect edits on itemChanged and persist on save
        elif chosen == aDel and r >= 0:
            rid = self.aliasTable.item(r,0).data(Qt.UserRole)
            if rid:
                self.app.db.alias_delete(int(rid))
            self.aliasTable.removeRow(r)

    def _alias_added(self, alias_type: str, alias: str):
        alias = (alias or "").strip()
        if not alias:
            return
        self.db.alias_type_upsert(self.app._current_project_id, alias_type)
        ok = self.db.alias_add(self.character_id, alias, alias_type)
        if not ok:
            QMessageBox.information(self, "Duplicate alias",
                                    f"“{alias}” already exists for this character.")
            return
        self._reload_alias_table()
        # rows = self.db.aliases_for_world_item(self.character_id)
        # self.aliasTable.set_rows(rows)
        # self.aliasTable.fit_height()

    # def _alias_edited(self, rid: int, alias: str, a_type: str):
    #     self.db.alias_type_upsert(self.app._current_project_id, a_type)
    #     self.db.alias_update(rid, alias, a_type)

    def _alias_edited(self, row_id: int, field: str, value: str):
        if field == "alias_type":
            self.db.alias_update_type(row_id, value)
            self.db.alias_type_upsert(self.app._current_project_id, value)
        elif field == "alias":
            ok = self.db.alias_update_alias(row_id, value)
            print(f"ok: {ok}, row_id: {row_id}, value: {value}")
            if not ok:
                QMessageBox.information(self, "Duplicate alias",
                                        f"“{value}” already exists for this character.")
                self._reload_alias_table()
                return
        # requery to keep table/model synced
        self._reload_alias_table()
        # rows = self.db.aliases_for_world_item(self.character_id)
        # self.aliasTable.set_rows(rows)

    def _alias_deleted(self, rid: int):
        self.db.alias_delete(rid)
        # table already removed the row; but refresh to keep _rows in sync
        self._reload_alias_table()
        # rows = self.db.aliases_for_world_item(self.character_id)
        # self.aliasTable.set_rows(rows)

    def _add_facet(self, facet_type: str, label: str, value: str, note: str):
        # Gentle guard: detect duplicate type+label for this character
        if self.db.character_facet_exists(self.character_id, facet_type, label):
            if QMessageBox.question(self, "Duplicate trait",
                                    f"“{label}” already exists. Add another?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.No:
                return
        self.app.db.character_facet_insert(self._id, facet_type, label, value, note)
        # reload that table only
        rows = self.app.db.character_facets_by_type(self._id, facet_type)
        if facet_type == "trait_physical":
            self.tblPhysical.set_rows(rows)
        elif facet_type == "trait_character":
            self.tblCharacter.set_rows(rows)

    def _edit_facet(self, facet_id: int, label: str, value: str, note: str):
        self.app.db.character_facet_update(facet_id, label=label, value=value, note=note)

    def _delete_facet(self, facet_id: int):
        self.app.db.character_facet_delete(facet_id)

    def _after_saved(self):
        self._dirty = False
        if hasattr(self, "status"):
            self.status.set_saved_now()
        self.setWindowModified(False)

    def _save_and_close(self):
        # Commit any in-place editors so itemChanged fires
        for t in (self.tblPhysical.table, self.tblCharacter.table, self.aliasTable.table):
            t.clearFocus()
            t.setCurrentCell(-1, -1)

        # 2) if add-row was filled but Enter not pressed, insert it now (correct order)
        self._commit_alias_addrow_if_filled()

        # 3) (your other saves: name, description, facets, etc.)
        title = self.nameEdit.text().strip()
        md    = self.descEdit.toPlainText()
        self.app.db.world_item_update(self._id, title=title, content_md=md)
        self.app.rebuild_world_item_render(self._id) # render html from md # TODO: Should this not happen until loading world item?
        # # aliases: iterate table and upsert # NOTE: This might already be done in _alias_edited
        # self._persist_aliases()

        # 4) refresh alias table from DB so UI mirrors truth
        self._reload_alias_table()


        # 5) notify right panel & close
        if hasattr(self.app, "worldDetail"):
            self.app.worldDetail.refresh()

        self._after_saved()
        self.accept()

    def _persist_aliases(self):
        t = self.aliasTable.table
        seen = set()
        for r in range(t.rowCount()):
            alias = (t.item(r,0).text() if t.item(r,0) else "").strip()
            a_type= (t.item(r,1).text() if t.item(r,1) else "alias").strip() or "alias"
            if not alias: continue
            rid = t.item(r,0).data(Qt.UserRole)
            if rid:
                self.app.db.alias_update(int(rid), alias, a_type)
                seen.add(int(rid))
            else:
                new_id = self.app.db.alias_add(self._id, alias, a_type)
                t.item(r,0).setData(Qt.UserRole, new_id)
                seen.add(new_id)
        # (optional) delete aliases missing from seen

    def _cancel_dialog(self):
        # if dirty detection wanted: compare to original snapshot and confirm discard
        self.reject()

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
    # def _save_all_and_close(self):
    #     self._save_all()
    #     self.accept()

    # def _save_all(self):
    #     if not self._dirty:
    #         return
    #     # update name + description
    #     name = self.nameEdit.text().strip()
    #     md   = self.descEdit.toPlainText()
    #     self.db.conn.execute(
    #         "UPDATE world_items SET title=?, content_md=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
    #         (name, md, self.character_id)
    #     )
    #     self.db.conn.commit()
    #     # refresh right panel if it's showing this character
    #     if getattr(self.app, "_current_world_item_id", None) == self.character_id:
    #         if hasattr(self.app, "show_world_item"):
    #             self.app.show_world_item(self.character_id, edit_mode=False)
    #     # keep tree label in sync if you show titles there
    #     if hasattr(self.app, "reload_world_tree"):
    #         self.app.reload_world_tree(keep_expansion=True, focus_id=self.character_id)
    #     self._dirty = False
    #     # self.btnSave.setEnabled(False)
    #     self.btnSaveBottom.setEnabled(False)

    def _mark_dirty(self):
        self._dirty = True
        # self.btnSave.setEnabled(True)
        self.btnSave.setEnabled(True)
