import requests
import json
import os
import re
import html
import time
from datetime import datetime, timezone, timedelta


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"❌ Відсутня обов'язкова змінна оточення: {name}")
    return value


YOUTUBE_API_KEY = require_env('YOUTUBE_API_KEY')
TELEGRAM_BOT_TOKEN = require_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = require_env('TELEGRAM_CHAT_ID')

NOTIFIED_FILE = 'notified_songs.json'
LAST_NOTIFICATION_FILE = 'last_notification.json'
WEEKLY_FILE = 'weekly_songs.json'

VIEWS_PER_DAY_THRESHOLD = 30000
WEEKLY_MIN_VIEWS_PER_DAY = 5000
MAX_AGE_DAYS = 10
# Якщо жодна пісня не дотягує до порога хіта — усе одно надсилаємо топ-N
# найшвидше зростаючих, щоб щоденне сповіщення не було порожнім у спокійні дні.
DAILY_FALLBACK_COUNT = 3
# Скільки днів зберігати ID у списку сповіщених. Відео старші за MAX_AGE_DAYS
# все одно відсіюються, тому такий запас гарантує відсутність повторів,
# але не даємо файлу рости безмежно.
NOTIFIED_RETENTION_DAYS = 30

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
TELEGRAM_MAX_LEN = 4000  # ліміт Telegram 4096, лишаємо запас

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
    ukrainian_chars = re.compile('[їЇєЄґҐіІ]')
    return bool(ukrainian_chars.search(text))


def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ Не вдалося прочитати {filepath} ({e}) — використовую значення за замовчуванням.")
    return default


def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_get_json(url, params):
    """GET із таймаутом, ретраями та логуванням помилок. Повертає dict або {}."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            print(f"  ⚠️ HTTP {r.status_code} від {url}: {r.text[:200]}")
            # Помилки клієнта (ключ/квота/поганий запит) — ретрай не допоможе.
            if 400 <= r.status_code < 500:
                break
        except (requests.RequestException, ValueError) as e:
            print(f"  ⚠️ Помилка запиту ({attempt + 1}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    return {}


def get_video_details(video_ids):
    """Деталі відео пакетами по 50 (обмеження YouTube API)."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    items = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        if not batch:
            continue
        params = {
            'part': 'snippet,statistics',
            'id': ','.join(batch),
            'key': YOUTUBE_API_KEY
        }
        data = http_get_json(url, params)
        items.extend(data.get('items', []))
    return items


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
        data = http_get_json(url, params)
        for item in data.get('items', []):
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
        data = http_get_json(url, params)
        for item in data.get('items', []):
            vid = item.get('id', {}).get('videoId')
            if vid:
                all_ids.add(vid)

    print(f"Search: знайдено {len(all_ids)} унікальних відео по {len(SEARCH_QUERIES)} запитах")
    return get_video_details(list(all_ids))


def parse_published(published_at):
    if not published_at:
        return None
    try:
        return datetime.fromisoformat(published_at.replace('Z', '+00:00'))
    except ValueError:
        return None


def views_per_day(view_count, published):
    now = datetime.now(timezone.utc)
    days = max((now - published).total_seconds() / 86400, 0.5)
    return view_count / days, days


