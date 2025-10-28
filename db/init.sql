CREATE TABLE IF NOT EXISTS user_library (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT,
    appid INT,
    name VARCHAR(255),
    playtime_hours FLOAT,
    added_at DATETIME,
    UNIQUE(user_id, appid)
);

CREATE TABLE IF NOT EXISTS popular_games (
    appid INT PRIMARY KEY,
    name VARCHAR(255),
    genre TEXT,
    categories TEXT,
    short_des TEXT,
    header_image TEXT,
    developer TEXT,
    publisher TEXT,
    release_date TEXT
);
