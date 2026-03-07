from __future__ import annotations

import struct
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QFontMetricsF, QPen, QRawFont
from PyQt6.QtWidgets import (
    QWidget,
    QScrollArea,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
)

from ass_parser import AssFile, AssStyle, LabelDialogue, parse_rich_text
from video_widget import extract_frame, extract_frame_as_image


@dataclass
class LabelGroup:
    labels: list[LabelDialogue] = field(default_factory=list)
    representative_time: float = 0.0


def compute_label_groups(labels: list[LabelDialogue]) -> list[LabelGroup]:
    """Group labels whose time intervals overlap (connected components via interval merge)."""
    if not labels:
        return []

    sorted_labels = sorted(labels, key=lambda lb: lb.start_time)

    groups: list[list[LabelDialogue]] = []
    cur_group = [sorted_labels[0]]
    max_end = sorted_labels[0].end_time

    for lb in sorted_labels[1:]:
        if lb.start_time <= max_end:
            cur_group.append(lb)
            max_end = max(max_end, lb.end_time)
        else:
            groups.append(cur_group)
            cur_group = [lb]
            max_end = lb.end_time
    groups.append(cur_group)

    result: list[LabelGroup] = []
    for group_labels in groups:
        rep_time = _best_representative_time(group_labels)
        result.append(LabelGroup(labels=group_labels, representative_time=rep_time))
    return result


def _best_representative_time(labels: list[LabelDialogue]) -> float:
    """Pick the midpoint time that maximizes simultaneous visible labels."""
    best_time = (labels[0].start_time + labels[0].end_time) / 2
    best_count = 0
    for lb in labels:
        t = (lb.start_time + lb.end_time) / 2
        count = sum(1 for other in labels if other.start_time <= t <= other.end_time)
        if count > best_count:
            best_count = count
            best_time = t
    return best_time


_THUMB_W = 160
_THUMB_IMG_H = 100
_GALLERY_H = 160


def _libass_font_correction(font: QFont) -> float:
    """Correction factor so Qt's setPixelSize matches libass rendering."""
    raw = QRawFont.fromFont(font)
    os2 = raw.fontTable(b"OS/2")
    if len(os2) < 78:
        return 1.0
    upm = raw.unitsPerEm()
    win_asc = struct.unpack_from(">H", os2, 74)[0]
    win_desc = struct.unpack_from(">H", os2, 76)[0]
    cell = win_asc + win_desc
    return upm / cell if cell > 0 else 1.0


def _format_time(seconds: float) -> str:
    """Format seconds as MM:ss.uuu."""
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


def _get_font_correction_for(font_name: str, bold: bool, italic: bool,
                              font_corrections: dict[tuple[str, bool, bool], float]) -> float:
    key = (font_name, bold, italic)
    if key not in font_corrections:
        probe = QFont(font_name)
        probe.setPixelSize(96)
        probe.setBold(bold)
        probe.setItalic(italic)
        font_corrections[key] = _libass_font_correction(probe)
    return font_corrections[key]


