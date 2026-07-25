"""Conservative Pal Admin blueprints backed by the Palworld 1.0 catalog."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .domain import PalTemplate


@dataclass(frozen=True)
class PalPreset:
    key: str
    label: str
    description: str
    changes: tuple[tuple[str, str], ...] = ()
    passive_skills: tuple[str, ...] = ()
    active_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class PresetScope:
    """Which parts of a blueprint are allowed to change."""

    level: bool = True
    rank: bool = True
    attributes: bool = True
    skills: bool = True
    active_skills: bool = False
    passives: bool | None = None

    @property
    def passives_enabled(self) -> bool:
        """Return the explicit passive scope, with ``skills`` kept as compatibility."""
        return self.skills if self.passives is None else self.passives


PRESETS = (
    PalPreset(
        "max_iv",
        "Max IVs",
        "Set HP, Attack, and Defense IVs to 100.",
        (("attributes", "HP IV -> 100"), ("attributes", "Attack IV -> 100"), ("attributes", "Defense IV -> 100")),
    ),
    PalPreset("max_level", "Level 80", "Set the Pal level to 80.", (("level", "Level -> 80"),)),
    PalPreset("rank_max", "Full Rank", "Set the Pal rank to 5.", (("rank", "Rank -> 5"),)),
    PalPreset(
        "combat_max",
        "Combat Max",
        "Set level to 80 and all three IVs to 100.",
        (
            ("level", "Level -> 80"),
            ("attributes", "HP IV -> 100"),
            ("attributes", "Attack IV -> 100"),
            ("attributes", "Defense IV -> 100"),
        ),
    ),
    PalPreset(
        "combat_skill_dps",
        "Combat - Maximum Skill DPS",
        "Glass-cannon skill damage. God of Destruction halves maximum health and Twin-Edged Holy Blade reduces Defense.",
        (("skills", "Passives -> Twin-Edged Holy Blade, God of Destruction, Demon God, Serenity"),),
        ("WorldTree_ATK", "WorldTree_ATK_DEF", "PAL_ALLAttack_up3", "CoolTimeReduction_Up_1"),
    ),
    PalPreset(
        "combat_balanced",
        "Combat - Balanced Fighter",
        "General endgame combat with damage, cooldown reduction, Defense, regeneration, and life steal without a major penalty.",
        (("skills", "Passives -> Demon God, Serenity, Legend, Immortality"),),
        ("PAL_ALLAttack_up3", "CoolTimeReduction_Up_1", "Legend", "MutationPal_Immortal"),
    ),
    PalPreset(
        "combat_raid_dps",
        "Raid - General DPS",
        "Sustained raid damage without the maximum-health penalty from God of Destruction. Use an elemental blueprint when the attack loadout is element-focused.",
        (("skills", "Passives -> Demon God, Serenity, Legend, Immortality"),),
        ("PAL_ALLAttack_up3", "CoolTimeReduction_Up_1", "Legend", "MutationPal_Immortal"),
    ),
    PalPreset(
        "combat_raid_survival",
        "Raid - Survivability",
        "Raid-ready damage and sustain with strong regeneration, Defense, and poison/burn immunity.",
        (("skills", "Passives -> Demon God, Serenity, Immortality, Idiosyncratic"),),
        ("PAL_ALLAttack_up3", "CoolTimeReduction_Up_1", "MutationPal_Immortal", "MutationPal_Mutant"),
    ),
    PalPreset(
        "combat_max_attack",
        "Combat - Maximum Attack",
        "Maximum raw Attack stat. Musclehead replaces Serenity, so active-skill cooldowns are longer and Work Speed is reduced.",
        (("skills", "Passives -> Twin-Edged Holy Blade, God of Destruction, Demon God, Musclehead"),),
        ("WorldTree_ATK", "WorldTree_ATK_DEF", "PAL_ALLAttack_up3", "Noukin"),
    ),
    PalPreset(
        "tank_max_defense",
        "Tank - Maximum Defense",
        "Maximum Defense stack. Sanctified Meat Shield reduces Attack, while Diamond Body adds flinch and knockback immunity.",
        (("skills", "Passives -> Sanctified Meat Shield, Diamond Body, Idiosyncratic, Legend"),),
        ("WorldTree_DEF", "Deffence_up3", "MutationPal_Mutant", "Legend"),
    ),
    PalPreset(
        "tank_regeneration",
        "Tank - Regeneration",
        "Defense and automatic healing for prolonged fights. The loadout gives up Legend's movement and Attack for Immortality's sustain.",
        (("skills", "Passives -> Sanctified Meat Shield, Diamond Body, Idiosyncratic, Immortality"),),
        ("WorldTree_DEF", "Deffence_up3", "MutationPal_Mutant", "MutationPal_Immortal"),
    ),
    PalPreset(
        "combat_mount_boss",
        "Combat Mount - Boss DPS",
        "All-purpose mounted burst damage. Choose an elemental blueprint instead when most mounted attacks share one element.",
        (("skills", "Passives -> Twin-Edged Holy Blade, God of Destruction, Demon God, Serenity"),),
        ("WorldTree_ATK", "WorldTree_ATK_DEF", "PAL_ALLAttack_up3", "CoolTimeReduction_Up_1"),
    ),
    PalPreset(
        "combat_mount_practical",
        "Combat Mount - Practical",
        "The sensible general-use mount: damage, Defense, cooldown reduction, movement speed, and mounted stamina.",
        (("skills", "Passives -> Demon God, Serenity, Legend, Eternal Engine"),),
        ("PAL_ALLAttack_up3", "CoolTimeReduction_Up_1", "Legend", "Stamina_Up_3"),
    ),
    PalPreset(
        "worker_max",
        "Worker - Maximum Work Speed",
        "Pure throughput worker. The work-speed traits trade convenience and combat power for output.",
        (("skills", "Passives -> Demon's Hand, Remarkable Craftsmanship, Artisan, Work Slave"),),
        ("WorldTree_CraftSpeed", "CraftSpeed_up3", "CraftSpeed_up2", "PAL_CorporateSlave"),
    ),
    PalPreset(
        "worker_247",
        "Worker - 24/7",
        "Low-maintenance worker. Insomnia is used in the default template; Vampiric can replace it when desired.",
        (("skills", "Passives -> Demon's Hand, Remarkable Craftsmanship, Artisan, Insomnia"),),
        ("WorldTree_CraftSpeed", "CraftSpeed_up3", "CraftSpeed_up2", "Nocturnal"),
    ),
    PalPreset(
        "transporter_max",
        "Transporter - Maximum Mobility",
        "Movement-focused transporter. Transporting suitability and loaded transport speed remain separate mechanics.",
        (("skills", "Passives -> Dimensional Leap, Swift, Legend, Runner"),),
        ("WorldTree_MoveSpeed", "MoveSpeed_up_3", "Legend", "MoveSpeed_up_2"),
    ),
    PalPreset(
        "transporter_247",
        "Transporter - 24/7",
        "Mobility-focused transporter with Vampiric for night operation and sustain.",
        (("skills", "Passives -> Dimensional Leap, Swift, Legend, Vampiric"),),
        ("WorldTree_MoveSpeed", "MoveSpeed_up_3", "Legend", "Vampire"),
    ),
    PalPreset(
        "ranch_max",
        "Ranch Worker - Maximum Production",
        "Ranch Master adds suitability; the remaining slots maximize work output.",
        (("skills", "Passives -> Ranch Master, Demon's Hand, Remarkable Craftsmanship, Artisan"),),
        ("WorkSuitabilityAddRank_MonsterFarm_2", "WorldTree_CraftSpeed", "CraftSpeed_up3", "CraftSpeed_up2"),
    ),
    PalPreset(
        "ranch_247",
        "Ranch Worker - 24/7",
        "Ranch production with Vampiric for night operation and sustain.",
        (("skills", "Passives -> Ranch Master, Demon's Hand, Remarkable Craftsmanship, Vampiric"),),
        ("WorkSuitabilityAddRank_MonsterFarm_2", "WorldTree_CraftSpeed", "CraftSpeed_up3", "Vampire"),
    ),
    PalPreset(
        "breeding_max",
        "Breeding Pal - Maximum Support",
        "Breeding speed, egg production, incubation, and hunger management.",
        (("skills", "Passives -> Philanthropist, Babysitter, Vampiric, Mastery of Fasting"),),
        ("Test_PalEgg_HatchingSpeed_Up", "MutationPal_Babysitter", "Vampire", "PAL_FullStomach_Down_3"),
    ),
    PalPreset(
        "breeding_low_maintenance",
        "Breeding Pal - Low Maintenance",
        "Breeding support with hunger and SAN interruption reduction.",
        (("skills", "Passives -> Philanthropist, Babysitter, Mastery of Fasting, Heart of the Immovable King"),),
        ("Test_PalEgg_HatchingSpeed_Up", "MutationPal_Babysitter", "PAL_FullStomach_Down_3", "PAL_Sanity_Down_3"),
    ),
    PalPreset(
        "mount_max",
        "Ground/Flying Mount - Maximum Speed",
        "General movement speed for naturally nocturnal mounts and high-speed travel.",
        (("skills", "Passives -> Dimensional Leap, Swift, Legend, Runner"),),
        ("WorldTree_MoveSpeed", "MoveSpeed_up_3", "Legend", "MoveSpeed_up_2"),
    ),
    PalPreset(
        "mount_stamina",
        "Ground/Flying Mount - Speed and Stamina",
        "Slightly less flexible than pure speed, but generally better for sustained exploration.",
        (("skills", "Passives -> Dimensional Leap, Swift, Legend, Eternal Engine"),),
        ("WorldTree_MoveSpeed", "MoveSpeed_up_3", "Legend", "Stamina_Up_3"),
    ),
    PalPreset(
        "water_max",
        "Water Mount - Maximum Water Speed",
        "Water-only movement speed stack.",
        (("skills", "Passives -> King of the Waves, Dimensional Leap, Ace Swimmer, Sleek Stroke"),),
        ("SwimSpeed_up_3", "WorldTree_MoveSpeed", "SwimSpeed_up_2", "SwimSpeed_up_1"),
    ),
    PalPreset(
        "water_stamina",
        "Water Mount - Speed and Stamina",
        "Water speed with additional mounted stamina for actual exploration.",
        (("skills", "Passives -> King of the Waves, Dimensional Leap, Ace Swimmer, Eternal Engine"),),
        ("SwimSpeed_up_3", "WorldTree_MoveSpeed", "SwimSpeed_up_2", "Stamina_Up_3"),
    ),
    PalPreset(
        "party_combat",
        "Player Support - Combat",
        "Player-facing attack, defense, reload, and stamina support while this Pal is in the party.",
        (("skills", "Passives -> Vanguard, Stronghold Strategist, Reload Master, Wellness Watcher"),),
        ("TrainerATK_UP_1", "TrainerDEF_UP_1", "ReloadSpeedUp_Passive", "PlayerSP_DecreaseRate_Passive"),
    ),
    PalPreset(
        "party_gathering",
        "Player Support - Gathering",
        "Player-facing mining, logging, work-speed, and stamina support while this Pal is in the party.",
        (("skills", "Passives -> Mine Foreman, Logging Foreman, Motivational Leader, Wellness Watcher"),),
        ("TrainerMining_up1", "TrainerLogging_up1", "TrainerWorkSpeed_UP_1", "PlayerSP_DecreaseRate_Passive"),
    ),
    PalPreset(
        "party_survival",
        "Player Support - Survival",
        "Player-facing attack, defense, regeneration, and stamina support while this Pal is in the party.",
        (("skills", "Passives -> Vanguard, Stronghold Strategist, Healing Coach, Wellness Watcher"),),
        ("TrainerATK_UP_1", "TrainerDEF_UP_1", "AutoHPRegeneRate_Passive", "PlayerSP_DecreaseRate_Passive"),
    ),
)


_ELEMENTAL_TRAITS = (
    ("Neutral", "Celestial Emperor", "ElementBoost_Normal_2_PAL"),
    ("Fire", "Flame Emperor", "ElementBoost_Fire_2_PAL"),
    ("Water", "Lord of the Sea", "ElementBoost_Aqua_2_PAL"),
    ("Lightning", "Lord of Lightning", "ElementBoost_Thunder_2_PAL"),
    ("Grass", "Spirit Emperor", "ElementBoost_Leaf_2_PAL"),
    ("Ice", "Ice Emperor", "ElementBoost_Ice_2_PAL"),
    ("Earth", "Earth Emperor", "ElementBoost_Earth_2_PAL"),
    ("Dark", "Lord of the Underworld", "ElementBoost_Dark_2_PAL"),
    ("Dragon", "Divine Dragon", "ElementBoost_Dragon_2_PAL"),
)


def _elemental_presets() -> tuple[PalPreset, ...]:
    """Create explicit element choices so the picker never applies a placeholder trait."""
    presets: list[PalPreset] = []
    for element, trait, code in _ELEMENTAL_TRAITS:
        presets.extend(
            (
                PalPreset(
                    f"elemental_{element.casefold()}_burst",
                    f"Elemental - {element} Burst",
                    f"Maximum {element} skill damage. Use only when most equipped attacks are {element}; Twin-Edged Holy Blade and God of Destruction add severe defensive tradeoffs.",
                    (("skills", f"Passives -> Twin-Edged Holy Blade, God of Destruction, Serenity, {trait}"),),
                    ("WorldTree_ATK", "WorldTree_ATK_DEF", "CoolTimeReduction_Up_1", code),
                ),
                PalPreset(
                    f"elemental_{element.casefold()}_balanced",
                    f"Elemental - {element} Balanced",
                    f"Balanced {element} combat. Use only when most equipped attacks are {element}; this keeps Legend's Defense and movement bonuses.",
                    (("skills", f"Passives -> Demon God, Serenity, Legend, {trait}"),),
                    ("PAL_ALLAttack_up3", "CoolTimeReduction_Up_1", "Legend", code),
                ),
            )
        )
    return tuple(presets)


PRESETS = PRESETS + _elemental_presets()


BLUEPRINT_CATEGORY_ORDER = (
    "Foundation",
    "Combat",
    "Raid",
    "Tank",
    "Combat Mount",
    "Ground/Flying Mount",
    "Water Mount",
    "Worker",
    "Transporter",
    "Ranch",
    "Breeding",
    "Player Support",
    "Elemental",
)


def blueprint_category(key: str) -> str:
    """Return the stable user-facing category used by the blueprint picker."""
    if key in {"max_iv", "max_level", "rank_max"}:
        return "Foundation"
    if key.startswith("combat_raid_"):
        return "Raid"
    if key.startswith("combat_mount_"):
        return "Combat Mount"
    if key.startswith("combat_"):
        return "Combat"
    if key.startswith("tank_"):
        return "Tank"
    if key.startswith("mount_"):
        return "Ground/Flying Mount"
    if key.startswith("water_"):
        return "Water Mount"
    if key.startswith("worker_"):
        return "Worker"
    if key.startswith("transporter_"):
        return "Transporter"
    if key.startswith("ranch_"):
        return "Ranch"
    if key.startswith("breeding_"):
        return "Breeding"
    if key.startswith("party_"):
        return "Player Support"
    if key.startswith("elemental_"):
        return "Elemental"
    return "Foundation"


def ordered_presets() -> tuple[PalPreset, ...]:
    """Return all registered blueprints in deterministic picker category order."""
    category_index = {name: index for index, name in enumerate(BLUEPRINT_CATEGORY_ORDER)}
    source_order = {preset.key: index for index, preset in enumerate(PRESETS)}
    return tuple(
        sorted(
            PRESETS,
            key=lambda preset: (
                category_index[blueprint_category(preset.key)],
                source_order[preset.key],
            ),
        )
    )


def _preset(key: str) -> PalPreset:
    preset = next((item for item in PRESETS if item.key == key), None)
    if preset is None:
        raise ValueError(f"Unknown blueprint: {key}")
    return preset


def preview_preset(key: str | None, scope: PresetScope | None = None) -> tuple[str, ...]:
    """Return human-readable impact lines without mutating the current draft."""
    if not key:
        return ("Choose a blueprint to see its affected fields.",)
    preset = _preset(key)
    scope = scope or PresetScope()
    allowed = {
        "level": scope.level,
        "rank": scope.rank,
        "attributes": scope.attributes,
        "skills": scope.passives_enabled,
        "active_skills": scope.active_skills,
    }
    lines = tuple(text for category, text in preset.changes if allowed.get(category, False))
    if not lines:
        return (f"{preset.label}: no fields selected.",)
    return (
        f"{preset.label} will affect: " + " | ".join(lines),
        "Skills/traits: unchanged by this blueprint."
        if not scope.passives_enabled or not preset.passive_skills
        else "Skills/traits: passive loadout will be replaced.",
    )


def apply_preset(
    template: PalTemplate,
    key: str,
    scope: PresetScope | None = None,
) -> PalTemplate:
    """Apply a blueprint while respecting its explicit change scope."""
    scope = scope or PresetScope()
    preset = _preset(key)
    changes: dict[str, object] = {}
    if key == "max_iv" and scope.attributes:
        changes.update(iv_hp=100, iv_attack=100, iv_defense=100)
    elif key == "max_level" and scope.level:
        changes["level"] = 80
    elif key == "rank_max" and scope.rank:
        changes["rank"] = 5
    elif key == "combat_max":
        if scope.level:
            changes["level"] = 80
        if scope.attributes:
            changes.update(iv_hp=100, iv_attack=100, iv_defense=100)
    if scope.active_skills and preset.active_skills:
        changes["active_skills"] = list(preset.active_skills)
    if scope.passives_enabled and preset.passive_skills:
        changes["passive_skills"] = list(preset.passive_skills)
    return replace(template, **changes)
