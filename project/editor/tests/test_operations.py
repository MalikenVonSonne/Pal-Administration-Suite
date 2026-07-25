from pal_editor.domain import PalTemplate
from pal_editor.operations import apply_template


def _document():
    return {
        "properties": {
            "worldSaveData": {
                "value": {
                    "CharacterSaveParameterMap": {
                        "value": [
                            {
                                "key": {"InstanceId": {"value": "pal-1"}},
                                "value": {
                                    "RawData": {
                                        "value": {
                                            "object": {
                                                "SaveParameter": {
                                                    "value": {
                                                        "CharacterID": {"value": "PinkCat"},
                                                        "Level": {"value": {"type": "None", "value": 2}},
                                                        "Talent_HP": {"value": {"type": "None", "value": 65}},
                                                    }
                                                }
                                            }
                                        }
                                    }
                                },
                            }
                        ]
                    }
                }
            }
        }
    }


def test_apply_template_preserves_unknown_fields_and_reports_actual_changes():
    document = _document()
    template = PalTemplate(species="PinkCat", level=2, iv_hp=99)

    result = apply_template(document, "pal-1", template)

    assert result.changed_fields == ("Talent_HP",)
    props = document["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"][0]["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    assert props["CharacterID"]["value"] == "PinkCat"
    assert props["Talent_HP"]["value"]["value"] == 99


def test_apply_template_can_clear_existing_nickname_and_skill_arrays():
    document = _document()
    properties = (
        document["properties"]["worldSaveData"]["value"]
        ["CharacterSaveParameterMap"]["value"][0]["value"]["RawData"]["value"]
        ["object"]["SaveParameter"]["value"]
    )
    properties.update(
        {
            "NickName": {"value": "Keep me", "type": "StrProperty"},
            "EquipWaza": {"value": {"values": ["Skill_A"]}},
            "PassiveSkillList": {"value": {"values": ["Passive_A"]}},
        }
    )

    result = apply_template(
        document,
        "pal-1",
        PalTemplate(species="PinkCat", nickname="", active_skills=[], passive_skills=[]),
    )

    assert result.changed_fields == ("NickName", "EquipWaza", "PassiveSkillList")
    assert properties["NickName"]["value"] == ""
    assert properties["EquipWaza"]["value"]["values"] == []
    assert properties["PassiveSkillList"]["value"]["values"] == []


def test_apply_template_materializes_omitted_default_level():
    document = _document()
    properties = (
        document["properties"]["worldSaveData"]["value"]
        ["CharacterSaveParameterMap"]["value"][0]["value"]["RawData"]["value"]
        ["object"]["SaveParameter"]["value"]
    )
    properties.pop("Level")

    result = apply_template(document, "pal-1", PalTemplate(species="PinkCat", level=7))

    assert result.changed_fields == ("Level",)
    assert properties["Level"] == {
        "id": None,
        "value": {"type": "None", "value": 7},
        "type": "ByteProperty",
    }
