import ctypes
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ISCC = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe"


@unittest.skipUnless(os.name == "nt" and ISCC.exists(), "Inno Setup on Windows")
class TestInstallerProcessGuard(unittest.TestCase):
    def test_running_worker_blocks_install_and_closed_worker_allows_it(self):
        self._run_guard_check(False)

    def test_worker_started_after_wizard_opens_blocks_file_copy(self):
        self._run_guard_check(True)

    def test_gui_mutex_blocks_install_until_the_gui_has_exited(self):
        self._run_guard_check(False, use_mutex=True)

    def _run_guard_check(self, start_after_wizard, use_mutex=False):
        # Compile the production guard into an isolated installer with one harmless file.
        suffix = uuid4().hex
        alias = ROOT / "dist" / "CapCap" / f"CapCapGuardProbe_{suffix}.exe"
        os.link(ROOT / "dist" / "CapCap" / "CapCap.exe", alias)
        self.addCleanup(alias.unlink, missing_ok=True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
            work = Path(folder).resolve()
            self.assertTrue(work.is_relative_to(ROOT / "temp"))
            code = (ROOT / "installer/CapCap_Setup.iss").read_text(encoding="utf-8").split("[Code]", 1)[1]
            (work / "marker.txt").write_text("installer guard test", encoding="utf-8")
            pids_file = work / "worker.json"
            launch = work / "launch_worker.py"
            launch.write_text(textwrap.dedent(f"""
                import json, os, subprocess
                env = os.environ.copy()
                env['CAPCAP_RUNTIME_PROFILE'] = 'local'
                env['CAPCAP_DEVICE'] = 'cpu'
                env['CAPCAP_REMOTE_PRELOAD_MODELS'] = '0'
                env['CAPCAP_REMOTE_API_PORT'] = '0'
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
                process = subprocess.Popen([{str(alias)!r}, '--worker-server'], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)
                with open({str(pids_file)!r}, 'w') as stream:
                    json.dump({{'pid':process.pid}}, stream)
            """), encoding="utf-8")
            fixture = textwrap.dedent(f"""
                #define MyAppExeName "{alias.name}"
                #define MyAppMutexName "Local\\CapCap.InstallerGuardTest.{suffix}"
                [Setup]
                AppName=CapCap Guard Test
                AppVersion=1
                AppId=CapCapGuardTest.{suffix}
                AppMutex={{#MyAppMutexName}}
                DefaultDirName={work / 'installed'}
                OutputDir={work}
                OutputBaseFilename=guard_setup
                PrivilegesRequired=lowest
                Uninstallable=no
                CreateUninstallRegKey=no
                DisableProgramGroupPage=yes
                UsePreviousAppDir=no
                [Files]
                Source: "{work / 'marker.txt'}"; DestDir: "{{app}}"
                [Code]
            """) + code
            if start_after_wizard:
                arguments = subprocess.list2cmdline([str(launch)]).replace("'", "''")
                executable = sys.executable.replace("'", "''")
                fixture += textwrap.dedent(f"""
                    procedure InitializeWizard;
                    var
                      ResultCode: Integer;
                    begin
                      Exec('{executable}', '{arguments}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
                    end;
                """)
            script = work / "guard.iss"
            script.write_text(fixture, encoding="utf-8")
            compile_result = subprocess.run([str(ISCC), "/Q", str(script)], capture_output=True, startupinfo=startup)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout.decode(errors="replace") + compile_result.stderr.decode(errors="replace"))
            setup = work / "guard_setup.exe"
            log = work / "install.log"

            def install():
                return subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"],
                                      capture_output=True, startupinfo=startup, timeout=20)

            mutex = None
            if use_mutex:
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
                kernel.CreateMutexW.restype = ctypes.c_void_p
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                mutex = kernel.CreateMutexW(None, False, f"Local\\CapCap.InstallerGuardTest.{suffix}")
                self.assertTrue(mutex)
            elif not start_after_wizard:
                subprocess.run([sys.executable, str(launch)], check=True, startupinfo=startup)
            try:
                blocked = install()
                self.assertNotEqual(blocked.returncode, 0)
                self.assertFalse((work / "installed" / "marker.txt").exists())
                self.assertIn("CapCap is still running", log.read_text(encoding="utf-8-sig"))
            finally:
                if mutex:
                    kernel.CloseHandle(mutex)
                if pids_file.exists():
                    pid = json.loads(pids_file.read_text())["pid"]
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startup)
            if not start_after_wizard:
                allowed = install()
                self.assertEqual(allowed.returncode, 0, log.read_text(encoding="utf-8-sig"))
                self.assertEqual((work / "installed" / "marker.txt").read_text(), "installer guard test")


if __name__ == "__main__":
    unittest.main()
