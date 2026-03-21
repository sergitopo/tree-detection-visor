import os
import argparse
import logging
import math
import numpy as np
import rasterio
from scipy import ndimage
from skimage.transform import probabilistic_hough_line
from skimage.morphology import skeletonize, binary_closing, disk
import shapefile

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ── Tuning parameters ─────────────────────────────────────────────────────────
NDVI_THRESHOLD      = 0.05   # minimum NDVI to be considered vegetation
CLOSE_RADIUS_M      = 1.5    # morphological closing radius to connect vine dots within a row (m)
MIN_ROW_LENGTH_M    = 5.0    # minimum row segment length to keep (m)
MAX_LINE_GAP_PX     = 15     # max pixel gap to bridge inside probabilistic Hough
ANGLE_TOLERANCE_DEG = 20.0   # ± tolerance around dominant orientation for filtering
ROW_MERGE_DIST_M    = 1.2    # max perpendicular distance to merge Hough segments into one row (m)
# ──────────────────────────────────────────────────────────────────────────────


def _pixel_to_geo(transform, col, row):
    """Pixel (col, row) → geographic (X, Y) using the full affine transform."""
    X = transform.c + col * transform.a + row * transform.b
    Y = transform.f + col * transform.d + row * transform.e
    return X, Y


def _perpendicular_intercept(c0, r0, c1, r1, angle_rad):
    """Signed distance from the image origin to the segment midpoint,
    measured along the normal of the dominant row direction."""
    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)
    nx, ny = -dy, dx          # normal to the row direction
    mc = (c0 + c1) / 2.0
    mr = (r0 + r1) / 2.0
    return mc * nx + mr * ny


def _merge_segments(lines, pixel_size, angle_rad):
    """Group Hough segments that belong to the same physical vineyard row.

    Segments are grouped by their perpendicular intercept (= position across
    rows).  Segments within ROW_MERGE_DIST_M of each other on that axis are
    merged into a single polyline spanning their combined extent.

    Returns a list of (c0, r0, c1, r1) pixel-coordinate merged segments.
    """
    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)
    nx, ny = -dy, dx
    merge_px = ROW_MERGE_DIST_M / pixel_size

    # Annotate each segment with its perpendicular intercept
    annotated = []
    for (c0, r0), (c1, r1) in lines:
        ic = _perpendicular_intercept(c0, r0, c1, r1, angle_rad)
        annotated.append((ic, c0, r0, c1, r1))
    annotated.sort(key=lambda s: s[0])

    # Single-linkage grouping along the sorted intercept axis
    groups = []
    current = [annotated[0]]
    for seg in annotated[1:]:
        if abs(seg[0] - current[-1][0]) <= merge_px:
            current.append(seg)
        else:
            groups.append(current)
            current = [seg]
    groups.append(current)

    # For each group reconstruct one merged segment
    merged = []
    for group in groups:
        # Average perpendicular position of the group (= row centreline)
        avg_ic = float(np.mean([s[0] for s in group]))

        # Collect all endpoints and project onto the row direction
        pts = []
        for _, c0, r0, c1, r1 in group:
            pts.extend([(c0, r0), (c1, r1)])
        proj = [p[0] * dx + p[1] * dy for p in pts]

        p_min = min(proj)
        p_max = max(proj)

        # Reconstruct endpoints from (row projection, perpendicular intercept)
        c_start = p_min * dx + avg_ic * nx
        r_start = p_min * dy + avg_ic * ny
        c_end   = p_max * dx + avg_ic * nx
        r_end   = p_max * dy + avg_ic * ny
        merged.append((c_start, r_start, c_end, r_end))

    return merged


