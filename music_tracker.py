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

VIEWS_PER_DAY_THRESHOLD = 50000  # знизив до 50k
MAX_AGE_DAYS = 10
TOP_RESULTS = 5  # тепер до 5 пісень

SEARCH_QUERIES = [
    'новая русская музыка клип 2026',
    'новинки русской музыки 2026',
    'русский поп новинка клип',
    'новый трек русский 2026',
    'русская попса новинка',
]

REGIONS = ['RU', 'BY', 'KZ', 'UA']

def has_cyrillic(text):
    return bool(re.search('[а-яА-ЯёЁ]', text))

def load_json(filepath, default):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_video_details(video_ids):
    if not video_ids:
        return []
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        'part': 'snippet,statistics',
        'id': ','.join(video_ids[:50]),
        'key': YOUTUBE_API_KEY
    }
    r = requests.get(url, params=params)
    return r.json().get('items', [])

def get_trending_music():
    all_items = {}
    for region in REGIONS:
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'snippet,statistics',
            'chart': 'mostPopular',
            'videoCategoryId': '10',
            'regionCode': region,
            'maxResults': 50,
            'key': YOUTUBE_API_KEY
        }
        r = requests.get(url, params=params)
        for item in r.json().get('items', []):
            all_items[item['id']] = item
    print(f"Trending: знайдено {len(all_items)} відео")
    return list(all_items.values())

def search_russian_music():
    published_after = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    all_ids = set()

    for query in SEARCH_QUERIES:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            'part': 'snippet',
            'q': query,
            'type': 'video',
            'videoCategoryId': '10',
            'relevanceLanguage': 'ru',
            'order': 'viewCount',
            'publishedAfter': published_after,
            'maxResults': 25,
            'key': YOUTUBE_API_KEY
        }
        r = requests.get(url, params=params)
        for item in r.json().get('items', []):
            all_ids.add(item['id']['videoId'])

    print(f"Search: знайдено {len(all_ids)} унікальних відео")
    return get_video_details(list(all_ids))

def views_per_day(view_count, published_at):
    published = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    days = max((now - published).total_seconds() / 86400, 0.5)
    return int(view_count) / days, days

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    })

def process_videos(videos, notified):
    rising = []
    seen_ids = set()

    for v in videos:
        vid_id = v['id'] if isinstance(v['id'], str) else v['id']
        if vid_id in notified or vid_id in seen_ids:
            continue
        seen_ids.add(vid_id)

        snippet = v['snippet']
        stats = v.get('statistics', {})
        title = snippet.get('title', '')
        channel = snippet.get('channelTitle', '')
        published_at = snippet.get('publishedAt', '')
        view_count = int(stats.get('viewCount', 0))

        if not has_cyrillic(title) and not has_cyrillic(channel):
            continue

        vpd, days_old = views_per_day(view_count, published_at)

        if days_old <= MAX_AGE_DAYS and vpd >= VIEWS_PER_DAY_THRESHOLD:
            rising.append({
                'id': vid_id,
                'title': title,
                'channel': channel,
                'views': view_count,
                'vpd': int(vpd),
                'days': round(days_old, 1),
                'url': f"https://www.youtube.com/watch?v={vid_id}"
            })

    return rising

def main():
    notified = load_json(NOTIFIED_FILE, {})
    last_notif = load_json(LAST_NOTIFICATION_FILE, {})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    if last_notif.get('date') == today:
        print("Вже надсилали сьогодні — пропускаємо.")
        return

    trending = get_trending_music()
    searched = search_russian_music()

    all_videos = {v['id']: v for v in trending}
    for v in searched:
        vid_id = v['id']
        if vid_id not in all_videos:
            all_videos[vid_id] = v

    print(f"Всього унікальних відео для аналізу: {len(all_videos)}")

    rising = process_videos(list(all_videos.values()), notified)
    print(f"Відповідають критеріям: {len(rising)}")

    if not rising:
        print("Нових хітів не знайдено.")
        return

    rising.sort(key=lambda x: x['vpd'], reverse=True)
    top = rising[:TOP_RESULTS]

    msg = "🔥 <b>Майбутні хіти — злітають прямо зараз!</b>\n\n"
    for h in top:
        msg += f"🎵 <b>{h['title']}</b>\n"
        msg += f"👤 {h['channel']}\n"
        msg += f"👁 {h['views']:,} переглядів за {h['days']} дн. "
        msg += f"(<b>{h['vpd']:,}/день</b>)\n"
        msg += f"🔗 {h['url']}\n\n"

    send_telegram(msg)

    for h in top:
        notified[h['id']] = today
    last_notif['date'] = today

    save_json(NOTIFIED_FILE, notified)
    save_json(LAST_NOTIFICATION_FILE, last_notif)
    print(f"Надіслано {len(top)} хітів.")

if __name__ == '__main__':
    main()
