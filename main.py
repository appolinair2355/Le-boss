import os
import asyncio
import re
import logging
import sys
import json
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel
from aiohttp import web

# Importation sécurisée de la config
try:
    from config import (
        API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
        SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
        SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY, SUIT_NAMES, PREDICTION_OFFSET
    )
except ImportError:
    logger.error("Fichier config.py introuvable !")
    sys.exit(1)

# ==========================================
# CONFIGURATION DU LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ==========================================
# VARIABLES D'ÉTAT GLOBALES
# ==========================================
# On utilise les IDs de config comme base, mais on pourra les changer
CURRENT_SOURCE_ID = SOURCE_CHANNEL_ID
CURRENT_PRED_ID = PREDICTION_CHANNEL_ID

active_prediction = None
recent_games = {}
processed_messages = set()
current_game_number = 0
waiting_for_finalization = False
source_channel_ok = False
prediction_channel_ok = False
start_time = datetime.now()

client = TelegramClient(StringSession(os.getenv('TELEGRAM_SESSION', '')), API_ID, API_HASH)

# ==========================================
# FONCTIONS DE TRAITEMENT
# ==========================================

def extract_game_number(message: str):
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    return int(match.group(1)) if match else None

def extract_parentheses_groups(message: str):
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(text: str) -> str:
    n = text.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    return n.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')

def get_first_card_suit(group: str) -> str:
    norm = normalize_suits(group)
    match = re.search(r"[0-9AJQKajqk]+\s*([♠♥♦♣])", norm)
    if match:
        suit = match.group(1)
        return SUIT_DISPLAY.get(suit, suit)
    for s in ALL_SUITS:
        if s in norm: return SUIT_DISPLAY.get(s, s)
    return None

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    norm = normalize_suits(group_str)
    target = normalize_suits(target_suit)
    return any(s in target and s in norm for s in ALL_SUITS)

# ==========================================
# LOGIQUE DE PRÉDICTION
# ==========================================

async def send_prediction(game_number: int, suit: str):
    global active_prediction, waiting_for_finalization
    try:
        target_game = game_number + PREDICTION_OFFSET
        suit_name = SUIT_NAMES.get(suit, suit)
        msg = f"📡 **PRÉDICTION #{target_game}**\n🎯 Couleur: {suit} {suit_name}\n🌪️ Statut: ⏳ EN COURS"

        if prediction_channel_ok:
            pred_msg = await client.send_message(CURRENT_PRED_ID, msg)
            active_prediction = {
                'source_game': game_number, 'target_game': target_game,
                'suit': suit, 'message_id': pred_msg.id, 'check_count': 0
            }
            waiting_for_finalization = True
            logger.info(f"✅ Prédiction envoyée pour #{target_game}")
    except Exception as e:
        logger.error(f"Erreur envoi: {e}")

async def update_status(target_game, status, count=0):
    global active_prediction, waiting_for_finalization
    if not active_prediction or active_prediction['target_game'] != target_game: return
    
    emoji = f"🍯✅{ {0:'0️⃣', 1:'1️⃣', 2:'2️⃣', 3:'3️⃣'}.get(count, '✅') }" if status == 'success' else '😶❌'
    txt = f"📡 **PRÉDICTION #{target_game}**\n🎯 Couleur: {active_prediction['suit']} {SUIT_NAMES.get(active_prediction['suit'], '')}\n🌪️ Statut: {emoji}"
    
    try:
        await client.edit_message(CURRENT_PRED_ID, active_prediction['message_id'], txt)
        if status in ['success', 'failed']:
            active_prediction = None
            waiting_for_finalization = False
    except Exception as e: logger.error(f"Edit error: {e}")

# ==========================================
# COMMANDES ADMINISTRATEUR
# ==========================================

