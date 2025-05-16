#!/usr/bin/env python3

import asyncio
import httpx
import logging
import os
import json
from datetime import datetime
from telegram import (
   Update,
   InlineKeyboardButton,
   InlineKeyboardMarkup,
   ReplyKeyboardMarkup,
   ReplyKeyboardRemove,
   InputMediaPhoto,
   BotCommand,
   BotCommandScopeAllPrivateChats,
)

from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.error import (
   TelegramError,
   TimedOut,
)

ITEMS: dict[str, str] = {}
logger = logging.getLogger(__name__)
logging.basicConfig(format="%(asctime)s — %(levelname)s — %(name)s — %(message)s",
               level=logging.WARNING)

# Configuration
token_env = os.getenv("BOT_TOKEN")
TOKEN = token_env if token_env else "7581997920:AAF_Yj8x221uOtXmCl8Y0kLbgsws3UQruFQ"
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001416437011"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "298224059, 535215996").split(',')))

REQUESTS_FILE = "requests.json"
ITEMS_FILE = "items.json"

# Conversation states
(
   MENU,
   ADD_MEDIA,
   TO_DESCRIPTION,
   DESCRIPTION_NAME,
   DESCRIPTION_COMPOSITION,
   DESCRIPTION_WIDTH,
   DESCRIPTION_PRICE,
   DESCRIPTION_STOCK,
   CONFIRM_POST,
   VIEW_REQUESTS,
   BUY_CONFIRM,
   BUY_QUANTITY,
   DIRECT_ART,
   DIRECT_QUANTITY,
) = range(14)

# JSON persistence
def load_json(path, default):
   if os.path.exists(path):
      with open(path, 'r', encoding='utf-8') as f:
         return json.load(f)
   return default

def save_json(path, data):
   with open(path, 'w', encoding='utf-8') as f:
      json.dump(data, f, ensure_ascii=False, indent=2)
      
ITEMS = load_json(ITEMS_FILE, {})
      
# /start handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   args = context.args
   article = None
   
   #3.1 сращивание /start из deeplink и /start от синей кнопки начала диалога
   
   # 1) Первый /start buy_<article> или автоматический после нажатия кнопки
   if args and args[0].startswith("buy_"):
      article = args[0].split("_", 1)[1]
      # сохраняем первый payload, чтобы поймать второй пустой /start
      context.user_data['pending_article'] = article
      
   # 2) Второй пустой /start (после нажатия синей кнопки), если pending_article есть
   if article is None and 'pending_article' in context.user_data:
      article = context.user_data['pending_article']
      
   # 3) Если у нас есть артикул хоть из одного из двух вариантов
   if article is not None:
      name = ITEMS.get(article, "<неизвестная ткань>")
      await update.message.reply_text(
         f"Вы выбрали ткань «{name}» (арт. {article}).\nПодтвердите покупку:",
         parse_mode="Markdown",
         reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, продолжить", callback_data=f"cont|{article}"),
            InlineKeyboardButton("Отмена",        callback_data="cancel_buy"),
         ]])
      )
      return BUY_CONFIRM
   
   # 3.2. Иначе старый код для админа и подписчика
   user_id = update.effective_user.id
   if user_id in ADMIN_IDS:
      keyboard = [["Добавить ткань", "Входящие заявки"]]
      await update.message.reply_text(
         "Выберите действие:",
         reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
      )
      return MENU
   else:
      await update.message.reply_text(
         "Введите артикул интересующей ткани:",
         reply_markup=ReplyKeyboardRemove(),
      )
      return DIRECT_ART
   
# Admin menu
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   choice = update.message.text
   if choice == "Добавить ткань":
      await update.message.reply_text(
         "Пришлите фото и/или видео ткани (1–10 файлов):",
         reply_markup=ReplyKeyboardRemove(),
      )
      context.chat_data['media'] = []
      context.chat_data.pop('status_msg_id', None)
      return ADD_MEDIA
   if choice == "Входящие заявки":
      return await view_requests(update, context)
   await update.message.reply_text("Пожалуйста, выберите опцию.")
   return MENU


# Receive media in one message
async def media_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   if update.message.photo:
      fid = update.message.photo[-1].file_id
   elif update.message.video:
      fid = update.message.video.file_id
   else:
      await update.message.reply_text("Пожалуйста, пришлите фото/видео.")
      return ADD_MEDIA
   
   media = context.chat_data.get('media', [])
   media.append(fid)
   context.chat_data['media'] = media
   
   markup = InlineKeyboardMarkup([
      [InlineKeyboardButton("Перейти к описанию", callback_data="to_desc")],
      [InlineKeyboardButton("Отмена",            callback_data="cancel")]
   ])
   
   chat_id = update.effective_chat.id
   count = len(media)
   text  = f"Получено файлов: {count}/10."
   if 'status_msg_id' in context.chat_data:
      # редакция уже существующего сообщения
      await context.bot.edit_message_text(
         chat_id=chat_id,
         message_id=context.chat_data['status_msg_id'],
         text=text,
         reply_markup=markup
      )
   else:
      # первое сообщение-счётчик
      msg = await update.message.reply_text(
         text,
         reply_markup=markup
      )
      context.chat_data['status_msg_id'] = msg.message_id
   
   return ADD_MEDIA



