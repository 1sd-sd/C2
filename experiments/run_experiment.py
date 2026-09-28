#!/usr/bin/env python
"""Run the controlled study and emit every number, table and figure used in the paper.

Usage
-----
    python run_experiment.py [--seed 20260928] [--n-per-class 40]

Outputs
-------
    results/results.json            machine-readable record of the whole run
    results/summary.md              human-readable summary
    paper/tables/*.tex              booktabs tables \\input by paper.tex
    paper/figures/*.tex             pgfplots bar charts \\input by paper.tex

The script is deterministic: same seed in, byte-identical results out.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

EXPERIMENTS = Path(__file__).resolve().parent
PROJECT = EXPERIMENTS.parent
sys.path.insert(0, str(EXPERIMENTS))

from ai4math_reliability import data as dataset  # noqa: E402
from ai4math_reliability import pipelines  # noqa: E402
from ai4math_reliability.ir import decide, is_consistent  # noqa: E402
from ai4math_reliability.reductor import features_to_spec  # noqa: E402

RESULTS_DIR = EXPERIMENTS / "results"
PAPER_DIR = PROJECT / "paper"
CLASS_LABELS = {
    "P1_quantifier_drift": "P1",
    "P2_domain_kind_drift": "P2",
    "P3_strictness_drift": "P3",
    "P4_assumption_omission": "P4",
    "P5_vacuity": "P5",
    "benign": "benign",
}


# --------------------------------------------------------------------------
# Metric containers
# --------------------------------------------------------------------------
@dataclass
class PipelineMetrics:
    name: str
    total: int = 0
    answered: int = 0
    correct: int = 0
    certified: int = 0

    @property
    def coverage(self) -> float:
        return self.answered / self.total if self.total else 0.0

    @property
    def accuracy_answered(self) -> float:
        return self.correct / self.answered if self.answered else 0.0

    @property
    def accuracy_overall(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def silent_error_rate(self) -> float:
        return (self.answered - self.correct) / self.total if self.total else 0.0

    @property
    def certificate_rate(self) -> float:
        return self.certified / self.total if self.total else 0.0


@dataclass
class DetectorMetrics:
    """How well an anchor mechanism finds *unfaithful reductions*.

    An instance counts as **unfaithful** when the blind reductor's IR differs from
    the reference IR built from the sentence's true features.  Note this is a
    property of the *reduction*, not of the answer: a reduction can be unfaithful
    and still happen to yield the right answer for the question asked, which is
    precisely why answer-level accuracy alone cannot police a reductor.

    Two outcomes are distinguished, because they are not the same thing:

    * **violation** -- the anchor contradicts the IR.  This is genuine *detection*.
    * **abstention** -- the answer is withheld, either because an anchor was
      violated or because it could not be read.  This is *safety*, bought at the
      price of coverage.
    """

    name: str
    unfaithful: int = 0
    violations: int = 0
    withheld_unfaithful: int = 0
    faithful: int = 0
    withheld_faithful: int = 0

    @property
    def recall(self) -> float:
        """Detection by contradiction, over unfaithful reductions."""
        return self.violations / self.unfaithful if self.unfaithful else 0.0

    @property
    def safety(self) -> float:
        """Share of unfaithful reductions whose answer was withheld."""
        return self.withheld_unfaithful / self.unfaithful if self.unfaithful else 0.0

    @property
    def over_abstention(self) -> float:
        """Cost: faithful reductions the mechanism refuses to certify."""
        return self.withheld_faithful / self.faithful if self.faithful else 0.0


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------
def evaluate_pipelines(instances: Sequence[dataset.Instance]) -> Dict[str, PipelineMetrics]:
    out: Dict[str, PipelineMetrics] = {}
    for name in pipelines.PIPELINE_NAMES:
        metrics = PipelineMetrics(name=name, total=len(instances))
        for inst in instances:
            answer = pipelines.run_pipeline(name, inst.text)
            if not answer.answered:
                continue
            metrics.answered += 1
            if answer.certified:
                metrics.certified += 1
            if answer.value == inst.truth:
                metrics.correct += 1
        out[name] = metrics
    return out


def reference_spec(inst: dataset.Instance):
    """The IR the sentence *should* have produced."""
    return features_to_spec(inst.features, frozenset(), name="reference")


def evaluate_detectors(instances: Sequence[dataset.Instance]) -> Dict[str, DetectorMetrics]:
    """Recall and over-abstention of each mechanism as a reduction-error detector."""
    kinds = {"verify+lexical_anchors": "lexical", "verify+typed_anchors": "typed"}
    out: Dict[str, DetectorMetrics] = {name: DetectorMetrics(name=name) for name in kinds}
    baseline = DetectorMetrics(name="ir_verification_only")

    for inst in instances:
        blind = pipelines.naive_spec_of(inst.text)
        if blind is None:
            continue  # nothing was reduced, so nothing to police
        unfaithful = blind != reference_spec(inst)

        for name, kind in kinds.items():
            verdict, _ = pipelines.anchor_verdict(kind, inst.text, blind)
            violated = verdict is not None and verdict.failed
            withheld = violated or verdict is None or not verdict.certifiable
            metrics = out[name]
            if unfaithful:
                metrics.unfaithful += 1
                metrics.violations += int(violated)
                metrics.withheld_unfaithful += int(withheld)
            else:
                metrics.faithful += 1
                metrics.withheld_faithful += int(withheld)

        if unfaithful:
            baseline.unfaithful += 1
        else:
            baseline.faithful += 1

    out["ir_verification_only"] = baseline
    return out


def invariance_report(pairs: Sequence[dataset.Pair]) -> Dict[str, float]:
    """Empirical confirmation of the verification blind spot (Proposition 2)."""
    identical_spec = identical_verdict = 0
    for pair in pairs:
        spec_base = pipelines.naive_spec_of(pair.base.text)
        spec_other = pipelines.naive_spec_of(pair.other.text)
        if spec_base is not None and spec_base == spec_other:
            identical_spec += 1
        verdict_base = pipelines.raw_naive_answer(pair.base.text)
        verdict_other = pipelines.raw_naive_answer(pair.other.text)
        if verdict_base == verdict_other:
            identical_verdict += 1
    total = len(pairs)
    return {
        "pairs": total,
        "identical_ir_fraction": identical_spec / total if total else 0.0,
        "identical_verdict_fraction": identical_verdict / total if total else 0.0,
    }


def vacuity_report(instances: Sequence[dataset.Instance]) -> Dict[str, Dict[str, int]]:
    """For genuinely vacuous claims, did each pipeline say so?"""
    vacuous = [
        inst
        for inst in instances
        if _is_vacuous_reference(inst)
    ]
    out: Dict[str, Dict[str, int]] = {}
    for name in pipelines.PIPELINE_NAMES:
        answered = warned = correct = 0
        for inst in vacuous:
            answer = pipelines.run_pipeline(name, inst.text)
            if answer.answered:
                answered += 1
                correct += int(answer.value == inst.truth)
            warned += int(answer.vacuous)
        out[name] = {"n": len(vacuous), "answered": answered, "warned": warned, "correct": correct}
    return out


def _is_vacuous_reference(inst: dataset.Instance) -> bool:
    spec = features_to_spec(inst.features, frozenset(), name="reference")
    return spec.quantifier.name == "FORALL" and not is_consistent(spec)


# --------------------------------------------------------------------------
# LaTeX emission
# --------------------------------------------------------------------------
def pct(x: float) -> str:
    return f"{100.0 * x:.1f}"


def write_pipeline_table(path: Path, metrics: Dict[str, PipelineMetrics], caption: str, label: str) -> None:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        rf"\caption{{{caption}}}",
        rf"\label{{{label}}}",
        r"\small",
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"Pipeline & $n$ & Coverage & Acc.\,$\mid$\,ans. & Silent err. & Cert. rate \\",
        r"\midrule",
    ]
    for name in pipelines.PIPELINE_NAMES:
        m = metrics[name]
        pretty = name.replace("_", r"\_")
        lines.append(
            f"{pretty} & {m.total} & {pct(m.coverage)} & {pct(m.accuracy_answered)} & "
            f"{pct(m.silent_error_rate)} & {pct(m.certificate_rate)} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_detector_table(path: Path, overall: Dict[str, DetectorMetrics], per_class: Dict[str, Dict[str, DetectorMetrics]]) -> None:
    label = {
        "ir_verification_only": "IR verification alone",
        "verify+lexical_anchors": "A-lex (round-trip)",
        "verify+typed_anchors": "A-type (typed)",
    }
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Mechanisms as detectors of \emph{unfaithful reductions} -- an IR that differs from the reference IR of the sentence, whether or not the answer happened to be right. \emph{Recall} counts violations of an anchor (genuine detection); \emph{safety} counts unfaithful reductions whose answer was withheld for any reason; \emph{over-abstention} is the price, i.e.\ the share of faithful reductions refused certification. IR verification alone has recall $0$ by Proposition~\ref{prop:blind}.}",
        r"\label{tab:detectors}",
        r"\small",
        r"\begin{tabular}{llrrrrr}",
        r"\toprule",
        r"Mechanism & Scope & $n_{\text{unf.}}$ & Recall & Safety & $n_{\text{fai.}}$ & Over-abst. \\",
        r"\midrule",
    ]
    for mech in ("ir_verification_only", "verify+lexical_anchors", "verify+typed_anchors"):
        m = overall[mech]
        lines.append(
            f"{label[mech]} & all & {m.unfaithful} & {pct(m.recall)} & {pct(m.safety)} & "
            f"{m.faithful} & {pct(m.over_abstention)} \\\\"
        )
    lines.append(r"\midrule")
    for cls in tuple(dataset.CLASSES) + ("benign",):
        for mech in ("verify+lexical_anchors", "verify+typed_anchors"):
            m = per_class[cls][mech]
            lines.append(
                f"{label[mech]} & {CLASS_LABELS[cls]} & {m.unfaithful} & {pct(m.recall)} & {pct(m.safety)} & "
                f"{m.faithful} & {pct(m.over_abstention)} \\\\"
            )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_sensitivity_table(path: Path, rows: Sequence[dict]) -> None:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Sensitivity to how far the input distribution strays from the anchor grammar. $p$ is the probability that a slot is realised with the form the lexicon was written for, so lower $p$ means more input falls outside the declared grammar. Recall is detection by contradiction; over-abstention is the certification cost.}",
        r"\label{tab:sensitivity}",
        r"\small",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"$p$ & A-lex recall & A-lex over-abst. & A-type recall & A-type over-abst. \\",
        r"\midrule",
    ]
    for row in rows:
        lines.append(
            f"{row['prior']:.2f} & {pct(row['lexical_recall'])} & {pct(row['lexical_over_abstention'])} & "
            f"{pct(row['typed_recall'])} & {pct(row['typed_over_abstention'])} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_canonical_detector_table(
    path: Path, per_class: Dict[str, Dict[str, DetectorMetrics]]
) -> None:
    """Per-class detection when every slot uses the form the lexicon was written for.

    Re-running the detector analysis at ``p = 1.00`` removes the paraphrase
    confound that inflates A-lex's headline recall: with no synonyms in the
    input, a lexical round-trip can only disagree when the IR really differs.
    Whatever A-lex "detects" at ``p < 1`` but not here was a paraphrase artefact,
    not a semantic change.
    """
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Detection at $p = 1.00$, i.e.\ every slot is realised with the exact "
        r"form the anchor lexicon declares, so no input falls outside the grammar. This "
        r"removes the paraphrase confound: A-lex's apparent detection of P2 vanishes "
        r"entirely, showing that its headline recall was partly synonym sensitivity rather "
        r"than semantic detection, whereas A-type's recall rises to $100\%$ on every class "
        r"it has a predicate for.}",
        r"\label{tab:canonical}",
        r"\small",
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"Class & $n_{\text{unf.}}$ & A-lex rec. & A-type rec. & A-lex o.-a. & A-type o.-a. \\",
        r"\midrule",
    ]
    for cls in dataset.CLASSES:
        lex = per_class[cls]["verify+lexical_anchors"]
        typ = per_class[cls]["verify+typed_anchors"]
        lines.append(
            f"{CLASS_LABELS[cls]} & {lex.unfaithful} & {pct(lex.recall)} & {pct(typ.recall)} & "
            f"{pct(lex.over_abstention)} & {pct(typ.over_abstention)} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_vacuity_table(path: Path, vacuity: Dict[str, Dict[str, int]]) -> None:
    """Whether each pipeline distinguishes 'true' from 'true only vacuously'."""
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Behaviour on the $32$ claims that are true \emph{only vacuously} (a "
        r"universal statement whose qualifying clause has no model in the declared box). "
        r"All of them are decided correctly by the rungs that answer them, but only the "
        r"rungs that record vacuity as a first-class outcome actually \emph{warn}. A pipeline "
        r"that reports ``true'' without the warning has reported a fact that is true of no "
        r"object.}",
        r"\label{tab:vacuity}",
        r"\small",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Pipeline & $n$ & Answered & Vacuity warned & Correct \\",
        r"\midrule",
    ]
    for name in pipelines.PIPELINE_NAMES:
        m = vacuity[name]
        pretty = name.replace("_", r"\_")
        lines.append(
            f"{pretty} & {m['n']} & {m['answered']} & {m['warned']} & {m['correct']} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def detectors_at_prior(
    n_per_class: int, seed: int, prior: float
) -> Tuple[Dict[str, DetectorMetrics], Dict[str, Dict[str, DetectorMetrics]]]:
    """Detector metrics (overall and per class) for one paraphrase prior."""
    pairs, _ = dataset.build_dataset(n_per_class, seed, canonical_prior=prior)
    instances = list(dataset.iter_instances(pairs))
    overall = evaluate_detectors(instances)
    per_class = {
        cls: evaluate_detectors([i for i in instances if i.cls == cls])
        for cls in dataset.CLASSES
    }
    return overall, per_class


def lexicon_sensitivity(n_per_class: int, seed: int, priors: Sequence[float]) -> List[dict]:
    """How recall and certification cost move as the input drifts off the grammar."""
    rows = []
    for prior in priors:
        pairs, _ = dataset.build_dataset(n_per_class, seed, canonical_prior=prior)
        instances = list(dataset.iter_instances(pairs))
        detectors = evaluate_detectors(instances)
        rows.append(
            {
                "prior": prior,
                "lexical_recall": detectors["verify+lexical_anchors"].recall,
                "lexical_over_abstention": detectors["verify+lexical_anchors"].over_abstention,
                "typed_recall": detectors["verify+typed_anchors"].recall,
                "typed_over_abstention": detectors["verify+typed_anchors"].over_abstention,
            }
        )
    return rows


def write_detection_figure(path: Path, per_class: Dict[str, Dict[str, DetectorMetrics]]) -> None:
    coords = {}
    for key, mech in (("lex", "verify+lexical_anchors"), ("typed", "verify+typed_anchors")):
        coords[key] = " ".join(
            f"({CLASS_LABELS[cls]},{per_class[cls][mech].recall:.3f})" for cls in dataset.CLASSES
        )
    zeros = " ".join(f"({CLASS_LABELS[cls]},0.000)" for cls in dataset.CLASSES)
    path.write_text(
        "\n".join(
            [
                r"\begin{tikzpicture}",
                r"\begin{axis}[",
                r"  ybar, bar width=7pt, width=\linewidth, height=5.6cm,",
                r"  ylabel={Detection recall}, ymin=0, ymax=1.05,",
                r"  xlabel={Semantic-reduction failure class},",
                r"  symbolic x coords={P1,P2,P3,P4,P5}, xtick=data,",
                r"  enlarge x limits=0.16, ymajorgrids, grid style={draw=black!12},",
                r"  legend style={at={(0.5,-0.30)}, anchor=north, legend columns=3, font=\small, draw=none},",
                r"]",
                rf"\addplot[fill=black!35, draw=black!55] coordinates {{{zeros}}};",
                r"\addlegendentry{IR verification only}",
                rf"\addplot[fill=blue!45, draw=blue!65] coordinates {{{coords['lex']}}};",
                r"\addlegendentry{A-lex (round-trip)}",
                rf"\addplot[fill=teal!55, draw=teal!75] coordinates {{{coords['typed']}}};",
                r"\addlegendentry{A-type (typed)}",
                r"\end{axis}",
                r"\end{tikzpicture}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_silent_error_figure(path: Path, metrics: Dict[str, PipelineMetrics]) -> None:
    # Short, brace-free tick labels: pgfplots expands symbolic coordinates
    # eagerly, and nesting \scriptsize groups inside them overflows TeX's input
    # stack.  Shrinking is delegated to ``x tick label style`` instead.
    pretty = {
        "gen_only": "gen-only",
        "verify": "reduce+verify",
        "verify+lexical_anchors": "+A-lex",
        "verify+typed_anchors": "+A-type",
        "typed_anchored": "typed+anch",
        "typed_best_effort": "best-effort",
        "oracle": "oracle",
    }
    coords = " ".join(
        f"({pretty[n]},{metrics[n].silent_error_rate * 100:.1f})"
        for n in pipelines.PIPELINE_NAMES
    )
    ticks = ",".join(pretty[n] for n in pipelines.PIPELINE_NAMES)
    path.write_text(
        "\n".join(
            [
                r"\begin{tikzpicture}",
                r"\begin{axis}[",
                r"  ybar, bar width=12pt, width=\linewidth, height=5.4cm,",
                r"  ylabel={Silent error rate (\%)}, ymin=0, ymax=62,",
                r"  symbolic x coords={" + ticks + "},",
                r"  xtick=data, x tick label style={rotate=20, anchor=east, font=\scriptsize},",
                r"  enlarge x limits=0.10, ymajorgrids, grid style={draw=black!12},",
                r"  nodes near coords, nodes near coords style={font=\scriptsize},",
                r"]",
                r"\addplot[fill=orange!55, draw=orange!75] coordinates {" + coords + "};",
                r"\end{axis}",
                r"\end{tikzpicture}",
                "",
            ]
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--n-per-class", type=int, default=40)
    args = parser.parse_args()

    critical_pairs, benign_pairs = dataset.build_dataset(args.n_per_class, args.seed)
    critical_instances = list(dataset.iter_instances(critical_pairs))
    benign_instances = list(dataset.iter_instances(benign_pairs))
    mixed_instances = dataset.build_mixed_corpus(critical_pairs, seed=args.seed)

    invariance = invariance_report(critical_pairs)
    critical_metrics = evaluate_pipelines(critical_instances)
    benign_metrics = evaluate_pipelines(benign_instances)
    mixed_metrics = evaluate_pipelines(mixed_instances)
    detector_instances = critical_instances + benign_instances
    detectors = evaluate_detectors(detector_instances)
    per_class_detectors = {
        cls: evaluate_detectors(
            [i for i in detector_instances if i.cls == cls]
        )
        for cls in tuple(dataset.CLASSES) + ("benign",)
    }
    vacuity = vacuity_report(detector_instances)
    sensitivity = lexicon_sensitivity(args.n_per_class, args.seed, (0.50, 0.75, 1.00))
    canonical_overall, canonical_per_class = detectors_at_prior(args.n_per_class, args.seed, 1.00)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (PAPER_DIR / "tables").mkdir(parents=True, exist_ok=True)
    (PAPER_DIR / "figures").mkdir(parents=True, exist_ok=True)

    payload = {
        "seed": args.seed,
        "n_per_class": args.n_per_class,
        "counts": {
            "critical_pairs": len(critical_pairs),
            "benign_pairs": len(benign_pairs),
            "critical_instances": len(critical_instances),
            "benign_instances": len(benign_instances),
            "per_class": {cls: sum(1 for p in critical_pairs if p.cls == cls) for cls in dataset.CLASSES},
        },
        "blind_spot_invariance": invariance,
        "pipelines_critical": {k: _dump(v) for k, v in critical_metrics.items()},
        "pipelines_mixed": {k: _dump(v) for k, v in mixed_metrics.items()},
        "pipelines_benign": {k: _dump(v) for k, v in benign_metrics.items()},
        "detectors_overall": {k: _dump(v) for k, v in detectors.items()},
        "detectors_per_class": {
            cls: {k: _dump(v) for k, v in per_class_detectors[cls].items()}
            for cls in tuple(dataset.CLASSES) + ("benign",)
        },
        "vacuity": vacuity,
        "lexicon_sensitivity": sensitivity,
        "detectors_canonical_overall": {k: _dump(v) for k, v in canonical_overall.items()},
        "detectors_canonical_per_class": {
            cls: {k: _dump(v) for k, v in canonical_per_class[cls].items()}
            for cls in dataset.CLASSES
        },
    }
    (RESULTS_DIR / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    write_pipeline_table(
        PAPER_DIR / "tables" / "pipeline_critical.tex",
        critical_metrics,
        "Behaviour on semantics-changing tasks (base + perturbed members of every critical pair). "
        "Coverage is the fraction answered; silent error rate counts wrong answers that were emitted "
        "as if certified; certificate rate is the fraction carrying a machine-checkable object.",
        "tab:critical",
    )
    write_pipeline_table(
        PAPER_DIR / "tables" / "pipeline_benign.tex",
        benign_metrics,
        "Behaviour on the benign paraphrase set, where the blind reductor is provably faithful. "
        "Ground truth is unchanged throughout and no answer here is wrong, so every abstention is "
        "a pure certification cost.",
        "tab:benign",
    )
    write_pipeline_table(
        PAPER_DIR / "tables" / "pipeline_mixed.tex",
        mixed_metrics,
        "Behaviour on a prevalence-controlled corpus: every base instance plus $20\\%$ of the "
        "perturbed ones. Unlike Table~\\ref{tab:critical} this is not balanced, so the absolute "
        "silent error rates are the ones a deployment of this shape would face.",
        "tab:mixed",
    )
    write_detector_table(PAPER_DIR / "tables" / "detector.tex", detectors, per_class_detectors)
    write_sensitivity_table(PAPER_DIR / "tables" / "sensitivity.tex", sensitivity)
    write_canonical_detector_table(
        PAPER_DIR / "tables" / "detector_canonical.tex", canonical_per_class
    )
    write_vacuity_table(PAPER_DIR / "tables" / "vacuity.tex", vacuity)
    write_detection_figure(PAPER_DIR / "figures" / "detection_recall.tex", per_class_detectors)
    write_silent_error_figure(PAPER_DIR / "figures" / "silent_errors.tex", mixed_metrics)

    summary = render_summary(payload)
    (RESULTS_DIR / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


def _dump(obj) -> dict:
    out = asdict(obj)
    for key, value in list(out.items()):
        if isinstance(value, float):
            out[key] = round(value, 6)
    for derived in ("coverage", "accuracy_answered", "accuracy_overall", "silent_error_rate",
                    "certificate_rate", "recall", "safety", "over_abstention"):
        if hasattr(obj, derived):
            out[derived] = round(getattr(obj, derived), 6)
    return out


def render_summary(payload: dict) -> str:
    lines = [
        "# AI4Math reliability study — run summary",
        "",
        f"seed = {payload['seed']}, n per class = {payload['n_per_class']}",
        "",
        "## Corpus",
        "",
    ]
    for key, value in payload["counts"].items():
        lines.append(f"- {key}: {value}")
    inv = payload["blind_spot_invariance"]
    lines += [
        "",
        "## Verification blind spot (Proposition 2)",
        "",
        f"- critical pairs: {inv['pairs']}",
        f"- identical IR produced for both members: {100 * inv['identical_ir_fraction']:.1f}%",
        f"- identical verdict returned for both members: {100 * inv['identical_verdict_fraction']:.1f}%",
        "",
        "## Pipelines on the critical set",
        "",
        "| pipeline | coverage | acc|answered | silent error | certificate |",
        "|---|---|---|---|---|",
    ]
    for name, m in payload["pipelines_critical"].items():
        lines.append(
            f"| {name} | {100 * m['coverage']:.1f}% | {100 * m['accuracy_answered']:.1f}% | "
            f"{100 * m['silent_error_rate']:.1f}% | {100 * m['certificate_rate']:.1f}% |"
        )
    lines += [
        "",
        "## Pipelines on the prevalence-controlled corpus (non-balanced headline)",
        "",
        "| pipeline | coverage | acc|answered | silent error | certificate |",
        "|---|---|---|---|---|",
    ]
    for name, m in payload["pipelines_mixed"].items():
        lines.append(
            f"| {name} | {100 * m['coverage']:.1f}% | {100 * m['accuracy_answered']:.1f}% | "
            f"{100 * m['silent_error_rate']:.1f}% | {100 * m['certificate_rate']:.1f}% |"
        )
    lines += [
        "",
        "## Pipelines on the benign paraphrase set (abstention = pure cost)",
        "",
        "| pipeline | coverage | acc|answered | silent error | certificate |",
        "|---|---|---|---|---|",
    ]
    for name, m in payload["pipelines_benign"].items():
        lines.append(
            f"| {name} | {100 * m['coverage']:.1f}% | {100 * m['accuracy_answered']:.1f}% | "
            f"{100 * m['silent_error_rate']:.1f}% | {100 * m['certificate_rate']:.1f}% |"
        )
    lines += [
        "",
        "## Anchors as reduction-error detectors",
        "",
        "An instance is *unfaithful* when the blind reductor's IR differs from the reference IR.",
        "",
        "| mechanism | n unfaithful | recall (violation) | safety (withheld) | n faithful | over-abstention |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in payload["detectors_overall"].items():
        lines.append(
            f"| {name} | {m['unfaithful']} | {100 * m['recall']:.1f}% | {100 * m['safety']:.1f}% | "
            f"{m['faithful']} | {100 * m['over_abstention']:.1f}% |"
        )
    lines += [
        "",
        "## Sensitivity to the lexicon gap",
        "",
        "| p (canonical form) | A-lex recall | A-lex over-abst. | A-type recall | A-type over-abst. |",
        "|---|---|---|---|---|",
    ]
    for row in payload["lexicon_sensitivity"]:
        lines.append(
            f"| {row['prior']:.2f} | {100 * row['lexical_recall']:.1f}% | {100 * row['lexical_over_abstention']:.1f}% | "
            f"{100 * row['typed_recall']:.1f}% | {100 * row['typed_over_abstention']:.1f}% |"
        )
    lines += ["", "## Detection at p = 1.00 (paraphrase confound removed)", "",
              "| class | n unfaithful | A-lex recall | A-type recall | A-lex over-abst. | A-type over-abst. |",
              "|---|---|---|---|---|---|"]
    for cls, mechs in payload["detectors_canonical_per_class"].items():
        lex, typ = mechs["verify+lexical_anchors"], mechs["verify+typed_anchors"]
        lines.append(
            f"| {cls} | {lex['unfaithful']} | {100 * lex['recall']:.1f}% | {100 * typ['recall']:.1f}% | "
            f"{100 * lex['over_abstention']:.1f}% | {100 * typ['over_abstention']:.1f}% |"
        )
    lines += ["", "## Vacuity warnings (claims that are true only vacuously)", "",
              "| pipeline | n | answered | vacuity warned | correct |", "|---|---|---|---|---|"]
    for name, m in payload["vacuity"].items():
        lines.append(f"| {name} | {m['n']} | {m['answered']} | {m['warned']} | {m['correct']} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
