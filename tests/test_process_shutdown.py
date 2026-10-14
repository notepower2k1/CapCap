import ctypes
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows process lifecycle")
class TestWindowsProcessShutdown(unittest.TestCase):
    def test_window_close_and_abrupt_exit_stop_owned_process_tree(self):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        for abrupt in (False, True):
            with self.subTest(abrupt=abrupt), tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
                work = Path(folder).resolve()
                self.assertTrue(work.is_relative_to(ROOT / "temp"))
                worker_script = work / "worker.py"
                worker_script.write_text(textwrap.dedent("""
                    import json, os, subprocess, sys, time
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = subprocess.SW_HIDE
                    child = subprocess.Popen([sys.argv[1], '-hide_banner', '-loglevel', 'error',
                        '-re', '-f', 'lavfi', '-i', 'color=s=32x32:r=5', '-f', 'null', '-'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)
                    with open(sys.argv[2], 'w') as stream:
                        json.dump({'worker': os.getpid(), 'ffmpeg': child.pid}, stream)
                    time.sleep(60)
                """), encoding="utf-8")
                gui_script = work / "gui.py"
                gui_script.write_text(textwrap.dedent("""
                    import ctypes, json, os, subprocess, sys, time
                    from pathlib import Path
                    from unittest.mock import MagicMock, patch
                    root = Path(sys.argv[1])
                    for path in (root, root/'app', root/'ui'):
                        sys.path.insert(0, str(path))
                    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
                    from ui.gui import _manage_child_processes, VideoTranslatorGUI
                    from PySide6.QtCore import QThread, QTimer
                    from PySide6.QtWidgets import QApplication
                    assert _manage_child_processes(), 'Windows job initialization failed'
                    app = QApplication([])
                    with patch('main_window.QTimer.singleShot'), patch('main_window.apply_windows_dark_title_bar_impl'):
                        gui = VideoTranslatorGUI()
                    gui.save_user_settings = MagicMock()
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = subprocess.SW_HIDE
                    worker = subprocess.Popen([sys.executable, sys.argv[2], str(root/'bin/ffmpeg/ffmpeg.exe'),
                        sys.argv[3]], startupinfo=startup)
                    gui.pipeline_controller.local_worker_process = worker
                    deadline = time.monotonic() + 10
                    while not Path(sys.argv[3]).exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    assert Path(sys.argv[3]).exists(), 'Worker did not start'
                    render_pid_file = Path(sys.argv[3]).with_name('render_pid.json')
                    class RenderThread(QThread):
                        def run(self):
                            process = subprocess.Popen([str(root/'bin/ffmpeg/ffmpeg.exe'),
                                '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
                                '-i', 'color=s=32x32:r=5', '-f', 'null', '-'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)
                            render_pid_file.write_text(json.dumps({'render':process.pid}))
                            process.wait()
                    gui.export_thread = RenderThread(gui)
                    gui.export_thread.start()
                    deadline = time.monotonic() + 10
                    while not render_pid_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    assert render_pid_file.exists(), 'Render thread did not start'
                    gui.show()
                    if sys.argv[4] == 'True':
                        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                        kernel.GetCurrentProcess.restype = ctypes.c_void_p
                        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                        QTimer.singleShot(300, lambda: kernel.TerminateProcess(kernel.GetCurrentProcess(), 9))
                    else:
                        QTimer.singleShot(300, gui.close)
                    code = app.exec()
                    os._exit(code)
                """), encoding="utf-8")
                pids_path = work / "pids.json"
                process = subprocess.Popen(
                    [sys.executable, str(gui_script), str(ROOT), str(worker_script), str(pids_path), str(abrupt)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, startupinfo=startup,
                )
                try:
                    output, _ = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, 9 if abrupt else 0, output.decode(errors="replace"))
                    pids = json.loads(pids_path.read_text())
                    pids.update(json.loads((work / "render_pid.json").read_text()))
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    for name, pid in pids.items():
                        handle = kernel.OpenProcess(0x00100000, False, pid)
                        if handle:
                            try:
                                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0, f"{name} still running: {pid}")
                            finally:
                                kernel.CloseHandle(handle)
                finally:
                    if process.poll() is None:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)


if __name__ == "__main__":
    unittest.main()
