#!/usr/bin/env python3
"""Infer and fill fund source categories for donor/source workbooks.

Default usage:
    /usr/bin/python mapping_fund_source_category.py \
        --input UNSPECIFIED.xlsx \
        --output unspecified_refixed2.xlsx

The script uses rule-based NLP and fuzzy matching over the name column to infer
categories from the official options list.
"""

from __future__ import annotations

import argparse
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


CATEGORY_OPTIONS = [
    "Research / Academic Institution",
    "Agency of Kenya Government",
    "National Government",
    "County Government",
    "United Nations Agency",
    "Individual Donors in Kenya / Foreign",
    "Embassy/High Commission",
    "Foundation/Trust",
    "Headquarter of this PBO",
    "Directors' Contributions",
    "Membership Subscription",
    "Returns from investments (e.g., dividends & interest)",
    "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
    "Non-Profit Organizations (PBOs / FBOs)",
    "Corporate Donors",
    "Foundations",
    "Affiliate / Parent of the PBO",
    "Religious Institutions",
    "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "Returns From Investments(eg dividends and interest)",
    "Others (Specify)",
]


# Canonical labels used by the inference engine.
INDIVIDUAL_CATEGORY = "Individual Donors in Kenya / Foreign"
RETURNS_FROM_INVESTMENTS = "Returns From Investments(eg dividends and interest)"
OTHER_SPECIFY = "Others (Specify)"


CATEGORY_ALIASES = {
    "DIRECTORS CONTRIBUTION": "Directors' Contributions",
    "DIRECTORS CONTRIBUTIONS": "Directors' Contributions",
    "DIRECTOR CONTRIBUTION": "Directors' Contributions",
    "MEMBERS SUBSCRIPTION": "Membership Subscription",
    "MEMBER SUBSCRIPTION": "Membership Subscription",
    "RETURNS FROM INVESTMENTS (E.G., DIVIDENDS & INTEREST)": "Returns from investments (e.g., dividends & interest)",
    "RETURNS FROM INVESTMENTS(EG DIVIDENDS AND INTEREST)": RETURNS_FROM_INVESTMENTS,
    "RETURNS FROM INVESTMENTS(EG DIVIDENDS & INTEREST)": RETURNS_FROM_INVESTMENTS,
    "NON PROFIT ORGANIZATIONS (PBOS / FBOS)": "Non-Profit Organizations (PBOs / FBOs)",
    "NON-PROFIT ORGANIZATIONS (NGOS / FBOS)": "Non-Profit Organizations (PBOs / FBOs)",
    "HEADQUARTER OF THIS NGO": "Headquarter of this PBO",
    "HEADQUARTERS OF THIS NGO": "Headquarter of this PBO",
    "OTHER": OTHER_SPECIFY,
}

