"""Generate the desktop app's icon files from the web app's SVG mark.

The single source of truth is ``app/web/static/favicon.svg`` -- the SVG mark the
GUI shows in the browser tab / top-left brand. PyInstaller needs a real ``.ico``
(Windows) or ``.icns`` (macOS), so this script rasterises that very SVG and packs
the result into both containers. Nothing is hand-drawn and nothing is downloaded:
change the SVG, re-run this, and the packaged app follows.

Run it explicitly after editing the SVG::

    python tools/make_app_icons.py

and commit the regenerated ``assets/`` files. ``finance_ranker.spec`` also calls
``generate_all()`` when an icon file is missing, so a checkout without the
generated assets still builds.

Why not just use an SVG->PNG library? The project ships no image stack (Pillow,
cairosvg) and adding one just to draw three shapes would be a poor trade. The
renderer below is exact, numpy-vectorised, and understands precisely the SVG
constructs the mark uses -- it raises ``IconSourceError`` if the SVG grows a
feature it cannot honour, rather than silently shipping a stale icon.
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SVG_PATH = ROOT / "app" / "web" / "static" / "favicon.svg"
ASSETS_DIR = ROOT / "assets"

ICO_NAME = "finance_ranker.ico"
ICNS_NAME = "finance_ranker.icns"
PNG_NAME = "finance_ranker-256.png"

# Windows picks from this ladder for the taskbar, Alt-Tab, Explorer and the
# installer; 256 is the largest size the .ico container allows.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# macOS reads PNG-compressed entries; icp4..ic10 are the 16..1024 slots.
ICNS_SLOTS = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
)

# Supersampling is capped by total sample count so a 1024px render stays cheap.
MAX_SUPERSAMPLE = 4
SAMPLE_BUDGET = 2048


class IconSourceError(RuntimeError):
    """The SVG uses a construct this renderer does not implement."""


# --------------------------------------------------------------------------- #
# minimal SVG reader (only what the mark uses)
# --------------------------------------------------------------------------- #
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"
_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")


@dataclass(frozen=True)
class Mark:
    """The icon mark in its own 0..`view` coordinate space."""

    view: float
    rect_w: float
    rect_h: float
    rect_rx: float
    grad_start: tuple[float, float]
    grad_end: tuple[float, float]
    grad_from: tuple[int, int, int]
    grad_to: tuple[int, int, int]
    points: tuple[tuple[float, float], ...]
    stroke: tuple[int, int, int]
    stroke_width: float
    dot_center: tuple[float, float]
    dot_radius: float


def _attr(tag: str, name: str, what: str) -> str:
    """One quoted attribute value from a single SVG tag."""
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag)
    if not match:
        raise IconSourceError(f"could not read {what} from the SVG")
    return match.group(1)


def _fnum(tag: str, name: str, what: str) -> float:
    text = _attr(tag, name, what)
    match = re.fullmatch(rf"\s*({_NUM})\s*", text)
    if not match:
        raise IconSourceError(f"{what} is not a plain number: {text!r}")
    return float(match.group(1))


def _floats(text: str, what: str) -> list[float]:
    """Every number in a whitespace/comma separated SVG value list."""
    values = [v for v in re.split(r"[\s,]+", text.strip()) if v]
    if not values or not all(re.fullmatch(_NUM, v) for v in values):
        raise IconSourceError(f"could not read {what} from the SVG: {text!r}")
    return [float(v) for v in values]


def _rgb(text: str, what: str) -> tuple[int, int, int]:
    match = _HEX.search(text)
    if not match:
        raise IconSourceError(f"could not read {what} from the SVG")
    value = match.group(1)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _path_points(d: str) -> tuple[tuple[float, float], ...]:
    """Parse the mark's polyline path: `M x y` followed by `l dx dy` pairs."""
    tokens = re.findall(r"[MmLlHhVvZz]|" + _NUM, d)
    points: list[tuple[float, float]] = []
    x = y = 0.0
    index = 0
    command: str | None = None

    def take() -> float:
        nonlocal index
        if index >= len(tokens) or re.fullmatch(r"[A-Za-z]", tokens[index]):
            raise IconSourceError(f"malformed path data: {d!r}")
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"[A-Za-z]", token):
            command = token
            index += 1
            if command in "Zz":
                command = None
            continue
        if command is None:
            raise IconSourceError(f"path data starts with a number: {d!r}")
        if command in "Mm":
            x, y = take(), take()
            points.append((x, y))
            command = "l" if command == "m" else "L"
        elif command in "Ll":
            dx, dy = take(), take()
            x = x + dx if command == "l" else dx
            y = y + dy if command == "l" else dy
            points.append((x, y))
        elif command in "Hh":
            x = x + take() if command == "h" else take()
            points.append((x, y))
        elif command in "Vv":
            y = y + take() if command == "v" else take()
            points.append((x, y))
        else:  # pragma: no cover - guarded above
            raise IconSourceError(f"unsupported path command {command!r}")
    if len(points) < 2:
        raise IconSourceError(f"the mark's path has fewer than two points: {d!r}")
    return tuple(points)


