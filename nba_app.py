import streamlit as st
import pandas as pd
import requests
import time
from io import StringIO
from datetime import datetime
import google.generativeai as genai

# NBA API imports
from nba_api.stats.endpoints import (
    playergamelog, 
    leaguedashplayerstats, 
    commonallplayers, 
    leaguedashteamstats, 
    scoreboardv2
)
from nba_api.stats.static import players

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="CourtVision AI", page_icon="🧠", layout="wide")
st.title("🧠 CourtVision AI")

# --- CONFIGURE GEMINI AI ---
try:
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    pass

# --- CSS HACKS ---
st.markdown("""
<style>
    .stAppDeployButton, [data-testid="stDecoration"] { display: none !important; }
    footer { visibility: hidden; }
    [data-testid="stToolbar"] { visibility: hidden; height: 0%; }
    .metric-card {background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;}
    .big-font {font-size:20px !important;}
</style>
""", unsafe_allow_html=True)

# --- CACHED FUNCTIONS ---

@st.cache_data(ttl=86400) # Cache for 24 hours
def get_team_map():
    try:
        # 1. Use CommonAllPlayers to get EVERYONE (active, inactive, G-League, etc.)
        roster = commonallplayers.CommonAllPlayers(is_only_current_season=1).get_data_frames()[0]
        return pd.Series(roster.TEAM_ABBREVIATION.values, index=roster.DISPLAY_FIRST_LAST).to_dict()
    except Exception as e:
        return {}

@st.cache_data(ttl=3600)
def get_live_injuries():
    url = "https://www.cbssports.com/nba/injuries/"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # Get the PROPER roster map
    team_map = get_team_map()
    
    try:
        response = requests.get(url, headers=headers)
        tables = pd.read_html(StringIO(response.text))
        injuries = {}
        
        for df in tables:
            if 'Player' in df.columns:
                for _, row in df.iterrows():
                    dirty_name = str(row['Player']).strip()
                    status = str(row['Injury Status'])
                    
                    clean_name = dirty_name
                    team_code = "Unknown"
                    
                    for official_name, team in team_map.items():
                        if official_name in dirty_name:
                            clean_name = official_name
                            team_code = team
                            break 
                    
                    injuries[clean_name] = f"{status} ({team_code})"
                    
        return injuries
    except:
        return {}

@st.cache_data(ttl=86400) # Cache daily
def get_defensive_rankings():
    """Fetches current defensive ratings for all 30 teams."""
    try:
        teams = leaguedashteamstats.LeagueDashTeamStats(season='2025-26').get_data_frames()[0]
        teams = teams.sort_values(by='DEF_RATING', ascending=False) # Worst defenses at the top
        
        defense_map = {}
        for _, row in teams.iterrows():
            # 🛑 FIX: Force Team ID to String
            defense_map[str(row['TEAM_ID'])] = {
                'Team': row['TEAM_NAME'],
                'Rating': row['DEF_RATING']
            }
        return defense_map
    except Exception as e:
        return {}

@st.cache_data(ttl=3600)
def get_todays_games():
    """Finds out who is playing TODAY."""
    try:
        # 🛑 FIX: Ensure we catch games even if server time is slightly off
        today = datetime.now().strftime('%m/%d/%Y')
        board = scoreboardv2.ScoreboardV2(game_date=today).get_data_frames()[0]
        
        games = {}
        if board.empty:
            return {}
            
        for _, row in board.iterrows():
            # 🛑 FIX: Force Team IDs to String
            home_id = str(row['HOME_TEAM_ID'])
            visitor_id = str(row['VISITOR_TEAM_ID'])
            
            games[home_id] = visitor_id
            games[visitor_id] = home_id
            
        return games
    except Exception:
        return {}

@st.cache_data(ttl=600) # Update every 10 mins
def get_league_trends():
    # Define the columns we EXPECT to have. This is our safety net.
    expected_cols = ['Player', 'Matchup', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status']
    
    try:
        # --- 1. GET THE DATA ---
        season_stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season='2025-26', per_mode_detailed='PerGame'
        ).get_data_frames()[0]

        last5_stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season='2025-26', per_mode_detailed='PerGame', last_n_games=5
        ).get_data_frames()[0]

        # FILTER: Consistency Check (Must play 3+ games to be "Trending")
        last5_stats = last5_stats[last5_stats['GP'] >= 3]

        # --- 2. MERGE ---
        merged = pd.merge(
            season_stats[[

