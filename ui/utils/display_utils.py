import os

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLabel, QMessageBox, QDialog, QScrollArea, QVBoxLayout


def log_message(gui, message: str):
    if not message:
        return
    print(str(message))


def clear_log(gui):
    return None


def show_error(gui, title: str, short_msg: str, details: str = ""):
    if details:
        print(f"[{title}] {details}")
        QMessageBox.critical(gui, title, short_msg)
    else:
        QMessageBox.critical(gui, title, short_msg)


def show_frame_preview_dialog(gui, image_path: str, qpixmap_cls, qt):
    dialog = QDialog(gui)
    dialog.setWindowTitle("Large Frame Preview")
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
            return f"- {label}: (none)"
        status = "OK" if os.path.exists(path) else "MISSING"
        return f"- {label}: [{status}]\n  {path}"

    lines = []
    lines.append("Generated / Selected Files:\n")
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

    QMessageBox.information(gui, "Processed Files", "\n\n".join(lines))


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