EXPLICIT_NAME_PATTERNS = {
    "WORLD FOOD PROGRAMME": "United Nations Agency",
    "WORLD FOOD PROGRAM": "United Nations Agency",
    "UNITEDNATIONS DEVELOPMENT PROGRAMME": "United Nations Agency",
    "UNITED NATIONS DEVELOPMENT PROGRAMME": "United Nations Agency",
    "UNITED NATIONS DEVELOPMENT PROGRAM": "United Nations Agency",
    "INTERNATIONAL LABOUR ORGANIZATION": "United Nations Agency",
    "INTERNATIONAL ORGANIZATION OF IMMIGRATION": "United Nations Agency",
    "INTERNATIONAL ORGANIZATION FOR MIGRATION": "United Nations Agency",
    "IOM": "United Nations Agency",
    "KOREAN INTERNATIONAL COOPERATION GENCY": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
    "KOREAN INTERNATIONAL COOPERATION AGENCY": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
    "ACTION AGAINST HUNGER": "Non-Profit Organizations (PBOs / FBOs)",
    "GLOBAL METHANE HUB": "Non-Profit Organizations (PBOs / FBOs)",
    "WINDWARD FUND": "Non-Profit Organizations (PBOs / FBOs)",
    "3STRANDS INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "APDK": "Non-Profit Organizations (PBOs / FBOs)",
    "SIL INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "INTERNATIONAL CHILD RESOURCE INSTITUTE": "Non-Profit Organizations (PBOs / FBOs)",
    "KUFIKIA INTERNATIONAL AMERICA": "Non-Profit Organizations (PBOs / FBOs)",
    "HELPAGE INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "HFHI": "Non-Profit Organizations (PBOs / FBOs)",
    "HABITAT FOR HUMANITY": "Non-Profit Organizations (PBOs / FBOs)",
    "PLAN INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "MERCY CORPS": "Non-Profit Organizations (PBOs / FBOs)",
    "CONCERN WORLD": "Non-Profit Organizations (PBOs / FBOs)",
    "CONCERN WORL WIDE": "Non-Profit Organizations (PBOs / FBOs)",
    "SAVE THE CHILDREN": "Non-Profit Organizations (PBOs / FBOs)",
    "CHILD FUND": "Non-Profit Organizations (PBOs / FBOs)",
    "CURE INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "REACH US INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "STICHING PHARMACCESS INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "PHARMACCESS INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
    "THE SAMBURU PROJECT": "Non-Profit Organizations (PBOs / FBOs)",
    "TRADEMARK EAST AFRICA": "Non-Profit Organizations (PBOs / FBOs)",
    "TMEA": "Non-Profit Organizations (PBOs / FBOs)",
    "OTHER INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHER INCOMES": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHERS INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHE INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "RENT INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "SALES": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "DISPOSALS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "FUNDRAISING": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "IGA": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "IGA(INCOME GENERATING ACTIVITY)": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "INCOME GENERATING ACTIVITY": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "INCOME GENERATING ACTIVITIES": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "SUNDRY INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "BASE INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHER- EXCHANGE GAIN": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHERS- EXCHANGE GAIN": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHER INCOME-FOREX GAINS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHER- DECREASE IN PROVISIONS FOR ACCRUED STAFF LEAVE": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "OTHER - ASSET DISPOSAL": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "CASH PROCEEDS FROM DISPOSAL OF ASSETS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "TRAINING CONSULTANCY": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "CONSULTANCY": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    "DIVIDENT": "Returns From Investments(eg dividends and interest)",
    "INVESTMENTS": "Returns From Investments(eg dividends and interest)",
    "DONATION": "Individual Donors in Kenya / Foreign",
    "DONATIONS": "Individual Donors in Kenya / Foreign",
    "LOCAL DONORS": "Individual Donors in Kenya / Foreign",
    "LOCAL AND INDIVIDUAL": "Individual Donors in Kenya / Foreign",
    "WELLWISHERS": "Individual Donors in Kenya / Foreign",
    "OTHER WELL WISHERS": "Individual Donors in Kenya / Foreign",
    "FRIENDS": "Individual Donors in Kenya / Foreign",
    "INDIVIDUALS": "Individual Donors in Kenya / Foreign",
    "INDIVIDUAL (LOCAL)": "Individual Donors in Kenya / Foreign",
    "INDIDUAL DONORS": "Individual Donors in Kenya / Foreign",
    "BOARD": "Directors' Contributions",
    "AAF STAFF": "Directors' Contributions",
    "NEEMA PHARMACY LIMITED": "Corporate Donors",
    "AAR HEALTH LTD": "Corporate Donors",
    "RFH SPECIALISTS HOSPITAL LTD": "Corporate Donors",
    "MM,M LTD": "Corporate Donors",
    "TTAPK INVESTMENT LTD": "Corporate Donors",
    "GA INSUARANCE LTD": "Corporate Donors",
    "GA INSURANCE LTD": "Corporate Donors",
    "ABSA BANK": "Corporate Donors",
    "NCBA BANK": "Corporate Donors",
    "AGHA KAN HEALTH SERVICES": "Corporate Donors",
        "COST GENERAL TEACHING AND REFERRAL HOSPITALS": "Agency of Kenya Government",
        # ── New training entries ──────────────────────────────────────────────────
        # United Nations Agency
        "FAO": "United Nations Agency",
        # Foreign Government Agency (add major abbreviations here so they take
        # precedence over any generic substring like "GRANT")
        "USAID": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "SIDA": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "NORAD": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "FCDO": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "DFID": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "JICA": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "GIZ": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "US FED, UN CONTRN & LOCAL GOVT GRANTS": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "USA": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        "AUSTRALIA": "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
        # Non-Profit Organizations (specific/longer patterns first to avoid partial shadowing)
        "HEALTHRIGHT INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
        "FREEDOM FUND AND DESTINY RESCUE": "Non-Profit Organizations (PBOs / FBOs)",
        "PLAIN INTERNATIONAL": "Non-Profit Organizations (PBOs / FBOs)",
        "TUMAINI MILES OF SMILES AUSTRALIA": "Non-Profit Organizations (PBOs / FBOs)",
        "127 WORLDWIDE": "Non-Profit Organizations (PBOs / FBOs)",
        "ACHAP PFIZER PROJECT": "Non-Profit Organizations (PBOs / FBOs)",
        "ACHAP ACCORD PROJECT": "Non-Profit Organizations (PBOs / FBOs)",
        "ACHAP": "Non-Profit Organizations (PBOs / FBOs)",
        "OXFAM": "Non-Profit Organizations (PBOs / FBOs)",
        "ACTED": "Non-Profit Organizations (PBOs / FBOs)",
        "Y-CARE": "Non-Profit Organizations (PBOs / FBOs)",
        "CROSS BORDER COMMUNITY RESILIENCE": "Non-Profit Organizations (PBOs / FBOs)",
        "INTERNATIONAL CONSERVATION FUND FOR CANADA": "Non-Profit Organizations (PBOs / FBOs)",
        "MIFF": "Non-Profit Organizations (PBOs / FBOs)",
        "HFHN STICHING OP EIGEN WIEKEN": "Non-Profit Organizations (PBOs / FBOs)",
        "MT.ELGON ELEPHANT PROJECTS": "Non-Profit Organizations (PBOs / FBOs)",
        "NETHERLAND COMMISSION FOR ENVIRONMENTAL ASSESSMENT": "Non-Profit Organizations (PBOs / FBOs)",
        "NETHERLAND COMMISION FOR ENVIRONMENTAL ASSESSMENT": "Non-Profit Organizations (PBOs / FBOs)",
        "WTS": "Non-Profit Organizations (PBOs / FBOs)",
        "CONTRIBUTION FROM PARTNERS IN KENYA": "Non-Profit Organizations (PBOs / FBOs)",
        "RIDEWORD": "Non-Profit Organizations (PBOs / FBOs)",
        "WYI": "Non-Profit Organizations (PBOs / FBOs)",
        "BRIDGE PROJECT": "Non-Profit Organizations (PBOs / FBOs)",
        "ROOTS": "Non-Profit Organizations (PBOs / FBOs)",
        "CSI": "Non-Profit Organizations (PBOs / FBOs)",
        "QPNZ": "Non-Profit Organizations (PBOs / FBOs)",
        "EMPOWERMENT": "Non-Profit Organizations (PBOs / FBOs)",
        "INSIGHT SHARE": "Non-Profit Organizations (PBOs / FBOs)",
        "MICCSOF": "Non-Profit Organizations (PBOs / FBOs)",
        "COALITION": "Non-Profit Organizations (PBOs / FBOs)",
        # Corporate Donors
        "EAST AFRICA BREWERIES LIMITED": "Corporate Donors",
        "EAST AFRICA BREWERIES": "Corporate Donors",
        "ABSA BUSIA BANK": "Corporate Donors",
        "NCBA": "Corporate Donors",
        # Agency of Kenya Government
        "KENYA FOREST SERVICES": "Agency of Kenya Government",
        # Membership Subscription
        "OTHER SECRETARIAT MEMBERSHIP": "Membership Subscription",
        "MEMBERS": "Membership Subscription",
        # Headquarter of this PBO
        "SECRETARIAT": "Headquarter of this PBO",
        # Returns from Investments
        "INVESTMENT INCOME": "Returns From Investments(eg dividends and interest)",
        "INTREST": "Returns From Investments(eg dividends and interest)",
        "WHT": "Returns From Investments(eg dividends and interest)",
        # NGOs Self Generated Income
        "NIGERIA PROJECT": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "MENTAL HEALTH PROJECT": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "SERVICES": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "OTHER GENERAL": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "ORGANIZATION STRENGTHENING": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "RENTAL INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "MISC INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "DEFFERED INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "OTHERINCOMES": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "HEALTHCARE": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "COST- SHARING PROJEVT": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "BORROWINGS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "ADMIN INCOME COSTS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "OTHERS(INCL EXCHANGE GAIN/LOSS)": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "MENSTRUAL MATTHERS WALK": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "MICROFINANCE": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "SUPPLIERS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "CROWDFUNDING": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "CANCER AWERENESS WALK": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "EDUCATION": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "PROGRAMM": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "VOCATIONAL CENTRE INCOME": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "CONSULTANCES": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "MISCELLENEOUS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "SELF": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "PASSBOOKS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "PASTBOOKS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        # Individual Donors
        "REMITTANCES": "Individual Donors in Kenya / Foreign",
        "INDIVIDUAL": "Individual Donors in Kenya / Foreign",
        "FOTA": "Individual Donors in Kenya / Foreign",
        "KELIAHIFE": "Individual Donors in Kenya / Foreign",
        "FORDERSEREM": "Individual Donors in Kenya / Foreign",
        "ARRACELLI": "Individual Donors in Kenya / Foreign",
        "SAIF": "Individual Donors in Kenya / Foreign",
        "CONTRIBUTIONS": "Individual Donors in Kenya / Foreign",
        "LOCAL": "Individual Donors in Kenya / Foreign",
        "VOLLONTERESSO": "Individual Donors in Kenya / Foreign",
        "NET DEFFERED RECEIPTS FROM DONORS": "Individual Donors in Kenya / Foreign",
        "DESIP": "Individual Donors in Kenya / Foreign",
        "BRITTANY": "Individual Donors in Kenya / Foreign",
        "DIKUNIE": "Individual Donors in Kenya / Foreign",
        "COMMUNITY": "Individual Donors in Kenya / Foreign",
        "MARIA": "Individual Donors in Kenya / Foreign",
        "RUBY": "Individual Donors in Kenya / Foreign",
        "JIMAYODO": "Individual Donors in Kenya / Foreign",
        "NIMROD": "Individual Donors in Kenya / Foreign",
        "VARIOUS": "Individual Donors in Kenya / Foreign",
        "PUBLIC": "Individual Donors in Kenya / Foreign",
        "DREAMS": "Individual Donors in Kenya / Foreign",
        # Generic project/programme labels — must stay last so specific patterns above win
        "PROJECT": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
        "PROJECTS": "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)",
    }


