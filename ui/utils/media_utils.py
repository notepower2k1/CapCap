import os
import time

from PySide6.QtCore import QTimer, QUrl

from .media_backend import create_media_backend, get_mpv_startup_diagnostic


def setup_media_player(gui):
    gui.media_player = create_media_backend(gui.video_view)
    if hasattr(gui, "log"):
        gui.log(f"[Preview] media backend: {gui.media_player.backend_name}")
    if getattr(gui.media_player, "backend_name", "") == "qt":
        diagnostic = get_mpv_startup_diagnostic()
        if diagnostic:
            stage = str(diagnostic.get("stage") or "MPV startup")
            summary = str(diagnostic.get("summary") or "Advanced Video Preview is unavailable.")
            technical = str(diagnostic.get("details") or diagnostic.get("error") or "")
            if hasattr(gui, "log"):
                gui.log(f"[Preview] Advanced Video Preview unavailable ({stage}): {technical or summary}")

            def _show_preview_warning():
                # The fallback is intentional and safe, but advanced MPV-only
                # preview features are unavailable. Keep the dialog concise;
                # the detailed loader error is preserved in the runtime log.
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    gui,
                    "Advanced Video Preview Unavailable",
                    f"Advanced Video Preview is unavailable.\n\nReason: {summary}\n\n"
                    "CapCap will use the compatible preview instead. See Logs for technical details.",
                )

            QTimer.singleShot(0, _show_preview_warning)

    gui.play_btn.clicked.connect(gui.toggle_play)
    gui.stop_btn.clicked.connect(gui.stop_video)

    gui.media_player.positionChanged.connect(gui.position_changed)
    gui.media_player.durationChanged.connect(gui.duration_changed)

    # Re-apply the managed visual effects on play/pause/stop. Effects remain
    # visible on the paused frame; only their editing handles are stateful.
    if hasattr(gui.media_player, "stateChanged"):
        gui.media_player.stateChanged.connect(gui._on_preview_state_changed)


def refresh_video_dimensions(gui, path: str, get_video_dimensions):
    try:
        if path and os.path.exists(path):
            width, height = get_video_dimensions(path)
            gui.video_view.set_video_dimensions(width, height)
    except Exception:
        pass


def toggle_play(gui):
    try:
        if hasattr(gui, "ensure_media_backend_ready"):
            gui.ensure_media_backend_ready()
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
        realtime_color_filters = bool(
            hasattr(gui, "_is_realtime_color_filter_state")
            and gui._is_realtime_color_filter_state()
        )

        if gui.media_player.is_playing():
            gui.media_player.pause()
            if hasattr(gui, "refresh_play_button_icon"):
                gui.refresh_play_button_icon()
            gui.timeline.set_playing(False)
            # Keep Blur and Mask applied to the paused frame. The backend's
            # stateChanged handler reapplies the current graph; clearing here
            # caused a visible gap until another layer was selected.
            # A selected Blur/Mask remains in lightweight edit mode while
            # paused. Refresh through the shared timed-layer path so that
            # only that selected effect stays suppressed; other effects keep
            # rendering normally.
            if hasattr(gui, "refresh_timed_layer_preview"):
                gui.refresh_timed_layer_preview()
            if (
                has_active_video_filters
                and filter_workflow_active
                and not realtime_color_filters
                and hasattr(gui, "schedule_live_video_filter_preview")
            ):
                gui.schedule_live_video_filter_preview()
            else:
                gui.schedule_seek_frame_preview()
        else:
            current_source = str(getattr(gui.media_player, "_source_path", "") or "")
            preview_source = str(getattr(gui, "last_preview_video_path", "") or "")
            if has_active_video_filters and filter_workflow_active and not realtime_color_filters:
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
            if realtime_color_filters and hasattr(gui, "_apply_realtime_color_filter_preview"):
                gui._apply_realtime_color_filter_preview()
            # Pressing Play is the commit boundary for paused Blur/Mask
            # edits. Restore their latest geometry before MPV resumes.
            if hasattr(gui, "prepare_preview_for_review_mode"):
                gui.prepare_preview_for_review_mode()
            elif hasattr(gui, "commit_deferred_effect_editing"):
                gui.commit_deferred_effect_editing(refresh=False)
            if hasattr(gui, "apply_preview_blur_region"):
                gui.apply_preview_blur_region(force=True)
            if (
                hasattr(gui, "video_view")
                and hasattr(gui.video_view, "set_blur_edit_enabled")
                and hasattr(gui, "_blur_effect_enabled")
                and gui._blur_effect_enabled()
            ):
                gui.video_view.set_blur_edit_enabled(False)
            # The track inspector is always expanded - no auto-collapse.
            gui.media_player.play()
            gui.timeline.set_playing(True)
            if hasattr(gui, "refresh_play_button_icon"):
                gui.refresh_play_button_icon()
            # Apply the M1 mask filter on play so the colour shows
            # while the video is playing. Use force=True to bypass
            # the is_playing() check (the play() call above already
            # set the state to PlayingState).
            if hasattr(gui, "_apply_mask_to_preview"):
                try:
                    gui._apply_mask_to_preview(force=True)
                except Exception:
                    pass
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] toggle play failed: {exc}")
        if hasattr(gui, "show_error"):
            gui.show_error("Preview Playback Failed", "Could not start or pause video preview.", str(exc))


