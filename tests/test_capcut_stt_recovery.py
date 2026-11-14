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

    def http_error(self, status):
        response = stt.requests.Response()
        response.status_code = status
        return stt.requests.HTTPError('test HTTP failure', response=response)

    @patch.object(stt, '_extract_chunk_mp3')
    def test_upload_504_retries_once_before_recognition_without_extracting_again(self, extract):
        with patch.object(stt.time, 'sleep') as sleep, patch.object(stt, '_direct_vod_upload',
                side_effect=[self.http_error(504), 'store']) as upload, patch.object(stt, '_direct_query',
                return_value=[{'start_time': 1000, 'end_time': 2000, 'text': 'done'}]) as query:
            result = self.recognize()
        self.assertEqual(upload.call_count, 2)
        extract.assert_called_once()
        query.assert_called_once()
        sleep.assert_called_once_with(2.0)
        self.assertEqual(result[0]['start_time'], 101000)

    @patch.object(stt, '_extract_chunk_mp3')
    def test_upload_auth_error_is_not_retried_and_never_starts_recognition(self, extract):
        with patch.object(stt, '_direct_vod_upload', side_effect=self.http_error(403)) as upload, patch.object(stt, '_direct_query') as query:
            with self.assertRaises(stt.requests.HTTPError):
                self.recognize()
        upload.assert_called_once()
        query.assert_not_called()

    @patch.object(stt, '_extract_chunk_mp3')
    def test_repeated_upload_504_stops_after_two_attempts_without_splitting(self, extract):
        with patch.object(stt.time, 'sleep'), patch.object(stt, '_direct_vod_upload',
                side_effect=self.http_error(504)) as upload, patch.object(stt, '_direct_query') as query:
            with self.assertRaises(stt.requests.HTTPError):
                self.recognize()
        self.assertEqual(upload.call_count, 2)
        extract.assert_called_once()
        query.assert_not_called()

    @patch.object(stt, '_extract_chunk_mp3')
    @patch.object(stt, '_direct_vod_upload', return_value='store')
    def test_query_http_503_retries_without_reuploading(self, upload, extract):
        with patch.object(stt.time, 'sleep'), patch.object(stt, '_direct_query', side_effect=[
                self.http_error(503), [{'start_time': 0, 'end_time': 1000, 'text': 'done'}]]) as query:
            self.recognize()
        self.assertEqual(query.call_count, 2)
        upload.assert_called_once()

    def test_three_chunks_only_reupload_failed_chunk_and_keep_two_successful_results(self):
        import tempfile
        import threading
        from collections import Counter
        uploads = Counter()
        queries = Counter()
        lock = threading.Lock()
        def upload(device, session, path, creds):
            label = path.stem
            with lock:
                uploads[label] += 1
                fail = label == 'chunk_0000' and uploads[label] == 1
            if fail:
                raise self.http_error(504)
            return label
        def query(device, session, store, duration, *, language):
            self.assertEqual(language, 'auto')
            with lock:
                queries[store] += 1
            return [{'start_time': 1000, 'end_time': 2000, 'text': store}]
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'source.wav'
            audio.write_bytes(b'placeholder')
            with patch.object(stt, '_probe_duration_seconds', return_value=1651.22), \
                 patch.object(stt, '_direct_upload_credentials', return_value={}), \
                 patch.object(stt, '_plan_silence_aware_chunks', return_value=[(0,600),(598,600),(1196,455.22)]), \
                 patch.object(stt, '_extract_chunk_mp3'), \
                 patch.object(stt, '_direct_vod_upload', side_effect=upload), \
                 patch.object(stt, '_direct_query', side_effect=query), patch.object(stt.time, 'sleep'):
                result = stt.transcribe_audio_capcut(str(audio), chunk_seconds=600, max_workers=3)
        self.assertEqual(uploads, {'chunk_0000': 2, 'chunk_0001': 1, 'chunk_0002': 1})
        self.assertEqual(queries, {'chunk_0000': 1, 'chunk_0001': 1, 'chunk_0002': 1})
        self.assertEqual([row['start'] for row in result], [1,599,1197])

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