# Keyword dictionaries to support NLP-style weak supervision by category.
CATEGORY_KEYWORDS = {
        "Individual Donors in Kenya / Foreign": [
            "individual donor",
            "individual donors",
            "personal donation",
            "private donor",
            "philanthropic individual",
        ],
    "Research / Academic Institution": [
        "research",
        "university",
        "college",
        "academy",
        "academic",
        "institute",
    ],
    "Agency of Kenya Government": ["ministry", "authority", "kenya government", "state department", "government agency"],
    "National Government": ["national government", "government of kenya", "republic of kenya"],
    "County Government": ["county government", "county", "governor", "county assembly"],
    "United Nations Agency": ["unicef", "undp", "who", "unfpa", "wfp", "un women", "unhcr", "united nations"],
    "Embassy/High Commission": ["embassy", "high commission", "consulate"],
    "Foundation/Trust": ["foundation", "trust", "charitable trust"],
    "Headquarter of this PBO": ["headquarter", "head office", "hq"],
    "Directors' Contributions": ["director", "director contribution", "directors contribution", "board contribution"],
    "Membership Subscription": ["membership subscription", "member subscription", "subscription"],
    "Returns from investments (e.g., dividends & interest)": ["dividend", "investment return", "interest income"],
    "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)": [
        "usaid",
        "sida",
        "norad",
        "fcdo",
        "dfid",
        "jica",
        "giz",
        "eu delegation",
        "uk aid",
        "foreign government",
        "foreign embassy",
        "high commission",
        "embassy",
    ],
    "Non-Profit Organizations (PBOs / FBOs)": ["ngo", "non profit", "pbo", "fbo", "charity", "civil society"],
    "Corporate Donors": ["limited", "ltd", "plc", "company", "bank", "telecom", "corporate", "inc"],
    "Foundations": ["foundation"],
    "Affiliate / Parent of the PBO": ["affiliate", "parent organization", "head office", "sister organization"],
    "Religious Institutions": ["church", "mosque", "diocese", "parish", "ministry", "temple"],
    "NGOs Self Generated Income (eg Consultancy services, Farming & Business Income)": [
        "consultancy",
        "farming",
        "business income",
        "self generated",
        "service income",
    ],
    RETURNS_FROM_INVESTMENTS: [
        "bank interest",
        "interest",
        "dividend",
        "fixed deposit",
        "treasury bill",
        "investment return",
    ],
}


