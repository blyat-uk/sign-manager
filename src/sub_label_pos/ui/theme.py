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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QColor, QPainter
from PyQt6.QtWidgets import QPushButton
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
