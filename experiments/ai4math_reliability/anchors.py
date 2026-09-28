"""Anchors: independently extracted statements about what a problem *means*.

An anchor is a predicate over an IR object, derived from the natural-language
statement rather than from the reductor.  Checking anchors is the only mechanism
in this framework that can, in principle, detect an unfaithful reduction, because
the IR verifier by construction cannot (see ``paper.tex``, Proposition 2).

Two families are implemented and compared:

``A-lex`` (lexical round-trip)
    Render the IR back to English and compare it with the original sentence slot
    by slot using *exact surface equality*.  Simple, and brittle: a synonym the
    renderer does not emit is indistinguishable from a semantic change.

``A-type`` (typed / predicated anchors)
    Turn the extracted anchors into predicates over the IR -- quantifier field,
    declared domain, the *solution set* of the goal over the domain, and the
    presence of a congruence witness -- and evaluate them.  Comparison is
    semantic where semantics is available and structural elsewhere.

Anchors the extractor cannot read are reported as *unconfirmed*.  A conservative
pipeline must then abstain: an unreadable anchor is not a passed anchor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .ir import Comparison, Quantifier, Spec, is_consistent
from .reductor import _BOUND_TO_COMPARISON
from .surface import Features, UNKNOWN, extract, render_text, UnparsedSurface

__all__ = ["AnchorVerdict", "lexical_anchor_check", "typed_anchor_check", "raw_phrases"]


@dataclass(frozen=True)
class AnchorVerdict:
    """Outcome of an anchor check."""

    violated: Tuple[str, ...]
    unconfirmed: Tuple[str, ...]
    vacuous: bool

    @property
    def certifiable(self) -> bool:
        """True iff every anchor was readable and none was violated."""
        return not self.violated and not self.unconfirmed

    @property
    def failed(self) -> bool:
        return bool(self.violated)


# --------------------------------------------------------------------------
# Lexical round-trip anchors
# --------------------------------------------------------------------------
def raw_phrases(text: str) -> Optional[Dict[str, str]]:
    """Split a sentence into its raw slot phrases (no normalisation)."""
    from .surface import _E_FRAME, _U_FRAME, _split_head

    text = " ".join(text.split())
    match = _U_FRAME.match(text)
    template = "u"
    if match is None:
        match = _E_FRAME.match(text)
        template = "e"
    if match is None:
        return None

    head = match.group("head")
    if template == "u":
        quant, adjective = _split_head(head)
        if quant is None:
            return None
    else:
        quant = "some"
        adjective = head[: -len(" integer")] if head.endswith(" integer") else head

    qual = (match.group("qual") or "").strip()
    return {
        "quantifier": quant,
        "adjective": adjective or "",
        "interval": f"{match.group('lo')}..{match.group('hi')}",
        "bound": match.group("bound").strip(),
        "bound_k": match.group("k").strip(),
        "qualifier": qual,
    }


def lexical_anchor_check(original: str, spec: Spec) -> AnchorVerdict:
    """Compare the round-trip rendering of ``spec`` with the original sentence.

    Only slots the IR can actually *express* are compared.  A slot the renderer
    had to mark ``<unknown:...>`` carries no information, so a lexical checker
    confined to the round trip is structurally blind to it -- which is exactly
    how it misses typing drift, whose only trace in the IR is the numeric
    interval, and that interval is reproduced faithfully.
    """
    from .reductor import spec_to_features

    rendered = render_text(spec_to_features(spec))
    left, right = raw_phrases(original), raw_phrases(rendered)
    if left is None or right is None:
        return AnchorVerdict(violated=(), unconfirmed=("parse",), vacuous=False)
    violated = tuple(
        key
        for key in left
        if _UNKNOWN_MARKER not in right[key] and left[key] != right[key]
    )
    return AnchorVerdict(violated=violated, unconfirmed=(), vacuous=_vacuous(spec))


_UNKNOWN_MARKER = "<unknown:"


# --------------------------------------------------------------------------
# Typed anchors
# --------------------------------------------------------------------------
def _goal_solution_set(spec: Spec, var: str) -> Optional[Tuple[int, ...]]:
    """Points of the declared domain satisfying the goal, or None if not single-var."""
    if var not in [n for n, _, _ in spec.variables]:
        return None
    lo, hi = spec.domain_of(var)
    coords = set()
    for term in spec.goal:
        names = term.variables()
        if names == (var,) and term.coeffs[0][1] == 1:
            coords.add((term.comp, term.rhs))
        else:
            return None
    solutions = []
    for x in range(lo, hi + 1):
        if all(comp.holds(x, rhs) for comp, rhs in coords):
            solutions.append(x)
    return tuple(solutions)


def _expected_solution_set(features: Features) -> Optional[Tuple[int, ...]]:
    if features.bound == UNKNOWN or features.bound_k is None:
        return None
    expected_lo, expected_hi = features.lo, features.hi
    if features.adjective == "positive":
        expected_lo = max(expected_lo, 1)
    elif features.adjective == "non_negative":
        expected_lo = max(expected_lo, 0)
    comp = _BOUND_TO_COMPARISON[features.bound]
    return tuple(x for x in range(expected_lo, expected_hi + 1) if comp.holds(x, features.bound_k))


def _vacuous(spec: Spec) -> bool:
    return spec.quantifier is Quantifier.FORALL and not is_consistent(spec)


def typed_anchor_check(original_features: Features, spec: Spec) -> AnchorVerdict:
    """Assert the extracted anchors as predicates over the IR."""
    violated: List[str] = []
    unconfirmed: List[str] = []

    # A1 -- quantifier
    if original_features.quantifier == UNKNOWN:
        unconfirmed.append("quantifier")
    else:
        expect = Quantifier.FORALL if original_features.quantifier == "every" else Quantifier.EXISTS
        if spec.quantifier is not expect:
            violated.append("quantifier")

    # A2 -- declared domain (typing constraints are visible as interval bounds)
    if original_features.adjective == UNKNOWN:
        unconfirmed.append("domain_kind")
    else:
        expected_lo = original_features.lo
        if original_features.adjective == "positive":
            expected_lo = max(expected_lo, 1)
        elif original_features.adjective == "non_negative":
            expected_lo = max(expected_lo, 0)
        actual = spec.domain_of(original_features.var)
        if actual != (expected_lo, original_features.hi):
            violated.append("domain_kind")

    # A3 -- strictness of the goal, compared *semantically* over the domain so
    # that equivalent encodings (e.g. "> 10" vs ">= 11") are not flagged.
    if original_features.bound == UNKNOWN:
        unconfirmed.append("strictness")
    else:
        expected = _expected_solution_set(original_features)
        actual = _goal_solution_set(spec, original_features.var)
        if expected is None or actual is None or expected != actual:
            violated.append("strictness")

    # A4 -- presence and modulus of a qualifying congruence
    if original_features.qualifier == UNKNOWN:
        unconfirmed.append("qualifier")
    else:
        aux_present = len(spec.variables) > 1
        if original_features.qualifier == "none":
            if aux_present:
                violated.append("qualifier")
        else:
            want = 2 if original_features.qualifier == "even" else original_features.qualifier_m
            got: Optional[int] = None
            if aux_present and len(spec.hypotheses) == 1:
                aux_name = spec.variables[1][0]
                got = abs(int(next(c for n, c in spec.hypotheses[0].coeffs if n == aux_name)))
            if got != want:
                violated.append("qualifier")

    return AnchorVerdict(violated=tuple(violated), unconfirmed=tuple(unconfirmed), vacuous=_vacuous(spec))


def extract_for_anchor(text: str, *, recognise: bool = True) -> Tuple[Optional[Features], str]:
    """Extract features for anchor purposes; returns ``(features, note)``."""
    try:
        return extract(text, recognise=recognise), ""
    except UnparsedSurface as exc:
        return None, str(exc)