FOREIGN_GOVERNMENT_TOKENS = {
    "USAID",
    "SIDA",
    "NORAD",
    "UK AID",
    "DFID",
    "FCDO",
    "JICA",
    "GIZ",
    "FOREIGN GOVERNMENT",
    "EMBASSY",
    "HIGH COMMISSION",
}

UN_TOKENS = {
    "UNITED NATIONS",
    "UNICEF",
    "UNHCR",
    "UN WOMEN",
    "UNDP",
    "UNFPA",
    "WFP",
    "WHO",
    "IOM",
}

RELIGIOUS_TOKENS = {
    "CHURCH",
    "MOSQUE",
    "DIOCESE",
    "PARISH",
    "MINISTRY",
    "TEMPLE",
}

CORPORATE_TOKENS = {
    " LTD",
    "LIMITED",
    "PLC",
    "INC",
    "BANK",
    "COMPANY",
    "CORPORATION",
    "INSURANCE",
    "BREWERIES",
}

NON_PROFIT_TOKENS = {
    "DAN-CHURCH-AID",
    "DAN CHURCH AID",
    "GLOBAL MISSION FUND",
    "MISSION FUND",
    "NGO",
    "NON PROFIT",
    "PBO",
    "FBO",
    "FOUNDATION",
    "TRUST",
    "CHARITY",
    "AID",
}

