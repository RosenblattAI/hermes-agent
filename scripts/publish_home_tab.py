#!/usr/bin/env python3
"""Hermes Dashboard — configurable Slack App Home tab with Jira boards, weather, news, and more."""
import json, subprocess, os, sys, time, urllib.request, base64, random, sys
from datetime import datetime, timezone, timedelta

# --- Load Config ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("DASHBOARD_CONFIG", os.path.join(SCRIPT_DIR, "dashboard_config.json"))

if not os.path.exists(CONFIG_PATH):
    print(f"ERROR: Config not found at {CONFIG_PATH}")
    print("Copy dashboard_config.template.json → dashboard_config.json and fill in your details.")
    sys.exit(1)

with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

# --- User Config ---
USER = CONFIG["user"]
DISPLAY_NAME = USER["display_name"]
SLACK_USER_ID = USER["slack_user_id"]
DM_CHANNEL = USER["slack_dm_channel"]
JIRA_EMAIL = USER["jira_email"]
JIRA_ACCOUNT_ID = USER["jira_account_id"]
TZ_OFFSET = USER.get("timezone_offset_hours", -4)
WEATHER_LAT = USER.get("weather_lat", 40.7128)
WEATHER_LON = USER.get("weather_lon", -74.0060)
WEATHER_LOCATION = USER.get("weather_location", "New York")

# --- Board Config ---
BOARDS = CONFIG["boards"]
BOARD_SITES = {b["key"]: b["site"] for b in BOARDS}
BOARD_LABEL = {b["key"]: b["emoji"] for b in BOARDS}
BOARD_NAME = {b["key"]: b["label"] for b in BOARDS}
BOARD_FILTER = {b["key"]: b.get("filter_by_assignee", False) for b in BOARDS}

# --- Channel Config ---
CHANNELS = CONFIG.get("channels", {})
NEWS_CHANNEL = CHANNELS.get("news_channel", "")
NEWS_BOT_ID = CHANNELS.get("news_bot_id", "")
COUNCIL_CHANNEL = CHANNELS.get("council_channel", "")

# --- Feature Flags ---
FEATURES = CONFIG.get("features", {})

# --- Display Config ---
DISPLAY = CONFIG.get("display", {})
SUMMARY_MAX = DISPLAY.get("summary_max_chars", 55)
BACKLOG_MAX = DISPLAY.get("backlog_max_items", 4)
DONE_MAX = DISPLAY.get("done_max_items", 5)
REVIEW_MAX = DISPLAY.get("review_max_items", 5)

# --- Secrets (from env) ---
JIRA_EMAIL = CONFIG['user']['jira_email']
SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]
JIRA_TOKEN = os.environ["ATLASSIAN_API_TOKEN"]
JIRA_AUTH = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()

# --- Data Files ---
DATA_DIR = os.environ.get("DASHBOARD_DATA_DIR", os.path.expanduser("~/.hermes/scripts"))
sys.path.insert(0, DATA_DIR)
LINKS_FILE = os.path.join(DATA_DIR, "ticket_thread_links.json")
DAILY_LINKS_FILE = os.path.join(DATA_DIR, "daily_thread_links.json")
WEATHER_CACHE = os.path.join(DATA_DIR, "weather_cache.json")

# --- Constants ---
GREETINGS = [
    "Bonjour", "Buongiorno", "Привет", "Guten Morgen", "Bom dia",
    "Hola", "Salut", "Ciao", "Buenos días", "Olá",
    "Bonsoir", "Buonasera", "Добрый день", "Guten Tag", "Boa tarde",
]

WELCOME_EMOJIS = [
    ":wave:", ":raised_hands:", ":star2:", ":sunny:", ":coffee:",
    ":rocket:", ":zap:", ":fire:", ":sparkles:", ":palm_tree:",
    ":rainbow:", ":crystal_ball:", ":v:", ":muscle:", ":headphones:",
]

PRIORITY_ORDER = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}

