# triangle_game.py
"""
Triangle Game – Streamlit project where two players create a geographic triangle
with the first three city names, then compete by adding cities inside the
triangle to earn points equal to each accepted city’s population.

Fixes in this version (2025‑08‑05)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Live scoreboard** now visible and updates after every turn.
* **Population parsing** tolerates numbers with spaces/commas (e.g. "1 256 000").
  – fewer legitimate inside‑triangle cities will score 0.
* Minor UI tweaks for clarity.

Run with::
    pip install streamlit geopy folium streamlit-folium shapely
    streamlit run triangle_game.py
"""

from __future__ import annotations

import re
import streamlit as st
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderUnavailable, GeocoderTimedOut
from shapely.geometry import Point, Polygon
import folium
from streamlit_folium import st_folium

################################################################################
# Page config & title
################################################################################

st.set_page_config(page_title="Triangle Game", layout="wide")
st.title("🏆 Triangle Game")

################################################################################
# Utility functions
################################################################################

POP_RE = re.compile(r"\d+")


def parse_population(raw: str | None) -> int:
    """Convert population string like "1,256,000" → 1256000. Return 0 on fail."""
    if not raw:
        return 0
    digits = "".join(POP_RE.findall(raw))
    return int(digits) if digits else 0


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def geocode_city(city_name: str):
    """Return (lat, lon, full_address, population) or None."""
    geolocator = Nominatim(user_agent="triangle_game")
    try:
        location = geolocator.geocode(city_name, addressdetails=True, extratags=True)
        if not location:
            return None
        population = parse_population(location.raw.get("extratags", {}).get("population"))
        return location.latitude, location.longitude, location.address, population
    except (GeocoderUnavailable, GeocoderTimedOut):
        return None


def build_map(tri: list[tuple[float, float]], subs: dict[str, list[dict]]):
    lat_c = sum(lat for lat, _ in tri) / 3
    lon_c = sum(lon for _, lon in tri) / 3
    m = folium.Map(location=[lat_c, lon_c], zoom_start=5)

    # Triangle
    for lat, lon in tri:
        folium.Marker([lat, lon], icon=folium.Icon(color="blue"), tooltip="Vertex").add_to(m)
    folium.Polygon(tri, color="red", weight=2, fill=True, fill_opacity=0.15).add_to(m)

    # Cities
    base_cols = ["green", "purple", "orange", "cadetblue"]
    for idx, (player, cities) in enumerate(subs.items()):
        p_col = base_cols[idx % len(base_cols)]
        for c in cities:
            col = "red" if c.get("outside") else p_col
            folium.Marker(
                [c["lat"], c["lon"]],
                icon=folium.Icon(color=col, icon="user"),
                tooltip=f"{player}: {c['address']} (pop {c['population']})" + (" – OUT" if c.get("outside") else ""),
            ).add_to(m)
    return m

################################################################################
# Session state defaults helper
################################################################################

def ss_set_default(key, val):
    if key not in st.session_state:
        st.session_state[key] = val

ss_set_default("stage", "intro")
ss_set_default("player_names", ["Player 1", "Player 2"])
ss_set_default("current_player", 0)
ss_set_default("triangle_coords", [])
ss_set_default("polygon", None)
ss_set_default("submissions", {n: [] for n in st.session_state["player_names"]})
ss_set_default("scores", {n: 0 for n in st.session_state["player_names"]})
ss_set_default("rounds", 3)
ss_set_default("submitted_counts", {n: 0 for n in st.session_state["player_names"]})

################################################################################
# Intro stage
################################################################################

