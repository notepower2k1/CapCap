import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication

# Ensure QApplication exists for Qt widget tests
app = QApplication.instance() or QApplication([])

from PySide6.QtWidgets import QDialog
from ui.controllers.subtitle_controller import (
    DialogueContextReviewDialog,
    SubtitleController,
    TranslationPromptDialog,
)
from app.translation.orchestrator import TranslationOrchestrator


class TestTranslationPromptDialogContextOption(unittest.TestCase):
    def setUp(self):
        for org, app_name in [("CapCap", "CapCap"), ("CapCap", "VideoTranslatorGUI")]:
            s = QSettings(org, app_name)
            s.setValue("auto_translation_context", "1")
            s.setValue("review_translation_context", "1")
            s.setValue("translation_provider", "google_ai_studio")
        self.orig_provider = os.environ.get("OPENAI_PROVIDER")
        os.environ["OPENAI_PROVIDER"] = "google_ai_studio"

    def tearDown(self):
        if self.orig_provider is not None:
            os.environ["OPENAI_PROVIDER"] = self.orig_provider
        else:
            os.environ.pop("OPENAI_PROVIDER", None)

    def test_review_checkbox_toggles_with_auto_context(self):
        parent = None
        dialog = TranslationPromptDialog(parent, src_lang="zh", target_lang="vi")

        # By default auto context is True, review context is enabled
        self.assertTrue(dialog.auto_context_cb.isEnabled())
        self.assertTrue(dialog.review_context_cb.isEnabled())

        # Unchecking auto context must disable review context
        dialog.auto_context_cb.setChecked(False)
        self.assertFalse(dialog.review_context_cb.isEnabled())

        # Checking auto context re-enables review context
        dialog.auto_context_cb.setChecked(True)
        self.assertTrue(dialog.review_context_cb.isEnabled())

    def test_save_prompts_stores_review_context_setting(self):
        dialog = TranslationPromptDialog(None, src_lang="zh", target_lang="vi")
        dialog.auto_context_cb.setChecked(True)
        dialog.review_context_cb.setChecked(True)
        dialog._on_translate()

        self.assertTrue(dialog.selected_auto_context)
        self.assertTrue(dialog.selected_review_context)
        self.assertEqual(dialog.settings.value("review_translation_context"), "1")

        # When auto context is unchecked, review context is forced to False
        dialog.auto_context_cb.setChecked(False)
        dialog.review_context_cb.setChecked(True)
        dialog._on_translate()
        self.assertFalse(dialog.selected_auto_context)
        self.assertFalse(dialog.selected_review_context)
        self.assertEqual(dialog.settings.value("review_translation_context"), "0")


class TestDialogueContextReviewDialog(unittest.TestCase):
    def setUp(self):
        self.settings = QSettings("CapCap", "CapCap")
        self.orig_review = self.settings.value("review_translation_context", "1")

    def tearDown(self):
        self.settings.setValue("review_translation_context", self.orig_review)

    def test_initial_content_and_edit_continue(self):
        initial_text = "Character A (anh) calls Character B (em)"
        dialog = DialogueContextReviewDialog(None, initial_context=initial_text)
        self.assertEqual(dialog.text_edit.toPlainText().strip(), initial_text)

        # User edits text
        dialog.text_edit.setPlainText("Character A (chị) calls Character B (em)")
        dialog._on_continue()

        self.assertEqual(dialog.result_context, "Character A (chị) calls Character B (em)")
        self.assertFalse(dialog.skipped)

    def test_skip_rules(self):
        initial_text = "Some rules"
        dialog = DialogueContextReviewDialog(None, initial_context=initial_text)
        dialog._on_skip()

        self.assertEqual(dialog.result_context, "__SKIP__")
        self.assertTrue(dialog.skipped)

    def test_dont_show_again_checkbox_updates_settings(self):
        dialog = DialogueContextReviewDialog(None, initial_context="rules")
        dialog.dont_show_again_cb.setChecked(True)
        dialog._on_continue()

        self.assertEqual(self.settings.value("review_translation_context"), "0")
        self.assertEqual(os.environ.get("CAPCAP_REVIEW_TRANSLATION_CONTEXT"), "0")

    @patch("ui.controllers.subtitle_controller.ContextExtractionWorker")
    def test_reanalyze_passes_user_feedback_to_worker(self, mock_worker_cls):
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker

        segments = [{"start": 0.0, "end": 1.0, "text": "你好"}]
        dialog = DialogueContextReviewDialog(None, initial_context="rules", segments=segments)
        dialog.feedback_input.setText("Triều Tịch là nữ sinh viên, kẻ bám đuôi xưng mày-tao")
        dialog._on_reanalyze()

        mock_worker_cls.assert_called_once()
        _, kwargs = mock_worker_cls.call_args
        self.assertEqual(kwargs.get("user_guidance"), "Triều Tịch là nữ sinh viên, kẻ bám đuôi xưng mày-tao")
        self.assertEqual(kwargs.get("existing_context"), "rules")
        mock_worker.start.assert_called_once()


