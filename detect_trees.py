import numpy as np
"""
Detect tree centroids from a multispectral raster image and export as shapefile.

This script processes a GeoTIFF image to identify individual trees using NDVI
(Normalized Difference Vegetation Index) and morphological operations. It then
extracts the centroid coordinates of detected trees and saves them as a point
shapefile with geographic coordinates.

The workflow:
1. Reads red (band 1) and NIR (band 4) bands from the input raster
2. Calculates NDVI to identify vegetation
3. Applies morphological opening to remove noise
4. Labels connected vegetation regions
5. Filters regions by size (pixel count) to isolate individual trees
6. Computes centroid of each tree region
7. Transforms pixel coordinates to geographic coordinates using the raster transform
8. Exports centroids as a point shapefile with spatial reference information

Args:
    input_tif (str): Path to input multispectral GeoTIFF file
    output_shp (str): Path to output shapefile

Returns:
    None. Outputs a shapefile with point geometries and a .prj file with CRS information.

Note:
    The `size` parameter refers to the number of pixels in each detected region.
    Regions with fewer than 8 pixels or more than 5000 pixels are filtered out.
"""
import rasterio
from scipy import ndimage
import shapefile

input_tif = "sample_parcel2.tif"
output_shp = "tree_centroids.shp"

with rasterio.open(input_tif) as src:
    red = src.read(1).astype(float)
    nir = src.read(4).astype(float)
    transform = src.transform
    bounds = src.bounds
    crs = src.crs

# NDVI
ndvi = (nir - red) / (nir + red + 1e-6)

# Vegetation mask
mask = ndvi > 0.1

# remove noise
mask = ndimage.binary_opening(mask, iterations=1)

# label objects
labeled, num = ndimage.label(mask)
objects = ndimage.find_objects(labeled)

centroids = []

for i, slc in enumerate(objects, start=1):

    region = (labeled[slc] == i)
    size = region.sum()

    if size < 8 or size > 5000:
        continue

    coords = np.argwhere(region)

    coords[:,0] += slc[0].start
    coords[:,1] += slc[1].start

    y, x = coords.mean(axis=0)

    X = transform.c + x * transform.a
    Y = transform.f + y * transform.e

    centroids.append((X,Y))

# create shapefile
w = shapefile.Writer(output_shp, shapeType=shapefile.POINT)
w.field("id","N")

for i,(x,y) in enumerate(centroids, start=1):
    w.point(x,y)
    w.record(i)

w.close()

# write projection
with open(output_shp.replace(".shp",".prj"),"w") as f:
    f.write(crs.to_wkt())

print("Detected trees:", len(centroids))