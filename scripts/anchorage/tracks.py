"""
scripts/anchorage/tracks.py — Tanker Queue track builder.

Reads anchorage_ships(date, lat, lon, length_m) from Supabase.
Produces:
  - anchorage_tracks  (track_id, first_date, last_date, sightings,
                       lat, lon, length_median, class)
  - anchorage_ships.track_id  (column added, then populated)
  - anchorage_queue   (date, total, vlcc, suezaframax, small, arrived, left)

Rules (verbatim from ТЗ):
  Dedup within pass   : merge detections <250 m; len = max(len, dist+halfsum) ≤420 m
  Track association   : <700 m to last position; greedy nearest; max 2-pass gap
  Class               : median(lengths ≤420 m); VLCC≥300, SuezAframax 200-<300,
                        small 100-<200; fixed after 3 sightings
  anchorage_daily     : DO NOT CHANGE

Usage:
  python scripts/anchorage/tracks.py              # full backfill (truncates tables)
  python scripts/anchorage/tracks.py --update     # incremental (new dates only)
  python scripts/anchorage/tracks.py --dry-run    # print stats, no DB write
  python scripts/anchorage/tracks.py --report     # print final report and exit
"""

import os, sys, json, argparse
from collections import defaultdict
from datetime import date as date_type, timedelta

import numpy as np

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")

# ── geometry helpers ──────────────────────────────────────────────────────────

EARTH_R_M = 6_371_000.0

def haversine_m(lat1, lon1, lat2, lon2):
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_R_M * np.arcsin(np.sqrt(a))

