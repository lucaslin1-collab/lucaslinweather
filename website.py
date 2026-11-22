"""Build the public website (Today, Track record, How it works) as static files in site/."""
import json
import os

import numpy as np
import pandas as pd

import config
from dashboard import (FOOTER, boards_section, eastern_now, esc, header_html, money, notices_html,
                       page, pct, picks_section, record_section, sort_picks)

MARK = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect class="mk-bg" width="32" height="32" rx="8" fill="#13202f"/>'
        '<rect class="mk-stem" x="13" y="6" width="6" height="15" rx="3" fill="#fbfcfd"/>'
        '<rect x="14.5" y="11" width="3" height="10" rx="1.5" fill="#eb6834"/>'
        '<circle cx="16" cy="22.5" r="4.5" fill="#eb6834"/></svg>')

PAGES = [("index.html", "Today"), ("record.html", "Track record"), ("method.html", "How it works")]


def nav(current):
    current_attr = ' aria-current="page"'
    links = "".join(
        f'<li><a class="nav" href="{href}"{current_attr if href == current else ""}>{label}</a></li>'
        for href, label in PAGES)
    if config.HUB_URL:
        links += f'<li><a class="nav" href="{config.HUB_URL}">All bots</a></li>'
    return (f'<nav class="site-nav" aria-label="Site"><a class="brand" href="index.html">{MARK}'
            f'<span>{esc(config.SITE_NAME)}</span></a><ul>{links}</ul></nav>')


def base_url():
    if config.SITE_DOMAIN:
        return f"https://{config.SITE_DOMAIN}"
    return config.SITE_URL


def page_url(path):
    return f"{base_url()}/{'' if path == 'index.html' else path}" if base_url() else ""


def head(path, title, description):
    url = page_url(path)
    links = (f'<link rel="canonical" href="{url}"><meta property="og:url" content="{url}">' if url else "")
    return (f'<meta name="description" content="{esc(description)}">{links}'
            '<link rel="icon" href="favicon.svg" type="image/svg+xml">'
            '<meta property="og:type" content="website">'
            f'<meta property="og:title" content="{esc(title)}">'
            f'<meta property="og:description" content="{esc(description)}">'
            f'<meta property="og:site_name" content="{esc(config.SITE_NAME)}">'
            '<meta name="twitter:card" content="summary">')


def site_footer():
    return FOOTER.replace("</p></footer>",
                          f' Not affiliated with Kalshi. <a href="method.html">How it works</a>.</p></footer>')


# --- pages ------------------------------------------------------------------------

def today_page(panels, picks, summary, demo, generated):
    body = (nav("index.html") + header_html(panels, picks, generated, "Today’s high-temperature picks")
            + notices_html(summary, demo) + intl_notice() + picks_section(picks, record_href="record.html")
            + dayahead_section() + boards_section(panels, picks) + site_footer())
    desc = ("Daily forecasts and AI picks for every Kalshi highest-temperature market, "
            "with an honest public track record.")
    return page(body, f"{config.SITE_NAME}: today’s picks", head("index.html", config.SITE_NAME, desc))


def intl_notice():
    try:
        import intl_watch
        w = intl_watch.load()
    except Exception:  # noqa: BLE001
        return ""
    if not w or not w.get("open"):
        return ""
    names = ", ".join(v["title"] for v in w["open"].values())
    return (f'<p class="notice">Kalshi has opened international daily-high markets: {esc(names)}. '
            'The AI doesn’t price these yet.</p>')


