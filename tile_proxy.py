#!/usr/bin/env python3
"""
Tile proxy server for ZMNI meliorācijas datu serviss
Converts WGS84 z/x/y tile requests to LKS-92 (EPSG:3059) coordinate system
"""

import math
import requests
from flask import Flask, Response, abort
from pyproj import Transformer

app = Flask(__name__)

# ZMNI Map Server configuration
BASE_URL = "https://lvmgeo.lvm.lv/proxy/D341478CE74F4F02B68607991448D499/CacheDinamic/ZMNI/MapServer/tile"
TILE_SIZE = 512

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

def wgs84_to_lks92_tile(x, y, z):
    """Convert WGS84 tile coordinates to LKS-92 tile coordinates"""
    # Get WGS84 bounds of the requested tile
    bounds = get_tile_bounds_wgs84(x, y, z)
    
    # Transform corners to LKS-92
    nw_x, nw_y = transformer_to_lks92.transform(bounds["west"], bounds["north"])
    se_x, se_y = transformer_to_lks92.transform(bounds["east"], bounds["south"])
    
    # Check if tile intersects with LKS-92 extent
    if (se_x < LKS92_EXTENT["xmin"] or nw_x > LKS92_EXTENT["xmax"] or 
        se_y < LKS92_EXTENT["ymin"] or nw_y > LKS92_EXTENT["ymax"]):
        return None
    
    # Find the best matching zoom level based on resolution
    # Calculate the resolution of the WGS84 tile in LKS-92 coordinates
    tile_width_lks92 = abs(se_x - nw_x)
    tile_resolution = tile_width_lks92 / TILE_SIZE
    
    # Find closest resolution level
    best_level = 0
    min_diff = float('inf')
    for i, res in enumerate(RESOLUTIONS):
        diff = abs(res - tile_resolution)
        if diff < min_diff:
            min_diff = diff
            best_level = i
    
    # Calculate LKS-92 tile coordinates
    resolution = RESOLUTIONS[best_level]
    
    # Use center point of the WGS84 tile
    center_lon = (bounds["west"] + bounds["east"]) / 2
    center_lat = (bounds["north"] + bounds["south"]) / 2
    center_x, center_y = transformer_to_lks92.transform(center_lon, center_lat)
    
    # Calculate tile indices
    tile_x = int((center_x - TILE_ORIGIN["x"]) / (resolution * TILE_SIZE))
    tile_y = int((TILE_ORIGIN["y"] - center_y) / (resolution * TILE_SIZE))
    
    return best_level, tile_x, tile_y

@app.route('/<int:z>/<int:x>/<int:y>.png')
def get_tile(z, x, y):
    """Proxy endpoint for tile requests"""
    try:
        # Convert WGS84 tile coordinates to LKS-92
        lks92_coords = wgs84_to_lks92_tile(x, y, z)
        
        if lks92_coords is None:
            # Tile is outside the coverage area
            abort(404)
        
        level, tile_x, tile_y = lks92_coords
        
        # Construct URL for ZMNI map server
        tile_url = f"{BASE_URL}/{level}/{tile_x}/{tile_y}"
        
        # Fetch tile from ZMNI server
        response = requests.get(tile_url, timeout=30)
        
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
            abort(response.status_code)
            
    except Exception as e:
        print(f"Error processing tile {z}/{x}/{y}: {e}")
        abort(500)

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
    app.run(host='0.0.0.0', port=8117, debug=True)
