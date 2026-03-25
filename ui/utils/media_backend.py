import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from video_processor import srt_to_ass


class QtMediaPlayerBackend(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)

    def __init__(self, video_view):
        super().__init__(video_view)
        from widgets import VideoView

        self.backend_name = "qt"
        self.video_view = video_view
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        if isinstance(video_view, VideoView):
            self._player.setVideoOutput(video_view.video_item)
        self._player.positionChanged.connect(self.positionChanged.emit)
        self._player.durationChanged.connect(self.durationChanged.emit)

    def setSource(self, source):
        self._player.setSource(source)

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def stop(self):
        self._player.stop()

    def setPosition(self, position):
        self._player.setPosition(position)

    def position(self):
        return self._player.position()

    def duration(self):
        return self._player.duration()

    def playbackState(self):
        return self._player.playbackState()

    def is_playing(self):
        return self.playbackState() == QMediaPlayer.PlayingState

    def set_subtitle_file(self, subtitle_path, subtitle_style=None):
        return None

    def clear_subtitle(self):
        return None


class MpvMediaPlayerBackend(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)

    def __init__(self, video_view):
        super().__init__(video_view)
        self.backend_name = "libmpv"
        self.video_view = video_view
        self._position_ms = 0
        self._duration_ms = 0
        self._state = QMediaPlayer.StoppedState
        self._source_path = ""
        self._subtitle_ass_path = ""

        prepare_mpv_bundle()
        import mpv

        self._player = mpv.MPV(
            wid=str(int(video_view.winId())),
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            pause=True,
            keep_open="always",
            sub_auto="no",
            sub_ass_override="no",
        )

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start()

    def _read_property(self, primary_name, fallback_name=None, default=None):
        names = [primary_name]
        if fallback_name:
            names.append(fallback_name)
        for name in names:
            try:
                value = self._player.property(name)
                if value is not None:
                    return value
            except Exception:
                pass
            attr_name = name.replace("-", "_")
            try:
                value = getattr(self._player, attr_name)
                if value is not None:
                    return value
            except Exception:
                pass
        return default

    def _normalize_source(self, source):
        if isinstance(source, QUrl):
            return source.toLocalFile()
        if isinstance(source, str):
            return source
        return ""

    def _poll_state(self):
        try:
            time_pos = self._read_property("time-pos", "time_pos", 0.0)
            duration = self._read_property("duration", default=0.0)
            pause = bool(self._read_property("pause", default=True))
        except Exception:
            return

        next_position = int(float(time_pos or 0.0) * 1000)
        next_duration = int(float(duration or 0.0) * 1000)
        next_state = QMediaPlayer.PausedState if pause else QMediaPlayer.PlayingState
        if not self._source_path:
            next_state = QMediaPlayer.StoppedState

        if next_position != self._position_ms:
            self._position_ms = next_position
            self.positionChanged.emit(next_position)
        if next_duration != self._duration_ms:
            self._duration_ms = next_duration
            self.durationChanged.emit(next_duration)
        self._state = next_state

    def setSource(self, source):
        source_path = self._normalize_source(source)
        if not source_path:
            self.stop()
            try:
                self._player.command("stop")
            except Exception:
                pass
            self._source_path = ""
            return

        self._source_path = source_path
        self._position_ms = 0
        self._duration_ms = 0
        self._state = QMediaPlayer.PausedState
        self._player.pause = True
        self._player.command("loadfile", source_path, "replace")
        self._apply_current_subtitle()

    def play(self):
        if not self._source_path:
            return
        self._player.pause = False
        self._state = QMediaPlayer.PlayingState

    def pause(self):
        self._player.pause = True
        self._state = QMediaPlayer.PausedState

    def stop(self):
        self._player.pause = True
        try:
            self._player.command("seek", 0, "absolute")
        except Exception:
            pass
        self._position_ms = 0
        self._state = QMediaPlayer.StoppedState
        self.positionChanged.emit(0)

    def setPosition(self, position):
        self._position_ms = int(position)
        self._player.command("seek", max(0.0, position / 1000.0), "absolute")
        self.positionChanged.emit(self._position_ms)

    def position(self):
        return self._position_ms

    def duration(self):
        return self._duration_ms

    def playbackState(self):
        return self._state

    def is_playing(self):
        return self._state == QMediaPlayer.PlayingState

    def clear_subtitle(self):
        if self._subtitle_ass_path and os.path.exists(self._subtitle_ass_path):
            try:
                os.remove(self._subtitle_ass_path)
            except OSError:
                pass
        self._subtitle_ass_path = ""
        try:
            self._player.sub_visibility = False
        except Exception:
            pass

    def set_subtitle_file(self, subtitle_path, subtitle_style=None):
        if not subtitle_path or not os.path.exists(subtitle_path):
            self.clear_subtitle()
            return

        if subtitle_path.lower().endswith(".ass"):
            ass_path = subtitle_path
        else:
            subtitle_style = subtitle_style or {}
            video_width = self.video_view.video_source_width or 1920
            video_height = self.video_view.video_source_height or 1080
            ass_path = srt_to_ass(
                subtitle_path,
                video_width=video_width,
                video_height=video_height,
                alignment=subtitle_style.get("alignment", 2),
                margin_v=subtitle_style.get("margin_v", 30),
                font_name=subtitle_style.get("font_name", "Arial"),
                font_size=subtitle_style.get("font_size", 18),
                font_color=subtitle_style.get("font_color", "&H00FFFFFF"),
                background_box=subtitle_style.get("background_box", False),
            )
        if self._subtitle_ass_path and self._subtitle_ass_path != ass_path and os.path.exists(self._subtitle_ass_path):
            try:
                os.remove(self._subtitle_ass_path)
            except OSError:
                pass
        self._subtitle_ass_path = ass_path
        self._apply_current_subtitle()

    def _apply_current_subtitle(self):
        if not self._source_path:
            return
        if not self._subtitle_ass_path or not os.path.exists(self._subtitle_ass_path):
            try:
                self._player.sub_visibility = False
            except Exception:
                pass
            return
        try:
            self._player.command("sub-add", self._subtitle_ass_path, "select")
            self._player.sub_visibility = True
        except Exception:
            pass


def create_media_backend(video_view):
    try:
        return MpvMediaPlayerBackend(video_view)
    except Exception:
        return QtMediaPlayerBackend(video_view)


def get_mpv_bundle_dir():
    return Path(__file__).resolve().parents[2] / "bin" / "mpv"


def prepare_mpv_bundle():
    mpv_dir = get_mpv_bundle_dir()
    if not mpv_dir.exists():
        raise FileNotFoundError(f"Bundled mpv directory not found: {mpv_dir}")

    mpv_dll = mpv_dir / "libmpv-2.dll"
    if not mpv_dll.exists():
        alt_dll = mpv_dir / "mpv-2.dll"
        if alt_dll.exists():
            mpv_dll = alt_dll
        else:
            raise FileNotFoundError(f"Bundled libmpv DLL not found in {mpv_dir}")

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(mpv_dir))

    os.environ["PATH"] = str(mpv_dir) + os.pathsep + os.environ.get("PATH", "")

    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.WinDLL(str(mpv_dll))
        except OSError as exc:
            raise RuntimeError(
                f"Could not load bundled libmpv from {mpv_dll}. "
                "The bundle may be missing dependent runtime DLLs."
            ) from exc
