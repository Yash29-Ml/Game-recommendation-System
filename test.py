import mysql.connector
import requests

STEAM_API_KEY = "E579F61D0F6B642C45C82A7A946D5EF7"

def test_database():
    print("Testing database connection with fixed config...")
    try:
        connection = mysql.connector.connect(
            host="localhost",
            user="python_user",
            password="aizen",
            database="game_recommender",
            auth_plugin='mysql_native_password'
        )
        cursor = connection.cursor()
        
        # Check if tables exist
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print(f"Tables in database: {tables}")
        
        # Check user_library table
        cursor.execute("SELECT COUNT(*) FROM user_library")
        user_library_count = cursor.fetchone()[0]
        print(f"Games in user_library: {user_library_count}")
        
        # Check popular_games table
        cursor.execute("SELECT COUNT(*) FROM popular_games")
        popular_games_count = cursor.fetchone()[0]
        print(f"Games in popular_games: {popular_games_count}")
        
        cursor.close()
        connection.close()
        return True
    except Exception as e:
        print(f"Database error: {e}")
        return False

if __name__ == "__main__":
    test_database()