#!/usr/bin/env python3
"""Generate a darktable XMP sidecar with retouch (heal) entries for sensor burns.

Converts burn polygons from burns.svg into darktable retouch module entries,
producing a .ARW.xmp sidecar that heals the burns when the ARW is opened in
darktable. Non-destructive: the ARW file itself is not modified.

The sidecar is built from the empty template (auto-presets applied, no retouch
history) and adds:
  - One path mask per burn in masks_history
  - One group mask linking all paths to the retouch module
  - A retouch history entry referencing the group

Usage:
    .venv/bin/python heal_arw.py <input.ARW> [--svg burns.svg] [--output path]

If output is omitted, writes <input>.xmp next to the input (darktable convention).
"""

import argparse
import base64
import io
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np

# darktable normalizes mask coordinates to iwidth x iheight.
# For Sony A7R3 full-frame ARW, the visible raw area is 7968x5320
# (the JPG is 7952x5304 — 16px difference, ~8px border per side).
# The JPG coordinate system starts ~8px into the ARW visible area, so we
# subtract this offset before normalizing to align with the sensor.
ARW_WIDTH = 7968
ARW_HEIGHT = 5320
ARW_X_OFFSET = 8  # JPG origin is 8px right of ARW visible origin
ARW_Y_OFFSET = 0  # Y is already aligned

# darktable mask type constants
DT_MASKS_PATH = 0x200       # 512 - path mask (non-clone)
DT_MASKS_GROUP = 0x400      # 1024 - group mask
# Actually from darktable source: DT_MASKS_PATH = 0x0200 (in the type enum it's
# a bitfield). But the XMP stores mask_type as the raw integer. From the sample:
# path masks have mask_type=10, group masks have mask_type=12.
# These are the values darktable writes to XMP (not the internal bitfield flags).
MASK_TYPE_PATH = 10
MASK_TYPE_GROUP = 12

# Retouch algorithm: 2 = heal
RETOUCH_ALGO_HEAL = 2

# Path point structure: dt_masks_point_path_t
# float corner[2], float ctrl1[2], float ctrl2[2], float border[2], int state
# = 8 floats + 1 int = 36 bytes
PATH_POINT_SIZE = 36

# Group point structure: dt_masks_point_group_t
# int formid, int parentid, float opacity, int state = 16 bytes
GROUP_POINT_SIZE = 16

# Retouch params blob: fixed 13260 bytes. Each shape occupies an 11-int32 slot
# at the start: [mask_id, 0, algo, 0, 0, 0, 0, 0, 0, 0, 0]. Global defaults at
# offset 3300 (int32 index): [algo, blur_radius_neg, blur_radius_pos, ...]
RETOUCH_PARAMS_SIZE = 13260
RETOUCH_SLOT_INTS = 11  # int32s per shape slot
# Global defaults at int32 offset 3300: [algo(2), -3.0f, 3.0f, 10.0f, 2000]
# These match the sample's non-zero values at positions 3300-3314.

# Blendop params: 420 bytes. The group mask_id is at int32 offset 6.
# We use the sample's blendop blob as a template and patch the mask_id.
BLENDOP_SIZE = 420
BLENDOP_MASK_ID_OFFSET = 6  # int32 offset

# Border (feather) for path masks: ~0.65% of image dimension, matching the
# sample's value of 0.0065. This gives a small feather around the burn.
PATH_BORDER = 0.002

# Default source position for heal (relative offset from target).
# darktable uses a small offset; the sample has source ~0.02 away.
# We'll set it to a small offset from the first corner.
SOURCE_OFFSET = 0.02


def parse_transform_translate(transform: str) -> tuple[float, float]:
    if not transform:
        return (0.0, 0.0)
    m = re.search(r"translate\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\)", transform)
    if not m:
        return (0.0, 0.0)
    return (float(m.group(1)), float(m.group(2)))


def parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    """Parse SVG path 'd' into subpaths (list of (x,y) points)."""
    tokens = re.findall(r"[MLHVCZmlhvcz]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", d)
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x, y = 0.0, 0.0
    start = (0.0, 0.0)
    i = 0
    n = len(tokens)

    def nums(count):
        nonlocal i
        vals = [float(tokens[i + j]) for j in range(count)]
        i += count
        return vals

    while i < n:
        cmd = tokens[i]
        i += 1
        rel = cmd.islower()
        cmdU = cmd.upper()

        if cmdU == "M":
            nx, ny = nums(2)
            if rel:
                x += nx; y += ny
            else:
                x, y = nx, ny
            start = (x, y)
            if cur:
                subpaths.append(cur)
            cur = [start]
            while i < n and not tokens[i].isalpha():
                nx, ny = nums(2)
                if rel: x += nx; y += ny
                else: x, y = nx, ny
                cur.append((x, y))
        elif cmdU == "L":
            while i < n and not tokens[i].isalpha():
                nx, ny = nums(2)
                if rel: x += nx; y += ny
                else: x, y = nx, ny
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
                    x1 += x; y1 += y; x2 += x; y2 += y; nx += x; ny += y
                for t in np.linspace(0, 1, 9)[1:]:
                    mt = 1 - t
                    bx = mt**3*x + 3*mt**2*t*x1 + 3*mt*t**2*x2 + t**3*nx
                    by = mt**3*y + 3*mt**2*t*y1 + 3*mt*t**2*y2 + t**3*ny
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
            pass
    if cur:
        subpaths.append(cur)
    return subpaths


def _rdp_simplify(points: np.ndarray, epsilon: float = 1.5) -> np.ndarray:
    """Ramer-Douglas-Peucker simplification for a closed polygon.

    Reduces dense point arrays to essential corners. epsilon is the max
    deviation in pixels — 1.5px is tight enough to preserve small burns
    while dropping the bezier-flattening interpolation points.
    """
    n = len(points)
    if n < 4:
        return points

    # For a closed polygon, anchor on the two farthest-apart points,
    # then simplify each arc. This avoids RDP's sensitivity to the
    # starting point on a closed loop.
    d0 = points[0]
    dists = np.sum((points - d0) ** 2, axis=1)
    far = int(np.argmax(dists))

    keep = np.zeros(n, dtype=bool)
    keep[0] = True
    keep[far] = True

    def rdp(lo, hi):
        if hi <= lo + 1:
            return
        seg = points[lo:hi + 1]
        a = points[lo]
        b = points[hi]
        ab = b - a
        ab_len2 = float(ab @ ab)
        if ab_len2 < 1e-12:
            d = np.sum((seg - a) ** 2, axis=1)
        else:
            # perpendicular distance squared from each point to line ab
            cross = (seg[:, 0] - a[0]) * ab[1] - (seg[:, 1] - a[1]) * ab[0]
            d = cross ** 2 / ab_len2
        d[0] = d[-1] = -1  # exclude endpoints
        idx = int(np.argmax(d))
        if d[idx] > epsilon * epsilon:
            keep[lo + idx] = True
            rdp(lo, lo + idx)
            rdp(lo + idx, hi)

    rdp(0, far)
    rdp(far, n - 1)

    simplified = points[keep]
    # Ensure closed
    if not np.allclose(simplified[0], simplified[-1]):
        simplified = np.vstack([simplified, simplified[0:1]])
    return simplified


