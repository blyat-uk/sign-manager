from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

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

from sub_label_pos.geometry.rich_text import parse_rich_text
from sub_label_pos.model.ass_file import AssFile, AssStyle, LabelDialogue
from sub_label_pos.model.groups import DerivedGroupModel
from sub_label_pos.model.groups import LabelGroup as ModelLabelGroup
from sub_label_pos.model.label_store import LabelStore
from sub_label_pos.services.exceptions import VideoServiceError
from sub_label_pos.services.video_service import VideoService

log = logging.getLogger(__name__)


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
        video_service: VideoService,
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
        thumb_max_dim: int = 720,
        thumb_jpeg_quality: int = 6,
    ):
        super().__init__()
        self._svc = video_service
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
        self._thumb_max_dim = thumb_max_dim
        self._thumb_jpeg_quality = thumb_jpeg_quality
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        p = Path(self._video_path)
        for i, group in enumerate(self._groups):
            if self._cancelled:
                break
            try:
                img = self._svc.get_frame(
                    p,
                    group.representative_time,
                    max_dim=self._thumb_max_dim,
                    jpeg_quality=self._thumb_jpeg_quality,
                )
            except VideoServiceError as e:
                log.warning("thumbnail get_frame failed for %s @ %s: %s",
                            self._video_path, group.representative_time, e)
                continue
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
        video_service: VideoService,
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
        thumb_max_dim: int = 720,
        thumb_jpeg_quality: int = 6,
    ):
        super().__init__()
        self._svc = video_service
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
        self._thumb_max_dim = thumb_max_dim
        self._thumb_jpeg_quality = thumb_jpeg_quality
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            self.finished.emit()
            return
        try:
            img = self._svc.get_frame(
                Path(self._video_path),
                self._representative_time,
                max_dim=self._thumb_max_dim,
                jpeg_quality=self._thumb_jpeg_quality,
            )
        except VideoServiceError as e:
            log.warning("single-thumb get_frame failed for %s @ %s: %s",
                        self._video_path, self._representative_time, e)
            self.finished.emit()
            return
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

    def __init__(
        self,
        store: LabelStore,
        groups: DerivedGroupModel,
        video_service: VideoService,
        parent=None,
        *,
        thumb_max_dim: int = 720,
        thumb_jpeg_quality: int = 6,
    ):
        super().__init__(parent)
        self.setFixedHeight(_GALLERY_H)
        self.setStyleSheet("background: #252525;")

        self._store = store
        self._groups_model = groups
        self._video_service = video_service
        self._thumb_max_dim = thumb_max_dim
        self._thumb_jpeg_quality = thumb_jpeg_quality
        self._thumbnails: list[GalleryThumbnail] = []
        # Legacy LabelGroup adapter list (built from model groups). One per
        # current thumbnail; mirrors model_groups index-for-index.
        self._groups: list[LabelGroup] = []
        # Optional cache of preloaded QImage thumbnails to skip worker rebuilds.
        self._preloaded_thumbnails: dict[str, QImage] = {}  # keyed by group_id
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

        # Subscribe to store + groups model signals
        self._store.file_loaded.connect(self._on_file_loaded)
        self._groups_model.groups_changed.connect(self._on_groups_changed)
        self._store.labels_mutated.connect(self._on_labels_mutated)

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

    def attach_ass(self, ass: AssFile | None) -> None:
        """Inform the gallery of the active AssFile (for thumbnail rendering).

        Required because ``LabelStore.state`` keeps ``styles`` but the gallery
        also needs ``play_res_x/y`` and label-style defaults that live on
        :class:`AssFile`. Until ``AssFile`` is fully retired (post L1), the
        gallery still reads these fields directly.
        """
        self._ass = ass
        self._compute_font_correction()

    def attach_video(self, video_path: str | None) -> None:
        """Tell the gallery which video file thumbnails should render from.

        The store's ``source_path`` is the .ass path; the video path is a
        separate concern owned by MainWindow until K2 extracts FileLoader.
        """
        self._video_path = video_path

    def cache_preloaded_thumbnails(
        self, ass: AssFile, model_groups: list[ModelLabelGroup], thumbnails: dict[int, QImage]
    ) -> None:
        """Pre-seed the per-group thumbnail cache (used by folder-preload).

        Keys are :class:`ModelLabelGroup` ``group_id``s; the gallery applies
        them when ``groups_changed`` fires after ``LabelStore.load``.
        """
        self.attach_ass(ass)
        # Map preloaded index-based thumbnails to group_id-based cache.
        for i, mg in enumerate(model_groups):
            if i in thumbnails:
                self._preloaded_thumbnails[mg.group_id] = thumbnails[i]

    # --- Signal handlers ------------------------------------------------

    def _on_file_loaded(self, _path) -> None:
        """Clear per-file UI state. ``groups_changed`` fires synchronously
        from inside this same ``file_loaded`` emission (DerivedGroupModel is
        connected first), so the actual rebuild + worker spawn happens in
        ``_on_groups_changed``.

        IMPORTANT: do NOT call ``_cancel_loading`` here. DerivedGroupModel is
        connected to ``file_loaded`` before us, so by the time we run, a new
        worker has already been spawned by the groups_changed cascade.
        Cancelling it here breaks first-load thumbnails. Worker cancellation
        for the prior file happens at the start of ``_on_groups_changed``.

        Note: ``_path`` here is the store's source_path (the .ass file). The
        video path is set separately via :meth:`attach_video`.
        """
        self._selected_index = -1
        # Clear preloaded thumbnail cache from the previous file.
        self._preloaded_thumbnails.clear()

    def _on_groups_changed(self, _changed_ids) -> None:
        """Full rebuild of the thumbnail list from the current model groups."""
        # Tear down existing thumbnails (without nuking preloaded cache).
        self._cancel_loading()
        self._cancel_single_refresh()
        prev_selected = self._selected_index
        self._selected_index = -1
        for thumb in self._thumbnails:
            self._hlayout.removeWidget(thumb)
            thumb.deleteLater()
        self._thumbnails.clear()

        # Build legacy LabelGroup adapters from current model groups.
        self._groups = [self._to_legacy_group(mg) for mg in self._groups_model.groups]

        # Capture video_path lazily from store if not set via file_loaded yet.
        if self._video_path is None and self._store.source_path is not None:
            # source_path on the store is the .ass path; video_path is set by
            # MainWindow via _on_file_loaded which fires with the source path.
            # Fall back: store source_path lets us populate when no video.
            pass  # _video_path stays None until file_loaded fires explicitly

        applied_indices: set[int] = set()
        for i, lg in enumerate(self._groups):
            texts = [lb.text for lb in lg.labels]
            earliest = min((lb.start_time for lb in lg.labels), default=0.0)
            thumb = GalleryThumbnail(i, texts, _format_time(earliest))
            thumb.clicked.connect(self._on_thumb_clicked)
            thumb.right_clicked.connect(self._on_thumb_right_clicked)
            self._thumbnails.append(thumb)
            self._hlayout.insertWidget(self._hlayout.count() - 1, thumb)

            # Apply preloaded cached thumbnail if available.
            mg = self._groups_model.groups[i]
            cached = self._preloaded_thumbnails.get(mg.group_id)
            if cached is not None and not cached.isNull():
                thumb.set_pixmap(QPixmap.fromImage(cached))
                applied_indices.add(i)

        # Spawn worker for thumbnails not covered by preload cache.
        if self._groups and self._video_path and self._ass:
            self._start_thumbnail_loading(skip_indices=applied_indices)

        # Restore selection visual if index still in range.
        if 0 <= prev_selected < len(self._thumbnails):
            self.select_group(prev_selected)

    def _on_labels_mutated(self, affected_ids) -> None:
        """Refresh thumbnails for any group containing an affected label_id.

        For a single affected group we fire the lightweight single-thumbnail
        worker; for multiple groups we kick off a fresh full bulk worker
        (which is preferable to chaining single workers and cancelling each
        other).
        """
        if not affected_ids or not self._video_path or not self._ass:
            return
        affected = set(affected_ids)
        impacted: list[int] = []
        for i, mg in enumerate(self._groups_model.groups):
            if any(lid in affected for lid in mg.label_ids):
                # Refresh legacy group adapter so cropping reflects current state.
                if i < len(self._groups):
                    self._groups[i] = self._to_legacy_group(mg)
                # Also update the visible text on the thumbnail synchronously.
                if i < len(self._thumbnails):
                    self._thumbnails[i].update_texts(
                        [lb.text for lb in self._groups[i].labels]
                    )
                impacted.append(i)
        if not impacted:
            return
        if len(impacted) == 1:
            self._refresh_single_thumbnail(impacted[0])
        else:
            # Multiple groups affected: full bulk re-render. Cancels any
            # running bulk worker.
            self._start_thumbnail_loading()

    # --- Internal helpers ----------------------------------------------

    def _to_legacy_group(self, mg: ModelLabelGroup) -> LabelGroup:
        """Build a legacy LabelGroup (with full LabelDialogue list) from a model
        group, looking labels up in store state."""
        state = self._store.state
        labels = [state.labels[lid] for lid in mg.label_ids if lid in state.labels]
        return LabelGroup(labels=labels, representative_time=mg.representative_time)

    def _on_thumb_clicked(self, index: int):
        self.select_group(index)
        self.group_selected.emit(index)

    def _on_thumb_right_clicked(self, index: int):
        self.group_right_clicked.emit(index)

    def _start_thumbnail_loading(self, skip_indices: set[int] | None = None):
        if not self._ass or not self._video_path:
            return
        # Skip the (potentially expensive) bulk ffmpeg + render work when the
        # gallery is hidden. ``showEvent`` re-triggers loading when the panel
        # becomes visible again. Saves CPU/IO on low-tier hardware where the
        # gallery is opt-in (PerfSettings.gallery_enabled defaults to False).
        if not self.isVisible():
            return
        # Filter out groups that already have a cached/applied thumbnail. We
        # still pass the full groups list (so indices match) — the worker just
        # skips entries we mark, but ThumbnailWorker doesn't have a skip hook,
        # so we simply re-render all (cached ones get overwritten with the
        # freshly-rendered identical image). Cheap and avoids special-casing.
        del skip_indices  # reserved for future incremental optimisation
        self._thumb_worker = ThumbnailWorker(
            self._video_service,
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
            thumb_max_dim=self._thumb_max_dim,
            thumb_jpeg_quality=self._thumb_jpeg_quality,
        )
        # Parent thread to self so Qt owns it; PyQt won't garbage-collect
        # while the underlying OS thread is still running. deleteLater on
        # finished cleans both up asynchronously when work completes.
        self._thumb_thread = QThread(self)
        self._thumb_worker.moveToThread(self._thumb_thread)
        self._thumb_thread.started.connect(self._thumb_worker.run)
        self._thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumb_worker.finished.connect(self._thumb_thread.quit)
        self._thumb_worker.finished.connect(self._thumb_worker.deleteLater)
        # Connect ref-clearing slot BEFORE deleteLater so we null our Python
        # references while the C++ object is still alive. Without this, the
        # wrapper would survive deleteLater and the next _cancel_loading call
        # would crash with 'wrapped C/C++ object of type QThread has been
        # deleted'.
        self._thumb_thread.finished.connect(self._on_thumb_thread_finished)
        self._thumb_thread.finished.connect(self._thumb_thread.deleteLater)
        self._thumb_thread.start()

    def _on_thumb_thread_finished(self) -> None:
        """Null bulk-thumbnail refs when the thread finishes naturally.

        Uses sender() identity check so an older thread's late-firing
        finished signal doesn't accidentally null the refs of a newer
        thread that has already replaced it.
        """
        sender = self.sender()
        if self._thumb_thread is sender:
            self._thumb_thread = None
            self._thumb_worker = None

    def _on_thumbnail_ready(self, index: int, image: QImage):
        if 0 <= index < len(self._thumbnails):
            pm = QPixmap.fromImage(image)
            self._thumbnails[index].set_pixmap(pm)

    def _cancel_loading(self):
        """Signal the in-flight worker to stop and drop our references.

        Does NOT wait synchronously: ffmpeg's per-frame subprocess call can
        take longer than the wait timeout, and replacing the QThread Python
        ref before the OS thread finishes triggers a 'Destroyed while
        running' abort. With the thread parented to self and deleteLater on
        finished, the orphaned thread cleans itself up once ffmpeg returns.
        Use :meth:`shutdown` to wait synchronously at app close.
        """
        if self._thumb_worker:
            self._thumb_worker.cancel()
        if self._thumb_thread:
            self._thumb_thread.quit()
        self._thumb_worker = None
        self._thumb_thread = None

    def _refresh_single_thumbnail(self, index: int) -> None:
        """Re-extract frame and re-crop for a single group asynchronously."""
        if not (0 <= index < len(self._groups)) or not self._video_path or not self._ass:
            return
        # Skip work while hidden; ``showEvent`` triggers a bulk rebuild on
        # re-show, which covers any thumbnails that would have been refreshed
        # individually while we were invisible.
        if not self.isVisible():
            return
        group = self._groups[index]
        thumb = self._thumbnails[index]
        thumb.update_texts([lb.text for lb in group.labels])
        # Cancel any previous single-thumbnail refresh
        self._cancel_single_refresh()
        self._single_worker = SingleThumbnailWorker(
            self._video_service,
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
            thumb_max_dim=self._thumb_max_dim,
            thumb_jpeg_quality=self._thumb_jpeg_quality,
        )
        self._single_thread = QThread(self)
        self._single_worker.moveToThread(self._single_thread)
        self._single_thread.started.connect(self._single_worker.run)
        self._single_worker.thumbnail_ready.connect(self._on_single_thumbnail_ready)
        self._single_worker.finished.connect(self._single_thread.quit)
        self._single_worker.finished.connect(self._single_worker.deleteLater)
        # See _on_thumb_thread_finished for rationale.
        self._single_thread.finished.connect(self._on_single_thread_finished)
        self._single_thread.finished.connect(self._single_thread.deleteLater)
        self._single_thread.start()

    def _on_single_thread_finished(self) -> None:
        """Null single-thumbnail refs when the thread finishes naturally.
        See :meth:`_on_thumb_thread_finished` for rationale."""
        sender = self.sender()
        if self._single_thread is sender:
            self._single_thread = None
            self._single_worker = None

    def _on_single_thumbnail_ready(self, index: int, image: QImage):
        if 0 <= index < len(self._thumbnails):
            pm = QPixmap.fromImage(image)
            self._thumbnails[index].set_pixmap(pm)

    def _cancel_single_refresh(self):
        """Signal the in-flight worker to stop and drop our references.
        See _cancel_loading for the rationale. Use :meth:`shutdown` at app
        close to wait for completion."""
        if self._single_worker:
            self._single_worker.cancel()
        if self._single_thread:
            self._single_thread.quit()
        self._single_worker = None
        self._single_thread = None

    def shutdown(self, wait_ms: int = 5000) -> None:
        """Wait synchronously for all in-flight thumbnail threads.

        Must be called before the gallery widget is destroyed (e.g. from
        MainWindow.closeEvent). Any thread still running when the widget is
        torn down causes a 'QThread: Destroyed while running' abort.
        """
        self._cancel_loading()
        self._cancel_single_refresh()
        for thread in self.findChildren(QThread):
            if thread.isRunning():
                thread.quit()
                thread.wait(wait_ms)

    def showEvent(self, event):  # type: ignore[override]
        """Trigger a one-time bulk thumbnail rebuild when the panel becomes
        visible after being hidden.

        While hidden, ``_start_thumbnail_loading`` and
        ``_refresh_single_thumbnail`` no-op, so on re-show we may have stale
        or missing thumbnails. Spawning the bulk worker here brings the
        gallery back in sync with the current store state. The guard on
        ``_thumb_thread`` avoids stacking workers when Qt fires showEvent
        for non-visibility reasons (parent reshow, theme change, etc.).
        """
        super().showEvent(event)
        if (
            self._groups
            and self._video_path
            and self._ass
            and self._thumb_thread is None
        ):
            self._start_thumbnail_loading()

    def select_group(self, index: int):
        if self._selected_index >= 0 and self._selected_index < len(self._thumbnails):
            self._thumbnails[self._selected_index].set_selected(False)
        self._selected_index = index
        if 0 <= index < len(self._thumbnails):
            thumb = self._thumbnails[index]
            thumb.set_selected(True)
            self._scroll.ensureWidgetVisible(thumb, 50, 0)

    @property
    def groups(self) -> list[LabelGroup]:
        return self._groups
