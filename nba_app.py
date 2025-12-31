import streamlit as st
import pandas as pd
import requests
from io import StringIO
from nba_api.stats.endpoints import playergamelog, leaguedashplayerstats, commonplayerinfo
from nba_api.stats.static import players

# --- PAGE CONFIGURATION (The "Razzle Dazzle") ---
st.set_page_config(page_title="CourtVision AI", page_icon="🧠", layout="wide")
# ... (keep your css code) ...
st.title("🧠 CourtVision AI")

# --- CSS HACKS FOR VISUALS ---
st.markdown("""
<style>
    .metric-card {background-color: #f0f2f6; padding: 20px; border-radius: 10px; margin: 10px 0;}
    .big-font {font-size:20px !important;}
</style>
""", unsafe_allow_html=True)

# --- CACHED FUNCTIONS (Speed up the tool) ---
# We use @st.cache_data so we don't re-scrape CBS every time the client clicks a button
@st.cache_data(ttl=3600) # Update cache every hour
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
    # ADDED: per_mode_detailed='PerGame'
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
# --- MAIN APP UI ---
st.markdown("### *Artificial Intelligence for Sports Investing*")

# 1. SIDEBAR: MORNING BRIEFING
with st.sidebar:
    st.header("🌞 Morning Briefing")
    st.info("Live Injury Report Loaded from CBS Sports")
    
    injuries = get_live_injuries()
    
    # Check for Key Injuries (Client's Watchlist)
    watchlist = ["LeBron James", "Joel Embiid", "Giannis Antetokounmpo", "Stephen Curry"]
    
    st.subheader("⚠️ Key Injury Watch")
    for star in watchlist:
        # Check partial match
        status = "Healthy"
        for k, v in injuries.items():
            if star in k:
                status = f"🚨 {v}"
        st.write(f"**{star}:** {status}")
        
    st.write("---")
    st.caption(f"Tracking {len(injuries)} total injuries today.")

# 2. MAIN AREA: PLAYER LOOKUP & ANALYSIS
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("🔎 Player Deep Dive")
    player_name = st.text_input("Enter Player Name (e.g. 'Jayson Tatum'):", placeholder="Type name here...")

    if player_name:
        # Find player
        matching_players = [p for p in players.get_active_players() 
                           if player_name.lower() in p['full_name'].lower()]
        
        if not matching_players:
            st.error("Player not found. Check spelling.")
        else:
            p = matching_players[0]
            st.success(f"Found: {p['full_name']}")
            
            # GET DATA
            with st.spinner('Crunching numbers...'):
                try:
                    # Career stats for season avg
                    career = leaguedashplayerstats.LeagueDashPlayerStats(season='2024-25', per_mode_detailed='PerGame').get_data_frames()[0]
                    player_season = career[career['PLAYER_ID'] == p['id']]
                    
                    # Game logs for recent form
                    logs = playergamelog.PlayerGameLog(player_id=p['id'], season='2024-25').get_data_frames()[0]
                    
                    if not player_season.empty and not logs.empty:
                        # METRICS ROW
                        stats = player_season.iloc[0]
                        l5 = logs.head(5)
                        
                        season_pra = stats['PTS'] + stats['REB'] + stats['AST']
                        l5_pra = l5['PTS'].mean() + l5['REB'].mean() + l5['AST'].mean()
                        delta = l5_pra - season_pra
                        
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Season PPG", f"{stats['PTS']:.1f}")
                        m2.metric("Last 5 PRA", f"{l5_pra:.1f}", delta=f"{delta:.1f}")
                        m3.metric("Trend", "🔥 HOT" if delta > 0 else "❄️ COLD")
                        
                        # CHART
                        st.subheader("Recent Performance (PTS + REB + AST)")
                        logs['PRA'] = logs['PTS'] + logs['REB'] + logs['AST']
                        # Reverse order so graph goes left-to-right
                        chart_data = logs.head(10).iloc[::-1].set_index('GAME_DATE')[['PRA']]
                        st.line_chart(chart_data)
                        
                        # INJURY IMPACT NOTE
                        st.info(f"💡 **Strategy Note:** If a key teammate is OUT, expect usage for {p['full_name']} to rise. Check the sidebar for injury alerts.")
                        
                    else:
                        st.warning("No data found for 2024-25 season yet.")
                        
                except Exception as e:
                    st.error(f"Error fetching data: {e}")

with col2:
    st.subheader("🔥 League Leaders (PTS)")
    # Load the cached trend data
    df_trends = get_league_trends()

    st.dataframe(df_trends[['Name', 'Team', 'PPG']].head(10), hide_index=True)

