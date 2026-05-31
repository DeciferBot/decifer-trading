// Supply chain definitions for each industry theme.
// Symbols repeat across chains — that is correct and intentional.
// Each stage flows left → right from raw input to end consumer.

export interface ChainStage {
  id: string;
  label: string;        // column header
  sublabel?: string;    // optional description
  symbols: string[];
}

export interface Chain {
  id: string;
  label: string;
  color: string;        // accent colour for this chain
  description: string;
  stages: ChainStage[];
}

export const CHAINS: Chain[] = [
  // ─────────────────────────────────────────────────────────────────────────
  // 1. AI Infrastructure
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "ai_infrastructure",
    label: "AI Infrastructure",
    color: "#6366f1",
    description: "From rare earth mining to enterprise software",
    stages: [
      {
        id: "minerals",
        label: "Critical Minerals",
        sublabel: "Copper, rare earth, lithium",
        symbols: ["FCX", "TECK", "MP", "ALB", "SQM", "VALE", "RIO"],
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
        sublabel: "GPUs, CPUs, ASICs, networking silicon",
        symbols: ["NVDA", "AMD", "AVGO", "MRVL"],
      },
      {
        id: "memory",
        label: "Memory",
        sublabel: "DRAM, HBM, NAND storage",
        symbols: ["MU"],
      },
      {
        id: "photonics",
        label: "Photonics & Optical",
        sublabel: "Optical transceivers, DSP, fibre",
        symbols: ["CIEN", "COHR", "LITE"],
      },
      {
        id: "networking",
        label: "Networking",
        sublabel: "Switches, routers, hyperscale fabric",
        symbols: ["ANET"],
      },
      {
        id: "power_cooling",
        label: "Power & Cooling",
        sublabel: "UPS, thermal, generation",
        symbols: ["VRT", "ETN", "CEG", "NRG", "VST", "EME", "PWR"],
      },
      {
        id: "data_centre",
        label: "Data Centre",
        sublabel: "Servers, colos, GPU clouds",
        symbols: ["SMCI", "DELL", "HPE", "EQIX", "DLR", "NBIS", "IREN"],
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
        sublabel: "SaaS, security, analytics",
        symbols: ["CRM", "NOW", "PLTR", "CRWD", "PANW"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 2. Defence & Aerospace
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "defence",
    label: "Defence & Aerospace",
    color: "#ef4444",
    description: "From aerospace alloys to space-based intelligence",
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
        id: "subsystems",
        label: "Subsystems",
        sublabel: "Autonomy, weapons, C2",
        symbols: ["AXON", "KTOS", "LHX"],
      },
      {
        id: "prime",
        label: "Prime Contractors",
        sublabel: "Full-platform integrators",
        symbols: ["LMT", "NOC", "RTX"],
      },
      {
        id: "space_launch",
        label: "Space & Launch",
        sublabel: "Rockets, satellites",
        symbols: ["RKLB", "MNTS", "PL", "SPIR"],
      },
      {
        id: "comms_isr",
        label: "Comms & ISR",
        sublabel: "Satellite comms, earth obs",
        symbols: ["VSAT", "IRDM"],
      },
      {
        id: "government",
        label: "Government / Operators",
        sublabel: "NATO, DoD, allied forces",
        symbols: [],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 3. GLP-1 / Healthcare
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "glp1_healthcare",
    label: "GLP-1 & Healthcare",
    color: "#ec4899",
    description: "From drug discovery to patient care",
    stages: [
      {
        id: "discovery",
        label: "Drug Discovery",
        sublabel: "Research, genomics, AI drug design",
        symbols: ["EXAS", "ILMN", "RXRX", "SEER"],
      },
      {
        id: "biotech",
        label: "Biotech / Pharma R&D",
        sublabel: "GLP-1, obesity, metabolic",
        symbols: ["NVO", "LLY", "VKTX", "AMGN"],
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
        sublabel: "Delivery, monitoring",
        symbols: ["DXCM", "ABT", "PHR", "PODD"],
      },
      {
        id: "distribution",
        label: "Distribution & PBM",
        sublabel: "Drug wholesalers, pharmacy",
        symbols: ["MCK", "CAH", "ABC", "CVS"],
      },
      {
        id: "care",
        label: "Care Delivery",
        sublabel: "Hospitals, clinics, telehealth",
        symbols: ["HCA", "THC", "UNH", "TDOC"],
      },
      {
        id: "patient",
        label: "Patient / Consumer",
        sublabel: "End payers and users",
        symbols: [],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 4. Critical Minerals & Clean Energy
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "critical_minerals",
    label: "Critical Minerals & Energy",
    color: "#f97316",
    description: "From copper mines to clean grid and EVs",
    stages: [
      {
        id: "mining",
        label: "Mining",
        sublabel: "Copper, lithium, nickel, cobalt",
        symbols: ["FCX", "TECK", "VALE", "RIO", "MP", "ALB", "SQM", "LUNR"],
      },
      {
        id: "processing",
        label: "Processing & Refining",
        sublabel: "Smelting, separation, purification",
        symbols: ["AA", "CENX", "UUUU"],
      },
      {
        id: "battery",
        label: "Battery & Storage",
        sublabel: "Cells, packs, grid storage",
        symbols: ["FREYR", "QS", "NKLA", "FLUX"],
      },
      {
        id: "power_gen",
        label: "Power Generation",
        sublabel: "Solar, nuclear, natural gas",
        symbols: ["CEG", "VST", "NRG", "FSLR", "ENPH", "BEP"],
      },
      {
        id: "grid",
        label: "Grid & Transmission",
        sublabel: "Transformers, cables, HVDC",
        symbols: ["ETN", "VRT", "EME", "PWR", "PRIM"],
      },
      {
        id: "ev_tech",
        label: "EV & Electrification",
        sublabel: "Vehicles, charging, motors",
        symbols: ["TSLA", "RIVN", "CHPT", "BLNK", "EVGO"],
      },
      {
        id: "consumer_energy",
        label: "End Consumer",
        sublabel: "Utilities, industrial, retail",
        symbols: ["NEE", "DUK", "SO"],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 5. Digital Assets Infrastructure
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "digital_assets",
    label: "Digital Assets",
    color: "#a855f7",
    description: "From energy and hardware to financial rails",
    stages: [
      {
        id: "energy",
        label: "Energy",
        sublabel: "Power for mining operations",
        symbols: ["CEG", "NRG", "VST", "IREN", "CLSK"],
      },
      {
        id: "mining_hw",
        label: "Mining Hardware",
        sublabel: "ASICs, rigs, cooling",
        symbols: ["NVDA", "AMD", "SMCI"],
      },
      {
        id: "miners",
        label: "Miners",
        sublabel: "BTC, ETH mining operators",
        symbols: ["MARA", "RIOT", "CLSK", "IREN", "HUT"],
      },
      {
        id: "custody",
        label: "Custody & Infrastructure",
        sublabel: "Wallets, nodes, security",
        symbols: ["COIN", "HOOD", "MSTR", "IBIT"],
      },
      {
        id: "exchanges",
        label: "Exchanges & Trading",
        sublabel: "Spot, derivatives, OTC",
        symbols: ["COIN", "HOOD", "MSTR"],
      },
      {
        id: "financial_rails",
        label: "Financial Rails",
        sublabel: "Payments, DeFi, stablecoins",
        symbols: ["PYPL", "V", "MA", "NXPI"],
      },
      {
        id: "consumer_fin",
        label: "End User",
        sublabel: "Retail holders, institutions",
        symbols: [],
      },
    ],
  },

  // ─────────────────────────────────────────────────────────────────────────
  // 6. Reshoring & Industrial Capex
  // ─────────────────────────────────────────────────────────────────────────
  {
    id: "reshoring",
    label: "Reshoring & Industrial",
    color: "#84cc16",
    description: "From raw steel and concrete to manufactured goods",
    stages: [
      {
        id: "raw_materials",
        label: "Raw Materials",
        sublabel: "Steel, cement, chemicals",
        symbols: ["NUE", "STLD", "RS", "VMC", "MLM"],
      },
      {
        id: "engineering",
        label: "Engineering & Design",
        sublabel: "Architecture, planning",
        symbols: ["AECOM", "ICF", "KBR"],
      },
      {
        id: "construction",
        label: "Construction & EPC",
        sublabel: "Industrial construction, EPC",
        symbols: ["PWR", "EME", "PRIM", "FLR", "MDU"],
      },
      {
        id: "equipment",
        label: "Industrial Equipment",
        sublabel: "Machinery, automation, tools",
        symbols: ["ROK", "HON", "EMR", "PH", "ITW"],
      },
      {
        id: "factory",
        label: "Manufacturing",
        sublabel: "Factories, fabs, assembly",
        symbols: ["TSM", "NVDA", "LMT", "CAT", "DE"],
      },
      {
        id: "logistics",
        label: "Logistics & Distribution",
        sublabel: "Freight, warehousing, ports",
        symbols: ["UPS", "FDX", "XPO", "CHRW"],
      },
      {
        id: "consumer_goods",
        label: "End Consumer",
        sublabel: "Retail, industrial buyers",
        symbols: [],
      },
    ],
  },
];

// ── Symbol metadata not in market_graph.json ────────────────────────────────
// Additional symbols referenced in chains that need labels for display
export const EXTRA_SYMBOL_LABELS: Record<string, string> = {
  FCX: "Freeport-McMoRan",  TECK: "Teck Resources",   MP: "MP Materials",
  ALB: "Albemarle",         SQM: "SQM",                VALE: "Vale",
  RIO: "Rio Tinto",         NVO: "Novo Nordisk",        LLY: "Eli Lilly",
  VKTX: "Viking Therapeutics", AMGN: "Amgen",          IQV: "IQVIA",
  MEDP: "Medpace",          ICLR: "ICON PLC",           CRL: "Charles River",
  DXCM: "Dexcom",           ABT: "Abbott",              PHR: "Phreesia",
  PODD: "Insulet",          MCK: "McKesson",             CAH: "Cardinal Health",
  ABC: "AmerisourceBergen", CVS: "CVS Health",          HCA: "HCA Healthcare",
  THC: "Tenet Healthcare",  UNH: "UnitedHealth",         TDOC: "Teladoc",
  EXAS: "Exact Sciences",   ILMN: "Illumina",            RXRX: "Recursion",
  SEER: "Seer Bio",         AA: "Alcoa",                 CENX: "Century Aluminium",
  UUUU: "Energy Fuels",     FSLR: "First Solar",         ENPH: "Enphase",
  BEP: "Brookfield Renewable", TSLA: "Tesla",           RIVN: "Rivian",
  CHPT: "ChargePoint",      BLNK: "Blink Charging",      EVGO: "EVgo",
  NEE: "NextEra Energy",    DUK: "Duke Energy",           SO: "Southern Company",
  FREYR: "FREYR Battery",   QS: "QuantumScape",           NKLA: "Nikola",
  FLUX: "Flux Power",       LUNR: "Intuitive Machines",
  MARA: "Marathon Digital",  RIOT: "Riot Platforms",      CLSK: "CleanSpark",
  HUT: "Hut 8",             COIN: "Coinbase",             HOOD: "Robinhood",
  MSTR: "MicroStrategy",    IBIT: "BlackRock Bitcoin ETF", PYPL: "PayPal",
  V: "Visa",                 MA: "Mastercard",             NXPI: "NXP Semi",
  NUE: "Nucor",              STLD: "Steel Dynamics",       RS: "Reliance Steel",
  VMC: "Vulcan Materials",   MLM: "Martin Marietta",
  AECOM: "AECOM",            ICF: "ICF International",     KBR: "KBR",
  PRIM: "Primoris Services", FLR: "Fluor",                 MDU: "MDU Resources",
  ROK: "Rockwell Automation",HON: "Honeywell",             EMR: "Emerson Electric",
  PH: "Parker Hannifin",    ITW: "Illinois Tool Works",
  CAT: "Caterpillar",        DE: "Deere & Co",
  UPS: "UPS",                FDX: "FedEx",                 XPO: "XPO",
  CHRW: "C.H. Robinson",
};
