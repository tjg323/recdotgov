#!/usr/bin/env python3
"""
fetch.py - Fetch availability data from Recreation.gov

1. Auto-downloads campground IDs within specified miles of a location from RIDB if download.csv doesn't exist
2. Fetches one month of availability for each campground
3. Rate-limits with randomized delays to appear more human-like
4. Uses rotating user agents to appear as browser traffic
5. Produces a single JSON object mapping FacilityID → availability payload

Usage:
    python fetch.py 2025-08                                    # Fetch August 2025 data (sequential, SF default)
    python fetch.py 2025-08 --parallel                         # Fetch with parallel requests (faster but may hit rate limits)
    python fetch.py --build-csv                                # Force rebuild download.csv from RIDB data (150 miles from SF)
    python fetch.py --build-csv --distance 75                  # Build CSV with campgrounds within 75 miles of SF
    python fetch.py --build-csv --location "South Lake Tahoe"  # Build CSV around South Lake Tahoe
    python fetch.py 2025-08 --distance 100 --location "Yosemite"  # Fetch data for campgrounds within 100 miles of Yosemite

Requirements:
    pip install requests pandas tqdm

Creates:
    download.csv              # Campgrounds within specified miles of location (auto-generated)
    temp/avail_<ID>.json     # Individual availability files
    all_avail_<MONTH>.json   # Merged availability data
"""

import csv
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import requests
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import io
import zipfile
import pandas as pd
from tqdm import tqdm
import math

# Constants for RIDB data
RIDB_URL = "https://ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip"
ZIP_NAME = "RIDBFullExport_V1_CSV.zip"
FAC_CSV = "Facilities_API_v1.csv"
ADDR_CSV = "FacilityAddresses_API_v1.csv"
DOWNLOAD_CSV = "download.csv"

# Default location: San Francisco coordinates (approximately downtown)
DEFAULT_LAT = 37.7749
DEFAULT_LON = -122.4194
MAX_DISTANCE_MILES = 150

# User agents to rotate through for human-like requests
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
]


def get_random_headers() -> Dict[str, str]:
    """Generate browser-like headers with a random user agent."""
    return {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
        "User-Agent": random.choice(USER_AGENTS)
    }


