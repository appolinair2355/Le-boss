import os
import asyncio
import re
import logging
import sys
from datetime import datetime, timedelta
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

# ============================================================
# ÉTAT GLOBAL DU BOT
# ============================================================

active_prediction = None
recent_games = {}
processed_messages = set()
current_game_number = 0
waiting_for_finalization = False
prediction_channel_ok = False
cycle_count = 1

# Système de vérification
verification_counter = 0
MAX_VERIFICATIONS = 3

# Système de timeout 20 minutes
last_prediction_time = None
TIMEOUT_MINUTES = 20
timeout_task = None

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

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
    if not first_p: 
        return False
    norm_p = normalize_suits(first_p)
    norm_t = normalize_suits(target_suit)
    return any(s in norm_t and s in norm_p for s in ALL_SUITS)

def is_message_finalized(message: str):
    return '⏰' not in message and ('✅' in message or '🔰' in message)

# ============================================================
# GESTION DU TIMEOUT 20 MINUTES
# ============================================================

async def start_timeout_monitor():
    global timeout_task, last_prediction_time
    
    if timeout_task:
        timeout_task.cancel()
    
    last_prediction_time = datetime.now()
    timeout_task = asyncio.create_task(timeout_worker())
    logger.info(f"⏱️ Timeout monitor démarré (20 min)")

async def timeout_worker():
    global active_prediction, waiting_for_finalization, verification_counter
    
    while True:
        await asyncio.sleep(60)
        
        if last_prediction_time is None:
            continue
            
        elapsed = datetime.now() - last_prediction_time
        
        # Alerte à 15 minutes
        if elapsed > timedelta(minutes=15) and elapsed < timedelta(minutes=16):
            if active_prediction:
                logger.warning(f"⚠️ ALERTE: Prédiction bloquée depuis 15 min! #{active_prediction['target_game']}")
                try:
                    await client.send_message(
                        ADMIN_ID, 
                        f"⚠️ ALERTE: Prédiction #{active_prediction['target_game']} bloquée depuis 15 min. Reset dans 5 min."
                    )
                except:
                    pass
        
        # RESET FORCÉ après 20 minutes
        if elapsed > timedelta(minutes=TIMEOUT_MINUTES):
            logger.critical(f"🚨 TIMEOUT 20MIN - RESET FORCÉ!")
            
            try:
                await client.send_message(
                    PREDICTION_CHANNEL_ID,
                    f"🚨 **RESET AUTOMATIQUE**\n\n⏱️ 20min d'inactivité\n🧹 Système nettoyé\n🔄 Prêt"
                )
            except:
                pass
            
            try:
                pred_info = f"#{active_prediction['target_game']}" if active_prediction else "Aucune"
                await client.send_message(
                    ADMIN_ID,
                    f"🚨 **RESET FORCÉ**\n⏱️ 20min\n🔒 {pred_info}\n🧹 Données effacées"
                )
            except:
                pass
            
            old_pred = active_prediction['target_game'] if active_prediction else None
            
            active_prediction = None
            waiting_for_finalization = False
            verification_counter = 0
            processed_messages.clear()
            
            logger.info(f"✅ Reset forcé terminé. #{old_pred} effacée.")
            
            last_prediction_time = None
            break

async def reset_bot_state():
    global active_prediction, recent_games, processed_messages
    global current_game_number, waiting_for_finalization, cycle_count
    global verification_counter, last_prediction_time, timeout_task
    
    logger.info(f"🔄 Reset manuel cycle {cycle_count}")
    
    if timeout_task:
        timeout_task.cancel()
        try:
            await timeout_task
        except asyncio.CancelledError:
            pass
        timeout_task = None
    
    active_prediction = None
    recent_games = {}
    processed_messages = set()
    current_game_number = 0
    waiting_for_finalization = False
    verification_counter = 0
    last_prediction_time = None
    cycle_count += 1
    
    logger.info("✅ Reset manuel terminé")

