import json
import time
import unittest
from threading import Event
from unittest.mock import Mock, patch

import test_performance_queue as harness
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from translation.errors import TranslationValidationError
from translation.providers.browser_bridge_provider import BrowserBridgePolisherProvider
from translation.video_summary import generate_video_hooks, generate_video_summary, transcript_snapshot


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

        browser.run_video_analysis(generate_video_summary, rows, progress=Mock(), is_canceled=lambda: False)

        task = server.enqueue_task.call_args.kwargs["payload"]
        self.assertEqual(task["operation"], "summary")
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
