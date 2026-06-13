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
WEEKLY_FILE = 'weekly_songs.json'

VIEWS_PER_DAY_THRESHOLD = 50000
WEEKLY_MIN_VIEWS_PER_DAY = 5000
MAX_AGE_DAYS = 10

SEARCH_QUERIES = [
    'новая музыка россия клип 2026',
    'новинки российской музыки 2026',
    'российский поп новинка клип 2026',
    'новый российский трек 2026',
    'российская попса новинка клип',
    'премьера песни 2026 россия',
    'премьера песни новинка российская',
    'премьера песни русский поп 2026',
    'премьера песни клип россия новый',
    'премьера песни российский исполнитель',
    'премьера клипа 2026 россия',
    'премьера клипа российская музыка',
    'премьера клипа русский поп 2026',
    'премьера клипа новинка россия',
    'премьера клипа российский исполнитель',
]

REGIONS = ['RU']

def has_cyrillic(text):
    return bool(re.search('[а-яА-ЯёЁ]', text))

def is_likely_ukrainian(text):
    ukrainian_chars = re.compile('[їЇєЄґҐ\u0456\u0406]')
    return bool(ukrainian_chars.search(text))

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

    print(f"Search: знайдено {len(all_ids)} унікальних відео по {len(SEARCH_QUERIES)} запитах")
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

def process_videos(videos, notified, weekly_songs):
    rising = []
    weekly_candidates = []
    seen_ids = set()
    skipped_not_cyrillic = 0
    skipped_ukrainian = 0
    skipped_old_or_low = 0

    for v in videos:
        vid_id = v['id'] if isinstance(v['id'], str) else v['id']
        if vid_id in seen_ids:
            continue
        seen_ids.add(vid_id)

        snippet = v['snippet']
        stats = v.get('statistics', {})
        title = snippet.get('title', '')
        channel = snippet.get('channelTitle', '')
        published_at = snippet.get('publishedAt', '')
        view_count = int(stats.get('viewCount', 0))

        if not has_cyrillic(title + channel):
            skipped_not_cyrillic += 1
            continue

        if is_likely_ukrainian(title + channel):
            skipped_ukrainian += 1
            continue

        vpd, days_old = views_per_day(view_count, published_at)

        if days_old > MAX_AGE_DAYS:
            skipped_old_or_low += 1
            continue

        song_data = {
            'id': vid_id,
            'title': title,
            'channel': channel,
            'views': view_count,
            'vpd': int(vpd),
            'days': round(days_old, 1),
            'url': f"https://www.youtube.com/watch?v={vid_id}"
        }

        if vpd >= VIEWS_PER_DAY_THRESHOLD:
            # Щоденний хіт
            if vid_id not in notified:
                rising.append(song_data)
        elif vpd >= WEEKLY_MIN_VIEWS_PER_DAY:
            # Кандидат для суботнього дайджесту
            if vid_id not in weekly_songs:
                weekly_candidates.append(song_data)
        else:
            skipped_old_or_low += 1

    print(f"  Пропущено (не кирилиця): {skipped_not_cyrillic}")
    print(f"  Пропущено (українські): {skipped_ukrainian}")
    print(f"  Пропущено (старі або мало переглядів): {skipped_old_or_low}")
    print(f"  Щоденні хіти: {len(rising)}")
    print(f"  Нові кандидати для суботи: {len(weekly_candidates)}")
    return rising, weekly_candidates

def send_saturday_digest(weekly_songs):
    if not weekly_songs:
        print("Суботній дайджест: пісень немає.")
        return

    songs = list(weekly_songs.values())
    songs.sort(key=lambda x: x['vpd'], reverse=True)

    chunks = [songs[i:i+5] for i in range(0, len(songs), 5)]

    for idx, chunk in enumerate(chunks):
        if idx == 0:
            msg = f"📅 <b>Суботній дайджест — нові російські пісні за тиждень ({len(songs)} пісень)</b>\n"
            msg += f"<i>Від 5,000 до 50,000 переглядів/день — ростуть але ще не хіти</i>\n\n"
        else:
            msg = f"📅 <b>Суботній дайджест — продовження ({idx+1}/{len(chunks)})</b>\n\n"

        for h in chunk:
            msg += f"🎵 <b>{h['title']}</b>\n"
            msg += f"👤 {h['channel']}\n"
            msg += f"👁 {h['views']:,} переглядів за {h['days']} дн. "
            msg += f"(<b>{h['vpd']:,}/день</b>)\n"
            msg += f"🔗 {h['url']}\n\n"

        send_telegram(msg)

    print(f"Суботній дайджест надіслано: {len(songs)} пісень у {len(chunks)} повідомленнях.")

def main():
    notified = load_json(NOTIFIED_FILE, {})
    last_notif = load_json(LAST_NOTIFICATION_FILE, {})
    weekly_songs = load_json(WEEKLY_FILE, {})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    is_saturday = datetime.now(timezone.utc).weekday() == 5

    trending = get_trending_music()
    searched = search_russian_music()

    all_videos = {v['id']: v for v in trending}
    for v in searched:
        vid_id = v['id']
        if vid_id not in all_videos:
            all_videos[vid_id] = v

    print(f"Всього унікальних відео для аналізу: {len(all_videos)}")

    rising, weekly_candidates = process_videos(list(all_videos.values()), notified, weekly_songs)

    # Зберігаємо нових кандидатів у тижневий список
    for s in weekly_candidates:
        weekly_songs[s['id']] = s

    # Щоденне сповіщення про хіти
    if rising and last_notif.get('date') != today:
        rising.sort(key=lambda x: x['vpd'], reverse=True)
        chunks = [rising[i:i+5] for i in range(0, len(rising), 5)]

        for idx, chunk in enumerate(chunks):
            if idx == 0:
                msg = f"🔥 <b>Майбутні хіти — злітають прямо зараз! ({len(rising)} пісень)</b>\n\n"
            else:
                msg = f"🔥 <b>Продовження ({idx+1}/{len(chunks)})</b>\n\n"

            for h in chunk:
                msg += f"🎵 <b>{h['title']}</b>\n"
                msg += f"👤 {h['channel']}\n"
                msg += f"👁 {h['views']:,} переглядів за {h['days']} дн. "
                msg += f"(<b>{h['vpd']:,}/день</b>)\n"
                msg += f"🔗 {h['url']}\n\n"

            send_telegram(msg)

        for h in rising:
            notified[h['id']] = today
        last_notif['date'] = today
        print(f"Надіслано {len(rising)} щоденних хітів.")
    else:
        print("Щоденних хітів немає або вже надсилали сьогодні.")

    # Суботній дайджест
    if is_saturday and last_notif.get('saturday') != today:
        send_saturday_digest(weekly_songs)
        last_notif['saturday'] = today
        weekly_songs = {}  # очищаємо після відправки
        print("Тижневий список очищено.")

    save_json(NOTIFIED_FILE, notified)
    save_json(LAST_NOTIFICATION_FILE, last_notif)
    save_json(WEEKLY_FILE, weekly_songs)

if __name__ == '__main__':
    main()