class TestOrchestratorContextGuidanceHandling(unittest.TestCase):
    def test_skip_sentinel_bypasses_learning(self):
        orch = TranslationOrchestrator()
        # Mock _resolve_ai_provider
        mock_polisher = MagicMock()
        mock_polisher.is_configured.return_value = True
        mock_polisher.polish_batch.return_value = (["Xin chào"], [], "test_model")

        with patch.object(orch, "_resolve_ai_provider", return_value=("google_ai_studio", mock_polisher)), \
             patch("app.translation.orchestrator.learn_dialogue_context") as mock_learn:
            segments = [{"start": 0.0, "end": 1.0, "text": "你好"}]
            # With context_guidance="__SKIP__", learn_dialogue_context must NOT be called
            res = orch.translate_segments(
                segments=segments,
                src_lang="zh",
                target_lang="vi",
                context_guidance="__SKIP__",
            )
            mock_learn.assert_not_called()
            self.assertTrue(res.success)


class TestSubtitleControllerIntegration(unittest.TestCase):
    def setUp(self):
        self.gui = MagicMock()
        self.gui.ensure_current_project.return_value = None
        self.gui.build_current_translation_signature.return_value = ""
        self.gui.get_source_language_code.return_value = "zh"
        self.gui.get_target_language_code.return_value = "vi"
        self.gui.is_ai_polish_enabled.return_value = True
        self.gui.current_translated_segments = []
        self.gui.translated_text.toPlainText.return_value = ""
        self.gui.original_text.toPlainText.return_value = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"
        self.gui.current_segments = [{"start": 0.0, "end": 1.0, "text": "你好", "speaker": "A"}]
        self.gui.translation_thread = None

        self.controller = SubtitleController(self.gui)
        self.controller._show_translation_progress = MagicMock()

    @patch("ui.controllers.subtitle_controller.TranslationPromptDialog")
    @patch("ui.controllers.subtitle_controller.TranslationWorker")
    def test_run_translation_calls_review_when_enabled(self, mock_worker_cls, mock_dialog_cls):
        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.Accepted
        mock_dialog.selected_provider = "google_ai_studio"
        mock_dialog.selected_batch_size = 50
        mock_dialog.selected_prompt = "custom prompt"
        mock_dialog.selected_auto_context = True
        mock_dialog.selected_review_context = True
        mock_dialog_cls.return_value = mock_dialog

        mock_worker_instance = MagicMock()
        mock_worker_cls.return_value = mock_worker_instance

        with patch.object(self.controller, "_extract_and_review_context", return_value="A calls B anh") as mock_review:
            self.controller.run_translation(show_prompt_dialog=True)

            mock_review.assert_called_once()
            mock_worker_cls.assert_called_once()
            _, kwargs = mock_worker_cls.call_args
            self.assertEqual(kwargs.get("context_guidance"), "A calls B anh")

    @patch("ui.controllers.subtitle_controller.TranslationPromptDialog")
    @patch("ui.controllers.subtitle_controller.TranslationWorker")
    def test_run_translation_bypasses_review_when_auto_context_disabled(self, mock_worker_cls, mock_dialog_cls):
        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.Accepted
        mock_dialog.selected_provider = "google_ai_studio"
        mock_dialog.selected_batch_size = 50
        mock_dialog.selected_prompt = ""
        mock_dialog.selected_auto_context = False
        mock_dialog.selected_review_context = False
        mock_dialog_cls.return_value = mock_dialog

        mock_worker_instance = MagicMock()
        mock_worker_cls.return_value = mock_worker_instance

        with patch.object(self.controller, "_extract_and_review_context") as mock_review:
            self.controller.run_translation(show_prompt_dialog=True)

            mock_review.assert_not_called()
            mock_worker_cls.assert_called_once()
            _, kwargs = mock_worker_cls.call_args
            self.assertEqual(kwargs.get("context_guidance"), "")

    @patch("ui.controllers.subtitle_controller.TranslationPromptDialog")
    @patch("ui.controllers.subtitle_controller.TranslationWorker")
    def test_run_translation_bypasses_review_when_provider_is_google(self, mock_worker_cls, mock_dialog_cls):
        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.Accepted
        mock_dialog.selected_provider = "google"
        mock_dialog.selected_batch_size = 50
        mock_dialog.selected_prompt = ""
        mock_dialog.selected_auto_context = True
        mock_dialog.selected_review_context = True
        mock_dialog_cls.return_value = mock_dialog

        mock_worker_instance = MagicMock()
        mock_worker_cls.return_value = mock_worker_instance

        with patch.object(self.controller, "_extract_and_review_context") as mock_review:
            self.controller.run_translation(show_prompt_dialog=True)

            mock_review.assert_not_called()
            mock_worker_cls.assert_called_once()
            _, kwargs = mock_worker_cls.call_args
            self.assertEqual(kwargs.get("context_guidance"), "")

    @patch("ui.controllers.subtitle_controller.TranslationPromptDialog")
    @patch("ui.controllers.subtitle_controller.TranslationWorker")
    def test_run_translation_aborts_when_user_cancels_review(self, mock_worker_cls, mock_dialog_cls):
        mock_dialog = MagicMock()
        mock_dialog.exec.return_value = QDialog.Accepted
        mock_dialog.selected_provider = "google_ai_studio"
        mock_dialog.selected_batch_size = 50
        mock_dialog.selected_prompt = ""
        mock_dialog.selected_auto_context = True
        mock_dialog.selected_review_context = True
        mock_dialog_cls.return_value = mock_dialog

        with patch.object(self.controller, "_extract_and_review_context", return_value=None) as mock_review:
            self.controller.run_translation(show_prompt_dialog=True)

            mock_review.assert_called_once()
            mock_worker_cls.assert_not_called()

    def test_on_translation_status_changed_updates_dialog(self):
        mock_dialog = MagicMock()
        mock_dialog._progress_str = "10/100 cues (10%)"
        mock_dialog._started_at = 100.0
        mock_dialog._action = "Translating"
        mock_dialog._provider = "Google AI Studio"
        self.gui._translation_progress_dialog = mock_dialog

        self.controller.on_translation_status_changed("bing", "Falling back to Bing...")
        self.assertEqual(mock_dialog._provider, "Bing Translator")
        actual_title = mock_dialog.setWindowTitle.call_args[0][0]
        self.assertIn("Bing Translator", actual_title)
        self.assertTrue("Subtitles" in actual_title or "Phụ đề" in actual_title)
        mock_dialog.setLabelText.assert_called()

    def test_on_translation_finished_reports_bing_fallback(self):
        self.gui.video_path_edit.text.return_value = ""
        with patch("ui.controllers.subtitle_controller.QMessageBox.information") as mock_info:
            self.controller.on_translation_finished("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", "", "BING_FALLBACK\nwarning")
            mock_info.assert_called_once()
            _, args, _ = mock_info.mock_calls[0]
            self.assertIn("Bing Translator", args[2])



