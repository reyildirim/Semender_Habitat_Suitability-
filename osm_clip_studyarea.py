"""
Clip all standard OSM PBF vector layers to the Semender study area with QGIS/GDAL.

Run:
    "C:\\Program Files\\QGIS 3.44.9\\bin\\python-qgis-ltr.bat" osm_clip_studyarea.py

Input (--pbf):
    An OpenStreetMap regional extract in .osm.pbf format, downloaded from
    https://download.geofabrik.de/ (the Asia extract covers the study area).

Clip layer (--gdb, --aoi-name):
    The study-area polygon, held in a file geodatabase.

Outputs in that geodatabase:
    OSM_Points
    OSM_Lines
    OSM_Multilinestrings
    OSM_Multipolygons

No thematic filter is applied. OSM tags are preserved in the all_tags field.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_AOI_NAME = "GADM_2_StudyArea_Merge"
DEFAULT_WORK_DIR = Path(__file__).resolve().parent / "Data" / "_osm_clip_work"
QGIS_OGR2OGR = Path(r"C:\Program Files\QGIS 3.44.9\bin\ogr2ogr.exe")

OSM_LAYERS = {
    "points": "OSM_Points",
    "lines": "OSM_Lines",
    "multilinestrings": "OSM_Multilinestrings",
    "multipolygons": "OSM_Multipolygons",
}

OSMCONF = """# Custom OSM import configuration for Semender clipping.
[general]
closed_ways_are_polygons=aeroway,amenity,boundary,building,craft,geological,historic,landuse,leisure,military,natural,office,place,shop,sport,tourism,highway=platform,public_transport=platform
report_all_tags=yes
tags_format=json

[points]
osm_id=yes
osm_version=yes
osm_timestamp=yes
osm_uid=yes
osm_user=yes
osm_changeset=yes
attributes=name,barrier,highway,ref,address,is_in,place,man_made
unsignificant=created_by,converted_by,source,time,ele,attribution
all_tags=yes

[lines]
osm_id=yes
osm_version=yes
osm_timestamp=yes
osm_uid=yes
osm_user=yes
osm_changeset=yes
attributes=name,highway,waterway,aerialway,barrier,man_made,railway,ref,route,service,surface,tracktype,access,bridge,tunnel,layer
computed_attributes=z_order
z_order_type=Integer
z_order_sql="SELECT (CASE [highway] WHEN 'minor' THEN 3 WHEN 'road' THEN 3 WHEN 'unclassified' THEN 3 WHEN 'residential' THEN 3 WHEN 'tertiary_link' THEN 4 WHEN 'tertiary' THEN 4 WHEN 'secondary_link' THEN 6 WHEN 'secondary' THEN 6 WHEN 'primary_link' THEN 7 WHEN 'primary' THEN 7 WHEN 'trunk_link' THEN 8 WHEN 'trunk' THEN 8 WHEN 'motorway_link' THEN 9 WHEN 'motorway' THEN 9 ELSE 0 END) + (CASE WHEN [bridge] IN ('yes', 'true', '1') THEN 10 ELSE 0 END) + (CASE WHEN [tunnel] IN ('yes', 'true', '1') THEN -10 ELSE 0 END) + (CASE WHEN [railway] IS NOT NULL THEN 5 ELSE 0 END) + (CASE WHEN [layer] IS NOT NULL THEN 10 * CAST([layer] AS INTEGER) ELSE 0 END)"
all_tags=yes

[multipolygons]
osm_id=yes
osm_version=yes
osm_timestamp=yes
osm_uid=yes
osm_user=yes
osm_changeset=yes
attributes=name,type,aeroway,amenity,admin_level,barrier,boundary,building,craft,geological,historic,land_area,landuse,leisure,man_made,military,natural,office,place,shop,sport,tourism
all_tags=yes

[multilinestrings]
osm_id=yes
osm_version=yes
osm_timestamp=yes
osm_uid=yes
osm_user=yes
osm_changeset=yes
attributes=name,type,route,network,operator,ref
all_tags=yes

