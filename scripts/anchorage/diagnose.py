"""
scripts/anchorage/diagnose.py — diagnostic queries for anchorage track data.

Usage:
  python scripts/anchorage/diagnose.py

Prints:
  1. Two closest post-dedup detections on 2026-11-11 (extra ship analysis)
  2. Tracks last seen 2026-11-11 (departed on 2026-11-17): history + nearest 11-17 detection
  3. Histogram of track length_median (sightings≥3) by 10m bins
"""

import os, sys, math
from collections import defaultdict

from supabase import create_client

SUPABASE_URL         = os.environ.get("SUPABASE_URL")        or sys.exit("FATAL: no SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or sys.exit("FATAL: no SUPABASE_SERVICE_KEY")
DEDUP_RADIUS_M = 250

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def dedup_pass(dets):
    remaining = [dict(d) for d in dets]
    merged = True
    while merged:
        merged = False
        out, used = [], set()
        for i, a in enumerate(remaining):
            if i in used:
                continue
            group = [a]
            for j, b in enumerate(remaining):
                if j <= i or j in used:
                    continue
                if haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= DEDUP_RADIUS_M:
                    group.append(b)
                    used.add(j)
            if len(group) > 1:
                merged = True
            lat = sum(g["lat"] for g in group) / len(group)
            lon = sum(g["lon"] for g in group) / len(group)
            lm  = max(g["length_m"] for g in group)
            out.append({"lat": lat, "lon": lon, "length_m": lm})
            used.add(i)
        remaining = out
    return remaining


# ── 1. Closest pair on 2026-11-11 ────────────────────────────────────────────
print("=" * 60)
print("1. Closest pair on 2026-11-11")
print("=" * 60)

raw11 = sb.table("anchorage_ships").select("lat,lon,length_m").eq("date", "2026-11-11").execute().data
ded11 = dedup_pass(raw11)
print(f"Raw: {len(raw11)}  After dedup: {len(ded11)}")

min_dist, pair = float("inf"), None
for i, a in enumerate(ded11):
    for j, b in enumerate(ded11):
        if j <= i:
            continue
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        if d < min_dist:
            min_dist, pair = d, (a, b)

if pair:
    a, b = pair
    print(f"\nClosest pair (dist={min_dist:.0f}m):")
    print(f"  A: lat={a['lat']:.6f}  lon={a['lon']:.6f}  length={a['length_m']:.0f}m")
    print(f"  B: lat={b['lat']:.6f}  lon={b['lon']:.6f}  length={b['length_m']:.0f}m")

# also show all pairs <700m
print("\nAll pairs <700m:")
found_any = False
for i, a in enumerate(ded11):
    for j, b in enumerate(ded11):
        if j <= i:
            continue
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        if d < 700:
            found_any = True
            print(f"  {d:5.0f}m  A({a['lat']:.5f},{a['lon']:.5f},{a['length_m']:.0f}m)"
                  f"  B({b['lat']:.5f},{b['lon']:.5f},{b['length_m']:.0f}m)")
if not found_any:
    print("  none")


# ── 2. Departed tracks on 2026-11-17 ─────────────────────────────────────────
print("\n" + "=" * 60)
print("2. Tracks last seen 2026-11-11 (departed on 2026-11-17)")
print("=" * 60)

dep_tracks = sb.table("anchorage_tracks").select("*").eq("last_date", "2026-11-11").execute().data
print(f"Tracks with last_date=2026-11-11: {len(dep_tracks)}")

raw17 = sb.table("anchorage_ships").select("lat,lon,length_m").eq("date", "2026-11-17").execute().data
ded17 = dedup_pass(raw17)
print(f"2026-11-17 post-dedup: {len(ded17)} detections\n")

for t in dep_tracks:
    nearest = min(haversine_m(t["lat"], t["lon"], d["lat"], d["lon"]) for d in ded17)
    print(f"Track {t['track_id']}: class={t['class']}  sightings={t['sightings']}"
          f"  first={t['first_date']}  last={t['last_date']}")
    print(f"  last_pos: lat={t['lat']:.6f}  lon={t['lon']:.6f}  len_median={t['length_median']:.0f}m")
    print(f"  nearest 11-17 detection: {nearest:.0f}m")

    # sightings history
    hist = sb.table("anchorage_ships").select("date,lat,lon,length_m") \
        .eq("track_id", t["track_id"]).order("date").execute().data
    if hist:
        print(f"  history ({len(hist)} sightings):")
        for h in hist[-6:]:  # last 6
            print(f"    {h['date']}: lat={h['lat']:.5f} lon={h['lon']:.5f} len={h['length_m']:.0f}m")
    print()


# ── 3. Histogram of track length_median (sightings≥3) ────────────────────────
print("=" * 60)
print("3. Histogram of track length_median (sightings≥3), bins 10m")
print("=" * 60)

tracks = sb.table("anchorage_tracks").select("length_median,sightings,class") \
    .gte("sightings", 3).execute().data

bins: dict[int, int] = defaultdict(int)
for t in tracks:
    b = int(t["length_median"] // 10) * 10
    bins[b] += 1

total_hist = sum(bins.values())
print(f"Tracks with sightings≥3: {total_hist}\n")
print(f"{'bin':>7}  {'n':>4}  bar")
for b in sorted(bins):
    bar = "█" * min(bins[b], 60)
    label = ""
    if b >= 330:
        label = " ← VLCC"
    elif b >= 250:
        label = " ← SuezAframax"
    elif b >= 100:
        label = " ← small"
    print(f"  {b:3d}-{b+9:3d}: {bins[b]:3d}  {bar}{label}")

print("\nDone.")
