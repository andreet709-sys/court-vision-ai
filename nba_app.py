import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
import time
from io import StringIO
from nba_api.stats.endpoints import playergamelog, leaguedashplayerstats
from nba_api.stats.static import players

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="CourtVision AI", page_icon="🧠", layout="wide")
st.title("🧠 CourtVision AI")

# --- CONFIGURE GEMINI AI ---
try:
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
@st.cache_data(ttl=3600)
def get_live_injuries():
    url = "https://www.cbssports.com/nba/injuries/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        tables = pd.read_html(StringIO(response.text))
        injuries = {}
        for df in tables:
            if 'Player' in df.columns:
                for _, row in df.iterrows():
                    injuries[str(row['Player'])] = str(row['Injury Status'])
        return injuries
    except:
        return {}

@st.cache_data(ttl=1200)  # Cache for 20 mins since this scrapes many players
def get_league_trends():
    try:
        # 1. Get Top 15 Scorers (Season Avg) - UPDATED TO 2025-26
        stats = leaguedashplayerstats.LeagueDashPlayerStats(season='2025-26', per_mode_detailed='PerGame').get_data_frames()[0]
        
        if stats.empty:
            return pd.DataFrame(columns=['Player', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status'])

        top_players = stats.sort_values(by='PTS', ascending=False).head(15)
        
        trend_data = []
        
        # 2. Loop through them to get "Last 5" context
        for _, p in top_players.iterrows():
            try:
                # Fetch game logs for this specific player - UPDATED TO 2025-26
                logs = playergamelog.PlayerGameLog(player_id=p['PLAYER_ID'], season='2025-26').get_data_frames()[0]
                
                if not logs.empty:
                    last_5 = logs.head(5)
                    l5_ppg = last_5['PTS'].mean()
                    season_ppg = p['PTS']
                    delta = l5_ppg - season_ppg
                    
                    trend_data.append({
                        "Player": p['PLAYER_NAME'],
                        "Season PPG": round(season_ppg, 1),
                        "Last 5 PPG": round(l5_ppg, 1),
                        "Trend (Delta)": round(delta, 1),
                        "Status": "🔥 Heating Up" if delta > 2.0 else "❄️ Cooling Down" if delta < -2.0 else "Zap"
                    })
                time.sleep(0.1) # Tiny pause to be nice to NBA API
            except:
                continue
        
        # SAFETY CHECK: If no data was collected, return empty structure to prevent crash
        if not trend_data:
            return pd.DataFrame(columns=['Player', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status'])
            
        return pd.DataFrame(trend_data)

    except Exception as e:
        # Fallback if the NBA API fails entirely
        return pd.DataFrame(columns=['Player', 'Season PPG', 'Last 5 PPG', 'Trend (Delta)', 'Status'])

# --- CREATE TABS ---
tab1, tab2 = st.tabs(["📊 Dashboard", "💬 The Oracle"])

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
        trends = get_league_trends() # Uses your Top 15 Scorer list
        
        st.subheader("⚠️ Impact Players OUT")
        
        # Dynamic Check: Filter Injury Report for Top Scorers
        found_impact_injury = False
        
        if not trends.empty and 'Player' in trends.columns:
            top_scorers = trends['Player'].tolist()
            
            # Check every top scorer against the injury database
            for star in top_scorers:
                for injured_player, status in injuries.items():
                    # Simple check: Does the star's name appear in the injury list?
                    if star in injured_player: 
                        st.error(f"**{star}**: {status}")
                        found_impact_injury = True
        
        if not found_impact_injury:
            st.success("✅ Top 15 Scorers are healthy.")
            
        st.write("---")
        
        # Full Database Access
        st.caption(f"Tracking {len(injuries)} total league injuries.")
        with st.expander("🚑 View Full Injury Report"):
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
                        # UPDATED TO 2025-26
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
        st.subheader("🔥 Trends (Top 15 Scorers)")
        df_trends = get_league_trends()
        st.dataframe(df_trends[['Player', 'Trend (Delta)', 'Status']].head(10), hide_index=True)

# ==========================================
# TAB 2: THE ORACLE (GEMINI CHATBOT)
# ==========================================
with tab2:
    st.header("Ask the AI Analyst")
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
            {trends_data.to_string()}
            """

            try:
                # --- MODEL CONFIGURATION ---
                # Using the latest Flash model for speed and accuracy
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                full_prompt = f"""
                SYSTEM ROLE:
                You are "Daily NBA Analyst," an expert AI basketball analyst for the 2025-26 season. 
                
                CORE RULES:
                1. **Data Authority:** Use the LIVE DATA SOURCE below as your absolute truth. 
                2. **Reasoning:**
                   - Use the "Trend (Delta)" column in the data to identify who is Hot/Cold.
                   - If a player is NOT in the "Top Scorer Trends" list, admit you don't have their live trend data yet.
                   - Note: Jayson Tatum has missed the entire season due to injury (Achilles), which is why he does not appear in the active scorers list.
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

