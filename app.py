from flask import Flask, redirect, request, session, render_template, jsonify
import requests
import mysql.connector
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from config import STEAM_API_KEY, DB_CONFIG

app = Flask(__name__)
app.secret_key = "aizen"

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"

# ----------------- Home & Login -----------------
@app.route("/")
def home():
    return render_template("login.html")

@app.route("/login")
def login():
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": "http://localhost:5000/authorize",
        "openid.realm": "http://localhost:5000/",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    url = STEAM_OPENID_URL + "?" + "&".join([f"{k}={v}" for k, v in params.items()])
    return redirect(url)

# ----------------- Steam Authorization -----------------
@app.route("/authorize")
def authorize():
    claimed_id = request.args.get("openid.claimed_id")
    steam_id = claimed_id.split("/")[-1]
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

# ----------------- Get Steam User Profile -----------------
def get_steam_user_profile(steam_id):
    try:
        url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_API_KEY}&steamids={steam_id}"
        response = requests.get(url, timeout=10)
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

        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        games_inserted = 0
        for game in owned_games:
            appid = game["appid"]
            name = game.get("name", f"AppID {appid}")
            playtime_hours = game.get("playtime_forever", 0) / 60
            user_id = int(steam_id[:16])
            
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
        connection = mysql.connector.connect(**DB_CONFIG)
        user_library = pd.read_sql(
            f"SELECT * FROM user_library WHERE user_id = {int(steam_id[:16])} ORDER BY playtime_hours DESC", 
            connection
        )
        connection.close()
        
        # Convert to list of dictionaries with native Python types
        games_list = []
        for _, game in user_library.iterrows():
            games_list.append({
                "appid": int(game["appid"]),  # Convert to native int
                "name": str(game["name"]),    # Convert to native str
                "playtime_hours": float(game["playtime_hours"]),  # Convert to native float
                "playtime_minutes": float(game["playtime_hours"] * 60)
            })
        
        return games_list
    except Exception as e:
        print(f"Error getting user games from DB: {e}")
        return []

# ----------------- FIXED: Get Recommendations for Specific Game -----------------
@app.route("/get_recommendations/<int:appid>")
def get_recommendations(appid):
    steam_id = session.get("steam_id")
    if not steam_id:
        return jsonify({"error": "Not authenticated"}), 401
    
    print(f"Getting recommendations for appid: {appid}, user: {steam_id}")
    recommendations = generate_recommendations_for_game(steam_id, appid)
    print(f"Generated {len(recommendations)} recommendations")
    return jsonify(recommendations)

def generate_recommendations_for_game(steam_id, appid, recommendations_count=6):
    try:
        print(f"Starting recommendation generation for appid: {appid}")
        
        connection = mysql.connector.connect(**DB_CONFIG)
        
        # Get the specific game details from user library
        game_query = f"SELECT * FROM user_library WHERE user_id = {int(steam_id[:16])} AND appid = {appid}"
        user_game = pd.read_sql(game_query, connection)
        
        if user_game.empty:
            print(f"Game {appid} not found in user library")
            return []
        
        game_name = str(user_game.iloc[0]["name"])  # Convert to native string
        print(f"Found game in user library: {game_name} (AppID: {appid})")
        
        # Get popular games
        popular_games = pd.read_sql("SELECT * FROM popular_games", connection)
        print(f"Loaded {len(popular_games)} games from popular_games")
        
        if popular_games.empty:
            print("Popular games table is empty")
            return []

        # Check if the game exists in popular_games
        game_in_popular = popular_games[popular_games["appid"] == appid]
        if game_in_popular.empty:
            print(f"Game {game_name} (AppID: {appid}) not found in popular_games")
            # Try to find similar games anyway using the game name
            return get_fallback_recommendations(game_name, popular_games, steam_id, recommendations_count)
        
        print(f"Game found in popular_games: {game_in_popular.iloc[0]['name']}")
        
        # --- Feature Engineering ---
        def combine_features(row):
            genre = str(row["genre"] or "") + " "
            categories = str(row["categories"] or "") + " "
            description = str(row["short_des"] or "")
            return (genre * 3) + (categories * 2) + description

        popular_games["combined_features"] = popular_games.apply(combine_features, axis=1)

        # --- TF-IDF Vectorization ---
        vectorizer = TfidfVectorizer(stop_words="english", max_features=5000, min_df=1)
        tfidf_matrix = vectorizer.fit_transform(popular_games["combined_features"])
        
        # Create appid to index mapping
        appid_to_index = {int(appid): idx for idx, appid in enumerate(popular_games["appid"])}  # Convert to int
        
        idx = appid_to_index.get(int(appid))  # Ensure appid is int
        if idx is None:
            print(f"Game index not found for appid: {appid}")
            return []

        # Calculate similarities
        sim_scores = cosine_similarity(tfidf_matrix[idx], tfidf_matrix).flatten()
        print(f"Calculated similarity scores, max: {sim_scores.max():.3f}, min: {sim_scores.min():.3f}")

        # Get user's owned games to exclude them
        user_library = pd.read_sql(
            f"SELECT appid FROM user_library WHERE user_id = {int(steam_id[:16])}", 
            connection
        )
        owned_appids = [int(x) for x in user_library["appid"].tolist()]  # Convert to native int
        print(f"User owns {len(owned_appids)} games, excluding from recommendations")

        # Get top recommendations
        recommendations = []
        top_indices = sim_scores.argsort()[::-1]
        
        count = 0
        for i in top_indices:
            if count >= recommendations_count:
                break
                
            rec = popular_games.iloc[i]
            similarity_score = float(sim_scores[i])  # Convert to native float
            
            # Skip if already owned or same game
            if int(rec["appid"]) in owned_appids:  # Convert to int for comparison
                continue
                
            # Skip if similarity is too low
            if similarity_score < 0.01:  # Lower threshold
                continue
                
            recommendations.append({
                "name": str(rec["name"]),  # Convert to native string
                "appid": int(rec["appid"]),  # Convert to native int
                "poster": str(rec["header_image"]) if pd.notna(rec["header_image"]) else "",
                "genre": str(rec["genre"] or "Unknown Genre"),  # Convert to native string
                "similarity": round(similarity_score, 3),  # Already native float
                "reason": f"Similar to {game_name}"
            })
            count += 1
            print(f"Added recommendation: {rec['name']} (score: {similarity_score:.3f})")

        connection.close()
        
        # If no recommendations found, try fallback
        if not recommendations:
            print("No recommendations found with content-based filtering, trying fallback...")
            return get_fallback_recommendations(game_name, popular_games, steam_id, recommendations_count)
        
        print(f"Successfully generated {len(recommendations)} recommendations")
        return recommendations
    
    except Exception as e:
        print(f"Error generating recommendations for appid {appid}: {e}")
        import traceback
        traceback.print_exc()
        return []

