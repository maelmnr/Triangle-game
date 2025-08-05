# triangle_game.py
"""
Triangle Game – Geodesic, Blind‑Play Edition (autorefresh & cleaner names)
===========================================================================
**Mise à jour (05 août 2025)**

* ✔️ Les *noms* des villes (triangle & propositions) s’affichent désormais de
  façon concise (segment avant la virgule).
* 🔄 Rafraîchissement automatique toutes les **5 s** (via `streamlit‑autorefresh`).
* 📝 Nouvel attribut `triangle_names` dans l’état pour stocker les libellés.

Installation :
```bash
pip install streamlit folium streamlit-folium geopy shapely pyproj streamlit-autorefresh
```
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from typing import Dict, Any

import streamlit as st
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderUnavailable, GeocoderTimedOut
from shapely.geometry import Point, Polygon
import folium
from streamlit_folium import st_folium
from pyproj import CRS, Transformer, Geod

# ---------------------------------------------------------------------------
# Optional autorefresh (ignored if lib missing)
# ---------------------------------------------------------------------------
try:
    from streamlit_autorefresh import st_autorefresh

    st_autorefresh(interval=5000, key="game_refresh")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Globals & helpers
# ---------------------------------------------------------------------------

@st.cache_resource
def get_games_store() -> Dict[str, Dict[str, Any]]:
    return {}

GAMES_STORE = get_games_store()

POP_RE = re.compile(r"\d+")
GEOD = Geod(ellps="WGS84")


def parse_population(raw: str | None) -> int:
    if not raw:
        return 0
    digits = "".join(POP_RE.findall(raw))
    return int(digits) if digits else 0


def short_name(full: str) -> str:
    return full.split(",")[0].strip()


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def geocode_city(city: str):
    geolocator = Nominatim(user_agent="triangle_game_multi")
    try:
        loc = geolocator.geocode(city, addressdetails=True, extratags=True)
        if not loc:
            return None
        pop = parse_population(loc.raw.get("extratags", {}).get("population"))
        return loc.latitude, loc.longitude, loc.address, pop
    except (GeocoderUnavailable, GeocoderTimedOut):
        return None


@lru_cache(maxsize=8)
def get_projector(lat_c: float, lon_c: float):
    crs = CRS.from_proj4(
        f"+proj=laea +lat_0={lat_c} +lon_0={lon_c} +datum=WGS84 +units=m +no_defs"
    )
    fwd = Transformer.from_crs("epsg:4326", crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, "epsg:4326", always_xy=True).transform
    return fwd, inv


def densify_gc(p1, p2, n=48):
    lat1, lon1 = p1
    lat2, lon2 = p2
    pts = GEOD.npts(lon1, lat1, lon2, lat2, n - 1)
    return [(lat1, lon1), *[(lat, lon) for lon, lat in pts], (lat2, lon2)]

# ---------------------------------------------------------------------------
# Game state helpers
# ---------------------------------------------------------------------------

def new_game_state() -> Dict[str, Any]:
    return {
        "triangle": [],
        "triangle_names": [],
        "proj": None,
        "poly_proj": None,
        "submissions": {1: [], 2: []},
        "scores": {1: 0, 2: 0},
        "turn": 1,
        "rounds": 3,
        "submitted_counts": {1: 0, 2: 0},
        "stage": "setup",
    }

# ---------------------------------------------------------------------------
# URL params
# ---------------------------------------------------------------------------

qp = st.query_params
current_game_id = qp.get("game")
current_player = int(qp.get("player") or 0)

if not current_game_id or current_player not in (1, 2):
    st.header("🆕 New or Join a Game")
    with st.form("join"):
        mode = st.radio("Choose", ["Create new game", "Join existing game"])
        existing = st.text_input("Game ID (if joining)")
        rounds = (
            st.number_input("Rounds per player", 1, 10, 3)
            if mode == "Create new game"
            else None
        )
        go = st.form_submit_button("Continue")
    if not go:
        st.stop()
    if mode == "Create new game":
        current_game_id = uuid.uuid4().hex[:8]
        current_player = 1
        GAMES_STORE[current_game_id] = new_game_state()
        GAMES_STORE[current_game_id]["rounds"] = int(rounds)
        GAMES_STORE[current_game_id]["stage"] = "triangle"
    else:
        if existing.strip() not in GAMES_STORE:
            st.error("Game ID not found – ask creator.")
            st.stop()
        current_game_id = existing.strip()
        current_player = 2
    qp.update({"game": current_game_id, "player": str(current_player)})
    st.rerun()

game = GAMES_STORE.setdefault(current_game_id, new_game_state())

st.sidebar.write(f"**Game ID:** `{current_game_id}`")
st.sidebar.write(f"**You are Player {current_player}**")
st.sidebar.write("Share this URL with your opponent.")

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def list_submitted_names():
    st.write("#### 📝 Submitted so far")
    if game["triangle_names"]:
        st.markdown("**Triangle:** " + ", ".join(game["triangle_names"]))
    for p in (1, 2):
        names = [c["short"] for c in game["submissions"][p]]
        if names:
            st.markdown(f"Player {p}: " + ", ".join(names))

# ---------------------------------------------------------------------------
# Triangle stage
# ---------------------------------------------------------------------------

if game["stage"] == "triangle":
    st.header("🔺 Define the triangle (shared)")
    st.write(f"Player {game['turn']} – choose vertex {len(game['triangle'])+1}/3")

    if current_player != game["turn"]:
        st.info("Waiting for other player…")
    else:
        city = st.text_input("City name")
        if st.button("Submit city") and city:
            geo = geocode_city(city)
            if geo:
                lat, lon, addr, _ = geo
                game["triangle"].append((lat, lon))
                game["triangle_names"].append(short_name(addr))
                game["turn"] = 2 if game["turn"] == 1 else 1
                if len(game["triangle"]) == 3:
                    lat_c = sum(lat for lat, _ in game["triangle"]) / 3
                    lon_c = sum(lon for _, lon in game["triangle"]) / 3
                    fwd, _ = get_projector(lat_c, lon_c)
                    game["proj"] = fwd
                    tri_proj = [Point(fwd(lon, lat)) for lat, lon in game["triangle"]]
                    game["poly_proj"] = Polygon([(p.x, p.y) for p in tri_proj])
                    game["stage"] = "scoring"
                st.rerun()
            else:
                st.error("City not found.")

    list_submitted_names()

# ---------------------------------------------------------------------------
# Scoring stage
# ---------------------------------------------------------------------------

if game["stage"] == "scoring":
    st.header("🏃 Play phase (scores hidden)")
    list_submitted_names()

    total_turns = game["rounds"] * 2
    if sum(game["submitted_counts"].values()) >= total_turns:
        game["stage"] = "finished"
        st.rerun()

    if current_player != game["turn"]:
        st.info("Waiting for other player…")
        st.stop()

    st.subheader(f"Your turn – Player {current_player}")
    city = st.text_input("City inside triangle")
    if st.button("Submit city"):
        if not city:
            st.warning("Enter a city name.")
        else:
            geo = geocode_city(city)
            if geo:
                lat, lon, addr, pop = geo
                x, y = game["proj"](lon, lat)
                inside = Point(x, y).within(game["poly_proj"])
                entry = {
                    "lat": lat,
                    "lon": lon,
                    "address": addr,
                    "short": short_name(addr),
                    "population": pop,
                    "outside": not inside,
                }
                game["submissions"][current_player].append(entry)
                game["submitted_counts"][current_player] += 1
                if inside and pop:
                    game["scores"][current_player] += pop
                game["turn"] = 2 if game["turn"] == 1 else 1
                st.success("City submitted ✔️")
                st.rerun()
            else:
                st.error("City not found.")

# ---------------------------------------------------------------------------
# Finished – reveal map & scores
# ---------------------------------------------------------------------------

if game["stage"] == "finished":
    st.success("🏁 All rounds finished – revealing results")

    def build_map():
        tri = game["triangle"]
        lat_c = sum(lat for lat, _ in tri) / 3
        lon_c = sum(lon for _, lon in tri) / 3
        m = folium.Map(location=[lat_c, lon_c], zoom_start=5)
        # vertices
        for (lat, lon), name in zip(tri, game["triangle_names"]):
            folium.Marker([lat, lon], icon=folium.Icon(color="blue"), tooltip=name).add_to(m)
        # edges
        for i in range(3):
            folium.PolyLine(densify_gc(tri[i], tri[(i + 1) % 3]), color="red", weight=2).add_to(m)
        folium.Polygon([*tri, tri[0]], color="#ff0000", weight=0, fill=True, fill_opacity=0.15).add_to(m)
        # submissions
        cols = {1: "green", 2: "purple"}
        for p in (1, 2):
            for c in game["submissions"][p]:
                base_col = cols[p]  # unique color per player
                icon_name = "remove" if c["outside"] else "user"  # different glyph if outside
                folium.Marker(
                    [c["lat"], c["lon"]],
                    icon=folium.Icon(color=base_col, icon=icon_name),
                    tooltip=f"P{p}: {c['short']} ({c['population']})" + (" – OUT" if c["outside"] else ""),
                ).add_to(m)
        return m

    st_folium(build_map(), width=850, height=550)

    st.subheader("Final Scores")
    cols = st.columns(2)
    for p, col in zip((1, 2), cols):
        col.metric(f"Player {p}", f"{game['scores'][p]:,}")

    max_score = max(game["scores"].values())
    winners = [p for p, s in game["scores"].items() if s == max_score]
    if len(winners) == 1:
        st.header(f"🥇 Player {winners[0    ]} wins!")
    else:
        st.header("🤝 It's a tie!")

    if st.button("New game"):
        del GAMES_STORE[current_game_id]
        st.query_params.clear()
        st.rerun()
