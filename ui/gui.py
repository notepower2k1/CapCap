import os
import shutil
import sys
import threading
import traceback

_SINGLE_INSTANCE_HANDLE = None


def _acquire_single_instance() -> bool:
    """Allow only one GUI process (worker-server children are exempt)."""
    global _SINGLE_INSTANCE_HANDLE
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        # A stable name makes separate launches of the packaged EXE share the
        # same kernel mutex, while avoiding the Global namespace's permission
        # requirements on locked-down Windows accounts.
        name = "Local\\CapCap.SingleInstance"
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        _SINGLE_INSTANCE_HANDLE = handle
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        # A mutex failure should never prevent the application from starting.
        return True

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication

# Keep the worker entrypoint before the GUI import.  In a windowed PyInstaller
# build importing main_window first can initialize Qt/UI resources and hide the
# actual worker startup error before the remote API server is reached.
if __name__ == "__main__" and ("--worker-server" in sys.argv or os.getenv("CAPCAP_RUN_REMOTE_API_SERVER") == "1"):
    from remote_api_server import main as remote_api_server_main

    remote_api_server_main()
    raise SystemExit(0)

def _set_windows_app_user_model_id():
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("notepower2k1.CapCap.App")
        except Exception:
            pass


if __name__ == "__main__":
    _set_windows_app_user_model_id()
    if not _acquire_single_instance():
        raise SystemExit(0)

from main_window import VideoTranslatorGUI
from utils.display_utils import apply_application_dark_theme

__all__ = ["VideoTranslatorGUI"]


