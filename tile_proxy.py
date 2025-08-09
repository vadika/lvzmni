#!/usr/bin/env python3
"""
Tile proxy server for ZMNI meliorācijas datu serviss
Converts WGS84 z/x/y tile requests to LKS-92 (EPSG:3059) coordinate system
"""

import math
import requests
import logging
from flask import Flask, Response, abort
from pyproj import Transformer

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ZMNI Map Server configuration
BASE_URL = "https://lvmgeo.lvm.lv/proxy/D341478CE74F4F02B68607991448D499/CacheDinamic/ZMNI/MapServer/tile"
TILE_SIZE = 256

# Coordinate transformers
# WGS84 to LKS-92 (EPSG:3059)
transformer_to_lks92 = Transformer.from_crs("EPSG:4326", "EPSG:3059", always_xy=True)
# LKS-92 to WGS84
transformer_to_wgs84 = Transformer.from_crs("EPSG:3059", "EPSG:4326", always_xy=True)

# LKS-92 extent from mapdesc.json
LKS92_EXTENT = {
    "xmin": 290000,
    "ymin": 160000, 
    "xmax": 780000,
    "ymax": 450000
}

# Tile origin and resolutions from mapdesc.json
TILE_ORIGIN = {"x": -5120900, "y": 3998100}
RESOLUTIONS = [
    1058.33545000423,   # level 0
    529.167725002117,   # level 1
    264.583862501058,   # level 2
    132.291931250529,   # level 3
    52.9167725002117,   # level 4
    26.4583862501058,   # level 5
    13.2291931250529,   # level 6
    10.5833545000423,   # level 7
    7.93751587503175,   # level 8
    5.29167725002117,   # level 9
    3.96875793751588,   # level 10
    2.64583862501058,   # level 11
    1.32291931250529,   # level 12
    0.529167725002117   # level 13
]

# Valid tile ranges for each zoom level (from ZMNI service documentation)
VALID_TILE_RANGES = {
    0: {"x_min": 6, "x_max": 7, "y_min": 9, "y_max": 10},
    1: {"x_min": 13, "x_max": 14, "y_min": 19, "y_max": 21},
    2: {"x_min": 26, "x_max": 28, "y_min": 39, "y_max": 43},
    3: {"x_min": 52, "x_max": 56, "y_min": 79, "y_max": 87},
    4: {"x_min": 130, "x_max": 141, "y_min": 199, "y_max": 217},
    5: {"x_min": 261, "x_max": 283, "y_min": 399, "y_max": 435},
    6: {"x_min": 523, "x_max": 566, "y_min": 798, "y_max": 871},
    7: {"x_min": 654, "x_max": 708, "y_min": 998, "y_max": 1088},
    8: {"x_min": 873, "x_max": 944, "y_min": 1331, "y_max": 1451},
    9: {"x_min": 1309, "x_max": 1416, "y_min": 1997, "y_max": 2177},
    10: {"x_min": 1746, "x_max": 1888, "y_min": 2662, "y_max": 2903},
    11: {"x_min": 2619, "x_max": 2833, "y_min": 3994, "y_max": 4355},
    12: {"x_min": 5238, "x_max": 5666, "y_min": 7988, "y_max": 8711},
    13: {"x_min": 13095, "x_max": 14166, "y_min": 19971, "y_max": 21779}
}

def deg2num(lat_deg, lon_deg, zoom):
    """Convert lat/lon to tile numbers for Web Mercator"""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def num2deg(xtile, ytile, zoom):
    """Convert tile numbers to lat/lon bounds for Web Mercator"""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)

def get_tile_bounds_wgs84(x, y, z):
    """Get WGS84 bounds for a Web Mercator tile"""
    # Get northwest corner
    lat_nw, lon_nw = num2deg(x, y, z)
    # Get southeast corner  
    lat_se, lon_se = num2deg(x + 1, y + 1, z)
    
    return {
        "west": lon_nw,
        "north": lat_nw, 
        "east": lon_se,
        "south": lat_se
    }

