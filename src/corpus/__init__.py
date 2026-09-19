"""A labelled corpus of agent tool-boundary cases, and a detector-agnostic harness.

Precision and recall only exist if somebody wrote the cases down. This package holds the
cases and scores any detector that can print JSON, so a claim about a scanner can be
checked rather than believed — with the standing caveat that the corpus was authored by
the same person who wrote the detectors it scores.
"""

from .harness import (
    SELF_AUTHORED_CAVEAT, Case, Outcome, evaluate, load_cases, materialise, metrics,
    report, run_detector, score,
)

__all__ = [
    "SELF_AUTHORED_CAVEAT", "Case", "Outcome", "evaluate", "load_cases",
    "materialise", "metrics", "report", "run_detector", "score",
]

__version__ = "0.1.0"