def stop_video(gui):
    if hasattr(gui, "ensure_media_backend_ready"):
        gui.ensure_media_backend_ready()
    if hasattr(gui, "audio_preview_player"):
        gui.audio_preview_player.stop()
    gui.media_player.stop()
    if hasattr(gui, "refresh_play_button_icon"):
        gui.refresh_play_button_icon()
    if hasattr(gui, "media_player"):
        gui.media_player.clear_blur_region()
    if hasattr(gui, "_sync_blur_controls"):
        gui._sync_blur_controls()
    gui.timeline.set_playing(False)
    gui.schedule_seek_frame_preview()


def position_changed(gui, position):
    gui.timeline.set_position(position)
    update_duration_label(gui, position, gui.media_player.duration())
    try:
        gui.refresh_timed_layer_preview(position)
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] timed layer refresh error: {exc}")
    try:
        gui.update_playback_subtitle_highlight(position)
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] position highlight error: {exc}")
    # Apply audio fade-in/fade-out during playback
    try:
        _apply_audio_fade(gui, position)
    except Exception:
        pass
    # Disable the play button when the playhead reaches the end of
    # the video (within 250 ms of the duration) so the user can tell
    # at a glance that playback has finished. The button is re-enabled
    # on seek (`set_position`) or when a new source is loaded.
    try:
        if hasattr(gui, "play_btn") and not getattr(
            gui, "_disable_play_at_end", False
        ):
            duration_ms = 0
            try:
                duration_ms = int(gui.media_player.duration() or 0)
            except Exception:
                duration_ms = 0
            at_end = duration_ms > 0 and position >= duration_ms - 250
            try:
                is_playing = bool(gui.media_player.is_playing())
            except Exception:
                is_playing = False
            gui.play_btn.setEnabled(not at_end)
            # When playback ends naturally, update the icon/tooltip to
            # "Play" so the next click re-starts from the end.
            if at_end and not is_playing and hasattr(gui, "refresh_play_button_icon"):
                gui.refresh_play_button_icon()
    except Exception:
        pass


