"""Milestone-2 catalog endpoints (spec §4)."""


def test_datatypes_seeded(client):
    r = client.get("/api/catalog/datatypes")
    assert r.status_code == 200
    names = {d["name"] for d in r.json()["datatypes"]}
    assert names == {"float", "integer", "boolean", "string",
                     "date", "time", "file", "enum"}


def test_object_types_ground_properties(client):
    r = client.get("/api/catalog/object-types")
    assert r.status_code == 200
    by_name = {ot["object"]: ot for ot in r.json()["object_types"]}
    assert "Ground" in by_name and "Crop" in by_name

    ground = by_name["Ground"]
    props = [p["property"] for p in ground["properties"]]
    assert props == ["length", "breadth", "resolution_x", "resolution_y",
                     "position_x", "position_y", "position_z",
                     "rotation_z", "texture_x", "texture_y"]

    by_prop = {p["property"]: p for p in ground["properties"]}
    assert by_prop["resolution_x"]["min"] == 1
    assert by_prop["resolution_x"]["max"] == 25000
    assert by_prop["resolution_x"]["required"] is True
    assert by_prop["rotation_z"]["max"] == 360
    # Every Ground parameter is required (story: clearing any → "Field is required").
    assert by_prop["rotation_z"]["required"] is True
    assert by_prop["position_x"]["required"] is True
    # Position is the inclusive range [-1,000,000, +1,000,000] (migration 020).
    assert by_prop["position_x"]["min"] == -1000000
    assert by_prop["position_x"]["max"] == 1000000

    # Crop is seeded with no property links yet
    assert by_name["Crop"]["properties"] == []


def test_material_types_seven_types_viz_on_visualiser_only(client):
    r = client.get("/api/catalog/material-types")
    assert r.status_code == 200
    by_name = {mt["materialtype"]: mt for mt in r.json()["material_types"]}
    assert set(by_name) == {
        "Radiation", "Energy Balance", "Solar Position", "Photosynthesis",
        "Boundary Layer Conductance", "Stomatal Conductance", "Visualiser",
    }

    # Plan B: the visualisation props (colour + opacity + texture) are owned
    # SOLELY by the new "Visualiser" type; the six model types no longer carry
    # them. Properties are also NO LONGER tagged with a model/visualisation
    # `group` — the response is a flat property list.
    VIZ = {"color_r", "color_g", "color_b", "opacity", "texture_file", "texture_toggle"}
    for name, mt in by_name.items():
        names = {p["property"] for p in mt["properties"]}
        if name == "Visualiser":
            assert names == VIZ, mt["materialtype"]
        else:
            assert not (VIZ & names), mt["materialtype"]
        assert all("group" not in p for p in mt["properties"]), mt["materialtype"]

    rad = {p["property"]: p for p in by_name["Radiation"]["properties"]}
    assert rad["surface_temperature"]["min"] == 223
    assert rad["surface_temperature"]["max"] == 5000
    assert rad["reflectivity"]["max"] == 1
    assert rad["two_sided_heat_transfer"]["datatype"] == "boolean"

    # Shared property narrowed per type via overrides
    photo = {p["property"]: p for p in by_name["Photosynthesis"]["properties"]}
    assert photo["radiation_flux"]["max"] == 1500
    eb = {p["property"]: p for p in by_name["Energy Balance"]["properties"]}
    assert eb["radiation_flux"]["max"] == 10000000

    blc = {p["property"]: p for p in by_name["Boundary Layer Conductance"]["properties"]}
    assert blc["boundary_layer_model"]["datatype"] == "enum"
    assert blc["boundary_layer_model"]["enum_values"] == [
        "Pohlhausen", "InclinedPlate", "Sphere", "Ground"]


def test_model_types_hierarchy(client):
    r = client.get("/api/catalog/model-types")
    assert r.status_code == 200
    by_name = {mt["model"]: mt for mt in r.json()["model_types"]}
    assert set(by_name) == {
        "Radiation", "Energy Balance", "Solar Position",
        "Photosynthesis", "Boundary Layer Conductance", "Stomatal Conductance",
    }
    assert {s["model"] for s in by_name["Stomatal Conductance"]["submodels"]} == {
        "Ball-Woodrow-Berry", "Ball-Berry-Leuning",
        "Medlyn Optimality", "Buckley-Mott-Farquhar"}
    assert {s["model"] for s in by_name["Boundary Layer Conductance"]["submodels"]} == {
        "Pohlhausen", "InclinedPlate", "Sphere", "Ground"}
    assert [s["model"] for s in by_name["Photosynthesis"]["submodels"]] == ["Farquhar"]
    assert by_name["Radiation"]["submodels"] == []
    # Submodels carry their own ids, distinct from parents
    sub_ids = {s["id"] for mt in by_name.values() for s in mt["submodels"]}
    top_ids = {mt["id"] for mt in by_name.values()}
    assert not (sub_ids & top_ids)