WEATHER_MAP = {
    0: (":sunny:", "Clear skies"), 1: (":partly_sunny:", "Mostly clear"),
    2: (":partly_sunny:", "Partly cloudy"), 3: (":cloud:", "Overcast"),
    45: (":fog:", "Foggy"), 48: (":fog:", "Rime fog"),
    51: (":rain_cloud:", "Light drizzle"), 53: (":rain_cloud:", "Drizzle"),
    55: (":rain_cloud:", "Heavy drizzle"), 61: (":rain_cloud:", "Light rain"),
    63: (":rain_cloud:", "Rain"), 65: (":rain_cloud:", "Heavy rain"),
    80: (":rain_cloud:", "Light showers"), 81: (":rain_cloud:", "Showers"),
    82: (":rain_cloud:", "Heavy showers"), 71: (":snowflake:", "Light snow"),
    73: (":snowflake:", "Snow"), 75: (":snowflake:", "Heavy snow"),
    77: (":snowflake:", "Snow grains"), 85: (":snowflake:", "Light snow showers"),
    86: (":snowflake:", "Heavy snow showers"), 95: (":thunder_cloud_and_rain:", "Thunderstorm"),
    96: (":thunder_cloud_and_rain:", "Thunderstorm w/ hail"),
    99: (":thunder_cloud_and_rain:", "Severe thunderstorm"),
}

# --- Helpers ---

def slack_get(method, params=""):
    r = subprocess.run(
        ["curl", "-s", f"https://slack.com/api/{method}?{params}",
         "-H", f"Authorization: Bearer {SLACK_TOKEN}"],
        capture_output=True, text=True)
    return json.loads(r.stdout)

def slack_post_api(method, payload):
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", f"https://slack.com/api/{method}",
         "-H", f"Authorization: Bearer {SLACK_TOKEN}",
         "-H", "Content-Type: application/json; charset=utf-8",
         "-d", json.dumps(payload)],
        capture_output=True, text=True)
    return json.loads(r.stdout)