# Proceed to description or cancel
async def to_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   query = update.callback_query
   await query.answer()
   await query.edit_message_text("Введите название ткани:")
   return DESCRIPTION_NAME


# Description handlers
async def desc_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   context.chat_data['name'] = update.message.text
   await update.message.reply_text("Введите состав ткани:")
   return DESCRIPTION_COMPOSITION

async def desc_composition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   context.chat_data['composition'] = update.message.text
   await update.message.reply_text("Введите ширину ткани в см (числом):")
   return DESCRIPTION_WIDTH

async def desc_width(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   if not update.message.text.isdigit():
      await update.message.reply_text("Пожалуйста, введите ширину цифрами:")
      return DESCRIPTION_WIDTH
   context.chat_data['width'] = update.message.text
   await update.message.reply_text("Введите цену за метр в тенге (числом):")
   return DESCRIPTION_PRICE

async def desc_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   if not update.message.text.isdigit():
      await update.message.reply_text("Пожалуйста, введите цену цифрами:")
      return DESCRIPTION_PRICE
   context.chat_data['price'] = update.message.text
   await update.message.reply_text("Введите метраж в наличии:")
   return DESCRIPTION_STOCK

async def desc_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   context.chat_data['stock'] = update.message.text
   article = f"TES{datetime.now().strftime('%f')[:5]}"
   context.chat_data['article'] = article
   media_group = [InputMediaPhoto(media=fid) for fid in context.chat_data['media']]
   await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group)
   text_preview = (
      f"*{context.chat_data['name'].upper()}*\n"
      f"`Артикул: {article}`\n"
      f"Состав: {context.chat_data['composition'].capitalize()}\n"
      f"Ширина: {context.chat_data['width']} см.\n"
      f"Цена: {context.chat_data['price']} тг/м\n"
      f"Наличие: {context.chat_data['stock']}"
   )
   kb = [
      [InlineKeyboardButton("Запостить", callback_data="post")],
      [InlineKeyboardButton("Отмена", callback_data="cancel")],
   ]
   await context.bot.send_message(chat_id=update.effective_chat.id, text=text_preview, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
   return CONFIRM_POST

# Confirm and publish
async def confirm_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   query = update.callback_query
   await query.answer()
   if query.data == "cancel":
      await query.edit_message_text("Операция отменена.")
      # Send main menu
      keyboard = [["Добавить ткань", "Входящие заявки"]]
      await context.bot.send_message(
         chat_id=query.from_user.id,
         text="Выберите действие:",
         reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
      )
      
      return MENU
   
   
   # Publish media
   media_group = [InputMediaPhoto(media=fid) for fid in context.chat_data['media']]
   
   
   text_post = (
      f"*{context.chat_data['name'].upper()}*\n"
      f"`Артикул: {context.chat_data['article']}`\n"
      f"Состав: {context.chat_data['composition'].capitalize()}\n"
      f"Ширина: {context.chat_data['width']} см.\n"
      f"Цена: {context.chat_data['price']} тг/м\n"
      f"Наличие: {context.chat_data['stock']}\n"
   )
   bot_username = (await context.bot.get_me()).username
   article = context.chat_data['article']
   name    = context.chat_data['name']
   # сохраняем в глобальный словарь
   ITEMS[article] = name
   save_json(ITEMS_FILE, ITEMS)
   
   deeplink = f"https://t.me/{bot_username}?start=buy_{article}"
   
   buy_btn = InlineKeyboardButton(
      "Купить",
      url=deeplink
   )
   
   tasks = [
      context.bot.send_media_group(chat_id=CHANNEL_ID, media=media_group),
      context.bot.send_message(
         chat_id=CHANNEL_ID,
         text=text_post,
         parse_mode='Markdown',
         reply_markup=InlineKeyboardMarkup([[buy_btn]])
      ),
   ]
   results = await asyncio.gather(*tasks, return_exceptions=True)
   
   for i, res in enumerate(results):
      if isinstance(res, Exception):
         logger.error(f"Publish task #{i} failed: {res!r}")
         
         
   # в блоке finally или просто после gather
   await query.edit_message_text("✅ Ткань опубликована.")
   keyboard = [["Добавить ткань", "Входящие заявки"]]
   await context.bot.send_message(
      chat_id=query.from_user.id,
      text="Выберите действие:",
      reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
   )
   
   context.chat_data.pop('media', None)
   context.chat_data.pop('status_msg_id', None)
   
   return MENU
   
   
# View requests
async def view_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   # 1.1. Загрузить список заявок
   requests = load_json(REQUESTS_FILE, [])
   
   # 1.2. Собрать клавиатуру: для каждой заявки кнопка «Удалить …», а внизу – одна «Назад»
   keyboard = []
   for idx, req in enumerate(requests):
      # кнопка удаления конкретной заявки
      keyboard.append([
         InlineKeyboardButton(
            f"Удалить {req['name']} (арт. {req['article']}): {req['quantity']} м от {req['user']}",
            callback_data=f"del|{idx}"
         )
      ])
   # единственная кнопка «Назад» внизу
   keyboard.append([
      InlineKeyboardButton("Назад", callback_data="back")
   ])
   
   # 1.3. Текст сообщения
   text = "Входящие заявки:" if requests else "Нет входящих заявок."
   
   # 1.4. Отправить или отредактировать сообщение
   # если вы всегда хотите новое сообщение:
   await update.message.reply_text(
      text,
      reply_markup=InlineKeyboardMarkup(keyboard)
   )
   
   # 1.5. Завершаем и устанавливаем state
   return VIEW_REQUESTS

# Handle delete
async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   query = update.callback_query
   await query.answer()
   
   # 1) Обработка «Назад»
   if query.data == "back":
      await query.edit_message_text("Возврат в меню.")
      keyboard = [["Добавить ткань", "Входящие заявки"]]
      await context.bot.send_message(
         chat_id=query.from_user.id,
         text="Выберите действие:",
         reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
      )
      return MENU
   
   # 2) Ветка удаления
   await query.edit_message_text("⏳ Удаляем заявку…")
   _, idx_str = query.data.split("|", 1)
   idx = int(idx_str)
   requests = load_json(REQUESTS_FILE, [])
   removed = requests.pop(idx)
   save_json(REQUESTS_FILE, requests)
   
   # 3) Собираем клавиатуру с удалёнными и «Назад»
   keyboard = []
   for i, req in enumerate(requests):
      keyboard.append([
         InlineKeyboardButton(f"Удалить {req['article']} от {req['user']}", callback_data=f"del|{i}")
      ])
   keyboard.append([InlineKeyboardButton("Назад", callback_data="back")])
   
   # 4) Отправляем обновлённый список
   text = "Входящие заявки:" if requests else "Нет входящих заявок."
   await context.bot.send_message(
      chat_id=query.from_user.id,
      text=text,
      reply_markup=InlineKeyboardMarkup(keyboard)
   )
   return VIEW_REQUESTS

# Buy callback
async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   query = update.callback_query
   await query.answer()
   _, article = query.data.split("|")
   
   # берём текст описания прямо из сообщения, где пользователь нажал «Купить»
   text = query.message.text
   
   # Correct inline keyboard: list of lists
   keyboard = [[
      InlineKeyboardButton("Да, продолжить", callback_data=f"cont|{article}"),
      InlineKeyboardButton("Отмена", callback_data="cancel_buy")
   ]]
   await context.bot.send_message(
      chat_id=query.from_user.id,
      text=f"Вы выбрали: {text}",
      parse_mode='Markdown',
      reply_markup=InlineKeyboardMarkup(keyboard)
   )
   return BUY_CONFIRM

# Confirm buy
async def buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   query = update.callback_query
   await query.answer()
   # больше не нужно помнить старый артикул
   context.user_data.pop('pending_article', None)
   
   if query.data == "cancel_buy":
      await query.edit_message_text("Покупка отменена.")
      return ConversationHandler.END
   _, article = query.data.split("|")
   context.user_data['article'] = article
   await context.bot.send_message(chat_id=query.from_user.id, text="Сколько метров хотите приобрести? Введите целое число или 'Все'.")
   
   return BUY_QUANTITY

# Handle buy quantity
async def buy_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   qty = update.message.text
   article = context.user_data.get('article')
   user = update.effective_user.username or update.effective_user.full_name
   requests = load_json(REQUESTS_FILE, [])
   requests.append({'date':datetime.now().strftime("%Y-%m-%d %H:%M:%S"),'name':     ITEMS.get(article, ""), 'article':article, 'quantity':qty, 'user':user})
   save_json(REQUESTS_FILE, requests)
   # больше не нужно помнить старый артикул
   context.user_data.pop('pending_article', None)
   
   await update.message.reply_text("Спасибо, Ваша заявка получена. Мы скоро свяжемся с вами.")
   for aid in ADMIN_IDS:
      await context.bot.send_message(chat_id=aid, text=f"Новая заявка от {user}: {article}, {qty} метров.")
      
   return ConversationHandler.END

# Direct art
async def direct_art(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   art = update.message.text.strip()

   context.user_data['article'] = art
   await update.message.reply_text("Сколько метров хотите приобрести? Введите целое число или 'Все'.")
   return DIRECT_QUANTITY

# Direct quantity
async def direct_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   qty = update.message.text
   article = context.user_data.get('article')
   user = update.effective_user.username or update.effective_user.full_name
   requests = load_json(REQUESTS_FILE, [])
   requests.append({'date':datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'article':article, 'quantity':qty, 'user':user})
   save_json(REQUESTS_FILE, requests)
   await update.message.reply_text("Спасибо, Ваша заявка получена. Мы скоро свяжемся с вами.")
   for aid in ADMIN_IDS:
      await context.bot.send_message(chat_id=aid, text=f"Новая заявка от {user}: {article}, {qty} метров.")
   return ConversationHandler.END

# Cancel handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
   # Закрываем текущий колбэк или сообщение
   if update.callback_query:
      await update.callback_query.answer()
      await update.callback_query.edit_message_text("Операция отменена.")
   else:
      await update.message.reply_text("Операция прервана.")
      
   # Решаем, куда вернуть пользователя
   user_id = update.effective_user.id
   if user_id in ADMIN_IDS:
      # Админу показываем главное меню
      keyboard = [["Добавить ткань", "Входящие заявки"]]
      await context.bot.send_message(
         chat_id=update.effective_chat.id,
         text="Выберите действие:",
         reply_markup=ReplyKeyboardMarkup(
            keyboard, one_time_keyboard=True, resize_keyboard=True
         ),
      )
      return MENU
   else:
      # Обычный подписчик — заканчиваем разговор
      return ConversationHandler.END
   
#Создание кнопки Menu в чате с ботом
async def setup_commands(application):
   await application.bot.set_my_commands(
      commands=[
         BotCommand("start",  "Запустить бота / вернуться в меню"),
         BotCommand("cancel", "Отменить текущую операцию"),
      ],
      scope=BotCommandScopeAllPrivateChats(),
   )
   

# Main
if __name__ == '__main__':
    request = HTTPXRequest(write_timeout=60.0, read_timeout=60.0, connect_timeout=30.0)
   app = (
      ApplicationBuilder()
         .token(TOKEN)
         # Таймаут на установление TCP-соединения (по умолчанию 5.0)
         .connect_timeout(10.0)
         # Таймаут чтения ответа от Telegram (по умолчанию 5.0)
         .read_timeout(30.0)
         # Таймаут записи запроса к Telegram (по умолчанию 5.0)
         .write_timeout(30.0)
         # Таймаут записи медиа (для send_media_group; по умолчанию 20.0)
         
         # Таймаут освобождения сокета в пуле (по умолчанию 1.0)
         .pool_timeout(5.0)
         .post_init(setup_commands)
         .build()
   )
   
   conv = ConversationHandler(
      entry_points=[
         CommandHandler('start', start),

      ],
      
      states={
         MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler)],
         ADD_MEDIA: [
                  MessageHandler(filters.PHOTO|filters.VIDEO, media_receiver),
                  CallbackQueryHandler(to_description, pattern='^to_desc$'),
                  CallbackQueryHandler(cancel,        pattern='^cancel$'),
         ],
         TO_DESCRIPTION: [CallbackQueryHandler(to_description, pattern='^(to_desc|cancel)$')],
         DESCRIPTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_name)],
         DESCRIPTION_COMPOSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_composition)],
         DESCRIPTION_WIDTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_width)],
         DESCRIPTION_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_price)],
         DESCRIPTION_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, desc_stock)],
         CONFIRM_POST: [CallbackQueryHandler(confirm_post, pattern='^(post|cancel)$')],
         VIEW_REQUESTS: [CallbackQueryHandler(handle_delete, pattern='^(del|back)\\|?\\d*$')],
         BUY_CONFIRM: [CallbackQueryHandler(buy_confirm, pattern=r'^(cont\|.+|cancel_buy)$')],
         BUY_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_quantity)],

         BUY_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_quantity)],
         DIRECT_ART: [MessageHandler(filters.TEXT & ~filters.COMMAND, direct_art)],
         DIRECT_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, direct_quantity)],
      },
      fallbacks=[CommandHandler('cancel', cancel)],
         per_chat=True,
         per_user=False,
   )
   
   app.add_handler(conv)

   app.run_polling()
   
   

if __name__ == '__main__':
    request = HTTPXRequest(write_timeout=60.0, read_timeout=60.0, connect_timeout=30.0)
    import asyncio
    asyncio.run(application.run_polling())
