#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil

# =================================================================
# POETRY BOOTSTRAP LOGIC
# =================================================================
def bootstrap():
    if os.environ.get("MESHTASTIC_BOOTSTRAPPED") == "1":
        return
    poetry_path = shutil.which("poetry") or "/opt/homebrew/bin/poetry"
    if not os.path.exists(poetry_path):
        print("Error: Poetry not found.")
        sys.exit(1)
    try:
        if not os.path.exists("pyproject.toml"):
            with open("pyproject.toml", "w") as f:
                f.write('[tool.poetry]\nname = "tdeck-gen"\nversion = "0.1.0"\ndescription = ""\nauthors = ["User"]\n'
                        '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "^2.31.0"\nPillow = "^10.0.0"\n'
                        '[build-system]\nrequires = ["poetry-core"]\nbuild-backend = "poetry.core.masonry.api"\n')
        subprocess.run([poetry_path, "install", "--no-root"], check=True, capture_output=True)
        venv_path = subprocess.check_output([poetry_path, "env", "info", "--path"], text=True).strip()
        venv_python = os.path.join(venv_path, "bin", "python3")
        os.environ["MESHTASTIC_BOOTSTRAPPED"] = "1"
        os.execv(venv_python, [venv_python] + sys.argv)
    except Exception as e:
        print(f"Setup failed: {e}")
        sys.exit(1)

bootstrap()

# =================================================================
# ORIGINAL SCRIPT LOGIC
# =================================================================
import math
import time
import requests
from PIL import Image, ImageDraw, ImageFont
import argparse
from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

