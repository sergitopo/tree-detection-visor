"""parcel-processor.py

Watches .raster-crops/ for new GeoTIFF files produced by crop-parcels.py.
For every new .tif it:
  1. Reads the companion .meta sidecar to get the parcel id and clase.
  2. Calls detect_trees.py to detect trees and write a point shapefile to
     .output-trees-parcels/<id>.shp.
  3. Deletes the consumed .tif and .meta files.
"""

import os
import sys
import json
import time
import logging
import subprocess

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
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CROPS_DIR      = os.path.join(BASE_DIR, '.raster-crops')
OUTPUT_DIR     = os.path.join(BASE_DIR, '.output-trees-parcels')
DETECT_SCRIPT  = os.path.join(BASE_DIR, 'detect_trees.py')
POLL_INTERVAL  = 2   # seconds between directory scans
FILE_SETTLE    = 1   # seconds to wait after detecting a new file


# ---------------------------------------------------------------------------
# Processing logic
# ---------------------------------------------------------------------------
def process_tif(tif_path: str):
    """Detect trees in *tif_path*, write output shapefile, delete source files."""
    base      = os.path.splitext(os.path.basename(tif_path))[0]
    meta_path = os.path.join(CROPS_DIR, f"{base}.meta")

    # Read sidecar metadata
    if not os.path.exists(meta_path):
        logger.warning(f"[{base}] No .meta sidecar found — skipping")
        return

    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)
        feature_id = meta['id']
        clase      = meta['clase']
    except Exception as e:
        logger.error(f"[{base}] Could not read .meta file: {e}")
        return

    out_shp = os.path.join(OUTPUT_DIR, f"{feature_id}.shp")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Skip if already successfully processed in a previous run
    if os.path.exists(out_shp):
        logger.info(f"[ID:{feature_id}] Output shapefile already exists — skipping")
        _delete_file(tif_path,  feature_id)
        _delete_file(meta_path, feature_id)
        return True

    logger.info(f"[ID:{feature_id}] Calling detect_trees on {tif_path}")

    try:
        result = subprocess.run(
            [
                sys.executable, DETECT_SCRIPT,
                '--input',  tif_path,
                '--output', out_shp,
                '--id',     str(feature_id),
                '--clase',  clase,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            logger.error(
                f"[ID:{feature_id}] detect_trees.py failed (rc={result.returncode}):\n"
                f"{result.stderr.strip()}"
            )
        else:
            # Forward any output from the child process
            if result.stdout.strip():
                logger.info(f"[ID:{feature_id}] {result.stdout.strip()}")
            if result.stderr.strip():
                logger.info(f"[ID:{feature_id}] (stderr) {result.stderr.strip()}")

        # Delete input files regardless of detection outcome so the pipeline
        # keeps moving; errors have already been logged above.
        _delete_file(tif_path,  feature_id)
        _delete_file(meta_path, feature_id)
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"[ID:{feature_id}] detect_trees.py timed out after 300 s — will retry")
        # Do NOT delete the tif so it gets retried on the next scan
        return False
    except Exception as e:
        logger.error(f"[ID:{feature_id}] Unexpected error: {e}")
        return False


def _delete_file(path: str, feature_id):
    try:
        os.remove(path)
        logger.info(f"[ID:{feature_id}] Deleted {os.path.basename(path)}")
    except Exception as e:
        logger.warning(f"[ID:{feature_id}] Could not delete {path}: {e}")


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------
def main():
    os.makedirs(CROPS_DIR,  exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logger.info(f"parcel-processor started — watching: {CROPS_DIR}")
    logger.info(f"Output directory: {OUTPUT_DIR}")

    processed = set()   # basenames of .tif files that have been dispatched

    while True:
        try:
            current_tifs = {
                f for f in os.listdir(CROPS_DIR) if f.endswith('.tif')
            }
            new_tifs = current_tifs - processed

            for fname in sorted(new_tifs):
                tif_path = os.path.join(CROPS_DIR, fname)
                # Let the writer finish before we open the file
                time.sleep(FILE_SETTLE)
                if not os.path.exists(tif_path):
                    processed.add(fname)   # gone externally, don't revisit
                    continue
                # Mark processed only on success so failures are retried
                if process_tif(tif_path):
                    processed.add(fname)

        except Exception as e:
            logger.error(f"Error in watcher loop: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
