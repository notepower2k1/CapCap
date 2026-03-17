import sys
import os
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QCheckBox, QTextEdit, QComboBox,
                             QGroupBox, QSlider, QFrame, QProgressBar, QMessageBox,
                             QScrollArea, QGraphicsScene, QGraphicsView, QGraphicsItem,
                             QSpinBox, QColorDialog, QDoubleSpinBox, QTabWidget)
from PySide6.QtCore import Qt, QSizeF, QRectF, QPointF, QUrl, QThread, Signal, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QPen
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

# --- UI Components ---
class SubtitleOverlayItem(QGraphicsItem):
    """A draggable subtitle preview item rendered inside the QGraphicsScene."""
    W, H = 500, 80 # Increased size for real text

    # Fixed Style
    preview_font_name = "Segoe UI"
    preview_font_size = 20
    preview_color = QColor(255, 255, 255)  # white

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setZValue(10)
        self.current_text = ""

    def set_text(self, text):
        if self.current_text != text:
            self.current_text = text
            self.update()

    def boundingRect(self):
        return QRectF(0, 0, self.W, self.H)

    def paint(self, painter, option, widget=None):
        if not self.current_text and not self.isVisible():
            return
            
        rect = QRectF(0, 0, self.W, self.H)
        painter.setRenderHint(QPainter.Antialiasing)
        
        if self.current_text:
            # Subtle glassmorphism effect for background
            painter.setBrush(QColor(0, 0, 0, 180))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 10, 10)
            
            # Text styling
            painter.setPen(SubtitleOverlayItem.preview_color)
            fnt = QFont(SubtitleOverlayItem.preview_font_name,
                        SubtitleOverlayItem.preview_font_size,
                        QFont.Bold)
            painter.setFont(fnt)
            
            # Shadow/Outline effect for better readability on different video backgrounds
            shadow_rect = rect.translated(2, 2)
            painter.setPen(QColor(0, 0, 0, 100))
            painter.drawText(shadow_rect, Qt.AlignCenter | Qt.TextWordWrap, self.current_text)
            
            painter.setPen(SubtitleOverlayItem.preview_color)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self.current_text)
        else:
            # Design phase placeholder
            painter.setBrush(QColor(20, 20, 20, 120))
            painter.setPen(QPen(SubtitleOverlayItem.preview_color, 1, Qt.DashLine))
            painter.drawRoundedRect(rect, 10, 10)
            
            painter.setPen(SubtitleOverlayItem.preview_color)
            painter.setFont(QFont(SubtitleOverlayItem.preview_font_name, 12))
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.video_item.setSize(QSizeF(w, h))
        self._scene.setSceneRect(0, 0, w, h)
        self.reposition_subtitle()

    def reposition_subtitle(self):
        """Always snap subtitle to bottom-center."""
        item = self.subtitle_item
        w, h = self.width(), self.height()
        iw, ih = item.W, item.H
        pos = QPointF((w - iw) / 2, h - ih - 30)
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
    finished = Signal(str) # Now returns raw SRT text
    def __init__(self, srt_text, model_path, src_lang):
        super().__init__()
        self.srt_text = srt_text
        self.model_path = model_path
        self.src_lang = src_lang
    def run(self):
        try:
            # Import to use the updated translate_segments
            from translator import translate_segments_to_srt
            translated_srt = translate_segments_to_srt(self.srt_text, self.model_path, src_lang=self.src_lang)
            self.finished.emit(translated_srt)
        except Exception as e:
            print(f"Translation Thread Error: {e}")
            self.finished.emit("")

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