def test_known_lks92_tile(level, tile_x, tile_y):
    """Test function to verify LKS-92 tile coordinate calculations"""
    resolution = RESOLUTIONS[level]
    
    # Calculate LKS-92 coordinates for this tile (northwest corner)
    lks92_x = TILE_ORIGIN["x"] + (tile_x * resolution * 512)
    lks92_y = TILE_ORIGIN["y"] - (tile_y * resolution * 512)
    
    # Calculate tile bounds in LKS-92
    tile_size_meters = resolution * 512
    lks92_bounds = {
        "xmin": lks92_x,
        "ymin": lks92_y - tile_size_meters,
        "xmax": lks92_x + tile_size_meters,
        "ymax": lks92_y
    }
    
    # Convert corners to WGS84
    nw_lon, nw_lat = transformer_to_wgs84.transform(lks92_bounds["xmin"], lks92_bounds["ymax"])
    se_lon, se_lat = transformer_to_wgs84.transform(lks92_bounds["xmax"], lks92_bounds["ymin"])
    center_lon, center_lat = transformer_to_wgs84.transform(
        (lks92_bounds["xmin"] + lks92_bounds["xmax"]) / 2,
        (lks92_bounds["ymin"] + lks92_bounds["ymax"]) / 2
    )
    
    logger.info(f"LKS-92 tile {level}/{tile_x}/{tile_y}:")
    logger.info(f"  LKS-92 bounds: {lks92_bounds}")
    logger.info(f"  WGS84 NW: {nw_lat:.6f}, {nw_lon:.6f}")
    logger.info(f"  WGS84 SE: {se_lat:.6f}, {se_lon:.6f}")
    logger.info(f"  WGS84 Center: {center_lat:.6f}, {center_lon:.6f}")
    logger.info(f"  Resolution: {resolution}")
    
    return center_lat, center_lon

def wgs84_to_lks92_tile(x, y, z):
    """Convert WGS84 tile coordinates to LKS-92 tile coordinates"""
    # Get WGS84 bounds of the requested tile
    bounds = get_tile_bounds_wgs84(x, y, z)
    
    # Transform corners to LKS-92
    nw_x, nw_y = transformer_to_lks92.transform(bounds["west"], bounds["north"])
    se_x, se_y = transformer_to_lks92.transform(bounds["east"], bounds["south"])
    
    # Debug logging
    logger.info(f"WGS84 tile {z}/{x}/{y} bounds: {bounds}")
    logger.info(f"LKS-92 bounds: NW({nw_x:.2f}, {nw_y:.2f}) SE({se_x:.2f}, {se_y:.2f})")
    
    # Check if tile intersects with LKS-92 extent
    if (se_x < LKS92_EXTENT["xmin"] or nw_x > LKS92_EXTENT["xmax"] or 
        se_y < LKS92_EXTENT["ymin"] or nw_y > LKS92_EXTENT["ymax"]):
        logger.info(f"Tile outside LKS-92 extent")
        return None
    
    # Find the best matching zoom level based on resolution
    # Calculate the resolution of the WGS84 tile in LKS-92 coordinates
    tile_width_lks92 = abs(se_x - nw_x)
    tile_resolution = tile_width_lks92 / 256  # WGS84 tiles are 256x256
    
    logger.info(f"Calculated tile resolution: {tile_resolution:.6f}")
    
    # Find closest resolution level
    best_level = 0
    min_diff = float('inf')
    for i, res in enumerate(RESOLUTIONS):
        diff = abs(res - tile_resolution)
        if diff < min_diff:
            min_diff = diff
            best_level = i
    
    logger.info(f"Best matching level: {best_level} (resolution: {RESOLUTIONS[best_level]:.6f})")
    
    # Calculate LKS-92 tile coordinates
    resolution = RESOLUTIONS[best_level]
    
    # Use center point of the WGS84 tile
    center_lon = (bounds["west"] + bounds["east"]) / 2
    center_lat = (bounds["north"] + bounds["south"]) / 2
    center_x, center_y = transformer_to_lks92.transform(center_lon, center_lat)
    
    logger.info(f"Center point - WGS84: ({center_lat:.6f}, {center_lon:.6f}) LKS-92: ({center_x:.2f}, {center_y:.2f})")
    
    # Calculate tile indices (LKS-92 tiles are 512x512)
    tile_x = int((center_x - TILE_ORIGIN["x"]) / (resolution * 512))
    tile_y = int((TILE_ORIGIN["y"] - center_y) / (resolution * 512))
    
    logger.info(f"Calculated LKS-92 tile: {best_level}/{tile_x}/{tile_y}")
    
    # Validate tile coordinates are within service coverage
    if best_level in VALID_TILE_RANGES:
        ranges = VALID_TILE_RANGES[best_level]
        if (tile_x < ranges["x_min"] or tile_x > ranges["x_max"] or
            tile_y < ranges["y_min"] or tile_y > ranges["y_max"]):
            logger.warning(f"Calculated tile {best_level}/{tile_x}/{tile_y} is outside valid range: "
                         f"x:{ranges['x_min']}-{ranges['x_max']}, y:{ranges['y_min']}-{ranges['y_max']}")
            return None
    
    return best_level, tile_x, tile_y

