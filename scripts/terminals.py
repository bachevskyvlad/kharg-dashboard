"""
terminals.py — Tank inventories for Gulf terminals beyond Kharg Island.

Each terminal dict contains:
  id          — unique terminal identifier (used as terminal_id in Supabase)
  name        — human-readable name
  country     — country code
  los_azimuth — Sentinel-1 line-of-sight azimuth for crescent mask direction
  tanks       — list of tank dicts with: id, lat, lon, radius (metres), h_max (metres)

Radius = distance from centre to tank wall edge (not diameter).
h_max sourced from API 650 standard aspect ratios for each radius group.

Notes:
  - Al Flaw: Iraqi crude terminal, coordinates from satellite mapping
  - Lavan: Iranian island terminal (Lavan Island), smaller tanks
  - Siri: Iranian island terminal (Siri Island)
  - Jask: Iranian terminal near Strait of Hormuz mouth
    * tanks marked uncertain=True may not yet be operational
"""

TERMINALS = [
    {
        "id": "al_flaw",
        "name": "Al Flaw",
        "country": "IQ",
        "los_azimuth": 282,
        "tanks": [
            # Group 1 (8 tanks)
            {"id": "af_0",  "lat": 29.976037712391882, "lon": 48.44195552300743, "radius": 40, "h_max": 14},
            {"id": "af_1",  "lat": 29.97446023561727,  "lon": 48.44041073298363, "radius": 40, "h_max": 14},
            {"id": "af_2",  "lat": 29.973482237683964, "lon": 48.44170682926327, "radius": 40, "h_max": 14},
            {"id": "af_3",  "lat": 29.97506237979022,  "lon": 48.443256604287775,"radius": 40, "h_max": 14},
            {"id": "af_4",  "lat": 29.972052466075166, "lon": 48.44360540262179, "radius": 40, "h_max": 14},
            {"id": "af_5",  "lat": 29.973613605327138, "lon": 48.44517136039961, "radius": 40, "h_max": 14},
            {"id": "af_6",  "lat": 29.971043737749962, "lon": 48.44490239092673, "radius": 40, "h_max": 14},
            {"id": "af_7",  "lat": 29.972645241700267, "lon": 48.44646203210755, "radius": 40, "h_max": 14},
            # Group 2 (6 tanks)
            {"id": "af_8",  "lat": 29.970602225788706, "lon": 48.44914349229505, "radius": 40, "h_max": 14},
            {"id": "af_9",  "lat": 29.96964506640903,  "lon": 48.45042448972437, "radius": 40, "h_max": 14},
            {"id": "af_10", "lat": 29.969053578374012, "lon": 48.44757323726147, "radius": 40, "h_max": 14},
            {"id": "af_11", "lat": 29.968074369587153, "lon": 48.44888119527686, "radius": 40, "h_max": 14},
            {"id": "af_12", "lat": 29.967562585491844, "lon": 48.44613477210609, "radius": 40, "h_max": 14},
            {"id": "af_13", "lat": 29.966616705980595, "lon": 48.44740303358956, "radius": 40, "h_max": 14},
            # Group 3 (4 tanks)
            {"id": "af_14", "lat": 29.965160381102958, "lon": 48.449324515383225,"radius": 40, "h_max": 14},
            {"id": "af_15", "lat": 29.966613187227228, "lon": 48.45079062045803, "radius": 40, "h_max": 14},
            {"id": "af_16", "lat": 29.964164829146835, "lon": 48.450625111425616,"radius": 40, "h_max": 14},
            {"id": "af_17", "lat": 29.965658494437534, "lon": 48.45208349893114, "radius": 40, "h_max": 14},
            # Group 4 (6 tanks)
            {"id": "af_18", "lat": 29.961779354173064, "lon": 48.45380166725049, "radius": 40, "h_max": 14},
            {"id": "af_19", "lat": 29.96274476143991,  "lon": 48.45253449582525, "radius": 40, "h_max": 14},
            {"id": "af_20", "lat": 29.964218037290507, "lon": 48.45399437056702, "radius": 40, "h_max": 14},
            {"id": "af_21", "lat": 29.96326674821542,  "lon": 48.45527466098349, "radius": 40, "h_max": 14},
            {"id": "af_22", "lat": 29.964822804270934, "lon": 48.45681522152186, "radius": 40, "h_max": 14},
            {"id": "af_23", "lat": 29.96580178961507,  "lon": 48.45553712042206, "radius": 40, "h_max": 14},
        ],
    },

    {
        "id": "lavan",
        "name": "Lavan Island",
        "country": "IR",
        "los_azimuth": 282,
        "tanks": [
            # r=20m tanks
            {"id": "lv_0", "lat": 26.79900382074584,  "lon": 53.35353039022181, "radius": 20, "h_max": 9},
            {"id": "lv_1", "lat": 26.798479852549846, "lon": 53.35358181469451, "radius": 20, "h_max": 9},
            {"id": "lv_2", "lat": 26.799107534277073, "lon": 53.35465579553378, "radius": 20, "h_max": 9},
            {"id": "lv_3", "lat": 26.798563876628965, "lon": 53.35471130709531, "radius": 20, "h_max": 9},
            # r=15m tanks
            {"id": "lv_4", "lat": 26.79833375021648,  "lon": 53.35236086038317, "radius": 15, "h_max": 7},
            {"id": "lv_5", "lat": 26.798934023118402, "lon": 53.35229752236978, "radius": 15, "h_max": 7},
            {"id": "lv_6", "lat": 26.79827467700471,  "lon": 53.35183098569965, "radius": 15, "h_max": 7},
            {"id": "lv_7", "lat": 26.798884861477664, "lon": 53.35177266861828, "radius": 15, "h_max": 7},
            {"id": "lv_8", "lat": 26.799137962674727, "lon": 53.347484855698475,"radius": 15, "h_max": 7},
            # r=25m tanks
            {"id": "lv_9",  "lat": 26.797287666825397, "lon": 53.353602092280966,"radius": 25, "h_max": 10},
            {"id": "lv_10", "lat": 26.797370393243344, "lon": 53.3542855443725,  "radius": 25, "h_max": 10},
            {"id": "lv_11", "lat": 26.797431880634935, "lon": 53.35497621233966, "radius": 25, "h_max": 10},
            {"id": "lv_12", "lat": 26.796139645955098, "lon": 53.35364409181301, "radius": 25, "h_max": 10},
            {"id": "lv_13", "lat": 26.796205560719393, "lon": 53.35442256036965, "radius": 25, "h_max": 10},
            {"id": "lv_14", "lat": 26.79628024605769,  "lon": 53.35518798806746, "radius": 25, "h_max": 10},
            {"id": "lv_15", "lat": 26.799086207709394, "lon": 53.34675495548526, "radius": 25, "h_max": 10},
            # r=55m tanks
            {"id": "lv_16", "lat": 26.80600956029062,  "lon": 53.33853345571229, "radius": 55, "h_max": 17},
            {"id": "lv_17", "lat": 26.8061274882919,   "lon": 53.3403030081189,  "radius": 55, "h_max": 17},
            # r=35m tanks
            {"id": "lv_18", "lat": 26.803974628177848, "lon": 53.33872987361962, "radius": 35, "h_max": 13},
            {"id": "lv_19", "lat": 26.80412236607243,  "lon": 53.34052628117685, "radius": 35, "h_max": 13},
            {"id": "lv_20", "lat": 26.802350551222506, "lon": 53.33891450240212, "radius": 35, "h_max": 13},
            {"id": "lv_21", "lat": 26.802499303852,    "lon": 53.34067630656812, "radius": 35, "h_max": 13},
            {"id": "lv_22", "lat": 26.800756760805854, "lon": 53.33906528744336, "radius": 35, "h_max": 13},
            {"id": "lv_23", "lat": 26.800933849735905, "lon": 53.340866771883356,"radius": 35, "h_max": 13},
        ],
    },

    {
        "id": "siri",
        "name": "Siri Island",
        "country": "IR",
        "los_azimuth": 282,
        "tanks": [
            # r=55m tanks
            {"id": "si_0", "lat": 25.906493850156608, "lon": 54.537083188997116, "radius": 55, "h_max": 17},
            {"id": "si_1", "lat": 25.904546808294974, "lon": 54.535470599866656, "radius": 55, "h_max": 17},
            {"id": "si_2", "lat": 25.90669475757451,  "lon": 54.53450541965348,  "radius": 55, "h_max": 17},
            # r=40m tanks
            {"id": "si_3", "lat": 25.90544779314368,  "lon": 54.53352182244191,  "radius": 40, "h_max": 14},
            {"id": "si_4", "lat": 25.907888267412808, "lon": 54.535572815324926, "radius": 40, "h_max": 14},
            {"id": "si_5", "lat": 25.907379839437166, "lon": 54.53236712959832,  "radius": 40, "h_max": 14},
        ],
    },

    {
        "id": "jask",
        "name": "Jask",
        "country": "IR",
        "los_azimuth": 282,
        "tanks": [
            # r=35m tanks (confirmed operational)
            {"id": "jk_0", "lat": 25.854547959796683, "lon": 57.30425500256653, "radius": 35, "h_max": 13},
            {"id": "jk_1", "lat": 25.854285897427793, "lon": 57.30285809909571, "radius": 35, "h_max": 13},
            {"id": "jk_2", "lat": 25.853004240288325, "lon": 57.303153860416884,"radius": 35, "h_max": 13},
            {"id": "jk_3", "lat": 25.853270400262147, "lon": 57.30455531406189, "radius": 35, "h_max": 13},
            {"id": "jk_4", "lat": 25.861242540725943, "lon": 57.30140013397687, "radius": 35, "h_max": 13},
            {"id": "jk_5", "lat": 25.859992238216282, "lon": 57.301668785557,   "radius": 35, "h_max": 13},
            {"id": "jk_6", "lat": 25.860219021790332, "lon": 57.30306060943653, "radius": 35, "h_max": 13},
            {"id": "jk_7", "lat": 25.861503877842335, "lon": 57.30277881775879, "radius": 35, "h_max": 13},
            # r=35m tanks (uncertain — may not yet be operational)
            {"id": "jk_8",  "lat": 25.853687716748603, "lon": 57.30698588912281, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_9",  "lat": 25.853928243776394, "lon": 57.30841264827629, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_10", "lat": 25.855236751552415, "lon": 57.30810298608718, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_11", "lat": 25.854958644693784, "lon": 57.30673002892039, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_12", "lat": 25.86191666534025,  "lon": 57.305243449053705,"radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_13", "lat": 25.86220236992449,  "lon": 57.30664059485674, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_14", "lat": 25.860673586711076, "lon": 57.305524427309486,"radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_15", "lat": 25.860904481636084, "lon": 57.30691674803369, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_16", "lat": 25.858441643613652, "lon": 57.30597802808436, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_17", "lat": 25.85870547057771,  "lon": 57.30737158935134, "radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_18", "lat": 25.85716642306246,  "lon": 57.306251980766476,"radius": 35, "h_max": 13, "uncertain": True},
            {"id": "jk_19", "lat": 25.857398072650852, "lon": 57.30766780088818, "radius": 35, "h_max": 13, "uncertain": True},
        ],
    },
]