# Import our backend modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))
from video_processor import extract_audio
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
                background-color: #0d0d0d;
            }
            QWidget {
                color: #e0e0e0;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            #centralWidget {
                background-color: #0d0d0d;
            }
            #leftPanelArea {
                background-color: #141414;
                border-right: 1px solid #2a2a2a;
            }
            #leftPanelContainer {
                background-color: #141414;
            }
            #rightPanel {
                background-color: #0d0d0d;
            }
            QGroupBox {
                border: 1px solid #333333;
                border-radius: 8px;
                margin-top: 25px;
                font-weight: bold;
                color: #bb86fc;
                background-color: #1a1a1a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #bb86fc;
            }
            QPushButton {
                background-color: #311b92;
                color: #ffffff;
                border: 1px solid #4527a0;
                border-radius: 6px;
                padding: 10px 18px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4527a0;
                border-color: #5e35b1;
            }
            QPushButton#mainActionBtn {
                background-color: #00bfa5;
                color: #000000;
                border: none;
                font-size: 13px;
                border-bottom: 2px solid #00796b;
            }
            QPushButton#mainActionBtn:hover {
                background-color: #1de9b6;
            }
            QLineEdit, QTextEdit, QComboBox {
                background-color: #262626;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                color: #ffffff;
                padding: 8px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border: 1px solid #bb86fc;
            }
            QProgressBar {
                border: 1px solid #2a2a2a;
                border-radius: 10px;
                text-align: center;
                background-color: #141414;
                color: white;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #03dac6, stop:1 #018786);
                border-radius: 10px;
            }
            QLabel {
                background: transparent;
                color: #e0e0e0;
                font-size: 12px;
            }
            QCheckBox {
                background: transparent;
                color: #e0e0e0;
            }
            QScrollArea {
                border: none;
                background-color: #141414;
            }
            QScrollBar:vertical {
                border: none;
                background: #1e1e1e;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #333333;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #444444;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            /* Fix ComboBox Dropdown colors */
            QComboBox QAbstractItemView {
                background-color: #262626;
                color: #ffffff;
                selection-background-color: #bb86fc;
                border: 1px solid #3d3d3d;
                outline: none;
            }
        """)

        self.setup_ui()
        self.setup_media_player()

        # Track generated/selected artifacts for quick inspection.
        # Keys are stable IDs, values are absolute file paths.
        self.processed_artifacts = {}

        # Simple pipeline runner (Run All)
        self._pipeline_active = False
        self._pipeline_step = ""

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
        scroll_area.setFixedWidth(420)
        scroll_area.setFrameShape(QFrame.NoFrame)
        
        left_panel_container = QWidget()
        left_panel_container.setObjectName("leftPanelContainer")
        left_layout = QVBoxLayout(left_panel_container)
        left_layout.setSpacing(15)
        
        scroll_area.setWidget(left_panel_container)

        # File Selection
        self.video_path_edit = QLineEdit()
        self.video_path_edit.setPlaceholderText("Select video file...")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_video)
        
        file_layout = QHBoxLayout()
        file_layout.addWidget(self.video_path_edit)
        file_layout.addWidget(browse_btn)
        
        left_layout.addWidget(QLabel("Target Video:"))
        left_layout.addLayout(file_layout)

        # Primary actions (one-click)
        primary_actions = QGroupBox("QUICK ACTIONS")
        primary_layout = QVBoxLayout(primary_actions)
        self.run_all_btn = QPushButton("Run All (Auto) → Preview")
        self.run_all_btn.setObjectName("mainActionBtn")
        self.run_all_btn.clicked.connect(self.run_all_pipeline)
        primary_layout.addWidget(self.run_all_btn)
        left_layout.addWidget(primary_actions)

        # Tabs for a cleaner UX
        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #2a2a2a; border-radius: 8px; }
            QTabBar::tab { background: #1a1a1a; padding: 8px 12px; border: 1px solid #2a2a2a; border-bottom: none; }
            QTabBar::tab:selected { background: #262626; color: #03dac6; }
        """)
        left_layout.addWidget(tabs, 1)

        tab_prepare = QWidget()
        tab_subtitles = QWidget()
        tab_voice = QWidget()
        tab_tools = QWidget()
        tabs.addTab(tab_prepare, "Prepare")
        tabs.addTab(tab_subtitles, "Subtitles")
        tabs.addTab(tab_voice, "Voice & Preview")
        tabs.addTab(tab_tools, "Tools")

        prepare_layout = QVBoxLayout(tab_prepare)
        prepare_layout.setSpacing(12)
        subtitles_layout = QVBoxLayout(tab_subtitles)
        subtitles_layout.setSpacing(12)
        voice_tab_layout = QVBoxLayout(tab_voice)
        voice_tab_layout.setSpacing(12)
        tools_layout = QVBoxLayout(tab_tools)
        tools_layout.setSpacing(12)

        # Section 1: Source & Extraction
        audio_group = QGroupBox("STEP 1: AUDIO EXTRACTION")
        audio_layout = QVBoxLayout(audio_group)
        
        # Folder selection for extracted audio
        self.audio_folder_edit = QLineEdit(os.path.join(os.getcwd(), "temp"))
        browse_folder_btn = QPushButton("Target Folder")
        browse_folder_btn.clicked.connect(self.browse_audio_folder)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.audio_folder_edit)
        folder_layout.addWidget(browse_folder_btn)
        
        self.keep_audio_cb = QCheckBox("Keep audio file after completion")
        self.keep_audio_cb.setChecked(True)
        
        extract_btn = QPushButton("Extract Audio from Video")
        extract_btn.setObjectName("mainActionBtn")
        
        vocal_sep_btn = QPushButton("Separate Vocals & Music (AI)")
        vocal_sep_btn.setObjectName("mainActionBtn")
        self.vocal_sep_btn = vocal_sep_btn
        
        audio_layout.addWidget(QLabel("Extracted Audio Destination:"))
        audio_layout.addLayout(folder_layout)
        audio_layout.addWidget(self.keep_audio_cb)
        audio_layout.addWidget(extract_btn)
        audio_layout.addWidget(vocal_sep_btn)
        prepare_layout.addWidget(audio_group)

        # Section 2: Recognition
        trans_group = QGroupBox("STEP 2: SPEECH RECOGNITION")
        trans_layout = QVBoxLayout(trans_group)
        
        self.audio_source_edit = QLineEdit()
        self.audio_source_edit.setPlaceholderText("Select extracted audio (.wav)...")
        browse_audio_src_btn = QPushButton("Select Source")
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
        
        self.lang_whisper_combo = QComboBox()
        self.lang_whisper_combo.addItems(["zh", "ko", "ja", "en", "vi", "auto"])
        
        self.transcript_text = QTextEdit()
        self.transcript_text.setPlaceholderText("Original SRT transcript will appear here...")
        
        self.transcribe_btn = QPushButton("Generate Original SRT")
        self.transcribe_btn.setObjectName("mainActionBtn")
        
        trans_layout.addWidget(QLabel("Audio Source:"))
        trans_layout.addLayout(audio_src_layout)
        trans_layout.addWidget(QLabel("Export Original SRT To:"))
        trans_layout.addLayout(srt_folder_layout)
        trans_layout.addWidget(QLabel("Source Language:"))
        trans_layout.addWidget(self.lang_whisper_combo)
        trans_layout.addWidget(self.transcript_text)
        trans_layout.addWidget(self.transcribe_btn)
        subtitles_layout.addWidget(trans_group)

        # Section 3: Translation
        translate_group = QGroupBox("STEP 3: TRANSLATION (AI)")
        translate_layout = QVBoxLayout(translate_group)
        
        self.lang_target_combo = QComboBox()
        self.lang_target_combo.addItems(["Vietnamese (vie_Latn)", "English (eng_Latn)"])
        
        self.translated_text = QTextEdit()
        self.translated_text.setPlaceholderText("AI Translated SRT results will appear here...")
        
        self.translate_btn = QPushButton("Run AI Translation")
        self.translate_btn.setObjectName("mainActionBtn")

        self.apply_translated_btn = QPushButton("Apply Edited SRT to Timeline")
        self.apply_translated_btn.clicked.connect(self.apply_edited_translation)
        
        translate_layout.addWidget(QLabel("Target Language:"))
        translate_layout.addWidget(self.lang_target_combo)
        translate_layout.addWidget(self.translated_text)
        translate_layout.addWidget(self.translate_btn)
        translate_layout.addWidget(self.apply_translated_btn)
        subtitles_layout.addWidget(translate_group)

        # Section 4: Voiceover (TTS) + Mix
        voice_group = QGroupBox("STEP 4: VIETNAMESE VOICEOVER (TTS)")
        voice_layout = QVBoxLayout(voice_group)

        self.voice_name_combo = QComboBox()
        self.voice_name_combo.addItems([
            "vi-VN-HoaiMyNeural",
            "vi-VN-NamMinhNeural",
        ])

        self.bg_music_edit = QLineEdit()
        self.bg_music_edit.setPlaceholderText("Background music (no_vocals.wav) ...")
        browse_bg_btn = QPushButton("Select Background")
        browse_bg_btn.clicked.connect(self.browse_background_audio)
        bg_layout = QHBoxLayout()
        bg_layout.addWidget(self.bg_music_edit)
        bg_layout.addWidget(browse_bg_btn)

        self.mixed_audio_edit = QLineEdit()
        self.mixed_audio_edit.setPlaceholderText("Use an existing mixed audio (optional) ...")
        browse_mixed_btn = QPushButton("Select Mixed")
        browse_mixed_btn.clicked.connect(self.browse_existing_mixed_audio)
        mixed_layout = QHBoxLayout()
        mixed_layout.addWidget(self.mixed_audio_edit)
        mixed_layout.addWidget(browse_mixed_btn)

        self.voice_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
        browse_voice_out_btn = QPushButton("Output Folder")
        browse_voice_out_btn.clicked.connect(self.browse_voice_output_folder)
        voice_out_layout = QHBoxLayout()
        voice_out_layout.addWidget(self.voice_output_folder_edit)
        voice_out_layout.addWidget(browse_voice_out_btn)

        # Advanced options (collapsed by default)
        adv_group = QGroupBox("Advanced")
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

        adv_layout.addWidget(QLabel("Export Voice/Mix To:"))
        adv_layout.addLayout(voice_out_layout)

        self.voiceover_btn = QPushButton("Generate Vietnamese Voice + Mix")
        self.voiceover_btn.setObjectName("mainActionBtn")

        self.preview_btn = QPushButton("Preview Video with Mixed Audio")
        self.preview_btn.clicked.connect(self.preview_video_with_mixed_audio)

        voice_layout.addWidget(QLabel("TTS Voice:"))
        voice_layout.addWidget(self.voice_name_combo)
        voice_layout.addWidget(QLabel("Background Audio (optional):"))
        voice_layout.addLayout(bg_layout)
        voice_layout.addWidget(QLabel("Existing Mixed Audio (optional):"))
        voice_layout.addLayout(mixed_layout)
        voice_layout.addWidget(adv_group)
        voice_layout.addWidget(self.voiceover_btn)
        voice_layout.addWidget(self.preview_btn)
        voice_tab_layout.addWidget(voice_group)

        # Section: Processed files quick view
        artifacts_group = QGroupBox("PROCESSED FILES")
        artifacts_layout = QVBoxLayout(artifacts_group)

        self.show_artifacts_btn = QPushButton("Show Processed Files")
        self.show_artifacts_btn.clicked.connect(self.show_processed_files)

        self.open_temp_btn = QPushButton("Open Temp Folder")
        self.open_temp_btn.clicked.connect(lambda: self.open_folder(self.audio_folder_edit.text()))

        self.open_output_btn = QPushButton("Open Output Folder")
        self.open_output_btn.clicked.connect(lambda: self.open_folder(self.srt_output_folder_edit.text()))

        artifacts_layout.addWidget(self.show_artifacts_btn)
        artifacts_layout.addWidget(self.open_temp_btn)
        artifacts_layout.addWidget(self.open_output_btn)
        tools_layout.addWidget(artifacts_group)

        # Manual Controls
        manual_group = QGroupBox("EXTERNAL TOOLS")
        manual_layout = QVBoxLayout(manual_group)
        
        browse_srt_btn = QPushButton("Load External SRT for Preview")
        browse_srt_btn.clicked.connect(self.browse_srt)
        manual_layout.addWidget(browse_srt_btn)
        
        tools_layout.addWidget(manual_group)
        tools_layout.addStretch()
        tools_layout.addWidget(QLabel("Video Information Extractor v2.0"))

        prepare_layout.addStretch()
        subtitles_layout.addStretch()
        voice_tab_layout.addStretch()


        # --- RIGHT PANEL ---
        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        
        # VideoView contains both the video and overlay inside one QGraphicsScene.
        # This avoids all OpenGL surface layering issues.
        self.video_view = VideoView()
        self.video_view.setMinimumHeight(400)
        
        # New Timeline Component
        self.timeline = TimelineWidget()
        self.timeline.seekRequested.connect(self.set_position)
        
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("font-weight: bold; min-width: 100px; color: #00bfa5;")
        
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.stop_btn = QPushButton("Stop")
        controls_layout.addWidget(self.play_btn)
        controls_layout.addWidget(self.stop_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.time_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        
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
        
        # Initial positioning
        QTimer.singleShot(100, self.video_view.reposition_subtitle)

        # Data
        self.current_segments = []
        self.current_translated_segments = []
        self.last_extracted_audio = ""
        self.last_vocals_path = ""
        self.last_music_path = ""
        self.last_original_srt_path = ""
        self.last_translated_srt_path = ""
        self.last_voice_vi_path = ""
        self.last_mixed_vi_path = ""
        self.last_preview_video_path = ""

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
            QMessageBox.critical(self, "Error", f"Extraction failed: {path}")
            self._pipeline_fail("Extraction failed.")
            return

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
        self.vocal_sep_btn.setText("Separate Vocals & Music (AI)")
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
        
        # Update Timeline with segments
        self.timeline.set_segments(segments)
        self.video_view.subtitle_item.show()
        
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

        self._pipeline_advance("transcription")

    def run_translation(self):
        srt_source = self.transcript_text.toPlainText()
        if not srt_source or not srt_source.strip():
            QMessageBox.warning(self, "Error", "No transcription available to translate!")
            return
        
        # We no longer need the local NLLB model path
        model_path = None
        # Simpler mapping for Cloudflare Workers AI
        src = self.lang_whisper_combo.currentText()
        
        self.translated_text.setText("Translating via Cloudflare... please wait (Loading...)")
        self.translate_btn.setEnabled(False)
        self.progress_bar.setValue(80)
        
        self.translation_thread = TranslationWorker(srt_source, model_path, src)
        self.translation_thread.finished.connect(self.on_translation_finished)
        self.translation_thread.start()

    def on_translation_finished(self, translated_srt):
        self.translate_btn.setEnabled(True)
        if not translated_srt:
            QMessageBox.warning(self, "Error", "Translation failed.")
            self._pipeline_fail("Translation failed.")
            return

        self.progress_bar.setValue(100)
        
        # Display as SRT
        self.translated_text.setText(translated_srt)
        
        # Update Timeline with translated segments
        self.apply_edited_translation(show_message=False)
        
        # Auto-save SRT and update Section 4
        v_path = self.video_path_edit.text()
        if v_path:
            file_basename = os.path.splitext(os.path.basename(v_path))[0]
            out_path = os.path.join(os.getcwd(), "output", file_basename + "_vi.srt")
            
            # Since we have the SRT text, we can just save it directly
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(translated_srt)
            self.last_translated_srt_path = out_path
            self.processed_artifacts["srt_translated"] = out_path
                
            self.video_view.subtitle_item.show() # Ensure preview is visible after translation
            self.video_view.reposition_subtitle()
            QMessageBox.information(self, "Finished", f"Process complete! Subtitle saved and loaded for preview:\n{out_path}")
        else:
            QMessageBox.information(self, "Finished", "Translation complete!")

        self._pipeline_advance("translation")

    def apply_edited_translation(self, show_message=True):
        """Re-parse current translated SRT text and apply to timeline/preview."""
        srt_text = self.translated_text.toPlainText()
        segs = self.parse_srt_to_segments(srt_text)
        if not segs:
            if show_message:
                QMessageBox.warning(self, "Error", "Could not parse edited translated SRT.\n\nTip: Keep standard SRT format:\n1\\n00:00:01,000 --> 00:00:02,000\\ntext")
            return False

        self.current_translated_segments = segs
        self.timeline.set_segments(segs)
        self.video_view.subtitle_item.show()
        self.video_view.reposition_subtitle()

        if show_message:
            QMessageBox.information(self, "Applied", f"Applied edited translation to timeline.\nSegments: {len(segs)}")
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
            self.play_btn.setText("Play")
            
            # Reset timeline state
            self.timeline.set_segments([])
            self.timeline.set_playing(False)
            self.current_segments = []
            self.current_translated_segments = []
            
            # Auto-detect matching SRT
            base_no_ext = os.path.splitext(file_path)[0]
            possible_srts = [base_no_ext + ".srt", base_no_ext + "_vi.srt", base_no_ext + "_original.srt"]
            for s_path in possible_srts:
                if os.path.exists(s_path):
                    # self.srt_path_edit.setText(s_path) # Removed
                    try:
                        with open(s_path, 'r', encoding='utf-8') as f:
                            segs = self.parse_srt_to_segments(f.read())
                            if segs:
                                self.current_segments = segs # Treat as current
                                self.timeline.set_segments(segs)
                                self.video_view.subtitle_item.show() # Auto-show preview
                                break
                    except: pass

            # Show preview by seeking to start
            self.media_player.pause()
            self.media_player.setPosition(0)
            
            # Refresh position once video settles
            QTimer.singleShot(500, self.video_view.reposition_subtitle)



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
    def browse_srt(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Subtitle", "", "SRT Files (*.srt)")
        if file_path:
            # Load SRT into timeline
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    segs = self.parse_srt_to_segments(content)
                    if segs:
                        self.current_segments = segs
                        self.timeline.set_segments(segs)
                        self.video_view.subtitle_item.show()
            except Exception as e:
                print(f"Error loading SRT to timeline: {e}")

    def browse_background_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Background Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
        if file_path:
            self.bg_music_edit.setText(file_path)

    def browse_existing_mixed_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Mixed Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
        if file_path:
            self.mixed_audio_edit.setText(file_path)
            # Treat as current mixed audio artifact for preview
            self.last_mixed_vi_path = file_path
            self.processed_artifacts["mixed_vi"] = file_path

    def browse_voice_output_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Voice Output Folder")
        if dir_path:
            self.voice_output_folder_edit.setText(dir_path)

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
        self.voiceover_btn.setText("Generate Vietnamese Voice + Mix")
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
        # Prefer explicitly selected mixed audio (if any)
        chosen = self.mixed_audio_edit.text().strip()
        audio_path = (chosen or self.processed_artifacts.get("mixed_vi") or self.last_mixed_vi_path or "").strip()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "Error", "Video file not found. Please select a video first.")
            return
        if not audio_path or not os.path.exists(audio_path):
            QMessageBox.warning(self, "Error", "Mixed audio not found. Please run STEP 4 to generate mixed audio first.")
            return

        preview_out = os.path.join(os.getcwd(), "temp", "preview_vi_voice.mp4")
        self.preview_btn.setEnabled(False)
        self.preview_btn.setText("Preparing preview...")
        self.progress_bar.setValue(95)

        self.preview_thread = PreviewMuxWorker(video_path, audio_path, preview_out)
        self.preview_thread.finished.connect(self.on_preview_ready)
        self.preview_thread.start()

    def on_preview_ready(self, preview_path, error):
        self.preview_btn.setEnabled(True)
        self.preview_btn.setText("Preview Video with Mixed Audio")
        self.progress_bar.setValue(100)

        if error:
            QMessageBox.critical(self, "Error", f"Preview failed:\n\n{error}")
            self._pipeline_fail("Preview failed.")
            return

        if preview_path and os.path.exists(preview_path):
            self.last_preview_video_path = preview_path
            self.processed_artifacts["preview_video"] = preview_path
            self.media_player.setSource(QUrl.fromLocalFile(preview_path))
            self.play_btn.setText("Play")
            QMessageBox.information(self, "Preview Ready", "Loaded preview video (original video + mixed Vietnamese audio) into the player.\nPress Play to preview.")
            self._pipeline_done()

    def run_all_pipeline(self):
        """One-click pipeline: Extract -> Separate -> Transcribe -> Translate -> Voiceover -> Preview."""
        v_path = self.video_path_edit.text().strip()
        if not v_path or not os.path.exists(v_path):
            QMessageBox.warning(self, "Error", "Please select a video first.")
            return
        self._pipeline_active = True
        self._pipeline_step = "start"
        self.run_all_btn.setEnabled(False)
        self.run_all_btn.setText("Running... (Auto)")
        self.run_extraction()

    def _pipeline_advance(self, completed_step: str):
        if not self._pipeline_active:
            return

        # Decide next step
        if completed_step == "extraction":
            self.run_vocal_separation()
            return
        if completed_step == "separation":
            self.run_transcription()
            return
        if completed_step == "transcription":
            self.run_translation()
            return
        if completed_step == "translation":
            self.run_voiceover()
            return
        if completed_step == "voiceover":
            # If user picked an existing mixed audio, preview uses it; otherwise uses generated mixed
            self.preview_video_with_mixed_audio()
            return

    def _pipeline_fail(self, reason: str):
        if not self._pipeline_active:
            return
        self._pipeline_active = False
        self._pipeline_step = ""
        self.run_all_btn.setEnabled(True)
        self.run_all_btn.setText("Run All (Auto) → Preview")

    def _pipeline_done(self):
        if not self._pipeline_active:
            return
        self._pipeline_active = False
        self._pipeline_step = ""
        self.run_all_btn.setEnabled(True)
        self.run_all_btn.setText("Run All (Auto) → Preview")

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

        QMessageBox.information(self, "Processed Files", "\n\n".join(lines))

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("Play")
            self.timeline.set_playing(False)
        else:
            self.media_player.play()
            self.play_btn.setText("Pause")
            self.timeline.set_playing(True)

    def stop_video(self):
        self.media_player.stop()
        self.play_btn.setText("Play")
        self.timeline.set_playing(False)

    def position_changed(self, position):
        self.timeline.set_position(position)
        self.update_duration_label(position, self.media_player.duration())
        
        # Live Subtitle Preview
        pos_sec = position / 1000.0
        active_text = ""
        # Use translated segments if they exist, otherwise original
        segs = self.current_translated_segments if self.current_translated_segments else self.current_segments
        for seg in segs:
            if seg['start'] <= pos_sec <= seg['end']:
                active_text = seg['text']
                break
        self.video_view.subtitle_item.set_text(active_text)

    def duration_changed(self, duration):
        self.timeline.set_duration(duration)
        self.update_duration_label(self.media_player.position(), duration)

    def set_position(self, position):
        self.media_player.setPosition(position)
        self.timeline.set_position(position)

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