EXPLICIT_NON_PROFIT_NAMES = {
    "DAN-CHURCH-AID",
    "DAN CHURCH AID",
    "GLOBAL MISSION FUND",
}


ORGANIZATION_HINTS = {
    "foundation",
    "trust",
    "ministry",
    "church",
    "ltd",
    "limited",
    "company",
    "university",
    "college",
    "agency",
    "government",
    "embassy",
    "commission",
    "programme",
    "program",
    "association",
    "organization",
    "organisation",
    "initiative",
    "project",
    "bank",
    "plc",
    "ngo",
    "pbo",
    "fbo",
}

NON_PERSON_HINTS = {
    "other",
    "income",
    "staff",
    "collective",
    "donor",
    "donors",
    "individual",
    "interest",
    "bank",
    "walk",
    "share",
    "project",
    "program",
    "programme",
    "international",
    "initiative",
    "services",
    "consultancy",
}


NAME_COLUMN_CANDIDATES = ["name", "donor_name", "source_name", "fund_source", "source"]
COUNTRY_COLUMN_CANDIDATES = ["country", "donor_country", "source_country"]
CATEGORY_COLUMN_CANDIDATES = ["category", "donor_category", "source_category"]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def key_text(value: object) -> str:
    text = clean_text(value).replace("\u2019", "'").upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_country_key(raw_country: object) -> str:
    country = key_text(raw_country)
    aliases = {
        "UNITED STATES OF AMERICA(USA)": "UNITED STATES OF AMERICA",
        "UNITED STATES OF AMERICA (USA)": "UNITED STATES OF AMERICA",
        "U.S.A.": "UNITED STATES OF AMERICA",
        "USA": "UNITED STATES OF AMERICA",
        "US": "UNITED STATES OF AMERICA",
        "UNITED KINGDOM (UK)": "UNITED KINGDOM",
        "UK": "UNITED KINGDOM",
        "U.K.": "UNITED KINGDOM",
    }
    return aliases.get(country, country)


def normalize_existing_category(raw_category: object) -> str:
    raw_key = key_text(raw_category)
    if not raw_key:
        return ""

    option_lookup = {key_text(option): option for option in CATEGORY_OPTIONS}
    if raw_key in option_lookup:
        return option_lookup[raw_key]
    if raw_key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[raw_key]

    close = difflib.get_close_matches(raw_key, list(option_lookup.keys()), n=1, cutoff=0.92)
    if close:
        return option_lookup[close[0]]
    return ""


