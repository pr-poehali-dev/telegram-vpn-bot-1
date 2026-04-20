"""
Telegram VPN бот RossoVPN на базе Marzban.
1 ключ на пользователя. Триал 7 дней. Подписка 199 руб/мес через ЮКасса.
"""

import os
import json
import uuid
import logging
import requests
import psycopg2
from datetime import datetime, timedelta, timezone

BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
MARZBAN_URL = os.environ['MARZBAN_URL'].rstrip('/')
MARZBAN_USERNAME = os.environ['MARZBAN_USERNAME']
MARZBAN_PASSWORD = os.environ['MARZBAN_PASSWORD']
DB_SCHEMA = os.environ.get('MAIN_DB_SCHEMA', 't_p89198250_telegram_vpn_bot_1')
ADMIN_USERNAMES = {'btb75', 'makarevichas'}
YUKASSA_SHOP_ID = os.environ.get('YUKASSA_SHOP_ID', '1327149')
YUKASSA_API_KEY = os.environ.get('YUKASSA_API_KEY', '')

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Marzban API ───────────────────────────────────────────────────────────────

_marzban_token = None
_marzban_token_expires = None


def marzban_get_token() -> str | None:
    global _marzban_token, _marzban_token_expires
    now = datetime.now(timezone.utc)
    if _marzban_token and _marzban_token_expires and now < _marzban_token_expires:
        return _marzban_token
    try:
        resp = requests.post(
            f"{MARZBAN_URL}/api/admin/token",
            data={"username": MARZBAN_USERNAME, "password": MARZBAN_PASSWORD},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            _marzban_token = data.get("access_token")
            _marzban_token_expires = now + timedelta(minutes=50)
            return _marzban_token
        print(f"[marzban_get_token] error {resp.status_code}: {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"[marzban_get_token] exception: {e}")
        return None


def marzban_headers() -> dict:
    token = marzban_get_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def marzban_create_user(username: str, expires_at: datetime | None) -> tuple:
    """Создаёт пользователя в Marzban. Возвращает (vless_link, error)."""
    headers = marzban_headers()
    if not headers:
        return None, "Ошибка авторизации в Marzban"

    expire_ts = int(expires_at.timestamp()) if expires_at else 0

    payload = {
        "username": username,
        "proxies": {
            "vless": {"flow": "xtls-rprx-vision"}
        },
        "inbounds": {
            "vless": ["VLESS_TCP_REALITY"]
        },
        "expire": expire_ts,
        "data_limit": 0,
        "data_limit_reset_strategy": "no_reset",
        "status": "active"
    }

    resp = requests.post(
        f"{MARZBAN_URL}/api/user",
        json=payload,
        headers=headers,
        timeout=10
    )
    print(f"[marzban_create_user] status={resp.status_code} body={resp.text[:300]}")

    if resp.status_code not in (200, 201):
        return None, f"Ошибка создания ключа: {resp.status_code}"

    data = resp.json()
    links = data.get("links", [])
    vless_link = next((l for l in links if l.startswith("vless://")), None)
    if not vless_link:
        sub_url = data.get("subscription_url", "")
        return None, f"Ключ создан, но ссылка не получена. Subscription: {sub_url}"

    return vless_link, None


def marzban_get_link(username: str) -> str | None:
    """Получает актуальную vless ссылку из Marzban."""
    headers = marzban_headers()
    if not headers:
        return None
    resp = requests.get(f"{MARZBAN_URL}/api/user/{username}", headers=headers, timeout=10)
    if resp.status_code == 200:
        links = resp.json().get("links", [])
        return next((l for l in links if l.startswith("vless://")), None)
    return None


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


# ── БД ───────────────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(os.environ['DATABASE_URL'])


def get_user(user_id: int) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT step, name, tg_username, tg_first_name, trial_used "
            f"FROM {DB_SCHEMA}.user_states WHERE user_id = {user_id}"
        )
        row = cur.fetchone()
        if row:
            return {"step": row[0], "name": row[1], "tg_username": row[2],
                    "tg_first_name": row[3], "trial_used": row[4]}
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


def set_trial_used(user_id: int):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE {DB_SCHEMA}.user_states SET trial_used=TRUE WHERE user_id={user_id}")
        conn.commit()
    finally:
        conn.close()


def save_key(user_id: int, marzban_username: str, name: str, vless_link: str, expires_at=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        name_s = name.replace("'", "''")
        link_s = vless_link.replace("'", "''")
        marz_s = marzban_username.replace("'", "''")
        if expires_at:
            cur.execute(f"""
                INSERT INTO {DB_SCHEMA}.user_keys (user_id, marzban_username, client_id, name, vless_link, created_at, expires_at)
                VALUES ({user_id}, '{marz_s}', '{marz_s}', '{name_s}', '{link_s}', NOW(), '{expires_at}')
            """)
        else:
            cur.execute(f"""
                INSERT INTO {DB_SCHEMA}.user_keys (user_id, marzban_username, client_id, name, vless_link, created_at)
                VALUES ({user_id}, '{marz_s}', '{marz_s}', '{name_s}', '{link_s}', NOW())
            """)
        conn.commit()
    finally:
        conn.close()


def get_key(user_id: int) -> dict | None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, marzban_username, name, vless_link, created_at, expires_at "
            f"FROM {DB_SCHEMA}.user_keys WHERE user_id = {user_id} ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            return {"id": row[0], "marzban_username": row[1], "name": row[2],
                    "vless_link": row[3], "created_at": row[4], "expires_at": row[5]}
        return None
    finally:
        conn.close()


def update_key_link(user_id: int, vless_link: str):
    conn = get_db()
    try:
        cur = conn.cursor()
        link_s = vless_link.replace("'", "''")
        cur.execute(f"UPDATE {DB_SCHEMA}.user_keys SET vless_link = '{link_s}' WHERE user_id = {user_id}")
        conn.commit()
    finally:
        conn.close()


def update_key_expires(user_id: int, expires_at):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {DB_SCHEMA}.user_keys SET expires_at = '{expires_at}' WHERE user_id = {user_id}"
        )
        conn.commit()
    finally:
        conn.close()


