"""Speech response, language, voice mapping and word timing regressions."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import requests
from capcut import stt, tts, stt_common
from capcut.api import checked_json, safe_detail


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode("utf-8")
    result.url = "https://example.com/test"
    return result


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        for target, value in (("monotonic", lambda: self.now), ("sleep", self.advance)):
            clock = patch.object(stt.time, target, side_effect=value)
            clock.start()
            self.addCleanup(clock.stop)
        env = patch.dict(os.environ, {"CAPCUT_BACKEND": "web"})
        env.start()
        self.addCleanup(env.stop)

    def advance(self, seconds):
        self.now += seconds

    def session(self, replies):
        session = MagicMock()
        session.post.side_effect = [response(data) for data in replies]
        return session


class SpeechResponseTests(ClockTests):
    def created(self):
        return {"data": {"bind_id": "bind", "tasks": [{"id": "job", "token": "private"}]}}

    def task(self, status="completed", **fields):
        return {"data": {"tasks": [dict(id="job", status=status, **fields)]}}

    def test_tts_success_aliases_use_correct_task_bind_id_and_complete_audio(self):
        for status in ("success", "succeed", "succeeded", "completed", "done"):
            with self.subTest(status=status):
                session = self.session([self.created(), self.task(status, payload=json.dumps({
                    "audio_subtitles": [{"speech_url": "https://example.com/speech.mp3", "code": 0}]}))])
                result = tts.CapCutTTSClient(session).synthesize(["hello"], "BV421_vivn_streaming")
                self.assertEqual(len(result), 1)
                body = json.loads(session.post.call_args.kwargs["data"])
                self.assertEqual(body["tasks"][0]["bind_id"], "bind")

    def test_query_api_error_fails_immediately_instead_of_waiting_for_timeout(self):
        session = self.session([self.created(), {"ret": 1014, "errmsg": "system busy", "log_id": "trace"}])
        with self.assertRaisesRegex(RuntimeError, "ret=1014.*request_id=trace"):
            tts.CapCutTTSClient(session).synthesize(["hello"], "BV421_vivn_streaming")
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(self.now, 0)

    def test_tts_terminal_failure_aliases_do_not_poll_forever(self):
        for status in ("failure", "cancelled", "canceled", "rejected", "expired"):
            with self.subTest(status=status):
                session = self.session([self.created(), self.task(status, err_code=4001, err_msg="denied")])
                with self.assertRaisesRegex(RuntimeError, "denied.*4001.*job=job"):
                    tts.CapCutTTSClient(session).synthesize(["hello"], "BV421_vivn_streaming")
                self.assertEqual(session.post.call_count, 2)

    def test_tts_does_not_accept_missing_audio_or_partial_batch_as_success(self):
        for items in ([], [{}], [{"speech_url": ""}], [{"speech_url": "https://example.com/a", "code": 42}]):
            with self.subTest(items=items):
                session = self.session([self.created(), self.task(payload={"audio_subtitles": items})])
                with self.assertRaises(RuntimeError):
                    tts.CapCutTTSClient(session).synthesize(["hello"], "BV421_vivn_streaming")
        session = self.session([self.created(), self.task(payload={"audio_subtitles": [{"speech_url": "https://example.com/a"}]})])
        with self.assertRaisesRegex(RuntimeError, "expected=2, received=1"):
            tts.CapCutTTSClient(session).synthesize(["one", "two"], "BV421_vivn_streaming")

    def test_unrelated_completed_task_is_not_returned(self):
        unrelated = {"data": {"tasks": [{"id": "another-job", "status": "done", "payload": {"audio_subtitles": []}}]}}
        session = self.session([self.created(), unrelated, self.task(payload={"audio_subtitles": [{"speech_url": "https://example.com/a"}]})])
        self.assertTrue(tts.CapCutTTSClient(session).synthesize(["hello"], "BV421_vivn_streaming"))
        self.assertEqual(session.post.call_count, 3)

    def test_common_stt_sends_language_and_returns_empty_completed_result_without_timeout(self):
        session = self.session([self.created(), self.task(payload={"utterances": []})])
        items = stt_common.query_common_stt(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session,
                    {"vid": "media", "md5": "checksum"}, 5000, "vi")
        self.assertEqual(items, [])
        body = json.loads(session.post.call_args_list[0].kwargs["data"])
        caption = json.loads(body["tasks"][0]["payload"])["cap_json"]
        self.assertEqual(caption["language"], "vi-VN")
        self.assertFalse(caption["use_translation"])

    def test_common_stt_timeout_respects_deadline_and_uses_same_job(self):
        session = self.session([self.created()] + [self.task("processing")] * 3)
        with self.assertRaisesRegex(TimeoutError, "job=job, polls=3, language=en-US"):
            stt_common.query_common_stt(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session,
                    {"vid": "media", "md5": "checksum"}, 5000, "en", timeout=3)
        self.assertEqual(self.now, 3)
        for call in session.post.call_args_list[1:]:
            self.assertLessEqual(call.kwargs["timeout"], 3)
            self.assertEqual(json.loads(call.kwargs["data"])["tasks"][0]["id"], "job")

    def test_common_stt_failed_task_or_invalid_payload_is_not_an_empty_success(self):
        for task in (self.task("failed", err_code=8, err_msg="denied"),
                     self.task(payload="not json"), self.task(payload={})):
            with self.subTest(task=task):
                session = self.session([self.created(), task])
                with self.assertRaises(RuntimeError):
                    stt_common.query_common_stt(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session,
                        {"vid": "media", "md5": "checksum"}, 5000, "en")
                self.assertEqual(session.post.call_count, 2)

    def test_direct_stt_sends_chinese_and_detects_terminal_empty_or_failed_jobs(self):
        for task in ({"status": "done", "utterances": []}, {"status": "failure", "err_code": 12, "err_msg": "failed"}):
            with self.subTest(task=task):
                session = self.session([{"data": {"id": "job"}}, {"data": task}])
                if task["status"] == "failure":
                    with self.assertRaisesRegex(RuntimeError, "code 12"):
                        stt._direct_query(stt._build_stt_device(), session, "store", 5000, language="zh")
                else:
                    self.assertEqual(stt._direct_query(stt._build_stt_device(), session, "store", 5000, language="zh"), [])
                self.assertEqual(json.loads(session.post.call_args_list[0].kwargs["data"])["language"], "zh-CN")

    def test_direct_stt_waits_for_completion_when_processing_response_contains_partial_text(self):
        partial = {"text": "partial"}
        completed = {"text": "complete"}
        session = self.session([{"data": {"id": "job"}}, {"data": {"status": "processing", "utterances": [partial]}},
                                {"data": {"status": "completed", "utterances": [completed]}}])
        result = stt._direct_query(stt._build_stt_device(), session, "store", 5000)
        self.assertEqual(result, [completed])
        self.assertEqual(session.post.call_count, 3)

    def test_non_json_and_malformed_payloads_are_actionable_without_response_body_leaks(self):
        bad = response(None)
        bad._content = b"<html>private server response</html>"
        with self.assertRaisesRegex(RuntimeError, "STT query: invalid JSON") as error:
            checked_json(bad, "STT query")
        self.assertNotIn("private", str(error.exception))
        for data in ([], {"data": "invalid"}):
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                checked_json(response(data), "query")
        with self.assertRaises(requests.HTTPError):
            checked_json(response({}, 503), "query")
        for detail in ('token=private', '{"token":"private"}', "Bearer private", "https://example.com/?token=private", {"token": "private"}):
            with self.subTest(detail=detail):
                self.assertNotIn("private", safe_detail(detail))


class VoiceAndWordTests(unittest.TestCase):
    def test_common_upload_uses_service_domain_and_commits_media_vid_and_checksum(self):
        session = MagicMock()
        device = stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE)
        credentials = {"access_key_id": "test", "secret_access_key": "test", "session_token": "test",
                       "domain": "vod.example.com", "space_name": "test-space"}
        session.post.return_value = response({"data": credentials})
        actual = stt._direct_upload_credentials(device, session, common_task=True)
        self.assertEqual(actual["domain"], "vod.example.com")
        self.assertEqual(json.loads(session.post.call_args.kwargs["data"])["biz"], "cc_pc_text_recognize")
        session.get.return_value = response({"Result": {"InnerUploadAddress": {"UploadNodes": [{
            "SessionKey": "test-session", "UploadHost": "upload.example.com", "StoreInfos": [{
                "StoreUri": "test-uri", "UploadID": "test-upload", "Auth": "test-auth"}]}]}}})
        session.post.reset_mock()
        session.post.side_effect = [response({}), response({}), response({"Result": {"Results": [{
            "Vid": "committed-media", "VideoMeta": {"Md5": "server-checksum"}}]}})]
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.mp3"
            audio.write_bytes(b"audio")
            result = stt._direct_vod_upload(device, session, audio, actual, common_task=True)
        self.assertEqual(result, {"vid": "committed-media", "md5": "server-checksum"})
        self.assertEqual(session.post.call_count, 3)
        self.assertIn("https://vod.example.com/", session.get.call_args.args[0])
        self.assertIn("CommitUploadInner", session.post.call_args.args[0])

    def test_common_upload_missing_vid_does_not_start_an_invalid_recognition_job(self):
        session = MagicMock()
        session.get.return_value = response({"Result": {"InnerUploadAddress": {"UploadNodes": [{
            "SessionKey": "session", "UploadHost": "upload.example.com", "StoreInfos": [{
                "StoreUri": "uri", "UploadID": "id", "Auth": "auth"}]}]}}})
        session.post.side_effect = [response({}), response({}), response({"Result": {"Results": []}})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.mp3"
            path.write_bytes(b"audio")
            with self.assertRaisesRegex(RuntimeError, "no media VID"):
                stt._direct_vod_upload(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session, path,
                    {"access_key_id": "test", "secret_access_key": "test", "session_token": "test"}, common_task=True)

    def test_unknown_or_incomplete_voice_is_rejected_before_any_network_request(self):
        session = MagicMock()
        with self.assertRaises(tts.CapCutVoiceError):
            tts.CapCutTTSClient(session).synthesize(["hello"], "not-a-known-voice")
        session.post.assert_not_called()
        catalog = [{"provider_voice": "known", "name": "Known (CapCut)", "resource_id": ""}]
        with patch.object(tts, "list_capcut_voices", return_value=catalog), self.assertRaises(tts.CapCutVoiceError):
            tts._resolve_resource_id("known")

    def test_voice_can_be_resolved_by_service_id_display_name_or_resource_id(self):
        entry = tts.list_capcut_voices()[0]
        for value in (entry["id"], entry["provider_voice"], entry["name"].removesuffix(" (CapCut)"), entry["resource_id"]):
            with self.subTest(value=value):
                self.assertEqual(tts._resolve_voice(value)["provider_voice"], entry["provider_voice"])

    def test_common_stt_retry_reuses_committed_upload_and_shifts_each_word_once(self):
        original = [{"text": "hello", "start_time": 100, "end_time": 1000,
                     "words": [{"text": "hello", "start_time": 100, "end_time": 1000}]}]
        pristine = copy.deepcopy(original)
        with patch.object(stt, "_extract_chunk_mp3"), \
             patch.object(stt, "_direct_vod_upload", return_value={"vid": "media", "md5": "sum"}) as upload, \
             patch.object(stt, "query_common_stt", side_effect=[requests.Timeout(), original]) as query, \
             patch.object(stt.time, "sleep"):
            result = stt._recognize_chunk("audio", "tmp", "chunk", MagicMock(), MagicMock(), {}, 100, 300, language="en-US")
        upload.assert_called_once()
        self.assertTrue(upload.call_args.kwargs["common_task"])
        self.assertEqual(query.call_count, 2)
        self.assertEqual(result[0]["words"][0]["start_time"], 100100)
        self.assertEqual(original, pristine)

    def test_fragment_upgrade_replaces_stale_words_and_clips_to_normalized_timeline(self):
        chunks = [(0, [{"text": "hello", "start_time": 100, "end_time": 1000,
                        "words": [{"text": "stale", "start_time": 100, "end_time": 1000}]}]),
                  (1, [{"text": "hello world", "start_time": 200, "end_time": 1500,
                        "words": [{"text": "hello", "start_time": 200, "end_time": 800},
                                  {"text": "world", "start_time": 800, "end_time": 1600}]}])]
        merged = stt._merge_chunks(chunks)
        self.assertEqual([w["text"] for w in merged[0]["words"]], ["hello", "world"])
        self.assertEqual(merged[0]["words"][-1]["end_time"], 1500)

    def test_language_and_words_survive_transcription_and_project_conversion(self):
        from app.core.models.segment import Segment
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.touch()
            with patch.object(stt, "_probe_duration_seconds", return_value=2), \
                 patch.object(stt, "_direct_upload_credentials", return_value={}) as credentials, \
                 patch.object(stt, "_recognize_chunk", return_value=[{"text": "hello", "start_time": 500, "end_time": 1500,
                     "words": [{"text": "hello", "start_time": 400, "end_time": 1600}]}]) as recognize:
                result = stt.transcribe_audio_capcut(str(audio), language="en")
        self.assertTrue(credentials.call_args.kwargs["common_task"])
        self.assertEqual(recognize.call_args.kwargs["language"], "en-US")
        expected = [{"text": "hello", "start": 0.5, "end": 1.5}]
        self.assertEqual(result[0]["words"], expected)
        saved = Segment.from_dict(result[0]).to_dict()
        self.assertEqual(Segment.from_dict(saved).to_subtitle_dict()["words"], expected)

    def test_small_batch_with_missing_audio_no_longer_reports_success(self):
        jobs = [{"text": "one", "wav_path": "one.wav"}, {"text": "two", "wav_path": "two.wav"}]
        with patch.object(tts.CapCutTTSClient, "synthesize", side_effect=RuntimeError("batch failed")), \
             patch.object(tts, "synthesize_capcut_tts_wav_16k_mono", side_effect=["one.wav", RuntimeError("missing")]):
            with self.assertRaisesRegex(RuntimeError, "failed for 1/2 segments"):
                tts.synthesize_capcut_tts_batch(jobs, voice_id="BV421_vivn_streaming")


if __name__ == "__main__":
    unittest.main()
