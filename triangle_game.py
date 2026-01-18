# triangle_game.py
"""
Triangle Game – v2.1 (3 joueurs, triangle aléatoire, noms FR + locaux)
=====================================================================

### Nouveautés (août 2025)
1. **Noms bilingues** : chaque ville est affichée comme « Paris / París » (français
   + nom dans la langue locale disponible via Nominatim). Les listes et info‐
   bulles utilisent ce format.
2. Fonctionnalités préexistantes conservées : jusqu’à 3 joueurs, triangle au
   hasard, multi‑session, auto‑refresh 5 s.

```bash
pip install streamlit folium streamlit-folium geopy shapely pyproj streamlit-autorefresh
```
"""

from __future__ import annotations

import html
import json
import math
import os
import textwrap
import random
import re
import time
import unicodedata
import uuid
from functools import lru_cache
from typing import Dict, Any, List, Tuple, Callable

import streamlit as st
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderUnavailable, GeocoderTimedOut
from shapely.geometry import Point, Polygon
import folium
from streamlit_folium import st_folium
import plotly.graph_objects as go
from pyproj import CRS, Transformer, Geod
import requests
from rapidfuzz import fuzz

# Auto-refresh helpers (only when waiting)
try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


# ──────────────────────────────────────────────────────────────────────────────
# Constants & helpers
# ──────────────────────────────────────────────────────────────────────────────

POP_RE = re.compile(r"\d+")
GEOD = Geod(ellps="WGS84")
COLORS = ["green", "purple", "orange"]  # un par joueur
CITY_ADDRESS_KEYS = {
    "city",
    "town",
    "village",
    "hamlet",
    "municipality",
    "borough",
    "suburb",
    "quarter",
    "neighbourhood",
    "locality",
}
DISALLOWED_TYPES = {
    "country",
    "state",
    "region",
    "province",
    "county",
    "continent",
    "ocean",
    "sea",
}
DIFFICULTY_LEVELS = ["Easy", "Medium", "Hard"]
CAPITALS_EUROPE = [
    "Paris", "Berlin", "Madrid", "Rome", "Lisbon", "London", "Athens", "Oslo",
    "Stockholm", "Helsinki", "Copenhagen", "Vienna", "Prague", "Warsaw",
    "Budapest", "Dublin", "Brussels", "Amsterdam", "Zagreb", "Bern",
    "Bucharest", "Sofia", "Belgrade", "Sarajevo", "Skopje", "Tirana",
    "Vilnius", "Riga", "Tallinn", "Reykjavik", "Luxembourg", "Monaco",
]
CAPITALS_AMERICAS = [
    "Washington", "Ottawa", "Mexico City", "Guatemala City", "Havana",
    "Bogota", "Caracas", "Quito", "Lima", "La Paz", "Santiago", "Buenos Aires",
    "Brasilia", "Asuncion", "Montevideo",
]
CAPITALS_AFRICA = [
    "Cairo", "Algiers", "Tunis", "Tripoli", "Rabat", "Dakar", "Accra", "Abuja",
    "Lagos", "Luanda", "Nairobi", "Addis Ababa", "Khartoum", "Kampala",
    "Dar es Salaam", "Maputo", "Pretoria", "Cape Town",
]
CAPITALS_ASIA = [
    "Tokyo", "Seoul", "Beijing", "Bangkok", "Hanoi", "Jakarta", "Manila",
    "Kuala Lumpur", "Singapore", "New Delhi", "Islamabad", "Kathmandu",
    "Ulaanbaatar", "Tehran", "Baghdad", "Riyadh", "Ankara",
]
CAPITALS_OCEANIA = [
    "Canberra", "Wellington", "Port Moresby", "Suva",
]
CAPITALS_BY_REGION = {
    "Europe": CAPITALS_EUROPE,
    "Americas": CAPITALS_AMERICAS,
    "Africa": CAPITALS_AFRICA,
    "Asia": CAPITALS_ASIA,
    "Oceania": CAPITALS_OCEANIA,
}
CAPITALS = (
    CAPITALS_EUROPE
    + CAPITALS_AMERICAS
    + CAPITALS_AFRICA
    + CAPITALS_ASIA
    + CAPITALS_OCEANIA
)

# Shared store across sessions
@st.cache_resource
def games_store() -> Dict[str, Dict[str, Any]]:
    return {}

STORE = games_store()
LEADERBOARD_PATH = os.path.join("data", "leaderboard.json")


