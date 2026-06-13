import requests
import json
import os
import re
from datetime import datetime, timezone, timedelta

YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

NOTIFIED_FILE = 'notified_songs.json'
LAST_NOTIFICATION_FILE = 'last_notification.json'

VIEWS_PER_DAY_THRESHOLD = 50000
MAX_AGE_DAYS = 10

SEARCH_QUERIES = [
    # Основні запити
    'новая музыка россия клип 2026',
    'новинки российской музыки 2026',
    'российский поп новинка клип 2026',
    'новый российский трек 2026',
    'российская попса новинка клип',
    # Прем'єра пісні — 5 варіантів
    'премьера песни 2026 россия',
    'премьера песни новинка российская',
    'премьера песни русский поп 2026',
    'премьера песни клип россия новый',
    'премьера песни российский исполнитель',
    # Прем'єра кліпу — 5 варіантів
    'премьера клипа 2026 россия',
    'премьера клипа российская музыка',