@client.on(events.NewMessage(pattern='/setpred'))
async def cmd_setpred(event):
    global CURRENT_PRED_ID, prediction_channel_ok
    if event.sender_id != ADMIN_ID: return
    
    args = event.text.split()
    if len(args) < 2:
        return await event.respond("❌ Usage: `/setpred -100xxxxxxxxxx`")
    
    try:
        new_id = int(args[1])
        # Test d'accès
        clean_id = int(str(new_id).replace('-100', ''))
        await client.get_entity(PeerChannel(clean_id))
        
        CURRENT_PRED_ID = new_id
        prediction_channel_ok = True
        await event.respond(f"✅ **Canal de prédiction mis à jour !**\nID: `{new_id}`")
        logger.info(f"Nouveau canal pred: {new_id}")
    except Exception as e:
        await event.respond(f"❌ Erreur: Le bot doit être admin du canal.\nDétail: {e}")

@client.on(events.NewMessage(pattern='/setsource'))
async def cmd_setsource(event):
    global CURRENT_SOURCE_ID, source_channel_ok
    if event.sender_id != ADMIN_ID: return
    
    args = event.text.split()
    if len(args) < 2:
        return await event.respond("❌ Usage: `/setsource -100xxxxxxxxxx`")
    
    try:
        new_id = int(args[1])
        await client.get_entity(new_id)
        CURRENT_SOURCE_ID = new_id
        source_channel_ok = True
        await event.respond(f"✅ **Canal source mis à jour !**\nID: `{new_id}`")
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.sender_id != ADMIN_ID: return
    uptime = str(datetime.now() - start_time).split('.')[0]
    msg = (f"📊 **STATUT**\n⏱ Uptime: {uptime}\n🎮 Jeu: #{current_game_number}\n"
           f"📡 Source: `{CURRENT_SOURCE_ID}` ({'✅' if source_channel_ok else '❌'})\n"
           f"🔮 Pred: `{CURRENT_PRED_ID}` ({'✅' if prediction_channel_ok else '❌'})\n"
           f"📏 Offset: +{PREDICTION_OFFSET}")
    await event.respond(msg)

# ==========================================
# GESTIONNAIRES DE FLUX
# ==========================================

async def process_flow(text, is_final):
    global current_game_number
    num = extract_game_number(text)
    if not num: return
    current_game_number = num
    groups = extract_parentheses_groups(text)
    if not groups: return

    if waiting_for_finalization and is_final:
        target = active_prediction['target_game']
        if num == target:
            if has_suit_in_group(groups[0], active_prediction['suit']): await update_status(target, 'success', 0)
            else: active_prediction['check_count'] = 1
        elif target < num <= target + 3:
            c = num - target
            if has_suit_in_group(groups[0], active_prediction['suit']): await update_status(target, 'success', c)
            elif c >= 3: await update_status(target, 'failed')
    
    elif not waiting_for_finalization and active_prediction is None:
        suit = get_first_card_suit(groups[0])
        if suit: await send_prediction(num, suit)

@client.on(events.NewMessage())
async def master_handler(event):
    if event.chat_id == CURRENT_SOURCE_ID:
        await process_flow(event.text, False)

@client.on(events.MessageEdited())
async def master_edit_handler(event):
    if event.chat_id == CURRENT_SOURCE_ID:
        final = '✅' in event.text or '🔰' in event.text
        await process_flow(event.text, final)

# ==========================================
# INITIALISATION ET LANCEMENT
# ==========================================

async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

async def main():
    global source_channel_ok, prediction_channel_ok
    await start_web()
    try:
        await client.start(bot_token=BOT_TOKEN)
        # Validation initiale
        try: 
            await client.get_entity(CURRENT_SOURCE_ID)
            source_channel_ok = True
        except: pass
        try:
            c_id = int(str(CURRENT_PRED_ID).replace('-100', ''))
            await client.get_entity(PeerChannel(c_id))
            prediction_channel_ok = True
        except: pass
        
        logger.info("Bot prêt.")
        await client.run_until_disconnected()
    finally: await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
