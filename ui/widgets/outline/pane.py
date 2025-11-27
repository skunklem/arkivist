from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .data import ChapterVersion
from .editor import OutlineEditor


class _VResizeHandle(QtWidgets.QFrame):
    resized = QtCore.Signal(int)       # new height in px
    resetAuto = QtCore.Signal()        # double-click to reset auto size

    def __init__(self, target_getter, parent=None):
        super().__init__(parent)
        self._target_getter = target_getter  # callable -> QWidget we resize
        self.setFixedHeight(8)
        self.setCursor(QtCore.Qt.SizeVerCursor)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("""
            QFrame { background: palette(dark); }
            QFrame:hover { background: palette(mid); }
        """)
        self.setToolTip("Drag to resize outline height • Double-click to reset")
        self._drag_origin_y = None
        self._start_h = None

    def mousePressEvent(self, ev: QtGui.QMouseEvent):
        if ev.button() == QtCore.Qt.LeftButton:
            self._drag_origin_y = ev.globalPosition().y() if hasattr(ev, "globalPosition") else ev.globalY()
            tgt = self._target_getter()
            self._start_h = tgt.height() if tgt else 0
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev: QtGui.QMouseEvent):
        if self._drag_origin_y is None:
            return super().mouseMoveEvent(ev)
        y = ev.globalPosition().y() if hasattr(ev, "globalPosition") else ev.globalY()
        dy = int(y - self._drag_origin_y)
        new_h = max(3 * self.fontMetrics().lineSpacing(), int(self._start_h + dy))
        self.resized.emit(new_h)
        ev.accept()

    def mouseReleaseEvent(self, ev: QtGui.QMouseEvent):
        self._drag_origin_y = None
        self._start_h = None
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev: QtGui.QMouseEvent):
        if ev.button() == QtCore.Qt.LeftButton:
            self.resetAuto.emit()
            ev.accept()
        else:
            super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev: QtGui.QContextMenuEvent):
        m = QtWidgets.QMenu(self)
        act = m.addAction("Reset automatic height")
        if m.exec(ev.globalPos()) is act:
            self.resetAuto.emit()


class _FocusFilter(QtCore.QObject):
    def __init__(self, on_focus_cb):
        super().__init__()
        self._cb = on_focus_cb
    def eventFilter(self, obj, ev):
        if ev.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.MouseButtonPress):
            self._cb()
        return False

