"""Draftable templates built from normal Text and Background timeline layers."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPixmap, QIcon, QImageReader
from PySide6.QtWidgets import (QWidget, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QComboBox, QLineEdit, QSpinBox, QPushButton, QFileDialog, QColorDialog, QMessageBox)
from app.video_layout import KEY, config, geometry, canvas_point
from i18n import t


class LayoutDiagram(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setMinimumHeight(180)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cfg = self.controller.draft()
        ratio = self.controller.canvas_size()
        scale = min((self.width()-16) / ratio[0], 200 / ratio[1])
        w, h = ratio[0]*scale, ratio[1]*scale
        p.translate((self.width()-w)/2, 8)
        p.fillRect(0, 0, int(w), int(h), QColor(cfg['color']))
        if cfg['template'] == 'free':
            p.fillRect(0, 0, int(w), int(h), QColor('#233b52'))
        else:
            slot, _ = geometry(cfg, w, h, 16, 9)
            p.setBrush(QColor('#355875')); p.setPen(Qt.NoPen)
            p.drawRoundedRect(*[int(v) for v in slot], int(cfg['corner']*scale), int(cfg['corner']*scale))
        p.setPen(QColor('#eaf5ff'))
        p.drawText(0, int(h*.4), int(w), int(h*.2), Qt.AlignCenter, t('Video'))
        if cfg['template'] in ('hook', 'parts'):
            p.drawText(0, 0, int(w), int(h*.2), Qt.AlignCenter, cfg['hook'] or cfg['title'] or t('Title'))
            p.drawText(0, int(h*.8), int(w), int(h*.18), Qt.AlignCenter, cfg['part'] or t('Subtitles'))


class VideoLayoutController:
    def __init__(self, gui, layout):
        self.gui, self.applied = gui, None
        self.pending_before = None
        self._applying = False
        self.restoring = False
        self.templates = QListWidget()
        self.templates.setViewMode(QListWidget.IconMode)
        self.templates.setIconSize(QSize(80, 96))
        self.templates.setMaximumHeight(160)
        for key, label in (('free', 'Free layout'), ('hook', 'Hook + Subtitles'), ('parts', 'Title + Part'), ('frame', 'Cinematic frame')):
            pix = QPixmap(80, 96); pix.fill(QColor('#101826'))
            p = QPainter(pix); p.fillRect(6, 28 if key != 'free' else 6, 68, 40 if key != 'free' else 84, QColor('#355875'))
            p.setPen(QColor('#5eead4')); p.drawText(pix.rect(), Qt.AlignCenter, '—\n▶\n—' if key != 'free' else '▶'); p.end()
            item = QListWidgetItem(QIcon(pix), t(label)); item.setData(Qt.UserRole, key); self.templates.addItem(item)
        layout.addWidget(self.templates)
        self.diagram = LayoutDiagram(self); layout.addWidget(self.diagram)
        self.fields = {}
        form = self.form = QFormLayout()
        for key, label in (('hook', 'Hook'), ('title', 'Title'), ('part', 'Part number'), ('description', 'Part description')):
            field = QLineEdit(); self.fields[key] = field; form.addRow(t(label), field)
            field.textChanged.connect(self.diagram.update)
        self.background = QComboBox()
        for key, label in (('color', 'Solid color'), ('image', 'Background image'), ('blur', 'Canvas Blur')):
            self.background.addItem(t(label), key)
        form.addRow(t('Background'), self.background)
        self.color = QPushButton('#101826'); form.addRow(t('Color'), self.color)
        self.image = QLineEdit(); self.image.setReadOnly(True)
        image_row = QHBoxLayout(); image_row.addWidget(self.image)
        choose = QPushButton(t('Choose image')); image_row.addWidget(choose); form.addRow(image_row)
        self.spins = {}
        for key, label, low, high in (('blur', 'Blur strength', 1, 60), ('inset', 'Canvas inset (%)', 0, 30),
                ('video_height', 'Video area height (%)', 20, 100), ('video_x', 'Horizontal position (%)', 0, 100),
                ('video_y', 'Vertical position (%)', 0, 100), ('corner', 'Corner radius (px)', 0, 100), ('shadow', 'Shadow (px)', 0, 40)):
            spin = QSpinBox(); spin.setRange(low, high); self.spins[key] = spin
            form.addRow(t(label), spin); spin.valueChanged.connect(self.diagram.update)
        layout.addLayout(form)
        hint = QLabel(t('Choose and customize a draft, then Apply. Existing user layers are preserved.'))
        hint.setWordWrap(True); layout.addWidget(hint)
        actions = QHBoxLayout()
        for name, label, callback in (('apply_btn', 'Apply layout', self.apply), ('reset_btn', 'Reset layout', self.reset),
                                      ('undo_btn', 'Undo', self.undo), ('redo_btn', 'Redo', self.redo)):
            button = QPushButton(t(label)); setattr(self, name, button); actions.addWidget(button); button.clicked.connect(callback)
        layout.addLayout(actions)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.templates.currentRowChanged.connect(self.template_changed)
        self.background.currentIndexChanged.connect(self.diagram.update)
        self.color.clicked.connect(self.choose_color); choose.clicked.connect(self.choose_image)
        self.fill(config())

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
        if not self.restoring:
            key = self.templates.currentItem().data(Qt.UserRole)
            self.form.setRowVisible(self.fields['hook'], key == 'hook')
            for field in ('title', 'part', 'description'):
                self.form.setRowVisible(self.fields[field], key == 'parts')
            if key == 'frame':
                self.spins['corner'].setValue(24)
                self.spins['shadow'].setValue(12)
                self.spins['video_height'].setValue(80)
            elif key in ('hook', 'parts'):
                self.spins['video_height'].setValue(58)
        self.diagram.update()

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.color.text()), self.gui, t('Background'))
        if color.isValid():
            self.color.setText(color.name()); self.diagram.update()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self.gui, t('Choose image'), '', 'Images (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self.image.setText(path); self.background.setCurrentIndex(self.background.findData('image'))

    def draft(self):
        item = self.templates.currentItem()
        return config(dict(template=item.data(Qt.UserRole) if item else 'free',
            background=self.background.currentData(), color=self.color.text(), image=self.image.text(),
            **{k: w.text() for k, w in self.fields.items()}, **{k: w.value() for k, w in self.spins.items()}))

    def fill(self, value):
        cfg = config(value); self.restoring = True
        self.templates.setCurrentRow(('free', 'hook', 'parts', 'frame').index(cfg['template']))
        self.background.setCurrentIndex(self.background.findData(cfg['background']))
        self.color.setText(cfg['color']); self.image.setText(cfg['image'])
        for key, widget in self.fields.items(): widget.setText(cfg[key])
        for key, widget in self.spins.items(): widget.setValue(round(cfg[key]))
        key = cfg['template']
        self.form.setRowVisible(self.fields['hook'], key == 'hook')
        for field in ('title', 'part', 'description'):
            self.form.setRowVisible(self.fields[field], key == 'parts')
        self.restoring = False; self.diagram.update(); self.buttons()

    def buttons(self):
        for button, name in ((self.undo_btn, '_timeline_timing_undo_stack'), (self.redo_btn, '_timeline_timing_redo_stack')):
            stack = getattr(self.gui, name, [])
            button.setEnabled(bool(stack and stack[-1].get('type') == 'video_layout'))

    def restore(self, state):
        self.applied = config(state.settings[KEY]) if state and state.settings.get(KEY) else None
        for name in ('_timeline_timing_undo_stack', '_timeline_timing_redo_stack'):
            stack = getattr(self.gui, name, [])
            stack[:] = [entry for entry in stack if entry.get('type') not in ('video_layout','background_layer')]
        self.pending_before = None; self.fill(self.applied)
        self.refresh()

    def current(self):
        has_slot = bool(self.applied and self.applied['template'] != 'free')
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
            self._apply()
        finally:
            self._applying = False
            self.apply_btn.setEnabled(True)

    def _apply(self):
        cfg = self.draft()
        if cfg['template'] == 'free': self.reset(); return
        if not self.gui.video_path_edit.text().strip():
            QMessageBox.warning(self.gui, t('Error'), t('Please choose a video first.')); return
        if cfg['background'] == 'image':
            reader = QImageReader(cfg['image'])
            reader.setScaledSize(QSize(64,64))
            if not Path(cfg['image']).is_file() or reader.read().isNull():
                QMessageBox.warning(self.gui, t('Error'), t('Choose a readable background image first.')); return
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
        roles = [('hook', .09, .045)] if cfg['template']=='hook' else [('title', .08, .045), ('part', .82, .033), ('description', .89, .028)] if cfg['template']=='parts' else []
        for role, y, size in roles:
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
        for role, y in (() if cfg['template']=='frame' else (('primary', 94 if cfg['template']=='hook' else 97), ('secondary', 85 if cfg['template']=='hook' else 92))):
            style = presentation.cfg['styles'].setdefault(role, {})
            style.update(preset='custom', size=max(1, round(ch*.035)))
            style['position'] = dict(style.get('position') or {}, position_mode='custom', custom_position_enabled=True,
                                     custom_position_x=50, custom_position_y=y)
        presentation.apply_controls(presentation.cfg['styles'][presentation.role]); presentation.capture_style()
        presentation._committed_styles = copy.deepcopy(presentation.cfg['styles'])
        self.finish()

    def reset(self):
        if self.applied is None: return
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
        self.applied = None; self.fill(config()); self.finish()

    def apply_history(self, entry, use_after):
        from app.layers.timeline import Track
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
        self.fill(self.applied); self.finish()

    def undo(self): self.gui.undo_last_timeline_timing_edit()
    def redo(self): self.gui.redo_last_timeline_timing_edit()

    def finish(self):
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
