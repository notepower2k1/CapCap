import sys
import os
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QFileDialog, QCheckBox, QTextEdit, QComboBox,
                             QGroupBox, QSlider, QFrame, QProgressBar, QMessageBox,
                             QScrollArea, QGraphicsScene, QGraphicsView, QGraphicsItem,
                             QSpinBox, QColorDialog)
from PySide6.QtCore import Qt, QSizeF, QRectF, QPointF, QUrl, QThread, Signal, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QPen
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

# --- UI Components ---
class SubtitleOverlayItem(QGraphicsItem):
    """A draggable subtitle preview item rendered inside the QGraphicsScene."""
    W, H = 500, 80 # Increased size for real text

    # Style state shared with the main window
    preview_font_name = "Arial"
    preview_font_size = 18
    preview_color = QColor(255, 255, 255)  # white

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
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
        
        # Draw background only if there's text or if it's the design phase
        if self.current_text:
            painter.fillRect(rect, QColor(0, 0, 0, 150))
        else:
            painter.fillRect(rect, QColor(20, 20, 20, 100))
            painter.setPen(QPen(SubtitleOverlayItem.preview_color, 1, Qt.DashLine))
            painter.drawRect(rect)

        painter.setPen(SubtitleOverlayItem.preview_color)
        fnt = QFont(SubtitleOverlayItem.preview_font_name,
                    SubtitleOverlayItem.preview_font_size,
                    QFont.Bold)
        painter.setFont(fnt)
        
        display_text = self.current_text if self.current_text else "(Subtitle Area)"
        painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, display_text)


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

class TimelineWidget(QGraphicsView):
    """CapCut-style timeline for subtitle preview and seeking."""
    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(130)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #0a0a0a; border-top: 1px solid #222;")
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
class EmbeddingWorker(QThread):
    finished = Signal(bool, str)
    def __init__(self, v_path, s_path, out_path, alignment, margin_v,
                 font_name="Arial", font_size=18, font_color="&H00FFFFFF"):
        super().__init__()
        self.v_path = v_path
        self.s_path = s_path
        self.out_path = out_path
        self.alignment = alignment
        self.margin_v = margin_v
        self.font_name = font_name
        self.font_size = font_size
        self.font_color = font_color
    def run(self):
        try:
            success = embed_subtitles(
                self.v_path, self.s_path, self.out_path,
                alignment=self.alignment, margin_v=self.margin_v,
                font_name=self.font_name, font_size=self.font_size,
                font_color=self.font_color
            )
            self.finished.emit(success, self.out_path)
        except Exception as e:
            self.finished.emit(False, str(e))
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

