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
            season_stats[['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ID', 'PTS', 'REB', 'AST']], 
            last5_stats[['PLAYER_ID', 'PTS', 'REB', 'AST']], 
            on='PLAYER_ID', 
            suffixes=('_Season', '_L5')
        )

        merged['Trend (Delta)'] = merged['PTS_L5'] - merged['PTS_Season']

        # --- 3. INTELLIGENCE LAYER ---
        games = get_todays_games()         
        defense = get_defensive_rankings() 

        def analyze_matchup(row):
            # 🛑 FIX: Force Player's Team ID to String to match the keys
            my_team = str(row['TEAM_ID'])
            
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

        # Add Status
        def get_status(row):
            d = row['Trend (Delta)']
            if d >= 4.0: return "🔥 Super Hot"
            elif d >= 2.0: return "🔥 Heating Up"
            elif d <= -3.0: return "❄️ Ice Cold"
            elif d <= -1.5: return "❄️ Cooling Down"
            else: return "Zap"

        final_df['Status'] = final_df.apply(get_status, axis=1)

        # Return organized data
        return final_df[expected_cols].sort_values(by='Trend (Delta)', ascending=False)

    except Exception as e:
        # 🛡️ SAFETY NET: If anything fails, return an empty table WITH HEADERS
        return pd.DataFrame(columns=expected_cols)

# --- CREATE TABS ---
tab1, tab2 = st.tabs(["📊 Dashboard", "🧠 CourtVision IQ"])

# ==========================================
# TAB 1: THE DASHBOARD
# ==========================================
with tab1:
    st.markdown("### *Daily Intelligence Agent*")

    # 1. SIDEBAR (Dynamic Injury Scanner)
    with st.sidebar:
        st.header("🌞 Morning Briefing")
        st.info("Live Injury Report Loaded from CBS Sports")
        
        # Load Data
        injuries = get_live_injuries()
        trends = get_league_trends() 
        
        # --- NEW COLLAPSIBLE SECTION ---
        with st.expander("⚠️ Impact Players OUT (Click to View)", expanded=False):
            found_impact_injury = False
            
            if not trends.empty and 'Player' in trends.columns:
                # FILTER: Only show "Impact" players (e.g., > 12 PPG)
                impact_df = trends[trends['Season PPG'] > 12]
                impact_names = impact_df['Player'].tolist()
                
                for star in impact_names:
                    for injured_player, status in injuries.items():
                        if star in injured_player: 
                            st.error(f"**{star}**: {status}")
                            found_impact_injury = True
            
            if not found_impact_injury:
                st.success("✅ No major impact players (>12 PPG) listed as out.")
        
        st.write("---")
        
        # Full Database Access
        st.caption(f"Tracking {len(injuries)} total league injuries.")
        with st.expander("🚑 View Full Roster Report"):
             if injuries:
                 df_inj = pd.DataFrame(list(injuries.items()), columns=["Player", "Status"])
                 st.dataframe(df_inj, hide_index=True, height=400)
             else:
                 st.write("No data available.")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("🔎 Player Deep Dive")
        player_name = st.text_input("Enter Player Name (e.g. 'Jayson Tatum'):", placeholder="Type name here...")

        if player_name:
            matching_players = [p for p in players.get_active_players() 
                               if player_name.lower() in p['full_name'].lower()]
            
            if not matching_players:
                st.error("Player not found. Check spelling.")
            else:
                p = matching_players[0]
                st.success(f"Found: {p['full_name']}")
                with st.spinner('Crunching numbers...'):
                    try:
                        # UPDATED TO 2025-26
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
                            st.warning("No data found for 2025-26 season yet.")
                    except Exception as e:
                        st.error(f"Error fetching data: {e}")

    with col2:
        st.subheader("🔥 Trends (Top Scorers)")
        df_trends = get_league_trends()
        
        # 🛡️ SAFETY CHECK: Only try to show data if the columns actually exist
        if not df_trends.empty and 'Trend (Delta)' in df_trends.columns:
            # Reorder columns to put Matchup first (if it exists)
            display_cols = ['Player', 'Matchup', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status']
            # Filter to only columns that are actually present
            valid_cols = [c for c in display_cols if c in df_trends.columns]
            
            st.dataframe(df_trends[valid_cols].head(10), hide_index=True)
        else:
            st.warning("⚠️ Market Data Unavailable. The NBA API connection may be down temporarily.")

# ==========================================
# TAB 2: CourtVision IQ (GEMINI CHATBOT)
# ==========================================
with tab2:
    st.header("CourtVision IQ Chat")
    st.info("💡 I have read today's injury reports and calculated the Last-5-Game trends for top scorers.")

    if "GOOGLE_API_KEY" not in st.secrets:
        st.error("⚠️ **Missing AI Key:** Please add `GOOGLE_API_KEY` to your Streamlit Secrets.")
    else:
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("Ex: Who is heating up right now?"):
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})

            # Prepare Data Context
            injuries_data = get_live_injuries()
            trends_data = get_league_trends()
            
            context_text = f"""
            LIVE DATA SOURCE (Primary Truth):
            
            1. INJURY REPORT (CBS Sports):
            {injuries_data}
            
            2. TOP SCORER TRENDS (Calculated Last 5 Games vs Season):
            {trends_data.to_string() if not trends_data.empty else "Data Unavailable"}
            """

            try:
                # --- MODEL CONFIGURATION ---
                # Changed to 'gemini-pro' to prevent 404/Quota errors on free tier
                model = genai.GenerativeModel('gemini-pro')
                
                full_prompt = f"""
                SYSTEM ROLE:
                You are "Daily NBA Analyst," an expert AI basketball analyst for the 2025-26 season. 
                
                CORE RULES:
                1. **Data Authority:** Use the LIVE DATA SOURCE below as your absolute truth. 
                2. **Reasoning:**
                   - Use the "Trend (Delta)" column in the data to identify who is Hot/Cold.
                   - If a player is NOT in the "Top Scorer Trends" list, admit you don't have their live trend data yet.
                3. **Style:** Conversational, sharp, show your math.

                LIVE DATA SOURCE:
                {context_text}
                
                USER QUESTION:
                {prompt}
                """
                
                with st.spinner("Analyzing trends..."):
                    response = model.generate_content(full_prompt)
                    ai_reply = response.text
                
                with st.chat_message("assistant"):
                    st.markdown(ai_reply)
                st.session_state.messages.append({"role": "assistant", "content": ai_reply})
                
            except Exception as e:
                st.error(f"AI Error: {e}")