def get_subscription(user_id: int) -> dict | None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, status, expires_at, payment_method_id "
            f"FROM {DB_SCHEMA}.subscriptions WHERE user_id={user_id} ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            return {"id": row[0], "status": row[1], "expires_at": row[2], "payment_method_id": row[3]}
        return None
    finally:
        conn.close()


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    r = requests.post(f"{TELEGRAM_API}/editMessageText", json=payload, timeout=10)
    if not r.ok:
        err = r.json().get("description", "")
        if "message is not modified" not in err:
            logging.warning(f"[edit_message] error: {err}")


def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json=payload, timeout=5)


# ── Меню ──────────────────────────────────────────────────────────────────────

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
    key = get_key(user_id) if user_id else None
    rows = [
        [{"text": "👤 Мой профиль", "callback_data": "profile"}],
    ]
    if key:
        rows.append([{"text": "🔑 Мой ключ", "callback_data": "show_key"}])
    else:
        rows.append([{"text": "➕ Создать ключ", "callback_data": "create_key"}])

    sub = get_subscription(user_id) if user_id else None
    if not sub or sub["status"] != "active":
        rows.append([{"text": "💳 Оформить подписку — 199 ₽/мес", "callback_data": "subscribe"}])
    else:
        rows.append([{"text": "🔕 Отменить подписку", "callback_data": "cancel_sub"}])

    rows.append([{"text": "🛟 Поддержка", "callback_data": "support"}])
    if user.get("tg_username") in ADMIN_USERNAMES:
        rows.append([{"text": "🛠 Админ панель", "callback_data": "admin_panel"}])
    keyboard = {"inline_keyboard": rows}
    send_message(
        chat_id,
        f"👋 Привет, *{name}*! Это твой личный кабинет VPN.\n\nВыбери действие:",
        reply_markup=keyboard
    )


def send_key_detail(chat_id, message_id, key: dict, edit=True):
    date = key["created_at"].strftime("%d.%m.%Y %H:%M") if key["created_at"] else "—"
    if key.get("expires_at"):
        exp = key["expires_at"]
        if hasattr(exp, 'tzinfo') and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_left = (exp - now).days
        expires_str = exp.strftime("%d.%m.%Y")
        if days_left < 0:
            validity = f"❌ истёк {expires_str}"
        elif days_left == 0:
            validity = "⚠️ истекает сегодня"
        else:
            validity = f"до *{expires_str}* ({days_left} дн.)"
    else:
        validity = "*бессрочно*"

    text = (
        f"🔑 *Ключ: {key['name']}*\n\n"
        f"📅 Создан: {date}\n"
        f"⏳ Действует: {validity}\n\n"
        f"`{key['vless_link']}`\n\n"
        "Скопируй и вставь в приложение:"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "📱 Инструкция по подключению", "callback_data": "instruction"}],
            [{"text": "◀️ Главное меню", "callback_data": "main_menu"}],
        ]
    }
    if edit:
        edit_message(chat_id, message_id, text, reply_markup=keyboard)
    else:
        send_message(chat_id, text, reply_markup=keyboard)


