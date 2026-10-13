"""
Create clipped raw source data for the Semender project.

Outputs:
  Raw_Data/raw_data.gdb        vector raw layers clipped to the study area
  Raw_Data/Rasters/*.tif       raster raw layers clipped to the study area
  Raw_Data/data_sources.docx   source links and manual download notes
"""

from __future__ import annotations

import html
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "Data"
RAW = ROOT / "Raw_Data"
GDB = RAW / "raw_data.gdb"
RASTERS = RAW / "Rasters"
AOI = DATA / "AOI_Boundary.gpkg"


def find_tool(name: str) -> str:
    from_path = shutil.which(name)
    candidates = [Path(from_path)] if from_path else []
    candidates.extend(
        [
            Path(r"C:\Program Files\QGIS 3.44.9\bin") / f"{name}.exe",
            Path(r"C:\Program Files\QGIS 4.0.1\bin") / f"{name}.exe",
            Path(r"C:\OSGeo4W\bin") / f"{name}.exe",
        ]
    )
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    raise SystemExit(f"{name} bulunamadi. QGIS/GDAL kurulum yolunu kontrol edin.")


OGR2OGR = find_tool("ogr2ogr")
GDALWARP = find_tool("gdalwarp")
GDALBUILDVRT = find_tool("gdalbuildvrt")


def run(command: list[str], label: str) -> None:
    print(label, flush=True)
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise SystemExit(f"{label} basarisiz oldu:\n{details}")
    if completed.stdout.strip():
        print(completed.stdout.strip(), flush=True)
    if completed.stderr.strip():
        print(completed.stderr.strip(), flush=True)


def ensure_dirs() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    RASTERS.mkdir(parents=True, exist_ok=True)


def export_vector(src: Path, layer: str, out_layer: str, overwrite_gdb: bool = False) -> None:
    command = [
        OGR2OGR,
        "-f",
        "OpenFileGDB",
        "-overwrite" if overwrite_gdb else "-update",
        "-skipfailures",
        str(GDB),
        str(src),
        layer,
        "-nln",
        out_layer,
        "-clipsrc",
        str(AOI),
        "-clipsrclayer",
        "AOI_Boundary",
        "-nlt",
        "PROMOTE_TO_MULTI",
    ]
    run(command, f"{out_layer} raw_data.gdb icine aktariliyor...")


def clip_raster(src: Path, out_name: str, resampling: str) -> None:
    out_file = RASTERS / out_name
    command = [
        GDALWARP,
        "-overwrite",
        "-cutline",
        str(AOI),
        "-cl",
        "AOI_Boundary",
        "-crop_to_cutline",
        "-multi",
        "-wo",
        "NUM_THREADS=ALL_CPUS",
        "-co",
        "COMPRESS=DEFLATE",
        "-r",
        resampling,
        str(src),
        str(out_file),
    ]
    run(command, f"{out_name} AOI'ye gore kesiliyor...")


def mosaic_and_clip_worldcover() -> None:
    tiles = sorted((DATA / "_cache" / "worldcover").glob("ESA_WorldCover_10m_2021_v200_*_Map.tif"))
    if not tiles:
        raise SystemExit("ESA WorldCover tile dosyalari bulunamadi.")
    vrt = RAW / "ESA_WorldCover_2021_v200_source_tiles.vrt"
    run([GDALBUILDVRT, "-overwrite", str(vrt), *map(str, tiles)], "ESA WorldCover VRT hazirlaniyor...")
    clip_raster(vrt, "ESA_WorldCover_2021_v200_AOI.tif", "near")


def write_docx(path: Path, rows: list[dict[str, str]]) -> None:
    def paragraph(text: str) -> str:
        return f"<w:p><w:r><w:t>{html.escape(text)}</w:t></w:r></w:p>"

    headers = ["Data", "Source", "Download link / local file", "Description"]
    header_xml = "".join(f"<w:tc>{paragraph(h)}</w:tc>" for h in headers)
    row_xml = []
    for row in rows:
        cells = "".join(f"<w:tc>{paragraph(row[h])}</w:tc>" for h in headers)
        row_xml.append(f"<w:tr>{cells}</w:tr>")

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + paragraph("Semender Raw Data Sources")
        + paragraph("All listed datasets were clipped to the Semender study area. Vector outputs are in Raw_Data/raw_data.gdb; raster outputs are in Raw_Data/Rasters because GDAL cannot write raster datasets into FileGDB without ArcGIS/arcpy.")
        + f'<w:tbl><w:tr>{header_xml}</w:tr>{"".join(row_xml)}</w:tbl>'
        + '<w:sectPr/></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document)


