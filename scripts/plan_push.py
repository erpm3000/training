#!/usr/bin/env python3
"""
plan_push.py — Push the Granada HM training plan to Intervals.icu calendar.

Usage:
  python3 plan_push.py              # Preview (dry-run, no writes)
  python3 plan_push.py --push       # Write all events to Intervals.icu
  python3 plan_push.py --push --week 3          # Push only week 3
  python3 plan_push.py --push --week 3 --week 5 # Push weeks 3 and 5
  python3 plan_push.py --structure  # Add structured workout steps to quality sessions
  python3 plan_push.py --delete     # Delete all previously pushed events
  python3 plan_push.py --status     # Show what's already been pushed
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

ENV_FILE = Path(__file__).parent / ".env"
PUSHED_IDS_FILE = Path(__file__).parent / "pushed_events.json"
INTERVALS_BASE = "https://intervals.icu/api/v1"
PLAN_TAG = "granada-hm-2026"  # external_id tag — used to identify/delete pushed events

# ─── Colors per session type ──────────────────────────────────────────────────

COLORS = {
    "EASY":     "#43A047",  # green
    "LONG":     "#1E88E5",  # blue
    "FF-LONG":  "#0D47A1",  # dark blue
    "TEMPO":    "#FB8C00",  # orange
    "HM-PACE":  "#8E24AA",  # purple
    "VO2MAX":   "#E53935",  # red
    "GYM":      "#546E7A",  # grey-blue
    "REST":     None,
}

# ─── Structured workout builder ──────────────────────────────────────────────
# Pace ranges in m/s. Low = slow end, High = fast end (counterintuitive but correct).
# Formula: 1000m / pace_seconds = m/s

_EASY    = (2.00, 2.60)   # ~6:25–8:20/km  — WU, CD, and easy recovery
_WALK    = (0.50, 1.50)   # walk recovery between intervals
_TEMPO   = (3.40, 3.50)   # 4:50/km ± 5s   — LT2 confirmed
_4_55    = (3.33, 3.45)   # 4:55/km ± 10s
_5_05    = (3.22, 3.34)   # 5:05/km ± 10s  — HM-pace / sub-threshold
_5_13    = (3.14, 3.25)   # 5:13/km ± 10s  — exact race pace
_VO2MAX  = (3.83, 3.97)   # 4:15–4:20/km   — VO2max / sharpening


def _wu(duration=600):
    return {"type": "Warmup", "duration": duration,
            "target": {"speed": {"min": _EASY[0], "max": _EASY[1]}}}

def _cd(duration=600):
    return {"type": "Cooldown", "duration": duration,
            "target": {"speed": {"min": _EASY[0], "max": _EASY[1]}}}

def _on(duration, pace):
    return {"type": "On", "duration": duration,
            "target": {"speed": {"min": pace[0], "max": pace[1]}}}

def _off(duration):
    return {"type": "Off", "duration": duration,
            "target": {"speed": {"min": _WALK[0], "max": _WALK[1]}}}

def _rpt(repeat, on_dur, on_pace, off_dur):
    return {"type": "IntervalsT", "repeat": repeat,
            "steps": [_on(on_dur, on_pace), _off(off_dur)]}

def _steady(duration, pace):
    """Continuous steady-state block — used for the easy portion of long runs."""
    return {"type": "SteadyState", "duration": duration,
            "target": {"speed": {"min": pace[0], "max": pace[1]}}}


# Quality session step definitions
# Duration in seconds. 1km@4:50≈290s, 1km@4:15≈255s, 3km@5:05≈915s, 5km@5:05≈1525s

STRUCTURED_WORKOUTS = {
    # ── Week 2 ── Runna Tempo modified: 3km@5:05 + 2km@4:55
    "TEMPO — 3km@5:05 + 2km@4:55 (Runna mod.)": lambda: [
        _wu(),
        _on(915, _5_05),   # 3km @ 5:05
        _off(180),          # 3min walk
        _on(590, _4_55),   # 2km @ 4:55
        _cd(),
    ],
    # ── Week 3 ── 3×10min@4:50
    "TEMPO — 3×10min @ 4:50/km": lambda: [
        _wu(), _rpt(3, 600, _TEMPO, 180), _cd(),
    ],
    # ── Week 4 ── 2×15min@4:50
    "TEMPO — 2×15min @ 4:50/km": lambda: [
        _wu(), _rpt(2, 900, _TEMPO, 300), _cd(),
    ],
    # ── Week 5 ── 3×12min@4:50
    "TEMPO — 3×12min @ 4:50/km": lambda: [
        _wu(), _rpt(3, 720, _TEMPO, 180), _cd(),
    ],
    # ── Week 8 ── Deload tempo: 2×10min@4:50
    "TEMPO light — 2×10min @ 4:50/km": lambda: [
        _wu(), _rpt(2, 600, _TEMPO, 300), _cd(),
    ],
    # ── Week 6 ── 5×1km@4:15–4:20 (90s rest)
    "VO2MAX — 5×1km @ 4:15–4:20/km": lambda: [
        _wu(), _rpt(5, 258, _VO2MAX, 90), _cd(),
    ],
    # ── Week 9 ── 6×1km@4:15 (90s rest)
    "VO2MAX (sharpening) — 6×1km @ 4:15/km": lambda: [
        _wu(), _rpt(6, 255, _VO2MAX, 90), _cd(),
    ],
    # ── Week 11 ── 3×1km@4:15 (2min rest)
    "Sharpening — 3×1km @ 4:15/km": lambda: [
        _wu(), _rpt(3, 255, _VO2MAX, 120), _cd(),
    ],
    # ── Week 7 ── 3×3km@5:05 (3min rest)
    "HM-PACE — 3×3km @ 5:05/km": lambda: [
        _wu(), _rpt(3, 915, _5_05, 180), _cd(),
    ],
    # ── Week 10 ── 2×5km@5:05 (3min rest)
    "HM-PACE (confianza) — 2×5km @ 5:05/km": lambda: [
        _wu(), _rpt(2, 1525, _5_05, 180), _cd(),
    ],

    # ── Fast-finish long runs ──────────────────────────────────────────────────
    # W6: 15km easy + 3km @ 5:05–5:10
    "FF-LONG — 18km (últimos 3km @ 5:05–5:10)": lambda: [
        _steady(6600, _EASY),    # ~15km at easy pace
        _on(920, _5_05),         # 3km @ 5:05–5:10
    ],
    # W7: 14km easy + 5km @ 5:13
    "FF-LONG — 19km (últimos 5km @ 5:13)": lambda: [
        _steady(6300, _EASY),    # ~14km at easy pace
        _on(1565, _5_13),        # 5km @ 5:13
    ],
    # W9: WU 2km + 12km @ race pace + CD 4km
    "RACE-SPECIFIC LONG — 18km (12km @ 5:05–5:13)": lambda: [
        _wu(900),                # 2km warmup (~15min easy)
        _on(3700, _5_05),        # 12km @ 5:05–5:13 (race pace effort)
        _cd(1800),               # 4km cooldown (~30min easy)
    ],
    # W10: 10km easy + 6km @ 5:13
    "FF-LONG — 16km (últimos 6km @ 5:13)": lambda: [
        _steady(4500, _EASY),    # ~10km at easy pace
        _on(1878, _5_13),        # 6km @ 5:13
    ],
    # W11 taper: 10km easy + 2km @ 5:13 (confidence check)
    "LONG — 12km + 2km @ 5:13 (TAPER)": lambda: [
        _steady(4500, _EASY),    # ~10km at easy pace
        _on(626, _5_13),         # 2km @ 5:13
    ],
}

# ─── Full 12-week plan ────────────────────────────────────────────────────────
# Each session: (date_str, session_type, name, description, duration_min, distance_m, tss)
# date_str: "YYYY-MM-DD"
# duration_min: total session time including WU/CD
# distance_m: 0 for gym/rest
# tss: estimated Training Stress Score (0 for gym)

PLAN = [
    # ══════════════════════════════════════════════════════════════
    # SEMANA 1 — May 4–10 — TRANSICIÓN (parcialmente ya ejecutada)
    # ══════════════════════════════════════════════════════════════
    ("2026-05-07", "EASY", "EASY — 50min",
     "HR cap 145. Salida 5:00–5:15AM. Calor esperado 30–31°C.\n"
     "Flats: HR 125–145. Cuestas: permitir hasta 158. No mirar el ritmo.",
     50, 6500, 38),

    ("2026-05-08", "GYM", "GYM — Legs & Core (introductorio)",
     "Primera sesión de fuerza. Técnica y rango de movimiento, sin carga máxima.\n"
     "Sentadilla, peso muerto, press, core.",
     50, 0, 0),

    ("2026-05-09", "LONG", "LONG — 60–70min",
     "HR-governed. Cap 148. Salida antes de 5:00AM.\n"
     "Objetivo: completar sin superar cap de HR. No importa el ritmo.\n"
     "Si HR no baja en primeros 10min, reducí ritmo desde el inicio.",
     70, 9000, 60),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 2 — May 11–17 — TRANSICIÓN + PRIMERA CALIDAD
    # ══════════════════════════════════════════════════════════════
    ("2026-05-11", "EASY", "EASY — 8km",
     "HR 125–145. Salida 5:00–5:15AM. Conversacional.",
     60, 8000, 42),

    ("2026-05-13", "TEMPO", "TEMPO — 3km@5:05 + 2km@4:55 (Runna mod.)",
     "Semana 2 post-retorno. Opción A: Tempo 3-2 sin el km final a 4:50.\n"
     "WU: 2km muy fácil\n"
     "3km @ 5:05/km → 3:00 caminando\n"
     "2km @ 4:55/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~9km. Si Feel ≥4 o HRV↓>15% al levantarte: convertir en EASY 8km.",
     55, 9000, 62),

    ("2026-05-14", "EASY", "EASY — 7km",
     "HR 125–145. Recuperación activa post-tempo.",
     52, 7000, 38),

    ("2026-05-15", "GYM", "GYM — Full Body",
     "Full body. Moderado. No legs heavy si las piernas están cargadas del tempo.",
     50, 0, 0),

    ("2026-05-16", "LONG", "LONG — 14km",
     "HR 130–148 avg. Salida antes de 5:00AM. Llevar agua.\n"
     "Terreno Managua: HR sube en cuestas — normal hasta 158. Bajar a 130–140 en bajadas.\n"
     "Cap de drift: 153 solo en últimos 3km.",
     105, 14000, 75),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 3 — May 18–24 — BASE BUILD W1 (calidad empieza)
    # ══════════════════════════════════════════════════════════════
    ("2026-05-18", "EASY", "EASY + STRIDES — 7km",
     "HR 125–145. Al final: 6×20s @ ~4:00/km, totalmente suelto, caminata de regreso completa.\n"
     "Los strides son neuromuscular — no esfuerzo, solo soltura.",
     55, 7000, 40),

    ("2026-05-19", "TEMPO", "TEMPO — 3×10min @ 4:50/km",
     "WU: 2km muy fácil\n"
     "Rep 1: 10min @ 4:50/km → 3:00 caminando\n"
     "Rep 2: 10min @ 4:50/km → 3:00 caminando\n"
     "Rep 3: 10min @ 4:50/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~9km | TSS est: 65\n"
     "Ritmo: 4:50/km es tu LT2 confirmado. No más rápido.",
     60, 9000, 65),

    ("2026-05-20", "GYM", "GYM — Legs & Core",
     "Legs & Core. Fuerza posterior (Romanian DL, hip thrust, calf raises).",
     50, 0, 0),

    ("2026-05-21", "EASY", "EASY — 8km",
     "HR 125–145. Salida 5:00AM. Conversacional.",
     60, 8000, 42),

    ("2026-05-22", "GYM", "GYM — Full Body",
     "Full body. Press, row, core.",
     50, 0, 0),

    ("2026-05-23", "LONG", "LONG — 15km",
     "HR 130–148 avg. Salida 4:45AM.\n"
     "Esfuerzo aeróbico puro. No forzar ritmo. Llevar agua + 1 gel si supera 75min.",
     112, 15000, 82),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 4 — May 25–31 — BASE BUILD W2
    # ══════════════════════════════════════════════════════════════
    ("2026-05-25", "EASY", "EASY — 7km",
     "HR 125–145. Recuperación activa inicio de semana.",
     52, 7000, 38),

    ("2026-05-26", "TEMPO", "TEMPO — 2×15min @ 4:50/km",
     "WU: 2km muy fácil\n"
     "Rep 1: 15min @ 4:50/km → 5:00 caminando\n"
     "Rep 2: 15min @ 4:50/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~9.5km | Bloques más largos que la semana pasada. Mismo ritmo.",
     62, 9500, 70),

    ("2026-05-27", "GYM", "GYM — Legs & Core",
     "Legs & Core. Progresar carga vs semana anterior.",
     50, 0, 0),

    ("2026-05-28", "EASY", "EASY — 9km",
     "HR 125–145. Terreno variado OK.",
     67, 9000, 48),

    ("2026-05-29", "GYM", "GYM — Full Body",
     "Full body. Progresar carga.",
     50, 0, 0),

    ("2026-05-30", "LONG", "LONG — 16km",
     "HR 130–148 avg. Salida 4:45AM.\n"
     "Llevar agua + 1 gel @ 75min si lo necesitás.\n"
     "Cap de drift: 153 solo en últimos 3km.",
     120, 16000, 88),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 5 — Jun 1–7 — BASE BUILD W3
    # ══════════════════════════════════════════════════════════════
    ("2026-06-01", "EASY", "EASY + STRIDES — 8km",
     "HR 125–145. Al final: 6×20s @ ~4:00/km suelto, caminata de regreso.",
     60, 8000, 43),

    ("2026-06-02", "TEMPO", "TEMPO — 3×12min @ 4:50/km",
     "WU: 2km muy fácil\n"
     "Rep 1: 12min @ 4:50/km → 3:00 caminando\n"
     "Rep 2: 12min @ 4:50/km → 3:00 caminando\n"
     "Rep 3: 12min @ 4:50/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~10.5km | 36min de trabajo a umbral. Mayor volumen de calidad.",
     65, 10500, 75),

    ("2026-06-03", "GYM", "GYM — Legs & Core",
     "Legs & Core. Mayor carga si las piernas respondieron bien.",
     50, 0, 0),

    ("2026-06-04", "EASY", "EASY — 10km",
     "HR 125–145. Más distancia, mismo esfuerzo.",
     75, 10000, 52),

    ("2026-06-05", "GYM", "GYM — Full Body",
     "Full body.",
     50, 0, 0),

    ("2026-06-06", "LONG", "LONG — 17km",
     "HR 130–148 avg. Salida 4:45AM.\n"
     "Llevar agua + 1 gel @ 60min, 1 gel @ 90min.\n"
     "Últimos 2km: permitir drift a 153. Últimos 500m: llevar a casa suave.",
     127, 17000, 95),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 6 — Jun 8–14 — BUILD W1
    # ══════════════════════════════════════════════════════════════
    ("2026-06-08", "EASY", "EASY — 7km",
     "HR 125–145. Inicio de fase Build. Recuperación activa.",
     52, 7000, 38),

    ("2026-06-09", "VO2MAX", "VO2MAX — 5×1km @ 4:15–4:20/km",
     "WU: 2km muy fácil\n"
     "Rep 1–5: 1km @ 4:15–4:20/km → 90s caminando entre reps\n"
     "CD: 2km muy fácil\n"
     "Total: ~9km | Primer workout de alta intensidad del ciclo.\n"
     "RPE por rep: 8–9/10. Si no podés mantener el ritmo en reps 4–5, pará en 4.\n"
     "No empezar si Feel ≥4 o HRV↓>20% — convertir en EASY.",
     55, 9000, 80),

    ("2026-06-10", "GYM", "GYM — Legs & Core",
     "Legs & Core. Moderado — piernas en recuperación post-VO2max.",
     50, 0, 0),

    ("2026-06-11", "EASY", "EASY + STRIDES — 8km",
     "HR 125–145. Al final: 6×20s suelto, caminata de regreso.",
     60, 8000, 43),

    ("2026-06-12", "GYM", "GYM — Full Body",
     "Full body.",
     50, 0, 0),

    ("2026-06-13", "FF-LONG", "FF-LONG — 18km (últimos 3km @ 5:05–5:10)",
     "HR 130–148 los primeros 15km. Salida 4:30AM.\n"
     "Km 1–15: fácil, HR-governed.\n"
     "Km 16–18: soltar a 5:05–5:10/km — primer contacto con ritmo cercano al de carrera.\n"
     "Llevar agua + 2 geles (protocolo de carrera).",
     135, 18000, 105),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 7 — Jun 15–21 — BUILD W2 (PICO DE VOLUMEN)
    # ══════════════════════════════════════════════════════════════
    ("2026-06-15", "EASY", "EASY — 8km",
     "HR 125–145. Inicio de semana pico.",
     60, 8000, 42),

    ("2026-06-16", "HM-PACE", "HM-PACE — 3×3km @ 5:05/km",
     "WU: 2km muy fácil\n"
     "Rep 1: 3km @ 5:05/km → 3:00 caminando\n"
     "Rep 2: 3km @ 5:05/km → 3:00 caminando\n"
     "Rep 3: 3km @ 5:05/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~13km | 9km a ritmo de carrera (5:05 es levemente más rápido que los 5:13 de carrera).\n"
     "RPE target: 7/10 — debe sentirse sostenible, no máximo.",
     80, 13000, 88),

    ("2026-06-17", "GYM", "GYM — Legs & Core",
     "Legs & Core. Moderado.",
     50, 0, 0),

    ("2026-06-18", "EASY", "EASY — 9km",
     "HR 125–145. Recuperación activa.",
     67, 9000, 48),

    ("2026-06-19", "GYM", "GYM — Full Body",
     "Full body.",
     50, 0, 0),

    ("2026-06-20", "FF-LONG", "FF-LONG — 19km (últimos 5km @ 5:13)",
     "SEMANA PICO — sesión más larga del ciclo.\n"
     "Salida 4:30AM. Llevar agua + 3 geles (protocolo de carrera — gel cada 25min).\n"
     "Km 1–14: HR 130–148, fácil.\n"
     "Km 15–19: 5:13/km — ritmo exacto de carrera bajo fatiga acumulada.\n"
     "Si a los 14km el cuerpo no está bien (HR elevado, piernas muertas): reducir a 17km total.",
     142, 19000, 115),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 8 — Jun 22–28 — DELOAD
    # ══════════════════════════════════════════════════════════════
    ("2026-06-22", "EASY", "EASY — 6km (DELOAD)",
     "HR 125–140. Muy suave. No mirar el ritmo.",
     45, 6000, 32),

    ("2026-06-23", "TEMPO", "TEMPO light — 2×10min @ 4:50/km",
     "WU: 2km muy fácil\n"
     "Rep 1: 10min @ 4:50/km → 5:00 caminando\n"
     "Rep 2: 10min @ 4:50/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~7km | Semana de deload — mantener estímulo sin cargar.",
     45, 7000, 52),

    ("2026-06-24", "GYM", "GYM — Full Body (ligero)",
     "Full body más liviano. Sin llevar las piernas al límite esta semana.",
     45, 0, 0),

    ("2026-06-25", "EASY", "EASY — 7km (DELOAD)",
     "HR 125–140. Conversacional.",
     52, 7000, 38),

    ("2026-06-26", "GYM", "GYM — Legs & Core (ligero)",
     "Ligero. Movilidad + core liviano.",
     40, 0, 0),

    ("2026-06-27", "LONG", "LONG — 13km (DELOAD)",
     "Completamente fácil. HR 130–145. Sin ritmo de carrera.\n"
     "Salida 5:00AM. Esta semana el objetivo es recuperar, no estresar.",
     97, 13000, 68),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 9 — Jun 29–Jul 5 — PEAK W1
    # ══════════════════════════════════════════════════════════════
    ("2026-06-29", "EASY", "EASY + STRIDES — 8km",
     "HR 125–145. Al final: 6×20s suelto. Volvemos con energía del deload.",
     60, 8000, 43),

    ("2026-06-30", "VO2MAX", "VO2MAX (sharpening) — 6×1km @ 4:15/km",
     "WU: 2km muy fácil\n"
     "Rep 1–6: 1km @ 4:15/km → 90s caminando\n"
     "CD: 2km muy fácil\n"
     "Total: ~10km | Una rep más que en semana 6. El cuerpo fresco del deload debería sentirse.",
     60, 10000, 85),

    ("2026-07-01", "GYM", "GYM — Legs & Core",
     "Legs & Core. Fuerza mantenida.",
     50, 0, 0),

    ("2026-07-02", "EASY", "EASY — 8km",
     "HR 125–145.",
     60, 8000, 42),

    ("2026-07-03", "GYM", "GYM — Full Body",
     "Full body.",
     50, 0, 0),

    ("2026-07-04", "FF-LONG", "RACE-SPECIFIC LONG — 18km (12km @ 5:05–5:13)",
     "⭐ SESIÓN MÁS IMPORTANTE DEL CICLO.\n"
     "Salida 4:30AM. Llevar 3 geles (cada 25min, protocolo Granada).\n"
     "WU: 2km fácil.\n"
     "WORK: 12km @ 5:05–5:13/km continuo. Sin parar. Ritmo de carrera real.\n"
     "CD: 4km muy fácil.\n"
     "Si salís bien de esto: el sub-1:50 está confirmado.\n"
     "Criterio de éxito: terminar los 12km dentro del rango de ritmo sin que el HR explote.",
     135, 18000, 110),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 10 — Jul 6–12 — PEAK W2
    # ══════════════════════════════════════════════════════════════
    ("2026-07-06", "EASY", "EASY — 7km",
     "HR 125–145.",
     52, 7000, 38),

    ("2026-07-07", "HM-PACE", "HM-PACE (confianza) — 2×5km @ 5:05/km",
     "WU: 2km muy fácil\n"
     "Rep 1: 5km @ 5:05/km → 3:00 caminando\n"
     "Rep 2: 5km @ 5:05/km\n"
     "CD: 2km muy fácil\n"
     "Total: ~14km | Bloques largos a ritmo de carrera. Confirmar que el ritmo se siente manejable.",
     88, 14000, 92),

    ("2026-07-08", "GYM", "GYM — Full Body (moderado)",
     "Full body moderado. 3 semanas para la carrera — no lesionarse.",
     50, 0, 0),

    ("2026-07-09", "EASY", "EASY — 6km",
     "HR 125–145. Recuperación activa.",
     45, 6000, 32),

    ("2026-07-10", "GYM", "GYM — Legs & Core (ligero)",
     "Ligero. Empezamos a bajar la intensidad del gym.",
     40, 0, 0),

    ("2026-07-11", "FF-LONG", "FF-LONG — 16km (últimos 6km @ 5:13)",
     "HR 130–148 primeros 10km.\n"
     "Km 11–16: 5:13/km. Ritmo exacto de carrera.\n"
     "Llevar 2 geles. Salida 4:45AM.",
     120, 16000, 98),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 11 — Jul 13–19 — TAPER W1
    # ══════════════════════════════════════════════════════════════
    ("2026-07-13", "EASY", "EASY — 6km (TAPER)",
     "HR 125–140. Muy fácil. El volumen baja — es normal sentir las piernas raras.",
     45, 6000, 32),

    ("2026-07-14", "VO2MAX", "Sharpening — 3×1km @ 4:15/km",
     "WU: 2km muy fácil\n"
     "Rep 1–3: 1km @ 4:15/km → 2:00 caminando\n"
     "CD: 2km muy fácil\n"
     "Total: ~7km | Solo 3 reps — mantener piernas rápidas sin fatigar.",
     45, 7000, 58),

    ("2026-07-15", "GYM", "GYM — Movilidad (muy ligero)",
     "Solo movilidad y activación. Sin carga.",
     35, 0, 0),

    ("2026-07-16", "EASY", "EASY — 5km (TAPER)",
     "Completamente conversacional. HR 120–135.",
     38, 5000, 28),

    ("2026-07-18", "LONG", "LONG — 12km + 2km @ 5:13 (TAPER)",
     "10km HR 125–140. Últimos 2km @ 5:13 como check de confianza.\n"
     "Salida 5:00AM.",
     90, 12000, 72),

    # ══════════════════════════════════════════════════════════════
    # SEMANA 12 — Jul 20–26 — SEMANA DE CARRERA
    # ══════════════════════════════════════════════════════════════
    ("2026-07-20", "EASY", "EASY — 5km (Race Week)",
     "Muy fácil. 5:15AM. Solo mover las piernas.",
     38, 5000, 28),

    ("2026-07-21", "EASY", "Activación — 3km + 4×30s strides",
     "3km fácil + 4×30s suelto (no 100% — 90%). Caminata de regreso entre cada uno.\n"
     "Total: ~4km.",
     30, 4000, 22),

    ("2026-07-26", "EASY", "🏁 RACE DAY — Medio Maratón de Granada",
     "⭐ TARGET: Sub-1:50:00 (5:13/km)\n"
     "ESTRATEGIA:\n"
     "Km 1–5: salir CONSERVADOR — 5:18–5:20/km. El calor de Granada en julio castiga los arranques rápidos.\n"
     "Km 5–10: 5:13–5:15/km si el cuerpo responde.\n"
     "Km 10–18: 5:10–5:13/km. Ritmo de crucero.\n"
     "Km 18–21.1: todo lo que quede.\n\n"
     "FUELING: Gel cada 25min desde km 5. SaltStick con cada gel (si hace calor/seco).\n"
     "Si llueve o <22°C: geles solamente, sin sal.\n\n"
     "CONTINGENCIA: Sub-1:53:00 si el calor o las condiciones obligan.",
     130, 21100, 145),
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update(os.environ)
    return env


def get_auth(env):
    key = env.get("INTERVALS_API_KEY", "")
    if not key:
        print("❌  INTERVALS_API_KEY not found in .env")
        sys.exit(1)
    return base64.b64encode(f"API_KEY:{key}".encode()).decode()


def get_athlete_id(env):
    aid = env.get("INTERVALS_ATHLETE_ID", "")
    if not aid:
        print("❌  INTERVALS_ATHLETE_ID not found in .env")
        sys.exit(1)
    return aid


def load_pushed_ids():
    if PUSHED_IDS_FILE.exists():
        return json.loads(PUSHED_IDS_FILE.read_text())
    return {}


def save_pushed_ids(data):
    PUSHED_IDS_FILE.write_text(json.dumps(data, indent=2))


def session_start_time(session_type):
    """Return default start time string for each session type."""
    times = {
        "EASY":    "05:00:00",
        "LONG":    "04:45:00",
        "FF-LONG": "04:30:00",
        "TEMPO":   "05:00:00",
        "HM-PACE": "05:00:00",
        "VO2MAX":  "05:00:00",
        "GYM":     "06:00:00",
    }
    return times.get(session_type, "05:00:00")


# ─── API calls ────────────────────────────────────────────────────────────────

def post_event(athlete_id, auth, session):
    date_str, stype, name, description, duration_min, distance_m, tss = session
    start_time = session_start_time(stype)
    start_dt = f"{date_str}T{start_time}"

    payload = {
        "start_date_local": start_dt,
        "name": name,
        "type": "WeightTraining" if stype == "GYM" else "Run",
        "category": "WORKOUT",
        "description": description,
        "duration": duration_min * 60,
        "distance": float(distance_m),
        "color": COLORS.get(stype),
        "external_id": f"{PLAN_TAG}|{date_str}|{name[:30]}",
    }
    if tss > 0:
        payload["load_target"] = tss

    steps_fn = STRUCTURED_WORKOUTS.get(name)
    if steps_fn:
        payload["workout_doc"] = {"steps": steps_fn(), "description": description}

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{INTERVALS_BASE}/athlete/{athlete_id}/events"
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def put_event_steps(athlete_id, auth, event_id, steps, description):
    """PATCH an existing event to add structured workout steps."""
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "workout_doc": {
            "steps": steps,
            "description": description,
        }
    }
    url = f"{INTERVALS_BASE}/athlete/{athlete_id}/events/{event_id}"
    resp = requests.put(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def delete_event(athlete_id, auth, event_id):
    headers = {"Authorization": f"Basic {auth}"}
    url = f"{INTERVALS_BASE}/athlete/{athlete_id}/events/{event_id}"
    resp = requests.delete(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return True


# ─── Week helpers ─────────────────────────────────────────────────────────────

def week_number(date_str):
    """Return week number (1–12) for a given date string."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    week_starts = [
        "2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25",
        "2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22",
        "2026-06-29", "2026-07-06", "2026-07-13", "2026-07-20",
    ]
    for i, ws in enumerate(week_starts, 1):
        ws_d = datetime.strptime(ws, "%Y-%m-%d").date()
        we_d = ws_d + timedelta(days=6)
        if ws_d <= d <= we_d:
            return i
    return 0


