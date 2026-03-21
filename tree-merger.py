"""tree-merger.py

Watches .output-trees-parcels/ for new point shapefiles produced by
parcel-processor.py.  For every new shapefile it:
  1. Creates union-trees.shp (multipoint, fields: id / clase / num_trees)
     if the file does not yet exist.
  2. Reads all tree points from the incoming shapefile.
  3. Appends a new MULTIPOINT feature to union-trees.shp with the parcel id,
     clase, and the total tree count.
  4. Deletes all component files of the incoming shapefile.
"""

import os
import json
import time
import logging
import shapefile
from rasterio.warp import transform as warp_transform
from rasterio.crs import CRS

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
INPUT_DIR   = os.path.join(BASE_DIR, '.output-trees-parcels')
OUTPUT_SHP     = os.path.join(BASE_DIR, 'union-trees.shp')
OUTPUT_GEOJSON = os.path.join(BASE_DIR, 'union-trees.geojson')
POLL_INTERVAL  = 2   # seconds between directory scans
FILE_SETTLE   = 1   # seconds to wait after a new file is detected

# EPSG:25830 WKT (ETRS89 / UTM zone 30N) — matches the mosaic CRS
_WKT_25830 = (
    'PROJCS["ETRS89 / UTM zone 30N",'
    'GEOGCS["ETRS89",'
    'DATUM["European_Terrestrial_Reference_System_1989",'
    'SPHEROID["GRS 1980",6378137,298.257222101]],'
    'PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",-3],'
    'PARAMETER["scale_factor",0.9996],'
    'PARAMETER["false_easting",500000],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1]]'
)


# ---------------------------------------------------------------------------
# Shapefile helpers
# ---------------------------------------------------------------------------
def ensure_union_shp():
    """Create union-trees.shp (empty multipoint) if it does not exist."""
    if not os.path.exists(OUTPUT_SHP):
        logger.info(f"Creating union-trees.shp at {OUTPUT_SHP}")
        w = shapefile.Writer(OUTPUT_SHP, shapeType=shapefile.MULTIPOINT)
        w.field('id',        'N', 15)
        w.field('clase',     'C', 80)
        w.field('num_trees', 'N', 10)
        w.close()
        _write_prj(OUTPUT_SHP)


def _write_prj(shp_path: str, wkt: str = _WKT_25830):
    prj_path = shp_path.replace('.shp', '.prj')
    # Try to inherit CRS from a file in INPUT_DIR first
    for fname in os.listdir(INPUT_DIR):
        if fname.endswith('.prj'):
            try:
                with open(os.path.join(INPUT_DIR, fname), encoding='utf-8') as f:
                    wkt = f.read()
                break
            except Exception:
                pass
    with open(prj_path, 'w', encoding='utf-8') as f:
        f.write(wkt)


def _read_existing():
    """Return (point_lists, records) from the current union-trees.shp.

    Point lists are plain Python [[x, y], ...] lists — not Shape objects —
    so we can safely pass them to w.multipoint() when rewriting the file.
    Features with no points are skipped to avoid writer count mismatches.
    """
    point_lists = []
    records     = []
    try:
        if os.path.exists(OUTPUT_SHP) and os.path.getsize(OUTPUT_SHP) > 100:
            with shapefile.Reader(OUTPUT_SHP) as sf:
                for sr in sf.iterShapeRecords():
                    pts = [[p[0], p[1]] for p in sr.shape.points]
                    if not pts:
                        continue   # skip empty/null features
                    point_lists.append(pts)
                    records.append(list(sr.record))
    except Exception as e:
        logger.warning(f"Could not read existing union-trees.shp: {e}")
    return point_lists, records


def delete_shapefile(shp_path: str):
    """Delete every component file (.shp, .shx, .dbf, .prj, .cpg) of a shapefile."""
    base = os.path.splitext(shp_path)[0]
    for ext in ('.shp', '.shx', '.dbf', '.prj', '.cpg'):
        fp = base + ext
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception as e:
                logger.warning(f"Could not delete {fp}: {e}")


# ---------------------------------------------------------------------------
# GeoJSON export (for the web visor)
# ---------------------------------------------------------------------------
_SRC_CRS = CRS.from_epsg(25830)
_DST_CRS = CRS.from_epsg(4326)


