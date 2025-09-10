import re
from enum import Enum


class Intent(str, Enum):
    RETRIEVE = "retrieve"
    OPTIMIZE = "optimize"
    VISUALIZE = "visualize"
    WHATIF = "whatif"
    HELP = "help"


KEYWORDS = {
    Intent.RETRIEVE: [r"retrieve", r"show", r"fetch", r"data", r"fast[- ]?moving"],
    Intent.OPTIMIZE: [r"optimi[sz]e", r"replenish", r"plan", r"model"],
    Intent.VISUALIZE: [r"visuali[sz]e", r"plot", r"chart", r"graph"],
    Intent.WHATIF: [r"what[- ]?if", r"scenario", r"simulate"],
}




def classify_intent(text: str) -> Intent:
    t = text.lower()
    for intent, pats in KEYWORDS.items():
        for p in pats:
            if re.search(p, t):
                return intent
    # simple fallbacks
    if any(x in t for x in ["opt", "lp", "reorder", "order qty"]):
        return Intent.OPTIMIZE
    return Intent.RETRIEVE