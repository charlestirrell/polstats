"""The published API documentation (/openapi.json) must describe every resource and stay in sync with the code.
No database needed: the schema is built from the code alone."""
import pytest
from fastapi.testclient import TestClient

from polstats.api import docs
from polstats.api.app import create_app
from polstats.api.search import INCLUDES, RESERVED, params_for
from polstats.resources import REGISTRY


@pytest.fixture(scope="module")
def spec():
    return TestClient(create_app()).get("/openapi.json").json()


def test_every_resource_has_a_tag_and_five_operations(spec):
    tags = [t["name"] for t in spec["tags"]]
    for rtype in REGISTRY:
        assert rtype in tags
        assert spec["paths"][f"/{rtype}"].keys() == {"get", "post"}
        assert spec["paths"][f"/{rtype}/{{rid}}"].keys() == {"get", "put"}
        assert f"get" in spec["paths"][f"/{rtype}/{{rid}}/_history"]
        for p in (f"/{rtype}", f"/{rtype}/{{rid}}", f"/{rtype}/{{rid}}/_history"):
            for op in spec["paths"][p].values():
                assert op["tags"] == [rtype]
                assert op["description"] and op["summary"]


def test_generic_fallback_routes_are_not_documented(spec):
    assert not any(p.startswith("/{rtype}") for p in spec["paths"])
    assert set(spec["paths"]) == {"/metadata"} | {f"{p}" for t in REGISTRY
                                                  for p in (f"/{t}", f"/{t}/{{rid}}", f"/{t}/{{rid}}/_history")}


def test_operation_ids_are_unique(spec):
    ids = [op["operationId"] for path in spec["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids))


def test_search_parameters_are_documented_exactly(spec):
    for rtype in REGISTRY:
        documented = {p["name"] for p in spec["paths"][f"/{rtype}"]["get"]["parameters"]}
        expected = set(params_for(rtype)) | RESERVED
        if not INCLUDES.get(rtype):
            expected -= {"_include"}
        assert documented == expected, rtype
        assert docs.documented_param_names(rtype) == set(params_for(rtype)), rtype


def test_include_values_match_the_code(spec):
    for rtype, fields in INCLUDES.items():
        inc = next(p for p in spec["paths"][f"/{rtype}"]["get"]["parameters"] if p["name"] == "_include")
        assert inc["schema"]["items"]["enum"] == [f"{rtype}:{f}" for f in fields]


def test_every_param_has_a_description(spec):
    for rtype in REGISTRY:
        for p in spec["paths"][f"/{rtype}"]["get"]["parameters"]:
            assert p["description"], (rtype, p["name"])


def test_schemas_are_published_with_descriptions(spec):
    schemas = spec["components"]["schemas"]
    for rtype, cls in REGISTRY.items():
        s = schemas[cls.__name__]
        assert s["description"], rtype
        props = s["properties"]
        for name, prop in props.items():
            if name not in ("resourceType",):
                assert "description" in prop or "$ref" in prop or "anyOf" in prop, (rtype, name)
        assert "OperationOutcome" in schemas


def test_search_response_is_a_typed_bundle(spec):
    ok = spec["paths"]["/Election"]["get"]["responses"]["200"]["content"]["application/json"]
    assert ok["schema"]["$ref"].endswith("ElectionSearchBundle")
    assert "400" in spec["paths"]["/Election"]["get"]["responses"]


def test_write_operations_document_body_and_errors(spec):
    post = spec["paths"]["/Contest"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/Contest")
    assert {"201", "400", "403"} <= set(post["responses"])
    put = spec["paths"]["/Contest/{rid}"]["put"]
    assert {"200", "400", "403", "404", "412"} <= set(put["responses"])
    assert any(p["name"] == "If-Match" and p["in"] == "header" for p in put["parameters"])
    assert any(p["name"] == "rid" and p["in"] == "path" for p in put["parameters"])


@pytest.mark.parametrize("rtype", sorted(REGISTRY))
def test_examples_validate_against_their_models(rtype):
    cls = REGISTRY[rtype]
    cls.model_validate(docs.EXAMPLES[rtype])
    cls.model_validate(docs.example_body(rtype))
    docs.search_bundle_model(rtype).model_validate(
        {"resourceType": "Bundle", "type": "searchset", "total": 1,
         "entry": [{"fullUrl": f"{rtype}/x", "resource": docs.EXAMPLES[rtype], "search": {"mode": "match"}}]})


def test_every_resource_type_is_documented():
    assert set(docs.TAG_ORDER) - {docs.SERVICE_TAG} == set(REGISTRY)
    assert set(docs.RESOURCE_DOCS) == set(docs.PARAM_DOCS) == set(docs.EXAMPLES) == set(REGISTRY)


def test_docs_pages_render():
    c = TestClient(create_app())
    assert c.get("/docs").status_code == 200
    assert c.get("/redoc").status_code == 200
