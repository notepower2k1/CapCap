import os
import shutil
import sys
import time

from PySide6.QtCore import QThread, Signal

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "app")
if APP_PATH not in sys.path:
    sys.path.append(APP_PATH)

from services import EngineRuntime


class PreviewMuxWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, video_path, audio_path, output_path, mode="voice", srt_path="", subtitle_style=None):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path
        self.output_path = output_path
        self.mode = mode
        self.srt_path = srt_path
        self.subtitle_style = subtitle_style or {}

    def run(self):
        temp_mux_path = ""
        try:
            from preview_processor import mux_audio_into_video_for_preview

            current_video = self.video_path
            if self.audio_path and os.path.exists(self.audio_path):
                temp_dir = os.path.join(os.getcwd(), "temp")
                os.makedirs(temp_dir, exist_ok=True)
                temp_mux_path = os.path.join(temp_dir, f"preview_mux_{int(time.time())}.mp4")
                current_video = mux_audio_into_video_for_preview(self.video_path, self.audio_path, temp_mux_path)

            if self.mode in ("subtitle", "both") and self.srt_path and os.path.exists(self.srt_path):
                engine = EngineRuntime()
                ok = engine.embed_subtitles(
                    current_video,
                    self.srt_path,
                    self.output_path,
                    subtitle_style=self.subtitle_style,
                )
                if not ok:
                    raise RuntimeError("Failed to render subtitle preview video.")
                output = self.output_path
            else:
                if current_video != self.output_path:
                    shutil.copyfile(current_video, self.output_path)
                output = self.output_path

            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
        finally:
            if temp_mux_path and os.path.exists(temp_mux_path):
                try:
                    os.remove(temp_mux_path)
                except OSError:
                    pass


class QuickPreviewWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, video_path, output_path, mode, start_seconds, duration_seconds, srt_path="", audio_path="", subtitle_style=None):
        super().__init__()
        self.video_path = video_path
        self.output_path = output_path
        self.mode = mode
        self.start_seconds = start_seconds
        self.duration_seconds = duration_seconds
        self.srt_path = srt_path
        self.audio_path = audio_path
        self.subtitle_style = subtitle_style or {}

    def run(self):
        temp_paths = []
        try:
            from preview_processor import mux_audio_into_video_clip_for_preview, trim_video_clip

            temp_dir = os.path.join(os.getcwd(), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            stamp = int(time.time())
            base_clip = os.path.join(temp_dir, f"preview_base_{stamp}.mp4")
            temp_paths.append(base_clip)
            trim_video_clip(self.video_path, base_clip, self.start_seconds, self.duration_seconds)

            current_video = base_clip
            if self.mode in ("voice", "both") and self.audio_path and os.path.exists(self.audio_path):
                voice_clip = os.path.join(temp_dir, f"preview_voice_{stamp}.mp4")
                temp_paths.append(voice_clip)
                mux_audio_into_video_clip_for_preview(
                    self.video_path,
                    self.audio_path,
                    voice_clip,
                    self.start_seconds,
                    self.duration_seconds,
                )
                current_video = voice_clip

            if self.mode in ("subtitle", "both") and self.srt_path and os.path.exists(self.srt_path):
                engine = EngineRuntime()
                ok = engine.embed_subtitles(
                    current_video,
                    self.srt_path,
                    self.output_path,
                    subtitle_style=self.subtitle_style,
                )
                if not ok:
                    raise RuntimeError("Failed to render subtitle preview clip.")
            else:
                shutil.copyfile(current_video, self.output_path)

            self.finished.emit(self.output_path, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
        finally:
            for path in temp_paths:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass


class ExactFramePreviewWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, video_path, output_path, timestamp_seconds, srt_path, subtitle_style=None):
        super().__init__()
        self.video_path = video_path
        self.output_path = output_path
        self.timestamp_seconds = timestamp_seconds
        self.srt_path = srt_path
        self.subtitle_style = subtitle_style or {}

    def run(self):
        try:
            from preview_processor import render_subtitle_frame_preview

            output = render_subtitle_frame_preview(
                self.video_path,
                self.srt_path,
                self.output_path,
                self.timestamp_seconds,
                alignment=self.subtitle_style.get("alignment", 2),
                margin_v=self.subtitle_style.get("margin_v", 30),
                font_name=self.subtitle_style.get("font_name", "Arial"),
                font_size=self.subtitle_style.get("font_size", 18),
                font_color=self.subtitle_style.get("font_color", "&H00FFFFFF"),
                background_box=self.subtitle_style.get("background_box", False),
                animation_style=self.subtitle_style.get("animation", "Static"),
                highlight_color=self.subtitle_style.get("highlight_color", self.subtitle_style.get("font_color", "&H00FFFFFF")),
                outline_color=self.subtitle_style.get("outline_color", "&H00000000"),
                outline_width=self.subtitle_style.get("outline_width", 2.0),
                shadow_color=self.subtitle_style.get("shadow_color", "&H80000000"),
                shadow_depth=self.subtitle_style.get("shadow_depth", 1.0),
                background_color=self.subtitle_style.get("background_color", "&H80000000"),
                background_alpha=self.subtitle_style.get("background_alpha", 0.5),
                bold=self.subtitle_style.get("bold", False),
                preset_key=self.subtitle_style.get("preset_key", ""),
                auto_keyword_highlight=self.subtitle_style.get("auto_keyword_highlight", False),
                animation_duration=self.subtitle_style.get("animation_duration", 0.22),
                manual_highlights=self.subtitle_style.get("manual_highlights", []),
                word_timings=self.subtitle_style.get("word_timings", []),
                karaoke_timing_mode=self.subtitle_style.get("karaoke_timing_mode", "vietnamese"),
            )
            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
