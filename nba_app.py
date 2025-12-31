import streamlit as st
import pandas as pd
import requests
import google.generativeai as genai
from io import StringIO
from nba_api.stats.endpoints import playergamelog, leaguedashplayerstats, commonplayerinfo
from nba_api.stats.static import players

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="CourtVision AI", page_icon="🧠", layout="wide")
st.title("🧠 CourtVision AI")

# --- CONFIGURE GEMINI AI ---
# This looks for the key in your Streamlit Secrets
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except:
    pass # We will show a friendly error in the chat tab if key is missing

# --- CSS HACKS (Stealth Mode) ---
st.markdown("""
<style>
    /* Hide the Streamlit "Manage App" button and top decoration bar */
    .stAppDeployButton, [data-testid="stDecoration"] {
        display: none !important;
    }
    
    /* Hide the "Made with Streamlit" footer */
    footer {
        visibility: hidden;
    }
    
    /* Hide the entire top toolbar (Hamburger menu + Deploy + Edit buttons) */
    [data-testid="stToolbar"] {
        visibility: hidden;
        height: 0%;
    }

    /* Card styles */
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

@st.cache_data(ttl=600)
def get_league_trends():
    # Fetching PER GAME stats
    stats = leaguedashplayerstats.LeagueDashPlayerStats(season='2024-25', per_mode_detailed='PerGame').get_data_frames()[0]
    top_players = stats.sort_values(by='PTS', ascending=False).head(30)
    
    trends = []
    
    for _, p in top_players.iterrows():
        trends.append({
            "Name": p['PLAYER_NAME'],
            "Team": p['TEAM_ABBREVIATION'],
            "PPG": p['PTS'],
            "ID": p['PLAYER_ID']
        })
    return pd.DataFrame(trends)

# --- CREATE TABS ---
tab1, tab2 = st.tabs(["📊 Dashboard", "💬 The Oracle"])

# ==========================================
# TAB 1: THE ORIGINAL DASHBOARD
# ==========================================
with tab1:
    st.markdown("### *Daily Intelligence Agent*")

    # 1. SIDEBAR (Moved inside Tab 1)
    with st.sidebar:
        st.header("🌞 Morning Briefing")
        st.info("Live Injury Report Loaded from CBS Sports")
        
        injuries = get_live_injuries()
        
        # Check for Key Injuries
        watchlist = ["LeBron James", "Joel Embiid", "Giannis Antetokounmpo", "Stephen Curry"]
        
        st.subheader("⚠️ Key Injury Watch")
        for star in watchlist:
            status = "Healthy"
            for k, v in injuries.items():
                if star in k:
                    status = f"🚨 {v}"
            st.write(f"**{star}:** {status}")
            
        st.write("---")
        st.caption(f"Tracking {len(injuries)} total injuries today.")

    # 2. MAIN AREA
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
                        career = leaguedashplayerstats.LeagueDashPlayerStats(season='2024-25', per_mode_detailed='PerGame').get_data_frames()[0]
                        player_season = career[career['PLAYER_ID'] == p['id']]
                        logs = playergamelog.PlayerGameLog(player_id=p['id'], season='2024-25').get_data_frames()[0]
                        
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
                            
                            st.subheader("Recent Performance (PTS + REB + AST)")
                            logs['PRA'] = logs['PTS'] + logs['REB'] + logs['AST']
                            chart_data = logs.head(10).iloc[::-1].set_index('GAME_DATE')[['PRA']]
                            st.line_chart(chart_data)
                            
                            st.info(f"💡 **Strategy Note:** If a key teammate is OUT, expect usage for {p['full_name']} to rise. Check the sidebar for injury alerts.")
                            
                        else:
                            st.warning("No data found for 2024-25 season yet.")
                            
                    except Exception as e:
                        st.error(f"Error fetching data: {e}")

    with col2:
        st.subheader("🔥 League Leaders (PTS)")
        df_trends = get_league_trends()
        st.dataframe(df_trends[['Name', 'Team', 'PPG']].head(10), hide_index=True)

# ==========================================
# TAB 2: THE ORACLE (GEMINI CHATBOT)
# ==========================================
with tab2:
    st.header("Ask the AI Analyst")
    st.info("💡 I have read today's injury reports and trend lines. Ask me anything.")

    # Check if API Key is set
    if "GOOGLE_API_KEY" not in st.secrets:
        st.error("⚠️ **Missing AI Key:** Please add `GOOGLE_API_KEY` to your Streamlit Secrets to enable the chat.")
    else:
        # 1. Initialize Chat History
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # 2. Display Old Messages
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # 3. Handle New User Input
        if prompt := st.chat_input("Ex: How does Embiid being out affect Maxey?"):
            # Show User Message
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})

            # 4. Prepare Data Context for Gemini
            injuries_data = get_live_injuries()
            trends_data = get_league_trends()
            
            # Create the "Source of Truth" Context
            context_text = f"""
            LIVE DATA SOURCE (Use this as your Primary Source):
            
            1. INJURY REPORT (Live from CBS Sports):
            {injuries_data}
            
            2. LEAGUE TRENDS (Top 30 Scorers - Last 5 Games vs Season Avg):
            {trends_data.to_string()}
            """

            # 5. Call Gemini with CUSTOM INSTRUCTIONS
            try:
                # Configure the model
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                full_prompt = f"""
                SYSTEM ROLE:
                You are "Daily NBA Analyst," an expert AI basketball analyst specializing in the current 2024-25 NBA season. 
                Your goal is to provide accurate, insightful, conversational answers using the LIVE DATA provided below.

                CORE RULES (Follow Exactly):
                1. **Data Authority:** The "LIVE DATA SOURCE" below is your absolute source of truth. It is live and up-to-date. Do not hallucinate stats not shown here.
                2. **Reasoning Process (Internal):**
                   - Compare "Last 5 Games" averages against "Season" averages.
                   - Identify significant deviations: ±10% or more = notable; ±20%+ = strong over/underperformance.
                   - Check Injury Report: If a key teammate is OUT, explicitly analyze the impact on usage.
                   - Show transparent math: e.g., "Season: 29.8 | Last 7: 33.2 → +3.4 delta (hot streak)".
                3. **Response Style:**
                   - Conversational and engaging — like a sharp hoops friend at the bar.
                   - Use clean markdown tables for comparisons.
                   - Be concise unless a deep dive is requested.

                LIVE DATA SOURCE:
                {context_text}
                
                USER QUESTION:
                {prompt}
                """
                
                with st.spinner("Analyzing markets & trends..."):
                    response = model.generate_content(full_prompt)
                    ai_reply = response.text
                
                # Show AI Message
                with st.chat_message("assistant"):
                    st.markdown(ai_reply)
                st.session_state.messages.append({"role": "assistant", "content": ai_reply})
                
            except Exception as e:
                st.error(f"AI Error: {e}")

