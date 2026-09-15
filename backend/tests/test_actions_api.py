"""Phase-11 Action Handoff HTTP-surface regressions.

The service-level invariants are covered in `test_actions.py`; these prove the
human API seam: Actor/Project isolation and the no-existence-oracle rule map to the
right statuses, execute/reject are explicitly requested (never a side effect of a
read), and neither operation is advertised as an Agent Tool.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from revolab import actions, services
from revolab.enums import (
    AgentToolAutonomy,
    DecisionStatus,
    Role,
    ToolExecutionClass,
    ToolSideEffectClass,
)
from revolab.models import Decision, ScientificObjectRevision
from revolab.schemas import ComputeSubmissionCreate, DecisionCommitCreate


def _project_with_object(session: Session) -> dict[str, Any]:
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "API P")
    series_id = services.create_object(session, actor, project.id, "protein", "Target")
    from sqlalchemy import select

    revision_id = session.scalar(
        select(ScientificObjectRevision.revision_id)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq.asc())
    )
    return {"actor": actor, "project": project, "series_id": series_id, "revision_id": revision_id}


def _propose_compute(session: Session, setup: dict[str, Any]) -> str:
    model = ComputeSubmissionCreate
    arguments = model(
        provider_key="testcompute",
        task_kind="echo",
        inputs=[{"kind": "scientific_object_revision", "resource_id": setup["revision_id"]}],
        params={},
    ).model_dump(mode="json")
    row = actions.propose_action_request(
        session,
        actor_id=setup["actor"],
        project_id=setup["project"].id,
        conversation_id=None,
        tool_id="testcompute.compute.submit",
        autonomy=AgentToolAutonomy.EXPLICIT_ACTION,
        execution_class=ToolExecutionClass.REMOTE,
        side_effect_class=ToolSideEffectClass.EXTERNAL_ACTION,
        arguments=arguments,
    )
    session.commit()
    return str(row.id)


def _propose_local_commit(session: Session, setup: dict[str, Any], decision_id: str) -> str:
    arguments = DecisionCommitCreate(decision_id=decision_id).model_dump(mode="json")
    row = actions.propose_action_request(
        session,
        actor_id=setup["actor"],
        project_id=setup["project"].id,
        conversation_id=None,
        tool_id="decision.commit",
        autonomy=AgentToolAutonomy.EXPLICIT_ACTION,
        execution_class=ToolExecutionClass.LOCAL,
        side_effect_class=ToolSideEffectClass.DOMAIN_MUTATION,
        arguments=arguments,
    )
    session.commit()
    return str(row.id)


def test_action_get_and_list_are_actor_scoped(session: Session, client: TestClient) -> None:
    setup = _project_with_object(session)
    action_id = _propose_compute(session, setup)
    headers = {"X-Actor-Id": str(setup["actor"])}
    base = f"/api/projects/{setup['project'].id}"

    detail = client.get(f"{base}/action-requests/{action_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == action_id
    assert body["status"] == "pending"
    assert body["execution_class"] == "remote"
    assert body["arguments"]["task_kind"] == "echo"

    # A guessed UUID is not an existence oracle for a non-member.
    outsider = services.create_actor(session)
    outsider_headers = {"X-Actor-Id": str(outsider)}
    assert client.get(f"{base}/action-requests/{uuid4()}", headers=outsider_headers).status_code == 403
    assert client.get(f"{base}/action-requests/{action_id}", headers=outsider_headers).status_code == 403
    assert (
        client.post(f"{base}/action-requests/{action_id}/execute", headers=outsider_headers).status_code
        == 403
    )
    assert (
        client.post(f"{base}/action-requests/{action_id}/reject", headers=outsider_headers).status_code
        == 403
    )

    # A member of the same Project who is not the owner still cannot see it.
    member = services.create_actor(session)
    services.add_membership(session, setup["actor"], setup["project"].id, member, Role.MEMBER.value)
    member_headers = {"X-Actor-Id": str(member)}
    assert client.get(f"{base}/action-requests/{action_id}", headers=member_headers).status_code == 404
    assert (
        client.post(f"{base}/action-requests/{action_id}/execute", headers=member_headers).status_code
        == 404
    )

    # Missing actor header is a 401, never an anonymous execution.
    assert client.get(f"{base}/action-requests/{action_id}").status_code == 401


def test_reject_via_api_is_terminal_and_execute_is_never_implicit(
    session: Session, client: TestClient
) -> None:
    setup = _project_with_object(session)
    action_id = _propose_compute(session, setup)
    headers = {"X-Actor-Id": str(setup["actor"])}
    base = f"/api/projects/{setup['project'].id}"

    # Reading and listing never execute: the action is still pending.
    client.get(f"{base}/action-requests/{action_id}", headers=headers)
    rejected = client.post(f"{base}/action-requests/{action_id}/reject", headers=headers)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    # Rejection is terminal: execute fails closed.
    assert client.post(f"{base}/action-requests/{action_id}/execute", headers=headers).status_code == 409


def test_execute_local_action_via_api_commits_the_decision(
    session: Session, client: TestClient
) -> None:
    setup = _project_with_object(session)
    decision = services.create_decision(
        session,
        setup["actor"],
        setup["project"].id,
        title="Draft",
        statement="statement",
        next_actions=[],
        cites=[],
        selects=[],
    )
    action_id = _propose_local_commit(session, setup, str(decision.id))
    headers = {"X-Actor-Id": str(setup["actor"])}
    base = f"/api/projects/{setup['project'].id}"

    executed = client.post(f"{base}/action-requests/{action_id}/execute", headers=headers)
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "succeeded"
    assert body["result_decision_id"] == str(decision.id)

    session.expire_all()
    stored = session.get(Decision, decision.id)
    assert stored is not None and stored.status == DecisionStatus.COMMITTED.value


def test_execute_reject_are_not_agent_tools(session: Session, client: TestClient) -> None:
    setup = _project_with_object(session)
    headers = {"X-Actor-Id": str(setup["actor"])}
    base = f"/api/projects/{setup['project'].id}/tools"
    for path in (base, f"/api/projects/{setup['project'].id}/agent/tools"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        ids = {tool["id"] for tool in response.json()["tools"]}
        assert "decision.commit" in ids
        assert not any("execute" in tool_id or "reject" in tool_id for tool_id in ids)
