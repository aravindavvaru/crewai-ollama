from fastapi.testclient import TestClient

from latest_ai_flow.ui_app import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_flows_endpoint():
    response = client.get("/api/flows")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(item["id"] == "content" for item in data)


def test_create_and_list_run():
    response = client.post("/api/run", json={"topic": "GraphQL UI", "workflow": "content"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["topic"] == "GraphQL UI"
    assert payload["workflow"] == "content"

    response = client.get("/api/runs")
    assert response.status_code == 200
    runs = response.json()
    assert any(item["id"] == payload["id"] for item in runs)

    response = client.get(f"/api/runs/{payload['id']}/logs")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == payload["id"]
    assert isinstance(data["logs"], list)
