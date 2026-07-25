from pal_editor.domain import PalTemplate
from pal_editor.game_data import GameDataCatalog
from pal_editor.presets import PRESETS, PresetScope, apply_preset, preview_preset


def test_blueprint_scope_can_apply_attributes_without_level() -> None:
    template = PalTemplate(species="PinkCat", level=3, iv_hp=10, iv_attack=20, iv_defense=30)

    result = apply_preset(
        template,
        "combat_max",
        PresetScope(level=False, rank=False, attributes=True),
    )

    assert result.level == 3
    assert (result.iv_hp, result.iv_attack, result.iv_defense) == (100, 100, 100)


def test_blueprint_scope_can_apply_level_without_attributes() -> None:
    template = PalTemplate(species="PinkCat", level=3, iv_hp=10, iv_attack=20, iv_defense=30)

    result = apply_preset(
        template,
        "combat_max",
        PresetScope(level=True, rank=False, attributes=False),
    )

    assert result.level == 80
    assert (result.iv_hp, result.iv_attack, result.iv_defense) == (10, 20, 30)


def test_rank_blueprint_uses_rank_scope() -> None:
    template = PalTemplate(species="PinkCat", level=3, rank=1)

    result = apply_preset(
        template,
        "rank_max",
        PresetScope(level=False, rank=True, attributes=False),
    )

    assert result.level == 3
    assert result.rank == 5


def test_blueprint_preview_reflects_scope_and_calls_out_skills() -> None:
    lines = preview_preset(
        "combat_max",
        PresetScope(level=True, rank=False, attributes=False),
    )

    assert lines == (
        "Combat Max will affect: Level -> 80",
        "Skills/traits: unchanged by this blueprint.",
    )


def test_role_blueprint_replaces_passives_when_skill_scope_is_enabled() -> None:
    template = PalTemplate(species="PinkCat", passive_skills=["Coward"])

    result = apply_preset(
        template,
        "worker_max",
        PresetScope(level=False, rank=False, attributes=False, skills=True),
    )

    assert result.passive_skills == [
        "WorldTree_CraftSpeed",
        "CraftSpeed_up3",
        "CraftSpeed_up2",
        "PAL_CorporateSlave",
    ]


def test_role_blueprint_respects_disabled_skill_scope() -> None:
    template = PalTemplate(species="PinkCat", passive_skills=["Coward"])

    result = apply_preset(
        template,
        "party_combat",
        PresetScope(level=False, rank=False, attributes=False, skills=False),
    )

    assert result.passive_skills == ["Coward"]


def test_combat_blueprints_cover_the_curated_families() -> None:
    keys = {preset.key for preset in PRESETS}

    assert {
        "combat_skill_dps",
        "combat_balanced",
        "combat_raid_dps",
        "combat_raid_survival",
        "combat_max_attack",
        "tank_max_defense",
        "tank_regeneration",
        "combat_mount_boss",
        "combat_mount_practical",
        "party_combat",
        "party_survival",
    } <= keys
    assert sum(key.startswith("elemental_") for key in keys) == 18


def test_combat_blueprints_use_catalog_passive_ids() -> None:
    catalog_codes = {entry.code for entry in GameDataCatalog.load().passives}

    invalid = {
        preset.key: sorted(set(preset.passive_skills) - catalog_codes)
        for preset in PRESETS
        if set(preset.passive_skills) - catalog_codes
    }

    assert invalid == {}
