import asyncio
import os
import sqlite3
import time
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup


BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 5134277438
OWNER_USERNAME = "@emptinessdurka"

DB_FILE = "bot.db"
REWARD = 10
COOLDOWN = 3600


if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Установите переменную окружения BOT_TOKEN."
    )


db = sqlite3.connect(DB_FILE)
db.row_factory = sqlite3.Row

db.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL DEFAULT '',
        points INTEGER NOT NULL DEFAULT 0,
        last_claim INTEGER NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0
    )
    """
)
db.commit()


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# Состояние админ-панели. В боте только один владелец.
admin_states: dict[int, str] = {}


def ensure_user(user_id: int, username: str | None) -> None:
    username = username or ""

    db.execute(
        """
        INSERT INTO users (user_id, username)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET username = excluded.username
        """,
        (user_id, username),
    )
    db.commit()


def get_user(user_id: int):
    return db.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def find_user(username: str):
    username = username.strip().lstrip("@").lower()

    if not username:
        return None

    return db.execute(
        """
        SELECT * FROM users
        WHERE LOWER(username) = ?
        LIMIT 1
        """,
        (username,),
    ).fetchone()


def username_text(user) -> str:
    if user["user_id"] == OWNER_ID:
        return f"{escape(OWNER_USERNAME)} 😎"

    if user["username"]:
        name = f"@{escape(user['username'].lstrip('@'))}"
    else:
        name = f"ID {user['user_id']}"

    if user["banned"]:
        name += " 🚫"

    return name


def get_remaining(last_claim: int) -> int:
    return max(0, COOLDOWN - (int(time.time()) - last_claim))


def format_remaining(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds %= 60

    if hours:
        return f"{hours} ч. {minutes} мин."
    if minutes:
        return f"{minutes} мин. {seconds} сек."
    return f"{seconds} сек."


def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🎁 Получить очки")],
        [
            KeyboardButton(text="👤 Профиль"),
            KeyboardButton(text="🏆 Лидеры"),
        ],
    ]

    if user_id == OWNER_ID:
        rows.append([KeyboardButton(text="⚙️ Админ-панель")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🚫 Забанить"),
                KeyboardButton(text="♻️ Чёрный список"),
            ],
            [KeyboardButton(text="🧹 Очистить игрока")],
            [KeyboardButton(text="👥 Пользователи")],
            [KeyboardButton(text="💥 Очистить всё")],
            [KeyboardButton(text="🔙 Главное меню")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        is_persistent=True,
    )


async def check_access(message: Message) -> bool:
    user = message.from_user
    if user is None:
        return False

    ensure_user(user.id, user.username)
    row = get_user(user.id)

    if row is None:
        return False

    if row["banned"] and user.id != OWNER_ID:
        await message.answer(
            "🚫 <b>Ваша учётная запись была заблокирована в боте!</b>\n"
            "Подать апелляцию - @emptinessdurka"
        )
        return False

    return True


def is_owner(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == OWNER_ID


@dp.message(CommandStart())
async def start(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    ensure_user(user.id, user.username)
    row = get_user(user.id)

    if row is None:
        await message.answer("Не удалось создать профиль. Попробуйте ещё раз.")
        return

    if row["banned"] and user.id != OWNER_ID:
        await message.answer(
            "🚫 <b>Ваша учётная запись была заблокирована в боте!</b>\n"
            "Подать апелляцию - @emptinessdurka"
        )
        return

    await message.answer(
        "🎉 <b>Добро пожаловать в самого бесполезного бота в вашей жизни!</b> 🤡\n"
        "🎯 Собирай очки каждый час и попади в лидеры 🏆\n"
        "😎 Автор: @emptinessdurka",
        reply_markup=main_keyboard(user.id),
    )


@dp.message(F.text == "🎁 Получить очки")
async def claim(message: Message) -> None:
    if not await check_access(message):
        return

    user_id = message.from_user.id
    now = int(time.time())

    # Атомарная проверка и выдача награды защищает от двойного начисления
    # при почти одновременных запросах.
    cursor = db.execute(
        """
        UPDATE users
        SET points = points + ?,
            last_claim = ?
        WHERE user_id = ?
          AND last_claim <= ?
          AND banned = 0
        """,
        (REWARD, now, user_id, now - COOLDOWN),
    )
    db.commit()

    if cursor.rowcount == 0:
        row = get_user(user_id)
        remaining = get_remaining(row["last_claim"]) if row else COOLDOWN

        await message.answer(
            "⏳ Награду нельзя забрать сейчас!\n"
            f"Попробуйте через {format_remaining(remaining)} 🕐",
            reply_markup=main_keyboard(user_id),
        )
        return

    await message.answer(
        f"🎁 Вы получили {REWARD} очков!\n"
        "Возвращайтесь через 1 час! ⏰",
        reply_markup=main_keyboard(user_id),
    )


@dp.message(F.text == "👤 Профиль")
async def profile(message: Message) -> None:
    if not await check_access(message):
        return

    row = get_user(message.from_user.id)
    if row is None:
        return

    await message.answer(
        "👤 <b>Ваш профиль:</b>\n"
        f"Юзернейм - {username_text(row)}\n"
        f"Очки - {row['points']} 💰",
        reply_markup=main_keyboard(message.from_user.id),
    )


@dp.message(F.text == "🏆 Лидеры")
async def leaders(message: Message) -> None:
    if not await check_access(message):
        return

    rows = db.execute(
        """
        SELECT * FROM users
        ORDER BY points DESC, user_id ASC
        LIMIT 5
        """
    ).fetchall()

    text = "🏆 <b>Лидеры</b>\n\n"
    places = ["👑", "2 место", "3 место", "4 место", "5 место"]

    if rows:
        for index, row in enumerate(rows):
            text += (
                f"{places[index]}: {username_text(row)} - "
                f"{row['points']} очков\n"
            )
    else:
        text += "Пока здесь никого нет 😴\n"

    await message.answer(
        text,
        reply_markup=main_keyboard(message.from_user.id),
    )


@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: Message) -> None:
    if not is_owner(message):
        return

    admin_states.pop(OWNER_ID, None)

    await message.answer(
        "⚙️ <b>Админ-панель</b>",
        reply_markup=admin_keyboard(),
    )


@dp.message(F.text == "🚫 Забанить")
async def ban_start(message: Message) -> None:
    if not is_owner(message):
        return

    admin_states[OWNER_ID] = "ban"

    await message.answer(
        "🚫 Введите юзернейм:",
        reply_markup=cancel_keyboard(),
    )


@dp.message(F.text == "♻️ Чёрный список")
async def blacklist(message: Message) -> None:
    if not is_owner(message):
        return

    rows = db.execute(
        "SELECT * FROM users WHERE banned = 1 ORDER BY user_id"
    ).fetchall()

    text = "♻️ <b>Чёрный список</b>\n\n"

    if rows:
        for row in rows:
            text += f"🚫 {username_text(row)}\n"

        text += "\nВведите имя пользователя для разблокировки:"
        admin_states[OWNER_ID] = "unban"
        markup = cancel_keyboard()
    else:
        text += "Список пуст."
        markup = admin_keyboard()

    await message.answer(text, reply_markup=markup)


@dp.message(F.text == "🧹 Очистить игрока")
async def clear_player_start(message: Message) -> None:
    if not is_owner(message):
        return

    admin_states[OWNER_ID] = "clear_user"

    await message.answer(
        "🧹 Введите юзернейм:",
        reply_markup=cancel_keyboard(),
    )


@dp.message(F.text == "👥 Пользователи")
async def users_count(message: Message) -> None:
    if not is_owner(message):
        return

    row = db.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()

    count = row["count"] if row else 0

    await message.answer(
        f"👥 Число пользователей в боте: {count}",
        reply_markup=admin_keyboard(),
    )


@dp.message(F.text == "💥 Очистить всё")
async def clear_all_start(message: Message) -> None:
    if not is_owner(message):
        return

    admin_states[OWNER_ID] = "wipe_first"

    await message.answer(
        "⚠️ Вы действительно хотите полностью очистить бота?\n\n"
        "Напишите ДА для продолжения.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(F.text == "❌ Отмена")
async def cancel(message: Message) -> None:
    if not is_owner(message):
        return

    admin_states.pop(OWNER_ID, None)

    await message.answer(
        "❌ Действие отменено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(F.text == "🔙 Главное меню")
async def back_to_menu(message: Message) -> None:
    if not is_owner(message):
        return

    admin_states.pop(OWNER_ID, None)

    await message.answer(
        "🏠 Главное меню",
        reply_markup=main_keyboard(OWNER_ID),
    )


@dp.message()
async def admin_input(message: Message) -> None:
    if not is_owner(message):
        return

    state = admin_states.get(OWNER_ID)
    if not state:
        return

    text = (message.text or "").strip()

    if state == "ban":
        row = find_user(text)

        if row is None:
            await message.answer(
                "❌ Пользователь не найден.",
                reply_markup=cancel_keyboard(),
            )
            return

        if row["user_id"] == OWNER_ID:
            await message.answer(
                "❌ Нельзя заблокировать владельца бота.",
                reply_markup=cancel_keyboard(),
            )
            return

        if row["banned"]:
            await message.answer(
                "🚫 Пользователь уже заблокирован.",
                reply_markup=cancel_keyboard(),
            )
            return

        db.execute(
            "UPDATE users SET banned = 1 WHERE user_id = ?",
            (row["user_id"],),
        )
        db.commit()
        admin_states.pop(OWNER_ID, None)

        try:
            await bot.send_message(
                row["user_id"],
                "🚫 Ваша учётная запись была заблокирована в боте!\n"
                "Подать апелляцию - @emptinessdurka",
            )
        except Exception:
            pass

        await message.answer(
            f"🚫 {username_text(row)} заблокирован.",
            reply_markup=admin_keyboard(),
        )
        return

    if state == "unban":
        row = find_user(text)

        if row is None:
            await message.answer(
                "❌ Пользователь не найден.",
                reply_markup=cancel_keyboard(),
            )
            return

        if not row["banned"]:
            await message.answer(
                "ℹ️ Пользователь не находится в чёрном списке.",
                reply_markup=cancel_keyboard(),
            )
            return

        db.execute(
            "UPDATE users SET banned = 0 WHERE user_id = ?",
            (row["user_id"],),
        )
        db.commit()
        admin_states.pop(OWNER_ID, None)

        try:
            await bot.send_message(
                row["user_id"],
                "♻️ Ваша учётная запись снова доступна в боте!",
            )
        except Exception:
            pass

        await message.answer(
            f"♻️ {username_text(row)} снова доступен.",
            reply_markup=admin_keyboard(),
        )
        return

    if state == "clear_user":
        row = find_user(text)

        if row is None:
            await message.answer(
                "❌ Пользователь не найден.",
                reply_markup=cancel_keyboard(),
            )
            return

        if row["user_id"] == OWNER_ID:
            await message.answer(
                "❌ Нельзя очистить профиль владельца этим действием.",
                reply_markup=cancel_keyboard(),
            )
            return

        db.execute(
            """
            UPDATE users
            SET points = 0,
                last_claim = 0
            WHERE user_id = ?
            """,
            (row["user_id"],),
        )
        db.commit()
        admin_states.pop(OWNER_ID, None)

        await message.answer(
            f"🧹 Данные игрока {username_text(row)} очищены.",
            reply_markup=admin_keyboard(),
        )
        return

    if state == "wipe_first":
        if text.upper() != "ДА":
            await message.answer(
                "❌ Напишите ДА для продолжения.",
                reply_markup=cancel_keyboard(),
            )
            return

        admin_states[OWNER_ID] = "wipe_second"

        await message.answer(
            "⚠️ Последнее подтверждение.\n\n"
            "Напишите УДАЛИТЬ для полной очистки.",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == "wipe_second":
        if text.upper() != "УДАЛИТЬ":
            await message.answer(
                "❌ Напишите УДАЛИТЬ для подтверждения.",
                reply_markup=cancel_keyboard(),
            )
            return

        # Полностью очищаем таблицу. При следующем сообщении владелец
        # будет автоматически создан снова через ensure_user().
        db.execute("DELETE FROM users")
        db.commit()
        admin_states.pop(OWNER_ID, None)

        await message.answer(
            "💥 Бот полностью очищен.",
            reply_markup=admin_keyboard(),
        )


async def main() -> None:
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
