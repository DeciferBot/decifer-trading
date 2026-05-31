// Supply chain definitions for each industry theme.
// Symbols may repeat across chains — intentional.
// Each stage flows left → right.

export interface ChainStage {
  id: string;
  label: string;
  sublabel?: string;
  symbols: string[];
}

export interface Chain {
  id: string;
  label: string;
  color: string;
  description: string;
  stages: ChainStage[];
}

// Conviction scores from TTG symbol_exposures.json (confidence field, 0–1).
// Only symbols with a curated TTG record are included.
export const SYMBOL_CONVICTION: Record<string, number> = {
  // AI / Energy / Nuclear
  NVDA: 0.95, VRT: 0.92, CEG: 0.90, AVGO: 0.88, ETN: 0.88, CCJ: 0.88,
  PWR: 0.85, URA: 0.85, LEU: 0.85, ANET: 0.82, EME: 0.82, VST: 0.82,
  URNM: 0.82, BWXT: 0.82, FCX: 0.82, AMD: 0.80, NLR: 0.80, MRVL: 0.75,
  HUBB: 0.75, GEV: 0.75, SMCI: 0.72, UEC: 0.72, IREN: 0.72, UUUU: 0.70,
  APLD: 0.70, DELL: 0.68, NOW: 0.65, DNN: 0.62, IBM: 0.60, SMR: 0.55, NEE: 0.52,
  // Critical Minerals
  SCCO: 0.85, COPX: 0.85, ICOP: 0.82, LIT: 0.82, ALB: 0.78, MP: 0.75,
  SQM: 0.72, TECK: 0.68,
  // Cybersecurity
  PANW: 0.90, CRWD: 0.88, CIBR: 0.88, FTNT: 0.85, ZS: 0.85, HACK: 0.82,
  NET: 0.80, OKTA: 0.80,
  // Defence
  LMT: 0.92, RTX: 0.90, NOC: 0.88, HII: 0.88, ITA: 0.88, GD: 0.85,
  LHX: 0.82, XAR: 0.82, AVAV: 0.80, KTOS: 0.72, PLTR: 0.68, ASTS: 0.65, RKLB: 0.62,
  // GLP-1 / Healthcare
  LLY: 0.95, NVO: 0.95, WST: 0.82, TMO: 0.78, DHR: 0.72, BDX: 0.72,
  MDLZ: 0.62, HSY: 0.60, AMGN: 0.60, DXCM: 0.58, PODD: 0.58, KO: 0.52, UNH: 0.50, MCD: 0.50,
  // Gold
  GLD: 0.92, IAU: 0.90, FNV: 0.88, GDX: 0.88, NEM: 0.85, WPM: 0.85,
  GOLD: 0.82, AEM: 0.82, RGLD: 0.82, GDXJ: 0.82,
  // Crypto
  IBIT: 0.90, FBTC: 0.85, COIN: 0.82, MSTR: 0.78, BLOK: 0.78,
  GBTC: 0.75, MARA: 0.72, RIOT: 0.70, CLSK: 0.62, HOOD: 0.60,
  // Reshoring
  ASML: 0.90, AMAT: 0.88, LRCX: 0.85, KLAC: 0.85, PAVE: 0.85,
  ROK: 0.82, NUE: 0.70, HON: 0.68, VMC: 0.68,
  // Housing
  DHI: 0.88, ITB: 0.88, LEN: 0.85, PHM: 0.82, XHB: 0.82, NVR: 0.80,
  BLDR: 0.80, RKT: 0.78, OC: 0.72, HD: 0.72, LOW: 0.70, FNF: 0.68,
  // Water
  XYL: 0.88, PHO: 0.88, AWK: 0.85, FIW: 0.82, PNR: 0.80, ECL: 0.80,
  ITRI: 0.75, WTRG: 0.72,
};

