"""
scripts/anchorage/diagnose.py — diagnostic queries for anchorage track data.

Usage:
  python scripts/anchorage/diagnose.py

Prints:
  1. Cross-check with reviewer's 15 ships on 2026-11-11
  2. Histogram of track length_median (sightings≥3) by 10m bins
"""

import os, sys, math
from collections import defaultdict

from supabase import create_client

SUPABASE_URL         = os.environ.get("SUPABASE_URL")        or sys.exit("FATAL: no SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or sys.exit("FATAL: no SUPABASE_SERVICE_KEY")

SMALL_MIN = 100
MAX_LEN_M = 420
VLCC_MIN      = 330
SUEZ_AFRA_MIN = 250

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# reviewer's 15 ships on 2026-11-11
REVIEWER_11 = [
    (29.3204, 50.4678, 303, "SuezAframax"),
    (29.2435, 50.4371, 362, "VLCC"),
    (29.3065, 50.4016, 360, "VLCC"),
    (29.2352, 50.4051, 360, "VLCC"),
    (29.3039, 50.4572, 301, "SuezAframax"),
    (29.2254, 50.4717, 364, "VLCC"),
    (29.3024, 50.3847, 208, "small"),
    (29.2078, 50.4201, 350, "VLCC"),
    (29.3009, 50.4858, 305, "SuezAframax"),
    (29.1719, 50.4043, 140, "small"),
    (29.2805, 50.3973, 278, "SuezAframax"),
    (29.1649, 50.3733, 355, "VLCC"),
    (29.2758, 50.4589, 350, "VLCC"),
    (29.1040, 50.3128, 360, "VLCC"),
    (29.0871, 50.4854, 274, "SuezAframax"),
]


# ── 1. Cross-check on 2026-11-11 ─────────────────────────────────────────────
print("=" * 70)
print("1. Cross-check with reviewer's 15 ships on 2026-11-11")
print("=" * 70)

# fetch all tracks
tracks = {}
p, ps = 0, 1000
while True:
    chunk = sb.table("anchorage_tracks").select(
        "track_id,first_date,last_date,sightings,length_median,class"
    ).range(p, p+ps-1).execute()
    for t in chunk.data:
        tracks[t["track_id"]] = t
    if len(chunk.data) < ps:
        break
    p += ps
print(f"Total tracks in DB: {len(tracks)}\n")

# active on 11-11: spanning tracks (approximation; track_id not on anchorage_ships)
active_11 = [t for t in tracks.values()
             if t["first_date"] <= "2026-11-11" <= t["last_date"]
             and t["length_median"] >= SMALL_MIN]

print(f"Tracks spanning 2026-11-11 with len_median>=100m: {len(active_11)}")
for cls in ["VLCC", "SuezAframax", "small"]:
    n = sum(1 for t in active_11 if t["class"] == cls)
    print(f"  {cls}: {n}")

print()
print(f"{'rev_lat':>9} {'rev_lon':>9}  {'rev_len':>7} {'rev_cls':>12}  "
      f"{'nearest_tid':>11} {'dist_m':>7} {'sights':>6} {'our_med':>7} {'our_cls':>12}  match")
print("-" * 100)

mismatches = []
for (rlat, rlon, rlen, rcls) in REVIEWER_11:
    best = None
    best_d = float("inf")
    for t in active_11:
        d = haversine_m(rlat, rlon, t["lat"] if "lat" in t else 0,
                        t["lon"] if "lon" in t else 0)
        if d < best_d:
            best_d, best = d, t

    # tracks don't have lat/lon stored per-date; use last-known from anchorage_tracks
    # re-query with lat/lon
    best = None
    best_d = float("inf")
    for t in active_11:
        # anchorage_tracks has lat, lon (mean of all sightings) — need to re-fetch
        pass

    # fetch with lat/lon
    break  # will redo below after fetching lat/lon

# fetch with lat/lon
tracks_ll = {}
p = 0
while True:
    chunk = sb.table("anchorage_tracks").select(
        "track_id,first_date,last_date,sightings,length_median,class,lat,lon"
    ).range(p, p+ps-1).execute()
    for t in chunk.data:
        tracks_ll[t["track_id"]] = t
    if len(chunk.data) < ps:
        break
    p += ps

active_11_ll = [t for t in tracks_ll.values()
                if t["first_date"] <= "2026-11-11" <= t["last_date"]
                and t["length_median"] >= SMALL_MIN]

print(f"\n(Re-check with lat/lon) Spanning 2026-11-11, len>=100m: {len(active_11_ll)}")
for cls in ["VLCC", "SuezAframax", "small"]:
    n = sum(1 for t in active_11_ll if t["class"] == cls)
    print(f"  {cls}: {n}")

print()
print(f"{'rev_lat':>9} {'rev_lon':>9}  {'rev_len':>7} {'rev_cls':>12}  "
      f"{'tid':>6} {'dist_m':>7} {'sights':>6} {'our_med':>7} {'our_cls':>12}  match")
print("-" * 100)

for (rlat, rlon, rlen, rcls) in REVIEWER_11:
    best = None
    best_d = float("inf")
    for t in active_11_ll:
        d = haversine_m(rlat, rlon, t["lat"], t["lon"])
        if d < best_d:
            best_d, best = d, t

    if best is None:
        print(f"  {rlat:.4f} {rlon:.4f}  {rlen:7}m {rcls:>12}  NO MATCH")
        continue

    match = "OK" if best["class"] == rcls else "MISMATCH"
    print(f"  {rlat:.4f} {rlon:.4f}  {rlen:7}m {rcls:>12}  "
          f"{best['track_id']:6d} {best_d:7.0f}m {best['sightings']:6d} "
          f"{best['length_median']:7.0f}m {best['class']:>12}  {match}")
    if match == "MISMATCH":
        mismatches.append((rlat, rlon, rlen, rcls, best))

# detail on mismatches
if mismatches:
    print(f"\n── Mismatch details ─────────────────────────────────────────────────")
    for (rlat, rlon, rlen, rcls, t) in mismatches:
        tid = t["track_id"]
        print(f"\ntrack_id={tid}  sightings={t['sightings']}  "
              f"first={t['first_date']}  last={t['last_date']}")
        print(f"  our class={t['class']}  our len_median={t['length_median']:.0f}m")
        print(f"  reviewer: lat={rlat} lon={rlon} len={rlen}m class={rcls}")
        # fetch sighting lengths from anchorage_ships
        hist = sb.table("anchorage_ships").select("date,length_m") \
            .eq("track_id", tid).order("date").execute().data
        if hist:
            lens = [h["length_m"] for h in hist]
            print(f"  sighting lengths: {[int(l) for l in lens]}")
        else:
            print(f"  (track_id not written to anchorage_ships — cannot fetch lengths)")
else:
    print("\nAll 15 reviewer positions match our classification.")


# ── 2. Histogram of track length_median (sightings≥3) ────────────────────────
print("\n" + "=" * 70)
print("2. Histogram of track length_median (sightings≥3), bins 10m")
print("=" * 70)

hist_tracks = sb.table("anchorage_tracks").select("length_median,sightings,class") \
    .gte("sightings", 3).execute().data

bins: dict[int, int] = defaultdict(int)
for t in hist_tracks:
    b = int(t["length_median"] // 10) * 10
    bins[b] += 1

total_hist = sum(bins.values())
print(f"Tracks with sightings≥3: {total_hist}\n")
print(f"{'bin':>7}  {'n':>4}  bar")
for b in sorted(bins):
    bar = "█" * min(bins[b], 60)
    label = ""
    if b >= VLCC_MIN:
        label = " ← VLCC"
    elif b >= SUEZ_AFRA_MIN:
        label = " ← SuezAframax"
    elif b >= SMALL_MIN:
        label = " ← small"
    print(f"  {b:3d}-{b+9:3d}: {bins[b]:3d}  {bar}{label}")

print("\nDone.")