def get_fallback_recommendations(game_name, popular_games, steam_id, recommendations_count=6):
    """Fallback method when content-based filtering fails"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        user_library = pd.read_sql(
            f"SELECT appid FROM user_library WHERE user_id = {int(steam_id[:16])}", 
            connection
        )
        owned_appids = [int(x) for x in user_library["appid"].tolist()]  # Convert to native int
        connection.close()
        
        # Get games with similar genres (simple text matching)
        recommendations = []
        game_words = set(game_name.lower().split())
        
        for _, game in popular_games.iterrows():
            if len(recommendations) >= recommendations_count:
                break
                
            if int(game["appid"]) in owned_appids:  # Convert to int for comparison
                continue
                
            # Simple keyword matching
            game_name_words = set(str(game["name"]).lower().split())
            common_words = game_words.intersection(game_name_words)
            
            if len(common_words) >= 1:  # At least one common word
                recommendations.append({
                    "name": str(game["name"]),  # Convert to native string
                    "appid": int(game["appid"]),  # Convert to native int
                    "poster": str(game["header_image"]) if pd.notna(game["header_image"]) else "",
                    "genre": str(game["genre"] or "Unknown Genre"),  # Convert to native string
                    "similarity": round(0.3 + (len(common_words) * 0.1), 3),  # Fake similarity score
                    "reason": f"Similar keywords to {game_name}"
                })
        
        # If still no recommendations, return popular games
        if not recommendations:
            for _, game in popular_games.iterrows():
                if len(recommendations) >= recommendations_count:
                    break
                    
                if int(game["appid"]) not in owned_appids:  # Convert to int for comparison
                    recommendations.append({
                        "name": str(game["name"]),  # Convert to native string
                        "appid": int(game["appid"]),  # Convert to native int
                        "poster": str(game["header_image"]) if pd.notna(game["header_image"]) else "",
                        "genre": str(game["genre"] or "Unknown Genre"),  # Convert to native string
                        "similarity": 0.2,  # Low similarity score
                        "reason": "Popular game you might like"
                    })
        
        print(f"Fallback generated {len(recommendations)} recommendations")
        return recommendations
        
    except Exception as e:
        print(f"Error in fallback recommendations: {e}")
        return []

# ----------------- Debug Route -----------------
@app.route("/debug/user_games")
def debug_user_games():
    steam_id = session.get("steam_id")
    if not steam_id:
        return "Not authenticated"
    
    connection = mysql.connector.connect(**DB_CONFIG)
    user_library = pd.read_sql(
        f"SELECT * FROM user_library WHERE user_id = {int(steam_id[:16])}", 
        connection
    )
    popular_games = pd.read_sql("SELECT * FROM popular_games", connection)
    connection.close()
    
    result = f"""
    <h1>Debug Info</h1>
    <h2>User Library ({len(user_library)} games):</h2>
    <ul>
    """
    for _, game in user_library.iterrows():
        result += f"<li>{game['name']} (AppID: {game['appid']}, Playtime: {game['playtime_hours']}h)</li>"
    
    result += f"""
    </ul>
    <h2>Popular Games ({len(popular_games)} games)</h2>
    <h2>Matching Games:</h2>
    <ul>
    """
    
    matching_games = popular_games[popular_games["appid"].isin(user_library["appid"])]
    for _, game in matching_games.iterrows():
        result += f"<li>{game['name']} (AppID: {game['appid']})</li>"
    
    result += "</ul>"
    return result

# ----------------- Run Flask App -----------------
if __name__ == "__main__":
    app.run(debug=True)