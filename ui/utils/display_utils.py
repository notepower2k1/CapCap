import os

import sys

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLabel, QMessageBox, QDialog, QScrollArea, QVBoxLayout

try:
    from i18n import t
except ImportError:
    from ui.i18n import t


def log_message(gui, message: str):
    if not message:
        return
    text = str(message)
    # PyInstaller's windowed bootloader sets both __stdout__ and stdout to
    # None. Logging must never interrupt media/timeline initialization just
    # because there is no attached console.
    # Prefer the active stream. ``ui.gui`` replaces it with a tee that writes
    # the packaged application's persistent CapCap\temp\capcap_runtime.log.
    # Writing to ``__stdout__`` first bypassed that file entirely.
    stream = getattr(sys, "stdout", None) or getattr(sys, "__stdout__", None)
    if stream is not None:
        try:
            stream.write(text + "\n")
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    if hasattr(gui, "runtime_log_received"):
        gui.runtime_log_received.emit(text)


def clear_log(gui):
    gui._runtime_logs = []
    gui._pending_runtime_log_entries = []
    gui._runtime_log_view_entry_count = 0
    timer = getattr(gui, "_runtime_log_flush_timer", None)
    if timer is not None:
        timer.stop()
    view = getattr(gui, "runtime_log_view", None)
    if view is not None:
        view.clear()


def show_error(gui, title: str, short_msg: str, details: str = ""):
    if details:
        print(f"[{title}] {details}")
        QMessageBox.critical(gui, t(title), t(short_msg))
    else:
        QMessageBox.critical(gui, t(title), t(short_msg))


def show_frame_preview_dialog(gui, image_path: str, qpixmap_cls, qt):
    dialog = QDialog(gui)
    dialog.setWindowTitle(t("Large Frame Preview"))
    dialog.resize(720, 820)

    layout = QVBoxLayout(dialog)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    label = QLabel()
    label.setAlignment(qt.AlignCenter)
    pixmap = qpixmap_cls(image_path)
    if not pixmap.isNull():
        scaled = pixmap.scaled(660, 720, qt.KeepAspectRatio, qt.SmoothTransformation)
        label.setPixmap(scaled)
    scroll.setWidget(label)
    layout.addWidget(scroll)

    path_label = QLabel(image_path)
    path_label.setWordWrap(True)
    layout.addWidget(path_label)
    dialog.exec()


def show_processed_files(gui):
    def fmt(label, path):
        if not path:
            return f"- {t(label)}: ({t('None')})"
        status = t("OK") if os.path.exists(path) else t("MISSING")
        return f"- {t(label)}: [{status}]\n  {path}"

    lines = []
    lines.append(t("Generated / Selected Files:\n"))
    lines.append(fmt("Video", gui.video_path_edit.text()))
    lines.append(fmt("Extracted Audio", gui.processed_artifacts.get("audio_extracted") or gui.last_extracted_audio))
    lines.append(fmt("Vocals", gui.processed_artifacts.get("vocals") or gui.last_vocals_path))
    lines.append(fmt("Music (no_vocals)", gui.processed_artifacts.get("music") or gui.last_music_path))
    lines.append(fmt("Original SRT", gui.processed_artifacts.get("srt_original") or gui.last_original_srt_path))
    lines.append(fmt("Translated SRT", gui.processed_artifacts.get("srt_translated") or gui.last_translated_srt_path))
    lines.append(fmt("Vietnamese Voice (TTS)", gui.processed_artifacts.get("voice_vi") or gui.last_voice_vi_path))
    lines.append(fmt("Mixed Audio (BG + VI Voice)", gui.processed_artifacts.get("mixed_vi") or gui.last_mixed_vi_path))
    lines.append(fmt("Preview Video (temp)", gui.processed_artifacts.get("preview_video") or gui.last_preview_video_path))
    lines.append(fmt("Final Exported Video", gui.processed_artifacts.get("final_video") or gui.last_exported_video_path))

    QMessageBox.information(gui, t("Processed Files"), "\n\n".join(lines))


def cleanup_temp_preview_files(gui):
    try:
        if getattr(gui, "media_player", None):
            gui.media_player.stop()
            gui.media_player.setSource(QUrl())
    except Exception:
        pass

    preview_temp_root = ""
    if hasattr(gui, "get_project_temp_path"):
        preview_temp_root = gui.get_project_temp_path("preview")

    paths = [
        gui.last_preview_video_path,
        gui.last_exact_preview_5s_path,
        gui.last_exact_preview_frame_path,
        os.path.join(preview_temp_root, "preview_subtitle_5s.srt") if preview_temp_root else "",
        os.path.join(preview_temp_root, "preview_subtitle_full.srt") if preview_temp_root else "",
    ]
    for path in paths:
        gui.cleanup_file_if_exists(path)

    # Prune old styled preview renders to avoid temp folder bloat.
    try:
        temp_root = preview_temp_root
        if os.path.isdir(temp_root):
            candidates = []
            for name in os.listdir(temp_root):
                lower = name.lower()
                if name.startswith("preview_vi_voice_") and lower.endswith(".mp4"):
                    full = os.path.join(temp_root, name)
                    if os.path.isfile(full):
                        candidates.append(full)
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for old_path in candidates[2:]:
                gui.cleanup_file_if_exists(old_path)
    except Exception:
        pass

    gui.last_preview_video_path = ""
    gui.last_exact_preview_5s_path = ""
    gui.last_exact_preview_frame_path = ""
    gui.processed_artifacts.pop("preview_video", None)
    gui.processed_artifacts.pop("preview_video_5s", None)
    gui.processed_artifacts.pop("preview_frame", None)


