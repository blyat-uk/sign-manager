"""Settings dialog. Absorbs the toolbar's HW Decode / High Quality knobs and
the old menu bar's actions (Re-detect Hardware, Show Gallery)."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QCheckBox, QComboBox, QPushButton, QLabel, QDialogButtonBox,
)

from sub_label_pos.services.app_settings import (
    AppSettings, TIER_LABELS, TIER_ORDER, apply_tier, save,
)
from sub_label_pos.ui import theme


_HWDEC_OPTIONS = [
    ("Auto (safe)", "auto-safe"),
    ("Auto (copy-back)", "auto-copy"),
    ("Software", "no"),
    ("VAAPI", "vaapi"),
    ("VAAPI (copy)", "vaapi-copy"),
    ("NVDEC", "nvdec"),
    ("NVDEC (copy)", "nvdec-copy"),
]


class SettingsDialog(QDialog):
    """Modal settings dialog."""

    hardware_redetect_requested = pyqtSignal()
    # Emitted with the chosen tier ("low"/"medium"/"high") when the user
    # picks a different tier and clicks OK. Caller persists + may prompt
    # the user to restart for cache/worker settings to take effect.
    tier_overridden = pyqtSignal(str)

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(440)
        self._settings = settings
        self._initial_tier = settings.hardware_tier
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(theme.Tokens.sp_4)

        # --- Display ---
        display_box = QGroupBox("Display")
        display_form = QFormLayout(display_box)
        self._gallery_cb = QCheckBox("Show gallery")
        self._gallery_cb.setChecked(self._settings.display.gallery_visible)
        self._sidebar_cb = QCheckBox("Show file sidebar (folder mode)")
        self._sidebar_cb.setChecked(self._settings.display.sidebar_visible)
        self._labels_sidebar_cb = QCheckBox("Show labels list (right)")
        self._labels_sidebar_cb.setChecked(self._settings.display.labels_sidebar_visible)
        display_form.addRow(self._gallery_cb)
        display_form.addRow(self._sidebar_cb)
        display_form.addRow(self._labels_sidebar_cb)
        layout.addWidget(display_box)

        # --- Playback ---
        playback_box = QGroupBox("Playback (mpv)")
        playback_form = QFormLayout(playback_box)
        self._hwdec_combo = QComboBox()
        for label, value in _HWDEC_OPTIONS:
            self._hwdec_combo.addItem(label, value)
        self._hq_cb = QCheckBox("High Quality (spline36 scaling, debanding)")
        self._hq_cb.setChecked(self._settings.perf.mpv_quality == "high")
        playback_form.addRow("HW Decode:", self._hwdec_combo)
        playback_form.addRow(self._hq_cb)
        layout.addWidget(playback_box)

        # --- Hardware ---
        hw_box = QGroupBox("Hardware profile")
        hw_layout = QVBoxLayout(hw_box)

        # Tier selector row: combo + re-detect button
        tier_row = QHBoxLayout()
        tier_row.setSpacing(8)
        tier_row.addWidget(QLabel("Profile:"))
        self._tier_combo = QComboBox()
        for tier in TIER_ORDER:
            self._tier_combo.addItem(TIER_LABELS[tier], tier)
        idx = TIER_ORDER.index(self._settings.hardware_tier) \
            if self._settings.hardware_tier in TIER_ORDER else 1  # default Balanced
        self._tier_combo.setCurrentIndex(idx)
        tier_row.addWidget(self._tier_combo, 1)
        redetect_btn = QPushButton("Auto-detect")
        redetect_btn.setToolTip(
            "Re-run hardware detection and reset the profile to the recommended tier."
        )
        redetect_btn.clicked.connect(self.hardware_redetect_requested.emit)
        tier_row.addWidget(redetect_btn)
        hw_layout.addLayout(tier_row)

        # Detected hardware info (read-only)
        detected_label = QLabel(
            f"Detected: <b>{self._settings.detected_ram_gb:.1f} GB</b> RAM · "
            f"<b>{self._settings.detected_cpu_cores}</b> cores · "
            f"auto-tier <b>{TIER_LABELS.get(self._settings.hardware_tier, self._settings.hardware_tier)}</b>"
        )
        detected_label.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 11px;"
        )
        hw_layout.addWidget(detected_label)

        # Footnote: tier changes need restart for cache/worker counts
        hint = QLabel(
            "Profile changes affect thumbnail size, cache size, and worker "
            "count. Restart the app for the new values to take effect."
        )
        hint.setStyleSheet(
            f"color: {theme.Tokens.text_muted}; font-size: 10.5px; "
            f"font-style: italic;"
        )
        hint.setWordWrap(True)
        hw_layout.addWidget(hint)

        layout.addWidget(hw_box)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_initial_hwdec(self, value: str) -> None:
        """Caller sets the current hwdec mode (from QSettings)."""
        idx = self._hwdec_combo.findData(value)
        if idx >= 0:
            self._hwdec_combo.setCurrentIndex(idx)

    def selected_hwdec(self) -> str:
        return self._hwdec_combo.currentData()

    def selected_tier(self) -> str:
        return self._tier_combo.currentData()

    def _on_accept(self):
        # Mutate the settings object the caller passed in, then persist.
        self._settings.display.gallery_visible = self._gallery_cb.isChecked()
        self._settings.display.sidebar_visible = self._sidebar_cb.isChecked()
        self._settings.display.labels_sidebar_visible = self._labels_sidebar_cb.isChecked()

        new_tier = self.selected_tier()
        tier_changed = new_tier != self._initial_tier
        if tier_changed:
            # Apply the tier's defaults (overwrites perf). User-chosen HQ
            # below is then re-stamped on top so it survives the tier swap.
            apply_tier(self._settings, new_tier)

        self._settings.perf.mpv_quality = "high" if self._hq_cb.isChecked() else "low"

        # Full save (display + perf + hardware_tier together) — a per-section
        # save_perf would round-trip through disk and lose the just-changed tier.
        save(self._settings)

        if tier_changed:
            self.tier_overridden.emit(new_tier)

        self.accept()
