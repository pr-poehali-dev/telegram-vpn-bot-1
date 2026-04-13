"""
Telegram бот для выдачи VPN ключей через панель 3x-ui.
Сценарий: пользователь вводит имя → нажимает "Получить ключ" → бот создаёт клиента в 3x-ui и отправляет VLESS ссылку.
"""

import os
import json
import uuid
import requests

BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
XUI_URL = os.environ['XUI_URL'].rstrip('/')
XUI_USERNAME = os.environ['XUI_USERNAME']
XUI_PASSWORD = os.environ['XUI_PASSWORD']
INBOUND_ID = 10

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Состояния пользователей (в памяти, сбрасывается при перезапуске)
user_states = {}

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
    if resp.status_code == 200 and resp.json().get("success"):
        return session
    return None

def create_vless_client(name: str):
    session = xui_login()
    if not session:
        return None, "Ошибка подключения к панели"

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

    if resp.status_code != 200:
        return None, f"Ошибка API панели: {resp.status_code}"

    data = resp.json()
    if not data.get("success"):
        msg = data.get("msg", "Неизвестная ошибка")
        return None, f"Панель вернула ошибку: {msg}"

    # Получаем данные inbound для формирования ссылки
    inbound_resp = session.get(f"{XUI_URL}/xui/inbound/get/{INBOUND_ID}", timeout=10)
    if inbound_resp.status_code != 200 or not inbound_resp.json().get("success"):
        return client_id, None

    inbound = inbound_resp.json().get("obj", {})
    stream_settings = json.loads(inbound.get("streamSettings", "{}"))
    reality_settings = stream_settings.get("realitySettings", {})
    server_names = reality_settings.get("serverNames", [""])
    public_key = reality_settings.get("settings", {}).get("publicKey", "")
    short_ids = reality_settings.get("shortIds", [""])
    fp = stream_settings.get("tlsSettings", {}).get("fingerprint", "chrome")

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
            state = user_states.get(user_id, {})
            name = state.get("name", "")
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
            user_states.pop(user_id, None)
        return

    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":
        user_states[user_id] = {"step": "ask_name"}
        send_message(
            chat_id,
            "👋 *Добро пожаловать в систему выдачи ключей*\n\n"
            "Пожалуйста, введите ваше имя для идентификации:"
        )
        return

    state = user_states.get(user_id, {})

    if state.get("step") == "ask_name":
        name = text
        user_states[user_id] = {"step": "confirm", "name": name}

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
        return {"statusCode": 200, "headers": headers, "body": {"ok": True, "error": str(e)}}

    return {"statusCode": 200, "headers": headers, "body": {"ok": True}}