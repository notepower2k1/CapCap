from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from capcut import stt


class CapCutQueryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.clock = patch.object(stt.time, "monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        sleep = patch.object(stt.time, "sleep", side_effect=self.advance)
        sleep.start()
        self.addCleanup(sleep.stop)

    def advance(self, seconds):
        self.now += seconds

    def session(self, replies):
        session = MagicMock()
        def post(url, **kwargs):
            result = MagicMock()
            result.json.return_value = next(replies)
            return result
        session.post.side_effect = post
        return session

    def test_five_minute_job_can_complete_after_old_120_second_limit_without_resubmit(self):
        replies = iter([{"data": {"id": "test-job"}}] + [{"ret": 0, "data": {}}] * 9 +
                       [{"ret": 0, "data": {"utterances": [{"text": "done"}]}}])
        session = self.session(replies)
        result = stt._direct_query(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session, "test-store", 300000, poll_interval=15)
        self.assertEqual(result, [{"text": "done"}])
        self.assertGreater(self.now, 120)
        self.assertEqual(sum('/submit?' in call.args[0] for call in session.post.call_args_list), 1)

    def test_timeout_has_job_duration_poll_diagnostics_and_respects_deadline(self):
        import itertools
        responses = itertools.chain([{"data": {"id": "test-job"}}], itertools.repeat({"ret": 0, "data": {}}))
        session = self.session(responses)
        with self.assertRaisesRegex(TimeoutError, 'job=test-job, audio_seconds=10.00, polls=3'):
            stt._direct_query(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session, "test-store", 10000, timeout=3, poll_interval=1)
        self.assertEqual(self.now, 3)
        for call in session.post.call_args_list[1:]:
            self.assertLessEqual(call.kwargs['timeout'], 3)

    def test_service_error_is_not_retried_or_accepted_as_success(self):
        session = self.session(iter([{"data": {"id": "job"}}, {"ret": 403, "errmsg": "denied", "data": {"utterances": [{"text": "invalid"}]}}]))
        with self.assertRaisesRegex(RuntimeError, 'ret=403'):
            stt._direct_query(stt.DeviceConfig.from_dict(stt.DEFAULT_DEVICE), session, "test-store", 300000)
        self.assertEqual(session.post.call_count, 2)


class CapCutChunkRecoveryTests(unittest.TestCase):
    def recognize(self):
        return stt._recognize_chunk('source.wav', 'test-dir', 'chunk_1', MagicMock(), MagicMock(), {}, 100, 300)

    @patch.object(stt, '_extract_chunk_mp3')
    @patch.object(stt, '_direct_vod_upload', return_value='store')
    def test_only_failed_chunk_is_split_and_offsets_are_preserved(self, upload, extract):
        with patch.object(stt.time, 'sleep'), patch.object(stt, '_direct_query', side_effect=[TimeoutError('slow'), TimeoutError('still slow'),
                 [{'start_time': 1000, 'end_time': 2000, 'text': 'first'}],
                 [{'start_time': 3000, 'end_time': 4000, 'text': 'second'}]]) as query:
            result = self.recognize()
        self.assertEqual(query.call_count, 4)
        self.assertEqual([row['start_time'] for row in result], [101000, 252000])
        self.assertEqual([call.args[2:] for call in extract.call_args_list], [(100, 300), (100.0, 151.0), (249.0, 151.0)])

    @patch.object(stt, '_extract_chunk_mp3')
    @patch.object(stt, '_direct_vod_upload', return_value='store')
    def test_second_timeout_does_not_split_forever(self, upload, extract):
        with patch.object(stt.time, 'sleep'), patch.object(stt, '_direct_query', side_effect=TimeoutError('slow')) as query:
            with self.assertRaises(TimeoutError):
                self.recognize()
        self.assertEqual(query.call_count, 4)

    @patch.object(stt, '_extract_chunk_mp3')
    @patch.object(stt, '_direct_vod_upload', return_value='store')
    def test_retry_success_uses_same_upload_and_keeps_original_offset(self, upload, extract):
        for error in [TimeoutError('slow'), stt.requests.Timeout('read timeout'), stt.requests.ConnectionError('network')]:
            with self.subTest(error=type(error).__name__), patch.object(stt.time, 'sleep') as sleep, patch.object(
                stt, '_direct_query', side_effect=[error, [{'start_time': 1000, 'end_time': 2000, 'text': 'done'}]]
            ) as query:
                upload.reset_mock()
                extract.reset_mock()
                result = self.recognize()
                self.assertEqual(query.call_count, 2)
                self.assertEqual(query.call_args_list[0], query.call_args_list[1])
                upload.assert_called_once()
                extract.assert_called_once()
                sleep.assert_called_once_with(2.0)
                self.assertEqual(result[0]['start_time'], 101000)

    @patch.object(stt, '_extract_chunk_mp3')
    @patch.object(stt, '_direct_vod_upload', return_value='store')
    def test_auth_error_is_not_treated_as_slow_recognition(self, upload, extract):
        with patch.object(stt, '_direct_query', side_effect=RuntimeError('denied')) as query:
            with self.assertRaises(RuntimeError):
                self.recognize()
        self.assertEqual(query.call_count, 1)
