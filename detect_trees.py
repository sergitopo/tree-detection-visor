import os
import argparse
import logging
import numpy as np
import rasterio
from scipy import ndimage
from skimage.feature import peak_local_max
from skimage.segmentation import watershed
import shapefile

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def detect_trees(input_tif, output_shp, feature_id, clase=''):
    """Detect trees in a parcel raster and write centroids to a point shapefile.

    Args:
        input_tif:  Path to the cropped parcel GeoTIFF (4-band RGBI, float32).
        output_shp: Path for the output point shapefile.
        feature_id: Integer ID of the parcel from Cultius.shp.
        clase:      'clase' attribute of the parcel from Cultius.shp.

    Returns:
        Number of trees detected.
    """
    logger.info(f"[ID:{feature_id}] Starting tree detection on {input_tif}")

    with rasterio.open(input_tif) as src:
        red = src.read(1).astype(float)
        nir = src.read(4).astype(float)
        transform = src.transform
        crs = src.crs

    # NDVI
    ndvi = (nir - red) / (nir + red + 1e-6)

    # Vegetation mask
    veg_mask = ndvi > 0.15

    # Smooth
    ndvi_smooth = ndimage.gaussian_filter(ndvi, sigma=1)

    # Local maxima (tree crown centres)
    coords = peak_local_max(
        ndvi_smooth,
        min_distance=3,
        threshold_abs=0.2
    )

    # Create markers
    markers = np.zeros(ndvi.shape, dtype=int)
    for i, (y, x) in enumerate(coords, start=1):
        markers[y, x] = i

    # Watershed segmentation
    labels = watershed(-ndvi_smooth, markers, mask=veg_mask)

    centroids = []
    for label in np.unique(labels):
        if label == 0:
            continue
        region = labels == label
        size = region.sum()
        if size < 10 or size > 2000:
            continue
        pts = np.argwhere(region)
        y, x = pts.mean(axis=0)
        X = transform.c + x * transform.a
        Y = transform.f + y * transform.e
        centroids.append((X, Y))

    # Write output shapefile
    os.makedirs(os.path.dirname(os.path.abspath(output_shp)), exist_ok=True)
    w = shapefile.Writer(output_shp, shapeType=shapefile.POINT)
    w.field('id', 'N', 15)
    w.field('clase', 'C', 80)
    for x, y in centroids:
        w.point(x, y)
        w.record(feature_id, clase)
    w.close()

    # Write .prj
    prj_path = output_shp.replace('.shp', '.prj')
    with open(prj_path, 'w') as f:
        f.write(crs.to_wkt())

    logger.info(f"[ID:{feature_id}] Detected {len(centroids)} trees → {output_shp}")
    return len(centroids)


def main():
    parser = argparse.ArgumentParser(description='Detect isolated crop trees in a parcel raster.')
    parser.add_argument('--input',  required=True, help='Input GeoTIFF path')
    parser.add_argument('--output', required=True, help='Output shapefile path (.shp)')
    parser.add_argument('--id',     required=True, help='Parcel feature ID')
    parser.add_argument('--clase',  default='',    help='Parcel clase attribute')
    args = parser.parse_args()

    detect_trees(args.input, args.output, int(args.id), args.clase)


if __name__ == '__main__':
    main()