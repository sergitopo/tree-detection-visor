import os
import argparse
import logging
import math
import numpy as np
import rasterio
from scipy import ndimage
from scipy.signal import find_peaks
from skimage.filters import threshold_otsu
from skimage.transform import hough_line, hough_line_peaks
from skimage.morphology import skeletonize, closing, opening, disk
import shapefile

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ── Tuning parameters ─────────────────────────────────────────────────────────
CLOSE_RADIUS_M      = 2.0    # morphological closing radius to connect vine dots within a row (m)
MIN_ROW_SPACING_M   = 2.0    # minimum expected spacing between row centrelines (m)
ANGLE_TOLERANCE_DEG = 12.0   # ± tolerance around dominant orientation for filtering
HOUGH_THRESHOLD_REL = 0.08   # Hough peak threshold relative to accumulator max
# ──────────────────────────────────────────────────────────────────────────────


def _pixel_to_geo(transform, col, row):
    """Pixel (col, row) → geographic (X, Y) using the full affine transform."""
    X = transform.c + col * transform.a + row * transform.b
    Y = transform.f + col * transform.d + row * transform.e
    return X, Y


def _clip_hough_line(angle_rad, dist, H, W):
    """Clip a Hough line to the image bounding box and return pixel endpoints.

    skimage Hough convention (verified from source):
        col * cos(angle) + row * sin(angle) = rho
    where col ∈ [0, W-1], row ∈ [0, H-1], origin at top-left.

    Returns (c0, r0, c1, r1) or None if the line doesn't cross the image.
    """
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    eps   = 1e-9
    pts   = []

    # Intersect with each of the four image edges
    if abs(cos_a) > eps:
        # top edge (row = 0)
        c = dist / cos_a
        if 0.0 <= c <= W - 1:
            pts.append((c, 0.0))
        # bottom edge (row = H-1)
        c = (dist - (H - 1) * sin_a) / cos_a
        if 0.0 <= c <= W - 1:
            pts.append((c, float(H - 1)))

    if abs(sin_a) > eps:
        # left edge (col = 0)
        r = dist / sin_a
        if 0.0 <= r <= H - 1:
            pts.append((0.0, r))
        # right edge (col = W-1)
        r = (dist - (W - 1) * cos_a) / sin_a
        if 0.0 <= r <= H - 1:
            pts.append((float(W - 1), r))

    # Remove near-duplicate corner hits
    unique = []
    for p in pts:
        if not any(abs(p[0] - u[0]) < 0.5 and abs(p[1] - u[1]) < 0.5 for u in unique):
            unique.append(p)

    if len(unique) < 2:
        return None

    # Return the most distant pair (handles >2 candidates at corners)
    best, best_d2 = None, -1.0
    for i in range(len(unique)):
        for j in range(i + 1, len(unique)):
            d2 = (unique[i][0] - unique[j][0]) ** 2 + (unique[i][1] - unique[j][1]) ** 2
            if d2 > best_d2:
                best_d2 = d2
                best = (unique[i][0], unique[i][1], unique[j][0], unique[j][1])
    return best


def _write_empty_shp(output_shp, crs):
    os.makedirs(os.path.dirname(os.path.abspath(output_shp)), exist_ok=True)
    w = shapefile.Writer(output_shp, shapeType=shapefile.POLYLINE)
    w.field('id',        'N', 15)
    w.field('clase',     'C', 80)
    w.field('row_id',    'N', 10)
    w.field('direction', 'F', 8, 2)
    w.close()
    with open(output_shp.replace('.shp', '.prj'), 'w') as f:
        f.write(crs.to_wkt())


