#!/usr/bin/env python3
"""Heal camera sensor laser burns in a JPG using burn outlines from an SVG.

The SVG (burns.svg) is drawn at full-frame sensor scale (7952x5304 at 96 DPI,
i.e. 2103.97mm x 1403.35mm). It contains a single <path> with one closed
subpath per burn, drawn as outlines (fill:none, stroke). We rasterize each
subpath as a filled polygon to build a binary heal mask, then run OpenCV
inpainting (Telea) over the masked pixels.

Usage:
    .venv/bin/python heal.py <input.jpg> [output.jpg] [--svg burns.svg]

If output is omitted, writes <input>.healed.jpg next to the input.
"""

import argparse
import io
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# SVG canvas is 2103.9666mm x 1403.35mm, which at 96 DPI maps to 7952x5304 px
# (the Sony A7R3 full-frame JPG resolution). APS-C crop is ~5248x3504.
MM_TO_PX = 96.0 / 25.4
# Display-space dimensions: the SVG is drawn as the image appears when EXIF
# orientation is applied (landscape, 7952 wide x 5304 tall).
DISPLAY_W, DISPLAY_H = 7952, 5304
# EXIF orientation tag
EXIF_ORIENTATION = 274


def parse_transform_translate(transform: str) -> tuple[float, float]:
    """Extract the translate(x,y) from an SVG transform attribute."""
    if not transform:
        return (0.0, 0.0)
    m = re.search(r"translate\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\)", transform)
    if not m:
        return (0.0, 0.0)
    return (float(m.group(1)), float(m.group(2)))


def parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    """Parse an SVG path 'd' string into a list of subpaths.

    Each subpath is a list of (x, y) points. We handle the subset of commands
    that Inkscape emits for these burn outlines: M/m (moveto), L/l (lineto),
    H/h (horizontal), V/v (vertical), C/c (cubic bezier), Z/z (closepath).
    Bezier segments are flattened to polylines.
    """
    # Tokenize: numbers and command letters. Handle scientific notation and
    # negative numbers that follow a command without a separator.
    tokens = re.findall(r"[MLHVCZmlhvcz]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", d)

    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x, y = 0.0, 0.0
    start = (0.0, 0.0)
    i = 0
    n = len(tokens)

    def nums(count: int) -> list[float]:
        nonlocal i
        vals = []
        for _ in range(count):
            vals.append(float(tokens[i]))
            i += 1
        return vals

    while i < n:
        cmd = tokens[i]
        i += 1
        rel = cmd.islower()
        cmdU = cmd.upper()

        if cmdU == "M":
            nx, ny = nums(2)
            if rel:
                x += nx
                y += ny
            else:
                x, y = nx, ny
            start = (x, y)
            if cur:
                subpaths.append(cur)
            cur = [start]
            # Subsequent coordinate pairs are treated as L
            while i < n and not tokens[i].isalpha():
                nx, ny = nums(2)
                if rel:
                    x += nx
                    y += ny
                else:
                    x, y = nx, ny
                cur.append((x, y))
        elif cmdU == "L":
            while i < n and not tokens[i].isalpha():
                nx, ny = nums(2)
                if rel:
                    x += nx
                    y += ny
                else:
                    x, y = nx, ny
                cur.append((x, y))
        elif cmdU == "H":
            while i < n and not tokens[i].isalpha():
                nx = nums(1)[0]
                x = x + nx if rel else nx
                cur.append((x, y))
        elif cmdU == "V":
            while i < n and not tokens[i].isalpha():
                ny = nums(1)[0]
                y = y + ny if rel else ny
                cur.append((x, y))
        elif cmdU == "C":
            while i < n and not tokens[i].isalpha():
                x1, y1, x2, y2, nx, ny = nums(6)
                if rel:
                    x1 += x
                    y1 += y
                    x2 += x
                    y2 += y
                    nx += x
                    ny += y
                # Flatten cubic bezier into ~8 segments
                for t in np.linspace(0, 1, 9)[1:]:
                    mt = 1 - t
                    bx = (
                        mt * mt * mt * x
                        + 3 * mt * mt * t * x1
                        + 3 * mt * t * t * x2
                        + t * t * t * nx
                    )
                    by = (
                        mt * mt * mt * y
                        + 3 * mt * mt * t * y1
                        + 3 * mt * t * t * y2
                        + t * t * t * ny
                    )
                    cur.append((bx, by))
                x, y = nx, ny
        elif cmdU == "Z":
            if cur and cur[0] != cur[-1]:
                cur.append(cur[0])
            if cur:
                subpaths.append(cur)
                cur = []
            x, y = start
        else:
            # Unknown command; skip
            pass

    if cur:
        subpaths.append(cur)

    return subpaths


