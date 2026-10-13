"""
Prepare open-source environmental layers for salamander habitat analysis.

The script creates ArcGIS-ready GeoTIFF layers for eastern Turkey and western
Georgia. Most layers come from Google Earth Engine (GEE). Road distance is
created locally from OpenStreetMap because the official GEE catalog does not
provide a current global road layer suitable for this region.

Required GEE layers:
    Elevation   NASADEM 30 m
    Temperature WorldClim BIO01, annual mean temperature in degree C
    Rain        WorldClim BIO12, annual precipitation in mm
    CityDist    GHSL built-up surface distance in meters
    WaterDist   JRC Global Surface Water distance in meters
    LandCover   ESA WorldCover 2021 class codes
    Slope       Derived from NASADEM
    Aspect      Derived from NASADEM

Typical use:
    python semender.py gee --task-mode drive

After downloading the exported Elevation.tif from Drive, create RoadDist:
    python semender.py road-dist --template outputs/Elevation.tif

For small test areas only, direct download can be used:
    python semender.py gee --task-mode direct --scale 500
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_BOUNDS = (35.5, 36.0, 46.8, 43.8)  # west, south, east, north
DEFAULT_SCALE = 100
DEFAULT_CRS = "EPSG:3857"
DEFAULT_MAX_DISTANCE = 100_000
DEFAULT_AOI = Path(__file__).resolve().parent / "aoi" / "AOI_Boundary.gpkg"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "Data"


@dataclass(frozen=True)
class LayerSpec:
    name: str
    image: object


def import_ee():
    try:
        import ee
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: earthengine-api. Install with "
            "`pip install earthengine-api geemap`."
        ) from exc
    return ee


def init_ee(project: str | None = None):
    ee = import_ee()
    try:
        ee.Initialize(project=project)
    except Exception:
        print("Earth Engine login is required. Opening authentication flow...")
        ee.Authenticate()
        ee.Initialize(project=project)
    return ee


def make_aoi(ee, bounds: tuple[float, float, float, float]):
    west, south, east, north = bounds
    return ee.Geometry.Rectangle([west, south, east, north], proj="EPSG:4326", geodesic=False)


def distance_to_mask(ee, mask, name: str, max_distance: int):
    """Distance in meters to non-zero pixels in a binary mask."""
    kernel = ee.Kernel.euclidean(max_distance, "meters")
    return (
        mask.selfMask()
        .distance(kernel, False)
        .unmask(max_distance)
        .toFloat()
        .rename(name)
    )


def build_gee_layers(ee, aoi, max_distance: int) -> list[LayerSpec]:
    dem = (
        ee.Image("NASA/NASADEM_HGT/001")
        .select("elevation")
        .toFloat()
        .rename("Elevation")
        .clip(aoi)
    )

    terrain = ee.Terrain.products(dem)
    slope = terrain.select("slope").toFloat().rename("Slope").clip(aoi)
    aspect = terrain.select("aspect").toFloat().rename("Aspect").clip(aoi)

    worldclim = ee.Image("WORLDCLIM/V1/BIO").clip(aoi)
    temperature = worldclim.select("bio01").multiply(0.1).toFloat().rename("Temperature")
    rain = worldclim.select("bio12").toFloat().rename("Rain")

    landcover = (
        ee.ImageCollection("ESA/WorldCover/v200")
        .first()
        .select("Map")
        .toInt16()
        .rename("LandCover")
        .clip(aoi)
    )

    water_mask = (
        ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
        .select("max_extent")
        .unmask(0)
        .eq(1)
        .clip(aoi)
    )
    water_dist = distance_to_mask(ee, water_mask, "WaterDist", max_distance).clip(aoi)

    built_surface = (
        ee.Image("JRC/GHSL/P2023A/GHS_BUILT_S/2020")
        .select("built_surface")
        .clip(aoi)
    )
    city_mask = built_surface.gte(2500)
    city_dist = distance_to_mask(ee, city_mask, "CityDist", max_distance).clip(aoi)

    return [
        LayerSpec("Elevation", dem),
        LayerSpec("Temperature", temperature),
        LayerSpec("Rain", rain),
        LayerSpec("CityDist", city_dist),
        LayerSpec("WaterDist", water_dist),
        LayerSpec("RoadDist", None),
        LayerSpec("LandCover", landcover),
        LayerSpec("Slope", slope),
        LayerSpec("Aspect", aspect),
    ]


def export_gee_layers(
    ee,
    layers: Iterable[LayerSpec],
    aoi,
    out_dir: Path,
    task_mode: str,
    scale: int,
    crs: str,
    drive_folder: str,
):
    out_dir.mkdir(parents=True, exist_ok=True)
    region = aoi.bounds().getInfo()["coordinates"]

    for layer in layers:
        if layer.image is None:
            continue

        if task_mode == "drive":
            task = ee.batch.Export.image.toDrive(
                image=layer.image,
                description=f"semender_{layer.name}",
                folder=drive_folder,
                fileNamePrefix=layer.name,
                region=region,
                scale=scale,
                crs=crs,
                maxPixels=1e13,
                fileFormat="GeoTIFF",
                formatOptions={"cloudOptimized": True},
            )
            task.start()
            print(f"Started Drive export: {layer.name} -> Drive/{drive_folder}/{layer.name}.tif")
        else:
            try:
                import geemap
            except ImportError as exc:
                raise SystemExit(
                    "Missing dependency: geemap. Install with `pip install geemap` "
                    "or use `--task-mode drive`."
                ) from exc

            filename = out_dir / f"{layer.name}.tif"
            print(f"Downloading {layer.name} -> {filename}")
            geemap.ee_export_image(
                layer.image,
                filename=str(filename),
                scale=scale,
                crs=crs,
                region=aoi,
                file_per_band=False,
            )


def road_filter() -> str:
    road_classes = [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
    ]
    values = "|".join(road_classes)
    return f'["highway"~"{values}"]'


def download_osm_roads(bounds: tuple[float, float, float, float]):
    try:
        import geopandas as gpd
        import inspect
        import osmnx as ox
    except ImportError as exc:
        raise SystemExit(
            "Missing dependencies for RoadDist. Install with "
            "`pip install osmnx geopandas rasterio scipy`."
        ) from exc

    west, south, east, north = bounds
    ox.settings.use_cache = True
    ox.settings.log_console = True

    print("Downloading OpenStreetMap roads. This can take a while for the full AOI.")
    signature = inspect.signature(ox.graph_from_bbox)
    first_param = next(iter(signature.parameters.values()))
    if first_param.name == "bbox":
        graph = ox.graph_from_bbox(
            (west, south, east, north),
            simplify=True,
            retain_all=True,
            custom_filter=road_filter(),
        )
    else:
        graph = ox.graph_from_bbox(
            north,
            south,
            east,
            west,
            simplify=True,
            retain_all=True,
            custom_filter=road_filter(),
        )

    _, edges = ox.graph_to_gdfs(graph)
    roads = gpd.GeoDataFrame(edges[["geometry"]], geometry="geometry", crs="EPSG:4326")
    roads = roads[~roads.geometry.is_empty & roads.geometry.notna()]
    if roads.empty:
        raise SystemExit("No OSM roads were downloaded for the selected AOI.")
    return roads


def write_road_distance(
    bounds: tuple[float, float, float, float],
    template: Path,
    out_file: Path,
    max_distance: int,
):
    try:
        import numpy as np
        import rasterio
        from rasterio.features import rasterize
        from scipy.ndimage import distance_transform_edt
    except ImportError as exc:
        raise SystemExit(
            "Missing dependencies for raster distance. Install with "
            "`pip install rasterio scipy numpy`."
        ) from exc

    roads = download_osm_roads(bounds)

    with rasterio.open(template) as src:
        meta = src.meta.copy()
        transform = src.transform
        shape = (src.height, src.width)
        target_crs = src.crs

    roads = roads.to_crs(target_crs)
    road_pixels = rasterize(
        ((geom, 1) for geom in roads.geometry),
        out_shape=shape,
        transform=transform,
        fill=0,
        all_touched=True,
        dtype="uint8",
    )

    pixel_width = abs(transform.a)
    pixel_height = abs(transform.e)
    distance = distance_transform_edt(road_pixels == 0, sampling=(pixel_height, pixel_width))
    distance = np.minimum(distance, max_distance).astype("float32")

    meta.update(count=1, dtype="float32", nodata=None, compress="deflate")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_file, "w", **meta) as dst:
        dst.write(distance, 1)

    print(f"Wrote RoadDist -> {out_file}")


def parse_bounds(value: str) -> tuple[float, float, float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("Bounds must be west,south,east,north")
    west, south, east, north = parts
    if not (west < east and south < north):
        raise argparse.ArgumentTypeError("Bounds must satisfy west < east and south < north")
    return west, south, east, north



def import_local_gis():
    try:
        import geopandas as gpd
        import numpy as np
        import rasterio
        from rasterio import features, mask, warp
        from scipy.ndimage import distance_transform_edt
        from shapely.geometry import LineString, Point, Polygon
    except ImportError as exc:
        raise SystemExit(
            "Missing local GIS dependency. Install geopandas, rasterio, scipy, numpy, "
            "and shapely in the Python environment used to run this command."
        ) from exc
    return gpd, np, rasterio, features, mask, warp, distance_transform_edt, LineString, Point, Polygon


def load_aoi(aoi_path: Path | str):
    gpd, *_ = import_local_gis()
    aoi_path = str(aoi_path)
    lower = aoi_path.lower()
    if ".gdb" in lower:
        marker = lower.index(".gdb") + 4
        gdb_path = aoi_path[:marker]
        layer_name = aoi_path[marker:].strip("\\/")
        aoi = gpd.read_file(gdb_path, layer=layer_name)
    else:
        aoi = gpd.read_file(aoi_path)
    if aoi.empty:
        raise SystemExit(f"AOI has no features: {aoi_path}")
    if aoi.crs is None:
        raise SystemExit(f"AOI has no CRS: {aoi_path}")
    return aoi.to_crs("EPSG:4326")


def download_file(url: str, out_file: Path) -> Path:
    import requests
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists() and out_file.stat().st_size > 0:
        print(f"Using cached download: {out_file}")
        return out_file
    tmp_file = out_file.with_suffix(out_file.suffix + ".part")
    print(f"Downloading {url}")
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with tmp_file.open("wb") as dst:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    dst.write(chunk)
    tmp_file.replace(out_file)
    return out_file


def write_template_from_aoi(aoi, out_file: Path, scale: int, crs: str):
    import math

    _, np, rasterio, _, _, warp, *_ = import_local_gis()
    from rasterio.transform import from_origin

    minx, miny, maxx, maxy = aoi.total_bounds
    left, bottom, right, top = warp.transform_bounds(
        "EPSG:4326", crs, minx, miny, maxx, maxy, densify_pts=21
    )
    width = math.ceil((right - left) / scale)
    height = math.ceil((top - bottom) / scale)
    dst_transform = from_origin(left, top, scale, scale)
    meta = {"driver":"GTiff","height":height,"width":width,"count":1,"dtype":"uint8","crs":crs,"transform":dst_transform,"nodata":0,"compress":"deflate","tiled":True,"blockxsize":256,"blockysize":256}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_file, "w", **meta) as dst:
        dst.write(np.ones((height, width), dtype="uint8"), 1)
    return out_file


def aoi_mask_array(aoi, template_file: Path):
    _, np, rasterio, features, *_ = import_local_gis()
    with rasterio.open(template_file) as template:
        data = aoi.to_crs(template.crs)
        return features.rasterize(
            ((geom, 1) for geom in data.geometry),
            out_shape=(template.height, template.width),
            transform=template.transform,
            fill=0,
            all_touched=True,
            dtype="uint8",
        ).astype(bool)


def mask_raster_to_aoi(raster_file: Path, aoi, template_file: Path):
    _, np, rasterio, *_ = import_local_gis()
    keep = aoi_mask_array(aoi, template_file)
    with rasterio.open(raster_file, "r+") as dst:
        data = dst.read(1)
        if np.issubdtype(data.dtype, np.floating):
            data = data.astype("float32", copy=False)
            data[~keep] = np.nan
            dst.nodata = np.nan
        else:
            data[~keep] = 0
            dst.nodata = 0
        dst.write(data, 1)


def clip_reproject_raster(src_file: Path, out_file: Path, aoi, template_file: Path):
    _, np, rasterio, _, mask, warp, *_ = import_local_gis()
    from shapely.geometry import mapping
    shapes = [mapping(geom) for geom in aoi.geometry]
    with rasterio.open(template_file) as template, rasterio.open(src_file) as src:
        clipped, clipped_transform = mask.mask(src, shapes, crop=True, indexes=1)
        destination = np.full((template.height, template.width), np.nan, dtype="float32")
        warp.reproject(source=clipped.astype("float32"), destination=destination, src_transform=clipped_transform, src_crs=src.crs or "EPSG:4326", src_nodata=src.nodata, dst_transform=template.transform, dst_crs=template.crs, dst_nodata=np.nan, resampling=warp.Resampling.bilinear)
        meta = template.meta.copy(); meta.update(dtype="float32", nodata=np.nan, compress="deflate")
        with rasterio.open(out_file, "w", **meta) as dst:
            dst.write(destination, 1)
    mask_raster_to_aoi(out_file, aoi, template_file)


def extract_worldclim_layer(zip_file: Path, member_suffix: str, out_file: Path, aoi, template_file: Path):
    import zipfile
    extract_dir = zip_file.parent / zip_file.stem
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_file) as archive:
        member = next((name for name in archive.namelist() if name.endswith(member_suffix)), None)
        if member is None:
            raise SystemExit(f"WorldClim member not found in {zip_file}: {member_suffix}")
        extracted = extract_dir / Path(member).name
        if not extracted.exists():
            archive.extract(member, extract_dir)
    clip_reproject_raster(extracted, out_file, aoi, template_file)
    return extracted


def overpass_elements_to_gdf(data: dict, default_kind: str):
    gpd, _, _, _, _, _, _, LineString, Point, Polygon = import_local_gis()
    records = []
    for element in data.get("elements", []):
        tags = element.get("tags", {}) or {}
        geom = None
        if element.get("type") == "node" and "lat" in element and "lon" in element:
            geom = Point(element["lon"], element["lat"])
        elif "geometry" in element:
            coords = [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
            if len(coords) >= 4 and coords[0] == coords[-1] and default_kind != "road":
                geom = Polygon(coords)
            elif len(coords) >= 2:
                geom = LineString(coords)
        if geom is None or geom.is_empty:
            continue
        records.append({"osm_id": element.get("id"), "osm_type": element.get("type"), "kind": default_kind, **tags, "geometry": geom})
    if not records:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def iter_bbox_tiles(bounds: tuple[float, float, float, float], step: float = 1.5):
    west, south, east, north = bounds
    y = south
    while y < north:
        x = west
        tile_north = min(y + step, north)
        while x < east:
            tile_east = min(x + step, east)
            yield (x, y, tile_east, tile_north)
            x = tile_east
        y = tile_north


def download_overpass(name: str, query_body: str, bounds: tuple[float, float, float, float], out_file: Path, tile_step: float = 1.5, request_timeout: int = 180):
    import requests
    import time

    gpd, *_ = import_local_gis()
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    headers = {"User-Agent": "SemenderDataPrep/1.0 (local GIS data preparation)"}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    errors = []
    for tile in iter_bbox_tiles(bounds, step=tile_step):
        west, south, east, north = tile
        bbox = f"{south},{west},{north},{east}"
        query = f'''[out:json][timeout:{request_timeout}];
(
  {query_body.format(bbox=bbox)}
);
out geom;'''
        last_error = None
        for endpoint in endpoints:
            try:
                print(f"Downloading OSM {name}: {bbox} from {endpoint}")
                response = requests.post(endpoint, data={"data": query}, headers=headers, timeout=request_timeout + 30)
                if response.status_code != 200:
                    last_error = f"{endpoint} {response.status_code}: {response.text[:500]}"
                    continue
                gdf = overpass_elements_to_gdf(response.json(), name)
                if not gdf.empty:
                    frames.append(gdf)
                last_error = None
                time.sleep(1)
                break
            except Exception as exc:
                last_error = f"{endpoint}: {exc}"
        if last_error:
            errors.append(f"{bbox}: {last_error}")
    if errors:
        out_file.with_suffix(".error.txt").write_text("\n\n".join(errors), encoding="utf-8")
    if not frames:
        raise SystemExit(f"Overpass returned no features for {name}; see {out_file.with_suffix('.error.txt')}")
    import pandas as pd
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    if "osm_id" in gdf.columns:
        gdf = gdf.drop_duplicates(subset=["osm_type", "osm_id"])
    gdf.to_file(out_file, driver="GPKG")
    return gdf


def rasterize_distance(gdf, out_file: Path, template_file: Path, max_distance: int, aoi=None):
    _, np, rasterio, features, _, _, distance_transform_edt, *_ = import_local_gis()
    with rasterio.open(template_file) as template:
        meta = template.meta.copy(); target_crs = template.crs; target_transform = template.transform; shape = (template.height, template.width)
    data = gdf.to_crs(target_crs)
    data = data[~data.geometry.is_empty & data.geometry.notna()]
    pixels = features.rasterize(((geom, 1) for geom in data.geometry), out_shape=shape, transform=target_transform, fill=0, all_touched=True, dtype="uint8")
    pixel_width = abs(target_transform.a); pixel_height = abs(target_transform.e)
    dist = distance_transform_edt(pixels == 0, sampling=(pixel_height, pixel_width))
    dist = np.minimum(dist, max_distance).astype("float32")
    if aoi is not None:
        dist[~aoi_mask_array(aoi, template_file)] = np.nan
    meta.update(dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out_file, "w", **meta) as dst:
        dst.write(dist, 1)


def distance_to_landcover_class(landcover_file: Path, class_value: int, out_file: Path, max_distance: int, aoi=None, template_file: Path | None = None):
    _, np, rasterio, _, _, _, distance_transform_edt, *_ = import_local_gis()
    with rasterio.open(landcover_file) as src:
        landcover = src.read(1)
        meta = src.meta.copy()
        pixel_width = abs(src.transform.a)
        pixel_height = abs(src.transform.e)
    mask_pixels = landcover == class_value
    if not mask_pixels.any():
        raise SystemExit(f"LandCover class {class_value} was not found in {landcover_file}")
    dist = distance_transform_edt(~mask_pixels, sampling=(pixel_height, pixel_width))
    dist = np.minimum(dist, max_distance).astype("float32")
    if aoi is not None and template_file is not None:
        dist[~aoi_mask_array(aoi, template_file)] = np.nan
    meta.update(dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out_file, "w", **meta) as dst:
        dst.write(dist, 1)


def worldcover_tile_urls(bounds: tuple[float, float, float, float]) -> list[str]:
    import math
    west, south, east, north = bounds
    urls = []
    for lat in range(math.floor(south / 3) * 3, math.ceil(north / 3) * 3, 3):
        for lon in range(math.floor(west / 3) * 3, math.ceil(east / 3) * 3, 3):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{abs(lat):02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{abs(lon):03d}"
            urls.append("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/" f"ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif")
    return urls


def download_worldcover(aoi, template_file: Path, out_file: Path, cache_dir: Path):
    import requests
    _, np, rasterio, _, _, warp, *_ = import_local_gis()
    bounds = tuple(aoi.total_bounds)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tile_files = []
    for url in worldcover_tile_urls(bounds):
        head = requests.head(url, allow_redirects=True, timeout=30)
        if head.status_code == 200:
            tile_files.append(download_file(url, cache_dir / Path(url).name))
    if not tile_files:
        raise SystemExit("No ESA WorldCover tiles found for AOI.")
    with rasterio.open(template_file) as template:
        destination = np.zeros((template.height, template.width), dtype="uint8")
        for tile_file in tile_files:
            print(f"Resampling WorldCover tile: {tile_file.name}")
            with rasterio.open(tile_file) as src:
                tmp = np.zeros_like(destination)
                warp.reproject(source=rasterio.band(src, 1), destination=tmp, src_transform=src.transform, src_crs=src.crs, src_nodata=0, dst_transform=template.transform, dst_crs=template.crs, dst_nodata=0, resampling=warp.Resampling.nearest)
                destination = np.where((destination == 0) & (tmp != 0), tmp, destination)
        meta = template.meta.copy(); meta.update(dtype="uint8", nodata=0, compress="deflate")
        with rasterio.open(out_file, "w", **meta) as dst:
            dst.write(destination, 1)
    mask_raster_to_aoi(out_file, aoi, template_file)


def clip_gdf_to_aoi(gdf, aoi):
    clipped = gdf.to_crs(aoi.crs)
    clipped = clipped[~clipped.geometry.is_empty & clipped.geometry.notna()]
    if clipped.empty:
        return clipped
    try:
        clipped = clipped.clip(aoi)
    except Exception:
        clipped = clipped[clipped.intersects(aoi.unary_union)]
    clipped = clipped[~clipped.geometry.is_empty & clipped.geometry.notna()]
    return clipped


def download_natural_earth_layer(name: str, url: str, shp_name: str, aoi, out_file: Path, cache_dir: Path):
    import zipfile

    gpd, *_ = import_local_gis()
    zip_file = download_file(url, cache_dir / Path(url).name)
    extract_dir = cache_dir / Path(url).stem
    extract_dir.mkdir(parents=True, exist_ok=True)
    shp_file = extract_dir / shp_name
    with zipfile.ZipFile(zip_file) as archive:
        if not shp_file.exists():
            archive.extractall(extract_dir)
    data = gpd.read_file(shp_file).to_crs("EPSG:4326")
    data = clip_gdf_to_aoi(data, aoi)
    if data.empty:
        raise SystemExit(f"Natural Earth {name} returned no features inside AOI.")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    data.to_file(out_file, driver="GPKG")
    return data


def export_gpkg_layers_to_file_gdb(layer_files: dict[str, Path], out_gdb: Path):
    import subprocess
    import tempfile
    import textwrap

    arcgis_python = Path(r"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe")
    if not arcgis_python.exists():
        raise SystemExit(f"ArcGIS Pro Python was not found: {arcgis_python}")
    script = textwrap.dedent(
        """
        import arcpy
        import sys
        from pathlib import Path

        out_gdb = Path(sys.argv[1])
        out_dir = out_gdb.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        if not arcpy.Exists(str(out_gdb)):
            arcpy.management.CreateFileGDB(str(out_dir), out_gdb.name)
        arcpy.env.overwriteOutput = True
        args = sys.argv[2:]
        for i in range(0, len(args), 2):
            name = args[i]
            src_path = Path(args[i + 1])
            src = str(src_path / name) if src_path.suffix.lower() == ".gpkg" else str(src_path)
            dst = str(out_gdb / name)
            if arcpy.Exists(dst):
                arcpy.management.Delete(dst)
            arcpy.conversion.ExportFeatures(src, dst)
        """
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_file = Path(handle.name)
    args = [str(arcgis_python), str(script_file), str(out_gdb)]
    for name, path in layer_files.items():
        args.extend([name, str(path)])
    result = subprocess.run(args, capture_output=True, text=True)
    script_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise SystemExit(result.stdout + result.stderr)
    return out_gdb


def create_vector_layers(aoi, out_dir: Path, cache_dir: Path):
    vector_dir = out_dir / "_vectors"
    vector_dir.mkdir(parents=True, exist_ok=True)
    ne_cache = cache_dir / "naturalearth"

    settlements = download_natural_earth_layer(
        "populated places",
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_populated_places.zip",
        "ne_10m_populated_places.shp",
        aoi,
        vector_dir / "Settlements_NE.gpkg",
        ne_cache,
    )
    roads = download_natural_earth_layer(
        "roads",
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_roads.zip",
        "ne_10m_roads.shp",
        aoi,
        vector_dir / "Roads_NE.gpkg",
        ne_cache,
    )
    rivers = download_natural_earth_layer(
        "rivers and lake centerlines",
        "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_rivers_lake_centerlines.zip",
        "ne_10m_rivers_lake_centerlines.shp",
        aoi,
        vector_dir / "Rivers_NE.gpkg",
        ne_cache,
    )

    layer_files = {
        "Settlements_NE": vector_dir / "Settlements_NE.gpkg",
        "Roads_NE": vector_dir / "Roads_NE.gpkg",
        "Rivers_NE": vector_dir / "Rivers_NE.gpkg",
    }
    export_gpkg_layers_to_file_gdb(layer_files, out_dir / "data.gdb")
    return {
        "settlements_count": len(settlements),
        "roads_count": len(roads),
        "rivers_count": len(rivers),
    }



def write_docx_report(report_file: Path, rows: list[dict[str, str]]):
    import html
    import zipfile

    headers = ["Layer", "Source", "Resolution", "Output", "Specs"]
    header_cells = "".join(f"<w:tc><w:p><w:r><w:t>{h}</w:t></w:r></w:p></w:tc>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(
            f"<w:tc><w:p><w:r><w:t>{html.escape(str(row[h]))}</w:t></w:r></w:p></w:tc>"
            for h in headers
        )
        body_rows.append(f"<w:tr>{cells}</w:tr>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:r><w:t>Semender Data Report</w:t></w:r></w:p>'
        f'<w:tbl><w:tr>{header_cells}</w:tr>{"".join(body_rows)}</w:tbl>'
        '<w:sectPr/></w:body></w:document>'
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
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(report_file, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document)


def download_data_layers(aoi_path: Path, out_dir: Path, scale: int, crs: str, max_distance: int):
    _, np, rasterio, *_ = import_local_gis()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "_cache"
    aoi = load_aoi(aoi_path)
    bounds = tuple(aoi.total_bounds)
    aoi.to_file(out_dir / "AOI_Boundary.gpkg", driver="GPKG")
    template_file = write_template_from_aoi(aoi, out_dir / "_template.tif", scale, crs)
    bio_zip = download_file("https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_bio.zip", cache_dir / "wc2.1_2.5m_bio.zip")
    elev_zip = download_file("https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_elev.zip", cache_dir / "wc2.1_2.5m_elev.zip")
    extract_worldclim_layer(elev_zip, "wc2.1_2.5m_elev.tif", out_dir / "Elevation.tif", aoi, template_file)
    extract_worldclim_layer(bio_zip, "wc2.1_2.5m_bio_1.tif", out_dir / "Temperature.tif", aoi, template_file)
    extract_worldclim_layer(bio_zip, "wc2.1_2.5m_bio_12.tif", out_dir / "Rain.tif", aoi, template_file)
    download_worldcover(aoi, template_file, out_dir / "LandCover.tif", cache_dir / "worldcover")
    vector_counts = create_vector_layers(aoi, out_dir, cache_dir)
    with rasterio.open(out_dir / "Elevation.tif") as src:
        dem = src.read(1).astype("float32"); meta = src.meta.copy(); xres = abs(src.transform.a); yres = abs(src.transform.e)
    gy, gx = np.gradient(dem, yres, xres)
    slope = np.degrees(np.arctan(np.sqrt(gx * gx + gy * gy))).astype("float32")
    aspect = (90.0 - np.degrees(np.arctan2(gy, -gx))).astype("float32")
    aspect = np.where(aspect < 0, 360 + aspect, aspect).astype("float32")
    meta.update(dtype="float32", nodata=np.nan, compress="deflate")
    keep = aoi_mask_array(aoi, template_file)
    slope[~keep] = np.nan
    aspect[~keep] = np.nan
    with rasterio.open(out_dir / "Slope.tif", "w", **meta) as dst: dst.write(slope, 1)
    with rasterio.open(out_dir / "Aspect.tif", "w", **meta) as dst: dst.write(aspect, 1)
    rows = [
        {"Layer":"AOI_Boundary","Source":str(aoi_path),"Resolution":"Vector polygon","Output":"AOI_Boundary.gpkg","Specs":"GADM level-2 study area merge."},
        {"Layer":"Elevation","Source":"WorldClim v2.1 elevation, derived from SRTM","Resolution":f"2.5 arc-min source; {scale} m output","Output":"Elevation.tif","Specs":"Continuous elevation GeoTIFF."},
        {"Layer":"Temperature","Source":"WorldClim v2.1 BIO1","Resolution":f"2.5 arc-min source; {scale} m output","Output":"Temperature.tif","Specs":"Annual mean temperature bioclimatic variable."},
        {"Layer":"Rain","Source":"WorldClim v2.1 BIO12","Resolution":f"2.5 arc-min source; {scale} m output","Output":"Rain.tif","Specs":"Annual precipitation in mm."},
        {"Layer":"LandCover","Source":"ESA WorldCover 2021 v200","Resolution":f"10 m source; {scale} m output","Output":"LandCover.tif","Specs":"11 land-cover classes; nearest-neighbor resampling."},
        {"Layer":"Settlements_NE","Source":"Natural Earth 1:10m populated places","Resolution":"1:10m vector point features","Output":"data.gdb/Settlements_NE","Specs":f"Populated place points; {vector_counts['settlements_count']} features clipped to AOI."},
        {"Layer":"Rivers_NE","Source":"Natural Earth 1:10m rivers and lake centerlines","Resolution":"1:10m vector line features","Output":"data.gdb/Rivers_NE","Specs":f"River and lake centerlines; {vector_counts['rivers_count']} features clipped to AOI."},
        {"Layer":"Roads_NE","Source":"Natural Earth 1:10m roads","Resolution":"1:10m vector line features","Output":"data.gdb/Roads_NE","Specs":f"Road line features; {vector_counts['roads_count']} features clipped to AOI."},
        {"Layer":"Slope","Source":"Derived from Elevation.tif","Resolution":f"{scale} m output","Output":"Slope.tif","Specs":"Degrees, calculated from DEM gradient."},
        {"Layer":"Aspect","Source":"Derived from Elevation.tif","Resolution":f"{scale} m output","Output":"Aspect.tif","Specs":"Degrees clockwise from north."},
    ]
    write_docx_report(out_dir / "Data_Report.docx", rows)
    print(f"Data layers and report written to {out_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create open-source habitat layers for ArcGIS suitability analysis."
    )
    parser.add_argument(
        "--bounds",
        type=parse_bounds,
        default=DEFAULT_BOUNDS,
        help=(
            "AOI bounds as west,south,east,north in EPSG:4326. "
            f"Default: {','.join(map(str, DEFAULT_BOUNDS))}"
        ),
    )
    parser.add_argument("--max-distance", type=int, default=DEFAULT_MAX_DISTANCE)

    subparsers = parser.add_subparsers(dest="command", required=True)

    gee = subparsers.add_parser("gee", help="Export GEE raster layers.")
    gee.add_argument("--project", default=None, help="Google Cloud project for Earth Engine.")
    gee.add_argument("--out-dir", type=Path, default=Path("outputs"))
    gee.add_argument("--task-mode", choices=("drive", "direct"), default="drive")
    gee.add_argument("--drive-folder", default="semender_layers")
    gee.add_argument("--scale", type=int, default=DEFAULT_SCALE, help="Output pixel size in meters.")
    gee.add_argument("--crs", default=DEFAULT_CRS)

    road = subparsers.add_parser("road-dist", help="Create RoadDist.tif from OpenStreetMap.")
    road.add_argument("--template", type=Path, required=True, help="A GEE-exported raster template.")
    road.add_argument("--out-file", type=Path, default=Path("outputs") / "RoadDist.tif")

    data = subparsers.add_parser("download-data", help="Download open-source GIS layers clipped to the GDB study area and write Data_Report.docx.")
    data.add_argument("--aoi", type=Path, default=DEFAULT_AOI,
                      help="Study-area polygon. Any OGR-readable vector dataset.")
    data.add_argument("--out-dir", type=Path, default=DEFAULT_DATA_DIR)
    data.add_argument("--scale", type=int, default=DEFAULT_SCALE, help="Output pixel size in meters.")
    data.add_argument("--crs", default=DEFAULT_CRS)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "gee":
        ee = init_ee(args.project)
        aoi = make_aoi(ee, args.bounds)
        layers = build_gee_layers(ee, aoi, args.max_distance)
        export_gee_layers(
            ee=ee,
            layers=layers,
            aoi=aoi,
            out_dir=args.out_dir,
            task_mode=args.task_mode,
            scale=args.scale,
            crs=args.crs,
            drive_folder=args.drive_folder,
        )
        print("GEE exports submitted. Check the Earth Engine Tasks tab or Google Drive.")
        print("RoadDist is created separately with the `road-dist` command.")
        return 0

    if args.command == "road-dist":
        if not args.template.exists():
            raise SystemExit(f"Template raster not found: {args.template}")
        write_road_distance(
            bounds=args.bounds,
            template=args.template,
            out_file=args.out_file,
            max_distance=args.max_distance,
        )
        return 0

    if args.command == "download-data":
        download_data_layers(
            aoi_path=args.aoi,
            out_dir=args.out_dir,
            scale=args.scale,
            crs=args.crs,
            max_distance=args.max_distance,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
