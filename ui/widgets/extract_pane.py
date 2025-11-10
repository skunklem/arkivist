from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex, QItemSelection, QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QToolBar, QTableView, QInputDialog,
                               QPushButton, QHBoxLayout, QStyle, QDialog, QLineEdit,
                               QDialogButtonBox, QLabel, QComboBox, QRadioButton,
                               QStyledItemDelegate)

COLS = ["✓", "Surface", "Kind", "Confidence", "Actions"]
COL_NUMS = {c:i for i,c in enumerate(COLS)}
class CandidateModel(QAbstractTableModel):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        self.checked = set()

    def rowCount(self, parent=QModelIndex()): return len(self.rows)
    def columnCount(self, parent=QModelIndex()): return len(COLS)
    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLS[section]
        return None

    def data(self, index, role):
        if not index.isValid(): return None
        r = self.rows[index.row()]
        c = index.column()
        if role == Qt.CheckStateRole and c == 0:
            rid = r["id"]
            return Qt.Checked if rid in self.checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            if c == COL_NUMS["Surface"]: return r["surface"]
            if c == COL_NUMS["Kind"]: return r["kind_guess"] or ""
            if c == COL_NUMS["Confidence"]: return f'{(r["confidence"] or 0)*100:.0f}%'
            if c == COL_NUMS["Actions"]: return "Accept | Alias… | Link | Dismiss"
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if r["id"] in self.checked else Qt.Unchecked
        return None

    def flags(self, index):
        fl = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_NUMS["✓"]:
            fl |= Qt.ItemIsUserCheckable
        return fl

    def setData(self, index, value, role):
        if index.column() == COL_NUMS["✓"] and role == Qt.CheckStateRole:
            rid = self.rows[index.row()]["id"]
            if value == Qt.Checked:
                self.checked.add(rid)
            else:
                self.checked.discard(rid)
            # notify view + any listeners
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

class CheckBoxDelegate(QStyledItemDelegate):
    def editorEvent(self, event, model, option, index):
        if index.column() != 0:
            return super().editorEvent(event, model, option, index)
        et = event.type()
        if et in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            # consume press so release toggles cleanly
            return True
        if et in (QEvent.MouseButtonRelease, QEvent.KeyPress):
            print("editorEvent", event)
            cur = model.data(index, Qt.CheckStateRole)
            new = Qt.Unchecked if cur == Qt.Checked else Qt.Checked
            ok  = model.setData(index, new, Qt.CheckStateRole)
            print("set data", cur, "->", new, ok)
            return ok
        return False

