CREATE TABLE IF NOT EXISTS t_p89198250_telegram_vpn_bot_1.user_states (
    user_id BIGINT PRIMARY KEY,
    step VARCHAR(50) NOT NULL DEFAULT 'idle',
    name TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT NOW()
);
