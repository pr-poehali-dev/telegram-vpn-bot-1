-- Добавляем колонку marzban_username в user_keys
ALTER TABLE t_p89198250_telegram_vpn_bot_1.user_keys
    ADD COLUMN IF NOT EXISTS marzban_username VARCHAR(255) NOT NULL DEFAULT '';