def is_kenya_country(raw_country: object) -> bool:
    country = normalize_country_key(raw_country)
    return country in {"KENYA", "KE", "REPUBLIC OF KENYA"}


def is_foreign_country(raw_country: object) -> bool:
    country = normalize_country_key(raw_country)
    return bool(country) and not is_kenya_country(country)


def infer_category_from_country_name_context(raw_name: object, raw_country: object) -> InferenceResult | None:
    """Country-aware rule layer requested by user for stronger foreign mapping."""
    text = key_text(raw_name)
    if not text:
        return None

    for pattern, category in EXPLICIT_NAME_PATTERNS.items():
        if pattern in text:
            return InferenceResult(category, "explicit-name-pattern")

    if any(token in text for token in EXPLICIT_NON_PROFIT_NAMES):
        return InferenceResult("Non-Profit Organizations (PBOs / FBOs)", "country-context-explicit-nonprofit")

    if any(token in text for token in UN_TOKENS):
        return InferenceResult("United Nations Agency", "country-context-un")

    if any(token in text for token in FOREIGN_GOVERNMENT_TOKENS):
        return InferenceResult(
            "Foreign Government Agency (e.g. SIDA, USAID, NORAD, UK AID, Foreign Embassies / High Commissions)",
            "country-context-foreign-government",
        )

    if any(token in text for token in NON_PROFIT_TOKENS):
        return InferenceResult("Non-Profit Organizations (PBOs / FBOs)", "country-context-nonprofit")

    if any(token in text for token in RELIGIOUS_TOKENS):
        return InferenceResult("Religious Institutions", "country-context-religious")

    if is_foreign_country(raw_country) and any(token in text for token in CORPORATE_TOKENS):
        return InferenceResult("Corporate Donors", "country-context-foreign-corporate")

    return None


def individual_bucket(raw_country: object) -> str:
    return "Kenya" if is_kenya_country(raw_country) else "Foreign"


def looks_like_person_name(raw_name: object) -> bool:
    name = clean_text(raw_name)
    if not name:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'\.-]+", name)
    if len(words) < 2 or len(words) > 4:
        return False

    lower_words = {w.lower() for w in words}
    if lower_words & ORGANIZATION_HINTS:
        return False
    if lower_words & NON_PERSON_HINTS:
        return False
    # Person-like if mostly alphabetic words with title-casing, initials, or
    # all-uppercase names that do not contain organization hints.
    caps_like = 0
    for word in words:
        if re.match(r"^[A-Z][a-z'\.-]+$", word) or re.match(r"^[A-Z]\.$", word):
            caps_like += 1
    if caps_like >= max(1, len(words) - 1):
        return True
    if all(re.match(r"^[A-Z]+$", word) for word in words):
        return True
    return False


@dataclass
class InferenceResult:
    category: str
    reason: str


def infer_category_from_name(raw_name: object, raw_country: object) -> InferenceResult:
    name = clean_text(raw_name)
    text = key_text(raw_name)
    if not text:
        return InferenceResult(OTHER_SPECIFY, "empty-name")

    # Hard rule from user: bank interest goes to Returns From Investments(eg dividends and interest).
    bank_interest_tokens = [
        "BANK INTEREST",
        "BANK INTREST",
        "INTEREST INCOME",
        "FIXED DEPOSIT",
        "FD INTEREST",
        "DIVIDEND",
    ]
    if any(token in text for token in bank_interest_tokens):
        return InferenceResult(RETURNS_FROM_INVESTMENTS, "bank-interest-rule")
    if "INTEREST" in text and ("BANK" in text or len(text.split()) <= 2):
        return InferenceResult(RETURNS_FROM_INVESTMENTS, "interest-rule")

    if "INDIVIDUAL DONOR" in text or "INDIVIDUAL DONORS" in text:
        return InferenceResult(INDIVIDUAL_CATEGORY, "individual-donor-keyword")

    context_result = infer_category_from_country_name_context(raw_name, raw_country)
    if context_result is not None:
        return context_result

    keyword_scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            keyword_key = key_text(keyword)
            if keyword_key and keyword_key in text:
                score += 2 if " " in keyword_key else 1
        if score:
            keyword_scores[category] = score

    if keyword_scores:
        best_category, best_score = max(keyword_scores.items(), key=lambda item: item[1])
        if best_score >= 2:
            return InferenceResult(best_category, "keyword-match")
        if best_score == 1 and best_category in {
            RETURNS_FROM_INVESTMENTS,
            "Directors' Contributions",
            INDIVIDUAL_CATEGORY,
        }:
            return InferenceResult(best_category, "keyword-match-low-threshold")

    # Fuzzy match against labels and aliases for short/noisy strings.
    candidates = {key_text(label): label for label in CATEGORY_OPTIONS}
    candidates.update({alias: label for alias, label in CATEGORY_ALIASES.items()})
    fuzzy = difflib.get_close_matches(text, list(candidates.keys()), n=1, cutoff=0.86)
    if fuzzy:
        return InferenceResult(candidates[fuzzy[0]], "fuzzy-match")

    context_result = infer_category_from_country_name_context(raw_name, raw_country)
    if context_result is not None:
        return context_result

    if looks_like_person_name(name):
        return InferenceResult(
            INDIVIDUAL_CATEGORY,
            f"person-name-fallback:{individual_bucket(raw_country)}",
        )

    return InferenceResult(OTHER_SPECIFY, "unclassified")


