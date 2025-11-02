from flask import Flask, redirect, request, session, render_template, jsonify
import requests
import mysql.connector
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from config import STEAM_API_KEY, DB_CONFIG
import os
import urllib.parse

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "aizen")

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"

# ----------------- Database Connection Helper -----------------
def get_db_connection():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None

# ----------------- Home & Login -----------------
@app.route("/")
def home():
    return render_template("login.html")

@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "message": "Game Master API is running"})

@app.route("/login")
def login():
    # Get current domain for production
    base_url = request.host_url.rstrip('/')
    if 'railway' in base_url or 'localhost' not in base_url:
        return_to = f"{base_url}/authorize"
        realm = base_url
    else:
        return_to = "http://localhost:5000/authorize"
        realm = "http://localhost:5000/"
    
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    url = STEAM_OPENID_URL + "?" + "&".join([f"{k}={v}" for k, v in params.items()])
    return redirect(url)

# ----------------- Steam Authorization -----------------
@app.route("/authorize")
def authorize():
    try:
        claimed_id = request.args.get("openid.claimed_id")
        if not claimed_id:
            return "Authentication failed: No claimed ID", 400
            
        steam_id = claimed_id.split("/")[-1]
        if not steam_id.isdigit():
            return "Invalid Steam ID", 400
            
        session["steam_id"] = steam_id
        
        # Get Steam username and avatar
        user_profile = get_steam_user_profile(steam_id)
        session["username"] = user_profile.get("personaname", "Steam User")
        session["avatar"] = user_profile.get("avatarfull", "")
        
        games_count = fetch_user_library(steam_id)
        user_games = get_user_games_from_db(steam_id)

        return render_template("dashboard.html", 
                             username=session["username"],
                             avatar=session["avatar"],
                             steam_id=steam_id, 
                             user_games=user_games,
                             games_count=games_count)
    except Exception as e:
        print(f"Authorization error: {e}")
        return f"Authentication error: {str(e)}", 500

# ----------------- Get Steam User Profile -----------------
def get_steam_user_profile(steam_id):
    try:
        url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_API_KEY}&steamids={steam_id}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        players = data.get("response", {}).get("players", [])
        if players:
            return players[0]
        return {}
    except Exception as e:
        print(f"Error fetching user profile: {e}")
        return {}

# ----------------- Fetch User Library -----------------
def fetch_user_library(steam_id):
    try:
        url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={steam_id}&include_appinfo=1&include_played_free_games=1"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        owned_games = data.get("response", {}).get("games", [])
        print(f"Found {len(owned_games)} games in Steam API response")

        if not owned_games:
            return 0

        connection = get_db_connection()
        if not connection:
            return 0

        cursor = connection.cursor()

        games_inserted = 0
        for game in owned_games:
            appid = game.get("appid")
            name = game.get("name", f"AppID {appid}")
            playtime_forever = game.get("playtime_forever", 0)
            playtime_hours = playtime_forever / 60.0
            
            # Create a stable user_id from steam_id
            user_id = int(steam_id) % 100000000
            
            cursor.execute(
                """
                INSERT INTO user_library (user_id, appid, name, playtime_hours, added_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE 
                    name=VALUES(name), 
                    playtime_hours=VALUES(playtime_hours),
                    added_at=NOW()
                """,
                (user_id, appid, name, playtime_hours),
            )
            games_inserted += 1

        connection.commit()
        cursor.close()
        connection.close()
        
        print(f"Successfully inserted/updated {games_inserted} games in database")
        return games_inserted
        
    except Exception as e:
        print(f"Error fetching user library: {e}")
        return 0

# ----------------- Get User Games from DB -----------------
def get_user_games_from_db(steam_id):
    try:
        connection = get_db_connection()
        if not connection:
            return []
        
        # Create stable user_id same as in fetch_user_library
        user_id = int(steam_id) % 100000000
            
        user_library = pd.read_sql(
            f"SELECT * FROM user_library WHERE user_id = {user_id} ORDER BY playtime_hours DESC", 
            connection
        )
        connection.close()
        
        # Convert to list of dictionaries with native Python types
        games_list = []
        for _, game in user_library.iterrows():
            games_list.append({
                "appid": int(game["appid"]),
                "name": str(game["name"]),
                "playtime_hours": float(game["playtime_hours"]),
                "playtime_minutes": float(game["playtime_hours"] * 60)
            })
        
        print(f"Retrieved {len(games_list)} games from database for user {user_id}")
        return games_list
    except Exception as e:
        print(f"Error getting user games from DB: {e}")
        return []

