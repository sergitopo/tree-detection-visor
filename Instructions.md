Hello, I want to process a parcels shapefiles to detect isolated crop trees, currently I only have detect_trees.py to process a single unit (parcel), but I want to automate the process from a set of parcels in ../Cultius.shp

I Have 4 main tasks for you to do:

1. Create a python script that reads the input of the ../Cultius.shp (and rest of shapfile files), and iterate each record.
    Then for each one do:
    1. filter only for the ones that the attribute "clase" is one of:
        - Olivar (secano)
        - Frutos secos (secano)
        - Frutales (regadío)
        - Olivar - frutales (secano)
        - Olivar (regadío)
    2. Crops the image in ./mosaic_fontanars_2023/mosaic_fontanars_2023.0.tif by the polygon of the feature and saves it in the same format and Spatial Reference System than the original tif in the folder ./.raster-crops, name it by the  the "id" attribute value

2. Then create another python script (call it parcel-processor.py) than 
    1. Listens for new files created in ./.raster-crops, and calls the detect_trees.py script to create a shapefile with the name of the input .tif file with the detected trees.
    2. Modify the current version of the detect_trees.py to pass the parameters of the input tif and the "id" of the feature record from Cultius.shp
    3. Then finally the parcel-processor.py saves the shapefile in the ./.output-trees-parcels just with the "id" and clase "attributes" from the input shapefile feature record and deletes the input tif that has been used.

3. Create another python script that:
    1. Creates a multipoint shapefile called union-trees.shp if not existis, with attributes "id", "classe", "number_of_trees".
    2. Listens for new files created in ./.output-trees-parcels and on each new files appends the trees of the files in a single multipoint feature with the "id", "clase" and the sum of the records of the shapefile.
    3. After it, deletes the input shapefile.

4. Create an orchestate python file that starts all the 3 processes in paralel.

Additional considerations, add logs for each processing step (image croping, detection, shapefile merging) with the id of the feature processed. Add try catch blocks to not stop the process, and add error logs on the ones that fail.

GO!


