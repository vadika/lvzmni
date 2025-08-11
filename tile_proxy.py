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
from PIL import Image
import io

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ZMNI Map Server configuration
#BASE_URL = "https://lvmgeo.lvm.lv/arcgis/rest/services/CacheDinamic/ZMNI/MapServer/tile"
BASE_URL = "https://lvmgeo.lvm.lv/proxy/D341478CE74F4F02B68607991448D499/CacheDinamic/ZMNI/MapServer/tile/"
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

def get_lks92_tile_bounds(level, tile_x, tile_y):
    """Calculate LKS-92 bounds for a given tile using the actual extent and tile ranges"""
    if level not in VALID_TILE_RANGES:
        return None
    
    ranges = VALID_TILE_RANGES[level]
    
    # Calculate tile size in LKS-92 coordinates based on extent and tile ranges
    extent_width = LKS92_EXTENT["xmax"] - LKS92_EXTENT["xmin"]  # 490000 meters
    extent_height = LKS92_EXTENT["ymax"] - LKS92_EXTENT["ymin"]  # 290000 meters
    
    tiles_x = ranges["x_max"] - ranges["x_min"] + 1
    tiles_y = ranges["y_max"] - ranges["y_min"] + 1
    
    tile_width = extent_width / tiles_x
    tile_height = extent_height / tiles_y
    
    # Calculate tile bounds
    x_offset = tile_x - ranges["x_min"]
    y_offset = tile_y - ranges["y_min"]
    
    xmin = LKS92_EXTENT["xmin"] + (x_offset * tile_width)
    xmax = xmin + tile_width
    ymax = LKS92_EXTENT["ymax"] - (y_offset * tile_height)
    ymin = ymax - tile_height
    
    return {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax
    }

def test_known_lks92_tile(level, tile_x, tile_y):
    """Test function to verify LKS-92 tile coordinate calculations"""
    bounds = get_lks92_tile_bounds(level, tile_x, tile_y)
    if not bounds:
        return None, None
    
    # Convert corners to WGS84
    nw_lon, nw_lat = transformer_to_wgs84.transform(bounds["xmin"], bounds["ymax"])
    se_lon, se_lat = transformer_to_wgs84.transform(bounds["xmax"], bounds["ymin"])
    center_lon, center_lat = transformer_to_wgs84.transform(
        (bounds["xmin"] + bounds["xmax"]) / 2,
        (bounds["ymin"] + bounds["ymax"]) / 2
    )
    
    logger.info(f"LKS-92 tile {level}/{tile_x}/{tile_y}:")
    logger.info(f"  LKS-92 bounds: {bounds}")
    logger.info(f"  WGS84 NW: {nw_lat:.6f}, {nw_lon:.6f}")
    logger.info(f"  WGS84 SE: {se_lat:.6f}, {se_lon:.6f}")
    logger.info(f"  WGS84 Center: {center_lat:.6f}, {center_lon:.6f}")
    
    return center_lat, center_lon

