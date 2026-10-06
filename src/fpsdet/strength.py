"""Offline evidence strength: ``fpsdet.strength/1``. Research only; the scorer never imports it.

The question: using only a frozen development half of one labelled population, how much more often did a
detector fire for positive-labelled players than for comparison players, among players it could run on, and
did the untouched evaluation half behave the same way? The answer is a label-conditioned evidence ratio. It
is a likelihood ratio in form, P(fire | positive label) / P(fire | comparison label), but the labels are not
ground truth ("RGL cheating-ban labelled" is not "cheated in every scored match"), so it is never called one
and never becomes a weight, a threshold or a probability that anyone cheated.

Everything below was declared before any estimate was computed, and is the same for every detector:

- The split (``fpsdet.calibration-split/1``): SHA-256 of the recipe and the unit of independence the dataset
  declares (the account, or the match where a player is one match). First byte under 128 is development,
  the rest evaluation. Nothing about a player's label, decision or numbers goes in.
- The estimator: each group's firing rate gets a Jeffreys prior, Beta(1/2, 1/2), and its posterior is
  Beta(fires + 1/2, eligible - fires + 1/2). The ratio is the positive posterior mean over the comparison
  posterior mean, so it is finite even with no comparison fires. Its credible set comes from each rate's
  central interval at sqrt(0.95): with independent groups, both hold with 95% posterior probability, so the
  set holds the ratio with at least 95%. Beta(1, 1) is reported beside it as a sensitivity check only.
- The gates: P8's. No estimate below MIN_DESCRIPTIVE eligible players in either group of the development
  half; ``measured`` from MIN_MEASURED.
- Stability, checked against the evaluation half by the development posterior predictive (beta-binomial):
  ``unsupported`` (no estimate, or the development set does not exclude 1), ``unstable`` (the evaluation
  fires fall outside the development predictive 95% interval in either group), ``tentative`` (consistent,
  but the evaluation half is too small or its own set does not exclude 1), ``replicated`` (consistent, and
  the evaluation half's own set excludes 1). One split never validates anything.
- The reverse split (fit evaluation, check development) is reported as sensitivity only, never averaged in.

Only player-level "fired at least once" is estimated. Detector estimates are never combined: two detectors
on one player are not independent evidence, and the co-occurrence tags say why.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from .calibration import EVALUATION_SCHEMA, MIN_DESCRIPTIVE, MIN_MEASURED, NATIVE_KINDS, check_dataset, verify_artifact
from .evidence import KINDS, canonical_json
from .provenance import normalized_source

STRENGTH_SCHEMA = "fpsdet.strength/1"
SPLIT_RECIPE = "fpsdet.calibration-split/1"
ESTIMATOR_RECIPE = "fpsdet.strength-estimator/1"
CODE_RECIPE = "fpsdet.strength-code/1"
PRIOR = {"name": "jeffreys", "alpha": 0.5, "beta": 0.5}
SENSITIVITY_PRIOR = {"name": "uniform", "alpha": 1.0, "beta": 1.0}
CREDIBLE = 0.95
# Each rate's central interval level, so that two independent ones hold together with CREDIBLE probability.
PER_RATE = math.sqrt(CREDIBLE)
STABILITY = ("unsupported", "unstable", "tentative", "replicated")
SPLITS = ("development", "evaluation")
# Families whose "any finding" is estimated as its own event, where the data allows.
FAMILIES = ("human_baseline", "physics", "weapon_rules", "relationship")
# Never given a strength, whatever a dataset shows: no labelled real-world data stands behind them.
NOT_CALIBRATED = {
    "challenge": "challenges are planned by a server; no public dataset carries them, so they are qualified on controlled fixtures only",
    "external": "provider-specific rates are unavailable; external records keep the fusion rules and never borrow native strength",
}
SIGNIFICANT = 6


class StrengthError(ValueError):
    """The inputs cannot be estimated from as given. Nothing was written."""


def _g(value: float | None) -> float | None:
    return None if value is None else float(f"{value:.{SIGNIFICANT}g}")


# The beta distribution, standard library only.


def _continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's continued fraction for the incomplete beta function."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        for numerator in (m * (b - m) * x / ((a + m2 - 1.0) * (a + m2)), -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1.0))):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _continued_fraction(a, b, x) / a
    return 1.0 - front * _continued_fraction(b, a, 1.0 - x) / b


def beta_quantile(q: float, a: float, b: float) -> float:
    """The q-quantile of Beta(a, b), by bisection. Deterministic, and fine down to 1e-30."""
    low, high = 0.0, 1.0
    for _ in range(110):
        mid = (low + high) / 2.0
        if beta_cdf(mid, a, b) < q:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def predictive_interval(n: int, a: float, b: float, level: float = 0.95) -> tuple[int, int]:
    """The central ``level`` interval of fires among ``n`` new players, under a Beta(a, b) posterior rate."""
    log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    tail = (1.0 - level) / 2.0
    total, low, high = 0.0, None, n
    for x in range(n + 1):
        log_pmf = (
            math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1)
            + math.lgamma(x + a) + math.lgamma(n - x + b) - math.lgamma(n + a + b) - log_beta
        )
        total += math.exp(log_pmf)
        if low is None and total >= tail:
            low = x
        if total >= 1.0 - tail:
            high = x
            break
    return (low if low is not None else 0), high


# One group's rate, and the ratio between two groups.


def posterior(fires: int, eligible: int, prior: Mapping = PRIOR) -> dict:
    """A group's firing rate: the counts, the posterior mean, its 95% interval, and the wider one the ratio uses."""
    a, b = fires + prior["alpha"], eligible - fires + prior["beta"]
    return {
        "fires": fires,
        "eligible": eligible,
        "mean": _g(a / (a + b)),
        "ci95": [_g(beta_quantile(0.025, a, b)), _g(beta_quantile(0.975, a, b))],
        "for_ratio": [_g(beta_quantile((1 - PER_RATE) / 2, a, b)), _g(beta_quantile((1 + PER_RATE) / 2, a, b))],
    }