def pick_column(columns: list[str], candidates: list[str], label: str) -> str:
    keyed = {key_text(col): col for col in columns}
    for candidate in candidates:
        key = key_text(candidate)
        if key in keyed:
            return keyed[key]
    raise ValueError(f"Could not find a {label} column. Available columns: {columns}")


def map_fund_source_categories(
    input_path: Path,
    output_path: Path | None = None,
    sheet_name: str | int = 0,
) -> dict[str, object]:
    dataframe = pd.read_excel(input_path, sheet_name=sheet_name)
    if dataframe.empty:
        raise ValueError("Input workbook is empty; nothing to map.")

    columns = [str(col) for col in dataframe.columns]
    name_col = pick_column(columns, NAME_COLUMN_CANDIDATES, "name")
    country_col = pick_column(columns, COUNTRY_COLUMN_CANDIDATES, "country")
    category_col = pick_column(columns, CATEGORY_COLUMN_CANDIDATES, "category")

    reasons: list[str] = []
    mapped_values: list[str] = []
    updated_rows = 0

    for _, row in dataframe.iterrows():
        normalized_existing = normalize_existing_category(row.get(category_col))
        if normalized_existing:
            mapped_values.append(normalized_existing)
            reasons.append("normalized-existing")
            if clean_text(row.get(category_col)) != normalized_existing:
                updated_rows += 1
            continue

        result = infer_category_from_name(row.get(name_col), row.get(country_col))
        mapped_values.append(result.category)
        reasons.append(result.reason)
        if clean_text(row.get(category_col)) != result.category:
            updated_rows += 1

    dataframe[category_col] = mapped_values
    dataframe["inference_reason"] = reasons

    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}.mapped{input_path.suffix}")

    dataframe.to_excel(output_path, index=False)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "rows": len(dataframe),
        "updated_rows": updated_rows,
        "name_column": name_col,
        "country_column": country_col,
        "category_column": category_col,
        "category_distribution": dataframe[category_col].value_counts(dropna=False).to_dict(),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Map fund source categories using NLP/fuzzy matching over the name column."
    )
    parser.add_argument("--input", default="category_fundSource.xlsx", help="Input Excel workbook path.")
    parser.add_argument("--output", default=None, help="Output Excel workbook path.")
    parser.add_argument(
        "--sheet",
        default=0,
        help="Sheet name or index (default: first sheet).",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input workbook not found: {input_path}")

    sheet_name: str | int
    if str(args.sheet).isdigit():
        sheet_name = int(args.sheet)
    else:
        sheet_name = str(args.sheet)

    summary = map_fund_source_categories(
        input_path=input_path,
        output_path=Path(args.output) if args.output else None,
        sheet_name=sheet_name,
    )

    print("Fund source mapping complete")
    print(f"Input: {summary['input']}")
    print(f"Output: {summary['output']}")
    print(f"Rows: {summary['rows']}")
    print(f"Updated category rows: {summary['updated_rows']}")
    print("Category distribution:")
    for category, count in summary["category_distribution"].items():
        print(f"  - {category}: {count}")


if __name__ == "__main__":
    main()
