import json
import os
import uuid
import psycopg2
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
DB_URL = os.environ["DATABASE_URL"]
SCHEMA = os.environ.get("MAIN_DB_SCHEMA", "t_p89198250_telegram_vpn_bot_1")
XUI_URL = os.environ['XUI_URL'].rstrip('/').replace('https://', 'http://')
XUI_USERNAME = os.environ['XUI_USERNAME']
XUI_PASSWORD = os.environ['XUI_PASSWORD']
INBOUND_ID = 1


def get_db():
    return psycopg2.connect(DB_URL)


def send_message(chat_id, text, parse_mode="Markdown"):
    requests.post(f"{TELEGRAM_API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }, timeout=10)


def xui_login():
    session = requests.Session()
    resp = session.post(f"{XUI_URL}/login", data={"username": XUI_USERNAME, "password": XUI_PASSWORD}, timeout=10)
    if resp.status_code == 200 and resp.json().get("success"):
        return session
    return None


def xui_update_client_expiry(client_id: str, email: str, expires_ms: int):
    """Обновляет срок действия существующего клиента в 3x-ui."""
    session = xui_login()
    if not session:
        return "Ошибка авторизации"
    client = {
        "id": client_id,
        "flow": "xtls-rprx-vision",
        "email": email,
        "limitIp": 1,
        "totalGB": 0,
        "expiryTime": expires_ms,
        "enable": True,
        "tgId": "",
        "subId": "",
        "reset": 0
    }
    resp = session.post(
        f"{XUI_URL}/panel/api/inbounds/updateClient/{client_id}",
        json={"id": INBOUND_ID, "settings": json.dumps({"clients": [client]})},
        allow_redirects=True,
        timeout=10
    )
    print(f"[updateClient] status={resp.status_code} body={resp.text[:200]}")
    if resp.status_code != 200 or not resp.json().get("success"):
        return resp.text
    return None


def xui_create_client(label: str, expires_ms: int) -> tuple:
    """Создаёт нового клиента в 3x-ui, возвращает (client_id, vless_link, error)."""
    session = xui_login()
    if not session:
        return None, None, "Ошибка авторизации"

    client_id = str(uuid.uuid4())
    client = {
        "id": client_id,
        "flow": "xtls-rprx-vision",
        "email": label,
        "limitIp": 1,
        "totalGB": 0,
        "expiryTime": expires_ms,
        "enable": True,
        "tgId": "",
        "subId": str(uuid.uuid4())[:8],
        "reset": 0
    }
    resp = session.post(
        f"{XUI_URL}/panel/api/inbounds/addClient",
        json={"id": INBOUND_ID, "settings": json.dumps({"clients": [client]})},
        allow_redirects=True,
        timeout=10
    )
    print(f"[addClient] status={resp.status_code} body={resp.text[:300]}")
    if resp.status_code != 200 or not resp.json().get("success"):
        return None, None, resp.text

    inbound_resp = session.get(f"{XUI_URL}/panel/api/inbounds/get/{INBOUND_ID}", allow_redirects=True, timeout=10)
    if inbound_resp.status_code != 200 or not inbound_resp.json().get("success"):
        return None, None, "Не удалось получить данные inbound"

    inbound = inbound_resp.json().get("obj", {})
    stream_settings = json.loads(inbound.get("streamSettings", "{}"))
    reality_settings = stream_settings.get("realitySettings", {})
    server_names = reality_settings.get("serverNames", [""])
    public_key = reality_settings.get("settings", {}).get("publicKey", "")
    short_ids = reality_settings.get("shortIds", [""])

    host = XUI_URL.replace("http://", "").replace("https://", "").split(":")[0]
    port = inbound.get("port", 443)
    sni = server_names[0] if server_names else ""
    short_id = short_ids[0] if short_ids else ""

    vless_link = (
        f"vless://{client_id}@{host}:{port}"
        f"?type=tcp&security=reality&pbk={public_key}"
        f"&fp=chrome&sni={sni}&sid={short_id}&spx=%2F&flow=xtls-rprx-vision"
        f"#{label}"
    )
    return client_id, vless_link, None


def handle_payment_succeeded(user_id: int, payment_id: str, payment_method_id: str):
    """Активирует подписку и создаёт/продлевает ключ с учётом остатка пробного периода."""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    # Обновляем статус платежа
    cur.execute(
        f"UPDATE {SCHEMA}.payments SET status='succeeded', payment_method_id=%s, updated_at=NOW() WHERE yukassa_payment_id=%s",
        (payment_method_id, payment_id)
    )

    # Смотрим текущую подписку
    cur.execute(
        f"SELECT id, expires_at FROM {SCHEMA}.subscriptions WHERE user_id=%s AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    existing_sub = cur.fetchone()

    if existing_sub:
        # Продлеваем от текущей даты истечения (если ещё не истекла)
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

    # Смотрим все ключи пользователя
    cur.execute(
        f"SELECT id, client_id, name, expires_at FROM {SCHEMA}.user_keys WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,)
    )
    key_rows = cur.fetchall()

    # Смотрим имя пользователя
    cur.execute(f"SELECT name FROM {SCHEMA}.user_states WHERE user_id=%s", (user_id,))
    user_row = cur.fetchone()
    user_name = user_row[0] if user_row else "user"

    expires_ms = int(new_expires.timestamp() * 1000)

    if key_rows:
        # Обновляем все ключи пользователя до даты окончания подписки
        for key_id, client_id, key_name, key_expires in key_rows:
            err = xui_update_client_expiry(client_id, key_name, expires_ms)
            if err:
                print(f"[webhook] xui update error for key {key_id}: {err}")
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
        # Ключей нет — создаём новый
        label = f"sub_{user_name}_{user_id}"
        client_id, vless_link, error = xui_create_client(label, expires_ms)

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
            (user_id, client_id, "Подписка", vless_link, new_expires)
        )
        conn.commit()
        cur.close()
        conn.close()

        send_message(
            user_id,
            "✅ *Оплата прошла успешно!*\n\n"
            f"Подписка активирована до *{new_expires.strftime('%d.%m.%Y')}*.\n\n"
            f"🔑 Твой VLESS ключ:\n\n`{vless_link}`\n\n"
            "Скопируй и вставь в приложение для подключения.\n\n"
            "Карта сохранена — следующее списание автоматически через 30 дней.\n"
            "Для отмены: /cancel"
        )


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
    metadata = payment_obj.get("metadata", {})
    user_id = metadata.get("user_id")
    payment_method = payment_obj.get("payment_method", {})
    payment_method_id = payment_method.get("id") if payment_method.get("saved") else None

    if not payment_id or not user_id:
        return {"statusCode": 200, "headers": headers, "body": json.dumps({"ok": True})}

    user_id = int(user_id)

    if event_type == "payment.succeeded":
        handle_payment_succeeded(user_id, payment_id, payment_method_id)

    elif event_type == "payment.canceled":
        conn = get_db()
        cur = conn.cursor()
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
        shop_id = os.environ["YUKASSA_SHOP_ID"]
        api_key = os.environ["YUKASSA_API_KEY"]
        requests.post(
            f"https://api.yookassa.ru/v3/payments/{payment_id}/capture",
            auth=(shop_id, api_key),
            json={"amount": payment_obj.get("amount")},
            timeout=10
        )

    return {"statusCode": 200, "headers": headers, "body": json.dumps({"ok": True})}