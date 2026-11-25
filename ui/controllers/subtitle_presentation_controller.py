"""Subtitle display settings, a secondary SRT editor and independent style banks."""
from __future__ import annotations

import copy
import json
import hashlib
from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, QTimer, QSignalBlocker, Qt, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFileDialog, QMessageBox, QDialog)

from app.subtitle_presentation import (KEY, config, build_secondary, signature, read_srt, valid_segments,
    map_to_timeline, presentation_ass, secondary_style, text_signature, secondary_signature)
from i18n import t


class _BuildWorker(QThread):
    completed = Signal(object, str)
    progress = Signal(int, int)
    status_changed = Signal(str, str)

    def __init__(self, settings, original, language, ai, style, parent, translation_options=None):
        super().__init__(parent)
        self.args = (settings, original, language, ai, style)
        self.translation_options = dict(translation_options or {})

    def cancel(self):
        self.requestInterruption()

    def run(self):
        from services.engine_runtime import EngineRuntime
        settings, original, language, ai, style = self.args
        completed = set()

        def on_batch(start, rows):
            if self.isInterruptionRequested():
                raise InterruptedError("canceled")
            completed.update(range(int(start), min(len(original), int(start) + len(rows or []))))
            self.progress.emit(len(completed), len(original))

        try:
            options = dict(self.translation_options, batch_callback=on_batch,
                status_callback=lambda provider, message: self.status_changed.emit(str(provider or ""), str(message or "")),
                is_canceled=self.isInterruptionRequested)
            result = build_secondary(settings, original, engine=EngineRuntime(), source_language=language,
                                     translator_ai=ai, translator_style=style, translation_options=options)
            self.completed.emit(None if self.isInterruptionRequested() else result,
                                "canceled" if self.isInterruptionRequested() else "")
        except Exception as exc:
            self.completed.emit(None, "canceled" if self.isInterruptionRequested() or isinstance(exc, InterruptedError) else str(exc))