class _RuntimeLogCollector:
    """Tee terminal output into the GUI once its log panel is available."""

    def __init__(self):
        self._pending = []
        self._window = None
        self._file_path = ""
        # A windowed PyInstaller build has no terminal. Keep a small
        # session log beside the executable so startup failures are not
        # silently lost before the in-app Logs panel is available.
        try:
            root = (
                os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False)
                else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            )
            log_dir = os.path.join(root, "temp")
            os.makedirs(log_dir, exist_ok=True)
            self._file_path = os.path.join(log_dir, "capcap_runtime.log")
            with open(self._file_path, "w", encoding="utf-8") as handle:
                handle.write("[CapCap] Runtime log started.\n")
        except OSError:
            self._file_path = ""

    def add(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if self._file_path:
            try:
                with open(self._file_path, "a", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            except OSError:
                pass
        if self._window is None:
            self._pending.append(text)
            self._pending = self._pending[-10000:]
            return
        self._window.runtime_log_received.emit(text)

    def attach(self, window) -> None:
        self._window = window
        for message in self._pending:
            window.runtime_log_received.emit(message)
        self._pending.clear()


class _LogTee:
    def __init__(self, stream, collector):
        self._stream = stream
        self._collector = collector
        self._partial = ""

    def write(self, data):
        text = str(data or "")
        if self._stream is not None:
            try:
                self._stream.write(text)
            except Exception:
                # Windowed PyInstaller builds may expose stdout/stderr as
                # None or as an already-closed stream. Runtime logs should
                # still reach the in-app collector in that case.
                pass
        self._partial += text
        lines = self._partial.splitlines(keepends=True)
        self._partial = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                self._collector.add(line.rstrip())
            else:
                self._partial = line
        return len(text)

    def flush(self):
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass
        if self._partial:
            self._collector.add(self._partial)
            self._partial = ""

    def isatty(self):
        return bool(self._stream is not None and getattr(self._stream, "isatty", lambda: False)())

    def fileno(self):
        if self._stream is None:
            raise OSError("No console stream is attached")
        return self._stream.fileno()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _capture_runtime_output():
    collector = _RuntimeLogCollector()
    sys.stdout = _LogTee(sys.stdout, collector)
    sys.stderr = _LogTee(sys.stderr, collector)

    original_thread_hook = getattr(threading, "excepthook", None)
    if original_thread_hook is not None:
        def _thread_exception_hook(args):
            collector.add(f"[Unhandled Thread Error] {args.exc_type.__name__}: {args.exc_value}")
            original_thread_hook(args)
        threading.excepthook = _thread_exception_hook
    return collector


def _app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bootstrap_env(app_root: str) -> None:
    env_path = os.path.join(app_root, ".env")
    env_example_path = os.path.join(app_root, ".env_example")

    if not os.path.exists(env_path) and os.path.exists(env_example_path):
        shutil.copyfile(env_example_path, env_path)

    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            os.environ.setdefault(key, value.strip())


def launch_editor_for_video(target_video: str, runtime_logs=None) -> VideoTranslatorGUI:
    win = VideoTranslatorGUI()
    if runtime_logs is not None:
        runtime_logs.attach(win)
    win._current_video_path = os.path.abspath(target_video)
    win.video_path_edit.setText(target_video)

    # 1. Resolve video dimensions before show so the canvas matches exact video aspect ratio
    if hasattr(win, "refresh_video_dimensions"):
        win.refresh_video_dimensions(target_video)

    # 2. Load project state and timeline metadata
    win.current_project_state = win.ensure_current_project()
    win.load_project_context(win.current_project_state)

    if hasattr(win, "timeline") and hasattr(win.timeline, "set_video_source"):
        dur = 0.0
        try:
            from views.launcher import _get_video_duration
            dur = float(_get_video_duration(win._current_video_path) or 0.0)
        except Exception:
            pass
        if dur <= 0.0:
            dur = 60.0
        win.timeline.set_video_source(win._current_video_path, dur)
        ensure_tracks = getattr(win.timeline, "_ensure_tracks_populated", None)
        if callable(ensure_tracks):
            ensure_tracks()
        redraw = getattr(win.timeline, "_redraw", None)
        if callable(redraw):
            redraw()
    win.schedule_timeline_visual_refresh(waveform=True, thumbnails=True)

    # 3. Resolve initial layout geometry while hidden so first paint is already settled
    win.prepare_initial_editor_layout()

    # 4. Show the complete editor UI cohesively as one window and paint immediately
    win.show()
    win.raise_()
    win.activateWindow()
    win.setFocus()
    try:
        win.repaint()
    except Exception:
        pass

    # 5. Defer media backend loading slightly so the entire UI paints first on screen
    # before MPV initializes and renders into the settled video canvas
    def _deferred_load_media():
        try:
            if not win.isVisible():
                return
            win.ensure_media_backend_ready()
            win.media_player.setSource(QUrl.fromLocalFile(target_video))
            if hasattr(win, "refresh_video_dimensions"):
                win.refresh_video_dimensions(target_video)
            if hasattr(win, "sync_preview_audio_track_to_output"):
                win.sync_preview_audio_track_to_output(apply_to_player=True, force=True)
            if hasattr(win, "_sync_preview_framing_to_player"):
                win._sync_preview_framing_to_player()
        except Exception as exc:
            print(f"[Preview] Deferred media load error: {exc}")

    QTimer.singleShot(50, _deferred_load_media)

    return win


if __name__ == "__main__":
    app_root = _app_root()
    _bootstrap_env(app_root)
    os.chdir(app_root)
    runtime_logs = _capture_runtime_output()
    app = QApplication(sys.argv)
    apply_application_dark_theme(app)

    from PySide6.QtGui import QIcon
    from runtime_paths import asset_path
    from utils.display_utils import build_contrasting_window_icon

    app_icon_path = asset_path("capcap.ico")
    if not os.path.exists(app_icon_path):
        app_icon_path = asset_path("capcap.png")
    if os.path.exists(app_icon_path):
        app.setWindowIcon(build_contrasting_window_icon(app_icon_path))

    from views.launcher import show_launcher, LauncherWindow

    video_path = show_launcher(None)
    if not video_path:
        sys.exit(0)

    LauncherWindow.add_recent(None, video_path)

    window = launch_editor_for_video(video_path, runtime_logs)
    sys.exit(app.exec())