def jira_post(site, path, payload):
    req = urllib.request.Request(
        f'{site}{path}', data=json.dumps(payload).encode(),
        headers={'Authorization': f'Basic {JIRA_AUTH}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

def load_json(path):
    try:
        with open(path) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f, indent=2)

def truncate_summary(text, max_len=None):
    max_len = max_len or SUMMARY_MAX
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(' ', 1)[0] + '…'

def ensure_thread(key, summary, links, board_key):
    if key in links:
        return links[key]
    site_url = BOARD_SITES[board_key]
    jira_url = f"{site_url}/browse/{key}"
    emoji = BOARD_LABEL[board_key]
    label = BOARD_NAME[board_key]
    resp = slack_post_api("chat.postMessage", {
        "channel": DM_CHANNEL,
        "text": f"{emoji} {label} | <{jira_url}|{key}>: {truncate_summary(summary, 40)}",
        "unfurl_links": False
    })
    if not resp.get("ok"):
        return None
    ts = resp["ts"]
    slack_post_api("chat.postMessage", {
        "channel": DM_CHANNEL, "thread_ts": ts,
        "text": f"<{jira_url}|View in Jira>", "unfurl_links": False
    })
    plink = slack_get("chat.getPermalink", f"channel={DM_CHANNEL}&message_ts={ts}")
    if plink.get("ok"):
        links[key] = plink["permalink"]
        return plink["permalink"]
    return None

def ensure_daily_thread(date_str, daily_links):
    if date_str in daily_links:
        return daily_links[date_str]
    resp = slack_post_api("chat.postMessage", {
        "channel": DM_CHANNEL,
        "text": f":calendar: Daily Thread — {date_str}",
        "unfurl_links": False
    })
    if not resp.get("ok"):
        return None
    ts = resp["ts"]
    plink = slack_get("chat.getPermalink", f"channel={DM_CHANNEL}&message_ts={ts}")
    if plink.get("ok"):
        daily_links[date_str] = plink["permalink"]
        return plink["permalink"]
    return None

# --- Weather ---

def get_weather():
    """Fetch live weather, cache it, fall back to cache on failure."""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={WEATHER_LAT}&longitude={WEATHER_LON}&current=temperature_2m,weather_code&temperature_unit=fahrenheit&timezone=auto"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as resp:
            data = json.loads(resp.read().decode())
        current = data.get("current", {})
        temp = current.get("temperature_2m", "?")
        code = current.get("weather_code", 0)
        cached = {"temp": temp, "code": code, "location": WEATHER_LOCATION}
        with open(WEATHER_CACHE, "w") as f:
            json.dump(cached, f)
    except Exception:
        try:
            with open(WEATHER_CACHE) as f:
                cached = json.load(f)
            temp = cached.get("temp", "?")
            code = cached.get("code", 0)
        except:
            return ":sunny:", "", "Clear skies", WEATHER_LOCATION

    emoji, desc = WEATHER_MAP.get(code, (":partly_sunny:", "Partly cloudy"))
    location = cached.get("location", WEATHER_LOCATION)
    return emoji, f"{temp}°F", desc, location

# --- News (sync to DB with ts-based dedup) ---

def sync_news_to_db():
    """Scan news channel for latest radar post and save to DB if newer than what we have.
    
    Uses post ts (via permalink) for comparison, NOT date string.
    This correctly handles multiple radar posts on the same day.
    """
    from dream_log_db import save_news_radar, get_latest_news_radar
    from datetime import datetime, timezone as tz, timedelta
    et = tz(timedelta(hours=TZ_OFFSET))
    if not NEWS_CHANNEL or not NEWS_BOT_ID:
        return
    existing = get_latest_news_radar()
    hist = slack_get("conversations.history", f"channel={NEWS_CHANNEL}&limit=20")
    if not hist.get("ok"):
        return
    for msg in hist.get("messages", []):
        if msg.get("bot_id") != NEWS_BOT_ID:
            continue
        text = msg.get("text", "")
        if ":satellite_antenna:" not in text:
            continue
        if "Quiet cycle" in text or "nothing major" in text:
            continue
        ts = msg["ts"]
        dt = datetime.fromtimestamp(float(ts), tz=tz.utc).astimezone(et)
        date_str = dt.strftime("%Y-%m-%d")
        # Check if we already have THIS exact post (by ts in permalink)
        ts_id = f"p{ts.replace('.', '')}"
        if existing and existing.get("permalink") and ts_id in existing["permalink"]:
            return  # already have this exact post
        plink = slack_get("chat.getPermalink", f"channel={NEWS_CHANNEL}&message_ts={ts}")
        permalink = plink.get("permalink", "") if plink.get("ok") else ""
        headline = text.strip().split("\n")[0]
        save_news_radar(date_str, headline, permalink)
        return

# --- Dream Log sync ---

def sync_dream_log_to_db():
    """Placeholder for dream log sync — dream logs are saved via dream_log_db.py directly."""
    pass

# --- Dream Log + News DB imports ---
from dream_log_db import get_latest_dream_log, get_latest_news_radar

# --- Fetch issues ---

def fetch_issues(site, jql):
    try:
        result = jira_post(site, '/rest/api/3/search/jql', {
            "jql": jql, "maxResults": 30,
            "fields": ["summary", "status", "issuetype", "updated", "priority"]
        })
        return result.get('issues', [])
    except Exception as e:
        print(f"Error fetching {jql[:50]}...: {e}")
        return []

def priority_sort_key(issue):
    p = issue['fields'].get('priority', {}).get('name', 'Medium')
    return PRIORITY_ORDER.get(p, 2)

def board_jql(board, status_filter, extra=""):
    """Build JQL for a board, optionally filtering by assignee."""
    key = board["key"]
    assignee_clause = f" AND assignee = '{JIRA_ACCOUNT_ID}'" if board.get("filter_by_assignee") else ""
    return f"project = {key}{assignee_clause} AND {status_filter}{extra} ORDER BY priority ASC, updated DESC"

# --- Build ticket display ---

def ticket_line(issue, board_key, links, strikethrough=False):
    key = issue['key']
    summary = truncate_summary(issue['fields']['summary'])
    thread_url = links.get(key)
    site_url = BOARD_SITES[board_key]
    jira_url = f"{site_url}/browse/{key}"
    if strikethrough:
        if thread_url:
            return f"~`{key}`~ <{thread_url}|{summary}>"
        return f"~<{jira_url}|`{key}`>~ {summary}"
    if thread_url:
        return f"`{key}` <{thread_url}|{summary}>"
    return f"<{jira_url}|`{key}`> {summary}"

def render_list(issues, board_key, links, max_per=None):
    max_per = max_per or BACKLOG_MAX
    lines = [ticket_line(i, board_key, links) for i in issues[:max_per]]
    if len(issues) > max_per:
        lines.append(f"_...and {len(issues)-max_per} more_")
    return "\n".join(lines)

def top_priority_block(issue, board_key, links):
    key = issue['key']
    summary = truncate_summary(issue['fields']['summary'])
    thread_url = links.get(key)
    jira_url = f"{BOARD_SITES[board_key]}/browse/{key}"
    title_link = f"<{thread_url}|Open Thread>" if thread_url else ""
    jira_link = f"<{jira_url}|View in Jira>"
    ctx = f"`{key}`  |  {jira_link}"
    if title_link:
        ctx += f"  |  {title_link}"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": summary, "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ctx}]},
    ]

