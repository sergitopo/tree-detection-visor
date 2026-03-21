"""crop-parcels.py

Reads ../Cultius.shp, filters parcels by target crop class, crops the mosaic
raster for each matching parcel, and saves each crop to .raster-crops/<id>.tif
together with a small JSON sidecar (.meta) carrying the parcel id and clase.
"""

import os
import json
import logging

import numpy as np
import rasterio
from rasterio.mask import mask as rasterio_mask
import shapefile

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CULTIUS_SHP = os.path.normpath(os.path.join(BASE_DIR, '..', 'Cultius.shp'))
MOSAIC_TIF  = os.path.join(BASE_DIR, 'mosaic_fontanars_2023',
                           'mosaic_fontanars_2023.0.tif')
OUTPUT_DIR  = os.path.join(BASE_DIR, '.raster-crops')

TARGET_CLASSES = {
    'Olivar (secano)',
    'Frutos secos (secano)',
    'Frutales (regadío)',
    'Olivar - frutales (secano)',
    'Olivar (regadío)',
    'Frutales (secano)',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def shape_to_geojson(shp):
    """Return a GeoJSON-like dict from a pyshp Shape via __geo_interface__."""
    return shp.__geo_interface__


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Read and filter shapefile
    # ------------------------------------------------------------------
    logger.info(f"Reading shapefile: {CULTIUS_SHP}")
    sf     = shapefile.Reader(CULTIUS_SHP)
    fields = [f[0] for f in sf.fields[1:]]

    total    = sf.numRecords
    filtered = []
    for i in range(total):
        rec = dict(zip(fields, sf.record(i)))
        if rec['clase'] in TARGET_CLASSES:
            filtered.append((i, rec))

    logger.info(f"Matched {len(filtered)} / {total} parcels for processing")

    if not filtered:
        logger.warning("No matching parcels found. Check TARGET_CLASSES.")
        return

    # ------------------------------------------------------------------
    # Open mosaic once and process all parcels
    # ------------------------------------------------------------------
    logger.info(f"Opening mosaic: {MOSAIC_TIF}")
    with rasterio.open(MOSAIC_TIF) as src:
        profile = src.profile.copy()

        for idx, (record_idx, attrs) in enumerate(filtered, start=1):
            feature_id = attrs['id']
            clase      = attrs['clase']
            out_tif    = os.path.join(OUTPUT_DIR, f"{feature_id}.tif")
            out_meta   = os.path.join(OUTPUT_DIR, f"{feature_id}.meta")

            if os.path.exists(out_tif):
                logger.info(f"[ID:{feature_id}] ({idx}/{len(filtered)}) "
                            f"Already exists, skipping")
                continue

            try:
                geom      = shape_to_geojson(sf.shape(record_idx))
                out_image, out_transform = rasterio_mask(
                    src, [geom], crop=True, all_touched=True
                )

                out_profile = profile.copy()
                out_profile.update({
                    'height':    out_image.shape[1],
                    'width':     out_image.shape[2],
                    'transform': out_transform,
                })

                with rasterio.open(out_tif, 'w', **out_profile) as dst:
                    dst.write(out_image)

                # Sidecar metadata for downstream scripts
                with open(out_meta, 'w', encoding='utf-8') as f:
                    json.dump({'id': feature_id, 'clase': clase}, f,
                              ensure_ascii=False)

                logger.info(f"[ID:{feature_id}] ({idx}/{len(filtered)}) "
                            f"Cropped — clase: {clase}")

            except Exception as e:
                logger.error(f"[ID:{feature_id}] ({idx}/{len(filtered)}) "
                             f"Failed to crop: {e}")

    logger.info("crop-parcels.py finished.")


if __name__ == '__main__':
    main()