@app.route('/<int:z>/<int:x>/<int:y>.png')
def get_tile(z, x, y):
    """Proxy endpoint for tile requests"""
    try:
        logger.info(f"Processing tile request: {z}/{x}/{y}")
        
        # Convert WGS84 tile coordinates to LKS-92
        lks92_coords = wgs84_to_lks92_tile(x, y, z)
        
        if lks92_coords is None:
            logger.warning(f"Tile {z}/{x}/{y} is outside coverage area")
            return Response(
                b'',
                status=404,
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*'
                }
            )
        
        level, tile_x, tile_y = lks92_coords
        logger.info(f"Mapped to LKS-92 tile: level={level}, x={tile_x}, y={tile_y}")
        
        # Construct URL for ZMNI map server
        tile_url = f"{BASE_URL}/{level}/{tile_x}/{tile_y}"
        logger.info(f"Fetching from: {tile_url}")
        
        # Fetch tile from ZMNI server
        response = requests.get(tile_url, timeout=30)
        logger.info(f"ZMNI server response: {response.status_code}")
        
        if response.status_code == 200:
            return Response(
                response.content,
                mimetype='image/png',
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*'
                }
            )
        else:
            logger.error(f"ZMNI server returned {response.status_code} for {tile_url}")
            abort(response.status_code)
            
    except Exception as e:
        logger.error(f"Error processing tile {z}/{x}/{y}: {str(e)}", exc_info=True)
        abort(500)

@app.route('/test/<int:level>/<int:tile_x>/<int:tile_y>')
def test_tile_coords(level, tile_x, tile_y):
    """Test endpoint to verify tile coordinate calculations"""
    try:
        lat, lon = test_known_lks92_tile(level, tile_x, tile_y)
        return {
            "lks92_tile": f"{level}/{tile_x}/{tile_y}",
            "wgs84_coords": {"lat": lat, "lon": lon},
            "resolution": RESOLUTIONS[level],
            "valid_range": VALID_TILE_RANGES.get(level, "Unknown")
        }
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return {"status": "ok", "service": "ZMNI Tile Proxy"}

@app.route('/')
def info():
    """Service information"""
    return {
        "service": "ZMNI meliorācijas datu serviss Tile Proxy",
        "description": "Converts WGS84 z/x/y tile requests to LKS-92 coordinate system",
        "usage": "/{z}/{x}/{y}.png",
        "source_crs": "EPSG:4326 (WGS84)",
        "target_crs": "EPSG:3059 (LKS-92)",
        "tile_size": TILE_SIZE,
        "zoom_levels": len(RESOLUTIONS)
    }

if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=8117, debug=debug_mode)
