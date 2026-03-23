import sys
import os
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QCheckBox, QTextEdit, QComboBox,
                             QGroupBox, QSlider, QFrame, QProgressBar, QMessageBox,
                             QScrollArea, QGraphicsScene, QGraphicsView, QGraphicsItem,
                             QSpinBox, QColorDialog, QDoubleSpinBox, QTabWidget, QDialog, QSizePolicy,
                             QRadioButton)
from PySide6.QtCore import Qt, QSizeF, QRectF, QPointF, QUrl, QThread, Signal, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

# --- UI Components ---
class SubtitleOverlayItem(QGraphicsItem):
    """A draggable subtitle preview item rendered inside the QGraphicsScene."""
    W, H = 640, 96

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setZValue(10)
        self.current_text = ""
        self.font_name = "Segoe UI"
        self.font_size = 20
        self.font_color = QColor(255, 255, 255)
        self.alignment = "Bottom Center"
        self.x_offset = 0
        self.bottom_offset = 30

    def set_text(self, text):
        if self.current_text != text:
            self.current_text = text
            self.update()

    def set_style(self, *, font_name=None, font_size=None, font_color=None):
        changed = False
        if font_name and font_name != self.font_name:
            self.font_name = font_name
            changed = True
        if font_size and font_size != self.font_size:
            self.prepareGeometryChange()
            self.font_size = font_size
            self.H = max(96, int(font_size * 4))
            changed = True
        if font_color and font_color != self.font_color:
            self.font_color = font_color
            changed = True
        if changed:
            self.update()

    def set_positioning(self, *, x_offset=None, bottom_offset=None):
        if x_offset is not None:
            self.x_offset = x_offset
        if bottom_offset is not None:
            self.bottom_offset = bottom_offset

    def set_alignment(self, alignment: str):
        self.alignment = alignment or "Bottom Center"

    def set_layout_width(self, width: int):
        width = max(160, int(width))
        if width != self.W:
            self.prepareGeometryChange()
            self.W = width
            self.update()

    def boundingRect(self):
        return QRectF(0, 0, self.W, self.H)

    def paint(self, painter, option, widget=None):
        if not self.current_text and not self.isVisible():
            return
            
        rect = QRectF(0, 0, self.W, self.H)
        painter.setRenderHint(QPainter.Antialiasing)
        
        if self.current_text:
            painter.setPen(self.font_color)
            fnt = QFont(self.font_name)
            fnt.setPixelSize(max(1, int(self.font_size)))
            fnt.setBold(True)
            painter.setFont(fnt)
            
            # Approximate ASS outline/shadow so preview is closer to exported libass output.
            outline_offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1)]
            painter.setPen(QColor(0, 0, 0, 220))
            for dx, dy in outline_offsets:
                painter.drawText(rect.translated(dx, dy), Qt.AlignCenter | Qt.TextWordWrap, self.current_text)

            painter.setPen(QColor(0, 0, 0, 120))
            painter.drawText(rect.translated(2, 2), Qt.AlignCenter | Qt.TextWordWrap, self.current_text)

            painter.setPen(self.font_color)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self.current_text)
        else:
            # Design phase placeholder
            painter.setPen(QPen(self.font_color, 1, Qt.DashLine))
            painter.drawRoundedRect(rect, 10, 10)
            
            painter.setPen(self.font_color)
            placeholder_font = QFont(self.font_name)
            placeholder_font.setPixelSize(12)
            painter.setFont(placeholder_font)
            painter.drawText(rect, Qt.AlignCenter, "(Subtitle Preview Area)")


