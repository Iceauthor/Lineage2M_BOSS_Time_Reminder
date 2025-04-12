-- 建立 boss_list 表
CREATE TABLE IF NOT EXISTS boss_list (
    id SERIAL PRIMARY KEY,
    display_name VARCHAR(255) NOT NULL,
    respawn_hours INTEGER DEFAULT 8
);

-- 建立 boss_aliases 表
CREATE TABLE IF NOT EXISTS boss_aliases (
    id SERIAL PRIMARY KEY,
    boss_id INTEGER REFERENCES boss_list(id) ON DELETE CASCADE,
    keyword VARCHAR(255) UNIQUE NOT NULL
);

-- 建立 boss_tasks 表
CREATE TABLE IF NOT EXISTS boss_tasks (
    id SERIAL PRIMARY KEY,
    boss_id INTEGER REFERENCES boss_list(id) ON DELETE CASCADE,
    group_id VARCHAR(255) NOT NULL,
    kill_time TIMESTAMP NOT NULL,
    respawn_time TIMESTAMP NOT NULL
);
