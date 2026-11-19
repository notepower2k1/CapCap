"""Static canvas and layer inventory for the selected layout."""
from collections import Counter
from html import escape

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QImageReader, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.layout_presets import BACKGROUND_ASSET, LOGO_ASSET, sample_asset
from app.layers.stack import DEFAULT_ORDER, overlay_order
from app.video_layout import config, geometry
from i18n import t


_KINDS = {
    'background': ('Background', '#93adcb'),
    'background_image': ('Background image', '#93adcb'),
    'background_blur': ('Canvas Blur', '#93adcb'),
    'video': ('Video', '#7cb6ff'),
    'blur': ('Blur Area', '#67dce2'),
    'mask': ('Mask', '#ffa986'),
    'overlay': ('Mask overlay image', '#ffa986'),
    'logo': ('Logo', '#79e6b9'),
    'text': ('Text Layer', '#ffd789'),
    'subtitle': ('Subtitle position', '#d7b9ff'),
    'subtitle_secondary': ('Secondary subtitles', '#f0acd5'),
}


def _saved_layers(recipe):
    controls = recipe['controls']
    order = overlay_order(recipe.get('overlay_order')) or DEFAULT_ORDER
    ranks = {group: rank for rank, group in enumerate(('background', 'video', 'blur', *order))}
    visible_tracks = controls.get('visibility', {})
    layers = [('video', {}, True)]
    for row in recipe['tracks']:
        track = row['track']
        for layer in track.get('layers', ()):
            kind = layer['type']
            group = 'logo' if kind == 'image' else kind
            visible = track.get('visible', True) and layer.get('visible', True)
            if group in ('text', 'logo', 'mask'):
                visible = visible and visible_tracks.get(f'_{group}_track_preview_visible', True)
            elif group == 'blur':
                visible = visible and controls.get('blur_enabled', False)
            layers.append((group, layer, visible))
    return sorted(layers, key=lambda item: ranks[item[0]])


def _kind(group, layer):
    if group == 'background':
        return {'image': 'background_image', 'blur': 'background_blur'}.get(layer.get('mode'), group)
    return 'overlay' if group == 'mask' and layer.get('mode') == 'image' else group


