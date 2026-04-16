import json
import os
import psycopg2
import requests
from datetime import datetime, timedelta

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
DB_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("MAIN_DB_SCHEMA", "t_p89198250_telegram_vpn_bot_1")


def get_db():
    return psycopg2.connect(DB_URL)


def send_message(chat_id, text, parse_mode="Markdown"):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }, timeout=10)


def handler(event: dict, context) -> dict:
    """Вебхук от ЮКасса — обрабатывает события оплаты и активирует/деактивирует подписки"""
    headers = {"Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": {**headers, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}, "body": ""}

    if event.get("httpMethod") != "POST":
        return {"statusCode": 405, "headers": headers, "body": json.dumps({"error": "Method not allowed"})}

    body = json.loads(event.get("body") or "{}")
    event_type = body.get("event")
    payment_obj = body.get("object", {})

    payment_id = payment_obj.get("id")
    status = payment_obj.get("status")
    metadata = payment_obj.get("metadata", {})
    user_id = metadata.get("user_id")
    payment_method = payment_obj.get("payment_method", {})
    payment_method_id = payment_method.get("id") if payment_method.get("saved") else None

    if not payment_id or not user_id:
        return {"statusCode": 200, "headers": headers, "body": json.dumps({"ok": True})}

    user_id = int(user_id)
    conn = get_db()
    cur = conn.cursor()

    if event_type == "payment.succeeded":
        # Обновляем запись платежа
        cur.execute(
            f"UPDATE {SCHEMA}.payments SET status='succeeded', payment_method_id=%s, updated_at=NOW() WHERE yukassa_payment_id=%s",
            (payment_method_id, payment_id)
        )

        # Проверяем, есть ли активная подписка
        cur.execute(
            f"SELECT id FROM {SCHEMA}.subscriptions WHERE user_id=%s AND status='active'",
            (user_id,)
        )
        existing = cur.fetchone()

        if existing:
            # Продлеваем
            cur.execute(
                f"UPDATE {SCHEMA}.subscriptions SET expires_at=NOW() + INTERVAL '30 days', payment_method_id=%s, updated_at=NOW() WHERE id=%s",
                (payment_method_id or existing[0], existing[0])
            )
        else:
            # Создаём новую подписку
            cur.execute(
                f"INSERT INTO {SCHEMA}.subscriptions (user_id, status, payment_method_id, started_at, expires_at) VALUES (%s, 'active', %s, NOW(), NOW() + INTERVAL '30 days') RETURNING id",
                (user_id, payment_method_id)
            )
            sub_id = cur.fetchone()[0]
            # Привязываем платёж к подписке
            cur.execute(
                f"UPDATE {SCHEMA}.payments SET subscription_id=%s WHERE yukassa_payment_id=%s",
                (sub_id, payment_id)
            )

        conn.commit()
        cur.close()
        conn.close()

        send_message(user_id,
            "✅ *Оплата прошла успешно!*\n\n"
            "Подписка активирована на 30 дней.\n"
            "Карта сохранена — следующее списание произойдёт автоматически.\n\n"
            "Для отмены автопродления — команда /cancel"
        )

    elif event_type == "payment.canceled":
        cur.execute(
            f"UPDATE {SCHEMA}.payments SET status='canceled', updated_at=NOW() WHERE yukassa_payment_id=%s",
            (payment_id,)
        )
        conn.commit()
        cur.close()
        conn.close()

        send_message(user_id,
            "❌ *Оплата отменена*\n\n"
            "Подписка не была активирована. Попробуй снова или напиши в поддержку: @btb75"
        )

    elif event_type == "payment.waiting_for_capture":
        # Автоматически подтверждаем платёж
        shop_id = os.environ["YUKASSA_SHOP_ID"]
        api_key = os.environ["YUKASSA_API_KEY"]
        requests.post(
            f"https://api.yookassa.ru/v3/payments/{payment_id}/capture",
            auth=(shop_id, api_key),
            json={"amount": payment_obj.get("amount")},
            timeout=10
        )
        cur.close()
        conn.close()

    else:
        cur.close()
        conn.close()

    return {"statusCode": 200, "headers": headers, "body": json.dumps({"ok": True})}
