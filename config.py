"""
Configuration du bot Telegram de prédiction Baccarat
"""
import os

def parse_channel_id(env_var: str, default: str = None) -> int:
    value = os.getenv(env_var, default)
    if value is None:
        raise ValueError(f"Variable d'environnement {env_var} manquante !")
    channel_id = int(value)
    if channel_id > 0 and len(str(channel_id)) >= 10:
        channel_id = -channel_id
    return channel_id

def get_env_or_raise(key: str) -> str:
    """Oblige la présence de la variable d'environnement"""
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Variable d'environnement {key} manquante !")
    return value

# Credentials Telegram API (OBLIGATOIRES - pas de valeur par défaut)
API_ID = int(get_env_or_raise('API_ID'))
API_HASH = get_env_or_raise('API_HASH')
BOT_TOKEN = get_env_or_raise('BOT_TOKEN')

# Identifiant de l'administrateur (OBLIGATOIRE)
ADMIN_ID = int(get_env_or_raise('ADMIN_ID'))

# Identifiants des canaux (OBLIGATOIRES)
SOURCE_CHANNEL_ID = parse_channel_id('SOURCE_CHANNEL_ID')
PREDICTION_CHANNEL_ID = parse_channel_id('PREDICTION_CHANNEL_ID')
STATS_CHANNEL_ID = parse_channel_id('STATS_CHANNEL_ID', '-1002682552255')  # Optionnel avec défaut

# Port pour le serveur web (Render.com utilise 10000)
PORT = int(os.getenv('PORT', '10000'))

# Paramètre 'a' pour la prédiction (nombre entier naturel, défaut = 2)
PREDICTION_OFFSET = int(os.getenv('PREDICTION_OFFSET', '2'))

# Session string Telethon (optionnel, auto-générée si vide)
TELEGRAM_SESSION = os.getenv('TELEGRAM_SESSION', '')

# Mapping des couleurs (constantes, pas besoin d'env var)
SUIT_MAPPING = {
    '♠️': '❤️',
    '♠': '❤️',
    '❤️': '♠️',
    '❤': '♠️',
    '♥️': '♠️',
    '♥': '♠️',
    '♣️': '♦️',
    '♣': '♦️',
    '♦️': '♣️',
    '♦': '♣️'
}

ALL_SUITS = ['♠', '♥', '♦', '♣']

SUIT_DISPLAY = {
    '♠': '♠️',
    '♥': '❤️',
    '♦': '♦️',
    '♣': '♣️'
}

SUIT