class SubtitlePresentationController(QObject):
    def __init__(self, gui, presentation_layout, style_layout):
        super().__init__(gui)
        self.gui, self.busy, self.switching = gui, False, False
        self.cfg, self.role = config(), "primary"
        self.worker = None
        self.progress_dialog = None
        self.selected_secondary_index = -1
        self.dragging_role = None
        self.overlays = []
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.commit_style)
        self.mode = QComboBox()
        self.mode.setObjectName("subtitle_display_mode")
        for label, key in (("No subtitles", "none"), ("With subtitles", "single"), ("Bilingual subtitles", "bilingual")):
            self.mode.addItem(t(label), key)
        presentation_layout.addWidget(self.mode)
        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        self.source = QComboBox()
        self.source.setObjectName("secondary_subtitle_source")
        for label, key in (("Original subtitles", "original"), ("Import SRT", "srt"), ("Translate to another language", "translate")):
            self.source.addItem(t(label), key)
        layout.addWidget(QLabel(t("Secondary subtitle source")))
        layout.addWidget(self.source)
        note = QLabel(t("Imported SRT uses source-video timing. Fit Voice also adjusts the secondary subtitles."))
        note.setWordWrap(True)
        note.setObjectName("helperLabel")
        layout.addWidget(note)
        self.language = QComboBox()
        from language_support import LANGUAGES
        for label, code in (("Chinese", "zh"),) + LANGUAGES:
            self.language.addItem(t(label), code)
        self.language.setCurrentIndex(max(0, self.language.findData("en")))
        layout.addWidget(self.language)
        buttons = QHBoxLayout()
        self.import_btn = QPushButton(t("Choose SRT"))
        self.build_btn = QPushButton(t("Create / Update secondary subtitles"))
        buttons.addWidget(self.import_btn)
        buttons.addWidget(self.build_btn)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        presentation_layout.addWidget(self.panel)
        row = QHBoxLayout()
        self.style_role = QComboBox()
        self.style_role.setObjectName("subtitle_style_role")
        self.style_role.addItem(t("Main subtitle style"), "primary")
        self.style_role.addItem(t("Secondary subtitle style"), "secondary")
        self.copy_btn = QPushButton(t("Copy main style"))
        self.swap_btn = QPushButton(t("Swap subtitle positions"))
        row.addWidget(self.copy_btn)
        row.addWidget(self.swap_btn)
        style_layout.insertWidget(0, self.style_role)
        style_layout.insertLayout(1, row)
        self.mode.currentIndexChanged.connect(self.changed)
        self.source.currentIndexChanged.connect(self.source_changed)
        self.language.currentIndexChanged.connect(self.source_changed)
        self.import_btn.clicked.connect(self.import_srt)
        self.build_btn.clicked.connect(self.build)
        self.style_role.currentIndexChanged.connect(self.switch_style)
        self.copy_btn.clicked.connect(self.copy_style)
        self.swap_btn.clicked.connect(self.swap_positions)
        self.refresh_widgets()

    def initialize(self):
        if getattr(self, "initialized", False):
            return
        self.initialized = True
        self.cfg = config()
        self.cfg["styles"]["primary"] = copy.deepcopy(self.gui._current_subtitle_style_controls_state())
        for role in ("primary", "secondary"):
            try:
                cached = json.loads(self.gui.settings.value("subtitle_style_default_" + role, "{}"))
                if isinstance(cached, dict) and cached:
                    self.cfg["styles"][role] = cached
            except (TypeError, ValueError):
                pass
        if "secondary" not in self.cfg["styles"]:
            secondary = copy.deepcopy(self.cfg["styles"]["primary"])
            secondary.update(size=max(30, round(secondary.get("size", 60) * .75)), animation="Static")
            secondary["position"] = dict(secondary.get("position") or {}, position_mode="custom",
                custom_position_enabled=True, custom_position_x=50, custom_position_y=76)
            self.cfg["styles"]["secondary"] = secondary
        self.apply_controls(self.cfg["styles"]["primary"])
        self.default_styles = copy.deepcopy(self.cfg["styles"])
        self._committed_styles = copy.deepcopy(self.cfg["styles"])
        self.refresh_widgets()
        self.gui.video_view.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Resize, QEvent.Show} and self.overlays:
            QTimer.singleShot(0, self._refresh_resized_preview)
        return False

    def _refresh_resized_preview(self):
        from shiboken6 import isValid
        if not isValid(self.gui) or not isValid(self.gui.video_view):
            return
        control = getattr(self.gui, "subtitle_single_line_cb", None)
        if control is not None and not isValid(control):
            return
        self.fallback_preview(self.gui.get_active_segments())

    def hide_overlays(self):
        for item in self.overlays:
            try:
                item.hide()
            except RuntimeError:
                pass

    def fallback_preview(self, primary, position_ms=None):
        view = self.gui.video_view
        if self.cfg["mode"] != "bilingual" or not getattr(self.gui, "_subtitle_track_preview_visible", True):
            self.hide_overlays()
            return
        if not self.overlays:
            for _ in range(2):
                if callable(getattr(view, "scene", None)):
                    from widgets.subtitle_overlay import SubtitleOverlayItem
                    item = SubtitleOverlayItem()
                    item.setAcceptedMouseButtons(Qt.NoButton)
                    view.scene().addItem(item)
                else:
                    from widgets.mpv_video_view import _SubtitleOverlayWidget
                    item = _SubtitleOverlayWidget()
                    item.setParent(view, Qt.Widget)
                    item.set_editable(False)
                self.overlays.append(item)
        position_ms = self.gui.media_player.position() if position_ms is None else position_ms
        seconds = (position_ms or 0) / 1000
        width, height = self.gui._subtitle_render_dimensions()
        if hasattr(view, "set_subtitle_render_dimensions"):
            view.set_subtitle_render_dimensions(width, height)
        self.capture_style()
        for role, item, rows in zip(("primary", "secondary"), self.overlays, (primary, self.timeline_rows(copy_rows=False))):
            active = self._fallback_active_rows(role, rows, seconds)
            style = self._fallback_style(role)
            lines = [row["text"] for row in active]
            appearance = (style, height, view.get_preview_canvas_rect().height())
            if getattr(item, "_fallback_appearance", None) != appearance:
                self.configure_preview_item(item, style)
                item._fallback_appearance = appearance
            item.set_text("\n".join(lines))
            from ui.utils.subtitle_effects import preview_effects
            if active:
                effects = preview_effects(active[-1], style, position_ms or 0)
                item.set_effects(**effects)
            main_item = view.subtitle_item
            try:
                view.subtitle_item = item
                view.reposition_subtitle()
            finally:
                view.subtitle_item = main_item
            item.setVisible(bool(lines) and view.isVisible() and self.dragging_role != role)
            if hasattr(item, "raise_"):
                item.raise_()
            else:
                item.setZValue(20)
        self.sync_drag_target(primary)

    def _fallback_active_rows(self, role, rows, seconds):
        cache = self.__dict__.setdefault("_fallback_row_indices", {})
        entry = cache.get(role)
        if entry is None or entry[0] is not rows or len(rows) != len(entry[1]):
            intervals = sorted((row["start"], row["end"], index) for index, row in enumerate(rows))
            starts, max_ends, running_end = [], [], float("-inf")
            for start, end, _ in intervals:
                starts.append(start)
                running_end = max(running_end, end)
                max_ends.append(running_end)
            entry = cache[role] = (rows, intervals, starts, max_ends)
        _, intervals, starts, max_ends = entry
        first, last = bisect_right(max_ends, seconds), bisect_right(starts, seconds)
        return [rows[index] for index in sorted(index for _, end, index in intervals[first:last] if end > seconds)]

    def _fallback_style(self, role):
        cache = self.__dict__.setdefault("_fallback_styles", {})
        key = (self.cfg["styles"].get(role), getattr(self.gui, "subtitle_export_font_scale", 1.0))
        entry = cache.get(role)
        if entry is None or entry[0] != key:
            # Cue words/highlights are applied from the active row, not
            # generated for the entire transcript on every playback tick.
            entry = cache[role] = (copy.deepcopy(key), self.export_style([], role))
        return entry[1]

    def configure_preview_item(self, item, style):
        view = self.gui.video_view
        _, height = self.gui._subtitle_render_dimensions()
        scale = max(1, view.get_preview_canvas_rect().height()) / max(1, height) * .85
        def color(value):
            if str(value).startswith("&H"):
                value = "#" + value[-2:] + value[-4:-2] + value[-6:-4]
            return QColor(value)
        bg = color(style.get("background_color", "#000000"))
        bg.setAlphaF(max(0., min(1., float(style.get("background_alpha", .5)))))
        item.set_style(font_name=style.get("font_name", "Arial"), font_size=max(1, round(style.get("font_size", 60) * scale)),
            font_color=color(style.get("font_color", "#FFFFFF")), outline_color=color(style.get("outline_color", "#000000")),
            outline_width=float(style.get("outline_width", 2)) * scale, background_box=style.get("background_box", False),
            background_color=bg, single_line=style.get("single_line", False), bold=style.get("bold", False))
        item.set_alignment({1: "Bottom Left", 2: "Bottom Center", 3: "Bottom Right", 5: "Center", 8: "Top Center"}.get(style.get("alignment", 2), "Bottom Center"))
        item.set_positioning(custom_position_enabled=style.get("custom_position_enabled", False),
            custom_x_percent=style.get("custom_position_x", 50), custom_y_percent=style.get("custom_position_y", 86),
            x_offset=style.get("x_offset", 0), bottom_offset=style.get("margin_v", 30))

    def drag_target(self):
        gui = self.gui
        if (self.cfg["mode"] != "bilingual" or gui._preview_is_playing()
                or not getattr(gui, "_subtitle_track_preview_visible", True)
                or getattr(gui, "_preview_video_has_burned_subtitles", False)
                or getattr(gui, "_project_loading_in_progress", False)):
            return None
        selected = str(getattr(gui, "_preview_edit_layer_id", "") or "")
        for track in getattr(getattr(gui.timeline, "_timeline", None), "tracks", []):
            for layer in track.layers:
                if layer.id != selected or track.locked or layer.locked:
                    continue
                if layer.metadata.get("secondary_subtitle"):
                    role, rows, index = "secondary", self.timeline_rows(copy_rows=False), int(layer.metadata["index"])
                elif str(getattr(layer.type, "value", layer.type)) in {"subtitle", "dub_subtitle"}:
                    role, rows = "primary", gui.get_active_segments()
                    index = gui.timeline.segment_index_for_layer_id(layer.id)
                else:
                    return None
                if 0 <= index < len(rows):
                    return role, rows, index
        return None

    def sync_drag_target(self, primary=None):
        item = self.gui.video_view.subtitle_item
        target = self.drag_target()
        if not target:
            item.set_editable(False)
            item.hide()
            self.dragging_role = None
            return
        width, height = self.gui._subtitle_render_dimensions()
        if hasattr(self.gui.video_view, "set_subtitle_render_dimensions"):
            self.gui.video_view.set_subtitle_render_dimensions(width, height)
        if getattr(item, "_drag_active", False):
            return
        role, rows, index = target
        style = self.export_style(rows, role)
        self.configure_preview_item(item, style)
        item.set_text(rows[index]["text"])
        if hasattr(item, "set_effects"):
            highlights = style.get("manual_highlights", [])
            value = str(style.get("highlight_color", "#FFD400"))
            if value.startswith("&H"):
                value = "#" + value[-2:] + value[-4:-2] + value[-6:-4]
            item.set_effects(highlight_color=value, highlight_phrases=highlights[index] if index < len(highlights) else [],
                             animation_style="Static", animation_progress=1.)
        item.set_text_rendering(self.dragging_role is not None)
        item.set_editable(True)
        if hasattr(item, "setZValue"):
            item.setZValue(30)
        item.show()
        self.gui.video_view.reposition_subtitle()

    def begin_drag(self):
        target = self.drag_target()
        if target is None:
            return
        self.dragging_role = target[0]
        self.gui.video_view.subtitle_item.set_text_rendering(True)
        self.gui.sync_live_subtitle_preview()

    def finish_drag(self, x, y):
        target = self.drag_target()
        if target is None:
            self.dragging_role = None
            return
        role = self.dragging_role or target[0]
        self.capture_style()
        self.cfg["styles"][role].setdefault("position", {}).update(position_mode="custom", custom_position_enabled=True,
            custom_position_x=max(0, min(100, int(x))), custom_position_y=max(0, min(100, int(y))))
        self.dragging_role = None
        if self.role == role:
            self.apply_controls(self.cfg["styles"][role])
        self.gui.settings.setValue("subtitle_style_default_" + role, json.dumps(self.cfg["styles"][role]))
        self.persist()
        self._committed_styles = dict(getattr(self, "_committed_styles", {}), **{role: copy.deepcopy(self.cfg["styles"][role])})
        self.gui.sync_live_subtitle_preview()

    def apply_controls(self, state):
        self.switching = True
        gui = self.gui
        blockers = [QSignalBlocker(widget) for name, widget in vars(gui).items()
                    if name.startswith("subtitle_") and isinstance(widget, QObject)]
        previous = getattr(gui, "_subtitle_preset_apply_in_progress", False)
        gui._subtitle_preset_apply_in_progress = True
        try:
            preset = str(state.get("preset", "custom"))
            radio = getattr(gui, "subtitle_preset_" + preset + "_radio", None)
            if radio is not None:
                radio.setChecked(True)
            gui._apply_subtitle_style_controls_state(copy.deepcopy(state))
            gui._subtitle_custom_style_state = copy.deepcopy(state)
            gui.sync_subtitle_font_scale_control()
        finally:
            blockers.clear()
            gui._subtitle_preset_apply_in_progress = previous
            self.switching = False

    def capture_style(self):
        if not self.switching:
            self.cfg["styles"][self.role] = copy.deepcopy(self.gui._current_subtitle_style_controls_state())

    def style_changed(self):
        if self.switching or getattr(self.gui, "_project_loading_in_progress", False):
            return
        self.timer.start(300)

    def commit_style(self):
        self.capture_style()
        previous = getattr(self, "_committed_styles", {})
        if previous.get(self.role) == self.cfg["styles"][self.role]:
            return
        self.gui.settings.setValue("subtitle_style_default_" + self.role, json.dumps(self.cfg["styles"][self.role]))
        self.persist()
        self._committed_styles = dict(previous, **{self.role: copy.deepcopy(self.cfg["styles"][self.role])})

    def primary_controls(self):
        self.capture_style()
        return copy.deepcopy(self.cfg["styles"].get("primary", {}))

    def export_style(self, segments=None, role="primary"):
        self.capture_style()
        current = copy.deepcopy(self.gui._current_subtitle_style_controls_state())
        desired = self.cfg["styles"].get(role)
        needs_switch = desired and desired != current
        if needs_switch:
            self.apply_controls(desired)
        try:
            result = self.gui._get_primary_subtitle_export_style(segments)
        finally:
            if needs_switch:
                self.apply_controls(current)
        return result

    def export_settings(self):
        self.capture_style()
        result = copy.deepcopy(self.cfg)
        result["secondary_segments"] = self.secondary_rows()
        result["secondary_render_style"] = self.export_style(self.secondary_rows(), "secondary")
        return result

    def validate_export(self):
        if self.cfg["mode"] != "bilingual":
            return
        if self.busy:
            raise ValueError(t("Secondary subtitles are still being created."))
        build_secondary(self.export_settings(), self.original_rows(), engine=None)

    def persist(self):
        if self.switching or getattr(self.gui, "_project_loading_in_progress", False):
            return
        state = getattr(self.gui, "current_project_state", None)
        if state is not None:
            from app.subtitle_presentation import save_secondary
            snapshot = copy.deepcopy(self.cfg)
            snapshot["secondary_segments"] = self.secondary_rows()
            save_secondary(state, self.gui.project_service, snapshot)

    def restore(self, state):
        self.initialize()
        self.preview_key = None
        self.timer.stop()
        self.selected_secondary_index = -1
        self.dragging_role = None
        if hasattr(self, "inspector"):
            self.inspector.refresh(-1)
            if self.gui.inspector_stack.currentWidget() is self.inspector_scroll:
                self.gui._show_default_inspector()
        defaults = copy.deepcopy(getattr(self, "default_styles", self.cfg["styles"]))
        for role in ("primary", "secondary"):
            try:
                cached = json.loads(self.gui.settings.value("subtitle_style_default_" + role, "{}"))
                if isinstance(cached, dict) and cached:
                    defaults[role] = cached
            except (TypeError, ValueError):
                pass
        self.cfg = config(state.settings.get(KEY))
        self._timeline_cache_key = None
        self._fallback_row_indices = {}
        self._fallback_styles = {}
        for role, value in defaults.items():
            self.cfg["styles"].setdefault(role, value)
        self.cfg["styles"]["primary"] = copy.deepcopy(self.cfg["styles"].get("primary") or state.settings.get("subtitle_style_controls") or defaults.get("primary", {}))
        if KEY not in state.settings and state.settings.get("subtitle_style_controls"):
            self.cfg["styles"]["primary"] = copy.deepcopy(state.settings["subtitle_style_controls"])
        self.role = "primary"
        self.apply_controls(self.cfg["styles"]["primary"])
        self._committed_styles = copy.deepcopy(self.cfg["styles"])
        self.refresh_widgets()

    def refresh_widgets(self):
        self.switching = True
        for widget, value in ((self.mode, self.cfg["mode"]), (self.source, self.cfg["source"]),
                              (self.language, self.cfg["language"]), (self.style_role, self.role)):
            blocker = QSignalBlocker(widget)
            widget.setCurrentIndex(max(0, widget.findData(value)))
            del blocker
        bilingual = self.cfg["mode"] == "bilingual"
        self.panel.setVisible(bilingual)
        self.style_role.setVisible(bilingual)
        card = getattr(self.gui, "subtitle_style_card", None)
        if card is not None:
            card.setEnabled(self.cfg["mode"] != "none")
        self.copy_btn.setVisible(bilingual)
        self.swap_btn.setVisible(bilingual)
        self.language.setVisible(self.cfg["source"] == "translate")
        self.import_btn.setVisible(self.cfg["source"] == "srt")
        self.build_btn.setEnabled(not self.busy)
        rows = self.timeline_rows(copy_rows=False)
        self.status.setText(t("Working...") if self.busy else t("Secondary subtitles ready") if rows else t("Secondary subtitles not created yet"))
        if self.cfg["source"] == "translate" and rows:
            original = self.original_rows()
            if original and self.cfg.get("source_signature") != secondary_signature(original, self.cfg):
                self.status.setText(t("Source changed. Update secondary subtitles."))
        self.switching = False
        if hasattr(self, "inspector"):
            self.inspector.refresh(self.selected_secondary_index)

    def original_rows(self):
        rows = copy.deepcopy(getattr(self.gui, "current_segments", []) or [])
        from app.services.time_warp_service import TimeWarpService
        return TimeWarpService.map_segments_to_media_time(rows, getattr(self.gui, "video_time_warps", []) or [])

    def secondary_rows(self):
        if self.cfg["mode"] == "bilingual" and self.cfg["source"] == "original" and not self.cfg.get("secondary_initialized"):
            if not self.cfg["secondary_segments"]:
                self.cfg["secondary_segments"] = valid_segments(self.original_rows())
                from app.subtitle_timing import capture
                capture(self.cfg["secondary_segments"])
            if self.cfg["secondary_segments"]:
                self.cfg["secondary_initialized"] = True
        return copy.deepcopy(self.cfg.get("secondary_segments") or [])

    def timeline_rows(self, *, copy_rows=True):
        if self.cfg["mode"] == "bilingual" and self.cfg["source"] == "original" and not self.cfg.get("secondary_initialized"):
            self.secondary_rows()
        source = self.cfg.get("secondary_segments") or []
        warps = getattr(self.gui, "video_time_warps", []) or []
        key = (id(source), repr(warps))
        if key != getattr(self, "_timeline_cache_key", None):
            self._timeline_source = source  # Keep the identity alive until the next invalidation.
            self._timeline_cache = map_to_timeline(source, warps) if warps else copy.deepcopy(source)
            self._timeline_cache_key = key
        return copy.deepcopy(self._timeline_cache) if copy_rows else self._timeline_cache

    def changed(self):
        if self.switching:
            return
        self._timeline_cache_key = None
        self.capture_style()
        self.gui._subtitle_ass_request_token = int(getattr(self.gui, "_subtitle_ass_request_token", 0)) + 1
        self.cfg["mode"] = self.mode.currentData()
        if self.cfg["mode"] != "bilingual" and self.role != "primary":
            self.role = "primary"
            self.apply_controls(self.cfg["styles"]["primary"])
        self.persist()
        if self.cfg["mode"] != "bilingual":
            self.hide_overlays()
        self.refresh_widgets()
        self.sync_tracks()
        self.gui.update_subtitle_preview_style()
        self.gui.sync_live_subtitle_preview()

    def source_changed(self):
        if self.switching:
            return
        source_changed = self.cfg["source"] != self.source.currentData()
        self.cfg.update(source=self.source.currentData(), language=self.language.currentData())
        if source_changed:
            self.cfg["secondary_segments"] = []
            self.cfg.pop("secondary_initialized", None)
            self.cfg.pop("source_signature", None)
        self.changed()

    def switch_style(self):
        if self.switching:
            return
        self.timer.stop()
        self.commit_style()
        self.role = self.style_role.currentData()
        self.apply_controls(self.cfg["styles"][self.role])

    def copy_style(self):
        self.capture_style()
        position = copy.deepcopy(self.cfg["styles"]["secondary"].get("position", {}))
        self.cfg["styles"]["secondary"] = copy.deepcopy(self.cfg["styles"]["primary"])
        self.cfg["styles"]["secondary"]["position"] = position
        self.gui.settings.setValue("subtitle_style_default_secondary", json.dumps(self.cfg["styles"]["secondary"]))
        if self.role == "secondary":
            self.apply_controls(self.cfg["styles"]["secondary"])
        self.changed()

    def swap_positions(self):
        self.capture_style()
        a, b = self.cfg["styles"]["primary"], self.cfg["styles"]["secondary"]
        a["position"], b["position"] = copy.deepcopy(b.get("position", {})), copy.deepcopy(a.get("position", {}))
        self.apply_controls(self.cfg["styles"][self.role])
        for role in ("primary", "secondary"):
            self.gui.settings.setValue("subtitle_style_default_" + role, json.dumps(self.cfg["styles"][role]))
        self.changed()

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self.gui, t("Import secondary subtitles"), "", "SubRip (*.srt)")
        if not path:
            return
        self.import_secondary_srt(path)

    def import_secondary_srt(self, path=None):
        if not path or isinstance(path, bool):
            path, _ = QFileDialog.getOpenFileName(self.gui, t("Import secondary subtitles"), "", "SubRip (*.srt)")
        if not path:
            return
        try:
            rows = read_srt(path)
            from app.subtitle_timing import capture
            capture(rows)
            self.cfg.update(source="srt", srt_path=path, secondary_segments=rows, status="ready")
            self.changed()
        except Exception as exc:
            QMessageBox.warning(self.gui, t("Secondary subtitles"), str(exc))

    def build(self):
        if self.busy:
            return
        main_worker = getattr(self.gui, "translation_thread", None)
        if getattr(self.gui, "_pipeline_active", False) or (main_worker is not None and main_worker.isRunning()):
            self.status.setText(t("A translation request is already running."))
            return
        if self.cfg["source"] != "srt" and not self.original_rows():
            # The existing workflow creates STT/OCR and then the secondary track.
            self.gui.run_pipeline_to_stage("transcript")
            return
        if self.cfg["source"] == "srt" and not self.cfg["secondary_segments"]:
            self.import_srt()
            return
        if self.cfg["source"] == "original":
            self.cfg["secondary_segments"] = []
            self.cfg.pop("secondary_initialized", None)
        self.busy = True
        self.refresh_widgets()
        options = {}
        ai = self.gui.is_ai_polish_enabled()
        if self.cfg["source"] == "translate":
            try:
                options = self.review_translation()
            except Exception as exc:
                self.busy = False
                self.refresh_widgets()
                self.gui.log(f"[Subtitles] Secondary settings failed: {exc}")
                QMessageBox.warning(self.gui, t("Secondary subtitles"), str(exc))
                return
            if options is None:
                self.busy = False
                self.refresh_widgets()
                return
            ai = options.get("override_provider") not in ("google", "bing")
        self.job_identity = (getattr(getattr(self.gui, "current_project_state", None), "project_id", ""),
                             self.cfg["source"], self.cfg["language"], self.cfg["secondary_preset_id"], self.cfg["secondary_prompt"])
        self.worker = _BuildWorker(copy.deepcopy(self.cfg), self.original_rows(), self.gui.get_source_language_code(),
            ai, self.gui.get_ai_style_instruction(), self, translation_options=options)
        self._build_canceled = False
        self.gui._secondary_subtitle_worker = self.worker
        self.worker.completed.connect(self.built)
        if self.cfg["source"] == "translate":
            from widgets.translation_progress_dialog import TranslationProgressDialog
            self.progress_dialog = TranslationProgressDialog(self.gui, provider=options.get("override_provider", ""),
                is_retranslation=bool(self.cfg["secondary_segments"]), secondary=True)
            self.progress_dialog.canceled.connect(self.cancel_build)
            self.progress_dialog.set_progress(0, len(self.original_rows()))
            self.worker.progress.connect(self.on_build_progress)
            self.worker.status_changed.connect(self.on_build_status)
            self.progress_dialog.show()
        self.gui.log(f"[Subtitles] Secondary START source={self.cfg['source']} language={self.cfg['language']} cues={len(self.original_rows())} provider={options.get('override_provider', 'default')}")
        self.worker.start()

    def review_translation(self):
        from ui.controllers.subtitle_controller import TranslationPromptDialog
        dialog = TranslationPromptDialog(self.gui, src_lang=self.gui.get_source_language_code(),
            target_lang=self.cfg["language"], segment_count=len(self.original_rows()),
            preset_id=self.cfg["secondary_preset_id"], custom_prompt=self.cfg["secondary_prompt"], persist_preset=False)
        if dialog.exec() != QDialog.Accepted:
            return None
        provider = dialog.selected_provider
        guidance = ""
        if provider not in ("google", "bing") and dialog.selected_auto_context and dialog.selected_review_context:
            guidance = self.gui.subtitle_controller._extract_and_review_context(
                source_segments=self.original_rows(), src_lang=self.gui.get_source_language_code(),
                target_lang=self.cfg["language"], provider=provider)
            if guidance is None:
                return None
        self.cfg["secondary_preset_id"] = dialog.selected_preset_id
        self.cfg["secondary_prompt"] = dialog.selected_prompt
        self.refresh_widgets()
        self.changed()
        options = dict(override_provider=provider, context_guidance=guidance)
        if dialog.selected_batch_size:
            options["polish_batch_size"] = dialog.selected_batch_size
        return options

    def on_build_progress(self, completed, total):
        if self.progress_dialog is not None:
            self.progress_dialog.set_progress(completed, total)

    def on_build_status(self, provider, message):
        if self.progress_dialog is not None:
            self.progress_dialog.set_status(provider, message)
        if message:
            self.gui.log(f"[Subtitles] Secondary provider={provider} status={message}")

    def cancel_build(self):
        if self.busy and self.worker is not None:
            self._build_canceled = True
            self.worker.cancel()
            self.status.setText(t("Canceling translation request..."))
            self.gui.log("[Subtitles] Secondary cancellation requested; keeping existing subtitles.")

    def close_progress(self):
        if self.progress_dialog is not None:
            self.progress_dialog.dispose()
            self.progress_dialog = None

    def built(self, result, error):
        self.close_progress()
        self.gui._secondary_subtitle_worker = None
        self.busy = False
        current = (getattr(getattr(self.gui, "current_project_state", None), "project_id", ""), self.cfg["source"], self.cfg["language"], self.cfg["secondary_preset_id"], self.cfg["secondary_prompt"])
        if current != self.job_identity:
            self.gui.log("[Subtitles] Secondary result discarded: project or translation settings changed.")
            self.refresh_widgets()
            return
        if error == "canceled" or getattr(self, "_build_canceled", False):
            self.refresh_widgets()
            self.gui.log("[Subtitles] Secondary translation canceled; existing subtitles preserved.")
            return
        if error:
            self.refresh_widgets()
            self.status.setText(error)
            self.gui.log(f"[Subtitles] Secondary failed source={self.cfg['source']} language={self.cfg['language']} error={error}")
            self.build_btn.setEnabled(True)
            QMessageBox.warning(self.gui, t("Secondary subtitles"), error)
            return
        # Keep styles changed while the network request was running.
        result["styles"] = self.cfg["styles"]
        result["mode"] = self.cfg["mode"]
        self.cfg = result
        self.cfg["main_signature"] = text_signature(self.gui.get_active_segments())
        self.changed()

    def sync_tracks(self):
        self._fallback_row_indices = {}
        timeline = getattr(getattr(self.gui, "timeline", None), "_timeline", None)
        if timeline is None:
            return
        labels_changed = False
        for track in timeline.tracks:
            if track.name in {"TS1", "S1", "A2 Dub"}:
                hidden = self.cfg["mode"] == "none"
                labels_changed |= track.metadata.get("subtitle_hidden_on_video") != hidden
                track.metadata["subtitle_hidden_on_video"] = hidden
        if labels_changed:
            self.gui.timeline.layoutChanged.emit()
        from app.layers.sync_bridge import find_or_create_track
        from app.layers.base import LayerType
        from app.layers.subtitle import SubtitleLayer
        existing = next((track for track in timeline.tracks if track.name == "TS2"), None)
        if self.cfg["mode"] != "bilingual":
            if existing and existing.visible:
                existing.visible = False
                self.gui.timeline.redraw_track(existing)
            if hasattr(self, "inspector") and self.gui.inspector_stack.currentWidget() is self.inspector_scroll:
                self.gui._show_default_inspector()
            return
        track = existing or find_or_create_track(timeline, "TS2", LayerType.SUBTITLE, 60)
        was_visible = track.visible
        track.visible = True
        rows = self.timeline_rows(copy_rows=False)
        track.metadata["secondary_subtitle"] = True
        same_geometry = existing is not None and len(track.layers) == len(rows) and all(
            layer.id == f"secondary-{i}" and layer.start == row["start"] and layer.end == row["end"]
            for i, (layer, row) in enumerate(zip(track.layers, rows)))
        if same_geometry:
            text_changed = False
            for layer, row in zip(track.layers, rows):
                text_changed |= layer.text != row["text"] or layer.name != row["text"]
                layer.text = layer.name = row["text"]
            if not was_visible:
                self.gui.timeline.redraw_track(track)
            elif text_changed:
                self.gui.timeline.viewport().update()
        else:
            track.layers = [SubtitleLayer(id=f"secondary-{i}", name=row["text"], start=row["start"], end=row["end"],
                            text=row["text"], metadata={"secondary_subtitle": True, "index": i}) for i, row in enumerate(rows)]
            self.gui.timeline.redraw_track(track)
        if self.cfg["source"] == "translate" and self.cfg.get("main_signature") and self.cfg["main_signature"] != text_signature(self.gui.get_active_segments()):
            self.status.setText(t("Main subtitles changed. Review or update secondary subtitles."))

    def move_layer(self, layer, start, end):
        if not getattr(layer, "metadata", {}).get("secondary_subtitle"):
            return False
        index = int(layer.metadata["index"])
        if 0 <= index < len(self.secondary_rows()) and end > start:
            before = copy.deepcopy(self.secondary_rows())
            self.update_secondary(index, start=start, end=end)
            if before != self.secondary_rows():
                self.gui._push_timeline_undo_entry(dict(type="secondary_subtitle", before=before, after=copy.deepcopy(self.secondary_rows())))
        return True

    def select_layer(self, layer):
        if not layer or not layer.metadata.get("secondary_subtitle"):
            return False
        self.style_role.setCurrentIndex(1)
        index = int(layer.metadata.get("index", 0))
        self.select_secondary(index, select_timeline=False)
        return True

    def select_secondary(self, index, *, select_timeline=True):
        rows = self.timeline_rows(copy_rows=False)
        if not 0 <= index < len(rows):
            return
        self.style_role.setCurrentIndex(1)
        if not hasattr(self, "inspector"):
            from ui.widgets.secondary_subtitle_inspector import SecondarySubtitleInspector
            from PySide6.QtWidgets import QScrollArea, QFrame
            self.inspector = SecondarySubtitleInspector(self)
            self.gui.secondary_subtitle_inspector_card = self.inspector
            self.inspector_scroll = QScrollArea()
            self.inspector_scroll.setObjectName("inspectorCardScroll")
            self.inspector_scroll.setWidgetResizable(True)
            self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.inspector_scroll.setFrameShape(QFrame.NoFrame)
            self.inspector_scroll.setWidget(self.inspector)
            self.gui.inspector_stack.addWidget(self.inspector_scroll)
        self.selected_secondary_index = index
        self.inspector.refresh(index)
        self.gui.inspector_stack.setCurrentWidget(self.inspector_scroll)
        self.gui.set_inspector_collapsed(False)
        if select_timeline:
            self.gui.timeline.select_layer(f"secondary-{index}")

    def replace_timeline_rows(self, rows):
        from app.services.time_warp_service import TimeWarpService
        rows = valid_segments(rows)
        self.cfg["secondary_segments"] = TimeWarpService.map_segments_to_media_time(
            rows, getattr(self.gui, "video_time_warps", []) or [])
        self.cfg["secondary_initialized"] = True
        self.changed()

    def update_secondary(self, index, **changes):
        rows = self.timeline_rows()
        if not 0 <= index < len(rows):
            return
        rows[index].update(changes)
        if "text" in changes:
            text = changes["text"].casefold()
            if "manual_highlights" in rows[index]:
                rows[index]["manual_highlights"] = [
                    phrase for phrase in rows[index]["manual_highlights"] if phrase.casefold() in text
                ]
        self.selected_secondary_index = sorted(range(len(rows)), key=lambda i: (rows[i]["start"], rows[i]["end"])).index(index)
        self.replace_timeline_rows(rows)

    def can_restore_timing(self, index):
        from app.subtitle_timing import baseline
        rows = self.cfg.get("secondary_segments") or []
        if self.gui._preview_is_playing() or not 0 <= index < len(rows):
            return False
        value = baseline(rows[index])
        if value is None or value["media_end"] <= value["media_start"]:
            return False
        if abs(rows[index]["start"] - value["media_start"]) < .001 and abs(rows[index]["end"] - value["media_end"]) < .001:
            return False
        for track in self.gui.timeline._timeline.tracks:
            if track.name == "TS2":
                return not (track.locked or (index < len(track.layers) and track.layers[index].locked))
        return True

    def restore_timing(self, index):
        from app.subtitle_timing import baseline
        if not self.can_restore_timing(index):
            return False
        before = self.secondary_rows()
        value = baseline(before[index])
        target = map_to_timeline([dict(start=value["media_start"], end=value["media_end"])], getattr(self.gui, "video_time_warps", []) or [])[0]
        self.update_secondary(index, start=target["start"], end=target["end"])
        self.gui._push_timeline_undo_entry(dict(type="secondary_subtitle", before=before, after=self.secondary_rows()))
        return True

    def selected_layer(self):
        timeline = getattr(self.gui, "timeline", None)
        selected = getattr(timeline, "_selected_layer_id", "")
        for track in getattr(getattr(timeline, "_timeline", None), "tracks", []):
            for layer in track.layers:
                if layer.id == selected and layer.metadata.get("secondary_subtitle"):
                    return track, layer
        return None, None

    def edit_selected_structure(self, operation, selection=None):
        track, layer = self.selected_layer()
        if layer is None:
            return False
        if track.locked or layer.locked:
            return True
        index = int(layer.metadata["index"])
        rows = self.timeline_rows()
        before = copy.deepcopy(self.secondary_rows())
        if operation == "delete":
            rows.pop(index)
        else:
            row = rows[index]
            times = sorted({float(value) for value in (selection or (self.gui.media_player.position() / 1000.,))
                            if row["start"] + .1 < float(value) < row["end"] - .1})
            if not times:
                QMessageBox.information(self.gui, t("Split Segment"), t("Move the playhead inside the selected block before splitting."))
                return True
            bounds = [row["start"], *times, row["end"]]
            pieces = [dict(row, start=start, end=end) for start, end in zip(bounds, bounds[1:])]
            for piece in pieces:
                piece.pop("_completed_timing", None)
            rows[index:index + 1] = pieces
        self.selected_secondary_index = min(index, len(rows) - 1)
        self.replace_timeline_rows(rows)
        self.gui._push_timeline_undo_entry(dict(type="secondary_subtitle", before=before, after=copy.deepcopy(self.secondary_rows())))
        if rows:
            self.select_secondary(self.selected_secondary_index)
        else:
            self.gui.timeline.select_layer("")
        return True

    def apply_history(self, entry, *, use_after):
        self.cfg["secondary_segments"] = copy.deepcopy(entry["after" if use_after else "before"])
        self.cfg["secondary_initialized"] = True
        self.selected_secondary_index = min(self.selected_secondary_index, len(self.cfg["secondary_segments"]) - 1)
        self.changed()

    def render_preview(self, primary, *, timeline=False):
        from ui.utils.media_utils import _is_warped_preview
        rows = self.timeline_rows(copy_rows=False)
        from app.services.time_warp_service import TimeWarpService
        warps = getattr(self.gui, "video_time_warps", []) or []
        if warps and not timeline and not _is_warped_preview(self.gui):
            rows = TimeWarpService.map_segments_to_media_time(rows, warps)
        settings = self.export_settings()
        if not rows and settings["mode"] == "bilingual":
            # Allow primary preview while the secondary track is being created.
            settings["mode"] = "single"
        # Only the actively dragged row uses Qt; libass keeps the other track.
        target = self.drag_target() if not timeline and self.dragging_role else None
        dragging = self.dragging_role if target and target[0] == self.dragging_role else None
        if dragging == "primary":
            primary = []
        elif dragging == "secondary":
            settings["mode"] = "single"
        width = max(1, int(getattr(self.gui.video_view, "video_source_width", 0) or 1920))
        height = max(1, int(getattr(self.gui.video_view, "video_source_height", 0) or 1080))
        style = self.export_style(primary)
        from app.subtitle_presentation import canvas_to_source
        canvas = self.gui._subtitle_render_dimensions()
        mode, focus = self.gui.get_output_scale_mode_key(), self.gui.get_output_fill_focus()
        style = canvas_to_source(style, (width, height), canvas, mode, focus)
        settings["secondary_render_style"] = canvas_to_source(settings["secondary_render_style"], (width, height), canvas, mode, focus)
        key = signature(primary) + repr(settings) + repr(rows) + repr(style) + str((width, height))
        if getattr(self, "preview_key", None) == key and Path(getattr(self, "preview_ass", "")).is_file():
            return self.preview_ass
        path = Path(self.gui.get_project_temp_dir("preview")) / "presentation" / hashlib.sha256(key.encode()).hexdigest()[:16]
        self.preview_ass = presentation_ass(settings, primary, rows, style, width, height, path)
        self.preview_key = key
        return self.preview_ass
