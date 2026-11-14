"""Transport selection, native response handling and PC discovery regressions."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from capcut import transport, tts
from capcut.device import DEFAULT_DEVICE
import requests


class CapCutTransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CAPCUT_BACKEND": "web", "CAPCUT_PC_PATH": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_web_is_default_and_does_not_inspect_or_launch_pc(self):
        os.environ.pop("CAPCUT_BACKEND")
        with patch.object(transport, "find_capcut_pc_dll") as find, patch.object(transport, "_NativeBridge") as bridge:
            with transport.create_capcut_session(DEFAULT_DEVICE) as session:
                self.assertIs(type(session), requests.Session)
            find.assert_not_called()
            bridge.assert_not_called()

    def test_invalid_backend_does_not_silently_change_transport(self):
        os.environ["CAPCUT_BACKEND"] = "unknown"
        with self.assertRaisesRegex(ValueError, "CAPCUT_BACKEND"):
            transport.create_capcut_session(DEFAULT_DEVICE)

    @unittest.skipUnless(os.name == "nt", "Windows PC discovery")
    def test_selects_latest_numeric_version_and_accepts_folder_exe_or_dll(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ("9.7.0.4153", "10.0.0.1234"):
                folder = root / "Apps" / version
                folder.mkdir(parents=True)
                (folder / "sscronet.dll").touch()
            expected = (root / "Apps" / "10.0.0.1234" / "sscronet.dll").resolve()
            for value in (root, expected, expected.parent / "CapCut.exe", expected.parent):
                with self.subTest(path=value):
                    self.assertEqual(transport.find_capcut_pc_dll(str(value)), expected)

    @unittest.skipUnless(os.name == "nt", "Windows PC discovery")
    def test_invalid_explicit_path_does_not_use_another_installed_capcut(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "sscronet.dll"):
                transport.find_capcut_pc_dll(str(Path(directory) / "missing"))

    def test_pc_requires_installation_without_opening_or_falling_back(self):
        os.environ["CAPCUT_BACKEND"] = "pc"
        with patch.object(transport, "find_capcut_pc_dll", side_effect=FileNotFoundError("Missing CapCut PC")), \
                patch.object(transport, "_NativeBridge") as bridge:
            with self.assertRaisesRegex(FileNotFoundError, "Missing CapCut PC"):
                transport.create_capcut_session(DEFAULT_DEVICE)
            bridge.assert_not_called()

    def test_native_json_and_http_status_are_preserved_for_existing_error_handlers(self):
        bridge = MagicMock()
        bridge.request.return_value = {"status": 503, "headers": {"Content-Type": "application/json"},
                                       "body": b'{"ret":1014}'}
        with transport.CapCutPCSession(DEFAULT_DEVICE, Path("sscronet.dll")) as session, \
                patch.object(transport, "_bridge", return_value=bridge):
            response = session.post("https://editor-api-sg.capcutapi.com/lv/v1/common_task/new",
                                    data=b'{"hello":"world"}', headers={"appid": "359289"}, timeout=45)
            self.assertEqual(response.json(), {"ret": 1014})
            with self.assertRaises(requests.HTTPError) as error:
                response.raise_for_status()
            self.assertEqual(error.exception.response.status_code, 503)
            message = bridge.request.call_args.args[0]
            self.assertEqual(message["body"], b'{"hello":"world"}')
            self.assertEqual(message["headers"]["appid"], "359289")
            self.assertEqual(message["timeout"], 45)

    def test_stt_control_calls_use_native_but_vod_and_audio_keep_standard_http(self):
        native = MagicMock()
        native.request.return_value = {"status": 200, "body": b"{}"}
        with transport.CapCutPCSession(DEFAULT_DEVICE, Path("sscronet.dll")) as session, \
                patch.object(transport, "_bridge", return_value=native), \
                patch.object(requests.Session, "request", return_value="download") as direct:
            session.post("https://lv-pc-api-sinfonlinec.ulikecam.com/lv/v1/audio_subtitle/query", data=b"{}", timeout=1.5)
            self.assertEqual(native.request.call_args.args[0]["timeout"], 1.5)
            self.assertEqual(session.get("https://vod.bytedanceapi.com/", timeout=60), "download")
            self.assertEqual(session.get("https://audio.example.com/speech.mp3", timeout=60), "download")
            self.assertEqual(direct.call_count, 2)
            self.assertEqual(native.request.call_count, 1)

    def test_tts_factory_selects_pc_session_and_windows_profile(self):
        os.environ["CAPCUT_BACKEND"] = "pc"
        with patch.object(tts, "create_capcut_session", return_value=MagicMock()) as create:
            client = tts.CapCutTTSClient()
            self.assertEqual(client.device["device_platform"], "windows")
            self.assertEqual(create.call_args.args[0], client.device)

    def test_reuses_one_native_process_for_parallel_sessions_per_api_profile(self):
        with patch.dict(transport._BRIDGES, {}, clear=True), \
                patch.object(transport, "_NativeBridge") as factory:
            factory.return_value.closed = False
            factory.return_value.error = ""
            factory.return_value.process.is_alive.return_value = True
            first = transport._bridge(DEFAULT_DEVICE, Path("sscronet.dll"))
            second = transport._bridge(dict(DEFAULT_DEVICE, device_id="another-worker"), Path("sscronet.dll"))
            self.assertIs(first, second)
            factory.assert_called_once()

    def test_failed_native_process_is_closed_and_recreated_on_next_request(self):
        failed = MagicMock(closed=False, error="disconnected")
        key = ("sscronet.dll", DEFAULT_DEVICE["aid"])
        with patch.dict(transport._BRIDGES, {key: failed}, clear=True), \
                patch.object(transport, "_NativeBridge") as factory:
            self.assertIs(transport._bridge(DEFAULT_DEVICE, Path("sscronet.dll")), factory.return_value)
            failed.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
