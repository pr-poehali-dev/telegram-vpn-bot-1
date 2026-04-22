import json
import os
import uuid
import psycopg2
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
DB_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("MAIN_DB_SCHEMA", "t_p89198250_telegram_vpn_bot_1")
SHOP_ID = os.environ.get("YUKASSA_SHOP_ID", "")
API_KEY = os.environ.get("YUKASSA_API_KEY", "")
MARZBAN_URL = os.environ.get("MARZBAN_URL", "").rstrip("/")
MARZBAN_USERNAME = os.environ.get("MARZBAN_USERNAME", "")
MARZBAN_PASSWORD = os.environ.get("MARZBAN_PASSWORD", "")

_marzban_token = None
_marzban_token_expires = None


def marzban_get_token() -> str | None:
    global _marzban_token, _marzban_token_expires
    now = datetime.now(timezone.utc)
    if _marzban_token and _marzban_token_expires and now < _marzban_token_expires:
        return _marzban_token
    resp = requests.post(
        f"{MARZBAN_URL}/api/admin/token",
        data={"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD},
        timeout=10
    )
    if resp.status_code == 200:
        _marzban_token = resp.json().get("access_token")
        _marzban_token_expires = now + timedelta(minutes=50)
        return _marzban_token
    print(f"[marzban_get_token] error {resp.status_code}: {resp.text[:200]}")
    return None


def marzban_headers() -> dict:
    token = marzban_get_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def marzban_delete_user(username: str) -> str | None:
    """Удаляет пользователя из Marzban. None = успех, строка = ошибка."""
    headers = marzban_headers()
    if not headers:
        return "Ошибка авторизации в Marzban"
    resp = requests.delete(
        f"{MARZBAN_URL}/api/user/{username}",
        headers=headers,
        timeout=10
    )
    print(f"[marzban_delete_user] status={resp.status_code}")
    if resp.status_code in (200, 204, 404):
        return None
    return f"Ошибка удаления: {resp.status_code}"


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
    """Cron-функция автосписания — проверяет истекающие подписки и списывает с карты через ЮКасса."""
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

    # Уведомление за 1 день до окончания пробного ключа
    cur.execute(
        f"""SELECT DISTINCT uk.user_id FROM {SCHEMA}.user_keys uk
            WHERE uk.expires_at BETWEEN NOW() + INTERVAL '23 hours' AND NOW() + INTERVAL '25 hours'
            AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.subscriptions s
                WHERE s.user_id = uk.user_id AND s.status = 'active'
            )"""
    )
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "⏰ *Пробный период заканчивается завтра*\n\n"
            "Твой пробный ключ RossoVPN истекает через 1 день.\n\n"
            "Оформи подписку — *199 ₽/месяц* — и продолжай пользоваться без перерыва:\n"
            "/start → 💳 Оформить подписку"
        )

    # Уведомление когда пробный ключ только что истёк
    cur.execute(
        f"""SELECT DISTINCT uk.user_id FROM {SCHEMA}.user_keys uk
            WHERE uk.expires_at BETWEEN NOW() - INTERVAL '1 hour' AND NOW()
            AND NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.subscriptions s
                WHERE s.user_id = uk.user_id AND s.status = 'active'
            )"""
    )
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "🔒 *Пробный период закончился*\n\n"
            "Спасибо, что попробовал RossoVPN!\n\n"
            "Чтобы продолжить пользоваться VPN, оформи подписку:\n"
            "💳 *199 ₽/месяц* — безлимитный трафик, высокая скорость, автопродление.\n\n"
            "Оформить прямо сейчас → /start"
        )

    # Напоминание об оплате каждые 3 дня для отменённых/истёкших подписок
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
    for (user_id,) in cur.fetchall():
        send_message(user_id,
            "💳 *Подписка не активна*\n\n"
            "Твоя подписка RossoVPN истекла — VPN не работает.\n\n"
            "Оформи снова за *199 ₽/месяц*:\n"
            "/start → 💳 Оформить подписку"
        )

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
    for key_id, user_id, marzban_username in keys_to_delete:
        err = marzban_delete_user(marzban_username)
        if err:
            print(f"[billing] marzban delete error for key {key_id}: {err}")
        cur.execute(f"DELETE FROM {SCHEMA}.user_keys WHERE id = %s", (key_id,))
        send_message(user_id,
            "🗑 *Ваш пробный ключ удалён*\n\n"
            "Прошло 5 дней с окончания пробного периода — ключ был автоматически удалён.\n\n"
            "Оформите подписку чтобы снова получить доступ:\n"
            "/start → 💳 Оформить подписку"
        )
        deleted += 1

    conn.commit()
    cur.close()
    conn.close()

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({
            "charged": charged,
            "failed": failed,
            "deleted": deleted
        })
    }
