import os
import asyncio
import re
import logging
import sys
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY, SUIT_NAMES, PREDICTION_OFFSET,
    STATS_CHANNEL_ID
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Initialisation du client Telegram
session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# État global du bot
active_prediction = None
recent_games = {}
processed_messages = set()
current_game_number = 0
waiting_for_finalization = False
prediction_channel_ok = False
cycle_count = 1

def extract_game_number(message: str):
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    return int(match.group(1)) if match else None

def extract_first_parenthesis_group(message: str):
    match = re.search(r"\(([^)]*)\)", message)
    return match.group(1) if match else ""

def normalize_suits(text: str):
    return text.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥').replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')

def get_first_card_suit(first_group: str):
    normalized = normalize_suits(first_group)
    match = re.search(r"[0-9AJQKajqk]+\s*([♠♥♦♣])", normalized)
    if match:
        suit = match.group(1)
        return SUIT_DISPLAY.get(suit, suit)
    for suit in ALL_SUITS:
        if suit in normalized:
            return SUIT_DISPLAY.get(suit, suit)
    return None

def has_suit_in_first_parenthesis(message_text: str, target_suit: str):
    first_p = extract_first_parenthesis_group(message_text)
    if not first_p: return False
    norm_p = normalize_suits(first_p)
    norm_t = normalize_suits(target_suit)
    return any(s in norm_t and s in norm_p for s in ALL_SUITS)

def is_message_finalized(message: str):
    return '⏰' not in message and ('✅' in message or '🔰' in message)

async def reset_bot_state():
    global active_prediction, recent_games, processed_messages, current_game_number, waiting_for_finalization, cycle_count
    logger.info(f"🔄 Reset cycle {cycle_count}")
    active_prediction = None
    recent_games = {}
    processed_messages = set()
    current_game_number = 0
    waiting_for_finalization = False
    cycle_count += 1

async def send_prediction(game_number, suit):
    global prediction_channel_ok, active_prediction, waiting_for_finalization
    try:
        target_game = game_number + PREDICTION_OFFSET
        suit_name = SUIT_NAMES.get(suit, suit)
        msg = f"📡 PRÉDICTION #{target_game}\n🎯 Couleur: {suit} {suit_name}\n🌪️ Statut: ⏳ EN COURS"
        p_msg = await client.send_message(PREDICTION_CHANNEL_ID, msg)
        active_prediction = {'target_game': target_game, 'suit': suit, 'message_id': p_msg.id, 'status': '⏳'}
        waiting_for_finalization = True
        return p_msg.id
    except Exception as e:
        logger.error(f"Error prediction: {e}")
        return None

async def update_status(target_game, success, count=0):
    global active_prediction, waiting_for_finalization
    if not active_prediction or active_prediction['target_game'] != target_game: return
    emoji = f"🍯✅{count if count <= 3 else ''}" if success else "😶❌"
    suit = active_prediction['suit']
    msg = f"📡 PRÉDICTION #{target_game}\n🎯 Couleur: {suit} {SUIT_NAMES.get(suit, suit)}\n🌪️ Statut: {emoji}"
    try:
        await client.edit_message(PREDICTION_CHANNEL_ID, active_prediction['message_id'], msg)
    except: pass
    if success or count >= 3:
        active_prediction = None
        waiting_for_finalization = False

@client.on(events.NewMessage())
async def handle_new(event):
    chat_id = event.chat_id
    if chat_id == SOURCE_CHANNEL_ID:
        text = event.message.message
        num = extract_game_number(text)
        if num == 1440: await reset_bot_state()
        elif num and not active_prediction:
            suit = get_first_card_suit(extract_first_parenthesis_group(text))
            if suit: await send_prediction(num, suit)
    elif chat_id == STATS_CHANNEL_ID and active_prediction:
        text = event.message.message
        num = extract_game_number(text)
        if num and active_prediction['target_game'] <= num <= active_prediction['target_game'] + 3:
            if has_suit_in_first_parenthesis(text, active_prediction['suit']):
                await update_status(active_prediction['target_game'], True, num - active_prediction['target_game'])

async def main():
    async def health(request): return web.Response(text="OK")
    app = web.Application()
    app.router.add_get('/', health)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Replit port
    await web.TCPSite(runner, '0.0.0.0', 5000).start()
    # Render port
    if PORT != 5000:
        await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    await client.start(bot_token=BOT_TOKEN)
    logger.info("Bot started")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
