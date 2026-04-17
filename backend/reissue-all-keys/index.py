"""
Перевыпуск всех ключей пользователей: удаляет старые ключи в 3x-ui и создаёт новые
с сохранением сроков действия. Отправляет уведомление каждому пользователю.
Доступно только для администраторов.
"""

import os
import json
import uuid
import requests
import psycopg2

BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
_raw_url = os.environ['XUI_URL'].rstrip('/').replace('https://', 'http://')
XUI_URL = _raw_url
XUI_USERNAME = os.environ['XUI_USERNAME']
XUI_PASSWORD = os.environ['XUI_PASSWORD']
INBOUND_ID = 1
DB_SCHEMA = os.environ.get('MAIN_DB_SCHEMA', 't_p89198250_telegram_vpn_bot_1')
ADMIN_USERNAMES = {'btb75', 'makarevichas'}
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def get_db():
    return psycopg2.connect(os.environ['DATABASE_URL'])


def xui_login():
    session = requests.Session()
    resp = session.post(
        f"{XUI_URL}/login",
        data={"username": XUI_USERNAME, "password": XUI_PASSWORD},
        allow_redirects=True,
        timeout=10
    )
    if resp.status_code != 200 or not resp.json().get("success"):
        return None
    return session


def xui_delete_client(session, client_id: str):
    resp = session.post(
        f"{XUI_URL}/panel/api/inbounds/{INBOUND_ID}/delClient/{client_id}",
        allow_redirects=True,
        timeout=10
    )
    print(f"[delClient] {client_id} status={resp.status_code}")
    return resp.status_code == 200 and resp.json().get("success")


def xui_create_client(session, label: str, expires_ms: int) -> tuple:
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
    print(f"[addClient] {label} status={resp.status_code} body={resp.text[:200]}")
    if resp.status_code != 200 or not resp.json().get("success"):
        return None, None, resp.text

    inbound_resp = session.get(
        f"{XUI_URL}/panel/api/inbounds/get/{INBOUND_ID}",
        allow_redirects=True,
        timeout=10
    )
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


def send_telegram(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10
    )


def handler(event: dict, context) -> dict:
    """Перевыпускает все ключи всех пользователей и отправляет уведомления."""
    headers = {"Access-Control-Allow-Origin": "*"}

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": {**headers, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, X-Admin-Token"}, "body": ""}

    if event.get("httpMethod") != "POST":
        return {"statusCode": 405, "headers": headers, "body": json.dumps({"error": "Method not allowed"})}

    body = json.loads(event.get("body") or "{}")
    admin_token = body.get("admin_token") or event.get("headers", {}).get("X-Admin-Token", "")

    if admin_token != os.environ.get("ADMIN_SECRET_TOKEN", ""):
        return {"statusCode": 403, "headers": headers, "body": json.dumps({"error": "Forbidden"})}

    conn = get_db()
    cur = conn.cursor()

    cur.execute(f"SELECT DISTINCT user_id FROM {DB_SCHEMA}.user_keys")
    user_ids = [row[0] for row in cur.fetchall()]

    session = xui_login()
    if not session:
        cur.close()
        conn.close()
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": "XUI login failed"})}

    results = {"success": 0, "failed": 0, "users": []}

    for user_id in user_ids:
        cur.execute(
            f"SELECT id, client_id, name, expires_at FROM {DB_SCHEMA}.user_keys WHERE user_id=%s",
            (user_id,)
        )
        keys = cur.fetchall()
        user_result = {"user_id": user_id, "keys_reissued": 0, "errors": []}

        for key_id, old_client_id, key_name, expires_at in keys:
            expires_ms = int(expires_at.timestamp() * 1000) if expires_at else 0

            label = f"sub_{user_id}_{key_id}"
            new_client_id, new_vless_link, error = xui_create_client(session, label, expires_ms)

            if error:
                user_result["errors"].append(f"key {key_id}: {error}")
                continue

            xui_delete_client(session, old_client_id)

            cur.execute(
                f"UPDATE {DB_SCHEMA}.user_keys SET client_id=%s, vless_link=%s WHERE id=%s",
                (new_client_id, new_vless_link, key_id)
            )
            user_result["keys_reissued"] += 1

        conn.commit()

        if user_result["keys_reissued"] > 0:
            keys_word = "ключ" if user_result["keys_reissued"] == 1 else ("ключа" if user_result["keys_reissued"] < 5 else "ключей")
            send_telegram(
                user_id,
                "🔄 *Обновление сервера*\n\n"
                "Мы модернизировали техническую базу и улучшили соединение.\n\n"
                f"Тебе выпущен новый ключ — старый больше не работает.\n\n"
                "📲 *Что нужно сделать:*\n"
                "1. Открой бот и нажми *«Мои ключи»*\n"
                "2. Скопируй новый ключ\n"
                "3. Удали старый из приложения и добавь новый\n\n"
                "Если что-то непонятно — нажми кнопку *«Инструкция»* рядом с ключом.\n\n"
                "Приносим извинения за неудобства 🙏"
            )
            results["success"] += 1
        else:
            results["failed"] += 1

        results["users"].append(user_result)

    cur.close()
    conn.close()

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({
            "ok": True,
            "users_processed": len(user_ids),
            "success": results["success"],
            "failed": results["failed"],
            "detail": results["users"]
        })
    }
