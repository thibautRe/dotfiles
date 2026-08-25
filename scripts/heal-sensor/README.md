# heal-sensor

Heals camera sensor laser burns in JPGs using burn outlines defined in an SVG.

The burns are on the sensor, so they appear at fixed pixel coordinates on every
photo. One SVG mask heals the entire library.

## Setup

```sh
cd scripts/heal-sensor
python3 -m venv .venv
.venv/bin/pip install opencv-python-headless numpy
```

## Usage

```sh
.venv/bin/python heal.py <input.jpg> [output.jpg]
```

If `output` is omitted, writes `<input>.healed.jpg` next to the input (does not
overwrite the original).

Options:
- `--svg burns.svg` — path to the burn-outlines SVG (default: `burns.svg` in this dir)
- `--radius 5` — inpaint radius in px
- `--quality 97` — output JPG quality (97 ~ matches Sony A7R3 camera JPGs)
- `--show-mask` — also write the rasterized mask as `<output>.mask.png`

## How it works

1. Reads the EXIF orientation tag (preserved in the output; cv2 strips it).
2. Reads raw stored pixels via `cv2.IMREAD_IGNORE_ORIENTATION` (cv2 5.x
   auto-applies orientation by default, which would misalign the mask).
3. Parses the single `<path>` in `burns.svg` (one closed subpath per burn,
   drawn as outlines in Inkscape at full-frame scale: 2103.97mm × 1403.35mm =
   7952×5304 px at 96 DPI).
4. Applies the layer `translate()` transform, converts mm→px, and rasterizes
   each subpath as a filled polygon into a binary mask.
5. Dilates the mask by 1px to cover the stroke-width gap.
6. Runs `cv2.inpaint` (Telea) over the masked pixels.
7. Re-encodes as JPG at quality 97, preserving the original EXIF.

## Orientation handling

The burn is a physical defect on the sensor, so its position is **fixed in
stored-pixel space** regardless of how the camera was held. The SVG mask is
therefore defined in stored-pixel coordinates (7952×5304) and applies directly
to any full-frame image — landscape or portrait — without transformation.

The EXIF orientation tag is read only to (a) preserve it in the output and
(b) for logging. It does not affect mask alignment.

## Resolution gating

Only full-frame images (7952×5304 stored) are healed. APS-C crop images
(5248×3504) are skipped — they need a separate SVG in crop space, or a
coordinate transform. This is intentional: it prevents healing images shot
with different settings where the burn positions don't match the SVG.

## Lens-correction shift

Some Sony lenses apply in-camera distortion correction that shifts the burn
positions by 20–40px. The current script uses a single static SVG
(`burns.svg`) for shots without lens correction. For lenses with correction,
the plan is to provide per-lens SVGs (rescaled + translated versions of the
base burns) and select the right one from EXIF. Not yet implemented.

## Limitations / future work

- **Generation loss**: re-encodes the whole JPG, so non-masked 8×8 DCT blocks
  that touch the mask boundary incur small changes (mean absdiff ~0.9/255 at
  q=97). Block-level `jpegtran` splicing would eliminate this.
- **ARW support**: not yet implemented. Options are XMP sidecar (non-destructive,
  depends on RAW developer) or destructive DNG.
- **Batch / watcher**: not yet implemented. A filesystem watcher on the photo
  library root would heal new JPGs after Digikam imports them.
- **Backfill**: a one-shot scan mode to heal the existing library.
