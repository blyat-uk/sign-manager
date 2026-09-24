"""Generate the Sign Manager app icon set.

Draws the icon with QPainter (no fonts / text) using the design tokens from
``sign_manager.ui.theme`` and writes, into ``src/sign_manager/resources/icons``:

* ``sign-manager.png``            1024 px master
* ``sign-manager-<N>.png``        N in 16, 32, 64, 128, 256, 512 (Linux packages)
* ``sign-manager.ico``            16, 24, 32, 48, 64, 128, 256 (Windows)
* ``sign-manager.icns``           16 .. 1024 (macOS)

Run from the repo root::

    uv run --with pillow python packaging/make_icon.py

The script is idempotent: every run overwrites the same outputs.
"""
from __future__ import annotations

import io
import os
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

from sign_manager.ui import theme

T = theme.Tokens
OUT_DIR = REPO_ROOT / "src" / "sign_manager" / "resources" / "icons"
BASENAME = "sign-manager"

MASTER_SIZE = 1024
PNG_SIZES = (16, 32, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# Sizes at or below this are drawn directly with a simplified, bolder layout
# instead of being downscaled from the master.
SMALL_MAX = 32


def _c(hex_str: str, alpha: int = 255) -> QColor:
    col = QColor(hex_str)
    col.setAlpha(alpha)
    return col


def render(size: int) -> QImage:
    """Draw the icon at ``size`` px, picking the layout suited to the size."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if size <= SMALL_MAX:
        _paint_small(p, size)
    else:
        _paint_large(p, size)
    p.end()
    return img


def _paint_small(p: QPainter, size: int) -> None:
    """Pixel-grid layout for 16/32 px: geometry on a 16-unit grid so every
    edge lands on a pixel boundary. No margin, no scene detail, one text bar,
    corner "handles" reduced to single white pixels."""
    p.scale(size / 16.0, size / 16.0)

    tile = QRectF(0, 0, 16, 16)
    tile_path = QPainterPath()
    tile_path.addRoundedRect(tile, 3.5, 3.5)
    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0.0, _c(T.bg_raised))
    grad.setColorAt(1.0, _c(T.bg_deepest))
    p.fillPath(tile_path, grad)

    # 14 x 8 video card (~16:9).
    card = QRectF(1, 4, 14, 8)
    sky = QLinearGradient(card.topLeft(), card.bottomLeft())
    sky.setColorAt(0.0, _c(T.accent_deep))
    sky.setColorAt(1.0, _c(T.bg_hover))
    p.fillRect(card, sky)

    # Label box: outline on pixel cols 3..12, rows 5..10.
    p.fillRect(QRectF(4, 6, 8, 4), _c(T.bg_deepest))
    p.fillRect(QRectF(5, 7, 6, 2), _c(T.text_emphasis))
    pen = QPen(_c(T.accent), 1)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(3.5, 5.5, 9, 5))
    for x, y in ((3, 5), (12, 5), (3, 10), (12, 10)):
        p.fillRect(QRectF(x, y, 1, 1), _c(T.text_emphasis))


def _paint_large(p: QPainter, size: int) -> None:
    """Full-detail layout on a 1024 grid with a macOS-style ~10% margin."""
    p.scale(size / 1024.0, size / 1024.0)

    # --- Tile ---------------------------------------------------------------
    margin = 100
    tile = QRectF(margin, margin, 1024 - 2 * margin, 1024 - 2 * margin)
    radius = tile.width() * 0.225
    tile_path = QPainterPath()
    tile_path.addRoundedRect(tile, radius, radius)

    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0.0, _c(T.bg_raised))
    grad.setColorAt(1.0, _c(T.bg_deepest))
    p.fillPath(tile_path, grad)
    p.setPen(QPen(_c(T.border_strong), 6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(tile.adjusted(3, 3, -3, -3), radius - 3, radius - 3)

    # --- 16:9 video frame card ------------------------------------------------
    card_w = 740
    card_h = card_w * 9 / 16
    card = QRectF((1024 - card_w) / 2, (1024 - card_h) / 2, card_w, card_h)
    card_path = QPainterPath()
    card_path.addRoundedRect(card, 28, 28)

    p.save()
    p.setClipPath(card_path)
    # Sky: deep accent fading into the surface colour.
    sky = QLinearGradient(card.topLeft(), card.bottomLeft())
    sky.setColorAt(0.0, _c(T.accent_deep))
    sky.setColorAt(1.0, _c(T.bg_hover))
    p.fillRect(card, sky)
    # Ground: a gentle hill across the lower part of the frame.
    hill = QPainterPath()
    y0 = card.top() + card_h * 0.74
    hill.moveTo(card.left(), y0)
    hill.cubicTo(
        QPointF(card.left() + card_w * 0.30, y0 - card_h * 0.16),
        QPointF(card.left() + card_w * 0.65, y0 + card_h * 0.08),
        QPointF(card.right(), y0 - card_h * 0.06),
    )
    hill.lineTo(card.bottomRight())
    hill.lineTo(card.bottomLeft())
    hill.closeSubpath()
    p.fillPath(hill, _c(T.bg_surface))
    # A small sun/moon for a hint of scene.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_c(T.text_primary, 90))
    p.drawEllipse(QPointF(card.left() + card_w * 0.86, card.top() + card_h * 0.17),
                  card_h * 0.065, card_h * 0.065)
    p.restore()

    p.setPen(QPen(_c(T.border_strong), 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(card_path)

    # --- Selected sign label ------------------------------------------------
    box = QRectF(card.left() + card_w * 0.18, card.top() + card_h * 0.38,
                 card_w * 0.64, card_h * 0.42)
    p.fillRect(box, _c(T.bg_deepest, 235))

    # Two "text" bars (no glyphs).
    bar_h = 34
    bar_r = bar_h / 2
    gap = box.height() * 0.14
    top_y = box.center().y() - gap / 2 - bar_h
    bar1 = QRectF(box.left() + box.width() * 0.16, top_y, box.width() * 0.68, bar_h)
    bar2 = QRectF(box.left() + box.width() * 0.26, top_y + bar_h + gap,
                  box.width() * 0.48, bar_h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_c(T.text_emphasis))
    p.drawRoundedRect(bar1, bar_r, bar_r)
    p.setBrush(_c(T.text_primary))
    p.drawRoundedRect(bar2, bar_r, bar_r)

    # Accent selection outline (solid, as in VideoFrameWidget).
    pen = QPen(_c(T.accent), 14)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(box)

    # White square drag handles with accent border at the corners.
    hs = 52
    p.setPen(QPen(_c(T.accent), 8))
    p.setBrush(_c(T.text_emphasis))
    for pt in (box.topLeft(), box.topRight(), box.bottomLeft(), box.bottomRight()):
        p.drawRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs))


def to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


# (OSType, pixel size) pairs; each entry holds a PNG payload.
ICNS_ENTRIES = (
    (b"icp4", 16), (b"icp5", 32), (b"ic11", 32),    # 16, 32, 16@2x
    (b"ic12", 64), (b"ic07", 128), (b"ic13", 256),  # 32@2x, 128, 128@2x
    (b"ic08", 256), (b"ic14", 512), (b"ic09", 512),  # 256, 256@2x, 512
    (b"ic10", 1024),                                 # 512@2x
)


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def write_icns(path: Path, at) -> None:
    body = b""
    for ostype, size in ICNS_ENTRIES:
        data = _png_bytes(at(size))
        body += ostype + struct.pack(">I", len(data) + 8) + data
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> None:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    master = to_pil(render(MASTER_SIZE))

    cache: dict[int, Image.Image] = {MASTER_SIZE: master}

    def at(size: int) -> Image.Image:
        if size not in cache:
            if size <= SMALL_MAX:
                cache[size] = to_pil(render(size))
            else:
                cache[size] = master.resize((size, size), Image.Resampling.LANCZOS)
        return cache[size]

    master.save(OUT_DIR / f"{BASENAME}.png")
    for size in PNG_SIZES:
        at(size).save(OUT_DIR / f"{BASENAME}-{size}.png")

    # ICO: Pillow's ICO writer resizes from the base image; pass pre-rendered
    # frames via append_images so small sizes use the crisp direct renders.
    ico_frames = [at(sz) for sz in ICO_SIZES]
    ico_frames[-1].save(
        OUT_DIR / f"{BASENAME}.ico",
        format="ICO",
        sizes=[(sz, sz) for sz in ICO_SIZES],
        append_images=ico_frames[:-1],
    )

    # ICNS: Pillow's writer omits the 1x 16/32 slots (icp4/icp5), so the
    # container is assembled by hand from PNG payloads.
    write_icns(OUT_DIR / f"{BASENAME}.icns", at)

    for f in sorted(OUT_DIR.iterdir()):
        print(f.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