[other_relations]
osm_id=yes
osm_version=yes
osm_timestamp=yes
osm_uid=yes
osm_user=yes
osm_changeset=yes
attributes=name,type
all_tags=yes
"""


def find_ogr2ogr(explicit_path: str | None = None) -> str:
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    from_path = shutil.which("ogr2ogr")
    if from_path:
        candidates.append(Path(from_path))

    candidates.extend(
        [
            QGIS_OGR2OGR,
            Path(r"C:\Program Files\QGIS 4.0.1\bin\ogr2ogr.exe"),
            Path(r"C:\Program Files\QGIS 3.40.0\bin\ogr2ogr.exe"),
            Path(r"C:\Program Files\QGIS 3.38.0\bin\ogr2ogr.exe"),
            Path(r"C:\Program Files\QGIS 3.36.0\bin\ogr2ogr.exe"),
            Path(r"C:\OSGeo4W\bin\ogr2ogr.exe"),
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise SystemExit("ogr2ogr bulunamadi. QGIS ogr2ogr.exe yolunu --ogr2ogr ile verin.")


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


def write_osmconf(work_dir: Path) -> Path:
    config = work_dir / "osmconf_all_tags.ini"
    config.write_text(OSMCONF, encoding="utf-8")
    return config


def export_aoi_to_wgs84(ogr2ogr: str, gdb: Path, aoi_name: str, out_gpkg: Path) -> Path:
    if out_gpkg.exists():
        out_gpkg.unlink()
    run(
        [
            ogr2ogr,
            "-f",
            "GPKG",
            str(out_gpkg),
            str(gdb),
            aoi_name,
            "-nln",
            "aoi_wgs84",
            "-t_srs",
            "EPSG:4326",
            "-overwrite",
        ],
        "AOI WGS84 gecici katmana aktariliyor...",
    )
    return out_gpkg


def layer_extent(dataset: Path, layer_name: str) -> tuple[float, float, float, float]:
    try:
        from osgeo import ogr
    except ImportError as exc:
        raise SystemExit("Scripti QGIS Python ile calistirin: python-qgis-ltr.bat") from exc

    source = ogr.Open(str(dataset))
    if source is None:
        raise SystemExit(f"Katman acilamadi: {dataset}")
    layer = source.GetLayerByName(layer_name)
    if layer is None:
        raise SystemExit(f"Katman bulunamadi: {dataset} / {layer_name}")
    minx, maxx, miny, maxy = layer.GetExtent()
    return minx, miny, maxx, maxy


def feature_count(dataset: Path, layer_name: str) -> int:
    from osgeo import ogr

    source = ogr.Open(str(dataset))
    if source is None:
        return -1
    layer = source.GetLayerByName(layer_name)
    if layer is None:
        return -1
    return layer.GetFeatureCount()


def clip_osm_to_gpkg(
    ogr2ogr: str,
    pbf: Path,
    clip_gpkg: Path,
    osmconf: Path,
    out_gpkg: Path,
) -> Path:
    west, south, east, north = layer_extent(clip_gpkg, "aoi_wgs84")
    print(
        "AOI WGS84 extent: "
        f"{west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}",
        flush=True,
    )

    if out_gpkg.exists():
        out_gpkg.unlink()

    command = [
        ogr2ogr,
        "--config",
        "OGR_INTERLEAVED_READING",
        "YES",
        "-f",
        "GPKG",
        str(out_gpkg),
        str(pbf),
        *OSM_LAYERS.keys(),
        "-oo",
        f"CONFIG_FILE={osmconf}",
        "-spat",
        str(west),
        str(south),
        str(east),
        str(north),
        "-clipsrc",
        str(clip_gpkg),
        "-clipsrclayer",
        "aoi_wgs84",
        "-skipfailures",
        "-overwrite",
    ]
    run(command, f"Asia PBF tum standart OSM katmanlariyla kirpiliyor: {out_gpkg.name}")
    return out_gpkg


def copy_layers_to_gdb(ogr2ogr: str, clipped_gpkg: Path, gdb: Path) -> dict[str, int]:
    counts = {}
    for source_layer, output_layer in OSM_LAYERS.items():
        count = feature_count(clipped_gpkg, source_layer)
        counts[output_layer] = count
        if count <= 0:
            print(f"{source_layer}: kayit yok, GDB aktarimi atlandi.", flush=True)
            continue

        run(
            [
                ogr2ogr,
                "-f",
                "OpenFileGDB",
                "-update",
                "-overwrite",
                "-gt",
                "0",
                str(gdb),
                str(clipped_gpkg),
                source_layer,
                "-nln",
                output_layer,
                "-skipfailures",
            ],
            f"{source_layer} -> {gdb.name}\\{output_layer} aktariliyor...",
        )
        counts[output_layer] = feature_count(gdb, output_layer)
    return counts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QGIS/GDAL ile Asia OSM PBF verisini Semender AOI ile kirpar."
    )
    parser.add_argument("--pbf", type=Path, required=True,
                        help="OpenStreetMap .osm.pbf extract (download from geofabrik.de)")
    parser.add_argument("--gdb", type=Path, required=True,
                        help="File geodatabase holding the study-area polygon")
    parser.add_argument("--aoi-name", default=DEFAULT_AOI_NAME)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--ogr2ogr", help="QGIS ogr2ogr.exe icin tam yol")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.pbf.exists():
        raise SystemExit(f"PBF bulunamadi: {args.pbf}")
    if not args.gdb.exists():
        raise SystemExit(f"Geodatabase bulunamadi: {args.gdb}")

    args.work_dir.mkdir(parents=True, exist_ok=True)
    ogr2ogr = find_ogr2ogr(args.ogr2ogr)
    osmconf = write_osmconf(args.work_dir)
    clip_gpkg = export_aoi_to_wgs84(
        ogr2ogr,
        args.gdb,
        args.aoi_name,
        args.work_dir / "aoi_wgs84.gpkg",
    )
    clipped_gpkg = clip_osm_to_gpkg(
        ogr2ogr,
        args.pbf,
        clip_gpkg,
        osmconf,
        args.work_dir / "osm_studyarea_all.gpkg",
    )
    counts = copy_layers_to_gdb(ogr2ogr, clipped_gpkg, args.gdb)

    print("Export tamamlandi:", flush=True)
    for layer, count in counts.items():
        print(f"  {args.gdb}\\{layer}: {count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