def _apply_audio_fade(gui, position_ms: int):
    """Apply fade-in/fade-out to audio track volumes during playback.

    The fade values are stored in each track's metadata. During the
    fade-in window the volume ramps from 0 to the set volume; during
    the fade-out window it ramps from the set volume down to 0.
    """
    if not getattr(gui, "media_player", None):
        return
    try:
        duration_ms = int(gui.media_player.duration())
    except Exception:
        duration_ms = 0
    if duration_ms <= 0:
        return
    pos_s = position_ms / 1000.0
    for track_name in ("A1 Audio", "TS1"):
        track, _ = _find_audio_track(gui, track_name)
        if track is None:
            continue
        meta = getattr(track, "metadata", None) or {}
        try:
            fade_in = float(meta.get("_fade_in", 0.0))
            fade_out = float(meta.get("_fade_out", 0.0))
        except (TypeError, ValueError):
            fade_in = fade_out = 0.0
        base_vol = _compute_base_volume(gui, track, meta)
        # Compute fade multiplier
        mult = 1.0
        if fade_in > 0 and pos_s < fade_in:
            mult = min(mult, pos_s / fade_in if fade_in > 0 else 1.0)
        if fade_out > 0:
            remaining = (duration_ms - position_ms) / 1000.0
            if remaining < fade_out:
                mult = min(mult, max(0.0, remaining / fade_out) if fade_out > 0 else 1.0)
        effective = base_vol * mult
        if track_name == "A1 Audio":
            if hasattr(gui.media_player, "set_original_volume"):
                gui.media_player.set_original_volume(effective)
        elif track_name == "TS1":
            if hasattr(gui.media_player, "set_dubbed_volume"):
                gui.media_player.set_dubbed_volume(effective)


def _find_audio_track(gui, name: str):
    if not getattr(gui, "timeline", None) or not gui.timeline._timeline:
        return None, None
    for t in gui.timeline._timeline.tracks:
        if t.name == name:
            return t, name
    return None, None


def _compute_base_volume(gui, track, meta) -> float:
    track_name = getattr(track, "name", "") or ""
    default_vol = 50.0 if track_name.startswith("A1") else 100.0
    try:
        vol = float(meta.get("_volume", default_vol))
    except (TypeError, ValueError):
        vol = default_vol
    try:
        gain_db = float(meta.get("_gain_db", 0.0))
    except (TypeError, ValueError):
        gain_db = 0.0
    effective = vol * (10 ** (gain_db / 20.0))
    return max(0.0, min(200.0, effective))


def duration_changed(gui, duration):
    gui.timeline.set_duration(duration)
    update_duration_label(gui, gui.media_player.position(), duration)


def set_position(gui, position):
    if hasattr(gui, "ensure_media_backend_ready"):
        gui.ensure_media_backend_ready()
    gui.media_player.setPosition(position)
    gui.timeline.set_position(position)
    try:
        gui.update_playback_subtitle_highlight(position)
    except Exception as exc:
        if hasattr(gui, "log"):
            gui.log(f"[Preview] seek highlight error: {exc}")
    # Seeking away from the end re-enables the play button.
    try:
        if hasattr(gui, "play_btn"):
            duration_ms = 0
            try:
                duration_ms = int(gui.media_player.duration() or 0)
            except Exception:
                duration_ms = 0
            gui.play_btn.setEnabled(duration_ms <= 0 or position < duration_ms - 250)
    except Exception:
        pass
    if (
        hasattr(gui, "has_active_video_filters")
        and gui.has_active_video_filters()
        and not (hasattr(gui, "_is_realtime_color_filter_state") and gui._is_realtime_color_filter_state())
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

    if hasattr(gui, "ensure_media_backend_ready"):
        gui.ensure_media_backend_ready()
    gui.video_path_edit.setText(file_path)
    gui._current_video_path = os.path.abspath(file_path)
    if hasattr(gui, "settings"):
        from views.launcher import LauncherWindow
        LauncherWindow.add_recent(gui.settings, file_path)
    gui.media_player.setSource(QUrl.fromLocalFile(file_path))
    gui._allow_post_pipeline_preview_assets = False
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
    if hasattr(gui, "auto_frame_preview_timer"):
        gui.auto_frame_preview_timer.stop()
    if hasattr(gui, "seek_frame_preview_timer"):
        gui.seek_frame_preview_timer.stop()
    QTimer.singleShot(120, lambda: gui.media_player.setPosition(0))
    QTimer.singleShot(220, gui.video_view.reposition_subtitle)
    gui.refresh_ui_state()
    gui.sync_live_subtitle_preview()


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
