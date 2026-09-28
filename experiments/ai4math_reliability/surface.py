"""Controlled natural-language surface language and paraphrase lexicon.

Everything the experiment needs from natural language lives here: the surface
grammar, a *synonym lexicon*, and the extractor that maps a sentence back to a
feature record.

Why a lexicon with recognised and unrecognised entries?
------------------------------------------------------
The paper studies whether *anchors* -- independently extracted statements about
what a problem means -- can catch a semantics-changing reduction.  A symbolic
anchor extractor can only recognise the surface forms declared in its lexicon.
Modelling that lexicon explicitly lets us measure the coverage/precision
trade-off instead of assuming it away.

* :data:`SURFACE`     -- every surface realisation the generator may emit.
* :data:`RECOGNISED`  -- the subset the extractor understands.  Anything outside
  it is reported as :data:`UNKNOWN` and forces a conservative pipeline to abstain
  rather than guess.

Parsing strategy
----------------
The sentence frame is fixed; the variable slots are not.  Rather than one
ambiguous regular expression (which mis-splits multi-word quantifiers such as
"for every"), the extractor matches the frame and then resolves each slot by
longest-phrase lookup against the lexicon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "UNKNOWN",
    "Features",
    "SURFACE",
    "RECOGNISED",
    "render_text",
    "extract",
    "UnparsedSurface",
    "paraphrase",
    "ALL_QUANTIFIERS",
    "ALL_ADJECTIVES",
    "ALL_BOUNDS",
    "ALL_QUALIFIERS",
]

UNKNOWN = "?"


# --------------------------------------------------------------------------
# Surface realisations.  Index 0 of every list is the *canonical* form emitted by
# the IR->text renderer.  No entry carries leading or trailing whitespace.
# --------------------------------------------------------------------------
SURFACE: Dict[str, Dict[str, List[str]]] = {
    "quantifier": {
        "every": ["every", "each", "for every", "all"],
        "some": ["some", "at least one", "a certain", "one particular"],
    },
    "adjective": {
        "positive": ["positive", "strictly positive", "nonzero"],
        "non_negative": ["non-negative", "nonnegative", "non negative"],
        "any": ["", "arbitrary"],
    },
    "bound": {
        "at_least": ["at least", "no less than", "not less than", "greater than or equal to"],
        "more_than": ["more than", "greater than", "strictly greater than", "in excess of"],
        "at_most": ["at most", "no more than", "not more than", "less than or equal to"],
        "less_than": ["less than", "strictly less than", "below"],
        "equal_to": ["equal to", "exactly", "precisely"],
    },
    "qualifier": {
        "none": [""],
        "multiple_of": [
            "and {v} is a multiple of {m}",
            "and {v} is divisible by {m}",
            "and {v} is an integer multiple of {m}",
        ],
        "not_multiple_of": [
            "and {v} is not a multiple of {m}",
            "and {v} is not divisible by {m}",
        ],
        "even": ["and {v} is even"],
        "odd": ["and {v} is odd"],
    },
}

#: Which surface indices the anchor extractor recognises, per feature value.
RECOGNISED: Dict[str, Dict[str, Tuple[int, ...]]] = {
    "quantifier": {"every": (0, 1), "some": (0, 1)},
    "adjective": {"positive": (0,), "non_negative": (0, 1), "any": (0,)},
    "bound": {
        "at_least": (0, 1),
        "more_than": (0, 1),
        "at_most": (0, 1),
        "less_than": (0,),
        "equal_to": (0, 1),
    },
    "qualifier": {
        "none": (0,),
        "multiple_of": (0, 1),
        "not_multiple_of": (0,),
        "even": (0,),
        "odd": (0,),
    },
}

ALL_QUANTIFIERS = tuple(SURFACE["quantifier"])
ALL_ADJECTIVES = tuple(SURFACE["adjective"])
ALL_BOUNDS = tuple(SURFACE["bound"])
ALL_QUALIFIERS = tuple(SURFACE["qualifier"])


@dataclass(frozen=True)
class Features:
    """The semantic content of a problem as recovered from its surface form."""

    var: str
    lo: int
    hi: int
    quantifier: str
    adjective: str
    bound: str
    bound_k: Optional[int]
    qualifier: str
    qualifier_m: Optional[int]

    def as_anchors(self) -> Dict[str, Optional[str]]:
        """Normalised anchor vector used by typed anchor checks."""
        return {
            "quantifier": self.quantifier,
            "adjective": self.adjective,
            "interval": f"{self.lo}..{self.hi}",
            "bound": self.bound,
            "bound_k": None if self.bound_k is None else str(self.bound_k),
            "qualifier": self.qualifier,
            "qualifier_m": None if self.qualifier_m is None else str(self.qualifier_m),
        }

    def surface_phrases(self) -> Dict[str, str]:
        """Raw canonical phrases, used by the lexical anchor check."""
        q = SURFACE["quantifier"][self.quantifier][0] if self.quantifier != UNKNOWN else UNKNOWN
        adj = SURFACE["adjective"][self.adjective][0] if self.adjective != UNKNOWN else UNKNOWN
        bound = SURFACE["bound"][self.bound][0] if self.bound != UNKNOWN else UNKNOWN
        if self.qualifier == UNKNOWN:
            qual = UNKNOWN
        else:
            qual = (
                SURFACE["qualifier"][self.qualifier][0]
                .replace("{v}", self.var)
                .replace("{m}", str(self.qualifier_m))
                .strip()
            )
        return {
            "quantifier": q,
            "adjective": adj,
            "interval": f"{self.lo}..{self.hi}",
            "bound": bound,
            "bound_k": "" if self.bound_k is None else str(self.bound_k),
            "qualifier": qual,
        }


class UnparsedSurface(ValueError):
    """Raised when a sentence uses a surface form outside the lexicon."""


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _slot(kind: str, value: str, variant: int) -> str:
    if value == UNKNOWN:
        return f"<unknown:{kind}>"
    variants = SURFACE[kind][value]
    return variants[min(variant, len(variants) - 1)]


def render_text(
    features: Features,
    variant: int = 0,
    template: str = "u",
    profile: Optional[Dict[str, int]] = None,
) -> str:
    """Render a feature record back to English.

    ``variant`` selects the surface realisation for every slot; ``profile``
    overrides it per slot.  Canonical (index 0) forms are what the round-trip
    renderer emits, so a lexical comparison against an original sentence written
    with a different synonym is expected to flag a difference.
    """
    def pick(kind: str, value: str) -> str:
        return _slot(kind, value, variant if profile is None else profile.get(kind, variant))

    quant = pick("quantifier", features.quantifier)
    adj = pick("adjective", features.adjective)
    bound = pick("bound", features.bound)
    k = "<unknown:k>" if features.bound_k is None else str(features.bound_k)

    if features.qualifier == UNKNOWN:
        qual = " and <unknown:qualifier>"
    elif features.qualifier == "none":
        qual = ""
    else:
        qual = " " + _slot("qualifier", features.qualifier, variant if profile is None else profile.get("qualifier", variant))
        qual = qual.replace("{v}", features.var).replace("{m}", str(features.qualifier_m))

    adj_part = f"{adj} " if adj else ""
    if template == "u":
        return (
            f"Is it true that {quant} {adj_part}integer {features.var} in "
            f"{features.lo}..{features.hi} satisfies {features.var} is {bound} {k}{qual}?"
        )
    article = "an" if adj_part[:1].lower() in {"a", "e", "i", "o", "u"} or not adj_part else "a"
    return (
        f"Is there {article} {adj_part}integer {features.var} in {features.lo}..{features.hi} "
        f"such that {features.var} is {bound} {k}{qual}?"
    )


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
_U_FRAME = re.compile(
    r"^Is it true that (?P<head>.+?) (?P<var>[a-z]+) in (?P<lo>-?\d+)\.\.(?P<hi>-?\d+) "
    r"satisfies (?P=var) is (?P<bound>.+?) (?P<k>-?\d+)(?P<qual>.*)\?$"
)
_E_FRAME = re.compile(
    r"^Is there (?:a|an) (?P<head>.+?) (?P<var>[a-z]+) in (?P<lo>-?\d+)\.\.(?P<hi>-?\d+) "
    r"such that (?P=var) is (?P<bound>.+?) (?P<k>-?\d+)(?P<qual>.*)\?$"
)

#: Longest-first so that "for every" is not mistaken for "all"/"every".
_QUANT_PHRASES: Sequence[Tuple[str, str]] = tuple(
    sorted(
        ((phrase, value) for value, variants in SURFACE["quantifier"].items() for phrase in variants),
        key=lambda pair: -len(pair[0]),
    )
)
_ADJ_PHRASES: Sequence[Tuple[str, str]] = tuple(
    sorted(
        ((phrase, value) for value, variants in SURFACE["adjective"].items() for phrase in variants),
        key=lambda pair: -len(pair[0]),
    )
)


def _lookup(kind: str, phrase: str, recognise: bool) -> Optional[str]:
    """Map a whole surface phrase to its feature value, or ``None`` if unknown."""
    table = SURFACE[kind]
    allowed = RECOGNISED[kind] if recognise else {k: tuple(range(len(v))) for k, v in table.items()}
    for value, variants in table.items():
        for idx, real in enumerate(variants):
            if real == phrase and idx in allowed[value]:
                return value
    return None


def _split_head(head: str) -> Tuple[Optional[str], Optional[str]]:
    """Split "<quantifier> <adjective> integer" into its two phrase slots."""
    if not head.endswith(" integer"):
        return None, None
    stem = head[: -len(" integer")]
    for phrase, _value in _QUANT_PHRASES:
        if stem == phrase:
            return phrase, ""
        if stem.startswith(phrase + " "):
            return phrase, stem[len(phrase) + 1 :]
    return None, None


def _parse_qualifier(rest: str, var: str) -> Tuple[str, Optional[int]]:
    rest = rest.strip()
    if not rest:
        return "none", None
    if rest == f"and {var} is even":
        return "even", None
    if rest == f"and {var} is odd":
        return "odd", None
    match = re.fullmatch(
        rf"and {var} is (not )?(?:a multiple of|divisible by|an integer multiple of) (\d+)", rest
    )
    if match:
        negated, modulus = match.group(1), int(match.group(2))
        if negated:
            return "not_multiple_of", modulus
        return "multiple_of", modulus
    return UNKNOWN, None


def extract(text: str, recognise: bool = True) -> Features:
    """Parse a sentence into a :class:`Features` record.

    With ``recognise=True`` (the default) any surface form outside
    :data:`RECOGNISED` degrades to :data:`UNKNOWN`, which is how a symbolic
    extractor signals "outside my declared grammar".  With ``recognise=False``
    the full :data:`SURFACE` table is used -- the *oracle* extractor.
    """
    text = " ".join(text.split())
    match = _U_FRAME.match(text)
    template = "u"
    if match is None:
        match = _E_FRAME.match(text)
        template = "e"
    if match is None:
        raise UnparsedSurface(f"outside controlled grammar: {text!r}")

    quant_phrase, adj_phrase = _split_head(match.group("head"))
    if quant_phrase is None and template == "e":
        quant_phrase, adj_phrase = "some", match.group("head")
    if quant_phrase is None:
        raise UnparsedSurface(f"unreadable domain phrase: {match.group('head')!r}")

    quantifier = _lookup("quantifier", quant_phrase, recognise) if template == "u" else "some"
    adjective = _lookup("adjective", adj_phrase or "", recognise)
    bound = _lookup("bound", match.group("bound").strip(), recognise)
    qualifier, modulus = _parse_qualifier(match.group("qual"), match.group("var"))

    return Features(
        var=match.group("var"),
        lo=int(match.group("lo")),
        hi=int(match.group("hi")),
        quantifier=quantifier if quantifier is not None else UNKNOWN,
        adjective=adjective if adjective is not None else UNKNOWN,
        bound=bound if bound is not None else UNKNOWN,
        bound_k=int(match.group("k")),
        qualifier=qualifier,
        qualifier_m=modulus,
    )


# --------------------------------------------------------------------------
# Paraphrase
# --------------------------------------------------------------------------
def paraphrase(features: Features, variant: int, template: Optional[str] = None) -> str:
    """Re-render ``features`` with a different but synonymous surface form."""
    return render_text(features, variant=variant, template=template or "u")