def load_burn_polygons(svg_path: Path, simplify: bool = True) -> list[np.ndarray]:
    """Load burn polygons from SVG, in JPG stored-pixel coordinates.

    When simplify=True, reduces dense bezier-flattened points to essential
    corners via RDP (epsilon=1.5px). This keeps darktable's path masks
    lightweight — typically 4-10 points per burn instead of 30-130.
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()
    polygons: list[np.ndarray] = []
    for g in root.iter():
        if g.tag.split("}")[-1] != "g":
            continue
        tx, ty = parse_transform_translate(g.get("transform", ""))
        for path in g.iter():
            if path.tag.split("}")[-1] != "path":
                continue
            d = path.get("d")
            if not d:
                continue
            for sp in parse_path_d(d):
                if len(sp) < 3:
                    continue
                pts = np.array(sp, dtype=np.float64)
                pts[:, 0] = (pts[:, 0] + tx) * (96.0 / 25.4)
                pts[:, 1] = (pts[:, 1] + ty) * (96.0 / 25.4)
                pts = pts.astype(np.int32)
                if simplify:
                    pts = _rdp_simplify(pts, epsilon=1.5)
                polygons.append(pts)
    return polygons


def encode_xmp_blob(data: bytes) -> str:
    """Encode bytes as darktable XMP blob: 'gz' + 2-digit factor + base64(zlib)."""
    compressed = zlib.compress(data)
    factor = min(len(data) // len(compressed) + 1, 99) if len(compressed) > 0 else 1
    b64 = base64.b64encode(compressed).decode("ascii")
    return f"gz{factor:02d}{b64}"


def encode_hex(data: bytes) -> str:
    """Encode bytes as hex string (for small blobs like mask_src)."""
    return data.hex()


def build_path_mask_points(polygon: np.ndarray) -> bytes:
    """Build the mask_points blob for a path mask from a polygon.

    Each point: corner[2] (normalized), ctrl1[2], ctrl2[2], border[2], state(int).
    For sharp corners, ctrl1 = ctrl2 = corner. State = 1 (normal/auto handles).
    """
    buf = io.BytesIO()
    for px, py in polygon:
        # Normalize from JPG pixel space to darktable's iwidth/iheight.
        # Subtract the ARW border offset so the shapes align with the sensor.
        nx = (px - ARW_X_OFFSET) / ARW_WIDTH
        ny = (py - ARW_Y_OFFSET) / ARW_HEIGHT
        # Sharp corner: control points coincide with corner
        buf.write(struct.pack("<ff", nx, ny))       # corner
        buf.write(struct.pack("<ff", nx, ny))       # ctrl1
        buf.write(struct.pack("<ff", nx, ny))       # ctrl2
        buf.write(struct.pack("<ff", PATH_BORDER, PATH_BORDER))  # border
        buf.write(struct.pack("<i", 1))             # state (normal)
    return buf.getvalue()


def build_group_mask_points(path_ids: list[int], group_id: int) -> bytes:
    """Build the mask_points blob for a group mask.

    Each entry: formid(int), parentid(int), state(int), opacity(float).
    State: 3 = SHOW|USE (first), 11 = SHOW|USE|UNION (rest).
    Opacity: 1.0 = 100%.
    """
    buf = io.BytesIO()
    for i, pid in enumerate(path_ids):
        state = 3 if i == 0 else 11  # SHOW|USE for first, +UNION for rest
        buf.write(struct.pack("<i", pid))       # formid
        buf.write(struct.pack("<i", group_id))  # parentid
        buf.write(struct.pack("<i", state))      # state
        buf.write(struct.pack("<f", 1.0))       # opacity (100%)
    return buf.getvalue()


def build_retouch_params(path_ids: list[int]) -> bytes:
    """Build the retouch module params blob (13260 bytes).

    Each shape has an 11-int32 slot: [mask_id, 0, algo, 0, 0, 0, 0, 0, 0, 0, algo].
    Global defaults at int32 offset 3300.
    """
    buf = bytearray(RETOUCH_PARAMS_SIZE)
    arr = np.frombuffer(buf, dtype="<i4")

    for i, pid in enumerate(path_ids):
        slot_start = i * RETOUCH_SLOT_INTS
        arr[slot_start] = pid           # mask_id
        arr[slot_start + 2] = RETOUCH_ALGO_HEAL  # algorithm = heal
        arr[slot_start + 10] = RETOUCH_ALGO_HEAL  # second algo field (matches sample)

    _set_retouch_global_defaults(buf)
    return bytes(buf)


def build_empty_retouch_params() -> bytes:
    """Build an empty retouch params blob (no shapes, only global defaults).

    This is the default retouch module instance that darktable creates alongside
    the one with shapes. It has only the global defaults at offset 3300+.
    """
    buf = bytearray(RETOUCH_PARAMS_SIZE)
    _set_retouch_global_defaults(buf)
    return bytes(buf)


def _set_retouch_global_defaults(buf: bytearray) -> None:
    """Set the global default values in the retouch params blob."""
    arr = np.frombuffer(buf, dtype="<i4")
    arr[3300] = RETOUCH_ALGO_HEAL  # default algorithm
    farr = np.frombuffer(buf, dtype="<f4")
    farr[3304] = -3.0
    farr[3306] = 3.0
    farr[3308] = 10.0
    farr[3314] = 2.802596928649634e-42  # matches sample (small denormalized float)


def build_blendop_params(group_id: int) -> bytes:
    """Build blendop params (420 bytes) with the group mask_id at offset 6.

    Uses the sample's blendop as template (the standard retouch blend config).
    """
    # The sample blendop (decoded) is 420 bytes. We use a minimal default:
    # The standard "no blending, just mask" blendop. From the sample:
    # blendop2 = "gz07eJxjYGBgYGFgYJBggIETTiBS/nBDFkyElQETMGIRY2BosIfgkcrHDipmXTkAwrj4uMD/////gxgAX9QpbQ=="
    # Decoded, it has mask_id at int32 offset 6. We construct from scratch
    # using the known structure of dt_develop_blend_params_t.
    # Rather than reverse-engineer the full struct, use the sample as template.
    sample_blendop_b64 = "gz07eJxjYGBgYGFgYJBggIETTiBS/nBDFkyElQETMGIRY2BosIfgkcrHDipmXTkAwrj4uMD/////gxgAX9QpbQ=="
    compressed = base64.b64decode(sample_blendop_b64[4:])
    data = bytearray(zlib.decompress(compressed))
    # Patch the group mask_id at int32 offset 6
    struct.pack_into("<i", data, BLENDOP_MASK_ID_OFFSET * 4, group_id)
    return bytes(data)


def generate_mask_id() -> int:
    """Generate a unique mask ID (timestamp-based, like darktable)."""
    return int(time.time()) + np.random.randint(0, 1000)


def build_xmp_sidecar(arw_path: Path, polygons: list[np.ndarray], template_xmp: str) -> str:
    """Build the complete XMP sidecar string.

    template_xmp: the empty template XMP (with auto-presets, no retouch).
    """
    # Generate unique IDs
    path_ids = [generate_mask_id() for _ in polygons]
    group_id = generate_mask_id()

    # Build masks_history entries
    mask_entries = []
    for i, (poly, pid) in enumerate(zip(polygons, path_ids)):
        points_blob = build_path_mask_points(poly)
        # Source position: small offset from centroid
        cx = poly[:, 0].mean() / ARW_WIDTH
        cy = poly[:, 1].mean() / ARW_HEIGHT
        src_blob = struct.pack("<ff", cx + SOURCE_OFFSET, cy + SOURCE_OFFSET)

        mask_entries.append(f"""     <rdf:li
      darktable:mask_num="{len(path_ids)}"
      darktable:mask_id="{pid}"
      darktable:mask_type="{MASK_TYPE_PATH}"
      darktable:mask_name="path #{i+1}"
      darktable:mask_version="6"
      darktable:mask_points="{encode_xmp_blob(points_blob)}"
      darktable:mask_nb="{len(poly)}"
      darktable:mask_src="{encode_hex(src_blob)}"/>""")

    # Group mask entry
    group_points = build_group_mask_points(path_ids, group_id)
    group_entry = f"""     <rdf:li
      darktable:mask_num="{len(path_ids)}"
      darktable:mask_id="{group_id}"
      darktable:mask_type="{MASK_TYPE_GROUP}"
      darktable:mask_name="group `retouch'"
      darktable:mask_version="6"
      darktable:mask_points="{encode_hex(group_points)}"
      darktable:mask_nb="{len(path_ids)}"
      darktable:mask_src="0000000000000000"/>"""

    all_mask_entries = "\n".join(mask_entries) + "\n" + group_entry

    # Build retouch history entries.
    # darktable creates two retouch instances: the default empty one (num=11)
    # and the one with shapes (num=12). Both are needed for the module to load.
    empty_retouch_params = build_empty_retouch_params()
    retouch_params = build_retouch_params(path_ids)
    blendop_params = build_blendop_params(group_id)

    # The default blendop (no mask, mask_id=0) — same as other modules use
    default_blendop = "gz08eJxjYGBgYGFgYJBggIETTgxogBVdgIGBgRGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+////H8QAAJsYJ6E="

    # Empty retouch entry (num=11) — default module instance
    retouch_entry_empty = f"""     <rdf:li
      darktable:num="11"
      darktable:operation="retouch"
      darktable:enabled="1"
      darktable:modversion="3"
      darktable:params="{encode_xmp_blob(empty_retouch_params)}"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="{default_blendop}"/>"""

    # Retouch entry with shapes (num=12)
    retouch_entry = f"""     <rdf:li
      darktable:num="12"
      darktable:operation="retouch"
      darktable:enabled="1"
      darktable:modversion="3"
      darktable:params="{encode_xmp_blob(retouch_params)}"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="{encode_xmp_blob(blendop_params)}"/>"""

    # Now assemble the full XMP by modifying the template.
    # Replace the empty masks_history and history sections.
    # The template has:
    #   <darktable:masks_history>\n    <rdf:Seq/>\n   </darktable:masks_history>
    # and a history section with entries.

    # Replace masks_history
    masks_history_block = f"""   <darktable:masks_history>
    <rdf:Seq>
{all_mask_entries}
    </rdf:Seq>
   </darktable:masks_history>"""

    xmp = template_xmp
    # Replace the empty masks_history
    xmp = re.sub(
        r'<darktable:masks_history>\s*<rdf:Seq/>\s*</darktable:masks_history>',
        masks_history_block,
        xmp,
    )

    # Insert both retouch entries before the closing </rdf:Seq> of history
    xmp = re.sub(
        r'(</darktable:history>)',
        f'    {retouch_entry_empty}\n    {retouch_entry}\n   ' + r'\1',
        xmp,
    )

    # Update history_end to 13 (was 11 in template with 0-10, now 0-12)
    xmp = re.sub(
        r'darktable:history_end="\d+"',
        f'darktable:history_end="13"',
        xmp,
    )

    # Update change_timestamp to current time
    # (darktable uses .NET ticks: 100ns intervals since 0001-01-01)
    import datetime
    ticks = int((datetime.datetime.now().timestamp() * 1e7) + 621355968000000000)
    xmp = re.sub(
        r'darktable:change_timestamp="[^"]*"',
        f'darktable:change_timestamp="{ticks}"',
        xmp,
    )

    # Update history_current_hash (darktable will recompute this, but we set a placeholder)
    # Actually, darktable will accept the sidecar even with a stale hash; it recomputes on save.
    # We leave the hash as-is from the template.

    return xmp


# The empty template XMP (from the user's clean sidecar, with auto-presets applied)
EMPTY_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:exif="http://ns.adobe.com/exif/1.0/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:darktable="http://darktable.sf.net/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
   exif:DateTimeOriginal="2026:08:15 09:29:13.000"
   xmpMM:DerivedFrom="A7R06280.ARW"
   xmp:Rating="0"
   darktable:import_timestamp="63922420163030068"
   darktable:change_timestamp="63922420323870829"
   darktable:export_timestamp="-1"
   darktable:print_timestamp="-1"
   darktable:xmp_version="5"
   darktable:raw_params="0"
   darktable:auto_presets_applied="1"
   darktable:history_end="11"
   darktable:iop_order_version="4"
   darktable:history_auto_hash="d6d679714588d4009b1b7b4240943874"
   darktable:history_current_hash="d7eae26bb5eeac614c57a697b8ab9f60">
   <darktable:masks_history>
    <rdf:Seq/>
   </darktable:masks_history>
   <darktable:history>
    <rdf:Seq>
     <rdf:li
      darktable:num="0"
      darktable:operation="rawprepare"
      darktable:enabled="1"
      darktable:modversion="2"
      darktable:params="000000000000000014000000000000000002000200020002003c000000000000"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="1"
      darktable:operation="demosaic"
      darktable:enabled="1"
      darktable:modversion="6"
      darktable:params="0000000000000000000000000500000001000000cdcc4c3e00000000713d8a3e00000000080000000000000000000000"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="2"
      darktable:operation="colorin"
      darktable:enabled="1"
      darktable:modversion="7"
      darktable:params="gz48eJzjZhgFowABWAbaAaNgwAEAOQAAEA=="
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="3"
      darktable:operation="colorout"
      darktable:enabled="1"
      darktable:modversion="5"
      darktable:params="gz35eJxjZBgFo4CBAQAEEAAC"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="4"
      darktable:operation="gamma"
      darktable:enabled="1"
      darktable:modversion="1"
      darktable:params="0000000000000000"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="5"
      darktable:operation="temperature"
      darktable:enabled="1"
      darktable:modversion="4"
      darktable:params="004024400000803f0080c43f0000000004000000"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="6"
      darktable:operation="highlights"
      darktable:enabled="1"
      darktable:modversion="4"
      darktable:params="050000000000803f00000000000000000000803f000000001e00000006000000cdcccc3e000000400000000000000000"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYGBgYGRgYJBggIETTgxogBVdAKoHEzTYQ/BI5VMX/P///z+IAQBsEh0G"/>
     <rdf:li
      darktable:num="7"
      darktable:operation="channelmixerrgb"
      darktable:enabled="1"
      darktable:modversion="3"
      darktable:params="gz04eJxjYGiwZ8AAxIqRD5igmIWBgYGRgYHBYNEuOxvzA3aLf3S5guxihMoDAKOXCK4="
      darktable:multi_name="_builtin_scene-referred default"
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz08eJxjYGBgYGFgYJBggIETTgxogBVdgIGBgRGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+////H8QAAJsYJ6E="/>
     <rdf:li
      darktable:num="8"
      darktable:operation="exposure"
      darktable:enabled="1"
      darktable:modversion="7"
      darktable:params="00000000000080b93333333f00004842000080c00100000001000000"
      darktable:multi_name="_builtin_scene-referred default"
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz08eJxjYGBgYGFgYJBggIETTgxogBVdgIGBgRGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+////H8QAAJsYJ6E="/>
     <rdf:li
      darktable:num="9"
      darktable:operation="flip"
      darktable:enabled="1"
      darktable:modversion="2"
      darktable:params="ffffffff"
      darktable:multi_name="_builtin_auto"
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz10eJxjYIAACQYYOOHEgAZY0QUYGBgYsYgxMDTYQ/BI5VMX/P///z+IAQBqch0F"/>
     <rdf:li
      darktable:num="10"
      darktable:operation="sigmoid"
      darktable:enabled="1"
      darktable:modversion="3"
      darktable:params="0000c03f000000000000c8426c09793c000000000000c8420000000000000000000000000000000000000000000000000000000000000000"
      darktable:multi_name="_builtin_scene-referred default"
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="gz08eJxjYGBgYGFgYJBggIETTgxogBVdgIGBgRGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+////H8QAAJsYJ6E="/>
    </rdf:Seq>
   </darktable:history>
   <dc:creator>
    <rdf:Seq>
     <rdf:li>Thibaut</rdf:li>
    </rdf:Seq>
   </dc:creator>
   <dc:subject>
    <rdf:Bag>
     <rdf:li>arw</rdf:li>
     <rdf:li>changed</rdf:li>
     <rdf:li>darktable</rdf:li>
     <rdf:li>format</rdf:li>
    </rdf:Bag>
   </dc:subject>
   <lr:hierarchicalSubject>
    <rdf:Bag>
     <rdf:li>darktable|changed</rdf:li>
     <rdf:li>darktable|format|arw</rdf:li>
    </rdf:Bag>
   </lr:hierarchicalSubject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Input ARW file")
    ap.add_argument("--svg", type=Path, default=Path(__file__).parent / "burns.svg",
                    help="SVG with burn outlines")
    ap.add_argument("--output", type=Path, default=None,
                    help="Output XMP path (default: <input>.xmp)")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"[error] input not found: {args.input}", file=sys.stderr)
        return 1
    if not args.svg.exists():
        print(f"[error] svg not found: {args.svg}", file=sys.stderr)
        return 1

    output = args.output or args.input.with_suffix(args.input.suffix + ".xmp")

    polygons = load_burn_polygons(args.svg)
    print(f"[info] loaded {len(polygons)} burn polygons from {args.svg}")

    if not polygons:
        print("[error] no polygons parsed from SVG", file=sys.stderr)
        return 1

    # Report polygon bounds
    for i, poly in enumerate(polygons):
        x0, y0 = poly.min(axis=0)
        x1, y1 = poly.max(axis=0)
        print(f"  burn {i+1:2d}: bbox {x1-x0}x{y1-y0}px at ({x0},{y0})")

    # Update template with correct DerivedFrom and DateTimeOriginal from the ARW
    # (for now we use the template as-is; darktable will update on first open)
    xmp = build_xmp_sidecar(args.input, polygons, EMPTY_TEMPLATE)

    # Update DerivedFrom to match the actual filename
    xmp = xmp.replace('xmpMM:DerivedFrom="A7R06280.ARW"',
                      f'xmpMM:DerivedFrom="{args.input.name}"')

    output.write_text(xmp)
    print(f"[ok] wrote sidecar to {output}")
    print(f"     open {args.input.name} in darktable to verify the retouch module loads")
    return 0


if __name__ == "__main__":
    sys.exit(main())