def load_burn_polygons(svg_path: Path) -> list[np.ndarray]:
    """Load burn polygons from the SVG, in pixel coordinates.

    Returns a list of Nx2 int32 arrays (pixel coords), one per burn.
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Namespace handling: Inkscape SVGs use default namespace
    ns = {"svg": "http://www.w3.org/2000/svg", "inkscape": "http://www.inkscape.org/namespaces/inkscape"}

    # Find the layer <g> with transform and the <path> inside it.
    polygons: list[np.ndarray] = []

    for g in root.iter():
        tag = g.tag.split("}")[-1]
        if tag != "g":
            continue
        g_transform = g.get("transform", "")
        tx, ty = parse_transform_translate(g_transform)

        for path in g.iter():
            ptag = path.tag.split("}")[-1]
            if ptag != "path":
                continue
            d = path.get("d")
            if not d:
                continue
            subpaths = parse_path_d(d)
            for sp in subpaths:
                if len(sp) < 3:
                    continue
                # Apply layer translate, then mm->px
                pts = np.array(sp, dtype=np.float64)
                pts[:, 0] = (pts[:, 0] + tx) * MM_TO_PX
                pts[:, 1] = (pts[:, 1] + ty) * MM_TO_PX
                polygons.append(pts.astype(np.int32))

    return polygons


def build_mask(polygons: list[np.ndarray], shape: tuple[int, int], dilate: int = 5) -> np.ndarray:
    """Rasterize polygons into a binary mask, then dilate.

    shape = (height, width). The SVG polygons are in stored-pixel coordinates
    (the burn is on the sensor, so its position is fixed in stored space
    regardless of EXIF orientation).

    dilate = pixels to expand the mask by. The burn's color cast extends a few
    pixels beyond the visible outline drawn in the SVG (the laser damage has a
    soft edge), so we dilate by more than the 1px stroke-width gap.
    """
    mask = np.zeros(shape, dtype=np.uint8)
    for poly in polygons:
        # cv2.fillPoly needs a list of polygons; each must be int32
        cv2.fillPoly(mask, [poly.astype(np.int32)], 255)
    if dilate > 0:
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=dilate)
    return mask


def get_exif_orientation(path: Path) -> int:
    """Read the EXIF orientation tag (274) from a JPG. Returns 1 if absent."""
    with Image.open(path) as im:
        return int(im.getexif().get(EXIF_ORIENTATION, 1))


def heal_image(img: np.ndarray, mask: np.ndarray, radius: int = 5) -> np.ndarray:
    """Inpaint masked pixels using Telea's algorithm."""
    # cv2.INPAINT_TELEA works well for small patches; radius slightly larger
    # than the largest patch (~20px) gives the algorithm enough neighborhood.
    return cv2.inpaint(img, mask, radius, cv2.INPAINT_TELEA)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Input JPG")
    ap.add_argument("output", nargs="?", type=Path, help="Output JPG (default: <input>.healed.jpg)")
    ap.add_argument("--svg", type=Path, default=Path(__file__).parent / "burns.svg", help="SVG with burn outlines")
    ap.add_argument("--radius", type=int, default=5, help="Inpaint radius (px)")
    ap.add_argument("--dilate", type=int, default=5, help="Mask dilation in px (burns have soft edges)")
    ap.add_argument("--quality", type=int, default=97, help="Output JPG quality (match source ~96-97)")
    ap.add_argument("--show-mask", action="store_true", help="Also write the mask as <output>.mask.png")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"[error] input not found: {args.input}", file=sys.stderr)
        return 1
    if not args.svg.exists():
        print(f"[error] svg not found: {args.svg}", file=sys.stderr)
        return 1

    output = args.output or args.input.with_suffix(args.input.suffix + ".healed.jpg")

    # Read EXIF orientation. The burn is on the sensor, so its position is fixed
    # in stored-pixel space regardless of orientation; we only need orientation
    # for the resolution gate (display dims) and to preserve it in the output.
    orientation = get_exif_orientation(args.input)
    ORIENT_NAME = {1: "normal", 2: "mirror-h", 3: "180", 4: "mirror-v",
                   5: "transpose", 6: "90-CW", 7: "transverse", 8: "90-CCW"}
    print(f"[info] EXIF orientation: {orientation} ({ORIENT_NAME.get(orientation, '?')})")

    # cv2.imread in OpenCV 5.x auto-applies EXIF orientation by default. We force
    # raw stored pixels so the SVG mask (defined in stored space) aligns directly.
    img = cv2.imread(str(args.input), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if img is None:
        print(f"[error] could not read image: {args.input}", file=sys.stderr)
        return 1
    h, w = img.shape[:2]
    print(f"[info] stored pixels: {w}x{h}")

    # Resolution gate: only heal full-frame images. The SVG mask is in
    # stored-pixel space (7952x5304), so we check stored dimensions directly.
    # APS-C crop (5248x3504) would need a separate SVG in crop space.
    if (w, h) != (DISPLAY_W, DISPLAY_H):
        print(
            f"[skip] image is {w}x{h} (stored), not full-frame "
            f"{DISPLAY_W}x{DISPLAY_H}. Skipping (APS-C or other resolution "
            "needs a crop-space SVG).",
            file=sys.stderr,
        )
        return 2

    polygons = load_burn_polygons(args.svg)
    print(f"[info] loaded {len(polygons)} burn polygons from {args.svg}")

    if not polygons:
        print("[error] no polygons parsed from SVG", file=sys.stderr)
        return 1

    # Report polygon bounds (in stored-pixel space) for sanity
    total_px = 0
    for i, poly in enumerate(polygons):
        x0, y0 = poly.min(axis=0)
        x1, y1 = poly.max(axis=0)
        bw, bh = x1 - x0, y1 - y0
        total_px += cv2.contourArea(poly.astype(np.float32))
        print(f"  burn {i+1:2d}: bbox {bw}x{bh}px at ({x0},{y0})")
    print(f"[info] total burn area: ~{int(total_px)} px ({total_px/(w*h)*100:.4f}% of image)")

    # Build mask directly in stored-pixel space (SVG coords are stored coords).
    # Dilate beyond the polygon outline: the burn's color cast extends a few
    # pixels beyond the visible edge drawn in the SVG.
    mask = build_mask(polygons, (h, w), dilate=args.dilate)
    masked_px = int((mask > 0).sum())
    print(f"[info] mask covers {masked_px} px (after {args.dilate}px dilation)")

    if args.show_mask:
        mask_path = output.with_suffix(output.suffix + ".mask.png")
        cv2.imwrite(str(mask_path), mask)
        print(f"[info] wrote mask to {mask_path}")

    healed = heal_image(img, mask, radius=args.radius)

    # Re-encode as JPG. Quality 97 roughly matches Sony A7R3 camera JPGs (~12MB for
    # full-frame). This still re-encodes the whole image; non-masked pixels incur
    # small generation loss (mean absdiff ~0.5/255). For zero loss outside burns
    # we'd need block-level jpegtran splicing, which is future work.
    # We write via PIL to preserve the original EXIF (including orientation),
    # which cv2.imwrite would strip.
    ok, buf = cv2.imencode(".jpg", healed, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
    if not ok:
        print("[error] failed to encode output JPG", file=sys.stderr)
        return 1
    with Image.open(args.input) as src:
        exif = src.getexif()
    out = Image.open(io.BytesIO(buf))
    out.save(str(output), exif=exif, quality=args.quality)
    print(f"[ok] wrote healed image to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