# ============================================================
# FONCTION DE LIBÉRATION FORCÉE (séparée pour éviter global dans condition)
# ============================================================

async def force_unlock_system():
    """Libère le système immédiatement"""
    global active_prediction, waiting_for_finalization, verification_counter, last_prediction_time, timeout_task
    
    old_pred = active_prediction['target_game'] if active_prediction else None
    
    if timeout_task:
        timeout_task.cancel()
        try:
            await timeout_task
        except asyncio.CancelledError:
            pass
        timeout_task = None
    
    active_prediction = None
    waiting_for_finalization = False
    verification_counter = 0
    last_prediction_time = None
    
    return old_pred

# ============================================================
# ENVOI ET MISE À JOUR DES PRÉDICTIONS
# ============================================================

async def send_prediction(game_number, suit):
    global prediction_channel_ok, active_prediction, waiting_for_finalization
    global last_prediction_time
    
    try:
        if active_prediction is not None:
            logger.error(f"⛔ Double prédiction bloquée! Active: #{active_prediction['target_game']}")
            return None
        
        target_game = game_number + PREDICTION_OFFSET
        suit_name = SUIT_NAMES.get(suit, suit)
        
        msg = f"📡 PRÉDICTION #{target_game}\n🎯 Couleur: {suit} {suit_name}\n🌪️ Statut: ⏳ EN COURS"
        p_msg = await client.send_message(PREDICTION_CHANNEL_ID, msg)
        
        active_prediction = {
            'target_game': target_game, 
            'suit': suit, 
            'message_id': p_msg.id, 
            'status': '⏳',
            'trigger_game': game_number,
            'timestamp': datetime.now()
        }
        waiting_for_finalization = True
        
        await start_timeout_monitor()
        
        logger.info(f"🚀 Prédiction #{target_game} lancée (déclencheur #{game_number})")
        return p_msg.id
        
    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
        active_prediction = None
        waiting_for_finalization = False
        return None

async def update_status(target_game, success, count=0):
    global active_prediction, waiting_for_finalization, verification_counter
    global last_prediction_time, timeout_task
    
    if not active_prediction or active_prediction['target_game'] != target_game:
        return
    
    if success:
        emoji = f"🍯✅{count if count <= 3 else ''}"
        status_text = f"GAGNÉ ({count} check{'s' if count > 1 else ''})"
    else:
        emoji = "😶❌"
        status_text = "PERDU (3 checks)"
    
    suit = active_prediction['suit']
    msg = f"📡 PRÉDICTION #{target_game}\n🎯 Couleur: {suit} {SUIT_NAMES.get(suit, suit)}\n🌪️ Statut: {emoji}"
    
    try:
        await client.edit_message(PREDICTION_CHANNEL_ID, active_prediction['message_id'], msg)
    except Exception as e:
        logger.error(f"Erreur mise à jour statut: {e}")
    
    if success or count >= 3:
        logger.info(f"🔓 #{target_game} {status_text} - Système LIBÉRÉ")
        
        if timeout_task:
            timeout_task.cancel()
            try:
                await timeout_task
            except asyncio.CancelledError:
                pass
            timeout_task = None
        
        active_prediction = None
        waiting_for_finalization = False
        verification_counter = 0
        last_prediction_time = None
        
        try:
            await client.send_message(
                ADMIN_ID,
                f"✅ **#{target_game} terminée**\n{status_text}\n🔄 Système libéré"
            )
        except:
            pass

# ============================================================
# GESTION DES MESSAGES
# ============================================================