def apply_windows_dark_title_bar(widget) -> bool:
    """Enable immersive dark title bar on Windows 10/11 using DWM API."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 build 18985+ and Windows 11)
        # Attribute 19 was used in Windows 10 builds 17763 to 18363
        value = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        res = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        if res != 0:
            res = dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        return res == 0
    except Exception:
        return False


def build_contrasting_window_icon(image_path: str, is_dark_bg: bool = True):
    """Generate a multi-resolution QIcon that contrasts with the title bar background.

    When the title bar is dark, tints the dark silhouette logo to crisp white (#FFFFFF)
    so it stands out clearly on the window title bar, taskbar, and Alt-Tab switcher.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

    if not os.path.exists(image_path):
        return QIcon()
    if str(image_path).lower().endswith(".ico"):
        return QIcon(image_path)
    pixmap = QPixmap(image_path)
    if pixmap.isNull():
        return QIcon()

    if is_dark_bg:
        img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        tinted = QImage(img.size(), QImage.Format_ARGB32)
        tinted.fill(Qt.transparent)
        for y in range(img.height()):
            for x in range(img.width()):
                pixel = img.pixelColor(x, y)
                alpha = pixel.alpha()
                if alpha > 0:
                    pixel.setRgb(255, 255, 255, alpha)
                    tinted.setPixelColor(x, y, pixel)
        base_pixmap = QPixmap.fromImage(tinted)
    else:
        base_pixmap = pixmap

    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        canvas = QPixmap(size, size)
        canvas.fill(Qt.transparent)
        scaled = base_pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter = QPainter(canvas)
        painter.drawPixmap(x, y, scaled)
        painter.end()
        icon.addPixmap(canvas)
    return icon


def set_windows_normal_geometry(widget, x: int, y: int, width: int, height: int) -> bool:
    """Configure the restored (normal) position for a maximized window on Windows via Win32 API.

    This ensures that when a window starts maximized, Windows knows the exact centered,
    safe coordinates to restore down to, preventing the title bar from ever being pushed off-screen.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.UINT),
                ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT),
                ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT),
                ("rcNormalPosition", RECT),
            ]

        hwnd = int(widget.winId())
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            wp.rcNormalPosition.left = int(x)
            wp.rcNormalPosition.top = int(y)
            wp.rcNormalPosition.right = int(x + width)
            wp.rcNormalPosition.bottom = int(y + height)
            wp.showCmd = 3  # SW_SHOWMAXIMIZED
            return bool(ctypes.windll.user32.SetWindowPlacement(hwnd, ctypes.byref(wp)))
    except Exception:
        pass
    return False


def apply_application_dark_theme(target):
    """Apply CapCap's deep dark palette to the QApplication or window.

    This ensures that Windows Win32 window creation and Qt canvas painting
    use a dark navy erase brush (#101826) instead of the OS default white (#FFFFFF),
    preventing any white background flash during window show and layout transitions.
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPalette

        dark_palette = QPalette()
        dark_palette.setColor(QPalette.Window, QColor("#101826"))
        dark_palette.setColor(QPalette.WindowText, QColor("#dbe5f3"))
        dark_palette.setColor(QPalette.Base, QColor("#101826"))
        dark_palette.setColor(QPalette.AlternateBase, QColor("#121b2b"))
        dark_palette.setColor(QPalette.ToolTipBase, QColor("#101826"))
        dark_palette.setColor(QPalette.ToolTipText, QColor("#dbe5f3"))
        dark_palette.setColor(QPalette.Text, QColor("#dbe5f3"))
        dark_palette.setColor(QPalette.Button, QColor("#121b2b"))
        dark_palette.setColor(QPalette.ButtonText, QColor("#dbe5f3"))
        dark_palette.setColor(QPalette.BrightText, Qt.red)
        dark_palette.setColor(QPalette.Link, QColor("#38bdf8"))
        dark_palette.setColor(QPalette.Highlight, QColor("#0ea5e9"))
        dark_palette.setColor(QPalette.HighlightedText, Qt.white)
        target.setPalette(dark_palette)
    except Exception:
        pass



