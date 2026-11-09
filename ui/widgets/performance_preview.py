"""A still image with shared layer editors, without a decoder or native surface."""
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QLabel
from widgets.mpv_video_view import MpvVideoView


class StaticVideoView(MpvVideoView):
    def __init__(self, parent=None):
        super().__init__(parent, native_surface=False)
        self.subtitle_item.setParent(self, Qt.Widget)
        for overlay in (self.blur_overlay,self.mask_overlay):
            overlay.setParent(self,overlay.windowFlags())
        self._frame = QPixmap()
        self._effects = []
        self._mirror = False
        self._image = QLabel(self.video_surface)
        self._image.setScaledContents(True)
        self._image.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._image.show()

    def set_frame(self, path):
        frame = QPixmap(path)
        if not frame.isNull():
            self._frame = frame
            self._render_frame()

    def set_effect_layers(self, effects, *, mirror=False):
        effects = list(effects)
        if self._effects == effects and self._mirror == bool(mirror):
            return
        self._effects, self._mirror = effects, bool(mirror)
        self._render_frame()

    def _render_frame(self):
        if self._frame.isNull():
            return
        image = self._frame.toImage()
        if self._mirror:
            image = image.mirrored(True, False)
        # A lightweight still approximation; export retains exact layer effects.
        for item in self._effects:
            rect = QRect(round(item['x']*image.width()), round(item['y']*image.height()),
                         round(item['width']*image.width()), round(item['height']*image.height()))
            rect = rect.intersected(image.rect())
            if rect.isEmpty():
                continue
            painter = QPainter()
            if item.get('mode') == 'solid':
                painter.begin(image)
                painter.setOpacity(item.get('opacity', 1.0))
                painter.fillRect(rect, QColor(item.get('color', '#000000')))
            else:
                crop = image.copy(rect)
                pixelate = item.get('mode') in {'pixelate', 'mosaic'}
                size = max(2, int(item.get('pixelate_size' if pixelate else 'blur_strength', 12)))
                smooth = Qt.FastTransformation if pixelate else Qt.SmoothTransformation
                crop = crop.scaled(max(1,rect.width()//size), max(1,rect.height()//size), Qt.IgnoreAspectRatio, smooth)
                crop = crop.scaled(rect.size(), Qt.IgnoreAspectRatio, smooth)
                painter.begin(image)
                painter.setOpacity(item.get('opacity', 1.0))
                painter.drawImage(rect, crop)
            painter.end()
        self._image.setPixmap(QPixmap.fromImage(image))
        self._image.setGeometry(self.video_surface.rect())
        if self.isVisible() and not getattr(self.window(),'_project_loading_in_progress',False):
            self.video_surface.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_image'):
            self._image.setGeometry(self.video_surface.rect())

    def _sync_preview_stack(self):
        super()._sync_preview_stack()
        if hasattr(self, '_image'):
            self._image.setGeometry(self.video_surface.rect())

    def set_mask_regions(self, *args, **kwargs):
        super().set_mask_regions(*args, **kwargs)
        timer = getattr(self.mask_overlay, '_sync_timer', None)
        if timer is not None:
            timer.stop()

    def set_logo(self,*args,**kwargs):
        super().set_logo(*args,**kwargs)
        if self.logo_overlay.parent() is None:
            self.logo_overlay.setParent(self,self.logo_overlay.windowFlags())
        timer=getattr(self.logo_overlay,'_sync_timer',None)
        if timer is not None:
            timer.stop()

    def set_logos(self,*args,**kwargs):
        super().set_logos(*args,**kwargs)
        timer=getattr(self.logo_overlay,'_sync_timer',None)
        if timer is not None:
            timer.stop()

    def set_text_layers(self,*args,**kwargs):
        super().set_text_layers(*args,**kwargs)
        if self.text_overlay.parent() is None:
            self.text_overlay.setParent(self,self.text_overlay.windowFlags())
            self.text_overlay.sync_to_view()
            if self.text_overlay._items:
                self.text_overlay.show()

    def dispose_overlays(self):
        for name in ('subtitle_item', 'blur_overlay', 'mask_overlay', 'logo_overlay', 'text_overlay'):
            overlay = getattr(self, name, None)
            if overlay is not None:
                overlay.hide()
