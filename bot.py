import asyncio
import os
import sqlite3
import time
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    CallbackQuery,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 5134277438
OWNER_USERNAME = "@emptinessdurka"

DB_FILE = "/app/data/bot.db"
REWARD = 10
COOLDOWN = 3600
MASKOT_FILE = Path("/app/data/maskot.jpeg")
if not MASKOT_FILE.exists():
    local_maskot = Path("maskot.jpeg")
    if local_maskot.exists():
        MASKOT_FILE = local_maskot

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN. Установите переменную окружения BOT_TOKEN.")


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
db.execute("CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC, user_id ASC)")
db.commit()

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Состояния нужны только для админских текстовых действий.
admin_states: dict[int, str] = {}


def ensure_user(user_id: int, username: str | None) -> None:
    username = username or ""
    db.execute(
        """
        INSERT INTO users (user_id, username)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
        """,
        (user_id, username),
    )
    db.commit()


def get_user(user_id: int):
    return db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def find_user(identifier: str):
    identifier = identifier.strip()
    if not identifier:
        return None
    if identifier.lstrip("-").isdigit():
        return db.execute(
            "SELECT * FROM users WHERE user_id = ? LIMIT 1", (int(identifier),)
        ).fetchone()
    username = identifier.lstrip("@").lower()
    return db.execute(
        "SELECT * FROM users WHERE LOWER(username) = ? LIMIT 1", (username,)
    ).fetchone()


def username_text(user) -> str:
    if user["user_id"] == OWNER_ID:
        return f"{escape(OWNER_USERNAME)} 😎"
    name = f"@{escape(user['username'].lstrip('@'))}" if user["username"] else f"ID {user['user_id']}"
    if user["banned"]:
        name += " 🚫"
    return name


def get_remaining(last_claim: int) -> int:
    return max(0, COOLDOWN - (int(time.time()) - int(last_claim)))


def format_remaining(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds %= 60
    if hours:
        return f"{hours} ч. {minutes} мин."
    if minutes:
        return f"{minutes} мин. {seconds} сек."
    return f"{seconds} сек."


def get_rank(user_id: int):
    row = db.execute(
        """
        SELECT 1 + COUNT(*) AS rank
        FROM users AS other
        WHERE other.points > (SELECT points FROM users WHERE user_id = ?)
        """,
        (user_id,),
    ).fetchone()
    return row["rank"] if row else None


def main_inline_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎁 Получить очки", callback_data="claim")],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
            InlineKeyboardButton(text="🏆 Лидеры", callback_data="leaders"),
        ],
        [InlineKeyboardButton(text="📰 Новости", callback_data="news")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ]
    if user_id == OWNER_ID:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚫 Забанить"), KeyboardButton(text="♻️ Чёрный список")],
            [KeyboardButton(text="🧹 Очистить игрока")],
            [KeyboardButton(text="👥 Пользователи")],
            [KeyboardButton(text="📢 Рассылка")],
            [KeyboardButton(text="💥 Сбросить очки")],
            [KeyboardButton(text="🗑️ Очистить всех пользователей")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_return_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Вернуться в меню", callback_data="back_menu")]]
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def help_keyboard(section: str | None = None) -> InlineKeyboardMarkup:
    if section is None:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📖 Основа", callback_data="help_base")],
                [InlineKeyboardButton(text="🧹 Вайпы", callback_data="help_wipes")],
                [InlineKeyboardButton(text="🏅 Значки", callback_data="help_badges")],
                [InlineKeyboardButton(text="↩️ Назад", callback_data="back_menu")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Основа", callback_data="help_base")],
            [InlineKeyboardButton(text="🧹 Вайпы", callback_data="help_wipes")],
            [InlineKeyboardButton(text="🏅 Значки", callback_data="help_badges")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="help")],
        ]
    )


async def check_access_user(user) -> bool:
    if user is None or user.is_bot:
        return False
    ensure_user(user.id, user.username)
    row = get_user(user.id)
    if row is None:
        return False
    return not (row["banned"] and user.id != OWNER_ID)


async def send_main_menu(message: Message, user_id: int, text: str | None = None):
    # ReplyKeyboardRemove гарантированно убирает старое нижнее меню.
    await message.answer(
        text or "🏠 Главное меню",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "🎉 <b>Добро пожаловать в самого бесполезного бота в вашей жизни!</b> 🤡\n"
        "🎯 Собирай очки каждый час и попади в лидеры 🏆\n"
        "😎 Автор: @emptinessdurka",
        reply_markup=main_inline_keyboard(user_id),
    )