class ChapterPane(QtWidgets.QWidget):
    activated = QtCore.Signal()
    collapsedChanged = QtCore.Signal(bool)
    versionChanged = QtCore.Signal(str)  # emit name

    def __init__(self, model: "ChaptersModel", row: int, parent=None):
        super().__init__(parent)
        self.model = model
        self.row = row
        self._current_version_name = "v1"  # sensible default

        # --- layout shell ---
        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().setContentsMargins(6,6,6,6)
        self.layout().setSpacing(6)

        # --- data & defaults ---
        self._bind_chapter_ref()
        self._ensure_default_version()  # creates v1 if empty

        # --- UI build ---
        self._build_header()            # collapse, title, version combo, + button
        self._build_body()              # meta + editor (left) and characters (right)

        # --- wires ---
        self._wire_charlist_buttons()
        self._wire_focus_tracking()
        self._wire_editor_height()

        # --- initial content ---
        self._populate_versions_combo(select_name=self.chapter.active().name, emit=False)
        # No initial editor.set_lines here; _load_version_to_ui will do it
        self._load_version_to_ui()

    # --- Initialization helpers ---

    def _bind_chapter_ref(self):
        self.chapter = self.model.chapter(self.row)

    def reload_chapter_ref(self):
        """Call after rows are reindexed to keep self.chapter in sync with self.row."""
        self._bind_chapter_ref()

    def _ensure_default_version(self):
        if not getattr(self.chapter, "versions", None):
            self.chapter.versions = [ChapterVersion(name="v1", lines=[])]
            self.chapter.active_index = 0

    def _build_header(self):
        head = QtWidgets.QHBoxLayout()
        self.btnCollapse = QtWidgets.QToolButton()
        self.btnCollapse.setText("▾")
        self.btnCollapse.setCheckable(True)
        self.btnCollapse.setChecked(False)
        self.btnCollapse.toggled.connect(self._on_toggle)

        self.titleEdit = QtWidgets.QLineEdit(self.chapter.title)
        self.titleEdit.textEdited.connect(self._save_title)

        # versions
        self.verCombo = QtWidgets.QComboBox()
        self.verCombo.setMinimumWidth(140)
        # NOTE: we populate it later via _populate_versions_combo(...)
        self.verCombo.currentIndexChanged.connect(self._on_version_switched)

        self.addVerBtn = QtWidgets.QToolButton()
        self.addVerBtn.setText("+")
        self.addVerBtn.setToolTip("Add version (clone current)")
        self.addVerBtn.clicked.connect(self._on_add_version)

        head.addWidget(self.btnCollapse, 0)
        head.addWidget(self.titleEdit, 1)
        head.addWidget(QtWidgets.QLabel("Version:"))
        head.addWidget(self.verCombo)
        head.addWidget(self.addVerBtn)
        self.layout().addLayout(head)

    def _build_body(self):
        self.body = QtWidgets.QWidget(self)
        body_layout = QtWidgets.QHBoxLayout(self.body)
        body_layout.setContentsMargins(0,0,0,0)
        body_layout.setSpacing(8)

        # --- meta row (left, top) ---
        meta = QtWidgets.QHBoxLayout()
        self.desc = QtWidgets.QLineEdit(self.chapter.description)
        self.desc.setPlaceholderText("Description…")

        self.setting = QtWidgets.QComboBox()
        self.setting.setEditable(True)
        if self.chapter.setting:
            self.setting.setCurrentText(self.chapter.setting)

        self.date = QtWidgets.QLineEdit(self.chapter.date or "")
        self.date.setPlaceholderText("Date…")

        self.chars = QtWidgets.QLineEdit(", ".join(self.chapter.characters))
        self.chars.setPlaceholderText("Characters (comma-sep)…")

        meta.addWidget(QtWidgets.QLabel("Desc:"));    meta.addWidget(self.desc, 2)
        meta.addWidget(QtWidgets.QLabel("Setting:")); meta.addWidget(self.setting, 1)
        meta.addWidget(QtWidgets.QLabel("Date:"));    meta.addWidget(self.date, 1)

        # --- editor + handle ---
        self.editor = OutlineEditor()
        # do not set_lines here; _load_version_to_ui() will own that

        self._editor_user_height: int | None = None  # None = auto; int = user-fixed

        self.editorContainer = QtWidgets.QWidget(self)
        ec_v = QtWidgets.QVBoxLayout(self.editorContainer)
        ec_v.setContentsMargins(0,0,0,0)
        ec_v.setSpacing(0)
        ec_v.addWidget(self.editor)

        self.editorSizer = _VResizeHandle(lambda: self.editorContainer, self)
        self.editorSizer.resized.connect(self._on_editor_user_resized)
        self.editorSizer.resetAuto.connect(self._on_editor_reset_auto)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(6)
        left.addLayout(meta)
        left.addWidget(self.editorContainer)
        left.addWidget(self.editorSizer)

        # --- characters column (right) ---
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(QtWidgets.QLabel("Characters"))
        self.charList = QtWidgets.QListWidget()
        right.addWidget(self.charList, 1)
        btns = QtWidgets.QHBoxLayout()
        self.charEdit = QtWidgets.QLineEdit(); self.charEdit.setPlaceholderText("Add character…")
        btnAdd = QtWidgets.QToolButton(); btnAdd.setText("Add")
        btnDel = QtWidgets.QToolButton(); btnDel.setText("Remove")
        btns.addWidget(self.charEdit, 1); btns.addWidget(btnAdd); btns.addWidget(btnDel)
        right.addLayout(btns)

        body_layout.addLayout(left, 3)
        body_layout.addLayout(right, 1)
        self.layout().addWidget(self.body, 1)

        # store for wiring
        self._charAddBtn = btnAdd
        self._charDelBtn = btnDel

    def _populate_versions_combo(self, select_name: str | None = None, emit: bool = False):
        names = [v.name for v in self.chapter.versions] or ["v1"]
        with QtCore.QSignalBlocker(self.verCombo):
            self.verCombo.clear()
            self.verCombo.addItems(names)
            if select_name and self.verCombo.findText(select_name) >= 0:
                self.verCombo.setCurrentText(select_name)
            else:
                # prefer active_index, else first
                idx = self.chapter.active_index if 0 <= self.chapter.active_index < len(names) else 0
                self.verCombo.setCurrentIndex(idx)
        if emit:
            self._on_version_switched(self.verCombo.currentIndex())

    def _wire_charlist_buttons(self):
        self._charAddBtn.clicked.connect(
            lambda: (self.charList.addItem(self.charEdit.text().strip()), self.charEdit.clear())
            if self.charEdit.text().strip() else None
        )
        self._charDelBtn.clicked.connect(
            lambda: self.charList.takeItem(self.charList.currentRow())
            if self.charList.currentRow() >= 0 else None
        )
        self.charEdit.returnPressed.connect(self._charAddBtn.click)

    def _wire_focus_tracking(self):
        self._focusFilter = _FocusFilter(lambda: self.activated.emit())
        for w in [self.titleEdit, self.desc, self.setting, self.date, self.editor, self.charEdit, self.charList]:
            w.installEventFilter(self._focusFilter)

    def _wire_editor_height(self):
        if getattr(self.chapter, "editor_user_height", None) is not None:
            self._editor_user_height = self.chapter.editor_user_height
        self.editor.blockCountChanged.connect(lambda _=None: self._apply_editor_height())
        self.editor.textChanged.connect(self._apply_editor_height)
        QtCore.QTimer.singleShot(0, self._apply_editor_height)

    # --- Data sync helpers ---

    def set_version_name(self, vname: str):
        if vname == self._current_version_name:
            return
        self._current_version_name = vname
        self.versionChanged.emit(vname)

    def current_version_name(self) -> str:
        return self._current_version_name

    # persistence into the model Chapter struct
    def sync_into_model(self):
        self.chapter.title = (self.titleEdit.text().strip() or "Untitled")
        self._save_ui_to_version()

    def _save_ui_to_version(self):
        v = self.chapter.active()
        v.description = self.desc.text().strip()
        v.setting = (self.setting.currentText() or "").strip() or None
        v.date = self.date.text().strip() or None
        v.characters = self._collect_char_list()
        v.lines = self.editor.lines()

    def _save_title(self, s):
        idx = self.model.index(self.row, 0)
        self.model.setData(idx, s, QtCore.Qt.EditRole)

    def _on_toggle(self, collapsed: bool):
        self.btnCollapse.setText("▸" if collapsed else "▾")

        if collapsed:
            # If any widget in the body currently has focus, move focus to the header
            fw = QtWidgets.QApplication.focusWidget()
            if fw and (fw is self.editor or self.body.isAncestorOf(fw)):
                # choose where you want header focus to land:
                self.titleEdit.setFocus(QtCore.Qt.OtherFocusReason)
            self.editor.moveCursor(QtGui.QTextCursor.NoMove) # makes caret repaint on some platforms
        self.body.setVisible(not collapsed)                  # actually toggle visibility

        self.collapsedChanged.emit(collapsed)

    def _on_version_combo_changed(self, name: str):
        if not name:
            return
        if name == "➕ New version…":
            self._prompt_add_version()
            return
        # swap apply
        self._save_ui_to_version()
        i = self._find_version_index(name)  # tiny helper: return index by name
        if i < 0:
            return
        self.chapter.active_index = i
        self._load_version_to_ui()
        self.versionChanged.emit(name)

    def _refresh_versions_combo(self, select_name: str | None = None, emit: bool = False):
        names = [v.name for v in self.chapter.versions] or ["v1"]
        with QtCore.QSignalBlocker(self.verCombo):
            self.verCombo.clear()
            self.verCombo.addItems(names)
            if select_name and self.verCombo.findText(select_name) >= 0:
                self.verCombo.setCurrentText(select_name)
            else:
                # ensure something is selected
                self.verCombo.setCurrentIndex(
                    max(0, self.chapter.active_index if 0 <= self.chapter.active_index < len(names) else 0)
                )
        # Only if you explicitly ask for it (not during __init__)
        if emit:
            self._on_version_switched(self.verCombo.currentIndex())

    def _load_version_to_ui(self):
        v = self.chapter.active()
        ed = self.editor
        self.titleEdit.setText(self.chapter.title)
        self.desc.setText(v.description)
        self.setting.setCurrentText(v.setting or "")
        self.date.setText(v.date or "")
        self._load_char_list(v.characters)
        ed.set_lines(v.lines)
        # ed._last_text_snapshot_text = ed.toPlainText()
        # ed._last_text_snapshot_cursor = ed.get_line_col()

    def _on_version_switched(self, idx: int):
        if idx < 0 or idx >= len(self.chapter.versions):
            return
        vname = self.verCombo.currentText()
        # 1) save current UI into the currently active version
        self._save_ui_to_version()
        # 2) switch active index
        self.chapter.active_index = idx
        # 3) load the chosen version into UI
        self._load_version_to_ui()
        # 4) notify the outside world (which will set current outline version name)
        self.versionChanged.emit(vname)

    def _add_version_named(self, name: str, clone_from_current=True):
        # the same logic you use in your "Add version…" action:
        # - build a ChapterVersion(name=..., lines=editor.lines(), description=..., etc.)
        # - append to self.chapter.versions
        # - refresh combo selecting `name`
        self._on_add_version_with_name(name, clone_from_current)  # factor your existing add-version logic here
    
    def _on_add_version_with_name(self, name: str, clone_from_current=True):
        name = (name or "").strip()
        if not name:
            return
        self._save_ui_to_version()
        if clone_from_current:
            cur = self.chapter.active()
            new_chapter_version = ChapterVersion(
                name=name.strip(),
                lines=list(cur.lines),
                description=cur.description,
                setting=cur.setting,
                date=cur.date,
                characters=list(cur.characters),
            )
        else:
            new_chapter_version = ChapterVersion(name=name.strip())

        self.chapter.versions.append(new_chapter_version)
        self.chapter.active_index = len(self.chapter.versions) - 1
        # repopulate & select the new version; will call _on_version_switched() because emit=True
        self._refresh_versions_combo(select_name=name, emit=True)

    def _on_add_version(self):
        count = len(self.chapter.versions)
        suggested = f"v{count+1}"
        name, ok = QtWidgets.QInputDialog.getText(self, "New version", "Version name:", text=suggested)
        if not ok:
            return
        self._on_add_version_with_name(name, clone_from_current=True)

    def _load_char_list(self, names: list[str]):
        self.charList.clear()
        self.charList.addItems(names)

    def _collect_char_list(self) -> list[str]:
        return [self.charList.item(i).text().strip() for i in range(self.charList.count()) if self.charList.item(i).text().strip()]
    
    def refresh_from_model(self, fields: set[str] | None = None):
        """
        Update UI from self.chapter (the model). If fields is None, refresh a sane subset.
        Won’t overwrite widgets the user is actively editing.
        """
        ch = self.chapter
        v  = ch.active() if hasattr(ch, "active") else None
        if fields is None:
            fields = {"title", "description", "setting", "date", "characters", "versions"}

        # Title
        if "title" in fields and hasattr(self, "titleEdit") and not self.titleEdit.hasFocus():
            new = getattr(ch, "title", "")
            if self.titleEdit.text() != new:
                with QtCore.QSignalBlocker(self.titleEdit):
                    self.titleEdit.setText(new)

        # Versions combo (names + selection)
        if "versions" in fields and hasattr(self, "verCombo"):
            want_names = [v.name for v in ch.versions] if getattr(ch, "versions", None) else ["v1"]
            have_names = [self.verCombo.itemText(i) for i in range(self.verCombo.count())]
            if want_names != have_names:
                with QtCore.QSignalBlocker(self.verCombo):
                    self.verCombo.clear()
                    self.verCombo.addItems(want_names)
                    # keep current active index/name if possible
                    sel = (v.name if v else want_names[0])
                    if self.verCombo.findText(sel) >= 0:
                        self.verCombo.setCurrentText(sel)

        # Description / Setting / Date / Characters
        if v:
            if "description" in fields and hasattr(self, "descEdit") and not self.descEdit.hasFocus():
                if self.descEdit.toPlainText() != v.description:
                    with QtCore.QSignalBlocker(self.descEdit):
                        self.descEdit.setPlainText(v.description or "")
            if "setting" in fields and hasattr(self, "settingCombo") and not self.settingCombo.hasFocus():
                if self.settingCombo.currentText() != (v.setting or ""):
                    with QtCore.QSignalBlocker(self.settingCombo):
                        self.settingCombo.setCurrentText(v.setting or "")
            if "date" in fields and hasattr(self, "dateEdit") and not self.dateEdit.hasFocus():
                if self.dateEdit.text() != (v.date or ""):
                    with QtCore.QSignalBlocker(self.dateEdit):
                        self.dateEdit.setText(v.date or "")
            if "characters" in fields and hasattr(self, "charactersEditor") and not self.charactersEditor.hasFocus():
                # whatever your API is for this widget — example:
                self.charactersEditor.set(v.characters or [])

    # editor resizing
    def _compute_auto_editor_height(self) -> int:
        ed = self.editor
        line_h = ed.fontMetrics().lineSpacing()
        blocks = max(6, ed.blockCount())  # show at least ~6 lines by default

        # PySide6: contentsMargins() returns QMargins
        m = ed.contentsMargins()
        top, bottom = m.top(), m.bottom()

        frame = ed.frameWidth() * 2
        # if your body layout exists, include its spacing; otherwise small fudge
        extra = getattr(self.layout(), "spacing", lambda: 6)()
        return int(blocks * line_h + top + bottom + frame + extra)

    def _apply_editor_height(self):
        """Apply user height if set; otherwise compute auto."""
        if self.btnCollapse.isChecked():
            return  # hidden anyway
        if self._editor_user_height is not None:
            h = self._editor_user_height
        else:
            h = self._compute_auto_editor_height()
        self.editorContainer.setFixedHeight(h)

        # track new height
        self.chapter.editor_user_height = self._editor_user_height

    def _on_editor_user_resized(self, new_h: int):
        self._editor_user_height = max(new_h, 3 * self.editor.fontMetrics().lineSpacing())
        self._apply_editor_height()
        if hasattr(self, "chapter"):
            self.chapter.editor_user_height = self._editor_user_height

    def _on_editor_reset_auto(self):
        self._editor_user_height = None
        self._apply_editor_height()
        if hasattr(self, "chapter"):
            self.chapter.editor_user_height = None

    def _on_collapse_toggled(self, collapsed: bool):
        self.body.setVisible(not collapsed)  # your existing body show/hide
        # # also hide/show editor container + handle (if body isn't a single group)
        # self.editorContainer.setVisible(not collapsed)
        # self.editorSizer.setVisible(not collapsed)
        if not collapsed:
            self._apply_editor_height()
