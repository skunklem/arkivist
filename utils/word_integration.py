from __future__ import annotations

import os, tempfile
from pathlib import Path
import shutil, subprocess

from PySide6.QtCore import QTimer, Signal, QObject
from PySide6.QtWidgets import QMessageBox
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from utils.md import docx_to_markdown

try:
    import win32com.client as win32
    from win32com.client import Dispatch, DispatchWithEvents
    _HAS_WIN32 = True
except Exception:
    _HAS_WIN32 = False

# COM automation, locks, callbacks

class _DocWatchHandler(FileSystemEventHandler):
    def __init__(self, path, on_change):
        self.path = str(Path(path).resolve())
        self.on_change = on_change
    def on_modified(self, event):
        if str(Path(event.src_path).resolve()) == self.path:
            self.on_change()
    on_created = on_modified
    on_moved   = on_modified

class DocxRoundTrip(QObject):
    synced = Signal(str)  # emits md text after sync

    def __init__(self, app, chap_id):
        super().__init__()
        self.app = app
        self.chap_id = chap_id
        self.tmp = None
        self.observer = None
        # COM/Word handles
        self._word_app = None
        self._word_doc = None
        self._word_events = None
        self._doc_fullname_cache = None  # str full path for our doc

    # helper: robustly get FullName from a COM doc
    def _get_fullname(self, doc):
        try:
            return str(Path(doc.FullName).resolve())
        except Exception:
            try:
                return str(Path(Dispatch(doc).FullName).resolve())
            except Exception:
                return None

    def _same_doc(self, doc):
        fn = self._get_fullname(doc)
        return bool(fn and self._doc_fullname_cache and fn == self._doc_fullname_cache)

    def start(self):
        # --- write MD -> DOCX with Pandoc (as we discussed) ---
        cur = self.app.db.conn.cursor()
        cur.execute("SELECT title, content FROM chapters WHERE id=?", (self.chap_id,))
        row = cur.fetchone()
        title, md = (row["title"], row["content"] or "")
        tmpdir = tempfile.mkdtemp(prefix="arkivist_")
        self.tmp = os.path.join(tmpdir, f"{(title or 'chapter').strip()}.docx")

        # REQUIRE Pandoc for Word round-trip
        if not shutil.which("pandoc"):
            QMessageBox.warning(self.app, "Pandoc Required",
                                "Editing in Word requires Pandoc. Please install it and retry.")
            return

        md_path = os.path.join(tmpdir, "chapter.md")
        with open(md_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)

        cmd = ["pandoc", "-f", "gfm", "-t", "docx", "-o", self.tmp, md_path]
        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            out = e.output.decode("utf-8", "replace")
            QMessageBox.critical(self.app, "Pandoc Error", out)
            return

        # --- Prefer COM for save/close events ---
        if _HAS_WIN32:
            try:
                class WordAppEvents:
                    def OnDocumentBeforeSave(self, doc, SaveAsUI, Cancel):
                        try:
                            print("BeforeSave fired")
                            if self.outer._same_doc(doc):
                                self.outer._on_word_before_save(doc)
                        except Exception as ex:
                            print("Word OnDocumentBeforeSave error:", ex)

                    def OnDocumentBeforeClose(self, doc, Cancel):
                        try:
                            print("BeforeClose fired")
                            if self.outer._same_doc(doc):
                                self.outer._on_word_before_close(doc)
                        except Exception as ex:
                            print("Word OnDocumentBeforeClose error:", ex)

                self._word_app = DispatchWithEvents("Word.Application", WordAppEvents)
                self._word_events = self._word_app
                self._word_events.outer = self

                docs = self._word_app.Documents
                self._word_doc = docs.Open(self.tmp)   # read/write
                self._doc_fullname_cache = str(Path(self._word_doc.FullName).resolve())
                self._word_app.Visible = True
                return
            except Exception as e:
                print("Word COM path failed, using watchdog fallback:", e)

        # --- Fallback: shell open + watchdog (no auto close detect) ---
        os.startfile(self.tmp)
        if Observer is None:
            print("watchdog not installed; no auto-sync/close. Use Stop Word Sync.")
            return
        dirpath = str(Path(self.tmp).parent)
        handler = _DocWatchHandler(self.tmp, self._sync_once)
        self.observer = Observer()
        self.observer.schedule(handler, dirpath, recursive=False)
        self.observer.start()

    # --- inside DocxRoundTrip ---

    def _try_sync_once(self) -> bool:
        """Attempt a single MD refresh from the DOCX; return True if it succeeded."""
        try:
            md = docx_to_markdown(self.tmp)
            self.synced.emit(md)
            return True
        except Exception as e:
            print("sync read failed:", e)
            return False

    def _sync_with_retries_then_stop(self, first_delay_ms=400, retries=3, gap_ms=300):
        """
        After a small delay (give Word time to flush), try to sync.
        Retry a few times if the file is still locked/not fully written, then stop.
        """
        self._autosave_attempts = 0

        def attempt():
            ok = self._try_sync_once()
            self._autosave_attempts += 1
            if ok or self._autosave_attempts >= retries:
                try:
                    self.stop()   # tear down COM/watchers
                finally:
                    # tell the app to finish the stop sequence (clear lock + update UI)
                    self.app._finish_stop_word_sync()
            else:
                QTimer.singleShot(gap_ms, attempt)

        QTimer.singleShot(first_delay_ms, attempt)

    def autosave_sync_stop(self):
        """
        Programmatic: save DOC in Word if dirty, then sync (with retries), then stop.
        No prompts.
        """
        try:
            if self._word_doc is not None and getattr(self._word_doc, "Saved", 1) == 0:
                self._word_doc.Save()
        except Exception:
            pass
        # Now sync (after a small delay) and stop
        self._sync_with_retries_then_stop(first_delay_ms=500, retries=3, gap_ms=300)

    def _on_word_before_save(self, doc):
        # Word is about to save; schedule sync shortly AFTER save completes
        QTimer.singleShot(400, self._sync_once)

    def _on_word_before_close(self, doc):
        """Word is closing our document: auto-save (if dirty), then sync+stop."""
        try:
            if getattr(doc, "Saved", 1) == 0:
                doc.Save()
        except Exception:
            pass
        # Use same autosave path (delayed sync + stop)
        self.autosave_sync_stop()


    def _finalize_close(self):
        try:
            self._sync_once()
        finally:
            # releases app-side lock, tears down watcher/COM, updates UI
            self.app.action_stop_word_sync()

    def _sync_once(self):
        try:
            md = docx_to_markdown(self.tmp)
            self.synced.emit(md)
        except Exception as e:
            print("sync read failed:", e)

    def stop(self):
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass
            self.observer = None

        if self._word_doc is not None:
            try:
                # do not force another Save here; if Word is closing, it's handled.
                self._word_doc.Close(SaveChanges=0)
            except Exception:
                pass
            self._word_doc = None

        self._word_app = None
        self._word_events = None