def bearing_deg(lat1, lon1, lat2, lon2):
    dlon = np.radians(lon2 - lon1)
    y = np.sin(dlon) * np.cos(np.radians(lat2))
    x = np.cos(np.radians(lat1)) * np.sin(np.radians(lat2)) - np.sin(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.cos(dlon)
    return float(np.degrees(np.arctan2(y, x)) % 360)

# ── constants ─────────────────────────────────────────────────────────────────

DEDUP_RADIUS_M   = 250    # merge detections within this radius
MAX_LEN_M        = 420    # hard cap on ship length
ASSOC_RADIUS_M   = 700    # max distance to link track across passes
MAX_GAP_PASSES   = 2      # max consecutive passes a track may be absent
MIN_FIX_SIGHTINGS = 3     # class is fixed after this many sightings

VLCC_MIN         = 300    # metres
SUEZ_AFRA_MIN    = 200    # metres
SMALL_MIN        = 100    # metres

# ── classification ────────────────────────────────────────────────────────────

def assign_class(lengths):
    """Classify based on median of valid lengths."""
    valid = [l for l in lengths if SMALL_MIN <= l <= MAX_LEN_M]
    if not valid:
        return "small"
    m = float(np.median(valid))
    if m >= VLCC_MIN:
        return "VLCC"
    elif m >= SUEZ_AFRA_MIN:
        return "SuezAframax"
    return "small"

# ── per-pass dedup ────────────────────────────────────────────────────────────

def dedup_pass(detections):
    """
    Merge detections within DEDUP_RADIUS_M.
    Merged length = min(MAX_LEN_M, max(len_a, dist + (len_a+len_b)/2)).
    Returns list of dicts: {lat, lon, length_m, source_ids}.
    """
    remaining = [dict(d, source_ids=[d["id"]]) for d in detections]
    merged = True
    while merged:
        merged = False
        out = []
        used = set()
        for i, a in enumerate(remaining):
            if i in used:
                continue
            group = [a]
            for j, b in enumerate(remaining):
                if j <= i or j in used:
                    continue
                dist = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                if dist < DEDUP_RADIUS_M:
                    group.append(b)
                    used.add(j)
            if len(group) > 1:
                # centroid weighted by length
                total_len = sum(g["length_m"] for g in group)
                if total_len > 0:
                    lat = sum(g["lat"] * g["length_m"] for g in group) / total_len
                    lon = sum(g["lon"] * g["length_m"] for g in group) / total_len
                else:
                    lat = np.mean([g["lat"] for g in group])
                    lon = np.mean([g["lon"] for g in group])
                # merged length rule: max(max_len, dist_between_extremes + halfsum)
                # approximation: use sum of pairwise distances to pick "largest"
                if len(group) == 2:
                    dist01 = haversine_m(group[0]["lat"], group[0]["lon"], group[1]["lat"], group[1]["lon"])
                    merged_len = max(group[0]["length_m"], group[1]["length_m"],
                                     dist01 + (group[0]["length_m"] + group[1]["length_m"]) / 2)
                else:
                    max_pair_dist = 0
                    for gi in range(len(group)):
                        for gj in range(gi+1, len(group)):
                            d2 = haversine_m(group[gi]["lat"], group[gi]["lon"], group[gj]["lat"], group[gj]["lon"])
                            max_pair_dist = max(max_pair_dist, d2)
                    sum_len = sum(g["length_m"] for g in group) / len(group)
                    merged_len = max(max(g["length_m"] for g in group), max_pair_dist + sum_len)
                length_m = min(MAX_LEN_M, merged_len)
                src_ids = []
                for g in group:
                    src_ids.extend(g["source_ids"])
                out.append({"lat": lat, "lon": lon, "length_m": length_m, "source_ids": src_ids})
                merged = True
            else:
                out.append(a)
            used.add(i)
        remaining = out
    return remaining

# ── track association ─────────────────────────────────────────────────────────

def build_tracks(passes_by_date):
    """
    passes_by_date: {date_str: [{lat, lon, length_m, source_ids}, ...]}

    Returns:
      tracks: {track_id: {first_date, last_date, sightings: [(date, lat, lon, length_m)],
                          class, class_fixed, lengths}}
      ship_to_track: {source_id: track_id}
    """
    tracks = {}           # track_id -> track dict
    active = {}           # track_id -> {lat, lon, last_date, gap_count}
    next_track_id = [1]
    ship_to_track = {}

    sorted_dates = sorted(passes_by_date.keys())

    for date_str in sorted_dates:
        detections = passes_by_date[date_str]

        # Age active tracks: increment gap counter for tracks not matched yet
        # (we'll reset it for matched ones below)
        to_delete = []
        for tid in list(active.keys()):
            active[tid]["gap_count"] += 1
            if active[tid]["gap_count"] > MAX_GAP_PASSES:
                to_delete.append(tid)
        for tid in to_delete:
            del active[tid]

        # Greedy nearest-match association
        unmatched_detections = list(detections)
        matched_tids = set()

        # Sort active tracks by nothing — greedy over detections
        for det in list(unmatched_detections):
            best_tid = None
            best_dist = ASSOC_RADIUS_M

            for tid, state in active.items():
                if tid in matched_tids:
                    continue
                dist = haversine_m(det["lat"], det["lon"], state["lat"], state["lon"])
                if dist < best_dist:
                    best_dist = dist
                    best_tid = tid

            if best_tid is not None:
                # Extend existing track
                track = tracks[best_tid]
                track["sightings"].append((date_str, det["lat"], det["lon"], det["length_m"]))
                track["last_date"] = date_str
                track["lengths"].append(det["length_m"])
                # Update class if not yet fixed
                if not track["class_fixed"]:
                    track["class"] = assign_class(track["lengths"])
                    if len(track["sightings"]) >= MIN_FIX_SIGHTINGS:
                        track["class_fixed"] = True
                active[best_tid] = {"lat": det["lat"], "lon": det["lon"],
                                     "last_date": date_str, "gap_count": 0}
                matched_tids.add(best_tid)
                for sid in det["source_ids"]:
                    ship_to_track[sid] = best_tid
                unmatched_detections.remove(det)

        # Create new tracks for unmatched detections
        for det in unmatched_detections:
            tid = next_track_id[0]
            next_track_id[0] += 1
            tracks[tid] = {
                "track_id": tid,
                "first_date": date_str,
                "last_date": date_str,
                "sightings": [(date_str, det["lat"], det["lon"], det["length_m"])],
                "lengths": [det["length_m"]],
                "class": assign_class([det["length_m"]]),
                "class_fixed": False,
            }
            active[tid] = {"lat": det["lat"], "lon": det["lon"],
                           "last_date": date_str, "gap_count": 0}
            for sid in det["source_ids"]:
                ship_to_track[sid] = tid

        # Reset gap_count for tracks matched this pass
        for tid in matched_tids:
            active[tid]["gap_count"] = 0

    return tracks, ship_to_track

# ── anchorage_queue derivation ────────────────────────────────────────────────

def build_queue(tracks, all_dates):
    """
    For each SAR pass date, count active tracks by class.
    Arrived = tracks whose first_date is this date.
    Left     = tracks whose last_date is the previous pass date
               (present on prev but absent on this).
    Returns list of dicts sorted by date.
    """
    sorted_dates = sorted(set(all_dates))

    # active_set[date] = set of track_ids present on that date
    active_set = defaultdict(set)
    for tid, track in tracks.items():
        for sig_date, _, _, _ in track["sightings"]:
            active_set[sig_date].add(tid)

    rows = []
    prev_active = set()
    for i, d in enumerate(sorted_dates):
        cur_active = active_set[d]
        arrived = len(cur_active - prev_active)
        left    = len(prev_active - cur_active)

        vlcc = sum(1 for tid in cur_active if tracks[tid]["class"] == "VLCC")
        suez = sum(1 for tid in cur_active if tracks[tid]["class"] == "SuezAframax")
        small = sum(1 for tid in cur_active if tracks[tid]["class"] == "small")
        total = len(cur_active)

        rows.append({
            "date":        d,
            "total":       total,
            "vlcc":        vlcc,
            "suezaframax": suez,
            "small":       small,
            "arrived":     arrived,
            "left":        left,
        })
        prev_active = cur_active

    return rows

# ── Supabase I/O ──────────────────────────────────────────────────────────────

def get_supabase():
    if not SUPABASE_URL:
        sys.exit("FATAL: SUPABASE_URL not set")
    if not SUPABASE_KEY:
        sys.exit("FATAL: SUPABASE_SERVICE_KEY not set")
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_all_ships(sb, since_date=None):
    """Fetch all rows from anchorage_ships, optionally filtered by date."""
    rows = []
    page_size = 1000
    start = 0
    while True:
        q = sb.table("anchorage_ships").select("id,date,lat,lon,length_m")
        if since_date:
            q = q.gte("date", since_date)
        chunk = q.range(start, start + page_size - 1).execute()
        data = chunk.data or []
        rows.extend(data)
        if len(data) < page_size:
            break
        start += page_size
    return rows

def write_tracks(sb, tracks):
    rows = []
    for tid, t in tracks.items():
        lats = [s[1] for s in t["sightings"]]
        lons = [s[2] for s in t["sightings"]]
        rows.append({
            "track_id":      tid,
            "first_date":    t["first_date"],
            "last_date":     t["last_date"],
            "sightings":     len(t["sightings"]),
            "lat":           float(np.mean(lats)),
            "lon":           float(np.mean(lons)),
            "length_median": float(np.median([l for l in t["lengths"] if SMALL_MIN <= l <= MAX_LEN_M] or t["lengths"])),
            "class":         t["class"],
        })
    batch = 500
    for i in range(0, len(rows), batch):
        sb.table("anchorage_tracks").upsert(rows[i:i+batch]).execute()
    print(f"  Wrote {len(rows)} tracks to anchorage_tracks")

def write_track_ids_to_ships(sb, ship_to_track):
    batch = 500
    items = list(ship_to_track.items())
    for i in range(0, len(items), batch):
        for ship_id, track_id in items[i:i+batch]:
            sb.table("anchorage_ships").update({"track_id": track_id}).eq("id", ship_id).execute()
    print(f"  Updated track_id on {len(ship_to_track)} anchorage_ships rows")

def write_queue(sb, queue_rows):
    batch = 500
    for i in range(0, len(queue_rows), batch):
        sb.table("anchorage_queue").upsert(queue_rows[i:i+batch]).execute()
    print(f"  Wrote {len(queue_rows)} rows to anchorage_queue")

# ── migration SQL (printed, not executed) ─────────────────────────────────────

MIGRATION_SQL = """
-- Run once in Supabase SQL editor before first tracks.py execution

-- 1. Add track_id to anchorage_ships (nullable; filled by tracks.py)
ALTER TABLE anchorage_ships
  ADD COLUMN IF NOT EXISTS track_id INTEGER;

-- 2. Create anchorage_tracks
CREATE TABLE IF NOT EXISTS anchorage_tracks (
  track_id      INTEGER PRIMARY KEY,
  first_date    DATE    NOT NULL,
  last_date     DATE    NOT NULL,
  sightings     INTEGER NOT NULL,
  lat           DOUBLE PRECISION NOT NULL,
  lon           DOUBLE PRECISION NOT NULL,
  length_median DOUBLE PRECISION NOT NULL,
  class         TEXT    NOT NULL
);

-- 3. Create anchorage_queue
CREATE TABLE IF NOT EXISTS anchorage_queue (
  date        DATE    PRIMARY KEY,
  total       INTEGER NOT NULL,
  vlcc        INTEGER NOT NULL,
  suezaframax INTEGER NOT NULL,
  small       INTEGER NOT NULL,
  arrived     INTEGER NOT NULL,
  left        INTEGER NOT NULL
);
"""

# ── main ──────────────────────────────────────────────────────────────────────

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
ANCHORAGE_JSON = os.path.join(ROOT_DIR, "data", "anchorage.json")


def export_json(sb):
    """Export anchorage_queue to data/anchorage.json (all history)."""
    rows = []
    page_size = 1000
    start = 0
    while True:
        chunk = sb.table("anchorage_queue").select("*").order("date").range(start, start + page_size - 1).execute()
        data = chunk.data or []
        rows.extend(data)
        if len(data) < page_size:
            break
        start += page_size

    from datetime import datetime as _dt
    out = {
        "updated": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "records": rows,
    }
    os.makedirs(os.path.dirname(ANCHORAGE_JSON), exist_ok=True)
    with open(ANCHORAGE_JSON, "w") as f:
        json.dump(out, f)
    print(f"  Exported {len(rows)} rows to data/anchorage.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update",   action="store_true", help="Incremental: new dates only")
    ap.add_argument("--dry-run",  action="store_true", help="Compute only; no DB writes")
    ap.add_argument("--report",   action="store_true", help="Print report and exit")
    ap.add_argument("--migration",action="store_true", help="Print migration SQL and exit")
    ap.add_argument("--export",   action="store_true", help="Export anchorage_queue to data/anchorage.json and exit")
    args = ap.parse_args()

    if args.migration:
        print(MIGRATION_SQL)
        return

    sb = get_supabase()

    if args.export:
        print("Exporting anchorage_queue to data/anchorage.json…")
        export_json(sb)
        return

    # ── fetch ships ──────────────────────────────────────────────────────────
    since = None
    if args.update:
        # find latest date in anchorage_queue to avoid reprocessing
        result = sb.table("anchorage_queue").select("date").order("date", desc=True).limit(1).execute()
        if result.data:
            since = result.data[0]["date"]
            print(f"  Incremental mode: processing dates ≥ {since}")

    print("Fetching anchorage_ships…")
    ships = fetch_all_ships(sb, since_date=since)
    print(f"  {len(ships)} rows loaded")

    if not ships:
        print("No data to process.")
        return

    # ── group by date and dedup ───────────────────────────────────────────────
    by_date = defaultdict(list)
    for s in ships:
        by_date[s["date"]].append(s)

    passes_by_date = {}
    total_before = 0
    total_after  = 0
    for d, dets in sorted(by_date.items()):
        total_before += len(dets)
        deduped = dedup_pass(dets)
        total_after += len(deduped)
        passes_by_date[d] = deduped

    print(f"  Dedup: {total_before} detections → {total_after} after merging duplicates ({total_before - total_after} merged)")

    # ── build tracks ─────────────────────────────────────────────────────────
    print("Building tracks…")
    tracks, ship_to_track = build_tracks(passes_by_date)
    print(f"  {len(tracks)} tracks built from {len(sorted(by_date))} passes")

    # ── report ───────────────────────────────────────────────────────────────
    if args.report or True:  # always print summary
        sorted_dates = sorted(by_date.keys())
        all_classes = [t["class"] for t in tracks.values()]
        vlcc_count  = all_classes.count("VLCC")
        suez_count  = all_classes.count("SuezAframax")
        small_count = all_classes.count("small")

        long_tracks = [t for t in tracks.values()
                       if (date_type.fromisoformat(t["last_date"]) -
                           date_type.fromisoformat(t["first_date"])).days >= 30]

        # last 6 passes
        last6 = sorted_dates[-6:] if len(sorted_dates) >= 6 else sorted_dates
        active_set = defaultdict(set)
        for tid, track in tracks.items():
            for sig_date, _, _, _ in track["sightings"]:
                active_set[sig_date].add(tid)

        print("\n── Report ──────────────────────────────────────────────────────")
        print(f"  Total tracks     : {len(tracks)}")
        print(f"  VLCC             : {vlcc_count}")
        print(f"  SuezAframax      : {suez_count}")
        print(f"  small            : {small_count}")
        print(f"  Merged duplicates: {total_before - total_after}")
        print(f"  Ships >30 days   : {len(long_tracks)}")
        print(f"\n  Last {len(last6)} passes:")
        for d in last6:
            cur = active_set[d]
            v = sum(1 for t in cur if tracks[t]["class"] == "VLCC")
            s = sum(1 for t in cur if tracks[t]["class"] == "SuezAframax")
            sm = sum(1 for t in cur if tracks[t]["class"] == "small")
            print(f"    {d}: total={len(cur)}  VLCC={v}  SA={s}  sm={sm}")
        print("────────────────────────────────────────────────────────────────")

    if args.report:
        return

    # ── build queue ───────────────────────────────────────────────────────────
    queue_rows = build_queue(tracks, list(by_date.keys()))

    # ── write to DB ───────────────────────────────────────────────────────────
    print("\nWriting to Supabase…")
    write_tracks(sb, tracks)
    write_track_ids_to_ships(sb, ship_to_track)
    write_queue(sb, queue_rows)

    # export anchorage.json after full rebuild
    if not args.update:
        export_json(sb)

    print("Done.")


if __name__ == "__main__":
    main()
