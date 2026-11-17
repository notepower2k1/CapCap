"""Pause/resume must retain the combined source-effects/layout graph."""
from pathlib import Path
import sys
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import MagicMock

ROOT=Path(__file__).resolve().parents[1]
for directory in (ROOT,ROOT/'app',ROOT/'ui'):
    sys.path.insert(0,str(directory))
from main_window import VideoTranslatorGUI as GUI
from utils.media_backend import MpvMediaPlayerBackend as Backend
from app.video_layout import config


class PreviewResumeTests(unittest.TestCase):
    def backend(self):
        cfg=config(dict(template='frame',background='image',image=str(ROOT/'assets/capcap.png'),corner=24,shadow=12))
        cfg.update(_canvas_width=320,_canvas_height=180,_source_width=320,_source_height=180,_mode='fit',_focus=(.5,.5))
        view=SimpleNamespace(video_source_width=320,video_source_height=180,
                             set_blur_track_visible=MagicMock(),set_mask_track_visible=MagicMock())
        player=SimpleNamespace(_is_closed=False,_blur_region=None,_mask_region=None,
                               _color_filter_state={'video_layout':cfg},_managed_vf_label='test',
                               _last_applied_vf_combined=None,_player=MagicMock(),video_view=view,log=MagicMock())
        for name in ('_apply_all_filters','_apply_blur_filter','_apply_mask_filter','_build_blur_body','_build_mask_body',
                     'set_blur_region','clear_blur_region','set_mask_region','clear_mask_region'):
            setattr(player,name,MethodType(getattr(Backend,name),player))
        return player,view

    def test_unchanged_blur_mask_and_background_survive_pause_resume_without_vf_reload(self):
        player,view=self.backend()
        playing=[True]
        blur=[dict(x=.1,y=.1,width=.3,height=.2,blur_strength=4)]
        masks=[dict(x=.5,y=.3,width=.2,height=.2,color='#FF0000',start=0,end=10)]
        gui=SimpleNamespace(media_player=player,video_view=view,_mask_track_preview_visible=True,
            _blur_effect_enabled=lambda:True,_preview_is_playing=lambda:playing[0],
            _deferred_effect_layer_id_for=lambda _: '')
        GUI.apply_preview_blur_region(gui,regions=blur)
        GUI._apply_mask_to_preview(gui,regions=masks)
        before=player._last_applied_vf_combined
        player._player.reset_mock()
        for state in (False,True,False,True):
            playing[0]=state
            GUI.apply_preview_blur_region(gui,regions=blur)
            GUI._apply_mask_to_preview(gui,regions=masks)
            self.assertEqual(player._last_applied_vf_combined,before)
        player._player.command.assert_not_called()

    def test_hiding_effects_still_removes_them(self):
        player,view=self.backend()
        gui=SimpleNamespace(media_player=player,video_view=view,_mask_track_preview_visible=False,
            _blur_effect_enabled=lambda:False,_preview_is_playing=lambda:False,
            _deferred_effect_layer_id_for=lambda _: '')
        player.set_blur_region([dict(x=.1,y=.1,width=.3,height=.2)])
        player.set_mask_region([dict(x=.3,y=.3,width=.3,height=.2)])
        GUI.apply_preview_blur_region(gui,regions=[dict(x=.1,y=.1,width=.3,height=.2)])
        GUI._apply_mask_to_preview(gui,regions=[dict(x=.3,y=.3,width=.3,height=.2)])
        self.assertIsNone(player._blur_region)
        self.assertIsNone(player._mask_region)
        self.assertIn('layout_bg',player._last_applied_vf_combined)

    def test_only_explicit_paused_edit_target_is_suppressed(self):
        player,view=self.backend()
        blur=[dict(x=.1,y=.1,width=.2,height=.2),dict(x=.5,y=.1,width=.2,height=.2)]
        remaining_mask=dict(x=.5,y=.5,width=.2,height=.2)
        masks=MagicMock(return_value=[remaining_mask])
        model=SimpleNamespace(tracks=[SimpleNamespace(name='B1',layers=[SimpleNamespace(id='editing'),SimpleNamespace(id='kept')])])
        gui=SimpleNamespace(media_player=player,video_view=view,timeline=SimpleNamespace(_timeline=model),
            _mask_track_preview_visible=True,_blur_effect_enabled=lambda:True,_preview_is_playing=lambda:False,
            _current_blur_regions_payload=lambda:blur,_current_mask_regions_payload=masks,
            _deferred_effect_layer_id_for=lambda kind:'editing' if kind=='blur' else 'editing-mask')
        GUI.apply_preview_blur_region(gui)
        GUI._apply_mask_to_preview(gui)
        self.assertEqual(player._blur_region,[blur[1]])
        self.assertEqual(player._mask_region,[remaining_mask])
        masks.assert_called_once_with(include_inactive=False,exclude_layer_id='editing-mask')


if __name__=='__main__': unittest.main()
