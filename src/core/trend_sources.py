"""Источники тренд-скаута (E4.1 + AL-002 — Reddit на RSS вместо API).

RSS-фиды gaming-новостей + Reddit-сабреддиты через публичные `*.reddit.com/r/.../.rss`
(authorization не требуется, парсинг XML feedparser'ом). Список редактируется
в одном месте — не в БД, потому что меняется редко.
"""

from __future__ import annotations

RSS_FEEDS: list[str] = [
    # Gaming-новости
    "https://www.pcgamer.com/rss/",
    "https://www.rockpapershotgun.com/feed",
    "https://www.eurogamer.net/?format=rss",
    "https://kotaku.com/rss",
    "https://www.gamesradar.com/rss/",
    "https://www.ign.com/feeds/news",
    "https://www.polygon.com/rss/index.xml",

    # Reddit сабы через публичные RSS (без auth)
    "https://old.reddit.com/r/pcgaming/.rss",
    "https://old.reddit.com/r/buildapc/.rss",
    "https://old.reddit.com/r/battlestations/.rss",
    "https://old.reddit.com/r/pcmasterrace/.rss",
    "https://old.reddit.com/r/gamingsetups/.rss",
    "https://old.reddit.com/r/mechanicalkeyboards/.rss",
    "https://old.reddit.com/r/MouseReview/.rss",
    "https://old.reddit.com/r/monitors/.rss",
    "https://old.reddit.com/r/headphones/.rss",
    "https://old.reddit.com/r/hardware/.rss",
    "https://old.reddit.com/r/nvidia/.rss",
    "https://old.reddit.com/r/AMD/.rss",
    "https://old.reddit.com/r/SteamDeck/.rss",
    "https://old.reddit.com/r/gaming/.rss",
]

# Сколько последних записей брать с каждого источника.
RSS_PER_FEED = 10
