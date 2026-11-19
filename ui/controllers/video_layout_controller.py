"""Draftable templates built from normal Text and Background timeline layers."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QPixmap, QIcon
from PySide6.QtWidgets import (
    QWidget, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSpinBox, QPushButton, QMessageBox,
)
from app.video_layout import KEY, FRAME_DEFAULTS, config, canvas_point, has_video_frame
from i18n import t
from ui.widgets.layout_preview import LayoutPreview


_TEMPLATES = (
    ('free', 'Free layout'),
    ('hook', 'Hook + Subtitles'),
    ('parts', 'Title + Part'),
    ('frame', 'Cinematic frame'),
)


class VideoLayoutController:
    TEXT_SLOTS = {
        'hook': (('hook', .09, .045),),
        'parts': (('title', .08, .045), ('part', .82, .033), ('description', .89, .028)),
    }
    SUBTITLE_POSITIONS = {'hook': (94, 85), 'parts': (97, 92)}
    FRAME_FIELDS = (
        ('inset', 'Canvas inset (%)', 0, 30),
        ('video_height', 'Video area height (%)', 20, 100),
        ('video_x', 'Horizontal position (%)', 0, 100),
        ('video_y', 'Vertical position (%)', 0, 100),
        ('corner', 'Corner radius (px)', 0, 100),
        ('shadow', 'Shadow (px)', 0, 40),
    )

    def __init__(self, gui, layout):
        self.gui, self.applied = gui, None
        self.pending_before = None
        self._applying = False
        self.templates = QListWidget()
        self.templates.setViewMode(QListWidget.IconMode)
        self.templates.setIconSize(QSize(80, 96))
        self.templates.setMaximumHeight(160)
        for key, label in _TEMPLATES:
            pix = QPixmap(80, 96); pix.fill(QColor('#101826'))
            p = QPainter(pix); p.fillRect(6, 28 if key != 'free' else 6, 68, 40 if key != 'free' else 84, QColor('#355875'))
            p.setPen(QColor('#5eead4')); p.drawText(pix.rect(), Qt.AlignCenter, '—\n▶\n—' if key != 'free' else '▶'); p.end()
            item = QListWidgetItem(QIcon(pix), t(label)); item.setData(Qt.UserRole, key); self.templates.addItem(item)
        layout.addWidget(self.templates)
        from ui.controllers.layout_presets_controller import LayoutPresetsController
        self.saved = LayoutPresetsController(self, layout)
        self.diagram = LayoutPreview(self); layout.addWidget(self.diagram)
        actions = QHBoxLayout()
        for name, label, callback in (('apply_btn', 'Apply layout', self.apply), ('reset_btn', 'Reset layout', self.reset),
                                      ('undo_btn', 'Undo', self.undo), ('redo_btn', 'Redo', self.redo)):
            button = QPushButton(t(label)); setattr(self, name, button); actions.addWidget(button); button.clicked.connect(callback)
        layout.addLayout(actions)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.templates.currentRowChanged.connect(self.template_changed)
        self.sync_selection()

    def canvas_size(self):
        try:
            from app.output_geometry import resolve_output_dimensions
            view = self.gui.video_view
            sw, sh = view.video_source_width or 1920, view.video_source_height or 1080
            w, h = resolve_output_dimensions(sw, sh, self.gui.get_output_quality_key(), self.gui.get_output_ratio_key())
            return (w or sw, h or sh)
        except Exception:
            return 1080, 1920

    def template_changed(self, *_):
        if self.templates.currentItem() is None:
            return
        self.saved.clear_draft()
        self.status.clear()
        self.diagram.refresh()

    def draft(self):
        if self.saved.pending is not None:
            return self.saved.draft_layout()
        item = self.templates.currentItem()
        key = item.data(Qt.UserRole) if item else 'free'
        defaults = {'template': key}
        if key == 'hook':
            defaults['hook'] = t('Hook')
        elif key == 'parts':
            defaults.update(title=t('Title'), part=t('Part 1'), description=t('Part description'))
        elif key == 'frame':
            defaults.update(video_height=80, corner=24, shadow=12)
        return config(defaults)

    def sync_selection(self):
        key = (self.applied or {}).get('template', 'free')
        row = -1 if self.saved.pending is not None else next(
            index for index, (template, _) in enumerate(_TEMPLATES) if template == key
        )
        with QSignalBlocker(self.templates):
            self.templates.setCurrentRow(row)
            if row == -1:
                self.templates.clearSelection()
        self.diagram.refresh()
        self.buttons()
        self.sync_video_inspector()

    def build_video_inspector(self, layout):
        self.frame_editor = QWidget()
        form = QFormLayout(self.frame_editor)
        form.setContentsMargins(0, 0, 0, 0)
        self.frame_spins = {}
        for key, label, low, high in self.FRAME_FIELDS:
            spin = QSpinBox()
            spin.setRange(low, high)
            self.frame_spins[key] = spin
            form.addRow(t(label), spin)
        actions = QHBoxLayout()
        apply = self.frame_apply_button = QPushButton(t('Apply video frame'))
        reset = QPushButton(t('Reset video frame'))
        apply.clicked.connect(self.apply_video_frame)
        reset.clicked.connect(lambda: self.apply_video_frame(reset=True))
        actions.addWidget(apply)
        actions.addWidget(reset)
        form.addRow(actions)
        heading = QLabel(t('Video frame'))
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        layout.addWidget(self.frame_editor)
        self.sync_video_inspector()

    def sync_video_inspector(self):
        if not hasattr(self, 'frame_spins'):
            return
        frame = self.applied if has_video_frame(self.applied) else FRAME_DEFAULTS
        for key, spin in self.frame_spins.items():
            spin.setValue(round(frame[key]))

    def apply_video_frame(self, *, reset=False):
        gui = self.gui
        if gui._preview_is_playing() or not gui.video_path_edit.text().strip():
            return
        selected = gui.timeline._selected_layer_id
        model = gui.timeline._timeline
        target = next(((tr, layer) for tr in model.tracks if tr.type.value == 'video'
                       for layer in tr.layers if layer.id == selected), None)
        if target is None or any(item.locked for item in target):
            return
        frame = FRAME_DEFAULTS if reset else {key: spin.value() for key, spin in self.frame_spins.items()}
        cfg = config(self.applied or FRAME_DEFAULTS)
        cfg.update(frame, custom_frame=cfg['template'] == 'free' and frame != FRAME_DEFAULTS)
        if cfg == self.applied or (self.applied is None and frame == FRAME_DEFAULTS):
            return
        before = copy.deepcopy(self.applied)
        self.set_video_frame(cfg)
        gui._push_timeline_undo_entry(dict(type='video_frame', before=before, after=copy.deepcopy(cfg)))

    def set_video_frame(self, cfg):
        self.applied = copy.deepcopy(cfg)
        state = self.gui.ensure_current_project()
        if state:
            state.set_setting(KEY, copy.deepcopy(cfg))
            self.gui.project_service.save_project(state)
        self.sync_video_inspector()
        self.refresh()

    def buttons(self):
        for button, name in ((self.undo_btn, '_timeline_timing_undo_stack'), (self.redo_btn, '_timeline_timing_redo_stack')):
            stack = getattr(self.gui, name, [])
            button.setEnabled(bool(stack and stack[-1].get('type') in ('video_layout', 'layout_preset')))

    def restore(self, state):
        self.saved.clear_draft()
        self.applied = config(state.settings[KEY]) if state and state.settings.get(KEY) else None
        for name in ('_timeline_timing_undo_stack', '_timeline_timing_redo_stack'):
            stack = getattr(self.gui, name, [])
            stack[:] = [entry for entry in stack if entry.get('type') not in ('video_layout','layout_preset','video_frame','background_layer','logo_source','overlay_order')]
        self.pending_before = None
        self.status.clear()
        self.sync_selection()
        self.refresh()

    def current(self):
        has_slot = has_video_frame(self.applied)
        result = copy.deepcopy(self.applied) if has_slot else config(dict(template='free',inset=0,video_height=100,corner=0,shadow=0))
        result.update(background='color', color='#000000', image='')
        timeline = getattr(getattr(self.gui, 'timeline', None), '_timeline', None)
        found = False
        for track in timeline.tracks if timeline else []:
            for layer in track.layers:
                if getattr(getattr(layer,'type',None),'value','') != 'background' or not track.visible or not layer.visible:
                    continue
                found = True
                image = layer.source
                state = getattr(self.gui, 'current_project_state', None)
                if image and not Path(image).is_absolute() and state:
                    image = str(Path(state.project_root) / image)
                result.update(background=layer.mode, color=layer.color, image=image, blur=layer.blur)
        if not has_slot and not found:
            return None
        cw, ch = self.canvas_size()
        view = getattr(self.gui, 'video_view', None)
        result.update(_canvas_width=cw, _canvas_height=ch,
            _source_width=getattr(view, 'video_source_width', 0) or cw,
            _source_height=getattr(view, 'video_source_height', 0) or ch,
            _mode=self.gui.get_output_scale_mode_key(), _focus=self.gui.get_output_fill_focus())
        return result

    def snapshot(self):
        p = self.gui.subtitle_presentation_controller; p.capture_style()
        tracks = []
        for track in self.gui.timeline._timeline.tracks:
            owned = [layer for layer in track.layers if layer.metadata.get('layout_role')]
            if owned:
                saved = copy.deepcopy(track.to_dict())
                saved['layers'] = [copy.deepcopy(layer.to_dict()) for layer in owned]
                ids = {layer.id for layer in owned}
                saved['clips'] = [copy.deepcopy(clip.to_dict()) for clip in track.clips if clip.layer_id in ids]
                tracks.append(saved)
        styles = {role: {key: copy.deepcopy(style[key]) for key in ('preset', 'size', 'position') if key in style}
                  for role, style in p.cfg['styles'].items()}
        return dict(layout=copy.deepcopy(self.applied), tracks=tracks, styles=styles)

    def remember(self):
        self.pending_before = self.snapshot()

    @staticmethod
    def remove_owned(model):
        for track in model.tracks:
            ids = {layer.id for layer in track.layers if layer.metadata.get('layout_role')}
            track.layers[:] = [layer for layer in track.layers if layer.id not in ids]
            track.clips[:] = [clip for clip in track.clips if clip.layer_id not in ids]
        # Older templates created T1 even when no text was requested. Remove
        # that empty row too, while preserving populated, custom or locked tracks.
        model.tracks[:] = [track for track in model.tracks if track.layers or track.clips or
            (not track.metadata.get('layout_owned') and not
             (track.type.value=='text' and track.name=='T1 Text' and not track.locked))]

    def apply(self):
        if self._applying:
            return
        self._applying = True
        self.apply_btn.setEnabled(False)
        try:
            if self.saved.pending is not None:
                self.saved.apply()
            else:
                self._apply()
        finally:
            self._applying = False
            self.apply_btn.setEnabled(True)

    def _apply(self):
        cfg = self.draft()
        if cfg['template'] == 'free': self.reset(); return
        if not self.gui.video_path_edit.text().strip():
            QMessageBox.warning(self.gui, t('Error'), t('Please choose a video first.')); return
        self.gui.log(f"[Layout] Apply template={cfg['template']} background={cfg['background']} image={cfg['image']!r} canvas={self.canvas_size()}")
        model = self.gui.timeline._timeline
        if any(t.locked or any(l.locked for l in t.layers if l.metadata.get('layout_role')) for t in model.tracks
               if t.metadata.get('layout_owned') or any(l.metadata.get('layout_role') for l in t.layers)
               or (t.name == 'T1 Text' and cfg['template'] in ('hook','parts'))):
            QMessageBox.warning(self.gui, t('Error'), t('Unlock layout layers before applying a template.')); return
        self.remember()
        state = self.gui.ensure_current_project()
        if state and not self.applied:
            state.set_setting('video_layout_original_styles', copy.deepcopy(self.gui.subtitle_presentation_controller.cfg['styles']))
            self.gui.project_service.save_project(state)
        self.applied = cfg
        from app.layers.text import TextLayer
        from app.layers.background import BackgroundLayer
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track
        from app.layers.transform import Transform
        self.remove_owned(model)
        duration = max(float(model.duration), max((r['end'] for r in self.gui.get_active_segments()), default=1.))
        bg = find_or_create_track(model, 'Background', LayerType.BACKGROUND, 55); bg.metadata['layout_owned'] = True
        bg.layers.append(BackgroundLayer(id='layout-background', name=t('Background'), start=0, end=duration,
            mode=cfg['background'], color=cfg['color'], source=cfg['image'] if cfg['background']=='image' else '', blur=cfg['blur'], metadata={'layout_role':'background'}))
        model.tracks.remove(bg); model.tracks.insert(0, bg)
        text_track = None
        cw, ch = self.canvas_size(); sw = self.gui.video_view.video_source_width or cw; sh = self.gui.video_view.video_source_height or ch
        for role, y, size in self.TEXT_SLOTS.get(cfg['template'], ()):
            text = cfg[role].strip()
            if not text: continue
            if text_track is None:
                text_track = next((track for track in model.tracks if track.name=='T1 Text' and track.type==LayerType.TEXT),None)
                if text_track is None:
                    text_track = find_or_create_track(model, 'T1 Text', LayerType.TEXT, 80)
                    text_track.metadata['layout_owned'] = True
            x, y = canvas_point(.5, y, cfg, (sw,sh), (cw,ch), self.gui.get_output_scale_mode_key(), self.gui.get_output_fill_focus())
            text_track.layers.append(TextLayer(id='layout-'+role, name=t(role.title()), text=text, font_size=max(1, round(ch*size)),
                font_color='#FFFFFF', font_bold=True, start=0, end=duration, transform=Transform(x=x,y=y), metadata={'layout_role':role}))
        presentation = self.gui.subtitle_presentation_controller
        presentation.timer.stop()
        presentation.capture_style()
        for role, y in zip(('primary', 'secondary'), self.SUBTITLE_POSITIONS.get(cfg['template'], ())):
            style = presentation.cfg['styles'].setdefault(role, {})
            style.update(preset='custom', size=max(1, round(ch*.035)))
            style['position'] = dict(style.get('position') or {}, position_mode='custom', custom_position_enabled=True,
                                     custom_position_x=50, custom_position_y=y)
        presentation.apply_controls(presentation.cfg['styles'][presentation.role]); presentation.capture_style()
        presentation._committed_styles = copy.deepcopy(presentation.cfg['styles'])
        self.finish()

    def reset(self):
        self.saved.clear_draft()
        if self.applied is None:
            self.sync_selection()
            self.status.clear()
            return
        model = self.gui.timeline._timeline
        if any((track.locked or layer.locked) for track in model.tracks for layer in track.layers if layer.metadata.get('layout_role')):
            QMessageBox.warning(self.gui, t('Error'), t('Unlock layout layers before applying a template.')); return
        self.remember()
        self.remove_owned(model)
        state = getattr(self.gui,'current_project_state',None)
        styles = state.settings.get('video_layout_original_styles') if state else None
        if styles:
            p = self.gui.subtitle_presentation_controller
            p.capture_style()
            for role, original in styles.items():
                for key in ('position', 'preset', 'size'):
                    if key in original:
                        p.cfg['styles'].setdefault(role, {})[key] = copy.deepcopy(original[key])
            p.apply_controls(p.cfg['styles'][p.role])
            p._committed_styles = copy.deepcopy(p.cfg['styles'])
        self.applied = None
        self.sync_selection()
        self.finish()

    def apply_history(self, entry, use_after):
        from app.layers.timeline import Track
        self.saved.clear_draft()
        snap = entry['after' if use_after else 'before']
        self.applied = snap['layout']
        timeline = self.gui.timeline
        tracks = timeline._timeline.tracks
        self.remove_owned(timeline._timeline)
        for saved in snap['tracks']:
            restored = Track.from_dict(saved)
            existing = next((track for track in tracks if track.id == restored.id), None)
            if existing:
                existing.layers.extend(restored.layers)
                existing.clips.extend(restored.clips)
            elif restored.type.value == 'background':
                tracks.insert(0, restored)
            else:
                tracks.append(restored)
        timeline._track_heights = {track.id: track.height for track in timeline._timeline.tracks}
        timeline._selected_layer_id = ''
        timeline._segment_indices.clear()
        timeline._overlap_layout_cache.clear()
        p = self.gui.subtitle_presentation_controller
        p.capture_style()
        for role, style in snap['styles'].items():
            p.cfg['styles'].setdefault(role, {}).update(copy.deepcopy(style))
        p.apply_controls(p.cfg['styles'][p.role])
        p.timer.stop(); p._committed_styles = copy.deepcopy(p.cfg['styles'])
        self.sync_selection()
        self.finish()

    def undo(self): self.gui.undo_last_timeline_timing_edit()
    def redo(self): self.gui.redo_last_timeline_timing_edit()

    def finish(self):
        self.sync_video_inspector()
        state = self.gui.ensure_current_project()
        if state:
            state.set_setting(KEY, copy.deepcopy(self.applied))
            self.gui.project_service.save_project(state)
        self.gui.subtitle_presentation_controller.persist()
        self.gui.timeline.select_layer('')
        self.gui._show_default_inspector()
        self.gui.timeline._redraw()
        if self.pending_before is not None:
            before, self.pending_before = self.pending_before, None
            self.gui._push_timeline_undo_entry(dict(type='video_layout', before=before, after=self.snapshot()))
        self.refresh(); self.gui.persist_current_timeline_project_data(); self.buttons()
        self.status.setText(t('Layout applied. Edit its layers on the timeline.'))

    def refresh(self):
        view = getattr(self.gui,'video_view',None)
        if view is None: return
        view.video_layout = self.current()
        self.gui._timed_layer_preview_signature = None
        player = getattr(self.gui, 'media_player', None)
        # Project metadata is restored before the native player is created.
        # Publish geometry now; render after the existing media-load callback.
        if getattr(self.gui, '_project_loading_in_progress', False):
            return
        if getattr(player, 'backend_name', '') == 'bootstrap' and not getattr(self.gui, 'performance_mode', False):
            return
        self.gui._sync_preview_framing_to_player()
        view._sync_preview_stack()
        if player is not None and hasattr(player, 'set_color_filter_state'):
            live = getattr(self.gui, '_apply_realtime_color_filter_preview', None)
            if getattr(self.gui, '_project_loading_in_progress', False) or not live or not live():
                player.set_color_filter_state(self.gui.get_video_filter_state())
        if getattr(self.gui, '_filter_thumbnail_visible', False):
            self.gui.hide_filter_thumbnail_preview()
        if hasattr(view, '_render_frame'):
            view._render_frame()
        self.gui.refresh_timed_layer_preview()
        self.gui.update_subtitle_preview_style()
        self.gui.sync_live_subtitle_preview()
        view.update()
