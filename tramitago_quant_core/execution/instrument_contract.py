"""The instrument contract: what each instrument may be traded with, and where.

WHY IT EXISTS. The operating chain in pipeline.py used to know exactly one instrument,
BTC-USD, and its terms were literals scattered over the proposal, the revalidation and the
request: capital exactly 200 USD, exposure at most 50, an operational risk budget of
exactly 5. Trading anything else meant changing those literals in place, which is
changing what protects real capital as a side effect. The contract makes the terms DATA,
declared per instrument, and keeps the two kinds of instrument apart:

  BTC-USD  The original instrument. Its terms are CONSTANTS in this file, equal to the old
           literals to the last digit, allowed in PAPER and LIVE, and not overridable by
           any file. Every BTC-USD behaviour of the chain is unchanged.

  others   Equities and ETFs, declared in config/instrument_contracts.json. PAPER ONLY, and
           that is not a setting: no field can name another environment and the loader
           refuses a file that tries. A real-capital order for one of them cannot be
           prepared, validated or executed, whatever the file says.

THE TERMS ARE BOUND TO THE ORDER. `contract_identity` is a digest of the terms, carried by
every proposal made under one. If the file changes between the Director approving a
proposal and the order being revalidated, the identity no longer matches and the
revalidation fails, which is the same guarantee the risk contract identity gives.

AN EXIT MUST NEVER BE BLOCKED BY AN ENTRY CAP. A short that has run against its holder can
need a cover larger than the notional it opened with, and a chain that refused it for that
would trap the position it exists to close. Exits have their own cap, `max_exit_exposure_usd`,
by default twice the entry cap. BTC-USD keeps one cap for both, as before.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tramitago_quant_core.shared.util import digest, encoded

BTC_USD = "BTC-USD"
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "instrument_contracts.json"
PAPER, LIVE = "PAPER", "LIVE"

# The original pilot's terms. Equal to the literals pipeline.py carried; held to a test.
_BTC_TERMS = {
    "instrument": BTC_USD, "asset_class": "crypto", "symbol": "BTC/USD",
    "environments": [PAPER, LIVE], "time_in_force": "gtc", "can_short": False,
    "max_capital_usd": "200", "max_exposure_usd": "50", "max_exit_exposure_usd": "50",
    "risk_budget_usd": "5",
}

_ALLOWED_KEYS = {"max_capital_usd", "max_exposure_usd", "max_exit_exposure_usd",
                 "risk_budget_usd", "can_short", "max_adverse_move",
                 "max_loss_per_position_usd", "observation_max_age_seconds"}
_REQUIRED_KEYS = {"max_capital_usd", "max_exposure_usd", "risk_budget_usd", "can_short"}
_SYMBOL = re.compile(r"^[A-Z]{1,5}$")
DEFAULT_OBSERVATION_MAX_AGE_SECONDS = 3600


def _decimal(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a decimal")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a decimal, got {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _equity_terms(symbol, raw):
    """One validated equity entry, as terms. Refuses rather than repairs."""
    if not _SYMBOL.match(symbol):
        raise ValueError(f"{symbol!r} is not an equity ticker; crypto pairs are not allowed")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"{symbol}: unknown contract fields {', '.join(sorted(unknown))}; "
                         f"an environment cannot be named, equities are PAPER only")
    absent = _REQUIRED_KEYS - set(raw)
    if absent:
        raise ValueError(f"{symbol}: missing {', '.join(sorted(absent))}")
    if not isinstance(raw["can_short"], bool):
        raise ValueError(f"{symbol}: can_short must be true or false")
    capital = _decimal(raw["max_capital_usd"], f"{symbol} max_capital_usd")
    exposure = _decimal(raw["max_exposure_usd"], f"{symbol} max_exposure_usd")
    exit_cap = _decimal(raw.get("max_exit_exposure_usd", exposure * 2),
                        f"{symbol} max_exit_exposure_usd")
    budget = _decimal(raw["risk_budget_usd"], f"{symbol} risk_budget_usd")
    if not (0 < exposure <= capital):
        raise ValueError(f"{symbol}: exposure must be positive and within capital")
    if exit_cap < exposure:
        raise ValueError(f"{symbol}: the exit cap cannot be below the entry cap")
    if not (0 < budget <= exposure):
        raise ValueError(f"{symbol}: the risk budget must be positive and within exposure")
    terms = {
        "instrument": symbol, "asset_class": "equity", "symbol": symbol,
        "environments": [PAPER], "time_in_force": "day", "can_short": raw["can_short"],
        "max_capital_usd": str(capital), "max_exposure_usd": str(exposure),
        "max_exit_exposure_usd": str(exit_cap), "risk_budget_usd": str(budget),
        "observation_max_age_seconds": int(raw.get(
            "observation_max_age_seconds", DEFAULT_OBSERVATION_MAX_AGE_SECONDS)),
    }
    if terms["observation_max_age_seconds"] <= 0:
        raise ValueError(f"{symbol}: observation_max_age_seconds must be positive")
    if raw["can_short"]:
        if "max_adverse_move" not in raw or "max_loss_per_position_usd" not in raw:
            raise ValueError(f"{symbol}: a shortable instrument must declare max_adverse_move "
                             f"and max_loss_per_position_usd, because a short loses without bound")
        move = _decimal(raw["max_adverse_move"], f"{symbol} max_adverse_move")
        loss = _decimal(raw["max_loss_per_position_usd"], f"{symbol} max_loss_per_position_usd")
        if not 0 < move < 1:
            raise ValueError(f"{symbol}: max_adverse_move must be between 0 and 1")
        if not 0 < loss <= budget:
            raise ValueError(f"{symbol}: the loss per position must be positive and within the "
                             f"risk budget, or one short could exceed what the chain allows")
        terms["max_adverse_move"], terms["max_loss_per_position_usd"] = str(move), str(loss)
    elif "max_adverse_move" in raw or "max_loss_per_position_usd" in raw:
        raise ValueError(f"{symbol}: short terms were given for an instrument that cannot short")
    return terms


def load_equity_contracts(path=DEFAULT_CONFIG):
    """{symbol: terms} from the file; an absent file is no equity contracts at all."""
    path = Path(path)
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("instruments")
    if not isinstance(entries, dict):
        raise ValueError("instrument_contracts.json must hold an object named instruments")
    if BTC_USD in entries:
        raise ValueError(f"{BTC_USD} cannot be declared in a file: its terms are fixed in "
                         f"code, because they protect real capital")
    return {symbol: _equity_terms(symbol, raw) for symbol, raw in entries.items()}


def terms_for(instrument, path=DEFAULT_CONFIG):
    """The terms of an instrument, or None when it has no contract. A corrupt file raises."""
    if instrument == BTC_USD:
        return {**_BTC_TERMS, "environments": list(_BTC_TERMS["environments"])}
    if not isinstance(instrument, str):
        return None
    return load_equity_contracts(path).get(instrument)


def contract_symbols(path=DEFAULT_CONFIG):
    """The equity symbols that have a contract, for the PAPER transport allowlist."""
    return sorted(load_equity_contracts(path))


def contract_identity(terms):
    return "INSTRUMENT_CONTRACT|" + digest(encoded(terms))


def allows_environment(terms, environment):
    return terms is not None and environment in terms["environments"]


def exposure_cap(terms, action):
    """The cap that applies to this action: entries and exits have separate ones."""
    if terms is None:
        return Decimal(-1)
    key = "max_exit_exposure_usd" if action == "EXIT" else "max_exposure_usd"
    return Decimal(terms[key])
