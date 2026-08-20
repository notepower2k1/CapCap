import os
import math
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from runtime_paths import asset_path, bin_path, ffprobe_path, mpv_library_path, subprocess_hidden_kwargs
from video_processor import srt_to_ass
try:
    # The normal launcher places ``app`` directly on sys.path.
    from experimental_mpv_lut import MpvGpuLutPrototype
except ImportError:  # pragma: no cover - package/import tooling context
    from app.experimental_mpv_lut import MpvGpuLutPrototype


_REALTIME_COLOR_FIELDS = (
    "brightness", "contrast", "saturation", "temperature", "gamma", "hue",
    "highlights", "shadows",
)


def _escape_mpv_filter_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    return value.replace(":", "\\:").replace("'", "\\'")


def _realtime_color_graph(state=None) -> str:
    """Build the managed MPV source-color graph in export order."""
    source = state.get("final", state) if isinstance(state, dict) else {}
    if not isinstance(source, dict):
        source = {}
    values = {}
    for field in _REALTIME_COLOR_FIELDS:
        try:
            values[field] = max(-100.0, min(100.0, float(source.get(field, 0.0) or 0.0)))
        except (TypeError, ValueError):
            values[field] = 0.0
    if not any(abs(value) > 0.01 for value in values.values()):
        return ""
    stages = []
    current = ""
    filters = []
    eq_parts = []
    if abs(values["brightness"]) > 0.01:
        eq_parts.append(f"brightness={values['brightness'] / 100.0 * 0.35:.4f}")
    if abs(values["contrast"]) > 0.01:
        eq_parts.append(f"contrast={max(0.4, min(1.8, 1.0 + values['contrast'] / 100.0 * 0.65)):.4f}")
    if abs(values["saturation"]) > 0.01:
        eq_parts.append(f"saturation={max(0.0, min(2.2, 1.0 + values['saturation'] / 100.0 * 1.2)):.4f}")
    if abs(values["gamma"]) > 0.01:
        eq_parts.append(f"gamma={max(0.5, min(2.0, 1.0 + values['gamma'] / 100.0 * 0.75)):.4f}")
    if eq_parts:
        filters.append("eq=" + ":".join(eq_parts))
    if abs(values["hue"]) > 0.01:
        filt = f"hue=h={max(-180.0, min(180.0, values['hue'] * 1.8)):.3f}"
        filters.append(filt)
    if abs(values["temperature"]) > 0.01:
        temp = max(-0.35, min(0.35, values["temperature"] / 100.0 * 0.35))
        filt = f"colorbalance=rs={temp:.4f}:gs={temp * 0.2:.4f}:bs={-temp:.4f}"
        filters.append(filt)
    if abs(values["highlights"]) > 0.01 or abs(values["shadows"]) > 0.01:
        shadow_point = max(0.0, min(0.45, 0.25 + values["shadows"] / 100.0 * 0.18))
        highlight_point = max(0.55, min(1.0, 0.75 + values["highlights"] / 100.0 * 0.18))
        filters.append(
            "curves=master="
            f"'0/0 0.25/{shadow_point:.3f} 0.75/{highlight_point:.3f} 1/1'"
        )
    for index, filt in enumerate(filters):
        output = "color_main" if index == len(filters) - 1 else f"color_stage{index}"
        stages.append(f"[{current}]{filt}[{output}]" if current else f"{filt}[{output}]")
        current = output
    if current and current != "color_main":
        stages.append(f"[{current}]null[color_main]")
    return ";".join(stages)


def _ffprobe_video_duration(video_path: str) -> float:
    """Get video duration using ffprobe as fallback."""
    try:
        ffprobe = ffprobe_path()
        if not ffprobe:
            return 0.0
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
            **subprocess_hidden_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


