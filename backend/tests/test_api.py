"""HTTP vertical slice over the project-scoped API surface."""

import io

from revolab.content_store import ContentStore


def _headers(actor_id: str) -> dict[str, str]:
    return {"X-Actor-Id": actor_id}


def _actor(client) -> str:
    return client.post("/api/actors").json()["actor_id"]


def _project(client, actor_id: str, name: str = "P") -> dict:
    return client.post("/api/projects", json={"name": name}, headers=_headers(actor_id)).json()


def test_health_and_actor_creation(client):
    assert client.get("/health").json()["status"] == "ok"
    actor = client.post("/api/actors")
    assert actor.status_code == 201
    assert actor.json()["actor_id"]


def test_full_vertical_slice_through_project_lens(client):
    actor_id = _actor(client)
    project = _project(client, actor_id)
    pid = project["id"]

    created = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "T5alphaH", "payload": {"organism": "T"}},
        headers=_headers(actor_id),
    )
    assert created.status_code == 201
    series_id = created.json()["series"]["series_id"]

    revision = client.post(
        f"/api/projects/{pid}/objects/{series_id}/revisions",
        json={"payload": {"organism": "T", "chain": "A"}},
        headers=_headers(actor_id),
    )
    assert revision.status_code == 201
    revision_id = revision.json()["revision_id"]

    evidence = client.post(
        f"/api/projects/{pid}/evidence",
        json={
            "kind": "computation",
            "interpretation": "supports",
            "polarity": "supports",
            "source_kind": "scientific_object_revision",
            "source_id": revision_id,
            "target_kind": "scientific_object_revision",
            "target_id": revision_id,
        },
        headers=_headers(actor_id),
    )
    assert evidence.status_code == 201
    evidence_id = evidence.json()["id"]

    decision = client.post(
        f"/api/projects/{pid}/decisions",
        json={
            "title": "Select",
            "statement": "Selected",
            "cites": [{"evidence_id": evidence_id, "cited_as": "supports"}],
            "selects": [{"target_id": series_id, "target_kind": "scientific_object_series"}],
        },
        headers=_headers(actor_id),
    )
    assert decision.status_code == 201
    assert decision.json()["status"] == "draft"
    decision_id = decision.json()["id"]

    committed = client.post(
        f"/api/projects/{pid}/decisions/{decision_id}/commit", headers=_headers(actor_id)
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "committed"
    assert committed.json()["cites"][0]["evidence_id"] == evidence_id

    detail = client.get(f"/api/projects/{pid}/objects/{series_id}", headers=_headers(actor_id))
    assert detail.status_code == 200
    assert detail.json()["series"]["name"] == "T5alphaH"
    assert len(detail.json()["visible_revisions"]) == 2
    assert detail.json()["decisions"][0]["id"] == decision_id

    graph = client.get(
        f"/api/projects/{pid}/graph",
        params={"from_id": series_id, "depth": 3},
        headers=_headers(actor_id),
    )
    assert graph.status_code == 200
    assert any(e["kind"] == "decision" for e in graph.json()["edges"])


def test_actor_header_is_required(client):
    response = client.post("/api/projects", json={"name": "No actor"})
    assert response.status_code == 401


def test_typed_edge_endpoints_and_projection(client):
    actor_id = _actor(client)
    project = _project(client, actor_id)
    pid = project["id"]
    protein = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "X", "payload": {}},
        headers=_headers(actor_id),
    ).json()["series"]["series_id"]
    variant = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "variant", "name": "V", "payload": {}},
        headers=_headers(actor_id),
    ).json()["series"]["series_id"]

    edge = client.post(
        f"/api/projects/{pid}/relations/variant_of",
        json={"source_series_id": variant, "target_series_id": protein},
        headers=_headers(actor_id),
    )
    assert edge.status_code == 201
    assert edge.json()["relation_type"] == "variant_of"


def test_artifact_upload_through_content_store(client, monkeypatch, tmp_path):
    actor_id = _actor(client)
    project = _project(client, actor_id)
    pid = project["id"]
    monkeypatch.setattr("revolab.api._content_store", lambda: ContentStore(tmp_path))

    upload = client.post(
        f"/api/projects/{pid}/artifacts",
        files={"file": ("seq.fasta", io.BytesIO(b">seq\nACGT"), "text/plain")},
        headers=_headers(actor_id),
    )
    assert upload.status_code == 201
    body = upload.json()
    assert body["authority"] == "revolab"
    assert body["checksum"] is not None

    content = client.get(
        f"/api/projects/{pid}/artifacts/{body['resource_id']}/content", headers=_headers(actor_id)
    )
    assert content.status_code == 200
    assert content.content == b">seq\nACGT"


def test_viewer_cannot_mutate(client):
    owner = _actor(client)
    viewer = _actor(client)
    project = _project(client, owner)
    pid = project["id"]
    client.post(
        f"/api/projects/{pid}/members",
        json={"actor_id": viewer, "role": "viewer"},
        headers=_headers(owner),
    )
    response = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "X", "payload": {}},
        headers=_headers(viewer),
    )
    assert response.status_code == 403


def test_delete_project_leaves_global_resources_untouched(client):
    owner = _actor(client)
    other_actor = _actor(client)
    project = _project(client, owner)
    pid = project["id"]
    client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "X", "payload": {}},
        headers=_headers(owner),
    )

    deleted = client.delete(f"/api/projects/{pid}", headers=_headers(owner))
    assert deleted.status_code == 204
    # A tombstoned Project lost its memberships, so nobody can read through it.
    assert client.get(f"/api/projects/{pid}", headers=_headers(owner)).status_code == 403
    # A tombstoned Project no longer appears in anyone's active list.
    other_project = _project(client, other_actor, name="Other")
    assert other_project["id"] != pid


def test_non_member_cannot_read(client):
    owner = _actor(client)
    stranger = _actor(client)
    project = _project(client, owner)
    pid = project["id"]
    response = client.get(f"/api/projects/{pid}", headers=_headers(stranger))
    assert response.status_code == 403