def evidence_ratio(positive: Mapping, comparison: Mapping) -> dict:
    """The label-conditioned evidence ratio: positive posterior mean over comparison posterior mean, with a
    credible set of at least 95%. Finite whenever the prior is proper, fires or not."""
    p_lo, p_hi = positive["for_ratio"]
    c_lo, c_hi = comparison["for_ratio"]
    empty = [name for name, group in (("positive", positive), ("comparison", comparison)) if group["fires"] == 0]
    return {
        "ratio": _g(positive["mean"] / comparison["mean"]),
        "credible95": [_g(p_lo / c_hi), _g(p_hi / c_lo)],
        "prior_dominated": bool(empty),
        # Which groups had no fires. With none in either, the ratio is the prior alone: (n_comparison + 1) / (n_positive + 1).
        "no_fires": "both" if len(empty) == 2 else (empty[0] if empty else None),
    }


def _sample(positive_n: int, comparison_n: int) -> str:
    low = min(positive_n, comparison_n)
    if low < MIN_DESCRIPTIVE:
        return "insufficient_sample"
    return "measured" if low >= MIN_MEASURED else "descriptive_only"


def fit(counts: Mapping[str, tuple[int, int]], prior: Mapping = PRIOR) -> dict:
    """Fit one detector on one half: ``counts`` is {"positive": (fires, eligible), "comparison": (...)}."""
    (pf, pn), (cf, cn) = counts["positive"], counts["comparison"]
    out: dict = {"sample": _sample(pn, cn), "positive": {"fires": pf, "eligible": pn}, "comparison": {"fires": cf, "eligible": cn}}
    if out["sample"] == "insufficient_sample":
        return out
    out["positive"] = posterior(pf, pn, prior)
    out["comparison"] = posterior(cf, cn, prior)
    out["evidence_ratio"] = evidence_ratio(out["positive"], out["comparison"])
    return out


def check(fitted: Mapping, held_out: Mapping[str, tuple[int, int]], prior: Mapping = PRIOR) -> dict:
    """What the held-out half showed, against the fitted half's posterior predictive, and the stability status."""
    (pf, pn), (cf, cn) = held_out["positive"], held_out["comparison"]
    out: dict = {
        "positive": {"fires": pf, "eligible": pn, "rate": _g(pf / pn) if pn else None},
        "comparison": {"fires": cf, "eligible": cn, "rate": _g(cf / cn) if cn else None},
    }
    observed = fit(held_out, prior)
    out["sample"] = observed["sample"]
    if "evidence_ratio" in observed:
        out["evidence_ratio"] = observed["evidence_ratio"]
    if "evidence_ratio" not in fitted or fitted["evidence_ratio"]["credible95"][0] <= 1.0:
        out["stability"] = "unsupported"
        out["why"] = "no estimate on the fitted half" if "evidence_ratio" not in fitted else "the fitted half's credible set does not exclude 1"
        return out
    consistent = True
    for group, (fires, eligible) in (("positive", (pf, pn)), ("comparison", (cf, cn))):
        f = fitted[group]
        low, high = predictive_interval(eligible, f["fires"] + prior["alpha"], f["eligible"] - f["fires"] + prior["beta"])
        out[group]["predicted95"] = [low, high]
        out[group]["within_prediction"] = low <= fires <= high
        consistent = consistent and low <= fires <= high
    if observed["sample"] != "insufficient_sample" and not consistent:
        out["stability"], out["why"] = "unstable", "the held-out fires fall outside the fitted half's prediction"
    elif observed["sample"] == "insufficient_sample":
        out["stability"], out["why"] = "tentative", "the held-out half is too small to check"
    elif observed["evidence_ratio"]["credible95"][0] <= 1.0:
        out["stability"], out["why"] = "tentative", "consistent, but the held-out half's own set does not exclude 1"
    else:
        out["stability"], out["why"] = "replicated", "consistent, and the held-out half's own set excludes 1"
    return out


