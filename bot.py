import asyncio
import logging
import datetime
import os
import json

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv

import gspread

logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Webhook URL — фиксированный, так как вы уже знаете адрес Render
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://event-nkos-bot.onrender.com{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# Подключение к Google Таблице: из переменной GOOGLE_CREDENTIALS или файла (для локального запуска)
google_credentials_raw = os.getenv("GOOGLE_CREDENTIALS")
if google_credentials_raw:
    try:
        credentials_dict = json.loads(google_credentials_raw)
        gc = gspread.service_account_from_dict(credentials_dict)
    except Exception as e:
        logging.error(f"Ошибка парсинга GOOGLE_CREDENTIALS: {e}")
        raise
else:
    # Для локального запуска (если вы тестируете на своём компьютере)
    gc = gspread.service_account(filename="credentials.json")

sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

class EventForm(StatesGroup):
    name = State()
    date = State()
    time = State()
    region = State()
    description = State()
    participation = State()
    confirm = State()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Здравствуйте! Я помогу вам добавить информацию о мероприятии.\n\n"
        "Пожалуйста, ответьте на несколько вопросов.\n\n"
        "1️⃣ Как называется мероприятие?"
    )
    await state.set_state(EventForm.name)

@router.message(EventForm.name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("2️⃣ Укажите дату проведения (например, 15.06.2025 или 15–17.06.2025):")
    await state.set_state(EventForm.date)

@router.message(EventForm.date)
async def process_date(message: Message, state: FSMContext):
    await state.update_data(date=message.text)
    await message.answer("3️⃣ Во сколько начинается мероприятие? (например, 14:00 или 14:00–17:00):")
    await state.set_state(EventForm.time)

@router.message(EventForm.time)
async def process_time(message: Message, state: FSMContext):
    await state.update_data(time=message.text)
    await message.answer("4️⃣ Где проходит мероприятие? (город и регион):")
    await state.set_state(EventForm.region)

@router.message(EventForm.region)
async def process_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("5️⃣ Кратко опишите мероприятие (до 300 символов):")
    await state.set_state(EventForm.description)

@router.message(EventForm.description)
async def process_description(message: Message, state: FSMContext):
    if len(message.text) > 300:
        await message.answer("❗ Пожалуйста, уложитесь в 300 символов. Попробуйте снова:")
        return
    await state.update_data(description=message.text)
    await message.answer("6️⃣ Как принять участие? (ссылка, телефон, форма и т.д.):")
    await state.set_state(EventForm.participation)

@router.message(EventForm.participation)
async def process_participation(message: Message, state: FSMContext):
    await state.update_data(participation=message.text)
    data = await state.get_data()
    summary = (
        f"✅ Проверьте информацию:\n\n"
        f"*Название:* {data['name']}\n"
        f"*Дата:* {data['date']}\n"
        f"*Время:* {data['time']}\n"
        f"*Регион:* {data['region']}\n"
        f"*Описание:* {data['description']}\n"
        f"*Как принять участие:* {data['participation']}\n\n"
        f"Если всё верно — напишите **отправить**.\n"
        f"Чтобы изменить — напишите «изменить [поле]»."
    )
    await message.answer(summary, parse_mode="Markdown")
    await state.set_state(EventForm.confirm)

@router.message(EventForm.confirm)
async def handle_confirmation(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if "отправить" in text:
        data = await state.get_data()
        try:
            row = [
                data.get("name", ""),
                data.get("date", ""),
                data.get("time", ""),
                data.get("region", ""),
                data.get("description", ""),
                data.get("participation", ""),
                datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            ]
            sheet.append_row(row)
            await message.answer("📨 Спасибо! Мероприятие добавлено в базу.")
        except Exception as e:
            logging.error(f"Ошибка Google Sheets: {e}")
            await message.answer("⚠️ Не удалось сохранить. Попробуйте позже.")
        await state.clear()
    elif "изменить" in text:
        field_map = {
            "название": "name",
            "дата": "date",
            "время": "time",
            "регион": "region",
            "описание": "description",
            "участие": "participation",
        }
        for key, val in field_map.items():
            if key in text:
                prompts = {
                    "name": "Введите новое название:",
                    "date": "Введите новую дату:",
                    "time": "Введите новое время:",
                    "region": "Введите новый регион:",
                    "description": "Новое описание (до 300 символов):",
                    "participation": "Как принять участие:",
                }
                await state.set_state(getattr(EventForm, val))
                await message.answer(prompts[val])
                return
        await message.answer("Не понял. Пример: «изменить дата».")
    else:
        await message.answer("Напишите «отправить» или «изменить [поле]».")

# --- Запуск через webhook ---
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"✅ Webhook установлен: {WEBHOOK_URL}")

def main():
    dp.include_router(router)
    dp.startup.register(on_startup)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()