class TestCleanDialogueContext(unittest.TestCase):
    def test_replaces_latex_arrows(self):
        from app.translation.context_analyzer import clean_dialogue_context
        raw = "- Triều Tịch $\\leftrightarrow$ Kẻ bám đuôi: gọi Anh $\\rightarrow$ xưng Tôi."
        cleaned = clean_dialogue_context(raw)
        self.assertNotIn("leftrightarrow", cleaned)
        self.assertNotIn("rightarrow", cleaned)
        self.assertNotIn("$", cleaned)
        self.assertIn("↔", cleaned)
        self.assertIn("→", cleaned)


class TestLearnDialogueContextWithUserGuidance(unittest.TestCase):
    def test_user_guidance_injected_into_prompt(self):
        from app.translation.context_analyzer import learn_dialogue_context

        mock_polisher = MagicMock()
        mock_polisher.generate_text.return_value = "### Hồ sơ nhân vật & Quy tắc xưng hô bắt buộc:\n- A ↔ B: mày - tao"

        segments = [
            {"text": "Line 1"},
            {"text": "Line 2"},
            {"text": "Line 3"},
        ]
        result = learn_dialogue_context(
            source_segments=segments,
            polisher=mock_polisher,
            src_lang="zh-Hans",
            target_lang="vi",
            user_guidance="A và B là kẻ thù, xưng mày-tao",
        )

        mock_polisher.generate_text.assert_called_once()
        _, kwargs = mock_polisher.generate_text.call_args
        system_msg = kwargs.get("system_msg", "")
        self.assertIn("CRITICAL USER CORRECTIONS & GUIDANCE", system_msg)
        self.assertIn("A và B là kẻ thù, xưng mày-tao", system_msg)
        self.assertIn("mày - tao", result)

    def test_existing_context_injected_into_prompt(self):
        from app.translation.context_analyzer import learn_dialogue_context

        mock_polisher = MagicMock()
        mock_polisher.generate_text.return_value = "### Hồ sơ nhân vật & Quy tắc xưng hô bắt buộc:\n- Triều Tịch ↔ Lưu Giai Ngọc: anh - em"

        segments = [{"text": "Line 1"}, {"text": "Line 2"}, {"text": "Line 3"}]
        result = learn_dialogue_context(
            source_segments=segments,
            polisher=mock_polisher,
            src_lang="zh-Hans",
            target_lang="vi",
            user_guidance="Ngược 2 nhân vật Triều Tịch và Lưu Giai Ngọc rồi, đảo lại",
            existing_context="Triều Tịch: Nữ; Lưu Giai Ngọc: Nam",
        )

        mock_polisher.generate_text.assert_called_once()
        _, kwargs = mock_polisher.generate_text.call_args
        system_msg = kwargs.get("system_msg", "")
        self.assertIn("PREVIOUS DRAFT OF CHARACTER PROFILES", system_msg)
        self.assertIn("Triều Tịch: Nữ; Lưu Giai Ngọc: Nam", system_msg)
        self.assertIn("Ngược 2 nhân vật Triều Tịch và Lưu Giai Ngọc rồi, đảo lại", system_msg)
        self.assertIn("SWAP their roles, genders, and addressing rules", system_msg)


class TestRollingBatchesMultiBatch(unittest.TestCase):
    @patch("app.translation.orchestrator.time.sleep")
    def test_multi_batch_calls_sleep_without_name_error(self, mock_sleep):
        orchestrator = TranslationOrchestrator()
        mock_polisher = MagicMock()
        mock_polisher.polish_batch.side_effect = [
            (["Dòng 1"], [], "gemini-3.5-flash"),
            (["Dòng 2"], [], "gemini-3.5-flash"),
        ]

        batches = [
            (["Line 1"], None, 100, None),
            (["Line 2"], None, 100, None),
        ]
        texts, providers, warnings = orchestrator._run_ai_batches_sequential(
            polisher=mock_polisher,
            batches=batches,
            src_lang="zh-Hans",
            target_lang="vi",
            style_instruction="",
            custom_system_prompt="",
            context_guidance="",
        )

        self.assertEqual(texts, ["Dòng 1", "Dòng 2"])
        mock_sleep.assert_called_once_with(0.25)


if __name__ == "__main__":
    unittest.main()