def send_telegram(message):
    """Надсилає повідомлення. Повертає True при успіху, False — інакше."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(url, json={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            }, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200 and r.json().get('ok'):
                return True
            print(f"  ⚠️ Telegram HTTP {r.status_code}: {r.text[:300]}")
            # Некоректний запит (напр. розмітка) ретраєм не виправити.
            if r.status_code == 400:
                break
            # Too Many Requests — почекати рекомендований час.
            if r.status_code == 429:
                retry_after = 1
                try:
                    retry_after = r.json().get('parameters', {}).get('retry_after', 1)
                except ValueError:
                    pass
                time.sleep(min(retry_after, 30))
                continue
        except (requests.RequestException, ValueError) as e:
            print(f"  ⚠️ Telegram помилка ({attempt + 1}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    return False


def format_song(h):
    title = html.escape(str(h['title']))
    channel = html.escape(str(h['channel']))
    return (
        f"🎵 <b>{title}</b>\n"
        f"👤 {channel}\n"
        f"👁 {h['views']:,} переглядів за {h['days']} дн. "
        f"(<b>{h['vpd']:,}/день</b>)\n"
        f"🔗 {h['url']}\n\n"
    )


def send_messages(first_header, songs, cont_emoji='🔥'):
    """Формує та шле повідомлення частинами. Повертає множину ID успішно надісланих пісень."""
    sent_ids = set()
    chunks = [songs[i:i + 5] for i in range(0, len(songs), 5)]
    total = len(chunks)
    for idx, chunk in enumerate(chunks):
        if idx == 0:
            msg = f"{first_header}\n\n"
        else:
            msg = f"{cont_emoji} <b>Продовження ({idx + 1}/{total})</b>\n\n"
        for h in chunk:
            msg += format_song(h)
        if len(msg) > TELEGRAM_MAX_LEN:
            msg = msg[:TELEGRAM_MAX_LEN]
        if send_telegram(msg):
            sent_ids.update(h['id'] for h in chunk)
        else:
            print(f"  ⚠️ Не вдалося надіслати частину {idx + 1}/{total}.")
    return sent_ids


def process_videos(videos, notified):
    rising = []
    weekly_candidates = []
    seen_ids = set()
    skipped_not_cyrillic = 0
    skipped_ukrainian = 0
    skipped_old_or_low = 0

    for v in videos:
        raw_id = v.get('id')
        vid_id = raw_id if isinstance(raw_id, str) else (raw_id or {}).get('videoId')
        if not vid_id or vid_id in seen_ids:
            continue
        seen_ids.add(vid_id)

        snippet = v.get('snippet', {})
        stats = v.get('statistics', {})
        title = snippet.get('title', '')
        channel = snippet.get('channelTitle', '')
        published_at = snippet.get('publishedAt', '')

        try:
            view_count = int(stats.get('viewCount', 0))
        except (TypeError, ValueError):
            view_count = 0

        if not has_cyrillic(title + channel):
            skipped_not_cyrillic += 1
            continue

        if is_likely_ukrainian(title + channel):
            skipped_ukrainian += 1
            continue

        published = parse_published(published_at)
        if published is None:
            skipped_old_or_low += 1
            continue

        vpd, days_old = views_per_day(view_count, published)

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
            # Кандидат для суботнього дайджесту (не той, що вже пішов як хіт)
            if vid_id not in notified:
                weekly_candidates.append(song_data)
        else:
            skipped_old_or_low += 1

    print(f"  Пропущено (не кирилиця): {skipped_not_cyrillic}")
    print(f"  Пропущено (українські): {skipped_ukrainian}")
    print(f"  Пропущено (старі або мало переглядів): {skipped_old_or_low}")
    print(f"  Щоденні хіти: {len(rising)}")
    print(f"  Кандидатів для суботи (нові/оновлені): {len(weekly_candidates)}")
    return rising, weekly_candidates


def prune_notified(notified):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=NOTIFIED_RETENTION_DAYS)).strftime('%Y-%m-%d')
    return {k: v for k, v in notified.items() if isinstance(v, str) and v >= cutoff}


def send_saturday_digest(weekly_songs):
    """Повертає множину ID пісень, надісланих успішно."""
    if not weekly_songs:
        print("Суботній дайджест: пісень немає.")
        return set()

    songs = list(weekly_songs.values())
    songs.sort(key=lambda x: x['vpd'], reverse=True)

    header = (
        f"📅 <b>Суботній дайджест — нові російські пісні за тиждень ({len(songs)} пісень)</b>\n"
        f"<i>Від 5,000 до 30,000 переглядів/день — ростуть але ще не хіти</i>"
    )
    sent_ids = send_messages(header, songs, cont_emoji='📅')

    print(f"Суботній дайджест: надіслано {len(sent_ids)}/{len(songs)} пісень.")
    return sent_ids


def main():
    notified = load_json(NOTIFIED_FILE, {})
    last_notif = load_json(LAST_NOTIFICATION_FILE, {})
    weekly_songs = load_json(WEEKLY_FILE, {})
    if not isinstance(notified, dict):
        notified = {}
    if not isinstance(last_notif, dict):
        last_notif = {}
    if not isinstance(weekly_songs, dict):
        weekly_songs = {}

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    is_saturday = datetime.now(timezone.utc).weekday() == 5

    trending = get_trending_music()
    searched = search_russian_music()

    all_videos = {v['id']: v for v in trending}
    for v in searched:
        vid_id = v.get('id')
        if isinstance(vid_id, str) and vid_id not in all_videos:
            all_videos[vid_id] = v

    print(f"Всього унікальних відео для аналізу: {len(all_videos)}")

    rising, weekly_candidates = process_videos(list(all_videos.values()), notified)

    # Оновлюємо тижневий список (свіжа статистика перезаписує стару)
    for s in weekly_candidates:
        weekly_songs[s['id']] = s

    # Щоденне сповіщення — раз на день. Беремо сильні хіти (>= порога), а
    # якщо їх немає — топ найшвидше зростаючих пісень, щоб день не був
    # порожнім навіть у спокійні періоди. Дедуплікація — через notified.
    if last_notif.get('date') != today:
        daily = list(rising)
        if not daily:
            daily = sorted(weekly_candidates, key=lambda x: x['vpd'], reverse=True)[:DAILY_FALLBACK_COUNT]

        if daily:
            daily.sort(key=lambda x: x['vpd'], reverse=True)
            header = f"🔥 <b>Пісні, що набирають популярність зараз! ({len(daily)} шт.)</b>"
            sent_ids = send_messages(header, daily)
            for h in daily:
                if h['id'] in sent_ids:
                    notified[h['id']] = today
                    weekly_songs.pop(h['id'], None)  # надіслане більше не кандидат на суботу
            # Позначаємо день закритим лише коли все надіслано; при частковому
            # збої залишок піде наступного запуску (без дублів — через notified).
            if len(sent_ids) == len(daily):
                last_notif['date'] = today
            print(f"Надіслано {len(sent_ids)}/{len(daily)} пісень для щоденного сповіщення.")
        else:
            print("Немає пісень для щоденного сповіщення.")
    else:
        print("Щоденне сповіщення вже надсилали сьогодні.")

    # Суботній дайджест. Прибираємо лише успішно надіслані пісні, тому при
    # частковому збої повтор надішле тільки залишок, без дублів.
    if is_saturday and last_notif.get('saturday') != today:
        sent_ids = send_saturday_digest(weekly_songs)
        for sid in sent_ids:
            weekly_songs.pop(sid, None)
        if not weekly_songs:
            last_notif['saturday'] = today
            print("Суботній дайджест завершено, тижневий список очищено.")
        else:
            print(f"⚠️ Суботній дайджест надіслано не повністю, лишилось {len(weekly_songs)} — повторимо наступного запуску.")

    # Обрізаємо старі записи, щоб файл не ріс безмежно
    notified = prune_notified(notified)

    save_json(NOTIFIED_FILE, notified)
    save_json(LAST_NOTIFICATION_FILE, last_notif)
    save_json(WEEKLY_FILE, weekly_songs)


if __name__ == '__main__':
    main()
