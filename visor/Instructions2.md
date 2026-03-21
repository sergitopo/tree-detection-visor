Create a web visor for the union_trees.shp you can use leaflet/openlayers and just a few controls to:
- Zoom in/out
- Centers the map in the Fontanars coordinates.
- Cluster the trees at levels that they are too much dense.
    - Different clustering levels
    - Do not display the points until scale of 1:1000 is reached.
    - Cluster bubles have the number of trees in the bubble and their size is relative to that number.
    - The last layer of the cluster groups the trees in the geojson with the same id. When clicked it loads all the points of that id.
    - Add a legend for the bubble sizes that has 3-4 ranges.
- Use public WMTS of Institut cartografic valencia for satelite image as base layer.

Considerations:
- Mobile first design & UX.
- Lean web application
- Deployable in free hosting platforms like github pages or vercel.
- Node & npm application with dev server and bundler.
- Create a task to create the cluster point layers before start and before building. The last layer is one geosjson for all the trees in the geojson that have the same id (the geojson features are sorted by this id, so they form groups)