WEEK_LABELS = {
    1: "Transición",  2: "Transición + 1ra calidad",
    3: "Base Build W1", 4: "Base Build W2", 5: "Base Build W3",
    6: "Build W1", 7: "Build W2 (PICO)",
    8: "DELOAD",
    9: "Peak W1", 10: "Peak W2",
    11: "TAPER", 12: "Race Week",
}


# ─── Actions ──────────────────────────────────────────────────────────────────

def preview(filter_weeks=None):
    print("\n📋  PLAN DE ENTRENAMIENTO — GRANADA HM 2026 — PREVIEW\n")
    current_week = 0
    total_runs = 0
    total_gym = 0
    for session in PLAN:
        date_str, stype, name, _, duration_min, distance_m, tss = session
        wk = week_number(date_str)
        if filter_weeks and wk not in filter_weeks:
            continue
        if wk != current_week:
            current_week = wk
            label = WEEK_LABELS.get(wk, "")
            print(f"\n  ── Semana {wk}: {label} ──")
        d = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"][d.weekday()]
        dist_str = f"{distance_m/1000:.1f}km" if distance_m else "—"
        tss_str  = f"{tss} TSS" if tss else "—"
        print(f"  {day_name} {date_str}  [{stype:8s}]  {name[:45]:<45}  {dist_str:>7}  {tss_str}")
        if stype != "GYM":
            total_runs += 1
        else:
            total_gym += 1

    print(f"\n  Total sesiones: {total_runs} runs + {total_gym} gym")
    print("\n  Para escribir en Intervals.icu: python3 plan_push.py --push")
    print("  Para una semana específica:     python3 plan_push.py --push --week 3\n")


