"""Independent full-canvas backgrounds, edited through the layer inspector."""
import copy
from pathlib import Path
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImageReader
from PySide6.QtWidgets import (QWidget,QFrame,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,
    QLineEdit,QSlider,QPushButton,QCheckBox,QScrollArea,QFileDialog,QColorDialog,QMessageBox)
from app.layers.base import LayerType
from app.layers.background import BackgroundLayer
from app.layers.timeline import Track, layer_from_dict
from app.layers.sync_bridge import find_or_create_track
from app.video_layout import KEY
from i18n import t


class BackgroundController:
    def __init__(self,gui):
        self.gui=gui
        self.layer_id=''
        self.scroll=None

    def target(self):
        for track in self.gui.timeline._timeline.tracks:
            for layer in track.layers:
                if layer.id==self.layer_id:
                    return track,layer
        return None,None

    def build_inspector(self):
        if self.scroll is not None: return
        self.card=QFrame()
        self.card.setObjectName('statusCard')
        reference=self.gui.logo_inspector_card
        self.card.setMinimumWidth(reference.minimumWidth())
        self.card.setMaximumWidth(reference.maximumWidth())
        self.gui.background_inspector_card=self.card
        layout=QVBoxLayout(self.card)
        layout.setContentsMargins(12,10,12,10)
        layout.setSpacing(8)
        summary=QLabel(t('Background fills the canvas behind the video. A video covering the canvas hides it.'))
        summary.setObjectName('helperLabel')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.controls=QWidget()
        controls=QVBoxLayout(self.controls)
        controls.setContentsMargins(0,0,0,0)
        controls.setSpacing(8)

        mode_label=QLabel(t('Background'))
        mode_label.setObjectName('sectionTitle')
        controls.addWidget(mode_label)
        self.mode=QComboBox()
        for key,label in (('color','Solid color'),('image','Background image'),('blur','Canvas Blur')):
            self.mode.addItem(t(label),key)
        controls.addWidget(self.mode)

        self.color_section=QWidget()
        color_row=QHBoxLayout(self.color_section)
        color_row.setContentsMargins(0,0,0,0)
        color_label=QLabel(t('Color'))
        color_label.setObjectName('sectionTitle')
        color_row.addWidget(color_label)
        self.color=QPushButton('#101826')
        self.color.setFixedWidth(110)
        color_row.addWidget(self.color)
        color_row.addStretch(1)
        controls.addWidget(self.color_section)

        self.image_section=QWidget()
        image_layout=QVBoxLayout(self.image_section)
        image_layout.setContentsMargins(0,0,0,0)
        image_layout.setSpacing(8)
        image_label=QLabel(t('Background image'))
        image_label.setObjectName('sectionTitle')
        image_layout.addWidget(image_label)
        image_row=QHBoxLayout()
        self.image=QLineEdit()
        self.image.setReadOnly(True)
        image_row.addWidget(self.image,1)
        self.choose=QPushButton(t('Choose image'))
        image_row.addWidget(self.choose)
        image_layout.addLayout(image_row)
        controls.addWidget(self.image_section)

        self.blur_section=QWidget()
        blur_layout=QVBoxLayout(self.blur_section)
        blur_layout.setContentsMargins(0,0,0,0)
        blur_layout.setSpacing(8)
        blur_header=QHBoxLayout()
        blur_label=QLabel(t('Blur strength'))
        blur_label.setObjectName('sectionTitle')
        blur_header.addWidget(blur_label)
        blur_header.addStretch(1)
        self.blur_value=QLabel('24')
        self.blur_value.setMinimumWidth(40)
        self.blur_value.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        blur_header.addWidget(self.blur_value)
        blur_layout.addLayout(blur_header)
        self.blur=QSlider(Qt.Horizontal)
        self.blur.setRange(1,60)
        self.blur.valueChanged.connect(lambda value:self.blur_value.setText(str(value)))
        blur_layout.addWidget(self.blur)
        controls.addWidget(self.blur_section)

        self.visible=QCheckBox(t('Show background'))
        controls.addWidget(self.visible)
        layout.addWidget(self.controls)
        self.apply_btn=QPushButton(t('Apply'));self.delete_btn=QPushButton(t('Delete'))
        row=QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.apply_btn);row.addWidget(self.delete_btn)
        layout.addLayout(row);layout.addStretch(1)
        self.scroll=QScrollArea()
        self.scroll.setObjectName('inspectorCardScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.card)
        self.gui.inspector_stack.addWidget(self.scroll)
        self.mode.currentIndexChanged.connect(self.refresh_sections)
        self.color.clicked.connect(self.choose_color);self.choose.clicked.connect(self.choose_image)
        self.apply_btn.clicked.connect(self.commit);self.delete_btn.clicked.connect(self.delete)
        self.refresh_sections()

    def refresh_sections(self):
        mode=self.mode.currentData()
        self.color_section.setVisible(mode=='color')
        self.image_section.setVisible(mode=='image')
        self.blur_section.setVisible(mode=='blur')

    def choose_color(self):
        color=QColorDialog.getColor(QColor(self.color.text()),self.gui,t('Color'))
        if color.isValid():self.color.setText(color.name());self.mode.setCurrentIndex(self.mode.findData('color'))

    def choose_image(self):
        path,_=QFileDialog.getOpenFileName(self.gui,t('Choose image'),'','Images (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:self.image.setText(path);self.mode.setCurrentIndex(self.mode.findData('image'))

    def show(self,layer):
        self.layer_id=layer.id;self.build_inspector()
        track,layer=self.target()
        self.mode.setCurrentIndex(self.mode.findData(layer.mode));self.color.setText(layer.color)
        path=layer.source
        state=getattr(self.gui,'current_project_state',None)
        if path and not Path(path).is_absolute() and state:path=str(Path(state.project_root)/path)
        self.image.setText(path);self.blur.setValue(round(layer.blur));self.visible.setChecked(layer.visible)
        self.refresh_enabled()
        self.gui.inspector_stack.setCurrentWidget(self.scroll)
        self.gui.set_inspector_collapsed(False)
        self.gui._sync_subtitle_inspector_shell_width()

    def refresh_enabled(self):
        if self.scroll is None:return
        track,layer=self.target()
        editable=bool(layer and not track.locked and not layer.locked and not self.gui._preview_is_playing())
        self.controls.setEnabled(editable);self.apply_btn.setEnabled(editable);self.delete_btn.setEnabled(editable)

    @staticmethod
    def snapshot(track,layer):
        metadata=copy.deepcopy(track.to_dict());metadata['layers']=[];metadata['clips']=[]
        return dict(track=metadata,layer=copy.deepcopy(layer.to_dict()),index=track.layers.index(layer))

    def record(self,before,after):
        self.gui._push_timeline_undo_entry(dict(type='background_layer',before=before,after=after))

    def add(self):
        model=self.gui.timeline._timeline
        track=find_or_create_track(model,'Background',LayerType.BACKGROUND,55)
        if track.locked:
            QMessageBox.information(self.gui,t('Layer Locked'),t('Unlock the background track before adding a background.'));return
        layer=BackgroundLayer(name=t('Background'),start=0,end=max(1,float(model.duration)),metadata={'manual_background':True})
        track.layers.append(layer);model.tracks.remove(track);model.tracks.insert(0,track)
        self.record(None,self.snapshot(track,layer));self.finish(layer.id)

    def commit(self):
        track,layer=self.target()
        if layer is None or track.locked or layer.locked or self.gui._preview_is_playing():return
        mode=self.mode.currentData();image=self.image.text().strip()
        if mode=='image':
            reader=QImageReader(image);reader.setScaledSize(QSize(64,64))
            if not Path(image).is_file() or reader.read().isNull():
                QMessageBox.warning(self.gui,t('Error'),t('Choose a readable background image first.'));return
        before=self.snapshot(track,layer)
        layer.mode=mode;layer.color=self.color.text();layer.source=image if mode=='image' else '';layer.blur=self.blur.value();layer.visible=self.visible.isChecked()
        after=self.snapshot(track,layer)
        if before==after:return
        self.record(before,after);self.finish(layer.id)

    def delete(self):
        track,layer=self.target()
        if layer is None or track.locked or layer.locked or self.gui._preview_is_playing():return
        self.record(self.snapshot(track,layer),None)
        track.layers.remove(layer)
        if not track.layers:self.gui.timeline._timeline.tracks.remove(track)
        self.finish('')

    def apply_history(self,entry,use_after):
        saved=entry['after' if use_after else 'before']
        identity=(entry.get('after') or entry['before'])['layer']['id']
        model=self.gui.timeline._timeline
        for track in model.tracks:
            track.layers[:]=[layer for layer in track.layers if layer.id!=identity]
        model.tracks[:]=[track for track in model.tracks if track.layers or track.type!=LayerType.BACKGROUND]
        if saved:
            track=next((track for track in model.tracks if track.id==saved['track']['id']),None)
            if track is None:track=Track.from_dict(saved['track']);model.tracks.insert(0,track)
            track.layers.insert(min(saved['index'],len(track.layers)),layer_from_dict(copy.deepcopy(saved['layer'])))
        self.finish(identity if saved else '')

    def finish(self,selected):
        self.layer_id=selected
        layout=self.gui.video_layout_controller
        # Keep a template-owned background editable through the same inspector.
        track,layer=self.target()
        if layer is not None and layer.metadata.get('layout_role')=='background' and layout.applied:
            layout.applied.update(background=layer.mode,color=layer.color,image=layer.source,blur=layer.blur)
            state=self.gui.ensure_current_project()
            if state:state.set_setting(KEY,copy.deepcopy(layout.applied));self.gui.project_service.save_project(state)
            draft=layout.draft()
            draft.update(background=layer.mode,color=layer.color,image=layer.source,blur=layer.blur)
            layout.fill(draft)
        self.gui.timeline._redraw()
        layout.refresh()
        self.gui.persist_current_timeline_project_data()
        self.gui.timeline.select_layer(selected)
        if selected:
            self.layer_id=selected;_,layer=self.target()
            if layer:self.show(layer)
        else:
            self.layer_id='';self.gui._show_default_inspector()


def controller(gui):
    value=getattr(gui,'background_controller',None)
    if value is None:value=BackgroundController(gui);gui.background_controller=value
    return value