class LayoutPreview(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(t('Layout preview'))
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        self.canvas = _LayoutCanvas()
        layout.addWidget(self.canvas)
        self.inventory = QLabel()
        self.inventory.setWordWrap(True)
        self.inventory.setTextFormat(Qt.RichText)
        layout.addWidget(self.inventory)

    def refresh(self):
        owner = self.controller
        gui = owner.gui
        recipe = owner.saved.pending
        presentation = getattr(gui, 'subtitle_presentation_controller', None)
        subtitle_cfg = presentation.cfg if presentation else {}
        styles = subtitle_cfg.get('styles', {})
        if recipe is not None:
            cfg = config(recipe.get('layout'))
            canvas, source = (tuple(map(float, recipe[key])) for key in ('canvas', 'source'))
            mode = recipe['controls'].get('scale_mode', 'fit')
            focus = recipe['controls'].get('focus') or (.5, .5)
            styles = recipe['styles']
            layers = _saved_layers(recipe)
        else:
            cfg = owner.draft()
            canvas = owner.canvas_size()
            source = owner.saved.source_size()
            mode, focus = gui.get_output_scale_mode_key(), gui.get_output_fill_focus()
            layers = [('video', {}, True)]
            if cfg['template'] != 'free':
                layers.insert(0, ('background', cfg, True))
            layers.extend(
                ('text', {'text': cfg[role], 'canvas_y': y, 'font_size': canvas[1] * size}, True)
                for role, y, size in owner.TEXT_SLOTS.get(cfg['template'], ())
            )
        subtitle_mode = subtitle_cfg.get('mode', 'single')
        if recipe is not None or cfg['template'] in owner.SUBTITLE_POSITIONS:
            roles = ('primary', 'secondary') if subtitle_mode == 'bilingual' else ('primary',)
            for index, role in enumerate(roles):
                position = styles.get(role, {}).get('position') or {}
                fallback_y = (89, 79)[index]
                y = position.get('custom_position_y', fallback_y) if position.get('custom_position_enabled') else fallback_y
                x = position.get('custom_position_x', 50) if position.get('custom_position_enabled') else 50
                if recipe is None:
                    x, y = 50, owner.SUBTITLE_POSITIONS[cfg['template']][index]
                group = 'subtitle' if index == 0 else 'subtitle_secondary'
                layers.append((group, {'canvas_x': float(x) / 100, 'canvas_y': float(y) / 100},
                               subtitle_mode != 'none' and getattr(gui, '_subtitle_track_preview_visible', True)))
        self.canvas.scene = cfg, canvas, source, mode, focus, layers
        self.canvas.update()
        counts = Counter((_kind(group, layer), visible) for group, layer, visible in layers)
        chips = []
        for (kind, visible), count in counts.items():
            label, color = _KINDS[kind]
            name = t(label)
            if count > 1:
                name = f'{name} ×{count}'
            if not visible:
                name = t('{layer} (hidden)', layer=name)
                color = '#98a5b4'
            chips.append(f'<span style="color:{color};">●</span> {escape(name)}')
        self.inventory.setText(' &nbsp; '.join(chips))


class _LayoutCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(224)
        self.scene = None
        self.samples = {}

    def _sample(self, token):
        if token not in self.samples:
            reader = QImageReader(sample_asset(token))
            size = reader.size().scaled(QSize(360, 224), Qt.KeepAspectRatio)
            if size.isValid():
                reader.setScaledSize(size)
            self.samples[token] = QPixmap.fromImage(reader.read())
        return self.samples[token]

    def paintEvent(self, event):
        if self.scene is None or self.width() <= 16:
            return
        cfg, (cw, ch), source, mode, focus, layers = self.scene
        scale = min((self.width() - 16) / cw, (self.height() - 16) / ch)
        w, h = cw * scale, ch * scale
        canvas = QRectF(0, 0, w, h)
        slot, source_box = (QRectF(*rect) for rect in geometry(cfg, w, h, *source, mode, focus))
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        painter.translate((self.width() - w) / 2, (self.height() - h) / 2)
        painter.setClipRect(canvas)
        painter.fillRect(canvas, QColor('#101826'))
        for group, layer, visible in layers:
            if visible:
                self._draw_layer(painter, group, layer, cfg, canvas, slot, source_box, scale)
        painter.end()

    def _draw_layer(self, p, group, layer, cfg, canvas, slot, source_box, scale):
        kind = _kind(group, layer)
        label, color = _KINDS[kind]
        color = QColor(color)
        w, h = canvas.width(), canvas.height()
        if group == 'background':
            if kind == 'background_image':
                pixmap = self._sample(BACKGROUND_ASSET)
                p.drawPixmap(canvas, pixmap, QRectF(pixmap.rect()))
            else:
                fill = QColor(layer.get('color', '#101826'))
                p.fillRect(canvas, QBrush(color.darker(270), Qt.Dense4Pattern) if kind == 'background_blur' else fill)
            return
        if group == 'video':
            box = source_box.intersected(slot)
            radius = min(cfg['corner'] * scale, box.height() / 2)
            p.setPen(Qt.NoPen)
            if cfg['shadow']:
                p.setBrush(QColor(0, 0, 0, 125))
                p.drawRoundedRect(box.translated(0, max(2., cfg['shadow'] * scale)), radius, radius)
            gradient = QLinearGradient(box.topLeft(), box.bottomRight())
            gradient.setColorAt(0, color.darker(235))
            gradient.setColorAt(1, color.darker(355))
            p.setBrush(gradient)
            p.drawRoundedRect(box, radius, radius)
            self._caption(p, box.center(), canvas, t('Video'), color, 12)
            return

        x0, y0, sw, sh = source_box.getRect()
        transform = layer.get('transform', {})
        if group in ('mask', 'blur'):
            rect = QRectF(x0 + sw * float(layer.get('position_x', .3)), y0 + sh * float(layer.get('position_y', .4)),
                          sw * float(layer.get('width', .4)), sh * float(layer.get('height', .2))).intersected(slot)
            if kind == 'overlay':
                sample = self._sample(LOGO_ASSET)
                p.fillRect(rect, color.darker(315))
                p.drawPixmap(rect, sample, QRectF(sample.rect()))
            else:
                p.fillRect(rect, QBrush(color, Qt.Dense4Pattern))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 1.2))
            p.drawRect(rect)
            return
        if group == 'logo':
            pixmap = self._sample(LOGO_ASSET)
            x, y = x0 + sw * float(transform.get('x', 0)) / 100, y0 + sh * float(transform.get('y', 0)) / 100
            width = max(8., sw * .2 * abs(float(transform.get('scale_x', 1))))
            height = max(8., sh * .2 * abs(float(transform.get('scale_y', 1))))
            rect = QRectF(x, y, width, height)
            p.fillRect(rect, color.darker(315))
            p.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
            return
        x = w * layer['canvas_x'] if 'canvas_x' in layer else x0 + sw * float(transform.get('x', .5))
        y = h * layer['canvas_y'] if 'canvas_y' in layer else y0 + sh * float(transform.get('y', .5))
        text = ' '.join(str(layer.get('text', t(label))).split()) or t(label)
        size = max(8, min(16, round(float(layer.get('font_size', 0)) * scale))) if group == 'text' else 10
        self._caption(p, QPointF(x, y), canvas, text, color, size)

    @staticmethod
    def _caption(p, anchor, canvas, text, color, size):
        if not canvas.contains(anchor):
            return
        font = p.font()
        font.setPixelSize(size)
        font.setBold(True)
        p.setFont(font)
        metrics = p.fontMetrics()
        text = metrics.elidedText(text, Qt.ElideRight, max(1, int(canvas.width() - 12)))
        box = QRectF(0, 0, metrics.horizontalAdvance(text) + 10, metrics.height() + 4)
        box.moveCenter(anchor)
        box.moveLeft(max(0, min(box.left(), canvas.right() - box.width())))
        box.moveTop(max(0, min(box.top(), canvas.bottom() - box.height())))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(13, 22, 35, 225))
        p.drawRoundedRect(box, 3, 3)
        p.setPen(color)
        p.drawText(box, Qt.AlignCenter, text)
