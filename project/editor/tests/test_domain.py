from pal_editor.domain import PalInstance, PalTemplate
from pal_editor.validation import validate_template


def test_record_becomes_portable_template_without_live_ids():
    record = {
        "species": "ChickenPal",
        "nickname": "Henrietta",
        "gender": "EPalGenderType::Female",
        "level": 3,
        "xp": 120,
        "iv_hp": 80,
        "iv_attack": 60,
        "iv_defense": 100,
        "active_skills": ["EPalWazaID::AirCanon"],
        "passives": ["PAL_ALLAttack_up1"],
        "instance_id": "instance-uuid",
        "owner_uid": "owner-uuid",
        "player_uid": "player-uuid",
        "slot": {"container_id": "container-uuid", "slot_index": 2},
    }

    instance = PalInstance.from_record(record, source_build="1.0")
    assert instance.template.species == "ChickenPal"
    assert instance.template.iv_defense == 100
    assert instance.template.to_dict()["species"] == "ChickenPal"
    assert instance.to_dict()["instance_id"] == "instance-uuid"


def test_legal_validation_rejects_unrestricted_passives():
    template = PalTemplate(
        species="ChickenPal",
        level=3,
        iv_hp=100,
        iv_attack=100,
        iv_defense=100,
        passive_skills=["a", "b", "c", "d", "e"],
    )
    report = validate_template(template)
    assert not report.valid
    assert report.errors[0].code == "too_many_passives"


def test_advanced_validation_allows_unrestricted_passives_with_warning():
    template = PalTemplate(species="ChickenPal", passive_skills=["a", "b", "c", "d", "e"])
    report = validate_template(template, mode="advanced")
    assert report.valid
    assert report.warnings[0].code == "too_many_passives"
