import os
import time

from PySide6.QtCore import QTimer, QUrl

from .media_backend import create_media_backend


def setup_media_player(gui):
    gui.media_player = create_media_backend(gui.video_view)
    if hasattr(gui, "log"):
        gui.log(f"[Preview] media backend: {gui.media_player.backend_name}")

    gui.play_btn.clicked.connect(gui.toggle_play)
    gui.stop_btn.clicked.connect(gui.stop_video)

    gui.media_player.positionChanged.connect(gui.position_changed)
    gui.media_player.durationChanged.connect(gui.duration_changed)


def refresh_video_dimensions(gui, path: str, get_video_dimensions):
    try:
        if path and os.path.exists(path):
            width, height = get_video_dimensions(path)
            gui.video_view.set_video_dimensions(width, height)
    except Exception:
        pass


def toggle_play(gui):
    if hasattr(gui, "audio_preview_player"):
        gui.audio_preview_player.stop()
    if gui.media_player.is_playing():
        gui.media_player.pause()
        gui.play_btn.setText("Play")
        gui.timeline.set_playing(False)
        gui.schedule_seek_frame_preview()
    else:
        gui.seek_frame_preview_timer.stop()
        gui.media_player.play()
        gui.play_btn.setText("Pause")
        gui.timeline.set_playing(True)


def stop_video(gui):
    if hasattr(gui, "audio_preview_player"):
        gui.audio_preview_player.stop()
    gui.media_player.stop()
    gui.play_btn.setText("Play")
    gui.timeline.set_playing(False)
    gui.schedule_seek_frame_preview()


def position_changed(gui, position):
    gui.timeline.set_position(position)
    update_duration_label(gui, position, gui.media_player.duration())
    try:
        gui.update_playback_subtitle_highlight(position)
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] position highlight error: {exc}")


def duration_changed(gui, duration):
    gui.timeline.set_duration(duration)
    update_duration_label(gui, gui.media_player.position(), duration)


def set_position(gui, position):
    gui.media_player.setPosition(position)
    gui.timeline.set_position(position)
    try:
        gui.update_playback_subtitle_highlight(position)
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] seek highlight error: {exc}")
    gui.schedule_seek_frame_preview()


def update_duration_label(gui, current, total):
    def fmt(ms):
        seconds = max(0, ms // 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"

    gui.time_label.setText(f"{fmt(current)} / {fmt(total)}")


def browse_video(gui):
    from PySide6.QtWidgets import QFileDialog

    file_path, _ = QFileDialog.getOpenFileName(gui, "Open Video", "", "Video Files (*.mp4 *.mkv *.avi *.mov)")
    if not file_path:
        return

    gui.video_path_edit.setText(file_path)
    gui.media_player.setSource(QUrl.fromLocalFile(file_path))
    gui.refresh_video_dimensions(file_path)
    gui.play_btn.setText("Play")

    gui.timeline.set_segments([])
    gui.timeline.set_playing(False)
    gui.current_segments = []
    gui.current_translated_segments = []
    gui.current_segment_models = []
    gui.current_translated_segment_models = []

    gui.current_project_state = gui.ensure_current_project()
    gui.load_project_context(gui.current_project_state)

    try:
        gui.media_player.pause()
    except Exception:
        pass
    QTimer.singleShot(120, lambda: gui.media_player.setPosition(0))
    QTimer.singleShot(220, gui.video_view.reposition_subtitle)
    gui.refresh_ui_state()
    gui.sync_live_subtitle_preview()
    gui.schedule_auto_frame_preview()


def update_frame_preview_thumbnail(gui, image_path: str, qpixmap_cls, qt):
    pixmap = qpixmap_cls(image_path)
    if pixmap.isNull():
        gui.frame_preview_image_label.setText("Could not load frame preview")
        gui.frame_preview_image_label.setPixmap(qpixmap_cls())
        return
    scaled = pixmap.scaled(320, 220, qt.KeepAspectRatio, qt.SmoothTransformation)
    gui.frame_preview_image_label.setPixmap(scaled)
    gui.frame_preview_image_label.setText("")
    gui.frame_preview_status_label.setText(f"Exact frame preview synced at {time.strftime('%H:%M:%S')}.")
