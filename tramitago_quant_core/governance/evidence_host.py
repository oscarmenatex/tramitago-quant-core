"""Only one machine may seal evidence.

WHY THIS EXISTS, and it is a real incident rather than a precaution. The VM was
four commits behind, so its copy of the Finding registry still had a Finding
OPEN. A research runner was executed there, promoted that Finding, sealed its own
Hypothesis -- a different identity from the one already committed -- and left the
two registries modified in the working tree. The next `git pull` refused to
merge, and the fix for a refused merge is to discard local changes. One
`git checkout --` later the record existed nowhere.

Nothing epistemically was lost: it was a duplicate run of the same pre-declared
test on the same sealed inputs, and the committed chain reproduces. What was lost
is a REPLICATION, and what was demonstrated is that sealed evidence can be
created somewhere it will be destroyed by routine maintenance.

THE RULE: evidence is sealed on ONE host and reaches every other by git. A
machine that only consumes code can pull without ever risking a merge conflict
over a registry, because it never writes one.

THE MARKER IS A FILE, NOT A HOSTNAME OR AN ENVIRONMENT VARIABLE. A hostname can
be matched by accident after a rebuild; an environment variable can be inherited
by a shell nobody meant to grant it. Creating a file in the repository root is a
deliberate act someone has to perform on purpose, and it is gitignored so it
cannot travel with a clone -- which is precisely the failure mode it exists to
stop.

This never blocks the OPERATIONAL loop. The machinery-verification tick writes to
/var/lib/tramitago-quant-core and seals no research artifact, which is why it was
never part of the problem.
"""

from pathlib import Path

EVIDENCE_HOST_MARKER = ".evidence-host"


def evidence_host_marker_path(repo_root):
    return Path(repo_root) / EVIDENCE_HOST_MARKER


def is_evidence_host(repo_root):
    return evidence_host_marker_path(repo_root).is_file()


def require_evidence_host(repo_root, *, what="seal research evidence"):
    """Refuse unless this checkout is the one designated to seal evidence.

    Raises rather than warning. A warning on a long-running script scrolls past,
    and the cost of being wrong here is a sealed record written where routine
    maintenance will delete it.
    """
    if is_evidence_host(repo_root):
        return True
    raise SystemExit(
        f"This checkout is not the evidence host, so it must not {what}.\n"
        f"Evidence is sealed on ONE machine and reaches the others by git pull; a "
        f"registry written here would be destroyed by the next merge conflict, which "
        f"has already happened once.\n"
        f"If this IS the machine that seals evidence, say so deliberately:\n"
        f"    touch {evidence_host_marker_path(repo_root)}")
