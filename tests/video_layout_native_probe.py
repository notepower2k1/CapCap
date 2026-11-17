"""Isolated native-player stress probe; a parent process enforces the timeout."""
import ctypes
import faulthandler
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT/'app', ROOT/'ui'):
    sys.path.insert(0, str(directory))


def working_set_mb():
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (key, ctypes.c_size_t) for key in ('peak', 'working', 'qpp', 'qp', 'qnp', 'qn', 'page', 'pagepeak')]
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi')
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.working // 1024**2


def main():
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtWidgets import QApplication
    from widgets.mpv_video_view import MpvVideoView
    from utils.media_backend import MpvMediaPlayerBackend
    from app.video_layout import config
    from runtime_paths import bin_path, subprocess_hidden_kwargs
    # A stack dump does not install the Windows exception handler, which some
    # GPU drivers trigger with handled native exceptions during initialization.
    faulthandler.dump_traceback_later(20)
    app = QApplication([])
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        folder = Path(directory)
        video = folder/'fixture.mp4'
        subprocess.run([bin_path('ffmpeg','ffmpeg.exe'), '-v','error','-f','lavfi','-i',
                        'color=c=blue:s=320x180:d=3','-c:v','libx264',str(video)],
                       check=True,capture_output=True,**subprocess_hidden_kwargs())
        images = []
        for index in range(24):
            color = QColor.fromHsv(index*15,180,170)
            image = QImage(3840,2160,QImage.Format_RGB32); image.fill(QColor(color))
            path = folder/f'background_{index}.jpg'; assert image.save(str(path))
            images.append(str(path))
        # Include PNGs and the caller's images as well as unique 4K JPEGs.
        images.extend(str(ROOT/'assets'/name) for name in ('preview_1.png','preview_2.png'))
        images.extend(sys.argv[1:])
        view = MpvVideoView(); view.resize(640,400); view.set_video_dimensions(320,180); view.show()
        backend = MpvMediaPlayerBackend(view)
        backend.setSource(QUrl.fromLocalFile(str(video)))
        samples = []
        def apply():
            index = len(samples)
            if index >= 36:
                print('RESULT '+json.dumps(samples),flush=True)
                backend.close(); view.hide(); app.quit(); return
            cfg = config(dict(template='frame',background='image',image=images[index % len(images)],
                              corner=24,shadow=12,video_height=80))
            cfg.update(_canvas_width=1920,_canvas_height=1080,_source_width=320,_source_height=180,
                       _mode='fit',_focus=(.5,.5))
            print(f'APPLY {index} rss_mb={working_set_mb()}',flush=True)
            backend.set_color_filter_state({'video_layout':cfg})
            samples.append(working_set_mb())
            QTimer.singleShot(250,apply)
        QTimer.singleShot(1000,apply)
        app.exec()
        faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    main()
