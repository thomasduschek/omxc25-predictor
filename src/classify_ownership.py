"""
Classification of ownership positions for free-float assessment.

This module deliberately separates:

1. factual ownership observations;
2. owner classification;
3. the final free-float decision.

Unknown cases are sent to REVIEW rather than guessed.
"""

import re


# ---------------------------------------------------------
# OWNER NORMALISATION
# ---------------------------------------------------------

def normalize_owner(owner):
    if not owner:
        return None

    owner = " ".join(str(owner).split()).strip()
    return owner


# ---------------------------------------------------------
# KNOWN OWNER TYPES
# ---------------------------------------------------------

OWNER_RULES = [
    {
        "patterns": [
            r"\barbejdmarkedets tillægspension\b",
            r"\batp\b",
        ],
        "owner_type": "pension_fund",
        "include_in_float": True,
        "reason": (
            "Pension fund; non-strategic investor under "
            "Nasdaq Alternate Float."
        ),
    },
    {
        "patterns": [
            r"\bubs group\b",
        ],
        "owner_type": "fund_asset_manager",
        "include_in_float": True,
        "reason": (
            "Financial asset manager; treated as non-strategic "
            "portfolio ownership."
        ),
    },
    {
        "patterns": [
            r"\bblackrock\b",
        ],
        "owner_type": "fund_asset_manager",
        "include_in_float": True,
        "reason": (
            "Fund/asset manager; non-strategic investor under "
            "Nasdaq Alternate Float."
        ),
    },
    {
        "patterns": [
            r"\bcapital group companies\b",
        ],
        "owner_type": "fund_asset_manager",
        "include_in_float": True,
        "reason": (
            "Fund/asset manager; non-strategic investor under "
            "Nasdaq Alternate Float."
        ),
    },
    {
        "patterns": [
            r"\bagility group\b",
        ],
        "owner_type": "non_strategic_owner",
        "include_in_float": True,
        "reason": (
            "DSV reports 100% free float while listing Agility "
            "as a disclosed >5% shareholder."
        ),
    },
    {
        "patterns": [
            r"\bdanske bank\b",
        ],
        "owner_type": "financial_institution",
        "include_in_float": True,
        "reason": (
            "Financial institution holding; treated as "
            "non-strategic unless contrary evidence exists."
        ),
    },
    {
        "patterns": [
            r"\ba\.?p\.?\s*m[oø]ller holding\b",
            r"\ba\.?p\.?\s*m[oø]ller holding group\b",
        ],
        "owner_type": "controlling_shareholder",
        "include_in_float": False,
        "reason": (
            "Holding exceeds 10% and owner is not a Nasdaq "
            "non-strategic investor type; classified as "
            "controlling shareholder under Alternate Float."
        ),
    },
]

def classify_owner(owner):
    owner = normalize_owner(owner)

    if not owner:
        return {
            "owner_type": "unknown",
            "include_in_float": None,
            "classification_method": "automatic",
            "classification_reason": "Owner name missing.",
            "classification_status": "REVIEW",
        }

    for rule in OWNER_RULES:
        for pattern in rule["patterns"]:
            if re.search(
                pattern,
                owner,
                flags=re.IGNORECASE,
            ):
                return {
                    "owner_type": rule["owner_type"],
                    "include_in_float": rule["include_in_float"],
                    "classification_method": "rule",
                    "classification_reason": rule["reason"],
                    "classification_status": "CLASSIFIED",
                }

    return {
        "owner_type": "unknown",
        "include_in_float": None,
        "classification_method": "automatic",
        "classification_reason": (
            "No reliable automatic owner classification rule matched."
        ),
        "classification_status": "REVIEW",
    }


# ---------------------------------------------------------
# DATABASE PROCESSOR
# ---------------------------------------------------------

def classify_ownership_positions(con):
    rows = con.execute(
        """
        SELECT
            as_of_date,
            ticker,
            owner,
            source
        FROM ownership_positions
        WHERE classification_status IS NULL
           OR classification_status = 'REVIEW'
        ORDER BY as_of_date, ticker, owner
        """
    ).fetchall()

    classified = 0
    review = 0

    for (
        as_of_date,
        ticker,
        owner,
        source,
    ) in rows:

        result = classify_owner(owner)

        con.execute(
            """
            UPDATE ownership_positions
            SET
                owner_type = ?,
                include_in_float = ?,
                classification_method = ?,
                classification_reason = ?,
                classification_status = ?
            WHERE as_of_date = ?
              AND ticker = ?
              AND owner = ?
              AND source = ?
            """,
            [
                result["owner_type"],
                result["include_in_float"],
                result["classification_method"],
                result["classification_reason"],
                result["classification_status"],
                as_of_date,
                ticker,
                owner,
                source,
            ],
        )

        if result["classification_status"] == "CLASSIFIED":
            classified += 1
        else:
            review += 1

    return {
        "processed": len(rows),
        "classified": classified,
        "review": review,
    }
