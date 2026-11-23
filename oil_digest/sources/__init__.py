from . import china, iea, jodi, opec, uani

# name -> collector; names are also the keys of schedule.WINDOWS
REGISTRY = {
    "uani_monthly": uani.collect_monthly,
    "uani_updates": uani.collect_updates,
    "china": china.collect,
    "opec": opec.collect,
    "jodi": jodi.collect,
    "iea": iea.collect,
}
ALIASES = {"uani": ["uani_monthly", "uani_updates"]}
