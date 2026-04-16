CREATE TABLE t_p89198250_telegram_vpn_bot_1.subscriptions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    payment_method_id TEXT,
    started_at TIMESTAMP,
    expires_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_subscriptions_user_id ON t_p89198250_telegram_vpn_bot_1.subscriptions(user_id);
CREATE INDEX idx_subscriptions_status ON t_p89198250_telegram_vpn_bot_1.subscriptions(status);
CREATE INDEX idx_subscriptions_expires_at ON t_p89198250_telegram_vpn_bot_1.subscriptions(expires_at);

CREATE TABLE t_p89198250_telegram_vpn_bot_1.payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    subscription_id INTEGER REFERENCES t_p89198250_telegram_vpn_bot_1.subscriptions(id),
    yukassa_payment_id TEXT UNIQUE,
    amount NUMERIC(10,2) NOT NULL DEFAULT 199.00,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    payment_method_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_payments_user_id ON t_p89198250_telegram_vpn_bot_1.payments(user_id);
CREATE INDEX idx_payments_yukassa_id ON t_p89198250_telegram_vpn_bot_1.payments(yukassa_payment_id);