def board_section(issues, board_key, label, emoji, links):
    if not issues:
        return []
    blks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji} *{label}*"}}]
    blks.extend(top_priority_block(issues[0], board_key, links))
    if len(issues) > 1:
        blks.append({"type": "divider"})
        blks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": render_list(issues[1:], board_key, links)}
        ]})
    blks.append({"type": "divider"})
    return blks

# =====================
# --- MAIN EXECUTION ---
# =====================

et = timezone(timedelta(hours=TZ_OFFSET))
now_et = datetime.now(et)
date_str = now_et.strftime("%m/%d/%Y")
now_utc = datetime.now(timezone.utc).strftime("%b %d, %Y · %H:%M UTC")

# Weather
weather_emoji, weather_temp, weather_desc, weather_location = get_weather() if FEATURES.get("weather", True) else (":sunny:", "", "", WEATHER_LOCATION)

# Daily thread
daily_links = load_json(DAILY_LINKS_FILE)
daily_url = None
if FEATURES.get("daily_thread", True):
    daily_url = ensure_daily_thread(date_str, daily_links)
    save_json(DAILY_LINKS_FILE, daily_links)

links = load_json(LINKS_FILE)

# Fetch issues per board
board_issues = {}
board_review = {}
board_done = {}

for board in BOARDS:
    k = board["key"]
    board_issues[k] = fetch_issues(board["site"], board_jql(board, "statusCategory != Done AND status != 'In Review'"))
    board_issues[k].sort(key=priority_sort_key)
    board_review[k] = fetch_issues(board["site"], board_jql(board, "status = 'In Review'"))
    board_done[k] = fetch_issues(board["site"], board_jql(board, "statusCategory = Done"))[:DONE_MAX]

all_review = [i for k in board_review for i in board_review[k]]
all_done = sorted(
    [i for k in board_done for i in board_done[k]],
    key=lambda i: i['fields'].get('updated', ''), reverse=True
)[:DONE_MAX]

# Ensure threads
if FEATURES.get("ticket_threads", True):
    for board in BOARDS:
        k = board["key"]
        for issue in board_issues[k] + board_done[k] + board_review[k]:
            key = issue['key']
            if key not in links:
                ensure_thread(key, issue['fields']['summary'], links, k)
                time.sleep(0.5)
    save_json(LINKS_FILE, links)

# News — sync from Slack to DB, then read from DB
if FEATURES.get("news", True):
    sync_news_to_db()
news = get_latest_news_radar() if FEATURES.get("news", True) else None

# --- Build Blocks ---

date_display = f"<{daily_url}|{date_str}>" if daily_url else date_str
weather_info = f"{weather_desc} in {weather_location} | {weather_temp}" if weather_temp else ""
greeting = random.choice(GREETINGS)
welcome_emoji = random.choice(WELCOME_EMOJIS)

blocks = [
    {"type": "header", "text": {"type": "plain_text", "text": f"{welcome_emoji}  {greeting}, {DISPLAY_NAME}  {welcome_emoji}", "emoji": True}},
    {"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"{weather_emoji} *{date_display}*  {weather_info}"}
    ]},
]