def find_intersecting_lks92_tiles(x, y, z):
    """Find all LKS-92 tiles that intersect with the WGS84 tile"""
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
    
    # Map WGS84 zoom level to appropriate LKS-92 level
    wgs84_to_lks92_zoom_mapping = {
        0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 2, 7: 3, 8: 4, 9: 5,
        10: 6, 11: 7, 12: 8, 13: 9, 14: 10, 15: 11, 16: 12, 17: 13, 18: 13
    }
    
    target_level = wgs84_to_lks92_zoom_mapping.get(z, 13)
    
    # Try the target level first, then nearby levels
    levels_to_try = [target_level]
    for offset in [-1, 1, -2, 2]:
        nearby_level = target_level + offset
        if 0 <= nearby_level <= 13 and nearby_level not in levels_to_try:
            levels_to_try.append(nearby_level)
    
    for level in levels_to_try:
        if level not in VALID_TILE_RANGES:
            continue
            
        ranges = VALID_TILE_RANGES[level]
        
        # Calculate tile size for this level
        extent_width = LKS92_EXTENT["xmax"] - LKS92_EXTENT["xmin"]
        extent_height = LKS92_EXTENT["ymax"] - LKS92_EXTENT["ymin"]
        
        tiles_x = ranges["x_max"] - ranges["x_min"] + 1
        tiles_y = ranges["y_max"] - ranges["y_min"] + 1
        
        tile_width = extent_width / tiles_x
        tile_height = extent_height / tiles_y
        
        # Find all tiles that intersect with the WGS84 area
        min_tile_x = max(ranges["x_min"], ranges["x_min"] + int((nw_x - LKS92_EXTENT["xmin"]) / tile_width))
        max_tile_x = min(ranges["x_max"], ranges["x_min"] + int((se_x - LKS92_EXTENT["xmin"]) / tile_width))
        min_tile_y = max(ranges["y_min"], ranges["y_min"] + int((LKS92_EXTENT["ymax"] - nw_y) / tile_height))
        max_tile_y = min(ranges["y_max"], ranges["y_min"] + int((LKS92_EXTENT["ymax"] - se_y) / tile_height))
        
        intersecting_tiles = []
        for tile_x in range(min_tile_x, max_tile_x + 1):
            for tile_y in range(min_tile_y, max_tile_y + 1):
                if (tile_x >= ranges["x_min"] and tile_x <= ranges["x_max"] and
                    tile_y >= ranges["y_min"] and tile_y <= ranges["y_max"]):
                    intersecting_tiles.append((level, tile_x, tile_y))
        
        if intersecting_tiles:
            logger.info(f"Found {len(intersecting_tiles)} intersecting tiles at level {level}")
            return {
                'level': level,
                'tiles': intersecting_tiles,
                'wgs84_bounds': bounds,
                'lks92_bounds': {'xmin': nw_x, 'ymin': se_y, 'xmax': se_x, 'ymax': nw_y}
            }
    
    logger.warning(f"No suitable LKS-92 tiles found for WGS84 tile {z}/{x}/{y}")
    return None

def wgs84_to_lks92_tile(x, y, z):
    """Convert WGS84 tile coordinates to LKS-92 tile coordinates (legacy function)"""
    result = find_intersecting_lks92_tiles(x, y, z)
    if result and result['tiles']:
        # Return the first tile for backward compatibility
        level, tile_x, tile_y = result['tiles'][0]
        return level, tile_x, tile_y
    return None