def push(filter_weeks=None):
    env = load_env()
    auth = get_auth(env)
    athlete_id = get_athlete_id(env)
    pushed = load_pushed_ids()

    sessions_to_push = [
        s for s in PLAN
        if (not filter_weeks or week_number(s[0]) in filter_weeks)
    ]

    print(f"\n🚀  Pushing {len(sessions_to_push)} events to Intervals.icu...\n")

    ok = 0
    errors = 0
    for session in sessions_to_push:
        date_str, stype, name, _, duration_min, distance_m, tss = session
        key = f"{date_str}|{name[:30]}"
        if key in pushed:
            print(f"  ⏭   SKIP (already pushed): {date_str} {name[:45]}")
            continue
        try:
            result = post_event(athlete_id, auth, session)
            event_id = result["id"]
            pushed[key] = {"id": event_id, "date": date_str, "name": name, "type": stype}
            save_pushed_ids(pushed)
            wk = week_number(date_str)
            d = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"][d.weekday()]
            print(f"  ✅  W{wk} {day_name} {date_str}  [{stype}]  {name[:45]}  (id:{event_id})")
            ok += 1
        except Exception as e:
            print(f"  ❌  ERROR {date_str} {name[:40]}: {e}")
            errors += 1

    print(f"\n  Done: {ok} created, {errors} errors, {len(sessions_to_push)-ok-errors} skipped.\n")
    if ok > 0:
        print("  💡  Tip: si tenés Intervals.icu ↔ Garmin Connect activado,")
        print("       los eventos aparecerán en el calendario de Garmin Connect.\n")


