"""Seven answer pipelines over the same corpus.

Two properties are routinely conflated, and the ladder exists to separate them:

* **correctness** -- does the answer match the ground truth of the *sentence*?
* **certifiability** -- is the answer accompanied by a machine-checkable object?

A modelling decision that matters
---------------------------------
The *reductor* and the *anchor extractor* draw on different resources:

* The reductor is an LLM-like component with full linguistic competence, so it
  reads the **full** lexicon (``recognise=False``).  Its defect is not lexical but
  *semantic*: it silently normalises away distinctions it does not model.
* The anchor extractor is a symbolic component with a **declared, closed**
  grammar (``recognise=True``).  Its defect is *coverage*: a form outside the
  declaration cannot be certified.

Keeping these separate is what makes the study interpretable -- otherwise a
lexicon gap and a semantic blindness are indistinguishable in the results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .anchors import AnchorVerdict, lexical_anchor_check, typed_anchor_check
from .ir import decide
from .reductor import ALL_FEATURES, features_to_spec, ReductionAbstain
from .surface import Features, UNKNOWN, extract, UnparsedSurface

__all__ = ["Answer", "PIPELINE_NAMES", "PIPELINES", "run_pipeline", "naive_spec_of", "raw_naive_answer"]

PIPELINE_NAMES: Tuple[str, ...] = (
    "gen_only",
    "verify",
    "verify+lexical_anchors",
    "verify+typed_anchors",
    "typed_anchored",
    "typed_best_effort",
    "oracle",
)

#: The reductor used by every rung except the ``typed_*``/``oracle`` ones: blind
#: to the four feature families, i.e. an LLM-like reductor that silently
#: normalises away distinctions it does not model.
NAIVE_BLIND = ALL_FEATURES

#: Full linguistic competence for the reductor; declared grammar for anchors.
REDUCTOR_LEXICON = False
ANCHOR_LEXICON = True


@dataclass(frozen=True)
class Answer:
    status: str  # "answered" | "abstained"
    value: Optional[bool]
    certified: bool
    vacuous: bool = False
    reason: str = ""

    @property
    def answered(self) -> bool:
        return self.status == "answered"


def _reduce(text: str, blind, *, lexicon: bool = REDUCTOR_LEXICON):
    """Reduce ``text``; returns ``(spec, features, note)`` with ``spec`` possibly None."""
    try:
        features = extract(text, recognise=lexicon)
    except UnparsedSurface as exc:
        return None, None, f"outside grammar: {exc}"
    try:
        spec = features_to_spec(features, blind, name="reductor")
    except ReductionAbstain as exc:
        return None, features, f"reduction abstained: {exc}"
    return spec, features, ""


def _naive_spec(text: str):
    return _reduce(text, NAIVE_BLIND)


# --------------------------------------------------------------------------
# Rungs
# --------------------------------------------------------------------------
def _gen_only(text: str) -> Answer:
    """Layer 1 alone: the model's reading of the problem, no IR, no certificate.

    Deliberately charitable: it reads with the full lexicon and, if it cannot read
    a sentence at all, falls back to the affirmative answer rather than failing.
    """
    spec, _, _ = _naive_spec(text)
    if spec is None:
        return Answer("answered", True, certified=False, reason="fallback guess")
    return Answer("answered", decide(spec).answer, certified=False)


def _verify(text: str) -> Answer:
    """Layers 2+3: reduce to the IR and *decide* the IR exactly."""
    spec, _, note = _naive_spec(text)
    if spec is None:
        return Answer("abstained", None, certified=False, reason=note)
    return Answer("answered", decide(spec).answer, certified=True)


def _anchored(text: str, checker: str, *, require_confirmation: bool) -> Answer:
    """Blind reduction + an anchor check.

    ``require_confirmation`` distinguishes the two policies the paper compares:
    a *conservative* one that abstains unless every anchor is readable and holds,
    and a *best-effort* one that answers regardless but only certifies confirmed
    answers.  A violated anchor always causes abstention under both.
    """
    spec, _, note = _naive_spec(text)
    if spec is None:
        return Answer("abstained", None, certified=False, reason=note)

    if checker == "lexical":
        verdict = lexical_anchor_check(text, spec)
    else:
        try:
            anchor_features = extract(text, recognise=ANCHOR_LEXICON)
        except UnparsedSurface as exc:
            return Answer("abstained", None, certified=False, reason=f"anchor extractor: {exc}")
        verdict = typed_anchor_check(anchor_features, spec)

    if verdict.failed:
        return Answer("abstained", None, certified=False, reason=f"anchor violated: {verdict.violated}")
    if not require_confirmation and verdict.unconfirmed:
        return Answer(
            "answered", decide(spec).answer, certified=False,
            vacuous=verdict.vacuous, reason=f"unconfirmed anchors: {verdict.unconfirmed}",
        )
    if not verdict.certifiable:
        return Answer("abstained", None, certified=False, reason=f"unconfirmed anchors: {verdict.unconfirmed}")
    return Answer("answered", decide(spec).answer, certified=True, vacuous=verdict.vacuous)


def _typed_anchored(text: str) -> Answer:
    """The proposed safety-first design: faithful reduction + typed anchors."""
    spec, features, note = _reduce(text, frozenset())
    if spec is None:
        return Answer("abstained", None, certified=False, reason=note)
    try:
        anchor_features = extract(text, recognise=ANCHOR_LEXICON)
    except UnparsedSurface as exc:
        return Answer("abstained", None, certified=False, reason=f"anchor extractor: {exc}")
    verdict = typed_anchor_check(anchor_features, spec)
    if not verdict.certifiable:
        return Answer("abstained", None, certified=False, reason=f"anchors: {verdict.violated or verdict.unconfirmed}")
    result = decide(spec)
    return Answer("answered", result.answer, certified=True, vacuous=result.is_vacuous)


def _typed_best_effort(text: str) -> Answer:
    """Faithful reduction; answer always, certify only when anchors confirm."""
    spec, _, note = _reduce(text, frozenset())
    if spec is None:
        return Answer("abstained", None, certified=False, reason=note)
    try:
        anchor_features = extract(text, recognise=ANCHOR_LEXICON)
    except UnparsedSurface:
        verdict = AnchorVerdict(("unparseable",), (), False)
    else:
        verdict = typed_anchor_check(anchor_features, spec)
    if verdict.failed:
        return Answer("abstained", None, certified=False, reason=f"anchors: {verdict.violated}")
    result = decide(spec)
    return Answer(
        "answered", result.answer,
        certified=verdict.certifiable,
        vacuous=result.is_vacuous,
        reason="" if verdict.certifiable else f"unconfirmed: {verdict.unconfirmed}",
    )


def _oracle(text: str) -> Answer:
    """Ceiling: a perfect reduction and a perfect anchor extractor."""
    spec, _, note = _reduce(text, frozenset(), lexicon=False)
    if spec is None:  # pragma: no cover - the full lexicon covers the corpus
        return Answer("abstained", None, certified=False, reason=note)
    result = decide(spec)
    return Answer("answered", result.answer, certified=True, vacuous=result.is_vacuous)


PIPELINES = {
    "gen_only": _gen_only,
    "verify": _verify,
    "verify+lexical_anchors": lambda t: _anchored(t, "lexical", require_confirmation=True),
    "verify+typed_anchors": lambda t: _anchored(t, "typed", require_confirmation=True),
    "typed_anchored": _typed_anchored,
    "typed_best_effort": _typed_best_effort,
    "oracle": _oracle,
}


def run_pipeline(name: str, text: str) -> Answer:
    return PIPELINES[name](text)


def naive_spec_of(text: str):
    """Expose the feature-blind reduction of ``text`` (``Spec`` or ``None``)."""
    return _naive_spec(text)[0]


def anchor_verdict(kind: str, text: str, spec=None):
    """Run just the anchor stage, returning ``(verdict, spec)``.

    ``None`` for the verdict means the stage could not run at all (the sentence is
    outside the anchor grammar).  Exposed separately so the analysis can tell
    *detection by contradiction* apart from *abstention for lack of coverage*.
    """
    if spec is None:
        spec = naive_spec_of(text)
    if spec is None:
        return None, None
    if kind == "lexical":
        # The lexical check needs no lexicon: it compares the round trip against
        # the sentence directly, which is exactly why it is brittle to paraphrase.
        return lexical_anchor_check(text, spec), spec
    try:
        anchor_features = extract(text, recognise=ANCHOR_LEXICON)
    except UnparsedSurface:
        return None, spec
    return typed_anchor_check(anchor_features, spec), spec


def raw_naive_answer(text: str) -> Optional[bool]:
    """Answer the feature-blind reductor would give, or ``None`` if it abstains."""
    spec = naive_spec_of(text)
    return None if spec is None else decide(spec).answer
