import streamlit as st
import pandas as pd
import requests
import time
from io import StringIO
from datetime import datetime, timedelta
import google.generativeai as genai

# NBA API imports
from nba_api.stats.endpoints import (
    playergamelog, 
    leaguedashplayerstats, 
    commonallplayers, 
    leaguedashteamstats, 
    scoreboardv2
)
from nba_api.stats.static import players, teams as static_teams

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="CourtVision AI", page_icon="🧠", layout="wide")
st.title("🧠 CourtVision AI")

# --- HELPER: DATA SCRUBBER ---
def clean_id(obj):
    """Forces any ID to a clean string."""
    try:
        return str(int(float(obj)))
    except:
        return str(obj)

# --- CONFIGURE GEMINI ---
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
</style>
""", unsafe_allow_html=True)

# --- CACHED FUNCTIONS ---

@st.cache_data(ttl=3600)
def get_team_map_v4():
    try:
        # Fallback to static NBA API data first (Faster/Safer)
        nba_teams = static_teams.get_teams()
        return {t['full_name']: t['abbreviation'] for t in nba_teams}
    except:
        return {}

@st.cache_data(ttl=3600)
def get_live_injuries_v4():
    url = "https://www.cbssports.com/nba/injuries/"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(url, headers=headers)
        tables = pd.read_html(StringIO(response.text))
        injuries = {}
        for df in tables:
            if 'Player' in df.columns:
                for _, row in df.iterrows():
                    name = str(row['Player']).strip()
                    status = str(row['Injury Status'])
                    injuries[name] = status
        return injuries
    except:
        return {}

@st.cache_data(ttl=86400) 
def get_defensive_rankings_v4():
    """Fetches defensive ratings with explicit Advanced Stats request."""
    defense_map = {}
    
    # 1. LIVE FETCH ATTEMPT
    try:
        # Request 'Advanced' stats to get DEF_RATING
        teams_data = leaguedashteamstats.LeagueDashTeamStats(
            season='2025-26', 
            measure_type_detailed_defense='Advanced'
        ).get_data_frames()[0]
        
        # Sort by Rating (High = Bad Defense)
        teams_data = teams_data.sort_values(by='DEF_RATING', ascending=False)
        
        for _, row in teams_data.iterrows():
            clean_team_id = clean_id(row['TEAM_ID'])
            defense_map[clean_team_id] = {
                'Team': row['TEAM_NAME'],
                'Rating': row['DEF_RATING']
            }
            
    except Exception as e:
        print(f"Defense Fetch Error: {e}")
        # 2. STATIC FALLBACK 
        nba_teams = static_teams.get_teams()
        for t in nba_teams:
            tid = clean_id(t['id'])
            defense_map[tid] = {
                'Team': t['abbreviation'], 
                'Rating': 114.0 
            }
            
    return defense_map

@st.cache_data(ttl=3600)
def get_todays_games_v4():
    """Finds today's games using explicit Eastern Time."""
    try:
        # Check Today & Tomorrow
        dates = [
            (datetime.utcnow() - timedelta(hours=5)).strftime('%m/%d/%Y'),
            (datetime.utcnow() + timedelta(hours=19)).strftime('%m/%d/%Y')
        ]
        
        games = {}
        for d in dates:
            board = scoreboardv2.ScoreboardV2(game_date=d).get_data_frames()[0]
            if not board.empty:
                for _, row in board.iterrows():
                    h = clean_id(row['HOME_TEAM_ID'])
                    v = clean_id(row['VISITOR_TEAM_ID'])
                    games[h] = v
                    games[v] = h
        return games
    except:
        return {}