def delete_all():
    env = load_env()
    auth = get_auth(env)
    athlete_id = get_athlete_id(env)
    pushed = load_pushed_ids()

    if not pushed:
        print("\n  ℹ️   No hay eventos registrados en pushed_events.json\n")
        return

    print(f"\n🗑   Deleting {len(pushed)} previously pushed events...\n")
    ok = 0
    errors = 0
    to_delete = list(pushed.items())
    for key, info in to_delete:
        event_id = info["id"]
        try:
            delete_event(athlete_id, auth, event_id)
            del pushed[key]
            save_pushed_ids(pushed)
            print(f"  🗑   Deleted: {info['date']} {info['name'][:40]} (id:{event_id})")
            ok += 1
        except Exception as e:
            print(f"  ❌  ERROR deleting id:{event_id}: {e}")
            errors += 1

    print(f"\n  Done: {ok} deleted, {errors} errors.\n")


def add_structure():
    """Add structured workout steps to already-pushed quality sessions."""
    env = load_env()
    auth = get_auth(env)
    athlete_id = get_athlete_id(env)
    pushed = load_pushed_ids()

    quality_sessions = {k: v for k, v in pushed.items()
                        if v["name"] in STRUCTURED_WORKOUTS}

    if not quality_sessions:
        print("\n  ℹ️   No quality sessions found in pushed events.\n")
        return

    print(f"\n🏗   Adding structured steps to {len(quality_sessions)} quality sessions...\n")

    ok = 0
    errors = 0
    for key, info in quality_sessions.items():
        event_id = info["id"]
        name = info["name"]
        steps_fn = STRUCTURED_WORKOUTS[name]
        # Find original description from PLAN
        description = next(
            (s[3] for s in PLAN if s[2] == name), ""
        )
        try:
            put_event_steps(athlete_id, auth, event_id, steps_fn(), description)
            stype = info["type"]
            print(f"  ✅  [{stype:8s}]  {name[:55]}  (id:{event_id})")
            ok += 1
        except Exception as e:
            print(f"  ❌  ERROR id:{event_id} {name[:40]}: {e}")
            errors += 1

    print(f"\n  Done: {ok} updated, {errors} errors.")
    print("  Garmin Connect sync may take a few minutes to reflect the changes.\n")