def geocode_location(location: str) -> Tuple[float, float]:
    """
    Geocode a location name to latitude and longitude using a free geocoding service.
    Returns (latitude, longitude) tuple.
    """
    # Using Nominatim (OpenStreetMap) free geocoding service
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        'q': location,
        'format': 'json',
        'limit': 1,
        'countrycodes': 'us'  # Limit to US since this is for US campgrounds
    }
    headers = {
        'User-Agent': 'campground-finder/1.0 (https://github.com/user/repo)'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        
        if not results:
            raise ValueError(f"Location '{location}' not found")
        
        result = results[0]
        lat = float(result['lat'])
        lon = float(result['lon'])
        
        print(f"[✓] Geocoded '{location}' to ({lat:.4f}, {lon:.4f})")
        return lat, lon
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to geocode '{location}': {e}")
    except (KeyError, ValueError, IndexError) as e:
        raise ValueError(f"Invalid geocoding response for '{location}': {e}")


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points on Earth in miles."""
    # Convert latitude and longitude from degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    # Radius of earth in miles
    r = 3956
    
    return c * r


def random_sleep(base_delay: float = 0.6) -> None:
    """Sleep for a randomized duration to appear more human-like."""
    # Add random variation: 80% to 150% of base delay
    variation = random.uniform(0.8, 1.5)
    delay = base_delay * variation
    # Add occasional longer pauses (5% chance)
    if random.random() < 0.05:
        delay += random.uniform(2, 5)
    time.sleep(delay)


def fetch_ridb_zip(url: str, local_name: str) -> Path:
    """Download the RIDB ZIP file if not already on disk."""
    p = Path(local_name)
    if p.exists():
        print(f"[✓] Using cached {local_name} ({p.stat().st_size/1e6:.1f} MB)")
        return p

    print(f"[→] Downloading {url} ...")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        with open(p, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc="download", ncols=80
        ) as bar:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                bar.update(len(chunk))
    return p


def build_download_csv(start_zip: Path, max_distance: float = MAX_DISTANCE_MILES, location: Optional[str] = None) -> None:
    """Build download.csv from RIDB data - campgrounds within specified miles of given location."""
    with zipfile.ZipFile(start_zip) as z:
        print("[→] Reading Facilities …")
        fac = pd.read_csv(
            z.open(FAC_CSV),
            usecols=[
                "FacilityID",
                "FacilityName", 
                "FacilityTypeDescription",
                "Reservable",
                "FacilityLatitude",
                "FacilityLongitude",
            ],
        )

        print("[→] Reading Addresses …")
        addr = pd.read_csv(
            z.open(ADDR_CSV),
            usecols=["FacilityID", "AddressStateCode"],
        )

    print("[→] Filtering reservable campgrounds …")
    # Filter out boat/sailing facilities by name patterns
    boat_patterns = r'(?i)(boat|sailing|aquatic|anchor|marina|pier|dock|vessel)'
    
    camp = fac[
        (fac["FacilityTypeDescription"] == "Campground")
        & (fac["Reservable"].astype(str).str.lower() == "true")
        & (fac["FacilityLatitude"].notna())
        & (fac["FacilityLongitude"].notna())
        & (~fac["FacilityName"].str.contains(boat_patterns, na=False))
    ]

    print("[→] Joining with address data …")
    # Ensure FacilityID columns have the same data type
    camp['FacilityID'] = camp['FacilityID'].astype(str)
    addr['FacilityID'] = addr['FacilityID'].astype(str)
    
    merged = camp.merge(addr, on="FacilityID", how="inner")
    
    # Determine center coordinates
    if location:
        center_lat, center_lon = geocode_location(location)
        location_name = location
    else:
        center_lat, center_lon = DEFAULT_LAT, DEFAULT_LON
        location_name = "San Francisco"
    
    print(f"[→] Calculating distances from {location_name} ({center_lat:.4f}, {center_lon:.4f}) …")
    # Calculate distance for each campground
    merged['distance_miles'] = merged.apply(
        lambda row: haversine_distance(
            center_lat, center_lon, 
            float(row['FacilityLatitude']), float(row['FacilityLongitude'])
        ), axis=1
    )
    
    print(f"[→] Filtering campgrounds within {max_distance} miles of {location_name} …")
    nearby = merged[merged['distance_miles'] <= max_distance]
    
    # Select final columns and sort
    result = (
        nearby[["FacilityID", "FacilityName", "AddressStateCode", "distance_miles"]]
        .drop_duplicates()
        .sort_values("distance_miles")
        .reset_index(drop=True)
    )

    print(f"[✓] Writing {DOWNLOAD_CSV} ({len(result)} rows)")
    # Save with distance info for reference
    result.to_csv(DOWNLOAD_CSV, index=False)
    
    states = result['AddressStateCode'].dropna().unique()
    print(f"[✓] Found campgrounds in states: {sorted(states)}")
    print(f"[✓] Distance range: {result['distance_miles'].min():.1f} - {result['distance_miles'].max():.1f} miles")


def ensure_download_csv(max_distance: float = MAX_DISTANCE_MILES, location: Optional[str] = None) -> None:
    """Ensure download.csv exists, creating it from RIDB data if needed."""
    if Path(DOWNLOAD_CSV).exists():
        print(f"[✓] Using existing {DOWNLOAD_CSV}")
        return
    
    print(f"[!] {DOWNLOAD_CSV} not found, building from RIDB data...")
    zip_path = fetch_ridb_zip(RIDB_URL, ZIP_NAME)
    build_download_csv(zip_path, max_distance, location)


def read_facility_ids(csv_file: str = DOWNLOAD_CSV) -> List[str]:
    """Read facility IDs from CSV file."""
    ids = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            if row:  # Ensure row is not empty
                ids.append(row[0])
    return ids


def fetch_availability(facility_id: str, month: str, temp_dir: Path, session: requests.Session) -> bool:
    """
    Fetch availability data for a single facility.
    Returns True if successful, False otherwise.
    """
    output_file = temp_dir / f"avail_{facility_id}.json"
    temp_file = temp_dir / f"avail_{facility_id}.json.tmp"
    
    # Skip if already exists and is non-empty
    if output_file.exists() and output_file.stat().st_size > 0:
        print(f"skipped (already exists)")
        return True
    
    # Construct URL with properly encoded date
    start_date = f"{month}-01T00:00:00.000Z"
    url = f"https://www.recreation.gov/api/camps/availability/campground/{facility_id}/month?start_date={quote(start_date)}"
    
    try:
        # Make request with browser-like headers
        response = session.get(
            url,
            headers=get_random_headers(),
            timeout=30,
            allow_redirects=True
        )
        
        # Handle rate limiting
        if response.status_code == 429:
            print(f"rate limited (429)")
            if temp_file.exists():
                temp_file.unlink()
            return False
        
        response.raise_for_status()
        
        # Save to temporary file first
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(response.json(), f)
        
        # Move to final location (atomic operation)
        temp_file.rename(output_file)
        print("done")
        return True
        
    except requests.exceptions.HTTPError as e:
        print(f"failed (HTTP {e.response.status_code})")
        # Clean up any partial file
        if temp_file.exists():
            temp_file.unlink()
        return False
    except requests.exceptions.RequestException as e:
        print(f"failed ({type(e).__name__})")
        # Clean up any partial file
        if temp_file.exists():
            temp_file.unlink()
        return False


def merge_availability_files(temp_dir: Path, month: str) -> None:
    """Merge individual availability JSON files into a single file."""
    output_file = Path(f"all_avail_{month}.json")
    temp_merged = output_file.with_suffix('.json.tmpmerge')
    
    # Find all availability files
    avail_files = list(temp_dir.glob('avail_*.json'))
    
    if not avail_files:
        print(f"No avail_*.json files found to merge. Creating an empty JSON object: {output_file}")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({}, f)
        return
    
    print(f"Found {len(avail_files)} 'avail_*.json' file(s) to merge.")
    
    # Build merged JSON object
    merged_data = {}
    
    for avail_file in avail_files:
        # Skip empty files
        if avail_file.stat().st_size == 0:
            print(f"Warning: Skipping empty file during merge: {avail_file.name}")
            continue
        
        # Extract ID from filename
        facility_id = avail_file.stem.replace('avail_', '')
        
        try:
            with open(avail_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                merged_data[facility_id] = data
        except json.JSONDecodeError:
            print(f"Warning: Skipping invalid JSON file: {avail_file.name}")
            continue
    
    # Write merged data
    with open(temp_merged, 'w', encoding='utf-8') as f:
        json.dump(merged_data, f)
    
    # Atomically replace
    temp_merged.rename(output_file)
    print(f"Merged availability written to {output_file}")


def fetch_parallel(facility_ids: List[str], month: str, temp_dir: Path, max_workers: int = 10) -> None:
    """Fetch availability data in parallel."""
    print(f"\nUsing parallel mode with {max_workers} workers")
    
    # Create a session for each thread
    def worker(facility_id: str, i: int, total: int):
        session = requests.Session()
        print(f"[{i:4d}/{total:4d}] ID {facility_id} ... ", end='', flush=True)
        return fetch_availability(facility_id, month, temp_dir, session)
    
    failed_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_id = {
            executor.submit(worker, fid, i, len(facility_ids)): (fid, i) 
            for i, fid in enumerate(facility_ids, 1)
        }
        
        # Process completed tasks
        for future in as_completed(future_to_id):
            facility_id, i = future_to_id[future]
            try:
                success = future.result()
                if not success:
                    failed_count += 1
            except Exception as e:
                print(f"[{i:4d}/{len(facility_ids):4d}] ID {facility_id} ... failed ({type(e).__name__})")
                failed_count += 1
    
    if failed_count > 0:
        print(f"\nWarning: {failed_count} downloads failed")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Fetch campground availability data from Recreation.gov")
    parser.add_argument("month", nargs="?", help="Month to fetch (YYYY-MM format)")
    parser.add_argument("--parallel", action="store_true", help="Use parallel requests (faster but may hit rate limits)")
    parser.add_argument("--build-csv", action="store_true", help="Force rebuild download.csv from RIDB data")
    parser.add_argument("--distance", type=float, default=MAX_DISTANCE_MILES, 
                       help=f"Maximum distance in miles (default: {MAX_DISTANCE_MILES})")
    parser.add_argument("--location", type=str, default=None,
                       help="Location to search around (default: San Francisco). Examples: 'South Lake Tahoe', 'Yosemite', 'Los Angeles'")
    
    args = parser.parse_args()
    
    # Handle special case for building CSV
    if args.build_csv:
        location_text = args.location if args.location else "San Francisco"
        print(f"Building download.csv from RIDB data (max distance: {args.distance} miles from {location_text})...")
        zip_path = fetch_ridb_zip(RIDB_URL, ZIP_NAME)
        build_download_csv(zip_path, args.distance, args.location)
        print("Done!")
        return
    
    if not args.month:
        parser.error("Month is required unless using --build-csv")
    
    month = args.month
    parallel = args.parallel
    
    # Create temp directory
    temp_dir = Path("temp")
    temp_dir.mkdir(exist_ok=True)
    
    # Ensure download.csv exists with the specified distance and location
    ensure_download_csv(args.distance, args.location)
    
    print("Reading IDs from download.csv...")
    facility_ids = read_facility_ids()
    total = len(facility_ids)
    print(f"Found {total} Facility IDs")
    
    if parallel:
        # Parallel mode
        fetch_parallel(facility_ids, month, temp_dir)
    else:
        # Sequential mode (default)
        # Create a session for connection pooling
        session = requests.Session()
        
        # Fetch loop
        for i, facility_id in enumerate(facility_ids, 1):
            print(f"[{i:4d}/{total:4d}] ID {facility_id} ... ", end='', flush=True)
            
            success = fetch_availability(facility_id, month, temp_dir, session)
            
            if not success:
                # Exit on failure like the bash script
                sys.exit(1)
            
            # Random delay between requests
            if i < total:  # Don't sleep after the last request
                random_sleep()
    
    print()  # New line for better output separation
    print(f"Merging into all_avail_{month}.json ...")
    merge_availability_files(temp_dir, month)
    
    print("\nExample query:")
    print(f'  jq \'.[\"232450\"].campsites | keys[0]\' all_avail_{month}.json')


if __name__ == "__main__":
    main()