import json
import os
import psycopg2
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
DB_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("MAIN_DB_SCHEMA", "t_p89198250_telegram_vpn_bot_1")
MARZBAN_URL = os.environ['MARZBAN_URL'].rstrip('/')
MARZBAN_USERNAME = os.environ['MARZBAN_USERNAME']
MARZBAN_PASSWORD = os.environ['MARZBAN_PASSWORD']

_marzban_token = None
_marzban_token_expires = None


def get_db():
    return psycopg2.connect(DB_URL)


def send_message(chat_id, text, parse_mode="Markdown"):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }, timeout=10)


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


def marzban_create_user(username: str, expires_at: datetime | None) -> tuple:
    """Создаёт пользователя в Marzban. Возвращает (vless_link, error)."""
    headers = marzban_headers()
    if not headers:
        return None, "Ошибка авторизации в Marzban"
    expire_ts = int(expires_at.timestamp()) if expires_at else 0
    payload = {
        "username": username,
        "proxies": {"vless": {"flow": "xtls-rprx-vision"}},
        "inbounds": {"vless": ["VLESS_TCP_REALITY"]},
        "expire": expire_ts,
        "data_limit": 0,
        "data_limit_reset_strategy": "no_reset",
        "status": "active"
    }
    resp = requests.post(f"{MARZBAN_URL}/api/user", json=payload, headers=headers, timeout=10)
    print(f"[marzban_create_user] status={resp.status_code} body={resp.text[:300]}")
    if resp.status_code not in (200, 201):
        return None, f"Ошибка создания ключа: {resp.status_code}"
    links = resp.json().get("links", [])
    vless_link = next((l for l in links if l.startswith("vless://")), None)
    if not vless_link:
        return None, "Ключ создан, но ссылка не получена"
    return vless_link, None


def marzban_update_expire(username: str, expires_at: datetime | None) -> bool:
    """Обновляет срок действия пользователя в Marzban."""
    headers = marzban_headers()
    if not headers:
        return False
    expire_ts = int(expires_at.timestamp()) if expires_at else 0
    resp = requests.put(
        f"{MARZBAN_URL}/api/user/{username}",
        json={"expire": expire_ts},
        headers=headers,
        timeout=10
    )
    print(f"[marzban_update_expire] status={resp.status_code}")
    return resp.status_code == 200


def handle_payment_succeeded(user_id: int, payment_id: str, payment_method_id: str):
    """Активирует подписку и создаёт/продлевает ключ через Marzban."""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    cur.execute(
        f"UPDATE {SCHEMA}.payments SET status='succeeded', payment_method_id=%s, updated_at=NOW() WHERE yukassa_payment_id=%s",
        (payment_method_id, payment_id)
    )

    cur.execute(
        f"SELECT id, expires_at FROM {SCHEMA}.subscriptions WHERE user_id=%s AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    existing_sub = cur.fetchone()

    if existing_sub:
        current_expires = existing_sub[1]
        if current_expires.tzinfo is None:
            current_expires = current_expires.replace(tzinfo=timezone.utc)
        base = current_expires if current_expires > now else now
        new_expires = base + timedelta(days=30)
        cur.execute(
            f"UPDATE {SCHEMA}.subscriptions SET expires_at=%s, payment_method_id=%s, updated_at=NOW() WHERE id=%s",
            (new_expires, payment_method_id, existing_sub[0])
        )
        sub_id = existing_sub[0]
    else:
        new_expires = now + timedelta(days=30)
        cur.execute(
            f"INSERT INTO {SCHEMA}.subscriptions (user_id, status, payment_method_id, started_at, expires_at) VALUES (%s, 'active', %s, NOW(), %s) RETURNING id",
            (user_id, payment_method_id, new_expires)
        )
        sub_id = cur.fetchone()[0]
        cur.execute(
            f"UPDATE {SCHEMA}.payments SET subscription_id=%s WHERE yukassa_payment_id=%s",
            (sub_id, payment_id)
        )

    conn.commit()

    cur.execute(
        f"SELECT id, client_id, name FROM {SCHEMA}.user_keys WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,)
    )
    key_rows = cur.fetchall()

    cur.execute(f"SELECT name FROM {SCHEMA}.user_states WHERE user_id=%s", (user_id,))
    user_row = cur.fetchone()
    user_name = user_row[0] if user_row else "user"

    if key_rows:
        for key_id, marzban_username, key_name in key_rows:
            ok = marzban_update_expire(marzban_username, new_expires)
            if not ok:
                print(f"[webhook] marzban update error for key {key_id}")
            cur.execute(
                f"UPDATE {SCHEMA}.user_keys SET expires_at=%s WHERE id=%s",
                (new_expires, key_id)
            )

        conn.commit()
        cur.close()
        conn.close()

        keys_count = len(key_rows)
        keys_word = "ключ" if keys_count == 1 else ("ключа" if keys_count < 5 else "ключей")
        send_message(
            user_id,
            "✅ *Оплата прошла успешно!*\n\n"
            f"Подписка активирована до *{new_expires.strftime('%d.%m.%Y')}*.\n\n"
            f"🔑 {keys_count} {keys_word} продлено до *{new_expires.strftime('%d.%m.%Y')}*\n\n"
            "Карта сохранена — следующее списание автоматически через 30 дней.\n"
            "Для отмены: /cancel"
        )
    else:
        label = f"sub_{user_name}_{user_id}"
        vless_link, error = marzban_create_user(label, new_expires)

        if error:
            cur.close()
            conn.close()
            send_message(
                user_id,
                "✅ *Оплата прошла успешно!*\n\n"
                "Подписка активирована, но не удалось создать ключ автоматически.\n"
                "Напиши в поддержку: @btb75 — выдадим ключ вручную."
            )
            return

        cur.execute(
            f"INSERT INTO {SCHEMA}.user_keys (user_id, client_id, name, vless_link, created_at, expires_at) VALUES (%s, %s, %s, %s, NOW(), %s)",
            (user_id, label, "Подписка", vless_link, new_expires)
        )
        conn.commit()
        cur.close()
        conn.close()

        send_message(
            user_id,
            "✅ *Оплата прошла успешно!*\n\n"
            f"Подписка активирована до *{new_expires.strftime('%d.%m.%Y')}*.\n\n"
            "🔑 *Твой VPN ключ:*\n"
            f"`{vless_link}`\n\n"
            "Карта сохранена — следующее списание автоматически через 30 дней.\n"
            "Для отмены: /cancel"
        )


def handler(event: dict, context) -> dict:
    """Webhook от ЮКассы — обрабатывает успешные платежи и активирует подписки через Marzban."""
    headers = {"Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": {**headers, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}, "body": ""}

    body = json.loads(event.get("body") or "{}")
    event_type = body.get("event")
    obj = body.get("object", {})

    if event_type == "payment.succeeded":
        payment_id = obj.get("id")
        payment_method_id = obj.get("payment_method", {}).get("id")
        user_id = int(obj.get("metadata", {}).get("user_id", 0))

        if user_id and payment_id:
            handle_payment_succeeded(user_id, payment_id, payment_method_id)

    return {"statusCode": 200, "headers": headers, "body": json.dumps({"status": "ok"})}
