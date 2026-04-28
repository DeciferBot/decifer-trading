# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER 2.0  —  theme_tracker.py                     ║
# ║   Sector & theme universe tracker                           ║
# ║                                                              ║
# ║   Three layers:                                              ║
# ║     1. Auto-detect from current holdings + watchlist         ║
# ║     2. Manually defined themes (AI/Semis, EV, Biotech...)   ║
# ║     3. Trending theme discovery (market narratives)          ║
# ║                                                              ║
# ║   Feeds into News Sentinel to determine which symbols       ║
# ║   to monitor for real-time news triggers.                   ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import logging
import os

from config import CONFIG

log = logging.getLogger("decifer.themes")

# ═══════════════════════════════════════════════════════════════
# LAYER 1: PREDEFINED THEME UNIVERSE
# ═══════════════════════════════════════════════════════════════
# Each theme is a market narrative with associated stocks.
# The sentinel monitors ALL stocks across active themes.

THEMES = {
    # ── AI & Data Infrastructure ────────────────────────────
    "ai_infrastructure": {
        "name": "AI & Data Infrastructure",
        "description": "Companies building and deploying AI models, chips, and cloud infrastructure",
        "symbols": [
            "NVDA",
            "AMD",
            "AVGO",
            "MRVL",
            "ARM",
            "TSM",  # AI chips
            "MSFT",
            "GOOGL",
            "AMZN",
            "META",
            "ORCL",  # Hyperscalers
            "PLTR",
            "SNOW",
            "DDOG",
            "MDB",
            "CRWD",  # AI software/data
            "SMCI",
            "DELL",
            "HPE",  # AI servers
            "ANET",
            "VRT",
            "EQIX",  # Network/power/data center
        ],
        "keywords": [
            "artificial intelligence",
            "ai",
            "gpu",
            "data center",
            "llm",
            "machine learning",
            "neural",
            "generative",
            "transformer",
            "inference",
            "training",
            "nvidia",
            "cuda",
            "ai chip",
        ],
        "active": True,
        "priority": 1,
    },
    # ── Semiconductor Cycle ─────────────────────────────────
    "semis": {
        "name": "Semiconductor Cycle",
        "description": "Semiconductor manufacturers and equipment makers — cyclical plays",
        "symbols": [
            "NVDA",
            "AMD",
            "INTC",
            "MU",
            "QCOM",
            "TXN",
            "AMAT",
            "LRCX",
            "KLAC",
            "ASML",
            "MRVL",
            "ON",
            "ADI",
            "NXPI",
            "MCHP",
            "TSM",
            "ARM",
        ],
        "keywords": [
            "semiconductor",
            "chip",
            "wafer",
            "fab",
            "foundry",
            "memory",
            "dram",
            "nand",
            "hbm",
            "process node",
            "chipmaker",
            "silicon",
        ],
        "active": True,
        "priority": 2,
    },
    # ── Electric Vehicles & Battery Tech ────────────────────
    "ev_battery": {
        "name": "EV & Battery Technology",
        "description": "Electric vehicle makers and battery/charging supply chain",
        "symbols": [
            "TSLA",
            "RIVN",
            "LCID",
            "NIO",
            "XPEV",
            "LI",
            "F",
            "GM",
            "TM",  # Legacy transitioning
            "CHPT",
            "BLNK",
            "EVGO",  # Charging
            "ALB",
            "SQM",
            "LAC",
            "LTHM",  # Lithium/battery materials
            "QS",
            "MVST",
            "ENVX",  # Battery tech
        ],
        "keywords": [
            "electric vehicle",
            "ev",
            "battery",
            "lithium",
            "charging",
            "range",
            "autonomous driving",
            "self-driving",
            "tesla",
            "supercharger",
            "gigafactory",
        ],
        "active": True,
        "priority": 3,
    },
    # ── Biotech & Pharma Catalysts ──────────────────────────
    "biotech": {
        "name": "Biotech & Pharma",
        "description": "Biotech companies with FDA catalysts, clinical trials, and drug approvals",
        "symbols": [
            "ABBV",
            "AMGN",
            "GILD",
            "BIIB",
            "REGN",
            "VRTX",
            "MRNA",
            "BNTX",
            "PFE",
            "LLY",
            "NVO",
            "SGEN",
            "BMRN",
            "ALNY",
            "IONS",
            "RARE",
            "XBI",  # Biotech ETF
        ],
        "keywords": [
            "fda",
            "approval",
            "clinical trial",
            "phase 3",
            "phase 2",
            "drug",
            "therapy",
            "oncology",
            "crispr",
            "gene therapy",
            "pdufa",
            "nda",
            "breakthrough designation",
            "adcom",
            "pipeline",
            "biologics",
        ],
        "active": True,
        "priority": 2,
    },
    # ── Fintech & Digital Payments ──────────────────────────
    "fintech": {
        "name": "Fintech & Payments",
        "description": "Digital payment networks, neobanks, and crypto infrastructure",
        "symbols": [
            "V",
            "MA",
            "PYPL",
            "SQ",
            "AFRM",
            "UPST",
            "COIN",
            "HOOD",
            "SOFI",
            "NU",
            "MELI",
            "FIS",
            "FISV",
            "GPN",
            "IBIT",
            "BITO",
            "MSTR",  # Crypto proxies
        ],
        "keywords": [
            "fintech",
            "payment",
            "digital wallet",
            "bnpl",
            "buy now pay later",
            "crypto",
            "bitcoin",
            "ethereum",
            "blockchain",
            "defi",
            "stablecoin",
            "central bank digital",
        ],
        "active": True,
        "priority": 3,
    },
    # ── Defense & Aerospace ─────────────────────────────────
    "defense": {
        "name": "Defense & Aerospace",
        "description": "Defense contractors and space companies — geopolitical sensitivity",
        "symbols": [
            "LMT",
            "RTX",
            "NOC",
            "GD",
            "BA",
            "LHX",
            "PLTR",
            "BWXT",
            "HII",
            "RKLB",
            "ASTS",
            "LUNR",  # Space
        ],
        "keywords": [
            "defense",
            "military",
            "pentagon",
            "nato",
            "missile",
            "fighter jet",
            "satellite",
            "space",
            "contract award",
            "geopolitical",
            "war",
            "sanctions",
            "arms",
        ],
        "active": True,
        "priority": 3,
    },
    # ── Energy Transition & Clean Energy ────────────────────
    "clean_energy": {
        "name": "Energy Transition",
        "description": "Renewable energy, nuclear, and grid infrastructure",
        "symbols": [
            "FSLR",
            "ENPH",
            "SEDG",
            "RUN",  # Solar
            "NEE",
            "AES",
            "CEG",  # Utilities/nuclear
            "VST",
            "OKLO",
            "SMR",
            "NNE",  # Nuclear/SMR
            "PLUG",
            "BE",
            "BLOOM",  # Hydrogen/fuel cells
            "ICLN",
            "TAN",  # Clean energy ETFs
        ],
        "keywords": [
            "solar",
            "wind",
            "nuclear",
            "renewable",
            "hydrogen",
            "clean energy",
            "grid",
            "power plant",
            "carbon",
            "net zero",
            "green energy",
            "small modular reactor",
        ],
        "active": True,
        "priority": 3,
    },
    # ── Tariffs & Trade War ─────────────────────────────────
    "tariffs_trade": {
        "name": "Tariffs & Trade War",
        "description": "Stocks most exposed to tariff escalation/de-escalation",
        "symbols": [
            "AAPL",
            "TSLA",
            "NKE",
            "CAT",
            "DE",  # China-exposed
            "BABA",
            "JD",
            "PDD",
            "BIDU",  # Chinese ADRs
            "UPS",
            "FDX",  # Logistics
            "X",
            "NUE",
            "CLF",  # Steel/metals
            "COPX",
            "FCX",  # Copper
        ],
        "keywords": [
            "tariff",
            "trade war",
            "import duty",
            "export ban",
            "sanctions",
            "china trade",
            "trade deal",
            "customs",
            "reshoring",
            "nearshoring",
            "supply chain",
        ],
        "active": True,
        "priority": 2,
    },
    # ── Healthcare Disruptors (Decifer Watchlist) ───────────
    "healthcare_disruptors": {
        "name": "Healthcare Disruptors",
        "description": "High-growth healthcare companies disrupting insurance, telehealth, and GLP-1",
        "symbols": [
            "HIMS",
            "OSCR",
            "TDOC",
            "DOCS",
            "LLY",
            "NVO",  # GLP-1 majors
            "AMZN",  # Amazon pharmacy
            "CVS",
            "UNH",
            "CI",  # Incumbents being disrupted
        ],
        "keywords": [
            "glp-1",
            "ozempic",
            "wegovy",
            "mounjaro",
            "telehealth",
            "digital health",
            "health insurance",
            "medicare",
            "pharmacy",
            "drug pricing",
        ],
        "active": True,
        "priority": 2,
    },
}


