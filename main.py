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
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY, SUIT_NAMES, PREDICTION_OFFSET
)

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
# VÉRIFICATION INITIALE
# ==========================================
if not API_ID or API_ID == 0:
    logger.error("API_ID manquant dans la configuration")
    sys.exit(1)
if not API_HASH:
    logger.error("API_HASH manquant dans la configuration")
    sys.exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant dans la configuration")
    sys.exit(1)

# ==========================================
# INITIALISATION DU CLIENT
# ==========================================
session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# Variables d'état globales
active_prediction = None
recent_games = {}
processed_messages = set()
current_game_number = 0
waiting_for_finalization = False
source_channel_ok = False
prediction_channel_ok = False
start_time = datetime.now()

# ==========================================
# FONCTIONS UTILITAIRES DE TEXTE
# ==========================================

def extract_game_number(message: str):
    """Extrait le numéro de jeu type #N 12345"""
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_parentheses_groups(message: str):
    """Extrait les cartes entre parenthèses (8♠ 9♥)"""
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(text: str) -> str:
    """Normalise les différents émojis de cartes"""
    normalized = text.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def get_first_card_suit(first_group: str) -> str:
    """Détermine la couleur de la première carte du premier groupe"""
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
    """Vérifie si le message contient les signes de fin de partie"""
    if '⏰' in message:
        return False
    return '✅' in message or '🔰' in message

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    """Vérifie si une couleur spécifique est présente dans un groupe de cartes"""
    normalized = normalize_suits(group_str)
    target_normalized = normalize_suits(target_suit)
    for suit in ALL_SUITS:
        if suit in target_normalized and suit in normalized:
            return True
    return False

# ==========================================
# LOGIQUE CORE DE PRÉDICTION
# ==========================================

async def send_prediction(game_number: int, suit: str):
    global active_prediction, waiting_for_finalization
    
    try:
        target_game = game_number + PREDICTION_OFFSET
        suit_name = get_suit_full_name(suit)
        
        prediction_msg = (
            f"📡 **PRÉDICTION #{target_game}**\n"
            f"🎯 Couleur: {suit} {suit_name}\n"
            f"🌪️ Statut: ⏳ EN COURS"
        )

        msg_id = 0
        if prediction_channel_ok:
            try:
                # Utilisation de l'entité résolue au démarrage
                pred_msg = await client.send_message(PREDICTION_CHANNEL_ID, prediction_msg)
                msg_id = pred_msg.id
                logger.info(f"✅ Prédiction envoyée: Jeu #{target_game}")
            except Exception as e:
                logger.error(f"❌ Erreur envoi: {e}")
                return None
        else:
            logger.warning("⚠️ Canal de prédiction non accessible")
            return None

        active_prediction = {
            'source_game': game_number,
            'target_game': target_game,
            'suit': suit,
            'message_id': msg_id,
            'status': '⏳',
            'check_count': 0,
            'created_at': datetime.now().isoformat()
        }
        waiting_for_finalization = True
        return msg_id

    except Exception as e:
        logger.error(f"Erreur send_prediction: {e}")
        return None

async def update_prediction_status(target_game: int, new_status: str, check_count: int = 0):
    global active_prediction, waiting_for_finalization
    
    if not active_prediction or active_prediction['target_game'] != target_game:
        return

    suit = active_prediction['suit']
    suit_name = get_suit_full_name(suit)
    
    if new_status == 'success':
        emojis = {0: '0️⃣', 1: '1️⃣', 2: '2️⃣', 3: '3️⃣'}
        status_emoji = f"🍯✅{emojis.get(check_count, '✅')}"
    else:
        status_emoji = '😶❌'

    updated_msg = (
        f"📡 **PRÉDICTION #{target_game}**\n"
        f"🎯 Couleur: {suit} {suit_name}\n"
        f"🌪️ Statut: {status_emoji}"
    )

    try:
        await client.edit_message(PREDICTION_CHANNEL_ID, active_prediction['message_id'], updated_msg)
        if new_status in ['success', 'failed']:
            active_prediction = None
            waiting_for_finalization = False
            logger.info(f"🏁 Résultat #{target_game}: {new_status}")
    except Exception as e:
        logger.error(f"❌ Erreur edit: {e}")

