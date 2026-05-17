"""Settings dialog. Absorbs the toolbar's HW Decode / High Quality knobs and
the old menu bar's actions (Re-detect Hardware, Show Gallery)."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QCheckBox, QComboBox, QPushButton, QLabel, QDialogButtonBox,
)

from sub_label_pos.services.app_settings import (
    AppSettings, save_perf, save_display,
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
    """Modal settings dialog. Emits hardware_redetect_requested when user clicks Re-detect."""

    hardware_redetect_requested = pyqtSignal()

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(420)
        self._settings = settings
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
        display_form.addRow(self._gallery_cb)
        display_form.addRow(self._sidebar_cb)
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
        hw_box = QGroupBox("Hardware")
        hw_layout = QVBoxLayout(hw_box)
        tier_label = QLabel(
            f"Detected tier: <b>{self._settings.hardware_tier}</b>  "
            f"(RAM {self._settings.detected_ram_gb:.1f} GB · "
            f"{self._settings.detected_cpu_cores} cores)"
        )
        tier_label.setStyleSheet(f"color: {theme.Tokens.text_primary};")
        redetect_btn = QPushButton("Re-detect Hardware…")
        redetect_btn.clicked.connect(self.hardware_redetect_requested.emit)
        hw_layout.addWidget(tier_label)
        hw_layout.addWidget(redetect_btn)
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

    def _on_accept(self):
        # Mutate the settings object the caller passed in, then persist
        self._settings.display.gallery_visible = self._gallery_cb.isChecked()
        self._settings.display.sidebar_visible = self._sidebar_cb.isChecked()
        self._settings.perf.mpv_quality = "high" if self._hq_cb.isChecked() else "low"
        save_display(self._settings.display)
        save_perf(self._settings.perf)
        self.accept()
