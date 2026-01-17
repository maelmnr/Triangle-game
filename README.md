# Triangle Game

Find cities within a geographic triangle - test your geography knowledge!

## Features

- 🎮 **Solo & Multiplayer**: Play alone or with up to 3 players
- 🌍 **Global Coverage**: Cities from around the world
- 🏆 **Leaderboard**: Track top scores and efficiency
- 🎯 **Difficulty Levels**: Easy, Medium, and Hard triangles
- 🗺️ **Interactive Maps**: View results on globe or map

## How to Play

1. A triangle is formed by three cities
2. Players take turns naming cities inside the triangle
3. Bigger cities = more points
4. Most points wins!

## Deployment Notes

This version uses in-memory storage for active games and local JSON file for leaderboard.

**Important**: On Streamlit Cloud:
- Active games will reset when the app restarts (after ~7 days of inactivity)
- Leaderboard will reset on redeployment
- For persistent storage, consider migrating to a database (Supabase, Firebase, etc.)

## Local Development

```bash
pip install -r requirements.txt
streamlit run triangle_game.py
```

## Technologies

- Streamlit
- Folium & Plotly (maps)
- Geopy (geocoding)
- Shapely (geometry)
- GeoNames (city data)
