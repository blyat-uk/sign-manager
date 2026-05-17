from __future__ import annotations

import logging
import math
from collections import OrderedDict
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal, QObject, QThread
from PyQt6.QtGui import (
    QPainter, QFont, QColor, QPen, QFontMetricsF, QCursor, QPixmap, QImage,
    QPainterPath,
)
from PyQt6.QtWidgets import QWidget, QTextEdit

from sub_label_pos.geometry.coords import (
    ass_to_widget as _coords_ass_to_widget,
    scale_factor as _coords_scale_factor,
    widget_to_ass as _coords_widget_to_ass,
)
from sub_label_pos.geometry.label_geometry import (
    compute_label_rect as _compute_label_rect,
    libass_font_correction as _libass_font_correction,
)
from sub_label_pos.geometry.rich_text import (
    parse_rich_text, segments_to_html, html_to_segments, segments_to_ass,
)
from sub_label_pos.model.ass_file import (
    AssFile, LabelDialogue, _seconds_to_time,
)
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.model.types import LabelId
from sub_label_pos.services.exceptions import VideoServiceError
from sub_label_pos.services.video_service import VideoService
from sub_label_pos.ui.label_toolbar import ass_colour_to_qcolor

log = logging.getLogger(__name__)


_CACHE_MAX = 50
_DRAG_THRESHOLD = 4  # pixels before press becomes drag
_HANDLE_SIZE = 8        # px, side length of corner resize squares
_HANDLE_HIT_RADIUS = 10  # px, hit test tolerance for handles
_ROTATE_OFFSET = 16     # px, distance of rotation handle from corner
_MIN_FONT_SIZE = 6


class _DragMode(Enum):
    NONE = 0
    MOVE = 1
    RESIZE = 2
    ROTATE = 3


def _rotate_point(p: QPointF, center: QPointF, angle_deg: float) -> QPointF:
    rad = math.radians(angle_deg)
    dx, dy = p.x() - center.x(), p.y() - center.y()
    rx = dx * math.cos(rad) - dy * math.sin(rad)
    ry = dx * math.sin(rad) + dy * math.cos(rad)
    return QPointF(center.x() + rx, center.y() + ry)


class VideoSetupWorker(QObject):
    """Fetches fps/dims/duration/initial-frame via a VideoService off the main thread."""
    finished = pyqtSignal(float, object, object, float)  # fps, dims, QImage, duration

    def __init__(self, video_service: VideoService, path: str):
        super().__init__()
        self._svc = video_service
        self._path = path

    def run(self):
        p = Path(self._path)
        try:
            fps = self._svc.get_fps(p)
        except VideoServiceError as e:
            log.warning("get_fps failed for %s: %s", self._path, e)
            fps = 24.0
        try:
            dims = self._svc.get_dimensions(p)
        except VideoServiceError as e:
            log.warning("get_dimensions failed for %s: %s", self._path, e)
            dims = None
        try:
            duration = self._svc.get_duration(p)
        except VideoServiceError as e:
            log.warning("get_duration failed for %s: %s", self._path, e)
            duration = 0.0
        try:
            frame = self._svc.get_frame(p, 0)
        except VideoServiceError as e:
            log.warning("get_frame(0) failed for %s: %s", self._path, e)
            frame = None
        self.finished.emit(fps, dims, frame, duration)


class FramePrefetchWorker(QObject):
    """Extracts nearby frames in the background for smoother stepping."""
    frame_ready = pyqtSignal(int, QPixmap)  # cs_key, pixmap
    finished = pyqtSignal()

    def __init__(self, video_service: VideoService, video_path: str, times: list[float]):
        super().__init__()
        self._svc = video_service
        self._video_path = video_path
        self._times = times
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        p = Path(self._video_path)
        for t in self._times:
            if self._cancelled:
                break
            try:
                img = self._svc.get_frame(p, t)
            except VideoServiceError as e:
                log.warning("prefetch get_frame failed for %s @ %s: %s",
                            self._video_path, t, e)
                continue
            if img and not img.isNull() and not self._cancelled:
                pm = QPixmap.fromImage(img)
                cs_key = int(round(t * 100))
                self.frame_ready.emit(cs_key, pm)
        self.finished.emit()


