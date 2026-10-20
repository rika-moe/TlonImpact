"""Public member projection of process metric snapshots."""

from copy import deepcopy
from typing import Any

from tlon.organization.mail.mail import HUMAN_ID_PREFIX


def _member_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Present member identities without changing the underlying metrics ledger."""
    result = deepcopy(snapshot)
    for name, entry in result.get("metrics", {}).items():
        for point in entry["points"]:
            attributes = {}
            for key, value in point["attributes"].items():
                public_key = "member_id" if key in {"agent", "agent_id"} else key
                if key in {"agent", "agent_id", "endorser"} and isinstance(value, str):
                    value = value.removeprefix(HUMAN_ID_PREFIX)
                attributes[public_key] = value
            point["attributes"] = attributes
        if name == "tlon.mail.sent":
            entry["description"] = (
                "Cumulative accepted mails, one per mail; member_id is the public sender ID"
            )
        elif name == "tlon.mail.delivered":
            entry["description"] = (
                "Cumulative recipient inbox/gateway successes; not reads, replies or execution"
            )
            if entry["points"]:
                entry["points"] = [
                    {
                        "attributes": {},
                        "value": sum(point["value"] for point in entry["points"]),
                    }
                ]
    return result