if st.session_state.stage == "intro":
    with st.form("setup"):
        st.header("🎲 Game Setup")
        p1 = st.text_input("Player 1", value=st.session_state.player_names[0])
        p2 = st.text_input("Player 2", value=st.session_state.player_names[1])
        rounds = st.number_input("Rounds per player", 1, 10, st.session_state.rounds)
        if st.form_submit_button("Start", type="primary"):
            st.session_state.player_names = [p1.strip() or "Player 1", p2.strip() or "Player 2"]
            st.session_state.rounds = int(rounds)
            st.session_state.submissions = {n: [] for n in st.session_state.player_names}
            st.session_state.scores = {n: 0 for n in st.session_state.player_names}
            st.session_state.submitted_counts = {n: 0 for n in st.session_state.player_names}
            st.session_state.current_player = 0
            st.session_state.stage = "triangle"
            st.rerun()

################################################################################
# Triangle stage (map hidden)
################################################################################

if st.session_state.stage == "triangle":
    ply = st.session_state.player_names[st.session_state.current_player]
    st.subheader(f"{ply} – Triangle vertex {len(st.session_state.triangle_coords)+1}/3")
    city = st.text_input("City", key=f"tri_{len(st.session_state.triangle_coords)}")
    if st.button("Submit Vertex"):
        if city:
            geo = geocode_city(city)
            if geo:
                lat, lon, addr, _ = geo
                st.session_state.triangle_coords.append((lat, lon))
                st.session_state.current_player ^= 1
                if len(st.session_state.triangle_coords) == 3:
                    st.session_state.polygon = Polygon([(lon, lat) for lat, lon in st.session_state.triangle_coords])
                    st.session_state.stage = "scoring"
                st.rerun()
            else:
                st.error("City not found")
        else:
            st.warning("Enter a city name.")

################################################################################
# Scoring stage (map hidden but live scoreboard visible)
################################################################################

if st.session_state.stage == "scoring":
    st.header("📊 Live Scoreboard")
    cols = st.columns(2)
    for c, n in zip(cols, st.session_state.player_names):
        c.metric(n, f"{st.session_state.scores[n]:,}")

    total_turns = st.session_state.rounds * 2
    if sum(st.session_state.submitted_counts.values()) >= total_turns:
        st.session_state.stage = "finished"
        st.rerun()

    ply = st.session_state.player_names[st.session_state.current_player]
    st.subheader(f"{ply} – Round {st.session_state.submitted_counts[ply]+1}/{st.session_state.rounds}")
    city = st.text_input("City inside triangle", key=f"score_{sum(st.session_state.submitted_counts.values())}")
    if st.button("Submit City"):
        if not city:
            st.warning("Enter a city.")
        else:
            geo = geocode_city(city)
            if not geo:
                st.error("City not found.")
            else:
                lat, lon, addr, pop = geo
                inside = Point(lon, lat).within(st.session_state.polygon)
                entry = {"lat": lat, "lon": lon, "address": addr, "population": pop, "outside": not inside}
                st.session_state.submissions[ply].append(entry)

                if inside and pop > 0:
                    st.success(f"✅ {addr} INSIDE – +{pop:,} pts")
                    st.session_state.scores[ply] += pop
                elif inside:
                    st.info(f"ℹ️ {addr} inside but population unknown – 0 pts")
                else:
                    st.warning(f"❌ {addr} OUTSIDE – 0 pts")

                st.session_state.submitted_counts[ply] += 1
                st.session_state.current_player ^= 1
                st.rerun()

################################################################################
# Finished stage – reveal map & winner
################################################################################

if st.session_state.stage == "finished":
    st.success("### 🎉 Game Over – Reveal Map & Scores")
    m = build_map(st.session_state.triangle_coords, st.session_state.submissions)
    st_folium(m, width=900, height=600)

    st.subheader("🏅 Final Scores")
    cols = st.columns(2)
    for c, n in zip(cols, st.session_state.player_names):
        c.metric(n, f"{st.session_state.scores[n]:,}")

    max_score = max(st.session_state.scores.values())
    winners = [n for n, s in st.session_state.scores.items() if s == max_score]
    if len(winners) == 1:
        st.balloons()
        st.header(f"🥇 {winners[0]} wins!")
    else:
        st.header("🤝 It's a tie between: " + ", ".join(winners))

    if st.button("Play Again"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
