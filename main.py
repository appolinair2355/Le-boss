import os
import asyncio
import re
import logging
import sys
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY, SUIT_NAMES, PREDICTION_OFFSET
)

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Vérification des variables essentielles
if not API_ID or API_ID == 0:
    logger.error("API_ID manquant")
    exit(1)
if not API_HASH:
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant")
    exit(1)

session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# Variables d'état
active_prediction = None
recent_games = {}
processed_messages = set()
current_game_number = 0
waiting_for_finalization = False
source_channel_ok = False
prediction_channel_ok = False

# --- Fonctions Utilitaires ---

def extract_game_number(message: str):
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    return int(match.group(1)) if match else None

def extract_parentheses_groups(message: str):
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(text: str) -> str:
    normalized = text.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def get_first_card_suit(first_group: str) -> str:
    normalized = normalize_suits(first_group)
    match = re.search(r"[0-9AJQKajqk]+\s*([♠♥♦♣])", normalized)
    if match:
        suit = match.group(1)
        return SUIT_DISPLAY.get(suit, suit)
    for suit in ALL_SUITS:
        if suit in normalized:
            return SUIT_DISPLAY.get(suit, suit)
    return None

def get_suit_full_name(suit: str) -> str:
    return SUIT_NAMES.get(suit, suit)

def is_message_finalized(message: str) -> bool:
    if '⏰' in message:
        return False
    return '✅' in message or '🔰' in message

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    normalized = normalize_suits(group_str)
    target_normalized = normalize_suits(target_suit)
    for suit in ALL_SUITS:
        if suit in target_normalized and suit in normalized:
            return True
    return False

# --- Logique de Prédiction ---

async def send_prediction(game_number: int, suit: str):
    global active_prediction, waiting_for_finalization
    try:
        target_game = game_number + PREDICTION_OFFSET
        suit_name = get_suit_full_name(suit)
        prediction_msg = f"📡 PRÉDICTION #{target_game}\n🎯 Couleur: {suit} {suit_name}\n🌪️ Statut: ⏳ EN COURS"

        if prediction_channel_ok:
            pred_msg = await client.send_message(PREDICTION_CHANNEL_ID, prediction_msg)
            active_prediction = {
                'source_game': game_number,
                'target_game': target_game,
                'suit': suit,
                'message_id': pred_msg.id,
                'status': '⏳',
                'check_count': 0,
                'created_at': datetime.now().isoformat()
            }
            waiting_for_finalization = True
            logger.info(f"✅ Prédiction envoyée pour #{target_game}")
            return pred_msg.id
    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
    return None

async def update_prediction_status(target_game: int, new_status: str, check_count: int = 0):
    global active_prediction, waiting_for_finalization
    if not active_prediction or active_prediction['target_game'] != target_game:
        return

    suit = active_prediction['suit']
    suit_name = get_suit_full_name(suit)
    message_id = active_prediction['message_id']
    
    if new_status == 'success':
        emojis = {0: '0️⃣', 1: '1️⃣', 2: '2️⃣', 3: '3️⃣'}
        status_emoji = f"🍯✅{emojis.get(check_count, '')}"
    else:
        status_emoji = '😶❌'

    updated_msg = f"📡 PRÉDICTION #{target_game}\n🎯 Couleur: {suit} {suit_name}\n🌪️ Statut: {status_emoji}"

    try:
        await client.edit_message(PREDICTION_CHANNEL_ID, message_id, updated_msg)
        if new_status in ['success', 'failed']:
            active_prediction = None
            waiting_for_finalization = False
    except Exception as e:
        logger.error(f"❌ Erreur mise à jour: {e}")

async def check_prediction_result(game_number: int, first_group: str):
    if not active_prediction: return
    target = active_prediction['target_game']
    
    if game_number == target:
        if has_suit_in_group(first_group, active_prediction['suit']):
            await update_prediction_status(target, 'success', 0)
        else:
            active_prediction['check_count'] = 1
    elif target < game_number <= target + 3:
        count = game_number - target
        if has_suit_in_group(first_group, active_prediction['suit']):
            await update_prediction_status(target, 'success', count)
        elif count >= 3:
            await update_prediction_status(target, 'failed')

# --- Gestionnaires de messages ---

async def process_message(message_text: str, chat_id: int, is_finalized: bool = False):
    global current_game_number, waiting_for_finalization, active_prediction
    game_number = extract_game_number(message_text)
    if not game_number: return

    current_game_number = game_number
    message_hash = f"{game_number}_{message_text[:50]}"
    if message_hash in processed_messages: return
    processed_messages.add(message_hash)

    groups = extract_parentheses_groups(message_text)
    if not groups: return

    if waiting_for_finalization and is_finalized:
        await check_prediction_result(game_number, groups[0])
    
    if not waiting_for_finalization and active_prediction is None:
        suit = get_first_card_suit(groups[0])
        if suit:
            await send_prediction(game_number, suit)

@client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
async def handle_new(event):
    await process_message(event.message.message, event.chat_id)

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL_ID))
async def handle_edit(event):
    await process_message(event.message.message, event.chat_id, is_message_finalized(event.message.message))

# --- Commandes ---

@client.on(events.NewMessage(pattern='/checkchannels'))
async def cmd_checkchannels(event):
    global source_channel_ok, prediction_channel_ok
    await event.respond("🔍 Vérification...")
    try:
        await client.get_entity(SOURCE_CHANNEL_ID)
        source_channel_ok = True
    except Exception as e: source_channel_ok = False
    
    try:
        # On force la résolution de l'ID pour PeerChannel
        real_id = int(str(PREDICTION_CHANNEL_ID).replace('-100', ''))
        await client.get_entity(PeerChannel(real_id))
        prediction_channel_ok = True
    except Exception as e: prediction_channel_ok = False
    
    msg = f"Source: {'✅' if source_channel_ok else '❌'}\nPred: {'✅' if prediction_channel_ok else '❌'}"
    await event.respond(msg)

# --- Serveur Web ---

async def health_check(request): return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# --- Main ---

async def main():
    global source_channel_ok, prediction_channel_ok
    await start_web_server()
    try:
        await client.start(bot_token=BOT_TOKEN)
        # Résolution initiale des canaux
        try:
            await client.get_entity(SOURCE_CHANNEL_ID)
            source_channel_ok = True
        except: pass
        try:
            real_id = int(str(PREDICTION_CHANNEL_ID).replace('-100', ''))
            await client.get_entity(PeerChannel(real_id))
            prediction_channel_ok = True
        except: pass
        
        logger.info("Bot en ligne")
        await client.run_until_disconnected()
    finally:
        await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
