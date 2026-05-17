"""Centralized design tokens, named icon factories, and reusable widget
subclasses for the UI refresh.

This module is the single source of truth for colors, spacing, radii,
typography, and Phosphor icon names. All other UI files MUST import from
here rather than declaring inline colors / sizes / icon names.

Spec: docs/superpowers/specs/2026-05-17-ui-refresh-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QIcon, QColor, QPainter
from PyQt6.QtWidgets import QPushButton, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame
import qtawesome as qta


@dataclass(frozen=True)
class _Tokens:
    # --- Color ---
    bg_deepest: str = "#0f1114"
    bg_base: str = "#14161a"
    bg_surface: str = "#1a1c20"
    bg_raised: str = "#1f2226"
    bg_hover: str = "#2a2d33"
    border: str = "#2d3036"
    border_strong: str = "#3a3d44"
    text_muted: str = "#888888"
    text_primary: str = "#d8d8d8"
    text_emphasis: str = "#ffffff"
    accent: str = "#4a9eff"
    accent_deep: str = "#1f5fa6"
    alert: str = "#ffcc55"
    danger: str = "#ff8a8a"
    success: str = "#8ad08a"
    overlay: str = "rgba(0,0,0,0.85)"

    # --- Spacing (px) ---
    sp_1: int = 4
    sp_2: int = 8
    sp_3: int = 12
    sp_4: int = 16
    sp_5: int = 24
    sp_6: int = 36

    # --- Radii (px) ---
    r_sm: int = 3
    r_md: int = 6
    r_lg: int = 10
    r_pill: int = 13

    # --- Type sizes (px) ---
    text_hero: int = 22
    text_lg: int = 14
    text_base: float = 12.5
    text_mono: int = 11
    text_eyebrow: int = 10


Tokens = _Tokens()


class Icons:
    """Phosphor icon factories. Default color is text-primary."""

    _DEFAULT_COLOR = Tokens.text_primary

    @classmethod
    def _i(cls, name: str, color: str | None = None) -> QIcon:
        return qta.icon(name, color=color or cls._DEFAULT_COLOR)

    # File / save / history
    @classmethod
    def open_folder(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.folder-open", color)

    @classmethod
    def save(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.floppy-disk-bold", color)

    @classmethod
    def undo(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.arrow-counter-clockwise", color)

    @classmethod
    def redo(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.arrow-clockwise", color)

    # Edit actions
    @classmethod
    def duplicate(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.copy", color)

    @classmethod
    def delete(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.trash", color)

    @classmethod
    def copy_style(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.clipboard-text", color)

    @classmethod
    def paste_style(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.clipboard", color)

    @classmethod
    def promote_to_style(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.arrow-fat-line-up", color)

    @classmethod
    def edit_text(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.pencil", color)

    @classmethod
    def sync_times(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.clock-clockwise", color)

    # Formatting
    @classmethod
    def bold(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.text-bolder", color)

    @classmethod
    def italic(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.text-italic", color)

    @classmethod
    def align_left(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.text-align-left", color)

    @classmethod
    def align_center(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.text-align-center", color)

    @classmethod
    def align_right(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.text-align-right", color)

    @classmethod
    def style_tag(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.tag", color)

    # Steppers
    @classmethod
    def minus(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.minus", color)

    @classmethod
    def plus(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.plus", color)

    # Layout
    @classmethod
    def gallery_toggle(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.rows", color)

    @classmethod
    def sidebar_toggle(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.list", color)

    @classmethod
    def settings(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.gear", color)

    @classmethod
    def caret_down(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.caret-down", color)

    # Playback
    @classmethod
    def play(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.play-fill", color)

    @classmethod
    def pause(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.pause-fill", color)

    @classmethod
    def step_back(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.skip-back", color)

    @classmethod
    def step_forward(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.skip-forward", color)

    @classmethod
    def prev_group(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.arrow-line-left", color)

    @classmethod
    def next_group(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.arrow-line-right", color)

    # Status / HUD
    @classmethod
    def time(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.clock", color)

    @classmethod
    def resolution(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.monitor", color)

    @classmethod
    def files(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.files", color)

    @classmethod
    def cpu(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.cpu", color)

    @classmethod
    def folder(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.folder", color)

    @classmethod
    def folder_dashed(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.folder-minus", color)

    @classmethod
    def upload(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.upload-simple", color)

    @classmethod
    def file_video(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.film-strip", color)

    @classmethod
    def magnet(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.magnet-fill", color)

    @classmethod
    def modified_dot(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.circle-fill", color)

    @classmethod
    def group_stack(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.stack-fill", color)

    @classmethod
    def zoom_in(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.magnifying-glass-plus-fill", color)

    @classmethod
    def app_logo(cls, color: str | None = None) -> QIcon:
        return cls._i("ph.text-aa-bold", color)


def format_relative_time(then: datetime | None, *, now: datetime | None = None) -> str:
    """Return short relative-time strings: '5m ago', 'yesterday', '3 wks', '1 mo'.

    `then` and `now` may be timezone-aware or naive; naive datetimes are
    assumed UTC. Returns '' for None input.
    """
    if then is None:
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta = now - then
    total_seconds = max(0, int(delta.total_seconds()))

    if total_seconds < 60 * 60:
        return f"{max(1, total_seconds // 60)}m ago"
    if total_seconds < 24 * 60 * 60:
        return f"{total_seconds // 3600}h ago"
    if total_seconds < 48 * 60 * 60:
        return "yesterday"
    days = total_seconds // (24 * 60 * 60)
    if days < 7:
        return f"{days}d ago"
    if days < 35:
        weeks = days // 7
        return f"{weeks} wk" if weeks == 1 else f"{weeks} wks"
    months = days // 30
    return f"{months} mo"


_ICON_BUTTON_QSS = f"""
QPushButton {{
    background: transparent;
    color: {Tokens.text_primary};
    border: none;
    border-radius: {Tokens.r_md - 1}px;
    padding: 0 {Tokens.sp_2}px;
    font-size: 12px;
}}
QPushButton:hover {{
    background: {Tokens.bg_hover};
    color: {Tokens.text_emphasis};
}}
QPushButton:pressed {{
    background: {Tokens.border};
}}
QPushButton:disabled {{
    color: {Tokens.text_muted};
    opacity: 0.4;
}}
QPushButton:checked {{
    background: {Tokens.accent_deep};
    color: {Tokens.text_emphasis};
}}
"""


class IconButton(QPushButton):
    """Flat icon-or-icon+text button. Hover, pressed, disabled, checked states styled.

    Pass tooltip with shortcut included: e.g. 'Save (Ctrl+S)'.
    """

    def __init__(
        self,
        icon: "QIcon | None" = None,
        text: str = "",
        *,
        tooltip: str = "",
        icon_only: bool = False,
        parent=None,
    ):
        super().__init__(text if not icon_only else "", parent)
        if icon is not None:
            self.setIcon(icon)
            from PyQt6.QtCore import QSize
            self.setIconSize(QSize(18, 18))
        if tooltip:
            self.setToolTip(tooltip)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(_ICON_BUTTON_QSS)
        if icon_only:
            self.setFixedSize(30, 30)
        else:
            self.setFixedHeight(30)


_PRIMARY_BUTTON_QSS = f"""
QPushButton {{
    background: {Tokens.accent_deep};
    color: {Tokens.text_emphasis};
    border: 1px solid {Tokens.accent};
    border-radius: {Tokens.r_md - 1}px;
    padding: 0 {Tokens.sp_3}px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #2a6fb8;
}}
QPushButton:pressed {{
    background: {Tokens.accent_deep};
}}
QPushButton:disabled {{
    background: {Tokens.bg_raised};
    color: {Tokens.text_muted};
    border-color: {Tokens.border};
}}
"""


class PrimaryButton(QPushButton):
    """Accent-blue Save-style button with optional unsaved-dot indicator."""

    def __init__(self, text: str, icon=None, *, tooltip: str = "", parent=None):
        super().__init__(text, parent)
        if icon is not None:
            self.setIcon(icon)
            from PyQt6.QtCore import QSize
            self.setIconSize(QSize(18, 18))
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(_PRIMARY_BUTTON_QSS)
        self.setFixedHeight(30)
        self._unsaved = False

    def set_unsaved(self, unsaved: bool) -> None:
        """Toggle the unsaved-changes dot indicator (drawn in paintEvent)."""
        if self._unsaved == unsaved:
            return
        self._unsaved = unsaved
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._unsaved:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Tokens.alert))
        # 6px dot, 5px from top-right
        x = self.width() - 11
        y = 5
        p.drawEllipse(x, y, 6, 6)
        p.end()


_STEPPER_QSS = f"""
QFrame#stepper {{
    background: {Tokens.bg_surface};
    border: 1px solid {Tokens.border};
    border-radius: {Tokens.r_md - 1}px;
}}
QLabel#stepper-value {{
    color: {Tokens.text_emphasis};
    font-size: 11px;
    font-weight: 600;
    padding: 0 {Tokens.sp_2}px;
    min-width: 18px;
    background: transparent;
}}
"""


class Stepper(QWidget):
    """Minus / value / plus stepper. Emits value_changed(int) on each click."""

    value_changed = pyqtSignal(int)

    def __init__(
        self,
        initial: int,
        *,
        step: int = 1,
        minimum: int | None = None,
        maximum: int | None = None,
        suffix: str = "",
        minus_tooltip: str = "Decrease",
        plus_tooltip: str = "Increase",
        parent=None,
    ):
        super().__init__(parent)
        self._value = initial
        self._step = step
        self._min = minimum
        self._max = maximum
        self._suffix = suffix

        frame = QFrame(self)
        frame.setObjectName("stepper")
        frame.setStyleSheet(_STEPPER_QSS)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self._minus = IconButton(Icons.minus(), tooltip=minus_tooltip, icon_only=True)
        self._minus.setFixedSize(24, 24)
        self._minus.setIconSize(QSize(14, 14))
        self._minus.clicked.connect(self._on_minus)

        self._value_label = QLabel(self._format(initial))
        self._value_label.setObjectName("stepper-value")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._plus = IconButton(Icons.plus(), tooltip=plus_tooltip, icon_only=True)
        self._plus.setFixedSize(24, 24)
        self._plus.setIconSize(QSize(14, 14))
        self._plus.clicked.connect(self._on_plus)

        layout.addWidget(self._minus)
        layout.addWidget(self._value_label)
        layout.addWidget(self._plus)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

    def _format(self, v: int | None) -> str:
        if v is None:
            return "—"
        return f"{v}{self._suffix}"

    def _on_minus(self):
        new = self._value - self._step
        if self._min is not None and new < self._min:
            return
        self._value = new
        self._value_label.setText(self._format(new))
        self.value_changed.emit(new)

    def _on_plus(self):
        new = self._value + self._step
        if self._max is not None and new > self._max:
            return
        self._value = new
        self._value_label.setText(self._format(new))
        self.value_changed.emit(new)

    def set_value(self, v: int | None) -> None:
        """Update displayed value WITHOUT emitting (use for external sync). None = mixed."""
        self._value = v if v is not None else self._value
        self._value_label.setText(self._format(v))


class SegmentedToggle(QWidget):
    """Group of mutually-exclusive `IconButton`s. Each option is (key, icon, tooltip).

    Emits selected(key) when the user picks one. set_selected(key | None) updates
    display without emitting; None clears all (use for multi-select 'mixed' state).
    """

    selected = pyqtSignal(object)  # key

    def __init__(self, options: "list[tuple[object, QIcon, str]]", *, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._btns: dict[object, IconButton] = {}
        for key, icon, tip in options:
            btn = IconButton(icon, tooltip=tip, icon_only=True)
            btn.setCheckable(True)
            btn.setFixedSize(28, 26)
            btn.clicked.connect(lambda _checked, k=key: self._on_clicked(k))
            layout.addWidget(btn)
            self._btns[key] = btn

    def _on_clicked(self, key: object) -> None:
        self.set_selected(key)
        self.selected.emit(key)

    def set_selected(self, key: object | None) -> None:
        for k, btn in self._btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == key)
            btn.blockSignals(False)


class ColorSwatch(QPushButton):
    """22x22 square color swatch with border. set_color(None) shows mixed state."""

    def __init__(self, *, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._color: QColor | None = None
        self.set_color(QColor("#888888"))

    def set_color(self, color: QColor | None) -> None:
        self._color = color
        if color is None:
            self.setStyleSheet(
                f"QPushButton {{ background: {Tokens.border_strong}; "
                f"color: {Tokens.text_primary}; border: 1.5px solid {Tokens.border_strong}; "
                f"border-radius: {Tokens.r_md - 2}px; }}"
                f"QPushButton:hover {{ border-color: {Tokens.text_muted}; }}"
            )
            return
        self.setStyleSheet(
            f"QPushButton {{ background: {color.name()}; "
            f"border: 1.5px solid {Tokens.border_strong}; "
            f"border-radius: {Tokens.r_md - 2}px; }}"
            f"QPushButton:hover {{ border-color: {Tokens.text_emphasis}; }}"
        )

    def color(self) -> QColor | None:
        return self._color


class StatusChip(QWidget):
    """Pill-shaped status bar entry with icon prefix + text. Supports 'alert' variant."""

    def __init__(self, icon=None, text: str = "", *, alert: bool = False, parent=None):
        super().__init__(parent)
        self._icon_label = QLabel()
        self._text_label = QLabel(text)
        self._alert = alert

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(5)
        layout.addWidget(self._icon_label)
        layout.addWidget(self._text_label)

        if icon is not None:
            self._icon_label.setPixmap(icon.pixmap(QSize(12, 12)))

        self.setFixedHeight(26)
        self._apply_style()

    def _apply_style(self):
        bg = Tokens.bg_deepest
        text = Tokens.text_muted if not self._alert else Tokens.alert
        border = Tokens.border if not self._alert else "#5a4622"
        self.setStyleSheet(
            f"StatusChip {{ background: {bg}; border: 1px solid {border}; "
            f"border-radius: {Tokens.r_pill}px; }}"
            f"QLabel {{ color: {text}; font-size: 11px; background: transparent; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_text(self, text: str) -> None:
        self._text_label.setText(text)

    def set_alert(self, alert: bool) -> None:
        if self._alert == alert:
            return
        self._alert = alert
        self._apply_style()


class StyleChip(QPushButton):
    """Left-side chip on the label toolbar: '[tag] Caption ▾'. Click opens style menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(Icons.style_tag(color=Tokens.accent))
        self.setIconSize(QSize(11, 11))
        self.setText("Style ▾")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(26)
        self.setStyleSheet(
            "QPushButton { background: #1a3a5a; color: #b8d4f0; "
            "border: 1px solid #2a5a8a; border-radius: 4px; padding: 0 10px; "
            "font-size: 11.5px; font-weight: 500; text-align: left; }"
            "QPushButton:hover { background: #234a72; color: #fff; }"
        )

    def set_style_name(self, name: str) -> None:
        self.setText(f"{name} ▾")


