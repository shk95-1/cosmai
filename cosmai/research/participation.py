"""Apply agent-interpreted contributions with explicit, local dependency effects."""

from __future__ import annotations


def required_decisions(state):
    return [d for d in state["discussions"].values() if d["status"] == "pending" and d["dependent_tasks"]]


def integrate(state: dict, args: dict):
    item = next((f for f in state["feedback"] if f.get("id") == args["feedback_id"]), None)
    if not item or item.get("status") != "pending":
        raise ValueError("feedback is unknown, stale or already interpreted")
    kind = args["kind"]
    target = args["discussion_id"] or item["discussion"]
    if item["discussion"] and target != item["discussion"]:
        raise ValueError("cannot redirect a response to another discussion")
    if kind != "goal_change" and any(
        args[k]
        for k in ("affected_tasks", "affected_hypotheses", "affected_discussions", "objective", "preferences")
    ):
        raise ValueError("only a genuine goal change may invalidate named work or revise objectives")
    for field, collection in (
        ("affected_tasks", "tasks"),
        ("affected_hypotheses", "hypotheses"),
        ("affected_discussions", "discussions"),
    ):
        if not set(args[field]) <= state[collection].keys():
            raise ValueError(f"{field} must name existing records")
    if kind == "decision":
        packet = state["discussions"].get(target)
        if not packet or packet["status"] != "pending":
            raise ValueError("decision must identify a current pending discussion")
        # Unbound natural replies may target only questions visible when the input arrived.
        if packet["state_revision"] > item["observed_revision"]:
            raise ValueError("response predates this discussion")
        if item.get("discussion_revision") is not None and (
            packet["state_revision"] != item["discussion_revision"]
        ):
            raise ValueError("response targets a superseded discussion version")
        packet.update(
            status="answered",
            response={
                "feedback_id": item["id"],
                "text": item["text"],
                "interpretation": args["interpretation"],
            },
        )
        still_blocked = {t for d in required_decisions(state) for t in d["dependent_tasks"]}
        for task_id in packet["dependent_tasks"]:
            task = state["tasks"][task_id]
            if task_id not in still_blocked and task["status"] == "blocked":
                task["status"] = "ready"
    elif kind == "goal_change":
        affected = set(args["affected_tasks"])
        for did in args["affected_discussions"]:
            affected.update(state["discussions"][did]["dependent_tasks"])
        while True:
            expanded = affected | {
                t["id"] for t in state["tasks"].values() if set(t["dependencies"]) & affected
            }
            if expanded == affected:
                break
            affected = expanded
        state["goal_revision"] += 1
        if args["objective"]:
            state["mission"]["objective"] = args["objective"]
        if args["preferences"] is not None:
            state["mission"]["preferences"] = args["preferences"]
        for task_id in affected:
            state["tasks"][task_id]["status"] = "superseded"
        for hid in args["affected_hypotheses"]:
            state["hypotheses"][hid]["needs_reconsideration"] = True
        for did, packet in state["discussions"].items():
            if packet["status"] == "pending" and (
                did in args["affected_discussions"] or set(packet["dependent_tasks"]) & affected
            ):
                packet["status"] = "superseded"
        # Preserve queued old replies as stale, never apply them to a replacement decision.
        for other in state["feedback"]:
            packet = state["discussions"].get(other.get("discussion"))
            if other.get("status") == "pending" and packet and packet["status"] == "superseded":
                other.update(status="stale", applied=False)
    item.update(
        status="interpreted",
        applied=True,
        interpretation=args,
        interpreted_goal_revision=state["goal_revision"],
    )
    state.update(status="running", outcome=None, reason="Participant contribution interpreted")
