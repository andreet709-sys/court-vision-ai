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
from nba_api.stats.static import players

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="CourtVision AI", page_icon="🧠", layout="wide")
st.title("🧠 CourtVision AI")

# --- HELPER: DATA SCRUBBER ---
def clean_id(obj):
    """Forces any ID (float, int, str) into a clean string without decimals."""
    try:
        return str(int(float(obj)))
    except:
        return str(obj)

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

# --- CACHED FUNCTIONS (Renamed to V2 to Bust Cache) ---

@st.cache_data(ttl=86400) 
def get_team_map_v2():
    try:
        roster = commonallplayers.CommonAllPlayers(is_only_current_season=1).get_data_frames()[0]
        return pd.Series(roster.TEAM_ABBREVIATION.values, index=roster.DISPLAY_FIRST_LAST).to_dict()
    except Exception:
        return {}

@st.cache_data(ttl=3600)
def get_live_injuries_v2():
    url = "https://www.cbssports.com/nba/injuries/"
    headers = {"User-Agent": "Mozilla/5.0"}
    team_map = get_team_map_v2()
    
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

@st.cache_data(ttl=86400) 
def get_defensive_rankings_v2():
    """Fetches defensive ratings and SCRUBS the Team IDs."""
    try:
        teams = leaguedashteamstats.LeagueDashTeamStats(season='2025-26').get_data_frames()[0]
        teams = teams.sort_values(by='DEF_RATING', ascending=False)
        
        defense_map = {}
        for _, row in teams.iterrows():
            # 🧼 SCRUB THE ID
            clean_team_id = clean_id(row['TEAM_ID'])
            defense_map[clean_team_id] = {
                'Team': row['TEAM_NAME'],
                'Rating': row['DEF_RATING']
            }
        return defense_map
    except Exception:
        return {}

@st.cache_data(ttl=3600)
def get_todays_games_v2():
    """Finds today's games using explicit Eastern Time."""
    try:
        # 🕒 FORCE EASTERN TIME (Server Time - 5 Hours) to ensure we get the right date
        # This fixes the "No Game" bug for Nurkić
        game_date = (datetime.utcnow() - timedelta(hours=5)).strftime('%m/%d/%Y')
        
        board = scoreboardv2.ScoreboardV2(game_date=game_date).get_data_frames()[0]
        games = {}
        
        if board.empty:
            return {}
            
        for _, row in board.iterrows():
            # 🧼 SCRUB THE IDs
            home_id = clean_id(row['HOME_TEAM_ID'])
            visitor_id = clean_id(row['VISITOR_TEAM_ID'])
            
            games[home_id] = visitor_id
            games[visitor_id] = home_id
            
        return games
    except Exception:
        return {}

@st.cache_data(ttl=600) 
def get_league_trends_v2():
    expected_cols = ['Player', 'Matchup', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status']
    
    try:
        # --- 1. GET DATA ---
        season_stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season='2025-26', per_mode_detailed='PerGame'
        ).get_data_frames()[0]

        last5_stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season='2025-26', per_mode_detailed='PerGame', last_n_games=5
        ).get_data_frames()[0]

        last5_stats = last5_stats[last5_stats['GP'] >= 3]

        # --- 2. MERGE ---
        merged = pd.merge(
            season_stats[['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ID', 'PTS', 'REB', 'AST']], 
            last5_stats[['PLAYER_ID', 'PTS', 'REB', 'AST']], 
            on='PLAYER_ID', 
            suffixes=('_Season', '_L5')
        )

        merged['Trend (Delta)'] = merged['PTS_L5'] - merged['PTS_Season']

        # --- 3. INTELLIGENCE LAYER ---
        games = get_todays_games_v2()         
        defense = get_defensive_rankings_v2() 

        def analyze_matchup(row):
            # 🧼 SCRUB THE ID to match our maps
            my_team = clean_id(row['TEAM_ID'])
            
            if my_team not in games:
                return "No Game"
            
            opponent_id = games[my_team]
            
            if opponent_id in defense:
                opp_name = defense[opponent_id]['Team']
                opp_rating = defense[opponent_id]['Rating']
                
                if opp_rating > 116.0: return f"vs {opp_name} (🟢 Soft)"
                elif opp_rating < 112.0: return f"vs {opp_name} (🔴 Tough)"
                else: return f"vs {opp_name} (⚪ Avg)"
            return "vs Unknown"

        merged['Matchup'] = merged.apply(analyze_matchup, axis=1)

        # --- 4. CLEANUP ---
        final_df = merged.rename(columns={
            'PLAYER_NAME': 'Player',
            'PTS_Season': 'Season PPG',
            'PTS_L5': 'Last 5 PPG'
        })

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

# --- CREATE TABS ---
tab1, tab2 = st.tabs(["📊 Dashboard", "🧠 CourtVision IQ"])