class CityLookup:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'MeshtasticTileGenerator/1.0'
        })
    
    def get_coordinates(self, city, state=None, country=None):
        """Get coordinates using OpenStreetMap Nominatim (free)"""
        base_url = "https://nominatim.openstreetmap.org/search"
        
        # Build query
        query = city
        if state:
            query += f", {state}"
        if country:
            query += f", {country}"
        
        params = {
            'q': query,
            'format': 'json',
            'limit': 1,
            'addressdetails': 1
        }
        
        try:
            response = self.session.get(base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                return None
            
            result = data[0]
            return {
                'name': result.get('display_name', 'Unknown'),
                'lat': float(result['lat']),
                'lon': float(result['lon']),
                'type': result.get('type', 'unknown')
            }
            
        except Exception as e:
            print(f"Error looking up coordinates for {query}: {e}")
            return None
    
    def get_bounding_box_for_cities(self, cities, buffer_km=10):
        """Get bounding box for multiple cities with buffer in kilometers"""
        all_coords = []
        
        print(f"Looking up coordinates for {len(cities)} cities...")
        for city_info in cities:
            if isinstance(city_info, str):
                city, state, country = city_info, None, None
            else:
                city = city_info.get('city')
                state = city_info.get('state')
                country = city_info.get('country')
            
            result = self.get_coordinates(city, state, country)
            if result:
                all_coords.append(result)
                print(f"✓ {city}: {result['lat']:.4f}, {result['lon']:.4f}")
            else:
                print(f"✗ {city}: Not found")
        
        if not all_coords:
            print("No valid coordinates found")
            return None
        
        # Calculate bounding box
        lats = [c['lat'] for c in all_coords]
        lons = [c['lon'] for c in all_coords]
        
        # Convert km buffer to degrees (approximate)
        buffer_deg = buffer_km / 111.0  # ~111km per degree
        
        north = max(lats) + buffer_deg
        south = min(lats) - buffer_deg
        east = max(lons) + buffer_deg
        west = min(lons) - buffer_deg
        
        print(f"\n📦 Bounding box for {len(all_coords)} cities (±{buffer_km}km buffer):")
        print(f"   North: {north:.4f}")
        print(f"   South: {south:.4f}")
        print(f"   East:  {east:.4f}")
        print(f"   West:  {west:.4f}")
        
        return {
            'north': north,
            'south': south,
            'east': east,
            'west': west,
            'cities': all_coords
        }

class MeshtasticTileGenerator:
    def __init__(self, output_dir="tiles", tile_size=256, delay=0.1):
        self.output_dir = Path(output_dir)
        self.tile_size = tile_size
        self.delay = delay  # Delay between requests to be respectful
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'MeshtasticTileGenerator/1.0'
        })
        
        # Create output directory
        self.output_dir.mkdir(exist_ok=True)
        
    def deg2num(self, lat_deg, lon_deg, zoom):
        """Convert lat/lon to tile numbers"""
        lat_rad = math.radians(lat_deg)
        n = 2.0 ** zoom
        x = int((lon_deg + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return (x, y)
    
    def num2deg(self, x, y, zoom):
        """Convert tile numbers to lat/lon"""
        n = 2.0 ** zoom
        lon_deg = x / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        lat_deg = math.degrees(lat_rad)
        return (lat_deg, lon_deg)
    
    def get_tile_url(self, x, y, zoom, source="osm"):
        """Get tile URL for different map sources"""
        sources = {
            "osm": f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png",
            "satellite": f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}",
            "terrain": f"https://tile.opentopomap.org/{zoom}/{x}/{y}.png",
            "cycle": f"https://tile.thunderforest.com/cycle/{zoom}/{x}/{y}.png"
        }
        return sources.get(source, sources["osm"])
    
    def download_tile(self, x, y, zoom, source="osm"):
        """Download a single tile"""
        url = self.get_tile_url(x, y, zoom, source)
        
        # Create directory structure
        tile_dir = self.output_dir / str(zoom) / str(x)
        tile_dir.mkdir(parents=True, exist_ok=True)
        
        tile_path = tile_dir / f"{y}.png"
        
        # Skip if tile already exists
        if tile_path.exists():
            return tile_path, True
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # Save the tile
            with open(tile_path, 'wb') as f:
                f.write(response.content)
            
            time.sleep(self.delay)  # Be respectful to tile servers
            return tile_path, True
            
        except Exception as e:
            print(f"Error downloading tile {x},{y},{zoom}: {e}")
            return None, False
    
    def generate_tiles(self, north, south, east, west, min_zoom=8, max_zoom=16, source="osm", max_workers=4):
        """Generate tiles for a bounding box"""
        print(f"Generating tiles for bounds: N:{north}, S:{south}, E:{east}, W:{west}")
        print(f"Zoom levels: {min_zoom} to {max_zoom}")
        print(f"Source: {source}")
        
        # Validate coordinates
        if north <= south:
            print("Error: North latitude must be greater than south latitude")
            return
        if east <= west:
            print("Error: East longitude must be greater than west longitude")
            return
        
        total_tiles = 0
        downloaded_tiles = 0
        
        # Calculate total tiles for progress tracking
        # Fixed range for original behavior
        z_step = 1 if max_zoom >= min_zoom else -1
        zoom_range = range(min_zoom, max_zoom + z_step, z_step)

        for zoom in zoom_range:
            x_min, y_max = self.deg2num(south, west, zoom)
            x_max, y_min = self.deg2num(north, east, zoom)
            
            if x_min > x_max: x_min, x_max = x_max, x_min
            if y_min > y_max: y_min, y_max = y_max, y_min
            
            tiles_this_zoom = (x_max - x_min + 1) * (y_max - y_min + 1)
            total_tiles += tiles_this_zoom
            print(f"Zoom {zoom}: {tiles_this_zoom} tiles (x:{x_min}-{x_max}, y:{y_min}-{y_max})")
        
        print(f"Total tiles to process: {total_tiles}")
        
        if total_tiles == 0:
            print("No tiles to download. Check your coordinates.")
            return
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for zoom in zoom_range:
                x_min, y_max = self.deg2num(south, west, zoom)
                x_max, y_min = self.deg2num(north, east, zoom)
                if x_min > x_max: x_min, x_max = x_max, x_min
                if y_min > y_max: y_min, y_max = y_max, y_min
                
                print(f"Processing zoom level {zoom} (x:{x_min}-{x_max}, y:{y_min}-{y_max})...")
                for x in range(x_min, x_max + 1):
                    for y in range(y_min, y_max + 1):
                        futures.append(executor.submit(self.download_tile, x, y, zoom, source))
            
            for future in as_completed(futures):
                tile_path, success = future.result()
                if success:
                    downloaded_tiles += 1
                if downloaded_tiles % 100 == 0:
                    print(f"Downloaded {downloaded_tiles}/{total_tiles} tiles")
        
        print(f"Completed! Downloaded {downloaded_tiles}/{total_tiles} tiles")
        self.generate_metadata(north, south, east, west, min_zoom, max_zoom, source)
    
    def generate_metadata(self, north, south, east, west, min_zoom, max_zoom, source):
        """Generate metadata file for Meshtastic"""
        metadata = {
            "name": f"Generated tiles ({source})",
            "description": f"Map tiles for Meshtastic T-Deck",
            "bounds": [west, south, east, north],
            "minzoom": min(min_zoom, max_zoom),
            "maxzoom": max(min_zoom, max_zoom),
            "format": "png",
            "type": "baselayer",
            "source": source,
            "generated": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        metadata_path = self.output_dir / "metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        print(f"Metadata saved to: {metadata_path}")

def get_region_bounds(region):
    """Get predefined bounds for common regions"""
    regions = {
        'north_america': {'north': 83.0, 'south': 7.0, 'east': -52.0, 'west': -168.0},
        'usa': {'north': 49.0, 'south': 24.0, 'east': -66.0, 'west': -125.0},
        'california': {'north': 42.0, 'south': 32.5, 'east': -114.131, 'west': -124.409}
    }
    return regions.get(region.lower())

def main():
    parser = argparse.ArgumentParser()
    method_group = parser.add_mutually_exclusive_group(required=True)
    method_group.add_argument('--region', type=str)
    method_group.add_argument('--city', type=str)
    method_group.add_argument('--coords', action='store_true')
    
    parser.add_argument('--buffer', type=int, default=20)
    parser.add_argument('--north', type=float)
    parser.add_argument('--south', type=float)
    parser.add_argument('--east', type=float)
    parser.add_argument('--west', type=float)
    parser.add_argument('--min-zoom', type=int, default=8)
    parser.add_argument('--max-zoom', type=int, default=12)
    parser.add_argument('--source', default='osm')
    parser.add_argument('--output-dir', default='tiles')
    
    args = parser.parse_args()
    generator = MeshtasticTileGenerator(output_dir=args.output_dir)
    
    north = south = east = west = None
    if args.region:
        bounds = get_region_bounds(args.region)
        if bounds: north, south, east, west = bounds['north'], bounds['south'], bounds['east'], bounds['west']
    elif args.city:
        coord = CityLookup().get_coordinates(args.city)
        if coord:
            buffer_deg = args.buffer / 111.0
            north, south, east, west = coord['lat']+buffer_deg, coord['lat']-buffer_deg, coord['lon']+buffer_deg, coord['lon']-buffer_deg
    elif args.coords:
        north, south, east, west = args.north, args.south, args.east, args.west

    if north is not None:
        generator.generate_tiles(north, south, east, west, args.min_zoom, args.max_zoom, args.source)

if __name__ == "__main__":
    main()