def _crop_to_labels_image(
    frame: QImage,
    group: LabelGroup,
    play_res_x: int,
    play_res_y: int,
    label_font_name: str,
    label_font_size: int,
    label_alignment: int,
    label_bold: bool = False,
    label_italic: bool = False,
    font_correction: float = 1.0,
    styles: dict[str, AssStyle] | None = None,
    font_corrections: dict[tuple[str, bool, bool], float] | None = None,
) -> QImage:
    """Crop a QImage around the label bounding box (thread-safe)."""
    frame_w = frame.width()
    frame_h = frame.height()

    scale_x = frame_w / play_res_x
    scale_y = frame_h / play_res_y

    if font_corrections is None:
        font_corrections = {}

    rects: list[QRectF] = []
    for lb in group.labels:
        # Determine style for this label
        if styles and lb.style_name in styles:
            st = styles[lb.style_name]
            fn = st.font_name
            fs = lb.font_size if lb.font_size is not None else st.font_size
            bd = lb.bold if lb.bold is not None else st.bold
            it = lb.italic if lb.italic is not None else st.italic
            al = lb.alignment if lb.alignment is not None else st.alignment
        else:
            fn = label_font_name
            fs = lb.font_size if lb.font_size is not None else label_font_size
            bd = lb.bold if lb.bold is not None else label_bold
            it = lb.italic if lb.italic is not None else label_italic
            al = lb.alignment if lb.alignment is not None else label_alignment

        corr = _get_font_correction_for(fn, bd, it, font_corrections)
        font_scale = frame_h / play_res_y * corr
        lb_pixel = max(int(fs * font_scale), 8)
        lb_font = QFont(fn)
        lb_font.setPixelSize(lb_pixel)
        lb_font.setBold(bd)
        lb_font.setItalic(it)
        lb_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        lb_fm = QFontMetricsF(lb_font)

        px = lb.pos_x * scale_x
        py = lb.pos_y * scale_y

        lines = lb.text.split("\\N") if "\\N" in lb.text else [lb.text]
        text_w = max(lb_fm.horizontalAdvance(line) for line in lines)
        text_h = lb_fm.height() * len(lines)
        pad_x, pad_y = 6, 4
        total_w = text_w + 2 * pad_x
        total_h = text_h + 2 * pad_y

        h_align = ((al - 1) % 3) + 1
        v_group = (al - 1) // 3

        if h_align == 1:
            x = px
        elif h_align == 3:
            x = px - total_w
        else:
            x = px - total_w / 2

        if v_group == 0:
            y = py - total_h
        elif v_group == 1:
            y = py - total_h / 2
        else:
            y = py

        rects.append(QRectF(x, y, total_w, total_h))

    if not rects:
        return frame

    union = rects[0]
    for r in rects[1:]:
        union = union.united(r)

    pad = max(union.width() * 0.15, union.height() * 0.15, 40)
    union = union.adjusted(-pad, -pad, pad, pad)

    x1 = max(0, int(union.left()))
    y1 = max(0, int(union.top()))
    x2 = min(frame_w, int(union.right()))
    y2 = min(frame_h, int(union.bottom()))

    if x2 <= x1 or y2 <= y1:
        return frame

    cropped = frame.copy(x1, y1, x2 - x1, y2 - y1)

    painter = QPainter(cropped)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for r in rects:
        local = QRectF(r.x() - x1, r.y() - y1, r.width(), r.height())
        painter.fillRect(local, QColor(0, 120, 255, 60))
        painter.setPen(QColor(0, 120, 255, 160))
        painter.drawRect(local)
    painter.end()

    return cropped


class ThumbnailWorker(QObject):
    """Generates all gallery thumbnails on a background thread."""
    thumbnail_ready = pyqtSignal(int, QImage)  # index, image
    finished = pyqtSignal()

    def __init__(
        self,
        video_path: str,
        groups: list[LabelGroup],
        play_res_x: int,
        play_res_y: int,
        label_font_name: str,
        label_font_size: int,
        label_alignment: int,
        label_bold: bool = False,
        label_italic: bool = False,
        font_correction: float = 1.0,
        styles: dict[str, AssStyle] | None = None,
        font_corrections: dict[tuple[str, bool, bool], float] | None = None,
    ):
        super().__init__()
        self._video_path = video_path
        self._groups = groups
        self._play_res_x = play_res_x
        self._play_res_y = play_res_y
        self._label_font_name = label_font_name
        self._label_font_size = label_font_size
        self._label_alignment = label_alignment
        self._label_bold = label_bold
        self._label_italic = label_italic
        self._font_correction = font_correction
        self._styles = styles
        self._font_corrections = font_corrections if font_corrections is not None else {}
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        for i, group in enumerate(self._groups):
            if self._cancelled:
                break
            img = extract_frame_as_image(self._video_path, group.representative_time)
            if img and not img.isNull():
                cropped = _crop_to_labels_image(
                    img, group,
                    self._play_res_x, self._play_res_y,
                    self._label_font_name, self._label_font_size,
                    self._label_alignment,
                    self._label_bold, self._label_italic,
                    self._font_correction,
                    styles=self._styles,
                    font_corrections=self._font_corrections,
                )
                self.thumbnail_ready.emit(i, cropped)
        self.finished.emit()