# ----------------- FIXED: Get Recommendations -----------------
@app.route("/get_recommendations/<int:appid>")
def get_recommendations(appid):
    try:
        steam_id = session.get("steam_id")
        if not steam_id:
            return jsonify({"error": "Not authenticated"}), 401
        
        print(f"Getting recommendations for appid: {appid}, user: {steam_id}")
        recommendations = generate_recommendations_for_game(steam_id, appid)
        print(f"Generated {len(recommendations)} recommendations")
        return jsonify(recommendations)
    except Exception as e:
        print(f"Error in get_recommendations route: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

def generate_recommendations_for_game(steam_id, appid, recommendations_count=6):
    connection = None
    try:
        print(f"Starting recommendation generation for appid: {appid}")
        
        connection = get_db_connection()
        if not connection:
            return []
        
        # Create user_id
        user_id = int(steam_id) % 100000000
        
        # Get user's owned games to exclude them
        user_library = pd.read_sql(
            f"SELECT appid FROM user_library WHERE user_id = {user_id}", 
            connection
        )
        owned_appids = [int(x) for x in user_library["appid"].tolist()]
        print(f"User owns {len(owned_appids)} games, excluding from recommendations")

        # Get popular games
        popular_games = pd.read_sql("SELECT * FROM popular_games", connection)
        print(f"Loaded {len(popular_games)} games from popular_games")
        
        if popular_games.empty:
            print("Popular games table is empty")
            return []

        # Get the specific game name from user library
        game_query = f"SELECT name FROM user_library WHERE user_id = {user_id} AND appid = {appid}"
        user_game = pd.read_sql(game_query, connection)
        
        if user_game.empty:
            print(f"Game {appid} not found in user library")
            return []
        
        game_name = str(user_game.iloc[0]["name"])
        print(f"Finding games similar to: {game_name}")

        # Check if the game exists in popular_games
        game_in_popular = popular_games[popular_games["appid"] == appid]
        
        if game_in_popular.empty:
            print(f"Game {game_name} not found in popular_games, using fallback")
            return get_fallback_recommendations(game_name, popular_games, owned_appids, recommendations_count)

        # --- Feature Engineering ---
        def combine_features(row):
            try:
                genre = str(row.get("genre", "") or "") + " "
                categories = str(row.get("categories", "") or "") + " "
                description = str(row.get("short_des", "") or "")
                tags = str(row.get("tags", "") or "")
                return (genre * 3) + (categories * 2) + description + " " + tags
            except:
                return ""

        # Handle missing columns gracefully
        required_columns = ["genre", "categories", "short_des", "tags"]
        for col in required_columns:
            if col not in popular_games.columns:
                popular_games[col] = ""

        popular_games["combined_features"] = popular_games.apply(combine_features, axis=1)

        # --- TF-IDF Vectorization ---
        try:
            vectorizer = TfidfVectorizer(stop_words="english", max_features=5000, min_df=1)
            tfidf_matrix = vectorizer.fit_transform(popular_games["combined_features"])
        except Exception as e:
            print(f"TF-IDF error: {e}, using fallback")
            return get_fallback_recommendations(game_name, popular_games, owned_appids, recommendations_count)

        # Create appid to index mapping
        appid_to_index = {int(appid): idx for idx, appid in enumerate(popular_games["appid"])}
        
        idx = appid_to_index.get(int(appid))
        if idx is None:
            print(f"Game index not found for appid: {appid}")
            return get_fallback_recommendations(game_name, popular_games, owned_appids, recommendations_count)

        # Calculate similarities
        sim_scores = cosine_similarity(tfidf_matrix[idx], tfidf_matrix).flatten()
        print(f"Similarity scores - Max: {sim_scores.max():.3f}, Min: {sim_scores.min():.3f}")

        # Get top recommendations
        recommendations = []
        
        # Get indices sorted by similarity (descending)
        similar_indices = sim_scores.argsort()[::-1]
        
        for i in similar_indices:
            if len(recommendations) >= recommendations_count:
                break
                
            rec_game = popular_games.iloc[i]
            similarity_score = float(sim_scores[i])
            rec_appid = int(rec_game["appid"])
            
            # Skip if already owned or same game
            if rec_appid in owned_appids or rec_appid == appid:
                continue
                
            # Skip if similarity is too low
            if similarity_score < 0.05:
                continue
                
            recommendations.append({
                "name": str(rec_game["name"]),
                "appid": rec_appid,
                "poster": str(rec_game.get("header_image", "")) if pd.notna(rec_game.get("header_image")) else "",
                "genre": str(rec_game.get("genre", "Unknown") or "Unknown"),
                "similarity": round(similarity_score, 3)
            })

        # If no recommendations found, use fallback
        if not recommendations:
            print("No recommendations found with similarity filtering, using fallback")
            return get_fallback_recommendations(game_name, popular_games, owned_appids, recommendations_count)
        
        print(f"Successfully generated {len(recommendations)} recommendations")
        return recommendations
        
    except Exception as e:
        print(f"Error generating recommendations: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        if connection:
            connection.close()

def get_fallback_recommendations(game_name, popular_games, owned_appids, recommendations_count=6):
    """Fallback method when similarity filtering fails"""
    try:
        recommendations = []
        
        # Try to find games with similar genres first
        game_words = set(game_name.lower().split())
        
        for _, game in popular_games.iterrows():
            if len(recommendations) >= recommendations_count:
                break
                
            game_appid = int(game["appid"])
            if game_appid in owned_appids:
                continue
                
            # Check for common words in game names
            current_game_words = set(str(game["name"]).lower().split())
            common_words = game_words.intersection(current_game_words)
            
            if len(common_words) >= 2:  # At least 2 common words
                recommendations.append({
                    "name": str(game["name"]),
                    "appid": game_appid,
                    "poster": str(game.get("header_image", "")) if pd.notna(game.get("header_image")) else "",
                    "genre": str(game.get("genre", "Unknown") or "Unknown"),
                    "similarity": round(0.4 + (len(common_words) * 0.1), 3)
                })

        # If still no recommendations, get random popular games
        if not recommendations:
            available_games = popular_games[~popular_games["appid"].isin(owned_appids)]
            if len(available_games) > 0:
                sample_games = available_games.sample(min(recommendations_count, len(available_games)))
                for _, game in sample_games.iterrows():
                    recommendations.append({
                        "name": str(game["name"]),
                        "appid": int(game["appid"]),
                        "poster": str(game.get("header_image", "")) if pd.notna(game.get("header_image")) else "",
                        "genre": str(game.get("genre", "Unknown") or "Unknown"),
                        "similarity": 0.3
                    })
        
        print(f"Fallback generated {len(recommendations)} recommendations")
        return recommendations
        
    except Exception as e:
        print(f"Error in fallback recommendations: {e}")
        return []

# ----------------- Debug Route -----------------
@app.route("/debug/db")
def debug_db():
    try:
        connection = get_db_connection()
        if not connection:
            return "Database connection failed"
            
        cursor = connection.cursor()
        
        # Check tables
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        # Check counts
        cursor.execute("SELECT COUNT(*) as count FROM user_library")
        user_library_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as count FROM popular_games")
        popular_games_count = cursor.fetchone()[0]
        
        cursor.close()
        connection.close()
        
        return f"""
        <h1>Database Debug</h1>
        <p>Tables: {tables}</p>
        <p>User Library Count: {user_library_count}</p>
        <p>Popular Games Count: {popular_games_count}</p>
        """
    except Exception as e:
        return f"Database error: {str(e)}"

# ----------------- Run Flask App -----------------
if __name__ == "__main__":
    print("🎮 GAME MASTER System Online")
    print("📍 Available Routes:")
    print("   / - Login Terminal")
    print("   /login - Steam Access")
    print("   /authorize - Authentication Protocol")
    print("   /get_recommendations/<appid> - Game Analysis")
    print("   /debug/db - System Diagnostics")
    print("   /health - Health Check")
    
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)