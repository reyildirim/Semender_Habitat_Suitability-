"""
Extract OSM road lines from a local PBF with QGIS/GDAL and clip them to the
Semender study area.

Run:
    "C:\\Program Files\\QGIS 3.44.9\\bin\\python-qgis-ltr.bat" roadextract.py

Input PBF (--pbf):
    An OpenStreetMap regional extract in .osm.pbf format, downloaded from
    https://download.geofabrik.de/ (the Asia extract covers the study area).

Clip layer (--gdb, --aoi-name):
    The study-area polygon, held in a file geodatabase.

Output:
    A road line feature class in that geodatabase (--output-name).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_AOI_NAME = "GADM_2_StudyArea_Merge"
DEFAULT_OUTPUT_NAME = "Roads_OSM"
DEFAULT_WORK_DIR = Path(__file__).resolve().parent / "Data" / "_roadextract_work"
QGIS_OGR2OGR = Path(r"C:\Program Files\QGIS 3.44.9\bin\ogr2ogr.exe")


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

    raise SystemExit(
        "ogr2ogr bulunamadi. QGIS kurulumundaki ogr2ogr.exe yolunu --ogr2ogr ile verin."
    )


def run(command: list[str], label: str) -> None:
    print(label, flush=True)
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise SystemExit(f"{label} basarisiz oldu:\n{details}")
    if completed.stdout.strip():
        print(completed.stdout.strip(), flush=True)


def export_aoi_to_wgs84(ogr2ogr: str, gdb: Path, aoi_name: str, out_gpkg: Path) -> Path:
    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
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
        raise SystemExit(
            "QGIS Python ortaminda osgeo/ogr bulunamadi. Scripti python-qgis-ltr.bat ile calistirin."
        ) from exc

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
        raise SystemExit(f"Katman acilamadi: {dataset}")
    layer = source.GetLayerByName(layer_name)
    if layer is None:
        raise SystemExit(f"Katman bulunamadi: {dataset} / {layer_name}")
    return layer.GetFeatureCount()


def extract_roads_to_gpkg(
    ogr2ogr: str,
    pbf: Path,
    clip_gpkg: Path,
    out_gpkg: Path,
    output_name: str,
) -> Path:
    west, south, east, north = layer_extent(clip_gpkg, "aoi_wgs84")
    print(
        "AOI WGS84 extent: "
        f"{west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}",
        flush=True,
    )

    if out_gpkg.exists():
        out_gpkg.unlink()

    run(
        [
            ogr2ogr,
            "--config",
            "OGR_INTERLEAVED_READING",
            "YES",
            "-f",
            "GPKG",
            str(out_gpkg),
            str(pbf),
            "lines",
            "-spat",
            str(west),
            str(south),
            str(east),
            str(north),
            "-where",
            "highway IS NOT NULL",
            "-clipsrc",
            str(clip_gpkg),
            "-clipsrclayer",
            "aoi_wgs84",
            "-nln",
            output_name,
            "-nlt",
            "PROMOTE_TO_MULTI",
            "-overwrite",
        ],
        f"OSM yollar gecici GeoPackage icine kirpiliyor: {out_gpkg.name}",
    )
    return out_gpkg


def copy_roads_to_gdb(ogr2ogr: str, roads_gpkg: Path, gdb: Path, output_name: str) -> None:
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
            str(roads_gpkg),
            output_name,
            "-nln",
            output_name,
            "-nlt",
            "PROMOTE_TO_MULTI",
        ],
        f"Gecici yollar {gdb.name}\\{output_name} katmanina aktariliyor...",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QGIS/GDAL ile OSM PBF yol cizgilerini Semender AOI icine kirpar."
    )
    parser.add_argument("--pbf", type=Path, required=True,
                        help="OpenStreetMap .osm.pbf extract (download from geofabrik.de)")
    parser.add_argument("--gdb", type=Path, required=True,
                        help="File geodatabase holding the study-area polygon")
    parser.add_argument("--aoi-name", default=DEFAULT_AOI_NAME)
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
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
    clip_gpkg = export_aoi_to_wgs84(
        ogr2ogr,
        args.gdb,
        args.aoi_name,
        args.work_dir / "aoi_wgs84.gpkg",
    )
    roads_gpkg = extract_roads_to_gpkg(
        ogr2ogr,
        args.pbf,
        clip_gpkg,
        args.work_dir / "roads_osm_clipped.gpkg",
        args.output_name,
    )
    clipped_count = feature_count(roads_gpkg, args.output_name)
    print(f"Gecici kirpilmis yol sayisi: {clipped_count}", flush=True)
    if clipped_count == 0:
        raise SystemExit("AOI icinde OSM road/highway cizgisi bulunamadi.")

    copy_roads_to_gdb(ogr2ogr, roads_gpkg, args.gdb, args.output_name)
    final_count = feature_count(args.gdb, args.output_name)
    print(f"Export tamamlandi: {args.gdb}\\{args.output_name} ({final_count} cizgi)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