@dp.message(CommandStart())
async def start(message: Message) -> None:
    user = message.from_user
    if not await check_access_user(user):
        return
    await send_main_menu(message, user.id)


@dp.message(Command("help"))
async def help_command(message: Message) -> None:
    user = message.from_user
    if not await check_access_user(user):
        return
    await send_help(message)


async def send_help(message: Message):
    caption = "Привет, я Уголёк! Готова рассказать тебе всё"
    if MASKOT_FILE.exists():
        await message.answer_photo(
            FSInputFile(MASKOT_FILE),
            caption=caption,
            reply_markup=help_keyboard(),
        )
    else:
        await message.answer(caption, reply_markup=help_keyboard())


async def edit_help(callback: CallbackQuery, text: str):
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text,
            reply_markup=help_keyboard("section"),
        )
    else:
        await callback.message.edit_text(
            text,
            reply_markup=help_keyboard("section"),
        )


@dp.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    if not await check_access_user(callback.from_user):
        await callback.answer()
        return

    await callback.answer()

    # Если помощь открыта из текстового сообщения меню, заменяем его
    # на сообщение с маскотом. Если маскот недоступен, оставляем текст.
    if callback.message.photo:
        await callback.message.edit_caption(
            caption="Привет, я Уголёк! Готова рассказать тебе всё",
            reply_markup=help_keyboard(),
        )
    elif MASKOT_FILE.exists():
        await callback.message.delete()
        await send_help(callback.message)
    else:
        await callback.message.edit_text(
            "Привет, я Уголёк! Готова рассказать тебе всё",
            reply_markup=help_keyboard(),
        )


@dp.callback_query(F.data == "help_base")
async def help_base(callback: CallbackQuery):
    await callback.answer()
    await edit_help(
        callback,
        "В меню есть кнопка «🎁Получить очки». Нажимай на неё и получай 10 очков каждый час! "
        "В «Профиле» ты можешь увидеть кол-во своих очков и место в таблице лидеров. "
        "В «Лидерах» ты можешь отслеживать лучших игроков. В «Новостях» ты найдёшь ссылку "
        "для перехода в новостной канал бота, там вся полезная информация и опросы"
    )


@dp.callback_query(F.data == "help_wipes")
async def help_wipes(callback: CallbackQuery):
    await callback.answer()
    await edit_help(
        callback,
        "Вайпы - (от англ. wipe — «стереть», «очистить»)\n\n"
        "Вайпы (очистка серверов) нужна для баланса между новичками и долгими игроками. "
        "В девятое число каждого месяца проходит опрос в новостном канале. После опроса "
        "решается будет ли сброс в этом месяце всех очков или нет."
    )


@dp.callback_query(F.data == "help_badges")
async def help_badges(callback: CallbackQuery):
    await callback.answer()
    await edit_help(
        callback,
        "Наверняка вы замечали в лидерах какие-то значки после никнейма. Что они значат?\n\n"
        "😎 - администрация бота (данный значок есть только у владельца бота)\n\n"
        "🚫 - блокировка (человек заблокирован в боте и не может ничего в нём делать)"
    )


@dp.callback_query(F.data == "claim")
async def claim_callback(callback: CallbackQuery):
    user = callback.from_user
    if not await check_access_user(user):
        await callback.answer("🚫 Доступ запрещён.", show_alert=True)
        return
    now = int(time.time())
    cursor = db.execute(
        """
        UPDATE users
        SET points = points + ?, last_claim = ?
        WHERE user_id = ? AND last_claim <= ? AND banned = 0
        """,
        (REWARD, now, user.id, now - COOLDOWN),
    )
    db.commit()
    if cursor.rowcount == 0:
        row = get_user(user.id)
        remaining = get_remaining(row["last_claim"]) if row else COOLDOWN
        await callback.answer(f"⏳ Попробуйте через {format_remaining(remaining)}", show_alert=True)
        return
    await callback.answer(f"🎁 Вы получили {REWARD} очков!", show_alert=True)


@dp.callback_query(F.data == "profile")
async def profile_callback(callback: CallbackQuery):
    user = callback.from_user
    if not await check_access_user(user):
        await callback.answer("🚫 Доступ запрещён.", show_alert=True)
        return
    row = get_user(user.id)
    rank = get_rank(user.id)
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>Ваш профиль:</b>\n"
        f"Юзернейм - {username_text(row)}\n"
        f"Очки - {row['points']} 💰\n"
        f"Место в топе - {rank} 🏆",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]]
        ),
    )