@client.on(events.NewMessage())
async def handle_new(event):
    global verification_counter, current_game_number
    
    chat_id = event.chat_id
    message_id = event.message.id
    
    if message_id in processed_messages:
        return
    processed_messages.add(message_id)
    
    # CANAL SOURCE
    if chat_id == SOURCE_CHANNEL_ID:
        text = event.message.message
        num = extract_game_number(text)
        
        if not num:
            return
        
        current_game_number = num
        
        if num == 1440:
            await reset_bot_state()
            return
        
        if not active_prediction and not waiting_for_finalization:
            first_group = extract_first_parenthesis_group(text)
            suit = get_first_card_suit(first_group)
            
            if suit:
                logger.info(f"🎯 #{num} → Prédiction #{num + PREDICTION_OFFSET}")
                await send_prediction(num, suit)
        else:
            if active_prediction:
                logger.debug(f"⏭️ #{num} ignoré: #{active_prediction['target_game']} en cours")
    
    # CANAL STATS
    elif chat_id == STATS_CHANNEL_ID and active_prediction:
        text = event.message.message
        num = extract_game_number(text)
        
        if not num:
            return
        
        target = active_prediction['target_game']
        predicted_suit = active_prediction['suit']
        
        if target <= num <= target + 3:
            if has_suit_in_first_parenthesis(text, predicted_suit):
                count = num - target
                logger.info(f"🎉 #{num}: {predicted_suit} trouvé! Gagné en {count} check(s)")
                await update_status(target, True, count)
                verification_counter = 0
            else:
                verification_counter += 1
                logger.info(f"❌ Check {verification_counter}/3 pour #{target}: #{num} sans {predicted_suit}")
                
                if verification_counter >= MAX_VERIFICATIONS:
                    logger.warning(f"💔 #{target}: PERDU après 3 vérifications")
                    await update_status(target, False, 3)
                    verification_counter = 0

# ============================================================
# COMMANDES ADMIN
# ============================================================

@client.on(events.NewMessage(pattern='/'))
async def handle_commands(event):
    if event.sender_id != ADMIN_ID:
        return
    
    text = event.message.text.strip()
    cmd = text.split()[0].lower()
    
    if cmd == '/status':
        elapsed = "N/A"
        if last_prediction_time:
            delta = datetime.now() - last_prediction_time
            elapsed = f"{delta.seconds // 60} min {delta.seconds % 60} sec"
        
        status_msg = f"""📊 **STATUT**

🔒 **Prédiction active:** {'OUI' if active_prediction else 'NON'}
"""
        if active_prediction:
            status_msg += f"""
   └ Numéro: #{active_prediction['target_game']}
   └ Costume: {active_prediction['suit']}
   └ Déclencheur: #{active_prediction.get('trigger_game', 'N/A')}
   └ Lancée: {active_prediction['timestamp'].strftime('%H:%M:%S')}
"""
        
        status_msg += f"""
⏳ **Attente:** {'OUI' if waiting_for_finalization else 'NON'}
🔍 **Checks:** {verification_counter}/3
⏱️ **Écoulé:** {elapsed}
🔄 **Timeout:** {TIMEOUT_MINUTES} min
"""
        await event.respond(status_msg)
    
    elif cmd == '/reset':
        await reset_bot_state()
        await event.respond("✅ **Reset manuel**\nDonnées effacées.")
    
    elif cmd == '/forceunlock':
        old_pred = await force_unlock_system()
        await event.respond(f"🔓 **FORCÉ!**\n#{old_pred} annulée.\nSystème libre.")

# ============================================================
# DÉMARRAGE
# ============================================================

async def main():
    async def health(request):
        status = "🔴 BLOQUÉ" if active_prediction else "🟢 LIBRE"
        if active_prediction and last_prediction_time:
            elapsed = (datetime.now() - last_prediction_time).seconds // 60
            status += f" ({elapsed}min)"
        return web.Response(text=f"Bot {status}", status=200)
    
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    
    await web.TCPSite(runner, '0.0.0.0', 5000).start()
    if PORT != 5000:
        await web.TCPSite(runner, '0.0.0.0', PORT).start()
    
    await client.start(bot_token=BOT_TOKEN)
    logger.info("🤖 Bot démarré avec timeout 20min")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