# Quote — fetch live, fall back to cache
quote_text = ""
if FEATURES.get("quote", True):
    try:
        with urllib.request.urlopen(urllib.request.Request("https://zenquotes.io/api/random"), timeout=5) as resp:
            qdata = json.loads(resp.read().decode())
            if qdata and qdata[0].get("q"):
                q = {"q": qdata[0]["q"], "a": qdata[0].get("a", "")}
                quote_text = f"_{q['q']}_ — {q['a']}"
                quote_cache = os.path.join(DATA_DIR, "quote_cache.json")
                with open(quote_cache, "w") as f:
                    json.dump(q, f)
    except:
        try:
            quote_cache = os.path.join(DATA_DIR, "quote_cache.json")
            with open(quote_cache) as f:
                q = json.load(f)
                if q.get("q"):
                    quote_text = f"_{q['q']}_ — {q.get('a', '')}"
        except:
            pass

if quote_text:
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": quote_text}
    ]})

blocks.append({"type": "divider"})

# News — from SQLite DB
if news:
    news_link = f"<{news['permalink']}|{news['headline']}>" if news.get("permalink") else news['headline']
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": news_link}})

# Dream Log — from SQLite DB
if FEATURES.get("dream_log", True):
    dream_log = get_latest_dream_log()
    if dream_log and dream_log.get("permalink"):
        headline = dream_log["headline"]
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"<{dream_log['permalink']}|{headline}>"}})
    else:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "_:crystal_ball: No recent Dream Log found_"}
        ]})

blocks.append({"type": "divider"})

# Board sections
for board in BOARDS:
    k = board["key"]
    blocks.extend(board_section(board_issues[k], k, board["label"], board["emoji"], links))

# In Review
if all_review:
    review_lines = []
    for i in all_review:
        bk = next((b["key"] for b in BOARDS if i['key'].startswith(b["key"])), BOARDS[0]["key"])
        review_lines.append(ticket_line(i, bk, links))
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ":kiwifruit: *In Review*"}})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": "\n".join(review_lines[:REVIEW_MAX])}
    ]})
    blocks.append({"type": "divider"})

# Recently Completed
if all_done:
    done_lines = []
    for i in all_done:
        bk = next((b["key"] for b in BOARDS if i['key'].startswith(b["key"])), BOARDS[0]["key"])
        done_lines.append(ticket_line(i, bk, links, strikethrough=True))
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ":green_apple: *Recently Completed*"}})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": "\n".join(done_lines)}
    ]})
    blocks.append({"type": "divider"})

# Calendar — reads from calendar_cache.json (populated by /refresh via Microsoft Graph API)
if FEATURES.get("calendar", True):
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": ":calendar: *Upcoming Events*"}})
    cal_events = []
    try:
        cal_cache = os.path.join(DATA_DIR, "calendar_cache.json")
        with open(cal_cache) as f:
            cal_events = json.load(f)
    except:
        pass

    if cal_events:
        for ev in cal_events[:5]:
            summary = ev.get("subject", ev.get("summary", "Untitled"))
            start = ev.get("start", "")
            web_link = ev.get("webLink", ev.get("htmlLink", ""))
            if isinstance(start, dict):
                start = start.get("dateTime", start.get("date", ""))
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(et)
                display_date = dt.strftime("`%m/%d %I:%M%p`")
            except:
                display_date = f"`{start[:10]}`" if start else "`TBD`"
            is_teams = ev.get("isOnlineMeeting", False) or ev.get("onlineMeetingUrl")
            teams_icon = " :teams:" if is_teams else ""
            ev_text = f"{display_date} *{summary}*{teams_icon}"
            ev_block = {"type": "section", "text": {"type": "mrkdwn", "text": ev_text}}
            if web_link:
                ev_block["accessory"] = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open", "emoji": True},
                    "url": web_link,
                    "action_id": f"cal-{ev.get('id', 'x')[:20]}"
                }
            blocks.append(ev_block)
    else:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "_Calendar not connected — run /refresh to set it up_"}
        ]})
    blocks.append({"type": "divider"})

# Footer
blocks.append({"type": "context", "elements": [
    {"type": "mrkdwn", "text": f":robot_face: Hermes Agent · online · _updated {now_utc}_"}
]})

# --- Publish ---
resp = slack_post_api("views.publish", {
    "user_id": SLACK_USER_ID,
    "view": {"type": "home", "blocks": blocks}
})

print("ok:", resp.get("ok"))
if not resp.get("ok"):
    print("error:", resp.get("error"))
    print(json.dumps(resp, indent=2))