# ═══════════════════════════════════════════════════════════════
# LAYER 2: AUTO-DETECT FROM HOLDINGS & WATCHLIST
# ═══════════════════════════════════════════════════════════════


def get_holdings_symbols(open_positions: list, favourites: list | None = None) -> list[str]:
    """
    Extract unique symbols from current portfolio positions and favourites.
    These get highest monitoring priority in the sentinel.
    """
    symbols = set()

    # Current open positions
    for pos in open_positions or []:
        sym = pos.get("symbol", "")
        if sym:
            symbols.add(sym)

    # User favourites / watchlist
    for fav in favourites or []:
        if isinstance(fav, str) and fav:
            symbols.add(fav)

    return list(symbols)


def detect_themes_from_holdings(holding_symbols: list[str]) -> list[str]:
    """
    Given a list of held symbols, determine which themes are relevant.
    Returns list of theme keys where at least 1 holding overlaps.
    """
    active_themes = []
    for theme_key, theme in THEMES.items():
        if not theme.get("active", True):
            continue
        theme_syms = set(theme["symbols"])
        overlap = set(holding_symbols) & theme_syms
        if overlap:
            active_themes.append(theme_key)
            log.debug(f"Theme '{theme['name']}' activated by holdings: {overlap}")

    return active_themes


