# AI4Math reliability study — run summary

seed = 20260928, n per class = 40

## Corpus

- critical_pairs: 192
- benign_pairs: 40
- critical_instances: 384
- benign_instances: 80
- per_class: {'P1_quantifier_drift': 40, 'P2_domain_kind_drift': 40, 'P3_strictness_drift': 40, 'P4_assumption_omission': 40, 'P5_vacuity': 32}

## Verification blind spot (Proposition 2)

- critical pairs: 192
- identical IR produced for both members: 100.0%
- identical verdict returned for both members: 100.0%

## Pipelines on the critical set

| pipeline | coverage | acc|answered | silent error | certificate |
|---|---|---|---|---|
| gen_only | 100.0% | 50.0% | 50.0% | 0.0% |
| verify | 100.0% | 50.0% | 50.0% | 100.0% |
| verify+lexical_anchors | 26.6% | 80.4% | 5.2% | 26.6% |
| verify+typed_anchors | 19.8% | 100.0% | 0.0% | 19.8% |
| typed_anchored | 50.5% | 100.0% | 0.0% | 50.5% |
| typed_best_effort | 97.9% | 100.0% | 0.0% | 50.5% |
| oracle | 100.0% | 100.0% | 0.0% | 100.0% |

## Pipelines on the prevalence-controlled corpus (non-balanced headline)

| pipeline | coverage | acc|answered | silent error | certificate |
|---|---|---|---|---|
| gen_only | 100.0% | 44.1% | 55.9% | 0.0% |
| verify | 100.0% | 44.1% | 55.9% | 100.0% |
| verify+lexical_anchors | 21.4% | 60.8% | 8.4% | 21.4% |
| verify+typed_anchors | 11.8% | 100.0% | 0.0% | 11.8% |
| typed_anchored | 48.7% | 100.0% | 0.0% | 48.7% |
| typed_best_effort | 96.6% | 100.0% | 0.0% | 48.7% |
| oracle | 100.0% | 100.0% | 0.0% | 100.0% |

## Pipelines on the benign paraphrase set (abstention = pure cost)

| pipeline | coverage | acc|answered | silent error | certificate |
|---|---|---|---|---|
| gen_only | 100.0% | 100.0% | 0.0% | 0.0% |
| verify | 100.0% | 100.0% | 0.0% | 100.0% |
| verify+lexical_anchors | 65.0% | 100.0% | 0.0% | 65.0% |
| verify+typed_anchors | 47.5% | 100.0% | 0.0% | 47.5% |
| typed_anchored | 47.5% | 100.0% | 0.0% | 47.5% |
| typed_best_effort | 100.0% | 100.0% | 0.0% | 47.5% |
| oracle | 100.0% | 100.0% | 0.0% | 100.0% |

## Anchors as reduction-error detectors

An instance is *unfaithful* when the blind reductor's IR differs from the reference IR.

| mechanism | n unfaithful | recall (violation) | safety (withheld) | n faithful | over-abstention |
|---|---|---|---|---|---|
| verify+lexical_anchors | 244 | 91.8% | 91.8% | 220 | 39.1% |
| verify+typed_anchors | 244 | 86.1% | 100.0% | 220 | 48.2% |
| ir_verification_only | 244 | 0.0% | 0.0% | 220 | 0.0% |

## Sensitivity to the lexicon gap

| p (canonical form) | A-lex recall | A-lex over-abst. | A-type recall | A-type over-abst. |
|---|---|---|---|---|
| 0.50 | 96.3% | 74.3% | 73.4% | 80.7% |
| 0.75 | 91.8% | 41.4% | 86.1% | 45.7% |
| 1.00 | 83.6% | 0.0% | 100.0% | 0.0% |

## Detection at p = 1.00 (paraphrase confound removed)

| class | n unfaithful | A-lex recall | A-type recall | A-lex over-abst. | A-type over-abst. |
|---|---|---|---|---|---|
| P1_quantifier_drift | 40 | 100.0% | 100.0% | 0.0% | 0.0% |
| P2_domain_kind_drift | 40 | 0.0% | 100.0% | 0.0% | 0.0% |
| P3_strictness_drift | 60 | 100.0% | 100.0% | 0.0% | 0.0% |
| P4_assumption_omission | 40 | 100.0% | 100.0% | 0.0% | 0.0% |
| P5_vacuity | 64 | 100.0% | 100.0% | 0.0% | 0.0% |

## Vacuity warnings (claims that are true only vacuously)

| pipeline | n | answered | vacuity warned | correct |
|---|---|---|---|---|
| gen_only | 32 | 32 | 0 | 32 |
| verify | 32 | 32 | 0 | 32 |
| verify+lexical_anchors | 32 | 0 | 0 | 0 |
| verify+typed_anchors | 32 | 0 | 0 | 0 |
| typed_anchored | 32 | 13 | 13 | 13 |
| typed_best_effort | 32 | 32 | 32 | 32 |
| oracle | 32 | 32 | 32 | 32 |
