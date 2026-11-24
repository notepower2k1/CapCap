import json
import subprocess
import time
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import test_performance_queue as harness
from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from translation.errors import TranslationValidationError
from translation.providers.browser_bridge_provider import BrowserBridgePolisherProvider
from translation.video_recap import generate_video_recap
from translation.video_summary import generate_video_hooks, generate_video_summary, transcript_snapshot
from services.recap_render import RecapClip, recap_subtitles, render_recap_video, suggest_scene_ranges, upgrade_scene_ranges
from services.recap_voice import narration_digest
from runtime_paths import bin_path, subprocess_hidden_kwargs
from workflows.export_workflow import ExportWorkflow


def _reply(ids):
    return json.dumps({
        "summary": "Tóm tắt mẫu",
        "points": [{"text": "Một ý chính", "cues": ids}],
        "people": [{"name": "An", "role": "", "kind": "mentioned", "cues": ids}],
    }, ensure_ascii=False)


class VideoSummaryTests(unittest.TestCase):
    def test_long_transcript_includes_every_cue_and_all_fragments(self):
        segments = [{"start": i + .125, "end": i + 1, "text": "ả" * 1600 + str(i)}
                    for i in range(24)]
        rows, _ = transcript_snapshot(segments)
        received = {}
        calls = []

        def answer(*, user_msg, **kwargs):
            instruction, _, data = user_msg.partition("\n")
            batch = json.loads(data)
            calls.append(instruction)
            if "lời thoại" in instruction:
                for item in batch:
                    received[item["id"]] = received.get(item["id"], "") + item["text"]
                ids = [batch[-1]["id"]]
            else:
                ids = [batch[-1]["points"][-1]["cues"][-1]]
            return _reply(ids + [99999])

        progress = Mock()
        result = generate_video_summary(rows, answer, progress=progress, is_canceled=lambda: False)
        self.assertEqual(received, {row["id"]: row["text"] for row in rows})
        self.assertGreater(calls.count("Tổng hợp tất cả phần sau theo trình tự, giữ ID làm căn cứ."), 1)
        self.assertEqual(result["points"][0]["times_ms"], [23125])
        self.assertEqual(result["people"][0]["times_ms"], [23125])
        steps = [call.args for call in progress.call_args_list]
        self.assertEqual(steps[0], ("transcript", 0, len(calls)))
        self.assertEqual(steps[-1], ("merge", len(calls), len(calls)))
        self.assertEqual({total for _, _, total in steps}, {len(calls)})
        self.assertEqual([done for _, done, _ in steps], sorted(done for _, done, _ in steps))

    def test_retries_only_bad_format_and_maps_citations_to_transcript(self):
        rows, _ = transcript_snapshot([{"start": .42, "end": .8, "text": "An"}])
        provider = Mock(side_effect=["invalid", _reply([1, 12, True, "1"])])
        result = generate_video_summary(rows, provider, progress=Mock(), is_canceled=lambda: False)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(result["points"][0]["times_ms"], [420])
        self.assertEqual(result["people"][0]["kind"], "mentioned")

    def test_browser_summary_uses_its_own_chat_and_closes_it(self):
        server = Mock()
        server.extension_connected = True
        completed = Mock()
        completed.wait.return_value = True
        server.enqueue_task.return_value = ("task", completed)
        server.get_task_result.return_value = {"success": True, "text": _reply([1])}
        browser = BrowserBridgePolisherProvider(bridge_server=server)
        rows, _ = transcript_snapshot([{"start": 0, "end": 1, "text": "An"}])

        browser.run_video_analysis(generate_video_summary, rows, progress=Mock(),
                                   is_canceled=lambda: False, json_reply_key="summary")

        task = server.enqueue_task.call_args.kwargs["payload"]
        self.assertEqual(task["operation"], "summary")
        self.assertEqual(task["json_reply_key"], "summary")
        self.assertTrue(task["isolated"])
        self.assertTrue(task["new_chat"])
        cleanup = server.enqueue_fire_and_forget_task.call_args.args[0]
        self.assertEqual(cleanup["session_id"], task["session_id"])
        self.assertTrue(cleanup["close_owned"])

    def test_hook_request_uses_only_summary_text_and_rejects_duplicate_suggestions(self):
        summary = {"summary": "Giới thiệu", "points": [{"text": "Một phát hiện", "times_ms": [330]}],
                   "people": [{"name": "An"}]}
        provider = Mock(return_value=json.dumps({"hooks": ["Một câu", "MỘT CÂU", "Câu khác"]}))
        with self.assertRaises(TranslationValidationError):
            generate_video_hooks(summary, provider, progress=Mock(), is_canceled=lambda: False)
        provider.assert_called_once()
        self.assertEqual(json.loads(provider.call_args.kwargs["user_msg"]),
                         {"summary": "Giới thiệu", "points": ["Một phát hiện"]})

    def test_recap_uses_nearby_dialogue_and_resolves_only_valid_citations(self):
        rows, _ = transcript_snapshot([
            {"start": 0.125, "end": 1.1, "text": "Điều gì đã xảy ra?"},
            {"start": 2.2, "end": 3.1, "text": "Cửa đã bị khóa."},
            {"start": 50.0, "end": 51.0, "text": "Không liên quan."},
        ])
        summary = {"summary": "Một người không thể vào.", "points": [{"text": "Cửa khóa", "times_ms": [2200]}]}
        provider = Mock(side_effect=[
            json.dumps({"segments": [{"narration": "Sai", "cue_ids": [3, 99]}]}),
            json.dumps({"segments": [{"narration": "Cánh cửa đã bị khóa.", "cue_ids": [2, 3], "time_ms": 99}]}),
        ])
        progress = Mock()
        result = generate_video_recap((summary, rows, 60), provider, progress=progress, is_canceled=lambda: False)
        request = json.loads(provider.call_args_list[0].kwargs["user_msg"])
        self.assertEqual([row["id"] for row in request["evidence"]], [1, 2])
        self.assertEqual(request["target_seconds"], 60)
        self.assertEqual(result["segments"][0]["evidence"],
                         [{"cue_id": 2, "time_ms": 2200, "text": "Cửa đã bị khóa."}])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual([call.args for call in progress.call_args_list], [("recap", 0, 1), ("recap", 1, 1)])

    def test_recap_reads_rendered_json_with_headers_in_one_request(self):
        rows, _ = transcript_snapshot([{"start": 1.2, "end": 2, "text": "A cue"}])
        summary = {"summary": "Summary", "points": [{"text": "Point", "times_ms": [1200]}]}
        payload = json.dumps({"segments": [{"narration": 'Anh nói: "Mở cửa {ngay}".', "cue_ids": [1]}]})
        for text in (
            f"Here is a recap.\nJSON\nCopy code\n{payload}\nEnd of reply.",
            f'Example: {{"other": true}}\n```json\n{payload}\n```\nDone.',
        ):
            with self.subTest(text=text):
                provider = Mock(return_value=text)
                result = generate_video_recap((summary, rows, 60), provider, progress=Mock(), is_canceled=lambda: False)
                self.assertEqual(result["segments"][0]["narration"], 'Anh nói: "Mở cửa {ngay}".')
                self.assertEqual(result["segments"][0]["evidence"][0]["time_ms"], 1200)
                provider.assert_called_once()


class VideoSummaryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.PerformanceQueueTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        harness.PerformanceQueueTests.tearDownClass()

    def setUp(self):
        self.host = harness.PerformanceQueueTests()
        self.host.setUp()

    def tearDown(self):
        self.host.tearDown()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_tab_saves_manual_summary_and_marks_original_edits_stale(self):
        video = self.host.source()
        gui = self.host.editor(video)
        gui.current_segments = [{"start": .42, "end": .8, "text": "Original"}]
        gui.current_translated_segments = [{"start": .42, "end": .8, "text": "Translated"}]
        controller = gui.video_summary_controller
        with patch("ui.controllers.video_summary_controller.TranslationOrchestrator") as factory, \
                patch.object(QMessageBox, "question", side_effect=[QMessageBox.No, QMessageBox.Yes]) as confirm, \
                patch.dict("os.environ", {"OPENAI_PROVIDER": "openai"}):
            engine = factory.return_value
            engine.summarize_video.side_effect = lambda rows, **kwargs: generate_video_summary(
                rows, Mock(return_value=_reply([1])), progress=kwargs["progress"],
                is_canceled=kwargs["is_canceled"],
            )
            gui.workflow_tab_buttons["summary"].click()
            controller.refresh()
            self.assertEqual(gui.left_panel_stack.currentWidget(), controller.page)
            factory.assert_not_called()

            controller.generate_btn.click()
            factory.assert_not_called()
            self.assertIsNone(controller.progress_dialog)
            self.assertEqual(confirm.call_args.args[-1], QMessageBox.No)

            controller.generate_btn.click()
            self.wait_until(lambda: controller.job is None)
            self.assertIsNone(controller.progress_dialog)
            engine.summarize_video.assert_called_once()
            self.assertEqual(engine.summarize_video.call_args.args[0][0]["text"], "Original")
            saved = gui.project_service.load_json_artifact(gui.current_project_state, "video_summary")
            self.assertEqual(saved, controller.result)
            self.assertEqual(controller.result["points"][0]["times_ms"], [420])
            with patch.object(gui, "set_position") as seek:
                controller.result_view.anchorClicked.emit(QUrl("seek:420"))
            seek.assert_called_once_with(420)
            controller.copy_btn.click()
            self.assertIn("Tóm tắt mẫu", QApplication.clipboard().text())

            reopened = self.host.editor(video)
            reopened.current_segments = list(gui.current_segments)
            new_tab = reopened.video_summary_controller
            reopened.workflow_tab_buttons["summary"].click()
            new_tab.refresh()
            self.assertEqual(new_tab.result, controller.result)
            self.assertFalse(new_tab.stale)
            reopened.current_segments = [{"start": .55, "end": .8, "text": "Original edited"}]
            new_tab.refresh()
            self.assertTrue(new_tab.stale)
            self.assertEqual(new_tab.result, controller.result)
            engine.summarize_video.assert_called_once()

    def test_progress_tracks_summary_and_cancel_discards_late_result(self):
        gui = self.host.editor(self.host.source())
        gui.current_segments = [{"start": 0, "end": .8, "text": "Original"}]
        controller = gui.video_summary_controller
        release_reply = Event()

        def blocked_summary(rows, **kwargs):
            kwargs["progress"]("merge", 2, 3)
            release_reply.wait(10)
            return {"summary": "Late result", "points": [], "people": []}

        with patch("ui.controllers.video_summary_controller.TranslationOrchestrator") as factory, \
                patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                patch.dict("os.environ", {"OPENAI_PROVIDER": "openai"}):
            factory.return_value.summarize_video.side_effect = blocked_summary
            gui.workflow_tab_buttons["summary"].click()
            controller.refresh()
            controller.generate_btn.click()
            dialog = controller.progress_dialog
            try:
                self.wait_until(lambda: dialog.value() == 66)
                self.assertIn(dialog, gui._tracked_progress_dialogs)
                self.assertIn("OpenAI", dialog.labelText())
                dialog.canceled.emit()
                self.assertTrue(controller.job.canceled.is_set())
                self.assertIsNone(controller.progress_dialog)
                self.assertNotIn(dialog, gui._tracked_progress_dialogs)
            finally:
                release_reply.set()
            self.wait_until(lambda: controller.job is None)
            self.assertIsNone(controller.result)
            self.assertIsNone(gui.project_service.load_json_artifact(gui.current_project_state, "video_summary"))

    def test_hooks_are_generated_separately_and_copied_one_at_a_time(self):
        video = self.host.source()
        gui = self.host.editor(video)
        gui.current_segments = [{"start": .42, "end": .8, "text": "Original"}]
        state = gui.current_project_state
        _, digest = transcript_snapshot(gui.current_segments)
        summary = {"version": 1, "transcript_digest": digest, "summary": "Tóm tắt mẫu",
                   "points": [{"text": "Một ý chính", "times_ms": [420]}], "people": []}
        gui.project_service.save_json_artifact(state, "video_summary", "analysis/video_summary.json", summary)
        hooks = ["Chuyện gì vừa xảy ra?", "Mọi thứ bắt đầu từ đâu?", "Một chi tiết đáng chú ý"]

        controller = gui.video_summary_controller
        with patch("ui.controllers.video_summary_controller.TranslationOrchestrator") as factory, \
                patch.object(QMessageBox, "question", side_effect=[QMessageBox.No, QMessageBox.Yes]), \
                patch.dict("os.environ", {"OPENAI_PROVIDER": "openai"}):
            engine = factory.return_value
            engine.suggest_video_hooks.return_value = hooks
            gui.workflow_tab_buttons["summary"].click()
            controller.refresh()
            factory.assert_not_called()
            controller.hook_btn.click()
            factory.assert_not_called()
            controller.hook_btn.click()
            self.wait_until(lambda: controller.job is None)
            engine.suggest_video_hooks.assert_called_once()
            engine.summarize_video.assert_not_called()
            self.assertEqual(engine.suggest_video_hooks.call_args.args[0], summary)
            copy_buttons = controller.hook_list.findChildren(QPushButton)
            copy_buttons[1].click()
            self.assertEqual(QApplication.clipboard().text(), hooks[1])
            saved = gui.project_service.load_json_artifact(gui.current_project_state, "video_summary")
            self.assertEqual(saved, {**summary, "hooks": hooks})

    def test_recap_draft_seek_edit_autosave_and_stale_transcript(self):
        video = self.host.source()
        gui = self.host.editor(video)
        gui.current_segments = [{"start": .42, "end": .8, "text": "Original dialogue"}]
        state = gui.current_project_state
        _, digest = transcript_snapshot(gui.current_segments)
        summary = {"version": 1, "transcript_digest": digest, "summary": "Tóm tắt mẫu",
                   "points": [{"text": "Một ý chính", "times_ms": [420]}], "people": []}
        gui.project_service.save_json_artifact(state, "video_summary", "analysis/video_summary.json", summary)
        controller = gui.video_summary_controller
        panel = controller.recap
        reply = json.dumps({"segments": [{"narration": "Lời dẫn mới", "cue_ids": [1]}]})
        with patch("ui.controllers.video_summary_controller.TranslationOrchestrator") as factory, \
                patch.object(QMessageBox, "question", side_effect=[QMessageBox.No, QMessageBox.Yes]), \
                patch.dict("os.environ", {"OPENAI_PROVIDER": "openai"}):
            engine = factory.return_value
            engine.write_video_recap.side_effect = lambda source, **kwargs: generate_video_recap(
                source, Mock(return_value=reply), progress=kwargs["progress"], is_canceled=kwargs["is_canceled"],
            )
            gui.workflow_tab_buttons["summary"].click()
            controller.refresh()
            factory.assert_not_called()
            panel.duration_combo.setCurrentIndex(panel.duration_combo.findData(120))
            panel.generate_btn.click()
            factory.assert_not_called()
            panel.generate_btn.click()
            self.wait_until(lambda: controller.job is None)
            engine.write_video_recap.assert_called_once()
            engine.summarize_video.assert_not_called()
            self.assertEqual(panel.draft["target_seconds"], 120)
            self.assertTrue(panel.evidence.isHidden())
            panel.evidence_toggle.click()
            self.assertFalse(panel.evidence.isHidden())
            self.assertIn("Original dialogue", panel.evidence.toPlainText())
            with patch.object(controller.recap_studio, "seek") as seek:
                panel.evidence.anchorClicked.emit(QUrl("seek:420"))
                seek.assert_called_once_with(420)
            panel.evidence_toggle.click()
            self.assertTrue(panel.evidence.isHidden())
            panel.narration.setPlainText("Lời dẫn đã chỉnh")
            self.wait_until(lambda: not controller.recap_save_timer.isActive())
            saved = gui.project_service.load_json_artifact(gui.current_project_state, "video_recap")
            self.assertEqual(saved["segments"][0]["narration"], "Lời dẫn đã chỉnh")
            panel.copy_btn.click()
            self.assertEqual(QApplication.clipboard().text(), "Lời dẫn đã chỉnh")

            reopened = self.host.editor(video)
            reopened.current_segments = list(gui.current_segments)
            reopened_controller = reopened.video_summary_controller
            reopened_controller.refresh()
            self.assertEqual(reopened_controller.recap.draft, saved)
            reopened.current_segments = [{"start": .65, "end": .8, "text": "Edited dialogue"}]
            reopened_controller.refresh()
            reopened_panel = reopened_controller.recap
            self.assertFalse(reopened_panel.generate_btn.isEnabled())
            with patch.object(reopened_controller.recap_studio, "seek") as seek:
                reopened_panel.evidence.anchorClicked.emit(QUrl("seek:420"))
                seek.assert_not_called()
            self.assertEqual(reopened_panel.narration.toPlainText(), "Lời dẫn đã chỉnh")

            project_file = gui.project_service.project_file(gui.current_project_state.project_root)
            panel.narration.setPlainText("Sửa ngay trước khi đổi dự án")
            self.assertTrue(controller.recap_save_timer.isActive())
            with patch.object(QTimer, "singleShot"), patch.object(gui, "_request_still"), \
                    patch.object(gui, "_preload_active_voice_if_needed"), patch("views.launcher.LauncherWindow.add_recent"):
                gui.load_video_project(str(self.host.source("another-video.mp4")))
            controller.refresh()
            self.assertIsNone(controller.recap.draft)
            original_state = gui.project_service.load_project(project_file)
            original_draft = gui.project_service.load_json_artifact(original_state, "video_recap")
            self.assertEqual(original_draft["segments"][0]["narration"], "Sửa ngay trước khi đổi dự án")

    def test_recap_controls_stay_at_top_and_wrapped_segments_share_the_available_height(self):
        gui = self.host.editor(self.host.source())
        controller = gui.video_summary_controller
        panel = controller.recap
        dialog = controller.recap_studio.dialog
        dialog.resize(1160, 800)
        dialog.show()
        try:
            QApplication.processEvents()
            self.assertIs(panel.window(), dialog)
            self.assertGreater(panel.width(), 200)
            self.assertGreater(panel.inspector.width(), 260)
            self.assertLess(panel.duration_combo.y(), 30)
            self.assertTrue(panel.segment_list.isHidden())

            long_narration = "Đoạn recap dài cần hiển thị đầy đủ và tự xuống dòng. " * 7
            evidence = [{"time_ms": 400, "text": "Original dialogue"}]
            panel.set_draft({"target_seconds": 60, "segments": [
                {"narration": "Đoạn ngắn", "evidence": evidence},
                {"narration": long_narration, "evidence": evidence},
            ]})
            QApplication.processEvents()
            segment_list = panel.segment_list
            self.assertGreater(segment_list.height(), 180)
            self.assertIn("…", segment_list.item(1).text())
            self.assertGreater(segment_list.visualItemRect(segment_list.item(1)).height(),
                               segment_list.visualItemRect(segment_list.item(0)).height())
            self.assertGreater(panel.narration.height(), 180)
            segment_list.setCurrentRow(1)
            self.assertEqual(panel.narration.toPlainText(), long_narration)
            self.assertEqual(panel.inspector.count(), 2)
        finally:
            dialog.close()

    def test_recap_voice_keeps_completed_clips_and_regenerates_edited_narration(self):
        gui = self.host.editor(self.host.source())
        gui.current_segments = [{"start": .4, "end": .8, "text": "Original"}]
        _, digest = transcript_snapshot(gui.current_segments)
        evidence = [{"time_ms": 400, "text": "Original"}]
        draft = {"version": 1, "target_seconds": 60, "transcript_digest": digest, "summary_digest": "summary",
                 "segments": [{"narration": text, "evidence": evidence} for text in ("Đoạn một", "Đoạn hai")]}
        state = gui.current_project_state
        gui.project_service.save_json_artifact(state, "video_recap", "analysis/video_recap.json", draft)
        gui.voice_catalog_entries_all = [
            {"name": "Người kể", "id": "recap-piper", "provider": "piper", "language": "vi", "gender": "female"},
            {"name": "Giọng nam", "id": "recap-male", "provider": "piper", "language": "vi", "gender": "male"},
            {"name": "Giọng CapCut", "id": "capcut:sample", "provider": "capcut", "language": "vi", "gender": "female"},
            {"name": "Other language", "id": "english-piper", "provider": "piper", "language": "en"},
        ]
        controller = gui.video_summary_controller
        voice = controller.recap_voice
        panel = controller.recap
        controller.refresh()
        calls = []

        def synthesize(**kwargs):
            calls.append(kwargs["text"])
            if calls == ["Đoạn một", "Đoạn hai"]:
                raise RuntimeError("Temporary TTS error")
            with wave.open(kwargs["wav_path"], "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00\x00" * 22000)

        with patch.object(gui, "ensure_required_resources", return_value=True), \
                patch("ui.controllers.recap_voice_controller.EngineRuntime") as runtime, \
                patch.object(gui, "play_audio_preview_file") as listen:
            runtime.return_value.synthesize_segment.side_effect = synthesize
            main_engine = gui.voice_engine_combo.currentData()
            panel.voice_engine_combo.setCurrentIndex(panel.voice_engine_combo.findData("piper"))
            panel.voice_gender_combo.setCurrentIndex(panel.voice_gender_combo.findData("female"))
            combo = panel.recap_voice_combo
            self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["", "recap-piper"])
            index = combo.findData("recap-piper")
            combo.setCurrentIndex(index)
            combo.activated.emit(index)
            self.assertEqual(panel.draft["voice_id"], "recap-piper")
            panel.voice_engine_combo.setCurrentIndex(panel.voice_engine_combo.findData("capcut"))
            self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["", "capcut:sample"])
            self.assertEqual(panel.draft["voice_id"], "recap-piper")
            self.assertEqual(gui.voice_engine_combo.currentData(), main_engine)
            panel.generate_all_voice_btn.click()
            studio = controller.recap_studio
            self.assertFalse(studio.voice_progress_row.isHidden())
            self.assertEqual(studio.voice_progress_bar.maximum(), 2)
            self.assertEqual(studio.voice_progress_bar.value(), 0)
            self.wait_until(lambda: not voice.busy)
            self.assertIn("Temporary TTS error", studio.voice_progress_label.text())
            first_audio = panel.draft["segments"][0]["audio"]
            self.assertEqual(first_audio["duration_ms"], 1375)
            self.assertNotIn("audio", panel.draft["segments"][1])
            panel.generate_all_voice_btn.click()
            self.wait_until(lambda: not voice.busy)
            self.assertEqual(calls, ["Đoạn một", "Đoạn hai", "Đoạn hai"])
            self.assertEqual(runtime.return_value.synthesize_segment.call_args.kwargs["language_code"], "vi")
            panel.play_voice_btn.click()
            listen.assert_called_once_with(str(Path(state.project_root) / first_audio["path"]))
            panel.narration.setPlainText("Đoạn một đã sửa")
            self.assertFalse(panel.play_voice_btn.isEnabled())
            panel.generate_segment_voice_btn.click()
            self.assertEqual(studio.voice_progress_bar.maximum(), 1)
            self.assertFalse(studio.voice_progress_bar.isHidden())
            self.wait_until(lambda: not voice.busy)
            self.assertTrue(studio.voice_progress_bar.isHidden())
            self.assertEqual(calls[-1], "Đoạn một đã sửa")
            self.assertTrue(panel.play_voice_btn.isEnabled())
            saved = gui.project_service.load_json_artifact(gui.current_project_state, "video_recap")
            self.assertEqual(saved, panel.draft)
            self.assertNotEqual(saved["segments"][0]["audio"]["path"], first_audio["path"])
            self.assertTrue((Path(state.project_root) / saved["segments"][1]["audio"]["path"]).is_file())

    def test_studio_previews_before_use_and_rebuilds_only_after_a_scene_changes(self):
        source = self.host.source()
        generated = self.host.source("recap-result.mp4")
        gui = self.host.editor(source)
        original = gui.current_project_state
        controller = gui.video_summary_controller
        studio = controller.recap_studio
        studio.player = Mock()
        studio.player.isSeekable.return_value = True
        gui.current_segments = [{"start": .4, "end": .8, "text": "Original"}]
        _, digest = transcript_snapshot(gui.current_segments)
        audio = Path(original.project_root) / "analysis" / "recap_audio" / "sample.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"mock audio")
        draft = {"version": 1, "target_seconds": 60, "transcript_digest": digest, "summary_digest": "summary",
                 "voice_id": "recap-voice", "segments": [{
                     "narration": "Lời dẫn", "evidence": [{"time_ms": 400, "text": "Original"}],
                     "scene": {"start_ms": 336, "end_ms": 270},
                     "audio": {"path": "analysis/recap_audio/sample.wav", "duration_ms": 450,
                               "voice_id": "recap-voice", "text_digest": narration_digest("Lời dẫn")},
                 }]}
        gui.project_service.save_json_artifact(original, "video_recap", "analysis/video_recap.json", draft)
        studio._duration_changed(1000)
        self.assertTrue(studio.use_btn.isEnabled())
        self.assertTrue(studio.avoid_overlap_cb.isChecked())
        studio.avoid_overlap_cb.click()
        self.assertFalse(controller.recap.draft["avoid_scene_overlap"])
        self.assertTrue(studio.preview_btn.isEnabled())
        self.assertEqual(studio.preview_btn.toolTip(), harness.main_window.t("Watch the assembled recap with narration."))
        self.assertEqual(controller.recap.draft["segments"][0]["scene"],
                         {"start_ms": 310, "end_ms": 760, "selection": "auto"})
        self.assertFalse(studio.restore_cut_btn.isEnabled())
        studio.clip_start.setValue(.2)
        studio.clip_end.setValue(.5)
        studio.apply_trim_btn.click()
        self.assertEqual(studio.clip_start.text(), "00:00.200")
        self.assertEqual(studio.clip_end.text(), "00:00.500")
        self.assertTrue(studio.restore_cut_btn.isEnabled())
        studio.restore_cut_btn.click()
        self.assertEqual((studio.clip_start.value(), studio.clip_end.value()), (.31, .76))
        self.assertFalse(studio.restore_cut_btn.isEnabled())
        studio.clip_start.setValue(.2)
        studio.clip_end.setValue(.5)
        studio.apply_trim_btn.click()
        segment = controller.recap.draft["segments"][0]
        segment.pop("original_scene")
        studio.refresh()
        self.assertTrue(studio.restore_cut_btn.isEnabled())
        studio.restore_cut_btn.click()
        self.assertEqual((studio.clip_start.value(), studio.clip_end.value()), (.31, .76))
        self.assertEqual(segment["original_scene"]["end_ms"], 760)
        studio.clip_start.setValue(.2)
        studio.clip_end.setValue(.5)
        studio.apply_trim_btn.click()
        self.assertTrue(studio.use_btn.isEnabled())
        self.assertIn("00:00.3", studio.scene_status.text())
        self.assertTrue(studio.timeline.spans[0].has_mismatch)
        self.assertFalse(studio.mismatch_status.isHidden())
        self.assertEqual(studio.mismatch_status.text(),
                         harness.main_window.t("Scenes with duration mismatch: {scenes}", scenes="1"))
        self.assertEqual(studio.source_timeline.cut, (200, 500))
        self.assertIn("00:00.200–00:00.500", studio.source_cut_label.text())
        self.assertIn("0.3", studio.source_cut_label.text())
        self.assertFalse(studio.source_timeline.isHidden())
        self.assertTrue(studio.slider.isHidden())
        source_end = round(studio.source_timeline._x(1000))
        QTest.mouseClick(studio.source_timeline, Qt.LeftButton, pos=QPoint(source_end, 30))
        studio.player.setPosition.assert_called_with(1000)
        studio._position_changed(400)
        self.assertEqual(studio.source_timeline.playhead, 400)
        studio.preview_scene()
        studio.player.setPosition.assert_called_with(200)
        studio.player.play.assert_called_once()
        previews = []

        def build_preview(_source, _clips, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            result = output_dir / f"preview-{len(previews) + 1}.mp4"
            result.write_bytes(generated.read_bytes())
            previews.append(result)
            return str(result)

        with patch("ui.controllers.recap_studio_controller.render_recap_video", side_effect=build_preview) as render, \
                patch.object(QMessageBox, "question", side_effect=[QMessageBox.No, QMessageBox.Yes,
                                                                    QMessageBox.Yes, QMessageBox.Yes]) as confirm, \
                patch.object(QTimer, "singleShot"), patch.object(gui, "_request_still"), \
                patch.object(gui, "_preload_active_voice_if_needed"), patch("views.launcher.LauncherWindow.add_recent"):
            studio.preview_btn.click()
            render.assert_not_called()
            self.assertTrue(studio.source_btn.isChecked())
            self.assertFalse(studio.preview_btn.isChecked())
            self.assertEqual(confirm.call_args.args[-1], QMessageBox.No)
            studio.preview_btn.click()
            self.wait_until(lambda: studio._preview_active)
            self.assertTrue(studio.preview_btn.isChecked())
            self.assertFalse(studio.source_btn.isChecked())
            self.assertTrue(studio.source_timeline.isHidden())
            self.assertFalse(studio.slider.isHidden())
            self.assertEqual(gui.current_project_state.project_root, original.project_root)
            self.assertIsNotNone(studio.timeline._trim_edges())
            studio._duration_changed(450)
            studio.player.mediaStatus.return_value = QMediaPlayer.LoadedMedia
            studio.player.playbackState.return_value = QMediaPlayer.PausedState
            studio.player.play.reset_mock()
            studio._media_status_changed(QMediaPlayer.LoadedMedia)
            studio.player.play.assert_not_called()
            self.assertTrue(studio.play_btn.isEnabled())
            studio._playback_changed(QMediaPlayer.PlayingState)
            self.assertTrue(studio.pause_btn.isEnabled())
            self.assertFalse(studio.play_btn.isEnabled())
            studio.pause_btn.click()
            studio.player.pause.assert_called()
            studio._playback_changed(QMediaPlayer.PausedState)
            self.assertTrue(studio.play_btn.isEnabled())
            self.assertFalse(studio.pause_btn.isEnabled())
            studio.play_btn.click()
            studio.player.play.assert_called()
            studio.player.mediaStatus.return_value = QMediaPlayer.EndOfMedia
            studio._playback_changed(QMediaPlayer.PlayingState)
            self.assertTrue(studio.play_btn.isEnabled())
            studio.play_btn.click()
            studio.player.setPosition.assert_called_with(0)
            studio.player.mediaStatus.return_value = QMediaPlayer.LoadedMedia
            self.assertTrue(studio.use_btn.isEnabled())
            studio.preview_btn.click()
            render.assert_called_once()

            studio.show_source()
            studio._duration_changed(1000)
            track = studio.timeline
            track.resize(965, track.height())
            y = track._SCENE_Y + 12
            QTest.mousePress(track, Qt.LeftButton, pos=QPoint(round(track._x(0)), y))
            QTest.mouseMove(track, QPoint(round(track._x(50)), y))
            QTest.mouseRelease(track, Qt.LeftButton, pos=QPoint(round(track._x(50)), y))
            self.assertEqual(controller.recap.draft["segments"][0]["scene"]["start_ms"], 250)
            QTest.mousePress(track, Qt.LeftButton, pos=QPoint(round(track._x(250)), y))
            QTest.mouseMove(track, QPoint(round(track._x(350)), y))
            QTest.keyClick(track, Qt.Key_Escape)
            QTest.mouseRelease(track, Qt.LeftButton, pos=QPoint(round(track._x(350)), y))
            self.assertEqual(controller.recap.draft["segments"][0]["scene"]["end_ms"], 500)
            self.assertEqual(studio.timeline.spans[0].clip_ms, 250)
            inspector_index = controller.recap.inspector.currentIndex()
            studio._timeline_picked(0, 180)
            studio.player.setPosition.assert_called_with(430)
            self.assertEqual(controller.recap.inspector.currentIndex(), inspector_index)
            studio.preview_btn.click()
            self.wait_until(lambda: studio._preview_active)
            self.assertEqual(render.call_count, 2)
            self.assertFalse(previews[0].exists())
            preview_path = studio._preview_path
            studio.include_subtitles_cb.setChecked(True)
            studio.use_btn.click()
            self.wait_until(lambda: gui.current_project_state.project_root != original.project_root)
            self.assertTrue(preview_path.is_file())

            reopened = self.host.editor(source)
            reopened.current_segments = [{"start": .4, "end": .8, "text": "Original"}]
            reopened_studio = reopened.video_summary_controller.recap_studio
            reopened_studio.player = Mock()
            reopened_studio.open()
            reopened_studio._duration_changed(1000)
            self.wait_until(reopened_studio.preview_btn.isChecked)
            reopened_studio.player.setSource.assert_called_with(QUrl.fromLocalFile(str(preview_path)))
            reopened_studio.player.play.assert_not_called()
            self.assertEqual(reopened_studio._preview_path, preview_path)
            audio.write_bytes(b"updated audio")
            self.assertIsNone(reopened_studio._cached_preview([RecapClip(250, 500, audio, 450)]))
        self.assertEqual(render.call_count, 2)
        opened = Path(gui.current_project_state.input_video)
        self.assertEqual(opened.parent, self.host.root / "output" / "recap")
        self.assertEqual(opened.read_bytes(), generated.read_bytes())
        self.assertEqual(gui.current_project_state.input_language, "vi")
        self.assertEqual([row["text"] for row in gui.current_segments], ["Lời dẫn"])
        self.assertEqual((gui.current_segments[0]["start"], gui.current_segments[0]["end"]), (0.0, .45))
        self.assertFalse(gui.current_translated_segments)
        self.assertEqual(gui.current_project_state.steps["translate_raw"], "skipped")
        self.assertEqual(gui.workflow_stage_badges["tts"].text(), harness.main_window.t("Included in recap video"))
        self.assertTrue(gui.export_btn.isEnabled())
        self.assertEqual(gui.preview_controller._effective_render_mode_without_tts("both"), "subtitle")
        export_srt = Path(gui.preview_controller._prepare_current_export_srt()).read_text(encoding="utf-8")
        self.assertIn("Lời dẫn", export_srt)
        self.assertEqual(gui.current_project_state.steps["translate_raw"], "skipped")
        self.assertEqual(render.call_args.args[1], [RecapClip(250, 500, audio, 450)])
        original_file = gui.project_service.project_file(original.project_root)
        original_draft = gui.project_service.load_json_artifact(gui.project_service.load_project(original_file), "video_recap")
        self.assertEqual(original_draft["segments"][0]["scene"],
                         {"start_ms": 250, "end_ms": 500, "selection": "manual"})
        self.assertEqual(original_draft["segments"][0]["original_scene"],
                         {"start_ms": 310, "end_ms": 760, "selection": "auto"})
        self.assertIsNone(gui.project_service.load_json_artifact(gui.current_project_state, "video_recap"))

    def test_source_timecode_accepts_minutes_seconds_and_milliseconds(self):
        from PySide6.QtGui import QValidator
        from ui.controllers.recap_studio_controller import _SourceTimeSpinBox

        spin = _SourceTimeSpinBox()
        spin.setDecimals(3)
        spin.setMaximum(300)
        spin.setValue(230.5)
        self.assertEqual(spin.text(), "03:50.500")
        spin.lineEdit().setText("04:06.417")
        spin.interpretText()
        self.assertAlmostEqual(spin.value(), 246.417)
        self.assertEqual(spin.validate("04:60.000", 9)[0], QValidator.Invalid)
        self.assertNotEqual(spin.validate("05:01.000", 9)[0], QValidator.Acceptable)

    def test_opening_recap_without_subtitles_caches_the_preview(self):
        source = self.host.source()
        gui = self.host.editor(source)
        source_project = gui.current_project_state
        controller = gui.video_summary_controller
        studio = controller.recap_studio
        gui.current_segments = [{"start": .1, "end": .4, "text": "Clip"}]
        _, digest = transcript_snapshot(gui.current_segments)
        audio = Path(source_project.project_root) / "analysis" / "recap_audio" / "intro.wav"
        audio.parent.mkdir(parents=True)
        with wave.open(str(audio), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 6400)
        draft = {
            "version": 1, "target_seconds": 60, "transcript_digest": digest, "summary_digest": "summary",
            "voice_id": "recap-voice",
            "segments": [{
                "narration": "Cảnh một", "evidence": [{"time_ms": 100, "text": "Clip"}],
                "scene": {"start_ms": 0, "end_ms": 400, "selection": "manual"},
                "audio": {"path": "analysis/recap_audio/intro.wav", "duration_ms": 400,
                          "voice_id": "recap-voice", "text_digest": narration_digest("Cảnh một")},
            }],
        }
        gui.project_service.save_json_artifact(source_project, "video_recap", "analysis/video_recap.json", draft)
        with patch("ui.controllers.recap_studio_controller.render_recap_video", wraps=render_recap_video) as render, \
                patch.object(QTimer, "singleShot"), patch.object(gui, "_request_still"), \
                patch.object(gui, "_preload_active_voice_if_needed"), patch("views.launcher.LauncherWindow.add_recent"):
            studio.open()
            self.wait_until(studio.use_btn.isEnabled)
            self.assertFalse(studio.include_subtitles_cb.isChecked())
            studio.use_btn.click()
            self.wait_until(lambda: gui.current_project_state.project_root != source_project.project_root)
            self.assertEqual(gui.current_segments, [])
            self.assertEqual(gui.current_project_state.steps["transcribe"], "skipped")
            self.assertEqual(gui.workflow_stage_badges["export"].text(), harness.main_window.t("Ready to export"))
            self.assertTrue(gui.export_btn.isEnabled())
            srt_path = gui.preview_controller._prepare_current_export_srt()
            self.assertEqual(Path(srt_path).read_bytes(), b"")
            exported = self.host.root / "final-recap.mp4"
            ExportWorkflow(str(self.host.root)).run(
                video_path=gui.current_project_state.input_video, output_path=str(exported),
                mode=gui.preview_controller._effective_render_mode_without_tts("both"), srt_path=srt_path,
            )
            probe = subprocess.run([
                bin_path("ffmpeg", "ffprobe.exe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(exported),
            ], check=True, capture_output=True, **subprocess_hidden_kwargs())
            self.assertEqual(probe.stdout.strip(), b"audio")

            gui.load_video_project(str(source))
            studio.open()
            self.wait_until(lambda: studio.preview_btn.isChecked() and studio.duration_ms > 0)
            cached = studio._preview_path
            self.assertIsNotNone(cached)
            self.assertTrue(cached.is_file())
            studio.include_subtitles_cb.setChecked(True)
            studio.dialog.reject()
            studio.open()
            self.wait_until(lambda: studio.preview_btn.isChecked() and studio.duration_ms > 0)
            self.assertFalse(studio.include_subtitles_cb.isChecked())
            self.assertEqual(studio._preview_path, cached)
            render.assert_called_once()
            studio.dialog.reject()

    def test_recap_renders_in_order_and_keeps_the_whole_narration(self):
        source = self.host.root / "colored-source.mp4"
        ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
        ffprobe = bin_path("ffmpeg", "ffprobe.exe")
        subprocess.run([
            ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x90:r=25:d=0.6",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25:d=0.6", "-f", "lavfi", "-i",
            "sine=frequency=220:duration=1.2", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", str(source),
        ], check=True, capture_output=True, **subprocess_hidden_kwargs())
        clips = []
        for index, (start, end, audio_ms) in enumerate(((640, 760, 440), (0, 400, 200))):
            audio = self.host.root / f"narration-{index}.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00\x00" * (audio_ms * 16))
            clips.append(RecapClip(start, end, audio, audio_ms))
        progress = Mock()
        output = render_recap_video(source, clips, self.host.root / "output", progress=progress, is_canceled=lambda: False)
        probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration",
                                "-of", "json", output], check=True, capture_output=True, **subprocess_hidden_kwargs())
        streams = {stream["codec_type"]: float(stream["duration"]) for stream in json.loads(probe.stdout)["streams"]}
        self.assertEqual(set(streams), {"video", "audio"})
        self.assertAlmostEqual(streams["video"], .84, delta=.05)
        self.assertAlmostEqual(streams["audio"], .84, delta=.03)
        self.assertEqual(recap_subtitles(clips, ["Một", "Hai"], Fraction(25)), [
            {"start": 0.0, "end": .44, "text": "Một"},
            {"start": .44, "end": .64, "text": "Hai"},
        ])
        self.assertEqual(recap_subtitles(clips[::-1], ["Hai", "Một"], Fraction(24))[1]["start"], 10 / 24)
        for position, channel in (("0.04", 2), ("0.36", 2), ("0.60", 0)):
            frame = subprocess.run([ffmpeg, "-v", "error", "-ss", position, "-i", output, "-frames:v", "1",
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                   check=True, capture_output=True, **subprocess_hidden_kwargs()).stdout
            self.assertGreater(frame[channel], 200)
        self.assertEqual(progress.call_args.args, (3, 3))

    def test_old_recap_adopts_first_clip_and_preserves_the_old_list(self):
        clips = [{"start_ms": 700, "end_ms": 900}, {"start_ms": 250, "end_ms": 500}]
        segment = {"clips": clips, "scene": {"start_ms": 250, "end_ms": 500}}
        draft = {"segments": [segment]}
        upgrade_scene_ranges(draft)
        self.assertEqual(segment["scene"], {"start_ms": 700, "end_ms": 900, "selection": "manual"})
        segment["scene"]["start_ms"] = 750
        upgrade_scene_ranges(draft)
        self.assertEqual(segment["legacy_clips"], clips)
        self.assertEqual(segment["legacy_clips"][0]["start_ms"], 700)
        self.assertEqual(segment["scene"]["start_ms"], 750)

    def test_scene_suggestions_resize_for_new_audio_and_preserve_manual_markers(self):
        rows, _ = transcript_snapshot([
            {"start": 1, "end": 1.8, "text": "First cue"},
            {"start": 5.88, "end": 6, "text": "Cue near end of source"},
        ])
        voice = "recap-voice"
        owned = {"start_ms": 0, "end_ms": 950}
        draft = {"voice_id": voice, "segments": [
            {"narration": "Đoạn đầu", "evidence": [{"time_ms": 1000}], "scene": owned},
            {"narration": "Đoạn sau", "evidence": [{"time_ms": 5880, "cue_id": 2}]},
        ]}
        root = self.host.root
        self.assertTrue(suggest_scene_ranges(draft, rows, root, 6000))
        self.assertEqual(draft["segments"][0]["scene"], owned)
        segment = draft["segments"][1]
        self.assertEqual(segment["scene"], {"start_ms": 5000, "end_ms": 6000, "selection": "auto"})
        audio = root / "analysis" / "recap_audio" / "recap.wav"
        audio.parent.mkdir(parents=True)
        audio.touch()
        segment["audio"] = {"path": audio.relative_to(root).as_posix(), "voice_id": voice,
                            "duration_ms": 1300, "text_digest": narration_digest(segment["narration"])}
        self.assertTrue(suggest_scene_ranges(draft, rows, root, 6000))
        self.assertEqual(segment["scene"], {"start_ms": 4700, "end_ms": 6000, "selection": "auto"})
        self.assertFalse(suggest_scene_ranges(draft, rows, root, 6000))

    def test_scene_overlap_preference_respects_manual_ranges(self):
        rows, _ = transcript_snapshot([
            {"start": 1, "end": 1.4, "text": "Shared cue"},
            {"start": 4, "end": 4.4, "text": "Other cue"},
        ])
        manual = {"start_ms": 800, "end_ms": 1800, "selection": "manual"}
        draft = {"segments": [
            {"narration": "Cảnh một", "evidence": [{"time_ms": 1000}], "scene": manual.copy()},
            {"narration": "Cảnh hai", "evidence": [{"time_ms": 1000}, {"time_ms": 4000}]},
        ]}
        root = self.host.root
        self.assertTrue(suggest_scene_ranges(draft, rows, root, 6000))
        self.assertEqual(draft["segments"][1]["scene"]["start_ms"], 3800)
        draft["avoid_scene_overlap"] = False
        self.assertTrue(suggest_scene_ranges(draft, rows, root, 6000, reselect_automatic=True))
        self.assertEqual(draft["segments"][1]["scene"]["start_ms"], 800)
        self.assertEqual(draft["segments"][0]["scene"], manual)
