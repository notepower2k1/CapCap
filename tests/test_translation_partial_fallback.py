import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
from app.translation.orchestrator import TranslationOrchestrator
from app.translation.errors import TranslationProviderError


class TestPartialFallback(unittest.TestCase):
    def run_case(self, *, cancel=False, callback=True, malformed=False, google_fails=False):
        orch = TranslationOrchestrator()
        segments = [dict(start=i, end=i+1, text=f"source-{i}") for i in range(6)]
        polisher = MagicMock()
        polisher.is_configured.return_value = True
        polisher.polish_batch.side_effect = [(["AI-0", "AI-1"], [], "browser_ai"),
            InterruptedError("canceled") if cancel else
            ((["incomplete"], [], "browser_ai") if malformed else TranslationProviderError("page error"))]
        batches = [(list(map(lambda s:s["text"], segments[i:i+2])), None, 1024, None) for i in range(0,6,2)]
        updates = []
        statuses = []
        google_effect = RuntimeError("google failed") if google_fails else lambda texts, **_: ["GOOGLE-"+t for t in texts]
        with patch.dict(os.environ, {"OPENAI_PROVIDER":"browser_ai", "CAPCAP_AUTO_TRANSLATION_CONTEXT":"0"}), \
             patch.object(orch, "_resolve_ai_provider", return_value=("browser_ai",polisher)), \
             patch.object(orch, "_build_ai_batches", return_value=(batches, False)), \
             patch.object(orch.google_web, "translate_batch", side_effect=google_effect) as google, \
             patch.object(orch.bing_web, "translate_batch", side_effect=lambda texts, **_: ["BING-"+t for t in texts]) as bing, \
             patch("app.translation.orchestrator.time.sleep"):
            result = orch.translate_segments(segments=segments, src_lang="en", ms_batch_size=2,
                batch_callback=(lambda start,cues: updates.append((start,cues))) if callback else None,
                status_callback=lambda provider,msg: statuses.append(provider))
        return result, google, bing, updates, statuses

    def test_keeps_ai_prefix_and_falls_back_only_remaining_cues(self):
        result, google, _, updates, statuses = self.run_case()
        self.assertTrue(result.success)
        self.assertTrue(result.used_fallback)
        self.assertEqual([s["text"] for s in result.segments[:2]], ["AI-0","AI-1"])
        self.assertEqual([s["provider"] for s in result.segments], ["browser_ai"]*2+["google-web"]*4)
        self.assertEqual([s["polished"] for s in result.segments], [True]*2+[False]*4)
        self.assertEqual([s["start"] for s in result.segments], list(range(6)))
        self.assertEqual([call.args[0] for call in google.call_args_list], [["source-2","source-3"],["source-4","source-5"]])
        self.assertEqual([start for start,_ in updates], [0,2,4])
        self.assertIn("Google Translate", statuses)
        self.assertIn("browser_ai", result.primary_provider)
        self.assertIn("google-web", result.primary_provider)

    def test_retains_results_without_ui_callback(self):
        result, google, *_ = self.run_case(callback=False)
        self.assertTrue(result.success)
        self.assertEqual(result.segments[0]["text"], "AI-0")
        self.assertEqual(sum(len(c.args[0]) for c in google.call_args_list),4)

    def test_invalid_batch_is_not_saved_as_success(self):
        result, google, *_ = self.run_case(malformed=True)
        self.assertTrue(result.success)
        self.assertEqual(result.segments[2]["text"], "GOOGLE-source-2")
        self.assertEqual(sum(len(c.args[0]) for c in google.call_args_list),4)

    def test_cancel_does_not_call_free_provider(self):
        result, google, bing, *_ = self.run_case(cancel=True)
        self.assertFalse(result.success)
        self.assertIn("canceled", result.errors)
        google.assert_not_called()
        bing.assert_not_called()

    def test_google_to_bing_fallback_preserves_ai(self):
        result, _, bing, *_ = self.run_case(google_fails=True)
        self.assertTrue(result.success)
        self.assertEqual(result.segments[0]["text"], "AI-0")
        self.assertEqual(result.segments[2]["provider"], "bing-web")
        self.assertEqual(sum(len(c.args[0]) for c in bing.call_args_list),4)

    def test_gaps_from_out_of_order_batches_keep_original_callback_offsets(self):
        orch = TranslationOrchestrator()
        segments = [dict(start=i,end=i+1,text=str(i)) for i in range(6)]
        completed = {i:dict(segments[i], text="AI",provider="openai",polished=True) for i in [2,3]}
        updates=[]
        with patch.object(orch.google_web,"translate_batch",side_effect=lambda texts,**_: ["G"+t for t in texts]) as google:
            result=orch._fallback_untranslated_segments(segments=segments,source_texts=[s["text"] for s in segments],
                completed=completed,normalized_src="en",target_lang="vi",ms_batch_size=50,
                batch_callback=lambda start,cues: updates.append(start),warnings=[],status_callback=None,is_canceled=None)
        self.assertTrue(result.success)
        self.assertEqual([call.args[0] for call in google.call_args_list],[["0","1"],["4","5"]])
        self.assertEqual(updates,[0,4])
        self.assertEqual([s["text"] for s in result.segments],["G0","G1","AI","AI","G4","G5"])

    def test_parallel_successes_in_same_completion_group_survive_an_error(self):
        from concurrent.futures import Future
        orch = TranslationOrchestrator()
        segments = [dict(start=i,end=i+1,text=str(i)) for i in range(6)]
        batches = [([str(i),str(i+1)],None,1024,None) for i in range(0,6,2)]
        def submit(*args, **kwargs):
            future = Future()
            offset = kwargs["start_offset"]
            if offset == 2:
                future.set_exception(TranslationProviderError("failed middle batch"))
            else:
                future.set_result((["AI-"+t for t in kwargs["source_texts"]],[],"openai"))
            return future
        executor = MagicMock()
        executor.submit.side_effect = submit
        polisher = MagicMock()
        with patch.dict(os.environ,{"CAPCAP_TRANSLATION_PARALLEL_BATCHES":"1","CAPCAP_AUTO_TRANSLATION_CONTEXT":"0"}), \
             patch.object(orch,"_resolve_ai_provider",return_value=("openai",polisher)), \
             patch.object(orch,"_build_ai_batches",return_value=(batches,False)), \
             patch("app.translation.orchestrator.concurrent.futures.ThreadPoolExecutor",return_value=executor), \
             patch("app.translation.orchestrator.concurrent.futures.wait",side_effect=lambda fs,**_: (set(fs),set())), \
             patch.object(orch.google_web,"translate_batch",side_effect=lambda texts,**_: ["G-"+t for t in texts]) as google:
            result=orch.translate_segments(segments=segments,src_lang="en")
        self.assertTrue(result.success)
        self.assertEqual([s["text"] for s in result.segments],["AI-0","AI-1","G-2","G-3","AI-4","AI-5"])
        self.assertEqual(google.call_args.args[0],["2","3"])

    def test_full_context_recovery_keeps_successful_ordered_batches(self):
        orch = TranslationOrchestrator()
        segments = [dict(start=i,end=i+1,text=str(i)) for i in range(6)]
        ordered = [([str(i),str(i+1)],None,1024,None) for i in range(0,6,2)]
        polisher = MagicMock()
        polisher.polish_batch.side_effect = [(["incomplete"],[],"browser_ai"),
            (["AI-0","AI-1"],[],"browser_ai"),TranslationProviderError("failed retry")]
        with patch.dict(os.environ,{"CAPCAP_AUTO_TRANSLATION_CONTEXT":"0"}), \
             patch.object(orch,"_resolve_ai_provider",return_value=("browser_ai",polisher)), \
             patch.object(orch,"_build_ai_batches",side_effect=[([([s["text"] for s in segments],None,1024,None)],True),(ordered,False)]), \
             patch.object(orch.google_web,"translate_batch",side_effect=lambda texts,**_: ["G-"+t for t in texts]) as google, \
             patch("app.translation.orchestrator.time.sleep"):
            result=orch.translate_segments(segments=segments,src_lang="en")
        self.assertTrue(result.success)
        self.assertEqual([s["text"] for s in result.segments],["AI-0","AI-1","G-2","G-3","G-4","G-5"])
        self.assertEqual(google.call_args.args[0],["2","3","4","5"])