def composite_tiles_for_wgs84(x, y, z):
    """Create a composite tile from multiple LKS-92 tiles for a WGS84 request"""
    # Find all intersecting LKS-92 tiles
    tile_info = find_intersecting_lks92_tiles(x, y, z)
    if not tile_info:
        return None
    
    level = tile_info['level']
    tiles = tile_info['tiles']
    wgs84_bounds = tile_info['wgs84_bounds']
    
    logger.info(f"Compositing {len(tiles)} tiles for WGS84 tile {z}/{x}/{y}")
    
    # Transform WGS84 bounds to LKS-92 for the exact area we need
    wgs84_nw_x, wgs84_nw_y = transformer_to_lks92.transform(wgs84_bounds["west"], wgs84_bounds["north"])
    wgs84_se_x, wgs84_se_y = transformer_to_lks92.transform(wgs84_bounds["east"], wgs84_bounds["south"])
    
    # Calculate the bounding box that encompasses all needed LKS-92 tiles
    ranges = VALID_TILE_RANGES[level]
    extent_width = LKS92_EXTENT["xmax"] - LKS92_EXTENT["xmin"]
    extent_height = LKS92_EXTENT["ymax"] - LKS92_EXTENT["ymin"]
    tiles_x = ranges["x_max"] - ranges["x_min"] + 1
    tiles_y = ranges["y_max"] - ranges["y_min"] + 1
    tile_width_meters = extent_width / tiles_x
    tile_height_meters = extent_height / tiles_y
    
    # Find the bounds of all tiles we need to fetch
    min_tile_x = min(tile[1] for tile in tiles)
    max_tile_x = max(tile[1] for tile in tiles)
    min_tile_y = min(tile[2] for tile in tiles)
    max_tile_y = max(tile[2] for tile in tiles)
    
    # Calculate the composite area bounds in LKS-92 based on actual tile bounds
    first_tile_bounds = get_lks92_tile_bounds(level, min_tile_x, min_tile_y)
    last_tile_bounds = get_lks92_tile_bounds(level, max_tile_x, max_tile_y)
    
    if not first_tile_bounds or not last_tile_bounds:
        logger.error("Could not get tile bounds for composite calculation")
        return None
    
    composite_xmin = first_tile_bounds["xmin"]
    composite_xmax = last_tile_bounds["xmax"]
    composite_ymin = last_tile_bounds["ymin"]
    composite_ymax = first_tile_bounds["ymax"]
    
    # Calculate canvas size (each LKS-92 tile is 512x512 pixels)
    canvas_width = (max_tile_x - min_tile_x + 1) * 512
    canvas_height = (max_tile_y - min_tile_y + 1) * 512
    
    logger.info(f"Canvas size: {canvas_width}x{canvas_height} for tiles {min_tile_x}-{max_tile_x}, {min_tile_y}-{max_tile_y}")
    logger.info(f"Composite bounds: ({composite_xmin:.2f}, {composite_ymin:.2f}) to ({composite_xmax:.2f}, {composite_ymax:.2f})")
    
    # Create the composite canvas
    canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
    
    # Log all tile URLs that will be fetched
    tile_urls = []
    for level, tile_x, tile_y in tiles:
        tile_url = f"{BASE_URL}{level}/{tile_x}/{tile_y}"
        tile_urls.append(tile_url)
    
    logger.info(f"Fetching {len(tile_urls)} tiles:")
    for i, url in enumerate(tile_urls, 1):
        logger.info(f"  {i}. {url}")
    
    # Fetch and place each tile
    for level, tile_x, tile_y in tiles:
        try:
            tile_url = f"{BASE_URL}{level}/{tile_x}/{tile_y}"
            response = requests.get(tile_url, timeout=10)
            
            if response.status_code == 200:
                # Calculate where this tile should be placed on the canvas (simple grid placement)
                x_offset = (tile_x - min_tile_x) * 512
                y_offset = (tile_y - min_tile_y) * 512
                
                logger.info(f"Placing tile {tile_x}/{tile_y} at grid offset ({x_offset}, {y_offset})")
                
                # Verify offsets are positive
                if x_offset < 0 or y_offset < 0:
                    logger.error(f"Negative offset detected: ({x_offset}, {y_offset}) for tile {tile_x}/{tile_y}")
                    continue
                
                # Open and paste the tile
                tile_img = Image.open(io.BytesIO(response.content))
                canvas.paste(tile_img, (x_offset, y_offset))
                logger.info(f"Successfully placed tile {tile_x}/{tile_y}")
            else:
                logger.warning(f"Failed to fetch tile {tile_x}/{tile_y}: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error processing tile {tile_x}/{tile_y}: {str(e)}")
            continue
    
    # Calculate exact pixel coordinates for the WGS84 tile area on the composite canvas
    pixels_per_meter_x = 512 / tile_width_meters
    pixels_per_meter_y = 512 / tile_height_meters
    
    # Calculate the exact crop area for this WGS84 tile (no centering, exact boundaries)
    crop_left = (wgs84_nw_x - composite_xmin) * pixels_per_meter_x
    crop_right = (wgs84_se_x - composite_xmin) * pixels_per_meter_x
    crop_top = (composite_ymax - wgs84_nw_y) * pixels_per_meter_y
    crop_bottom = (composite_ymax - wgs84_se_y) * pixels_per_meter_y
    
    # Calculate the actual pixel dimensions of the WGS84 area
    wgs84_width_pixels = crop_right - crop_left
    wgs84_height_pixels = crop_bottom - crop_top
    
    logger.info(f"WGS84 area in LKS-92: NW({wgs84_nw_x:.2f}, {wgs84_nw_y:.2f}) SE({wgs84_se_x:.2f}, {wgs84_se_y:.2f})")
    logger.info(f"Composite area: ({composite_xmin:.2f}, {composite_ymin:.2f}) to ({composite_xmax:.2f}, {composite_ymax:.2f})")
    logger.info(f"WGS84 area in pixels: {wgs84_width_pixels:.1f} x {wgs84_height_pixels:.1f}")
    logger.info(f"Exact crop coordinates: ({crop_left:.1f}, {crop_top:.1f}) to ({crop_right:.1f}, {crop_bottom:.1f})")
    
    # Convert to integer pixel coordinates
    crop_left = int(round(crop_left))
    crop_right = int(round(crop_right))
    crop_top = int(round(crop_top))
    crop_bottom = int(round(crop_bottom))
    
    # Ensure crop coordinates are within canvas bounds
    crop_left = max(0, min(canvas_width, crop_left))
    crop_right = max(crop_left, min(canvas_width, crop_right))
    crop_top = max(0, min(canvas_height, crop_top))
    crop_bottom = max(crop_top, min(canvas_height, crop_bottom))
    
    # Ensure we have a valid crop area
    if crop_right <= crop_left or crop_bottom <= crop_top:
        logger.warning(f"Invalid crop area: ({crop_left}, {crop_top}) to ({crop_right}, {crop_bottom})")
        # Return empty tile
        empty_img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
        return empty_img
    
    logger.info(f"Final crop coordinates: ({crop_left}, {crop_top}) to ({crop_right}, {crop_bottom})")
    
    # Crop the exact WGS84 area
    final_img = canvas.crop((crop_left, crop_top, crop_right, crop_bottom))
    
    # Always resize to exactly 256x256 (this is the standard for WGS84 tiles)
    final_img = final_img.resize((256, 256), Image.LANCZOS)
    
    return final_img

