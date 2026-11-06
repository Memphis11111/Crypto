"""Перевіряє ціни і пише в Telegram.

Налаштування беруться з секретів репозиторію:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ALERTS_CONFIG (JSON із трекера),
  COINGECKO_API_KEY (необов'язково).
Репозиторій публічний, тому лог навмисно не містить ні монет, ні сум.
"""
import json, os, sys, time, urllib.request, urllib.parse

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
CG_KEY = os.environ.get("COINGECKO_API_KEY", "")
DRY = os.environ.get("DRY_RUN") == "1"
TEST = os.environ.get("TEST_MESSAGE") == "true"
STATE_FILE = os.environ.get("STATE_FILE", "alert-state.json")


def http_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "portfolio-alerts"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(20 * (i + 1))
                continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(5)
                continue
            raise


def cg(path, params):
    if CG_KEY:
        params["x_cg_demo_api_key"] = CG_KEY
    return http_json("https://api.coingecko.com/api/v3/" + path + "?" + urllib.parse.urlencode(params))


def send(text):
    if DRY:
        print("---- повідомлення ----\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % TOKEN, data=data, timeout=30)


def rsi(cl, n=14):
    if len(cl) < n + 1:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = cl[i] - cl[i - 1]; g += max(d, 0); l += max(-d, 0)
    ag, al = g / n, l / n
    for i in range(n + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def money(x):
    if x >= 100: return "$%s" % format(round(x), ",").replace(",", " ")
    if x >= 1: return "$%.2f" % x
    return "$%.4f" % x


def qty(x):
    return ("%.6f" % x).rstrip("0").rstrip(".") if x < 1 else ("%.4f" % x).rstrip("0").rstrip(".")


def main():
    if not DRY and (not TOKEN or not CHAT):
        sys.exit("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")
    raw = os.environ.get("ALERTS_CONFIG", "")
    if not raw:
        if TEST:
            send("✅ Бот підключено. Тепер додай секрет ALERTS_CONFIG із трекера — і я почну стежити за цінами.")
            return
        sys.exit("Не задано ALERTS_CONFIG — скопіюй налаштування з трекера")
    cfg = json.loads(raw)
    coins = cfg.get("coins", [])
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        state = {}

    ids = sorted({c["id"] for c in coins if c.get("id")})
    prices = {}
    if ids:
        data = cg("simple/price", {"ids": ",".join(ids), "vs_currencies": "usd"})
        prices = {k: v.get("usd") for k, v in data.items()}

    alerts = []
    for c in coins:
        p = prices.get(c.get("id"))
        if not p:
            continue
        s, avg, q = c["s"], c.get("avg") or 0, c.get("qty") or 0

        # ціль 2× — забрати вкладене
        key = "2x:" + s
        if avg > 0 and q > 0:
            if p >= 2 * avg and not state.get(key):
                sell = avg * q / p
                alerts.append("🎯 <b>%s</b> виріс у %.1f раза (%s → %s).\nЩоб забрати вкладене, продай %s %s ≈ %s — решта лишиться «безкоштовною»."
                              % (s, p / avg, money(avg), money(p), qty(sell), s, money(avg * q)))
                state[key] = True
            elif p < 1.8 * avg:
                state.pop(key, None)

        # своя ціль
        tg = c.get("target")
        key = "tg:%s:%s" % (s, tg)
        if tg:
            if p >= tg and not state.get(key):
                alerts.append("🏁 <b>%s</b> досяг твоєї цілі %s — зараз %s." % (s, money(tg), money(p)))
                state[key] = True
            elif p < 0.95 * tg:
                state.pop(key, None)

    # зона входу: RSI ≤ 35 для сильних монет
    for c in [c for c in coins if c.get("buy")]:
        p = prices.get(c.get("id"))
        if not p:
            continue
        time.sleep(2.5)
        try:
            d = cg("coins/%s/market_chart" % c["id"], {"vs_currency": "usd", "days": "120", "interval": "daily"})
            r = rsi([x[1] for x in d["prices"]])
        except Exception:
            continue
        if r is None:
            continue
        key = "rsi:" + c["s"]
        if r <= 35 and not state.get(key):
            tag = " (нова монета)" if c.get("cand") else ""
            alerts.append("🟢 <b>%s</b>%s перепродана — RSI %d, ціна %s.\nХороший момент для докупки за планом."
                          % (c["s"], tag, round(r), money(p)))
            state[key] = True
        elif r >= 45:
            state.pop(key, None)

    if TEST:
        alerts.insert(0, "✅ Бот працює. Стежу за %d монетами, перевірка кожні 4 години." % len(coins))
    if alerts:
        send("\n\n".join(alerts))
    json.dump(state, open(STATE_FILE, "w"))
    print("Перевірку завершено, сповіщень: %d" % len(alerts))


if __name__ == "__main__":
    main()
