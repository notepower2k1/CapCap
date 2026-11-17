"""Video pixels visible inside the output canvas, excluding letterboxing."""
def visible_video_rect(view):
    canvas = view.get_preview_canvas_rect()
    rect = view.get_video_content_rect().intersected(canvas)
    return rect if rect.width() > 0 and rect.height() > 0 else canvas


def layer_bounds_rect(view):
    return view.get_preview_canvas_rect()


def has_letterbox(view, tolerance=.5):
    """Detect canvas padding, not black pixels already encoded in a video."""
    canvas, visible = view.get_preview_canvas_rect(), visible_video_rect(view)
    if canvas.width() <= 0 or canvas.height() <= 0:
        return False
    return any(abs(a - b) > tolerance for a, b in zip(
        (canvas.left(), canvas.top(), canvas.right(), canvas.bottom()),
        (visible.left(), visible.top(), visible.right(), visible.bottom())))


def native_subtitle_preview(view):
    return not getattr(view, 'video_layout', None) and not has_letterbox(view)


def source_to_canvas(view, x, y):
    source, canvas = view.get_video_content_rect(), view.get_preview_canvas_rect()
    return ((source.left() + x * source.width() - canvas.left()) / max(1., canvas.width()),
            (source.top() + y * source.height() - canvas.top()) / max(1., canvas.height()))


def canvas_to_source_point(view, x, y):
    source, canvas = view.get_video_content_rect(), view.get_preview_canvas_rect()
    return ((canvas.left() + x * canvas.width() - source.left()) / max(1., source.width()),
            (canvas.top() + y * canvas.height() - source.top()) / max(1., source.height()))
