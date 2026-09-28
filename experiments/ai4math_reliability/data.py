"""Instance generator for the controlled study.

Five semantics-changing perturbation classes, each isolating one feature of the
surface language, plus a **control** set of strictly semantics-preserving
paraphrases used to measure false alarms.

Ground truth is defined by the reference semantics of the controlled language:
the feature record is translated with *no* blindness and decided exactly.  A
perturbation class is admitted into the corpus only when it actually flips the
ground truth; this is checked at generation time rather than assumed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .ir import decide
from .reductor import features_to_spec
from .surface import Features, SURFACE, render_text

__all__ = ["CLASSES", "Instance", "Pair", "build_dataset", "truth_of"]


CLASSES: Tuple[str, ...] = (
    "P1_quantifier_drift",
    "P2_domain_kind_drift",
    "P3_strictness_drift",
    "P4_assumption_omission",
    "P5_vacuity",
)

#: Fraction of the time a slot is rendered with its canonical (index-0) form.
CANONICAL_PRIOR = 0.75

SLOTS = ("quantifier", "adjective", "bound", "qualifier")


@dataclass(frozen=True)
class Instance:
    text: str
    features: Features
    truth: bool
    cls: str
    pair: int
    role: str  # "base" | "perturbed" | "paraphrase"
    template: str

    @property
    def key(self) -> Tuple[str, int]:
        return self.cls, self.pair


@dataclass(frozen=True)
class Pair:
    cls: str
    index: int
    base: Instance
    other: Instance

    @property
    def truth_differs(self) -> bool:
        return self.base.truth != self.other.truth


# --------------------------------------------------------------------------
# Reference semantics
# --------------------------------------------------------------------------
def truth_of(features: Features) -> bool:
    """Ground truth = exact decision on the faithfully translated IR."""
    return decide(features_to_spec(features, frozenset(), name="reference")).answer


# --------------------------------------------------------------------------
# Controlled perturbations
# --------------------------------------------------------------------------
def _perturb(cls: str, base: Features) -> Features:
    if cls == "P1_quantifier_drift":
        return replace(base, quantifier="some" if base.quantifier == "every" else "every")
    if cls == "P2_domain_kind_drift":
        return replace(base, adjective="non_negative" if base.adjective == "positive" else "positive")
    if cls == "P3_strictness_drift":
        mapping = {"at_least": "more_than", "more_than": "at_least", "at_most": "less_than", "less_than": "at_most"}
        return replace(base, bound=mapping[base.bound])
    if cls == "P4_assumption_omission":
        return replace(base, qualifier="none", qualifier_m=None)
    if cls == "P5_vacuity":
        return replace(base, qualifier="none", qualifier_m=None)
    raise KeyError(cls)


#: Candidate base records per class.  Grids are generated, then filtered at build
#: time: only instances whose ground truth actually flips under the perturbation
#: are admitted, so every reported pair is a genuine semantics-changing edit.
#:
#: Classes P1--P3 are built *without* a qualifying clause and with an untyped
#: domain, so that the only anchor that can catch them is the one matching their
#: own failure mechanism.  Otherwise the reductor's blindness to qualifying
#: clauses would fire on every class and mask the mechanism under study.
def _candidates(cls: str) -> List[Features]:
    def f(**kw) -> Features:
        defaults = dict(
            var="x", lo=0, hi=12, quantifier="some", adjective="any",
            bound="at_most", bound_k=6, qualifier="none", qualifier_m=None,
        )
        defaults.update(kw)
        return Features(**defaults)

    if cls == "P1_quantifier_drift":
        # "every" and "some" disagree as soon as the bound is strictly inside the
        # interval: all-but-one fail, while at least one succeeds.
        return [
            f(var=var, quantifier="every", lo=0, hi=hi, bound=bound, bound_k=k)
            for var in ("x", "n")
            for hi in (6, 8, 10, 12, 14)
            for bound in ("at_most", "at_least")
            for k in (1, 2, 3)
        ]

    if cls == "P2_domain_kind_drift":
        # x = 0 is the pivot.  With a lower bound of 0 it is the unique witness,
        # so excluding it under "positive" flips the answer -- and the flip leaves
        # no trace in the declared interval phrase, which is "0..hi" either way.
        return [
            f(var=var, adjective="positive", lo=0, hi=hi, bound=bound, bound_k=0)
            for var in ("x", "n")
            for hi in (3, 5, 7, 9, 11, 13, 15, 17, 19, 21)
            for bound in ("at_most", "equal_to")
        ]

    if cls == "P3_strictness_drift":
        # Strict and non-strict bounds differ only at the extreme they pin down.
        return [
            f(var=var, quantifier=quant, lo=lo, hi=hi, bound="at_least", bound_k=k)
            for var in ("x", "n")
            for lo in (0, 1)
            for hi in (4, 6, 8, 10, 12)
            for quant, k in (("some", hi), ("every", lo))
        ]

    if cls == "P4_assumption_omission":
        # Flip requires no multiple of m to satisfy the goal while some
        # unqualified integer does.
        return [
            f(lo=1, hi=hi, bound="at_most", bound_k=k, qualifier="multiple_of", qualifier_m=m)
            for m in (3, 4, 5, 6, 7)
            for hi in (9, 10, 11, 12)
            for k in range(1, m)
        ]

    if cls == "P5_vacuity":
        # Base is *vacuously* true: the qualifying clause has no model at all.
        return [
            f(quantifier="every", lo=1, hi=hi, bound="equal_to", bound_k=k,
              qualifier="multiple_of", qualifier_m=m)
            for hi in (2, 3, 4, 5)
            for m in range(hi + 1, hi + 5)
            for k in (1, 3, 5)
            if k <= hi
        ]
    raise KeyError(cls)


# --------------------------------------------------------------------------
# Surface realisation sampling
# --------------------------------------------------------------------------
def _sample_profile(rng: random.Random, canonical_prior: float = CANONICAL_PRIOR) -> Dict[str, int]:
    profile: Dict[str, int] = {}
    for slot in SLOTS:
        width = max(len(variants) for variants in SURFACE[slot].values())
        if rng.random() < canonical_prior or width <= 1:
            profile[slot] = 0
        else:
            profile[slot] = rng.randrange(1, width)
    return profile


def _render(features: Features, profile: Dict[str, int], template: str) -> str:
    """Render with a per-slot variant profile (delegates to the surface module)."""
    return render_text(features, profile=profile, template=template)


# --------------------------------------------------------------------------
# Corpus construction
# --------------------------------------------------------------------------
def build_dataset(
    n_per_class: int = 40,
    seed: int = 20260928,
    canonical_prior: float = CANONICAL_PRIOR,
) -> Tuple[List[Pair], List[Pair]]:
    """Return ``(critical_pairs, benign_pairs)``.

    * ``critical_pairs`` -- semantics-changing pairs whose ground truth flips.
      These carry the *unfaithful* reductions the anchor mechanisms must catch.
    * ``benign_pairs``  -- paraphrase pairs drawn from a population on which the
      blind reductor is provably faithful (see :func:`_benign_candidates`).  Here
      abstention is pure cost, so this set measures over-abstention rather than
      detection.

    Splitting the two is what makes the detector analysis interpretable: with a
    single mixed corpus the blindness of the reductor to four feature families at
    once leaves almost no faithful instances to measure the cost against.
    """
    rng = random.Random(seed)
    critical: List[Pair] = []
    benign: List[Pair] = []

    for cls in CLASSES:
        admitted = 0
        for candidate in _candidates(cls):
            if admitted >= n_per_class:
                break
            perturbed = _perturb(cls, candidate)
            base_truth, pert_truth = truth_of(candidate), truth_of(perturbed)
            if base_truth == pert_truth:
                continue  # not a semantics-changing instance; skip by construction
            template = "u"
            base_text = _render(candidate, _sample_profile(rng, canonical_prior), template)
            pert_text = _render(perturbed, _sample_profile(rng, canonical_prior), template)
            critical.append(
                Pair(
                    cls=cls,
                    index=admitted,
                    base=Instance(base_text, candidate, base_truth, cls, admitted, "base", template),
                    other=Instance(pert_text, perturbed, pert_truth, cls, admitted, "perturbed", template),
                )
            )
            admitted += 1

    for index, candidate in enumerate(_benign_candidates(n_per_class)):
        template = "u"
        benign.append(
            Pair(
                cls="benign",
                index=index,
                base=Instance(
                    _render(candidate, _sample_profile(rng, canonical_prior), template),
                    candidate,
                    truth_of(candidate),
                    "benign",
                    index,
                    "base",
                    template,
                ),
                other=Instance(
                    _render(candidate, _sample_profile(rng, canonical_prior), template),
                    candidate,
                    truth_of(candidate),
                    "benign",
                    index,
                    "paraphrase",
                    template,
                ),
            )
        )
    return critical, benign


def _benign_candidates(count: int) -> List[Features]:
    """A population on which the feature-blind reductor is faithful.

    Restricted to the region of the language its blindness cannot reach: no
    qualifying clause, an existential reading, an untyped domain, and a
    non-strict inequality.  On these instances an unfaithful IR is impossible, so
    every abstention an anchor mechanism makes here is a certification cost.
    """
    out: List[Features] = []
    for hi in (8, 9, 10, 11, 12):
        for bound in ("at_most", "at_least", "equal_to"):
            for k in (1, 3, 5, 7):
                out.append(
                    Features(
                        var="x", lo=0, hi=hi, quantifier="some", adjective="any",
                        bound=bound, bound_k=k, qualifier="none", qualifier_m=None,
                    )
                )
                if len(out) >= count:
                    return out
    return out


def iter_instances(pairs: Sequence[Pair]) -> Iterator[Instance]:
    for pair in pairs:
        yield pair.base
        yield pair.other


def build_mixed_corpus(
    critical_pairs: Sequence[Pair],
    *,
    prevalence: float = 0.2,
    seed: int = 20260928,
) -> List[Instance]:
    """A prevalence-controlled corpus: every base instance plus a fraction of the
    perturbed ones.

    The balanced critical set makes the *contrast between pipelines* legible but
    inflates absolute error rates, because half of it is by construction an
    instance the blind reductor gets wrong.  Reporting a realistic-prevalence
    corpus as well keeps the headline numbers honest.
    """
    rng = random.Random(seed + 1)
    out: List[Instance] = [pair.base for pair in critical_pairs]
    for pair in critical_pairs:
        if rng.random() < prevalence:
            out.append(pair.other)
    return out
