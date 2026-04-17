import json
import os
import uuid
import psycopg2
import requests

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
DB_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("MAIN_DB_SCHEMA", "t_p89198250_telegram_vpn_bot_1")
SHOP_ID = os.environ.get("YUKASSA_SHOP_ID", "")
API_KEY = os.environ.get("YUKASSA_API_KEY", "")
XUI_URL = os.environ.get("XUI_URL", "").rstrip("/").replace("https://", "http://")
XUI_USERNAME = os.environ.get("XUI_USERNAME", "")
XUI_PASSWORD = os.environ.get("XUI_PASSWORD", "")
INBOUND_ID = 10


def xui_login():
    session = requests.Session()
    resp = session.post(f"{XUI_URL}/login", data={"username": XUI_USERNAME, "password": XUI_PASSWORD}, timeout=10)
    if resp.status_code == 200 and resp.json().get("success"):
        return session
    return None


def xui_delete_client(client_id: str):
    session = xui_login()
    if not session:
        return "Ошибка авторизации в панели"
    resp = session.post(f"{XUI_URL}/panel/api/inbounds/{INBOUND_ID}/delClient/{client_id}", timeout=10)
    if resp.status_code != 200:
        return f"Ошибка API: {resp.status_code}"
    data = resp.json()
    if not data.get("success"):
        return data.get("msg", "Ошибка удаления")
    return None


def get_db():
    return psycopg2.connect(DB_URL)


def send_message(chat_id, text):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
    }, timeout=10)


def charge_subscription(user_id, subscription_id, payment_method_id):
    idempotency_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": "199.00", "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": f"Подписка RossoVPN — 30 дней (user {user_id})",
        "metadata": {"user_id": str(user_id), "subscription_id": str(subscription_id)}
    }
    resp = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(SHOP_ID, API_KEY),
        json=payload,
        headers={"Idempotence-Key": idempotency_key},
        timeout=30
    )
    return resp.json()


