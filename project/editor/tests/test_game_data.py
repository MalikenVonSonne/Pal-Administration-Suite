import os
from pathlib import Path

from pal_editor.game_data import GameDataCatalog


def test_reference_catalog_loads_expected_core_entries():
    reference_dir = os.environ.get("PALADMIN_REFERENCE_CATALOG")
    catalog = GameDataCatalog.load(Path(reference_dir)) if reference_dir else GameDataCatalog.load()
    assert any(entry.code == "ChickenPal" for entry in catalog.pals)
    assert any(entry.code == "EPalWazaID::AirCanon" for entry in catalog.attacks)
    if reference_dir:
        assert any(entry.code == "NONE" for entry in catalog.passives)


def test_palworld_catalog_uses_readable_labels_for_internal_ids():
    catalog = GameDataCatalog.load()

    sheepball = next(entry for entry in catalog.pals if entry.code == "SheepBall")
    punch = next(
        entry
        for entry in catalog.attacks
        if entry.code == "EPalWazaID::Unique_PinkCat_CatPunch"
    )
    morale = next(
        entry for entry in catalog.passives if entry.code == "BaseCampPal_SAN_Down_5"
    )

    assert sheepball.label == "Lamball"
    assert punch.label == "Punch Flurry"
    assert morale.label == "Base Camp Morale Down 5"


def test_palworld_catalog_separates_standard_passives_from_raw_records():
    catalog = GameDataCatalog.load()

    assert len(catalog.passives) == 1905
    assert len(catalog.standard_passives) == 115
    assert any(entry.label == "Whopper" for entry in catalog.standard_passives)
