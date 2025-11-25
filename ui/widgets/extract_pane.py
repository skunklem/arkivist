from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex, QItemSelection, QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QToolBar, QTableView, QInputDialog,
                               QPushButton, QHBoxLayout, QStyle, QDialog, QLineEdit,
                               QDialogButtonBox, QLabel, QComboBox, QRadioButton,
                               QStyledItemDelegate, QFormLayout, QButtonGroup
)

COLS = ["✓", "Candidate", "Kind", "Confidence", "Actions"]
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
            if c == COL_NUMS["Candidate"]: return r["candidate"]
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
        self.act_reject = QAction("Dismiss selected", self); self.toolbar.addAction(self.act_reject)
        self.act_accept.triggered.connect(self.on_accept_selected)
        self.act_reject.triggered.connect(self.on_reject_selected)

        lay = QVBoxLayout(self)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.table)
        self.setLayout(lay)
        self.refresh()

        # start disabled until set_chapter() is called
        for act in (self.act_accept, self.act_reject):
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
        print("Fetched", len(rows), "candidates, row 1:", rows[0].keys() if rows else "n/a")
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
        # buffer: add 16px to candidate/kind columns
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
        print(f"Update actions enabled: has_rows={has_rows}, any_checked={any_checked}")

        # Quick parse is always available for the current chapter
        self.act_refresh.setEnabled(self.chapter_id is not None)

        # enable set depends on your UX; a good default:
        self.act_accept.setEnabled(has_rows and any_checked)
        self.act_reject.setEnabled(has_rows and any_checked)

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
        # TODO: allow type addition in this witihin a dropdown
        # TODO: customize per kind
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
            candidate = (row["candidate"] or "").strip()
            kind_guess = (row["kind_guess"] or "").strip()
            cand_id = row["id"]

            dlg = CandidateAcceptDialog(self.mw, candidate, kind_guess or "item", parent=self)
            if dlg.exec() == QDialog.Accepted:
                res = dlg.payload()
                if res["mode"] == "alias" and res["target_wid"]:
                    self.mw.accept_candidate(cand_id, as_alias_of=int(res["target_wid"]), add_alias=True, alias_type="alias")
                    self.mw.load_world_item(int(res["target_wid"]), edit_mode=False)
                else:
                    self.mw.accept_candidate_create(cand_id)  # creates + renders
                    # open proper editor based on type
                    row = self.mw.db.ingest_candidate_row(cand_id)   # has target_world_item_id after accept
                    if row and row[-1]:
                        wid = int(row[-1])
                        if res["kind"].lower() == "character":
                            self.mw.load_world_item(wid, edit_mode=True)  # your load_* opens character dialog when edit=True
                        else:
                            self.mw.load_world_item(wid, edit_mode=True)
                # Extract refresh (if not already automatic)
                if hasattr(self, "refresh"): self.refresh()


            # # Require a kind if none, then create/link
            # if not kind_guess:
            #     kind_guess = self.mw._prompt_kind_for_new_item()
            #     if not kind_guess:
            #         continue

            # # Create or link
            # choice = self._prompt_alias_or_create(candidate, kind_guess)   # 'create' | 'alias' | None
            # if choice == "alias":
            #     if kind_guess == "character":
            #         print("Prompting for alias link for", candidate)
            #         wid = self._prompt_pick_world_item(prefill=candidate, restrict_kind="character")
            #     else:
            #         wid = self._prompt_pick_world_item(prefill=candidate)
            #     if wid:
            #         alias_type = self._prompt_alias_type()
            #         self.mw.accept_candidate(cand_id, as_alias_of=wid, add_alias=True, alias_type=alias_type)
            #     continue
            # elif choice == "create":
            #     self.mw.accept_candidate_create(cand_id)
            #     continue
            # # canceled → skip

        # 1) table and chapter reflects immediately
        self.refresh()
        # 2) references update for current view
        self._refresh_refs_after_accept()

    def _prompt_alias_or_create(self, name: str, kind: str) -> str | None:
        """
        Returns 'create', 'alias', or None (cancel).
        """
        dlg = QDialog(self); dlg.setWindowTitle("Person detected")
        lab = QLabel(f"What do you want to do with “{name}”?")
        rb_create = QRadioButton(f"Create new {kind.lower()}")
        rb_alias  = QRadioButton(f"Add as alias to an existing {kind.lower()}")
        rb_create.setChecked(True)
        btns = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel, parent=dlg)
        lay = QVBoxLayout(dlg); lay.addWidget(lab); lay.addWidget(rb_create); lay.addWidget(rb_alias); lay.addWidget(btns)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return None
        return "alias" if rb_alias.isChecked() else "create"

    def on_reject_selected(self):
        for cand_id in self._selected_ids():
            self.mw.reject_candidate(cand_id, rerender=False)
        self.mw.rerender_center_and_extract()

    def on_double_clicked(self, index):
        # quick per-row action dispatch (Accept | Link | Dismiss)
        print("Row double-clicked:")
        r = self.model.rows[index.row()]
        if index.column() == COL_NUMS["Actions"]:
            # choose based on cursor position? keep it simple: open link dialog
            wid = self._prompt_pick_world_item(prefill=r["candidate"])
            if wid:
                self.mw.db.ingest_candidate_link_world(r["id"], wid)
                self.refresh()
                self._refresh_refs_after_accept()

    def _refresh_refs_after_accept(self):
        if self.chapter_id is None: return
        text = self.mw.db.chapter_content(self.chapter_id, version_id=self.view_version_id) or ""
        self.mw.recompute_chapter_references(self.chapter_id, text,
                                             chapter_version_id=self.view_version_id)
        self.mw._render_center_preview(self.view_version_id)
        # Refresh the refs tree UI immediately
        self.mw.populate_refs_tree(self.chapter_id)

    def focus_candidate(self, cand_id: int):
        """Select + scroll to the candidate row in our table."""
        tv = self.table  # QTableView
        model = tv.model()
        if model is None:
            return
        # TODO: set to the correct column index for candidate id in your model:
        ID_COL = 0  # <-- adjust this to match your table's column for 'id'
        # linear scan is fine here; optimize later if needed
        for r in range(model.rowCount()):
            idx = model.index(r, ID_COL)
            try:
                val = int(model.data(idx))
            except Exception:
                continue
            if val == int(cand_id):
                sel = tv.selectionModel()
                sel.clearSelection()
                sel.select(idx, sel.Select | sel.Rows)
                tv.scrollTo(idx, tv.PositionAtCenter)
                tv.setCurrentIndex(idx)
                tv.setFocus()
                break

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