@st.cache_data(ttl=600) 
def get_league_trends_v4():
    expected_cols = ['Player', 'Matchup', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status']
    try:
        # Fetch Data
        season = leaguedashplayerstats.LeagueDashPlayerStats(season='2025-26', per_mode_detailed='PerGame').get_data_frames()[0]
        l5 = leaguedashplayerstats.LeagueDashPlayerStats(season='2025-26', per_mode_detailed='PerGame', last_n_games=5).get_data_frames()[0]
        l5 = l5[l5['GP'] >= 3] 

        # Merge
        merged = pd.merge(season[['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ID', 'PTS']], 
                          l5[['PLAYER_ID', 'PTS']], on='PLAYER_ID', suffixes=('_Season', '_L5'))

        merged['Trend (Delta)'] = merged['PTS_L5'] - merged['PTS_Season']

        # Intelligence
        games = get_todays_games_v4()         
        defense = get_defensive_rankings_v4() 

        def analyze_matchup(row):
            my_team = clean_id(row['TEAM_ID'])
            
            # 1. Check Schedule
            if my_team not in games: 
                return "No Game"
            
            # 2. Check Opponent
            opp_id = games[my_team]
            if opp_id in defense:
                opp_name = defense[opp_id]['Team']
                opp_rating = defense[opp_id]['Rating']
                
                # Logic
                if opp_rating > 116.0: return f"vs {opp_name} (🟢 Soft)"
                elif opp_rating < 112.0: return f"vs {opp_name} (🔴 Tough)"
                else: return f"vs {opp_name} (⚪ Avg)"
            
            return "vs ???"

        merged['Matchup'] = merged.apply(analyze_matchup, axis=1)
        final_df = merged.rename(columns={'PLAYER_NAME': 'Player', 'PTS_Season': 'Season PPG', 'PTS_L5': 'Last 5 PPG'})
        
        def get_status(row):
            d = row['Trend (Delta)']
            if d >= 4.0: return "🔥 Super Hot"
            elif d >= 2.0: return "🔥 Heating Up"
            elif d <= -3.0: return "❄️ Ice Cold"
            elif d <= -1.5: return "❄️ Cooling Down"
            else: return "Zap"

        final_df['Status'] = final_df.apply(get_status, axis=1)
        return final_df[expected_cols].sort_values(by='Trend (Delta)', ascending=False)

    except Exception:
        return pd.DataFrame(columns=expected_cols)

# --- HELPER: ROBUST AI GENERATOR ---
def generate_ai_response(prompt_text):
    """Auto-detects the best available model for your specific API Key."""
    try:
        # 1. List valid models for this account
        available = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available.append(m.name)
        
        # 2. Smart Selection (Prefer Flash, then Pro, then anything)
        selected_model = None
        
        # Priority 1: Flash (Fast/Cheap)
        for m in available:
            if 'flash' in m and '1.5' in m:
                selected_model = m
                break
        
        # Priority 2: Pro (Standard)
        if not selected_model:
            for m in available:
                if 'pro' in m and '1.5' in m:
                    selected_model = m
                    break
                    
        # Priority 3: Any Gemeni
        if not selected_model:
             for m in available:
                if 'gemini' in m:
                    selected_model = m
                    break
        
        if not selected_model:
            return "Error: No AI models found. Check API Key permissions."

        # 3. Generate
        model = genai.GenerativeModel(selected_model)
        return model.generate_content(prompt_text).text

    except Exception as e:
        return f"System Error: {e}"

# --- MAIN APP LAYOUT ---
tab1, tab2 = st.tabs(["📊 Dashboard", "🧠 CourtVision IQ"])

with tab1:
    st.markdown("### *Daily Intelligence Agent*")
    
    with st.sidebar:
        st.header("⚙️ System Status")
        if st.button("🔄 Force Reset Data"):
            st.cache_data.clear()
            st.rerun()
            
        # Load Data
        injuries = get_live_injuries_v4()
        trends = get_league_trends_v4()
        def_debug = get_defensive_rankings_v4()
        
        # METRICS
        c1, c2 = st.columns(2)
        c1.metric("Injuries", len(injuries))
        c2.metric("Trends", len(trends))
        c3, c4 = st.columns(2)
        c3.metric("Def Teams", len(def_debug))
        
        if len(def_debug) == 0:
            st.error("❌ Critical: Defense Data Missing")
        elif len(def_debug) == 30:
             st.success("✅ Defense Data Loaded")
        else:
             st.warning(f"⚠️ Partial Defense Data: {len(def_debug)}/30")

        st.write("---")
        st.header("🌞 Morning Briefing")
        
        with st.expander("⚠️ Impact Players OUT", expanded=False):
            found_impact = False
            if not trends.empty:
                impact_names = trends[trends['Season PPG'] > 12]['Player'].tolist()
                for star in impact_names:
                    for injured_name, status in injuries.items():
                        if star in injured_name: 
                            st.error(f"**{star}**: {status}")
                            found_impact = True
            if not found_impact: st.success("✅ No impact players out.")

    # --- MAIN TABLE ---
    st.subheader("🔥 Trends (Top Scorers)")
    if not trends.empty:
        st.dataframe(trends.head(15), hide_index=True)
    else:
        st.warning("⚠️ Market Data Unavailable.")

with tab2:
    st.header("CourtVision IQ Chat")
    if "messages" not in st.session_state: st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about matchups..."):
        with st.chat_message("user"): st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.spinner("Analyzing..."):
            context = f"TRENDS DATA:\n{trends.to_string()}\n\nINJURIES:\n{injuries}"
            final_prompt = f"ROLE: NBA Analyst. DATA: {context}. QUESTION: {prompt}"
            
            # Use the robust generator
            reply = generate_ai_response(final_prompt)
            
        with st.chat_message("assistant"): st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
