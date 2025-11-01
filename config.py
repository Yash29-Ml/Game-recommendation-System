import os

STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "E579F61D0F6B642C45C82A7A946D5EF7")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "python_user"),
    "password": os.environ.get("DB_PASSWORD", "aizen"),
    "database": os.environ.get("DB_NAME", "game_recommender")
}