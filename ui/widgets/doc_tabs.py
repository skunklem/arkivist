# ui/widgets/doc_tabs.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal, QEvent, QObject, QSize, QTimer
from PySide6.QtGui import QIcon, QImage, QPainter, QColor, QPalette, QPixmap
from PySide6.QtWidgets import (
    QWidget, QTabWidget, QVBoxLayout, QMessageBox, QSplitter,
    QTabBar, QToolButton, QStyle, QAbstractButton
)

from ui.widgets.doc_page import DocPage


@dataclass(frozen=True, slots=True)
class DocKey:
    """Uniquely identifies a document instance in the tab system."""
    doc_type: str
    doc_id: int
    version_id: Optional[int] = None   # IMPORTANT: default None, not 0
    role: Optional[str] = None         # later: e.g. "main", "notes", "summary"


def _tint_icon(icon: QIcon, color: QColor, size: int) -> QIcon:
    pm = icon.pixmap(size, size)
    img = QImage(pm.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_Source)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(img.rect(), color)
    p.end()

    return QIcon(QPixmap.fromImage(img))


class _CloseButtonHoverFilter(QObject):
    def __init__(self, btn: QAbstractButton, icon_idle: QIcon, icon_hover: QIcon):
        super().__init__(btn)
        self._btn = btn
        self._idle = icon_idle
        self._hover = icon_hover

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Enter:
            self._btn.setIcon(self._hover)
            print("Hovering close button: setting color:", self._hover)
        elif ev.type() == QEvent.Leave:
            self._btn.setIcon(self._idle)
            print("Leaving close button: setting color:", self._idle)
        return False