def handler(event: dict, context) -> dict:
    """Cron-функция автосписания — проверяет истекающие подписки и списывает с карты"""
    headers = {"Access-Control-Allow-Origin": "*"}

    conn = get_db()
    cur = conn.cursor()

    # Уведомление за 3 дня до окончания
    cur.execute(
        f"""SELECT user_id FROM {SCHEMA}.subscriptions
            WHERE status='active'
            AND expires_at BETWEEN NOW() + INTERVAL '2 days 23 hours' AND NOW() + INTERVAL '3 days 1 hour'
            AND payment_method_id IS NOT NULL"""
    )
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "⏰ *Напоминание*\n\n"
            "Через 3 дня произойдёт автоматическое продление подписки RossoVPN за 199 ₽.\n\n"
            "Отменить можно командой /cancel"
        )

    # Списание за истёкшие подписки с привязанной картой
    cur.execute(
        f"""SELECT id, user_id, payment_method_id FROM {SCHEMA}.subscriptions
            WHERE status='active'
            AND expires_at < NOW()
            AND payment_method_id IS NOT NULL"""
    )
    subs = cur.fetchall()

    charged = 0
    failed = 0

    for sub_id, user_id, payment_method_id in subs:
        result = charge_subscription(user_id, sub_id, payment_method_id)
        payment_id = result.get("id")
        status = result.get("status")

        # Сохраняем платёж
        cur.execute(
            f"""INSERT INTO {SCHEMA}.payments (user_id, subscription_id, yukassa_payment_id, amount, status, payment_method_id)
                VALUES (%s, %s, %s, 199.00, %s, %s)""",
            (user_id, sub_id, payment_id, status or "pending", payment_method_id)
        )

        if status == "succeeded":
            cur.execute(
                f"UPDATE {SCHEMA}.subscriptions SET expires_at=NOW() + INTERVAL '30 days', updated_at=NOW() WHERE id=%s",
                (sub_id,)
            )
            charged += 1
        elif status in ("canceled", "refunded"):
            # Отключаем подписку
            cur.execute(
                f"UPDATE {SCHEMA}.subscriptions SET status='cancelled', cancelled_at=NOW(), updated_at=NOW() WHERE id=%s",
                (sub_id,)
            )
            send_message(user_id,
                "⚠️ *Не удалось списать оплату*\n\n"
                "Автопродление подписки не прошло — карта отклонила платёж.\n"
                "Оформить заново можно в главном меню.\n\n"
                "Поддержка: @btb75"
            )
            failed += 1

    # Деактивируем просроченные подписки без карты
    cur.execute(
        f"""UPDATE {SCHEMA}.subscriptions SET status='expired', updated_at=NOW()
            WHERE status='active' AND expires_at < NOW() AND payment_method_id IS NULL"""
    )

    # Уведомление за 1 день до окончания пробного ключа (только у кого нет активной подписки)
    cur.execute(
        f"""SELECT DISTINCT uk.user_id FROM {SCHEMA}.user_keys uk
            WHERE uk.expires_at BETWEEN NOW() + INTERVAL '23 hours' AND NOW() + INTERVAL '25 hours'
            AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.subscriptions s
                WHERE s.user_id = uk.user_id AND s.status = 'active'
            )"""
    )
    trial_notified = 0
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "⏰ *Пробный период заканчивается завтра*\n\n"
            "Твой пробный ключ RossoVPN истекает через 1 день.\n\n"
            "Оформи подписку — *199 ₽/месяц* — и продолжай пользоваться без перерыва:\n"
            "/start → 💳 Оформить подписку"
        )
        trial_notified += 1

    # Уведомление когда пробный ключ только что истёк (только у кого нет активной подписки)
    cur.execute(
        f"""SELECT DISTINCT uk.user_id FROM {SCHEMA}.user_keys uk
            WHERE uk.expires_at BETWEEN NOW() - INTERVAL '1 hour' AND NOW()
            AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.subscriptions s
                WHERE s.user_id = uk.user_id AND s.status = 'active'
            )"""
    )
    trial_expired = 0
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "🔒 *Пробный период закончился*\n\n"
            "Спасибо, что попробовал RossoVPN!\n\n"
            "Чтобы продолжить пользоваться VPN, оформи подписку:\n"
            "💳 *199 ₽/месяц* — безлимитный трафик, высокая скорость, автопродление.\n\n"
            "Оформить прямо сейчас → /start"
        )
        trial_expired += 1

    # Напоминание об оплате каждые 3 дня для отменённых/истёкших подписок без карты
    cur.execute(
        f"""SELECT DISTINCT s.user_id FROM {SCHEMA}.subscriptions s
            WHERE s.status IN ('cancelled', 'expired')
            AND s.expires_at < NOW()
            AND s.payment_method_id IS NULL
            AND EXTRACT(EPOCH FROM (NOW() - s.expires_at)) / 86400 > 0
            AND MOD(CAST(EXTRACT(EPOCH FROM (NOW() - s.expires_at)) / 86400 AS INTEGER), 3) = 0
            AND EXTRACT(HOUR FROM NOW()) BETWEEN 10 AND 11
            AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.subscriptions s2
                WHERE s2.user_id = s.user_id AND s2.status = 'active'
            )"""
    )
    sub_reminded = 0
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "💳 *Подписка не активна*\n\n"
            "Твоя подписка RossoVPN истекла — VPN не работает.\n\n"
            "Оформи снова за *199 ₽/месяц*:\n"
            "/start → 💳 Оформить подписку"
        )
        sub_reminded += 1

    # Удаление пробных ключей через 5 дней после истечения (если нет активной подписки)
    cur.execute(
        f"""SELECT uk.id, uk.user_id, uk.client_id FROM {SCHEMA}.user_keys uk
            WHERE uk.expires_at IS NOT NULL
            AND uk.expires_at < NOW() - INTERVAL '5 days'
            AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.subscriptions s
                WHERE s.user_id = uk.user_id AND s.status = 'active'
            )"""
    )
    keys_to_delete = cur.fetchall()
    deleted = 0
    for key_id, user_id, client_id in keys_to_delete:
        xui_delete_client(client_id)
        cur.execute(f"DELETE FROM {SCHEMA}.user_keys WHERE id = %s", (key_id,))
        send_message(user_id,
            "🗑 *Ваш пробный ключ удалён*\n\n"
            "Прошло 5 дней с окончания пробного периода — ключ был автоматически удалён.\n\n"
            "Чтобы снова пользоваться RossoVPN, оформите подписку:\n"
            "💳 *199 ₽/месяц* — безлимитный трафик, высокая скорость.\n\n"
            "Подключиться → /start"
        )
        deleted += 1

    conn.commit()
    cur.close()
    conn.close()

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"ok": True, "charged": charged, "failed": failed, "trial_notified": trial_notified, "trial_expired": trial_expired, "deleted": deleted, "sub_reminded": sub_reminded})
    }