@dp.callback_query(F.data == "news")
async def news_callback(callback: CallbackQuery):
    user = callback.from_user
    if not await check_access_user(user):
        await callback.answer("🚫 Доступ запрещён.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "📰 <b>Новости</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📰 Открыть новостной канал", url="https://t.me/points_collector_channel")],
                [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "leaders")
async def leaders_callback(callback: CallbackQuery):
    user = callback.from_user
    if not await check_access_user(user):
        await callback.answer("🚫 Доступ запрещён.", show_alert=True)
        return
    rows = db.execute(
        "SELECT * FROM users WHERE points >= 0 ORDER BY points DESC, user_id ASC LIMIT 5"
    ).fetchall()
    text = "🏆 <b>Лидеры</b>\n\n"
    places = ["👑", "2 место", "3 место", "4 место", "5 место"]
    for index, row in enumerate(rows):
        text += f"{places[index]}: {username_text(row)} - {row['points']} очков\n"
    if not rows:
        text += "Пока здесь никого нет 😴\n"
    await callback.answer()
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]]
        ),
    )


@dp.callback_query(F.data == "admin")
async def admin_callback(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("🚫 Доступ запрещён.", show_alert=True)
        return
    admin_states.pop(OWNER_ID, None)
    await callback.answer()
    # Показываем админку, а снизу оставляем только её временную клавиатуру.
    await callback.message.answer("⚙️ <b>Админ-панель</b>", reply_markup=admin_keyboard())
    await callback.message.answer("↩️ Когда закончишь, нажми кнопку ниже.", reply_markup=admin_return_inline())


@dp.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    user = callback.from_user
    if user is None:
        return
    if not await check_access_user(user):
        await callback.answer("🚫 Доступ запрещён.", show_alert=True)
        return
    admin_states.pop(user.id, None)
    await callback.answer()
    await callback.message.answer("🏠 Главное меню", reply_markup=ReplyKeyboardRemove())
    await callback.message.answer(
        "🎉 <b>Добро пожаловать в самого бесполезного бота в вашей жизни!</b> 🤡\n"
        "🎯 Собирай очки каждый час и попади в лидеры 🏆\n"
        "😎 Автор: @emptinessdurka",
        reply_markup=main_inline_keyboard(user.id),
    )


# ===== Админка. Оставлена на ReplyKeyboard для удобства владельца. =====


def is_owner(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == OWNER_ID


@dp.message(F.text == "🚫 Забанить")
async def ban_start(message: Message):
    if not is_owner(message): return
    admin_states[OWNER_ID] = "ban"
    await message.answer("🚫 Введите юзернейм:", reply_markup=cancel_keyboard())


@dp.message(F.text == "♻️ Чёрный список")
async def blacklist(message: Message):
    if not is_owner(message): return
    rows = db.execute("SELECT * FROM users WHERE banned = 1 ORDER BY user_id").fetchall()
    text = "♻️ <b>Чёрный список</b>\n\n"
    if rows:
        text += "\n".join(f"🚫 {username_text(row)}" for row in rows)
        text += "\n\nВведите имя пользователя для разблокировки:"
        admin_states[OWNER_ID] = "unban"
        markup = cancel_keyboard()
    else:
        text += "Список пуст."
        markup = admin_keyboard()
    await message.answer(text, reply_markup=markup)


@dp.message(F.text == "🧹 Очистить игрока")
async def clear_player_start(message: Message):
    if not is_owner(message): return
    admin_states[OWNER_ID] = "clear_user"
    await message.answer("🧹 Введите юзернейм:", reply_markup=cancel_keyboard())


@dp.message(F.text == "👥 Пользователи")
async def users_count(message: Message):
    if not is_owner(message): return
    count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    await message.answer(f"👥 Число пользователей в боте: {count}", reply_markup=admin_keyboard())


@dp.message(F.text == "📢 Рассылка")
async def broadcast_start(message: Message):
    if not is_owner(message): return
    admin_states[OWNER_ID] = "broadcast"
    await message.answer("📢 Введите сообщение для рассылки всем пользователям бота.", reply_markup=cancel_keyboard())


@dp.message(F.text == "💥 Сбросить очки")
async def reset_points_start(message: Message):
    if not is_owner(message): return
    admin_states[OWNER_ID] = "reset_points_first"
    await message.answer(
        "⚠️ Сбросить очки и таймеры у всех пользователей?\n\nНапишите ДА для продолжения.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(F.text == "🗑️ Очистить всех пользователей")
async def delete_all_start(message: Message):
    if not is_owner(message): return
    admin_states[OWNER_ID] = "delete_all_first"
    await message.answer(
        "⚠️ Это удалит аккаунты всех пользователей из базы.\n\nНапишите ДА для продолжения.",
        reply_markup=cancel_keyboard(),
    )


@dp.message(F.text == "❌ Отмена")
async def cancel(message: Message):
    if not is_owner(message): return
    admin_states.pop(OWNER_ID, None)
    await message.answer("❌ Действие отменено.", reply_markup=admin_keyboard())


@dp.message()
async def admin_input(message: Message):
    # Игроки и любые их обычные сообщения здесь полностью игнорируются.
    if not is_owner(message):
        return
    state = admin_states.get(OWNER_ID)
    if not state:
        return
    text = (message.text or "").strip()

    if state == "broadcast":
        if not text:
            await message.answer("❌ Сообщение не может быть пустым.", reply_markup=cancel_keyboard())
            return
        rows = db.execute("SELECT user_id FROM users WHERE banned = 0").fetchall()
        admin_states.pop(OWNER_ID, None)
        sent = failed = 0
        for row in rows:
            try:
                await bot.send_message(row["user_id"], text)
                sent += 1
            except Exception:
                failed += 1
        await message.answer(f"📢 Рассылка завершена.\n✅ Доставлено: {sent}\n❌ Не доставлено: {failed}", reply_markup=admin_keyboard())
        return

    if state in {"ban", "unban", "clear_user"}:
        row = find_user(text)
        if row is None:
            await message.answer("❌ Пользователь не найден.", reply_markup=cancel_keyboard())
            return
        if state == "ban":
            if row["user_id"] == OWNER_ID:
                await message.answer("❌ Нельзя заблокировать владельца бота.", reply_markup=cancel_keyboard()); return
            if row["banned"]:
                await message.answer("🚫 Пользователь уже заблокирован.", reply_markup=cancel_keyboard()); return
            db.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (row["user_id"],)); db.commit()
            admin_states.pop(OWNER_ID, None)
            try: await bot.send_message(row["user_id"], "🚫 Ваша учётная запись была заблокирована в боте!\nПодать апелляцию - @emptinessdurka")
            except Exception: pass
            await message.answer(f"🚫 {username_text(row)} заблокирован.", reply_markup=admin_keyboard()); return
        if state == "unban":
            if not row["banned"]:
                await message.answer("ℹ️ Пользователь не находится в чёрном списке.", reply_markup=cancel_keyboard()); return
            db.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (row["user_id"],)); db.commit()
            admin_states.pop(OWNER_ID, None)
            try: await bot.send_message(row["user_id"], "♻️ Ваша учётная запись снова доступна в боте!")
            except Exception: pass
            await message.answer(f"♻️ {username_text(row)} снова доступен.", reply_markup=admin_keyboard()); return
        if row["user_id"] == OWNER_ID:
            await message.answer("❌ Нельзя очистить профиль владельца этим действием.", reply_markup=cancel_keyboard()); return
        db.execute("UPDATE users SET points = 0, last_claim = 0 WHERE user_id = ?", (row["user_id"],)); db.commit()
        admin_states.pop(OWNER_ID, None)
        await message.answer(f"🧹 Данные игрока {username_text(row)} очищены.", reply_markup=admin_keyboard()); return

    if state == "reset_points_first":
        if text.upper() != "ДА":
            await message.answer("❌ Напишите ДА для продолжения.", reply_markup=cancel_keyboard()); return
        db.execute("UPDATE users SET points = 0, last_claim = 0")
        db.commit()
        admin_states.pop(OWNER_ID, None)
        await message.answer("💥 Очки и таймеры всех пользователей сброшены. Сами аккаунты сохранены.", reply_markup=admin_keyboard())
        return

    if state == "delete_all_first":
        if text.upper() != "ДА":
            await message.answer("❌ Напишите ДА для продолжения.", reply_markup=cancel_keyboard()); return
        db.execute("DELETE FROM users")
        db.commit()
        admin_states.pop(OWNER_ID, None)
        await message.answer("🗑️ Все аккаунты пользователей удалены из базы.", reply_markup=admin_keyboard())


async def main():
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