class ExtractPane(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.chapter_id: int | None = None
        self.view_version_id: int | None = None
        self.table = QTableView(self)
        self.table.setItemDelegateForColumn(0, CheckBoxDelegate(self.table))
        self.toolbar = QToolBar(self)
        self.act_refresh = QAction("Quick Parse", self)
        self.act_refresh.triggered.connect(self.on_quick_parse)
        self.toolbar.addAction(self.act_refresh)

        self.act_accept = QAction("Accept selected", self); self.toolbar.addAction(self.act_accept)
        self.act_link   = QAction("Link selected…", self); self.toolbar.addAction(self.act_link)
        self.act_reject = QAction("Dismiss selected", self); self.toolbar.addAction(self.act_reject)
        self.act_accept.triggered.connect(self.on_accept_selected)
        self.act_link.triggered.connect(self.on_link_selected)
        self.act_reject.triggered.connect(self.on_reject_selected)

        lay = QVBoxLayout(self)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.table)
        self.setLayout(lay)
        self.refresh()

        # start disabled until set_chapter() is called
        for act in (self.act_accept, self.act_link, self.act_reject):
            act.setEnabled(False)

        self.model = CandidateModel([])
        self.table.setModel(self.model)

        # connect signals
        self.table.doubleClicked.connect(self.on_double_clicked)
        self.table.clicked.connect(self._on_table_clicked)
        sel_model = self.table.selectionModel()
        print("sel_model:", sel_model)
        sel_model.selectionChanged.connect(self._on_selection_changed)


    def set_chapter(self, chapter_id: int):
        self.chapter_id = int(chapter_id)
        self.view_version_id = None  # default: active
        # auto-parse if empty; else just load
        rows = self.mw.db.ingest_candidates_by_chapter(self.chapter_id, version_id=None, statuses=("pending",))
        if not rows:
            self.mw.cmd_quick_parse_chapter(self.chapter_id, version_id=None)
        self.refresh()

    def set_chapter_version(self, chapter_id: int, version_id: int | None):
        self.chapter_id = int(chapter_id)
        self.view_version_id = int(version_id) if version_id is not None else None
        # auto-parse if empty for this version; else just load
        rows = self.mw.db.ingest_candidates_by_chapter(self.chapter_id, version_id=self.view_version_id, statuses=("pending",))
        if not rows:
            self.mw.cmd_quick_parse_chapter(self.chapter_id, version_id=self.view_version_id)
        self.refresh()

    def refresh(self):
        if self.chapter_id is None:
            return  # nothing to show yet
        print(f"ExtractPane: refresh chapter {self.chapter_id}")
        rows = self.mw.db.ingest_candidates_by_chapter(
            self.chapter_id, version_id=self.view_version_id, statuses=("pending",))
        self.model = CandidateModel(rows)
        self.table.setModel(self.model)

        # Reconnect signals to the *new* model/selection model
        try:
            self.model.dataChanged.disconnect()
        except Exception:
            pass
        self.model.dataChanged.connect(lambda *_: self._update_actions_enabled())
        try:
            self.table.selectionModel().selectionChanged.disconnect()
        except Exception:
            pass
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # size polish
        print(f"table length: {len(rows)} rows, {len(self.model.checked)} checked")
        self.table.resizeColumnsToContents()
        # buffer: add 16px to surface/kind columns
        try:
            self.table.setColumnWidth(1, self.table.columnWidth(1) + 16)
            self.table.setColumnWidth(2, self.table.columnWidth(2) + 12)
        except Exception:
            pass
        self._update_actions_enabled()

    def _update_actions_enabled(self):
        has_rows = self.model.rowCount() > 0
        checked_set = self.model.checked
        any_checked = bool(checked_set)
        exactly_one = (len(checked_set) == 1)
        print(f"Update actions enabled: has_rows={has_rows}, any_checked={any_checked}, exactly_one={exactly_one}")

        # Quick parse is always available for the current chapter
        self.act_refresh.setEnabled(self.chapter_id is not None)

        # enable set depends on your UX; a good default:
        self.act_accept.setEnabled(has_rows and any_checked)
        self.act_reject.setEnabled(has_rows and any_checked)
        self.act_link.setEnabled(has_rows and exactly_one)   # linking generally expects one

    # hook these so the buttons react immediately
    def _on_table_clicked(self, index):
        self._update_actions_enabled()

    def _on_selection_changed(self, _new: QItemSelection, _old: QItemSelection):
        self._update_actions_enabled()

    def on_quick_parse(self):
        if self.chapter_id is None:
            return
        self.mw.cmd_quick_parse_chapter(self.chapter_id, version_id=self.view_version_id)
        self.refresh()

    def _selected_ids(self):
        return list(self.model.checked)

    # --- actions ---
    def _prompt_alias_type(self) -> str | None:
        types = ["nickname", "formal", "diminutive", "epithet", "title", "alias"]
        choice, ok = QInputDialog.getItem(self, "Alias type", "Choose alias type:", types, 0, False)
        return choice if ok else None

    def on_accept_selected(self):
        checked_ids = list(getattr(self.model, "checked", set()))
        if not checked_ids:
            return
        for cid in checked_ids:
            row = next((r for r in self.model.rows if r["id"] == cid), None)
            if not row: 
                continue
            surface = (row["surface"] or "").strip()
            kind = (row["kind_guess"] or "").strip()

            # Always OFFER for PERSON/character
            if kind == "character":
                choice = self._prompt_person_choice(surface)   # 'create' | 'alias' | None
                if choice == "alias":
                    wid = self._prompt_pick_world_item(prefill=surface, restrict_kind="character")
                    if wid:
                        alias_type = self._prompt_alias_type()
                        if alias_type:
                            self.mw.db.alias_add(wid, surface, alias_type)
                            self.mw.db.ingest_candidate_link_world(cid, wid)
                            self.mw.db.ingest_candidate_set_status(cid, "accepted")
                    continue
                if choice == "create":
                    wid = self.mw.find_or_create_world_item(surface, "character")
                    if wid:
                        self.mw.db.ingest_candidate_link_world(cid, wid)
                        self.mw.db.ingest_candidate_set_status(cid, "accepted")
                    continue
                # canceled → skip
                continue

            # Non-character: require a kind if none, then create/link
            if not kind:
                kind = self.mw._prompt_kind_for_new_item()
                if not kind:
                    continue
            wid = self.mw.ensure_world_item_from_candidate(row)  # uses _prompt_kind_for_new_item if needed
            if wid:
                self.mw.db.ingest_candidate_link_world(cid, wid)
                self.mw.db.ingest_candidate_set_status(cid, "accepted")

        # 1) table reflects immediately
        self.refresh()
        # 2) references update for current view
        self._refresh_refs_after_accept()

    def _prompt_person_choice(self, name: str) -> str | None:
        """
        Returns 'create', 'alias', or None (cancel).
        """
        dlg = QDialog(self); dlg.setWindowTitle("Person detected")
        lab = QLabel(f"What do you want to do with “{name}”?")
        rb_create = QRadioButton("Create new character")
        rb_alias  = QRadioButton("Add as alias to an existing character")
        rb_create.setChecked(True)
        btns = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel, parent=dlg)
        lay = QVBoxLayout(dlg); lay.addWidget(lab); lay.addWidget(rb_create); lay.addWidget(rb_alias); lay.addWidget(btns)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return None
        return "alias" if rb_alias.isChecked() else "create"

    def on_reject_selected(self):
        for cid in self._selected_ids():
            self.mw.db.ingest_candidate_set_status(cid, "rejected")
        self.refresh()

    def on_link_selected(self):
        checked_ids = self._selected_ids()
        if len(checked_ids) != 1:
            return
        cid = checked_ids[0]
        row = next((r for r in self.model.rows if r["id"] == cid), None)
        if not row:
            return
        wid = self._prompt_pick_world_item(prefill=row["surface"] or "")
        if wid:
            self.mw.db.ingest_candidate_link_world(cid, wid)
            self.mw.db.ingest_candidate_set_status(cid, "accepted")
            self.refresh()
            self._refresh_refs_after_accept()

    def on_double_clicked(self, index):
        # quick per-row action dispatch (Accept | Link | Dismiss)
        r = self.model.rows[index.row()]
        if index.column() == COL_NUMS["Actions"]:
            # choose based on cursor position? keep it simple: open link dialog
            wid = self._prompt_pick_world_item(prefill=r["surface"])
            if wid:
                self.mw.db.ingest_candidate_link_world(r["id"], wid)
                self.refresh()
                self._refresh_refs_after_accept()

    def _refresh_refs_after_accept(self):
        if self.chapter_id is None: return
        text = self.mw.db.chapter_content(self.chapter_id, version_id=self.view_version_id) or ""
        self.mw.recompute_chapter_references(self.chapter_id, text,
                                             chapter_version_id=self.view_version_id)
        # Refresh the refs tree UI immediately
        self.mw.populate_refs_tree(self.chapter_id)

    def _prompt_pick_world_item(self, prefill: str = "", restrict_kind: str | None = None) -> int | None:
        # dumb dialog: user types exact or new → MW will either find or create
        # TODO: better UI, search list, kind restriction, etc.
        dlg = QDialog(self)
        dlg.setWindowTitle("Link to world item")
        le = QLineEdit(dlg); le.setText(prefill)
        kind = QComboBox(dlg); kind.addItems(["character","place","organization","object","concept"])
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Title or alias:"))
        layout.addWidget(le)
        layout.addWidget(QLabel("Kind (if creating new):"))
        layout.addWidget(kind)
        layout.addWidget(box)
        box.accepted.connect(dlg.accept); box.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return None
        title = le.text().strip()
        if not title:
            return None
        return self.mw.find_or_create_world_item(title, kind.currentText())