class SingleThumbnailWorker(QObject):
    """Generates a single thumbnail on a background thread."""
    thumbnail_ready = pyqtSignal(int, QImage)  # index, image
    finished = pyqtSignal()

    def __init__(
        self,
        video_path: str,
        index: int,
        representative_time: float,
        group: LabelGroup,
        play_res_x: int,
        play_res_y: int,
        label_font_name: str,
        label_font_size: int,
        label_alignment: int,
        label_bold: bool = False,
        label_italic: bool = False,
        font_correction: float = 1.0,
        styles: dict[str, AssStyle] | None = None,
        font_corrections: dict[tuple[str, bool, bool], float] | None = None,
    ):
        super().__init__()
        self._video_path = video_path
        self._index = index
        self._representative_time = representative_time
        self._group = group
        self._play_res_x = play_res_x
        self._play_res_y = play_res_y
        self._label_font_name = label_font_name
        self._label_font_size = label_font_size
        self._label_alignment = label_alignment
        self._label_bold = label_bold
        self._label_italic = label_italic
        self._font_correction = font_correction
        self._styles = styles
        self._font_corrections = font_corrections if font_corrections is not None else {}
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            self.finished.emit()
            return
        img = extract_frame_as_image(self._video_path, self._representative_time)
        if img and not img.isNull() and not self._cancelled:
            cropped = _crop_to_labels_image(
                img, self._group,
                self._play_res_x, self._play_res_y,
                self._label_font_name, self._label_font_size,
                self._label_alignment,
                self._label_bold, self._label_italic,
                self._font_correction,
                styles=self._styles,
                font_corrections=self._font_corrections,
            )
            self.thumbnail_ready.emit(self._index, cropped)
        self.finished.emit()


