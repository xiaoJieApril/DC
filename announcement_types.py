"""Registry of announcement kinds and their supported draft fields."""

ANNOUNCEMENT_TYPES = {
    "crowdfunding": {
        "label": "Crowdfunding",
        "fields": ("draft_illustrator", "draft_funding_goal", "draft_minimum_donation", "draft_donation_url"),
    },
    "offkai": {
        "label": "Offkai",
        "fields": (),
    },
}

DEFAULT_ANNOUNCEMENT_TYPE = "crowdfunding"


def normalize_announcement_type(value):
    value = str(value or DEFAULT_ANNOUNCEMENT_TYPE).strip().lower()
    if value not in ANNOUNCEMENT_TYPES:
        raise ValueError("Unsupported announcement type.")
    return value