class CandidateAcceptDialog(QDialog):
    def __init__(self, mw, candidate_text: str, suggested_kind: str | None = None, parent=None):
        super().__init__(parent or mw)
        self.mw = mw
        self.setWindowTitle("Accept Candidate")
        self.setModal(True)
        form = QFormLayout(self)

        # 1) Type
        self.kind = QComboBox(self)
        kinds = ["character","place","organization","artifact","concept","item"]
        if suggested_kind and suggested_kind not in kinds:
            kinds.insert(0, suggested_kind)
        self.kind.addItems(kinds)
        if suggested_kind:
            self.kind.setCurrentText(suggested_kind)
        form.addRow("Type:", self.kind)

        # 2) Action: toggle buttons (exclusive)
        self.btnCreate = QPushButton("Create new"); self.btnCreate.setCheckable(True)
        self.btnAlias  = QPushButton("Alias existing"); self.btnAlias.setCheckable(True)
        grp = QButtonGroup(self); grp.setExclusive(True)
        grp.addButton(self.btnCreate); grp.addButton(self.btnAlias)
        self.btnCreate.setChecked(True)
        row = QHBoxLayout(); row.addWidget(self.btnCreate); row.addWidget(self.btnAlias)
        form.addRow("Action:", row)

        # visual feedback for checked state
        self.setStyleSheet("""
        QPushButton:checked {
          background: palette(Highlight);
          color: palette(HighlightedText);
          border: 1px solid palette(Highlight);
          border-radius: 6px;
        }
        QPushButton { padding: 4px 8px; }
        """)

        # 3) Alias picker (lazy fill)
        self.aliasPicker = QComboBox(self); self.aliasPicker.setVisible(False)
        form.addRow("Alias to:", self.aliasPicker)

        def refresh_aliases():
            self.aliasPicker.clear()
            kind = (self.kind.currentText() or "").strip()
            # requires a db helper; fall back to all items if you don’t have a kind filter
            rows = self.mw.db.world_items_list_for_kind(self.mw._current_project_id, kind) \
                   if hasattr(self.mw.db, "world_items_list_for_kind") else \
                   self.mw.db.world_items_list(self.mw._current_project_id)
            for r in rows:
                self.aliasPicker.addItem(r["title"], r["id"])

        # track alias row widgets to toggle visibility
        alias_label = None
        if isinstance(form, QFormLayout):
            alias_label = form.labelForField(self.aliasPicker)

        def _toggle_alias_row(on: bool):
            self.aliasPicker.setVisible(on)
            if alias_label: alias_label.setVisible(on)

        self.btnAlias.toggled.connect(lambda on: (_toggle_alias_row(on), on and refresh_aliases()))
        self.kind.currentTextChanged.connect(lambda _: self.btnAlias.isChecked() and refresh_aliases())

        # start hidden
        _toggle_alias_row(False)

        # 4) Buttons
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        form.addRow(box)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)

        self._candidate_text = candidate_text

    def payload(self):
        mode = "alias" if self.btnAlias.isChecked() else "create"
        wid  = self.aliasPicker.currentData() if mode == "alias" else None
        return {"mode": mode, "kind": self.kind.currentText(), "target_wid": wid, "title": self._candidate_text}