# ═══════════════════════════════════════════════════════════════
# LAYER 3: TRENDING THEMES (keyword-based detection)
# ═══════════════════════════════════════════════════════════════


def detect_trending_themes(headlines: list[str]) -> list[str]:
    """
    Scan a batch of general market headlines to detect which themes
    are currently "hot" based on keyword frequency.

    Returns list of theme keys sorted by relevance.
    """
    if not headlines:
        return []

    combined = " ".join(headlines).lower()
    theme_scores = {}

    for theme_key, theme in THEMES.items():
        if not theme.get("active", True):
            continue
        score = 0
        for kw in theme.get("keywords", []):
            count = combined.count(kw.lower())
            if count > 0:
                score += count
        if score > 0:
            theme_scores[theme_key] = score

    # Sort by score descending
    trending = sorted(theme_scores.keys(), key=lambda k: theme_scores[k], reverse=True)
    return trending


# ═══════════════════════════════════════════════════════════════
# MASTER UNIVERSE BUILDER — combines all three layers
# ═══════════════════════════════════════════════════════════════


def build_sentinel_universe(
    open_positions: list | None = None,
    favourites: list | None = None,
    trending_headlines: list[str] | None = None,
    max_symbols: int | None = None,
) -> list[str]:
    """
    Build the complete universe of symbols for the News Sentinel to monitor.

    Priority order (highest to lowest):
      1. Current holdings (always monitored, highest priority)
      2. User watchlist / favourites (always monitored)
      3. Symbols from themes overlapping with holdings
      4. All active theme symbols
      5. Trending theme symbols (if headlines provided)

    Returns deduplicated list of symbols, ordered by priority.
    """
    max_symbols = max_symbols or CONFIG.get("sentinel_max_symbols", 80)

    universe = []
    seen = set()

    def _add(symbols, tag=""):
        for sym in symbols:
            if sym and sym not in seen:
                seen.add(sym)
                universe.append(sym)

    # ── Priority 1: Holdings ──────────────────────────────────
    holdings = get_holdings_symbols(open_positions, favourites)
    _add(holdings, "holdings")
    log.debug(f"Sentinel universe — holdings: {len(holdings)}")

    # ── Priority 2: Themes matching holdings ──────────────────
    holding_themes = detect_themes_from_holdings(holdings)
    for theme_key in holding_themes:
        theme_syms = THEMES[theme_key]["symbols"]
        _add(theme_syms, f"theme:{theme_key}")

    # ── Priority 3: All active themes ─────────────────────────
    active_themes = [k for k, v in THEMES.items() if v.get("active", True)]
    # Sort by priority
    active_themes.sort(key=lambda k: THEMES[k].get("priority", 5))
    for theme_key in active_themes:
        _add(THEMES[theme_key]["symbols"], f"theme:{theme_key}")

    # ── Priority 4: Trending themes (boost) ───────────────────
    if trending_headlines:
        trending = detect_trending_themes(trending_headlines)
        for theme_key in trending[:3]:  # Top 3 trending themes
            _add(THEMES[theme_key]["symbols"], f"trending:{theme_key}")

    # ── Core symbols always included ──────────────────────────
    try:
        from scanner import CORE_SYMBOLS, MOMENTUM_FALLBACK

        _add(CORE_SYMBOLS, "core")
        _add(MOMENTUM_FALLBACK[:10], "momentum")  # Top 10 momentum
    except ImportError:
        # Fallback if scanner can't be imported (shouldn't happen in prod)
        _add(["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA", "AMZN", "MSFT", "META", "AMD"], "fallback")

    # Cap at max
    if len(universe) > max_symbols:
        universe = universe[:max_symbols]

    log.info(
        f"Sentinel universe: {len(universe)} symbols | "
        f"holdings={len(holdings)} | "
        f"themes={len(holding_themes)} active | "
        f"trending={len(trending_headlines or [])} headlines checked"
    )

    return universe