def detect_vineyard_lines(input_tif, output_shp, feature_id, clase=''):
    """Detect vineyard row lines in a parcel raster and write them to a polyline shapefile.

    Algorithm:
      1. Build a nodata mask (pixels where all bands == 0).
      2. Compute NDVI (Red = band 1, NIR = band 4) on valid pixels only.
      3. Determine an adaptive threshold via Otsu on the valid-pixel NDVI.
      4. Morphological closing connects individual vine plants into elongated row blobs.
      5. Skeletonize to 1-pixel-wide centrelines.
      6. Standard (accumulator) Hough transform — robust even on fragmented skeletons
         because every skeleton pixel votes for the correct (angle, distance) regardless
         of whether it belongs to a long connected segment.
      7. Dominant orientation = theta column with the highest total accumulator vote.
      8. Extract all Hough peaks; filter to dominant orientation ± ANGLE_TOLERANCE_DEG.
      9. Clip each surviving (angle, rho) Hough line to the image bound → one polyline
         per vineyard row.
     10. Write georeferenced polyline shapefile.

    Args:
        input_tif:  Path to the cropped parcel GeoTIFF (4-band RGBI, DN uint8/float).
        output_shp: Path for the output polyline shapefile (.shp).
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
        crs       = src.crs
        pixel_size = abs(transform.a)   # metres per pixel (assumes square pixels)

    H, W = red.shape

    # ── Nodata mask ───────────────────────────────────────────────────────────
    # Outside parcels every band is 0 (crop with nodata=0). Exclude those pixels
    # before NDVI so that boundary zeros don't pollute the spectral statistics.
    nodata = (red == 0) & (nir == 0)
    valid  = ~nodata

    # ── NDVI (valid pixels only) ──────────────────────────────────────────────
    ndvi = np.where(valid, (nir - red) / (nir + red + 1e-6), np.nan)
    # Gentle spatial smoothing reduces single-pixel noise and helps adjacent
    # vine plants along the same row appear as a connected blob.
    sigma_px = max(0.5, 1.0 / pixel_size)   # ~1 m in pixels
    ndvi_s = ndimage.gaussian_filter(
        np.where(valid, ndvi, 0.0), sigma=sigma_px
    )
    ndvi_s = np.where(valid, ndvi_s, np.nan)

    valid_vals = ndvi_s[valid]
    if valid_vals.size < 200:
        logger.warning(f"[ID:{feature_id}] Too few valid pixels — skipping")
        _write_empty_shp(output_shp, crs)
        return 0

    # ── Adaptive threshold (Otsu on valid NDVI) ───────────────────────────────
    otsu_t = float(threshold_otsu(valid_vals))
    # Clip to a sensible range: very low Otsu means uniform vegetation (no rows
    # to separate) and very high means most pixels end up empty.
    otsu_t = float(np.clip(otsu_t, 0.08, 0.35))
    logger.info(f"[ID:{feature_id}] NDVI threshold (Otsu clipped): {otsu_t:.3f}")

    # ── Binary vegetation mask ────────────────────────────────────────────────
    binary = (ndvi_s > otsu_t) & valid
    veg_px = int(binary.sum())
    logger.info(f"[ID:{feature_id}] Vegetation pixels: {veg_px} "
                f"({100 * veg_px / max(valid.sum(), 1):.1f}% of valid area)")

    if veg_px < 100:
        logger.warning(f"[ID:{feature_id}] Insufficient vegetation — skipping")
        _write_empty_shp(output_shp, crs)
        return 0

    # ── Morphological closing: connect vine plants into continuous row blobs ──
    close_px = max(2, int(round(CLOSE_RADIUS_M / pixel_size)))
    binary   = closing(binary, disk(close_px))
    # Remove isolated single-pixel specks that survived closing
    binary   = opening(binary, disk(1))

    # ── Skeletonize ───────────────────────────────────────────────────────────
    skeleton = skeletonize(binary)
    skel_px  = int(skeleton.sum())
    logger.info(f"[ID:{feature_id}] Skeleton pixels: {skel_px}")

    if skel_px < 20:
        logger.warning(f"[ID:{feature_id}] Near-empty skeleton — skipping")
        _write_empty_shp(output_shp, crs)
        return 0

    # ── Standard (accumulator) Hough transform ────────────────────────────────
    # Uses ALL skeleton pixels collectively: even highly fragmented rows
    # produce strong accumulator peaks at the correct (theta, rho).
    tested_angles = np.deg2rad(np.arange(0, 180, 0.5))
    h_acc, theta_arr, d_arr = hough_line(skeleton, theta=tested_angles)

    # ── Step 1: dominant orientation via broad 2D peak extraction ────────────
    # Extract a generous set of peaks to see where the accumulator mass lies.
    # min_distance is loose here; we only need the angle distribution.
    min_rho_step = max(3, int(round(MIN_ROW_SPACING_M / pixel_size)))
    av_broad, ao_broad, _ = hough_line_peaks(
        h_acc, theta_arr, d_arr,
        num_peaks=500,
        threshold=0.04 * float(h_acc.max()),
        min_distance=max(2, min_rho_step // 2),
        min_angle=0,
    )

    if len(av_broad) == 0:
        logger.warning(f"[ID:{feature_id}] No Hough peaks found")
        _write_empty_shp(output_shp, crs)
        return 0

    # Dominant angle = mode of peak angles (histogram at 0.5° resolution)
    peak_degs = np.degrees(ao_broad) % 180.0
    n_bins    = int(180 / 0.5)
    hist, edges = np.histogram(peak_degs, bins=n_bins, range=(0, 180))
    dom_bin   = int(np.argmax(hist))
    dom_deg   = float((edges[dom_bin] + edges[dom_bin + 1]) / 2.0)
    dom_angle = math.radians(dom_deg)
    logger.info(f"[ID:{feature_id}] Dominant row orientation: {dom_deg:.1f}°")

    # ── Step 2: 1D rho-profile at the dominant orientation ────────────────────
    # Average the Hough accumulator over a ±3° band around the dominant angle
    # to improve SNR, then find peaks in that 1D rho profile.
    # This guarantees that every detected row uses EXACTLY the same theta
    # → all output lines are perfectly parallel and free of angle duplicates.
    BAND_DEG  = 3.0
    idx_dom   = int(np.argmin(np.abs(theta_arr - dom_angle)))
    n_spread  = max(1, int(round(BAND_DEG / 0.5)))
    idx_lo    = max(0, idx_dom - n_spread)
    idx_hi    = min(len(theta_arr), idx_dom + n_spread + 1)
    rho_prof  = h_acc[:, idx_lo:idx_hi].sum(axis=1).astype(float)

    rho_thr   = HOUGH_THRESHOLD_REL * float(h_acc.max()) * (idx_hi - idx_lo)
    peak_idxs, _ = find_peaks(rho_prof, height=rho_thr, distance=min_rho_step)
    logger.info(f"[ID:{feature_id}] {len(peak_idxs)} row rho peaks detected")

    if len(peak_idxs) == 0:
        logger.warning(f"[ID:{feature_id}] No row peaks in rho profile")
        _write_empty_shp(output_shp, crs)
        return 0

    row_rhos = d_arr[peak_idxs]

    # ── Step 3: clip each (dom_angle, rho) to image bounds ──────────────────
    # All rows use the SAME dom_angle → perfectly parallel output.
    rows_geo = []
    for rho in row_rhos:
        pts = _clip_hough_line(dom_angle, float(rho), H, W)
        if pts is None:
            continue
        c0, r0, c1, r1 = pts
        X0, Y0 = _pixel_to_geo(transform, c0, r0)
        X1, Y1 = _pixel_to_geo(transform, c1, r1)
        rows_geo.append((X0, Y0, X1, Y1, dom_deg))

    # ── Write polyline shapefile ──────────────────────────────────────────────
    os.makedirs(os.path.dirname(os.path.abspath(output_shp)), exist_ok=True)
    w = shapefile.Writer(output_shp, shapeType=shapefile.POLYLINE)
    w.field('id',        'N', 15)
    w.field('clase',     'C', 80)
    w.field('row_id',    'N', 10)
    w.field('direction', 'F', 8, 2)   # row orientation in degrees (0–180)

    for row_id, (X0, Y0, X1, Y1, direction) in enumerate(rows_geo, start=1):
        w.line([[[X0, Y0], [X1, Y1]]])
        w.record(feature_id, clase, row_id, round(direction, 2))

    w.close()
    with open(output_shp.replace('.shp', '.prj'), 'w') as f:
        f.write(crs.to_wkt())

    logger.info(f"[ID:{feature_id}] Wrote {len(rows_geo)} vineyard rows → {output_shp}")
    return len(rows_geo)


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