class DocTabSetWidget(QWidget):
    """
    One tab strip (QTabWidget) plus bookkeeping.
    Later, SplitTabsContainer can host multiple tab sets side-by-side.
    """
    activeDocChanged = Signal(object)  # DocKey
    docOpened = Signal(object)         # DocKey
    docClosed = Signal(object)         # DocKey

    def __init__(self, app, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app = app

        self._pages_by_key: Dict[DocKey, DocPage] = {}
        self._base_title_by_key: Dict[DocKey, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)

        self._close_filters = []

        # Close button: always grey until hover
        self._close_icon_hover = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        idle_color = self.palette().color(QPalette.Disabled, QPalette.Text)
        self._close_icon_idle = _tint_icon(self._close_icon_hover, idle_color, 12)
        print(f"[DocTabSetWidget] Close button idle color: {idle_color.name()}")

        self.tabs.tabCloseRequested.connect(self.request_close_index)
        self.tabs.currentChanged.connect(self._on_current_changed)

        layout.addWidget(self.tabs, 1)

    def _polish_close_button(self, idx: int) -> None:
        bar: QTabBar = self.tabs.tabBar()

        btn = bar.tabButton(idx, QTabBar.RightSide) or bar.tabButton(idx, QTabBar.LeftSide)
        if btn is None:
            print("[DocTabSetWidget] No close button for index:", idx)
            return

        if not isinstance(btn, QAbstractButton):
            print("[DocTabSetWidget] Close button is not a QAbstractButton:", btn)
            print("Available buttons:", bar.tabButton(idx, QTabBar.RightSide), bar.tabButton(idx, QTabBar.LeftSide))
            return

        # Avoid stacking filters if we repolish
        if getattr(btn, "_sa_close_polished", False):
            return
        btn._sa_close_polished = True  # type: ignore[attr-defined]

        # QToolButton-only nicety
        if isinstance(btn, QToolButton):
            btn.setAutoRaise(True)

        btn.setCursor(Qt.PointingHandCursor)
        btn.setIconSize(QSize(12, 12))
        btn.setIcon(self._close_icon_idle)

        # Install hover filter
        print("[DocTabSetWidget] Installing close button hover filter for tab index:", idx)
        f = _CloseButtonHoverFilter(btn, self._close_icon_idle, self._close_icon_hover)
        btn.installEventFilter(f)
        self._close_filters.append(f)

    def _on_title_changed_for_key(self, key: DocKey, title: str) -> None:
        self._base_title_by_key[key] = title
        page = self._pages_by_key.get(key)
        if page is None:
            return
        self._set_dirty_for_key(key, page.is_dirty())

    def update_doc_title(self, doc_type: str, doc_id: int, new_title: str) -> None:
        doc_type = str(doc_type)
        doc_id = int(doc_id)
        for key, page in list(self._pages_by_key.items()):
            if key.doc_type == doc_type and key.doc_id == doc_id:
                page.set_title_text(new_title)

    def open_doc(
        self,
        doc_type: str,
        doc_id: int,
        *,
        version_id: Optional[int] = None,
        role: Optional[str] = None,
        prefer_existing: bool = True,
        show_header: bool = False,
        show_status_line: bool = True,
        initial_prefs: Optional[dict] = None,
    ) -> DocPage:
        key = DocKey(str(doc_type), int(doc_id), int(version_id) if version_id is not None else None, role)

        if prefer_existing and key in self._pages_by_key:
            self.tabs.setCurrentWidget(self._pages_by_key[key])
            return self._pages_by_key[key]

        page = DocPage(
            app=self.app,
            doc_type=key.doc_type,
            doc_id=key.doc_id,
            parent=self.tabs,
            initial_prefs=initial_prefs,
            show_header=show_header,
            show_status_line=show_status_line,
        )

        self._pages_by_key[key] = page
        base = page.title_text()
        self._base_title_by_key[key] = base

        idx = self.tabs.addTab(page, base)
        self.tabs.setCurrentIndex(idx)

        QTimer.singleShot(0, lambda i=idx: self._polish_close_button(i))

        # Dirty indicator in tab title
        page.editorPane.docChanged.connect(
            lambda _doc_id, _ver_id, dirty, _key=key: self._set_dirty_for_key(_key, bool(dirty))
        )
        page.saved.connect(lambda _doc_id, _key=key: self._set_dirty_for_key(_key, False))

        # Title changes (rename should update tab label)
        page.titleChanged.connect(lambda title, _key=key: self._on_title_changed_for_key(_key, title))

        page.setProperty("_doc_key", key)

        self.docOpened.emit(key)
        self.activeDocChanged.emit(key)
        return page

    def current_key(self) -> Optional[DocKey]:
        w = self.tabs.currentWidget()
        if w is None:
            return None
        return w.property("_doc_key")

    def current_page(self) -> Optional[DocPage]:
        w = self.tabs.currentWidget()
        if isinstance(w, DocPage):
            return w
        return None

    def request_close_index(self, index: int) -> None:
        w = self.tabs.widget(index)
        if w is None:
            return

        if not isinstance(w, DocPage):
            self.tabs.removeTab(index)
            w.deleteLater()
            return

        if w.is_dirty():
            mb = QMessageBox(self)
            mb.setIcon(QMessageBox.Warning)
            mb.setWindowTitle("Unsaved changes")
            mb.setText("This tab has unsaved changes.")
            mb.setInformativeText("Save before closing?")
            btn_save = mb.addButton("Save", QMessageBox.AcceptRole)
            btn_discard = mb.addButton("Discard", QMessageBox.DestructiveRole)
            btn_cancel = mb.addButton("Cancel", QMessageBox.RejectRole)
            mb.setDefaultButton(btn_save)
            mb.exec()

            clicked = mb.clickedButton()
            if clicked is btn_cancel:
                return
            if clicked is btn_save:
                w.request_save_all_editors()

        key = w.property("_doc_key")
        if key in self._pages_by_key:
            del self._pages_by_key[key]
        if key in self._base_title_by_key:
            del self._base_title_by_key[key]

        self.tabs.removeTab(index)
        w.deleteLater()

        if key is not None:
            self.docClosed.emit(key)

        cur = self.current_key()
        if cur is not None:
            self.activeDocChanged.emit(cur)

    def request_save_current(self) -> None:
        page = self.current_page()
        if page is not None:
            page.request_save_all_editors()

    def request_save_all_open(self) -> None:
        for page in list(self._pages_by_key.values()):
            if page.is_dirty():
                page.request_save_all_editors()

    def save_current_doc(self) -> bool:
        """Save only the ACTIVE tab. Returns True if we triggered a save."""
        page = self.tabs.currentWidget()
        if page is None:
            print("[DocTabSetWidget] No current page to save")
            return False

        page.request_save_all_editors()
        return True

    def _on_current_changed(self, _index: int) -> None:
        key = self.current_key()
        if key is not None:
            self.activeDocChanged.emit(key)

    def _set_dirty_for_key(self, key: DocKey, dirty: bool) -> None:
        page = self._pages_by_key.get(key)
        if page is None:
            return
        base = self._base_title_by_key.get(key, page.title_text())
        title = f"{base} *" if dirty else base
        idx = self.tabs.indexOf(page)
        if idx >= 0:
            self.tabs.setTabText(idx, title)


class SplitTabsContainer(QWidget):
    """
    Hosts multiple DocTabSetWidget instances in a splitter and tracks which one
    is currently active (focused/last-clicked).
    """
    currentChanged = Signal(object)   # emits DocKey (whatever your DocTabSetWidget emits)
    activeDocChanged = Signal(object)  # DocKey  (compat alias)
    activated = Signal(object)        # emits self (useful once you have popouts/multiple containers)

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.app = app

        self._tabsets: list = []
        self._active_tabset_index: int = 0
        self._obj_to_tabset = {}
        self._last_key_by_tabset = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        # MVP: one tabset to start
        self._add_tabset()

    def _add_tabset(self):
        tabset = DocTabSetWidget(self.app, parent=self.splitter)
        self._tabsets.append(tabset)
        self.splitter.addWidget(tabset)

        # When the tabset’s current doc changes, bubble it up (but only if that tabset is active)
        tabset.activeDocChanged.connect(lambda key, ts=tabset: self._on_tabset_current_changed(ts, key))

        # Track which tabset is “active” (focused / last clicked)
        tabs = tabset.tabs
        bar = tabs.tabBar()
        tabs.installEventFilter(self)
        bar.installEventFilter(self)
        self._obj_to_tabset[tabs] = tabset
        self._obj_to_tabset[bar] = tabset

        return tabset

    def eventFilter(self, obj, ev):
        ts = self._obj_to_tabset.get(obj)
        if ts is not None:
            if ev.type() in (QEvent.FocusIn, QEvent.MouseButtonPress):
                self._set_active_tabset(ts)
                self.activated.emit(self)
        return super().eventFilter(obj, ev)

    def _set_active_tabset(self, tabset):
        try:
            idx = self._tabsets.index(tabset)
        except ValueError:
            return
        self._active_tabset_index = idx

        # If we already know the key for this tabset, emit it as "current"
        key = self._last_key_by_tabset.get(tabset)
        if key is not None:
            self.currentChanged.emit(key)
            self.activeDocChanged.emit(key)  # compat

    def _on_tabset_current_changed(self, tabset, key):
        self._last_key_by_tabset[tabset] = key
        if self.current_tab_container() is tabset:
            self.currentChanged.emit(key)
            self.activeDocChanged.emit(key)  # compat

    def current_tab_container(self):
        if not self._tabsets:
            return None
        return self._tabsets[self._active_tabset_index]

    def save_current_doc(self) -> bool:
        ts = self.current_tab_container()
        if ts is None:
            return False
        return ts.save_current_doc()

    # convenience passthrough: open in the active tabset
    def open_doc(self, *args, **kwargs):
        ts = self.current_tab_container()
        if ts is None:
            return None
        return ts.open_doc(*args, **kwargs)

    def update_doc_title(self, doc_type: str, doc_id: int, new_title: str) -> None:
        for ts in list(self._tabsets):
            ts.update_doc_title(doc_type, doc_id, new_title)
