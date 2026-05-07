"""
Sprint 7A.1 — Reference Data Layer Builder

Reads all approved local sources (no external APIs, no .env, no LLM calls) and
writes four static reference files:

  data/reference/sector_schema.json
  data/reference/symbol_master.json
  data/reference/theme_overlay_map.json
  data/intelligence/coverage_gap_review.json

Run:
  python3 reference_data_builder.py

Safety invariants:
  favourites_used_as_discovery = false
  live_api_called = false
  llm_called = false
  env_inspected = false
  production_decision_changed = false
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "data")
_INTEL = os.path.join(_DATA, "intelligence")
_REF = os.path.join(_DATA, "reference")
_UB = os.path.join(_DATA, "universe_builder")

# ---------------------------------------------------------------------------
# Static sector classification — covers all thematic roster symbols, top
# committed universe names, shadow universe, advisory report symbols, and
# position research universe members known at Sprint 7A.1 build time.
# Remaining symbols fall back to "unknown_requires_provider_enrichment".
# ---------------------------------------------------------------------------
_SECTOR_MAP: dict[str, dict[str, str]] = {
    # ---- Information Technology ----
    "AAPL":  {"sector": "information_technology", "industry": "technology_hardware"},
    "MSFT":  {"sector": "information_technology", "industry": "software"},
    "NVDA":  {"sector": "information_technology", "industry": "semiconductors"},
    "AMD":   {"sector": "information_technology", "industry": "semiconductors"},
    "INTC":  {"sector": "information_technology", "industry": "semiconductors"},
    "AVGO":  {"sector": "information_technology", "industry": "semiconductors"},
    "QCOM":  {"sector": "information_technology", "industry": "semiconductors"},
    "MRVL":  {"sector": "information_technology", "industry": "semiconductors"},
    "KLAC":  {"sector": "information_technology", "industry": "semiconductor_equipment"},
    "LRCX":  {"sector": "information_technology", "industry": "semiconductor_equipment"},
    "AMAT":  {"sector": "information_technology", "industry": "semiconductor_equipment"},
    "ASML":  {"sector": "information_technology", "industry": "semiconductor_equipment"},
    "TER":   {"sector": "information_technology", "industry": "semiconductor_equipment"},
    "TSM":   {"sector": "information_technology", "industry": "semiconductors"},
    "MU":    {"sector": "information_technology", "industry": "semiconductors"},
    "SNDK":  {"sector": "information_technology", "industry": "semiconductors"},
    "WDC":   {"sector": "information_technology", "industry": "technology_hardware"},
    "STX":   {"sector": "information_technology", "industry": "technology_hardware"},
    "DELL":  {"sector": "information_technology", "industry": "technology_hardware"},
    "HPQ":   {"sector": "information_technology", "industry": "technology_hardware"},
    "SMCI":  {"sector": "information_technology", "industry": "technology_hardware"},
    "ORCL":  {"sector": "information_technology", "industry": "software"},
    "CRM":   {"sector": "information_technology", "industry": "software"},
    "NOW":   {"sector": "information_technology", "industry": "software"},
    "INTU":  {"sector": "information_technology", "industry": "software"},
    "ADBE":  {"sector": "information_technology", "industry": "software"},
    "SNPS":  {"sector": "information_technology", "industry": "software"},
    "CDNS":  {"sector": "information_technology", "industry": "software"},
    "PANW":  {"sector": "information_technology", "industry": "cybersecurity"},
    "CRWD":  {"sector": "information_technology", "industry": "cybersecurity"},
    "NET":   {"sector": "information_technology", "industry": "cybersecurity"},
    "FTNT":  {"sector": "information_technology", "industry": "cybersecurity"},
    "S":     {"sector": "information_technology", "industry": "cybersecurity"},
    "ANET":  {"sector": "information_technology", "industry": "networking"},
    "CSCO":  {"sector": "information_technology", "industry": "networking"},
    "GLW":   {"sector": "information_technology", "industry": "networking"},
    "CIEN":  {"sector": "information_technology", "industry": "networking"},
    "COHR":  {"sector": "information_technology", "industry": "networking"},
    "LITE":  {"sector": "information_technology", "industry": "networking"},
    "AAOI":  {"sector": "information_technology", "industry": "networking"},
    "APH":   {"sector": "information_technology", "industry": "electronic_components"},
    "CLS":   {"sector": "information_technology", "industry": "electronic_components"},
    "PLTR":  {"sector": "information_technology", "industry": "software"},
    "APP":   {"sector": "information_technology", "industry": "software"},
    "TEAM":  {"sector": "information_technology", "industry": "software"},
    "SNOW":  {"sector": "information_technology", "industry": "software"},
    "MDB":   {"sector": "information_technology", "industry": "software"},
    "ACN":   {"sector": "information_technology", "industry": "it_services"},
    "IBM":   {"sector": "information_technology", "industry": "it_services"},
    "CTSH":  {"sector": "information_technology", "industry": "it_services"},
    "ADI":   {"sector": "information_technology", "industry": "semiconductors"},
    "TXN":   {"sector": "information_technology", "industry": "semiconductors"},
    "ALAB":  {"sector": "information_technology", "industry": "semiconductors"},
    "CRDO":  {"sector": "information_technology", "industry": "semiconductors"},
    "NBIS":  {"sector": "information_technology", "industry": "semiconductors"},
    "IONQ":  {"sector": "information_technology", "industry": "quantum_computing"},
    "QBTS":  {"sector": "information_technology", "industry": "quantum_computing"},
    "RGTI":  {"sector": "information_technology", "industry": "quantum_computing"},
    "OKLO":  {"sector": "information_technology", "industry": "advanced_nuclear"},
    "BE":    {"sector": "information_technology", "industry": "clean_energy_tech"},
    "APLD":  {"sector": "information_technology", "industry": "ai_infrastructure"},
    "CRWV":  {"sector": "information_technology", "industry": "ai_infrastructure"},
    "IREN":  {"sector": "information_technology", "industry": "ai_infrastructure"},
    "WULF":  {"sector": "information_technology", "industry": "ai_infrastructure"},
    # ---- Communication Services ----
    "GOOGL": {"sector": "communication_services", "industry": "internet_search"},
    "GOOG":  {"sector": "communication_services", "industry": "internet_search"},
    "META":  {"sector": "communication_services", "industry": "social_media"},
    "SNAP":  {"sector": "communication_services", "industry": "social_media"},
    "NFLX":  {"sector": "communication_services", "industry": "streaming"},
    "SPOT":  {"sector": "communication_services", "industry": "streaming"},
    "DIS":   {"sector": "communication_services", "industry": "media_entertainment"},
    "WBD":   {"sector": "communication_services", "industry": "media_entertainment"},
    "LYV":   {"sector": "communication_services", "industry": "live_entertainment"},
    "VZ":    {"sector": "communication_services", "industry": "telecom"},
    "T":     {"sector": "communication_services", "industry": "telecom"},
    "TMUS":  {"sector": "communication_services", "industry": "telecom"},
    "NOK":   {"sector": "communication_services", "industry": "telecom_equipment"},
    # ---- Consumer Discretionary ----
    "AMZN":  {"sector": "consumer_discretionary", "industry": "ecommerce"},
    "TSLA":  {"sector": "consumer_discretionary", "industry": "automotive_ev"},
    "HOOD":  {"sector": "consumer_discretionary", "industry": "fintech_retail"},
    "BABA":  {"sector": "consumer_discretionary", "industry": "ecommerce"},
    "MELI":  {"sector": "consumer_discretionary", "industry": "ecommerce"},
    "SHOP":  {"sector": "consumer_discretionary", "industry": "ecommerce"},
    "BKNG":  {"sector": "consumer_discretionary", "industry": "travel_online"},
    "CVNA":  {"sector": "consumer_discretionary", "industry": "automotive_retail"},
    "CAR":   {"sector": "consumer_discretionary", "industry": "automotive_rental"},
    "NKE":   {"sector": "consumer_discretionary", "industry": "apparel_footwear"},
    "HD":    {"sector": "consumer_discretionary", "industry": "home_improvement"},
    "TSLL":  {"sector": "consumer_discretionary", "industry": "automotive_ev"},
    "HIMS":  {"sector": "consumer_discretionary", "industry": "digital_health"},
    "BIRD":  {"sector": "consumer_discretionary", "industry": "apparel_footwear"},
    "RBLX":  {"sector": "consumer_discretionary", "industry": "gaming"},
    "TTWO":  {"sector": "consumer_discretionary", "industry": "gaming"},
    "AZO":   {"sector": "consumer_discretionary", "industry": "auto_parts_retail"},
    "DASH":  {"sector": "consumer_discretionary", "industry": "food_delivery"},
    "UBER":  {"sector": "consumer_discretionary", "industry": "rideshare"},
    "LCID":  {"sector": "consumer_discretionary", "industry": "automotive_ev"},
    "MCD":   {"sector": "consumer_discretionary", "industry": "restaurants"},
    "ORLY":  {"sector": "consumer_discretionary", "industry": "auto_parts_retail"},
    "WMT":   {"sector": "consumer_staples", "industry": "discount_retail"},
    "DEO":   {"sector": "consumer_staples", "industry": "beverages_spirits"},
    "DKS":   {"sector": "consumer_discretionary", "industry": "sporting_goods_retail"},
    "CRCL":  {"sector": "consumer_discretionary", "industry": "ecommerce"},
    # ---- Consumer Staples ----
    "KO":    {"sector": "consumer_staples", "industry": "beverages"},
    "PEP":   {"sector": "consumer_staples", "industry": "beverages"},
    "COST":  {"sector": "consumer_staples", "industry": "discount_retail"},
    "PG":    {"sector": "consumer_staples", "industry": "household_products"},
    "CL":    {"sector": "consumer_staples", "industry": "household_products"},
    "KMB":   {"sector": "consumer_staples", "industry": "household_products"},
    # ---- Health Care ----
    "LLY":   {"sector": "health_care", "industry": "pharma"},
    "JNJ":   {"sector": "health_care", "industry": "pharma"},
    "ABT":   {"sector": "health_care", "industry": "medical_devices"},
    "BSX":   {"sector": "health_care", "industry": "medical_devices"},
    "MDT":   {"sector": "health_care", "industry": "medical_devices"},
    "ISRG":  {"sector": "health_care", "industry": "medical_devices"},
    "TMO":   {"sector": "health_care", "industry": "life_sciences_tools"},
    "MRK":   {"sector": "health_care", "industry": "pharma"},
    "ABBV":  {"sector": "health_care", "industry": "pharma"},
    "PFE":   {"sector": "health_care", "industry": "pharma"},
    "AMGN":  {"sector": "health_care", "industry": "biotech"},
    "UNH":   {"sector": "health_care", "industry": "health_insurance"},
    "NVO":   {"sector": "health_care", "industry": "pharma"},
    "PODD":  {"sector": "health_care", "industry": "medical_devices"},
    "RVMD":  {"sector": "health_care", "industry": "biotech"},
    "TVTX":  {"sector": "health_care", "industry": "biotech"},
    "SLNO":  {"sector": "health_care", "industry": "biotech"},
    "PRAX":  {"sector": "health_care", "industry": "biotech"},
    "ARGX":  {"sector": "health_care", "industry": "biotech"},
    "ALNY":  {"sector": "health_care", "industry": "biotech"},
    "GEHC":  {"sector": "health_care", "industry": "medical_devices"},
    "EFX":   {"sector": "health_care", "industry": "data_analytics"},
    "MSCI":  {"sector": "financials", "industry": "financial_data"},
    "HUM":   {"sector": "health_care", "industry": "health_insurance"},
    "EW":    {"sector": "health_care", "industry": "medical_devices"},
    # ---- Financials ----
    "JPM":   {"sector": "financials", "industry": "banks_diversified"},
    "BAC":   {"sector": "financials", "industry": "banks_diversified"},
    "WFC":   {"sector": "financials", "industry": "banks_diversified"},
    "GS":    {"sector": "financials", "industry": "investment_banking"},
    "MS":    {"sector": "financials", "industry": "investment_banking"},
    "C":     {"sector": "financials", "industry": "banks_diversified"},
    "SCHW":  {"sector": "financials", "industry": "brokerage"},
    "BX":    {"sector": "financials", "industry": "asset_management"},
    "BLK":   {"sector": "financials", "industry": "asset_management"},
    "APO":   {"sector": "financials", "industry": "asset_management"},
    "AXP":   {"sector": "financials", "industry": "payments"},
    "V":     {"sector": "financials", "industry": "payments"},
    "MA":    {"sector": "financials", "industry": "payments"},
    "COF":   {"sector": "financials", "industry": "consumer_finance"},
    "SOFI":  {"sector": "financials", "industry": "fintech"},
    "COIN":  {"sector": "financials", "industry": "crypto_exchange"},
    "MSTR":  {"sector": "financials", "industry": "crypto_holding"},
    "PGR":   {"sector": "financials", "industry": "insurance"},
    "SPGI":  {"sector": "financials", "industry": "financial_data"},
    "MCO":   {"sector": "financials", "industry": "financial_data"},
    "HBAN":  {"sector": "financials", "industry": "banks_regional"},
    "TFC":   {"sector": "financials", "industry": "banks_regional"},
    "FITB":  {"sector": "financials", "industry": "banks_regional"},
    "MTB":   {"sector": "financials", "industry": "banks_regional"},
    "CFG":   {"sector": "financials", "industry": "banks_regional"},
    "AON":   {"sector": "financials", "industry": "insurance"},
    "BAP":   {"sector": "financials", "industry": "banks_diversified"},
    "LPLA":  {"sector": "financials", "industry": "brokerage"},
    "AMP":   {"sector": "financials", "industry": "asset_management"},
    "TPG":   {"sector": "financials", "industry": "asset_management"},
    "CG":    {"sector": "financials", "industry": "asset_management"},
    "EQH":   {"sector": "financials", "industry": "insurance"},
    "CRBG":  {"sector": "financials", "industry": "insurance"},
    "FIG":   {"sector": "financials", "industry": "asset_management"},
    "GPN":   {"sector": "financials", "industry": "payments"},
    "FISV":  {"sector": "financials", "industry": "payments"},
    "TOST":  {"sector": "financials", "industry": "fintech"},
    "SYF":   {"sector": "financials", "industry": "consumer_finance"},
    "AFRM":  {"sector": "financials", "industry": "fintech"},
    "PNC":   {"sector": "financials", "industry": "banks_diversified"},
    "BR":    {"sector": "financials", "industry": "financial_services"},
    "WIX":   {"sector": "information_technology", "industry": "software"},
    "MNDY":  {"sector": "information_technology", "industry": "software"},
    "SE":    {"sector": "consumer_discretionary", "industry": "ecommerce"},
    "JD":    {"sector": "consumer_discretionary", "industry": "ecommerce"},
    "BIDU":  {"sector": "communication_services", "industry": "internet_search"},
    "FUTU":  {"sector": "financials", "industry": "brokerage"},
    # ---- Industrials ----
    "GE":    {"sector": "industrials", "industry": "aerospace_defense"},
    "GEV":   {"sector": "industrials", "industry": "power_equipment"},
    "ETN":   {"sector": "industrials", "industry": "electrical_equipment"},
    "PWR":   {"sector": "industrials", "industry": "construction_engineering"},
    "BA":    {"sector": "industrials", "industry": "aerospace_defense"},
    "LMT":   {"sector": "industrials", "industry": "aerospace_defense"},
    "NOC":   {"sector": "industrials", "industry": "aerospace_defense"},
    "RTX":   {"sector": "industrials", "industry": "aerospace_defense"},
    "GD":    {"sector": "industrials", "industry": "aerospace_defense"},
    "HWM":   {"sector": "industrials", "industry": "aerospace_components"},
    "CAT":   {"sector": "industrials", "industry": "construction_machinery"},
    "DE":    {"sector": "industrials", "industry": "agricultural_machinery"},
    "HON":   {"sector": "industrials", "industry": "industrial_conglomerate"},
    "UNP":   {"sector": "industrials", "industry": "railroads"},
    "DAL":   {"sector": "industrials", "industry": "airlines"},
    "UAL":   {"sector": "industrials", "industry": "airlines"},
    "CARR":  {"sector": "industrials", "industry": "hvac"},
    "OTIS":  {"sector": "industrials", "industry": "elevators"},
    "ADP":   {"sector": "industrials", "industry": "hr_services"},
    "ITW":   {"sector": "industrials", "industry": "industrial_conglomerate"},
    "XPO":   {"sector": "industrials", "industry": "logistics"},
    "VRSK":  {"sector": "industrials", "industry": "data_analytics"},
    "STE":   {"sector": "health_care", "industry": "medical_equipment_services"},
    "AXON":  {"sector": "industrials", "industry": "public_safety_tech"},
    "SYM":   {"sector": "industrials", "industry": "automation_robotics"},
    "RKLB":  {"sector": "industrials", "industry": "space_launch"},
    "JOBY":  {"sector": "industrials", "industry": "evtol_aviation"},
    "ASTS":  {"sector": "communication_services", "industry": "satellite"},
    "SATS":  {"sector": "communication_services", "industry": "satellite"},
    "STRL":  {"sector": "industrials", "industry": "construction_engineering"},
    "CACI":  {"sector": "industrials", "industry": "government_it_services"},
    "TYL":   {"sector": "industrials", "industry": "government_software"},
    "VLTO":  {"sector": "industrials", "industry": "measurement_instruments"},
    "MTD":   {"sector": "industrials", "industry": "measurement_instruments"},
    "AME":   {"sector": "industrials", "industry": "electronic_instruments"},
    "CW":    {"sector": "industrials", "industry": "aerospace_components"},
    "TDY":   {"sector": "industrials", "industry": "aerospace_electronics"},
    "ACGL":  {"sector": "financials", "industry": "insurance_reinsurance"},
    "FLY":   {"sector": "industrials", "industry": "aircraft_leasing"},
    "GPN":   {"sector": "financials", "industry": "payments"},
    "DOO":   {"sector": "consumer_discretionary", "industry": "recreational_vehicles"},
    "KEYS":  {"sector": "information_technology", "industry": "measurement_instruments"},
    "ONTO":  {"sector": "information_technology", "industry": "semiconductor_equipment"},
    "FN":    {"sector": "information_technology", "industry": "optical_components"},
    "ZBRA":  {"sector": "information_technology", "industry": "enterprise_hardware"},
    "AYI":   {"sector": "industrials", "industry": "lighting"},
    "XYL":   {"sector": "industrials", "industry": "water_equipment"},
    "BC":    {"sector": "consumer_discretionary", "industry": "recreational_vehicles"},
    "SNA":   {"sector": "industrials", "industry": "tools"},
    "ALGN":  {"sector": "health_care", "industry": "medical_devices"},
    "ROL":   {"sector": "industrials", "industry": "pest_control"},
    "CLH":   {"sector": "industrials", "industry": "environmental_services"},
    "GRMN":  {"sector": "consumer_discretionary", "industry": "consumer_electronics"},
    "MORN":  {"sector": "financials", "industry": "financial_data"},
    "SSNC":  {"sector": "financials", "industry": "financial_software"},
    "A":     {"sector": "health_care", "industry": "life_sciences_tools"},
    # ---- Energy ----
    "XOM":   {"sector": "energy", "industry": "integrated_oil_gas"},
    "CVX":   {"sector": "energy", "industry": "integrated_oil_gas"},
    "COP":   {"sector": "energy", "industry": "oil_gas_ep"},
    "OXY":   {"sector": "energy", "industry": "oil_gas_ep"},
    "SLB":   {"sector": "energy", "industry": "oilfield_services"},
    "FCX":   {"sector": "materials", "industry": "copper_mining"},
    "ET":    {"sector": "energy", "industry": "oil_gas_midstream"},
    "TLN":   {"sector": "energy", "industry": "power_generation"},
    "CEG":   {"sector": "utilities", "industry": "nuclear_power"},
    "NEE":   {"sector": "utilities", "industry": "renewable_electric"},
    "NI":    {"sector": "utilities", "industry": "electric_gas_utility"},
    "XEL":   {"sector": "utilities", "industry": "electric_gas_utility"},
    "EQR":   {"sector": "real_estate", "industry": "residential_reit"},
    "UDR":   {"sector": "real_estate", "industry": "residential_reit"},
    "DLR":   {"sector": "real_estate", "industry": "data_centre_reit"},
    "PSA":   {"sector": "real_estate", "industry": "storage_reit"},
    "SPG":   {"sector": "real_estate", "industry": "retail_reit"},
    "CCI":   {"sector": "real_estate", "industry": "cell_tower_reit"},
    "HST":   {"sector": "real_estate", "industry": "hotel_reit"},
    # ---- Materials ----
    "NEM":   {"sector": "materials", "industry": "gold_mining"},
    "GDX":   {"sector": "materials", "industry": "gold_mining"},
    "AEM":   {"sector": "materials", "industry": "gold_mining"},
    "RGLD":  {"sector": "materials", "industry": "gold_royalty"},
    "PAAS":  {"sector": "materials", "industry": "silver_mining"},
    "CDE":   {"sector": "materials", "industry": "silver_mining"},
    "HL":    {"sector": "materials", "industry": "silver_mining"},
    "LIN":   {"sector": "materials", "industry": "industrial_gases"},
    "PPG":   {"sector": "materials", "industry": "specialty_chemicals"},
    "CTVA":  {"sector": "materials", "industry": "agricultural_chemicals"},
    "UAMY":  {"sector": "materials", "industry": "antimony_mining"},
    "UUUU":  {"sector": "materials", "industry": "uranium_mining"},
    "LEU":   {"sector": "materials", "industry": "uranium_enrichment"},
    "STLD":  {"sector": "materials", "industry": "steel"},
    "B":     {"sector": "materials", "industry": "industrial_metals"},
    "SN":    {"sector": "materials", "industry": "specialty_materials"},
    # ---- ETF Proxies — Technology/Growth ----
    "SMH":   {"sector": "etf_proxy", "industry": "semiconductor_etf"},
    "SOXX":  {"sector": "etf_proxy", "industry": "semiconductor_etf"},
    "IGV":   {"sector": "etf_proxy", "industry": "software_etf"},
    "XLK":   {"sector": "etf_proxy", "industry": "technology_sector_etf"},
    "SOXL":  {"sector": "etf_proxy", "industry": "leveraged_semiconductor_etf"},
    "SOXS":  {"sector": "etf_proxy", "industry": "inverse_semiconductor_etf"},
    "TQQQ":  {"sector": "etf_proxy", "industry": "leveraged_nasdaq_etf"},
    "SQQQ":  {"sector": "etf_proxy", "industry": "inverse_nasdaq_etf"},
    "NVDL":  {"sector": "etf_proxy", "industry": "leveraged_single_stock_etf"},
    "TSLL":  {"sector": "etf_proxy", "industry": "leveraged_single_stock_etf"},
    "BITO":  {"sector": "etf_proxy", "industry": "crypto_futures_etf"},
    "IBIT":  {"sector": "etf_proxy", "industry": "spot_bitcoin_etf"},
    "XBI":   {"sector": "etf_proxy", "industry": "biotech_etf"},
    # ---- ETF Proxies — Broad Market / Multi-Sector ----
    "SPY":   {"sector": "etf_proxy", "industry": "sp500_etf"},
    "QQQ":   {"sector": "etf_proxy", "industry": "nasdaq100_etf"},
    "QQQM":  {"sector": "etf_proxy", "industry": "nasdaq100_etf"},
    "IWM":   {"sector": "etf_proxy", "industry": "small_cap_etf"},
    "DIA":   {"sector": "etf_proxy", "industry": "dow_jones_etf"},
    "VOO":   {"sector": "etf_proxy", "industry": "sp500_etf"},
    "VTI":   {"sector": "etf_proxy", "industry": "total_market_etf"},
    "RSP":   {"sector": "etf_proxy", "industry": "equal_weight_sp500_etf"},
    "IVV":   {"sector": "etf_proxy", "industry": "sp500_etf"},
    "EEM":   {"sector": "etf_proxy", "industry": "emerging_markets_etf"},
    "EFA":   {"sector": "etf_proxy", "industry": "international_developed_etf"},
    "EWY":   {"sector": "etf_proxy", "industry": "south_korea_etf"},
    "EWZ":   {"sector": "etf_proxy", "industry": "brazil_etf"},
    "FXI":   {"sector": "etf_proxy", "industry": "china_etf"},
    "KRE":   {"sector": "etf_proxy", "industry": "regional_bank_etf"},
    # ---- ETF Proxies — Sector ----
    "XLU":   {"sector": "etf_proxy", "industry": "utilities_sector_etf"},
    "XLF":   {"sector": "etf_proxy", "industry": "financials_sector_etf"},
    "XLE":   {"sector": "etf_proxy", "industry": "energy_sector_etf"},
    "XLV":   {"sector": "etf_proxy", "industry": "healthcare_sector_etf"},
    "XLI":   {"sector": "etf_proxy", "industry": "industrials_sector_etf"},
    "XLB":   {"sector": "etf_proxy", "industry": "materials_sector_etf"},
    "XLY":   {"sector": "etf_proxy", "industry": "consumer_discretionary_etf"},
    "XLP":   {"sector": "etf_proxy", "industry": "consumer_staples_sector_etf"},
    "ITA":   {"sector": "etf_proxy", "industry": "aerospace_defense_etf"},
    "QUAL":  {"sector": "etf_proxy", "industry": "quality_factor_etf"},
    "SPLV":  {"sector": "etf_proxy", "industry": "low_volatility_etf"},
    # ---- ETF Proxies — Fixed Income / Macro ----
    "TLT":   {"sector": "etf_proxy", "industry": "long_treasury_etf"},
    "HYG":   {"sector": "etf_proxy", "industry": "high_yield_bond_etf"},
    "LQD":   {"sector": "etf_proxy", "industry": "investment_grade_bond_etf"},
    "SGOV":  {"sector": "etf_proxy", "industry": "short_treasury_etf"},
    "BIL":   {"sector": "etf_proxy", "industry": "tbill_etf"},
    "VCLT":  {"sector": "etf_proxy", "industry": "long_corp_bond_etf"},
    "VCIT":  {"sector": "etf_proxy", "industry": "intermediate_corp_bond_etf"},
    # ---- Commodity Proxies ----
    "GLD":   {"sector": "commodity_proxy", "industry": "gold_etf"},
    "SLV":   {"sector": "commodity_proxy", "industry": "silver_etf"},
    "USO":   {"sector": "commodity_proxy", "industry": "crude_oil_etf"},
    # ---- Volatility / Inverse ----
    "UVXY":  {"sector": "volatility_proxy", "industry": "leveraged_vix_etf"},
    "SVXY":  {"sector": "volatility_proxy", "industry": "inverse_vix_etf"},
    "VXX":   {"sector": "volatility_proxy", "industry": "vix_futures_etp"},
    "SPXS":  {"sector": "etf_proxy", "industry": "leveraged_inverse_sp500_etf"},
    # ---- Additional PRU + position research ----
    "VRT":   {"sector": "industrials", "industry": "power_management"},
    "SWK":   {"sector": "industrials", "industry": "tools"},
    "ADSK":  {"sector": "information_technology", "industry": "software"},
    "CHTR":  {"sector": "communication_services", "industry": "cable_broadband"},
    "PATH":  {"sector": "information_technology", "industry": "software"},
    "FROG":  {"sector": "information_technology", "industry": "software"},
    "TTD":   {"sector": "communication_services", "industry": "advertising_tech"},
    "EOSE":  {"sector": "industrials", "industry": "energy_storage"},
    "INFQ":  {"sector": "information_technology", "industry": "semiconductors"},
    "U":     {"sector": "information_technology", "industry": "software"},
    "ASND":  {"sector": "health_care", "industry": "biotech"},
    "WAT":   {"sector": "health_care", "industry": "life_sciences_tools"},
    "RL":    {"sector": "consumer_discretionary", "industry": "apparel_footwear"},
    "POOL":  {"sector": "consumer_discretionary", "industry": "pool_supplies"},
    "BBY":   {"sector": "consumer_discretionary", "industry": "consumer_electronics_retail"},
    "XNDU":  {"sector": "information_technology", "industry": "semiconductors"},
    "USAR":  {"sector": "financials", "industry": "specialty_finance"},
    "FPS":   {"sector": "materials", "industry": "specialty_materials"},
    "YSS":   {"sector": "information_technology", "industry": "software"},
    "SYF":   {"sector": "financials", "industry": "consumer_finance"},
    "MRSH":  {"sector": "financials", "industry": "financial_services"},
    "FDS":   {"sector": "financials", "industry": "financial_data"},
    "TW":    {"sector": "financials", "industry": "financial_exchanges"},
    "EPAM":  {"sector": "information_technology", "industry": "it_services"},
    "CBRE":  {"sector": "real_estate", "industry": "real_estate_services"},
    "BURL":  {"sector": "consumer_discretionary", "industry": "off_price_retail"},
    "HAS":   {"sector": "consumer_discretionary", "industry": "toys_games"},
    "CFG":   {"sector": "financials", "industry": "banks_regional"},
    "DKS":   {"sector": "consumer_discretionary", "industry": "sporting_goods_retail"},
    "MUSA":  {"sector": "consumer_staples", "industry": "convenience_fuel_retail"},
    "TKO":   {"sector": "communication_services", "industry": "live_entertainment"},
    "AVTR":  {"sector": "health_care", "industry": "life_sciences_tools"},
    "AZO":   {"sector": "consumer_discretionary", "industry": "auto_parts_retail"},
    "ELV":   {"sector": "health_care", "industry": "health_insurance"},
    "BMNR":  {"sector": "health_care", "industry": "biotech"},
    "TTD":   {"sector": "communication_services", "industry": "advertising_tech"},
    "EOSE":  {"sector": "industrials", "industry": "energy_storage"},
    "BULL":  {"sector": "etf_proxy", "industry": "leveraged_etf"},
    "LIN":   {"sector": "materials", "industry": "industrial_gases"},
    "ONDS":  {"sector": "communication_services", "industry": "satellite"},
    "CRWG":  {"sector": "information_technology", "industry": "software"},
    "DOCN":  {"sector": "information_technology", "industry": "cloud_computing"},
    "EXPE":  {"sector": "consumer_discretionary", "industry": "travel_online"},
    "CHD":   {"sector": "consumer_staples", "industry": "household_products"},
    "JHX":   {"sector": "materials", "industry": "building_materials"},
    "PPG":   {"sector": "materials", "industry": "specialty_chemicals"},
    "ZBRA":  {"sector": "information_technology", "industry": "enterprise_hardware"},
    "TE":    {"sector": "industrials", "industry": "industrial_conglomerate"},
    "TVTX":  {"sector": "health_care", "industry": "biotech"},
}

# ---------------------------------------------------------------------------
# Approval status logic
# ---------------------------------------------------------------------------
_APPROVED_SECTORS = {
    "information_technology", "communication_services", "consumer_discretionary",
    "consumer_staples", "health_care", "financials", "industrials", "energy",
    "materials", "real_estate", "utilities",
}
_ETF_PROXY_SECTORS = {"etf_proxy", "commodity_proxy", "volatility_proxy"}


def _approval_status(symbol: str, sector: str, sources: list[str]) -> str:
    if sector in _APPROVED_SECTORS:
        return "approved"
    if sector in _ETF_PROXY_SECTORS:
        return "approved"
    return "unknown_requires_provider_enrichment"


# ---------------------------------------------------------------------------
# Source file readers
# ---------------------------------------------------------------------------
def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_committed_universe(path: str) -> list[str]:
    data = _load_json(path)
    return [rec["symbol"] for rec in data.get("symbols", [])]


def _read_position_research(path: str) -> list[str]:
    data = _load_json(path)
    symbols = data.get("symbols", [])
    if symbols and isinstance(symbols[0], dict):
        return [s["ticker"] for s in symbols]
    return [str(s) for s in symbols]


def _read_daily_promoted(path: str) -> list[str]:
    data = _load_json(path)
    symbols = data.get("symbols", [])
    if symbols and isinstance(symbols[0], dict):
        return [s["ticker"] for s in symbols]
    return [str(s) for s in symbols]


def _read_favourites(path: str) -> list[str]:
    data = _load_json(path)
    if isinstance(data, list):
        return [str(s) for s in data]
    return []


def _read_thematic_roster(path: str) -> dict[str, list[str]]:
    """Returns {theme_id: [symbols]}."""
    data = _load_json(path)
    result: dict[str, list[str]] = {}
    for roster in data.get("rosters", []):
        tid = roster["theme_id"]
        result[tid] = roster.get("core_symbols", []) + roster.get("etf_proxies", [])
    return result


def _read_shadow_universe(path: str) -> list[str]:
    data = _load_json(path)
    return [c["symbol"] for c in data.get("candidates", [])]


def _read_advisory_log(path: str) -> list[dict]:
    records = []
    if not os.path.exists(path):
        return records
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


# ---------------------------------------------------------------------------
# Build sector_schema.json
# ---------------------------------------------------------------------------
def _build_sector_schema() -> dict:
    sectors = [
        {
            "sector_id": "information_technology",
            "sector_name": "Information Technology",
            "gics_code": "45",
            "industries": [
                "semiconductors", "semiconductor_equipment", "software",
                "technology_hardware", "it_services", "networking",
                "cybersecurity", "electronic_components", "quantum_computing",
                "ai_infrastructure", "clean_energy_tech", "advanced_nuclear",
                "optical_components", "enterprise_hardware", "measurement_instruments",
            ],
        },
        {
            "sector_id": "communication_services",
            "sector_name": "Communication Services",
            "gics_code": "50",
            "industries": [
                "internet_search", "social_media", "streaming", "media_entertainment",
                "live_entertainment", "telecom", "telecom_equipment",
                "cable_broadband", "advertising_tech", "satellite",
            ],
        },
        {
            "sector_id": "consumer_discretionary",
            "sector_name": "Consumer Discretionary",
            "gics_code": "25",
            "industries": [
                "ecommerce", "automotive_ev", "automotive_retail", "automotive_rental",
                "apparel_footwear", "home_improvement", "food_delivery", "rideshare",
                "restaurants", "travel_online", "gaming", "sporting_goods_retail",
                "recreational_vehicles", "off_price_retail", "consumer_electronics",
                "auto_parts_retail", "pool_supplies", "digital_health", "toys_games",
            ],
        },
        {
            "sector_id": "consumer_staples",
            "sector_name": "Consumer Staples",
            "gics_code": "30",
            "industries": [
                "beverages", "beverages_spirits", "household_products",
                "discount_retail", "convenience_fuel_retail",
            ],
        },
        {
            "sector_id": "health_care",
            "sector_name": "Health Care",
            "gics_code": "35",
            "industries": [
                "pharma", "biotech", "medical_devices", "health_insurance",
                "life_sciences_tools", "medical_equipment_services", "data_analytics",
            ],
        },
        {
            "sector_id": "financials",
            "sector_name": "Financials",
            "gics_code": "40",
            "industries": [
                "banks_diversified", "banks_regional", "investment_banking",
                "asset_management", "brokerage", "payments", "consumer_finance",
                "fintech", "insurance", "insurance_reinsurance", "financial_data",
                "financial_software", "financial_exchanges", "financial_services",
                "crypto_exchange", "crypto_holding", "specialty_finance",
            ],
        },
        {
            "sector_id": "industrials",
            "sector_name": "Industrials",
            "gics_code": "20",
            "industries": [
                "aerospace_defense", "aerospace_components", "aerospace_electronics",
                "power_equipment", "electrical_equipment", "construction_engineering",
                "construction_machinery", "agricultural_machinery",
                "industrial_conglomerate", "railroads", "airlines", "hvac",
                "elevators", "hr_services", "tools", "logistics", "government_it_services",
                "government_software", "automation_robotics", "space_launch",
                "evtol_aviation", "aircraft_leasing", "lighting", "water_equipment",
                "environmental_services", "pest_control", "public_safety_tech",
                "energy_storage", "power_management",
            ],
        },
        {
            "sector_id": "energy",
            "sector_name": "Energy",
            "gics_code": "10",
            "industries": [
                "integrated_oil_gas", "oil_gas_ep", "oilfield_services",
                "oil_gas_midstream", "power_generation",
            ],
        },
        {
            "sector_id": "materials",
            "sector_name": "Materials",
            "gics_code": "15",
            "industries": [
                "gold_mining", "gold_royalty", "silver_mining", "copper_mining",
                "uranium_mining", "uranium_enrichment", "industrial_gases",
                "specialty_chemicals", "agricultural_chemicals", "industrial_metals",
                "specialty_materials", "antimony_mining", "steel", "building_materials",
            ],
        },
        {
            "sector_id": "real_estate",
            "sector_name": "Real Estate",
            "gics_code": "60",
            "industries": [
                "residential_reit", "data_centre_reit", "storage_reit",
                "retail_reit", "cell_tower_reit", "hotel_reit", "real_estate_services",
            ],
        },
        {
            "sector_id": "utilities",
            "sector_name": "Utilities",
            "gics_code": "55",
            "industries": [
                "nuclear_power", "renewable_electric", "electric_gas_utility",
            ],
        },
    ]
    proxy_classifications = [
        {
            "classification_id": "etf_proxy",
            "description": "Exchange-traded funds covering broad market, sector, leverage, or inverse exposure",
            "sub_types": [
                "sp500_etf", "nasdaq100_etf", "small_cap_etf", "total_market_etf",
                "equal_weight_sp500_etf", "emerging_markets_etf", "international_developed_etf",
                "technology_sector_etf", "semiconductor_etf", "software_etf",
                "financials_sector_etf", "healthcare_sector_etf", "energy_sector_etf",
                "consumer_staples_sector_etf", "consumer_discretionary_etf",
                "industrials_sector_etf", "materials_sector_etf", "utilities_sector_etf",
                "aerospace_defense_etf", "quality_factor_etf", "low_volatility_etf",
                "regional_bank_etf", "biotech_etf",
                "leveraged_nasdaq_etf", "inverse_nasdaq_etf",
                "leveraged_semiconductor_etf", "inverse_semiconductor_etf",
                "leveraged_inverse_sp500_etf", "leveraged_single_stock_etf",
                "leveraged_etf", "inverse_etf",
                "long_treasury_etf", "short_treasury_etf", "tbill_etf",
                "high_yield_bond_etf", "investment_grade_bond_etf",
                "long_corp_bond_etf", "intermediate_corp_bond_etf",
                "crypto_futures_etf", "spot_bitcoin_etf",
                "south_korea_etf", "brazil_etf", "china_etf", "dow_jones_etf",
            ],
        },
        {
            "classification_id": "index_proxy",
            "description": "Index-tracking instruments representing a benchmark rather than a directly tradable security (e.g. ^VIX, ^SPX). Used as reference data only.",
            "sub_types": ["volatility_index", "equity_index", "rate_index"],
        },
        {
            "classification_id": "commodity_proxy",
            "description": "ETFs or physical trusts providing direct commodity exposure (gold, silver, crude oil)",
            "sub_types": ["gold_etf", "silver_etf", "crude_oil_etf"],
        },
        {
            "classification_id": "crypto_proxy",
            "description": "Spot or futures-based cryptocurrency ETFs and crypto-holding companies providing regulated crypto exposure",
            "sub_types": ["spot_bitcoin_etf", "crypto_futures_etf", "crypto_holding_company"],
        },
        {
            "classification_id": "volatility_proxy",
            "description": "Volatility-linked ETPs (VIX futures, leveraged/inverse VIX) — negative carry in contango; short-duration hedge only",
            "sub_types": ["vix_futures_etp", "leveraged_vix_etf", "inverse_vix_etf"],
        },
        {
            "classification_id": "macro_proxy",
            "description": "Instruments providing broad macro economic regime exposure not captured by a single sector ETF (dollar, rates, inflation)",
            "sub_types": ["dollar_index_etf", "rate_sensitive_etf", "inflation_proxy_etf"],
        },
        {
            "classification_id": "unknown",
            "description": "Symbol not classifiable from local sources — requires provider enrichment before sector or theme can be assigned",
            "sub_types": ["unknown_requires_provider_enrichment"],
        },
    ]
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "reference_data_builder",
        "sectors": sectors,
        "proxy_classifications": proxy_classifications,
    }


# ---------------------------------------------------------------------------
# Build theme_overlay_map.json
# ---------------------------------------------------------------------------
def _build_theme_overlay_map() -> dict:
    themes = [
        # ---- Existing thematic roster themes ----
        {
            "theme_id": "data_centre_power",
            "theme_name": "Data Centre Power Infrastructure",
            "description": "Direct beneficiaries of AI data-centre power demand: cooling, power management, grid engineering, nuclear baseload.",
            "supply_chain_role": "critical_infrastructure_build_out",
            "risk_flags": ["capex_cycle_dependency", "utility_regulation"],
            "canonical_symbols": ["VRT", "ETN", "PWR", "CEG"],
            "proxy_symbols": ["XLU"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "semiconductors",
            "theme_name": "AI Semiconductor Infrastructure",
            "description": "GPU accelerators, foundry capacity, advanced packaging, EUV lithography — the compute stack for AI.",
            "supply_chain_role": "end_market_demand_driver",
            "risk_flags": ["geopolitical_export_controls", "capex_cycle", "customer_concentration"],
            "canonical_symbols": ["NVDA", "TSM", "AVGO", "AMD", "ASML"],
            "proxy_symbols": ["SMH"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "banks",
            "theme_name": "Rate-Sensitive Banks",
            "description": "Net-interest-margin beneficiaries when rates rising and credit contained.",
            "supply_chain_role": "financial_intermediary",
            "risk_flags": ["credit_cycle_turn", "deposit_beta_compression", "regulatory_capital"],
            "canonical_symbols": ["JPM", "BAC", "WFC", "GS"],
            "proxy_symbols": ["XLF"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "energy",
            "theme_name": "Oil Supply Shock Beneficiaries",
            "description": "Integrated oil, E&P, and oilfield services — responsive to supply-side shocks.",
            "supply_chain_role": "commodity_producer",
            "risk_flags": ["oil_price_volatility", "energy_transition_headwind", "geopolitical_risk"],
            "canonical_symbols": ["XOM", "CVX", "OXY", "SLB"],
            "proxy_symbols": ["XLE"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "defence",
            "theme_name": "Defence & Aerospace",
            "description": "Prime contractors with long-cycle government contracts — beneficiaries of elevated defence budgets.",
            "supply_chain_role": "government_prime_contractor",
            "risk_flags": ["budget_sequestration", "programme_cost_overruns", "geopolitical_dependency"],
            "canonical_symbols": ["LMT", "NOC", "RTX", "GD"],
            "proxy_symbols": ["ITA"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "quality_cash_flow",
            "theme_name": "Quality Cash Flow Compounders",
            "description": "High-moat businesses with durable free cash flow and secular growth.",
            "supply_chain_role": "platform_business",
            "risk_flags": ["valuation_multiple_compression", "regulatory_antitrust"],
            "canonical_symbols": ["MSFT", "AAPL", "COST", "BRK.B"],
            "proxy_symbols": ["QUAL"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "defensive_quality",
            "theme_name": "Defensive Quality / Staples",
            "description": "Consumer staples and healthcare with low beta, pricing power, and dividend reliability.",
            "supply_chain_role": "consumer_staples_anchor",
            "risk_flags": ["currency_headwinds", "input_cost_inflation"],
            "canonical_symbols": ["COST", "JNJ", "PG", "KO", "PEP"],
            "proxy_symbols": ["XLP", "XLV", "SPLV"],
            "source": "thematic_roster",
        },
        {
            "theme_id": "small_caps",
            "theme_name": "Small Cap Recovery",
            "description": "Broad small-cap universe — rate-sensitive, domestic-revenue heavy. Headwind roster: trade with caution.",
            "supply_chain_role": "broad_domestic_economy",
            "risk_flags": ["rate_sensitivity", "credit_availability", "headwind_regime"],
            "canonical_symbols": [],
            "proxy_symbols": ["IWM"],
            "source": "thematic_roster",
            "headwind_roster": True,
        },
        # ---- Coverage-gap approved themes (Sprint 7A.2) ----
        {
            "theme_id": "memory_storage",
            "theme_name": "Memory and Storage — AI Data-Centre Demand",
            "description": "Memory/storage names with recurring advisory gap evidence. Approved via coverage_gap_review (22 occurrences each). AI data-centre storage demand and memory cycle tightening.",
            "supply_chain_role": "ai_storage_infrastructure",
            "risk_flags": ["memory_cycle_risk", "commodity_pricing", "capex_sensitivity", "earnings_volatility", "supply_demand_reversal", "valuation_risk"],
            "canonical_symbols": ["SNDK", "WDC"],
            "proxy_symbols": [],
            "source": "thematic_roster",
            "approval_source": "coverage_gap_review",
        },
        {
            "theme_id": "ai_compute_infrastructure",
            "theme_name": "AI Compute Infrastructure — Neocloud and Power-Constrained Compute",
            "description": "Approved AI compute infrastructure/neocloud names. IREN approved with caution (10 occurrences as advisory_unresolved). Speculative risk profile: financing, power cost, capacity execution.",
            "supply_chain_role": "ai_compute_capacity_provider",
            "risk_flags": ["speculative_growth", "financing_risk", "power_cost", "capacity_execution_risk", "customer_concentration", "dilution_risk", "volatility_risk"],
            "canonical_symbols": ["IREN"],
            "proxy_symbols": [],
            "source": "thematic_roster",
            "approval_source": "coverage_gap_review",
        },
        # ---- Technology sub-themes ----
        {
            "theme_id": "ai_infrastructure_hardware",
            "theme_name": "AI Infrastructure Hardware",
            "description": "Servers, networking, power supplies, and cooling — physical layer of AI compute.",
            "supply_chain_role": "hardware_platform",
            "risk_flags": ["capex_cycle", "customer_concentration", "component_shortage"],
            "canonical_symbols": ["SMCI", "DELL", "APLD", "CRWV", "IREN", "WULF"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "semiconductor_equipment",
            "theme_name": "Semiconductor Equipment",
            "description": "Capital equipment for advanced chip fabrication — bottleneck for all leading-edge capacity.",
            "supply_chain_role": "upstream_bottleneck",
            "risk_flags": ["export_control_risk", "cyclical_capex", "customer_concentration_tsmc"],
            "canonical_symbols": ["ASML", "AMAT", "LRCX", "KLAC", "TER", "ONTO"],
            "proxy_symbols": ["SMH"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "cloud_computing",
            "theme_name": "Cloud Computing",
            "description": "Hyperscaler cloud platforms and cloud-native software — subscription revenue, network effects.",
            "supply_chain_role": "hyperscaler_platform",
            "risk_flags": ["multi_cloud_commoditisation", "regulatory_antitrust"],
            "canonical_symbols": ["AMZN", "MSFT", "GOOGL", "SNOW", "MDB"],
            "proxy_symbols": ["IGV"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "enterprise_software",
            "theme_name": "Enterprise Software",
            "description": "Recurring-revenue SaaS with high switching costs — CRM, ERP, workflow, analytics.",
            "supply_chain_role": "recurring_revenue_platform",
            "risk_flags": ["ai_commoditisation_of_features", "budget_freeze_risk"],
            "canonical_symbols": ["CRM", "NOW", "INTU", "ADBE", "PLTR", "APP"],
            "proxy_symbols": ["IGV"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "cybersecurity",
            "theme_name": "Cybersecurity",
            "description": "Zero-trust, endpoint, cloud security — beneficiaries of rising threat surface and compliance mandates.",
            "supply_chain_role": "mission_critical_security",
            "risk_flags": ["vendor_consolidation", "platform_competition_from_msft"],
            "canonical_symbols": ["PANW", "CRWD", "NET", "FTNT"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "ai_application_software",
            "theme_name": "AI Application Software",
            "description": "Software natively embedding AI inference at the application layer — highest monetisation leverage on AI adoption.",
            "supply_chain_role": "application_layer_ai",
            "risk_flags": ["model_commoditisation", "open_source_disruption"],
            "canonical_symbols": ["PLTR", "APP", "SNOW", "TEAM"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "fintech",
            "theme_name": "Fintech & Digital Payments",
            "description": "Digital payments, neobanks, BNPL, brokerage disruption — beneficiaries of cash-to-digital shift.",
            "supply_chain_role": "financial_intermediary_disruptor",
            "risk_flags": ["regulatory_scrutiny", "credit_cycle_exposure", "incumbent_response"],
            "canonical_symbols": ["SOFI", "AFRM", "HOOD", "COIN", "TOST"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "internet_platform",
            "theme_name": "Internet Platform / Search / Social",
            "description": "Ad-supported internet platforms with massive DAU scale — duopoly in search and social.",
            "supply_chain_role": "ad_network_monopoly",
            "risk_flags": ["regulatory_antitrust", "ad_spend_cyclicality", "ai_disruption_to_search"],
            "canonical_symbols": ["GOOGL", "META", "SNAP"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "streaming_media",
            "theme_name": "Streaming & Digital Media",
            "description": "Subscription streaming, music, and live events — beneficiaries of cord-cutting acceleration.",
            "supply_chain_role": "content_distribution",
            "risk_flags": ["content_cost_inflation", "subscriber_saturation"],
            "canonical_symbols": ["NFLX", "SPOT", "DIS"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        # ---- Healthcare sub-themes ----
        {
            "theme_id": "biotech_oncology",
            "theme_name": "Biotech — Oncology",
            "description": "Clinical-stage and commercial oncology biotechs — binary catalyst risk, blockbuster TAM.",
            "supply_chain_role": "clinical_stage_binary",
            "risk_flags": ["fda_binary_readout", "trial_failure", "competitive_indication"],
            "canonical_symbols": ["RVMD", "BMNR"],
            "proxy_symbols": ["XBI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "biotech_rare_disease",
            "theme_name": "Biotech — Rare Disease",
            "description": "Rare disease / orphan drug biotechs — high pricing power, fast FDA review, smaller patient populations.",
            "supply_chain_role": "orphan_drug_developer",
            "risk_flags": ["payer_coverage_risk", "fda_binary_readout"],
            "canonical_symbols": ["ALNY", "ARGX", "TVTX", "PRAX"],
            "proxy_symbols": ["XBI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "medical_devices",
            "theme_name": "Medical Devices",
            "description": "Implantable, surgical, and monitoring devices — recurring consumable revenue with procedure volume leverage.",
            "supply_chain_role": "hospital_capital_consumables",
            "risk_flags": ["hospital_capex_cycle", "reimbursement_risk", "procedure_volume_sensitivity"],
            "canonical_symbols": ["ABT", "BSX", "MDT", "ISRG", "PODD", "EW"],
            "proxy_symbols": ["XLV"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "pharma_large_cap",
            "theme_name": "Large Cap Pharma",
            "description": "Diversified large pharma with patent-protected blockbusters — dividend stability.",
            "supply_chain_role": "blockbuster_drug_franchise",
            "risk_flags": ["patent_cliff", "drug_pricing_regulation", "pipeline_binary_risk"],
            "canonical_symbols": ["LLY", "JNJ", "MRK", "ABBV", "PFE", "AMGN", "NVO"],
            "proxy_symbols": ["XLV"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "health_insurance",
            "theme_name": "Health Insurance / Managed Care",
            "description": "Health insurers — premium revenue, MLR leverage, Medicaid/Medicare exposure.",
            "supply_chain_role": "healthcare_payer",
            "risk_flags": ["medical_cost_ratio_deterioration", "cms_reimbursement_cuts"],
            "canonical_symbols": ["UNH", "HUM", "ELV"],
            "proxy_symbols": ["XLV"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "obesity_drugs",
            "theme_name": "GLP-1 / Obesity Drugs",
            "description": "GLP-1 agonist beneficiaries — LLY and NVO lead; supply chain and adjacent beneficiaries.",
            "supply_chain_role": "blockbuster_single_drug_platform",
            "risk_flags": ["single_drug_dependency", "competitive_pipeline", "payer_access_limits"],
            "canonical_symbols": ["LLY", "NVO"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "genomics_diagnostics",
            "theme_name": "Genomics & Diagnostics",
            "description": "Next-gen sequencing, liquid biopsy, multi-cancer early detection — high-growth diagnostic platforms.",
            "supply_chain_role": "diagnostic_platform",
            "risk_flags": ["reimbursement_coverage_uncertainty", "competitive_intensity"],
            "canonical_symbols": ["TMO", "A"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        # ---- Financial sub-themes ----
        {
            "theme_id": "investment_banking",
            "theme_name": "Investment Banking & Capital Markets",
            "description": "M&A advisory, ECM/DCM underwriting, trading revenue — cyclical leverage to deal flow.",
            "supply_chain_role": "capital_markets_intermediary",
            "risk_flags": ["m_and_a_cycle", "rate_environment", "trading_vol_dependency"],
            "canonical_symbols": ["GS", "MS", "BX", "APO", "CG"],
            "proxy_symbols": ["XLF"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "asset_management",
            "theme_name": "Asset Management",
            "description": "Fee-based AUM businesses — beneficiaries of rising equity markets and alternative allocations.",
            "supply_chain_role": "capital_allocator",
            "risk_flags": ["aum_market_sensitivity", "fee_compression"],
            "canonical_symbols": ["BLK", "BX", "APO", "AMP"],
            "proxy_symbols": ["XLF"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "insurance",
            "theme_name": "Insurance & Reinsurance",
            "description": "P&C and specialty insurance — float income, underwriting discipline, hard market pricing.",
            "supply_chain_role": "risk_transfer",
            "risk_flags": ["catastrophe_loss_exposure", "reserve_adequacy"],
            "canonical_symbols": ["PGR", "AON", "ACGL"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "payments_processing",
            "theme_name": "Payments & Processing",
            "description": "Card network duopoly and payment processors — network effect moats, volume leverage.",
            "supply_chain_role": "payment_network_moat",
            "risk_flags": ["cbdc_disruption", "crypto_displacement", "regulatory_interchange_caps"],
            "canonical_symbols": ["V", "MA", "AXP", "FISV", "GPN"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "crypto_exchange",
            "theme_name": "Crypto Exchange & Holdings",
            "description": "Regulated crypto exchanges and BTC holding companies — vol and price-level leverage.",
            "supply_chain_role": "crypto_market_infrastructure",
            "risk_flags": ["regulatory_uncertainty", "crypto_price_vol", "security_risk"],
            "canonical_symbols": ["COIN", "MSTR"],
            "proxy_symbols": ["IBIT", "BITO"],
            "source": "reference_data_builder",
        },
        # ---- Industrials sub-themes ----
        {
            "theme_id": "aerospace_manufacturing",
            "theme_name": "Aerospace Manufacturing",
            "description": "Commercial and military aerospace — production ramp, supply chain normalisation.",
            "supply_chain_role": "prime_airframe_manufacturer",
            "risk_flags": ["supply_chain_bottleneck", "certification_risk", "labour_disputes"],
            "canonical_symbols": ["BA", "GE", "HWM"],
            "proxy_symbols": ["ITA"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "electrical_equipment",
            "theme_name": "Electrical Equipment & Grid",
            "description": "Power management, transformers, switchgear — infrastructure backbone for AI power demand.",
            "supply_chain_role": "grid_equipment_supplier",
            "risk_flags": ["lead_time_constraints", "utility_capex_cycle"],
            "canonical_symbols": ["ETN", "GEV"],
            "proxy_symbols": ["XLI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "construction_engineering",
            "theme_name": "Construction & Engineering",
            "description": "EPC contractors, electrical/grid infrastructure build-out, data-centre construction.",
            "supply_chain_role": "infrastructure_contractor",
            "risk_flags": ["project_execution_risk", "cost_overrun", "permit_delay"],
            "canonical_symbols": ["PWR", "STRL"],
            "proxy_symbols": ["XLI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "automation_robotics",
            "theme_name": "Industrial Automation & Robotics",
            "description": "Factory automation, robotics, industrial AI — beneficiaries of reshoring and labour cost inflation.",
            "supply_chain_role": "manufacturing_capex_cycle",
            "risk_flags": ["capex_cycle_delay", "adoption_rate_uncertainty"],
            "canonical_symbols": ["SYM"],
            "proxy_symbols": ["XLI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "industrial_conglomerates",
            "theme_name": "Industrial Conglomerates",
            "description": "Diversified industrial holding companies — defensive cash flows, portfolio optionality.",
            "supply_chain_role": "diversified_industrial",
            "risk_flags": ["conglomerate_discount", "segment_mix_shift"],
            "canonical_symbols": ["HON", "CAT", "DE"],
            "proxy_symbols": ["XLI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "logistics_transportation",
            "theme_name": "Logistics & Transportation",
            "description": "Airlines, rails, freight — volume leverage to economic cycle.",
            "supply_chain_role": "freight_volume_lever",
            "risk_flags": ["fuel_cost_passthrough", "demand_cyclicality", "labour_cost"],
            "canonical_symbols": ["DAL", "UAL", "UNP"],
            "proxy_symbols": ["XLI"],
            "source": "reference_data_builder",
        },
        # ---- Energy sub-themes ----
        {
            "theme_id": "nuclear_power",
            "theme_name": "Nuclear Power",
            "description": "Clean baseload power generation — beneficiaries of AI data-centre power procurement and decarbonisation.",
            "supply_chain_role": "baseload_clean_energy",
            "risk_flags": ["regulatory_approval_timeline", "construction_cost_overrun"],
            "canonical_symbols": ["CEG", "OKLO", "TLN"],
            "proxy_symbols": ["XLU"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "renewable_energy",
            "theme_name": "Renewable Energy",
            "description": "Solar, wind, and clean electricity generation and distribution.",
            "supply_chain_role": "clean_power_generation",
            "risk_flags": ["ira_subsidy_policy_risk", "grid_interconnection_queue"],
            "canonical_symbols": ["NEE", "BE"],
            "proxy_symbols": ["XLU"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "energy_storage",
            "theme_name": "Energy Storage",
            "description": "Battery and long-duration storage — grid balancing and EV supply chain.",
            "supply_chain_role": "grid_stabilisation",
            "risk_flags": ["technology_cost_curve", "supply_chain_lithium"],
            "canonical_symbols": ["EOSE"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "uranium",
            "theme_name": "Uranium & Nuclear Fuel",
            "description": "Uranium miners and enrichers — supply constraint + nuclear renaissance tailwind.",
            "supply_chain_role": "nuclear_fuel_supply",
            "risk_flags": ["uranium_spot_price_vol", "enrichment_capacity"],
            "canonical_symbols": ["UUUU", "LEU"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        # ---- Materials sub-themes ----
        {
            "theme_id": "gold_mining",
            "theme_name": "Gold Mining",
            "description": "Senior and royalty gold miners — inflation hedge, FX diversifier, leveraged to spot gold.",
            "supply_chain_role": "gold_beta_leverage",
            "risk_flags": ["operating_cost_inflation", "geopolitical_asset_risk"],
            "canonical_symbols": ["NEM", "AEM", "RGLD"],
            "proxy_symbols": ["GDX", "GLD"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "copper_mining",
            "theme_name": "Copper & Base Metals",
            "description": "Copper producers — infrastructure and EV demand proxy.",
            "supply_chain_role": "industrial_metals_supply",
            "risk_flags": ["china_demand_sensitivity", "mine_supply_disruption"],
            "canonical_symbols": ["FCX"],
            "proxy_symbols": ["XLB"],
            "source": "reference_data_builder",
        },
        # ---- Consumer sub-themes ----
        {
            "theme_id": "consumer_discretionary_retail",
            "theme_name": "Consumer Retail",
            "description": "Discretionary retail including e-commerce, specialty, and value retail.",
            "supply_chain_role": "consumer_demand_barometer",
            "risk_flags": ["consumer_credit_health", "inventory_cycle"],
            "canonical_symbols": ["AMZN", "HD", "COST"],
            "proxy_symbols": ["XLY"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "consumer_staples_food_bev",
            "theme_name": "Consumer Staples — Food & Beverage",
            "description": "Branded food, beverage, and personal care — pricing power, dividend reliability.",
            "supply_chain_role": "non_cyclical_consumer_spend",
            "risk_flags": ["input_cost_inflation", "private_label_substitution"],
            "canonical_symbols": ["KO", "PEP", "PG"],
            "proxy_symbols": ["XLP"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "luxury_consumer",
            "theme_name": "Luxury Consumer",
            "description": "Aspirational and luxury brands — wealth effect leverage, China exposure.",
            "supply_chain_role": "aspirational_brand",
            "risk_flags": ["china_slowdown", "aspirational_consumer_trade_down"],
            "canonical_symbols": [],
            "proxy_symbols": ["XLY"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "automotive",
            "theme_name": "Automotive & EV",
            "description": "EV transition — Tesla dominance, legacy OEM transition, EV infrastructure.",
            "supply_chain_role": "ev_transition_play",
            "risk_flags": ["ev_demand_normalisation", "battery_cost_curve", "competitive_intensity"],
            "canonical_symbols": ["TSLA", "LCID"],
            "proxy_symbols": ["XLY"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "travel_hospitality",
            "theme_name": "Travel & Hospitality",
            "description": "Online travel, hotels, airlines — post-COVID recovery and leisure demand.",
            "supply_chain_role": "cyclical_leisure_demand",
            "risk_flags": ["macro_demand_sensitivity", "fuel_cost"],
            "canonical_symbols": ["BKNG", "DAL", "UAL"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        # ---- Communication sub-themes ----
        {
            "theme_id": "wireless_telecom",
            "theme_name": "Wireless Telecom",
            "description": "Mobile network operators — stable FCF, dividend, 5G spectrum leverage.",
            "supply_chain_role": "network_utility",
            "risk_flags": ["spectrum_cost", "arpu_compression", "5g_capex_payback"],
            "canonical_symbols": ["TMUS", "VZ", "T"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "media_entertainment",
            "theme_name": "Media & Entertainment",
            "description": "Traditional and digital media, live events, gaming — content monetisation.",
            "supply_chain_role": "content_owner_distributor",
            "risk_flags": ["content_cost_inflation", "cord_cutting_acceleration"],
            "canonical_symbols": ["DIS", "LYV"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "social_media",
            "theme_name": "Social Media",
            "description": "User-generated content platforms — DAU/MAU monetisation via advertising.",
            "supply_chain_role": "ad_supported_platform",
            "risk_flags": ["regulatory_risk", "teen_usage_decline", "ad_spend_cyclicality"],
            "canonical_symbols": ["META", "SNAP"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "advertising_tech",
            "theme_name": "Advertising Technology",
            "description": "Programmatic ad infrastructure — demand-side platforms, ad measurement, commerce media.",
            "supply_chain_role": "ad_infrastructure",
            "risk_flags": ["cookie_deprecation", "google_antitrust", "ad_spend_cyclicality"],
            "canonical_symbols": ["TTD"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        # ---- Real Estate sub-themes ----
        {
            "theme_id": "data_centre_reit",
            "theme_name": "Data Centre REIT",
            "description": "Wholesale and colocation data centres — AI-driven hyperscaler demand for physical compute space.",
            "supply_chain_role": "physical_ai_compute_infrastructure",
            "risk_flags": ["power_availability", "hyperscaler_build_own_risk"],
            "canonical_symbols": ["DLR"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "industrial_reit",
            "theme_name": "Industrial REIT",
            "description": "Logistics warehouses and last-mile distribution — e-commerce fulfilment demand.",
            "supply_chain_role": "logistics_real_estate",
            "risk_flags": ["rent_growth_normalisation", "supply_overhang"],
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "residential_reit",
            "theme_name": "Residential REIT",
            "description": "Apartment and single-family rental — housing affordability crisis beneficiaries.",
            "supply_chain_role": "rental_housing_operator",
            "risk_flags": ["rent_control_legislation", "supply_pipeline"],
            "canonical_symbols": ["EQR", "UDR"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        # ---- Market structure themes ----
        {
            "theme_id": "broad_market_index",
            "theme_name": "Broad Market Index",
            "description": "S&P 500, Nasdaq 100, total market, and international index ETFs.",
            "supply_chain_role": "passive_index_exposure",
            "risk_flags": ["concentration_risk_mega_cap"],
            "canonical_symbols": [],
            "proxy_symbols": ["SPY", "QQQ", "QQQM", "VOO", "VTI", "IWM", "DIA", "RSP", "IVV", "EEM", "EFA"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_technology",
            "theme_name": "Technology Sector ETF",
            "description": "XLK and equivalent sector ETFs.",
            "canonical_symbols": [],
            "proxy_symbols": ["XLK", "IGV", "SMH", "SOXX"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_healthcare",
            "theme_name": "Healthcare Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLV", "XBI"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_financials",
            "theme_name": "Financials Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLF", "KRE"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_energy",
            "theme_name": "Energy Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLE"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_industrials",
            "theme_name": "Industrials Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLI", "ITA"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_consumer_discretionary",
            "theme_name": "Consumer Discretionary Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLY"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_consumer_staples",
            "theme_name": "Consumer Staples Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLP"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_materials",
            "theme_name": "Materials Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLB", "GDX"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_real_estate",
            "theme_name": "Real Estate Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_communication",
            "theme_name": "Communication Services Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "sector_etf_utilities",
            "theme_name": "Utilities Sector ETF",
            "canonical_symbols": [],
            "proxy_symbols": ["XLU"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "leveraged_etf",
            "theme_name": "Leveraged ETF",
            "description": "2x/3x leveraged ETFs — path-dependency decay risk, short-duration only.",
            "supply_chain_role": "leveraged_beta_amplifier",
            "risk_flags": ["vol_decay", "path_dependency", "not_for_swing_holding"],
            "canonical_symbols": ["SOXL", "TQQQ", "TSLL", "NVDL", "BULL"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "inverse_etf",
            "theme_name": "Inverse ETF",
            "description": "Inverse and leveraged-inverse ETFs — short-duration bearish exposure.",
            "supply_chain_role": "inverse_beta",
            "risk_flags": ["vol_decay", "rebalance_drag"],
            "canonical_symbols": ["SQQQ", "SOXS", "SPXS"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "bond_etf",
            "theme_name": "Bond ETF",
            "description": "Fixed income ETFs covering treasuries, IG, and HY corporates.",
            "canonical_symbols": [],
            "proxy_symbols": ["TLT", "HYG", "LQD", "SGOV", "BIL", "VCLT", "VCIT"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "commodity_etf_gold",
            "theme_name": "Gold Commodity ETF",
            "description": "Physical gold ETFs — inflation hedge, FX reserve diversification.",
            "supply_chain_role": "gold_spot_exposure",
            "risk_flags": ["opportunity_cost_vs_rates"],
            "canonical_symbols": [],
            "proxy_symbols": ["GLD"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "commodity_etf_oil",
            "theme_name": "Crude Oil Commodity ETF",
            "description": "Crude oil futures ETF — supply shock and geopolitical hedge.",
            "supply_chain_role": "oil_spot_proxy",
            "risk_flags": ["contango_roll_cost"],
            "canonical_symbols": [],
            "proxy_symbols": ["USO"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "commodity_etf_silver",
            "theme_name": "Silver Commodity ETF",
            "description": "Physical silver ETF — industrial + monetary hybrid.",
            "canonical_symbols": [],
            "proxy_symbols": ["SLV"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "crypto_etf",
            "theme_name": "Crypto ETF",
            "description": "Spot and futures-based Bitcoin and Ethereum ETFs.",
            "supply_chain_role": "regulated_crypto_exposure",
            "risk_flags": ["crypto_vol", "regulatory_uncertainty"],
            "canonical_symbols": [],
            "proxy_symbols": ["IBIT", "BITO"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "volatility_product",
            "theme_name": "Volatility Product",
            "description": "VIX-linked ETPs — negative carry in contango, only useful for short-term hedges.",
            "supply_chain_role": "tail_hedge",
            "risk_flags": ["negative_carry", "contango_decay", "unsuitable_for_long_hold"],
            "canonical_symbols": ["UVXY", "VXX"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "quantum_computing",
            "theme_name": "Quantum Computing",
            "description": "Early-stage quantum hardware and software companies — binary technology milestones, long time-to-commercial-scale.",
            "supply_chain_role": "next_generation_compute",
            "risk_flags": ["pre_revenue_binary_risk", "decoherence_engineering_challenge", "long_commercialisation_timeline"],
            "canonical_symbols": ["IONQ", "QBTS", "RGTI"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "space_launch",
            "theme_name": "Space Launch & Satellite",
            "description": "Commercial space launch, satellite connectivity, and space-as-infrastructure — new market formation.",
            "supply_chain_role": "space_infrastructure",
            "risk_flags": ["launch_failure_risk", "regulatory_spectrum", "capex_intensity"],
            "canonical_symbols": ["RKLB", "ASTS", "ONDS"],
            "proxy_symbols": [],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "restaurants_qsr",
            "theme_name": "Restaurants & Quick Service",
            "description": "QSR and fast-casual restaurant chains — franchised asset-light models, stable traffic.",
            "supply_chain_role": "consumer_staples_adjacent",
            "risk_flags": ["labour_cost_inflation", "commodity_food_cost"],
            "canonical_symbols": ["MCD"],
            "proxy_symbols": ["XLY"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "ecommerce_marketplace",
            "theme_name": "E-Commerce Marketplace",
            "description": "Online retail marketplaces and platforms — GMV growth, logistics network, third-party seller ecosystem.",
            "supply_chain_role": "retail_distribution_platform",
            "risk_flags": ["logistics_cost", "regulatory_antitrust", "competition_from_temu_shein"],
            "canonical_symbols": ["AMZN", "SHOP", "MELI", "BABA"],
            "proxy_symbols": ["XLY"],
            "source": "reference_data_builder",
        },
        {
            "theme_id": "digital_health",
            "theme_name": "Digital Health & Telehealth",
            "description": "Consumer-facing digital health, telehealth, and at-home diagnostics — disrupting traditional care delivery.",
            "supply_chain_role": "healthcare_access_disruption",
            "risk_flags": ["reimbursement_uncertainty", "regulatory_prescription_rules"],
            "canonical_symbols": ["HIMS"],
            "proxy_symbols": ["XBI"],
            "source": "reference_data_builder",
        },
        # ---- Meta-overlays (ensure no symbol is dropped) ----
        {
            "theme_id": "emerging_or_unclassified_theme",
            "theme_name": "Emerging or Unclassified Theme",
            "description": "Catch-all for symbols that are real equities but do not yet fit a named theme. Prevents any valid symbol from being dropped from analysis.",
            "supply_chain_role": "unclassified",
            "risk_flags": ["requires_manual_theme_assignment"],
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
            "meta_overlay": True,
        },
        {
            "theme_id": "scanner_only_attention",
            "theme_name": "Scanner-Only Attention",
            "description": "Symbols that appear in scanner output but have no approved intelligence theme. Tracked for coverage-gap evidence only — not admitted to shadow universe without a theme assignment.",
            "supply_chain_role": "scanner_signal_only",
            "risk_flags": ["no_intelligence_context", "theme_required_before_admission"],
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
            "meta_overlay": True,
        },
        {
            "theme_id": "event_driven_special_situation",
            "theme_name": "Event-Driven / Special Situation",
            "description": "Symbols in the system because of a specific catalyst — earnings, M&A, regulatory. Not a structural theme. Eligible for universe while catalyst is active.",
            "supply_chain_role": "event_catalyst",
            "risk_flags": ["event_expiry", "catalyst_failure"],
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
            "meta_overlay": True,
        },
        {
            "theme_id": "unknown_requires_provider_enrichment",
            "theme_name": "Unknown — Requires Provider Enrichment",
            "description": "Symbol cannot be classified from local sources alone. Must be enriched via FMP or another approved provider before a theme can be assigned.",
            "supply_chain_role": "unknown",
            "risk_flags": ["classification_blocked", "no_admission_until_classified"],
            "canonical_symbols": [],
            "proxy_symbols": [],
            "source": "reference_data_builder",
            "meta_overlay": True,
        },
    ]
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "reference_data_builder",
        "theme_count": len(themes),
        "themes": themes,
    }


# ---------------------------------------------------------------------------
# Build symbol_master.json
# ---------------------------------------------------------------------------
def _collect_all_symbols(
    committed: list[str],
    position_research: list[str],
    daily_promoted: list[str],
    favourites: list[str],
    thematic: dict[str, list[str]],
    shadow: list[str],
) -> dict[str, list[str]]:
    """Returns {symbol: [source_labels]}."""
    result: dict[str, list[str]] = {}

    def _add(sym: str, label: str) -> None:
        if sym not in result:
            result[sym] = []
        if label not in result[sym]:
            result[sym].append(label)

    for s in committed:
        _add(s, "committed_universe")
    for s in position_research:
        _add(s, "position_research_universe")
    for s in daily_promoted:
        _add(s, "daily_promoted")
    # favourites tracked as reference only — NOT as discovery source
    for s in favourites:
        _add(s, "favourites_reference_only")
    for theme_id, syms in thematic.items():
        for s in syms:
            _add(s, f"thematic_roster:{theme_id}")
    for s in shadow:
        _add(s, "shadow_universe")

    return result


def _build_symbol_master(all_symbols: dict[str, list[str]]) -> dict:
    records = []
    for symbol, sources in sorted(all_symbols.items()):
        info = _SECTOR_MAP.get(symbol)
        if info:
            sector = info["sector"]
            industry = info["industry"]
            classification_status = "classified_local"
            if sector in _ETF_PROXY_SECTORS:
                classification_status = "etf_proxy_classification"
                if sector == "commodity_proxy":
                    classification_status = "commodity_proxy"
                elif sector == "volatility_proxy":
                    classification_status = "volatility_proxy"
        else:
            sector = "unknown"
            industry = "unknown"
            classification_status = "unknown_requires_provider_enrichment"

        approval = _approval_status(symbol, sector, sources)
        records.append({
            "symbol": symbol,
            "sector": sector,
            "industry": industry,
            "classification_status": classification_status,
            "approval_status": approval,
            "sources": sources,
        })

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "reference_data_builder",
        "symbol_count": len(records),
        "favourites_used_as_discovery": False,
        "live_api_called": False,
        "llm_called": False,
        "env_inspected": False,
        "symbols": records,
    }


# ---------------------------------------------------------------------------
# Build coverage_gap_review.json
# ---------------------------------------------------------------------------
def _build_coverage_gap_review(
    advisory_log_records: list[dict],
    symbol_master: dict,
) -> dict:
    """
    Definitions (corrected per Sprint 7A.1 patch):

    unsupported_current / unresolved:
        Symbols that appear in the current pipeline candidate_matches with
        advisory_status == "advisory_unresolved" — i.e. current pipeline
        candidates that have no advisory/shadow coverage.
        Source: candidate_matches[*].advisory_status == "advisory_unresolved"
        NOT unsupported_current_candidates.symbols (which stores only count,
        not the symbol list, in the advisory_runtime_log format).

    missing_shadow:
        Shadow universe candidates that are not present in the current pipeline.
        Source: missing_shadow_candidates.symbols per record.
    """
    missing_shadow_counter: Counter = Counter()
    unresolved_counter: Counter = Counter()  # advisory_unresolved in candidate_matches

    for r in advisory_log_records:
        # missing_shadow: shadow symbols not in current pipeline
        ms = r.get("missing_shadow_candidates", {})
        if isinstance(ms, dict):
            for sym in ms.get("symbols", []):
                missing_shadow_counter[sym] += 1

        # unresolved: current candidates with no advisory/shadow coverage
        # Read from candidate_matches where advisory_status == advisory_unresolved
        for match in r.get("candidate_matches", []):
            if match.get("advisory_status") == "advisory_unresolved":
                sym = match.get("symbol")
                if sym:
                    unresolved_counter[sym] += 1

    # Evidence quality gate
    n_records = len(advisory_log_records)
    if n_records == 0:
        evidence_status = "insufficient_or_stale_advisory_input"
        required_input_missing = True
    elif n_records < 10:
        evidence_status = "partial_advisory_input"
        required_input_missing = False
    else:
        evidence_status = "sufficient_advisory_input"
        required_input_missing = False

    sym_map = {rec["symbol"]: rec for rec in symbol_master.get("symbols", [])}

    def _gap_entry(sym: str, count: int, counter_type: str) -> dict:
        rec = sym_map.get(sym, {})
        sector = rec.get("sector", "unknown")
        industry = rec.get("industry", "unknown")
        classification = rec.get("classification_status", "unknown_requires_provider_enrichment")

        if classification == "unknown_requires_provider_enrichment":
            action = "needs_provider_enrichment"
        elif sector == "etf_proxy":
            action = "add_to_approved_roster"
        elif sector in _APPROVED_SECTORS:
            action = "add_to_approved_roster"
        else:
            action = "needs_provider_enrichment"

        return {
            "symbol": sym,
            "occurrence_count": count,
            "total_records": n_records,
            "occurrence_rate": round(count / n_records, 3) if n_records else 0.0,
            "counter_type": counter_type,
            "sector": sector,
            "industry": industry,
            "classification_status": classification,
            "recommended_action": action,
        }

    # Recurring threshold: ≥2 appearances if we have ≥10 records; else ≥1
    recur_min = 2 if n_records >= 10 else 1

    recurring_missing_shadow = [
        _gap_entry(sym, cnt, "missing_shadow")
        for sym, cnt in missing_shadow_counter.most_common()
        if cnt >= recur_min
    ]
    recurring_unresolved = [
        _gap_entry(sym, cnt, "unsupported_current_unresolved")
        for sym, cnt in unresolved_counter.most_common()
        if cnt >= recur_min
    ]

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "reference_data_builder",
        "advisory_records_analysed": n_records,
        "evidence_status": evidence_status,
        "required_input_missing": required_input_missing,
        "recurring_missing_shadow_count": len(recurring_missing_shadow),
        "recurring_unsupported_current_count": len(recurring_unresolved),
        "recurring_missing_shadow": recurring_missing_shadow,
        "recurring_unsupported_current": recurring_unresolved,
        "live_api_called": False,
        "llm_called": False,
        "env_inspected": False,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def build(base_dir: str = _HERE) -> None:
    data_dir = os.path.join(base_dir, "data")
    intel_dir = os.path.join(data_dir, "intelligence")
    ref_dir = os.path.join(data_dir, "reference")
    ub_dir = os.path.join(data_dir, "universe_builder")
    log_dir = intel_dir

    os.makedirs(ref_dir, exist_ok=True)

    # 1. Read all local sources
    committed = _read_committed_universe(os.path.join(data_dir, "committed_universe.json"))
    position_research = _read_position_research(os.path.join(data_dir, "position_research_universe.json"))

    promoted_path = os.path.join(data_dir, "daily_promoted.json")
    daily_promoted = _read_daily_promoted(promoted_path) if os.path.exists(promoted_path) else []

    favourites_path = os.path.join(data_dir, "favourites.json")
    favourites = _read_favourites(favourites_path) if os.path.exists(favourites_path) else []

    roster_path = os.path.join(intel_dir, "thematic_roster.json")
    thematic = _read_thematic_roster(roster_path) if os.path.exists(roster_path) else {}

    shadow_path = os.path.join(ub_dir, "active_opportunity_universe_shadow.json")
    shadow = _read_shadow_universe(shadow_path) if os.path.exists(shadow_path) else []

    advisory_log_path = os.path.join(log_dir, "advisory_runtime_log.jsonl")
    advisory_log = _read_advisory_log(advisory_log_path)

    # 2. Collect all symbols
    all_symbols = _collect_all_symbols(
        committed, position_research, daily_promoted,
        favourites, thematic, shadow,
    )

    # 3. Build each output
    sector_schema = _build_sector_schema()
    theme_overlay_map = _build_theme_overlay_map()
    symbol_master = _build_symbol_master(all_symbols)
    coverage_gap_review = _build_coverage_gap_review(advisory_log, symbol_master)

    # 4. Write outputs
    def _write(path: str, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        print(f"  wrote {os.path.relpath(path, base_dir)}")

    _write(os.path.join(ref_dir, "sector_schema.json"), sector_schema)
    _write(os.path.join(ref_dir, "symbol_master.json"), symbol_master)
    _write(os.path.join(ref_dir, "theme_overlay_map.json"), theme_overlay_map)
    _write(os.path.join(intel_dir, "coverage_gap_review.json"), coverage_gap_review)

    # 5. Print summary
    classified = sum(
        1 for r in symbol_master["symbols"]
        if r["classification_status"] != "unknown_requires_provider_enrichment"
    )
    unknown = symbol_master["symbol_count"] - classified
    print(f"\nSymbol master: {symbol_master['symbol_count']} total, "
          f"{classified} classified, {unknown} unknown")
    print(f"Theme overlays: {theme_overlay_map['theme_count']}")
    print(f"Missing shadow recurring: {coverage_gap_review['recurring_missing_shadow_count']}")
    print(f"Unsupported recurring: {coverage_gap_review['recurring_unsupported_current_count']}")
    print("\nSafety: favourites_used_as_discovery=false, live_api_called=false, "
          "llm_called=false, env_inspected=false")


if __name__ == "__main__":
    build()
