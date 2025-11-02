import os
import urllib.parse

STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "E579F61D0F6B642C45C82A7A946D5EF7")

# Railway provides MYSQLURL environment variable
if os.environ.get("MYSQLURL"):
    # Parse Railway's MySQL URL
    url = urllib.parse.urlparse(os.environ.get("MYSQLURL"))
    DB_CONFIG = {
        "host": url.hostname,
        "user": url.username,
        "password": url.password,
        "database": url.path[1:],  # Remove leading slash
        "port": url.port or 3306
    }
else:
    # Local development
    DB_CONFIG = {
        "host": os.environ.get("DB_HOST", "localhost"),
        "user": os.environ.get("DB_USER", "python_user"),
        "password": os.environ.get("DB_PASSWORD", "aizen"),
        "database": os.environ.get("DB_NAME", "game_recommender"),
        "port": int(os.environ.get("DB_PORT", 3306))
    }