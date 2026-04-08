import os
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

    def set_audio_file(self, audio_path):
        return None

    def clear_audio(self):
        return None

    def set_blur_region(self, blur_region=None):
        return None

    def clear_blur_region(self):
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
        self._applied_subtitle_path = ""
        self._blur_region = None

        prepare_mpv_bundle()
        import mpv

        target_wid = video_view.get_mpv_target_winid() if hasattr(video_view, "get_mpv_target_winid") else video_view.winId()
        try:
            target_wid = int(target_wid)
        except Exception:
            target_wid = 0
        if sys.platform.startswith("win"):
            # mpv expects a Win32 HWND passed as an unsigned 32-bit integer.
            target_wid &= 0xFFFFFFFF

        self._player = mpv.MPV(
            wid=str(target_wid),
            input_default_bindings=False,
            input_vo_keyboard=False,
            force_window="no",
            osc=False,
            pause=True,
            keep_open="always",
            sub_auto="no",
            sub_ass_override="no",
        )

        @self._player.event_callback("file-loaded")
        def _on_file_loaded(event):
            # Re-apply external tracks if needed once the file is ready
            self._apply_current_subtitle()
            self._apply_current_audio()
            self._apply_blur_filter()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start()
        self._audio_path = ""
        self._applied_subtitle_path = ""
        self._applied_audio_path = ""

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
            return source.toLocalFile() or source.toString()
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
            self.clear_subtitle()
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
        
        # Reset applied tracking on source change
        self._applied_subtitle_path = ""
        self._applied_audio_path = ""
        
        # We also trigger them here just in case, though file-loaded callback
        # is the main authority now.
        self._apply_blur_filter()
        self._apply_current_subtitle()
        self._apply_current_audio()

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
        if not self._source_path:
            self.positionChanged.emit(self._position_ms)
            return
        try:
            self._player.command("seek", max(0.0, position / 1000.0), "absolute")
        except Exception:
            pass
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
        self._applied_subtitle_path = ""
        try:
            self._player.sub_visibility = False
        except Exception:
            pass

    def _build_blur_filter(self):
        blur = self._blur_region or {}
        if not blur:
            return ""
        video_width = int(self.video_view.video_source_width or 0)
        video_height = int(self.video_view.video_source_height or 0)
        if video_width <= 0 or video_height <= 0:
            return ""
        x = max(0, min(video_width - 2, int(round(float(blur.get("x", 0.0)) * video_width))))
        y = max(0, min(video_height - 2, int(round(float(blur.get("y", 0.0)) * video_height))))
        w = max(16, min(video_width - x, int(round(float(blur.get("width", 0.0)) * video_width))))
        h = max(16, min(video_height - y, int(round(float(blur.get("height", 0.0)) * video_height))))
        return (
            "lavfi=[split[main][tmp];"
            f"[tmp]crop=w={w}:h={h}:x={x}:y={y},boxblur=20:3[blur];"
            f"[main][blur]overlay={x}:{y}]"
        )

    def _apply_blur_filter(self):
        try:
            self._player.command("vf", "clr", "")
        except Exception:
            pass
        filter_spec = self._build_blur_filter()
        if not filter_spec:
            return
        try:
            self._player.command("vf", "add", f"@capcap-blur:{filter_spec}")
        except Exception:
            try:
                self._player.vf = filter_spec
            except Exception:
                pass

    def set_blur_region(self, blur_region=None):
        self._blur_region = dict(blur_region or {}) if blur_region else None
        self._apply_blur_filter()

    def clear_blur_region(self):
        self._blur_region = None
        self._apply_blur_filter()

    def set_audio_file(self, audio_path):
        if not audio_path or not os.path.exists(audio_path):
            self.clear_audio()
            return
        self._audio_path = audio_path
        self._apply_current_audio()

    def clear_audio(self):
        self._audio_path = ""
        try:
            # Setting audio to no or 0
            self._player.audio_delay = 0
            self._player.command("audio-remove", "1") # Best effort
        except Exception:
            pass

    def _apply_current_audio(self):
        if not self._source_path or not self._audio_path:
            return
        if not os.path.exists(self._audio_path):
            return
        try:
            if self._applied_audio_path == self._audio_path:
                 # In mpv, you can't easily 'reload' an audio file like srt, 
                 # but we can assume it's already there or just re-add if needed.
                 # Actually, better to just re-add if it's the first time for this source.
                 return
            self._player.command("audio-add", self._audio_path, "select")
            self._applied_audio_path = self._audio_path
            self.log(f"[Backend] External audio applied: {self._audio_path}")
        except Exception:
            pass

    def log(self, text):
        # We can reach out to the gui if needed
        if hasattr(self.video_view, "parent") and hasattr(self.video_view.parent(), "log"):
             self.video_view.parent().log(text)
        elif hasattr(self, "gui") and hasattr(self.gui, "log"):
             self.gui.log(text)

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
                animation_style=subtitle_style.get("animation", "Static"),
                highlight_color=subtitle_style.get("highlight_color", subtitle_style.get("font_color", "&H00FFFFFF")),
                outline_color=subtitle_style.get("outline_color", "&H00000000"),
                outline_width=subtitle_style.get("outline_width", 2.0),
                shadow_color=subtitle_style.get("shadow_color", "&H80000000"),
                shadow_depth=subtitle_style.get("shadow_depth", 1.0),
                background_color=subtitle_style.get("background_color", "&H80000000"),
                background_alpha=subtitle_style.get("background_alpha", 0.5),
                bold=subtitle_style.get("bold", False),
                preset_key=subtitle_style.get("preset_key", ""),
                auto_keyword_highlight=subtitle_style.get("auto_keyword_highlight", False),
                animation_duration=subtitle_style.get("animation_duration", 0.22),
                manual_highlights=subtitle_style.get("manual_highlights", []),
                custom_position_enabled=subtitle_style.get("custom_position_enabled", False),
                custom_position_x=subtitle_style.get("custom_position_x", 50),
                custom_position_y=subtitle_style.get("custom_position_y", 86),
                single_line=subtitle_style.get("single_line", False),
            )
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
            self._applied_subtitle_path = ""
            return
        try:
            # We use 'sub-add' with 'replace' or just add a new one and select it.
            # To avoid accumulating too many tracks, we could try to clear first,
            # but mpv's sub-add with same path might already handle it.
            # Better: use 'sub-reload' if it's already the applied path.
            if self._applied_subtitle_path == self._subtitle_ass_path:
                self._player.command("sub-reload")
            else:
                self._player.command("sub-add", self._subtitle_ass_path, "select")
            self._player.sub_visibility = True
            self._applied_subtitle_path = self._subtitle_ass_path
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
