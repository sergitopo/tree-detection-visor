import numpy as np
import rasterio
from scipy import ndimage
from skimage.feature import peak_local_max
from skimage.segmentation import watershed
import shapefile


input_tif = "sample_parcel2.tif"
output_shp = "trees_centroids.shp"


with rasterio.open(input_tif) as src:

    red = src.read(1).astype(float)
    nir = src.read(4).astype(float)

    transform = src.transform
    bounds = src.bounds
    crs = src.crs


# NDVI
ndvi = (nir - red) / (nir + red + 1e-6)


# vegetación
veg_mask = ndvi > 0.15


# suavizar
ndvi_smooth = ndimage.gaussian_filter(ndvi, sigma=1)


# encontrar máximos locales (centros de copa)
coords = peak_local_max(
    ndvi_smooth,
    min_distance=3,
    threshold_abs=0.2
)


# crear marcadores
markers = np.zeros(ndvi.shape, dtype=int)

for i,(y,x) in enumerate(coords, start=1):
    markers[y,x] = i


# segmentación watershed
labels = watershed(-ndvi_smooth, markers, mask=veg_mask)


centroids = []


for label in np.unique(labels):

    if label == 0:
        continue

    region = labels == label

    size = region.sum()

    if size < 10 or size > 2000:
        continue


    coords = np.argwhere(region)

    y,x = coords.mean(axis=0)


    X = transform.c + x * transform.a
    Y = transform.f + y * transform.e


    centroids.append((X,Y))


# escribir shapefile

w = shapefile.Writer(output_shp, shapeType=shapefile.POINT)
w.field("id","N")

for i,(x,y) in enumerate(centroids, start=1):

    w.point(x,y)
    w.record(i)

w.close()


with open(output_shp.replace(".shp",".prj"),"w") as f:
    f.write(crs.to_wkt())


print("Árboles detectados:",len(centroids))