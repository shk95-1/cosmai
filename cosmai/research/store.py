"""Short PostgreSQL transactions with revision checks and fenced execution leases."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from db.runtime import connect

from .state import Mission, fingerprint, initial_state


class Conflict(RuntimeError):
    pass


class StaleRevision(Conflict):
    pass


class Store:
    def __init__(self, url: str):
        if not url:
            raise ValueError("explicit session database required")
        self.url = url

    @contextmanager
    def connection(self):
        with connect(self.url) as conn:
            conn.row_factory = dict_row
            yield conn

    def create(self, mission: Mission) -> str:
        sid = str(uuid4())
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO research_session(id,state) VALUES (%s,%s)", (sid, Jsonb(initial_state(mission)))
            )
            self._event(conn, sid, 0, "created", {"mission": mission.model_dump()})
        return sid

    def get(self, sid: str) -> dict:
        with self.connection() as conn:
            return self._row(conn, sid)

    def actions(self, sid: str) -> list[dict]:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM research_action WHERE session_id=%s ORDER BY created_at,id", (sid,)
            ).fetchall()

    def events(self, sid: str) -> list[dict]:
        with self.connection() as conn:
            return conn.execute(
                "SELECT * FROM research_event WHERE session_id=%s ORDER BY id", (sid,)
            ).fetchall()

    def _row(self, conn, sid: str, lock=False) -> dict:
        row = conn.execute(
            "SELECT *, clock_timestamp() AS clock FROM research_session WHERE id=%s"
            + (" FOR UPDATE" if lock else ""),
            (sid,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown session")
        return row

    def _event(self, conn, sid, revision, kind, payload):
        conn.execute(
            "INSERT INTO research_event(session_id,revision,kind,payload) VALUES (%s,%s,%s,%s)",
            (sid, revision, kind, Jsonb(payload)),
        )

    def _save(self, conn, row, kind, payload):
        row["revision"] += 1
        conn.execute(
            "UPDATE research_session SET state=%s,revision=%s WHERE id=%s",
            (Jsonb(row["state"]), row["revision"], row["id"]),
        )
        self._event(conn, row["id"], row["revision"], kind, payload)

    def _fence(self, row, token):
        if row["lease_token"] != token or not row["lease_until"] or row["lease_until"] <= row["clock"]:
            raise Conflict("execution lease expired or replaced")

    def acquire(self, sid: str, owner: str) -> int:
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            if row["lease_until"] and row["lease_until"] > row["clock"]:
                raise Conflict("another runner holds this session")
            token = row["lease_token"] + 1
            limits = row["state"]["mission"]["limits"]
            deadline = row["deadline"] or row["clock"] + timedelta(seconds=limits["seconds"])
            conn.execute(
                "UPDATE research_session SET lease_token=%s,lease_owner=%s,lease_until=%s,"
                "deadline=%s WHERE id=%s",
                (token, owner, row["clock"] + timedelta(seconds=limits["call_seconds"] + 30), deadline, sid),
            )
            pending = conn.execute(
                "UPDATE research_action SET status='uncertain' WHERE session_id=%s "
                "AND status='reserved' RETURNING id,kind",
                (sid,),
            ).fetchall()
            if pending:
                row["state"]["usage"]["uncertain"] += len(pending)
                row["state"].update(
                    status="blocked",
                    outcome="reconciliation_required",
                    reason="Interrupted executions need explicit reconciliation; reservations retained",
                )
                self._save(conn, row, "interrupted", {"actions": pending})
            elif row["state"]["usage"]["uncertain"]:
                row["state"].update(status="blocked", outcome="reconciliation_required")
            elif row["state"]["status"] != "finished":
                row["state"].update(status="running", outcome=None, reason="Runner acquired execution lease")
                self._save(conn, row, "resumed", {"worker": owner, "lease_token": token})
            return token

    def release(self, sid: str, token: int):
        with self.connection() as conn:
            conn.execute(
                "UPDATE research_session SET lease_owner=NULL,lease_until=NULL "
                "WHERE id=%s AND lease_token=%s",
                (sid, token),
            )

    def reserve(
        self, sid: str, token: int, request_id: str, kind: str, revision: int, request: dict
    ) -> dict | None:
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            self._fence(row, token)
            digest = fingerprint(request)
            previous = conn.execute(
                "SELECT * FROM research_action WHERE session_id=%s AND request_id=%s AND kind=%s",
                (sid, request_id, kind),
            ).fetchone()
            if previous:
                if previous["input_digest"] != digest:
                    raise Conflict("duplicate decision has different inputs")
                return previous
            if row["revision"] != revision:
                raise StaleRevision("stale decision revision")
            state = row["state"]
            if state["status"] != "running":
                raise Conflict("session is no longer running")
            limits = state["mission"]["limits"]
            bucket = "manager_calls" if kind == "manager" else "tool_calls"
            if (
                row["clock"] >= row["deadline"]
                or state["usage"][bucket] >= limits[bucket]
                or state["usage"]["failures"] >= limits["failures"]
            ):
                state.update(
                    status="paused", outcome="resource_limit", reason="Session resource envelope exhausted"
                )
                self._save(conn, row, "resource_stop", {"bucket": bucket})
                return None
            state["usage"][bucket] += 1
            conn.execute(
                "UPDATE research_session SET state=%s,lease_until=%s WHERE id=%s",
                (Jsonb(state), row["clock"] + timedelta(seconds=limits["call_seconds"] + 30), sid),
            )
            aid = str(uuid4())
            conn.execute(
                "INSERT INTO research_action(id,session_id,request_id,kind,input_digest,"
                "observed_revision,lease_token,status,request) VALUES (%s,%s,%s,%s,%s,%s,%s,'reserved',%s)",
                (aid, sid, request_id, kind, digest, revision, token, Jsonb(request)),
            )
            self._event(
                conn,
                sid,
                revision,
                "reserved",
                {"execution_id": aid, "kind": kind, "input_digest": digest, "reserved": {bucket: 1}},
            )
            return conn.execute("SELECT * FROM research_action WHERE id=%s", (aid,)).fetchone()

    def complete(
        self,
        sid: str,
        token: int,
        aid: str,
        result: dict,
        usage: dict,
        apply=None,
        failure=False,
        uncertain=False,
    ) -> bool:
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            self._fence(row, token)
            action = conn.execute(
                "SELECT * FROM research_action WHERE id=%s AND session_id=%s FOR UPDATE", (aid, sid)
            ).fetchone()
            if not action or action["lease_token"] != token:
                raise Conflict("action belongs to another execution")
            if action["status"] != "reserved":
                return False
            stale = row["revision"] != action["observed_revision"]
            status = "uncertain" if uncertain else "failed" if failure else "stale" if stale else "done"
            conn.execute(
                "UPDATE research_action SET status=%s,result=%s,usage=%s WHERE id=%s",
                (status, Jsonb(result), Jsonb(usage), aid),
            )
            state = row["state"]
            if failure:
                state["usage"]["failures"] += 1
            if uncertain:
                state["usage"]["uncertain"] += 1
                state.update(
                    status="blocked",
                    outcome="reconciliation_required",
                    reason="Provider/tool outcome uncertain; reconcile before further execution",
                )
            if action["kind"] != "manager":
                state["observations"].append(
                    {
                        "execution_id": aid,
                        "kind": action["kind"],
                        "status": status,
                        "input_revision": action["observed_revision"],
                    }
                )
            if apply and not stale and not failure and not uncertain:
                apply(state, result)
            # Manager completion itself leaves the observed revision valid for its proposal.
            if action["kind"] == "manager":
                conn.execute("UPDATE research_session SET state=%s WHERE id=%s", (Jsonb(state), sid))
                self._event(
                    conn, sid, row["revision"], "manager_result", {"execution_id": aid, "status": status}
                )
            else:
                self._save(conn, row, "observation", {"execution_id": aid, "status": status})
            return not stale

    def feedback(self, sid: str, text: str, discussion: str | None = None):
        if not text.strip() or len(text) > 8000:
            raise ValueError("feedback needs 1–8000 characters")
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            state = row["state"]
            observed = row["revision"]
            packet = state["discussions"].get(discussion) if discussion else None
            if discussion and packet is None:
                raise ValueError("unknown discussion")
            stale = bool(
                packet
                and (packet["goal_revision"] != state["goal_revision"] or packet["status"] != "pending")
            )
            response = {
                "text": text,
                "discussion": discussion,
                "observed_revision": observed,
                "goal_revision": state["goal_revision"],
                "applied": not stale,
            }
            state["feedback"].append(response)
            if not stale:
                state["goal_revision"] += 1
                for task in state["tasks"].values():
                    task["status"] = "superseded"
                for hypothesis in state["hypotheses"].values():
                    hypothesis["needs_reconsideration"] = True
                for old in state["discussions"].values():
                    if old["status"] == "pending":
                        old["status"] = "superseded"
                if packet:
                    packet.update(status="answered", response=response)
                state.update(
                    status="ready", outcome=None, reason="Participant input requires reconsideration"
                )
                if row["lease_until"] and row["lease_until"] > row["clock"]:
                    state["status"] = "running"
            self._save(conn, row, "feedback", response)

    def pause(self, sid: str, reason="Participant paused execution"):
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            row["state"].update(status="paused", outcome="participant_input_required", reason=reason)
            self._save(conn, row, "paused", {"reason": reason})

    def reconcile(self, sid: str, aid: str, note: str):
        """Operator records externally checked disposition; no automatic external retry."""
        if not note.strip():
            raise ValueError("reconciliation requires a disposition")
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            if row["lease_until"] and row["lease_until"] > row["clock"]:
                raise Conflict("cannot reconcile during active execution")
            action = conn.execute(
                "UPDATE research_action SET status='failed' WHERE id=%s AND session_id=%s "
                "AND status='uncertain' RETURNING id",
                (aid, sid),
            ).fetchone()
            if not action:
                raise ValueError("no uncertain action to reconcile")
            row["state"]["usage"]["uncertain"] -= 1
            row["state"].update(status="ready", reason="Reconciled; reservations remain consumed")
            self._save(conn, row, "reconciled", {"execution_id": aid, "disposition": note})

    def remaining_seconds(self, sid: str) -> float:
        row = self.get(sid)
        return max(0, (row["deadline"] - datetime.now(UTC)).total_seconds()) if row["deadline"] else 0

    def bind_source(self, sid: str, token: int, identity: dict):
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            self._fence(row, token)
            previous = row["state"].get("source")
            if previous and previous != identity:
                raise Conflict("evidence source changed; preserve the original session source")
            if not previous:
                row["state"]["source"] = identity
                self._save(conn, row, "source_selected", identity)

    def stop(self, sid: str, token: int, outcome: str, reason: str):
        with self.connection() as conn:
            row = self._row(conn, sid, True)
            self._fence(row, token)
            row["state"].update(status="paused", outcome=outcome, reason=reason)
            self._save(conn, row, "stopped", {"outcome": outcome, "reason": reason})

    def rebuild_summary(self, sid: str) -> dict:
        """The working summary can be reconstructed without trusting its cached state field."""
        accepted = [
            a for a in self.actions(sid) if a["status"] == "done" and a["kind"] in {"assess", "finish"}
        ]
        if not accepted:
            return {"summary": "No findings yet", "goal_revision": None}
        action = accepted[-1]
        return {
            "summary": action["result"]["arguments"]["summary"],
            "goal_revision": action["request"]["goal_revision"],
            "execution_id": action["id"],
        }
