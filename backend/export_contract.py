import json

from fastapi.testclient import TestClient

from .api import ROOT, create_app

BBOX = "-122.43,37.76,-122.4,37.79"
PARCEL_ID = "sf:fixture-001"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    app = create_app(mode="fixture")
    directory = ROOT / "contracts"
    examples = directory / "examples"
    examples.mkdir(exist_ok=True)
    with TestClient(app) as client:
        request = {"parcel_id": PARCEL_ID, "objective_weights": {"need": 1, "economic": 1, "sustainability": 1}}
        write_json(examples / "compare-request.json", request)
        requests = {
            "health": client.get("/api/v1/health"),
            "metadata": client.get("/api/v1/metadata"),
            "parcels": client.get("/api/v1/parcels", params={"bbox": BBOX}),
            "parcel": client.get(f"/api/v1/parcels/{PARCEL_ID}"),
            "services": client.get("/api/v1/services", params={"bbox": BBOX}),
            "permit-checklist": client.post(
                "/api/v1/permits/check", json={"parcel_id": PARCEL_ID, "category_id": "grocery"}
            ),
            "compare": client.post("/api/v1/scenarios/compare", json=request),
        }
        for name, response in requests.items():
            response.raise_for_status()
            write_json(examples / f"{name}.json", response.json())
        write_json(directory / "openapi.json", app.openapi())


if __name__ == "__main__":
    main()