def load_leaderboard() -> List[Dict[str, Any]]:
    try:
        with open(LEADERBOARD_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return []


def save_leaderboard(entries: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(LEADERBOARD_PATH), exist_ok=True)
    with open(LEADERBOARD_PATH, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=True, indent=2)


def add_leaderboard_entries(new_entries: List[Dict[str, Any]]) -> None:
    if not new_entries:
        return
    entries = load_leaderboard()
    existing = {(e.get("game_id"), e.get("player")) for e in entries}
    for entry in new_entries:
        key = (entry.get("game_id"), entry.get("player"))
        if key not in existing:
            entries.append(entry)
            existing.add(key)
    if len(entries) > 300:
        entries = entries[-300:]
    save_leaderboard(entries)


def render_html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def set_active_game(gid: str, player: int, key: str | None) -> None:
    st.session_state["active_game"] = gid
    st.session_state["active_player"] = player
    st.session_state["active_key"] = key or ""


def clear_active_game() -> None:
    st.session_state.pop("active_game", None)
    st.session_state.pop("active_player", None)
    st.session_state.pop("active_key", None)


def _first_param(value: Any) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    if value is None:
        return None
    return str(value)


def get_query_params() -> Dict[str, str | None]:
    if hasattr(st, "query_params"):
        qp = st.query_params
        return {
            "game": _first_param(qp.get("game")),
            "player": _first_param(qp.get("player")),
            "key": _first_param(qp.get("key")),
        }
    qp = st.experimental_get_query_params()
    return {
        "game": _first_param(qp.get("game")),
        "player": _first_param(qp.get("player")),
        "key": _first_param(qp.get("key")),
    }


def set_query_params(params: Dict[str, Any]) -> None:
    clean = {k: str(v) for k, v in params.items() if v is not None}
    if hasattr(st, "query_params"):
        st.query_params.update(clean)
    else:
        st.experimental_set_query_params(**clean)


def inject_lobby_styles() -> None:
    style_block = textwrap.dedent(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=Fraunces:wght@400;600;700&display=swap');
        :root {
            --bg-cream: #faf8f5;
            --bg-white: #ffffff;
            --accent-coral: #ff6b5b;
            --accent-peach: #ffb4a2;
            --accent-sage: #84a98c;
            --accent-gold: #e9c46a;
            --text-dark: #2d3436;
            --text-muted: #636e72;
            --border-soft: #eee8e0;
        }

        html, body, [class*="stApp"] {
            font-family: "DM Sans", sans-serif;
            color: var(--text-dark);
        }

        div[data-testid="stAppViewContainer"] {
            background: var(--bg-cream);
        }

        div[data-testid="stAppViewContainer"] > .main .block-container {
            max-width: 1100px;
            padding-top: 40px;
            padding-bottom: 60px;
            position: relative;
            z-index: 1;
        }

        .bg-shapes {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 0;
            overflow: hidden;
        }

        .shape {
            position: absolute;
            opacity: 0.4;
        }

        .shape-1 {
            top: -100px;
            right: -50px;
            width: 380px;
            height: 380px;
            background: linear-gradient(135deg, var(--accent-peach), var(--accent-coral));
            border-radius: 50%;
            filter: blur(80px);
        }

        .shape-2 {
            bottom: -140px;
            left: -90px;
            width: 460px;
            height: 460px;
            background: linear-gradient(135deg, var(--accent-sage), #b7e4c7);
            border-radius: 50%;
            filter: blur(100px);
        }

        .shape-3 {
            top: 40%;
            right: 20%;
            width: 180px;
            height: 180px;
            background: var(--accent-gold);
            border-radius: 50%;
            filter: blur(60px);
            opacity: 0.3;
        }

        .lobby-header {
            display: flex;
            align-items: center;
            gap: 18px;
            margin-bottom: 36px;
        }

        .logo-mark {
            width: 64px;
            height: 64px;
            background: linear-gradient(135deg, var(--accent-coral), var(--accent-peach));
            border-radius: 18px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 10px 30px rgba(255, 107, 91, 0.3);
            transform: rotate(-5deg);
            transition: transform 0.3s ease;
        }

        .logo-mark:hover {
            transform: rotate(0deg) scale(1.04);
        }

        .logo-text h1 {
            font-family: "Fraunces", serif;
            font-size: 2.2rem;
            font-weight: 700;
            letter-spacing: -1px;
            margin-bottom: 6px;
        }

        .logo-text p {
            color: var(--text-muted);
            font-size: 0.95rem;
            margin: 0;
        }

        .card-anchor {
            display: block;
            height: 0;
            width: 0;
        }

        div[data-testid="stVerticalBlock"]:has(.card-anchor) {
            background: var(--bg-white);
            border-radius: 22px;
            padding: 26px;
            border: 1px solid var(--border-soft);
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.02), 0 20px 60px rgba(0, 0, 0, 0.05);
            margin-bottom: 22px;
        }

        .section-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
        }

        .section-icon {
            width: 36px;
            height: 36px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.1rem;
        }

        .icon-gold { background: linear-gradient(135deg, #fff3cd, var(--accent-gold)); }
        .icon-coral { background: linear-gradient(135deg, #ffe5e0, var(--accent-coral)); }
        .icon-sage { background: linear-gradient(135deg, #e0f0e3, var(--accent-sage)); }

        .section-title {
            font-family: "Fraunces", serif;
            font-size: 1.3rem;
            font-weight: 600;
        }

        div[data-baseweb="input"] input,
        div[data-baseweb="select"] > div {
            background: var(--bg-cream);
            border: 2px solid transparent;
            border-radius: 14px;
            min-height: 46px;
            color: var(--text-dark) !important;
        }

        div[data-baseweb="input"] input:focus,
        div[data-baseweb="select"] > div:focus-within {
            border-color: var(--accent-coral);
            background: #ffffff;
            box-shadow: none;
        }

        /* Texte visible dans les selects et inputs */
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] div {
            color: var(--text-dark) !important;
        }

        /* Number input */
        input[type="number"] {
            color: var(--text-dark) !important;
        }

        /* Labels des formulaires */
        label {
            color: var(--text-dark) !important;
        }

        button[kind="primary"] {
            background: linear-gradient(135deg, var(--accent-coral), #ff8a7a) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 14px !important;
            padding: 0.6rem 1rem !important;
            font-weight: 600 !important;
            box-shadow: 0 8px 25px rgba(255, 107, 91, 0.35) !important;
        }

        button[kind="primary"]:hover {
            transform: translateY(-1px);
        }

        button[kind="secondary"] {
            background: transparent !important;
            color: var(--accent-coral) !important;
            border: 2px solid var(--accent-coral) !important;
            border-radius: 12px !important;
            font-weight: 600 !important;
        }

        button[kind="secondary"]:hover {
            background: var(--accent-coral) !important;
            color: #ffffff !important;
        }

        .leaderboard-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 6px;
            table-layout: fixed;
        }

        .leaderboard-table th:nth-child(1) { width: 50px; }
        .leaderboard-table th:nth-child(2) { width: 20%; }
        .leaderboard-table th:nth-child(3) { width: 22%; }
        .leaderboard-table th:nth-child(4) { width: 16%; }
        .leaderboard-table th:nth-child(5) { width: 14%; }
        .leaderboard-table th:nth-child(6) { width: 10%; }
        .leaderboard-table th:nth-child(7) { width: 18%; }

        .leaderboard-table th {
            text-align: left;
            padding: 10px 6px;
            color: var(--text-muted);
            font-weight: 500;
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            border-bottom: 2px solid var(--border-soft);
        }

        .leaderboard-table td {
            padding: 12px 6px;
            border-bottom: 1px solid var(--border-soft);
            vertical-align: middle;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        /* Mobile: cacher certaines colonnes */
        @media (max-width: 768px) {
            .leaderboard-table th:nth-child(6),
            .leaderboard-table td:nth-child(6),
            .leaderboard-table th:nth-child(7),
            .leaderboard-table td:nth-child(7) {
                display: none;
            }

            .leaderboard-table th:nth-child(1) { width: 40px; }
            .leaderboard-table th:nth-child(2) { width: 30%; }
            .leaderboard-table th:nth-child(3) { width: 25%; }
            .leaderboard-table th:nth-child(4) { width: 20%; }
            .leaderboard-table th:nth-child(5) { width: 25%; }

            .leaderboard-table th,
            .leaderboard-table td {
                padding: 8px 4px;
                font-size: 0.7rem;
            }

            .player-avatar {
                width: 24px;
                height: 24px;
                font-size: 0.65rem;
            }

            .rank-badge {
                width: 28px;
                height: 28px;
                font-size: 0.75rem;
            }

            .efficiency-bar {
                width: 50px;
            }

            .efficiency-text {
                font-size: 0.7rem;
            }

            .score-value {
                font-size: 0.85rem;
            }

            .difficulty-pill {
                padding: 4px 8px;
                font-size: 0.65rem;
            }
        }

        .leaderboard-row:hover {
            background: #fdfcfa;
        }

        .rank-badge {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.85rem;
            background: #f5f0ea;
            color: #7f8c8d;
        }

        .rank-1 { background: linear-gradient(135deg, #ffd700, #ffb700); color: #7a5c00; }
        .rank-2 { background: linear-gradient(135deg, #d7d7d7, #b9b9b9); color: #5f6a6a; }
        .rank-3 { background: linear-gradient(135deg, #d0a37b, #b8875e); color: #5a3e24; }

        .player-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .player-avatar {
            width: 30px;
            height: 30px;
            border-radius: 8px;
            background: linear-gradient(135deg, var(--accent-peach), var(--accent-coral));
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
            font-size: 0.75rem;
        }

        .efficiency-wrapper {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .efficiency-bar {
            width: 70px;
            height: 7px;
            background: var(--border-soft);
            border-radius: 10px;
            overflow: hidden;
        }

        .efficiency-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent-sage), #52b788);
            border-radius: 10px;
        }

        .efficiency-text {
            font-weight: 600;
            color: var(--accent-sage);
            font-size: 0.78rem;
        }

        .score-value {
            font-weight: 700;
            font-size: 0.95rem;
        }

        .difficulty-pill {
            display: inline-flex;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }

        .diff-easy { background: #e0f2f1; color: #00796b; }
        .diff-medium { background: #fff3e0; color: #e65100; }
        .diff-hard { background: #ffebee; color: #c62828; }

        @media (max-width: 900px) {
            .lobby-header {
                flex-direction: column;
                align-items: flex-start;
            }

            .logo-text h1 {
                font-size: 1.8rem;
            }

            .logo-text p {
                font-size: 0.85rem;
            }

            .section-title {
                font-size: 1.1rem;
            }

            div[data-testid="stAppViewContainer"] > .main .block-container {
                padding-top: 20px;
                padding-bottom: 40px;
            }
        }

        /* Très petits écrans */
        @media (max-width: 480px) {
            .logo-text h1 {
                font-size: 1.5rem;
            }

            .logo-mark {
                width: 50px;
                height: 50px;
            }

            .section-title {
                font-size: 1rem;
            }

            button[kind="primary"],
            button[kind="secondary"] {
                font-size: 0.9rem;
                padding: 0.5rem 0.8rem !important;
            }
        }
        </style>
        """
    ).strip()
    st.markdown(style_block, unsafe_allow_html=True)
    render_html_block(
        textwrap.dedent(
            """
            <div class="bg-shapes">
                <div class="shape shape-1"></div>
                <div class="shape shape-2"></div>
                <div class="shape shape-3"></div>
            </div>
            """
        ).strip()
    )


def leaderboard_html(entries: List[Dict[str, Any]]) -> str:
    rows = []
    for idx, entry in enumerate(entries, 1):
        name = entry.get("player", "") or "Player"
        raw_name = str(name)
        name_safe = html.escape(raw_name)
        initials = "".join([part[0] for part in raw_name.split()[:2]])[:2].upper()
        eff = float(entry.get("efficiency", 0) or 0)
        eff = max(0.0, min(1.0, eff))
        eff_pct = eff * 100
        score = int(entry.get("score", 0) or 0)
        diff = (entry.get("difficulty", "") or "").lower()
        diff_class = "diff-medium"
        if diff == "easy":
            diff_class = "diff-easy"
        elif diff == "hard":
            diff_class = "diff-hard"
        rounds = int(entry.get("rounds", 0) or 0)
        ts = entry.get("timestamp")
        date = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        rank_class = f"rank-{idx}" if idx <= 3 else ""
        rows.append(
            textwrap.dedent(
                f"""
                <tr class="leaderboard-row">
                    <td><div class="rank-badge {rank_class}">{idx}</div></td>
                    <td>
                        <div class="player-info">
                            <div class="player-avatar">{initials or "P"}</div>
                            <span class="player-name">{name_safe}</span>
                        </div>
                    </td>
                    <td>
                        <div class="efficiency-wrapper">
                            <div class="efficiency-bar">
                                <div class="efficiency-fill" style="width: {eff_pct:.1f}%"></div>
                            </div>
                            <span class="efficiency-text">{eff_pct:.1f}%</span>
                        </div>
                    </td>
                    <td class="score-value">{score:,}</td>
                    <td><span class="difficulty-pill {diff_class}">{diff.title() or "Medium"}</span></td>
                    <td>{rounds}</td>
                    <td>{date}</td>
                </tr>
                """
            ).strip()
        )
    return textwrap.dedent(
        f"""
        <table class="leaderboard-table">
            <thead>
                <tr>
                    <th>Rank</th>
                    <th>Player</th>
                    <th>Efficiency</th>
                    <th>Score</th>
                    <th>Difficulty</th>
                    <th>Rounds</th>
                    <th>Date</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        """
    ).strip()


def parse_pop(raw: str | None) -> int:
    if not raw:
        return 0
    return int("".join(POP_RE.findall(raw)) or 0)


def short(text: str) -> str:
    return text.split(",")[0].strip()

def maybe_autorefresh(active: bool, interval_ms: int = 4000) -> None:
    if st_autorefresh is None:
        return
    if active:
        st_autorefresh(interval=interval_ms, key="triangle_game_refresh")


def wait_for_state_change(
    ready_check: Callable[[], bool],
    interval_s: float = 0.4,
) -> None:
    if ready_check():
        st.rerun()
    if st_autorefresh is not None:
        st_autorefresh(interval=int(interval_s * 1000), key="triangle_game_refresh")
        return
    time.sleep(interval_s)
    st.rerun()


def normalize_city_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = name.strip().lower()
    name = "".join(
        c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn"
    )
    name = re.sub(r"[-'`]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def is_city_result(raw: Dict[str, Any]) -> bool:
    if not isinstance(raw, dict):
        return False
    addr = raw.get("address", {}) or {}
    addresstype = raw.get("addresstype") or raw.get("type")
    if addresstype == "country" or raw.get("type") == "country":
        return False
    if raw.get("type") in DISALLOWED_TYPES or addresstype in DISALLOWED_TYPES:
        extratags = raw.get("extratags", {}) or {}
        if extratags.get("linked_place") == "city" or extratags.get("place") == "city":
            return True
        return False
    for key in CITY_ADDRESS_KEYS:
        if key in addr:
            return True
    return False


def candidate_names(raw: Dict[str, Any], fallback: str | None) -> List[str]:
    names = set()
    if fallback:
        names.add(fallback)
    display = raw.get("display_name")
    if display:
        names.add(short(display))
    raw_name = raw.get("name")
    if isinstance(raw_name, str):
        names.add(raw_name)
    namedetails = raw.get("namedetails", {}) or {}
    for val in namedetails.values():
        if isinstance(val, str):
            names.add(val)
    addr = raw.get("address", {}) or {}
    for key in CITY_ADDRESS_KEYS:
        val = addr.get(key)
        if isinstance(val, str):
            names.add(val)
    return [n for n in names if n]


def name_match_score(query_norm: str, names: List[str]) -> int:
    if not query_norm:
        return 0
    best = 0
    for name in names:
        norm = normalize_city_name(name)
        if not norm:
            continue
        score = fuzz.WRatio(query_norm, norm)
        if score > best:
            best = score
    return best


@st.cache_resource
def city_catalog() -> List[Tuple[int, str, str, float, float]]:
    try:
        import geonamescache
    except Exception:
        return []
    gc = geonamescache.GeonamesCache()
    cities = gc.get_cities()
    catalog = []
    for city in cities.values():
        pop = city.get("population") or 0
        try:
            pop = int(pop)
        except (TypeError, ValueError):
            continue
        if pop <= 0:
            continue
        try:
            lat = float(city.get("latitude"))
            lon = float(city.get("longitude"))
        except (TypeError, ValueError):
            continue
        name = city.get("name") or ""
        if not name:
            continue
        country = city.get("countrycode") or ""
        catalog.append((pop, name, country, lat, lon))
    catalog.sort(key=lambda item: item[0], reverse=True)
    return catalog


@st.cache_resource
def city_name_index() -> Dict[str, Tuple[int, str, str, float, float]]:
    catalog = city_catalog()
    index: Dict[str, Tuple[int, str, str, float, float]] = {}
    for pop, name, country, lat, lon in catalog:
        norm = normalize_city_name(name)
        if not norm:
            continue
        if norm not in index or pop > index[norm][0]:
            index[norm] = (pop, name, country, lat, lon)
    return index


def fast_city_lookup(name: str) -> Tuple[float, float, str, str, int] | None:
    index = city_name_index()
    if not index:
        return None
    norm = normalize_city_name(name)
    entry = index.get(norm)
    if not entry:
        return None
    pop, city_name, _country, lat, lon = entry
    return (lat, lon, city_name, city_name, int(pop))


@st.cache_data(show_spinner=False)
def best_cities_for_triangle(
    tri_key: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]],
    count: int,
    excluded_norms: Tuple[str, ...],
) -> List[Tuple[str, str, int, float, float]]:
    if count <= 0:
        return []
    catalog = city_catalog()
    if not catalog:
        return []
    tri = [(lat, lon) for lat, lon in tri_key]
    used = set(excluded_norms)
    results = []
    for pop, name, country, lat, lon in catalog:
        if len(results) >= count:
            break
        norm = normalize_city_name(name)
        if norm in used:
            continue
        if inside_geodesic_triangle(tri, (lat, lon)):
            results.append((name, country, pop, lat, lon))
            used.add(norm)
    return results


@st.cache_resource
def geocoder() -> Nominatim:
    return Nominatim(user_agent="triangle_game")


@st.cache_data(show_spinner=False, ttl=7 * 24 * 3600)
def wikidata_population(qid: str) -> int:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": "triangle_game/1.0"}, timeout=10)
    except requests.RequestException:
        return 0
    if resp.status_code != 200:
        return 0
    try:
        data = resp.json()
    except ValueError:
        return 0
    entity = data.get("entities", {}).get(qid, {})
    claims = entity.get("claims", {})
    pop_claims = claims.get("P1082", [])
    best_pop = 0
    best_time = ""
    for claim in pop_claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})
        amount = value.get("amount")
        if not amount:
            continue
        try:
            pop = int(float(amount))
        except (ValueError, TypeError):
            continue
        time_vals = claim.get("qualifiers", {}).get("P585", [])
        time_val = ""
        if time_vals:
            time_val = time_vals[0].get("datavalue", {}).get("value", {}).get("time", "")
        if time_val and time_val > best_time:
            best_time = time_val
            best_pop = pop
        elif not time_val and pop > best_pop:
            best_pop = pop
    return max(best_pop, 0)


def extract_population_nom(raw: Dict[str, Any]) -> int:
    if not isinstance(raw, dict):
        return 0
    extratags = raw.get("extratags") or {}
    return parse_pop(extratags.get("population"))


def extract_population_wd(raw: Dict[str, Any]) -> int:
    if not isinstance(raw, dict):
        return 0
    extratags = raw.get("extratags") or {}
    qid = extratags.get("wikidata") or raw.get("wikidata")
    return wikidata_population(qid) if qid else 0


def show_geo_error(reason: str | None) -> None:
    if reason == "country":
        st.error("Country selection is not allowed.")
    elif reason == "not_city":
        st.error("Only cities and towns are allowed.")
    elif reason == "no_match":
        st.error("No close match found - check the spelling.")
    elif reason == "population":
        st.warning("City not available (population missing).")
    elif reason == "geocoder":
        st.error("Geocoder unavailable - try again.")
    else:
        st.error("City not found.")


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def bilingual_geocode(
    query: str, require_population: bool = False
) -> Tuple[Tuple[float, float, str, str, int] | None, str | None]:
    """Return ((lat, lon, name_fr, name_local, population), reason)."""
    nominatim = geocoder()
    query_norm = normalize_city_name(query)

    def _search(featuretype: str | None):
        try:
            return nominatim.geocode(
                query,
                language="fr",
                addressdetails=True,
                extratags=True,
                namedetails=True,
                exactly_one=False,
                limit=10,
                featuretype=featuretype,
            )
        except (GeocoderUnavailable, GeocoderTimedOut):
            return None

    def _pick_best(results):
        saw_country = False
        candidates = []
        for res in results:
            raw = res.raw or {}
            addresstype = raw.get("addresstype") or raw.get("type")
            if addresstype == "country" or raw.get("type") == "country":
                saw_country = True
                continue
            if not is_city_result(raw):
                continue
            fallback = short(res.address) if res.address else None
            names = candidate_names(raw, fallback)
            name_score = name_match_score(query_norm, names)
            pop_nom = extract_population_nom(raw)
            importance = raw.get("importance")
            try:
                importance_score = float(importance or 0.0)
            except (TypeError, ValueError):
                importance_score = 0.0
            candidates.append(
                {
                    "res": res,
                    "raw": raw,
                    "name_score": name_score,
                    "pop_nom": pop_nom,
                    "importance": importance_score,
                }
            )
        if not candidates:
            return None, "country" if saw_country else "not_city"

        candidates.sort(key=lambda c: c["name_score"], reverse=True)
        for c in candidates[:3]:
            c["pop"] = c["pop_nom"] or extract_population_wd(c["raw"])
        for c in candidates[3:]:
            c["pop"] = c["pop_nom"]

        for c in candidates:
            pop = c["pop"]
            c["score"] = (
                c["name_score"] * 1.5
                + math.log10(pop + 10) * 20
                + c["importance"] * 10
            )
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        if best["name_score"] < 60:
            return None, "no_match"

        res = best["res"]
        raw = best["raw"]
        lat, lon = res.latitude, res.longitude
        name_fr = short(res.address) if res.address else query
        namedetails = raw.get("namedetails", {}) or {}
        name_local = (
            namedetails.get("name")
            or namedetails.get("name:fr")
            or namedetails.get("name:en")
            or name_fr
        )
        if not name_local:
            try:
                local = nominatim.reverse((lat, lon), language="", addressdetails=True)
                name_local = short(local.address) if local else name_fr
            except (GeocoderUnavailable, GeocoderTimedOut):
                name_local = name_fr
        pop = best["pop"] or extract_population_wd(raw)
        if require_population and pop == 0:
            return None, "population"
        return (lat, lon, name_fr, name_local, pop), None

    results = _search("city")
    if results:
        pick, reason = _pick_best(results)
        if pick:
            return pick, None
        if reason not in ("not_city", "no_match"):
            return None, reason

    results = _search(None)
    if results:
        return _pick_best(results)
    return None, "not_found"


@lru_cache(maxsize=8)
def get_proj(latc: float, lonc: float):
    crs = CRS.from_proj4(f"+proj=laea +lat_0={latc} +lon_0={lonc} +datum=WGS84 +units=m +no_defs")
    fwd = Transformer.from_crs("epsg:4326", crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, "epsg:4326", always_xy=True).transform
    return fwd, inv


def gc_line(p1, p2, n=48):
    lat1, lon1 = p1
    lat2, lon2 = p2
    seg = GEOD.npts(lon1, lat1, lon2, lat2, n-1)
    return [(lat1, lon1), *[(lat, lon) for lon, lat in seg], (lat2, lon2)]


def geodesic_edge(p1, p2, max_step_km=250, min_points=12, max_points=96):
    lat1, lon1 = p1
    lat2, lon2 = p2
    _, _, dist_m = GEOD.inv(lon1, lat1, lon2, lat2)
    n = max(min_points, int(dist_m / (max_step_km * 1000)) + 2)
    n = min(n, max_points)
    return gc_line(p1, p2, n=n)


def geodesic_polygon_points(tri, max_step_km=250):
    ring = []
    for i in range(3):
        edge = geodesic_edge(tri[i], tri[(i + 1) % 3], max_step_km=max_step_km)
        ring.extend(edge[:-1])
    return ring


def build_poly_proj(tri, fwd):
    ring = geodesic_polygon_points(tri)
    return Polygon([fwd(lon, lat) for lat, lon in ring])


def unwrap_longitudes(lons):
    if not lons:
        return lons
    unwrapped = [lons[0]]
    for lon in lons[1:]:
        prev = unwrapped[-1]
        lon_adj = min((lon - 360, lon, lon + 360), key=lambda x: abs(x - prev))
        unwrapped.append(lon_adj)
    return unwrapped


def wrap_lon(lon):
    return ((lon + 180) % 360) - 180


def _unit_vec(lat, lon):
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    cos_lat = math.cos(lat_r)
    return (cos_lat * math.cos(lon_r), cos_lat * math.sin(lon_r), math.sin(lat_r))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def inside_geodesic_triangle(tri, p, eps=1e-10):
    if len(tri) != 3:
        return False
    a = _unit_vec(*tri[0])
    b = _unit_vec(*tri[1])
    c = _unit_vec(*tri[2])

    n_ab = _cross(a, b)
    n_bc = _cross(b, c)
    n_ca = _cross(c, a)
    if _norm(n_ab) < eps or _norm(n_bc) < eps or _norm(n_ca) < eps:
        return False

    s_ab = _dot(n_ab, c)
    s_bc = _dot(n_bc, a)
    s_ca = _dot(n_ca, b)
    if abs(s_ab) < eps or abs(s_bc) < eps or abs(s_ca) < eps:
        return False

    p_vec = _unit_vec(*p)

    if s_ab > 0 and _dot(n_ab, p_vec) < -eps:
        return False
    if s_ab < 0 and _dot(n_ab, p_vec) > eps:
        return False
    if s_bc > 0 and _dot(n_bc, p_vec) < -eps:
        return False
    if s_bc < 0 and _dot(n_bc, p_vec) > eps:
        return False
    if s_ca > 0 and _dot(n_ca, p_vec) < -eps:
        return False
    if s_ca < 0 and _dot(n_ca, p_vec) > eps:
        return False
    return True


def globe_fill_points(tri, step_deg=3.0, max_points=3500):
    lats = [lat for lat, _ in tri]
    lons = [lon for _, lon in tri]
    lons_u = unwrap_longitudes(lons)
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons_u), max(lons_u)
    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon

    if lat_span <= 0 or lon_span <= 0:
        return [], []

    lat_count = int(lat_span / step_deg) + 1
    lon_count = int(lon_span / step_deg) + 1
    total = max(1, lat_count * lon_count)
    if total > max_points:
        scale = math.sqrt(total / max_points)
        step_deg = step_deg * scale

    fill_lat = []
    fill_lon = []
    lat = min_lat
    while lat <= max_lat + 1e-6:
        lon = min_lon
        while lon <= max_lon + 1e-6:
            if inside_geodesic_triangle(tri, (lat, lon)):
                fill_lat.append(lat)
                fill_lon.append(wrap_lon(lon))
            lon += step_deg
        lat += step_deg

    if not fill_lat:
        lat_c = sum(lats) / 3
        lon_c = sum(lons_u) / 3
        if inside_geodesic_triangle(tri, (lat_c, lon_c)):
            fill_lat.append(lat_c)
            fill_lon.append(wrap_lon(lon_c))
    return fill_lat, fill_lon


def build_globe(tri, labels, submissions):
    lat_c = sum(lat for lat, _ in tri) / 3
    lon_c = sum(lon for _, lon in tri) / 3
    ring = geodesic_polygon_points(tri)
    ring_lat = [lat for lat, _ in ring]
    ring_lon = unwrap_longitudes([lon for _, lon in ring])
    if ring_lat and ring_lon:
        close_lon = ring_lon[0]
        close_lon = min(
            (close_lon - 360, close_lon, close_lon + 360),
            key=lambda x: abs(x - ring_lon[-1]),
        )
        ring_lon.append(close_lon)
        ring_lat.append(ring_lat[0])

    fig = go.Figure()
    fig.add_trace(
        go.Scattergeo(
            lat=ring_lat,
            lon=ring_lon,
            mode="lines",
            line=dict(color="red", width=2),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    fill_lat, fill_lon = globe_fill_points(tri)
    if fill_lat:
        fig.add_trace(
            go.Scattergeo(
                lat=fill_lat,
                lon=fill_lon,
                mode="markers",
                marker=dict(size=3, color="red", opacity=0.15),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    for (lat, lon), label in zip(tri, labels):
        fig.add_trace(
            go.Scattergeo(
                lat=[lat],
                lon=[lon],
                text=[label],
                mode="markers",
                marker=dict(size=8, color="blue"),
                hoverinfo="text",
                showlegend=False,
            )
        )

    points_by_color = {}
    texts_by_color = {}
    for p in sorted(submissions.keys()):
        for c in submissions[p]:
            color = COLORS[p - 1]
            if c.get("outside"):
                color = "red"
            points_by_color.setdefault(color, {"lat": [], "lon": []})
            texts_by_color.setdefault(color, [])
            points_by_color[color]["lat"].append(c["lat"])
            points_by_color[color]["lon"].append(c["lon"])
            label = f"P{p}: {c['label']} ({c['population']})"
            if c.get("outside"):
                label += " OUT"
            texts_by_color[color].append(label)

    for color, coords in points_by_color.items():
        fig.add_trace(
            go.Scattergeo(
                lat=coords["lat"],
                lon=coords["lon"],
                text=texts_by_color[color],
                mode="markers",
                marker=dict(size=6, color=color),
                hoverinfo="text",
                showlegend=False,
            )
        )

    fig.update_geos(
        projection_type="orthographic",
        projection_rotation=dict(lat=lat_c, lon=lon_c),
        showland=True,
        landcolor="rgb(230,230,230)",
        showocean=True,
        oceancolor="rgb(200,220,240)",
        showcountries=True,
        countrycolor="rgb(160,160,160)",
        showlakes=True,
        lakecolor="rgb(200,220,240)",
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
    return fig

# ──────────────────────────────────────────────────────────────────────────────
# Game‑state helpers
# ──────────────────────────────────────────────────────────────────────────────

def new_state(n_players: int) -> Dict[str, Any]:
    now = time.time()
    return {
        "players": n_players,
        "triangle": [],              # [(lat,lon)]
        "triangle_labels": [],        # ["fr / local"]
        "triangle_names": [],
        "proj": None,
        "poly_proj": None,
        "submissions": {i+1: [] for i in range(n_players)},  # list of dicts
        "scores": {i+1: 0 for i in range(n_players)},
        "turn": 1,
        "rounds": 3,
        "submitted_counts": {i+1: 0 for i in range(n_players)},
        "stage": "triangle",        # triangle|scoring|name_entry|finished
        "name": "",
        "triangle_difficulty": "Medium",
        "leaderboard_saved": False,
        "player_names": {i + 1: "" for i in range(n_players)},
        "created_at": now,
        "last_seen": now,
        "seats": {i + 1: None for i in range(n_players)},
        "seat_keys": {i + 1: None for i in range(n_players)},
    }


def get_session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex
    return st.session_state["session_id"]


def ensure_state_meta(state: Dict[str, Any]) -> None:
    players = state["players"]
    if "seats" not in state:
        state["seats"] = {}
    if "seat_keys" not in state:
        state["seat_keys"] = {}
    for i in range(1, players + 1):
        state["seats"].setdefault(i, None)
        state["seat_keys"].setdefault(i, None)
    if "triangle_names" not in state:
        state["triangle_names"] = []
    if not state["triangle_names"] and state.get("triangle_labels"):
        state["triangle_names"] = [label.split(" / ")[0] for label in state["triangle_labels"]]
    if "created_at" not in state:
        state["created_at"] = time.time()
    if "last_seen" not in state:
        state["last_seen"] = time.time()
    if "name" not in state:
        state["name"] = "Game"
    if "triangle_difficulty" not in state:
        state["triangle_difficulty"] = "Medium"
    if "leaderboard_saved" not in state:
        state["leaderboard_saved"] = False
    if "player_names" not in state:
        state["player_names"] = {}
    for i in range(1, players + 1):
        state["player_names"].setdefault(i, "")


def touch_state(state: Dict[str, Any]) -> None:
    state["last_seen"] = time.time()


def available_seats(state: Dict[str, Any]) -> List[int]:
    return [p for p, sid in state["seats"].items() if sid is None]


def seat_key_for(state: Dict[str, Any], player: int) -> str:
    key = state["seat_keys"].get(player)
    if not key:
        key = uuid.uuid4().hex
        state["seat_keys"][player] = key
    return key


def claim_seat(
    state: Dict[str, Any],
    player: int,
    session_id: str,
    seat_key: str | None = None,
    force: bool = False,
) -> Tuple[bool, str | None]:
    seats = state["seats"]
    keys = state["seat_keys"]
    current = seats.get(player)
    if current in (None, session_id):
        seats[player] = session_id
        return True, seat_key_for(state, player)
    current_key = keys.get(player)
    if seat_key and current_key and seat_key == current_key:
        seats[player] = session_id
        return True, current_key
    if force:
        seats[player] = session_id
        return True, seat_key_for(state, player)
    return False, current_key


def render_lobby(session_id: str) -> None:
    inject_lobby_styles()
    render_html_block(
        textwrap.dedent(
            """
            <div class="lobby-header">
                <div class="logo-mark">
                    <svg viewBox="0 0 40 40" fill="none" width="34" height="34">
                        <polygon points="20,5 35,32 5,32" fill="white"/>
                    </svg>
                </div>
                <div class="logo-text">
                    <h1>Triangle Game</h1>
                    <p>Find cities within a geographic triangle</p>
                </div>
            </div>
            """
        ).strip()
    )

    # Leaderboard en haut
    render_html_block('<span class="card-anchor"></span>')
    render_html_block(
        textwrap.dedent(
            """
            <div class="section-header">
                <div class="section-icon icon-gold">🏆</div>
                <div class="section-title">Leaderboard</div>
            </div>
            """
        ).strip()
    )
    leaderboard = load_leaderboard()
    if leaderboard:
        leaderboard = sorted(
            leaderboard,
            key=lambda e: (e.get("efficiency", 0), e.get("score", 0)),
            reverse=True,
        )
        top = leaderboard[:10]
        render_html_block(leaderboard_html(top))
    else:
        st.caption("No leaderboard entries yet.")

    # Play solo et Create game l'un sur l'autre
    render_html_block('<span class="card-anchor"></span>')
    render_html_block(
        textwrap.dedent(
            """
            <div class="section-header">
                <div class="section-icon icon-coral">🎮</div>
                <div class="section-title">Play solo</div>
            </div>
            """
        ).strip()
    )
    solo_difficulty = st.selectbox(
        "Triangle difficulty",
        DIFFICULTY_LEVELS,
        index=1,
        key="solo_difficulty",
    )
    solo_rounds = st.number_input("Rounds", 1, 10, 3, key="solo_rounds")
    if st.button("Play solo", key="play_solo", type="primary"):
        gid = uuid.uuid4().hex[:8]
        state = new_state(1)
        state["rounds"] = int(solo_rounds)
        state["triangle_difficulty"] = solo_difficulty
        state["name"] = "Solo game"
        STORE[gid] = state
        ok, key = claim_seat(state, 1, session_id)
        if ok:
            st.query_params.update({"game": gid, "player": "1", "key": key})
            set_active_game(gid, 1, key)
        st.rerun()

    render_html_block('<span class="card-anchor"></span>')
    render_html_block(
        textwrap.dedent(
            """
            <div class="section-header">
                <div class="section-icon icon-sage">⚔️</div>
                <div class="section-title">Create game</div>
            </div>
            """
        ).strip()
    )
    with st.form("create_game"):
        name = st.text_input("Game name (optional)")
        n_players = st.selectbox(
            "Number of players",
            [2, 3],
        )
        n_rounds = st.number_input("Rounds per player", 1, 10, 3)
        difficulty = st.selectbox(
            "Random triangle difficulty",
            DIFFICULTY_LEVELS,
            index=1,
            help="Used when generating a random triangle.",
        )
        create = st.form_submit_button("Create game", type="primary")
    if create:
        gid = uuid.uuid4().hex[:8]
        state = new_state(n_players)
        state["rounds"] = int(n_rounds)
        state["triangle_difficulty"] = difficulty
        state["name"] = name.strip() or f"Game {gid}"
        STORE[gid] = state
        ok, key = claim_seat(state, 1, session_id)
        if ok:
            st.query_params.update({"game": gid, "player": "1", "key": key})
            set_active_game(gid, 1, key)
        st.rerun()

    render_html_block('<span class="card-anchor"></span>')
    render_html_block(
        textwrap.dedent(
            """
            <div class="section-header">
                <div class="section-icon icon-gold">🎯</div>
                <div class="section-title">Join a game</div>
            </div>
            """
        ).strip()
    )
    active = []
    for gid, state in STORE.items():
        ensure_state_meta(state)
        if state.get("stage") == "finished":
            continue
        active.append((gid, state))
    if not active:
        st.info("No active games yet.")
        return
    active.sort(key=lambda item: item[1].get("created_at", 0), reverse=True)
    for gid, state in active:
        available = available_seats(state)
        label = state.get("name") or f"Game {gid}"
        row_cols = st.columns([3, 1])
        with row_cols[0]:
            st.markdown(
                f"**{label}**  \nID {gid} | {state['players']} players | {state['stage']}",
            )
        with row_cols[1]:
            if available:
                seat_cols = st.columns(len(available))
                for col, seat in zip(seat_cols, available):
                        if col.button(f"Join P{seat}", key=f"join_{gid}_{seat}"):
                            ok, key = claim_seat(state, seat, session_id)
                            if ok:
                                st.query_params.update(
                                    {"game": gid, "player": str(seat), "key": key}
                                )
                                set_active_game(gid, seat, key)
                                st.rerun()
                        else:
                            st.warning("Seat already taken.")
            else:
                st.caption("Full")


def render_seat_picker(gid: str, state: Dict[str, Any], session_id: str) -> None:
    name = state.get("name") or f"Game {gid}"
    st.header(name)
    st.write("Choose your seat to join.")
    st.text_input("Invite link", value=f"?game={gid}", disabled=True)

    available = available_seats(state)
    if not available:
        st.warning("Game is full.")
        if st.button("Back to lobby"):
            st.query_params.clear()
            clear_active_game()
            st.rerun()
        return

    cols = st.columns(len(available))
    for col, seat in zip(cols, available):
        if col.button(f"Join as Player {seat}", key=f"seat_{gid}_{seat}"):
            ok, key = claim_seat(state, seat, session_id)
            if ok:
                st.query_params.update({"game": gid, "player": str(seat), "key": key})
                set_active_game(gid, seat, key)
                st.rerun()
            else:
                st.warning("Seat already taken.")

    if st.button("Back to lobby", key="back_lobby"):
        st.query_params.clear()
        clear_active_game()
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# URL params handling
# ──────────────────────────────────────────────────────────────────────────────

qp = st.query_params
gid = qp.get("game")
player_raw = qp.get("player")
seat_key = qp.get("key")
if isinstance(seat_key, list):
    seat_key = seat_key[0] if seat_key else None
if not gid:
    gid = st.session_state.get("active_game")
    if gid:
        player_raw = st.session_state.get("active_player")
        seat_key = st.session_state.get("active_key")
try:
    player_id = int(player_raw) if player_raw else 0
except (ValueError, TypeError):
    player_id = 0

session_id = get_session_id()

if gid and gid not in STORE:
    st.error("Game not found.")
    if st.button("Back to lobby"):
        st.query_params.clear()
        clear_active_game()
        st.rerun()
    st.stop()

if not gid:
    render_lobby(session_id)
    st.stop()

state = STORE[gid]
ensure_state_meta(state)
touch_state(state)
players = state["players"]

if player_id <= 0 or player_id > players:
    render_seat_picker(gid, state, session_id)
    st.stop()

ok, key = claim_seat(state, player_id, session_id, seat_key=seat_key)
if not ok:
    st.warning("Seat already taken.")
    if st.button("Take seat"):
        ok, key = claim_seat(state, player_id, session_id, force=True)
        if ok and key:
            st.query_params.update({"game": gid, "player": str(player_id), "key": key})
            set_active_game(gid, player_id, key)
        st.rerun()
    if st.button("Choose another seat"):
        st.query_params.clear()
        st.query_params.update({"game": gid})
        clear_active_game()
        st.rerun()
    st.stop()
if key and seat_key != key:
    st.query_params.update({"game": gid, "player": str(player_id), "key": key})
    set_active_game(gid, player_id, key)
    st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Display utilities (must be defined before game logic)
# ──────────────────────────────────────────────────────────────────────────────

def player_label_func(player: int, game_state: Dict[str, Any]) -> str:
    name = game_state.get("player_names", {}).get(player, "")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"Player {player}"


def list_names_func(game_state: Dict[str, Any], num_players: int):
    st.write("#### 📝 Submitted so far")
    if game_state["triangle_labels"]:
        st.markdown("**Triangle:** " + ", ".join(game_state["triangle_labels"]))
    for p in range(1, num_players+1):
        if game_state["submissions"][p]:
            display = ", ".join(c["label"] for c in game_state["submissions"][p])
            st.markdown(f"{player_label_func(p, game_state)}: " + display)


def submission_name_fr(entry: Dict[str, Any]) -> str:
    name = entry.get("name_fr")
    if isinstance(name, str) and name.strip():
        return name
    label = entry.get("label", "")
    if isinstance(label, str) and " / " in label:
        return label.split(" / ")[0].strip()
    return label if isinstance(label, str) else ""


def triangle_edge_avg_km(coords: List[Tuple[float, float]]) -> float:
    dists = []
    for i in range(3):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[(i + 1) % 3]
        _, _, dist_m = GEOD.inv(lon1, lat1, lon2, lat2)
        dists.append(dist_m / 1000.0)
    return sum(dists) / 3.0 if dists else 0.0


def difficulty_ok(coords: List[Tuple[float, float]], difficulty: str) -> bool:
    avg_km = triangle_edge_avg_km(coords)
    if difficulty == "Easy":
        return avg_km >= 3500
    if difficulty == "Medium":
        return 2000 <= avg_km < 3500
    if difficulty == "Hard":
        return avg_km < 2000
    return True


def pick_triangle_candidates(difficulty: str) -> List[str]:
    regions = list(CAPITALS_BY_REGION.keys())
    if difficulty == "Easy":
        pick_regions = random.sample(regions, 3)
        return [random.choice(CAPITALS_BY_REGION[r]) for r in pick_regions]
    if difficulty == "Hard":
        region = "Europe"
        return random.sample(CAPITALS_BY_REGION[region], 3)
    region_a, region_b = random.sample(regions, 2)
    return random.sample(CAPITALS_BY_REGION[region_a], 2) + [
        random.choice(CAPITALS_BY_REGION[region_b])
    ]

label = state.get("name") or f"Game {gid}"
st.sidebar.info(label)
st.sidebar.write(f"Game ID: `{gid}`")
st.sidebar.write(f"You are {player_label_func(player_id, state)} / {players}")
st.sidebar.text_input("Invite link", value=f"?game={gid}", disabled=True)

# ──────────────────────────────────────────────────────────────────────────────
# Random triangle
# ──────────────────────────────────────────────────────────────────────────────

def random_triangle(difficulty: str | None = None):
    level = difficulty or state.get("triangle_difficulty") or "Medium"
    if level not in DIFFICULTY_LEVELS:
        level = "Medium"
    best = None
    for _ in range(8):  # try up to 8 times in case geocode fails
        choices = pick_triangle_candidates(level)
        data = []
        for c in choices:
            geo = fast_city_lookup(c)
            if not geo:
                geo, _ = bilingual_geocode(c, require_population=False)
            data.append(geo)
        if not all(data):
            continue
        coords = [(d[0], d[1]) for d in data]
        if difficulty_ok(coords, level):
            best = data
            break
        if best is None:
            best = data
    if not best:
        st.error("Random geocoding failed - try again")
        return
    coords = [(d[0], d[1]) for d in best]
    if not difficulty_ok(coords, level):
        st.warning("Triangle difficulty not met - using closest match.")
    labels = [f"{d[2]} / {d[3]}" for d in best]
    state["triangle"] = coords
    state["triangle_labels"] = labels
    state["triangle_names"] = [d[2] for d in best]
    lat_c = sum(lat for lat,_ in coords) / 3
    lon_c = sum(lon for _,lon in coords) / 3
    fwd,_ = get_proj(lat_c, lon_c)
    state["proj"] = fwd
    state["poly_proj"] = build_poly_proj(coords, fwd)
    state["stage"] = "scoring"
    state["turn"] = 1

# Triangle stage
# ──────────────────────────────────────────────────────────────────────────────

if state["stage"] == "triangle":
    st.header("🔺 Define the triangle")
    if players == 1 and not state["triangle"]:
        random_triangle()
        st.rerun()
    if st.button("🎲 Random triangle") and player_id == 1:
        random_triangle()
        st.rerun()

    if state["stage"] == "triangle":
        st.write(f"Player {state['turn']} – vertex {len(state['triangle'])+1}/3")
        if player_id != state["turn"]:
            st.info("Waiting for other player…")
            wait_for_state_change(
                lambda: state["stage"] != "triangle" or state["turn"] == player_id
            )
        else:
            city = st.text_input("City name")
            if st.button("Submit city") and city:
                geo, reason = bilingual_geocode(city, require_population=False)
                if geo:
                    lat, lon, fr, local, _ = geo
                    norm = normalize_city_name(fr)
                    tri_norms = {normalize_city_name(n) for n in state["triangle_names"]}
                    if norm in tri_norms:
                        st.warning("City already used in the triangle.")
                    else:
                        state["triangle"].append((lat, lon))
                        state["triangle_labels"].append(f"{fr} / {local}")
                        state["triangle_names"].append(fr)
                        state["turn"] = state["turn"] % players + 1
                        if len(state["triangle"]) == 3:
                            lat_c = sum(lat for lat,_ in state["triangle"]) / 3
                            lon_c = sum(lon for _,lon in state["triangle"]) / 3
                            fwd,_ = get_proj(lat_c, lon_c)
                            state["proj"] = fwd
                            state["poly_proj"] = build_poly_proj(state["triangle"], fwd)
                            state["stage"] = "scoring"
                        st.rerun()
                else:
                    show_geo_error(reason)
    list_names_func(state, players)

# ──────────────────────────────────────────────────────────────────────────────
# Scoring stage
# ──────────────────────────────────────────────────────────────────────────────

if state["stage"] == "scoring":
    st.header("🏃 Play phase (scores hidden)")
    list_names_func(state, players)

    total_turns = state["rounds"] * players
    if sum(state["submitted_counts"].values()) >= total_turns:
        state["stage"] = "name_entry"
        st.rerun()

    if player_id != state["turn"]:
        st.info("Waiting for other player…")
        wait_for_state_change(
            lambda: state["stage"] != "scoring" or state["turn"] == player_id
        )
        st.stop()

    st.subheader(f"Your turn - Player {player_id}")
    city = st.text_input("City inside triangle")
    if st.button("Submit city"):
        if not city:
            st.warning("Enter a city name")
        else:
            geo, reason = bilingual_geocode(city, require_population=True)
            if geo:
                lat, lon, fr, local, pop = geo
                tri_norms = {normalize_city_name(n) for n in state["triangle_names"]}
                if normalize_city_name(fr) in tri_norms:
                    st.warning("This city is part of the triangle.")
                else:
                    used_norms = set()
                    for entries in state["submissions"].values():
                        for entry in entries:
                            used_norms.add(
                                normalize_city_name(submission_name_fr(entry))
                            )
                    if normalize_city_name(fr) in used_norms:
                        st.warning("City already used in this game.")
                        st.stop()
                    inside = inside_geodesic_triangle(state["triangle"], (lat, lon))
                    label = f"{fr} / {local}"
                    entry = {
                        "lat": lat,
                        "lon": lon,
                        "label": label,
                        "name_fr": fr,
                        "population": pop,
                        "outside": not inside,
                    }
                    state["submissions"][player_id].append(entry)
                    state["submitted_counts"][player_id] += 1
                    if inside and pop:
                        state["scores"][player_id] += pop
                    # next player
                    state["turn"] = state["turn"] % players + 1
                    st.success("City submitted")
                    st.rerun()
            else:
                show_geo_error(reason)

# Results rendering
# ──────────────────────────────────────────────────────────────────────────────

def render_results(allow_leaderboard: bool, allow_reset: bool) -> None:
    st.success("All rounds finished - revealing results")

    tri = state["triangle"]
    view = st.radio("View", ["Map", "Globe"], horizontal=True, index=1)

    if view == "Map":
        lat_c = sum(lat for lat, _ in tri) / 3
        lon_c = sum(lon for _, lon in tri) / 3
        m = folium.Map(
            location=[lat_c, lon_c],
            zoom_start=5,
            tiles="CartoDB positron",
            control_scale=True,
        )

        # Vertices
        for (lat, lon), lab in zip(tri, state["triangle_labels"]):
            folium.Marker([lat, lon],
                          icon=folium.Icon(color="blue"),
                          tooltip=lab).add_to(m)

        # Edges
        edge_ring = []
        for i in range(3):
            edge = geodesic_edge(tri[i], tri[(i + 1) % 3])
            edge_ring.extend(edge[:-1])
            folium.PolyLine(edge, color="red", weight=2).add_to(m)
        if edge_ring:
            folium.Polygon(edge_ring + [edge_ring[0]],
                           color="#ff0000", weight=0, fill=True, fill_opacity=0.15).add_to(m)

        # Submissions
        for p in range(1, players + 1):
            for c in state["submissions"][p]:
                col = COLORS[p - 1]
                if c["outside"]:
                    col = "red"
                tip = (f"P{p}: {c['label']} ({c['population']})"
                       + (" OUT" if c["outside"] else ""))
                folium.Marker([c["lat"], c["lon"]],
                              icon=folium.Icon(color=col, icon="user"),
                              tooltip=tip).add_to(m)

        st_folium(m, width=850, height=550)
    else:
        fig = build_globe(tri, state["triangle_labels"], state["submissions"])
        st.plotly_chart(fig, use_container_width=True)

    tri_key = tuple((round(lat, 6), round(lon, 6)) for lat, lon in tri)
    excluded_norms = tuple(
        sorted({normalize_city_name(n) for n in state["triangle_names"]})
    )
    per_player_moves = int(state["rounds"])
    best_per_player = best_cities_for_triangle(tri_key, per_player_moves, excluded_norms)
    if best_per_player:
        st.subheader("Best possible answers")
        st.caption("Based on GeoNames cities data.")
        best_score = sum(pop for _, _, pop, _, _ in best_per_player)
        st.metric("Best possible score (per player)", f"{best_score:,}")
        best_table = [
            {"City": name, "Country": country, "Population": pop}
            for name, country, pop, _, _ in best_per_player
        ]
        st.dataframe(best_table, use_container_width=True, hide_index=True)
        if players > 1:
            total_moves = int(state["rounds"]) * players
            best_all = best_cities_for_triangle(tri_key, total_moves, excluded_norms)
            if best_all:
                total_score = sum(pop for _, _, pop, _, _ in best_all)
                st.metric("Best possible score (all players)", f"{total_score:,}")
                total_table = [
                    {"City": name, "Country": country, "Population": pop}
                    for name, country, pop, _, _ in best_all
                ]
                st.dataframe(total_table, use_container_width=True, hide_index=True)
    else:
        st.info("Best possible answers unavailable (city dataset missing).")

    st.subheader("Final Scores")
    cols = st.columns(players)
    for p, col in zip(range(1, players + 1), cols):
        col.metric(player_label_func(p, state), f"{state['scores'][p]:,}")
        if best_per_player:
            efficiency = state["scores"][p] / best_score if best_score else 0
            col.caption(f"Efficiency: {efficiency:.1%}")

    if best_per_player and allow_leaderboard and not state.get("leaderboard_saved"):
        entries = []
        for p in range(1, players + 1):
            score = int(state["scores"][p])
            efficiency = score / best_score if best_score else 0
            entries.append(
                {
                    "timestamp": int(time.time()),
                    "game_id": gid,
                    "player": player_label_func(p, state),
                    "score": score,
                    "best_score": int(best_score),
                    "efficiency": round(efficiency, 4),
                    "rounds": int(state["rounds"]),
                    "difficulty": state.get("triangle_difficulty", "Medium"),
                }
            )
        add_leaderboard_entries(entries)
        state["leaderboard_saved"] = True

    max_score = max(state["scores"].values())
    winners = [p for p, s in state["scores"].items() if s == max_score]
    if len(winners) == 1:
        st.header(f"🥇 Player {winners[0]} wins!")
    else:
        st.header("🤝 Tie between: " + ", ".join(map(str, winners)))

    if allow_reset and st.button("New game"):
        del STORE[gid]
        st.query_params.clear()
        clear_active_game()
        st.rerun()

# Name entry stage
# ──────────────────────────────────────────────────────────────────────────────

if state["stage"] == "name_entry":
    st.header("✨ Almost done")
    st.write("Enter your name to appear on the leaderboard.")
    current_name = state["player_names"].get(player_id, "")
    name_input = st.text_input("Your name", value=current_name, key="player_name")
    if st.button("Save name"):
        cleaned = name_input.strip()
        if cleaned:
            state["player_names"][player_id] = cleaned[:40]
            st.rerun()
        else:
            st.warning("Enter a name.")

    missing = [
        p for p in range(1, players + 1) if not state["player_names"].get(p)
    ]
    if not missing:
        state["stage"] = "finished"
        st.rerun()

    if state["player_names"].get(player_id):
        if missing:
            waiting = ", ".join(player_label_func(p, state) for p in missing)
            st.info(f"Waiting for: {waiting}")
            wait_for_state_change(
                lambda: state["stage"] != "name_entry"
                or all(state["player_names"].get(p) for p in range(1, players + 1))
            )
        render_results(allow_leaderboard=False, allow_reset=False)
    else:
        waiting = ", ".join(player_label_func(p, state) for p in missing)
        st.info(f"Waiting for: {waiting}")
        wait_for_state_change(
            lambda: state["stage"] != "name_entry"
            or all(state["player_names"].get(p) for p in range(1, players + 1))
        )
        st.stop()

# Finished stage – reveal everything
# ──────────────────────────────────────────────────────────────────────────────

if state["stage"] == "finished":
    render_results(allow_leaderboard=True, allow_reset=True)
