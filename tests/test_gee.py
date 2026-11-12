from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from coastwatch import gee


def test_annual_median_uses_only_clear_observations_from_requested_year(monkeypatch):
    surface_collection = MagicMock()
    probability_collection = MagicMock()
    joined_collection = MagicMock()
    annual_collection = MagicMock()

    for collection in (surface_collection, probability_collection):
        collection.filterBounds.return_value = collection
        collection.filterDate.return_value = collection
    joined_collection.filter.return_value = joined_collection
    joined_collection.map.return_value = joined_collection
    joined_collection.filterDate.return_value = annual_collection
    annual_collection.size.return_value.getInfo.return_value = 10
    annual_composite = MagicMock()
    annual_collection.median.return_value = annual_composite

    fake_ee = SimpleNamespace(
        Geometry=MagicMock(return_value="region"),
        ImageCollection=MagicMock(
            side_effect=[
                surface_collection,
                probability_collection,
                joined_collection,
            ]
        ),
        Join=SimpleNamespace(
            saveFirst=MagicMock(return_value=MagicMock(apply=MagicMock(return_value="joined")))
        ),
        Filter=SimpleNamespace(
            equals=MagicMock(return_value="same-index"),
            notNull=MagicMock(return_value="has-cloud-probability"),
        ),
        Image=MagicMock(side_effect=lambda image: image),
    )
    monkeypatch.setattr(gee, "ee", fake_ee)

    region, composite, mndwi, annual_count = gee.make_composite(
        {"type": "Polygon"},
        date(2016, 1, 1),
        date(2017, 1, 1),
    )

    assert region == "region"
    assert composite is annual_composite.clip.return_value
    assert mndwi is composite.normalizedDifference.return_value.rename.return_value
    assert annual_count == 10
    surface_collection.filterDate.assert_called_once_with("2016-01-01", "2017-01-01")
    probability_collection.filterDate.assert_called_once_with(
        "2016-01-01", "2017-01-01"
    )
    joined_collection.filterDate.assert_called_once_with("2016-01-01", "2017-01-01")
    annual_collection.median.assert_called_once_with()
    annual_composite.unmask.assert_not_called()

    mask_clouds = joined_collection.map.call_args.args[0]
    source_image = MagicMock()
    scl = MagicMock()
    cloud_probability_image = MagicMock()
    probability = MagicMock()
    source_image.select.return_value = scl
    source_image.get.return_value = cloud_probability_image
    cloud_probability_image.select.return_value = probability

    masked_image = mask_clouds(source_image)

    assert masked_image is source_image.updateMask.return_value.select.return_value
    assert [call.args[0] for call in scl.neq.call_args_list] == [
        0,
        1,
        3,
        7,
        8,
        9,
        10,
        11,
    ]
    probability.lt.assert_called_once_with(40)
    source_image.updateMask.assert_called_once()
