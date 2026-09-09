"""Phase-5 collaboration HTTP surface: members, visibility, sharing, and the
authorization-aware read projections returned through the generated contract."""


def _headers(actor_id: str) -> dict[str, str]:
    return {"X-Actor-Id": actor_id}


def _actor(client) -> str:
    return client.post("/api/actors").json()["actor_id"]


def _project(client, actor_id: str, name: str, visibility: str = "private") -> dict:
    return client.post(
        "/api/projects",
        json={"name": name, "visibility": visibility},
        headers=_headers(actor_id),
    ).json()


def test_membership_endpoints_and_last_owner_protection(client):
    a = _actor(client)
    b = _actor(client)
    project = _project(client, a, "P")
    pid = project["id"]

    added = client.post(
        f"/api/projects/{pid}/members",
        json={"actor_id": b, "role": "member"},
        headers=_headers(a),
    )
    assert added.status_code == 201
    assert added.json()["role"] == "member"

    roster = client.get(f"/api/projects/{pid}/members", headers=_headers(b))
    assert roster.status_code == 200
    assert {m["actor_id"] for m in roster.json()} == {a, b}

    # Member cannot change roles.
    viewer_actor = _actor(client)
    client.post(
        f"/api/projects/{pid}/members",
        json={"actor_id": viewer_actor, "role": "viewer"},
        headers=_headers(a),
    )
    changed = client.patch(
        f"/api/projects/{pid}/members/{viewer_actor}",
        json={"role": "member"},
        headers=_headers(b),
    )
    assert changed.status_code == 403
    # Owner can.
    changed = client.patch(
        f"/api/projects/{pid}/members/{viewer_actor}",
        json={"role": "member"},
        headers=_headers(a),
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "member"

    # Last-owner invariant: owner self-demotion → 409.
    demote = client.patch(
        f"/api/projects/{pid}/members/{a}", json={"role": "viewer"}, headers=_headers(a)
    )
    assert demote.status_code == 409
    # Owner self-removal → 409.
    remove = client.delete(f"/api/projects/{pid}/members/{a}", headers=_headers(a))
    assert remove.status_code == 409
    # Removing a non-owner works.
    assert client.delete(f"/api/projects/{pid}/members/{b}", headers=_headers(a)).status_code == 204


def test_project_visibility_endpoints(client):
    a = _actor(client)
    b = _actor(client)
    project = _project(client, a, "P", visibility="shared_with_members")
    pid = project["id"]
    assert project["visibility"] == "shared_with_members"
    # Non-owner cannot change visibility.
    assert (
        client.patch(
            f"/api/projects/{pid}", json={"visibility": "private"}, headers=_headers(b)
        ).status_code
        == 403
    )
    # Owner can.
    patched = client.patch(f"/api/projects/{pid}", json={"visibility": "private"}, headers=_headers(a))
    assert patched.status_code == 200
    assert patched.json()["visibility"] == "private"


def test_cross_project_share_http_surface(client):
    a = _actor(client)
    b = _actor(client)
    c = _actor(client)
    pa = _project(client, a, "A")
    pb = _project(client, b, "B")
    pc = _project(client, c, "C")

    created = client.post(
        f"/api/projects/{pa['id']}/objects",
        json={"object_type": "protein", "name": "X", "payload": {"organism": "T"}},
        headers=_headers(a),
    )
    series_id = created.json()["series"]["series_id"]
    rev1 = created.json()["visible_revisions"][0]
    # Private rev2 stays in A.
    client.post(
        f"/api/projects/{pa['id']}/objects/{series_id}/revisions",
        json={"payload": {"organism": "T", "chain": "A"}},
        headers=_headers(a),
    )

    # c (no read path to the resource) cannot share it into C → 403.
    assert (
        client.post(
            f"/api/projects/{pc['id']}/shares",
            json={"resource_id": series_id},
            headers=_headers(c),
        ).status_code
        == 403
    )

    # a joins B as a member, then shares rev1 into B.
    client.post(
        f"/api/projects/{pb['id']}/members",
        json={"actor_id": a, "role": "member"},
        headers=_headers(b),
    )
    shared = client.post(
        f"/api/projects/{pb['id']}/shares",
        json={"resource_id": rev1["revision_id"]},
        headers=_headers(a),
    )
    assert shared.status_code == 201
    assert shared.json()["resource_id"] == rev1["revision_id"]

    detail = client.get(
        f"/api/projects/{pb['id']}/objects/{series_id}", headers=_headers(b)
    )
    assert detail.status_code == 200
    assert [r["revision_seq"] for r in detail.json()["visible_revisions"]] == [1]
    assert detail.json()["read_only"] is True

    # B holds only the read lens — mutation is denied.
    assert (
        client.patch(
            f"/api/projects/{pb['id']}/objects/{series_id}",
            json={"name": "Renamed by B"},
            headers=_headers(b),
        ).status_code
        == 403
    )

    # B forms its own interpretation; A must not see it.
    evidence = client.post(
        f"/api/projects/{pb['id']}/evidence",
        json={
            "kind": "computation",
            "polarity": "contradicts",
            "source_kind": "scientific_object_revision",
            "source_id": rev1["revision_id"],
            "target_kind": "scientific_object_revision",
            "target_id": rev1["revision_id"],
        },
        headers=_headers(b),
    )
    assert evidence.status_code == 201
    decision = client.post(
        f"/api/projects/{pb['id']}/decisions",
        json={
            "title": "B decision",
            "statement": "B truth",
            "cites": [{"evidence_id": evidence.json()["id"], "cited_as": "supports"}],
            "selects": [{"target_id": series_id, "target_kind": "scientific_object_series"}],
        },
        headers=_headers(b),
    )
    assert client.post(
        f"/api/projects/{pb['id']}/decisions/{decision.json()['id']}/commit",
        headers=_headers(b),
    ).status_code == 200

    detail_a = client.get(f"/api/projects/{pa['id']}/objects/{series_id}", headers=_headers(a))
    assert detail_a.json()["evidence"] == []
    assert detail_a.json()["decisions"] == []

    # Non-member read is denied regardless of sharing.
    assert (
        client.get(f"/api/projects/{pb['id']}/objects/{series_id}", headers=_headers(c)).status_code
        == 403
    )


def test_preferred_revision_endpoint(client):
    a = _actor(client)
    project = _project(client, a, "P")
    pid = project["id"]
    created = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "X", "payload": {"organism": "T"}},
        headers=_headers(a),
    )
    series_id = created.json()["series"]["series_id"]
    rev2 = client.post(
        f"/api/projects/{pid}/objects/{series_id}/revisions",
        json={"payload": {"organism": "T", "chain": "A"}},
        headers=_headers(a),
    ).json()

    set_pin = client.put(
        f"/api/projects/{pid}/objects/{series_id}/preferred-revision",
        json={"revision_id": rev2["revision_id"]},
        headers=_headers(a),
    )
    assert set_pin.status_code == 204
    detail = client.get(f"/api/projects/{pid}/objects/{series_id}", headers=_headers(a)).json()
    assert detail["preferred_revision_id"] == rev2["revision_id"]

    cleared = client.put(
        f"/api/projects/{pid}/objects/{series_id}/preferred-revision",
        json={"revision_id": None},
        headers=_headers(a),
    )
    assert cleared.status_code == 204
    assert (
        client.get(f"/api/projects/{pid}/objects/{series_id}", headers=_headers(a)).json()[
            "preferred_revision_id"
        ]
        is None
    )


