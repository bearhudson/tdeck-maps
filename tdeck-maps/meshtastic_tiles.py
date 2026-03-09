#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import argparse

# --- BOOTSTRAPPER ---
def bootstrap():
    if os.environ.get("BOOTSTRAP_COMPLETE") == "1":
        return
    poetry_path = shutil.which("poetry") or "/opt/homebrew/bin/poetry"
    if not os.path.exists(poetry_path):
        print("Error: Poetry not found.")
        sys.exit(1)
    try:
        subprocess.run([poetry_path, "install", "--no-root"], check=True, capture_output=True)
        venv_path = subprocess.check_output([poetry_path, "env", "info", "--path"], text=True).strip()
        executable = os.path.join(venv_path, "bin", "python3")
        os.environ["BOOTSTRAP_COMPLETE"] = "1"
        os.execv(executable, [executable] + sys.argv)
    except Exception as e:
        print(f"Setup failed: {e}")
        sys.exit(1)

bootstrap()

# --- IMPORTS ---
import requests
import math
import time
import json

# --- LOGIC ---
class MeshtasticTileGenerator:
    def __init__(self, output_dir, delay=0.1):
        self.output_dir = output_dir
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'TDeckTileGen/1.1'})

    def get_city_coords(self, city_name):
        url = "https://nominatim.openstreetmap.org/search"
        params = {'q': city_name, 'format': 'json', 'limit': 1}
        res = self.session.get(url, params=params)
        if res.status_code == 200 and res.json():
            data = res.json()[0]
            return [float(x) for x in data['boundingbox']]
        return None

    def deg2num(self, lat_deg, lon_deg, zoom):
        lat_rad = math.radians(lat_deg)
        n = 2.0 ** zoom
        xtile = int((lon_deg + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
        return xtile, ytile

    def download_tiles(self, city_name, min_zoom, max_zoom):
        bbox = self.get_city_coords(city_name)
        if not bbox: return
        
        lat_min, lat_max, lon_min, lon_max = bbox

        for zoom in range(min_zoom, max_zoom + 1):
            x1, y1 = self.deg2num(lat_max, lon_min, zoom)
            x2, y2 = self.deg2num(lat_min, lon_max, zoom)
            
            xr, yr = range(min(x1, x2), max(x1, x2) + 1), range(min(y1, y2), max(y1, y2) + 1)
            print(f"\nZoom {zoom}: {len(xr)*len(yr)} tiles", end=" ")
            for x in xr:
                for y in yr:
                    self._save_tile(zoom, x, y)
        
        # SAVE THE MISSING JSON
        self.save_metadata(city_name, bbox, min_zoom, max_zoom)

    def _save_tile(self, z, x, y):
        path = os.path.join(self.output_dir, str(z), str(x), f"{y}.png")
        if os.path.exists(path): return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        res = self.session.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        if res.status_code == 200:
            with open(path, 'wb') as f: f.write(res.content)
            sys.stdout.write("."); sys.stdout.flush()
            time.sleep(self.delay)

    def save_metadata(self, city_name, bbox, min_zoom, max_zoom):
        data = {
            "city": city_name,
            "bbox": bbox,
            "zooms": list(range(min_zoom, max_zoom + 1)),
            "center": [(bbox[0]+bbox[1])/2, (bbox[2]+bbox[3])/2]
        }
        with open(os.path.join(self.output_dir, "map.json"), "w") as f:
            json.dump(data, f, indent=4)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    parser.add_argument("--min-zoom", type=int, default=8)
    parser.add_argument("--max-zoom", type=int, default=12)
    parser.add_argument("--output-dir", default="tiles")
    args = parser.parse_args()

    gen = MeshtasticTileGenerator(os.path.abspath(args.output_dir))
    gen.download_tiles(args.city, args.min_zoom, args.max_zoom)
    print("\nDone!")
