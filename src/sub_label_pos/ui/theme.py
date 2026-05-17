"""Centralized design tokens, named icon factories, and reusable widget
subclasses for the UI refresh.

This module is the single source of truth for colors, spacing, radii,
typography, and Phosphor icon names. All other UI files MUST import from
here rather than declaring inline colors / sizes / icon names.

Spec: docs/superpowers/specs/2026-05-17-ui-refresh-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QIcon
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