def load_mark(svg_path: Path = SVG_PATH) -> Mark:
    """Read `favicon.svg` into a renderable `Mark`."""
    text = svg_path.read_text(encoding="utf-8")

    view_box = _floats(_attr(text, "viewBox", "viewBox"), "viewBox")
    if len(view_box) != 4 or view_box[0] != 0 or view_box[1] != 0:
        raise IconSourceError(f"only a 0 0 W H viewBox is supported, got {view_box}")
    if view_box[2] != view_box[3]:
        raise IconSourceError(f"the viewBox must be square, got {view_box[2]}x{view_box[3]}")

    rect = re.search(r"<rect\b[^>]*>", text)
    if not rect:
        raise IconSourceError("the SVG has no background <rect>")
    rect_w = _fnum(rect.group(0), "width", "rect width")
    rect_h = _fnum(rect.group(0), "height", "rect height")
    rx_match = re.search(rf'\brx="({_NUM})"', rect.group(0))
    rect_rx = float(rx_match.group(1)) if rx_match else 0.0

    gradient = re.search(r"<linearGradient\b[^>]*>(.*?)</linearGradient>", text, re.S)
    if not gradient:
        raise IconSourceError("the SVG has no <linearGradient>")
    grad_tag = gradient.group(0)
    grad_coords = [
        _fnum(grad_tag, name, f"linearGradient {name}")
        for name in ("x1", "y1", "x2", "y2")
    ]
    stops = re.findall(r'<stop\b[^>]*stop-color="([^"]+)"', gradient.group(1))
    if len(stops) < 2:
        raise IconSourceError("the gradient needs at least two stop-color entries")

    path = re.search(r'<path\b[^>]*\bd="([^"]+)"[^>]*>', text)
    if not path:
        raise IconSourceError("the SVG has no stroked <path>")
    tag = path.group(0)
    stroke = _rgb(_attr(tag, "stroke", "path stroke"), "path stroke")
    stroke_width = _fnum(tag, "stroke-width", "stroke-width")

    circle = re.search(r"<circle\b[^>]*>", text)
    if not circle:
        raise IconSourceError("the SVG has no <circle>")
    dot = [
        _fnum(circle.group(0), name, f"circle {name}") for name in ("cx", "cy", "r")
    ]

    return Mark(
        view=float(view_box[2]),
        rect_w=rect_w,
        rect_h=rect_h,
        rect_rx=rect_rx,
        grad_start=(grad_coords[0], grad_coords[1]),
        grad_end=(grad_coords[2], grad_coords[3]),
        grad_from=_rgb(stops[0], "first gradient stop"),
        grad_to=_rgb(stops[-1], "last gradient stop"),
        points=_path_points(path.group(1)),
        stroke=stroke,
        stroke_width=stroke_width,
        dot_center=(dot[0], dot[1]),
        dot_radius=dot[2],
    )


# --------------------------------------------------------------------------- #
# rasteriser
# --------------------------------------------------------------------------- #
def _segment_distance(
    px: np.ndarray, py: np.ndarray, ax: float, ay: float, bx: float, by: float
) -> np.ndarray:
    """Distance from every sample to the segment (ax,ay)-(bx,by)."""
    vx, vy = bx - ax, by - ay
    length_sq = vx * vx + vy * vy
    if length_sq == 0:
        return np.hypot(px - ax, py - ay)
    t = np.clip(((px - ax) * vx + (py - ay) * vy) / length_sq, 0.0, 1.0)
    return np.hypot(px - (ax + t * vx), py - (ay + t * vy))