@app.route('/<int:z>/<int:x>/<int:y>.png')
def get_tile(z, x, y):
    """Proxy endpoint for tile requests"""
    try:
        logger.info(f"Processing tile request: {z}/{x}/{y}")
        
        # Try to create a composite tile from multiple LKS-92 tiles
        composite_img = composite_tiles_for_wgs84(x, y, z)
        
        if composite_img:
            # Save to bytes
            output = io.BytesIO()
            composite_img.save(output, format='PNG')
            output.seek(0)
            
            return Response(
                output.getvalue(),
                mimetype='image/png',
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*'
                }
            )
        else:
            logger.warning(f"Tile {z}/{x}/{y} is outside coverage area")
            return Response(
                b'',
                status=404,
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*'
                }
            )
            
    except Exception as e:
        logger.error(f"Error processing tile {z}/{x}/{y}: {str(e)}", exc_info=True)
        abort(500)

def lks92_to_wgs84_tiles(level, tile_x, tile_y):
    """Find WGS84 tiles that would map to the given LKS-92 tile"""
    bounds = get_lks92_tile_bounds(level, tile_x, tile_y)
    if not bounds:
        return []
    
    # Convert LKS-92 bounds to WGS84
    nw_lon, nw_lat = transformer_to_wgs84.transform(bounds["xmin"], bounds["ymax"])
    se_lon, se_lat = transformer_to_wgs84.transform(bounds["xmax"], bounds["ymin"])
    
    wgs84_tiles = []
    
    # Check zoom levels that might contain this area
    for z in range(8, 19):  # Check zoom levels 8-18 (reasonable range)
        # Calculate WGS84 tile coordinates for the corners
        nw_x, nw_y = deg2num(nw_lat, nw_lon, z)
        se_x, se_y = deg2num(se_lat, se_lon, z)
        
        # Get all tiles that intersect with this area
        min_x = min(nw_x, se_x)
        max_x = max(nw_x, se_x)
        min_y = min(nw_y, se_y)
        max_y = max(nw_y, se_y)
        
        # Limit the number of tiles to prevent excessive results
        if (max_x - min_x + 1) * (max_y - min_y + 1) > 100:
            continue
        
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                wgs84_tiles.append({
                    "z": z,
                    "x": x,
                    "y": y,
                    "url": f"http://localhost:8117/{z}/{x}/{y}.png"
                })
    
    return wgs84_tiles

@app.route('/test/<int:level>/<int:tile_x>/<int:tile_y>')
def test_tile_coords(level, tile_x, tile_y):
    """Test endpoint to verify tile coordinate calculations"""
    try:
        lat, lon = test_known_lks92_tile(level, tile_x, tile_y)
        
        # Test if the LKS-92 tile actually exists
        tile_url = f"{BASE_URL}/{level}/{tile_x}/{tile_y}"
        try:
            response = requests.head(tile_url, timeout=10)
            tile_exists = response.status_code == 200
            tile_status = response.status_code
        except Exception as e:
            tile_exists = False
            tile_status = f"Error: {str(e)}"
        
        # Find corresponding WGS84 tiles (simplified to avoid recursion)
        wgs84_tiles = lks92_to_wgs84_tiles(level, tile_x, tile_y)
        
        return {
            "lks92_tile": f"{level}/{tile_x}/{tile_y}",
            "wgs84_coords": {"lat": lat, "lon": lon},
            "resolution": RESOLUTIONS[level],
            "valid_range": VALID_TILE_RANGES.get(level, "Unknown"),
            "tile_exists": tile_exists,
            "tile_status": tile_status,
            "tile_url": tile_url,
            "wgs84_tiles": wgs84_tiles[:10]  # Limit to first 10 results
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
