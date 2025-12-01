from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTextBrowser, QLabel,
    QToolButton, QFrame, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QInputDialog, QMessageBox,
)

from utils.md import md_to_html


class MembersTree(QTreeWidget):
    """
    QTreeWidget with:
    - Extended selection (Ctrl-click, Shift-click).
    - Clicking on empty space clears the selection.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item is None:
            self.clearSelection()
        super().mousePressEvent(event)


class NotesNotebook(QWidget):
    """
    Notebook-style viewer for notes_docs attached to a notes node.
    - Shows each notes_docs row as a tab.
    - Has a '+' button to add a new tab.
    - When the node is a membership node, shows a members panel.
    """

    def __init__(self, app, db, parent=None):
        super().__init__(parent)
        self.app = app            # MainWindow
        self.db = db
        self._current_node_id = None
        self._current_node = None

        self._build_ui()

    # --- UI construction -----------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Tabs area
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        # '+' button as corner widget
        self.btnAddTab = QToolButton(self)
        self.btnAddTab.setText("+")
        self.btnAddTab.setToolTip("Add note tab")
        self.btnAddTab.clicked.connect(self._on_add_tab)
        self.tabs.setCornerWidget(self.btnAddTab, Qt.TopRightCorner)

        # Members panel (hidden unless node_kind is membership-related)
        self.membersFrame = QFrame(self)
        self.membersFrame.setFrameShape(QFrame.StyledPanel)
        mvl = QVBoxLayout(self.membersFrame)
        mvl.setContentsMargins(0, 8, 0, 0)
        mvl.setSpacing(4)

        header = QHBoxLayout()
        self.lblMembersTitle = QLabel("Members", self.membersFrame)
        header.addWidget(self.lblMembersTitle)

        self.btnAddMember = QToolButton(self.membersFrame)
        self.btnAddMember.setText("+")
        self.btnAddMember.setToolTip("Add member")
        self.btnAddMember.clicked.connect(self._on_add_member)
        header.addWidget(self.btnAddMember)

        self.btnRemoveMember = QToolButton(self.membersFrame)
        self.btnRemoveMember.setText("−")
        self.btnRemoveMember.setToolTip("Remove selected member(s)")
        self.btnRemoveMember.clicked.connect(self._on_remove_member)
        header.addWidget(self.btnRemoveMember)

        header.addStretch(1)
        mvl.addLayout(header)

        self.membersList = MembersTree(self.membersFrame)
        self.membersList.setHeaderHidden(True)  # one column, headerless
        mvl.addWidget(self.membersList)

        layout.addWidget(self.membersFrame)
        self.membersFrame.setVisible(False)

    # --- Public API ----------------------------------------------------------

    def clear(self):
        self._current_node_id = None
        self._current_node = None
        self.tabs.clear()
        self.membersFrame.setVisible(False)
        self.btnAddTab.setEnabled(False)

    def show_node(self, node_id: int):
        """
        Load docs and membership for the given node id.
        """
        self.clear()
        node = self.db.notes_node_get(node_id)
        if not node:
            # nothing to show
            return

        self._current_node_id = int(node_id)
        self._current_node = node
        self.btnAddTab.setEnabled(True)

        self._rebuild_docs()
        self._rebuild_members_if_needed()

    # --- Docs / tabs ---------------------------------------------------------

    def _rebuild_docs(self):
        self.tabs.clear()
        node_id = self._current_node_id
        if not node_id:
            return

        docs = self.db.notes_docs_for_node(node_id)
        if not docs:
            # show placeholder tab
            placeholder = QTextBrowser(self)
            placeholder.setHtml("<p><i>No notes yet for this node.</i></p>")
            self.app._attach_link_handlers(placeholder)
            self.app._install_wikilink_hover(placeholder)
            self.tabs.addTab(placeholder, "Overview")
            return

        for doc in docs:
            title = doc["title"] or "(untitled)"
            content_md = doc["content_md"] or ""
            content_render = doc["content_render"] or ""
            if not content_render and content_md:
                content_render = md_to_html(content_md, css=None, include_scaffold=False)

            view = QTextBrowser(self)
            view.setOpenExternalLinks(False)
            view.setOpenLinks(False)
            self.app._attach_link_handlers(view)
            self.app._install_wikilink_hover(view)
            view.setHtml(content_render or "")

            self.tabs.addTab(view, title)

    def _on_add_tab(self):
        node_id = self._current_node_id
        if not node_id:
            return
        # Simple: create an empty doc titled "New Tab" and reload
        self.db.notes_doc_insert(
            node_id=node_id,
            title="New Tab",
            content_md="",
        )
        self._rebuild_docs()
        # Jump to the newly added tab
        if self.tabs.count() > 0:
            self.tabs.setCurrentIndex(self.tabs.count() - 1)

    # --- Membership panel ----------------------------------------------------

    def _node_is_membership_kind(self) -> bool:
        if not self._current_node:
            return False
        return self._current_node["node_kind"] in ("members_container", "members_subcategory")

    def _rebuild_members_if_needed(self):
        node = self._current_node
        if not node or not self._node_is_membership_kind():
            self.membersFrame.setVisible(False)
            return

        self.membersFrame.setVisible(True)
        label = node["relationship_label"] or "Members"
        self.lblMembersTitle.setText(f"{label.capitalize()} List")

        self.membersList.clear()
        rows = self.db.note_members_for_node(node["id"])
        for r in rows:
            it = QTreeWidgetItem([r["world_title"] or ""])
            it.setData(0, Qt.UserRole, ("world_item", int(r["world_item_id"])))
            self.membersList.addTopLevelItem(it)

        self.membersList.resizeColumnToContents(0)

    def _on_add_member(self):
        node = self._current_node
        if not node or not self._node_is_membership_kind():
            return

        allowed_type = node["allowed_item_type"]

        box = QMessageBox(self)
        box.setWindowTitle("Add member")
        box.setText("How would you like to add a member?")

        btn_select = box.addButton("Select existing", QMessageBox.AcceptRole)
        btn_create = box.addButton("Create new", QMessageBox.AcceptRole)
        btn_cancel = box.addButton("Cancel", QMessageBox.RejectRole)

        box.setDefaultButton(btn_select)
        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_cancel:
            return
        elif clicked is btn_select:
            wid = self._prompt_pick_existing_member(allowed_type)
        else: # clicked is btn_create:
            wid = self._prompt_create_member(allowed_type)

        if not wid:
            return

        self.db.note_member_add(node["id"], wid)
        self._rebuild_members_if_needed()

    def _on_remove_member(self):
        node = self._current_node
        if not node or not self._node_is_membership_kind():
            return

        items = self.membersList.selectedItems()
        if not items:
            return

        for it in items:
            data = it.data(0, Qt.UserRole)
            if not data or data[0] != "world_item":
                continue
            wid = int(data[1])
            self.db.note_member_remove(node["id"], wid)

        self._rebuild_members_if_needed()

    def _on_member_double_clicked(self, item, column):
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "world_item":
            return
        wid = int(data[1])
        if hasattr(self.app, "worldDetail"):
            self.app.worldDetail.open_world_item(wid)

    # --- Member creation / selection helpers ---------------------------------

    def _prompt_pick_existing_member(self, allowed_type: str | None) -> int | None:
        pid = getattr(self.app, "_current_project_id", None)
        if not pid:
            QMessageBox.warning(self, "No project", "No project is currently loaded.")
            return None

        rows = self.db.world_items_by_type(pid, allowed_type)
        if not rows:
            QMessageBox.information(
                self,
                "No items",
                "There are no world items of the required type to choose from.",
            )
            return None

        titles = [r["title"] for r in rows]
        title, ok = QInputDialog.getItem(
            self,
            "Select member",
            "Choose an existing world item:",
            titles,
            0,
            False,
        )
        if not ok or not title:
            return None

        idx = titles.index(title)
        return int(rows[idx]["id"])

    def _prompt_create_member(self, allowed_type: str | None) -> int | None:
        pid = getattr(self.app, "_current_project_id", None)
        if not pid:
            QMessageBox.warning(self, "No project", "No project is currently loaded.")
            return None

        name, ok = QInputDialog.getText(
            self,
            "Create member",
            "Name for new member:",
        )
        if not ok:
            return None
        name = (name or "").strip()
        if not name:
            return None

        kind = allowed_type or "item"
        # NOTE: world_item_insert signature may have more params; this call uses
        # only the keyword args we know exist from existing call sites.
        wid = self.db.world_item_insert(
            project_id=pid,
            item_type=kind,
            title=name,
            content_md="",
        )
        return wid
