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
    try:
        if hasattr(gui, "audio_preview_player"):
            gui.audio_preview_player.stop()

        video_path = ""
        if hasattr(gui, "video_path_edit"):
            video_path = gui.video_path_edit.text().strip()

        # If the backend lost its loaded source after UI/state changes, restore it lazily.
        if video_path and os.path.exists(video_path):
            source_path = str(getattr(gui.media_player, "_source_path", "") or "")
            if not source_path:
                gui.media_player.setSource(QUrl.fromLocalFile(video_path))

        has_active_video_filters = bool(hasattr(gui, "has_active_video_filters") and gui.has_active_video_filters())
        filter_workflow_active = bool(hasattr(gui, "is_filter_workflow_active") and gui.is_filter_workflow_active())

        if gui.media_player.is_playing():
            gui.media_player.pause()
            gui.timeline.set_playing(False)
            if (
                has_active_video_filters
                and filter_workflow_active
                and hasattr(gui, "schedule_live_video_filter_preview")
            ):
                gui.schedule_live_video_filter_preview()
            else:
                gui.schedule_seek_frame_preview()
        else:
            current_source = str(getattr(gui.media_player, "_source_path", "") or "")
            preview_source = str(getattr(gui, "last_preview_video_path", "") or "")
            if has_active_video_filters and filter_workflow_active:
                if filter_workflow_active and bool(getattr(gui, "_video_filter_preview_dirty", False)):
                    gui._play_video_filter_preview_when_ready = False
                    if hasattr(gui, "video_filter_render_status_label") and gui.video_filter_render_status_label is not None:
                        gui.video_filter_render_status_label.setText("Filter changes are pending. Click Apply Filter before playing.")
                        gui.video_filter_render_status_label.setVisible(True)
                    if hasattr(gui, "video_filter_render_progress") and gui.video_filter_render_progress is not None:
                        gui.video_filter_render_progress.setVisible(False)
                    if hasattr(gui, "refresh_ui_state"):
                        gui.refresh_ui_state()
                    return
                if not (current_source and preview_source and os.path.exists(preview_source) and os.path.abspath(current_source) == os.path.abspath(preview_source)):
                    gui.seek_frame_preview_timer.stop()
                    gui._play_video_filter_preview_when_ready = True
                    if hasattr(gui, "hide_filter_thumbnail_preview"):
                        gui.hide_filter_thumbnail_preview()
                    gui.preview_video()
                    return
            gui.seek_frame_preview_timer.stop()
            if hasattr(gui, "hide_filter_thumbnail_preview"):
                gui.hide_filter_thumbnail_preview()
            gui.media_player.play()
            gui.timeline.set_playing(True)
        if hasattr(gui, "_refresh_preview_audio_controls"):
            gui._refresh_preview_audio_controls()
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] toggle play failed: {exc}")
        if hasattr(gui, "show_error"):
            gui.show_error("Preview Playback Failed", "Could not start or pause video preview.", str(exc))


def stop_video(gui):
    if hasattr(gui, "audio_preview_player"):
        gui.audio_preview_player.stop()
    gui.media_player.stop()
    gui.timeline.set_playing(False)
    gui.schedule_seek_frame_preview()
    if hasattr(gui, "_refresh_preview_audio_controls"):
        gui._refresh_preview_audio_controls()


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
    if (
        hasattr(gui, "has_active_video_filters")
        and gui.has_active_video_filters()
        and hasattr(gui, "is_filter_workflow_active")
        and gui.is_filter_workflow_active()
        and not gui.media_player.is_playing()
    ):
        if hasattr(gui, "schedule_live_video_filter_preview"):
            gui.schedule_live_video_filter_preview()
    else:
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
    if hasattr(gui, "_refresh_preview_audio_controls"):
        gui._refresh_preview_audio_controls()


def update_frame_preview_thumbnail(gui, image_path: str, qpixmap_cls, qt):
    pixmap = qpixmap_cls(image_path)
    if pixmap.isNull():
        gui.frame_preview_image_label.setText("Could not load frame preview")
        gui.frame_preview_image_label.setPixmap(qpixmap_cls())
        return
    target_width = 0
    target_height = 0
    if hasattr(gui, "video_view") and gui.video_view is not None:
        target_width = int(gui.video_view.width() or 0)
        target_height = int(gui.video_view.height() or 0)
    if target_width <= 0 or target_height <= 0:
        target_width = int(gui.frame_preview_image_label.width() or 0)
        target_height = int(gui.frame_preview_image_label.height() or 0)
    if target_width <= 0:
        target_width = 960
    if target_height <= 0:
        target_height = 540
    scaled = pixmap.scaled(target_width, target_height, qt.KeepAspectRatio, qt.SmoothTransformation)
    gui.frame_preview_image_label.setPixmap(scaled)
    gui.frame_preview_image_label.setText("")
    gui.frame_preview_status_label.setText(f"Exact frame preview synced at {time.strftime('%H:%M:%S')}.")
