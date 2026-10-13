# Mertensiella caucasica GIS Habitat Suitability — Data Preparation

Scripts that build the environmental criterion layers for the multi-criteria
habitat suitability assessment of the Caucasian salamander (*Mertensiella
caucasica* sensu lato) in north-eastern Türkiye and Georgia. All inputs are
openly available.

## Contents

| File | Purpose |
|---|---|
| `semender.py` | Downloads and clips the raster criteria, derives slope and aspect, and builds the road-distance surface. |
| `prepare_raw_data.py` | Clips the raw source datasets to the study area. |
| `osm_clip_studyarea.py` | Clips OpenStreetMap vector layers to the study area. |
| `roadextract.py` | Extracts OpenStreetMap road lines for the study area. |
| `aoi/AOI_Boundary.gpkg` | Study-area boundary (GADM level-2 units, EPSG:4326). |
| `DATA_AVAILABILITY.md` | Data sources and download endpoints. |

Generated layers are not tracked; they are several gigabytes and reproducible
from the scripts.

## Criterion layers

Elevation, slope, aspect, mean annual temperature, annual precipitation, land
use / land cover, and distance to freshwater, settlements and roads. Road data
are downloaded from OpenStreetMap as a regional `.osm.pbf` extract
(https://download.geofabrik.de/) and converted to a Euclidean distance surface.
Sources for the remaining layers are listed in `DATA_AVAILABILITY.md`.

## Requirements

Python 3.10 or later and `pip install -r requirements.txt`. GDAL/OGR is also
needed and ships with QGIS — run the OpenStreetMap scripts with the QGIS Python
interpreter.

## Usage

```
python semender.py download-data --aoi aoi/AOI_Boundary.gpkg --out-dir Data

python roadextract.py --pbf <extract>.osm.pbf --gdb <study_area>.gdb
python osm_clip_studyarea.py --pbf <extract>.osm.pbf --gdb <study_area>.gdb
python prepare_raw_data.py
```

An Earth Engine route is also available (`python semender.py gee`), followed by
`python semender.py road-dist --template Data/Elevation.tif`.

## Scope

Data preparation only. The Analytic Hierarchy Process weighting, the weighted
overlay, the classification and the validation were carried out in ArcGIS Pro;
their parameters and results are reported in the article.

## Occurrence records

The 165 occurrence records used for validation are not distributed here.
*Mertensiella caucasica* is assessed as Vulnerable, so coordinates are available
from the corresponding author on reasonable request.

## Contact

Rıdvan Yıldırım — ridvan.yildirim@omu.edu.tr