def status():
    pushed = load_pushed_ids()
    if not pushed:
        print("\n  ℹ️   No events pushed yet.\n")
        return
    print(f"\n📊  Pushed events ({len(pushed)} total):\n")
    for key, info in pushed.items():
        print(f"  {info['date']}  [{info['type']:8s}]  {info['name'][:45]}  (id:{info['id']})")
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Push Granada HM training plan to Intervals.icu")
    parser.add_argument("--push",      action="store_true", help="Write events to Intervals.icu")
    parser.add_argument("--structure", action="store_true", help="Add structured steps to quality sessions")
    parser.add_argument("--delete",    action="store_true", help="Delete previously pushed events")
    parser.add_argument("--status",    action="store_true", help="Show pushed events")
    parser.add_argument("--week",   type=int, action="append", metavar="N",
                        help="Filter to specific week(s) — e.g. --week 3 --week 5")
    args = parser.parse_args()

    filter_weeks = set(args.week) if args.week else None

    if args.structure:
        add_structure()
    elif args.delete:
        confirm = input("  ¿Seguro que querés borrar todos los eventos pusheados? [s/N] ")
        if confirm.lower() != "s":
            print("  Cancelado.")
            return
        delete_all()
    elif args.status:
        status()
    elif args.push:
        push(filter_weeks)
    else:
        preview(filter_weeks)


if __name__ == "__main__":
    main()