def send_instruction(chat_id, message_id):
    text = (
        "📱 *Инструкция по подключению*\n\n"
        "*iPhone / iPad:*\n"
        "1. Установи Streisand из App Store\n"
        "2. Нажми + → вставь ключ → Сохрани\n"
        "3. Нажми Connect\n\n"
        "*Android:*\n"
        "1. Установи v2rayNG из Google Play\n"
        "2. Нажми + → вставь ключ → OK\n"
        "3. Нажми кнопку запуска\n\n"
        "*Windows / Mac:*\n"
        "1. Установи Hiddify\n"
        "2. Добавь профиль → вставь ключ\n"
        "3. Нажми Connect\n\n"
        "*Мобильная связь (4G/5G):*\n"
        "Ключ работает на любом типе соединения — Wi-Fi и мобильный интернет.\n\n"
        "Проблемы? Пиши в поддержку: @btb75"
    )
    keyboard = {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "show_key"}]]}
    edit_message(chat_id, message_id, text, reply_markup=keyboard)


def send_admin_menu(chat_id, message_id=None, edit=False):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {DB_SCHEMA}.user_states")
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT u.user_id, u.name, u.tg_username, u.trial_used,
                   k.expires_at as key_expires,
                   s.status as sub_status, s.expires_at as sub_expires
            FROM {DB_SCHEMA}.user_states u
            LEFT JOIN {DB_SCHEMA}.user_keys k ON k.user_id = u.user_id
            LEFT JOIN (
                SELECT DISTINCT ON (user_id) user_id, status, expires_at
                FROM {DB_SCHEMA}.subscriptions
                ORDER BY user_id, id DESC
            ) s ON s.user_id = u.user_id
            ORDER BY u.updated_at DESC
            LIMIT 15
        """)
        users = cur.fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    lines = [f"🛠 Админ-панель RossoVPN\n\nВсего пользователей: {total}\n"]
    rows = []

    for u in users:
        uid, name, tg, trial_used, key_exp, sub_status, sub_exp = u
        name = name or "—"
        tg_str = f"@{tg}" if tg else "без username"

        if sub_status == "active" and sub_exp:
            if hasattr(sub_exp, 'tzinfo') and sub_exp.tzinfo is None:
                sub_exp = sub_exp.replace(tzinfo=timezone.utc)
            sub_str = f"💳 до {sub_exp.strftime('%d.%m.%Y')}"
        elif trial_used:
            sub_str = "🎁 триал"
        else:
            sub_str = "➖ нет"

        if key_exp:
            if hasattr(key_exp, 'tzinfo') and key_exp.tzinfo is None:
                key_exp = key_exp.replace(tzinfo=timezone.utc)
            days = (key_exp - now).days
            if days < 0:
                key_str = f"❌ истёк"
            elif days <= 3:
                key_str = f"⚠️ {days}д"
            else:
                key_str = f"🔑 до {key_exp.strftime('%d.%m')}"
        else:
            key_str = "🔑 нет"

        lines.append(f"👤 {name} ({tg_str})\n   {sub_str} | {key_str}")
        rows.append([{"text": f"🗑 Удалить {name}", "callback_data": f"admin_del_{uid}"}])

    rows.append([{"text": "🔄 Обновить", "callback_data": "admin_panel"}])
    rows.append([{"text": "◀️ Главное меню", "callback_data": "main_menu"}])
    keyboard = {"inline_keyboard": rows}
    text = "\n".join(lines)
    if edit and message_id:
        edit_message(chat_id, message_id, text, reply_markup=keyboard, parse_mode=None)
    else:
        send_message(chat_id, text, reply_markup=keyboard, parse_mode=None)


# ── Обработчик ────────────────────────────────────────────────────────────────

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

        if data == "main_menu":
            set_step(user_id, "menu")
            send_main_menu(chat_id, user, user_id)

        elif data == "show_key":
            key = get_key(user_id)
            if key:
                fresh_link = marzban_get_link(key["marzban_username"])
                if fresh_link and fresh_link != key["vless_link"]:
                    update_key_link(user_id, fresh_link)
                    key["vless_link"] = fresh_link
                send_key_detail(chat_id, message_id, key, edit=True)
            else:
                edit_message(chat_id, message_id, "У тебя пока нет ключа.",
                             reply_markup={"inline_keyboard": [[{"text": "➕ Создать ключ", "callback_data": "create_key"}, {"text": "◀️ Назад", "callback_data": "main_menu"}]]})

        elif data == "instruction":
            send_instruction(chat_id, message_id)

        elif data == "get_trial":
            if user.get("trial_used"):
                answer_callback(callback["id"], "Пробный ключ уже был использован", show_alert=True)
            else:
                key = get_key(user_id)
                if key:
                    answer_callback(callback["id"], "У тебя уже есть ключ!", show_alert=True)
                else:
                    send_message(chat_id, "⏳ Создаю пробный ключ на 7 дней...")
                    expires_dt = datetime.now(timezone.utc) + timedelta(days=7)
                    marz_user = f"u{user_id}_{uuid.uuid4().hex[:8]}"
                    vless_link, error = marzban_create_user(marz_user, expires_dt)
                    if error:
                        send_message(chat_id, f"❌ Не удалось создать ключ: {error}\nНапиши в поддержку: @btb75")
                    else:
                        set_trial_used(user_id)
                        save_key(user_id, marz_user, "Пробный (7 дней)", vless_link, expires_at=expires_dt)
                        send_message(
                            chat_id,
                            "🎁 *Пробный ключ активирован на 7 дней!*\n\n"
                            f"🔑 Твой VLESS ключ:\n\n`{vless_link}`\n\n"
                            "Скопируй и вставь в приложение.\n"
                            "После пробного периода оформи подписку — *199 ₽/месяц*."
                        )
                        user = get_user(user_id)
                        send_main_menu(chat_id, user, user_id)

        elif data == "create_key":
            key = get_key(user_id)
            if key:
                send_key_detail(chat_id, message_id, key, edit=True)
            else:
                # Определяем срок — по подписке или 7 дней
                sub = get_subscription(user_id)
                if sub and sub["status"] == "active" and sub["expires_at"]:
                    expires_dt = sub["expires_at"]
                    if hasattr(expires_dt, 'tzinfo') and expires_dt.tzinfo is None:
                        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                    key_name = "Мой VPN ключ"
                else:
                    expires_dt = datetime.now(timezone.utc) + timedelta(days=7)
                    key_name = "Пробный (7 дней)"

                send_message(chat_id, "⏳ Создаю ключ...")
                marz_user = f"u{user_id}_{uuid.uuid4().hex[:8]}"
                vless_link, error = marzban_create_user(marz_user, expires_dt)
                if error:
                    send_message(chat_id, f"❌ Не удалось создать ключ: {error}\nНапиши в поддержку: @btb75")
                else:
                    save_key(user_id, marz_user, key_name, vless_link, expires_at=expires_dt)
                    key = get_key(user_id)
                    send_key_detail(chat_id, message_id, key, edit=False)

        elif data == "profile":
            name = user.get("name", "—")
            tg_u = user.get("tg_username", "")
            tg_line = f"@{tg_u}" if tg_u else "не указан"
            key = get_key(user_id)
            key_count = 1 if key else 0

            sub = get_subscription(user_id)
            if sub and sub["status"] == "active" and sub["expires_at"]:
                exp = sub["expires_at"]
                if hasattr(exp, 'tzinfo') and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                sub_line = f"💳 Активна до *{exp.strftime('%d.%m.%Y')}*"
            elif sub and sub["status"] == "cancelled":
                sub_line = "🔕 Отменена"
            elif sub and sub["status"] == "expired":
                sub_line = "❌ Истекла"
            else:
                sub_line = "Нет активной подписки"

            text = (
                f"👤 *Профиль*\n\n"
                f"Имя: *{name}*\n"
                f"Telegram: {tg_line}\n"
                f"Ключей: *{key_count}*\n"
                f"Подписка: {sub_line}"
            )
            kb_rows = []
            if not sub or sub["status"] != "active":
                kb_rows.append([{"text": "💳 Оформить подписку — 199 ₽/мес", "callback_data": "subscribe"}])
            kb_rows.append([{"text": "◀️ Назад", "callback_data": "main_menu"}])
            edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": kb_rows})

        elif data == "subscribe":
            sub = get_subscription(user_id)
            if sub and sub["status"] == "active":
                exp = sub["expires_at"]
                if exp and hasattr(exp, 'tzinfo') and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                exp_str = exp.strftime('%d.%m.%Y') if exp else "—"
                edit_message(chat_id, message_id,
                             f"✅ Подписка уже активна до *{exp_str}*.",
                             reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]})
                return

            if not YUKASSA_API_KEY:
                edit_message(chat_id, message_id, "Оплата временно недоступна. Напиши @btb75",
                             reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]})
                return

            import uuid as _uuid
            idempotency_key = str(_uuid.uuid4())
            payload = {
                "amount": {"value": "199.00", "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": "https://t.me/RossoVPN_bot"},
                "capture": True,
                "save_payment_method": True,
                "description": f"Подписка RossoVPN — пользователь {user_id}",
                "metadata": {"user_id": str(user_id)}
            }
            resp = requests.post(
                "https://api.yookassa.ru/v3/payments",
                json=payload,
                auth=(YUKASSA_SHOP_ID, YUKASSA_API_KEY),
                headers={"Idempotence-Key": idempotency_key},
                timeout=15
            )
            if resp.status_code == 200:
                pay_data = resp.json()
                pay_url = pay_data.get("confirmation", {}).get("confirmation_url", "")
                if pay_url:
                    keyboard = {"inline_keyboard": [
                        [{"text": "💳 Оплатить 199 ₽", "url": pay_url}],
                        [{"text": "◀️ Отмена", "callback_data": "main_menu"}]
                    ]}
                    edit_message(chat_id, message_id,
                                 "💳 *Оформление подписки*\n\n"
                                 "Стоимость: *199 ₽/месяц*\n"
                                 "Автопродление каждые 30 дней.\n\n"
                                 "Нажми кнопку для оплаты:",
                                 reply_markup=keyboard)
                else:
                    edit_message(chat_id, message_id, "Ошибка создания платежа. Напиши @btb75",
                                 reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]})
            else:
                edit_message(chat_id, message_id, f"Ошибка оплаты: {resp.status_code}. Напиши @btb75",
                             reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]})

        elif data == "cancel_sub":
            keyboard = {"inline_keyboard": [
                [{"text": "✅ Да, отменить", "callback_data": "cancel_sub_do"}],
                [{"text": "◀️ Нет, назад", "callback_data": "main_menu"}]
            ]}
            edit_message(chat_id, message_id,
                         "⚠️ *Отмена подписки*\n\nПосле отмены VPN продолжит работать до конца оплаченного периода.\n\nТочно отменить?",
                         reply_markup=keyboard)

        elif data == "cancel_sub_do":
            sub = get_subscription(user_id)
            if sub and sub["status"] == "active" and YUKASSA_API_KEY and sub.get("payment_method_id"):
                requests.post(
                    f"https://api.yookassa.ru/v3/recurring-payments/{sub['payment_method_id']}/cancel",
                    auth=(YUKASSA_SHOP_ID, YUKASSA_API_KEY),
                    timeout=10
                )
            if sub:
                conn = get_db()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        f"UPDATE {DB_SCHEMA}.subscriptions SET status='cancelled' WHERE user_id={user_id} AND status='active'"
                    )
                    conn.commit()
                finally:
                    conn.close()
            edit_message(chat_id, message_id,
                         "✅ Подписка отменена. VPN будет работать до конца оплаченного периода.",
                         reply_markup={"inline_keyboard": [[{"text": "◀️ Главное меню", "callback_data": "main_menu"}]]})

        elif data == "support":
            edit_message(chat_id, message_id,
                         "🛟 *Поддержка RossoVPN*\n\n"
                         "Пиши нам:\n"
                         "• @btb75\n"
                         "• @makarevichas\n\n"
                         "Отвечаем быстро 🚀",
                         reply_markup={"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "main_menu"}]]})

        elif data == "admin_panel":
            if user.get("tg_username") not in ADMIN_USERNAMES:
                return
            send_admin_menu(chat_id, message_id, edit=True)

        elif data.startswith("admin_del_"):
            if user.get("tg_username") not in ADMIN_USERNAMES:
                return
            target_id = int(data.replace("admin_del_", ""))
            # Удаляем ключ из Marzban
            conn = get_db()
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT marzban_username FROM {DB_SCHEMA}.user_keys WHERE user_id={target_id}")
                row = cur.fetchone()
                if row and row[0]:
                    marzban_delete_user(row[0])
                cur.execute(f"DELETE FROM {DB_SCHEMA}.user_keys WHERE user_id={target_id}")
                cur.execute(f"DELETE FROM {DB_SCHEMA}.subscriptions WHERE user_id={target_id}")
                cur.execute(f"DELETE FROM {DB_SCHEMA}.user_states WHERE user_id={target_id}")
                conn.commit()
            finally:
                conn.close()
            send_admin_menu(chat_id, message_id, edit=True)

        return

    # ── Message ──
    msg = update.get("message", {})
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "").strip()
    tg_username = msg["from"].get("username", "")
    tg_first_name = msg["from"].get("first_name", "")

    user = get_user(user_id)

    if text == "/start":
        if not user:
            upsert_user(user_id, "reg_name", tg_username=tg_username, tg_first_name=tg_first_name)
            send_message(chat_id, f"👋 Привет, *{tg_first_name or 'друг'}*!\n\nКак тебя зовут? Напиши своё имя:")
        else:
            upsert_user(user_id, "menu", tg_username=tg_username, tg_first_name=tg_first_name)
            if not user.get("trial_used") and not get_key(user_id):
                send_trial_menu(chat_id, user.get("name", tg_first_name))
            else:
                send_main_menu(chat_id, user, user_id)

    elif text == "/offer":
        send_message(chat_id, "📄 Публичная оферта: https://telegra.ph/Publichnaya-oferta-RossoVPN-06-01")

    elif text == "/refund":
        send_message(chat_id, "💰 *Возврат средств*\n\nВозврат возможен в течение 7 дней с момента оплаты, если VPN не работает и мы не смогли решить проблему.\n\nПиши: @btb75")

    elif text == "/support":
        send_message(chat_id, "🛟 Поддержка: @btb75 или @makarevichas")

    elif text == "/cancel":
        sub = get_subscription(user_id)
        if sub and sub["status"] == "active":
            keyboard = {"inline_keyboard": [
                [{"text": "✅ Да, отменить", "callback_data": "cancel_sub_do"}],
                [{"text": "◀️ Нет, назад", "callback_data": "main_menu"}]
            ]}
            send_message(chat_id, "⚠️ Отменить подписку?", reply_markup=keyboard)
        else:
            send_message(chat_id, "У тебя нет активной подписки.")

    elif user.get("step") == "reg_name":
        if len(text) < 2:
            send_message(chat_id, "Имя слишком короткое. Попробуй ещё раз:")
        else:
            upsert_user(user_id, "menu", name=text, tg_username=tg_username, tg_first_name=tg_first_name)
            user = get_user(user_id)
            send_trial_menu(chat_id, text)

    else:
        send_main_menu(chat_id, user or {"name": tg_first_name, "tg_username": tg_username}, user_id)


def setup_bot():
    requests.post(f"{TELEGRAM_API}/setMyName", json={"name": "RossoVPN"}, timeout=10)
    requests.post(f"{TELEGRAM_API}/setMyDescription", json={
        "description": (
            "🔒 RossoVPN — быстрый и надёжный VPN-сервис.\n\n"
            "✅ Безлимитный трафик\n"
            "✅ Работает на любом устройстве и мобильной связи\n"
            "✅ 199 ₽/месяц\n\n"
            "Поддержка: @btb75, @makarevichas"
        )
    }, timeout=10)
    requests.post(f"{TELEGRAM_API}/setMyCommands", json={"commands": [
        {"command": "start",   "description": "Личный кабинет"},
        {"command": "offer",   "description": "Публичная оферта"},
        {"command": "refund",  "description": "Условия возврата"},
        {"command": "support", "description": "Связаться с поддержкой"},
        {"command": "cancel",  "description": "Отменить подписку"},
    ]}, timeout=10)
    print("[setup_bot] done")


setup_bot()


def handler(event: dict, context) -> dict:
    """Обработчик вебхука Telegram бота RossoVPN (Marzban)."""
    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 200, 'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        }, 'body': ''}

    try:
        body = event.get('body', '{}')
        if isinstance(body, str):
            update = json.loads(body)
        else:
            update = body
        handle_update(update)
    except Exception as e:
        print(f"[handler] error: {e}")

    return {
        'statusCode': 200,
        'headers': {'Access-Control-Allow-Origin': '*'},
        'body': json.dumps({'ok': True})
    }