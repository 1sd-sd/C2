"""The semantic reduction layer: controlled natural language -> typed IR.

Two things live here:

* ``features_to_spec`` -- the *translation* from a recovered feature record to the
  typed IR.  It takes an explicit ``blind`` set naming the features the reductor
  does not distinguish.  A reductor with a non-empty ``blind`` set is a faithful
  model of an LLM that silently normalises away a distinction it never noticed --
  which is exactly the failure the paper is about.
* ``spec_to_features`` -- the *partial inverse*, used to render an IR back into
  English for round-trip anchor checking.  It can only recover what the IR
  actually encodes; anything the reductor dropped is reported as ``UNKNOWN``.
  The asymmetry between the two directions is the technical crux of the paper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import FrozenSet, Optional, Tuple

from .ir import Comparison, Quantifier, Spec, Term
from .surface import Features, UNKNOWN, UnparsedSurface

__all__ = [
    "ALL_FEATURES",
    "ReductionAbstain",
    "Reduction",
    "features_to_spec",
    "spec_to_features",
    "make_reductor",
]

#: The four feature families a reductor may be blind to.
ALL_FEATURES: FrozenSet[str] = frozenset({"quantifier", "adjective", "strictness", "qualifier"})

_BOUND_TO_COMPARISON = {
    "at_least": Comparison.GE,
    "more_than": Comparison.GT,
    "at_most": Comparison.LE,
    "less_than": Comparison.LT,
    "equal_to": Comparison.EQ,
}
_COMPARISON_TO_BOUND = {v: k for k, v in _BOUND_TO_COMPARISON.items()}


class ReductionAbstain(Exception):
    """The reductor declines to translate (a feature it needs is unknown)."""


@dataclass(frozen=True)
class Reduction:
    """A reduction outcome, with provenance kept *outside* the IR object."""

    spec: Optional[Spec]
    features: Features
    reductor: str
    note: str = ""

    @property
    def abstained(self) -> bool:
        return self.spec is None


def _aux_domain(lo: int, hi: int, modulus: int) -> Tuple[int, int]:
    """Domain wide enough to witness every multiple of ``modulus`` in ``[lo, hi]``."""
    return math.floor(lo / modulus) - 1, math.ceil(hi / modulus) + 1


def features_to_spec(
    features: Features,
    blind: FrozenSet[str] = frozenset(),
    *,
    name: str,
    require_known: bool = True,
) -> Spec:
    """Translate a feature record into the typed IR.

    Parameters
    ----------
    blind
        Feature families the reductor normalises away.  ``"quantifier"`` makes it
        always read the question existentially; ``"adjective"`` makes it ignore
        the declared domain kind; ``"strictness"`` collapses strict and
        non-strict inequalities; ``"qualifier"`` drops qualifying clauses.
    require_known
        If true (default) an ``UNKNOWN`` feature outside ``blind`` raises
        :class:`ReductionAbstain` instead of being guessed.
    """
    needed = {
        "quantifier": features.quantifier,
        "adjective": features.adjective,
        "strictness": features.bound,
        "qualifier": features.qualifier,
    }
    if require_known:
        for family, value in needed.items():
            if family not in blind and value == UNKNOWN:
                raise ReductionAbstain(f"cannot resolve feature '{family}'")

    lo, hi = features.lo, features.hi

    # -- domain kind: a typing constraint on the declared domain --------------
    if "adjective" not in blind and features.adjective == "positive":
        lo = max(lo, 1)
    elif "adjective" not in blind and features.adjective == "non_negative":
        lo = max(lo, 0)

    variables: list[Tuple[str, int, int]] = [(features.var, lo, hi)]
    hypotheses: list[Term] = []

    # -- qualifying clause: an auxiliary witness variable ---------------------
    if "qualifier" not in blind and features.qualifier != "none":
        if features.qualifier in ("multiple_of", "even"):
            modulus = 2 if features.qualifier == "even" else int(features.qualifier_m)
            aux = f"{features.var}_w{modulus}"
            t_lo, t_hi = _aux_domain(lo, hi, modulus)
            variables.append((aux, t_lo, t_hi))
            hypotheses.append(
                Term.linear(
                    {features.var: Fraction(1), aux: Fraction(-modulus)},
                    0,
                    Comparison.EQ,
                )
            )
        elif features.qualifier == "not_multiple_of":
            # A negated congruence is a disjunction and therefore sits outside
            # the conjunctive affine fragment; the experiment never generates it.
            raise ReductionAbstain("negated congruence is outside the affine fragment")
        elif features.qualifier == "odd":
            raise ReductionAbstain("'odd' is outside the affine fragment as encoded")
        else:  # pragma: no cover - defensive
            raise ReductionAbstain(f"unknown qualifier {features.qualifier!r}")

    # -- the goal: the inequality under test ----------------------------------
    if features.bound == UNKNOWN and "strictness" in blind:
        comp = Comparison.GE  # blind reductor falls back to a weak reading
    elif features.bound == UNKNOWN:
        raise ReductionAbstain("cannot resolve the target inequality")
    else:
        comp = _BOUND_TO_COMPARISON[features.bound]
        if "strictness" in blind:
            if comp is Comparison.GT:
                comp = Comparison.GE
            elif comp is Comparison.LT:
                comp = Comparison.LE

    goal = [Term.linear({features.var: Fraction(1)}, features.bound_k, comp)]

    # -- the quantifier -------------------------------------------------------
    if "quantifier" in blind:
        quantifier = Quantifier.EXISTS
    elif features.quantifier == "every":
        quantifier = Quantifier.FORALL
    elif features.quantifier == "some":
        quantifier = Quantifier.EXISTS
    else:
        raise ReductionAbstain("cannot resolve the quantifier")

    spec = Spec(
        variables=tuple(variables),
        hypotheses=tuple(hypotheses),
        goal=tuple(goal),
        quantifier=quantifier,
    )
    spec.validate()
    return spec


def spec_to_features(spec: Spec) -> Features:
    """Recover as much of the feature record as the IR actually encodes.

    Fields the IR cannot express come back as :data:`UNKNOWN`; that asymmetry is
    what makes a round-trip anchor check informative.
    """
    primary = spec.variables[0]
    var, lo, hi = primary

    quantifier = "every" if spec.quantifier is Quantifier.FORALL else "some"

    bound, bound_k = UNKNOWN, None
    if len(spec.goal) == 1:
        goal = spec.goal[0]
        if len(goal.coeffs) == 1 and goal.coeffs[0][0] == var and goal.coeffs[0][1] == 1:
            bound = _COMPARISON_TO_BOUND.get(goal.comp, UNKNOWN)
            bound_k = int(goal.rhs) if goal.rhs.denominator == 1 else None

    qualifier, qualifier_m = "none", None
    if len(spec.hypotheses) == 1 and len(spec.variables) == 2:
        aux_name, aux_lo, aux_hi = spec.variables[1]
        term = spec.hypotheses[0]
        modulus = abs(int(next(c for n, c in term.coeffs if n == aux_name)))
        qualifier = "even" if modulus == 2 else "multiple_of"
        qualifier_m = modulus

    return Features(
        var=var,
        lo=lo,
        hi=hi,
        # The IR has no slot for the declared domain *kind*; it only carries the
        # resulting interval, so the adjective is genuinely lost.
        adjective=UNKNOWN,
        quantifier=quantifier,
        bound=bound,
        bound_k=bound_k,
        qualifier=qualifier,
        qualifier_m=qualifier_m,
    )


def make_reductor(blind: FrozenSet[str], name: str):
    """Build a reductor callable with a given blindness profile."""

    def reductor(text: str, *, recognise: bool = True) -> Reduction:
        from .surface import extract

        try:
            features = extract(text, recognise=recognise)
        except UnparsedSurface as exc:
            return Reduction(None, _EMPTY_FEATURES, name, note=str(exc))
        try:
            spec = features_to_spec(features, blind, name=name)
        except ReductionAbstain as exc:
            return Reduction(None, features, name, note=str(exc))
        return Reduction(spec, features, name)

    return reductor


_EMPTY_FEATURES = Features(
    var="?", lo=0, hi=0, quantifier=UNKNOWN, adjective=UNKNOWN,
    bound=UNKNOWN, bound_k=None, qualifier=UNKNOWN, qualifier_m=None,
)