async def check_prediction_result(game_number: int, first_group: str):
    if not active_prediction:
        return
    
    target = active_prediction['target_game']
    suit = active_prediction['suit']
    
    if game_number == target:
        if has_suit_in_group(first_group, suit):
            await update_prediction_status(target, 'success', 0)
        else:
            active_prediction['check_count'] = 1
            
    elif target < game_number <= target + 3:
        count = game_number - target
        if has_suit_in_group(first_group, suit):
            await update_prediction_status(target, 'success', count)
        elif count >= 3:
            await update_prediction_status(target, 'failed')

# ==========================================
# TRAITEMENT DES MESSAGES
# ==========================================

async def process_message(message_text: str, is_finalized: bool = False):
    global current_game_number, waiting_for_finalization, active_prediction
    
    try:
        game_number = extract_game_number(message_text)
        if not game_number:
            return

        current_game_number = game_number
        groups = extract_parentheses_groups(message_text)
        if not groups:
            return

        # Gestion des résultats (Messages édités avec ✅)
        if waiting_for_finalization and is_finalized:
            await check_prediction_result(game_number, groups[0])

        # Création d'une nouvelle prédiction
        if not waiting_for_finalization and active_prediction is None:
            suit = get_first_card_suit(groups[0])
            if suit:
                await send_prediction(game_number, suit)

    except Exception as e:
        logger.error(f"Erreur process_message: {e}")

@client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
async def handle_new_message(event):
    await process_message(event.message.message, is_finalized=False)

@client.on(events.MessageEdited(chats=SOURCE_CHANNEL_ID))
async def handle_edited_message(event):
    await process_message(event.message.message, is_finalized=is_message_finalized(event.message.message))

# ==========================================
# COMMANDES DU BOT
# ==========================================

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    await event.respond("🤖 **JOKER BOT PRÉDICTION**\nActif et en attente de signaux...")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.sender_id != ADMIN_ID: return
    uptime = datetime.now() - start_time
    msg = (
        f"📊 **STATUT DU SYSTÈME**\n"
        f"⏱ Uptime: {str(uptime).split('.')[0]}\n"
        f"🎮 Dernier Jeu: #{current_game_number}\n"
        f"📡 Source: {'✅' if source_channel_ok else '❌'}\n"
        f"🔮 Pred: {'✅' if prediction_channel_ok else '❌'}\n"
        f"📏 Offset: +{PREDICTION_OFFSET}"
    )
    await event.respond(msg)

@client.on(events.NewMessage(pattern='/checkchannels'))
async def cmd_checkchannels(event):
    global source_channel_ok, prediction_channel_ok
    if event.sender_id != ADMIN_ID: return
    
    status = "🔍 **Vérification...**\n"
    try:
        await client.get_entity(SOURCE_CHANNEL_ID)
        source_channel_ok = True
        status += "✅ Source: OK\n"
    except: status += "❌ Source: Erreur\n"
    
    try:
        # Correction PeerChannel pour forcer la reconnaissance de l'ID
        clean_id = int(str(PREDICTION_CHANNEL_ID).replace('-100', ''))
        await client.get_entity(PeerChannel(clean_id))
        prediction_channel_ok = True
        status += "✅ Prédiction: OK"
    except: status += "❌ Prédiction: Introuvable"
    
    await event.respond(status)

# ==========================================
# SERVEUR WEB (HEALTCHECK)
# ==========================================

async def web_handler(request):
    return web.Response(text="Bot is running", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', web_handler)
    app.router.add_get('/health', web_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Serveur Web sur port {PORT}")

# ==========================================
# BOUCLE PRINCIPALE
# ==========================================

async def main():
    global source_channel_ok, prediction_channel_ok
    
    # 1. Lancer le serveur web pour Render
    await start_web_server()
    
    # 2. Démarrer le client Telegram
    try:
        logger.info("Connexion à Telegram...")
        await client.start(bot_token=BOT_TOKEN)
        
        # 3. Forcer la résolution des canaux au démarrage
        try:
            await client.get_entity(SOURCE_CHANNEL_ID)
            source_channel_ok = True
        except: logger.error("Échec accès canal SOURCE")
        
        try:
            p_id = int(str(PREDICTION_CHANNEL_ID).replace('-100', ''))
            await client.get_entity(PeerChannel(p_id))
            prediction_channel_ok = True
            logger.info("Canal PRÉDICTION identifié avec succès")
        except: logger.error("Échec accès canal PRÉDICTION")

        logger.info("--- BOT OPÉRATIONNEL ---")
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
    finally:
        await client.disconnect()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