# ═══════════════════════════════════════════════════════════════
# THEME MANAGEMENT — add/remove/toggle themes
# ═══════════════════════════════════════════════════════════════

_CUSTOM_THEMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "custom_themes.json")


def add_custom_theme(key: str, name: str, symbols: list[str], keywords: list[str] | None = None, priority: int = 3):
    """Add a user-defined theme at runtime."""
    THEMES[key] = {
        "name": name,
        "description": f"Custom theme: {name}",
        "symbols": symbols,
        "keywords": keywords or [],
        "active": True,
        "priority": priority,
        "custom": True,
    }
    _save_custom_themes()
    log.info(f"Custom theme added: '{name}' with {len(symbols)} symbols")


def remove_theme(key: str):
    """Remove a theme (only custom themes can be fully removed)."""
    if key in THEMES:
        if THEMES[key].get("custom"):
            del THEMES[key]
            _save_custom_themes()
        else:
            THEMES[key]["active"] = False
        log.info(f"Theme '{key}' removed/deactivated")


def toggle_theme(key: str, active: bool):
    """Enable or disable a theme."""
    if key in THEMES:
        THEMES[key]["active"] = active
        log.info(f"Theme '{key}' {'activated' if active else 'deactivated'}")


def get_all_themes() -> dict:
    """Return all themes with their status."""
    return {
        k: {
            "name": v["name"],
            "symbols_count": len(v["symbols"]),
            "active": v.get("active", True),
            "priority": v.get("priority", 5),
            "custom": v.get("custom", False),
        }
        for k, v in THEMES.items()
    }


def _save_custom_themes():
    """Persist custom themes to disk."""
    customs = {k: v for k, v in THEMES.items() if v.get("custom")}
    try:
        os.makedirs(os.path.dirname(_CUSTOM_THEMES_FILE), exist_ok=True)
        with open(_CUSTOM_THEMES_FILE, "w") as f:
            json.dump(customs, f, indent=2)
    except Exception as e:
        log.error(f"Failed to save custom themes: {e}")


def load_custom_themes():
    """Load custom themes from disk on startup."""
    try:
        if os.path.exists(_CUSTOM_THEMES_FILE):
            with open(_CUSTOM_THEMES_FILE) as f:
                customs = json.load(f)
            for k, v in customs.items():
                v["custom"] = True
                THEMES[k] = v
            log.info(f"Loaded {len(customs)} custom themes from disk")
    except Exception as e:
        log.error(f"Failed to load custom themes: {e}")


# ═══════════════════════════════════════════════════════════════
# THEME-BASED NEWS RELEVANCE SCORING
# ═══════════════════════════════════════════════════════════════


def score_headline_theme_relevance(headline: str, symbol: str) -> dict:
    """
    Score how relevant a headline is to active themes.
    Returns {theme_key: score} for matching themes.
    Used to boost materiality when news aligns with a trending narrative.
    """
    h_lower = headline.lower()
    relevance = {}

    for theme_key, theme in THEMES.items():
        if not theme.get("active", True):
            continue

        # Check if symbol is in this theme
        sym_in_theme = symbol in theme["symbols"]

        # Check keyword matches
        kw_matches = sum(1 for kw in theme.get("keywords", []) if kw.lower() in h_lower)

        if kw_matches > 0 or sym_in_theme:
            score = kw_matches * 2
            if sym_in_theme:
                score += 3  # Bonus for symbol being in the theme
            relevance[theme_key] = score

    return relevance