# Import our backend modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))
from video_processor import extract_audio, embed_subtitles
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

        # Section 1
        audio_group = QGroupBox("SECTION 1: AUDIO EXTRACTION")
        audio_layout = QVBoxLayout(audio_group)
        self.audio_folder_edit = QLineEdit(os.path.join(os.getcwd(), "temp"))
        browse_folder_btn = QPushButton("Folder")
        browse_folder_btn.clicked.connect(self.browse_audio_folder)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.audio_folder_edit)
        folder_layout.addWidget(browse_folder_btn)
        self.keep_audio_cb = QCheckBox("Keep audio file after completion")
        self.keep_audio_cb.setChecked(True)
        extract_btn = QPushButton("Extract Audio Only")
        extract_btn.setObjectName("mainActionBtn")
        audio_layout.addWidget(QLabel("Save Audio To:"))
        audio_layout.addLayout(folder_layout)
        audio_layout.addWidget(self.keep_audio_cb)
        audio_layout.addWidget(extract_btn)
        left_layout.addWidget(audio_group)

        # Section 2
        trans_group = QGroupBox("SECTION 2: SPEECH RECOGNITION")
        trans_layout = QVBoxLayout(trans_group)
        
        # Audio Source Path for Recognition
        self.audio_source_edit = QLineEdit()
        self.audio_source_edit.setPlaceholderText("Select audio source (.wav)...")
        browse_audio_src_btn = QPushButton("Source")
        browse_audio_src_btn.clicked.connect(self.browse_audio_source)
        
        audio_src_layout = QHBoxLayout()
        audio_src_layout.addWidget(self.audio_source_edit)
        audio_src_layout.addWidget(browse_audio_src_btn)

        # NEW: SRT Save Folder
        self.srt_output_folder_edit = QLineEdit(os.path.join(os.getcwd(), "output"))
        browse_srt_folder_btn = QPushButton("SRT Folder")
        browse_srt_folder_btn.clicked.connect(self.browse_srt_output_folder)
        srt_folder_layout = QHBoxLayout()
        srt_folder_layout.addWidget(self.srt_output_folder_edit)
        srt_folder_layout.addWidget(browse_srt_folder_btn)
        
        self.lang_whisper_combo = QComboBox()
        self.lang_whisper_combo.addItems(["zh", "ko", "ja", "en", "vi", "auto"])
        self.lang_whisper_combo.setPlaceholderText("Select Source Language (Recommended)")
        self.transcript_text = QTextEdit()
        # Editable to allow manual fixes
        self.transcript_text.setPlaceholderText("SRT Transcript will appear here...")
        self.transcribe_btn = QPushButton("Run Transcription")
        self.transcribe_btn.setObjectName("mainActionBtn")
        
        trans_layout.addWidget(QLabel("Audio Source:"))
        trans_layout.addLayout(audio_src_layout)
        trans_layout.addWidget(QLabel("Export SRT To:"))
        trans_layout.addLayout(srt_folder_layout)
        trans_layout.addWidget(QLabel("Source Language:"))
        trans_layout.addWidget(self.lang_whisper_combo)
        trans_layout.addWidget(self.transcript_text)
        trans_layout.addWidget(self.transcribe_btn)
        left_layout.addWidget(trans_group)

        # Section 3
        translate_group = QGroupBox("SECTION 3: TRANSLATION")
        translate_layout = QVBoxLayout(translate_group)
        self.lang_target_combo = QComboBox()
        self.lang_target_combo.addItems(["Vietnamese (vie_Latn)", "English (eng_Latn)"])
        self.translated_text = QTextEdit()
        # MAKE EDITABLE
        self.translated_text.setReadOnly(False) 
        self.translated_text.setPlaceholderText("Paste your translation here or use Auto Translate...")
        self.translate_btn = QPushButton("Execute Auto Translation")
        self.translate_btn.setObjectName("mainActionBtn")
        translate_layout.addWidget(QLabel("Target Language:"))
        translate_layout.addWidget(self.lang_target_combo)
        translate_layout.addWidget(self.translated_text)
        translate_layout.addWidget(self.translate_btn)
        left_layout.addWidget(translate_group)
        left_layout.addStretch()

        # Section 4: Video Embedding
        embed_group = QGroupBox("SECTION 4: VIDEO EMBEDDING")
        embed_layout = QVBoxLayout(embed_group)
        
        self.srt_path_edit = QLineEdit()
        self.srt_path_edit.setPlaceholderText("Select .srt file...")
        browse_srt_btn = QPushButton("SRT")
        browse_srt_btn.clicked.connect(self.browse_srt)
        
        srt_layout = QHBoxLayout()
        srt_layout.addWidget(self.srt_path_edit)
        srt_layout.addWidget(browse_srt_btn)
        
        self.pos_mode_combo = QComboBox()
        self.pos_mode_combo.addItems(["Bottom-Center (Preset)", "Top-Center (Preset)", "Middle-Center (Preset)", "Custom (Drag Overlay)"])

        # --- Font Style Controls ---
        font_style_group = QGroupBox("Subtitle Style")
        font_style_group.setStyleSheet("QGroupBox { margin-top: 12px; font-size: 11px; }")
        fs_layout = QVBoxLayout(font_style_group)
        fs_layout.setSpacing(6)

        # Font Family
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Font:"))
        self.font_family_combo = QComboBox()
        common_fonts = ["Arial", "Arial Black", "Calibri", "Cambria", "Comic Sans MS",
                        "Courier New", "Georgia", "Impact", "Segoe UI", "Tahoma",
                        "Times New Roman", "Trebuchet MS", "Verdana"]
        self.font_family_combo.addItems(common_fonts)
        self.font_family_combo.setCurrentText("Arial")
        self.font_family_combo.currentTextChanged.connect(self._on_style_changed)
        font_row.addWidget(self.font_family_combo)
        fs_layout.addLayout(font_row)

        # Font Size
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:"))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setValue(18)
        self.font_size_spin.setStyleSheet("background-color:#262626; color:#fff; border:1px solid #3d3d3d; border-radius:4px; padding:4px;")
        self.font_size_spin.valueChanged.connect(self._on_style_changed)
        size_row.addWidget(self.font_size_spin)
        size_row.addStretch()
        fs_layout.addLayout(size_row)

        # Font Color
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        self.font_color_btn = QPushButton("  White  ")
        self.font_color_btn.setStyleSheet(
            "background-color: #ffffff; color: #000000; border-radius:4px; padding:4px 10px;")
        self._selected_color = QColor(255, 255, 255)
        self.font_color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self.font_color_btn)
        color_row.addStretch()
        fs_layout.addLayout(color_row)

        self.embed_btn = QPushButton("Burn Subtitles to Video")
        self.embed_btn.setObjectName("mainActionBtn")

        self.preview_btn = QPushButton("Generate Preview Caption")
        self.preview_btn.setStyleSheet("background-color: #4527a0;")
        self.preview_btn.setObjectName("previewActionBtn")
        self.preview_btn.clicked.connect(lambda: self.toggle_preview_overlay())

        embed_layout.addWidget(QLabel("SRT Source:"))
        embed_layout.addLayout(srt_layout)
        embed_layout.addWidget(QLabel("Position Mode:"))
        embed_layout.addWidget(self.pos_mode_combo)
        embed_layout.addWidget(font_style_group)
        embed_layout.addWidget(self.preview_btn)
        embed_layout.addWidget(self.embed_btn)
        left_layout.addWidget(embed_group)
        
        left_layout.addStretch()
        left_layout.addWidget(QLabel("v1.2.0 - Developed for VIP Users"))

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
        self.transcribe_btn.clicked.connect(self.run_transcription)
        self.translate_btn.clicked.connect(self.run_translation)
        self.embed_btn.clicked.connect(self.run_embedding)
        self.pos_mode_combo.currentIndexChanged.connect(self.on_pos_mode_changed)

        # Trigger initial position after UI is fully rendered
        QTimer.singleShot(100, lambda: self.on_pos_mode_changed(0))

        # Data
        self.current_segments = []
        self.current_translated_segments = []
        self.last_extracted_audio = ""

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
            QMessageBox.information(self, "Success", "Audio extraction completed!")
        else:
            QMessageBox.critical(self, "Error", f"Extraction failed: {path}")

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
            return

        self.current_segments = segments
        self.progress_bar.setValue(60)
        
        # Update Timeline with segments
        self.timeline.set_segments(segments)
        
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
            QMessageBox.information(self, "Success", f"Transcription completed!\nOriginal SRT saved to: {out_path}")
        else:
            QMessageBox.information(self, "Success", "Transcription completed!")

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
            return

        self.progress_bar.setValue(100)
        
        # Display as SRT
        self.translated_text.setText(translated_srt)
        
        # Update Timeline with translated segments
        translated_segs = self.parse_srt_to_segments(translated_srt)
        if translated_segs:
            self.current_translated_segments = translated_segs
            self.timeline.set_segments(translated_segs)
        
        # Auto-save SRT and update Section 4
        v_path = self.video_path_edit.text()
        if v_path:
            file_basename = os.path.splitext(os.path.basename(v_path))[0]
            out_path = os.path.join(os.getcwd(), "output", file_basename + "_vi.srt")
            
            # Since we have the SRT text, we can just save it directly
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(translated_srt)
                
            self.srt_path_edit.setText(out_path)
            QMessageBox.information(self, "Finished", f"Process complete! Subtitle saved to:\n{out_path}")
        else:
            QMessageBox.information(self, "Finished", "Translation complete!")

    def on_pos_mode_changed(self, index):
        """Snap the subtitle overlay to a preset position within the scene."""
        item = self.video_view.subtitle_item
        if not item.isVisible():
            return

        scene_rect = self.video_view._scene.sceneRect()
        w, h = scene_rect.width(), scene_rect.height()
        iw, ih = item.W, item.H

        if index == 0:   # Bottom-Center
            pos = QPointF((w - iw) / 2, h - ih - 30)
        elif index == 1: # Top-Center
            pos = QPointF((w - iw) / 2, 30)
        elif index == 2: # Middle-Center
            pos = QPointF((w - iw) / 2, (h - ih) / 2)
        else:
            return  # Custom mode: user drags freely inside the view

        item.setPos(pos)
            
    def _on_style_changed(self):
        """Propagate font/color changes to the live subtitle preview."""
        SubtitleOverlayItem.preview_font_name = self.font_family_combo.currentText()
        SubtitleOverlayItem.preview_font_size = self.font_size_spin.value()
        SubtitleOverlayItem.preview_color = self._selected_color
        self.video_view.subtitle_item.update()  # Trigger repaint

    def _pick_color(self):
        color = QColorDialog.getColor(self._selected_color, self, "Pick Subtitle Color")
        if color.isValid():
            self._selected_color = color
            # Show a preview swatch on the button
            luma = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
            text_col = "#000" if luma > 140 else "#fff"
            self.font_color_btn.setStyleSheet(
                f"background-color: {color.name()}; color: {text_col};"
                "border-radius:4px; padding:4px 10px;")
            self._on_style_changed()

    def _qt_color_to_ass(self, color: QColor) -> str:
        """Convert QColor to ASS &HAABBGGRR hex string (alpha=00 = opaque)."""
        return f"&H00{color.blue():02X}{color.green():02X}{color.red():02X}"
    def run_embedding(self):
        v_path = self.video_path_edit.text()
        s_path = self.srt_path_edit.text()
        if not v_path or not s_path:
            QMessageBox.warning(self, "Error", "Video or SRT source missing!")
            return

        mode = self.pos_mode_combo.currentText()

        # Get actual video native height for accurate pixel calculations.
        # nativeSize() is populated once the video source is set.
        native = self.video_view.video_item.nativeSize()
        native_h = int(native.height()) if native.height() > 0 else 1080
        native_w = int(native.width())  if native.width()  > 0 else 1920

        # Default: bottom-center, 30px from bottom in video pixels
        alignment = 2
        margin_v  = 30

        if "Top" in mode:
            alignment = 8
            margin_v  = 30                       # 30 px from top
        elif "Middle" in mode:
            alignment = 5
            margin_v  = 0
        elif "Custom" in mode:
            item    = self.video_view.subtitle_item
            scene_h = self.video_view._scene.sceneRect().height()
            if scene_h > 0:
                # Fraction of scene height from the bottom of the overlay to
                # the bottom of the scene → convert to video pixels
                dist_pct = (scene_h - item.pos().y() - item.H) / scene_h
                margin_v = max(0, int(dist_pct * native_h))
            alignment = 2

        font_name  = self.font_family_combo.currentText()
        font_size  = self.font_size_spin.value()
        font_color = self._qt_color_to_ass(self._selected_color)

        out_dir  = os.path.join(os.getcwd(), "output")
        out_path = os.path.join(out_dir,
                                os.path.splitext(os.path.basename(v_path))[0] + "_burned.mp4")

        self.embed_btn.setEnabled(False)
        self.progress_bar.setValue(90)

        self.embed_thread = EmbeddingWorker(
            v_path, s_path, out_path, alignment, margin_v,
            font_name=font_name, font_size=font_size, font_color=font_color
        )
        self.embed_thread.finished.connect(self.on_embedding_finished)
        self.embed_thread.start()



    def on_embedding_finished(self, success, path):
        self.embed_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        if success:
            QMessageBox.information(self, "Success", f"Video exported with subtitles:\n{path}")
        else:
            QMessageBox.critical(self, "Error", "Embedding failed.")

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
                    self.srt_path_edit.setText(s_path)
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
            QTimer.singleShot(500, lambda: self.on_pos_mode_changed(self.pos_mode_combo.currentIndex()))

    def toggle_preview_overlay(self):
        """Toggle subtitle overlay visibility inside the video scene."""
        item = self.video_view.subtitle_item
        if item.isVisible():
            item.hide()
        else:
            item.show()
            self.on_pos_mode_changed(self.pos_mode_combo.currentIndex())

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
            self.srt_path_edit.setText(file_path)
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
