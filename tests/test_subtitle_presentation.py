import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch
import os

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(directory))
from app.subtitle_presentation import (config, build_secondary, signature, map_to_timeline,
    presentation_ass, read_srt, secondary_style)


class SubtitlePresentationTests(unittest.TestCase):
    @staticmethod
    def plain(rows):
        return [{key: value for key, value in row.items() if key != "_completed_timing"} for row in rows]
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [{"start": 0., "end": 2., "text": "Hello"}, {"start": 3., "end": 5., "text": "World"}]

    def test_default_is_single_and_hidden_preserves_data(self):
        self.assertEqual(config()["mode"], "single")
        settings = dict(mode="none", secondary_segments=self.rows)
        self.assertEqual(build_secondary(settings, [], engine=MagicMock())["secondary_segments"], self.rows)

    def test_runtime_forwards_secondary_job_options_to_local_orchestrator(self):
        from app.services.engine_runtime import EngineRuntime
        from app.engines.translator_adapter import TranslatorAdapter
        from translation import TranslationOrchestrator
        runtime = EngineRuntime()
        runtime._instances["translator"] = TranslatorAdapter()
        result = MagicMock(success=True, segments=self.rows)
        with patch.object(TranslationOrchestrator, "translate_segments", return_value=result) as translate:
            cfg = build_secondary(dict(mode="bilingual", source="translate", language="en"), self.rows,
                engine=runtime, translation_options=dict(override_provider="openai", polish_batch_size=40,
                    context_guidance="Secondary rules", is_canceled=lambda: False))
        self.assertEqual(len(cfg["secondary_segments"]), 2)
        self.assertEqual(translate.call_args.kwargs["override_provider"], "openai")
        self.assertEqual(translate.call_args.kwargs["polish_batch_size"], 40)
        self.assertEqual(translate.call_args.kwargs["context_guidance"], "Secondary rules")
        self.assertFalse(translate.call_args.kwargs["is_canceled"]())

    def test_remote_secondary_options_are_serializable_and_cancellation_discards_response(self):
        from app.engines.remote_translator_adapter import RemoteTranslatorAdapter
        from app.translation.prompt_context import translation_prompt_scope
        status, batch = MagicMock(), MagicMock()
        response = dict(segments=self.rows, translation_status=[("google-web", "Fallback")])
        with patch("app.engines.remote_translator_adapter.remote_api_post", return_value=response) as post, \
                translation_prompt_scope("secondary-only"):
            adapter = RemoteTranslatorAdapter()
            rows = adapter.translate_segments(self.rows, custom_system_prompt="Secondary prompt",
                override_provider="openai", context_guidance="Rules", polish_batch_size=40,
                is_canceled=lambda: False, status_callback=status, batch_callback=batch)
        payload = post.call_args.args[1]
        import json
        json.dumps(payload)
        self.assertEqual(payload["translation_preset_id"], "secondary-only")
        self.assertEqual(payload["custom_system_prompt"], "Secondary prompt")
        self.assertEqual(payload["override_provider"], "openai")
        self.assertEqual(rows, self.rows)
        status.assert_called_once_with("google-web", "Fallback")
        batch.assert_called_once_with(0, self.rows)
        with patch("app.engines.remote_translator_adapter.remote_api_post", return_value=response), \
                self.assertRaises(InterruptedError):
            canceled = iter((False, True))
            adapter.translate_segments(self.rows, is_canceled=lambda: next(canceled))

    def test_original_copy_is_independent_and_needs_no_ai(self):
        engine = MagicMock()
        result = build_secondary(dict(mode="bilingual", source="original"), self.rows, engine=engine)
        self.assertEqual(self.plain(result["secondary_segments"]), self.rows)
        result["secondary_segments"][0]["text"] = "changed"
        self.assertEqual(self.rows[0]["text"], "Hello")
        engine.translate_segments.assert_not_called()

    def test_original_snapshot_is_not_replaced_and_empty_initialized_track_cannot_regenerate_on_export(self):
        cfg = build_secondary(dict(mode="bilingual", source="original"), self.rows)
        changed = [dict(row, text="New source") for row in self.rows]
        self.assertEqual(self.plain(build_secondary(cfg, changed)["secondary_segments"]), self.rows)
        cfg["secondary_segments"] = []
        with self.assertRaisesRegex(ValueError, "empty"):
            build_secondary(cfg, changed)

    def test_export_uses_secondary_snapshot_instead_of_reading_original_again(self):
        from app.workflows.export_workflow import ExportWorkflow
        engine = MagicMock()
        engine.get_video_dimensions.return_value = (320, 180)
        workflow = ExportWorkflow.__new__(ExportWorkflow)
        workflow.engine_runtime = engine
        main = self.root / "main.srt"
        main.write_text("1\n00:00:00,000 --> 00:00:02,000\nMain\n", encoding="utf-8")
        original = self.root / "original.srt"
        original.write_text("1\n00:00:00,000 --> 00:00:02,000\nUpdated original\n", encoding="utf-8")
        settings = dict(mode="bilingual", source="original", secondary_initialized=True,
                        original_srt=str(original), secondary_segments=[dict(start=0., end=2., text="Independent snapshot")])
        result = workflow._ensure_subtitle_ass("", str(main), {"subtitle_presentation": settings}, "unused.mp4")
        text = Path(result).read_text(encoding="utf-8-sig")
        self.assertIn("Independent snapshot", text)
        self.assertNotIn("Updated original", text)

    def test_import_preserves_independent_cue_count_and_timing(self):
        path = self.root / "second.srt"
        path.write_text("1\n00:00:01,000 --> 00:00:04,000\nOther subtitle\n", encoding="utf-8-sig")
        cfg = build_secondary(dict(mode="bilingual", source="srt", srt_path=str(path)), self.rows)
        self.assertEqual(self.plain(cfg["secondary_segments"]), [{"start": 1., "end": 4., "text": "Other subtitle"}])
        path.unlink()
        self.assertEqual(build_secondary(cfg, self.rows)["secondary_segments"], cfg["secondary_segments"])

    def test_translate_reuses_results_and_detects_changed_source(self):
        engine = MagicMock()
        engine.translate_segments.return_value = [dict(row, start=99, text="Translated " + row["text"]) for row in self.rows]
        cfg = build_secondary(dict(mode="bilingual", source="translate", language="vi"), self.rows, engine=engine)
        self.assertEqual([r["start"] for r in cfg["secondary_segments"]], [0, 3])
        build_secondary(cfg, self.rows, engine=engine)
        engine.translate_segments.assert_called_once()
        changed = copy.deepcopy(self.rows)
        changed[0]["text"] = "New source"
        with self.assertRaises(ValueError):
            build_secondary(cfg, changed)

    def test_missing_or_partial_secondary_stops_export(self):
        with self.assertRaises(ValueError):
            presentation_ass(dict(mode="bilingual"), self.rows, [], {}, 320, 180, self.root)
        engine = MagicMock()
        engine.translate_segments.return_value = self.rows[:1]
        with self.assertRaises(RuntimeError):
            build_secondary(dict(mode="bilingual", source="translate", language="vi"), self.rows, engine=engine)

    def test_none_ass_contains_no_dialogue_and_bilingual_has_independent_styles(self):
        style = dict(font_name="Arial", font_size=36, custom_position_enabled=True, custom_position_x=50, custom_position_y=86)
        none = presentation_ass(dict(mode="none"), self.rows, self.rows, style, 320, 180, self.root / "none")
        self.assertNotIn("Dialogue:", Path(none).read_text(encoding="utf-8-sig"))
        second = [{"start": 1., "end": 4., "text": "Secondary"}]
        both = presentation_ass(dict(mode="bilingual"), self.rows, second, style, 320, 180, self.root / "both")
        ass = Path(both).read_text(encoding="utf-8-sig")
        self.assertIn("Secondary_", ass)
        self.assertIn("Secondary", ass)
        self.assertIn("Hello", ass)
        self.assertEqual(secondary_style(style)["custom_position_y"], 76)

    def test_time_warp_maps_independent_secondary_without_mutating_source(self):
        from app.services.time_warp_service import TimeWarpService
        warps = [{"type": "freeze", "time": 1., "duration": 2.}]
        original = copy.deepcopy(self.rows)
        rows = map_to_timeline(self.rows, warps)
        self.assertEqual(rows[1]["start"], TimeWarpService.media_to_timeline_time(3., warps))
        self.assertEqual(self.rows, original)

    def test_secondary_prompt_is_per_call_and_does_not_inherit_main_prompt_or_style(self):
        from app.translation.prompt_context import active_translation_preset
        engine = MagicMock()
        def translate(rows, **kwargs):
            self.assertEqual(active_translation_preset(), "general_default")
            self.assertEqual(kwargs["custom_system_prompt"], "Translate to {{target_lang}} in a documentary style.")
            self.assertEqual(kwargs["style_instruction"], "")
            return rows
        engine.translate_segments.side_effect = translate
        cfg = dict(mode="bilingual", source="translate", language="en", secondary_preset_id="general_default",
                   secondary_prompt="Translate to {{target_lang}} in a documentary style.")
        with patch.dict(os.environ, {"CAPCAP_TRANSLATION_PRESET_ID": "main_only"}):
            built = build_secondary(cfg, self.rows, engine=engine, translator_style="main style only")
            self.assertEqual(active_translation_preset(), "main_only")
            self.assertEqual(os.environ["CAPCAP_TRANSLATION_PRESET_ID"], "main_only")
            built["secondary_prompt"] = "Different prompt"
            with self.assertRaises(ValueError):
                build_secondary(built, self.rows)

    def test_parallel_batches_receive_secondary_preset_and_restore_main_context(self):
        from app.translation.prompt_context import active_translation_preset, translation_prompt_scope
        from app.translation.orchestrator import TranslationOrchestrator
        orchestrator = TranslationOrchestrator()
        def batch(**kwargs):
            self.assertEqual(active_translation_preset(), "secondary_only")
            return ["translated"], [], "test"
        with patch.dict(os.environ, {"CAPCAP_TRANSLATION_PRESET_ID": "main_only"}), \
             patch.object(orchestrator, "_translate_ai_batch", side_effect=batch):
            with translation_prompt_scope("secondary_only"):
                result = orchestrator._run_ai_batch_requests(polisher=object(), batches=[(["a"], None, 100), (["b"], None, 100)],
                    src_lang="zh", target_lang="vi", style_instruction="", max_workers=2)
                self.assertEqual(result[0], ["translated", "translated"])
            self.assertEqual(active_translation_preset(), "main_only")

    def test_prompt_scope_is_restored_after_translation_failure(self):
        from app.translation.prompt_context import active_translation_preset
        engine = MagicMock()
        engine.translate_segments.side_effect = RuntimeError("provider failed")
        with patch.dict(os.environ, {"CAPCAP_TRANSLATION_PRESET_ID": "main_only"}):
            with self.assertRaises(RuntimeError):
                build_secondary(dict(mode="bilingual", source="translate", language="en"), self.rows, engine=engine)
            self.assertEqual(active_translation_preset(), "main_only")


if __name__ == "__main__":
    unittest.main()