# From an evaluation to a strength artifact.


def split_of(unit: str) -> str:
    digest = hashlib.sha256(f"{SPLIT_RECIPE}\0{unit}".encode("utf-8")).digest()
    return "development" if digest[0] < 128 else "evaluation"


def _unit(row: Mapping, dataset: Mapping) -> str:
    return row["split_group"] if dataset.get("split_unit") == "match" else row["player"]


def _counts(rows: Iterable[Mapping], positive: str, comparison: str, kinds: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    out = {}
    for name, label in (("positive", positive), ("comparison", comparison)):
        eligible = [row for row in rows if row["label"] == label and any(kind in row["evaluated"] for kind in kinds)]
        out[name] = (sum(any(kind in row["fired"] for kind in kinds) for row in eligible), len(eligible))
    return out


def _entry(halves: Mapping[str, list[Mapping]], positive: str, comparison: str, kinds: tuple[str, ...]) -> dict:
    development = _counts(halves["development"], positive, comparison, kinds)
    evaluation = _counts(halves["evaluation"], positive, comparison, kinds)
    fitted = fit(development)
    reverse = fit(evaluation)
    return {
        "development": fitted,
        "evaluation": check(fitted, evaluation),
        "sensitivity": {
            "uniform_prior": fit(development, SENSITIVITY_PRIOR).get("evidence_ratio"),
            # Fitted on evaluation, checked on development: how much the answer depends on which half fits.
            "reverse_split": {"fitted": reverse, "check": check(reverse, development)},
        },
    }


def _tags(rows: Iterable[Mapping]) -> dict:
    """What two detectors that fired on one player share, from the evidence graph, as P8's rows record it."""
    names = {"dependency": "explicit_dependency", "cohort": "shared_cohort", "domain": "shared_domain"}
    out: dict[str, Counter] = {}
    for row in rows:
        for a, b, shared in row.get("pairs", []):
            tags = out.setdefault(f"{a} + {b}", Counter())
            tags["players"] += 1
            for name in shared:
                if name in names:
                    tags[names[name]] += 1
            if "dependency" not in shared:
                tags["co_occurring_no_explicit_dependency"] += 1
    order = {f"{a} + {b}": (NATIVE_KINDS.index(a), NATIVE_KINDS.index(b)) for a in NATIVE_KINDS for b in NATIVE_KINDS}
    return {pair: dict(sorted(tags.items())) for pair, tags in sorted(out.items(), key=lambda item: order[item[0]])}


def code_digest() -> str:
    here = Path(__file__).resolve().parent
    hasher = hashlib.sha256(CODE_RECIPE.encode("ascii") + b"\0")
    for name in ("strength.py", "calibration.py", "statsutil.py"):
        source = normalized_source((here / name).read_bytes())
        hasher.update(f"fpsdet.{name[:-3]}\0{len(source)}\0".encode("ascii"))
        hasher.update(source)
    return "sha256:" + hasher.hexdigest()


def estimator() -> dict:
    return {
        "recipe": ESTIMATOR_RECIPE,
        "event": "the detector fired at least once on the player, among players it could run on",
        "prior": dict(PRIOR),
        "sensitivity_prior": dict(SENSITIVITY_PRIOR),
        "rate": "posterior mean of Beta(fires + alpha, eligible - fires + beta)",
        "ratio": "positive posterior mean / comparison posterior mean (label-conditioned evidence ratio)",
        "credible": {"level": CREDIBLE, "per_rate": _g(PER_RATE), "method": "product of the two central per-rate intervals"},
        "gates": {"min_descriptive": MIN_DESCRIPTIVE, "min_measured": MIN_MEASURED, "applied_to": "the development half"},
        "stability": list(STABILITY),
        "predictive": "beta-binomial, central 95%",
    }


def strength(evaluation: Mapping) -> dict:
    """The strength artifact for one evaluation (fpsdet.evaluation/2). Refuses an evaluation that does not verify."""
    if evaluation.get("schema") != EVALUATION_SCHEMA:
        raise StrengthError(f"strength is estimated from {EVALUATION_SCHEMA}, not {evaluation.get('schema')!r}")
    problems = verify_artifact(evaluation)
    if problems:
        raise StrengthError("the evaluation does not verify: " + "; ".join(problems[:3]))
    dataset = check_dataset(evaluation["dataset"])
    groups = dataset["labels"]
    positive = next(group["label"] for group in groups if group["role"] == "positive")
    comparison = next(group["label"] for group in groups if group["role"] == "comparison")
    rows = evaluation["rows"]
    if dataset.get("split_unit") == "match" and any("split_group" not in row for row in rows):
        raise StrengthError("the dataset splits by match, but its rows do not name their match")
    halves = {name: [] for name in SPLITS}
    for row in rows:
        halves[split_of(_unit(row, dataset))].append(row)
    observable = evaluation["observability"]
    detectors = []
    for kind in NATIVE_KINDS:
        family = KINDS[kind][0]
        entry: dict = {"kind": kind, "family": family}
        if family in NOT_CALIBRATED:
            entry.update(status="not_calibrated", why=NOT_CALIBRATED[family])
        elif not observable[kind]["observable"]:
            entry.update(status="not_calibrated", why="not observable with this dataset: " + ", ".join(observable[kind]["reasons"]))
        else:
            entry.update(_entry(halves, positive, comparison, (kind,)))
            entry["status"] = entry["development"]["sample"]
        detectors.append(entry)
    families = []
    for family in FAMILIES:
        kinds = tuple(kind for kind in NATIVE_KINDS if KINDS[kind][0] == family and observable[kind]["observable"])
        entry = {"family": family, "detectors": list(kinds)}
        if not kinds:
            entry.update(status="not_calibrated", why="no detector of this family is observable with this dataset")
        else:
            entry.update(_entry(halves, positive, comparison, kinds))
            entry["status"] = entry["development"]["sample"]
        families.append(entry)
    composition = {name: dict(sorted(Counter(row["label"] for row in halves[name]).items())) for name in SPLITS}
    artifact = {
        "schema": STRENGTH_SCHEMA,
        "research_only": "Offline research. The scorer never reads it, and nothing here is a threshold, a weight or a probability that anyone cheated.",
        "evaluation": {"schema": evaluation["schema"], "digest": evaluation["digest"]},
        "dataset": {
            "dataset": dataset["dataset"],
            "digest": "sha256:" + hashlib.sha256(canonical_json(dataset).encode("utf-8")).hexdigest(),
            "labels": [{key: group[key] for key in ("label", "role", "name", "meaning", "not_meaning")} for group in groups],
            "caveats": dataset["caveats"],
            "positive": positive,
            "comparison": comparison,
        },
        "inputs": {key: evaluation["inputs"][key] for key in ("detector", "profile", "cohort", "cohort_mode", "digest")},
        "labels": evaluation["labels"],
        "split": {"recipe": SPLIT_RECIPE, "unit": dataset.get("split_unit", "player"), "composition": composition},
        "estimator": estimator(),
        "code": {"recipe": CODE_RECIPE, "digest": code_digest()},
        "detectors": detectors,
        "families": families,
        "co_occurrence": {"half": "development", "pairs": _tags(halves["development"])},
        "not_calibrated": {**NOT_CALIBRATED, "human_review": "a person's ruling is the person's, not a detector's, and is never given a strength"},
    }
    artifact = json.loads(canonical_json(artifact))
    artifact["digest"] = strength_digest(artifact)
    return artifact


def strength_digest(artifact: Mapping) -> str:
    body = {key: value for key, value in artifact.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(STRENGTH_SCHEMA.encode("ascii") + b"\0" + canonical_json(body).encode("utf-8")).hexdigest()


def _close(a, b) -> bool:
    """Equal, with floats to a relative 1e-5: a different libm may move the last digit of a gamma function."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return a == b


def verify_strength(artifact: Mapping, evaluation: Mapping) -> list[str]:
    """Is a strength artifact what it says, from this evaluation? Empty when it is."""
    if artifact.get("schema") != STRENGTH_SCHEMA:
        return [f"not {STRENGTH_SCHEMA}"]
    problems = []
    if artifact.get("digest") != strength_digest(artifact):
        problems.append("the strength digest does not match its contents")
    if artifact.get("evaluation", {}).get("digest") != evaluation.get("digest"):
        return problems + ["it was not estimated from this evaluation"]
    again = strength(evaluation)
    for key in ("detectors", "families", "co_occurrence", "split", "estimator"):
        if not _close(again[key], artifact[key]):
            problems.append(f"the {key} do not follow from the evaluation")
    return problems


def dumps(artifact: Mapping) -> str:
    return json.dumps(artifact, indent=1, ensure_ascii=False) + "\n"


# The report.


def _rate(cell: Mapping) -> str:
    return f"{cell['fires']}/{cell['eligible']:,}"


def _ratio(cell: Mapping | None) -> str:
    if not cell:
        return "-"
    low, high = cell["credible95"]
    flag = {
        "both": " (no fires in either group: the prior alone, not evidence)",
        "positive": " (no positive fires: the prior sets the low end)",
        "comparison": " (no comparison fires: the prior sets the far end)",
    }.get(cell.get("no_fires") or "", "")
    return f"{cell['ratio']:.3g} ({low:.3g}–{high:.3g}){flag}"


def _observed(check_: Mapping) -> str:
    pos, comp = check_["positive"], check_["comparison"]
    return f"{_rate(pos)} vs {_rate(comp)}"


def render_markdown(artifact: Mapping) -> str:
    dataset = artifact["dataset"]
    names = {group["label"]: group["name"] for group in dataset["labels"]}
    pos, comp = names[dataset["positive"]], names[dataset["comparison"]]
    out: list[str] = []
    add = out.append
    add(f"# Evidence strength: {dataset['dataset']}")
    add("")
    add(f"Generated by `fpsdet evaluate strength` ({artifact['schema']}). Offline research: the scorer never reads it, and nothing here is a threshold, a weight, or a probability that any player cheated. The direct measurement is the evaluation it was estimated from; this is a separate research layer.")
    add("")
    add("## Read this first")
    add("")
    for group in dataset["labels"]:
        if group["role"] in ("positive", "comparison") and group["label"] in (dataset["positive"], dataset["comparison"]):
            add(f"- **{group['name']}** ({group['role']}): {group['meaning']} Not: {group['not_meaning']}")
    for caveat in dataset["caveats"]:
        add(f"- {caveat}")
    add(f"- The number below is a **label-conditioned evidence ratio**: how much more often a detector fired for {pos} players than for {comp} players, among players it could run on. It is a likelihood ratio in form only. With these labels it is not one, and it never becomes a weight.")
    add("- Each detector is estimated alone, on \"fired at least once\". Estimates are never multiplied together: findings on one player share cohorts, matches and telemetry.")
    add("")
    split = artifact["split"]
    add("## The split")
    add("")
    add(f"`{split['recipe']}`, by {split['unit']}: a hash of the {split['unit']} id alone, fixed before any estimate. The estimate is fitted on the development half and checked, untouched, on the evaluation half.")
    add("")
    labels = sorted({label for half in split["composition"].values() for label in half})
    add("| Half | " + " | ".join(_md(names.get(label, label)) for label in labels) + " |")
    add("| --- |" + " ---: |" * len(labels))
    for half, counts in split["composition"].items():
        add(f"| {half} | " + " | ".join(f"{counts.get(label, 0):,}" for label in labels) + " |")
    add("")
    est = artifact["estimator"]
    add("## The estimator")
    add("")
    add(f"- Each group's firing rate: Beta({est['prior']['alpha']}, {est['prior']['beta']}) prior (Jeffreys), posterior Beta(fires + ½, eligible − fires + ½), posterior mean.")
    add("- The ratio: positive posterior mean over comparison posterior mean. Finite even with no comparison fires.")
    add(f"- Its credible set: from each rate's central {est['credible']['per_rate']:.4f} interval, so it holds the ratio with at least {est['credible']['level']:.0%} posterior probability.")
    add(f"- No estimate below {est['gates']['min_descriptive']} eligible players in either group of the development half; measured from {est['gates']['min_measured']}.")
    add("- Stability: **replicated** (the evaluation half fell inside the development prediction in both groups, and its own set excludes 1), **tentative** (consistent, but too small or not excluding 1), **unstable** (outside the prediction), **unsupported** (no development estimate, or it does not exclude 1). One split never validates anything.")
    add("")
    add("## Detectors")
    add("")
    add(f"Development: fires/eligible. Ratio: the label-conditioned evidence ratio fitted on development, with its 95% credible set. Evaluation: fires/eligible, {pos} vs {comp}.")
    add("")
    add(f"| Detector | Sample | Dev {_md(pos)} | Dev {_md(comp)} | Ratio (95% credible) | Eval {_md(pos)} vs {_md(comp)} | Eval ratio | Stability |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for entry in artifact["detectors"]:
        if entry["status"] == "not_calibrated":
            continue
        dev, ev = entry["development"], entry["evaluation"]
        add(
            f"| {entry['kind']} | {dev['sample']} | {_rate(dev['positive'])} | {_rate(dev['comparison'])} | {_ratio(dev.get('evidence_ratio'))} | "
            f"{_observed(ev)} | {_ratio(ev.get('evidence_ratio'))} | {ev['stability']} |"
        )
    add("")
    add("**Sensitivity.** The same development counts under a uniform Beta(1, 1) prior, and the split reversed (fitted on evaluation, checked on development). Neither is averaged in.")
    add("")
    add("| Detector | Ratio, uniform prior | Reverse: ratio fitted on evaluation | Reverse: stability |")
    add("| --- | --- | --- | --- |")
    for entry in artifact["detectors"]:
        if entry["status"] == "not_calibrated":
            continue
        reverse = entry["sensitivity"]["reverse_split"]
        add(f"| {entry['kind']} | {_ratio(entry['sensitivity']['uniform_prior'])} | {_ratio(reverse['fitted'].get('evidence_ratio'))} | {reverse['check']['stability']} |")
    add("")
    add("## Families")
    add("")
    add("Any finding in the family, as its own event. Never a combination of the detector estimates.")
    add("")
    add(f"| Family | Sample | Dev {_md(pos)} | Dev {_md(comp)} | Ratio (95% credible) | Eval | Stability |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for entry in artifact["families"]:
        if entry["status"] == "not_calibrated":
            add(f"| {entry['family']} | not_calibrated | - | - | - | - | - |")
            continue
        dev, ev = entry["development"], entry["evaluation"]
        add(f"| {entry['family']} | {dev['sample']} | {_rate(dev['positive'])} | {_rate(dev['comparison'])} | {_ratio(dev.get('evidence_ratio'))} | {_observed(ev)} | {ev['stability']} |")
    add("")
    add("## Not calibrated")
    add("")
    add("| Detector | Why |")
    add("| --- | --- |")
    for entry in artifact["detectors"]:
        if entry["status"] == "not_calibrated":
            add(f"| {entry['kind']} | {_md(entry['why'])} |")
    for name, why in artifact["not_calibrated"].items():
        if name not in ("challenge",):
            add(f"| {name.replace('_', ' ')} | {_md(why)} |")
    add("")
    add("## Co-occurrence")
    add("")
    pairs = artifact["co_occurrence"]["pairs"]
    if pairs:
        add("Pairs of detectors that fired on the same player in the development half, and what the evidence graph says they share. Not combined: these are candidates for future fusion research, nothing more.")
        add("")
        add("| Pair | Players | Explicit dependency | Shared cohort | Shared domain | No explicit dependency |")
        add("| --- | ---: | ---: | ---: | ---: | ---: |")
        for pair, tags in pairs.items():
            add(f"| {pair} | {tags.get('players', 0)} | {tags.get('explicit_dependency', 0)} | {tags.get('shared_cohort', 0)} | {tags.get('shared_domain', 0)} | {tags.get('co_occurring_no_explicit_dependency', 0)} |")
    else:
        add("No two detectors fired on the same player in the development half.")
    add("")
    add("## Provenance")
    add("")
    add(f"- Evaluation `{artifact['evaluation']['digest']}` ({artifact['evaluation']['schema']}), dataset definition `{dataset['digest']}`")
    add(f"- Detector `{artifact['inputs']['detector']}`, profile `{artifact['inputs']['profile']}`, cohort `{artifact['inputs']['cohort']}`")
    add(f"- Labels `{artifact['labels']['digest']}`; estimator `{est['recipe']}`; code `{artifact['code']['digest']}`")
    add(f"- Strength digest `{artifact['digest']}` (not signed, and in no case packet)")
    add("")
    return "\n".join(out)


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")