class QtMediaPlayerBackend(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    stateChanged = Signal(int)  # QMediaPlayer.PlaybackState

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
        self._player.stateChanged.connect(lambda s: self.stateChanged.emit(int(s.value)))
        # When the clip reaches the end, the QMediaPlayer goes to
        # StoppedState — surface this so the timeline can stop too
        # (Bug 2: video not pausing at end, timeline keeps running).
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._mute_original = False
        self._mute_dubbed = False

    def _on_media_status(self, status):
        try:
            from PySide6.QtMultimedia import QMediaPlayer as _QMP
            if status == _QMP.EndOfMedia:
                # Pause (hold last frame) and emit StoppedState so the
                # timeline play state is re-synced to "not playing".
                self._player.pause()
                self.stateChanged.emit(int(QMediaPlayer.PausedState.value))
        except Exception:
            pass

    def setSource(self, source):
        self._source_path = source.toLocalFile() if isinstance(source, QUrl) else str(source)
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

    def set_original_audio_file(self, audio_path):
        return None

    def _clear_original_audio(self):
        return None

    def set_blur_region(self, blur_region=None):
        return None

    def clear_blur_region(self):
        return None

    def set_color_filter_state(self, state=None):
        return None

    def clear_color_filter(self):
        return None

    def set_preview_framing(self, source_ratio=None, canvas_ratio=None, scale_mode="fit", focus_x=0.5, focus_y=0.5):
        return None

    def set_volume(self, percent):
        value = max(0, min(100, int(percent)))
        self._audio_output.setVolume(value / 100.0)

    def set_original_volume(self, percent):
        value = max(0, min(200, int(percent))) / 100.0
        self._audio_output.setVolume(value)

    def set_dubbed_volume(self, percent):
        value = max(0, min(200, int(percent))) / 100.0
        self._audio_output.setVolume(value)

    def original_volume(self):
        return int(round(self._audio_output.volume() * 100.0))

    def dubbed_volume(self):
        return int(round(self._audio_output.volume() * 100.0))

    def volume(self):
        return int(round(self._audio_output.volume() * 100.0))

    def set_muted(self, muted):
        self._mute_original = bool(muted)
        self._mute_dubbed = bool(muted)
        self._audio_output.setMuted(bool(muted))

    def is_muted(self):
        return bool(self._audio_output.isMuted())

    def set_mute_original(self, muted):
        self._mute_original = bool(muted)
        self._audio_output.setMuted(self._mute_original and self._mute_dubbed)

    def set_mute_dubbed(self, muted):
        self._mute_dubbed = bool(muted)
        self._audio_output.setMuted(self._mute_original and self._mute_dubbed)

    def is_original_muted(self):
        return self._mute_original

    def is_dubbed_muted(self):
        return self._mute_dubbed

    def set_playback_rate(self, rate):
        try:
            self._player.setPlaybackRate(float(rate))
        except Exception:
            pass

    def playback_rate(self):
        try:
            return float(self._player.playbackRate())
        except Exception:
            return 1.0


class MpvMediaPlayerBackend(QObject):
    """Three-track design with truly independent mute:

    - mpv plays the source video with NO audio (`ao=null`) — video only
    - QMediaPlayer sidecar #1 plays the original audio file (extracted_audio)
    - QMediaPlayer sidecar #2 plays the dubbed audio file (mixed_vi)

    Each audio has its own QAudioOutput with its own mute. A sync timer
    keeps all three playheads aligned.

    The mpv Python bindings in this build reject `audio-add`, `lavfi=*`
    af chains, and multi-value `aid`, so we cannot mix both tracks inside
    one mpv. Using two QMediaPlayer sidecars avoids the audio-device
    conflict of two-mpv design and works with the bundled libmpv.
    """

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    stateChanged = Signal(int)  # QMediaPlayer.PlaybackState

    def __init__(self, video_view):
        super().__init__(video_view)
        self.backend_name = "libmpv"
        self.video_view = video_view
        self._position_ms = 0
        self._duration_ms = 0
        self._state = QMediaPlayer.StoppedState
        self._source_path = ""
        self._audio_path = ""        # dubbed audio
        self._original_audio_path = ""  # original audio (extracted)
        self._subtitle_ass_path = ""
        self._applied_subtitle_path = ""
        self._sub_track_id = -1
        self._blur_region = None
        self._mask_region = None
        self._color_filter_state = {}
        self._managed_vf_label = "capcap-managed"
        self._gpu_lut = None
        self.supports_native_lut = False
        self._mute_original = False
        self._mute_dubbed = False

        prepare_mpv_bundle()
        import mpv

        # gpu-next is the preferred production renderer. Set
        # MPV_GPU_NEXT_ENABLED=false/0 to force the stable gpu backend while
        # diagnosing a driver or libplacebo compatibility issue.
        gpu_next_enabled = str(os.environ.get("MPV_GPU_NEXT_ENABLED", "true") or "true").strip().lower() not in {
            "0", "false", "no", "off"
        }
        self._gpu_next_enabled = gpu_next_enabled

        target_wid = video_view.get_mpv_target_winid() if hasattr(video_view, "get_mpv_target_winid") else video_view.winId()
        try:
            target_wid = int(target_wid)
        except Exception:
            target_wid = 0
        if sys.platform.startswith("win"):
            target_wid &= 0xFFFFFFFF

        bundled_font_dir = asset_path("fonts")
        player_options = {
            "wid": str(target_wid),
            "vo": "gpu-next" if gpu_next_enabled else "gpu",
            "ao": "null",  # No audio output from mpv — we use QMediaPlayer sidecars
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "force_window": "no",
            "osc": False,
            "pause": True,
            "keep_open": "always",
            "sub_auto": "no",
            "sub_ass_override": "no",
        }
        # FFmpeg export passes this directory to libass. Give MPV the same
        # bundled faces so subtitle wrapping and the full-block background
        # use matching font metrics in Preview and Export.
        if bundled_font_dir and os.path.isdir(bundled_font_dir):
            player_options["sub_fonts_dir"] = bundled_font_dir
        try:
            self._player = mpv.MPV(**player_options)
        except Exception as exc:
            if not gpu_next_enabled:
                raise
            # gpu-next is experimental and may be unavailable with a
            # particular driver/libplacebo combination. Keep startup safe by
            # falling back to the stable renderer.
            self.log(f"[Preview] MPV gpu-next unavailable; falling back to gpu: {exc}")
            player_options["vo"] = "gpu"
            self._gpu_next_enabled = False
            self._player = mpv.MPV(**player_options)
        if self._gpu_next_enabled:
            try:
                self._gpu_lut = MpvGpuLutPrototype(
                    self._player,
                    Path("temp/mpv_lut_cache").resolve(),
                )
                self.supports_native_lut = True
            except Exception as exc:
                self.log(f"[Preview] Native MPV LUT unavailable: {exc}")
        self.log(f"[Preview] MPV video output: {'gpu-next' if gpu_next_enabled else 'gpu'}")

        @self._player.event_callback("file-loaded")
        def _on_file_loaded(event):
            self._apply_current_subtitle()
            self._apply_blur_filter()

        @self._player.event_callback("end-file")
        def _on_end_file(event):
            # mpv emits this when the source reaches EOF. With
            # `keep_open="always"` the player self-pauses instead of
            # quitting, but the event still fires. Surface it as a
            # PausedState so the timeline stops running (Bug 2).
            try:
                reason = None
                if isinstance(event, dict):
                    reason = event.get("reason")
                else:
                    reason = getattr(event, "reason", None)
                if reason == "eof":
                    self._state = QMediaPlayer.PausedState
                    try:
                        self.stateChanged.emit(int(QMediaPlayer.PausedState.value))
                    except Exception:
                        pass
            except Exception:
                pass

        # --- Original audio sidecar ---
        self._original_output = QAudioOutput()
        self._original_output.setVolume(1.0)
        self._original_player = QMediaPlayer()
        self._original_player.setAudioOutput(self._original_output)
        self._original_loaded_path = ""
        self._original_position_ms = 0
        self._original_player.positionChanged.connect(self._on_original_position_changed)

        # --- Dubbed audio sidecar ---
        self._dubbed_output = QAudioOutput()
        self._dubbed_output.setVolume(1.0)
        self._dubbed_player = QMediaPlayer()
        self._dubbed_player.setAudioOutput(self._dubbed_output)
        self._dubbed_loaded_path = ""
        self._dubbed_position_ms = 0
        self._dubbed_player.positionChanged.connect(self._on_dubbed_position_changed)
        self._dubbed_player.mediaStatusChanged.connect(self._on_dubbed_status_changed)

        # --- Timers ---
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start()

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(500)
        self._sync_timer.timeout.connect(self._sync_audio_to_video)
        self._sync_timer.start()

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
        # The timer remains allocated for backend lifetime, but querying mpv
        # properties every 200 ms while no media is loaded is needless work.
        if not self._source_path:
            return
        try:
            time_pos = self._read_property("time-pos", "time_pos", 0.0)
            duration = self._read_property("duration", default=0.0)
            pause = bool(self._read_property("pause", default=True))
            # `eof-reached` is mpv's authoritative end-of-stream flag.
            # It stays reliable even when a vf change transiently
            # zeroes the `duration` property (e.g. after applying a
            # mask filter) — Bug 2 / "video plays past duration".
            eof = bool(self._read_property("eof-reached", "eof_reached", False))
            core_idle = bool(self._read_property("core-idle", "core_idle", False))
        except Exception:
            return

        next_position = int(float(time_pos or 0.0) * 1000)
        next_duration = int(float(duration or 0.0) * 1000)
        next_state = QMediaPlayer.PausedState if pause else QMediaPlayer.PlayingState
        if next_position != self._position_ms:
            self._position_ms = next_position
            self.positionChanged.emit(next_position)
        if next_duration != self._duration_ms:
            self._duration_ms = next_duration
            self.durationChanged.emit(next_duration)
        prev_state = self._state
        self._state = next_state
        # Surface EOF per the end-file event handler as well, but the
        # poller also catches the transition here as a safety net. We
        # only emit when we were actively Playing and mpv now reports
        # EOF (or paused *at* the duration) — not on transient
        # pause readings immediately after play() to avoid racing
        # the explicit state change and killing the audio sidecars.
        if (
            prev_state == QMediaPlayer.PlayingState
            and next_state == QMediaPlayer.PausedState
            and (eof or core_idle or self._duration_ms <= 0
                 or self._position_ms >= self._duration_ms - 250)
        ):
            try:
                self.stateChanged.emit(int(next_state.value))
            except Exception:
                pass

    def setSource(self, source):
        source_path = self._normalize_source(source)
        if not source_path:
            self.stop()
            self.clear_subtitle()
            try:
                self._player.command("stop")
            except Exception:
                pass
            try:
                self._original_player.stop()
                self._dubbed_player.stop()
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
        
        # Use ffprobe as fallback for duration detection
        # mpv might not report duration immediately after loading
        ffprobe_duration = _ffprobe_video_duration(source_path)
        if ffprobe_duration > 0:
            self._duration_ms = int(ffprobe_duration * 1000)
            self.durationChanged.emit(self._duration_ms)
        
        # Reset applied tracking on source change
        self._applied_subtitle_path = ""
        # Pause both audio sidecars at 0 until user plays.
        if self._original_loaded_path:
            try:
                self._original_player.pause()
                self._original_player.setPosition(0)
            except Exception:
                pass
        if self._dubbed_loaded_path:
            try:
                self._dubbed_player.pause()
                self._dubbed_player.setPosition(0)
            except Exception:
                pass
        self._apply_blur_filter()
        self._apply_current_subtitle()

    def play(self):
        if not self._source_path:
            return
        self._player.pause = False
        if self._original_loaded_path:
            try:
                self._original_player.play()
            except Exception:
                pass
        if self._dubbed_loaded_path:
            try:
                self._dubbed_player.play()
            except Exception:
                pass
        self._state = QMediaPlayer.PlayingState
        try:
            self.stateChanged.emit(int(self._state.value))
        except Exception:
            pass

    def pause(self):
        self._player.pause = True
        if self._original_loaded_path:
            try:
                self._original_player.pause()
            except Exception:
                pass
        if self._dubbed_loaded_path:
            try:
                self._dubbed_player.pause()
            except Exception:
                pass
        self._state = QMediaPlayer.PausedState
        try:
            self.stateChanged.emit(int(self._state.value))
        except Exception:
            pass

    def stop(self):
        self._player.pause = True
        if self._original_loaded_path:
            try:
                self._original_player.pause()
                self._original_player.setPosition(0)
            except Exception:
                pass
        if self._dubbed_loaded_path:
            try:
                self._dubbed_player.pause()
                self._dubbed_player.setPosition(0)
            except Exception:
                pass
        try:
            self._player.command("seek", 0, "absolute")
        except Exception:
            pass
        self._position_ms = 0
        self._state = QMediaPlayer.StoppedState
        self.positionChanged.emit(0)
        try:
            self.stateChanged.emit(int(self._state.value))
        except Exception:
            pass

    def setPosition(self, position):
        self._position_ms = int(position)
        if not self._source_path:
            self.positionChanged.emit(self._position_ms)
            return
        seconds = max(0.0, position / 1000.0)
        try:
            self._player.command("seek", seconds, "absolute")
        except Exception:
            pass
        if self._original_loaded_path:
            try:
                self._original_player.setPosition(int(position))
            except Exception:
                pass
        if self._dubbed_loaded_path:
            try:
                self._dubbed_player.setPosition(int(position))
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
        # Hiding a selected MPV subtitle track is not sufficient here: its
        # renderer can remain visible briefly while the editable Qt subtitle
        # overlay is being positioned. Remove the track completely.
        track_id = self._sub_track_id
        if isinstance(track_id, int) and track_id >= 0:
            try:
                self._player.command("sub-remove", track_id)
            except Exception:
                pass
        self._sub_track_id = -1
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
        """Return a `lavfi=[...]` string for the blur effect, or ''."""
        body = self._build_blur_body()
        return f"lavfi=[{body}]" if body else ""

    def _build_blur_body(self, source_label: str | None = None) -> str:
        """Return the lavfi graph body (no `lavfi=[` wrapper) for
        the blur effect. The body takes the source video as input
        and outputs the blurred video on a named label `[b0]`
        (or unnamed if there are no regions) so it can be chained
        with the mask body in a single lavfi graph.
        """
        raw_regions = self._blur_region or []
        if isinstance(raw_regions, dict):
            raw_regions = [raw_regions]
        if not isinstance(raw_regions, list) or not raw_regions:
            return ""
        video_width = int(self.video_view.video_source_width or 0)
        video_height = int(self.video_view.video_source_height or 0)
        if video_width <= 0 or video_height <= 0:
            return ""
        regions = []
        for blur in raw_regions:
            if not isinstance(blur, dict):
                continue
            try:
                x = max(0, min(video_width - 2, int(round(float(blur.get("x", 0.0)) * video_width))))
                y = max(0, min(video_height - 2, int(round(float(blur.get("y", 0.0)) * video_height))))
                w = max(2, min(video_width - x, int(round(float(blur.get("width", 0.0)) * video_width))))
                h = max(2, min(video_height - y, int(round(float(blur.get("height", 0.0)) * video_height))))
            except (TypeError, ValueError):
                continue
            try:
                strength_raw = blur.get("blur_strength", blur.get("strength"))
                if strength_raw is None:
                    min_dimension = min(w, h)
                    luma_radius = max(1, min(20, int(min_dimension // 2)))
                else:
                    luma_radius = max(1, min(20, int(round(float(strength_raw)))))
            except (TypeError, ValueError):
                luma_radius = max(1, min(20, int(min(w, h) // 2)))
            chroma_radius = max(0, min(20, luma_radius // 2))
            try:
                opacity = float(blur.get("blur_opacity", blur.get("opacity", 1.0)))
            except (TypeError, ValueError):
                opacity = 1.0
            opacity = max(0.0, min(1.0, opacity))
            pixelate = bool(blur.get("pixelate", False))
            try:
                pixel_size = int(blur.get("pixelate_size", 12))
            except (TypeError, ValueError):
                pixel_size = 12
            pixel_size = max(2, min(60, pixel_size))
            regions.append((x, y, w, h, luma_radius, chroma_radius, opacity, pixelate, pixel_size))
        if not regions:
            return ""

        crop_parts = []
        overlay_parts = []
        for index, region in enumerate(regions):
            x, y, w, h, luma_radius, chroma_radius, opacity, pixelate, pixel_size = region
            if pixelate:
                cell_w = max(1, w // pixel_size)
                cell_h = max(1, h // pixel_size)
                effect = (
                    f"scale={cell_w}:{cell_h}:flags=neighbor,"
                    f"scale={w}:{h}:flags=neighbor"
                )
            else:
                effect = f"boxblur={luma_radius}:3:{chroma_radius}:3"
            if opacity < 0.999:
                effect = (
                    f"format=yuva420p,{effect},"
                    f"colorchannelmixer=aa={opacity:.3f}"
                )
            crop_parts.append(
                f"[tmp{index}]crop=w={w}:h={h}:x={x}:y={y},{effect}[blur{index}]"
            )
            base_label = "main" if index == 0 else f"b{index - 1}"
            # Always name the output. The last region outputs to
            # `[b_last]` so the mask body can chain from it; the
            # intermediate regions output to `[b{index}]` so the
            # next region's `[base_label]` (= `b{index-1}`) finds
            # its input.
            output_label = "[b_last]" if index == len(regions) - 1 else f"[b{index}]"
            overlay_parts.append(f"[{base_label}][blur{index}]overlay={x}:{y}{output_label}")
        split_inputs = f"[{source_label}]" if source_label else ""
        split_outputs = "[main]" + "".join(f"[tmp{i}]" for i in range(len(regions)))
        return (
            f"{split_inputs}split={len(regions) + 1}{split_outputs};"
            + ";".join(crop_parts + overlay_parts)
        )

    def _apply_blur_filter(self):
        self._apply_all_filters()

    def _apply_mask_filter(self):
        self._apply_all_filters()

    def _apply_all_filters(self):
        """Build a single combined lavfi filter for blur + mask and
        push it as one `vf add`.

        Previously the blur and mask were pushed as two separate
        `vf add` commands. Each `lavfi=[...]` creates its own
        internal graph, and the second one's `[main]` label is not
        reliably connected to the first one's output — the mask
        would draw on the source video instead of on the blurred
        video, and the blur was effectively dropped.

        Combining them into a single lavfi graph (with the blur
        output labelled `[b0]` and the mask taking `[b0]` as input)
        makes the chain deterministic: blur first, then mask on top.
        """
        color_body = _realtime_color_graph(self._color_filter_state)
        blur_body = self._build_blur_body(source_label="color_main" if color_body else None)
        mask_body = self._build_mask_body(
            input_label=("b_last" if blur_body else ("color_main" if color_body else None))
        )
        if not color_body and not blur_body and not mask_body:
            try:
                self._player.command("vf", "remove", f"@{self._managed_vf_label}")
            except Exception:
                try:
                    self._player.vf = []
                except Exception:
                    pass
            return
        if blur_body and mask_body:
            # The blur body ends with a named output `[b0]` (or the
            # last `[bN]` for multiple regions). The mask body takes
            # `[b0]` as input. We just concatenate the two bodies
            # with a `;` separator — the `[b0]` label carries the
            # blur output into the mask.
            combined = f"{blur_body};{mask_body}"
        elif blur_body:
            combined = blur_body
        else:
            combined = mask_body
        if color_body:
            combined = f"{color_body};{combined}" if combined else color_body
        try:
            self._player.command("vf", "set", f"@{self._managed_vf_label}:lavfi=[{combined}]")
        except Exception:
            try:
                self._player.vf = f"lavfi=[{combined}]"
            except Exception:
                pass

    def set_color_filter_state(self, state=None):
        """Update realtime Brightness/Contrast/Saturation state."""
        self._color_filter_state = dict(state or {}) if isinstance(state, dict) else {}
        self._apply_all_filters()
        self._apply_native_lut(self._color_filter_state)

    def clear_color_filter(self):
        self._color_filter_state = {}
        self._apply_all_filters()
        self._apply_native_lut({})

    def set_preview_framing(self, source_ratio=None, canvas_ratio=None, scale_mode="fit", focus_x=0.5, focus_y=0.5):
        """Apply preview-only crop framing inside the native MPV canvas."""
        try:
            source_ratio = float(source_ratio or 0.0)
            canvas_ratio = float(canvas_ratio or 0.0)
            mode = str(scale_mode or "fit").strip().lower()
            if mode != "fill" or source_ratio <= 0.0 or canvas_ratio <= 0.0:
                zoom = 0.0
                pan_x = pan_y = 0.0
            else:
                crop_factor = max(source_ratio / canvas_ratio, canvas_ratio / source_ratio)
                zoom = max(0.0, math.log(crop_factor, 2.0))
                pan_x = (max(0.0, min(1.0, float(focus_x))) - 0.5) * 2.0
                pan_y = (max(0.0, min(1.0, float(focus_y))) - 0.5) * 2.0
            self._player.command("set", "video-zoom", str(zoom))
            self._player.command("set", "video-pan-x", str(pan_x))
            self._player.command("set", "video-pan-y", str(pan_y))
        except Exception as exc:
            self.log(f"[Preview] Framing update failed: {exc}")

    def _apply_native_lut(self, state=None):
        if not self._gpu_lut:
            return
        state = state if isinstance(state, dict) else {}
        lut_path = str(state.get("lut_path", "") or "").strip()
        try:
            strength = float(state.get("lut_strength", 0.0) or 0.0)
        except (TypeError, ValueError):
            strength = 0.0
        if not lut_path or strength <= 0.001 or not os.path.exists(lut_path):
            self._gpu_lut.clear()
            return
        try:
            self._gpu_lut.apply(lut_path, strength * 100.0)
        except Exception as exc:
            self.log(f"[Filter] Native MPV LUT update failed: {exc}")

    def set_blur_region(self, blur_region=None):
        if isinstance(blur_region, list):
            self._blur_region = [dict(region) for region in blur_region if isinstance(region, dict)]
        else:
            self._blur_region = dict(blur_region or {}) if blur_region else None
        self._apply_blur_filter()

    def clear_blur_region(self):
        self._blur_region = None
        self._apply_blur_filter()

    # ---- Mask regions (solid/pixelate/blur) ----
    def set_mask_region(self, mask_region=None):
        if isinstance(mask_region, list):
            self._mask_region = [dict(r) for r in mask_region if isinstance(r, dict)]
        else:
            self._mask_region = dict(mask_region or {}) if mask_region else None
        self._apply_mask_filter()

    def clear_mask_region(self):
        self._mask_region = None
        self._apply_mask_filter()

    def _build_mask_filter(self):
        """Build the lavfi filter chain for the M1 mask track.

        The mask is always a solid-colour rectangle. Pixelate and blur
        modes were removed; the only effect is changing the colour of
        the selected region.
        """
        body = self._build_mask_body(input_label="main")
        return f"lavfi=[{body}]" if body else ""

    def _build_mask_body(self, input_label: str | None = None) -> str:
        """Return the lavfi graph body (no `lavfi=[` wrapper) for
        the mask effect. Takes the source video (or the named
        `input_label` from a previous filter in the same graph) and
        outputs the masked video.

        The colour stream is a single frame (`d=1`). The overlay
        filter's default `eof_action=repeat` holds that single frame
        for the rest of the main stream's duration, so the mask
        doesn't extend the video past its real length.
        """
        raw = self._mask_region or []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            return ""
        video_width = int(self.video_view.video_source_width or 0)
        video_height = int(self.video_view.video_source_height or 0)
        if video_width <= 0 or video_height <= 0:
            return ""
        items = []
        for mask in raw:
            if not isinstance(mask, dict):
                continue
            try:
                x = max(0, min(video_width - 2, int(round(float(mask.get("x", 0.0)) * video_width))))
                y = max(0, min(video_height - 2, int(round(float(mask.get("y", 0.0)) * video_height))))
                w = max(2, min(video_width - x, int(round(float(mask.get("width", 0.0)) * video_width))))
                h = max(2, min(video_height - y, int(round(float(mask.get("height", 0.0)) * video_height))))
            except (TypeError, ValueError):
                continue
            color = str(mask.get("color", "#000000") or "#000000")
            try:
                opacity = float(mask.get("opacity", mask.get("mask_opacity", 1.0)))
            except (TypeError, ValueError):
                opacity = 1.0
            opacity = max(0.0, min(1.0, opacity))
            try:
                start = max(0.0, float(mask.get("start", 0.0) or 0.0))
                end = float(mask.get("end", 0.0) or 0.0)
            except (TypeError, ValueError):
                start, end = 0.0, 0.0
            items.append((x, y, w, h, color, opacity, start, end))
        if not items:
            return ""

        # The first overlay's base label is either the previous
        # filter's output label (when chained) or undefined (when
        # the mask is alone, in which case the lavfi input becomes
        # the base via label mapping).
        base_label = input_label or "main_in"

        chain_parts: list[str] = []
        for i, (x, y, w, h, color, opacity, start, end) in enumerate(items):
            hex_color = color.lstrip("#")
            if len(hex_color) == 3:
                hex_color = "".join(c * 2 for c in hex_color)
            if len(hex_color) != 6:
                hex_color = "000000"
            if opacity < 0.999:
                synth = (
                    f"color=0x{hex_color}:s={w}x{h}:d=1,"
                    f"format=yuva420p,colorchannelmixer=aa={opacity:.3f}"
                )
            else:
                synth = f"color=0x{hex_color}:s={w}x{h}:d=1,format=yuva420p"
            chain_parts.append(f"{synth}[rect{i}]")
            current_label = base_label if i == 0 else f"b{i - 1}"
            out_label = "" if i == len(items) - 1 else f"[b{i}]"
            # Keep the graph loaded for the whole playback session and let
            # FFmpeg enable this overlay at its own timeline interval. This
            # avoids clearing/recreating the MPV filter at a mask boundary,
            # which caused a visible flicker when the mask first appeared.
            timing = ""
            if end > start:
                timing = f":enable='between(t,{start:.3f},{end:.3f})'"
            chain_parts.append(
                f"[{current_label}][rect{i}]overlay={x}:{y}{timing}{out_label}"
            )
        return ";".join(chain_parts)

    def set_audio_file(self, audio_path):
        """Load the dubbed audio file into the QMediaPlayer sidecar."""
        if not audio_path or not os.path.exists(audio_path):
            self.clear_audio()
            return
        self._audio_path = audio_path
        self._apply_current_dubbed()

    def set_original_audio_file(self, audio_path):
        """Load the original audio file into the QMediaPlayer sidecar."""
        if not audio_path or not os.path.exists(audio_path):
            self._clear_original_audio()
            return
        self._original_audio_path = audio_path
        self._apply_current_original()

    def _clear_original_audio(self):
        self._original_audio_path = ""
        self._original_loaded_path = ""
        try:
            self._original_player.stop()
        except Exception:
            pass
        self._apply_original_mute()

    def clear_audio(self):
        """Unload the dubbed audio sidecar."""
        self._audio_path = ""
        self._dubbed_loaded_path = ""
        try:
            self._dubbed_player.stop()
        except Exception:
            pass
        self._apply_dubbed_mute()

    def _apply_current_dubbed(self):
        if not self._audio_path or not os.path.exists(self._audio_path):
            return
        if self._dubbed_loaded_path == self._audio_path:
            self._apply_dubbed_mute()
            return
        try:
            self._dubbed_player.setSource(QUrl.fromLocalFile(self._audio_path))
            self._dubbed_loaded_path = self._audio_path
        except Exception as exc:
            self.log(f"[Backend] Dubbed audio load failed: {exc}")
            return
        self._apply_dubbed_mute()

    def _apply_current_original(self):
        if not self._original_audio_path or not os.path.exists(self._original_audio_path):
            return
        if self._original_loaded_path == self._original_audio_path:
            self._apply_original_mute()
            return
        try:
            self._original_player.setSource(QUrl.fromLocalFile(self._original_audio_path))
            self._original_loaded_path = self._original_audio_path
        except Exception as exc:
            self.log(f"[Backend] Original audio load failed: {exc}")
            return
        self._apply_original_mute()

    def _on_original_position_changed(self, position):
        self._original_position_ms = int(position or 0)

    def _on_dubbed_position_changed(self, position):
        self._dubbed_position_ms = int(position or 0)

    def _on_dubbed_status_changed(self, status):
        try:
            from PySide6.QtMultimedia import QMediaPlayer as _QMP
            if status == _QMP.EndOfMedia:
                if not self._player.pause and self._state == QMediaPlayer.PlayingState:
                    self._player.pause = True
        except Exception:
            pass

    def _apply_dubbed_mute(self):
        """Mute the dubbed audio QMediaPlayer sidecar."""
        try:
            self._dubbed_output.setMuted(bool(self._mute_dubbed))
        except Exception as exc:
            self.log(f"[Backend] Dubbed mute failed: {exc}")

    def _apply_original_mute(self):
        """Mute the original audio QMediaPlayer sidecar."""
        try:
            self._original_output.setMuted(bool(self._mute_original))
        except Exception as exc:
            self.log(f"[Backend] Original mute failed: {exc}")

    def _apply_video_mute(self):
        # mpv uses ao=null so this is a no-op. Kept for API compatibility.
        pass

    def _resync_audio_position(self):
        try:
            v_pos_ms = int(float(self._player.time_pos or 0) * 1000)
        except Exception:
            v_pos_ms = 0
        if v_pos_ms < 0:
            v_pos_ms = 0
        if self._original_loaded_path:
            try:
                self._original_player.setPosition(int(v_pos_ms))
            except Exception:
                pass
        if self._dubbed_loaded_path:
            try:
                self._dubbed_player.setPosition(int(v_pos_ms))
            except Exception:
                pass

    def _sync_audio_to_video(self):
        if not self._source_path:
            return
        try:
            v_pos_ms = int(float(self._player.time_pos or 0) * 1000)
        except Exception:
            return
        try:
            v_paused = bool(self._player.pause)
        except Exception:
            return
        if self._original_loaded_path:
            try:
                a_state = self._original_player.playbackState()
                a_paused = a_state == QMediaPlayer.PausedState or a_state == QMediaPlayer.StoppedState
            except Exception:
                a_paused = True
            if v_paused != a_paused:
                try:
                    if v_paused:
                        self._original_player.pause()
                    else:
                        self._original_player.play()
                except Exception:
                    pass
            try:
                a_pos_ms = int(self._original_player.position() or 0)
            except Exception:
                a_pos_ms = 0
            if abs(v_pos_ms - a_pos_ms) > 300:
                try:
                    self._original_player.setPosition(int(v_pos_ms))
                except Exception:
                    pass
        if self._dubbed_loaded_path:
            try:
                a_state = self._dubbed_player.playbackState()
                a_paused = a_state == QMediaPlayer.PausedState or a_state == QMediaPlayer.StoppedState
            except Exception:
                a_paused = True
            if v_paused != a_paused:
                try:
                    if v_paused:
                        self._dubbed_player.pause()
                    else:
                        self._dubbed_player.play()
                except Exception:
                    pass
            try:
                a_pos_ms = int(self._dubbed_player.position() or 0)
            except Exception:
                a_pos_ms = 0
            if abs(v_pos_ms - a_pos_ms) > 300:
                try:
                    self._dubbed_player.setPosition(int(v_pos_ms))
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
                speaker_colors=subtitle_style.get("speaker_colors", []),
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
            # Remove old subtitle track first to avoid accumulation
            if self._applied_subtitle_path and self._applied_subtitle_path != self._subtitle_ass_path:
                try:
                    self._player.command("sub-remove", self._sub_track_id)
                except Exception:
                    pass
            if self._applied_subtitle_path == self._subtitle_ass_path:
                self._player.command("sub-reload")
            else:
                track_id = self._player.command("sub-add", self._subtitle_ass_path, "select")
                self._sub_track_id = track_id
            self._player.sub_visibility = True
            self._applied_subtitle_path = self._subtitle_ass_path
        except Exception:
            pass

    def set_volume(self, percent):
        try:
            v = max(0, min(100, int(percent)))
            # Legacy global volume: apply to both sidecars.
            try:
                self._original_output.setVolume(v / 100.0)
            except Exception:
                pass
            try:
                self._dubbed_output.setVolume(v / 100.0)
            except Exception:
                pass
        except Exception:
            pass

    def set_original_volume(self, percent):
        try:
            v = max(0.0, min(200.0, float(percent))) / 100.0
            self._original_output.setVolume(v)
        except Exception:
            pass

    def set_dubbed_volume(self, percent):
        try:
            v = max(0.0, min(200.0, float(percent))) / 100.0
            self._dubbed_output.setVolume(v)
        except Exception:
            pass

    def original_volume(self):
        try:
            return int(round(self._original_output.volume() * 100.0))
        except Exception:
            return 100

    def dubbed_volume(self):
        try:
            return int(round(self._dubbed_output.volume() * 100.0))
        except Exception:
            return 100

    def volume(self):
        try:
            return int(round(self._original_output.volume() * 100.0))
        except Exception:
            return 100

    def set_muted(self, muted):
        # Legacy: apply to BOTH tracks so existing callers keep working.
        self.set_mute_original(bool(muted))
        self.set_mute_dubbed(bool(muted))

    def is_muted(self):
        return self._mute_original and self._mute_dubbed

    def set_mute_original(self, muted):
        self._mute_original = bool(muted)
        self._apply_original_mute()

    def set_mute_dubbed(self, muted):
        self._mute_dubbed = bool(muted)
        self._apply_dubbed_mute()

    def is_original_muted(self):
        return self._mute_original

    def is_dubbed_muted(self):
        return self._mute_dubbed

    def set_playback_rate(self, rate):
        try:
            self._player.speed = float(rate)
        except Exception:
            pass

    def playback_rate(self):
        try:
            return float(self._read_property("speed", default=1.0) or 1.0)
        except Exception:
            return 1.0


def create_media_backend(video_view):
    try:
        return MpvMediaPlayerBackend(video_view)
    except Exception:
        return QtMediaPlayerBackend(video_view)


def get_mpv_bundle_dir():
    return Path(bin_path("mpv"))


def is_mpv_backend_available():
    try:
        prepare_mpv_bundle()
        import mpv  # noqa: F401
        return True
    except Exception:
        return False


def prepare_mpv_bundle():
    """Make libmpv importable before ``python-mpv`` loads it.

    Windows builds ship libmpv inside ``bin/mpv`` and must pre-load it so the
    dependent runtime DLLs resolve.  Linux and macOS install libmpv through the
    system package manager (``libmpv2`` / ``brew install mpv``), where the
    dynamic loader already finds it, so a missing bundle there is not an error.
    """
    mpv_library = mpv_library_path()

    if not sys.platform.startswith("win"):
        # Honour a bundled library when one is present, otherwise defer to the
        # system loader instead of failing the whole preview backend.
        if mpv_library:
            mpv_dir = str(Path(mpv_library).parent)
            var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
            existing = os.environ.get(var, "")
            if mpv_dir not in existing.split(os.pathsep):
                os.environ[var] = mpv_dir + (os.pathsep + existing if existing else "")
        return

    mpv_dir = get_mpv_bundle_dir()
    if not mpv_dir.exists():
        raise FileNotFoundError(f"Bundled mpv directory not found: {mpv_dir}")

    if not mpv_library:
        raise FileNotFoundError(f"Bundled libmpv DLL not found in {mpv_dir}")
    mpv_dll = Path(mpv_library)

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(mpv_dir))

    os.environ["PATH"] = str(mpv_dir) + os.pathsep + os.environ.get("PATH", "")

    try:
        import ctypes

        ctypes.WinDLL(str(mpv_dll))
    except OSError as exc:
        raise RuntimeError(
            f"Could not load bundled libmpv from {mpv_dll}. "
            "The bundle may be missing dependent runtime DLLs."
        ) from exc