def picks_history(log, limit=60, heading="Every logged pick"):
    if log is None or log.empty:
        return ""
    recent = log.sort_values(["date", "ev"], ascending=[False, False]).head(limit)
    rows = []
    for r in recent.itertuples():
        if pd.isna(r.won):
            result = '<span class="badge warn"><span class="ico" aria-hidden="true">○</span>Pending</span>'
            profit = "–"
        elif r.won:
            result = '<span class="badge good"><span class="ico" aria-hidden="true">✓</span>Won</span>'
            profit = money(r.pnl)
        else:
            result = '<span class="badge bad"><span class="ico" aria-hidden="true">✕</span>Lost</span>'
            profit = money(r.pnl)
        rows.append(f'<tr><td>{pd.Timestamp(r.date):%b %-d}</td><td class="city">{esc(r.city)}</td>'
                    f'<td class="num">{esc(r.label if isinstance(r.label, str) else "")}</td>'
                    f'<td><span class="side {str(r.side).lower()}">Buy {esc(r.side)}</span></td>'
                    f'<td class="num">{r.price * 100:.0f}¢</td><td class="num">{pct(r.prob)}</td>'
                    f'<td>{result}</td><td class="num strong">{profit}</td></tr>')
    return (f'<section><h2>{esc(heading)}</h2><p class="fine">Newest first, at the price shown when '
            'the pick was published. Profit is per $1 contract after fees.</p>'
            '<div class="scroll"><table class="log"><thead><tr><th>Date</th><th>City</th><th>Bracket</th>'
            '<th>Bet</th><th class="num">Price</th><th class="num">AI chance</th><th>Result</th>'
            f'<th class="num">Profit</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def dayahead_log():
    try:
        import dayahead
        return dayahead.load_dayahead_log()
    except Exception:  # noqa: BLE001
        return None


def dayahead_section():
    """Tomorrow's picks from the midday day-before run (a separate paper test)."""
    try:
        import dayahead
        st = dayahead.load_state()
    except Exception:  # noqa: BLE001
        return ""
    if not st:
        return ""
    day = pd.Timestamp(st["target_date"])
    made = pd.Timestamp(st["generated"])
    intro = (f'<p class="fine">Paper test. In the backtest, buying around midday the day before beat '
             f'buying at 8 a.m., so this run prices {day:%A}’s markets a day early. Picked '
             f'{made:%b %-d at %-I:%M %p} ET, tracked separately in the '
             f'<a href="record.html#dayahead">day-before record</a>.</p>')
    return sort_and_section(st["picks"], f"Early bets for {day:%A, %B %-d}", intro)


def sort_and_section(picks, heading, intro):
    return picks_section(sort_picks(picks), record_href="record.html#dayahead", heading=heading, intro=intro,
                         when="cleared the filters")


def dayahead_record():
    log = dayahead_log()
    if log is None or log.empty:
        return ('<section id="dayahead"><h2>Day-before picks</h2><p class="fine">A paper test of buying '
                'around midday the day before. Its first picks will show here once they settle.</p></section>')
    return record_section(log, None, heading="Day-before picks", anchor="dayahead", table=False)


def _usd(x):
    if x is None:
        return "–"
    return f"−${abs(x):,.0f}" if x < -0.5 else f"${x:,.0f}"


def _cents(r):
    return "–" if r is None else f"{'+' if r >= 0 else '−'}{abs(r) * 100:.1f}¢"


STATUS = {
    "waiting": ("unproven", "No settled picks yet"),
    "too early": ("unproven", "Too early to tell"),
    "on track": ("edge", "On track"),
    "losing, within luck": ("unproven", "Behind, but within luck"),
    "behind": ("no edge", "Behind the backtest"),
}


def insights_section():
    """Plain-language takeaways from results/insights.json, plus live picks vs backtest."""
    import insights
    from dashboard import verdict_badge
    data = insights.load()
    if not data:
        return ""
    et = data["entry_times"]
    order = sorted(et, key=lambda s: config.ENTRY_TIMES.get(s, (99,))[0])
    out = ['<section id="insights"><h2>Insights</h2>'
           '<p class="fine">What the history says in dollars, and whether the paper picks are living up to it. '
           f'Money assumes a flat ${data["stake"]} on every bet, after Kalshi’s fees. Refreshed with each backtest '
           f'run; last {pd.Timestamp(data["generated"]):%B %-d}.</p>']

    # 1. Is the edge holding?
    import daily
    rows = []
    for name, slot, log in (("Day-before picks", "d1_12", dayahead_log()), ("8 a.m. picks", "d0_08", daily.load_log())):
        if slot not in et:
            continue
        c = insights.compare_live(log, et[slot])
        v, words = STATUS[c["status"]]
        band = (f'{_cents(c["band"][0])} to {_cents(c["band"][1])}' if c["band"] else "–")
        rows.append(f'<tr><td class="city">{name}</td><td class="num">{c["n"]} of {c["target"]}</td>'
                    f'<td class="num strong">{_cents(c["actual"])}</td><td class="num">{_cents(c["expected"])}</td>'
                    f'<td class="num">{band}</td><td>{verdict_badge(v).split("</span>")[0]}</span>{words}</span></td></tr>')
    out.append('<h3>Is the edge holding?</h3><div class="scroll"><table class="cities"><thead><tr><th>Picks</th>'
               '<th class="num">Settled</th><th class="num">Profit per $1, so far</th>'
               '<th class="num">Backtest expects</th><th class="num">Normal luck range</th><th>Status</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div>'
               '<p class="fine">The luck range is where results land 90% of the time if the backtest is right, and it '
               f'narrows as picks settle. With an edge this small it takes about {insights.TARGET_PICKS} settled picks '
               'to tell skill from luck.</p>')

    # 2. What $100 a bet would have made
    rows = []
    for s in order:
        e = et[s]
        if not e.get("bets"):
            continue
        rows.append(f'<tr><td class="city">{esc(e["label"])}</td><td class="num">{e["bets_per_day"]:.1f}</td>'
                    f'<td class="num">{_usd(e["per_bet"])}</td><td class="num">{_usd(e["per_day"])}</td>'
                    f'<td class="num strong">{_usd(e["per_year"])}</td>'
                    f'<td class="num">{_usd(e["range"][0])} to {_usd(e["range"][1])}</td>'
                    f'<td class="num">{_usd(e["recent_per_year"])}</td><td class="num">{_usd(e["worst_month"])}</td></tr>')
    d = et.get("d1_12", {})
    best = d.get("best_only")
    extra = (f' Taking only the single best-looking bet each day instead: about {best["bets_per_year"]:.0f} bets '
             f'and {_usd(best["per_year"])} a year at the day-before time.' if best else "")
    nob = (f' The day-before run places no bet on {d["no_bet_days"]:.0%} of days and up to '
           f'{d["max_bets_day"]} on a busy one; a typical day has {d["median_bets_on_bet_days"]:.0f}.' if d.get("bets") else "")
    out.append(f'<h3>What ${data["stake"]} a bet would have made</h3><div class="scroll"><table class="cities"><thead><tr>'
               '<th>Entry time</th><th class="num">Bets a day</th><th class="num">Per bet</th>'
               '<th class="num">Per day</th><th class="num">Per year</th><th class="num">Likely range, per year</th>'
               f'<th class="num">Last {data["recent_days"]} days, per year</th><th class="num">Worst month</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table></div>'
               f'<p class="fine">{nob}{extra} These assume every bet fills at the posted price. At ${data["stake"]} '
               'a bet the day before, many markets don’t have that many contracts on offer, so real results '
               'would be smaller.</p>')

    # 3. How steady it is
    halves = sorted({h for s in order for h in et[s].get("halves", {})})
    if halves:
        head = "".join(f'<th class="num">{esc(et[s]["label"])}</th>' for s in order)
        body = []
        for h in halves:
            cells = "".join(f'<td class="num">{_cents(et[s].get("halves", {}).get(h, {}).get("ret"))}</td>' for s in order)
            body.append(f'<tr><td class="city">{esc(h)}</td>{cells}</tr>')
        for key, words in (("without_top1", "Without its best city"), ("without_top3", "Without its best 3 cities")):
            cells = "".join(f'<td class="num">{_cents(et[s].get(key, {}).get("ret"))}</td>' for s in order)
            body.append(f'<tr><td class="city">{words}</td>{cells}</tr>')
        out.append('<h3>How steady it is</h3><p class="fine">Profit per $1 bet in each half-year, and with the '
                   'best cities taken out. An edge that only shows up in a few cities or a few months is more '
                   'likely to be luck.</p><div class="scroll"><table class="cities"><thead><tr><th>Period</th>'
                   f'{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')

    # 4. Where the day-before money comes from
    if d.get("top_cities"):
        top = ", ".join(f'{esc(c["city"])} ({_usd(c["per_year"])})' for c in d["top_cities"])
        bot = ", ".join(f'{esc(c["city"])} ({_usd(c["per_year"])})' for c in d["bottom_cities"])
        w3 = d["without_top3"]
        out.append('<h3>Where the day-before profit comes from</h3>'
                   f'<p>Best cities, per year at ${data["stake"]} a bet: {top}. Weakest: {bot}. Without '
                   f'{", ".join(esc(c) for c in w3["cities"])}, the rest would have made about '
                   f'{_usd(w3["per_year"])} a year (likely range {_usd(w3["range"][0])} to {_usd(w3["range"][1])}).</p>')
    out.append('</section>')
    return "".join(out)


def timing_section():
    """Backtest by entry time, from results/timing.json (written by the backfill job)."""
    import json
    from dashboard import ORDER, verdict_badge
    p = os.path.join(config.RESULTS_DIR, "timing.json")
    if not os.path.exists(p):
        return ""
    t = json.load(open(p)).get("entry_times", {})
    if not t:
        return ""
    rows = []
    for slot, e in sorted(t.items(), key=lambda kv: kv[1]["hours"]):
        o = e["overall"]
        ci = o.get("ci90")
        rows.append(f'<tr><td class="city">{esc(e["label"])}</td><td>{verdict_badge(o["verdict"])}</td>'
                    f'<td class="num">{o.get("bss_model", float("nan")):+.3f}</td>'
                    f'<td class="num">{o.get("bss_blend", float("nan")):+.3f}</td>'
                    f'<td class="num">{o["trades"]}</td><td class="num">{o.get("bets_per_day", 0):.1f}</td>'
                    f'<td class="num">{pct(o.get("win_rate"))}</td><td class="num strong">{money(o.get("pnl", 0.0))}</td>'
                    f'<td class="num">{(money(ci[0]) + " to " + money(ci[1])) if ci else "–"}</td></tr>')
    best = []
    for slot, e in t.items():
        for s, c in e["cities"].items():
            if c["verdict"] == "edge":
                best.append(f'{esc(c["city"])} at {esc(e["label"].lower())}')
    note = (f'<p class="fine">Proven edge: {"; ".join(best)}.</p>' if best else
            '<p class="fine">No entry time has a proven edge yet.</p>')
    first = next(iter(t.values()))
    return ('<section><h2>Backtest by entry time</h2><p class="fine">The same AI, rebuilt with only what '
            'was public at each entry time: that moment’s prices, the latest NWS forecast run, and model '
            'forecasts issued two days ahead for entries the day before. Each row is a separate test, '
            f'{esc(first["period"][0])} to {esc(first["period"][1])}, with Kalshi’s fees included.</p>'
            '<div class="scroll"><table class="cities"><thead><tr><th>Entry time</th><th>Verdict</th>'
            '<th class="num">AI alone vs market</th><th class="num">Blended vs market</th><th class="num">Bets</th>'
            '<th class="num">Per day</th><th class="num">Won</th><th class="num">Profit</th>'
            f'<th class="num">90% range</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{note}</section>')


def record_page(log, summary, demo, generated):
    intro = ('<header class="top"><h1>Track record</h1><p class="lede">Every pick is logged at the '
             'price it was published at and scored when the market settles, wins and losses alike. '
             f'Updated {generated:%B %-d, %-I:%M %p} ET.</p></header>')
    body = (nav("record.html") + intro + (notices_html(None, True).split('<p class="notice">')[0] if demo else "")
            + record_section(log, summary, heading="Results so far") + dayahead_record() + insights_section() + timing_section()
            + picks_history(log) + picks_history(dayahead_log(), heading="Every day-before pick")
            + site_footer())
    desc = "Every published pick and its result, plus a month-by-month backtest for each city."
    return page(body, f"Track record: {config.SITE_NAME}", head("record.html", "Track record", desc))


def method_page():
    e, s = config.EDGE_THRESHOLD * 100, config.MAX_SPREAD * 100
    body = nav("method.html") + f'''
<header class="top"><h1>How it works</h1><p class="lede">An AI that forecasts each city’s daily high
and checks whether Kalshi’s prices agree.</p></header>
<article class="prose">
<h2>The markets</h2>
<p>Kalshi lists a market every day for the highest temperature in two dozen US cities. Each market
splits the possible highs into brackets, such as 58–59°F, and each bracket trades between 1¢ and 99¢.
A bracket pays $1 if the official high lands in it. The price is roughly the crowd’s estimate of
the chance that it will.</p>
<p>The official high comes from the National Weather Service’s Daily Climate Report for one specific
station. That’s Central Park for New York and Midway for Chicago, not O’Hare. The AI reads the station
from each market’s contract rules rather than its title, because titles have been wrong.</p>

<h2>The forecast</h2>
<p>Each morning the AI takes four global weather models’ forecasts (GFS, ECMWF, ICON and GEM) for
that station, plus the National Weather Service’s own forecast for it: the National Blend of Models,
from the latest run issued before 8 a.m. It corrects each model’s typical bias there, learned from past days. It then turns them
into a full range of likely highs, which is wider on days when the models disagree. That range
assigns a probability to every bracket.</p>

<h2>Checking against the market</h2>
<p>The market often knows things the models don’t, so the AI doesn’t simply trust itself. It blends
its probability with the market price, weighting each by how well it predicted past outcomes. A bet is
published only when:</p>
<ul>
<li>the expected profit beats {e:.0f}¢ per $1 contract after Kalshi’s fee,</li>
<li>the bid–ask spread is {s:.0f}¢ or less, so the price is real,</li>
<li>the price isn’t near-certain (below {config.MIN_PRICE * 100:.0f}¢ or above {config.MAX_PRICE * 100:.0f}¢),
where rare forecast misses dominate, and</li>
<li>it’s one of at most {config.MAX_PICKS_PER_CITY} bets in that city, since brackets in one city move
together.</li>
</ul>

<h2>Proving it</h2>
<p>Every city is backtested month by month. Each month is predicted using only data that existed
before it, at the 8 a.m. price, with fees included. A city earns <strong>proven edge</strong> only
after at least 30 bets, with forecasts more accurate than the market and a 90% range on profit that
stays above $0. Cities without that evidence are <strong>unproven</strong>. Cities where the AI loses
to the market are marked <strong>no edge</strong>.</p>
<p>The <a href="record.html">track record</a> also logs every published pick at its published price and
scores it when the market settles. That record is the real test, because nothing in it can be tuned
after the fact.</p>

<h2>Schedule</h2>
<p>The site updates every morning shortly after 8 a.m. Eastern, when the day’s markets are open and
the overnight model runs are in. Prices move during the day, so a pick may no longer be available at
the price shown.</p>

<h2>Limits</h2>
<p>Weather markets are watched closely by traders using the same public forecasts. Most of the time
the market is right, and an honest system will often find nothing worth betting. Past results don’t
guarantee future ones. This is a research project, not financial advice, and it isn’t affiliated with
Kalshi.</p>
<p>Weather data comes from <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY 4.0) and the
NWS Daily Climate Report, through Iowa State University’s Iowa Environmental Mesonet archive.</p>
</article>''' + site_footer()
    desc = "How the AI forecasts daily highs, compares them with Kalshi prices, and proves its record."
    return page(body, f"How it works: {config.SITE_NAME}", head("method.html", "How it works", desc))


def not_found_page():
    body = (nav("") + '<header class="top"><h1>Page not found</h1><p class="lede">That page doesn’t exist. '
            '<a href="index.html">See today’s picks</a>.</p></header>')
    return page(body, f"Not found: {config.SITE_NAME}")


# --- build -----------------------------------------------------------------------------

def picks_json(picks, generated):
    cols = ["date", "series", "city", "ticker", "label", "side", "price", "prob", "ev", "verdict"]
    rows = [] if picks.empty else picks[cols].assign(date=picks["date"].dt.strftime("%Y-%m-%d")).to_dict("records")
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (np.floating, float)):
                r[k] = round(float(v), 4)
    return json.dumps({"generated": generated.isoformat(timespec="minutes"), "picks": rows}, indent=2)


def build(panels, picks, log, summary, demo=False, out_dir=None):
    out_dir = out_dir or config.SITE_DIR
    os.makedirs(out_dir, exist_ok=True)
    generated = eastern_now()
    picks = sort_picks(picks)
    files = {
        "index.html": today_page(panels, picks, summary, demo, generated),
        "record.html": record_page(log, summary, demo, generated),
        "method.html": method_page(),
        "404.html": not_found_page(),
        "picks.json": picks_json(picks, generated),
        "favicon.svg": MARK.replace('aria-hidden="true"', 'xmlns="http://www.w3.org/2000/svg"'),
        ".nojekyll": "",
    }
    if config.SITE_DOMAIN:
        files["CNAME"] = config.SITE_DOMAIN + "\n"
    if base_url():
        files["robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {base_url()}/sitemap.xml\n"
        files["sitemap.xml"] = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                + "".join(f"<url><loc>{page_url(p)}</loc><lastmod>{generated:%Y-%m-%d}</lastmod></url>"
                                          for p, _ in PAGES) + "</urlset>\n")
    for name, content in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    print(f"Website: {os.path.abspath(out_dir)}/index.html")
    return out_dir