class VideoView(QGraphicsView):
    """QGraphicsView that hosts the video and subtitle overlay in one scene.
    Resizing automatically scales the video item to fill the view.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: black; border-radius: 10px;")
        self.setRenderHint(QPainter.Antialiasing)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # Video rendered as a scene item — no native surface issue
        self.video_item = QGraphicsVideoItem()
        self._scene.addItem(self.video_item)

        # Subtitle overlay as another scene item on top
        self.subtitle_item = SubtitleOverlayItem()
        self._scene.addItem(self.subtitle_item)
        self.subtitle_item.hide()
        self.video_source_width = 0
        self.video_source_height = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.video_item.setSize(QSizeF(w, h))
        self._scene.setSceneRect(0, 0, w, h)
        self.reposition_subtitle()

    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))
        self.reposition_subtitle()

    def get_video_content_rect(self) -> QRectF:
        view_w, view_h = float(self.width()), float(self.height())
        if view_w <= 0 or view_h <= 0:
            return QRectF(0, 0, 0, 0)
        if not self.video_source_width or not self.video_source_height:
            return QRectF(0, 0, view_w, view_h)

        source_ratio = self.video_source_width / self.video_source_height
        view_ratio = view_w / view_h if view_h else source_ratio

        if source_ratio > view_ratio:
            content_w = view_w
            content_h = view_w / source_ratio
            offset_x = 0
            offset_y = (view_h - content_h) / 2
        else:
            content_h = view_h
            content_w = view_h * source_ratio
            offset_x = (view_w - content_w) / 2
            offset_y = 0

        return QRectF(offset_x, offset_y, content_w, content_h)

    def reposition_subtitle(self):
        """Keep subtitle inside the actual video content area, not the letterbox bars."""
        item = self.subtitle_item
        rect = self.get_video_content_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        source_w = max(1, self.video_source_width or int(rect.width()))
        source_h = max(1, self.video_source_height or int(rect.height()))
        scale_x = rect.width() / source_w
        scale_y = rect.height() / source_h
        side_margin_px = 60 * scale_x

        desired_width = min(int(rect.width() - 2 * side_margin_px), max(160, int((source_w - 120) * scale_x)))
        item.set_layout_width(desired_width)

        iw, ih = item.W, item.H
        left_pad = rect.left() + side_margin_px
        right_limit = rect.right() - iw - side_margin_px
        if item.alignment == "Bottom Left":
            x = left_pad
        elif item.alignment == "Bottom Right":
            x = right_limit
        else:
            x = rect.left() + (rect.width() - iw) / 2

        x += item.x_offset * scale_x
        y = rect.bottom() - ih - (item.bottom_offset * scale_y)

        x = max(left_pad, min(x, right_limit))
        y_min = rect.top() + 12
        y_max = rect.bottom() - ih - 12
        y = max(y_min, min(y, y_max))

        pos = QPointF(x, y)
        item.setPos(pos)

class TimelineWidget(QGraphicsView):
    """CapCut-style timeline for subtitle preview and seeking."""
    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(130)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #0d0d0d; border-top: 1px solid #1a1a1a;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)
        
        # Custom ScrollBar Styling
        self.horizontalScrollBar().setStyleSheet("""
            QScrollBar:horizontal {
                border: none;
                background: #111;
                height: 10px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #333;
                min-width: 30px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #444;
            }
        """)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        
        self.pixels_per_second = 100 
        self.duration = 0 # ms
        self.segments = []
        self.playhead = None
        self.is_moving_playhead = False
        self._playing = False

    def set_playing(self, playing):
        self._playing = playing

    def set_duration(self, ms):
        self.duration = ms
        self.refresh()

    def set_segments(self, segments):
        self.segments = segments
        self.refresh()

    def refresh(self):
        self._scene.clear()
        width = (self.duration / 1000.0) * self.pixels_per_second
        width = max(width, self.width()) + 200
        self._scene.setSceneRect(0, 0, width, 110)
        
        # 1. Draw Ruler
        ruler_pen = QPen(QColor(80, 80, 80), 1)
        font = QFont("Segoe UI", 7)
        for sec in range(0, int(self.duration / 1000) + 5):
            x = sec * self.pixels_per_second
            is_major = (sec % 5 == 0)
            h = 14 if is_major else 6
            self._scene.addLine(x, 0, x, h, ruler_pen)
            if is_major:
                m, s = divmod(sec, 60)
                txt = self._scene.addText(f"{m:02d}:{s:02d}", font)
                txt.setDefaultTextColor(QColor(150, 150, 150))
                txt.setPos(x + 2, -2)

        # 2. Draw Segments
        row_y = 35
        row_h = 35
        for seg in self.segments:
            sx = seg['start'] * self.pixels_per_second
            ex = seg['end'] * self.pixels_per_second
            sw = max(2, ex - sx)
            
            # Block
            rect = self._scene.addRect(0, 0, sw, row_h, QPen(QColor(60, 60, 60), 1), QColor(41, 121, 255, 120))
            rect.setPos(sx, row_y)
            rect.setToolTip(seg['text'])
            
            # Label
            clean_txt = seg['text'].replace('\n', ' ').strip()
            if len(clean_txt) > 25: clean_txt = clean_txt[:22] + "..."
            t_item = self._scene.addText(clean_txt, QFont("Segoe UI", 8))
            t_item.setDefaultTextColor(Qt.white)
            t_item.setPos(sx + 4, row_y + 4)

        # 3. Create Playhead
        self.playhead = self._scene.addLine(0, 0, 0, 110, QPen(QColor(255, 40, 40), 2))
        if self.playhead:
            self.playhead.setZValue(1000)

    def set_position(self, ms):
        if not self.playhead: return
        x = (ms / 1000.0) * self.pixels_per_second
        self.playhead.setLine(x, 0, x, 110)
        
        if self._playing:
            # Auto-scroll if it goes near edge
            view_rect = self.viewport().rect()
            scene_rect = self.mapToScene(view_rect).boundingRect()
            if x > scene_rect.right() - 100 or x < scene_rect.left():
                self.centerOn(x, 55)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_moving_playhead = True
            self.handle_seek(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_moving_playhead:
            self.handle_seek(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.is_moving_playhead = False
        super().mouseReleaseEvent(event)

    def handle_seek(self, pos):
        sp = self.mapToScene(pos)
        ms = int((sp.x() / self.pixels_per_second) * 1000)
        ms = max(0, min(ms, self.duration))
        self.seekRequested.emit(ms)

# --- Worker Threads ---

class VocalSeparationWorker(QThread):
    # vocal_path, music_path, error_msg
    finished = Signal(str, str, str)
    def __init__(self, audio_path, output_dir):
        super().__init__()
        self.audio_path = audio_path
        self.output_dir = output_dir
    def run(self):
        try:
            # Add app to path for import
            import sys
            import os
            app_path = os.path.join(os.getcwd(), 'app')
            if app_path not in sys.path:
                sys.path.append(app_path)
            
            from vocal_processor import separate_vocals
            v, m = separate_vocals(self.audio_path, self.output_dir)
            if v and m:
                self.finished.emit(v, m, "")
            else:
                self.finished.emit("", "", "Failed to separate audio stems.")
        except ImportError as e:
            self.finished.emit("", "", str(e))
        except Exception as e:
            self.finished.emit("", "", f"Unexpected error: {str(e)}")

class ExtractionWorker(QThread):
    finished = Signal(bool, str)
    def __init__(self, video_path, audio_output_path):
        super().__init__()
        self.video_path = video_path
        self.audio_output_path = audio_output_path
    def run(self):
        try:
            success = extract_audio(self.video_path, self.audio_output_path)
            self.finished.emit(success, self.audio_output_path)
        except Exception as e:
            self.finished.emit(False, str(e))

class TranscriptionWorker(QThread):
    finished = Signal(list)
    def __init__(self, audio_path, model_path, language):
        super().__init__()
        self.audio_path = audio_path
        self.model_path = model_path
        self.language = language
    def run(self):
        try:
            segments = transcribe_audio(self.audio_path, self.model_path, language=self.language)
            self.finished.emit(segments if segments else [])
        except Exception as e:
            print(f"Transcription Thread Error: {e}")
            self.finished.emit([])

class TranslationWorker(QThread):
    finished = Signal(str, str) # translated_srt, error_msg
    def __init__(self, srt_text, model_path, src_lang, enable_polish):
        super().__init__()
        self.srt_text = srt_text
        self.model_path = model_path
        self.src_lang = src_lang
        self.enable_polish = enable_polish
    def run(self):
        try:
            # Import to use the updated translate_segments
            from translator import translate_segments_to_srt
            translated_srt = translate_segments_to_srt(
                self.srt_text,
                self.model_path,
                src_lang=self.src_lang,
                enable_polish=self.enable_polish,
            )
            self.finished.emit(translated_srt, "")
        except Exception as e:
            print(f"Translation Thread Error: {e}")
            self.finished.emit("", str(e))

# --- TTS Worker ---
class VoiceOverWorker(QThread):
    # voice_track_path, mixed_path, error_msg
    finished = Signal(str, str, str)
    def __init__(self, segments, output_dir, background_path, voice_name, voice_gain_db, bg_gain_db):
        super().__init__()
        self.segments = segments
        self.output_dir = output_dir
        self.background_path = background_path
        self.voice_name = voice_name
        self.voice_gain_db = voice_gain_db
        self.bg_gain_db = bg_gain_db

    def run(self):
        try:
            import sys
            import os
            app_path = os.path.join(os.getcwd(), 'app')
            if app_path not in sys.path:
                sys.path.append(app_path)

            from tts_processor import edge_tts_to_wav_16k_mono
            from audio_mixer import build_voice_track_from_srt_segments, mix_voice_with_background

            os.makedirs(self.output_dir, exist_ok=True)
            tmp_dir = os.path.join(self.output_dir, "_tts_tmp")
            os.makedirs(tmp_dir, exist_ok=True)

            wavs = []
            for idx, seg in enumerate(self.segments):
                txt = (seg.get("text") or "").strip()
                if not txt:
                    wavs.append("")
                    continue
                seg_wav = os.path.join(tmp_dir, f"seg_{idx:04d}.wav")
                edge_tts_to_wav_16k_mono(
                    text=txt,
                    wav_path=seg_wav,
                    voice=self.voice_name,
                    tmp_dir=tmp_dir,
                )
                wavs.append(seg_wav)

            base = "voice_vi.wav"
            voice_track = os.path.join(self.output_dir, base)
            build_voice_track_from_srt_segments(
                segments=self.segments,
                tts_wav_paths=wavs,
                output_wav_path=voice_track,
                gain_db=float(self.voice_gain_db),
            )

            mixed = ""
            if self.background_path and os.path.exists(self.background_path):
                mixed = os.path.join(self.output_dir, "mixed_vi.wav")
                mix_voice_with_background(
                    background_wav_path=self.background_path,
                    voice_wav_path=voice_track,
                    output_wav_path=mixed,
                    background_gain_db=float(self.bg_gain_db),
                    voice_gain_db=0.0,
                )

            self.finished.emit(voice_track, mixed, "")
        except Exception as e:
            self.finished.emit("", "", str(e))

class PreviewMuxWorker(QThread):
    # preview_video_path, error_msg
    finished = Signal(str, str)
    def __init__(self, video_path, audio_path, output_path):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path
        self.output_path = output_path

    def run(self):
        try:
            import sys
            import os
            app_path = os.path.join(os.getcwd(), 'app')
            if app_path not in sys.path:
                sys.path.append(app_path)
            from preview_processor import mux_audio_into_video_for_preview
            out = mux_audio_into_video_for_preview(self.video_path, self.audio_path, self.output_path)
            self.finished.emit(out, "")
        except Exception as e:
            self.finished.emit("", str(e))


class FinalExportWorker(QThread):
    # output_video_path, error_msg
    finished = Signal(str, str)

    def __init__(self, video_path, output_path, mode, srt_path="", audio_path="", subtitle_style=None):
        super().__init__()
        self.video_path = video_path
        self.output_path = output_path
        self.mode = mode
        self.srt_path = srt_path
        self.audio_path = audio_path
        self.subtitle_style = subtitle_style or {}

    def run(self):
        tmp_mux_path = ""
        try:
            import sys
            import os
            import time
            app_path = os.path.join(os.getcwd(), 'app')
            if app_path not in sys.path:
                sys.path.append(app_path)

            from preview_processor import mux_audio_into_video_for_preview
            from video_processor import embed_subtitles

            mode = self.mode
            if mode == "subtitle":
                ok = embed_subtitles(
                    self.video_path,
                    self.srt_path,
                    self.output_path,
                    alignment=self.subtitle_style.get("alignment", 2),
                    margin_v=self.subtitle_style.get("margin_v", 30),
                    font_name=self.subtitle_style.get("font_name", "Arial"),
                    font_size=self.subtitle_style.get("font_size", 18),
                    font_color=self.subtitle_style.get("font_color", "&H00FFFFFF"),
                )
                if not ok:
                    raise RuntimeError("Failed to burn subtitles into the output video.")
            elif mode == "voice":
                mux_audio_into_video_for_preview(self.video_path, self.audio_path, self.output_path)
            elif mode == "both":
                tmp_dir = os.path.join(os.getcwd(), "temp")
                os.makedirs(tmp_dir, exist_ok=True)
                tmp_mux_path = os.path.join(tmp_dir, f"final_mux_{int(time.time())}.mp4")
                mux_audio_into_video_for_preview(self.video_path, self.audio_path, tmp_mux_path)
                ok = embed_subtitles(
                    tmp_mux_path,
                    self.srt_path,
                    self.output_path,
                    alignment=self.subtitle_style.get("alignment", 2),
                    margin_v=self.subtitle_style.get("margin_v", 30),
                    font_name=self.subtitle_style.get("font_name", "Arial"),
                    font_size=self.subtitle_style.get("font_size", 18),
                    font_color=self.subtitle_style.get("font_color", "&H00FFFFFF"),
                )
                if not ok:
                    raise RuntimeError("Failed to create the final video with audio and subtitles.")
            else:
                raise ValueError(f"Unsupported export mode: {mode}")

            self.finished.emit(self.output_path, "")
        except Exception as e:
            self.finished.emit("", str(e))
        finally:
            if tmp_mux_path and os.path.exists(tmp_mux_path):
                try:
                    os.remove(tmp_mux_path)
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
            import sys
            import os
            import time
            app_path = os.path.join(os.getcwd(), 'app')
            if app_path not in sys.path:
                sys.path.append(app_path)

            from preview_processor import trim_video_clip, mux_audio_into_video_clip_for_preview
            from video_processor import embed_subtitles

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
                ok = embed_subtitles(
                    current_video,
                    self.srt_path,
                    self.output_path,
                    alignment=self.subtitle_style.get("alignment", 2),
                    margin_v=self.subtitle_style.get("margin_v", 30),
                    font_name=self.subtitle_style.get("font_name", "Arial"),
                    font_size=self.subtitle_style.get("font_size", 18),
                    font_color=self.subtitle_style.get("font_color", "&H00FFFFFF"),
                )
                if not ok:
                    raise RuntimeError("Failed to render subtitle preview clip.")
            else:
                import shutil
                shutil.copyfile(current_video, self.output_path)

            self.finished.emit(self.output_path, "")
        except Exception as e:
            self.finished.emit("", str(e))
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
            import sys
            import os
            app_path = os.path.join(os.getcwd(), 'app')
            if app_path not in sys.path:
                sys.path.append(app_path)

            from preview_processor import render_subtitle_frame_preview

            out = render_subtitle_frame_preview(
                self.video_path,
                self.srt_path,
                self.output_path,
                self.timestamp_seconds,
                alignment=self.subtitle_style.get("alignment", 2),
                margin_v=self.subtitle_style.get("margin_v", 30),
                font_name=self.subtitle_style.get("font_name", "Arial"),
                font_size=self.subtitle_style.get("font_size", 18),
                font_color=self.subtitle_style.get("font_color", "&H00FFFFFF"),
            )
            self.finished.emit(out, "")
        except Exception as e:
            self.finished.emit("", str(e))

# Import our backend modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))
from video_processor import extract_audio, get_video_dimensions
from whisper_processor import transcribe_audio
from translator import translate_segments
from subtitle_builder import generate_srt

class VideoTranslatorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Subtitle Translator - Antigravity")
        
        # Maximize and prevent resizing
        self.setWindowState(Qt.WindowMaximized)
        # To strictly prevent resizing after maximizing:
        self.setFixedSize(QApplication.primaryScreen().availableGeometry().size())
        
        # Stylesheet for Premium Dark Mode
        self.setStyleSheet("""
            QMainWindow {
                background-color: #101826;
            }
            QWidget {
                color: #dbe5f3;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            #centralWidget {
                background-color: #101826;
            }
            #leftPanelArea {
                background-color: #162133;
                border-right: 1px solid #28364c;
            }
            #leftPanelContainer {
                background-color: #162133;
            }
            #rightPanel {
                background-color: #101826;
            }
            QGroupBox {
                border: 1px solid #30425b;
                border-radius: 14px;
                margin-top: 25px;
                font-weight: bold;
                color: #f3f7fb;
                background-color: #1b273a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #8ad7ff;
            }
            QFrame#heroCard, QFrame#statusCard, QFrame#sideInfoCard {
                background-color: #0f1724;
                border: 1px solid #2d425d;
                border-radius: 14px;
            }
            QLabel#heroTitle {
                font-size: 20px;
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#heroBody, QLabel#statusBody, QLabel#helperLabel, QLabel#previewContextLabel {
                color: #a9b8cb;
                line-height: 1.35em;
            }
            QLabel#sectionTitle {
                font-size: 13px;
                font-weight: 700;
                color: #8ad7ff;
            }
            QLabel#statusHeadline {
                font-size: 16px;
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#statusPill {
                background-color: #1d3a52;
                color: #9fe5ff;
                border: 1px solid #336180;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton {
                background-color: #24364f;
                color: #ffffff;
                border: 1px solid #335171;
                border-radius: 10px;
                padding: 10px 18px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2d4665;
                border-color: #4575a8;
            }
            QPushButton#mainActionBtn {
                background-color: #4ed0b3;
                color: #0b1620;
                border: none;
                font-size: 13px;
                border-bottom: 2px solid #258971;
            }
            QPushButton#mainActionBtn:hover {
                background-color: #66ddc2;
            }
            QLineEdit, QTextEdit, QComboBox {
                background-color: #111927;
                border: 1px solid #31445d;
                border-radius: 10px;
                color: #ffffff;
                padding: 8px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 1px solid #8ad7ff;
            }
            QProgressBar {
                border: 1px solid #2a3a50;
                border-radius: 10px;
                text-align: center;
                background-color: #111927;
                color: white;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5ed5c9, stop:1 #2b9f96);
                border-radius: 10px;
            }
            QLabel {
                background: transparent;
                color: #dbe5f3;
                font-size: 12px;
            }
            QCheckBox {
                background: transparent;
                color: #dbe5f3;
            }
            QScrollArea {
                border: none;
                background-color: #162133;
            }
            QScrollBar:vertical {
                border: none;
                background: #142030;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #35506f;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #416287;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            /* Fix ComboBox Dropdown colors */
            QComboBox QAbstractItemView {
                background-color: #111927;
                color: #ffffff;
                selection-background-color: #325173;
                border: 1px solid #31445d;
                outline: none;
            }
            QTabWidget::pane {
                border: 1px solid #30425b;
                border-radius: 12px;
                background: #111927;
                top: -1px;
            }
            QTabBar::tab {
                background: #1d2c40;
                color: #a8bad2;
                padding: 9px 14px;
                border: 1px solid #30425b;
                border-bottom: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                min-width: 110px;
            }
            QTabBar::tab:selected {
                background: #111927;
                color: #8ad7ff;
            }
        """)

        # -----------------------------
        # State (must exist before setup_ui)
        # -----------------------------
        # Track generated/selected artifacts for quick inspection.
        # Keys are stable IDs, values are absolute file paths.
        self.processed_artifacts = {}

        # Simple pipeline runner (Run All)
        self._pipeline_active = False
        self._pipeline_step = ""

        # Log buffer (UI panel)
        self._log_lines = []

        self.setup_ui()
        self.setup_media_player()

    def parse_srt_to_segments(self, srt_text):
        """Standard SRT parser to convert back to segments list for the timeline."""
        segments = []
        if not srt_text: return segments
        # Split by double newline to get SRT entries
        blocks = [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]
        for block in blocks:
            lines = block.split('\n')
            if len(lines) < 3: continue
            time_line = lines[1]
            if " --> " not in time_line: continue
            t_parts = time_line.split(" --> ")
            
            def to_seconds(t_str):
                t_str = t_str.replace(',', '.')
                parts = t_str.split(':')
                if len(parts) != 3: return 0.0
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            
            try:
                start = to_seconds(t_parts[0])
                end = to_seconds(t_parts[1])
                text = "\n".join(lines[2:])
                segments.append({'start': start, 'end': end, 'text': text})
            except:
                continue
        return segments

    def extract_subtitle_text_entries(self, srt_text):
        entries = []
        if not srt_text:
            return entries
        blocks = [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]
        for block in blocks:
            lines = [line.rstrip() for line in block.splitlines()]
            if not lines:
                continue
            if len(lines) >= 3 and " --> " in lines[1]:
                text = "\n".join(lines[2:]).strip()
            elif len(lines) >= 2 and lines[0].strip().isdigit():
                text = "\n".join(lines[1:]).strip()
            else:
                text = "\n".join(lines).strip()
            entries.append(text)
        return entries

    def format_to_srt(self, segments):
        """Helper to format segments list into a standard SRT string."""
        lines = []
        for i, seg in enumerate(segments):
            start = self.format_timestamp(seg['start'])
            end = self.format_timestamp(seg['end'])
            lines.append(f"{i+1}")
            lines.append(f"{start} --> {end}")
            lines.append(f"{seg['text'].strip()}\n")
        return "\n".join(lines)

    def format_timestamp(self, seconds):
        """Formats seconds into HH:MM:SS,mmm."""
        td = int(seconds * 1000)
        ms = td % 1000
        td //= 1000
        sec = td % 60
        td //= 60
        mins = td % 60
        hrs = td // 60
        return f"{hrs:02d}:{mins:02d}:{sec:02d},{ms:03d}"

    def setup_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("centralWidget")
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # --- LEFT PANEL (Scrollable) ---
        scroll_area = QScrollArea()
        scroll_area.setObjectName("leftPanelArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFixedWidth(680)
        scroll_area.setFrameShape(QFrame.NoFrame)
        
        left_panel_container = QWidget()
        left_panel_container.setObjectName("leftPanelContainer")
        left_layout = QVBoxLayout(left_panel_container)
        left_layout.setSpacing(15)
        
        scroll_area.setWidget(left_panel_container)

        # File Selection
        self.video_path_edit = QLineEdit()
        self.video_path_edit.setPlaceholderText("Choose one Chinese video to process...")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_video)
        
        file_layout = QHBoxLayout()
        file_layout.addWidget(self.video_path_edit)
        file_layout.addWidget(browse_btn)
        
        start_group = QGroupBox("START HERE")
        start_layout = QVBoxLayout(start_group)
        hero_card = QFrame()
        hero_card.setObjectName("heroCard")
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(14, 14, 14, 14)
        hero_layout.setSpacing(6)
        hero_title = QLabel("Make CapCap easier for first-time users")
        hero_title.setObjectName("heroTitle")
        hero_body = QLabel(
            "Pick one video, choose the output you want, then let the guided pipeline handle the heavy lifting. "
            "The detailed tabs below are there when you want to review or fine-tune."
        )
        hero_body.setWordWrap(True)
        hero_body.setObjectName("heroBody")
        hero_layout.addWidget(hero_title)
        hero_layout.addWidget(hero_body)
        start_layout.addWidget(hero_card)

        start_layout.addWidget(QLabel("1. Target Video"))
        start_layout.addLayout(file_layout)

        # Primary actions (one-click)
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItems([
            "Vietnamese subtitles only",
            "Vietnamese voice only",
            "Vietnamese subtitles + voice",
        ])
        self.output_mode_combo.setCurrentText("Vietnamese subtitles + voice")

        self.lang_whisper_combo = QComboBox()
        self.lang_whisper_combo.addItems(["zh", "auto", "ko", "ja", "en", "vi"])

        self.enable_ai_polish_cb = QCheckBox("Use AI polish after Microsoft translation")
        self.enable_ai_polish_cb.setChecked(True)

        self.final_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
        self.final_output_folder_edit.setPlaceholderText("Folder to save final results...")
        browse_final_out_btn = QPushButton("Save To")
        browse_final_out_btn.clicked.connect(self.browse_voice_output_folder)
        final_out_layout = QHBoxLayout()
        final_out_layout.addWidget(self.final_output_folder_edit)
        final_out_layout.addWidget(browse_final_out_btn)

        self.workflow_hint_label = QLabel()
        self.workflow_hint_label.setWordWrap(True)
        self.workflow_hint_label.setObjectName("statusBody")
        self.workflow_status_badge = QLabel("Waiting for video")
        self.workflow_status_badge.setObjectName("statusPill")
        self.next_step_label = QLabel()
        self.next_step_label.setWordWrap(True)
        self.next_step_label.setObjectName("statusHeadline")
        self.readiness_label = QLabel()
        self.readiness_label.setWordWrap(True)
        self.readiness_label.setObjectName("statusBody")

        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(14, 14, 14, 14)
        status_layout.setSpacing(8)
        status_title = QLabel("Guided status")
        status_title.setObjectName("sectionTitle")
        status_layout.addWidget(status_title)
        status_layout.addWidget(self.workflow_status_badge, 0, Qt.AlignLeft)
        status_layout.addWidget(self.next_step_label)
        status_layout.addWidget(self.workflow_hint_label)
        status_layout.addWidget(self.readiness_label)

        self.run_all_btn = QPushButton("Create Vietnamese Output")
        self.run_all_btn.setObjectName("mainActionBtn")
        self.run_all_btn.clicked.connect(self.run_all_pipeline)

        self.export_btn = QPushButton("Export Final Video")
        self.export_btn.setObjectName("mainActionBtn")
        self.export_btn.clicked.connect(self.export_final_video)

        self.preview_5s_btn = QPushButton("Preview 5 Seconds")
        self.preview_5s_btn.clicked.connect(self.preview_five_seconds)

        self.preview_frame_btn = QPushButton("Preview Current Frame")
        self.preview_frame_btn.clicked.connect(self.preview_exact_frame)

        self.open_output_btn = QPushButton("Open Results Folder")
        self.open_output_btn.clicked.connect(lambda: self.open_folder(self.final_output_folder_edit.text()))

        self.stabilize_button(self.run_all_btn, min_width=260)
        self.stabilize_button(self.preview_frame_btn, min_width=260)
        self.stabilize_button(self.preview_5s_btn, min_width=260)
        self.stabilize_button(self.export_btn, min_width=260)
        self.stabilize_button(self.open_output_btn, min_width=260)

        start_actions_layout = QVBoxLayout()
        start_actions_row_1 = QHBoxLayout()
        start_actions_row_1.addWidget(self.run_all_btn)
        start_actions_row_1.addWidget(self.export_btn)
        start_actions_row_2 = QHBoxLayout()
        start_actions_row_2.addWidget(self.preview_frame_btn)
        start_actions_row_2.addWidget(self.preview_5s_btn)
        start_actions_layout.addLayout(start_actions_row_1)
        start_actions_layout.addLayout(start_actions_row_2)

        start_layout.addWidget(QLabel("2. What do you want back?"))
        start_layout.addWidget(self.output_mode_combo)
        start_layout.addWidget(QLabel("3. Source speech language"))
        start_layout.addWidget(self.lang_whisper_combo)
        start_layout.addWidget(QLabel("4. Translation quality"))
        start_layout.addWidget(self.enable_ai_polish_cb)
        start_layout.addWidget(QLabel("5. Save results to"))
        start_layout.addLayout(final_out_layout)
        start_layout.addWidget(status_card)
        start_layout.addLayout(start_actions_layout)
        start_layout.addWidget(self.open_output_btn)
        left_layout.addWidget(start_group)

        workflow_group = QGroupBox("WORKFLOW")
        workflow_layout = QVBoxLayout(workflow_group)
        workflow_text = QLabel(
            "Recommended flow:\n"
            "1. Choose the video.\n"
            "2. Pick the output mode.\n"
            "3. Click 'Create Vietnamese Output'.\n"
            "4. Use 'Preview Current Frame' or 'Preview 5 Seconds' to review the real subtitle render.\n"
            "5. Click 'Export Final Video' when you are happy."
        )
        workflow_text.setWordWrap(True)
        workflow_layout.addWidget(workflow_text)
        left_layout.addWidget(workflow_group)

        # Tabs for a cleaner UX
        self.tabs = QTabWidget()
        tabs = self.tabs
        left_layout.addWidget(tabs, 1)

        tab_prepare = QWidget()
        tab_subtitles = QWidget()
        tab_voice = QWidget()
        tab_tools = QWidget()
        tabs.addTab(tab_prepare, "1. Prepare")
        tabs.addTab(tab_subtitles, "2. Subtitles")
        tabs.addTab(tab_voice, "3. Voice")
        tabs.addTab(tab_tools, "4. Tools")

        prepare_layout = QVBoxLayout(tab_prepare)
        prepare_layout.setSpacing(12)
        subtitles_layout = QVBoxLayout(tab_subtitles)
        subtitles_layout.setSpacing(12)
        voice_tab_layout = QVBoxLayout(tab_voice)
        voice_tab_layout.setSpacing(12)
        tools_layout = QVBoxLayout(tab_tools)
        tools_layout.setSpacing(12)

        # Section 1: Source & Extraction
        audio_group = QGroupBox("STEP 1: PREPARE AUDIO")
        audio_layout = QVBoxLayout(audio_group)
        audio_intro = self.make_helper_label(
            "Use this only when you want to run each step manually. If you are new, the top button 'Create Vietnamese Output' already performs these steps in order."
        )
        audio_layout.addWidget(audio_intro)
        
        # Folder selection for extracted audio
        self.audio_folder_edit = QLineEdit(os.path.join(os.getcwd(), "temp"))
        browse_folder_btn = QPushButton("Target Folder")
        browse_folder_btn.clicked.connect(self.browse_audio_folder)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.audio_folder_edit)
        folder_layout.addWidget(browse_folder_btn)
        
        self.keep_audio_cb = QCheckBox("Keep audio file after completion")
        self.keep_audio_cb.setChecked(True)
        
        extract_btn = QPushButton("Extract Audio")
        extract_btn.setObjectName("mainActionBtn")
        
        vocal_sep_btn = QPushButton("Separate Voice and Background")
        vocal_sep_btn.setObjectName("mainActionBtn")
        self.vocal_sep_btn = vocal_sep_btn
        
        audio_layout.addWidget(QLabel("Temporary audio folder"))
        audio_layout.addLayout(folder_layout)
        audio_layout.addWidget(self.keep_audio_cb)
        audio_layout.addWidget(extract_btn)
        audio_layout.addWidget(vocal_sep_btn)
        prepare_layout.addWidget(audio_group)

        # Section 2: Recognition
        trans_group = QGroupBox("STEP 2: CREATE ORIGINAL SUBTITLES")
        trans_layout = QVBoxLayout(trans_group)
        trans_layout.addWidget(self.make_helper_label("Turn the source speech into an editable subtitle track."))
        
        self.audio_source_edit = QLineEdit()
        self.audio_source_edit.setPlaceholderText("Optional: choose a custom audio file...")
        browse_audio_src_btn = QPushButton("Choose Audio")
        browse_audio_src_btn.clicked.connect(self.browse_audio_source)
        
        audio_src_layout = QHBoxLayout()
        audio_src_layout.addWidget(self.audio_source_edit)
        audio_src_layout.addWidget(browse_audio_src_btn)

        self.srt_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
        browse_srt_folder_btn = QPushButton("SRT Folder")
        browse_srt_folder_btn.clicked.connect(self.browse_srt_output_folder)
        srt_folder_layout = QHBoxLayout()
        srt_folder_layout.addWidget(self.srt_output_folder_edit)
        srt_folder_layout.addWidget(browse_srt_folder_btn)
        
        self.transcript_text = QTextEdit()
        self.transcript_text.setPlaceholderText("The original subtitle transcript will appear here...")
        
        self.transcribe_btn = QPushButton("Create Original Subtitle")
        self.transcribe_btn.setObjectName("mainActionBtn")
        self.stabilize_button(self.transcribe_btn, min_width=320)
        
        trans_layout.addWidget(QLabel("Audio source"))
        trans_layout.addLayout(audio_src_layout)
        trans_layout.addWidget(QLabel("Where to save the original SRT"))
        trans_layout.addLayout(srt_folder_layout)
        trans_layout.addWidget(self.transcript_text)
        trans_layout.addWidget(self.transcribe_btn)
        subtitles_layout.addWidget(trans_group)

        # Section 3: Translation
        translate_group = QGroupBox("STEP 3: VIETNAMESE TRANSLATION")
        translate_layout = QVBoxLayout(translate_group)
        translate_layout.addWidget(self.make_helper_label("Review the Vietnamese text here, edit if needed, then push it to the preview player."))
        
        self.lang_target_combo = QComboBox()
        self.lang_target_combo.addItems(["Vietnamese (vie_Latn)", "English (eng_Latn)"])
        self.lang_target_combo.setCurrentText("Vietnamese (vie_Latn)")
        self.lang_target_combo.setEnabled(False)
        
        self.translated_text = QTextEdit()
        self.translated_text.setPlaceholderText("Vietnamese subtitle text will appear here. You can edit it before export.")
        
        self.translate_btn = QPushButton("Translate to Vietnamese")
        self.translate_btn.setObjectName("mainActionBtn")
        self.stabilize_button(self.translate_btn, min_width=320)

        self.keep_timeline_cb = QCheckBox("Keep the current timeline when editing Vietnamese text")
        self.keep_timeline_cb.setChecked(True)

        self.apply_translated_btn = QPushButton("Apply Edited Subtitle To Preview")
        self.apply_translated_btn.clicked.connect(self.apply_edited_translation)
        self.auto_preview_frame_cb = QCheckBox("Auto refresh exact frame preview")
        self.auto_preview_frame_cb.setChecked(True)

        style_group = QGroupBox("SUBTITLE LOOK")
        style_layout = QVBoxLayout(style_group)

        self.subtitle_font_combo = QComboBox()
        self.subtitle_font_combo.setEditable(True)
        self.subtitle_font_combo.addItems(["Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"])
        self.subtitle_font_combo.setCurrentText("Segoe UI")

        self.subtitle_font_size_spin = QSpinBox()
        self.subtitle_font_size_spin.setRange(12, 72)
        self.subtitle_font_size_spin.setValue(20)

        self.subtitle_align_combo = QComboBox()
        self.subtitle_align_combo.addItems(["Bottom Center", "Bottom Left", "Bottom Right"])
        self.subtitle_align_combo.setCurrentText("Bottom Center")

        self.subtitle_x_offset_spin = QSpinBox()
        self.subtitle_x_offset_spin.setRange(-400, 400)
        self.subtitle_x_offset_spin.setValue(0)

        self.subtitle_bottom_offset_spin = QSpinBox()
        self.subtitle_bottom_offset_spin.setRange(0, 300)
        self.subtitle_bottom_offset_spin.setValue(30)

        self.subtitle_color_btn = QPushButton("Text Color: #FFFFFF")
        self.subtitle_color_btn.clicked.connect(self.choose_subtitle_color)
        self.subtitle_color_hex = "#FFFFFF"

        style_row_1 = QHBoxLayout()
        style_row_1.addWidget(QLabel("Font"))
        style_row_1.addWidget(self.subtitle_font_combo)
        style_row_1.addWidget(QLabel("Size"))
        style_row_1.addWidget(self.subtitle_font_size_spin)

        style_row_2 = QHBoxLayout()
        style_row_2.addWidget(QLabel("Position"))
        style_row_2.addWidget(self.subtitle_align_combo)
        style_row_2.addWidget(QLabel("X Offset"))
        style_row_2.addWidget(self.subtitle_x_offset_spin)
        style_row_2.addWidget(QLabel("Bottom"))
        style_row_2.addWidget(self.subtitle_bottom_offset_spin)

        style_layout.addLayout(style_row_1)
        style_layout.addLayout(style_row_2)
        style_layout.addWidget(self.subtitle_color_btn)
        
        translate_layout.addWidget(QLabel("Output language"))
        translate_layout.addWidget(self.lang_target_combo)
        translate_layout.addWidget(style_group)
        translate_layout.addWidget(self.translated_text)
        translate_layout.addWidget(self.translate_btn)
        translate_layout.addWidget(self.keep_timeline_cb)
        translate_layout.addWidget(self.apply_translated_btn)
        translate_layout.addWidget(self.auto_preview_frame_cb)
        subtitles_layout.addWidget(translate_group)

        # Section 4: Voiceover (TTS) + Mix
        voice_group = QGroupBox("STEP 4: VIETNAMESE VOICE")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.addWidget(self.make_helper_label("Only needed for voice output modes. You can skip this entire card when exporting subtitles only."))

        self.voice_name_combo = QComboBox()
        self.voice_name_combo.addItems([
            "vi-VN-HoaiMyNeural",
            "vi-VN-NamMinhNeural",
        ])

        self.bg_music_edit = QLineEdit()
        self.bg_music_edit.setPlaceholderText("Optional: background/no_vocals audio...")
        browse_bg_btn = QPushButton("Choose Background")
        browse_bg_btn.clicked.connect(self.browse_background_audio)
        bg_layout = QHBoxLayout()
        bg_layout.addWidget(self.bg_music_edit)
        bg_layout.addWidget(browse_bg_btn)

        self.mixed_audio_edit = QLineEdit()
        self.mixed_audio_edit.setPlaceholderText("Optional: use an existing mixed audio file...")
        browse_mixed_btn = QPushButton("Choose Mixed Audio")
        browse_mixed_btn.clicked.connect(self.browse_existing_mixed_audio)
        mixed_layout = QHBoxLayout()
        mixed_layout.addWidget(self.mixed_audio_edit)
        mixed_layout.addWidget(browse_mixed_btn)

        self.use_generated_audio_radio = QRadioButton("Use generated Vietnamese voice")
        self.use_existing_audio_radio = QRadioButton("Use existing mixed audio")
        self.use_generated_audio_radio.setChecked(True)
        self.audio_source_hint_label = self.make_helper_label(
            "Preview and export will use the generated voice by default. Switch to existing mixed audio when you want to override it."
        )

        self.voice_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
        browse_voice_out_btn = QPushButton("Output Folder")
        browse_voice_out_btn.clicked.connect(self.browse_voice_output_folder)
        voice_out_layout = QHBoxLayout()
        voice_out_layout.addWidget(self.voice_output_folder_edit)
        voice_out_layout.addWidget(browse_voice_out_btn)

        # Advanced options (collapsed by default)
        adv_group = QGroupBox("Advanced Voice Settings")
        adv_group.setCheckable(True)
        adv_group.setChecked(False)
        adv_layout = QVBoxLayout(adv_group)

        gains_layout = QHBoxLayout()
        self.voice_gain_spin = QDoubleSpinBox()
        self.voice_gain_spin.setRange(-30.0, 30.0)
        self.voice_gain_spin.setSingleStep(1.0)
        self.voice_gain_spin.setValue(6.0)
        self.bg_gain_spin = QDoubleSpinBox()
        self.bg_gain_spin.setRange(-30.0, 30.0)
        self.bg_gain_spin.setSingleStep(1.0)
        self.bg_gain_spin.setValue(-3.0)
        gains_layout.addWidget(QLabel("Voice Gain (dB):"))
        gains_layout.addWidget(self.voice_gain_spin)
        gains_layout.addWidget(QLabel("BG Gain (dB):"))
        gains_layout.addWidget(self.bg_gain_spin)
        adv_layout.addLayout(gains_layout)

        adv_layout.addWidget(QLabel("Where to save intermediate voice files"))
        adv_layout.addLayout(voice_out_layout)

        self.voiceover_btn = QPushButton("Create Vietnamese Voice")
        self.voiceover_btn.setObjectName("mainActionBtn")
        self.stabilize_button(self.voiceover_btn, min_width=320)

        self.preview_btn = QPushButton("Preview With Vietnamese Voice")
        self.preview_btn.clicked.connect(self.preview_video_with_mixed_audio)
        self.stabilize_button(self.preview_btn, min_width=320)

        voice_layout.addWidget(QLabel("Voice"))
        voice_layout.addWidget(self.voice_name_combo)
        voice_layout.addWidget(QLabel("Audio source for preview/export"))
        voice_layout.addWidget(self.use_generated_audio_radio)
        voice_layout.addWidget(self.use_existing_audio_radio)
        voice_layout.addWidget(self.audio_source_hint_label)
        voice_layout.addWidget(QLabel("Background audio"))
        voice_layout.addLayout(bg_layout)
        voice_layout.addWidget(QLabel("Existing mixed audio"))
        voice_layout.addLayout(mixed_layout)
        voice_layout.addWidget(adv_group)
        voice_layout.addWidget(self.voiceover_btn)
        voice_layout.addWidget(self.preview_btn)
        voice_tab_layout.addWidget(voice_group)

        # Section: Processed files quick view
        artifacts_group = QGroupBox("RESULTS AND FILES")
        artifacts_layout = QVBoxLayout(artifacts_group)
        artifacts_layout.addWidget(self.make_helper_label("Open generated files quickly when you want to inspect or reuse outputs."))

        self.show_artifacts_btn = QPushButton("Show Processed Files")
        self.show_artifacts_btn.clicked.connect(self.show_processed_files)

        self.open_temp_btn = QPushButton("Open Temp Folder")
        self.open_temp_btn.clicked.connect(lambda: self.open_folder(self.audio_folder_edit.text()))

        artifacts_layout.addWidget(self.show_artifacts_btn)
        artifacts_layout.addWidget(self.open_temp_btn)
        artifacts_layout.addWidget(self.open_output_btn)
        tools_layout.addWidget(artifacts_group)

        # Log / details panel
        log_group = QGroupBox("LOG")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Errors and detailed logs will appear here...")
        log_btns = QHBoxLayout()
        self.log_copy_btn = QPushButton("Copy Log")
        self.log_copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.log_view.toPlainText()))
        self.log_clear_btn = QPushButton("Clear Log")
        self.log_clear_btn.clicked.connect(self.clear_log)
        log_btns.addWidget(self.log_copy_btn)
        log_btns.addWidget(self.log_clear_btn)
        log_layout.addWidget(self.log_view)
        log_layout.addLayout(log_btns)
        tools_layout.addWidget(log_group)

        tools_layout.addStretch()
        tools_layout.addWidget(QLabel("CapCap guided workflow"))

        prepare_layout.addStretch()
        subtitles_layout.addStretch()
        voice_tab_layout.addStretch()


        # --- RIGHT PANEL ---
        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        side_info_card = QFrame()
        side_info_card.setObjectName("sideInfoCard")
        side_info_layout = QVBoxLayout(side_info_card)
        side_info_layout.setContentsMargins(14, 12, 14, 12)
        side_info_layout.setSpacing(4)
        preview_title = QLabel("Live preview")
        preview_title.setObjectName("sectionTitle")
        self.preview_context_label = QLabel("Choose a video to start previewing. Subtitle and voice status will appear here as you work.")
        self.preview_context_label.setWordWrap(True)
        self.preview_context_label.setObjectName("previewContextLabel")
        self.frame_preview_status_label = QLabel("Exact frame preview updates here when available.")
        self.frame_preview_status_label.setWordWrap(True)
        self.frame_preview_status_label.setObjectName("helperLabel")
        self.frame_preview_image_label = QLabel("No frame preview yet")
        self.frame_preview_image_label.setAlignment(Qt.AlignCenter)
        self.frame_preview_image_label.setMinimumHeight(170)
        self.frame_preview_image_label.setStyleSheet(
            "background-color: #0b1220; border: 1px dashed #325173; border-radius: 10px; color: #7f93ad; padding: 12px;"
        )
        side_info_layout.addWidget(preview_title)
        side_info_layout.addWidget(self.preview_context_label)
        side_info_layout.addWidget(self.frame_preview_status_label)
        side_info_layout.addWidget(self.frame_preview_image_label)

        # VideoView contains both the video and overlay inside one QGraphicsScene.
        # This avoids all OpenGL surface layering issues.
        self.video_view = VideoView()
        self.video_view.setMinimumHeight(400)
        
        # New Timeline Component
        self.timeline = TimelineWidget()
        self.timeline.seekRequested.connect(self.set_position)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("font-weight: bold; min-width: 100px; color: #6ee7d6;")
        
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.stop_btn = QPushButton("Reset")
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.time_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        
        right_layout.addWidget(side_info_card)
        right_layout.addWidget(self.video_view, 5)
        right_layout.addWidget(self.timeline)
        right_layout.addLayout(controls_layout)
        right_layout.addWidget(QLabel("Process Status:"))
        right_layout.addWidget(self.progress_bar)

        # Add panels to main layout
        main_layout.addWidget(scroll_area)
        main_layout.addWidget(right_panel, 1)

        # Connect signals
        self.extract_btn = extract_btn
        self.extract_btn.clicked.connect(self.run_extraction)
        self.vocal_sep_btn.clicked.connect(self.run_vocal_separation)
        self.transcribe_btn.clicked.connect(self.run_transcription)
        self.translate_btn.clicked.connect(self.run_translation)
        self.voiceover_btn.clicked.connect(self.run_voiceover)
        self.output_mode_combo.currentTextChanged.connect(self.on_output_mode_changed)
        self.enable_ai_polish_cb.toggled.connect(lambda _: self.on_output_mode_changed(self.output_mode_combo.currentText()))
        self.final_output_folder_edit.textChanged.connect(self.voice_output_folder_edit.setText)
        self.final_output_folder_edit.textChanged.connect(self.srt_output_folder_edit.setText)
        self.video_path_edit.textChanged.connect(self.refresh_ui_state)
        self.audio_source_edit.textChanged.connect(self.refresh_ui_state)
        self.bg_music_edit.textChanged.connect(self.refresh_ui_state)
        self.mixed_audio_edit.textChanged.connect(self.refresh_ui_state)
        self.use_generated_audio_radio.toggled.connect(self.on_audio_source_mode_changed)
        self.use_existing_audio_radio.toggled.connect(self.on_audio_source_mode_changed)
        self.transcript_text.textChanged.connect(self.refresh_ui_state)
        self.translated_text.textChanged.connect(self.refresh_ui_state)
        self.translated_text.textChanged.connect(self.schedule_auto_frame_preview)
        self.auto_preview_frame_cb.toggled.connect(self.on_auto_preview_toggled)
        self.subtitle_font_combo.currentTextChanged.connect(self.update_subtitle_preview_style)
        self.subtitle_font_size_spin.valueChanged.connect(self.update_subtitle_preview_style)
        self.subtitle_align_combo.currentTextChanged.connect(self.update_subtitle_preview_style)
        self.subtitle_x_offset_spin.valueChanged.connect(self.update_subtitle_preview_style)
        self.subtitle_bottom_offset_spin.valueChanged.connect(self.update_subtitle_preview_style)
        
        # Initial positioning
        QTimer.singleShot(100, self.video_view.reposition_subtitle)

        # Data
        self.current_segments = []
        self.current_translated_segments = []
        self._frame_preview_running = False
        self._pending_auto_frame_preview = False
        self._show_dialog_on_frame_preview = False
        self.auto_frame_preview_timer = QTimer(self)
        self.auto_frame_preview_timer.setSingleShot(True)
        self.auto_frame_preview_timer.setInterval(700)
        self.auto_frame_preview_timer.timeout.connect(self.trigger_auto_frame_preview)
        self.seek_frame_preview_timer = QTimer(self)
        self.seek_frame_preview_timer.setSingleShot(True)
        self.seek_frame_preview_timer.setInterval(300)
        self.seek_frame_preview_timer.timeout.connect(self.trigger_seek_frame_preview)
        self.last_extracted_audio = ""
        self.last_vocals_path = ""
        self.last_music_path = ""
        self.last_original_srt_path = ""
        self.last_translated_srt_path = ""
        self.last_voice_vi_path = ""
        self.last_mixed_vi_path = ""
        self.last_preview_video_path = ""
        self.last_exported_video_path = ""
        self.last_exact_preview_5s_path = ""
        self.last_exact_preview_frame_path = ""
        self.subtitle_export_font_scale = 1.3
        self.use_exact_subtitle_preview = True

        # Initial button states
        self.update_subtitle_preview_style()
        self.on_output_mode_changed(self.output_mode_combo.currentText())
        self.refresh_ui_state()

    # -----------------------------
    # Logging + error helpers
    # -----------------------------
    def log(self, message: str):
        if not message:
            return
        self._log_lines.append(str(message))
        # keep last ~500 lines
        if len(self._log_lines) > 500:
            self._log_lines = self._log_lines[-500:]
        self.log_view.setPlainText("\n".join(self._log_lines))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def clear_log(self):
        self._log_lines = []
        if hasattr(self, "log_view"):
            self.log_view.setPlainText("")

    def show_error(self, title: str, short_msg: str, details: str = ""):
        if details:
            self.log(f"[{title}] {details}")
            QMessageBox.critical(self, title, f"{short_msg}\n\n(See LOG tab for details)")
        else:
            QMessageBox.critical(self, title, short_msg)

    def stabilize_button(self, button: QPushButton, min_width: int = 220, min_height: int = 42):
        button.setMinimumWidth(min_width)
        button.setMinimumHeight(min_height)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def make_helper_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("helperLabel")
        return label

    def using_existing_audio_source(self) -> bool:
        return hasattr(self, "use_existing_audio_radio") and self.use_existing_audio_radio.isChecked()

    def resolve_selected_audio_path(self) -> str:
        if self.using_existing_audio_source():
            return self.mixed_audio_edit.text().strip()
        return (
            self.processed_artifacts.get("mixed_vi")
            or self.last_mixed_vi_path
            or self.last_voice_vi_path
            or ""
        ).strip()

    def on_audio_source_mode_changed(self):
        if not hasattr(self, "audio_source_hint_label"):
            return
        if self.using_existing_audio_source():
            self.audio_source_hint_label.setText(
                "Preview and export will use the file in 'Existing mixed audio'. Generated voice and background settings are ignored until you switch back."
            )
        else:
            self.audio_source_hint_label.setText(
                "Preview and export will use the audio generated by CapCap. Existing mixed audio is ignored until you switch to it."
            )
        self.refresh_ui_state()

    def on_auto_preview_toggled(self, checked: bool):
        if checked:
            self.schedule_auto_frame_preview()
        else:
            self.auto_frame_preview_timer.stop()
            self.seek_frame_preview_timer.stop()

    def schedule_auto_frame_preview(self):
        if not hasattr(self, "auto_preview_frame_cb") or not self.auto_preview_frame_cb.isChecked():
            return
        if getattr(self, "_pipeline_active", False):
            return
        if not self.video_path_edit.text().strip() or not self.get_active_segments():
            return
        self.frame_preview_status_label.setText("Refreshing exact frame preview...")
        self.auto_frame_preview_timer.start()

    def trigger_auto_frame_preview(self):
        self.start_exact_frame_preview(show_dialog=False)

    def schedule_seek_frame_preview(self):
        if not hasattr(self, "auto_preview_frame_cb") or not self.auto_preview_frame_cb.isChecked():
            return
        if getattr(self, "_pipeline_active", False):
            return
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            return
        if not self.video_path_edit.text().strip() or not self.get_active_segments():
            return
        self.frame_preview_status_label.setText("Updating exact frame preview for the selected timeline position...")
        self.seek_frame_preview_timer.start()

    def trigger_seek_frame_preview(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            return
        self.start_exact_frame_preview(show_dialog=False)

    def update_frame_preview_thumbnail(self, image_path: str):
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.frame_preview_image_label.setText("Could not load frame preview")
            self.frame_preview_image_label.setPixmap(QPixmap())
            return
        scaled = pixmap.scaled(320, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.frame_preview_image_label.setPixmap(scaled)
        self.frame_preview_image_label.setText("")
        self.frame_preview_status_label.setText(f"Exact frame preview synced at {time.strftime('%H:%M:%S')}.")

    def cleanup_file_if_exists(self, path: str):
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def get_output_mode_key(self):
        value = self.output_mode_combo.currentText() if hasattr(self, "output_mode_combo") else "Vietnamese subtitles + voice"
        mapping = {
            "Vietnamese subtitles only": "subtitle",
            "Vietnamese voice only": "voice",
            "Vietnamese subtitles + voice": "both",
        }
        return mapping.get(value, "both")

    def is_ai_polish_enabled(self):
        return bool(getattr(self, "enable_ai_polish_cb", None) and self.enable_ai_polish_cb.isChecked())

    def on_output_mode_changed(self, value: str):
        mode = self.get_output_mode_key()
        hints = {
            "subtitle": "This mode will create Vietnamese subtitles first. You can review the subtitle text, then export a subtitled video.",
            "voice": "This mode will create a Vietnamese voice track, keep or reuse background audio, then export a video with new audio.",
            "both": "This mode will create both Vietnamese subtitles and Vietnamese voice, then combine them into one final video.",
        }
        polish_hint = (
            " AI polish is ON, so Microsoft translation will be refined for wording and context."
            if self.is_ai_polish_enabled()
            else " AI polish is OFF, so you can inspect the raw Microsoft translation."
        )
        self.workflow_hint_label.setText(hints.get(mode, "Choose an output mode to begin.") + polish_hint)

        show_voice = mode in ("voice", "both")
        self.voiceover_btn.setVisible(show_voice)
        self.preview_btn.setVisible(show_voice)
        self.mixed_audio_edit.setEnabled(show_voice)
        self.use_generated_audio_radio.setVisible(show_voice)
        self.use_existing_audio_radio.setVisible(show_voice)
        self.audio_source_hint_label.setVisible(show_voice)

        export_labels = {
            "subtitle": "Export Subtitled Video",
            "voice": "Export Vietnamese Voice Video",
            "both": "Export Final Video",
        }
        self.export_btn.setText(export_labels.get(mode, "Export Final Video"))
        self.refresh_ui_state()

    def update_guidance_panel(self):
        v_ok = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        has_original = bool(self.transcript_text.toPlainText().strip())
        has_translated = bool(self.translated_text.toPlainText().strip())
        has_applied_subtitles = bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
        selected_audio_path = self.resolve_selected_audio_path()
        has_voice_audio = bool(selected_audio_path and os.path.exists(selected_audio_path))
        mode = self.get_output_mode_key()

        if not v_ok:
            badge = "Waiting for video"
            headline = "Step 1: choose the source video."
            readiness = "After you select a video, CapCap can guide the full flow automatically."
        elif not has_original:
            badge = "Ready to process"
            headline = "Next best step: create the original subtitle track."
            readiness = "Use 'Create Vietnamese Output' for the guided flow, or go to '1. Prepare' and run steps manually."
        elif mode in ("subtitle", "both") and not has_translated:
            badge = "Subtitle review"
            headline = "Original subtitles are ready. Translate them to Vietnamese next."
            readiness = "You can edit the Vietnamese text before applying it to preview and export."
        elif mode in ("voice", "both") and not has_voice_audio:
            badge = "Voice step"
            headline = "Subtitles are ready. Generate Vietnamese voice when you want dubbed output."
            readiness = "If you already have a mixed audio file, load it in the Voice tab and preview immediately."
        else:
            badge = "Ready to preview/export"
            headline = "The core assets are ready. Preview the result, then export when it looks right."
            readiness = "Use frame preview for subtitle placement, 5-second preview for motion/context, then export the final video."

        if getattr(self, "_pipeline_active", False):
            badge = "Processing"
            headline = "CapCap is running the guided pipeline for you."
            readiness = "You can watch progress on the right; the UI will unlock the next actions automatically."

        status_summary = [
            f"Video: {'Ready' if v_ok else 'Missing'}",
            f"Original subtitles: {'Ready' if has_original else 'Pending'}",
            f"Vietnamese subtitles: {'Ready' if has_applied_subtitles or has_translated else 'Pending'}",
            f"Vietnamese voice: {'Ready' if has_voice_audio else 'Optional / Pending'}",
        ]

        self.workflow_status_badge.setText(badge)
        self.next_step_label.setText(headline)
        self.readiness_label.setText(" | ".join(status_summary) + f"\nMode: {self.output_mode_combo.currentText()}")
        self.update_preview_context_label(has_applied_subtitles or has_translated, has_voice_audio)

    def update_preview_context_label(self, has_subtitles: bool, has_voice_audio: bool):
        video_ready = bool(self.video_path_edit.text().strip())
        if not video_ready:
            text = "Choose a video to start previewing. Subtitle and voice status will appear here as you work."
        else:
            subtitle_source = "Vietnamese review track" if self.current_translated_segments else ("original subtitle track" if self.current_segments else "no subtitle track yet")
            audio_source = "existing mixed audio" if self.using_existing_audio_source() else "generated Vietnamese voice"
            text = (
                f"Preview is using {subtitle_source}. "
                f"Audio source: {audio_source}. "
                f"Vietnamese subtitles: {'available' if has_subtitles else 'not ready yet'}. "
                f"Vietnamese voice: {'available' if has_voice_audio else 'not ready yet'}."
            )
        self.preview_context_label.setText(text)

    def choose_subtitle_color(self):
        color = QColorDialog.getColor(QColor(self.subtitle_color_hex), self, "Choose Subtitle Color")
        if not color.isValid():
            return
        self.subtitle_color_hex = color.name().upper()
        self.subtitle_color_btn.setText(f"Text Color: {self.subtitle_color_hex}")
        self.update_subtitle_preview_style()

    def update_subtitle_preview_style(self):
        if not hasattr(self, "video_view"):
            return
        item = self.video_view.subtitle_item
        source_h = max(1, getattr(self.video_view, "video_source_height", 0) or 1080)
        preview_rect = self.video_view.get_video_content_rect()
        preview_h = max(1.0, preview_rect.height() or float(self.video_view.height()) or 1.0)
        export_font_size = int(self.subtitle_font_size_spin.value())
        preview_font_size = max(10, int(round(export_font_size * (preview_h / source_h))))
        item.set_style(
            font_name=self.subtitle_font_combo.currentText().strip() or "Segoe UI",
            font_size=preview_font_size,
            font_color=QColor(self.subtitle_color_hex),
        )
        item.set_alignment(self.subtitle_align_combo.currentText())
        item.set_positioning(
            x_offset=int(self.subtitle_x_offset_spin.value()),
            bottom_offset=int(self.subtitle_bottom_offset_spin.value()),
        )
        self.video_view.reposition_subtitle()
        self.schedule_auto_frame_preview()

    def get_subtitle_export_style(self):
        alignment_map = {
            "Bottom Left": 1,
            "Bottom Center": 2,
            "Bottom Right": 3,
        }
        export_font_size = max(1, int(round(self.subtitle_font_size_spin.value() * self.subtitle_export_font_scale)))
        return {
            "font_name": self.subtitle_font_combo.currentText().strip() or "Arial",
            "font_size": export_font_size,
            "font_color": self._hex_to_ass_color(self.subtitle_color_hex),
            "alignment": alignment_map.get(self.subtitle_align_combo.currentText(), 2),
            "margin_v": int(self.subtitle_bottom_offset_spin.value()),
        }

    def refresh_video_dimensions(self, path: str):
        try:
            if path and os.path.exists(path):
                width, height = get_video_dimensions(path)
                self.video_view.set_video_dimensions(width, height)
        except Exception:
            pass

    def _hex_to_ass_color(self, hex_color: str) -> str:
        color = QColor(hex_color)
        return f"&H00{color.blue():02X}{color.green():02X}{color.red():02X}"

    def export_final_video(self):
        video_path = self.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "Error", "Please choose a video first.")
            return

        mode = self.get_output_mode_key()
        out_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(out_dir, exist_ok=True)

        video_name = os.path.splitext(os.path.basename(video_path))[0]
        translated_srt_path = self.last_translated_srt_path
        chosen_audio = self.resolve_selected_audio_path()

        if mode in ("subtitle", "both"):
            if not translated_srt_path or not os.path.exists(translated_srt_path):
                QMessageBox.warning(self, "Error", "Vietnamese subtitle file not found. Please run translation first.")
                return

        if mode in ("voice", "both"):
            if not chosen_audio or not os.path.exists(chosen_audio):
                QMessageBox.warning(
                    self,
                    "Error",
                    "Selected audio source is not ready. Create Vietnamese voice first, or switch to 'Use existing mixed audio' and choose a valid file.",
                )
                return

        if mode == "subtitle":
            output_path = os.path.join(out_dir, f"{video_name}_sub_vi.mp4")
        elif mode == "voice":
            output_path = os.path.join(out_dir, f"{video_name}_voice_vi.mp4")
        else:
            output_path = os.path.join(out_dir, f"{video_name}_final_vi.mp4")

        self.export_btn.setEnabled(False)
        self.export_btn.setText("Exporting...")
        self.progress_bar.setValue(96)

        self.export_thread = FinalExportWorker(
            video_path=video_path,
            output_path=output_path,
            mode=mode,
            srt_path=translated_srt_path,
            audio_path=chosen_audio,
            subtitle_style=self.get_subtitle_export_style(),
        )
        self.export_thread.finished.connect(self.on_export_finished)
        self.export_thread.start()

    def preview_five_seconds(self):
        video_path = self.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "Error", "Please choose a video first.")
            return

        mode = self.get_output_mode_key()
        out_dir = self.final_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
        os.makedirs(out_dir, exist_ok=True)

        translated_srt_path = self.last_translated_srt_path
        chosen_audio = self.resolve_selected_audio_path()

        if mode in ("subtitle", "both"):
            if not translated_srt_path or not os.path.exists(translated_srt_path):
                QMessageBox.warning(self, "Error", "Vietnamese subtitle file not found. Please run translation first.")
                return

        if mode in ("voice", "both") and (not chosen_audio or not os.path.exists(chosen_audio)):
            QMessageBox.warning(
                self,
                "Error",
                "Selected audio source is not ready. Create Vietnamese voice first, or switch to 'Use existing mixed audio' and choose a valid file.",
            )
            return

        start_seconds = max(0.0, self.media_player.position() / 1000.0)
        duration_seconds = 5.0
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        self.cleanup_file_if_exists(self.last_exact_preview_5s_path)
        preview_output = os.path.join(out_dir, f"{video_name}_preview5s_{int(time.time())}.mp4")
        preview_srt_path = ""

        if mode in ("subtitle", "both"):
            preview_srt_path = self.build_subtitle_preview_srt(start_seconds, duration_seconds)
            if not preview_srt_path:
                QMessageBox.warning(self, "Error", "Could not build the 5-second subtitle preview clip.")
                return

        self.preview_5s_btn.setEnabled(False)
        self.preview_5s_btn.setText("Rendering 5s...")
        self.progress_bar.setValue(92)

        try:
            self.media_player.stop()
            self.media_player.setSource(QUrl())
        except Exception:
            pass

        self.quick_preview_thread = QuickPreviewWorker(
            video_path=video_path,
            output_path=preview_output,
            mode=mode,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            srt_path=preview_srt_path,
            audio_path=chosen_audio,
            subtitle_style=self.get_subtitle_export_style(),
        )
        self.quick_preview_thread.finished.connect(self.on_quick_preview_ready)
        self.quick_preview_thread.start()

    def start_exact_frame_preview(self, show_dialog: bool = True):
        video_path = self.video_path_edit.text().strip()
        if not video_path or not os.path.exists(video_path):
            if show_dialog:
                QMessageBox.warning(self, "Error", "Please choose a video first.")
            return

        preview_srt_path = self.build_full_active_subtitle_srt()
        if not preview_srt_path:
            if show_dialog:
                QMessageBox.warning(self, "Error", "No active subtitle track is available for frame preview.")
            return

        if self._frame_preview_running:
            self._pending_auto_frame_preview = True
            self._show_dialog_on_frame_preview = self._show_dialog_on_frame_preview or show_dialog
            return

        timestamp_seconds = max(0.0, self.media_player.position() / 1000.0)
        out_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(out_dir, exist_ok=True)
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        self.cleanup_file_if_exists(self.last_exact_preview_frame_path)
        frame_output = os.path.join(out_dir, f"{video_name}_preview_frame_{int(time.time())}.png")

        self._frame_preview_running = True
        self._show_dialog_on_frame_preview = show_dialog
        self.preview_frame_btn.setEnabled(False)
        self.preview_frame_btn.setText("Rendering frame...")
        self.progress_bar.setValue(90)
        self.frame_preview_status_label.setText("Rendering exact frame preview...")

        self.frame_preview_thread = ExactFramePreviewWorker(
            video_path=video_path,
            output_path=frame_output,
            timestamp_seconds=timestamp_seconds,
            srt_path=preview_srt_path,
            subtitle_style=self.get_subtitle_export_style(),
        )
        self.frame_preview_thread.finished.connect(self.on_exact_frame_ready)
        self.frame_preview_thread.start()

    def preview_exact_frame(self):
        self.start_exact_frame_preview(show_dialog=True)

    def build_subtitle_preview_srt(self, start_seconds: float, duration_seconds: float):
        segments = self.get_active_segments()
        if not segments:
            return ""

        clipped = []
        end_seconds = start_seconds + duration_seconds
        for seg in segments:
            seg_start = float(seg["start"])
            seg_end = float(seg["end"])
            if seg_end < start_seconds or seg_start > end_seconds:
                continue
            clipped.append(
                {
                    "start": max(0.0, seg_start - start_seconds),
                    "end": min(duration_seconds, seg_end - start_seconds),
                    "text": seg.get("text", ""),
                }
            )

        if not clipped:
            return ""

        preview_srt_path = os.path.join(os.getcwd(), "temp", "preview_subtitle_5s.srt")
        self.cleanup_file_if_exists(preview_srt_path)
        generate_srt(clipped, preview_srt_path)
        return preview_srt_path

    def build_full_active_subtitle_srt(self):
        segments = self.get_active_segments()
        if not segments:
            return ""
        preview_srt_path = os.path.join(os.getcwd(), "temp", "preview_subtitle_full.srt")
        self.cleanup_file_if_exists(preview_srt_path)
        generate_srt(segments, preview_srt_path)
        return preview_srt_path

    def on_export_finished(self, output_path, error):
        self.export_btn.setEnabled(True)
        self.on_output_mode_changed(self.output_mode_combo.currentText())
        self.progress_bar.setValue(100)

        if error:
            self.show_error("Error", "Final export failed.", error)
            return

        if output_path and os.path.exists(output_path):
            self.last_exported_video_path = output_path
            self.processed_artifacts["final_video"] = output_path
            QMessageBox.information(self, "Success", f"Final video exported successfully:\n\n{output_path}")

    def on_quick_preview_ready(self, output_path, error):
        self.preview_5s_btn.setEnabled(True)
        self.preview_5s_btn.setText("Preview 5 Seconds")
        self.progress_bar.setValue(100)

        if error:
            self.show_error("Error", "5-second preview failed.", error)
            return

        if output_path and os.path.exists(output_path):
            self.last_exact_preview_5s_path = output_path
            self.last_preview_video_path = output_path
            self.processed_artifacts["preview_video_5s"] = output_path
            self.refresh_video_dimensions(output_path)
            self.media_player.setSource(QUrl.fromLocalFile(output_path))
            self.media_player.setPosition(0)
            self.play_btn.setText("Play")
            QMessageBox.information(
                self,
                "Preview Ready",
                f"Generated a 5-second preview clip with the current export style:\n\n{output_path}",
            )

    def on_exact_frame_ready(self, output_path, error):
        self._frame_preview_running = False
        self.preview_frame_btn.setEnabled(True)
        self.preview_frame_btn.setText("Preview Current Frame")
        self.progress_bar.setValue(100)

        if error:
            self.show_error("Error", "Exact frame preview failed.", error)
            self.frame_preview_status_label.setText("Exact frame preview failed. You can try again.")
        elif output_path and os.path.exists(output_path):
            self.last_exact_preview_frame_path = output_path
            self.processed_artifacts["preview_frame"] = output_path
            self.update_frame_preview_thumbnail(output_path)
            if self._show_dialog_on_frame_preview:
                self.show_frame_preview_dialog(output_path)

        self._show_dialog_on_frame_preview = False
        if self._pending_auto_frame_preview:
            self._pending_auto_frame_preview = False
            if self.auto_preview_frame_cb.isChecked():
                self.schedule_auto_frame_preview()

    def show_frame_preview_dialog(self, image_path: str):
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview Current Frame")
        dialog.resize(720, 820)

        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(660, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(scaled)
        scroll.setWidget(label)
        layout.addWidget(scroll)

        path_label = QLabel(image_path)
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        dialog.exec()

    # -----------------------------
    # Subtitle source handling
    # -----------------------------
    def get_active_segments(self):
        return self.current_translated_segments or self.current_segments or []

    def apply_segments_to_timeline(self):
        segs = self.get_active_segments()
        self.timeline.set_segments(segs if segs else [])
        self.video_view.subtitle_item.hide()

    def refresh_ui_state(self):
        """Basic enable/disable rules to guide user flow."""
        v_ok = bool(self.video_path_edit.text().strip()) and os.path.exists(self.video_path_edit.text().strip())
        a_ok = bool(self.audio_source_edit.text().strip()) and os.path.exists(self.audio_source_edit.text().strip())
        has_translated_text = bool(self.translated_text.toPlainText().strip())
        selected_audio_path = self.resolve_selected_audio_path()
        has_voice_audio = bool(selected_audio_path and os.path.exists(selected_audio_path))
        mode = self.get_output_mode_key()
        can_export = False
        if mode == "subtitle":
            can_export = v_ok and bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
        elif mode == "voice":
            can_export = v_ok and has_voice_audio
        else:
            can_export = (
                v_ok
                and has_voice_audio
                and bool(self.last_translated_srt_path and os.path.exists(self.last_translated_srt_path))
            )

        self.extract_btn.setEnabled(v_ok)
        self.vocal_sep_btn.setEnabled(a_ok)
        self.transcribe_btn.setEnabled(a_ok)
        self.translate_btn.setEnabled(bool(self.transcript_text.toPlainText().strip()))
        self.apply_translated_btn.setEnabled(has_translated_text)
        generated_mode = not self.using_existing_audio_source()
        self.voiceover_btn.setEnabled(has_translated_text and generated_mode and mode in ("voice", "both"))
        self.preview_btn.setEnabled(v_ok and has_voice_audio and mode in ("voice", "both"))
        if hasattr(self, "voice_name_combo"):
            self.voice_name_combo.setEnabled(generated_mode and mode in ("voice", "both"))
            self.bg_music_edit.setEnabled(generated_mode and mode in ("voice", "both"))
        self.run_all_btn.setEnabled(v_ok and not self._pipeline_active)
        self.preview_frame_btn.setEnabled(v_ok and bool(self.get_active_segments()))
        self.preview_5s_btn.setEnabled(v_ok)
        self.export_btn.setEnabled(can_export)
        if hasattr(self, "tabs"):
            self.tabs.setTabEnabled(1, v_ok)
            self.tabs.setTabEnabled(2, v_ok and mode in ("voice", "both"))
        self.update_guidance_panel()

    def run_extraction(self):
        v_path = self.video_path_edit.text()
        if not v_path: return
        
        target_dir = self.audio_folder_edit.text()
        file_basename = os.path.splitext(os.path.basename(v_path))[0]
        a_path = os.path.join(target_dir, file_basename + ".wav")
        
        self.progress_bar.setValue(10)
        self.extraction_thread = ExtractionWorker(v_path, a_path)
        self.extraction_thread.finished.connect(self.on_extraction_finished)
        self.extraction_thread.start()

    def on_extraction_finished(self, success, path):
        self.progress_bar.setValue(30)
        self.extract_btn.setEnabled(True)
        if success:
            self.last_extracted_audio = path
            self.audio_source_edit.setText(path)
            self.processed_artifacts["audio_extracted"] = path
            QMessageBox.information(self, "Success", "Audio extraction completed!")
        else:
            self.show_error("Error", "Extraction failed.", str(path))
            self._pipeline_fail("Extraction failed.")
            return

        self.refresh_ui_state()
        self._pipeline_advance("extraction")

    def run_vocal_separation(self):
        audio_src = self.audio_source_edit.text()
        if not audio_src or not os.path.exists(audio_src):
            QMessageBox.warning(self, "Error", "Please extract audio or select a source first!")
            return
        
        target_dir = self.audio_folder_edit.text()
        self.progress_bar.setValue(35)
        self.vocal_sep_btn.setEnabled(False)
        self.vocal_sep_btn.setText("Separating... (AI Processing)")
        
        self.vocal_thread = VocalSeparationWorker(audio_src, target_dir)
        self.vocal_thread.finished.connect(self.on_vocal_separation_finished)
        self.vocal_thread.start()

    def on_vocal_separation_finished(self, vocal, music, error):
        self.vocal_sep_btn.setEnabled(True)
        self.vocal_sep_btn.setText("Separate Voice and Background")
        self.progress_bar.setValue(50)
        
        if error:
            err_lower = error.lower()
            missing_demucs = (
                "no module named" in err_lower and "demucs" in err_lower
            ) or (
                "demucs is not installed" in err_lower
            ) or (
                "requires the 'demucs' library" in err_lower
            )
            if missing_demucs:
                QMessageBox.warning(
                    self,
                    "Dependency Missing",
                    "Vocal Separation requires the 'demucs' library.\n\n"
                    "Please run (using the same Python you run this app with):\n"
                    "python -m pip install demucs\n\n"
                    f"Details:\n{error}",
                )
            else:
                QMessageBox.critical(self, "Error", f"Separation failed:\n\n{error}")
            self.log(error)
            self.refresh_ui_state()
            return
        
        if vocal and os.path.exists(vocal):
            self.audio_source_edit.setText(vocal)
            self.last_extracted_audio = vocal
            self.last_vocals_path = vocal
            self.last_music_path = music
            self.processed_artifacts["vocals"] = vocal
            if music:
                self.processed_artifacts["music"] = music
                # Auto-fill background for STEP 4
                if not self.bg_music_edit.text().strip():
                    self.bg_music_edit.setText(music)
            QMessageBox.information(self, "Success", 
                f"Audio stems separated!\n\nVocals: {os.path.basename(vocal)}\nBackground: {os.path.basename(music)}\n\nVocals are now selected for transcription.")
            self._pipeline_advance("separation")
        else:
            self._pipeline_fail("Separation did not produce output.")
        self.refresh_ui_state()

    def run_transcription(self):
        audio_src = self.audio_source_edit.text()
        if not audio_src or not os.path.exists(audio_src):
            QMessageBox.warning(self, "Error", "Audio source file not found! Please extract audio first.")
            return
        
        model_path = os.path.join(os.getcwd(), "models", "ggml-medium.bin")
        lang = self.lang_whisper_combo.currentText()
        
        self.transcript_text.setText("Transcribing... please wait (Loading...)")
        self.transcribe_btn.setEnabled(False)
        self.progress_bar.setValue(40)
        
        self.transcription_thread = TranscriptionWorker(audio_src, model_path, lang)
        self.transcription_thread.finished.connect(self.on_transcription_finished)
        self.transcription_thread.start()

    def on_transcription_finished(self, segments):
        self.transcribe_btn.setEnabled(True)
        if not segments:
            QMessageBox.warning(self, "Warning", "Transcription failed or returned no results.")
            self._pipeline_fail("Transcription failed.")
            return

        self.current_segments = segments
        self.progress_bar.setValue(60)
        
        # Show original subtitles immediately until a Vietnamese track is ready.
        self.apply_segments_to_timeline()
        
        # Display as SRT
        srt_text = self.format_to_srt(segments)
        self.transcript_text.setText(srt_text)
        
        # Save to selected folder
        v_path = self.video_path_edit.text()
        if v_path:
            file_basename = os.path.splitext(os.path.basename(v_path))[0]
            out_folder = self.srt_output_folder_edit.text()
            if not os.path.exists(out_folder): os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_original.srt")
            from subtitle_builder import generate_srt
            generate_srt(segments, out_path)
            self.last_original_srt_path = out_path
            self.processed_artifacts["srt_original"] = out_path
            QMessageBox.information(self, "Success", f"Transcription completed!\nOriginal SRT saved to: {out_path}")
        else:
            QMessageBox.information(self, "Success", "Transcription completed!")

        self.refresh_ui_state()
        self.schedule_auto_frame_preview()
        self._pipeline_advance("transcription")

    def run_translation(self):
        srt_source = self.transcript_text.toPlainText()
        if not srt_source or not srt_source.strip():
            QMessageBox.warning(self, "Error", "No transcription available to translate!")
            return
        
        # We no longer need the local NLLB model path
        model_path = None
        src = self.lang_whisper_combo.currentText()
        enable_polish = self.is_ai_polish_enabled()
        
        if enable_polish:
            self.translated_text.setText("Translating with Microsoft Translator and AI polish... please wait.")
        else:
            self.translated_text.setText("Translating with Microsoft Translator only... please wait.")
        self.translate_btn.setEnabled(False)
        self.progress_bar.setValue(80)
        
        self.translation_thread = TranslationWorker(srt_source, model_path, src, enable_polish)
        self.translation_thread.finished.connect(self.on_translation_finished)
        self.translation_thread.start()

    def on_translation_finished(self, translated_srt, error):
        self.translate_btn.setEnabled(True)
        if error or not translated_srt:
            self.show_error(
                "Translation Failed",
                "Could not complete the Vietnamese translation.",
                error or "The translator API returned an empty result.",
            )
            self._pipeline_fail("Translation failed.")
            return

        self.progress_bar.setValue(100)
        
        # Display as SRT
        self.translated_text.setText(translated_srt)
        self.apply_edited_translation(show_message=False, force_apply=True)
        
        # Auto-save SRT and update Section 4
        v_path = self.video_path_edit.text()
        if v_path:
            file_basename = os.path.splitext(os.path.basename(v_path))[0]
            out_folder = self.srt_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_vi.srt")
            
            # Since we have the SRT text, we can just save it directly
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(translated_srt)
            self.last_translated_srt_path = out_path
            self.processed_artifacts["srt_translated"] = out_path
                
            QMessageBox.information(self, "Finished", f"Process complete! Subtitle saved and loaded for preview:\n{out_path}")
        else:
            QMessageBox.information(self, "Finished", "Translation complete!")

        self.refresh_ui_state()
        self._pipeline_advance("translation")

    def apply_edited_translation(self, show_message=True, force_apply=True):
        """Re-parse STEP 3 SRT text (translated or external). Apply to timeline only when desired."""
        srt_text = self.translated_text.toPlainText()
        segs = []
        if self.keep_timeline_cb.isChecked():
            base_segments = self.current_translated_segments or self.current_segments
            edited_texts = self.extract_subtitle_text_entries(srt_text)
            if base_segments and len(edited_texts) == len(base_segments):
                segs = [
                    {
                        'start': base['start'],
                        'end': base['end'],
                        'text': edited_texts[idx],
                    }
                    for idx, base in enumerate(base_segments)
                ]
        if not segs:
            segs = self.parse_srt_to_segments(srt_text)
        if not segs:
            if show_message:
                QMessageBox.warning(self, "Error", "Could not parse edited translated SRT.\n\nTip: Keep standard SRT format:\n1\\n00:00:01,000 --> 00:00:02,000\\ntext")
            return False

        self.current_translated_segments = segs
        if force_apply:
            self.apply_segments_to_timeline()

        if show_message:
            QMessageBox.information(self, "Applied", f"Applied edited translation to timeline.\nSegments: {len(segs)}")
        self.refresh_ui_state()
        self.schedule_auto_frame_preview()
        return True



    def setup_media_player(self):
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_view.video_item)
        
        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn.clicked.connect(self.stop_video)
        
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)

    def browse_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video Files (*.mp4 *.mkv *.avi *.mov)")
        if file_path:
            self.video_path_edit.setText(file_path)
            self.media_player.setSource(QUrl.fromLocalFile(file_path))
            self.refresh_video_dimensions(file_path)
            self.play_btn.setText("Play")
            
            # Reset timeline state
            self.timeline.set_segments([])
            self.timeline.set_playing(False)
            self.current_segments = []
            self.current_translated_segments = []
            # Do not wipe external; keep it until user clears/changes source.
            
            # Auto-detect matching SRT
            base_no_ext = os.path.splitext(file_path)[0]
            possible_srts = [base_no_ext + ".srt", base_no_ext + "_vi.srt", base_no_ext + "_original.srt"]
            for s_path in possible_srts:
                if os.path.exists(s_path):
                    try:
                        with open(s_path, 'r', encoding='utf-8') as f:
                            segs = self.parse_srt_to_segments(f.read())
                            if segs:
                                self.current_segments = segs
                                self.apply_segments_to_timeline()
                                break
                    except: pass

            # Show preview by seeking to start
            self.media_player.pause()
            self.media_player.setPosition(0)
            
            # Refresh position once video settles
            QTimer.singleShot(500, self.video_view.reposition_subtitle)
            self.refresh_ui_state()
            self.schedule_auto_frame_preview()



    def browse_audio_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Audio Folder")
        if dir_path:
            self.audio_folder_edit.setText(dir_path)

    def browse_srt_output_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select SRT Export Folder")
        if dir_path:
            self.srt_output_folder_edit.setText(dir_path)

    def browse_audio_source(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
        if file_path:
            self.audio_source_edit.setText(file_path)

    def browse_background_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Background Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
        if file_path:
            self.bg_music_edit.setText(file_path)

    def browse_existing_mixed_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Mixed Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
        if file_path:
            self.mixed_audio_edit.setText(file_path)
            self.use_existing_audio_radio.setChecked(True)
            # Treat as current mixed audio artifact for preview
            self.last_mixed_vi_path = file_path
            self.processed_artifacts["mixed_vi"] = file_path

    def browse_voice_output_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Voice Output Folder")
        if dir_path:
            self.voice_output_folder_edit.setText(dir_path)
            if hasattr(self, "final_output_folder_edit"):
                self.final_output_folder_edit.setText(dir_path)

    def run_voiceover(self):
        translated_srt = self.translated_text.toPlainText().strip()
        if not translated_srt:
            QMessageBox.warning(self, "Error", "No translated SRT available. Please run translation first (STEP 3).")
            return

        segments = self.parse_srt_to_segments(translated_srt)
        if not segments:
            QMessageBox.warning(self, "Error", "Translated SRT could not be parsed to segments.")
            return

        out_dir = self.voice_output_folder_edit.text().strip() or os.path.join(os.getcwd(), "output")
        bg_path = self.bg_music_edit.text().strip()
        voice_name = self.voice_name_combo.currentText().strip()
        voice_gain = float(self.voice_gain_spin.value())
        bg_gain = float(self.bg_gain_spin.value())

        self.voiceover_btn.setEnabled(False)
        self.voiceover_btn.setText("Generating... (TTS)")
        self.progress_bar.setValue(85)

        self.voice_thread = VoiceOverWorker(segments, out_dir, bg_path, voice_name, voice_gain, bg_gain)
        self.voice_thread.finished.connect(self.on_voiceover_finished)
        self.voice_thread.start()

    def on_voiceover_finished(self, voice_track, mixed, error):
        self.voiceover_btn.setEnabled(True)
        self.voiceover_btn.setText("Create Vietnamese Voice")
        self.progress_bar.setValue(100)

        if error:
            QMessageBox.critical(self, "Error", f"Voiceover failed:\n\n{error}")
            self._pipeline_fail("Voiceover failed.")
            return

        if voice_track and os.path.exists(voice_track):
            self.last_voice_vi_path = voice_track
            self.processed_artifacts["voice_vi"] = voice_track
        if mixed and os.path.exists(mixed):
            self.last_mixed_vi_path = mixed
            self.processed_artifacts["mixed_vi"] = mixed

        if mixed:
            QMessageBox.information(self, "Success", f"Generated Vietnamese voice and mixed audio:\n\nVoice: {voice_track}\nMixed: {mixed}")
        else:
            QMessageBox.information(self, "Success", f"Generated Vietnamese voice track:\n\n{voice_track}\n\n(Background not provided, so no mix was created.)")

        self._pipeline_advance("voiceover")

    def preview_video_with_mixed_audio(self):
        video_path = self.video_path_edit.text().strip()
        audio_path = self.resolve_selected_audio_path()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "Error", "Video file not found. Please select a video first.")
            return
        if not audio_path or not os.path.exists(audio_path):
            QMessageBox.warning(
                self,
                "Error",
                "Selected audio source is not ready. Create Vietnamese voice first, or switch to 'Use existing mixed audio' and choose a valid file.",
            )
            return

        # Use a unique preview filename to avoid file locks/caching in QMediaPlayer.
        ts = int(time.time())
        preview_out = os.path.join(os.getcwd(), "temp", f"preview_vi_voice_{ts}.mp4")

        # Stop/clear current source to avoid locking the previous preview file.
        try:
            self.media_player.stop()
            self.media_player.setSource(QUrl())
        except Exception:
            pass

        self.log(f"[Preview] video={video_path}")
        self.log(f"[Preview] audio={audio_path}")
        self.log(f"[Preview] out={preview_out}")
        self.preview_btn.setEnabled(False)
        self.preview_btn.setText("Preparing preview...")
        self.progress_bar.setValue(95)

        self.preview_thread = PreviewMuxWorker(video_path, audio_path, preview_out)
        self.preview_thread.finished.connect(self.on_preview_ready)
        self.preview_thread.start()

    def on_preview_ready(self, preview_path, error):
        self.preview_btn.setEnabled(True)
        self.preview_btn.setText("Preview With Vietnamese Voice")
        self.progress_bar.setValue(100)

        if error:
            self.show_error("Error", "Preview failed.", str(error))
            self._pipeline_fail("Preview failed.")
            return

        if preview_path and os.path.exists(preview_path):
            self.last_preview_video_path = preview_path
            self.processed_artifacts["preview_video"] = preview_path
            self.log(f"[Preview] ready={preview_path}")
            self.refresh_video_dimensions(preview_path)
            self.media_player.setSource(QUrl.fromLocalFile(preview_path))
            self.play_btn.setText("Play")
            QMessageBox.information(
                self,
                "Preview Ready",
                "Loaded the preview video into the player.\nPress Play to review it, then click 'Export Final Video' when you are satisfied.",
            )
            self._pipeline_done()
            # Keep subtitles as selected source
            self.apply_segments_to_timeline()
            self.refresh_ui_state()

    def run_all_pipeline(self):
        """Guided pipeline based on the selected output mode."""
        v_path = self.video_path_edit.text().strip()
        if not v_path or not os.path.exists(v_path):
            QMessageBox.warning(self, "Error", "Please select a video first.")
            return
        self._pipeline_active = True
        self._pipeline_step = "start"
        self.run_all_btn.setEnabled(False)
        self.run_all_btn.setText("Processing...")
        self.run_extraction()

    def _pipeline_advance(self, completed_step: str):
        if not self._pipeline_active:
            return

        # Decide next step
        mode = self.get_output_mode_key()
        if completed_step == "extraction":
            if mode == "subtitle":
                self.run_transcription()
                return
            self.run_vocal_separation()
            return
        if completed_step == "separation":
            self.run_transcription()
            return
        if completed_step == "transcription":
            self.run_translation()
            return
        if completed_step == "translation":
            if mode == "subtitle":
                self._pipeline_done()
                QMessageBox.information(
                    self,
                    "Ready",
                    "Vietnamese subtitles are ready.\n\nReview them if needed, then click 'Export Final Video'.",
                )
                return
            self.run_voiceover()
            return
        if completed_step == "voiceover":
            self.preview_video_with_mixed_audio()
            return

    def _pipeline_fail(self, reason: str):
        if not self._pipeline_active:
            return
        self._pipeline_active = False
        self._pipeline_step = ""
        self.run_all_btn.setEnabled(True)
        self.run_all_btn.setText("Create Vietnamese Output")

    def _pipeline_done(self):
        if not self._pipeline_active:
            return
        self._pipeline_active = False
        self._pipeline_step = ""
        self.run_all_btn.setEnabled(True)
        self.run_all_btn.setText("Create Vietnamese Output")

    def open_folder(self, path):
        try:
            if not path:
                return
            os.makedirs(path, exist_ok=True)
            # Windows: open Explorer
            os.startfile(os.path.abspath(path))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open folder:\n{e}")

    def show_processed_files(self):
        def fmt(label, p):
            if not p:
                return f"- {label}: (none)"
            status = "OK" if os.path.exists(p) else "MISSING"
            return f"- {label}: [{status}]\n  {p}"

        lines = []
        lines.append("Generated / Selected Files:\n")
        lines.append(fmt("Video", self.video_path_edit.text()))
        lines.append(fmt("Extracted Audio", self.processed_artifacts.get('audio_extracted') or self.last_extracted_audio))
        lines.append(fmt("Vocals", self.processed_artifacts.get('vocals') or self.last_vocals_path))
        lines.append(fmt("Music (no_vocals)", self.processed_artifacts.get('music') or self.last_music_path))
        lines.append(fmt("Original SRT", self.processed_artifacts.get('srt_original') or self.last_original_srt_path))
        lines.append(fmt("Translated SRT", self.processed_artifacts.get('srt_translated') or self.last_translated_srt_path))
        lines.append(fmt("Vietnamese Voice (TTS)", self.processed_artifacts.get('voice_vi') or self.last_voice_vi_path))
        lines.append(fmt("Mixed Audio (BG + VI Voice)", self.processed_artifacts.get('mixed_vi') or self.last_mixed_vi_path))
        lines.append(fmt("Preview Video (temp)", self.processed_artifacts.get('preview_video') or self.last_preview_video_path))
        lines.append(fmt("Final Exported Video", self.processed_artifacts.get('final_video') or self.last_exported_video_path))

        QMessageBox.information(self, "Processed Files", "\n\n".join(lines))

    def cleanup_temp_preview_files(self):
        paths = [
            self.last_exact_preview_5s_path,
            self.last_exact_preview_frame_path,
            os.path.join(os.getcwd(), "temp", "preview_subtitle_5s.srt"),
            os.path.join(os.getcwd(), "temp", "preview_subtitle_full.srt"),
        ]
        for path in paths:
            self.cleanup_file_if_exists(path)

    def closeEvent(self, event):
        try:
            self.cleanup_temp_preview_files()
        finally:
            super().closeEvent(event)

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("Play")
            self.timeline.set_playing(False)
            self.schedule_seek_frame_preview()
        else:
            self.seek_frame_preview_timer.stop()
            self.media_player.play()
            self.play_btn.setText("Pause")
            self.timeline.set_playing(True)

    def stop_video(self):
        self.media_player.stop()
        self.play_btn.setText("Play")
        self.timeline.set_playing(False)
        self.schedule_seek_frame_preview()

    def position_changed(self, position):
        self.timeline.set_position(position)
        self.update_duration_label(position, self.media_player.duration())

    def duration_changed(self, duration):
        self.timeline.set_duration(duration)
        self.update_duration_label(self.media_player.position(), duration)

    def set_position(self, position):
        self.media_player.setPosition(position)
        self.timeline.set_position(position)
        self.schedule_seek_frame_preview()

    def update_duration_label(self, current, total):
        def fmt(ms):
            s = max(0, ms // 1000)
            m, s = divmod(s, 60)
            return f"{m:02d}:{s:02d}"
        self.time_label.setText(f"{fmt(current)} / {fmt(total)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())