def render_rgba(mark: Mark, size: int) -> np.ndarray:
    """Render the mark at `size`x`size` as straight (non-premultiplied) RGBA."""
    supersample = max(1, min(MAX_SUPERSAMPLE, SAMPLE_BUDGET // size))
    grid = size * supersample
    scale = grid / mark.view

    # Sample positions in the mark's own coordinate space.
    ys, xs = np.mgrid[0:grid, 0:grid]
    px = (xs + 0.5) / scale
    py = (ys + 0.5) / scale

    # Premultiplied accumulation: the rounded rect is opaque, the stroke and dot
    # are painted on top of it, and the corners stay fully transparent.
    accum = np.zeros((grid, grid, 4), dtype=np.float32)

    half_w, half_h = mark.rect_w / 2.0, mark.rect_h / 2.0
    dx = np.abs(px - half_w) - (half_w - mark.rect_rx)
    dy = np.abs(py - half_h) - (half_h - mark.rect_rx)
    outside = np.hypot(np.maximum(dx, 0.0), np.maximum(dy, 0.0))
    rect_sdf = outside + np.minimum(np.maximum(dx, dy), 0.0) - mark.rect_rx
    body = rect_sdf <= 0.0

    # linearGradient with gradientUnits="userSpaceOnUse": project onto the axis.
    gx0, gy0 = mark.grad_start
    gx1, gy1 = mark.grad_end
    axis_x, axis_y = gx1 - gx0, gy1 - gy0
    axis_sq = axis_x * axis_x + axis_y * axis_y
    t = np.clip(((px - gx0) * axis_x + (py - gy0) * axis_y) / axis_sq, 0.0, 1.0)[..., None]
    low = np.array(mark.grad_from, dtype=np.float32)
    high = np.array(mark.grad_to, dtype=np.float32)
    gradient = low + (high - low) * t

    accum[..., :3] = np.where(body[..., None], gradient, 0.0)
    accum[..., 3] = body.astype(np.float32)

    ink = np.array(mark.stroke, dtype=np.float32)
    stroke_mask = np.zeros((grid, grid), dtype=bool)
    for (ax, ay), (bx, by) in zip(mark.points, mark.points[1:]):
        stroke_mask |= _segment_distance(px, py, ax, ay, bx, by) <= mark.stroke_width / 2.0
    dot = (px - mark.dot_center[0]) ** 2 + (py - mark.dot_center[1]) ** 2 <= mark.dot_radius**2
    ink_mask = stroke_mask | dot
    accum[..., :3] = np.where(ink_mask[..., None], ink, accum[..., :3])
    accum[..., 3] = np.where(ink_mask, 1.0, accum[..., 3])

    # Box-filter the supersampled grid down to the requested size.
    blocks = accum.reshape(size, supersample, size, supersample, 4)
    average = blocks.mean(axis=(1, 3))

    # Back to straight alpha (the containers store non-premultiplied RGBA).
    alpha = average[..., 3:4]
    rgb = np.divide(average[..., :3], alpha, out=np.zeros_like(average[..., :3]), where=alpha > 0)
    rgba = np.concatenate([rgb, alpha], axis=-1)
    return np.clip(np.rint(rgba * 255.0), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# container writers
# --------------------------------------------------------------------------- #
def encode_png(rgba: np.ndarray) -> bytes:
    """Minimal RGBA8 PNG encoder (filter 0 on every scanline)."""
    height, width = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _dib_entry(rgba: np.ndarray) -> bytes:
    """One classic .ico image: BITMAPINFOHEADER + bottom-up BGRA + AND mask."""
    height, width = rgba.shape[:2]
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, width * height * 4, 0, 0, 0, 0
    )
    # BGRA, bottom-up rows.
    bgra = rgba[..., [2, 1, 0, 3]][::-1].tobytes()
    # The 1bpp AND mask is ignored for 32bpp icons but must be sized correctly.
    mask_stride = ((width + 31) // 32) * 4
    return header + bgra + b"\x00" * (mask_stride * height)


def write_ico(path: Path, images: dict[int, np.ndarray]) -> None:
    sizes = sorted(images)
    entries = [(_dib_entry(images[size]), size) for size in sizes]

    offset = 6 + 16 * len(entries)
    directory = b""
    for blob, size in entries:
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset
        )
        offset += len(blob)

    header = struct.pack("<HHH", 0, 1, len(entries))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + directory + b"".join(blob for blob, _ in entries))


def write_icns(path: Path, images: dict[int, np.ndarray]) -> None:
    chunks = []
    for tag, size in ICNS_SLOTS:
        payload = encode_png(images[size])
        chunks.append(tag + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #
def generate_all(svg_path: Path = SVG_PATH, assets_dir: Path = ASSETS_DIR) -> list[Path]:
    """Render every container from the SVG and return the files written."""
    mark = load_mark(svg_path)
    cache: dict[int, np.ndarray] = {}

    def image(size: int) -> np.ndarray:
        if size not in cache:
            cache[size] = render_rgba(mark, size)
        return cache[size]

    ico_path = assets_dir / ICO_NAME
    icns_path = assets_dir / ICNS_NAME
    png_path = assets_dir / PNG_NAME

    write_ico(ico_path, {size: image(size) for size in ICO_SIZES})
    write_icns(icns_path, {size: image(size) for _, size in ICNS_SLOTS})
    png_path.write_bytes(encode_png(image(256)))
    return [ico_path, icns_path, png_path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--svg", type=Path, default=SVG_PATH, help="source SVG mark")
    parser.add_argument("--out", type=Path, default=ASSETS_DIR, help="output directory")
    args = parser.parse_args(argv)

    for path in generate_all(args.svg, args.out):
        print(f"{path.relative_to(ROOT) if ROOT in path.parents else path}  "
              f"({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
