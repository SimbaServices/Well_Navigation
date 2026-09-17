import importlib.util

for name in ("shapely", "shapefile", "pyproj", "wellnav.pipelines"):
    print(name, importlib.util.find_spec(name) is not None)