export const CHAINS: Chain[] = [
  // ─────────────────────────────────────────────────────────────────────────
  // 1. AI Infrastructure
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "ai_infrastructure",
    label: "AI Infrastructure",
    color: "#6366f1",
    description: "From chip materials and fabs to cloud platforms and enterprise AI",
    stages: [
      {
        id: "minerals",
        label: "Critical Minerals",
        sublabel: "Copper, rare earth, lithium",
        symbols: ["FCX", "MP", "ALB", "SQM"],
      },
      {
        id: "fab_equipment",
        label: "Fab Equipment",
        sublabel: "Lithography, deposition, etch",
        symbols: ["ASML", "AMAT", "KLAC", "LRCX"],
      },
      {
        id: "eda",
        label: "EDA & IP",
        sublabel: "Chip design tools",
        symbols: ["SNPS", "CDNS"],
      },
      {
        id: "foundry",
        label: "Foundry",
        sublabel: "Contract chip manufacturing",
        symbols: ["TSM", "INTC"],
      },
      {
        id: "chips",
        label: "Compute Chips",
        sublabel: "GPUs, CPUs, custom ASICs",
        symbols: ["NVDA", "AMD", "AVGO"],
      },
      {
        id: "memory",
        label: "Memory",
        sublabel: "DRAM, HBM, NAND storage",
        symbols: ["MU", "WDC", "SNDK"],
      },
      {
        id: "networking",
        label: "Networking & Silicon",
        sublabel: "Switches, routers, networking ASICs",
        symbols: ["ANET", "MRVL"],
      },
      {
        id: "photonics",
        label: "Photonics & Optical",
        sublabel: "Optical transceivers, DSP, fibre",
        symbols: ["CIEN", "COHR", "LITE"],
      },
      {
        id: "power_cooling",
        label: "Power & Cooling",
        sublabel: "UPS, thermal, electrical equipment",
        symbols: ["VRT", "ETN", "EME", "PWR", "HUBB"],
      },
      {
        id: "nuclear",
        label: "Nuclear & Energy",
        sublabel: "Nuclear power, uranium, SMRs",
        symbols: ["CEG", "VST", "NRG", "CCJ", "BWXT", "GEV", "SMR", "URA", "URNM", "LEU"],
      },
      {
        id: "data_centre",
        label: "Data Centre",
        sublabel: "Servers, colos, GPU clouds",
        symbols: ["SMCI", "DELL", "HPE", "EQIX", "DLR", "NBIS", "IREN", "APLD"],
      },
      {
        id: "cloud",
        label: "Cloud Platforms",
        sublabel: "IaaS, PaaS, hyperscalers",
        symbols: ["MSFT", "AMZN", "GOOG", "META", "ORCL"],
      },
      {
        id: "enterprise_ai",
        label: "Enterprise AI Apps",
        sublabel: "SaaS, automation, analytics",
        symbols: ["CRM", "NOW", "PLTR", "IBM"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 2. Cybersecurity
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "cybersecurity",
    label: "Cybersecurity",
    color: "#06b6d4",
    description: "From endpoint protection to cloud security and identity",
    stages: [
      {
        id: "platform_security",
        label: "Platform Security",
        sublabel: "Endpoint, XDR, SOC platforms",
        symbols: ["CRWD", "PANW", "FTNT", "S"],
      },
      {
        id: "cloud_sase",
        label: "Cloud & SASE",
        sublabel: "Zero trust, cloud-native security",
        symbols: ["ZS", "NET", "ZSCL"],
      },
      {
        id: "identity",
        label: "Identity & Access",
        sublabel: "IAM, privileged access, data security",
        symbols: ["OKTA", "CYBR", "VRNS"],
      },
      {
        id: "resilience",
        label: "Recovery & Resilience",
        sublabel: "Backup, threat intel, compliance",
        symbols: ["VEEAM", "TENB", "RPM"],
      },
      {
        id: "cyber_etfs",
        label: "ETFs",
        sublabel: "Broad cyber exposure",
        symbols: ["CIBR", "HACK", "BUG"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 3. Defence
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "defence",
    label: "Defence",
    color: "#ef4444",
    description: "From aerospace materials to defence IT and services",
    stages: [
      {
        id: "materials",
        label: "Aerospace Materials",
        sublabel: "Titanium, composites, alloys",
        symbols: ["HXL", "ATI"],
      },
      {
        id: "components",
        label: "Components",
        sublabel: "Sensors, electronics, structures",
        symbols: ["MOGA", "CW", "HEI", "TDY"],
      },
      {
        id: "drones",
        label: "Drones & Autonomy",
        sublabel: "UAS, autonomous systems, C2",
        symbols: ["KTOS", "ONDS", "AVAV", "LHX", "AXON"],
      },
      {
        id: "prime",
        label: "Prime Contractors",
        sublabel: "Full-platform integrators",
        symbols: ["LMT", "NOC", "RTX", "GD", "HII"],
      },
      {
        id: "services",
        label: "Defence Services & IT",
        sublabel: "Government IT, intelligence, consulting",
        symbols: ["BAH", "SAIC", "LDOS", "CACI"],
      },
      {
        id: "etfs",
        label: "ETFs",
        sublabel: "Broad defence exposure",
        symbols: ["ITA", "XAR"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 4. Space
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "space",
    label: "Space",
    color: "#38bdf8",
    description: "From launch vehicles to satellite connectivity and earth intelligence",
    stages: [
      {
        id: "launch",
        label: "Launch Vehicles",
        sublabel: "Rockets, small launch, reusable",
        symbols: ["RKLB", "MNTS"],
      },
      {
        id: "satellites",
        label: "Satellites",
        sublabel: "Earth observation, data collection",
        symbols: ["PL", "SPIR"],
      },
      {
        id: "connectivity",
        label: "Space Connectivity",
        sublabel: "Space-based broadband, satellite comms",
        symbols: ["ASTS", "VSAT", "IRDM"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 5. GLP-1 & Healthcare
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "glp1_healthcare",
    label: "GLP-1 & Healthcare",
    color: "#ec4899",
    description: "From drug discovery to patient care — and the consumer disruption",
    stages: [
      {
        id: "discovery",
        label: "Drug Discovery",
        sublabel: "Research, genomics, AI drug design",
        symbols: ["EXAS", "ILMN", "RXRX"],
      },
      {
        id: "biotech",
        label: "Biotech / Pharma R&D",
        sublabel: "GLP-1, obesity, next-gen metabolic",
        symbols: ["NVO", "LLY", "VKTX", "AMGN"],
      },
      {
        id: "supply_chain",
        label: "Manufacturing Supply Chain",
        sublabel: "Peptide APIs, fill-finish, lab tools",
        symbols: ["WST", "TMO", "DHR", "BDX"],
      },
      {
        id: "cro_cdmo",
        label: "CRO / CDMO",
        sublabel: "Contract research & manufacturing",
        symbols: ["IQV", "MEDP", "ICLR", "CRL"],
      },
      {
        id: "med_devices",
        label: "Medical Devices",
        sublabel: "CGM, delivery, monitoring",
        symbols: ["DXCM", "ABT", "PODD"],
      },
      {
        id: "distribution",
        label: "Distribution & PBM",
        sublabel: "Drug wholesalers, pharmacy",
        symbols: ["MCK", "CAH", "ABC", "CVS"],
      },
      {
        id: "consumer_impact",
        label: "Consumer Impact",
        sublabel: "Food & beverage under pressure",
        symbols: ["MDLZ", "HSY", "KO", "MCD"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 6. Critical Minerals
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "critical_minerals",
    label: "Critical Minerals",
    color: "#f97316",
    description: "Copper, lithium, rare earths — the materials powering electrification and AI",
    stages: [
      {
        id: "copper",
        label: "Copper Miners",
        sublabel: "Primary copper producers",
        symbols: ["FCX", "SCCO", "TECK"],
      },
      {
        id: "lithium",
        label: "Lithium & Battery Materials",
        sublabel: "Lithium, cobalt, nickel",
        symbols: ["ALB", "SQM", "VALE"],
      },
      {
        id: "rare_earths",
        label: "Rare Earths",
        sublabel: "REE mining and processing",
        symbols: ["MP", "UUUU"],
      },
      {
        id: "processing",
        label: "Processing & Refining",
        sublabel: "Smelting, separation, purification",
        symbols: ["AA", "CENX"],
      },
      {
        id: "minerals_etfs",
        label: "ETFs",
        sublabel: "Copper and critical minerals exposure",
        symbols: ["COPX", "LIT", "REMX"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 7. Gold & Real Assets
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "gold",
    label: "Gold & Real Assets",
    color: "#f59e0b",
    description: "From bullion and royalty streamers to gold miners",
    stages: [
      {
        id: "bullion",
        label: "Bullion & ETFs",
        sublabel: "Physical gold, spot ETFs",
        symbols: ["GLD", "IAU"],
      },
      {
        id: "royalty",
        label: "Royalty & Streaming",
        sublabel: "Low-risk royalty models",
        symbols: ["FNV", "WPM", "RGLD"],
      },
      {
        id: "senior_miners",
        label: "Senior Miners",
        sublabel: "Large-cap gold producers",
        symbols: ["NEM", "GOLD", "AEM"],
      },
      {
        id: "miner_etfs",
        label: "Miner ETFs",
        sublabel: "Broad gold equity exposure",
        symbols: ["GDX", "GDXJ"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 8. Crypto
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "crypto",
    label: "Crypto",
    color: "#a855f7",
    description: "From spot ETFs and corporate treasury to miners and exchanges",
    stages: [
      {
        id: "spot_access",
        label: "Spot BTC Access",
        sublabel: "ETFs for direct bitcoin exposure",
        symbols: ["IBIT", "FBTC", "GBTC"],
      },
      {
        id: "corporate",
        label: "Corporate Treasury",
        sublabel: "Public companies holding BTC",
        symbols: ["MSTR"],
      },
      {
        id: "miners",
        label: "Bitcoin Miners",
        sublabel: "BTC mining operators",
        symbols: ["MARA", "RIOT", "CLSK", "IREN", "HUT", "APLD"],
      },
      {
        id: "exchanges",
        label: "Exchanges & Platforms",
        sublabel: "Spot, derivatives, custody",
        symbols: ["COIN", "HOOD"],
      },
      {
        id: "crypto_etfs",
        label: "Broad ETFs",
        sublabel: "Blockchain ecosystem exposure",
        symbols: ["BLOK"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 9. Reshoring & Industrial
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "reshoring",
    label: "Reshoring & Industrial",
    color: "#84cc16",
    description: "From raw materials to manufactured goods — domestic capex buildout",
    stages: [
      {
        id: "raw_materials",
        label: "Raw Materials",
        sublabel: "Steel, cement, aggregates",
        symbols: ["NUE", "STLD", "RS", "VMC", "MLM"],
      },
      {
        id: "engineering",
        label: "Engineering & Design",
        sublabel: "Architecture, planning, EPC",
        symbols: ["AECOM", "ICF", "KBR", "FLR"],
      },
      {
        id: "construction",
        label: "Construction",
        sublabel: "Industrial construction, electrical",
        symbols: ["PWR", "EME", "PRIM", "MDU"],
      },
      {
        id: "equipment",
        label: "Industrial Equipment",
        sublabel: "Machinery, automation, tools",
        symbols: ["ROK", "HON", "EMR", "PH", "ITW"],
      },
      {
        id: "factory",
        label: "Manufacturing & Fabs",
        sublabel: "Factories, fabs, assembly",
        symbols: ["TSM", "LMT", "CAT", "DE"],
      },
      {
        id: "logistics",
        label: "Logistics",
        sublabel: "Freight, warehousing, ports",
        symbols: ["UPS", "FDX", "XPO", "CHRW"],
      },
      {
        id: "industrial_etfs",
        label: "ETFs",
        sublabel: "Infrastructure and industrial exposure",
        symbols: ["PAVE"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 10. Housing & Rate Sensitivity
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "housing",
    label: "Housing",
    color: "#10b981",
    description: "Homebuilders, building products, and rate-sensitive financials",
    stages: [
      {
        id: "homebuilders",
        label: "Homebuilders",
        sublabel: "Single-family construction",
        symbols: ["DHI", "LEN", "PHM", "NVR", "TOL"],
      },
      {
        id: "building_products",
        label: "Building Products",
        sublabel: "Windows, insulation, roofing",
        symbols: ["BLDR", "OC", "MAS", "TREX"],
      },
      {
        id: "home_improvement",
        label: "Home Improvement",
        sublabel: "Retail and services",
        symbols: ["HD", "LOW", "FND"],
      },
      {
        id: "mortgage",
        label: "Mortgage & Title",
        sublabel: "Origination, servicing, title insurance",
        symbols: ["RKT", "FNF", "UWMC"],
      },
      {
        id: "rate_sensitive",
        label: "Rate-Sensitive Financials",
        sublabel: "Insurers and asset managers with bond exposure",
        symbols: ["PRU", "MET", "ALL"],
      },
      {
        id: "housing_etfs",
        label: "ETFs",
        sublabel: "Broad housing exposure",
        symbols: ["ITB", "XHB"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 11. Water Infrastructure
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "water",
    label: "Water",
    color: "#22d3ee",
    description: "Water utilities, treatment technology, and infrastructure equipment",
    stages: [
      {
        id: "utilities",
        label: "Water Utilities",
        sublabel: "Regulated water and wastewater",
        symbols: ["AWK", "WTRG", "CWT"],
      },
      {
        id: "treatment",
        label: "Treatment & Analytics",
        sublabel: "Purification, testing, chemicals",
        symbols: ["ECL", "ITRI", "HIFS"],
      },
      {
        id: "equipment",
        label: "Pumps & Equipment",
        sublabel: "Pumps, valves, meters, flow control",
        symbols: ["XYL", "PNR", "RXO"],
      },
      {
        id: "water_etfs",
        label: "ETFs",
        sublabel: "Broad water sector exposure",
        symbols: ["PHO", "FIW"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 12. Automotive
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "automotive",
    label: "Automotive",
    color: "#3b82f6",
    description: "From EV makers and legacy OEMs to charging networks and auto tech",
    stages: [
      {
        id: "auto_tech",
        label: "Auto Tech & Software",
        sublabel: "Autonomous driving, vision, ADAS",
        symbols: ["MBLY", "LAZR", "MOBILEYE"],
      },
      {
        id: "ev_makers",
        label: "EV Makers",
        sublabel: "Pure-play electric vehicle manufacturers",
        symbols: ["TSLA", "RIVN", "NIO", "XPEV", "LI"],
      },
      {
        id: "legacy_oem",
        label: "Legacy OEMs",
        sublabel: "Transitioning to EV platforms",
        symbols: ["F", "GM", "STLA", "TM"],
      },
      {
        id: "charging",
        label: "Charging Infrastructure",
        sublabel: "Public EV charging networks",
        symbols: ["CHPT", "BLNK", "EVGO"],
      },
      {
        id: "components",
        label: "Auto Components",
        sublabel: "Tier-1 suppliers, electrical systems",
        symbols: ["APTV", "LEA", "MGA", "NXPI"],
      },
    ],
  },
];

// ── Symbol labels not in market_graph.json ───────────────────────────────────
export const EXTRA_SYMBOL_LABELS: Record<string, string> = {
  // Critical minerals
  FCX: "Freeport-McMoRan",  TECK: "Teck Resources",    MP: "MP Materials",
  ALB: "Albemarle",          SQM: "SQM",                VALE: "Vale",
  SCCO: "Southern Copper",   AA: "Alcoa",               CENX: "Century Aluminium",
  UUUU: "Energy Fuels",      COPX: "Copper Miners ETF", LIT: "Lithium & Battery ETF",
  REMX: "VanEck Rare Earth ETF",

  // AI / power / nuclear
  VRT: "Vertiv",             ETN: "Eaton",              EME: "EMCOR Group",
  PWR: "Quanta Services",    HUBB: "Hubbell",           CCJ: "Cameco",
  BWXT: "BWX Technologies",  GEV: "GE Vernova",         SMR: "NuScale Power",
  URA: "Global X Uranium ETF", URNM: "Sprott Uranium ETF", LEU: "Centrus Energy",
  UEC: "Uranium Energy",     DNN: "Denison Mines",      NLR: "VanEck Uranium ETF",
  APLD: "Applied Digital",   NBIS: "Nebius Group",      IREN: "IREN Ltd",
  EQIX: "Equinix",           DLR: "Digital Realty",

  // Cybersecurity
  PANW: "Palo Alto Networks", CRWD: "CrowdStrike",      FTNT: "Fortinet",
  ZS: "Zscaler",              NET: "Cloudflare",        OKTA: "Okta",
  CYBR: "CyberArk",          VRNS: "Varonis",           TENB: "Tenable",
  CIBR: "First Trust Cyber ETF", HACK: "ETFMG Cyber ETF", BUG: "Global X Cyber ETF",
  S: "SentinelOne",          ZSCL: "Zscaler",

  // Defence
  HXL: "Hexcel",             ATI: "ATI Inc",            MOGA: "Moog",
  CW: "Curtiss-Wright",      HEI: "Heico",              TDY: "Teledyne",
  KTOS: "Kratos Defence",    ONDS: "Ondas Holdings",    AVAV: "AeroVironment",
  LHX: "L3Harris",           AXON: "Axon",              HII: "Huntington Ingalls",
  BAH: "Booz Allen Hamilton", SAIC: "SAIC",              LDOS: "Leidos",
  CACI: "CACI International", ITA: "iShares Defence ETF", XAR: "SPDR Aerospace ETF",
  GD: "General Dynamics",

  // Space
  RKLB: "Rocket Lab",        MNTS: "Momentus",          PL: "Planet Labs",
  SPIR: "Spire Global",      ASTS: "AST SpaceMobile",   VSAT: "Viasat",
  IRDM: "Iridium",

  // GLP-1 / Healthcare
  NVO: "Novo Nordisk",       LLY: "Eli Lilly",          VKTX: "Viking Therapeutics",
  AMGN: "Amgen",             WST: "West Pharmaceutical", TMO: "Thermo Fisher",
  DHR: "Danaher",            BDX: "Becton Dickinson",   IQV: "IQVIA",
  MEDP: "Medpace",           ICLR: "ICON PLC",           CRL: "Charles River",
  DXCM: "Dexcom",            ABT: "Abbott",              PODD: "Insulet",
  MCK: "McKesson",           CAH: "Cardinal Health",    ABC: "AmerisourceBergen",
  CVS: "CVS Health",         MDLZ: "Mondelez",          HSY: "Hershey",
  KO: "Coca-Cola",           MCD: "McDonald's",
  EXAS: "Exact Sciences",    ILMN: "Illumina",          RXRX: "Recursion",
  UNH: "UnitedHealth",

  // Gold
  GLD: "SPDR Gold ETF",      IAU: "iShares Gold ETF",   FNV: "Franco-Nevada",
  WPM: "Wheaton Precious Metals", RGLD: "Royal Gold",   NEM: "Newmont",
  GOLD: "Barrick Gold",      AEM: "Agnico Eagle",       GDX: "VanEck Gold Miners ETF",
  GDXJ: "VanEck Junior Gold ETF",

  // Crypto
  IBIT: "BlackRock Bitcoin ETF", FBTC: "Fidelity Bitcoin ETF", GBTC: "Grayscale Bitcoin Trust",
  MSTR: "MicroStrategy",     MARA: "Marathon Digital",  RIOT: "Riot Platforms",
  CLSK: "CleanSpark",        HUT: "Hut 8",              BLOK: "Amplify Blockchain ETF",
  COIN: "Coinbase",          HOOD: "Robinhood",

  // Reshoring
  NUE: "Nucor",              STLD: "Steel Dynamics",    RS: "Reliance Steel",
  VMC: "Vulcan Materials",   MLM: "Martin Marietta",
  AECOM: "AECOM",            ICF: "ICF International",  KBR: "KBR",
  PRIM: "Primoris Services", FLR: "Fluor",              MDU: "MDU Resources",
  ROK: "Rockwell Automation", HON: "Honeywell",         EMR: "Emerson Electric",
  PH: "Parker Hannifin",     ITW: "Illinois Tool Works",
  CAT: "Caterpillar",        DE: "Deere & Co",
  UPS: "UPS",                FDX: "FedEx",              XPO: "XPO",
  CHRW: "C.H. Robinson",     PAVE: "Global X Infrastructure ETF",

  // Housing
  DHI: "D.R. Horton",        LEN: "Lennar",             PHM: "PulteGroup",
  NVR: "NVR Inc",            TOL: "Toll Brothers",      BLDR: "Builders FirstSource",
  OC: "Owens Corning",       MAS: "Masco",              TREX: "Trex Company",
  HD: "Home Depot",          LOW: "Lowe's",             FND: "Floor & Decor",
  RKT: "Rocket Companies",   FNF: "Fidelity National Financial", UWMC: "UWM Holdings",
  PRU: "Prudential Financial", MET: "MetLife",           ALL: "Allstate",
  ITB: "iShares Home Construction ETF", XHB: "SPDR Homebuilders ETF",

  // Water
  AWK: "American Water Works", WTRG: "Essential Utilities", CWT: "California Water Service",
  ECL: "Ecolab",             ITRI: "Itron",             HIFS: "Hingham Institution for Savings",
  XYL: "Xylem",              PNR: "Pentair",            RXO: "RXO Inc",
  PHO: "Invesco Water ETF",  FIW: "First Trust Water ETF",

  // Automotive
  TSLA: "Tesla",             RIVN: "Rivian",            NIO: "NIO",
  XPEV: "XPeng",             LI: "Li Auto",             F: "Ford",
  GM: "General Motors",      STLA: "Stellantis",        TM: "Toyota",
  CHPT: "ChargePoint",       BLNK: "Blink Charging",    EVGO: "EVgo",
  APTV: "Aptiv",             LEA: "Lear Corporation",   MGA: "Magna International",
  NXPI: "NXP Semiconductors", MBLY: "Mobileye",         LAZR: "Luminar Technologies",

  // Memory
  WDC: "Western Digital",    SNDK: "SanDisk",
};