# ==========================================
# TAB 1: THE DASHBOARD
# ==========================================
with tab1:
    st.markdown("### *Daily Intelligence Agent*")

    with st.sidebar:
        st.header("🌞 Morning Briefing")
        st.info("Live Injury Report Loaded from CBS Sports")
        
        # Calling V2 functions forces a fresh data pull
        injuries = get_live_injuries_v2()
        trends = get_league_trends_v2() 
        
        with st.expander("⚠️ Impact Players OUT", expanded=False):
            found_impact_injury = False
            if not trends.empty and 'Player' in trends.columns:
                impact_df = trends[trends['Season PPG'] > 12]
                impact_names = impact_df['Player'].tolist()
                for star in impact_names:
                    for injured_player, status in injuries.items():
                        if star in injured_player: 
                            st.error(f"**{star}**: {status}")
                            found_impact_injury = True
            if not found_impact_injury:
                st.success("✅ No major impact players (>12 PPG) out.")
        
        st.write("---")
        st.caption(f"Tracking {len(injuries)} injuries.")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("🔎 Player Deep Dive")
        player_name = st.text_input("Enter Player Name:", placeholder="e.g. 'Jayson Tatum'")

        if player_name:
            matching_players = [p for p in players.get_active_players() 
                               if player_name.lower() in p['full_name'].lower()]
            
            if not matching_players:
                st.error("Player not found.")
            else:
                p = matching_players[0]
                st.success(f"Found: {p['full_name']}")
                with st.spinner('Crunching numbers...'):
                    try:
                        career = leaguedashplayerstats.LeagueDashPlayerStats(season='2025-26', per_mode_detailed='PerGame').get_data_frames()[0]
                        player_season = career[career['PLAYER_ID'] == p['id']]
                        logs = playergamelog.PlayerGameLog(player_id=p['id'], season='2025-26').get_data_frames()[0]
                        
                        if not player_season.empty and not logs.empty:
                            stats = player_season.iloc[0]
                            l5 = logs.head(5)
                            season_pra = stats['PTS'] + stats['REB'] + stats['AST']
                            l5_pra = l5['PTS'].mean() + l5['REB'].mean() + l5['AST'].mean()
                            delta = l5_pra - season_pra
                            
                            m1, m2, m3 = st.columns(3)
                            m1.metric("Season PPG", f"{stats['PTS']:.1f}")
                            m2.metric("Last 5 PRA", f"{l5_pra:.1f}", delta=f"{delta:.1f}")
                            m3.metric("Trend", "🔥 HOT" if delta > 0 else "❄️ COLD")
                            
                            st.subheader("Recent Performance")
                            logs['PRA'] = logs['PTS'] + logs['REB'] + logs['AST']
                            chart_data = logs.head(10).iloc[::-1].set_index('GAME_DATE')[['PRA']]
                            st.line_chart(chart_data)
                        else:
                            st.warning("No data found for this season.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    with col2:
        st.subheader("🔥 Trends (Top Scorers)")
        df_trends = get_league_trends_v2()
        
        if not df_trends.empty and 'Trend (Delta)' in df_trends.columns:
            display_cols = ['Player', 'Matchup', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status']
            valid_cols = [c for c in display_cols if c in df_trends.columns]
            st.dataframe(df_trends[valid_cols].head(10), hide_index=True)
        else:
            st.warning("⚠️ Market Data Unavailable.")

# ==========================================
# TAB 2: CourtVision IQ
# ==========================================
with tab2:
    st.header("CourtVision IQ Chat")

    if "GOOGLE_API_KEY" not in st.secrets:
        st.error("⚠️ Missing API Key in Secrets.")
    else:
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("Ex: Who is heating up?"):
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})

            injuries_data = get_live_injuries_v2()
            trends_data = get_league_trends_v2()
            
            context_text = f"""
            LIVE DATA:
            1. INJURY REPORT: {injuries_data}
            2. TRENDS: {trends_data.to_string() if not trends_data.empty else "Unavailable"}
            """

            try:
                # 🤖 ROBUST AI MODEL SELECTION
                # Tries 'gemini-pro' first (Most reliable Free Tier)
                try:
                    model = genai.GenerativeModel('gemini-pro')
                except:
                    model = genai.GenerativeModel('gemini-1.5-flash')
                
                full_prompt = f"""
                ROLE: Expert NBA Analyst (2025-26 Season).
                DATA: Use this source only: {context_text}
                QUESTION: {prompt}
                """
                
                with st.spinner("Thinking..."):
                    response = model.generate_content(full_prompt)
                    ai_reply = response.text
                
                with st.chat_message("assistant"):
                    st.markdown(ai_reply)
                st.session_state.messages.append({"role": "assistant", "content": ai_reply})
                
            except Exception as e:
                st.error(f"AI Error: {e}. Try checking your API Key quotas.")