class GalleryThumbnail(QWidget):
    clicked = pyqtSignal(int)
    right_clicked = pyqtSignal(int)

    def __init__(self, index: int, label_texts: list[str], time_str: str, parent=None):
        super().__init__(parent)
        self._index = index
        self._selected = False

        self.setFixedWidth(_THUMB_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(_THUMB_W - 8, _THUMB_IMG_H)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(
            "background: #2a2a2a; color: #888; font-size: 11px;"
        )
        self._image_label.setText("Loading...")
        layout.addWidget(self._image_label)

        combined = ", ".join(label_texts)
        if len(combined) > 24:
            combined = combined[:22] + "..."
        self._text_label = QLabel(combined)
        self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_label.setStyleSheet("color: #ccc; font-size: 10px;")
        self._text_label.setWordWrap(False)
        layout.addWidget(self._text_label)

        self._time_label = QLabel(time_str)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setStyleSheet("color: #888; font-size: 9px;")
        layout.addWidget(self._time_label)

    def update_texts(self, label_texts: list[str]) -> None:
        combined = ", ".join(label_texts)
        if len(combined) > 24:
            combined = combined[:22] + "..."
        self._text_label.setText(combined)

    def set_pixmap(self, pixmap: QPixmap):
        scaled = pixmap.scaled(
            _THUMB_W - 8,
            _THUMB_IMG_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def paintEvent(self, event):
        if self._selected:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#4a9eff"), 2)
            painter.setPen(pen)
            painter.setBrush(QColor(26, 26, 46, 80))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)
            painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(self._index)
            event.accept()
        else:
            super().mousePressEvent(event)


class GalleryPanel(QWidget):
    group_selected = pyqtSignal(int)
    group_right_clicked = pyqtSignal(int)  # index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_GALLERY_H)
        self.setStyleSheet("background: #252525;")

        self._thumbnails: list[GalleryThumbnail] = []
        self._groups: list[LabelGroup] = []
        self._video_path: str | None = None
        self._ass: AssFile | None = None
        self._font_correction: float = 1.0
        self._selected_index = -1

        # Bulk thumbnail loading thread/worker
        self._thumb_thread: QThread | None = None
        self._thumb_worker: ThumbnailWorker | None = None

        # Single thumbnail refresh thread/worker
        self._single_thread: QThread | None = None
        self._single_worker: SingleThumbnailWorker | None = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        outer.addWidget(self._scroll)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._hlayout = QHBoxLayout(self._container)
        self._hlayout.setContentsMargins(4, 0, 4, 0)
        self._hlayout.setSpacing(4)
        self._hlayout.addStretch()
        self._scroll.setWidget(self._container)

    def _compute_font_correction(self):
        """Compute the libass-compatible font size correction for the Label style."""
        if not self._ass:
            self._font_correction = 1.0
            return
        probe = QFont(self._ass.label_font_name)
        probe.setPixelSize(96)
        probe.setBold(self._ass.label_bold)
        probe.setItalic(self._ass.label_italic)
        self._font_correction = _libass_font_correction(probe)
        # Also populate per-font corrections for all styles
        self._font_corrections: dict[tuple[str, bool, bool], float] = {}
        if self._ass:
            for st in self._ass.styles.values():
                _get_font_correction_for(st.font_name, st.bold, st.italic, self._font_corrections)

    def set_data(self, video_path: str, ass: AssFile):
        self.clear()
        self._video_path = video_path
        self._ass = ass
        self._compute_font_correction()
        self._groups = compute_label_groups(ass.labels)

        for i, group in enumerate(self._groups):
            texts = [lb.text for lb in group.labels]
            earliest = min(lb.start_time for lb in group.labels)
            thumb = GalleryThumbnail(i, texts, _format_time(earliest))
            thumb.clicked.connect(self._on_thumb_clicked)
            thumb.right_clicked.connect(self._on_thumb_right_clicked)
            self._thumbnails.append(thumb)
            self._hlayout.insertWidget(self._hlayout.count() - 1, thumb)

        if self._groups:
            self._start_thumbnail_loading()

    def set_data_preloaded(self, video_path: str, ass: AssFile,
                           groups: list[LabelGroup], thumbnails: dict[int, QImage]):
        """Apply pre-computed groups and thumbnails without starting a ThumbnailWorker."""
        self.clear()
        self._video_path = video_path
        self._ass = ass
        self._compute_font_correction()
        self._groups = groups

        for i, group in enumerate(groups):
            texts = [lb.text for lb in group.labels]
            earliest = min(lb.start_time for lb in group.labels)
            thumb = GalleryThumbnail(i, texts, _format_time(earliest))
            thumb.clicked.connect(self._on_thumb_clicked)
            thumb.right_clicked.connect(self._on_thumb_right_clicked)
            self._thumbnails.append(thumb)
            self._hlayout.insertWidget(self._hlayout.count() - 1, thumb)
            if i in thumbnails:
                pm = QPixmap.fromImage(thumbnails[i])
                thumb.set_pixmap(pm)

    def _on_thumb_clicked(self, index: int):
        self.select_group(index)
        self.group_selected.emit(index)

    def _on_thumb_right_clicked(self, index: int):
        self.group_right_clicked.emit(index)

    def _start_thumbnail_loading(self):
        if not self._ass or not self._video_path:
            return
        self._thumb_worker = ThumbnailWorker(
            self._video_path,
            self._groups,
            self._ass.play_res_x,
            self._ass.play_res_y,
            self._ass.label_font_name,
            self._ass.label_font_size,
            self._ass.label_alignment,
            self._ass.label_bold,
            self._ass.label_italic,
            self._font_correction,
            styles=dict(self._ass.styles),
            font_corrections=dict(getattr(self, '_font_corrections', {})),
        )
        self._thumb_thread = QThread()
        self._thumb_worker.moveToThread(self._thumb_thread)
        self._thumb_thread.started.connect(self._thumb_worker.run)
        self._thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumb_worker.finished.connect(self._thumb_thread.quit)
        self._thumb_thread.start()

    def _on_thumbnail_ready(self, index: int, image: QImage):
        if 0 <= index < len(self._thumbnails):
            pm = QPixmap.fromImage(image)
            self._thumbnails[index].set_pixmap(pm)

    def _cancel_loading(self):
        if self._thumb_worker:
            self._thumb_worker.cancel()
        if self._thumb_thread and self._thumb_thread.isRunning():
            self._thumb_thread.quit()
            self._thumb_thread.wait(2000)
        self._thumb_worker = None
        self._thumb_thread = None

    def refresh_thumbnail(self, index: int) -> None:
        """Re-extract frame and re-crop for a single group asynchronously."""
        if not (0 <= index < len(self._groups)) or not self._video_path or not self._ass:
            return
        group = self._groups[index]
        thumb = self._thumbnails[index]
        thumb.update_texts([lb.text for lb in group.labels])
        # Cancel any previous single-thumbnail refresh
        self._cancel_single_refresh()
        self._single_worker = SingleThumbnailWorker(
            self._video_path,
            index,
            group.representative_time,
            group,
            self._ass.play_res_x,
            self._ass.play_res_y,
            self._ass.label_font_name,
            self._ass.label_font_size,
            self._ass.label_alignment,
            self._ass.label_bold,
            self._ass.label_italic,
            self._font_correction,
            styles=dict(self._ass.styles),
            font_corrections=dict(getattr(self, '_font_corrections', {})),
        )
        self._single_thread = QThread()
        self._single_worker.moveToThread(self._single_thread)
        self._single_thread.started.connect(self._single_worker.run)
        self._single_worker.thumbnail_ready.connect(self._on_single_thumbnail_ready)
        self._single_worker.finished.connect(self._single_thread.quit)
        self._single_thread.start()

    def _on_single_thumbnail_ready(self, index: int, image: QImage):
        if 0 <= index < len(self._thumbnails):
            pm = QPixmap.fromImage(image)
            self._thumbnails[index].set_pixmap(pm)

    def _cancel_single_refresh(self):
        if self._single_worker:
            self._single_worker.cancel()
        if self._single_thread and self._single_thread.isRunning():
            self._single_thread.quit()
            self._single_thread.wait(2000)
        self._single_worker = None
        self._single_thread = None

    def remove_group(self, index: int) -> None:
        """Remove a group and its thumbnail widget. Adjusts indices."""
        if not (0 <= index < len(self._groups)):
            return
        self._groups.pop(index)
        thumb = self._thumbnails.pop(index)
        self._hlayout.removeWidget(thumb)
        thumb.deleteLater()
        # Re-index remaining thumbnails so clicked signals emit correct index
        for i, t in enumerate(self._thumbnails):
            t._index = i
        # Fix selection
        if self._selected_index == index:
            self._selected_index = -1
        elif self._selected_index > index:
            self._selected_index -= 1

    def select_group(self, index: int):
        if self._selected_index >= 0 and self._selected_index < len(self._thumbnails):
            self._thumbnails[self._selected_index].set_selected(False)
        self._selected_index = index
        if 0 <= index < len(self._thumbnails):
            thumb = self._thumbnails[index]
            thumb.set_selected(True)
            self._scroll.ensureWidgetVisible(thumb, 50, 0)

    def clear(self):
        self._cancel_loading()
        self._cancel_single_refresh()
        self._selected_index = -1
        self._groups = []
        for thumb in self._thumbnails:
            self._hlayout.removeWidget(thumb)
            thumb.deleteLater()
        self._thumbnails.clear()

    @property
    def groups(self) -> list[LabelGroup]:
        return self._groups
