import os
import requests
import sqlite3
from datetime import datetime, time
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
import logging

# Загрузка переменных окружения из .env файла
load_dotenv()

# Конфигурация API Edamam и Telegram бота
EDAMAM_APP_ID = os.getenv('EDAMAM_APP_ID')
EDAMAM_APP_KEY = os.getenv('EDAMAM_APP_KEY')
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not all([EDAMAM_APP_ID, EDAMAM_APP_KEY, BOT_TOKEN]):
    raise ValueError("Пожалуйста, убедитесь, что все необходимые переменные окружения заданы в .env файле.")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация переводчика
translator = GoogleTranslator(source='ru', target='en')

# Подключение к SQLite базе данных
conn = sqlite3.connect('nutrition.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        chat_id INTEGER
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS entries (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        date TEXT,
        product TEXT,
        amount REAL,
        calories REAL,
        proteins REAL,
        fats REAL,
        carbs REAL,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )
''')
conn.commit()

# Функция для получения КБЖУ через API Edamam
def get_nutrition(product, amount):
    url = 'https://api.edamam.com/api/nutrition-data'
    params = {
        'app_id': EDAMAM_APP_ID,
        'app_key': EDAMAM_APP_KEY,
        'ingr': f"{amount}g {product}"
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get('totalNutrients'):
            nutrients = data['totalNutrients']
            calories = data.get('calories', 0)
            proteins = nutrients.get('PROCNT', {}).get('quantity', 0)
            fats = nutrients.get('FAT', {}).get('quantity', 0)
            carbs = nutrients.get('CHOCDF', {}).get('quantity', 0)
            return {
                'калории': calories,
                'белки': proteins,
                'жиры': fats,
                'углеводы': carbs
            }
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе к API Edamam: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при обработке ответа API Edamam: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id

    # Добавление пользователя в базу данных, если его там еще нет
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO users (user_id, chat_id) VALUES (?, ?)', (user_id, chat_id))
        conn.commit()
        logger.info(f"Добавлен новый пользователь: user_id={user_id}, chat_id={chat_id}")

    await update.message.reply_text(
        "Добро пожаловать в бот 'Здоровое питание'!\n"
        "Отправьте название продукта и его количество, например:\n"
        "яблоко 200\n"
        "рис 100\n"
        "куриная грудка 200\n\n"
        "Используйте команду /help для просмотра всех доступных команд."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Доступные команды:*\n\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать список доступных команд\n"
        "/report - Получить отчет о потребленных КБЖУ за сегодня\n\n"
        "💬 *Использование:* Отправьте название продукта и его количество, например:\n"
        "`яблоко 200`\n"
        "`рис 100`\n"
        "`куриная грудка 200`"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower().strip()
    if not user_input:
        await update.message.reply_text("Пожалуйста, введите название продукта и его количество, например: яблоко 200")
        return

    parts = user_input.split()
    if len(parts) < 2:
        await update.message.reply_text("Пожалуйста, введите продукт и его количество, например: яблоко 200")
        return
    try:
        amount = float(parts[-1])
        if amount <= 0 or amount > 5000:
            await update.message.reply_text("Пожалуйста, введите количество от 1 до 5000 грамм.")
            return
        product_russian = ' '.join(parts[:-1])
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите корректное количество, например: 100")
        return

    # Перевод названия продукта на английский
    try:
        product_english = translator.translate(product_russian)
        logger.info(f"Переведено '{product_russian}' на '{product_english}'")
    except Exception as e:
        logger.error(f"Ошибка при переводе продукта '{product_russian}': {e}")
        await update.message.reply_text("Произошла ошибка при переводе названия продукта. Попробуйте снова.")
        return

    if not product_english:
        logger.error(f"Перевод продукта '{product_russian}' не дал результата.")
        await update.message.reply_text("Не удалось перевести название продукта. Проверьте ввод и попробуйте снова.")
        return

    nutrition = get_nutrition(product_english, amount)
    if not nutrition:
        await update.message.reply_text("Извините, не удалось получить информацию о продукте. Проверьте название и попробуйте снова.")
        return

    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Добавление пользователя в базу данных, если его там еще нет
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO users (user_id, chat_id) VALUES (?, ?)', (user_id, chat_id))
        conn.commit()
        logger.info(f"Добавлен новый пользователь через add_product: user_id={user_id}, chat_id={chat_id}")

    # Вставка записи в таблицу entries
    cursor.execute('''
        INSERT INTO entries (user_id, date, product, amount, calories, proteins, fats, carbs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id,
        date_str,
        product_russian,  # Храним название на русском
        amount,
        nutrition['калории'],
        nutrition['белки'],
        nutrition['жиры'],
        nutrition['углеводы']
    ))
    conn.commit()
    logger.info(f"Добавлена запись: user_id={user_id}, product='{product_russian}', amount={amount}")

    await update.message.reply_text(
        f"Добавлено: {product_russian} - {amount} г\n"
        f"Калории: {nutrition['калории']:.2f} ккал\n"
        f"Белки: {nutrition['белки']:.2f} г, Жиры: {nutrition['жиры']:.2f} г, Углеводы: {nutrition['углеводы']:.2f} г"
    )

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    date_str = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT product, amount, calories, proteins, fats, carbs FROM entries
        WHERE user_id = ? AND date = ?
    ''', (user_id, date_str))
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("Вы ничего не записали сегодня.")
        return

    total_calories = total_proteins = total_fats = total_carbs = 0
    report_lines = [f"📊 *Отчет за {date_str}:*\n"]
    for product, amount, calories, proteins, fats, carbs in rows:
        total_calories += calories
        total_proteins += proteins
        total_fats += fats
        total_carbs += carbs
        report_lines.append(
            f"- {product.capitalize()}: {amount} г\n"
            f"  Калории: {calories:.2f} ккал, "
            f"Б: {proteins:.2f} г, Ж: {fats:.2f} г, У: {carbs:.2f} г\n"
        )

    report_lines.append(
        f"\n*Итого за день:*\n"
        f"Калории: {total_calories:.2f} ккал\n"
        f"Белки: {total_proteins:.2f} г\n"
        f"Жиры: {total_fats:.2f} г\n"
        f"Углеводы: {total_carbs:.2f} г"
    )
    report_text = ''.join(report_lines)
    await update.message.reply_text(report_text, parse_mode='Markdown')

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Извините, я не понимаю эту команду. Отправьте название продукта и его количество, например: яблоко 200"
    )

async def scheduled_daily_report(application):
    # Получение всех пользователей из базы данных
    cursor.execute('SELECT chat_id, user_id FROM users')
    users = cursor.fetchall()
    date_str = datetime.now().strftime("%Y-%m-%d")
    for (chat_id, user_id) in users:
        cursor.execute('''
            SELECT product, amount, calories, proteins, fats, carbs FROM entries
            WHERE user_id = ? AND date = ?
        ''', (user_id, date_str))
        rows = cursor.fetchall()
        if not rows:
            await application.bot.send_message(chat_id=chat_id, text="Вы ничего не записали сегодня.")
            continue

        total_calories = total_proteins = total_fats = total_carbs = 0
        report_lines = [f"📊 *Автоматический отчет за {date_str}:*\n"]
        for product, amount, calories, proteins, fats, carbs in rows:
            total_calories += calories
            total_proteins += proteins
            total_fats += fats
            total_carbs += carbs
            report_lines.append(
                f"- {product.capitalize()}: {amount} г\n"
                f"  Калории: {calories:.2f} ккал, "
                f"Б: {proteins:.2f} г, Ж: {fats:.2f} г, У: {carbs:.2f} г\n"
            )

        report_lines.append(
            f"\n*Итого за день:*\n"
            f"Калории: {total_calories:.2f} ккал\n"
            f"Белки: {total_proteins:.2f} г\n"
            f"Жиры: {total_fats:.2f} г\n"
            f"Углеводы: {total_carbs:.2f} г"
        )
        report_text = ''.join(report_lines)
        await application.bot.send_message(chat_id=chat_id, text=report_text, parse_mode='Markdown')
        logger.info(f"Отчет отправлен пользователю {user_id} (chat_id={chat_id})")

def main():
    # Инициализация приложения с использованием BOT_TOKEN
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Добавление обработчиков команд8
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("report", report))

    # Обработчик для добавления продуктов
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_product))

    # Обработчик неизвестных команд
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Планирование автоматического отчета в 23:59 каждый день
    async def job_callback(context: ContextTypes.DEFAULT_TYPE):
        await scheduled_daily_report(application)

    # Добавление задачи в Job Queue
    job_time = time(hour=23, minute=59, second=0)
    application.job_queue.run_daily(job_callback, job_time)

    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
