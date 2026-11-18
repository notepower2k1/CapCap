"""A still image with shared layer editors, without a decoder or native surface."""
from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QPainter, QPixmap, QImage, QPainterPath
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
        self._background_path = None
        self._background_source = QImage()
        self._background_size = None
        self._background_scaled = QImage()
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

    def _scaled_image_background(self, path, width, height):
        if path != self._background_path:
            self._background_path = path
            self._background_source = QImage(path)
            self._background_size = None
        size = (width, height)
        if self._background_size != size:
            self._background_scaled = self._background_source.scaled(
                width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            ) if not self._background_source.isNull() else QImage()
            self._background_size = size
        return self._background_scaled

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
        cfg = getattr(self, 'video_layout', None)
        if cfg:
            from app.video_layout import geometry, background_is_covered
        if cfg and (image.hasAlphaChannel() or not background_is_covered(
                cfg, cfg["_canvas_width"], cfg["_canvas_height"], cfg["_source_width"], cfg["_source_height"]
        )):
            # Render at display resolution; full-resolution composition uses FFmpeg.
            canvas = self.get_preview_canvas_rect()
            w, h = max(2, round(canvas.width())), max(2, round(canvas.height()))
            frame = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            frame.fill(QColor(cfg['color']))
            p = QPainter(frame); p.setRenderHint(QPainter.SmoothPixmapTransform)
            bg = QImage()
            if cfg['background'] == 'image':
                bg = self._scaled_image_background(cfg['image'], w, h)
            elif cfg['background'] == 'blur':
                bg = image.scaled(max(2,w//int(cfg['blur'])), max(2,h//int(cfg['blur'])),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
                bg = bg.scaled(w,h,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            if not bg.isNull():
                p.drawImage(round((w-bg.width())/2),round((h-bg.height())/2),bg)
            slot, displayed = geometry(cfg,w,h,image.width(),image.height(),self.preview_scale_mode,self.get_preview_fill_focus())
            face = QRectF(*(slot if self.preview_scale_mode == 'fill' else displayed))
            radius = cfg['corner'] * h / max(1,cfg['_canvas_height'])
            if cfg['shadow']:
                offset = cfg['shadow'] * h / max(1,cfg['_canvas_height'])
                p.setPen(Qt.NoPen); p.setBrush(QColor(0,0,0,110))
                p.drawRoundedRect(face.translated(offset,offset),radius,radius)
            clip = QPainterPath(); clip.addRoundedRect(face,radius,radius)
            p.setClipPath(clip)
            p.drawImage(QRectF(*displayed), image)
            p.end(); image = frame
        self._image.setPixmap(QPixmap.fromImage(image))
        self._image.setGeometry(self.video_surface.rect())
        if self.isVisible() and not getattr(self.window(),'_project_loading_in_progress',False):
            self.video_surface.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_image'):
            self._image.setGeometry(self.video_surface.rect())
            if getattr(self, 'video_layout', None):
                self._render_frame()

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
