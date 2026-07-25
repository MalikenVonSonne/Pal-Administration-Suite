# Pal Admin runtime reader

This mod contains a read-only UE4SS Lua module for the live Pal roster. It locates `PalCharacterManager`, enumerates `IndividualParameterMap`, resolves each parameter through its `UPalIndividualCharacterHandle`, and reads the verified `SaveParameter` fields into fresh plain-Lua tables.

The module does not call any setter, save API, inventory API, or world mutation API. It filters strong `Player` markers, plus `Human`, `NPC`, or `Enemy` markers only when neither reflected class identifies the record as a Pal. Unknown classes and Pal variants such as a hypothetical `PalNPC` are retained.

## Loading and API

Load it from the future PalAdminRuntime entrypoint with:

```lua
local RuntimeReader = require("runtime_reader")
local records, snapshot = RuntimeReader.get_records()
```

`RuntimeReader.read()` returns a fresh snapshot:

```lua
{
    ok = true,
    manager = "...",
    scanned = 0,
    included = 0,
    filtered = 0,
    records = {
        {
            map_key = "...",
            identity_key = "...",
            identity_source = "handle.instance_id",
            player_uid = "...",
            instance_id = "...",
            debug_name = "...",
            individual_class = "...",
            character_id = "...",
            character_class = "...",
            level = 1,
            -- plus the other normalized fields listed below
        }
    }
}
```

Normalized public fields include the `PalInstanceID` members `player_uid`, `instance_id`, and `debug_name` resolved through the individual handle, plus `identity_key` (currently the canonical instance ID) and `identity_source`. The record also includes `character_id`, `unique_npc_id`, `gender`, `character_class`, `level`, `rank`, `exp`, `nickname`, `equip_waza`, `passive_skill_list`, `owner_player_uid`, `item_container_id`, `slot_id`, `talent_hp`, `talent_melee`, `talent_shot`, `talent_defense`, `current_work_suitability`, and `is_favorite_pal`. Array fields are copied to ordinary Lua arrays; reflected scalar values are converted to Lua primitives or text where needed.

Loading the module registers the read-only console command `paladmin_runtime_read`. It prints the scan counts and a compact line for each included record. The module also publishes the same table as `_G.PalAdminRuntimeReader` for later UI integration.

Each initial load and F9 refresh also writes an atomic local bridge file at
`%LOCALAPPDATA%\PalAdmin\runtime_snapshot.json`. The file is observation-only
and contains the versioned `paladmin-runtime-snapshot` envelope. The desktop
UI can poll it while Palworld is running; a missing or stale file is reported
as unavailable rather than treated as a live connection.

The F9 entrypoint prints each record's canonical `identity_key` and compares it with the previous F9 snapshot. `stable=true` means every included record had a usable canonical identity and the identity multiset did not add or lose records between the two reads. `RuntimeReader.find_record(snapshot, identity_key)` resolves a selection by identity rather than list position. No edit target or mutation is created by this reader.

## Runtime assumptions

- UE4SS provides `FindFirstOf`, `RegisterConsoleCommandHandler`, valid UObject `:IsValid()`/`:GetFullName()` methods, and the reflected-container `:ForEach()` pattern used by the existing development probe.
- The manager and map are available after the Palworld world has initialized. Before then, `read()` returns `ok = false` with an explanatory `error`.
- `IndividualParameterMap` values are expected to be UObject or weak-object wrappers exposing `:get()`; invalid entries are skipped.
- The field list is intentionally explicit and is based on the fields verified by `PalAdminDevProbe`; missing fields are omitted from an individual normalized record rather than guessed.
- Array normalization is capped at 128 items per field to keep a UI refresh bounded. No live UObject references are retained in the returned snapshot.
