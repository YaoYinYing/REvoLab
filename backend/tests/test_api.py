from uuid import uuid4


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_project_graph_vertical_slice(client):
    project = client.post("/api/projects", json={"name": "T5alphaH Engineering"}).json()
    project_id = project["id"]
    protein = client.post(
        f"/api/projects/{project_id}/objects",
        json={"name": "T5alphaH", "object_type": "protein"},
    ).json()
    variant = client.post(
        f"/api/projects/{project_id}/objects",
        json={"name": "L72M/Q122A", "object_type": "variant"},
    ).json()
    relation = client.post(
        f"/api/projects/{project_id}/relations",
        json={
            "source_id": variant["id"],
            "target_id": protein["id"],
            "relation_type": "variant_of",
        },
    )
    assert relation.status_code == 201
    evidence = client.post(
        f"/api/projects/{project_id}/evidence",
        json={
            "evidence_type": "run",
            "label": "External evaluation",
            "provider": "revocompute",
            "external_id": "run-123",
        },
    ).json()
    decision = client.post(
        f"/api/projects/{project_id}/decisions",
        json={
            "title": "Select variant",
            "statement": "Select L72M/Q122A for experimental validation",
            "evidence_ids": [evidence["id"]],
        },
    )
    assert decision.status_code == 201
    graph = client.get(f"/api/projects/{project_id}")
    assert graph.status_code == 200
    assert len(graph.json()["objects"]) == 2
    assert graph.json()["relations"][0]["relation_type"] == "variant_of"
    assert graph.json()["decisions"][0]["title"] == "Select variant"


def test_rejects_cross_project_relation(client):
    first = client.post("/api/projects", json={"name": "First"}).json()
    second = client.post("/api/projects", json={"name": "Second"}).json()
    first_object = client.post(
        f"/api/projects/{first['id']}/objects", json={"name": "A", "object_type": "protein"}
    ).json()
    second_object = client.post(
        f"/api/projects/{second['id']}/objects", json={"name": "B", "object_type": "variant"}
    ).json()
    response = client.post(
        f"/api/projects/{first['id']}/relations",
        json={
            "source_id": first_object["id"],
            "target_id": second_object["id"],
            "relation_type": "related_to",
        },
    )
    assert response.status_code == 422


def test_rejects_unpaired_external_reference(client):
    project = client.post("/api/projects", json={"name": "Evidence"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/evidence",
        json={"evidence_type": "artifact", "label": "Missing provider"},
    )
    assert response.status_code == 422


def test_rejects_self_relation(client):
    project = client.post("/api/projects", json={"name": "Self"}).json()
    item = client.post(
        f"/api/projects/{project['id']}/objects", json={"name": "A", "object_type": "protein"}
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/relations",
        json={"source_id": item["id"], "target_id": item["id"], "relation_type": "related_to"},
    )
    assert response.status_code == 422


def test_unknown_relation_object_is_rejected(client):
    project = client.post("/api/projects", json={"name": "Unknown"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/relations",
        json={"source_id": str(uuid4()), "target_id": str(uuid4()), "relation_type": "related_to"},
    )
    assert response.status_code == 422