def detect_vineyard_lines(input_tif, output_shp, feature_id, clase=''):
    """Detect vineyard row lines in a parcel raster and write them to a polyline shapefile.

    Algorithm overview:
      1. Compute NDVI from Red (band 1) and NIR (band 4).
      2. Threshold to a binary vegetation mask.
      3. Morphological closing to connect individual vine plants within each row.
      4. Skeletonize to obtain 1-pixel-wide row centrelines.
      5. Probabilistic Hough transform to extract line segments.
      6. Detect the dominant row orientation via angular histogram.
      7. Filter segments outside the dominant orientation.
      8. Merge collinear/nearby segments into single rows.
      9. Write georeferenced polyline shapefile.

    Args:
        input_tif:  Path to the cropped parcel GeoTIFF (4-band RGBI, float32).
        output_shp: Path for the output polyline shapefile.
        feature_id: Integer ID of the parcel.
        clase:      'clase' attribute of the parcel.

    Returns:
        Number of vineyard rows written to the shapefile.
    """
    logger.info(f"[ID:{feature_id}] Starting vineyard line detection on {input_tif}")

    with rasterio.open(input_tif) as src:
        red = src.read(1).astype(float)
        nir = src.read(4).astype(float)
        transform = src.transform
        crs = src.crs
        pixel_size = abs(transform.a)   # metres per pixel (square pixels assumed)

    # ── NDVI & vegetation mask ────────────────────────────────────────────────
    ndvi = (nir - red) / (nir + red + 1e-6)
    ndvi_smooth = ndimage.gaussian_filter(ndvi, sigma=1.2)
    binary = ndvi_smooth > NDVI_THRESHOLD

    # ── Morphological cleanup ─────────────────────────────────────────────────
    # Closing connects individual vine dots within a row.
    close_radius_px = max(2, int(CLOSE_RADIUS_M / pixel_size))
    binary = binary_closing(binary, footprint=disk(close_radius_px))
    binary = ndimage.binary_opening(binary, structure=np.ones((2, 2)))

    # ── Skeletonize → thin row centrelines ───────────────────────────────────
    skeleton = skeletonize(binary)

    # ── Probabilistic Hough ───────────────────────────────────────────────────
    min_line_px = max(5, int(MIN_ROW_LENGTH_M / pixel_size))
    lines = probabilistic_hough_line(
        skeleton,
        threshold=max(5, min_line_px // 2),
        line_length=min_line_px,
        line_gap=MAX_LINE_GAP_PX,
    )
    logger.info(f"[ID:{feature_id}] Hough found {len(lines)} raw segments")

    def _write_empty():
        os.makedirs(os.path.dirname(os.path.abspath(output_shp)), exist_ok=True)
        w = shapefile.Writer(output_shp, shapeType=shapefile.POLYLINE)
        w.field('id',        'N', 15)
        w.field('clase',     'C', 80)
        w.field('row_id',    'N', 10)
        w.field('direction', 'F', 8, 2)
        w.close()
        with open(output_shp.replace('.shp', '.prj'), 'w') as f:
            f.write(crs.to_wkt())

    if not lines:
        logger.warning(f"[ID:{feature_id}] No vineyard rows detected")
        _write_empty()
        return 0

    # ── Dominant orientation ──────────────────────────────────────────────────
    angles = np.array([
        math.degrees(math.atan2(r1 - r0, c1 - c0)) % 180
        for (c0, r0), (c1, r1) in lines
    ])
    hist, edges = np.histogram(angles, bins=36, range=(0, 180))
    peak_bin = int(np.argmax(hist))
    dominant_deg = float((edges[peak_bin] + edges[peak_bin + 1]) / 2.0)
    dominant_rad = math.radians(dominant_deg)
    logger.info(f"[ID:{feature_id}] Dominant row orientation: {dominant_deg:.1f}°")

    # ── Orientation filter ────────────────────────────────────────────────────
    filtered = [
        line for line, a in zip(lines, angles)
        if min(abs(a - dominant_deg), 180 - abs(a - dominant_deg)) < ANGLE_TOLERANCE_DEG
    ]
    logger.info(f"[ID:{feature_id}] {len(filtered)} segments after orientation filter")

    if not filtered:
        logger.warning(f"[ID:{feature_id}] No segments left after orientation filter")
        _write_empty()
        return 0

    # ── Merge segments into individual rows ───────────────────────────────────
    rows = _merge_segments(filtered, pixel_size, dominant_rad)
    logger.info(f"[ID:{feature_id}] {len(rows)} rows after merging")

    # ── Write polyline shapefile ──────────────────────────────────────────────
    os.makedirs(os.path.dirname(os.path.abspath(output_shp)), exist_ok=True)
    w = shapefile.Writer(output_shp, shapeType=shapefile.POLYLINE)
    w.field('id',        'N', 15)
    w.field('clase',     'C', 80)
    w.field('row_id',    'N', 10)
    w.field('direction', 'F', 8, 2)   # row orientation in degrees (0–180)

    for row_id, (c0, r0, c1, r1) in enumerate(rows, start=1):
        X0, Y0 = _pixel_to_geo(transform, c0, r0)
        X1, Y1 = _pixel_to_geo(transform, c1, r1)
        w.line([[[X0, Y0], [X1, Y1]]])
        w.record(feature_id, clase, row_id, round(dominant_deg, 2))

    w.close()

    prj_path = output_shp.replace('.shp', '.prj')
    with open(prj_path, 'w') as f:
        f.write(crs.to_wkt())

    logger.info(f"[ID:{feature_id}] Wrote {len(rows)} vineyard rows → {output_shp}")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(
        description='Detect vineyard row lines in a parcel raster (4-band RGBI GeoTIFF).'
    )
    parser.add_argument('--input',  required=True, help='Input GeoTIFF path')
    parser.add_argument('--output', required=True, help='Output shapefile path (.shp)')
    parser.add_argument('--id',     required=True, help='Parcel feature ID')
    parser.add_argument('--clase',  default='',    help='Parcel clase attribute')
    args = parser.parse_args()

    detect_vineyard_lines(args.input, args.output, int(args.id), args.clase)


if __name__ == '__main__':
    main()
