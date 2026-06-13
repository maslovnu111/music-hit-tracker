import requests
import json
import os
from datetime import datetime, timezone

YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

NOTIFIED_FILE = 'notified_songs.json'
LAST_NOTIFICATION_FILE = 'last_notification.json'

VIEWS_PER_DAY_THRESHOLD = 300000  # 300k переглядів/день
MAX_AGE_DAYS = 10                  # відео не старіше 10 днів
TOP_RESULTS = 3                    # максимум пісень в одному повідомленні

def load_json(filepath, default):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_trending_music():
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        'part': 'snippet,statistics',
        'chart': 'mostPopular',
        'videoCategoryId': '10',  # Music
        'regionCode': 'RU',
        'maxResults': 50,
        'key': YOUTUBE_API_KEY
    }
    r = requests.get(url, params=params)
    return r.json().get('items', [])

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

def main():
    notified = load_json(NOTIFIED_FILE, {})
    last_notif = load_json(LAST_NOTIFICATION_FILE, {})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    if last_notif.get('date') == today:
        print("Вже надсилали сьогодні — пропускаємо.")
        return

    videos = get_trending_music()
    rising = []

    for v in videos:
        vid_id = v['id']
        if vid_id in notified:
            continue

        snippet = v['snippet']
        stats = v.get('statistics', {})
        published_at = snippet.get('publishedAt', '')
        view_count = int(stats.get('viewCount', 0))

        vpd, days_old = views_per_day(view_count, published_at)

        if days_old <= MAX_AGE_DAYS and vpd >= VIEWS_PER_DAY_THRESHOLD:
            rising.append({
                'id': vid_id,
                'title': snippet.get('title', ''),
                'channel': snippet.get('channelTitle', ''),
                'views': view_count,
                'vpd': int(vpd),
                'days': round(days_old, 1),
                'url': f"https://www.youtube.com/watch?v={vid_id}"
            })

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
