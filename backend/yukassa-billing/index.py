import json
import os
import uuid
import psycopg2
import requests

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
DB_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("MAIN_DB_SCHEMA", "t_p89198250_telegram_vpn_bot_1")
SHOP_ID = os.environ["YUKASSA_SHOP_ID"]
API_KEY = os.environ["YUKASSA_API_KEY"]


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

    conn.commit()
    cur.close()
    conn.close()

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"ok": True, "charged": charged, "failed": failed})
    }
