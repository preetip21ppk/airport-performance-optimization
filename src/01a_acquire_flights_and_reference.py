from __future__ import annotations

import io
import os
import zipfile

import requests

from common import CFG

RAW_F = CFG["paths"]["raw_flights"]
RAW_R = CFG["paths"]["raw_reference"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}

def bts_lookup_token(name: str) -> str:
    out = []
    for ch in name:
        if not ch.isalpha():
            out.append(ch)
            continue
        base = ord("A")
        shifted = (ord(ch.upper()) - base + 13) % 26
        letter = chr(base + shifted)
        wrapped = (ord(ch.upper()) - base) + 13 >= 26
        out.append(letter.lower() if wrapped else letter)
    return "".join(out)

def download(url: str, dest: str, label: str) -> bool:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  cached   {label}  ({os.path.getsize(dest)/1e6:.1f} MB)")
        return True
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with requests.get(url, headers=HEADERS, stream=True, timeout=900) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for block in r.iter_content(chunk_size=1 << 20):
                    fh.write(block)
        print(f"  ok       {label}  ({os.path.getsize(dest)/1e6:.1f} MB)")
        return True
    except Exception as exc:
        print(f"  FAILED   {label}: {exc}")
        if os.path.exists(dest):
            os.remove(dest)
        return False

def acquire_flights() -> None:
    print("\n-- BTS on-time performance (one zip per month) --")
    pattern = CFG["sources"]["flights"]["url_pattern"]
    for m in CFG["window"]["months"]:
        y, mo = m["year"], m["month"]
        url = pattern.format(year=y, month=mo)
        zip_path = os.path.join(RAW_F, f"ontime_{y}_{mo:02d}.zip")
        if not download(url, zip_path, f"on-time {y}-{mo:02d}"):
            continue
        out_dir = os.path.join(RAW_F, f"extract_{y}_{mo:02d}")
        if os.path.isdir(out_dir) and any(f.endswith(".csv") for f in os.listdir(out_dir)):
            print(f"           already extracted -> {os.path.basename(out_dir)}")
            continue
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(out_dir)
        print(f"           extracted -> {os.path.basename(out_dir)}")

def acquire_reference() -> None:
    print("\n-- reference sources --")
    for lookup in ["L_AIRPORT", "L_UNIQUE_CARRIERS", "L_AIRPORT_ID"]:
        token = bts_lookup_token(lookup)
        download(f"https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72={token}",
                 os.path.join(RAW_R, f"{lookup}.csv"), f"BTS {lookup}")

    download(CFG["sources"]["airports"]["url"],
             os.path.join(RAW_R, "ourairports_airports.csv"), "OurAirports airports")
    download("https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat",
             os.path.join(RAW_R, "openflights_airports.dat"), "OpenFlights airports (timezones)")
    download("https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat",
             os.path.join(RAW_R, "openflights_airlines.dat"), "OpenFlights airlines")

    faa_zip = os.path.join(RAW_R, "FAA_ReleasableAircraft.zip")
    if download("https://registry.faa.gov/database/ReleasableAircraft.zip",
                faa_zip, "FAA aircraft registry"):
        faa_dir = os.path.join(RAW_R, "faa")
        if not os.path.exists(os.path.join(faa_dir, "MASTER.txt")):
            with zipfile.ZipFile(faa_zip) as z:
                z.extractall(faa_dir)
            print("           extracted -> faa/ (MASTER.txt, ACFTREF.txt)")
        else:
            print("           already extracted -> faa/")

def main() -> None:
    print("=== STAGE 0: ACQUIRE RAW SOURCES (landing zone, never modified) ===")
    acquire_flights()
    acquire_reference()
    print("\nlanding zone ready. Next: 01b_acquire_weather.py")

if __name__ == "__main__":
    main()
