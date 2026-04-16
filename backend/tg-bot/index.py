"""
Telegram VPN бот с личным кабинетом.
Сценарий: /start → регистрация имени (один раз) → главное меню (профиль, ключи, создать, удалить).
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
YUKASSA_SHOP_ID = os.environ.get('YUKASSA_SHOP_ID', '1327149')
YUKASSA_API_KEY = os.environ.get('YUKASSA_API_KEY', '')

print(f"[init] XUI_URL={XUI_URL}")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def setup_bot():
    """Устанавливает имя, описание и команды бота при первом запуске."""
    requests.post(f"{TELEGRAM_API}/setMyName", json={"name": "RossoVPN"}, timeout=10)
    requests.post(f"{TELEGRAM_API}/setMyDescription", json={
        "description": (
            "🔒 RossoVPN — быстрый и надёжный VPN-сервис.\n\n"
            "✅ Безлимитный трафик\n"
            "✅ Высокая скорость\n"
            "✅ 199 ₽/месяц\n\n"
            "Поддержка: @btb75, @makarevichas"
        )
    }, timeout=10)
    requests.post(f"{TELEGRAM_API}/setMyShortDescription", json={
        "short_description": "Быстрый VPN — 199 ₽/месяц. Поддержка: @btb75"
    }, timeout=10)
    requests.post(f"{TELEGRAM_API}/setMyCommands", json={"commands": [
        {"command": "start",   "description": "Личный кабинет"},
        {"command": "offer",   "description": "Публичная оферта"},
        {"command": "refund",  "description": "Условия возврата"},
        {"command": "support", "description": "Связаться с поддержкой"},
    ]}, timeout=10)
    print("[setup_bot] done")


setup_bot()


# ── БД ──────────────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(os.environ['DATABASE_URL'])


def get_user(user_id: int) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT step, name, tg_username, tg_first_name, trial_used FROM {DB_SCHEMA}.user_states WHERE user_id = {user_id}"
        )
        row = cur.fetchone()
        if row:
            return {"step": row[0], "name": row[1], "tg_username": row[2], "tg_first_name": row[3], "trial_used": row[4]}
        return {}
    finally:
        conn.close()


def upsert_user(user_id: int, step: str, name: str = "", tg_username: str = "", tg_first_name: str = ""):
    conn = get_db()
    try:
        cur = conn.cursor()
        name_s = name.replace("'", "''")
        tg_u = tg_username.replace("'", "''")
        tg_f = tg_first_name.replace("'", "''")
        cur.execute(f"""
            INSERT INTO {DB_SCHEMA}.user_states (user_id, step, name, tg_username, tg_first_name, updated_at)
            VALUES ({user_id}, '{step}', '{name_s}', '{tg_u}', '{tg_f}', NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET step = EXCLUDED.step,
                name = CASE WHEN EXCLUDED.name = '' THEN {DB_SCHEMA}.user_states.name ELSE EXCLUDED.name END,
                tg_username = EXCLUDED.tg_username,
                tg_first_name = EXCLUDED.tg_first_name,
                updated_at = NOW()
        """)
        conn.commit()
    finally:
        conn.close()


def set_step(user_id: int, step: str):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {DB_SCHEMA}.user_states SET step = '{step}', updated_at = NOW() WHERE user_id = {user_id}"
        )
        conn.commit()
    finally:
        conn.close()


def save_key(user_id: int, client_id: str, name: str, vless_link: str, expires_at=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        name_s = name.replace("'", "''")
        link_s = vless_link.replace("'", "''")
        cid_s = client_id.replace("'", "''")
        if expires_at:
            cur.execute(f"""
                INSERT INTO {DB_SCHEMA}.user_keys (user_id, client_id, name, vless_link, created_at, expires_at)
                VALUES ({user_id}, '{cid_s}', '{name_s}', '{link_s}', NOW(), '{expires_at}')
            """)
        else:
            cur.execute(f"""
                INSERT INTO {DB_SCHEMA}.user_keys (user_id, client_id, name, vless_link, created_at)
                VALUES ({user_id}, '{cid_s}', '{name_s}', '{link_s}', NOW())
            """)
        conn.commit()
    finally:
        conn.close()


def get_keys(user_id: int) -> list:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, client_id, name, vless_link, created_at, expires_at FROM {DB_SCHEMA}.user_keys WHERE user_id = {user_id} ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
        return [{"id": r[0], "client_id": r[1], "name": r[2], "vless_link": r[3], "created_at": r[4], "expires_at": r[5]} for r in rows]
    finally:
        conn.close()


def delete_key_by_id(key_id: int, user_id: int) -> dict | None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT client_id, name FROM {DB_SCHEMA}.user_keys WHERE id = {key_id} AND user_id = {user_id}"
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(f"DELETE FROM {DB_SCHEMA}.user_keys WHERE id = {key_id}")
        conn.commit()
        return {"client_id": row[0], "name": row[1]}
    finally:
        conn.close()


# ── Telegram ─────────────────────────────────────────────────────────────────

def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{TELEGRAM_API}/editMessageText", json=payload, timeout=10)


def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload, timeout=5)


# ── 3x-ui ─────────────────────────────────────────────────────────────────────

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


def xui_create_client(label: str, expires_ms: int = 0) -> tuple:
    """Создаёт клиента в панели, возвращает (client_id, vless_link) или (None, error)."""
    session = xui_login()
    if not session:
        return None, None, "Ошибка авторизации в панели 3x-ui"

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

    if resp.status_code != 200:
        return None, None, f"Ошибка API панели: {resp.status_code}"

    data = resp.json()
    if not data.get("success"):
        return None, None, f"Панель вернула ошибку: {data.get('msg', '?')}"

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


def xui_delete_client(client_id: str) -> str | None:
    """Удаляет клиента из панели. Возвращает None при успехе или строку с ошибкой."""
    session = xui_login()
    if not session:
        return "Ошибка авторизации в панели"

    resp = session.post(
        f"{XUI_URL}/panel/api/inbounds/{INBOUND_ID}/delClient/{client_id}",
        allow_redirects=True,
        timeout=10
    )
    print(f"[delClient] status={resp.status_code} body={resp.text[:200]}")

    if resp.status_code != 200:
        return f"Ошибка API: {resp.status_code}"

    data = resp.json()
    if not data.get("success"):
        return data.get("msg", "Ошибка удаления")

    return None


# ── Меню ─────────────────────────────────────────────────────────────────────

def send_trial_menu(chat_id, name: str):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎁 Получить пробный ключ на 7 дней", "callback_data": "get_trial"}],
            [{"text": "🛟 Поддержка", "callback_data": "support"}],
        ]
    }
    send_message(
        chat_id,
        f"👋 Привет, *{name}*! Добро пожаловать в RossoVPN.\n\n"
        "Попробуй VPN бесплатно — *7 дней без ограничений*.\n\n"
        "Нажми кнопку ниже, чтобы получить пробный ключ 👇",
        reply_markup=keyboard
    )


def send_main_menu(chat_id, user: dict, user_id: int = None):
    name = user.get("name", "—")
    rows = [
        [{"text": "👤 Мой профиль", "callback_data": "profile"}],
    ]
    if user_id:
        keys = get_keys(user_id)
        if keys:
            rows.append([{"text": "🔑 Показать мой ключ", "callback_data": f"key_{keys[0]['id']}"}])
    rows.append([{"text": "💳 Оформить подписку — 199 ₽/мес", "callback_data": "subscribe"}])
    rows.append([{"text": "➕ Создать новый ключ", "callback_data": "create_key"}])
    rows.append([{"text": "🛟 Поддержка", "callback_data": "support"}])
    keyboard = {"inline_keyboard": rows}
    send_message(
        chat_id,
        f"👋 Привет, *{name}*! Это твой личный кабинет VPN.\n\nВыбери действие:",
        reply_markup=keyboard
    )


def send_keys_list(chat_id, user_id: int, edit=False, message_id=None):
    keys = get_keys(user_id)
    if not keys:
        text = "У тебя пока нет ключей.\nНажми *➕ Создать новый ключ* в главном меню."
        keyboard = {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]}
        if edit and message_id:
            edit_message(chat_id, message_id, text, reply_markup=keyboard)
        else:
            send_message(chat_id, text, reply_markup=keyboard)
        return

    rows = []
    for k in keys:
        date = k["created_at"].strftime("%d.%m.%Y") if k["created_at"] else "—"
        rows.append([{"text": f"🔑 {k['name']} • {date}", "callback_data": f"key_{k['id']}"}])
    rows.append([{"text": "◀️ Назад", "callback_data": "main_menu"}])

    text = f"🔑 *Твои ключи* ({len(keys)} шт.):\n\nНажми на ключ чтобы посмотреть или удалить:"
    keyboard = {"inline_keyboard": rows}

    if edit and message_id:
        edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        send_message(chat_id, text, reply_markup=keyboard)


def send_key_detail(chat_id, message_id, key: dict):
    date = key["created_at"].strftime("%d.%m.%Y %H:%M") if key["created_at"] else "—"
    if key.get("expires_at"):
        expires_str = key["expires_at"].strftime("%d.%m.%Y")
        validity = f"до *{expires_str}*"
    else:
        validity = "*бессрочно*"
    text = (
        f"🔑 *Ключ: {key['name']}*\n\n"
        f"📅 Создан: {date}\n"
        f"⏳ Действует: {validity}\n\n"
        f"`{key['vless_link']}`"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "🗑 Удалить этот ключ", "callback_data": f"del_{key['id']}"}],
            [{"text": "◀️ К списку ключей", "callback_data": "my_keys"}],
        ]
    }
    edit_message(chat_id, message_id, text, reply_markup=keyboard)


# ── Обработчик ───────────────────────────────────────────────────────────────

def handle_update(update: dict):
    print(f"[handle_update] keys={list(update.keys())}")

    # ── Callback ──
    callback = update.get("callback_query", {})
    if callback:
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        data = callback.get("data", "")
        user_id = callback["from"]["id"]
        answer_callback(callback["id"])

        user = get_user(user_id)

        if data == "get_trial":
            if user.get("trial_used"):
                answer_callback(callback["id"], "Пробный ключ уже был использован", show_alert=True)
            else:
                send_message(chat_id, "⏳ Создаю пробный ключ на 7 дней, подождите...")
                user_name = user.get("name", "user")
                full_label = f"trial_{user_name}_{user_id}"
                import time
                from datetime import datetime, timedelta
                expires_dt = datetime.utcnow() + timedelta(days=7)
                expires_ms = int(expires_dt.timestamp() * 1000)
                client_id, vless_link, error = xui_create_client(full_label, expires_ms)
                if error:
                    send_message(chat_id, f"❌ Не удалось создать ключ: {error}\nНапиши в поддержку: @btb75")
                else:
                    save_key(user_id, client_id, "Пробный (7 дней)", vless_link, expires_at=expires_dt)
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute(f"UPDATE {DB_SCHEMA}.user_states SET trial_used=TRUE WHERE user_id={user_id}")
                    conn.commit()
                    cur.close()
                    conn.close()
                    send_message(
                        chat_id,
                        "🎁 *Пробный ключ активирован на 7 дней!*\n\n"
                        f"🔑 Твой VLESS ключ:\n\n`{vless_link}`\n\n"
                        "Скопируй и вставь в приложение для подключения.\n\n"
                        "После пробного периода оформи подписку — *199 ₽/месяц*."
                    )
                    send_main_menu(chat_id, user, user_id)

        elif data == "main_menu":
            set_step(user_id, "menu")
            send_main_menu(chat_id, user, user_id)

        elif data == "profile":
            name = user.get("name", "—")
            tg_u = user.get("tg_username", "")
            keys = get_keys(user_id)
            tg_line = f"@{tg_u}" if tg_u else "не указан"

            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                f"SELECT status, expires_at FROM {DB_SCHEMA}.subscriptions WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            sub = cur.fetchone()
            cur.close()
            conn.close()

            if sub and sub[0] == "active" and sub[1]:
                expires = sub[1].strftime("%d.%m.%Y")
                sub_line = f"💳 Активна до *{expires}*"
            elif sub and sub[0] == "cancelled":
                sub_line = "🔕 Отменена"
            elif sub and sub[0] == "expired":
                sub_line = "❌ Истекла"
            else:
                sub_line = "Нет активной подписки"

            text = (
                f"👤 *Профиль*\n\n"
                f"Имя: *{name}*\n"
                f"Telegram: {tg_line}\n"
                f"Ключей: *{len(keys)}*\n"
                f"Подписка: {sub_line}"
            )
            kb_rows = [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]
            if not sub or sub[0] != "active":
                kb_rows.insert(0, [{"text": "💳 Оформить подписку — 199 ₽/мес", "callback_data": "subscribe"}])
            keyboard = {"inline_keyboard": kb_rows}
            edit_message(chat_id, message_id, text, reply_markup=keyboard)

        elif data == "my_keys":
            send_keys_list(chat_id, user_id, edit=True, message_id=message_id)

        elif data == "create_key":
            keys = get_keys(user_id)
            if keys:
                old = keys[0]
                date = old["created_at"].strftime("%d.%m.%Y") if old["created_at"] else "—"
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "✅ Да, удалить старый и создать новый", "callback_data": f"replace_key_{old['id']}"}],
                        [{"text": "◀️ Отмена", "callback_data": "main_menu"}],
                    ]
                }
                edit_message(
                    chat_id, message_id,
                    f"⚠️ *У тебя уже есть ключ*\n\n"
                    f"🔑 «{old['name']}» (создан {date})\n\n"
                    f"Он будет *отключён и удалён*. Продолжить?",
                    reply_markup=keyboard
                )
            else:
                set_step(user_id, "creating_key")
                keyboard = {"inline_keyboard": [[{"text": "◀️ Отмена", "callback_data": "main_menu"}]]}
                edit_message(chat_id, message_id, "✏️ Введи название для нового ключа (например: *Телефон*, *Ноутбук*):", reply_markup=keyboard)

        elif data == "cancel_sub_do":
            answer_callback(callback["id"], "⏳ Обрабатываем запрос...", show_alert=False)
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, payment_method_id FROM {DB_SCHEMA}.subscriptions WHERE user_id=%s AND status='active' ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            sub = cur.fetchone()
            if sub and YUKASSA_API_KEY:
                sub_id, payment_method_id = sub
                resp = requests.post(
                    f"https://api.yookassa.ru/v3/recurring-payments/{payment_method_id}/cancel",
                    auth=(YUKASSA_SHOP_ID, YUKASSA_API_KEY),
                    timeout=10
                )
                cur.execute(
                    f"UPDATE {DB_SCHEMA}.subscriptions SET status='cancelled', cancelled_at=NOW(), updated_at=NOW() WHERE id=%s",
                    (sub_id,)
                )
                conn.commit()
                send_message(
                    chat_id,
                    "✅ *Подписка отменена*\n\n"
                    "Автопродление отключено — больше списаний не будет.\n"
                    "Доступ к VPN сохранится до конца оплаченного периода."
                )
            elif sub:
                cur.execute(
                    f"UPDATE {DB_SCHEMA}.subscriptions SET status='cancelled', cancelled_at=NOW(), updated_at=NOW() WHERE id=%s",
                    (sub[0],)
                )
                conn.commit()
                send_message(chat_id, "✅ Подписка отменена. Списаний больше не будет.")
            else:
                send_message(chat_id, "У тебя нет активной подписки.")
            cur.close()
            conn.close()

        elif data == "subscribe":
            if not YUKASSA_API_KEY:
                edit_message(
                    chat_id, message_id,
                    "⏳ *Оплата временно недоступна*\n\nПожалуйста, попробуй позже или напиши в поддержку: @btb75",
                    reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]}
                )
            else:
                idempotency_key = str(uuid.uuid4())
                payment_payload = {
                    "amount": {"value": "199.00", "currency": "RUB"},
                    "confirmation": {"type": "redirect", "return_url": "https://t.me/RossoVPN_bot"},
                    "capture": True,
                    "save_payment_method": True,
                    "description": f"Подписка RossoVPN — 30 дней (user {user_id})",
                    "metadata": {"user_id": str(user_id)}
                }
                resp = requests.post(
                    "https://api.yookassa.ru/v3/payments",
                    auth=(YUKASSA_SHOP_ID, YUKASSA_API_KEY),
                    json=payment_payload,
                    headers={"Idempotence-Key": idempotency_key},
                    timeout=15
                )
                pay_data = resp.json()
                pay_id = pay_data.get("id")
                pay_url = pay_data.get("confirmation", {}).get("confirmation_url")

                if pay_id and pay_url:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute(
                        f"INSERT INTO {DB_SCHEMA}.payments (user_id, yukassa_payment_id, amount, status) VALUES (%s, %s, 199.00, 'pending')",
                        (user_id, pay_id)
                    )
                    conn.commit()
                    cur.close()
                    conn.close()

                    keyboard = {
                        "inline_keyboard": [
                            [{"text": "💳 Перейти к оплате", "url": pay_url}],
                            [{"text": "📄 Читать оферту", "url": "https://telegra.ph/Publichnaya-oferta-RossoVPN-04-14"}],
                            [{"text": "◀️ Назад", "callback_data": "main_menu"}],
                        ]
                    }
                    edit_message(
                        chat_id, message_id,
                        "💳 *Оформление подписки*\n\n"
                        "Тариф: *Базовый — 199 ₽/месяц*\n"
                        "✅ Безлимитный трафик\n"
                        "✅ Высокая скорость\n"
                        "✅ Автопродление каждые 30 дней\n\n"
                        "📌 После оплаты карта сохранится — следующие списания будут автоматическими.\n"
                        "Отключить в любой момент: /cancel\n\n"
                        "Нажимая «Перейти к оплате», ты принимаешь условия публичной оферты 👇",
                        reply_markup=keyboard
                    )
                else:
                    edit_message(
                        chat_id, message_id,
                        "❌ Не удалось создать платёж. Попробуй позже или напиши в поддержку: @btb75",
                        reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]}
                    )

        elif data == "support":
            keyboard = {
                "inline_keyboard": [
                    [{"text": "👤 Написать @btb75", "url": "https://t.me/btb75"}],
                    [{"text": "👤 Написать @makarevichas", "url": "https://t.me/makarevichas"}],
                    [{"text": "◀️ Назад", "callback_data": "main_menu"}],
                ]
            }
            edit_message(
                chat_id, message_id,
                "🛟 *Поддержка RossoVPN*\n\n"
                "По любым вопросам — подключение, оплата, возврат или что-то пошло не так — наши специалисты всегда на связи.\n\n"
                "⏱ Среднее время ответа: *до 2 часов*\n\n"
                "Выбери удобного специалиста 👇",
                reply_markup=keyboard
            )

        elif data.startswith("replace_key_"):
            old_id = int(data.split("_", 2)[2])
            key_info = delete_key_by_id(old_id, user_id)
            if key_info:
                xui_delete_client(key_info["client_id"])
            set_step(user_id, "creating_key")
            keyboard = {"inline_keyboard": [[{"text": "◀️ Отмена", "callback_data": "main_menu"}]]}
            edit_message(chat_id, message_id, "✏️ Старый ключ удалён. Введи название для нового ключа (например: *Телефон*, *Ноутбук*):", reply_markup=keyboard)

        elif data.startswith("key_"):
            key_id = int(data.split("_", 1)[1])
            keys = get_keys(user_id)
            key = next((k for k in keys if k["id"] == key_id), None)
            if key:
                send_key_detail(chat_id, message_id, key)
            else:
                edit_message(chat_id, message_id, "Ключ не найден.")

        elif data.startswith("del_"):
            key_id = int(data.split("_", 1)[1])
            keyboard = {
                "inline_keyboard": [
                    [{"text": "✅ Да, удалить", "callback_data": f"confirm_del_{key_id}"}],
                    [{"text": "◀️ Отмена", "callback_data": f"key_{key_id}"}],
                ]
            }
            edit_message(chat_id, message_id, "⚠️ Ты уверен? Ключ будет удалён и перестанет работать.", reply_markup=keyboard)

        elif data.startswith("confirm_del_"):
            key_id = int(data.split("_", 2)[2])
            key_info = delete_key_by_id(key_id, user_id)
            if not key_info:
                edit_message(chat_id, message_id, "Ключ не найден или уже удалён.")
                return

            err = xui_delete_client(key_info["client_id"])
            if err:
                print(f"[del_client] xui error: {err}")

            set_step(user_id, "menu")
            send_keys_list(chat_id, user_id, edit=True, message_id=message_id)

        return

    # ── Message ──
    message = update.get("message", {})
    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()
    tg_username = message["from"].get("username", "")
    tg_first_name = message["from"].get("first_name", "")

    user = get_user(user_id)

    if text == "/start":
        if user.get("name"):
            upsert_user(user_id, "menu", user["name"], tg_username, tg_first_name)
            if not user.get("trial_used"):
                send_trial_menu(chat_id, user["name"])
            else:
                send_main_menu(chat_id, user, user_id)
        else:
            upsert_user(user_id, "ask_name", "", tg_username, tg_first_name)
            send_message(
                chat_id,
                "👋 *Добро пожаловать в RossoVPN!*\n\n"
                "Быстрый и надёжный VPN — *199 ₽/месяц*.\n\n"
                "Введи своё имя для регистрации:"
            )
        return

    if text == "/offer":
        keyboard = {
            "inline_keyboard": [
                [{"text": "📄 Читать полную оферту", "url": "https://telegra.ph/Publichnaya-oferta-RossoVPN-04-14"}],
            ]
        }
        send_message(
            chat_id,
            "📄 *Публичная оферта RossoVPN*\n\n"
            "Тариф: *199 ₽/месяц* — безлимитный трафик, высокая скорость, протокол VLESS Reality.\n\n"
            "📌 *Автоплатежи:*\n"
            "— Списание раз в 30 дней\n"
            "— Уведомление за 3 дня до списания\n"
            "— Отключение: команда /cancel или через поддержку\n\n"
            "💸 *Возврат:* в течение 7 дней с момента оплаты\n\n"
            "Полный текст оферты — по кнопке ниже 👇",
            reply_markup=keyboard
        )
        return

    if text == "/refund":
        send_message(
            chat_id,
            "💸 *Условия возврата*\n\n"
            "Мы принимаем заявки на возврат в течение *7 дней* с момента оплаты.\n\n"
            "Для оформления возврата обратитесь в поддержку:\n"
            "👤 @btb75\n"
            "👤 @makarevichas\n\n"
            "Укажи свой Telegram и дату оплаты — вернём деньги в течение 3 рабочих дней."
        )
        return

    if text == "/cancel":
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔕 Отменить подписку", "callback_data": "cancel_sub_do"}],
            ]
        }
        send_message(
            chat_id,
            "🔕 *Отмена подписки*\n\n"
            "Нажми кнопку ниже — подписка будет автоматически отменена, а ЮКасса прекратит списания.\n\n"
            "После отмены доступ сохранится до конца оплаченного периода.\n\n"
            "Поддержка: @btb75, @makarevichas",
            reply_markup=keyboard
        )
        return

    if text == "/support":
        keyboard = {
            "inline_keyboard": [
                [{"text": "👤 Написать @btb75", "url": "https://t.me/btb75"}],
                [{"text": "👤 Написать @makarevichas", "url": "https://t.me/makarevichas"}],
            ]
        }
        send_message(
            chat_id,
            "🛟 *Поддержка RossoVPN*\n\n"
            "По любым вопросам — подключение, оплата, возврат или что-то пошло не так — наши специалисты всегда на связи.\n\n"
            "⏱ Среднее время ответа: *до 2 часов*\n\n"
            "Выбери удобного специалиста 👇",
            reply_markup=keyboard
        )
        return

    step = user.get("step", "")
    print(f"[message] user_id={user_id} step={step} text={text[:50]}")

    if step == "ask_name":
        name = text[:50]
        upsert_user(user_id, "menu", name, tg_username, tg_first_name)
        send_message(chat_id, f"✅ Отлично, *{name}*! Регистрация завершена.")
        send_trial_menu(chat_id, name)
        return

    if step == "creating_key":
        label = text[:50]
        upsert_user(user_id, "menu", "", tg_username, tg_first_name)
        send_message(chat_id, "⏳ Создаю ключ, подождите...")

        from datetime import datetime, timedelta
        user_name = get_user(user_id).get("name", "user")
        full_label = f"{user_name}_{label}_{user_id}"
        expires_dt = datetime.utcnow() + timedelta(days=7)
        expires_ms = int(expires_dt.timestamp() * 1000)

        client_id, vless_link, error = xui_create_client(full_label, expires_ms)
        if error:
            send_message(chat_id, f"❌ Не удалось создать ключ: {error}")
            return

        save_key(user_id, client_id, label, vless_link, expires_at=expires_dt)
        set_step(user_id, "menu")

        text_out = (
            f"✅ *Ключ «{label}» создан!*\n\n"
            f"⏳ Действует: *7 дней*\n\n"
            f"🔑 Твой VLESS ключ:\n\n"
            f"`{vless_link}`\n\n"
            f"Скопируй и вставь в приложение для подключения."
        )
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔑 Мои ключи", "callback_data": "my_keys"}],
                [{"text": "🏠 Главное меню", "callback_data": "main_menu"}],
            ]
        }
        send_message(chat_id, text_out, reply_markup=keyboard)
        return

    send_main_menu(chat_id, user, user_id)


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
        print(f"[handler] error: {e}")

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"ok": True})
    }