def _export_geojson():
    """Re-read union-trees.shp, reproject every point to WGS84, write
    union-trees.geojson so the Leaflet visor can load it directly.
    Each MULTIPOINT feature is exploded into individual POINT features.
    """
    features = []
    try:
        if not os.path.exists(OUTPUT_SHP) or os.path.getsize(OUTPUT_SHP) < 100:
            return

        with shapefile.Reader(OUTPUT_SHP) as sf:
            flds = [f[0] for f in sf.fields[1:]]
            for sr in sf.iterShapeRecords():
                props = dict(zip(flds, sr.record))
                pts   = sr.shape.points
                if not pts:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                lons, lats = warp_transform(_SRC_CRS, _DST_CRS, xs, ys)
                for lon, lat in zip(lons, lats):
                    features.append({
                        'type': 'Feature',
                        'geometry': {
                            'type': 'Point',
                            'coordinates': [round(lon, 7), round(lat, 7)],
                        },
                        'properties': {
                            'id':        int(props.get('id', 0)),
                            'clase':     str(props.get('clase', '')),
                            'num_trees': int(props.get('num_trees', 0)),
                        },
                    })

        geojson = {'type': 'FeatureCollection', 'features': features}
        with open(OUTPUT_GEOJSON, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, ensure_ascii=False, separators=(',', ':'))

        logger.info(f"Exported {len(features)} tree points to {OUTPUT_GEOJSON}")

    except Exception as e:
        logger.error(f"GeoJSON export failed: {e}")


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def process_shp(shp_path: str):
    """Merge the trees in *shp_path* into union-trees.shp as a MULTIPOINT feature."""
    base = os.path.splitext(os.path.basename(shp_path))[0]

    try:
        with shapefile.Reader(shp_path) as sf:
            fields  = [fld[0] for fld in sf.fields[1:]]
            records = list(sf.iterShapeRecords())

        if not records:
            logger.warning(f"[{base}] Shapefile is empty — no trees recorded")
            # Still register it in the union so we have a complete audit trail
            feature_id = base
            clase      = ''
            points     = []
        else:
            first      = dict(zip(fields, records[0].record))
            feature_id = first.get('id', base)
            clase      = first.get('clase', '')
            # Collect all point coordinates
            points = []
            for sr in records:
                for pt in sr.shape.points:
                    points.append([pt[0], pt[1]])

        num_trees = len(points)

        # Read existing union contents
        ex_shapes, ex_records = _read_existing()

        # Rewrite union-trees.shp with the new feature appended
        w = shapefile.Writer(OUTPUT_SHP, shapeType=shapefile.MULTIPOINT)
        w.field('id',        'N', 15)
        w.field('clase',     'C', 80)
        w.field('num_trees', 'N', 10)

        for pts, rec in zip(ex_shapes, ex_records):
            w.multipoint(pts)
            w.record(*rec)

        if points:
            w.multipoint(points)
            w.record(feature_id, clase, num_trees)
        # If no points, skip writing this feature entirely (no shape = no record)
        w.close()

        _write_prj(OUTPUT_SHP)
        _export_geojson()

        logger.info(
            f"[ID:{feature_id}] Appended {num_trees} trees to union-trees.shp "
            f"(total features now: {len(ex_shapes) + 1})"
        )

    except Exception as e:
        logger.error(f"[{base}] Error processing shapefile: {e}")
        return   # Do NOT delete the source file if something went wrong

    delete_shapefile(shp_path)


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------
def main():
    os.makedirs(INPUT_DIR, exist_ok=True)
    ensure_union_shp()

    logger.info(f"tree-merger started — watching: {INPUT_DIR}")
    logger.info(f"Union output: {OUTPUT_SHP}")

    processed = set()   # basenames of .shp files already handled

    while True:
        try:
            current_shps = {
                f for f in os.listdir(INPUT_DIR) if f.endswith('.shp')
            }
            new_shps = current_shps - processed

            for fname in sorted(new_shps):
                shp_path = os.path.join(INPUT_DIR, fname)
                time.sleep(FILE_SETTLE)
                if not os.path.exists(shp_path):
                    continue
                processed.add(fname)
                process_shp(shp_path)

        except Exception as e:
            logger.error(f"Error in watcher loop: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
