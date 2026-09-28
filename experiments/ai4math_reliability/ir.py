"""Typed intermediate representation (IR) for finite-domain affine arithmetic.

The IR is the object that the *semantic reduction layer* produces and that the
*verification layer* consumes.  It is deliberately small so that the verification
layer can be made **sound and complete** inside the fragment: every question the
IR can express is decided exactly by enumeration over a finite box.

Design notes
------------
* Variables are integers restricted to an explicit, finite, closed interval
  ``[lo, hi]``.  Finiteness is what buys us exact, reproducible decision
  procedures without pulling in an SMT solver; the restriction is reported as a
  scope limitation in the paper.
* Constraints are linear with rational coefficients, so all arithmetic is exact
  (``fractions.Fraction``) and no floating-point tolerance can mask an error.
* A ``Spec`` carries (i) the variable declarations, (ii) the hypotheses, (iii)
  the goal, and (iv) the *quantifier of the question*.  The quantifier is part of
  the IR on purpose: dropping it is one of the semantic-reduction failure modes
  the paper studies.

Nothing in this module knows about natural language.  That separation is the
point of the architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction
from itertools import product
from typing import Dict, Iterator, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Comparison",
    "Term",
    "Quantifier",
    "Spec",
    "EvalResult",
    "IRValidationError",
    "decide",
    "is_consistent",
    "MAX_BOX_SIZE",
]

#: Upper bound on the number of points we are willing to enumerate.  Guards the
#: experiment against an accidental combinatorial blow-up; see ``Spec.decide``.
MAX_BOX_SIZE = 5_000_000


class IRValidationError(ValueError):
    """Raised when a well-formedness condition on the IR is violated."""


class Comparison(IntEnum):
    """Ordering relation between a linear form and its right-hand side."""

    LT = 0
    LE = 1
    EQ = 2
    GE = 3
    GT = 4

    @property
    def symbol(self) -> str:
        return {
            Comparison.LT: "<",
            Comparison.LE: "<=",
            Comparison.EQ: "=",
            Comparison.GE: ">=",
            Comparison.GT: ">",
        }[self]

    def holds(self, lhs: Fraction, rhs: Fraction) -> bool:
        if self is Comparison.LT:
            return lhs < rhs
        if self is Comparison.LE:
            return lhs <= rhs
        if self is Comparison.EQ:
            return lhs == rhs
        if self is Comparison.GE:
            return lhs >= rhs
        return lhs > rhs


class Quantifier(IntEnum):
    """Quantifier of the yes/no question the IR encodes."""

    EXISTS = 0
    FORALL = 1

    @property
    def keyword(self) -> str:
        return "there exists" if self is Quantifier.EXISTS else "for every"


def _freeze(coeffs: Mapping[str, Fraction]) -> Tuple[Tuple[str, Fraction], ...]:
    """Canonical, hashable form of a coefficient map (drops exact zeros)."""
    return tuple(sorted((k, Fraction(v)) for k, v in coeffs.items() if v != 0))


@dataclass(frozen=True, order=True)
class Term:
    """A single linear constraint ``sum(coeffs[v] * v)  <comp>  rhs``."""

    coeffs: Tuple[Tuple[str, Fraction], ...]
    rhs: Fraction
    comp: Comparison

    @staticmethod
    def linear(coeffs: Mapping[str, Fraction], rhs: object, comp: Comparison) -> "Term":
        return Term(_freeze(coeffs), Fraction(rhs), comp)

    def variables(self) -> Tuple[str, ...]:
        return tuple(name for name, _ in self.coeffs)

    def lhs(self, assignment: Mapping[str, int]) -> Fraction:
        total = Fraction(0)
        for name, coeff in self.coeffs:
            total += coeff * assignment[name]
        return total

    def satisfied_by(self, assignment: Mapping[str, int]) -> bool:
        return self.comp.holds(self.lhs(assignment), self.rhs)

    def render(self) -> str:
        if not self.coeffs:
            return f"0 {self.comp.symbol} {_num(self.rhs)}"
        chunks = []
        for name, coeff in self.coeffs:
            if coeff == 1:
                chunks.append(name)
            elif coeff == -1:
                chunks.append(f"-{name}")
            else:
                chunks.append(f"{_num(coeff)}*{name}")
        return f"{' + '.join(chunks).replace('+ -', '- ')} {self.comp.symbol} {_num(self.rhs)}"


def _num(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(value)


@dataclass(frozen=True)
class Spec:
    """A typed IR object: variable declarations + hypotheses + goal + quantifier."""

    variables: Tuple[Tuple[str, int, int], ...]
    hypotheses: Tuple[Term, ...]
    goal: Tuple[Term, ...]
    quantifier: Quantifier

    # ---------------------------------------------------------------- checks
    def validate(self) -> None:
        names = [name for name, _, _ in self.variables]
        if len(set(names)) != len(names):
            raise IRValidationError("duplicate variable declarations")
        for name, lo, hi in self.variables:
            if lo > hi:
                raise IRValidationError(f"empty declared domain for {name}: [{lo}, {hi}]")
        declared = set(names)
        for term in tuple(self.hypotheses) + tuple(self.goal):
            unknown = set(term.variables()) - declared
            if unknown:
                raise IRValidationError(f"term references undeclared variable(s): {sorted(unknown)}")
        if self.box_size > MAX_BOX_SIZE:
            raise IRValidationError(
                f"search box too large ({self.box_size} points); reduce declared domains"
            )

    @property
    def box_size(self) -> int:
        size = 1
        for _, lo, hi in self.variables:
            size *= hi - lo + 1
        return size

    @property
    def is_empty_domain(self) -> bool:
        return any(lo > hi for _, lo, hi in self.variables)

    def domain_of(self, name: str) -> Tuple[int, int]:
        for cand, lo, hi in self.variables:
            if cand == name:
                return lo, hi
        raise KeyError(name)

    def assignments(self) -> Iterator[Dict[str, int]]:
        """Enumerate every point of the declared box (exact, deterministic)."""
        names = [name for name, _, _ in self.variables]
        ranges = [range(lo, hi + 1) for _, lo, hi in self.variables]
        for values in product(*ranges):
            yield dict(zip(names, values))

    def all_hold(self, terms: Sequence[Term], assignment: Mapping[str, int]) -> bool:
        return all(term.satisfied_by(assignment) for term in terms)

    def render(self) -> str:
        lines = ["vars: " + ", ".join(f"{n}: [{lo}, {hi}]" for n, lo, hi in self.variables)]
        lines.append("hyp : " + (" and ".join(t.render() for t in self.hypotheses) or "true"))
        lines.append("goal: " + (" and ".join(t.render() for t in self.goal) or "true"))
        lines.append("quant: " + self.quantifier.name.lower())
        return "\n".join(lines)


@dataclass(frozen=True)
class EvalResult:
    """Outcome of an exact decision procedure on a :class:`Spec`."""

    answer: bool
    n_hypothesis_models: int
    witness: Optional[Tuple[Tuple[str, int], ...]]
    counterexample: Optional[Tuple[Tuple[str, int], ...]]
    certificate: str

    @property
    def hypotheses_satisfiable(self) -> bool:
        return self.n_hypothesis_models > 0

    @property
    def is_vacuous(self) -> bool:
        """A universal claim over an empty model set is vacuously true."""
        return self.n_hypothesis_models == 0


def decide(spec: Spec) -> EvalResult:
    """Decide the yes/no question encoded by ``spec`` -- sound *and* complete.

    * ``EXISTS``  -> is there an assignment satisfying hypotheses *and* goal?
    * ``FORALL``  -> does every assignment satisfying hypotheses satisfy goal?
      (vacuously true when no assignment satisfies the hypotheses)

    The procedure enumerates the declared box and is therefore exact within the
    fragment.  It raises :class:`IRValidationError` if the object is malformed or
    the box is unreasonably large.
    """
    spec.validate()
    models = 0
    witness: Optional[Tuple[Tuple[str, int], ...]] = None
    counterexample: Optional[Tuple[Tuple[str, int], ...]] = None

    for assignment in spec.assignments():
        if not spec.all_hold(spec.hypotheses, assignment):
            continue
        models += 1
        frozen = tuple(sorted(assignment.items()))
        goal_holds = spec.all_hold(spec.goal, assignment)
        if spec.quantifier is Quantifier.EXISTS:
            if goal_holds:
                witness = frozen
                break
        else:
            if not goal_holds and counterexample is None:
                counterexample = frozen

    if spec.quantifier is Quantifier.EXISTS:
        answer = witness is not None
        cert = (
            f"witness {dict(witness)} satisfies hypotheses and goal"
            if witness is not None
            else f"no point of the declared box satisfies hypotheses and goal "
            f"({models} hypothesis model(s) examined)"
        )
    else:
        answer = counterexample is None
        cert = (
            f"all {models} hypothesis model(s) satisfy the goal"
            if counterexample is None
            else f"counterexample {dict(counterexample)} satisfies hypotheses but not goal"
        )
    if models == 0:
        cert += " [vacuous: hypothesis set has no model in the declared box]"
    return EvalResult(answer, models, witness, counterexample, cert)


def is_consistent(spec: Spec) -> bool:
    """True iff the hypothesis set admits at least one model in the declared box."""
    return decide(spec).hypotheses_satisfiable