class VideoFrameWidget(QWidget):
    """Displays video frames extracted via ffmpeg and renders draggable ASS labels."""

    label_moved = pyqtSignal(LabelDialogue, int, int)  # label, new_x, new_y
    label_resized = pyqtSignal(object, int)    # label, new_font_size
    label_rotated = pyqtSignal(object, float)  # label, new_rotation_degrees
    edit_requested = pyqtSignal(LabelDialogue)
    text_edited = pyqtSignal(LabelDialogue, str)
    editing_cancelled = pyqtSignal()
    context_menu_requested = pyqtSignal(QPointF)
    empty_context_menu_requested = pyqtSignal(QPointF)
    drag_started = pyqtSignal()   # emitted when move or rotate drag begins
    drag_finished = pyqtSignal()  # emitted when move or rotate drag ends

    def __init__(self, store: LabelStore, video_service: VideoService, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setStyleSheet("background: #1e1e1e;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._store = store
        self._video_service = video_service
        self._video_path: str | None = None
        self._ass: AssFile | None = None
        self._font_corrections: dict[tuple[str, bool, bool], float] = {}
        self._visible_labels: list[LabelDialogue] = []
        self._label_rects: dict[int, QRectF] = {}

        # Frame display
        self._pixmap: QPixmap | None = None  # original resolution
        self._scaled_pixmap: QPixmap | None = None
        self._frame_x = 0
        self._frame_y = 0
        self._frame_w = 1
        self._frame_h = 1

        # Frame cache: centisecond key -> QPixmap
        self._cache: OrderedDict[int, QPixmap] = OrderedDict()

        # Current time
        self._current_time = 0.0
        self._fps: float = 24.0

        # Selection state is owned by the store. Subscribe to changes so the
        # widget repaints when selection updates from any source (toolbar,
        # gallery, programmatic clear-on-load, etc.).
        self._store.selection_changed.connect(self._on_store_selection_changed)
        # Refresh visible_labels (which reads from store.state) whenever the
        # store reports structural changes -- the LabelDialogue instances in
        # state get swapped in by mutations, so we re-collect them and repaint.
        self._store.labels_mutated.connect(self._on_store_labels_changed)
        self._store.labels_added.connect(self._on_store_labels_changed)
        self._store.labels_removed.connect(self._on_store_labels_changed)

        # Click/drag distinction
        self._press_pos: QPointF | None = None
        self._press_label: LabelDialogue | None = None
        self._drag_started: bool = False

        # Drag state (single or multi)
        self._drag_mode: _DragMode = _DragMode.NONE
        self._dragging: LabelDialogue | None = None
        self._drag_offset = QPointF()
        self._multi_drag_initial: dict[int, tuple[int, int]] = {}  # line_index -> (pos_x, pos_y)

        # Resize/rotate handle state
        self._resize_corner: int = -1       # 0=TL, 1=TR, 2=BR, 3=BL
        self._resize_initial_fs: int = 0
        self._resize_initial_dist: float = 0.0
        self._rotate_initial_angle: float = 0.0
        self._rotate_initial_frz: float = 0.0
        self._handle_label: LabelDialogue | None = None

        # Snap guides
        self._snap_lines: list[tuple[QPointF, QPointF, bool]] = []  # (p1, p2, is_screen_center)
        self._SNAP_THRESHOLD = 8

        # Inline text editing
        self._editing_label: LabelDialogue | None = None
        self._text_edit: QTextEdit | None = None

        # Hover tracking
        self._hovered_label: LabelDialogue | None = None

        # Frame prefetch
        self._prefetch_thread: QThread | None = None
        self._prefetch_worker: FramePrefetchWorker | None = None
        self._prefetched_times: set[int] = set()  # cs_keys of center times already prefetched

    def set_ass(self, ass: AssFile | None):
        self._ass = ass
        self._font_corrections.clear()
        # Selection is owned by the store; clearing on file change is the
        # store's job (LabelStore.load wipes selection). We just cancel any
        # in-progress edit.
        self._cancel_editing()
        self.update()

    # --- Store signal slots ---------------------------------------------

    def _on_store_selection_changed(self, _selected_ids: set) -> None:
        """Selection changed in the store — repaint to reflect new state."""
        self.update()

    def _on_store_labels_changed(self, _ids: set) -> None:
        """Store reported labels added/removed/mutated -- refresh visible list."""
        self._update_visible_labels(self._current_time)
        self.update()

    # --- Selection helpers (read-through to the store) ------------------

    def _selected_ids(self) -> set[LabelId]:
        """Current selection as label_ids, sourced from the store."""
        return self._store.selected

    def _set_selection(self, new_ids: set[LabelId]) -> None:
        """Submit a new selection to the store. The store fires
        ``selection_changed`` which triggers our repaint slot."""
        self._store.set_selection(new_ids)

    def set_video(self, path: str):
        self._cancel_prefetch()
        self._video_path = path
        self._cache.clear()
        self._prefetched_times.clear()
        self._fps = 24.0

    def set_fps(self, fps: float):
        self._fps = fps

    def show_frame_from_image(self, image: QImage):
        """Convert a QImage (from async worker) to QPixmap and display it."""
        if image.isNull():
            return
        pm = QPixmap.fromImage(image)
        self._pixmap = pm
        cs_key = int(round(self._current_time * 100))
        self._cache[cs_key] = pm
        if len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)
        self._update_scaled_pixmap()
        self.update()

    def step_frame(self, delta: int):
        """Step delta frames forward (positive) or backward (negative)."""
        self._current_time += delta / self._fps
        self._current_time = max(0.0, self._current_time)
        self.show_time(self._current_time)

    def show_time(self, seconds: float):
        """Extract and display the frame at the given time."""
        self._current_time = seconds
        self._update_visible_labels(seconds)

        if not self._video_path:
            return

        cs_key = int(round(seconds * 100))
        if cs_key in self._cache:
            self._cache.move_to_end(cs_key)
            self._pixmap = self._cache[cs_key]
        else:
            pixmap = self._extract_frame(seconds)
            if pixmap and not pixmap.isNull():
                self._pixmap = pixmap
                self._cache[cs_key] = pixmap
                if len(self._cache) > _CACHE_MAX:
                    self._cache.popitem(last=False)

        self._update_scaled_pixmap()
        self.update()

    def _extract_frame(self, seconds: float) -> QPixmap | None:
        if not self._video_path:
            return None
        try:
            img = self._video_service.get_frame(Path(self._video_path), seconds)
        except VideoServiceError as e:
            log.warning("get_frame failed for %s @ %s: %s",
                        self._video_path, seconds, e)
            return None
        if img.isNull():
            return None
        return QPixmap.fromImage(img)

    def _update_scaled_pixmap(self):
        if not self._pixmap or self._pixmap.isNull():
            self._scaled_pixmap = None
            return

        pw = self._pixmap.width()
        ph = self._pixmap.height()
        ww = self.width()
        wh = self.height()

        if ww <= 0 or wh <= 0:
            return

        scale = min(ww / pw, wh / ph)
        self._frame_w = int(pw * scale)
        self._frame_h = int(ph * scale)
        self._frame_x = (ww - self._frame_w) // 2
        self._frame_y = (wh - self._frame_h) // 2

        self._scaled_pixmap = self._pixmap.scaled(
            self._frame_w, self._frame_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _update_visible_labels(self, current_time: float):
        """Recompute ``self._visible_labels`` from the live store state.

        Labels are sourced from ``self._store.state`` (the source of truth),
        not from ``self._ass.labels`` -- store mutations don't refresh the
        AssFile's label list, so reading from state guarantees we see the
        latest LabelDialogue instances (mutations swap them in via
        dataclasses.replace).
        """
        state = self._store.state
        if not state.order:
            self._visible_labels = []
        else:
            visible: list[LabelDialogue] = []
            for lid in state.order:
                lb = state.labels[lid]
                if lb.start_time <= current_time <= lb.end_time:
                    visible.append(lb)
            self._visible_labels = visible
        # Prune selection to only visible labels. Selection is owned by the
        # store; we ask the store to drop ids that are no longer visible.
        visible_ids = {lb.label_id for lb in self._visible_labels if lb.label_id}
        current = self._store.selected
        pruned = current & visible_ids
        if pruned != current:
            self._store.set_selection(pruned)

    # ── Coordinate mapping ──

    def _ass_to_widget(self, ass_x: float, ass_y: float) -> QPointF:
        if not self._ass:
            return QPointF(ass_x, ass_y)
        ass_size = (self._ass.play_res_x, self._ass.play_res_y)
        frame_size = (self._frame_w, self._frame_h)
        inner = _coords_ass_to_widget(QPointF(ass_x, ass_y), ass_size, frame_size)
        return QPointF(self._frame_x + inner.x(), self._frame_y + inner.y())

    def _widget_to_ass(self, wx: float, wy: float) -> tuple[int, int]:
        if not self._ass:
            return int(wx), int(wy)
        ass_size = (self._ass.play_res_x, self._ass.play_res_y)
        frame_size = (self._frame_w, self._frame_h)
        local = QPointF(wx - self._frame_x, wy - self._frame_y)
        ass_pt = _coords_widget_to_ass(local, ass_size, frame_size)
        return int(round(ass_pt.x())), int(round(ass_pt.y()))

    # ── Label geometry ──

    def _get_font_correction(self, font_name: str, bold: bool, italic: bool) -> float:
        key = (font_name, bold, italic)
        if key not in self._font_corrections:
            probe = QFont(font_name)
            probe.setPixelSize(96)
            probe.setBold(bold)
            probe.setItalic(italic)
            self._font_corrections[key] = _libass_font_correction(probe)
        return self._font_corrections[key]

    def _style_for_label(self, label: LabelDialogue) -> tuple[str, int, bool, bool]:
        """Return (font_name, font_size, bold, italic) for a label, applying style + overrides."""
        if not self._ass:
            return ("Arial", 36, False, False)
        style = self._ass.styles.get(label.style_name)
        if style:
            font_name = style.font_name
            base_size = label.font_size if label.font_size is not None else style.font_size
            bold = label.bold if label.bold is not None else style.bold
            italic = label.italic if label.italic is not None else style.italic
        else:
            font_name = self._ass.label_font_name
            base_size = label.font_size if label.font_size is not None else self._ass.label_font_size
            bold = label.bold if label.bold is not None else self._ass.label_bold
            italic = label.italic if label.italic is not None else self._ass.label_italic
        return (font_name, base_size, bold, italic)

    def _font_for_label(self, label: LabelDialogue | None = None, bold_override: bool | None = None, italic_override: bool | None = None) -> QFont:
        if not self._ass:
            return QFont("Arial", 16)
        if label:
            font_name, base_size, bold, italic = self._style_for_label(label)
        else:
            font_name = self._ass.label_font_name
            base_size = self._ass.label_font_size
            bold = self._ass.label_bold
            italic = self._ass.label_italic
        if bold_override is not None:
            bold = bold_override
        if italic_override is not None:
            italic = italic_override
        correction = self._get_font_correction(font_name, bold, italic)
        scale = self._frame_h / self._ass.play_res_y
        pixel_size = max(int(base_size * scale * correction), 8)
        font = QFont(font_name)
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        font.setItalic(italic)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return font

    def _compute_rect(self, label: LabelDialogue, font: QFont) -> QRectF:
        _, _, default_bold, default_italic = self._style_for_label(label)
        default_alignment = self._ass.label_alignment if self._ass else 2

        def font_provider(bold_override, italic_override):
            if bold_override is None and italic_override is None:
                return font
            return self._font_for_label(
                label,
                bold_override=bold_override,
                italic_override=italic_override,
            )

        return _compute_label_rect(
            label,
            default_alignment=default_alignment,
            default_bold=default_bold,
            default_italic=default_italic,
            font_provider=font_provider,
            ass_pos_to_widget=self._ass_to_widget,
        )

    def _anchor_from_rect(self, rect: QRectF, label: LabelDialogue | None = None) -> QPointF:
        alignment = self._ass.label_alignment if self._ass else 2
        if label is not None and label.alignment is not None:
            alignment = label.alignment
        h_align = ((alignment - 1) % 3) + 1
        v_group = (alignment - 1) // 3

        if h_align == 1:
            ax = rect.x()
        elif h_align == 3:
            ax = rect.right()
        else:
            ax = rect.center().x()

        if v_group == 0:
            ay = rect.bottom()
        elif v_group == 1:
            ay = rect.center().y()
        else:
            ay = rect.top()

        return QPointF(ax, ay)

    # ── Handle geometry ──

    def _get_handle_positions(self, rect: QRectF, anchor: QPointF, rotation: float):
        """Return (resize_positions, rotate_positions) as lists of 4 QPointFs each."""
        corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
        # Direction vectors pointing outward from center for each corner
        diag_dirs = [(-1, -1), (1, -1), (1, 1), (-1, 1)]

        resize_pts = []
        rotate_pts = []
        for i, corner in enumerate(corners):
            rc = _rotate_point(corner, anchor, -rotation) if rotation != 0 else corner
            resize_pts.append(rc)
            dx, dy = diag_dirs[i]
            norm = math.sqrt(2)
            offset = QPointF(dx / norm * _ROTATE_OFFSET, dy / norm * _ROTATE_OFFSET)
            rotate_pts.append(QPointF(rc.x() + offset.x(), rc.y() + offset.y()))
        return resize_pts, rotate_pts

    def _draw_handles(self, painter: QPainter, rect: QRectF, anchor: QPointF, rotation: float):
        hs = _HANDLE_SIZE
        resize_pts, rotate_pts = self._get_handle_positions(rect, anchor, rotation)

        # Resize handles — white filled squares
        painter.setPen(QPen(QColor(74, 158, 255), 1.0))
        for pt in resize_pts:
            handle_rect = QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs)
            painter.fillRect(handle_rect, QColor(255, 255, 255))
            painter.drawRect(handle_rect)

        # Rotation handles — small curved arrows
        for pt in rotate_pts:
            self._draw_rotation_handle(painter, pt)

    def _draw_rotation_handle(self, painter: QPainter, center: QPointF, size: float = 5.0):
        r = size
        arc_rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)
        path = QPainterPath()
        path.arcMoveTo(arc_rect, 45)
        path.arcTo(arc_rect, 45, 270)
        end = path.currentPosition()

        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # Small arrowhead at the end of the arc
        arrow_len = 3.0
        # The arc ends at 45+270 = 315 degrees, tangent points inward
        angle_rad = math.radians(315)
        tangent_angle = angle_rad + math.pi / 2  # perpendicular to radius
        a1 = tangent_angle + math.radians(30)
        a2 = tangent_angle - math.radians(30)
        painter.drawLine(end, QPointF(end.x() + arrow_len * math.cos(a1),
                                       end.y() - arrow_len * math.sin(a1)))
        painter.drawLine(end, QPointF(end.x() + arrow_len * math.cos(a2),
                                       end.y() - arrow_len * math.sin(a2)))

    def _hit_test_handles(self, pos: QPointF) -> tuple[_DragMode, int, LabelDialogue] | None:
        """Check if pos hits a resize or rotation handle. Single-select only."""
        selected_ids = self._selected_ids()
        if len(selected_ids) != 1:
            return None
        label = None
        for lb in self._visible_labels:
            if lb.label_id in selected_ids:
                label = lb
                break
        if label is None:
            return None

        rect = self._label_rects.get(label.line_index)
        if rect is None:
            return None

        rotation = label.rotation if label.rotation is not None else 0.0
        anchor = self._anchor_from_rect(rect, label)
        resize_pts, rotate_pts = self._get_handle_positions(rect, anchor, rotation)

        # Check resize handles first (higher priority, closer to label)
        for i, pt in enumerate(resize_pts):
            dist = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
            if dist < _HANDLE_HIT_RADIUS:
                return (_DragMode.RESIZE, i, label)

        # Check rotation handles
        for i, pt in enumerate(rotate_pts):
            dist = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
            if dist < _HANDLE_HIT_RADIUS:
                return (_DragMode.ROTATE, i, label)

        return None

    # ── Selection helpers ──

    def selected_labels(self) -> list[LabelDialogue]:
        selected_ids = self._selected_ids()
        return [lb for lb in self._visible_labels if lb.label_id in selected_ids]

    def clear_selection(self):
        if self._store.selected:
            self._store.set_selection(set())
            # repaint happens via selection_changed slot

    # ── Painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw frame
        if self._scaled_pixmap and not self._scaled_pixmap.isNull():
            painter.drawPixmap(self._frame_x, self._frame_y, self._scaled_pixmap)

        # Draw labels
        if self._visible_labels:
            self._label_rects.clear()
            selected_ids = self._selected_ids()

            for label in self._visible_labels:
                # Skip the label being edited inline
                if self._editing_label and label.line_index == self._editing_label.line_index:
                    continue

                font = self._font_for_label(label)
                painter.setFont(font)
                rect = self._compute_rect(label, font)
                self._label_rects[label.line_index] = rect

                rotation = label.rotation if label.rotation is not None else 0.0
                anchor = self._anchor_from_rect(rect, label)

                if rotation != 0:
                    painter.save()
                    painter.translate(anchor)
                    painter.rotate(-rotation)  # ASS is counterclockwise, Qt is clockwise
                    painter.translate(-anchor)

                painter.fillRect(rect, QColor(0, 0, 0, 140))

                is_selected = label.label_id in selected_ids
                if is_selected:
                    pen = QPen(QColor(74, 158, 255), 2.0, Qt.PenStyle.SolidLine)
                else:
                    pen = QPen(QColor(255, 255, 255, 180), 1.5, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)

                text_rect = rect.adjusted(6, 4, -6, -4)

                # Resolve colours and outline for this label
                style = self._ass.styles.get(label.style_name) if self._ass else None
                text_colour = ass_colour_to_qcolor(
                    label.primary_colour if label.primary_colour is not None else
                    (style.primary_colour if style else "&H00FFFFFF&")
                )
                outline_colour = ass_colour_to_qcolor(
                    label.outline_colour if label.outline_colour is not None else
                    (style.outline_colour if style else "&H00000000&")
                )
                outline_width = (
                    label.outline_width if label.outline_width is not None else
                    (style.outline_width if style else 2.0)
                )

                # Segment-aware multi-line rendering
                font_name, base_size, default_bold, default_italic = self._style_for_label(label)
                segments = parse_rich_text(label.rich_text, default_bold, default_italic)

                # Split segments by \N into lines
                from sub_label_pos.model.ass_file import TextSegment
                seg_lines: list[list[TextSegment]] = [[]]
                for seg in segments:
                    parts = seg.text.split("\\N")
                    for pi, part in enumerate(parts):
                        if pi > 0:
                            seg_lines.append([])
                        if part:
                            seg_lines[-1].append(TextSegment(part, seg.bold, seg.italic))

                # Compute max line height
                max_line_h = QFontMetricsF(font).height()
                for seg_line in seg_lines:
                    for seg in seg_line:
                        seg_font = self._font_for_label(label, bold_override=seg.bold, italic_override=seg.italic)
                        max_line_h = max(max_line_h, QFontMetricsF(seg_font).height())

                alignment = label.alignment if label.alignment is not None else (self._ass.label_alignment if self._ass else 2)
                h_align = ((alignment - 1) % 3) + 1  # 1=left, 2=center, 3=right

                for li, seg_line in enumerate(seg_lines):
                    # Measure total line width for alignment
                    line_w = 0.0
                    for seg in seg_line:
                        seg_font = self._font_for_label(label, bold_override=seg.bold, italic_override=seg.italic)
                        line_w += QFontMetricsF(seg_font).horizontalAdvance(seg.text)

                    if h_align == 1:
                        x_offset = text_rect.x()
                    elif h_align == 3:
                        x_offset = text_rect.right() - line_w
                    else:
                        x_offset = text_rect.x() + (text_rect.width() - line_w) / 2
                    y_pos = text_rect.y() + li * max_line_h

                    for seg in seg_line:
                        seg_font = self._font_for_label(label, bold_override=seg.bold, italic_override=seg.italic)
                        seg_fm = QFontMetricsF(seg_font)
                        seg_w = seg_fm.horizontalAdvance(seg.text)
                        baseline_y = y_pos + (max_line_h + seg_fm.ascent() - seg_fm.descent()) / 2

                        path = QPainterPath()
                        path.addText(x_offset, baseline_y, seg_font, seg.text)

                        if outline_width > 0:
                            painter.setPen(QPen(outline_colour, outline_width * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                            painter.setBrush(Qt.BrushStyle.NoBrush)
                            painter.drawPath(path)

                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(text_colour)
                        painter.drawPath(path)

                        x_offset += seg_w

                if rotation != 0:
                    painter.restore()

                # Draw resize/rotate handles for single selection
                if is_selected and len(selected_ids) == 1:
                    self._draw_handles(painter, rect, anchor, rotation)

        # Draw hovered label timestamp
        if self._hovered_label is not None:
            hr = self._label_rects.get(self._hovered_label.line_index)
            if hr is not None:
                ts = (f"{_seconds_to_time(self._hovered_label.start_time)}"
                      f" \u2192 {_seconds_to_time(self._hovered_label.end_time)}")
                ts_font = QFont("monospace", 10)
                ts_font.setPixelSize(12)
                painter.setFont(ts_font)
                ts_fm = QFontMetricsF(ts_font)
                tw = ts_fm.horizontalAdvance(ts) + 8
                th = ts_fm.height() + 4
                tx = hr.center().x() - tw / 2
                ty = hr.bottom() + 3
                ts_rect = QRectF(tx, ty, tw, th)
                painter.fillRect(ts_rect, QColor(0, 0, 0, 180))
                painter.setPen(QColor(220, 220, 220))
                painter.drawText(ts_rect, Qt.AlignmentFlag.AlignCenter, ts)

        # Draw snap guide lines
        if self._snap_lines:
            for p1, p2, is_screen_center in self._snap_lines:
                if is_screen_center:
                    pen = QPen(QColor(255, 180, 0, 180), 1, Qt.PenStyle.DashLine)
                else:
                    pen = QPen(QColor(0, 180, 255, 180), 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(p1, p2)

        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scaled_pixmap()
        self.update()

    # ── Mouse interaction ──

    def _hit_test(self, pos: QPointF) -> LabelDialogue | None:
        for label in reversed(self._visible_labels):
            rect = self._label_rects.get(label.line_index)
            if not rect:
                continue
            rotation = label.rotation if label.rotation is not None else 0.0
            if rotation != 0:
                # Rotate the test point into the label's local (unrotated) space
                anchor = self._anchor_from_rect(rect, label)
                local_pos = _rotate_point(pos, anchor, rotation)
                if rect.contains(local_pos):
                    return label
            elif rect.contains(pos):
                return label
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            # Check handles first (resize/rotate)
            handle_hit = self._hit_test_handles(pos)
            if handle_hit:
                mode, corner, label = handle_hit
                self._drag_mode = mode
                self._handle_label = label
                self._resize_corner = corner
                self._drag_started = True
                self._press_pos = pos

                rect = self._label_rects.get(label.line_index)
                if rect:
                    anchor = self._anchor_from_rect(rect, label)
                    if mode == _DragMode.RESIZE:
                        font_name, base_size, bold, italic = self._style_for_label(label)
                        self._resize_initial_fs = label.font_size if label.font_size is not None else base_size
                        self._resize_initial_dist = math.hypot(
                            pos.x() - anchor.x(), pos.y() - anchor.y()
                        )
                    elif mode == _DragMode.ROTATE:
                        self._rotate_initial_angle = math.atan2(
                            pos.y() - anchor.y(), pos.x() - anchor.x()
                        )
                        self._rotate_initial_frz = label.rotation if label.rotation is not None else 0.0
                if mode == _DragMode.ROTATE:
                    self.drag_started.emit()
                event.accept()
                return

            self._press_pos = pos
            self._press_label = self._hit_test(pos)
            self._drag_started = False
            self._drag_mode = _DragMode.NONE
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            label = self._hit_test(event.position())
            if label:
                # Select if not already selected
                if label.label_id not in self._selected_ids():
                    self._set_selection({label.label_id})
                self.context_menu_requested.emit(event.position())
            else:
                self.empty_context_menu_requested.emit(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()

        # Active resize drag
        if self._drag_started and self._drag_mode == _DragMode.RESIZE:
            self._do_resize_move(pos)
            event.accept()
            return

        # Active rotate drag
        if self._drag_started and self._drag_mode == _DragMode.ROTATE:
            self._do_rotate_move(pos)
            event.accept()
            return

        # Active move drag in progress
        if self._drag_started and self._dragging:
            self._do_drag_move(pos)
            event.accept()
            return

        # Check if we should start a move drag
        if self._press_pos is not None and self._press_label is not None and self._drag_mode == _DragMode.NONE:
            delta = pos - self._press_pos
            if (delta.x() ** 2 + delta.y() ** 2) ** 0.5 > _DRAG_THRESHOLD:
                self._start_drag(pos)
                event.accept()
                return

        # Hover cursor
        handle_hit = self._hit_test_handles(pos)
        if handle_hit:
            mode, corner, _ = handle_hit
            if mode == _DragMode.RESIZE:
                if corner in (0, 2):  # TL, BR
                    self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
                else:  # TR, BL
                    self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            label = self._hit_test(pos)
            if label:
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            if label is not self._hovered_label:
                self._hovered_label = label
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_started and self._drag_mode == _DragMode.RESIZE:
                self._finish_resize()
            elif self._drag_started and self._drag_mode == _DragMode.ROTATE:
                self._finish_rotate()
            elif self._drag_started and self._dragging:
                # Finish drag — emit label_moved for all selected
                self._finish_drag()
            elif self._press_pos is not None:
                # No drag happened — treat as click
                self._handle_click(event.position(), event.modifiers())
            self._press_pos = None
            self._press_label = None
            self._drag_started = False
            self._drag_mode = _DragMode.NONE
            self._handle_label = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._hovered_label is not None:
            self._hovered_label = None
            self.update()
        super().leaveEvent(event)

    def _handle_click(self, pos: QPointF, modifiers):
        label = self._hit_test(pos)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)

        if label is None:
            # Click on empty space: deselect all
            self._cancel_editing()
            self.clear_selection()
            return

        current = self._selected_ids()

        if ctrl:
            # Ctrl+click: toggle in/out of multi-selection
            self._cancel_editing()
            new = set(current)
            if label.label_id in new:
                new.discard(label.label_id)
            else:
                new.add(label.label_id)
            self._set_selection(new)
            return

        # Plain click on label
        if label.label_id in current and len(current) == 1:
            # Already selected alone — enter edit mode
            self.edit_requested.emit(label)
        else:
            self._cancel_editing()
            self._set_selection({label.label_id})

    # ── Drag (single + multi) ──

    def _start_drag(self, current_pos: QPointF):
        label = self._press_label
        if not label:
            return

        self._drag_started = True
        self._drag_mode = _DragMode.MOVE
        self._dragging = label
        self._hovered_label = None

        # If dragged label isn't selected, select it alone
        if label.label_id not in self._selected_ids():
            self._set_selection({label.label_id})

        # Record initial positions for all selected labels
        self._multi_drag_initial = {
            lb.line_index: (lb.pos_x, lb.pos_y) for lb in self.selected_labels()
        }

        # Compute drag offset from the primary label's anchor
        font = self._font_for_label(label)
        rect = self._compute_rect(label, font)
        anchor = self._anchor_from_rect(rect, label)
        assert self._press_pos is not None
        self._drag_offset = anchor - self._press_pos
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        self.drag_started.emit()

    def _do_drag_move(self, current_pos: QPointF):
        if not self._dragging:
            return

        primary = self._dragging
        new_anchor = current_pos + self._drag_offset
        font = self._font_for_label(primary)

        # Set tentative position to compute the rect
        ass_x, ass_y = self._widget_to_ass(new_anchor.x(), new_anchor.y())
        primary.pos_x = ass_x
        primary.pos_y = ass_y
        tent_rect = self._compute_rect(primary, font)
        tent_center = tent_rect.center()

        # Offset from rect center to anchor point
        offset = tent_center - new_anchor

        # Snap: check dragged label edges/center against other labels and screen center
        snapped_cx, snapped_cy = tent_center.x(), tent_center.y()
        best_dx: float | None = None
        best_dy: float | None = None
        snap_guide_x: float | None = None
        snap_guide_y: float | None = None
        is_screen_center_x = False
        is_screen_center_y = False

        dragged_xs = [tent_rect.left(), tent_center.x(), tent_rect.right()]
        dragged_ys = [tent_rect.top(), tent_center.y(), tent_rect.bottom()]

        selected_ids = self._selected_ids()
        for other in self._visible_labels:
            if other.label_id in selected_ids:
                continue
            other_font = self._font_for_label(other)
            other_rect = self._compute_rect(other, other_font)
            other_xs = [other_rect.left(), other_rect.center().x(), other_rect.right()]
            other_ys = [other_rect.top(), other_rect.center().y(), other_rect.bottom()]

            for dx in dragged_xs:
                for ox in other_xs:
                    dist = abs(dx - ox)
                    if dist < self._SNAP_THRESHOLD and (best_dx is None or dist < best_dx):
                        best_dx = dist
                        snapped_cx = tent_center.x() + (ox - dx)
                        snap_guide_x = ox
                        is_screen_center_x = False

            for dy in dragged_ys:
                for oy in other_ys:
                    dist = abs(dy - oy)
                    if dist < self._SNAP_THRESHOLD and (best_dy is None or dist < best_dy):
                        best_dy = dist
                        snapped_cy = tent_center.y() + (oy - dy)
                        snap_guide_y = oy
                        is_screen_center_y = False

        # Screen center snap
        frame_mid_x = self._frame_x + self._frame_w / 2.0
        frame_mid_y = self._frame_y + self._frame_h / 2.0

        for dx in dragged_xs:
            dist = abs(dx - frame_mid_x)
            if dist < self._SNAP_THRESHOLD and (best_dx is None or dist < best_dx):
                best_dx = dist
                snapped_cx = tent_center.x() + (frame_mid_x - dx)
                snap_guide_x = frame_mid_x
                is_screen_center_x = True

        for dy in dragged_ys:
            dist = abs(dy - frame_mid_y)
            if dist < self._SNAP_THRESHOLD and (best_dy is None or dist < best_dy):
                best_dy = dist
                snapped_cy = tent_center.y() + (frame_mid_y - dy)
                snap_guide_y = frame_mid_y
                is_screen_center_y = True

        # Build snap guide lines
        snap_lines: list[tuple[QPointF, QPointF, bool]] = []
        if snap_guide_x is not None:
            snap_lines.append((
                QPointF(snap_guide_x, self._frame_y),
                QPointF(snap_guide_x, self._frame_y + self._frame_h),
                is_screen_center_x,
            ))
        if snap_guide_y is not None:
            snap_lines.append((
                QPointF(self._frame_x, snap_guide_y),
                QPointF(self._frame_x + self._frame_w, snap_guide_y),
                is_screen_center_y,
            ))
        self._snap_lines = snap_lines

        # Derive snapped anchor from snapped center
        snapped_anchor = QPointF(snapped_cx, snapped_cy) - offset
        final_x, final_y = self._widget_to_ass(snapped_anchor.x(), snapped_anchor.y())

        # Compute delta from primary label's initial position
        init_x, init_y = self._multi_drag_initial.get(
            primary.line_index, (primary.pos_x, primary.pos_y)
        )
        delta_x = final_x - init_x
        delta_y = final_y - init_y

        # Apply delta to all selected labels
        for lb in self.selected_labels():
            lb_init_x, lb_init_y = self._multi_drag_initial.get(
                lb.line_index, (lb.pos_x, lb.pos_y)
            )
            lb.pos_x = lb_init_x + delta_x
            lb.pos_y = lb_init_y + delta_y

        self.update()

    def _finish_drag(self):
        # Emit label_moved for all selected labels
        for lb in self.selected_labels():
            self.label_moved.emit(lb, lb.pos_x, lb.pos_y)
        self._dragging = None
        self._snap_lines.clear()
        self._multi_drag_initial.clear()
        self.update()
        self.drag_finished.emit()

    # ── Resize / Rotate drag ──

    def _do_resize_move(self, pos: QPointF):
        label = self._handle_label
        if not label:
            return
        rect = self._label_rects.get(label.line_index)
        if not rect:
            return
        anchor = self._anchor_from_rect(rect, label)
        current_dist = math.hypot(pos.x() - anchor.x(), pos.y() - anchor.y())
        if self._resize_initial_dist < 1:
            return
        scale = current_dist / self._resize_initial_dist
        new_fs = max(_MIN_FONT_SIZE, round(self._resize_initial_fs * scale))
        # Live preview: mutate the LabelDialogue in place. It's the same
        # Python object the store holds, so the next paint sees the new size
        # without a mutation round-trip. The final size is committed via the
        # label_resized signal -> ResizeLabel mutation in _finish_resize.
        label.font_size = new_fs
        self.label_resized.emit(label, new_fs)
        self.update()

    def _finish_resize(self):
        self._handle_label = None
        self.update()

    def _do_rotate_move(self, pos: QPointF):
        label = self._handle_label
        if not label:
            return
        rect = self._label_rects.get(label.line_index)
        if not rect:
            return
        anchor = self._anchor_from_rect(rect, label)
        current_angle = math.atan2(pos.y() - anchor.y(), pos.x() - anchor.x())
        delta = math.degrees(self._rotate_initial_angle - current_angle)
        new_rotation = self._rotate_initial_frz + delta
        # Normalize to -180..180
        new_rotation = ((new_rotation + 180) % 360) - 180
        label.rotation = new_rotation
        self.update()

    def _finish_rotate(self):
        label = self._handle_label
        if label:
            rotation = label.rotation if label.rotation is not None else 0.0
            # Commit via signal -> RotateLabel mutation in controller.
            self.label_rotated.emit(label, rotation)
        self._handle_label = None
        self.update()
        self.drag_finished.emit()

    # ── Inline text editing ──

    def start_editing(self, label: LabelDialogue):
        self._cancel_editing()
        self._editing_label = label

        font = self._font_for_label(label)
        rect = self._label_rects.get(label.line_index)
        if not rect:
            rect = self._compute_rect(label, font)

        edit = QTextEdit(self)
        edit.setFont(font)
        edit.setAcceptRichText(True)

        # Parse rich_text into segments and convert to HTML
        font_name, base_size, default_bold, default_italic = self._style_for_label(label)
        segments = parse_rich_text(label.rich_text, default_bold, default_italic)
        html = segments_to_html(segments)
        edit.setHtml(html)

        # Style the editor
        edit.setStyleSheet(
            "QTextEdit {"
            "  background: rgba(30, 30, 30, 220);"
            "  color: white;"
            "  border: 2px solid #4a9eff;"
            "  padding: 2px;"
            "}"
        )

        # Size: at least as big as the label rect, but allow growth
        min_w = max(int(rect.width()) + 20, 120)
        min_h = max(int(rect.height()) + 10, 50)
        edit.setFixedSize(min_w, min_h)
        edit.move(int(rect.x()), int(rect.y()))
        edit.setFocus()
        edit.selectAll()

        edit.installEventFilter(self)
        self._text_edit = edit
        edit.show()
        self.update()

    def _commit_editing(self):
        if not self._text_edit or not self._editing_label:
            return
        label = self._editing_label
        html = self._text_edit.toHtml()
        segments = html_to_segments(html)
        # Get style defaults
        font_name, base_size, default_bold, default_italic = self._style_for_label(label)
        rich_text = segments_to_ass(segments, default_bold, default_italic)
        self._cleanup_edit_widget()
        self.text_edited.emit(label, rich_text)

    def _cancel_editing(self):
        was_editing = self._editing_label is not None
        self._cleanup_edit_widget()
        if was_editing:
            self.editing_cancelled.emit()

    def _cleanup_edit_widget(self):
        if self._text_edit:
            self._text_edit.removeEventFilter(self)
            self._text_edit.deleteLater()
            self._text_edit = None
        self._editing_label = None
        self.update()

    def eventFilter(self, obj, event):
        if obj is self._text_edit:
            from PyQt6.QtCore import QEvent
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self._cancel_editing()
                    return True
                # Ctrl+B / Ctrl+I: toggle bold/italic on selection
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    if event.key() == Qt.Key.Key_B:
                        self._toggle_editor_bold()
                        return True
                    elif event.key() == Qt.Key.Key_I:
                        self._toggle_editor_italic()
                        return True
            elif event.type() == QEvent.Type.FocusOut:
                self._commit_editing()
                return True
        return super().eventFilter(obj, event)

    def _toggle_editor_bold(self):
        if not self._text_edit:
            return
        from PyQt6.QtGui import QTextCharFormat
        fmt = self._text_edit.currentCharFormat()
        is_bold = fmt.fontWeight() >= QFont.Weight.Bold
        new_fmt = QTextCharFormat()
        new_fmt.setFontWeight(QFont.Weight.Normal if is_bold else QFont.Weight.Bold)
        self._text_edit.mergeCurrentCharFormat(new_fmt)

    def _toggle_editor_italic(self):
        if not self._text_edit:
            return
        from PyQt6.QtGui import QTextCharFormat
        fmt = self._text_edit.currentCharFormat()
        new_fmt = QTextCharFormat()
        new_fmt.setFontItalic(not fmt.fontItalic())
        self._text_edit.mergeCurrentCharFormat(new_fmt)

    # ── Frame prefetch ──

    def prefetch_around(self, seconds: float, count: int = 10) -> None:
        """Prefetch ±count frames around the given time in the background."""
        center_key = int(round(seconds * 100))
        if center_key in self._prefetched_times:
            return
        self._cancel_prefetch()
        if not self._video_path or self._fps <= 0:
            return
        self._prefetched_times.add(center_key)
        frame_dur = 1.0 / self._fps
        times: list[float] = []
        for i in range(-count, count + 1):
            if i == 0:
                continue
            t = seconds + i * frame_dur
            if t < 0:
                continue
            cs_key = int(round(t * 100))
            if cs_key not in self._cache:
                times.append(t)
        if not times:
            return
        self._prefetch_worker = FramePrefetchWorker(
            self._video_service, self._video_path, times,
        )
        self._prefetch_thread = QThread()
        self._prefetch_worker.moveToThread(self._prefetch_thread)
        self._prefetch_thread.started.connect(self._prefetch_worker.run)
        self._prefetch_worker.frame_ready.connect(self._on_prefetch_frame)
        self._prefetch_worker.finished.connect(self._prefetch_thread.quit)
        self._prefetch_thread.start()

    def _on_prefetch_frame(self, cs_key: int, pixmap: QPixmap) -> None:
        if cs_key not in self._cache:
            self._cache[cs_key] = pixmap
            if len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)

    def _cancel_prefetch(self) -> None:
        if self._prefetch_worker:
            self._prefetch_worker.cancel()
        if self._prefetch_thread and self._prefetch_thread.isRunning():
            self._prefetch_thread.quit()
            self._prefetch_thread.wait(2000)
        self._prefetch_worker = None
        self._prefetch_thread = None

    def shutdown(self):
        self._cancel_prefetch()
        self._cancel_editing()
        self._cache.clear()
