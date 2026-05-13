#!/usr/bin/env python3
"""
add_steps.py — Add structured workout steps to Intervals.icu calendar events.

Uses the native Intervals.icu workout_doc format (seconds/km, warmup/intensity flags)
so steps render correctly in the UI and sync to the Forerunner 265.

Usage:
  python add_steps.py                  # preview all
  python add_steps.py --confirm        # execute all
  python add_steps.py --id 108628337   # preview single event
  python add_steps.py --id 108628337 --confirm  # execute single event

Credentials: env vars INTERVALS_ATHLETE_ID, INTERVALS_API_KEY
or .env file 4 levels up from this script (training-data/.env)
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    sys.exit(1)


# ── Pace helpers ─────────────────────────────────────────────────────────────

def s(pace_str: str) -> int:
    """'M:SS' → integer seconds/km. E.g. '5:05' → 305"""
    m, sec = pace_str.split(":")
    return int(m) * 60 + int(sec)


# ── Zone computation ──────────────────────────────────────────────────────────
# Athlete threshold pace: 3.4482758 m/s → 290 secs/km (from sportSettings)
# Pace zone upper bounds: [77.5, 87.7, 94.3, 100.0, 103.4, 111.5] % of FTP speed
# → secs/km thresholds (slower pace = higher secs/km = lower zone)
_TP = 290  # threshold pace secs/km
_ZONE_BOUNDS = [round(_TP / (p / 100)) for p in [77.5, 87.7, 94.3, 100.0, 103.4, 111.5]]
# = [374, 331, 308, 290, 281, 260]

def _pace_to_zone(avg_secs_per_km: float) -> str:
    if avg_secs_per_km >= _ZONE_BOUNDS[0]:   return "Z1"
    elif avg_secs_per_km >= _ZONE_BOUNDS[1]: return "Z2"
    elif avg_secs_per_km >= _ZONE_BOUNDS[2]: return "Z3"
    elif avg_secs_per_km >= _ZONE_BOUNDS[3]: return "Z4"
    elif avg_secs_per_km >= _ZONE_BOUNDS[4]: return "Z5"
    elif avg_secs_per_km >= _ZONE_BOUNDS[5]: return "Z6"
    else:                                     return "Z7"

def _avg_pace(step: dict) -> float:
    pace = step.get("pace", {})
    if "value" in pace:
        return pace["value"]
    if "start" in pace and "end" in pace:
        return (pace["start"] + pace["end"]) / 2
    return 435  # no pace → Z1

def compute_zone_times(steps: list) -> list:
    zones = {"Z1": 0, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0, "Z6": 0, "Z7": 0}
    for step in steps:
        dur = step.get("duration", 0)
        if dur:
            zones[_pace_to_zone(_avg_pace(step))] += dur
    return [{"id": z, "secs": zones[z]} for z in ("Z1","Z2","Z3","Z4","Z5","Z6","Z7")]

def compute_load(steps: list) -> tuple[float, float]:
    """Returns (icu_intensity %, icu_training_load) using same TSS/IF formula as Intervals.icu.
    Verified against Test2: IF=81.13, TSS=59 ✓
    """
    total_dur = sum(st.get("duration", 0) for st in steps)
    if not total_dur:
        return 0.0, 0.0
    weighted_if2 = sum((_TP / _avg_pace(st)) ** 2 * st.get("duration", 0) for st in steps)
    if2 = weighted_if2 / total_dur
    intensity = if2 ** 0.5 * 100
    training_load = if2 * total_dur / 36
    return round(intensity, 5), round(training_load, 1)


# ── Step builders (native Intervals.icu format) ───────────────────────────────
# Each distance-based step includes duration (= distance * avg_pace / 1000)
# so Intervals.icu renders the zone chart and computes moving_time.

def _dur(distance_m: int, pace_start_secs: int, pace_end_secs: int) -> int:
    return round(distance_m * (pace_start_secs + pace_end_secs) / 2 / 1000)

def warmup(distance_m: int, hr_zone: int = 1):
    """Warmup step — ramping pace (Z1), shown as WU in Intervals UI."""
    p0, p1 = s("6:30"), s("10:00")
    return {
        "warmup": True,
        "ramp": True,
        "pace": {"start": p0, "end": p1, "units": "secs"},
        "distance": distance_m,
        "duration": _dur(distance_m, p0, p1),
    }

def cooldown(distance_m: int):
    """Cooldown step — easy finish."""
    p0, p1 = s("6:30"), s("10:00")
    return {
        "cooldown": True,
        "pace": {"start": p0, "end": p1, "units": "secs"},
        "distance": distance_m,
        "duration": _dur(distance_m, p0, p1),
    }

def easy(distance_m: int, text: str = "HR 125–145"):
    """Easy aerobic step \u2014 Managua pace range 6:30\u201310:00 (cuestas)."""
    p0, p1 = s("6:30"), s("10:00")
    return {
        "pace": {"start": p0, "end": p1, "units": "secs"},
        "text": text,
        "distance": distance_m,
        "duration": _dur(distance_m, p0, p1),
    }

def long_run(distance_m: int, text: str = "HR 130–148"):
    """Long run step — slightly faster easy."""
    p0, p1 = s("6:00"), s("7:30")
    return {
        "pace": {"start": p0, "end": p1, "units": "secs"},
        "text": text,
        "distance": distance_m,
        "duration": _dur(distance_m, p0, p1),
    }

def tempo(distance_m: int = None, duration_s: int = None,
          pace_start: str = "4:50", pace_end: str = "5:00", text: str = None):
    """Threshold / tempo work step."""
    p0, p1 = s(pace_start), s(pace_end)
    step = {
        "intensity": "active",
        "pace": {"start": p0, "end": p1, "units": "secs"},
    }
    if text:
        step["text"] = text
    if distance_m:
        step["distance"] = distance_m
        step["duration"] = _dur(distance_m, p0, p1)
    elif duration_s:
        step["duration"] = duration_s
    return step

def hm_pace(distance_m: int, text: str = "Ritmo HM — 5:05–5:13/km"):
    """HM goal pace work step."""
    p0, p1 = s("5:05"), s("5:13")
    return {
        "intensity": "active",
        "pace": {"start": p0, "end": p1, "units": "secs"},
        "text": text,
        "distance": distance_m,
        "duration": _dur(distance_m, p0, p1),
    }

def vo2max(distance_m: int = 1000, text: str = "VO2max — 4:15–4:20/km"):
    """VO2max interval step."""
    p0, p1 = s("4:15"), s("4:20")
    return {
        "intensity": "active",
        "pace": {"start": p0, "end": p1, "units": "secs"},
        "text": text,
        "distance": distance_m,
        "duration": _dur(distance_m, p0, p1),
    }

def rest_walk(duration_s: int, text: str = "Caminá — recuperación completa"):
    """Walk recovery between intervals."""
    return {
        "intensity": "rest",
        "pace": {"start": s("10:00"), "end": s("12:00"), "units": "secs"},
        "text": text,
        "duration": duration_s,
    }

def stride(text: str = "Stride — suelto"):
    """20s stride at ~4:00/km."""
    return {
        "intensity": "active",
        "pace": {"start": s("4:00"), "end": s("4:10"), "units": "secs"},
        "text": text,
        "duration": 20,
    }

def stride_rest():
    """60s walk recovery after stride."""
    return {
        "intensity": "rest",
        "duration": 60,
    }


# ── Workout definitions ───────────────────────────────────────────────────────
# event_id → {"steps": [...], "distance": m, "duration": s}
# distance=0 for time-based, duration=0 for distance-based

WORKOUTS = {

    # ── WEEK 1 (May 4–10) ──────────────────────────────────────────────────
    108628328: {
        "steps": [warmup(1000), long_run(8000), cooldown(1000)],
        "distance": 10000, "duration": 0,
    },

    # ── WEEK 2 (May 11–17) ─────────────────────────────────────────────────
    108628330: {
        "steps": [warmup(1000), easy(6000), cooldown(1000)],
        "distance": 8000, "duration": 0,
    },
    108628331: {  # TEMPO 3km@5:05 + 2km@4:55
        "steps": [
            warmup(2000),
            tempo(distance_m=3000, pace_start="5:00", pace_end="5:10", text="3km @ 5:05/km"),
            rest_walk(180, "3:00 caminando"),
            tempo(distance_m=2000, pace_start="4:50", pace_end="5:00", text="2km @ 4:55/km"),
            cooldown(2000),
        ],
        "distance": 9000, "duration": 0,
    },
    108628332: {
        "steps": [warmup(1000), easy(5000), cooldown(1000)],
        "distance": 7000, "duration": 0,
    },
    108628334: {  # LONG 14km
        "steps": [warmup(1000), long_run(12000), cooldown(1000)],
        "distance": 14000, "duration": 0,
    },

    # ── WEEK 3 (May 18–24) ─────────────────────────────────────────────────
    108628336: {  # EASY + STRIDES 7km
        "steps": [
            warmup(1000),
            easy(4000),
            stride("Stride 1/6"), stride_rest(),
            stride("Stride 2/6"), stride_rest(),
            stride("Stride 3/6"), stride_rest(),
            stride("Stride 4/6"), stride_rest(),
            stride("Stride 5/6"), stride_rest(),
            stride("Stride 6/6"), stride_rest(),
            cooldown(1000),
        ],
        "distance": 6500, "duration": 0,
    },
    108628337: {  # TEMPO 3×10min @ 4:50/km
        "steps": [
            warmup(2000),
            tempo(duration_s=600, pace_start="4:50", pace_end="5:00", text="10min @ 4:50 (1/3)"),
            rest_walk(180, "3:00 caminando"),
            tempo(duration_s=600, pace_start="4:50", pace_end="5:00", text="10min @ 4:50 (2/3)"),
            rest_walk(180, "3:00 caminando"),
            tempo(duration_s=600, pace_start="4:50", pace_end="5:00", text="10min @ 4:50 (3/3)"),
            cooldown(2000),
        ],
        "distance": 0, "duration": 2760,
    },
    108628339: {
        "steps": [warmup(1000), easy(6000), cooldown(1000)],
        "distance": 8000, "duration": 0,
    },
    108628342: {  # LONG 15km
        "steps": [warmup(1000), long_run(13000), cooldown(1000)],
        "distance": 15000, "duration": 0,
    },

    # ── WEEK 4 (May 25–31) ─────────────────────────────────────────────────
    108628343: {
        "steps": [warmup(1000), easy(5000), cooldown(1000)],
        "distance": 7000, "duration": 0,
    },
    108628344: {  # TEMPO 2×15min @ 4:50/km
        "steps": [
            warmup(2000),
            tempo(duration_s=900, pace_start="4:50", pace_end="5:00", text="15min @ 4:50 (1/2)"),
            rest_walk(300, "5:00 caminando"),
            tempo(duration_s=900, pace_start="4:50", pace_end="5:00", text="15min @ 4:50 (2/2)"),
            cooldown(2000),
        ],
        "distance": 0, "duration": 2700,
    },
    108628347: {
        "steps": [warmup(1000), easy(7000), cooldown(1000)],
        "distance": 9000, "duration": 0,
    },
    108628351: {  # LONG 16km
        "steps": [warmup(1000), long_run(14000), cooldown(1000)],
        "distance": 16000, "duration": 0,
    },

    # ── WEEK 5 (Jun 1–7) ───────────────────────────────────────────────────
    108628353: {  # EASY + STRIDES 8km
        "steps": [
            warmup(1000),
            easy(5000),
            stride("Stride 1/6"), stride_rest(),
            stride("Stride 2/6"), stride_rest(),
            stride("Stride 3/6"), stride_rest(),
            stride("Stride 4/6"), stride_rest(),
            stride("Stride 5/6"), stride_rest(),
            stride("Stride 6/6"), stride_rest(),
            cooldown(1000),
        ],
        "distance": 7500, "duration": 0,
    },
    108628355: {  # TEMPO 3×12min @ 4:50/km
        "steps": [
            warmup(2000),
            tempo(duration_s=720, pace_start="4:50", pace_end="5:00", text="12min @ 4:50 (1/3)"),
            rest_walk(180, "3:00 caminando"),
            tempo(duration_s=720, pace_start="4:50", pace_end="5:00", text="12min @ 4:50 (2/3)"),
            rest_walk(180, "3:00 caminando"),
            tempo(duration_s=720, pace_start="4:50", pace_end="5:00", text="12min @ 4:50 (3/3)"),
            cooldown(2000),
        ],
        "distance": 0, "duration": 3000,
    },
    108628357: {
        "steps": [warmup(1000), easy(8000), cooldown(1000)],
        "distance": 10000, "duration": 0,
    },
    108628360: {  # LONG 17km
        "steps": [warmup(1000), long_run(15000), cooldown(1000)],
        "distance": 17000, "duration": 0,
    },

    # ── WEEK 6 (Jun 8–14) — Build phase ────────────────────────────────────
    108628362: {
        "steps": [warmup(1000), easy(5000), cooldown(1000)],
        "distance": 7000, "duration": 0,
    },
    108628363: {  # VO2MAX 5×1km @ 4:15–4:20/km
        "steps": [
            warmup(2000),
            vo2max(1000, "1km @ 4:15–4:20 (1/5)"), rest_walk(90, "90s caminando"),
            vo2max(1000, "1km @ 4:15–4:20 (2/5)"), rest_walk(90, "90s caminando"),
            vo2max(1000, "1km @ 4:15–4:20 (3/5)"), rest_walk(90, "90s caminando"),
            vo2max(1000, "1km @ 4:15–4:20 (4/5)"), rest_walk(90, "90s caminando"),
            vo2max(1000, "1km @ 4:15–4:20 (5/5)"),
            cooldown(2000),
        ],
        "distance": 9000, "duration": 0,
    },
    108628365: {  # EASY + STRIDES 8km
        "steps": [
            warmup(1000),
            easy(5000),
            stride("Stride 1/6"), stride_rest(),
            stride("Stride 2/6"), stride_rest(),
            stride("Stride 3/6"), stride_rest(),
            stride("Stride 4/6"), stride_rest(),
            stride("Stride 5/6"), stride_rest(),
            stride("Stride 6/6"), stride_rest(),
            cooldown(1000),
        ],
        "distance": 7500, "duration": 0,
    },
    108628367: {  # FF-LONG 18km — últimos 3km @ ritmo HM
        "steps": [
            warmup(1000),
            long_run(14000, "HR 130–148 — aéroba pura"),
            hm_pace(3000, "Últimos 3km @ 5:05–5:13"),
        ],
        "distance": 18000, "duration": 0,
    },

    # ── WEEK 7 (Jun 15–21) — Pico de volumen ───────────────────────────────
    108628369: {
        "steps": [warmup(1000), easy(6000), cooldown(1000)],
        "distance": 8000, "duration": 0,
    },
    108628371: {  # HM-PACE 3×3km @ 5:05/km
        "steps": [
            warmup(2000),
            hm_pace(3000, "3km @ 5:05 (1/3)"), rest_walk(180, "3:00 caminando"),
            hm_pace(3000, "3km @ 5:05 (2/3)"), rest_walk(180, "3:00 caminando"),
            hm_pace(3000, "3km @ 5:05 (3/3)"),
            cooldown(2000),
        ],
        "distance": 13000, "duration": 0,
    },
    108628373: {
        "steps": [warmup(1000), easy(7000), cooldown(1000)],
        "distance": 9000, "duration": 0,
    },

    # ── WEEK 8 (Jun 22–28) — Deload + Race ─────────────────────────────────
    # Events for Jun 22–28 TBD — add IDs when visible in latest.json

}


# ── API ───────────────────────────────────────────────────────────────────────

def load_credentials():
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    creds = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    athlete_id = creds.get("INTERVALS_ATHLETE_ID") or os.getenv("INTERVALS_ATHLETE_ID")
    api_key    = creds.get("INTERVALS_API_KEY")    or os.getenv("INTERVALS_API_KEY")
    return athlete_id, api_key


def update_event(athlete_id, api_key, event_id, workout):
    auth = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/events/{event_id}"
    steps = workout["steps"]
    moving_time = sum(st.get("duration", 0) for st in steps)
    zone_times = compute_zone_times(steps)
    intensity, training_load = compute_load(steps)
    payload = {
        "target": "PACE",
        "moving_time": moving_time,
        "icu_intensity": intensity,
        "icu_training_load": training_load,
        "joules": 0,
        "joules_above_ftp": 0,
        "workout_doc": {
            "steps": steps,
            "distance": workout.get("distance", 0),
            "duration": workout.get("duration", 0),
            "zoneTimes": zone_times,
            "options": {},
            "locales": [],
        },
    }
    r = requests.put(url, headers=headers, json=payload)
    r.raise_for_status()
    return r.json()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Add structured steps to Intervals.icu workouts")
    parser.add_argument("--confirm", action="store_true", help="Execute (default: preview only)")
    parser.add_argument("--id", type=int, help="Single event ID to update")
    args = parser.parse_args()

    athlete_id, api_key = load_credentials()
    if not athlete_id or not api_key:
        print("ERROR: Missing credentials. Set INTERVALS_ATHLETE_ID and INTERVALS_API_KEY in env or .env", file=sys.stderr)
        sys.exit(1)

    targets = {args.id: WORKOUTS[args.id]} if args.id else WORKOUTS
    mode = "EXECUTE" if args.confirm else "PREVIEW"

    print(f"\n{'='*60}")
    print(f"  add_steps.py — {mode}")
    print(f"  {len(targets)} workout(s)")
    print(f"{'='*60}\n")

    ok = err = 0
    for event_id, workout in sorted(targets.items()):
        n = len(workout["steps"])
        if not args.confirm:
            print(f"  [{event_id}] {n} steps (preview)")
            ok += 1
        else:
            try:
                update_event(athlete_id, api_key, event_id, workout)
                print(f"  ✅ [{event_id}] {n} steps")
                ok += 1
            except Exception as e:
                print(f"  ❌ [{event_id}] ERROR: {e}")
                err += 1

    print(f"\n{'='*60}")
    if args.confirm:
        print(f"  Done: {ok} updated, {err} failed")
    else:
        print(f"  Preview: {ok} ready — add --confirm to execute")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
