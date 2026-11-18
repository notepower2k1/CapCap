"""Save the live visual layout, stage a named recipe, then apply atomically."""
import copy
import math
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QLabel, QMessageBox

from app import layout_presets as recipes
from i18n import t
from widgets.named_presets import NamedPresets


class LayoutPresetsController:
    def __init__(self, owner, layout):
        self.owner, self.gui, self.pending = owner, owner.gui, None
        layout.addWidget(QLabel(t('Saved layouts')))
        self.library = NamedPresets(self.gui, settings=self.gui.settings, key='video_layout_presets_v1',
                                   capture=self.capture, apply=self.select, compact=True)
        self.library.save_button.setText(t('Save current layout'))
        layout.addWidget(self.library)
        note = QLabel(t('Saved layouts use sample logo/background images. Apply replaces visual layers; Undo restores them.'))
        note.setWordWrap(True)
        layout.addWidget(note)

    def source_size(self):
        view = self.gui.video_view
        return (view.video_source_width or 1920, view.video_source_height or 1080)

    def controls(self, *, portable=False):
        ratio = self.gui.get_output_ratio_key()
        if portable and ratio == 'source':
            w, h = (int(v) for v in self.owner.canvas_size())
            divisor = math.gcd(w, h)
            ratio = f'{w // divisor}:{h // divisor}'
        result = dict(ratio=ratio, scale_mode=self.gui.get_output_scale_mode_key(),
                    focus=list(self.gui.get_output_fill_focus()), auto_layers=self.gui.auto_layer_reframe_cb.isChecked(),
                    visibility={name: bool(getattr(self.gui, name, True)) for name in
                                ('_text_track_preview_visible', '_logo_track_preview_visible', '_mask_track_preview_visible')},
                    blur_enabled=self.gui._blur_effect_enabled())
        if not portable:
            result['quality'] = self.gui.get_output_quality_key()
        return result

    def capture(self):
        if not self.gui.video_path_edit.text().strip():
            QMessageBox.warning(self.gui, t('Error'), t('Please choose a video first.'))
            return None
        p = self.gui.subtitle_presentation_controller
        p.capture_style()
        return recipes.capture(self.gui.timeline._timeline, layout=self.owner.current(), controls=self.controls(portable=True),
                               styles=p.cfg['styles'], canvas=self.owner.canvas_size(), source=self.source_size())

    def select(self, value):
        recipes.validate(value)
        cfg = copy.deepcopy(value.get('layout'))
        if cfg:
            if cfg.get('image') == recipes.BACKGROUND_ASSET:
                cfg['image'] = recipes.sample_asset(recipes.BACKGROUND_ASSET)
            _, height = self.target_canvas(value['controls'])
            for key in ('corner', 'shadow'):
                cfg[key] *= height / value['canvas'][1]
        self.owner.fill(cfg)
        # Set after fill so built-in selection signals cannot clear the draft.
        self.pending = copy.deepcopy(value)
        self.show_recipe_fields(True)
        self.owner.diagram.update()
        message = t('Saved layout selected. Click Apply layout to use it.')
        if cfg and cfg['background'] == 'image':
            message += ' ' + t('The saved layout uses a sample background image.')
        self.owner.status.setText(message)

    def clear_draft(self):
        self.pending = None
        self.library.refresh()
        self.show_recipe_fields(False)

    def show_recipe_fields(self, selected):
        # Saved text/images are edited through their normal layer inspectors.
        # Keep the form's video slot controls available for custom recipes.
        for field in (self.owner.background, self.owner.color, self.owner.image_row, self.owner.spins['blur']):
            self.owner.form.setRowVisible(field, not selected)
        if selected:
            for field in self.owner.fields.values():
                self.owner.form.setRowVisible(field, False)

    def snapshot(self):
        p = self.gui.subtitle_presentation_controller
        p.capture_style()
        state = self.gui.current_project_state
        return dict(layout=copy.deepcopy(self.owner.applied), tracks=recipes.visual_tracks(self.gui.timeline._timeline),
                    overlay_order=self.gui._overlay_stack_order(),
                    styles=copy.deepcopy(p.cfg['styles']), controls=self.controls(),
                    legacy_effects={key: copy.deepcopy(state.settings.get(key)) if state else None
                                    for key in ('mask_state', 'blur_state')},
                    original_styles=copy.deepcopy(state.settings.get('video_layout_original_styles')) if state else None,
                    project_controls=copy.deepcopy(state.settings.get('package_output_controls')) if state else None)

    def set_controls(self, controls):
        from controllers.resource_export_controller import restore_package_output_controls
        # That helper blocks control signals, avoiding automatic reframe/cache
        # mutations while a complete authored layout is being restored.
        restore_package_output_controls(self.gui, SimpleNamespace(settings={'package_output_controls': controls}))
        for name, visible in controls.get('visibility', {}).items():
            if name in ('_text_track_preview_visible', '_logo_track_preview_visible', '_mask_track_preview_visible'):
                setattr(self.gui, name, bool(visible))
        if hasattr(self.gui.video_view, 'set_logo_track_visible'):
            self.gui.video_view.set_logo_track_visible(bool(getattr(self.gui, '_logo_track_preview_visible', True)))
        if hasattr(self.gui.video_view, 'set_mask_track_visible'):
            self.gui.video_view.set_mask_track_visible(bool(getattr(self.gui, '_mask_track_preview_visible', True)))
        if hasattr(self.gui.video_view, 'set_blur_track_visible'):
            self.gui.video_view.set_blur_track_visible(bool(controls.get('blur_enabled', False)))
        button = self.gui.blur_area_btn
        previous = button.blockSignals(True)
        button.setChecked(bool(controls.get('blur_enabled', False)))
        button.blockSignals(previous)
        self.gui._sync_preview_output_canvas_dimensions()

    def target_canvas(self, controls):
        from app.output_geometry import resolve_output_dimensions
        canvas = resolve_output_dimensions(*self.source_size(), self.gui.get_output_quality_key(), controls['ratio'])
        return tuple(value or fallback for value, fallback in zip(canvas, self.source_size()))

    def apply(self):
        if self.gui._preview_is_playing():
            return
        if not self.gui.video_path_edit.text().strip():
            QMessageBox.warning(self.gui, t('Error'), t('Please choose a video first.'))
            return
        model = self.gui.timeline._timeline
        if any(tr.locked or l.locked for tr in model.tracks for l in tr.layers if l.type.value in recipes.VISUAL_TYPES):
            QMessageBox.warning(self.gui, t('Error'), t('Unlock visual layers before applying a saved layout.'))
            return
        before = self.snapshot()
        controls = copy.deepcopy(self.pending['controls'])
        # Resolve target dimensions without changing the live editor.
        canvas = self.target_canvas(controls)
        cfg = self.owner.draft()
        if cfg['background'] == 'image':
            cfg['image'] = recipes.sample_asset(recipes.BACKGROUND_ASSET)
        try:
            tracks, styles = recipes.materialize(self.pending, layout=cfg, source=self.source_size(),
                                                 canvas=canvas, controls=controls, duration=model.duration or 1.)
            for path in (recipes.sample_asset(recipes.LOGO_ASSET), recipes.sample_asset(recipes.BACKGROUND_ASSET)):
                if not Path(path).is_file():
                    raise ValueError(t('A sample layout image is missing.'))
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
            QMessageBox.warning(self.gui, t('Error'), str(exc))
            return
        after = dict(layout=cfg, tracks=tracks, styles=styles, controls=controls,
                     overlay_order=list(self.pending.get('overlay_order', ())),
                     # The new timeline is authoritative; old fallback regions
                     # must not resurrect effects removed by this recipe.
                     legacy_effects={'mask_state': dict(enabled=controls.get('visibility', {}).get('_mask_track_preview_visible', True), regions=[]),
                                     'blur_state': dict(enabled=controls.get('blur_enabled', False), regions=[])},
                     original_styles=before['original_styles'] if before['layout'] else before['styles'],
                     project_controls={**(before['project_controls'] or {}), **controls,
                                       'quality': self.gui.get_output_quality_key()})
        try:
            self.restore_snapshot(after)
        except Exception:
            self.restore_snapshot(before)
            raise
        self.gui._push_timeline_undo_entry(dict(type='layout_preset', before=before, after=self.snapshot()))
        self.owner.buttons()
        self.gui.log(f"[Layout] Applied saved layout tracks={len(tracks)} canvas={canvas} duration={model.duration:.3f}s")

    def restore_snapshot(self, snap):
        p = self.gui.subtitle_presentation_controller
        p.timer.stop()
        self.owner.pending_before = None
        self.owner.applied = copy.deepcopy(snap['layout'])
        recipes.replace_visual_tracks(self.gui.timeline._timeline, snap['tracks'])
        self.gui.timeline._timeline.overlay_order = list(snap.get('overlay_order', ()))
        self.set_controls(snap['controls'])
        p.cfg['styles'] = copy.deepcopy(snap['styles'])
        p.apply_controls(p.cfg['styles'][p.role])
        p._committed_styles = copy.deepcopy(p.cfg['styles'])
        view = self.gui.video_view
        # No L1 row in the new recipe means the old logo must disappear too.
        if hasattr(view, 'clear_logo'):
            view.clear_logo()
        if hasattr(view, 'clear_mask_region'):
            view.clear_mask_region()
        if hasattr(view, 'clear_blur_region'):
            # This is a preview reset, not an edit to the restored B1 layers.
            # Quality mode otherwise rebuilds them through blurRegionChanged.
            with QSignalBlocker(view):
                view.clear_blur_region()
        self.gui._preview_edit_layer_id = ''
        self.gui._deferred_effect_edit_layer_id = ''
        view._authored_canvas_dimensions = self.owner.canvas_size()
        view._authored_canvas_project = getattr(self.gui.current_project_state, 'project_id', '')
        timeline = self.gui.timeline
        timeline._track_heights = {track.id: track.height for track in timeline._timeline.tracks}
        timeline._segment_indices.clear()
        timeline._overlap_layout_cache.clear()
        timeline._overlap_row_assignments.clear()
        state = self.gui.ensure_current_project()
        if state:
            for name, value in (('video_layout_original_styles', snap['original_styles']),
                                ('package_output_controls', snap['project_controls']),
                                *snap['legacy_effects'].items()):
                if value is None:
                    state.settings.pop(name, None)
                else:
                    state.set_setting(name, copy.deepcopy(value))
            # finish() resolves the project again from disk.
            self.gui.project_service.save_project(state)
        self.owner.fill(self.owner.applied)
        self.owner.finish()

    def apply_history(self, entry, use_after):
        self.clear_draft()
        self.restore_snapshot(entry['after' if use_after else 'before'])
