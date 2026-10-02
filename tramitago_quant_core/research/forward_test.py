"""A Hypothesis whose evaluation period has not happened yet.

FORTY-FIVE HYPOTHESES AND NOT ONE WAS TESTED FORWARD. Every verdict this project
holds is retrospective, and every holdout is a slice of the past declared unread.
That discipline is real and it has caught real things -- but a held-back slice of
history is a finite resource, and two populations were declared EXHAUSTED BY
MEASUREMENT today, which is what running out looks like.

The future is the only out-of-sample that renews itself. It is also the only one
that cannot be peeked at, by anyone, for any reason, including by accident.

WHAT MAKES A HYPOTHESIS FORWARD-DATED IS NOT A FLAG. It is a property of its own
sealed content: the evaluation period starts at or after the moment the
Hypothesis was constituted. Nothing has to be declared, trusted or remembered,
and nothing can be set to the wrong value later, because the identity of a
Hypothesis already covers both its period and its creation timestamp. A flag
would be a claim; this is an observation.

THE FAILURE MODE THIS GUARDS, and it is not hypothetical. Declare a forward test
for a year, watch three good months go by, evaluate those three months and call
it the declared test. The period would be wrong, the fold count would be wrong,
and the result would carry the authority of a pre-declaration it does not have.
So a forward test's dataset cannot be built until its period has FULLY elapsed --
not mostly, not nearly. `require_forward_test_ready` raises, and it is wired into
dataset creation rather than left as advice, because advice does not survive the
moment somebody is curious.

A RETROSPECTIVE HYPOTHESIS IS UNAFFECTED. Its period starts long before it was
written, so it is not forward-dated, and every check here is a no-op for it. All
forty-five reproduce untouched.
"""

from tramitago_quant_core.shared.util import epoch, _explicit_utc

FORWARD_TEST_PENDING = "PENDING"
FORWARD_TEST_READY = "READY"
FORWARD_TEST_NOT_FORWARD = "NOT_A_FORWARD_TEST"


def _period(hypothesis):
    constraints = hypothesis.get("constraints")
    if not isinstance(constraints, dict) or not isinstance(constraints.get("period"), dict):
        raise ValueError("Hypothesis has no declared period")
    period = constraints["period"]
    if not _explicit_utc(period.get("start_utc")) \
            or not _explicit_utc(period.get("end_exclusive_utc")):
        raise ValueError("Hypothesis period is not canonical UTC")
    return period


def _created_at(hypothesis):
    """The moment the Hypothesis was sealed. A top-level field, not inside
    creation_context -- which holds who wrote it and what it came from."""
    if not _explicit_utc(hypothesis.get("creation_timestamp")):
        raise ValueError("Hypothesis has no canonical creation timestamp")
    return hypothesis["creation_timestamp"]


def is_forward_hypothesis(hypothesis):
    """Whether the evaluation period begins at or after the Hypothesis was sealed.

    Derived, never declared. A Hypothesis cannot become forward-dated later and
    cannot stop being so, because its identity already covers both dates.
    """
    return epoch(_period(hypothesis)["start_utc"]) >= epoch(_created_at(hypothesis))


def forward_test_status(hypothesis, now_utc):
    """PENDING until the declared period has fully elapsed, then READY.

    Fully, not mostly: the test that was declared is the whole period, and a
    partial one is a different test wearing its name.
    """
    if not _explicit_utc(now_utc):
        raise ValueError("The current time must be canonical UTC")
    if not is_forward_hypothesis(hypothesis):
        return FORWARD_TEST_NOT_FORWARD
    return (FORWARD_TEST_READY
            if epoch(now_utc) >= epoch(_period(hypothesis)["end_exclusive_utc"])
            else FORWARD_TEST_PENDING)


def require_forward_test_ready(hypothesis, now_utc):
    """Refuse to touch a forward test before its period has elapsed.

    Raises rather than warning, and is called from dataset creation rather than
    offered as advice, because the moment this matters is the moment somebody is
    curious about how it is going -- and curiosity is exactly what a
    pre-declaration exists to survive.
    """
    status = forward_test_status(hypothesis, now_utc)
    if status != FORWARD_TEST_PENDING:
        return status
    period = _period(hypothesis)
    remaining = (epoch(period["end_exclusive_utc"]) - epoch(now_utc)) // 86400
    raise ValueError(
        f"This Hypothesis was declared forward on {_created_at(hypothesis)[:10]} for "
        f"{period['start_utc'][:10]} to {period['end_exclusive_utc'][:10]}, and that period "
        f"has {remaining} day(s) left to run. Building its dataset now would evaluate a "
        f"shorter period than the one declared and give the result the authority of a "
        f"pre-declaration it does not have. Nothing here can be looked at until it is over.")


def pending_forward_tests(hypotheses, now_utc):
    """Every declared forward test still waiting, soonest first.

    A forward test nobody can see is a forward test nobody will remember, and the
    cost of forgetting one is a year."""
    pending = []
    for hypothesis in hypotheses:
        try:
            status = forward_test_status(hypothesis, now_utc)
        except ValueError:
            continue
        if status != FORWARD_TEST_PENDING:
            continue
        period = _period(hypothesis)
        pending.append({
            "hypothesis_id": hypothesis["hypothesis_id"],
            "declared_at": _created_at(hypothesis),
            "period": dict(period),
            "days_remaining": (epoch(period["end_exclusive_utc"]) - epoch(now_utc)) // 86400,
        })
    return sorted(pending, key=lambda item: item["days_remaining"])
