import io
import os
import sys
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ui"), str(ROOT / "app"), str(ROOT)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
from ui.views.launcher import LauncherWindow
from ui.i18n import get_language, set_language


class LauncherGpuDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.gpu_name_patch = patch.object(LauncherWindow, "_gpu_name", "", create=True)
        self.gpu_name_patch.start()
        self.language = get_language()
        set_language("en")
        self.host = QWidget()
        self.service = MagicMock()
        self.service.is_requirement_met.return_value = True
        self.ui = SimpleNamespace(
            _resource_service=lambda: self.service,
            _gpu_label=QLabel(self.host), _gpu_diagnostic_label=QLabel(self.host),
            gpu_btn=QPushButton(self.host), new_btn=QPushButton(self.host),
            _missing_label=QLabel(self.host), selected_device="cpu", grid=MagicMock(),
        )
        self.ui.grid.count.return_value = 0
        self.ui._launch_resource_state = lambda service: (True, [], [])
        self.ui._detect_gpu_with_cuda = lambda: LauncherWindow._detect_gpu_with_cuda(self.ui)
        self.ui._update_gpu_label = lambda *args: LauncherWindow._update_gpu_label(self.ui, *args)
        self.torch = SimpleNamespace(
            __version__="test", version=SimpleNamespace(cuda=None),
            cuda=SimpleNamespace(is_available=lambda: False),
        )

    def tearDown(self):
        self.gpu_name_patch.stop()
        self.host.deleteLater()
        set_language(self.language)

    def detect(self, *, result=None, error=None):
        with patch("subprocess.run", **({"side_effect": error} if error else {"return_value": result})), \
                patch.dict(sys.modules, {"torch": self.torch}), redirect_stdout(io.StringIO()) as output:
            data = LauncherWindow._detect_gpu_with_cuda(self.ui)
        LauncherWindow._update_gpu_label(self.ui, *data)
        return data, output.getvalue()

    def test_missing_smi_and_cpu_torch_show_driver_and_app_recovery(self):
        data, log = self.detect(error=FileNotFoundError("nvidia-smi"))
        self.assertEqual(data, (False, "", False))
        self.assertEqual(self.ui._gpu_diagnostics["reason"], "smi_missing")
        self.assertEqual(self.ui._gpu_diagnostics["torch"], "cpu_build")
        text = self.ui._gpu_diagnostic_label.text()
        self.assertIn("NVIDIA driver", text)
        self.assertIn("GPU-enabled CapCap", text)
        self.assertIn("CPU mode", text)
        self.assertIn("torch CUDA=None", log)
        self.assertEqual(self.ui.gpu_btn.toolTip(), text)

    def test_command_failure_and_timeout_are_distinct(self):
        self.torch.version.cuda = "12.6"
        for result, error, reason in (
            (SimpleNamespace(returncode=9, stdout="", stderr="driver failed"), None, "smi_failed"),
            (None, subprocess.TimeoutExpired("nvidia-smi", 10), "smi_timeout"),
        ):
            with self.subTest(reason=reason):
                self.detect(result=result, error=error)
                self.assertEqual(self.ui._gpu_diagnostics["reason"], reason)
                self.assertEqual(self.ui._gpu_diagnostics["torch"], "no_device")
                self.assertFalse(self.ui._gpu_diagnostic_label.isHidden())
                self.assertNotIn("GPU-enabled CapCap", self.ui._gpu_diagnostic_label.text())

    def test_missing_pack_and_check_failure_have_different_messages(self):
        result = SimpleNamespace(returncode=0, stdout="GTX 1050, 2048\n")
        self.service.is_requirement_met.return_value = False
        data, _ = self.detect(result=result)
        self.assertEqual(data, (True, "GTX 1050 (2GB)", False))
        self.assertEqual(self.ui._gpu_diagnostics["reason"], "pack_missing")
        self.assertIn("Manage Resources", self.ui._gpu_diagnostic_label.text())
        self.service.is_requirement_met.side_effect = OSError("cannot inspect pack")
        self.detect(result=result)
        self.assertEqual(self.ui._gpu_diagnostics["reason"], "pack_check_failed")
        self.assertIn("could not check", self.ui._gpu_diagnostic_label.text())

    def test_torch_fallback_success_hides_recovered_smi_error(self):
        self.torch.cuda = SimpleNamespace(
            is_available=lambda: True, get_device_name=lambda index: "GTX 1050",
            get_device_properties=lambda index: SimpleNamespace(total_memory=2 * 1024**3),
        )
        data, _ = self.detect(error=FileNotFoundError("nvidia-smi"))
        self.assertEqual(data, (True, "GTX 1050 (2GB)", True))
        self.assertTrue(self.ui._gpu_diagnostic_label.isHidden())
        self.assertEqual(self.ui.gpu_btn.toolTip(), "")

    def test_missing_vram_metadata_does_not_hide_cuda_device(self):
        self.torch.cuda = SimpleNamespace(
            is_available=lambda: True, get_device_name=lambda index: "GTX 1050",
            get_device_properties=MagicMock(side_effect=RuntimeError("VRAM query unavailable")),
        )
        data, log = self.detect(error=FileNotFoundError("nvidia-smi"))
        self.assertEqual(data, (True, "GTX 1050", True))
        self.assertTrue(self.ui._gpu_diagnostic_label.isHidden())
        self.assertIn("GPU metadata", log)

    def test_torch_import_and_runtime_errors_are_logged(self):
        for module, expected in (
            (None, "import_error"),
            (SimpleNamespace(cuda=SimpleNamespace(is_available=MagicMock(side_effect=RuntimeError("CUDA DLL load failed")))), "runtime_error"),
        ):
            self.torch = module
            _, log = self.detect(error=FileNotFoundError("nvidia-smi"))
            self.assertEqual(self.ui._gpu_diagnostics["torch"], expected)
            self.assertIn("PyTorch", log)
            self.assertIn("GPU-enabled CapCap", self.ui._gpu_diagnostic_label.text())

    def test_vietnamese_message_and_gpu_recovery_do_not_block_cpu(self):
        set_language("vi")
        with patch("subprocess.run", side_effect=FileNotFoundError("nvidia-smi")), \
                patch.dict(sys.modules, {"torch": self.torch}), redirect_stdout(io.StringIO()):
            LauncherWindow._validate_resources_for_device(self.ui)
        self.assertTrue(self.ui.new_btn.isEnabled())
        self.assertFalse(self.ui.gpu_btn.isEnabled())
        self.assertIn("NVIDIA", self.ui._gpu_diagnostic_label.text())
        self.assertNotIn("was not found", self.ui._gpu_diagnostic_label.text())
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="GTX 1050, 2048\n")), \
                redirect_stdout(io.StringIO()):
            LauncherWindow._validate_resources_for_device(self.ui)
        self.assertTrue(self.ui.gpu_btn.isEnabled())
        self.assertTrue(self.ui._gpu_diagnostic_label.isHidden())
        self.assertEqual(self.ui.gpu_btn.toolTip(), "")


if __name__ == "__main__":
    unittest.main()