class LoadingPill(QWidget):
    """Corner overlay shown during slow async work. Pulsing dot + label."""

    def __init__(self, text: str = "Loading frame", *, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._text = QLabel(text)

        layout.addWidget(self._dot)
        layout.addWidget(self._text)

        self.setStyleSheet(
            f"LoadingPill {{ background: rgba(0,0,0,0.75); border: 1px solid #333; "
            f"border-radius: 12px; }}"
            f"QLabel {{ color: {Tokens.text_primary}; font-size: 10.5px; "
            f"background: transparent; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._dot_visible = True
        self._timer = QTimer(self)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._toggle_dot)
        self._update_dot()
        self.hide()

    def _toggle_dot(self):
        self._dot_visible = not self._dot_visible
        self._update_dot()

    def _update_dot(self):
        color = Tokens.accent if self._dot_visible else Tokens.bg_raised
        self._dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def start(self):
        self._timer.start()
        self.show()
        self.adjustSize()

    def stop(self):
        self._timer.stop()
        self.hide()


class DragChip(QWidget):
    """Floating x/y readout shown during an active drag.

    Layout:
      x  960  +12
      y  648  −4
      [snap]  snapped to Title       (only when snapped)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(2)

        self._x_label = QLabel("x  0  +0")
        self._y_label = QLabel("y  0  +0")
        self._snap_label = QLabel("")
        self._snap_label.hide()

        layout.addWidget(self._x_label)
        layout.addWidget(self._y_label)
        layout.addWidget(self._snap_label)

        self.setStyleSheet(
            f"DragChip {{ background: {Tokens.overlay}; border: 1px solid {Tokens.accent}; "
            f"border-radius: 5px; }}"
            f"QLabel {{ color: {Tokens.text_emphasis}; font-size: 10.5px; "
            f"background: transparent; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

    def update_position(self, x: int, y: int, dx: int, dy: int) -> None:
        def fmt(v: int) -> str:
            return f"+{v}" if v >= 0 else f"−{abs(v)}"

        self._x_label.setText(
            f'<span style="color:{Tokens.text_muted};">x</span> '
            f'<span style="color:{Tokens.text_emphasis};">{x}</span> '
            f'<span style="color:{Tokens.alert};font-size:9.5px;">{fmt(dx)}</span>'
        )
        self._y_label.setText(
            f'<span style="color:{Tokens.text_muted};">y</span> '
            f'<span style="color:{Tokens.text_emphasis};">{y}</span> '
            f'<span style="color:{Tokens.alert};font-size:9.5px;">{fmt(dy)}</span>'
        )

    def set_snap_target(self, target_name: str | None) -> None:
        if not target_name:
            self._snap_label.hide()
        else:
            self._snap_label.setText(
                f'<span style="color:{Tokens.alert};">⊙</span> '
                f'<span style="color:{Tokens.alert};">snapped to</span> '
                f'<span style="color:{Tokens.text_emphasis};">{target_name}</span>'
            )
            self._snap_label.show()
        self.adjustSize()


class GroupBadge(QLabel):
    """'N selected' pill anchored to multi-select group bbox."""

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFixedHeight(18)
        self.setStyleSheet(
            f"GroupBadge {{ background: {Tokens.accent}; color: {Tokens.text_emphasis}; "
            f"font-size: 9.5px; font-weight: 700; padding: 2px 7px; border-radius: 8px; "
            f"letter-spacing: 0.3px; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

    def set_count(self, n: int) -> None:
        self.setText(f"❖ {n} selected")
        self.adjustSize()
        self.show()


class SnapLabel(QLabel):
    """Yellow pill anchored to a snap guide line: 'aligned to Title'."""

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setStyleSheet(
            f"SnapLabel {{ background: {Tokens.alert}; color: {Tokens.bg_deepest}; "
            f"font-size: 9.5px; font-weight: 600; padding: 1px 6px; border-radius: 3px; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

    def set_target(self, target_name: str) -> None:
        self.setText(f"aligned to {target_name}")
        self.adjustSize()
        self.show()


class RecentItemWidget(QWidget):
    """Welcome-screen recent-folder row: [icon] name + path  ·  files · time."""

    def __init__(
        self,
        name: str,
        path: str,
        *,
        file_count: int | None = None,
        last_opened: datetime | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # Let mouse events pass through to the parent QListWidget so item
        # clicks/double-clicks register as list activations.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 11, 14, 11)
        outer.setSpacing(12)

        # Icon tile
        icon_label = QLabel()
        icon_label.setFixedSize(32, 32)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(Icons.folder(color=Tokens.accent).pixmap(QSize(18, 18)))
        icon_label.setStyleSheet(
            f"background: {Tokens.bg_raised}; border-radius: {Tokens.r_md}px;"
        )

        # Meta column
        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(1)
        name_label = QLabel(name)
        name_label.setStyleSheet(
            f"color: {Tokens.text_emphasis}; font-size: 13px; font-weight: 600; "
            f"background: transparent;"
        )
        path_label = QLabel(path)
        path_label.setStyleSheet(
            f"color: {Tokens.text_muted}; font-size: 10.5px; "
            f"font-family: ui-monospace, Menlo, Consolas, monospace; background: transparent;"
        )
        meta.addWidget(name_label)
        meta.addWidget(path_label)

        # Stats column
        stats = QHBoxLayout()
        stats.setSpacing(12)
        if file_count is not None:
            files_label = QLabel(f"📁 {file_count}")
            files_label.setStyleSheet(
                f"color: {Tokens.text_muted}; font-size: 10.5px; "
                f"background: transparent; font-variant-numeric: tabular-nums;"
            )
            stats.addWidget(files_label)
        if last_opened is not None:
            time_label = QLabel(f"🕐 {format_relative_time(last_opened)}")
            time_label.setStyleSheet(
                f"color: {Tokens.text_muted}; font-size: 10.5px; "
                f"background: transparent;"
            )
            stats.addWidget(time_label)

        outer.addWidget(icon_label)
        outer.addLayout(meta, 1)
        outer.addLayout(stats)
