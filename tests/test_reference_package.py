import json

import pytest
from fastapi.testclient import TestClient

from workflow.api import create_app
from workflow.collection.reference import build_reference_package, validate_reference_package


def _candidate(cid, *, same=True, material=False, likes=0):
    return {
        "id": cid,
        "source_url": f"https://www.tiktok.com/@creator/video/{1000000000000000000 + len(cid)}",
        "product_match_level": "same_product_family" if same else "different_or_unverified_product",
        "material_eligibility": material,
        "structure_reference_eligibility": True,
        "hook": f"开头动作 {cid}",
        "demo_or_proof": f"演示动作 {cid}",
        "visible_metrics": {"views": None, "likes": likes, "comments": 3, "saves": 2, "shares": 1},
        "popularity_status": "互动可见，不能推出销量",
        "purchase_intent_evidence": None,
        "commercial_intent_evidence": None,
        "reuse_rights": "unverified",
    }


def test_reference_package_prioritizes_same_product_and_declares_evidence_scope(tmp_path):
    report = {
        "schema_version": 1,
        "product": {"brand": "SHEGLAM", "name": "Color Bloom Liquid Blush"},
        "discovery": {"automatic_tiktok_search": "not_implemented"},
        "candidates": [
            _candidate("structure", same=False, likes=999999),
            _candidate("same", same=True, material=True, likes=10),
        ],
    }
    analysis = tmp_path / "same-analysis.json"
    analysis.write_text(json.dumps({
        "schema_version": 1,
        "status": "codex_observed_needs_review",
        "asr_status": "not_run",
        "ocr_status": "not_run",
        "segments": 2,
    }), encoding="utf-8")

    package = build_reference_package(report, market="BR", analysis_paths={"same": analysis})

    assert package["references"][0]["candidate_id"] == "same"
    assert package["references"][0]["relationship"] == "same_product_candidate"
    assert package["references"][0]["evidence_scope"] == "visual_structure_only"
    assert package["references"][0]["analysis"]["asr_status"] == "not_run"
    assert package["references"][1]["relationship"] == "different_product_structure_reference"
    assert package["references"][1]["use_scope"] == "structure_only"
    assert package["discovery"]["automatic_search"] == "not_implemented"
    validate_reference_package(package)


def test_reference_package_accepts_saved_raw_candidate_evidence():
    report = {
        "product": {"brand": "SHEGLAM", "name": "Color Bloom Liquid Blush"},
        "candidates": [{
            "id": "raw-same", "source_url": "https://www.tiktok.com/@a/video/1234567890123456789",
            "product_match": "SHEGLAM Color Bloom 系列", "commercial_status": "品牌合作标记：embajador",
            "hook_and_action_observation": "上脸并晕染", "visible_metrics": {"likes": 7},
        }],
    }
    package = build_reference_package(report, market="BR",
                                      product={"brand": "SHEGLAM", "name": "Color Bloom Liquid Blush"})
    assert package["references"][0]["relationship"] == "same_product_candidate"
    assert package["references"][0]["use_scope"] == "material_candidate_and_structure"


def test_reference_package_rejects_unlabeled_reference_and_missing_beats():
    with pytest.raises(ValueError, match="relationship"):
        validate_reference_package({
            "schema_version": 1,
            "product": {"brand": "S", "name": "P"},
            "market": "BR",
            "references": [{"candidate_id": "x"}],
        })


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.headers['Authorization']='Bearer '+(client.app.state.data_dir/'access.token').read_text(encoding='utf-8').strip()
        client.headers["X-Workflow-Client"] = "studio"
        yield client


def test_api_registers_reference_package_as_pending_research(client):
    job_response = client.post("/api/jobs", json={
        "title": "腮红巴西批次", "product": "SHEGLAM Color Bloom Liquid Blush",
        "market": "BR", "count": 3,
    })
    assert job_response.status_code == 201
    job_id = job_response.json()["id"]
    package = {
        "schema_version": 1,
        "product": {"brand": "SHEGLAM", "name": "Color Bloom Liquid Blush"},
        "market": "BR",
        "discovery": {"automatic_search": "not_implemented"},
        "references": [{
            "candidate_id": "eli",
            "source_url": "https://www.tiktok.com/@creator/video/1234567890123456789",
            "relationship": "same_product_candidate",
            "use_scope": "material_candidate_and_structure",
            "visible_metrics": {"likes": 1},
            "hook": "开头动作",
            "buyer_concern": "未从口播确认",
            "proof": "点涂并晕染",
            "objection_or_turn": "未从口播确认",
            "cta": "未核实",
            "transcription_status": "unavailable",
            "evidence_scope": "visual_structure_only",
        }],
    }
    response = client.post(f"/api/jobs/{job_id}/reference-package", json={"package": package})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["reference_package"]["references"][0]["candidate_id"] == "eli"
    assert saved["batch"]["rendered_count"] == 0
    assert any(event["kind"] == "reference_package_registered" for event in saved["events"])


def test_reference_package_cannot_be_registered_to_another_market(client):
    job = client.post("/api/jobs", json={
        "title": "墨西哥采集", "product": "SHEGLAM Color Bloom Liquid Blush",
        "market": "MX",
    }).json()
    package = build_reference_package({
        "product": {"brand": "SHEGLAM", "name": "Color Bloom Liquid Blush"},
        "candidates": [_candidate("same", material=True)],
    }, market="BR")
    response = client.post(f"/api/jobs/{job['id']}/reference-package", json={"package": package})
    assert response.status_code == 409
    saved = client.get(f"/api/jobs/{job['id']}").json()
    assert "reference_package" not in saved
    assert saved["market"] == "MX"
    assert not any(event["kind"] == "reference_package_registered" for event in saved["events"])
