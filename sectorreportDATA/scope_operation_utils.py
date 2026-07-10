from __future__ import annotations

import re


ALLOWED_SCOPE_VALUES = [
    "Agriculture",
    "Animal welfare",
    "Culture",
    "Disability",
    "Drug and Alcohol Addiction",
    "Education",
    "Energy",
    "Environmental Conservation",
    "Health",
    "HIV/AIDS awareness/mitigation",
    "Housing and Settlement",
    "ICT",
    "Microfinance",
    "Peace building",
    "Population and Reproductive Health",
    "Promotion of Good Governance",
    "Promotion of Human Rights",
    "Relief/Disaster Management",
    "Relief of Poverty",
    "Road Safety",
    "Sports",
    "Water and Sanitation",
    "Welfare",
]


SECTOR_PATTERNS = {
    "Agriculture": [
        r"\bagricultur",
        r"\bfarm",
        r"\bfarmer",
        r"\bhorticultur",
        r"\blivestock",
        r"\bcrop",
        r"\bfood production\b",
    ],
    "Animal welfare": [
        r"\banimal",
        r"\bveterinar",
        r"\bwildlife",
        r"\bpet\b",
    ],
    "Culture": [
        r"\bcultur",
        r"\bheritage",
        r"\bart\b",
        r"\barts\b",
        r"\bcreative\b",
        r"\btradition",
    ],
    "Disability": [
        r"\bdisab",
        r"\bspecial needs\b",
        r"\binclusion\b",
    ],
    "Drug and Alcohol Addiction": [
        r"\bdrug",
        r"\bsubstance",
        r"\balcohol",
        r"\baddiction",
        r"\brehabilit",
    ],
    "Education": [
        r"\beducat",
        r"\bschool",
        r"\bliteracy",
        r"\blearning",
        r"\bscholarship",
        r"\bvocational",
        r"\btraining\b",
    ],
    "Energy": [
        r"\benergy\b",
        r"\bsolar\b",
        r"\belectric",
        r"\bpower\b",
        r"\blight",
        r"\blighting\b",
        r"\blightening\b",
    ],
    "Environmental Conservation": [
        r"\benvironment",
        r"\bconservation",
        r"\bclimate",
        r"\bforest",
        r"\becosystem",
        r"\bpollution",
        r"\bwetland",
        r"\brestore\b",
        r"\brestoration\b",
    ],
    "Health": [
        r"\bhealth\b",
        r"\bhealth care\b",
        r"\bhealthcare\b",
        r"\bmedical\b",
        r"\bpatient\b",
        r"\bcancer\b",
        r"\beczema\b",
        r"\bskin\b",
        r"\bmental health\b",
        r"\bnutrition\b",
        r"\bdisease\b",
        r"\bclinic\b",
        r"\bhospital\b",
    ],
    "HIV/AIDS awareness/mitigation": [
        r"\bhiv\b",
        r"\baids\b",
        r"\bawareness\b",
        r"\bmitigation\b",
        r"\bprevention\b",
        r"\bart\b",
    ],
    "Housing and Settlement": [
        r"\bhousing\b",
        r"\bshelter\b",
        r"\bsettlement\b",
        r"\bhomeless\b",
    ],
    "ICT": [
        r"\bict\b",
        r"\bdigital\b",
        r"\bcomputer\b",
        r"\binternet\b",
        r"\btechnology\b",
        r"\btech\b",
    ],
    "Microfinance": [
        r"\bmicro[- ]finance\b",
        r"\bmicrocredit\b",
        r"\bloan\b",
        r"\bsavings?\b",
        r"\bstart[- ]up\b",
        r"\bstartup\b",
        r"\bcapital\b",
        r"\bentrepreneur",
    ],
    "Peace building": [
        r"\bpeace\b",
        r"\bconflict\b",
        r"\bcohesion\b",
        r"\breconciliation\b",
    ],
    "Population and Reproductive Health": [
        r"\bpopulation\b",
        r"\breproductive health\b",
        r"\bsexual reproductive\b",
        r"\bfamily planning\b",
        r"\bmaternal\b",
        r"\breproductive\b",
    ],
    "Promotion of Good Governance": [
        r"\bgovernance\b",
        r"\bgood governance\b",
        r"\bdevolved\b",
        r"\baccountability\b",
        r"\btransparency\b",
        r"\bdemocracy\b",
        r"\bleadership\b",
        r"\bpolicy\b",
        r"\bcivic\b",
    ],
    "Promotion of Human Rights": [
        r"\bhuman rights\b",
        r"\brights\b",
        r"\bjustice\b",
        r"\bequality\b",
        r"\bdignity\b",
        r"\blegal aid\b",
        r"\badvoca",
        r"\bgbv\b",
        r"\bsexual violence\b",
        r"\bprotect",
    ],
    "Relief/Disaster Management": [
        r"\brelief\b",
        r"\bdisaster\b",
        r"\bemergency\b",
        r"\bhumanitarian\b",
        r"\bflood\b",
        r"\bdrought\b",
        r"\brespond\b",
        r"\bresponse\b",
        r"\brefugee\b",
        r"\bdisplaced\b",
    ],
    "Relief of Poverty": [
        r"\bpoverty\b",
        r"\blivelihood\b",
        r"\beconomic empowerment\b",
        r"\bself-reliance\b",
        r"\bincome generation\b",
        r"\bcommunity development\b",
        r"\bsocio-economic\b",
        r"\balleviat",
        r"\bhunger\b",
        r"\bneedy\b",
        r"\bopportunity\b",
    ],
    "Road Safety": [
        r"\broad safety\b",
        r"\btraffic\b",
    ],
    "Sports": [
        r"\bsport",
        r"\bgame",
        r"\bathlet",
        r"\btalent\b",
    ],
    "Water and Sanitation": [
        r"\bwater\b",
        r"\bsanitation\b",
        r"\bhygiene\b",
        r"\bwash\b",
    ],
    "Welfare": [
        r"\bwelfare\b",
        r"\bwellbeing\b",
        r"\bwell-being\b",
        r"\bwell being\b",
        r"\bvulnerable\b",
        r"\borphan",
        r"\bchildren\b",
        r"\bchild\b",
        r"\byouth\b",
        r"\bfamily\b",
        r"\bold age\b",
        r"\belderly\b",
        r"\bsocial protection\b",
    ],
}


def normalize_text(text: str) -> str:
    normalized = (text or "").lower()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9\s/-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def extract_scope_labels(objective: str) -> list[str]:
    text = normalize_text(objective)
    if not text:
        return []

    matches: list[str] = []
    for label in ALLOWED_SCOPE_VALUES:
        patterns = SECTOR_PATTERNS.get(label, [])
        if any(re.search(pattern, text) for pattern in patterns):
            matches.append(label)

    ordered: list[str] = []
    seen: set[str] = set()
    for label in matches:
        if label not in seen:
            ordered.append(label)
            seen.add(label)
    return ordered


def format_scope_labels(objective: str) -> str:
    return ", ".join(extract_scope_labels(objective))


def normalize_scope_value_for_report(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return ["Other"]

    parts = [item.strip() for item in raw.split(",") if item.strip()]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in parts:
        if item in ALLOWED_SCOPE_VALUES and item not in seen:
            cleaned.append(item)
            seen.add(item)

    if cleaned:
        return cleaned
    return ["Other"]
