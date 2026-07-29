"""Reading the tabular inputs, including the messy cases real exports have."""

from __future__ import annotations

import pytest

from route_coverage.inputs import InputError, read_routes, read_specialists
from route_coverage.inputs.routes import split_areas

HEADER = "route_id,specialist_id,region,state,city,district\n"


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_reads_districts_and_whole_cities(tmp_path):
    path = write(
        tmp_path,
        "routes.csv",
        HEADER
        + "ROUTE-A,TECH-001,SOUTH,SC,Porto Aurora,Centro\n"
        + "ROUTE-A,TECH-001,SOUTH,SC,Nova Bracara,\n",
    )
    areas = read_routes(path)
    cities, districts = split_areas(areas)
    assert [area.district for area in districts] == ["Centro"]
    assert [area.city for area in cities] == ["Nova Bracara"]
    assert districts[0].district_key == "Centro|Porto Aurora|SC"
    assert cities[0].city_key == "Nova Bracara|SC"


def test_totalling_rows_are_dropped():
    """A spreadsheet's "Sum" row is not a route and must not become one."""


@pytest.mark.parametrize("label", ["Sum", "TOTAL", "total geral", "Grand Total"])
def test_totalling_rows_are_not_routes(tmp_path, label):
    path = write(
        tmp_path,
        "routes.csv",
        HEADER + "ROUTE-A,TECH-001,SOUTH,SC,Porto Aurora,Centro\n" + f"{label},,,SC,Porto Aurora,Centro\n",
    )
    assert [area.route_id for area in read_routes(path)] == ["ROUTE-A"]


def test_rows_without_city_or_state_are_skipped(tmp_path):
    path = write(
        tmp_path,
        "routes.csv",
        HEADER + "ROUTE-A,TECH-001,SOUTH,SC,Porto Aurora,Centro\n" + "ROUTE-B,TECH-002,SOUTH,,,\n",
    )
    assert len(read_routes(path)) == 1


def test_values_are_normalized_on_read(tmp_path):
    path = write(
        tmp_path,
        "routes.csv",
        HEADER + "route-a,tech-001,south,Santa Catarina,PORTO AURORA,centro historico\n",
    )
    area = read_routes(path)[0]
    assert (area.route_id, area.specialist_id, area.region) == ("ROUTE-A", "TECH-001", "SOUTH")
    assert (area.state, area.city, area.district) == ("SC", "Porto Aurora", "Centro Historico")


def test_alternative_column_names_are_accepted(tmp_path):
    path = write(
        tmp_path,
        "routes.csv",
        "Rota,Especialista,Regional,UF,Município,Bairro\n" + "ROUTE-A,TECH-001,SOUTH,SC,Porto Aurora,Centro\n",
    )
    assert read_routes(path)[0].city == "Porto Aurora"


def test_missing_required_column_is_a_readable_error(tmp_path):
    path = write(tmp_path, "routes.csv", "route_id,region\nROUTE-A,SOUTH\n")
    with pytest.raises(InputError, match="city"):
        read_routes(path)


def test_unsupported_format_is_rejected(tmp_path):
    path = write(tmp_path, "routes.json", "{}")
    with pytest.raises(InputError, match="unsupported"):
        read_routes(path)


def test_specialists_read_coordinates_and_reject_duplicate_ids(tmp_path):
    path = write(
        tmp_path,
        "specialists.csv",
        "specialist_id,region,street,number,district,city,state,latitude,longitude\n"
        "TECH-001,SOUTH,Rua A,10,Centro,Porto Aurora,SC,-27.95,-46.30\n",
    )
    specialist = read_specialists(path)[0]
    assert specialist.has_fixed_coordinates
    assert specialist.latitude == pytest.approx(-27.95)

    duplicated = write(
        tmp_path,
        "dup.csv",
        "specialist_id,city,state\nTECH-001,Porto Aurora,SC\nTECH-001,Nova Bracara,SC\n",
    )
    with pytest.raises(InputError, match="duplicated"):
        read_specialists(duplicated)


def test_demo_dataset_is_readable(project_root):
    areas = read_routes(project_root / "data" / "demo" / "routes.csv")
    specialists = read_specialists(project_root / "data" / "demo" / "specialists.csv")
    assert len(areas) >= 20
    assert len(specialists) >= 5
    assert all(area.route_id for area in areas)