def source_rows() -> list[dict[str, str]]:
    return [
        {
            "Data": "AOI_Boundary",
            "Source": "Semender project study area",
            "Download link / local file": str(AOI),
            "Description": "Project boundary used to clip all raw source datasets.",
        },
        {
            "Data": "WorldClim Elevation",
            "Source": "WorldClim v2.1 elevation, derived from SRTM",
            "Download link / local file": "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_elev.zip",
            "Description": "Raw 2.5 arc-minute elevation GeoTIFF clipped to AOI as WorldClim_Elevation_AOI.tif.",
        },
        {
            "Data": "WorldClim BIO1 Temperature",
            "Source": "WorldClim v2.1 bioclimatic variables",
            "Download link / local file": "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_bio.zip",
            "Description": "BIO1 annual mean temperature clipped to AOI as WorldClim_BIO1_Temperature_AOI.tif.",
        },
        {
            "Data": "WorldClim BIO12 Rain",
            "Source": "WorldClim v2.1 bioclimatic variables",
            "Download link / local file": "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_bio.zip",
            "Description": "BIO12 annual precipitation clipped to AOI as WorldClim_BIO12_Rain_AOI.tif.",
        },
        {
            "Data": "ESA WorldCover 2021 v200",
            "Source": "ESA WorldCover S3 public bucket",
            "Download link / local file": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/",
            "Description": "10 m source tiles covering the AOI, mosaicked virtually and clipped as ESA_WorldCover_2021_v200_AOI.tif.",
        },
        {
            "Data": "Natural Earth populated places",
            "Source": "Natural Earth 1:10m cultural vectors",
            "Download link / local file": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_populated_places.zip",
            "Description": "Settlement point features clipped into raw_data.gdb/NaturalEarth_PopulatedPlaces.",
        },
        {
            "Data": "Natural Earth roads",
            "Source": "Natural Earth 1:10m cultural vectors",
            "Download link / local file": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_roads.zip",
            "Description": "Road line features clipped into raw_data.gdb/NaturalEarth_Roads.",
        },
        {
            "Data": "Natural Earth rivers and lake centerlines",
            "Source": "Natural Earth 1:10m physical vectors",
            "Download link / local file": "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_rivers_lake_centerlines.zip",
            "Description": "River/lake centerline features clipped into raw_data.gdb/NaturalEarth_Rivers_LakeCenterlines.",
        },
        {
            "Data": "OpenStreetMap Asia PBF",
            "Source": "OpenStreetMap, Geofabrik regional extract",
            "Download link / local file": "https://download.geofabrik.de/asia.html",
            "Description": "OSM PBF extract clipped to AOI. Standard OSM points, lines, multilinestrings, multipolygons and highway lines are saved in raw_data.gdb.",
        },
        {
            "Data": "HydroRIVERS waterways",
            "Source": "HydroSHEDS HydroRIVERS",
            "Download link / local file": "https://www.hydrosheds.org/products/hydrorivers",
            "Description": "Existing HydroRIVERS shapefile in Data clipped into raw_data.gdb/HydroRIVERS_Waterways.",
        },
    ]


def main() -> int:
    ensure_dirs()
    if not AOI.exists():
        raise SystemExit(f"AOI bulunamadi: {AOI}")

    export_vector(AOI, "AOI_Boundary", "AOI_Boundary", overwrite_gdb=True)
    clip_raster(DATA / "_cache" / "wc2.1_2.5m_elev" / "wc2.1_2.5m_elev.tif", "WorldClim_Elevation_AOI.tif", "bilinear")
    clip_raster(DATA / "_cache" / "wc2.1_2.5m_bio" / "wc2.1_2.5m_bio_1.tif", "WorldClim_BIO1_Temperature_AOI.tif", "bilinear")
    clip_raster(DATA / "_cache" / "wc2.1_2.5m_bio" / "wc2.1_2.5m_bio_12.tif", "WorldClim_BIO12_Rain_AOI.tif", "bilinear")
    mosaic_and_clip_worldcover()

    ne = DATA / "_cache" / "naturalearth"
    export_vector(ne / "ne_10m_populated_places" / "ne_10m_populated_places.shp", "ne_10m_populated_places", "NaturalEarth_PopulatedPlaces")
    export_vector(ne / "ne_10m_roads" / "ne_10m_roads.shp", "ne_10m_roads", "NaturalEarth_Roads")
    export_vector(ne / "ne_10m_rivers_lake_centerlines" / "ne_10m_rivers_lake_centerlines.shp", "ne_10m_rivers_lake_centerlines", "NaturalEarth_Rivers_LakeCenterlines")

    hydro = DATA / "Waterways_HydroRIVERS.shp"
    if hydro.exists():
        export_vector(hydro, "Waterways_HydroRIVERS", "HydroRIVERS_Waterways")

    osm = DATA / "_osm_clip_work" / "osm_studyarea_all.gpkg"
    if osm.exists():
        for source_layer, out_layer in [
            ("points", "OSM_Points_AllTags"),
            ("lines", "OSM_Lines_AllTags"),
            ("multilinestrings", "OSM_Multilinestrings_AllTags"),
            ("multipolygons", "OSM_Multipolygons_AllTags"),
        ]:
            export_vector(osm, source_layer, out_layer)

    roads = DATA / "_roadextract_work" / "roads_osm_clipped.gpkg"
    if roads.exists():
        export_vector(roads, "Roads_OSM", "OSM_Roads_Highway")

    write_docx(RAW / "data_sources.docx", source_rows())
    print(f"Tamamlandi: {RAW}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
