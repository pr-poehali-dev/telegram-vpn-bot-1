"""
Telegram бот для выдачи VPN ключей через панель 3x-ui.
Сценарий: пользователь вводит имя → нажимает "Получить ключ" → бот создаёт клиента в 3x-ui и отправляет VLESS ссылку.
"""

import os
import json
import uuid
import requests
import psycopg2

BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
XUI_URL = os.environ['XUI_URL'].rstrip('/')
XUI_USERNAME = os.environ['XUI_USERNAME']
XUI_PASSWORD = os.environ['XUI_PASSWORD']
INBOUND_ID = 10
DB_SCHEMA = os.environ.get('MAIN_DB_SCHEMA', 't_p89198250_telegram_vpn_bot_1')

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def get_db():
    return psycopg2.connect(os.environ['DATABASE_URL'])


def get_state(user_id: int) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT step, name FROM {DB_SCHEMA}.user_states WHERE user_id = {user_id}")
        row = cur.fetchone()
        if row:
            return {"step": row[0], "name": row[1]}
        return {}
    finally:
        conn.close()


def set_state(user_id: int, step: str, name: str = ""):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO {DB_SCHEMA}.user_states (user_id, step, name, updated_at)
            VALUES ({user_id}, '{step}', '{name.replace("'", "''")}', NOW())
            ON CONFLICT (user_id) DO UPDATE SET step = EXCLUDED.step, name = EXCLUDED.name, updated_at = NOW()
        """)
        conn.commit()
    finally:
        conn.close()


def clear_state(user_id: int):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {DB_SCHEMA}.user_states WHERE user_id = {user_id}")
        conn.commit()
    finally:
        conn.close()


def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def xui_login():
    session = requests.Session()
    resp = session.post(
        f"{XUI_URL}/login",
        data={"username": XUI_USERNAME, "password": XUI_PASSWORD},
        timeout=10
    )
    print(f"[xui_login] status={resp.status_code} body={resp.text[:200]}")
    if resp.status_code == 200 and resp.json().get("success"):
        return session
    return None


def create_vless_client(name: str):
    session = xui_login()
    if not session:
        return None, "Ошибка авторизации в панели 3x-ui"

    client_id = str(uuid.uuid4())
    client = {
        "id": client_id,
        "flow": "xtls-rprx-vision",
        "email": name,
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": "",
        "subId": str(uuid.uuid4())[:8],
        "reset": 0
    }

    resp = session.post(
        f"{XUI_URL}/xui/inbound/addClient",
        json={"id": INBOUND_ID, "settings": json.dumps({"clients": [client]})},
        timeout=10
    )
    print(f"[addClient] status={resp.status_code} body={resp.text[:300]}")

    if resp.status_code != 200:
        return None, f"Ошибка API панели: {resp.status_code}"

    data = resp.json()
    if not data.get("success"):
        msg = data.get("msg", "Неизвестная ошибка")
        return None, f"Панель вернула ошибку: {msg}"

    # Получаем данные inbound для формирования ссылки
    inbound_resp = session.get(f"{XUI_URL}/xui/inbound/get/{INBOUND_ID}", timeout=10)
    print(f"[getInbound] status={inbound_resp.status_code} body={inbound_resp.text[:300]}")

    if inbound_resp.status_code != 200 or not inbound_resp.json().get("success"):
        return None, "Не удалось получить данные inbound"

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
        f"#{name}"
    )

    return vless_link, None


def handle_update(update: dict):
    message = update.get("message", {})
    callback = update.get("callback_query", {})

    if callback:
        chat_id = callback["message"]["chat"]["id"]
        data = callback.get("data", "")
        user_id = callback["from"]["id"]

        requests.post(f"{TELEGRAM_API}/answerCallbackQuery",
                      json={"callback_query_id": callback["id"]}, timeout=5)

        if data == "get_key":
            state = get_state(user_id)
            name = state.get("name", "")
            print(f"[get_key] user_id={user_id} state={state}")

            if not name:
                send_message(chat_id, "Сначала укажите ваше имя командой /start")
                return

            send_message(chat_id, "⏳ Создаю ключ доступа, подождите...")

            vless_link, error = create_vless_client(name)
            if error:
                send_message(chat_id, f"❌ Не удалось создать ключ: {error}")
                return

            text = (
                f"✅ *Ключ доступа выдан*\n\n"
                f"👤 Пользователь: `{name}`\n\n"
                f"🔑 Ваш VLESS ключ:\n\n"
                f"`{vless_link}`\n\n"
                f"Скопируйте ключ и вставьте его в приложение для подключения."
            )
            send_message(chat_id, text)
            clear_state(user_id)
        return

    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":
        set_state(user_id, "ask_name", "")
        send_message(
            chat_id,
            "👋 *Добро пожаловать в систему выдачи ключей*\n\n"
            "Пожалуйста, введите ваше имя для идентификации:"
        )
        return

    state = get_state(user_id)
    print(f"[message] user_id={user_id} step={state.get('step')} text={text}")

    if state.get("step") == "ask_name":
        name = text
        set_state(user_id, "confirm", name)

        keyboard = {
            "inline_keyboard": [[
                {"text": "🔑 Получить ключ", "callback_data": "get_key"}
            ]]
        }
        send_message(
            chat_id,
            f"✅ Имя принято: *{name}*\n\n"
            f"Нажмите кнопку ниже, чтобы получить ключ доступа:",
            reply_markup=keyboard
        )
        return

    send_message(
        chat_id,
        "Введите /start чтобы начать получение ключа."
    )


def handler(event, context) -> dict:
    """Обработчик webhook от Telegram."""
    headers = {"Access-Control-Allow-Origin": "*"}

    if isinstance(event, str):
        try:
            event = json.loads(event)
        except Exception:
            event = {}

    if event.get("httpMethod") == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {
                **headers,
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
            "body": ""
        }

    try:
        raw_body = event.get("body", "{}")
        if isinstance(raw_body, str):
            body = json.loads(raw_body) if raw_body else {}
        else:
            body = raw_body or {}
        handle_update(body)
    except Exception as e:
        print(f"[handler ERROR] {e}")
        return {"statusCode": 200, "headers": headers, "body": {"ok": True, "error": str(e)}}

    return {"statusCode": 200, "headers": headers, "body": {"ok": True}}