def test_role_and_membership_changes_take_effect_immediately(client):
    a = _actor(client)
    b = _actor(client)
    project = _project(client, a, "P")
    pid = project["id"]
    client.post(
        f"/api/projects/{pid}/members",
        json={"actor_id": b, "role": "member"},
        headers=_headers(a),
    )
    # b (member) can read.
    assert client.get(f"/api/projects/{pid}", headers=_headers(b)).status_code == 200

    # Demote b to viewer: the very next write is denied (no cached auth truth).
    assert (
        client.patch(
            f"/api/projects/{pid}/members/{b}",
            json={"role": "viewer"},
            headers=_headers(a),
        ).status_code
        == 200
    )
    created = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "X", "payload": {"organism": "T"}},
        headers=_headers(b),
    )
    assert created.status_code == 403
    # Viewer can still read.
    assert client.get(f"/api/projects/{pid}", headers=_headers(b)).status_code == 200

    # Remove b: the very next read is denied.
    assert client.delete(f"/api/projects/{pid}/members/{b}", headers=_headers(a)).status_code == 204
    assert client.get(f"/api/projects/{pid}", headers=_headers(b)).status_code == 403


def test_project_description_can_be_cleared(client):
    a = _actor(client)
    project = client.post(
        "/api/projects",
        json={"name": "P", "description": "initial"},
        headers=_headers(a),
    ).json()
    pid = project["id"]
    # Omitting the description preserves it.
    renamed = client.patch(f"/api/projects/{pid}", json={"name": "P2"}, headers=_headers(a))
    assert renamed.status_code == 200
    assert renamed.json()["description"] == "initial"
    # An explicit null clears it.
    cleared = client.patch(
        f"/api/projects/{pid}", json={"description": None}, headers=_headers(a)
    )
    assert cleared.status_code == 200
    assert cleared.json()["description"] is None
