
ALTER TABLE t_p89198250_telegram_vpn_bot_1.user_states
    ADD COLUMN IF NOT EXISTS tg_username TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS tg_first_name TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS t_p89198250_telegram_vpn_bot_1.user_keys (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    client_id TEXT NOT NULL,
    name TEXT NOT NULL,
    vless_link TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_keys_user_id ON t_p89198250_telegram_vpn_bot_1.user_keys(user_id);
