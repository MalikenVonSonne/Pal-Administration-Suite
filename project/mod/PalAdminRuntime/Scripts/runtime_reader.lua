-- Pal Admin Runtime Reader
--
-- Read-only live Pal roster access for UE4SS.  The module deliberately returns
-- plain Lua values so a later UI does not need to retain reflected UObject
-- references between refreshes.

local RuntimeReader = {
    VERSION = 2,
    COMMAND = "paladmin_runtime_read",
}

local MAX_ARRAY_ITEMS = 128

-- These are the SaveParameter members currently verified by the development
-- probe.  Keep this list explicit: it is the public reader schema, rather than
-- an attempt to expose every reflected field.
local VERIFIED_FIELDS = {
    { key = "character_id", source = "CharacterID" },
    { key = "unique_npc_id", source = "UniqueNPCID" },
    { key = "gender", source = "Gender" },
    { key = "character_class", source = "CharacterClass" },
    { key = "is_player", source = "IsPlayer" },
    { key = "level", source = "Level" },
    { key = "rank", source = "Rank" },
    { key = "exp", source = "Exp" },
    { key = "nickname", source = "NickName" },
    { key = "equip_waza", source = "EquipWaza" },
    { key = "passive_skill_list", source = "PassiveSkillList" },
    { key = "owner_player_uid", source = "OwnerPlayerUId" },
    { key = "item_container_id", source = "ItemContainerId" },
    { key = "slot_id", source = "SlotId" },
    { key = "talent_hp", source = "Talent_HP" },
    { key = "talent_melee", source = "Talent_Melee" },
    { key = "talent_shot", source = "Talent_Shot" },
    { key = "talent_defense", source = "Talent_Defense" },
    { key = "current_work_suitability", source = "CurrentWorkSuitability" },
    { key = "is_favorite_pal", source = "IsFavoritePal" },
}

RuntimeReader.VERIFIED_FIELDS = VERIFIED_FIELDS

local function is_valid_object(value)
    if value == nil then return false end

    local ok, valid = pcall(function()
        return value:IsValid()
    end)
    return ok and valid == true
end

local function unwrap(value)
    if value == nil then return nil end
    local valueType = type(value)
    if valueType == "string" or valueType == "number" or valueType == "boolean" then
        return value
    end

    local ok, result = pcall(function()
        if value.get then
            return value:get()
        end
        return value
    end)
    if ok then return result end
    return nil
end

local function object_name(value)
    if value == nil then return nil end

    local ok, name = pcall(function()
        return value:GetFullName()
    end)
    if ok and name ~= nil then return tostring(name) end

    ok, name = pcall(function()
        return value:GetName()
    end)
    if ok and name ~= nil then return tostring(name) end

    return nil
end

local function value_text(value)
    if value == nil then return nil end
    if type(value) == "string" or type(value) == "number" or type(value) == "boolean" then
        return value
    end

    local ok, text = pcall(function()
        return value:ToString()
    end)
    if ok and text ~= nil then return tostring(text) end

    return tostring(value)
end

local function normalize_value(value, depth)
    if value == nil then return nil end

    local valueType = type(value)
    if valueType == "string" or valueType == "number" or valueType == "boolean" then
        return value
    end

    depth = depth or 0
    if depth < 2 then
        local arrayLikeOk, arrayLike = pcall(function()
            return value.GetArrayNum and value.ForEach
        end)
        if arrayLikeOk and arrayLike then
            local countOk, count = pcall(function()
                return value:GetArrayNum()
            end)
            if countOk and type(count) == "number" then
                local normalized = {}
                local emitted = 0
                pcall(function()
                    value:ForEach(function(_, item)
                        if emitted >= MAX_ARRAY_ITEMS then return true end
                        emitted = emitted + 1
                        normalized[emitted] = normalize_value(unwrap(item), depth + 1)
                    end)
                end)
                return normalized
            end
        end
    end

    return value_text(value)
end

local function unusable_wrapper_text(text)
    if text == nil then return true end
    local lower = string.lower(tostring(text))
    return lower == ""
        or lower == "none"
        or lower == "nil"
        or lower == "null"
        or string.find(lower, "trivialobject:", 1, true) ~= nil
        or string.find(lower, "uscriptstruct:", 1, true) ~= nil
        or string.find(lower, "remoteunrealparam", 1, true) ~= nil
        or string.find(lower, "table:", 1, true) ~= nil
end

local function normalize_guid(value)
    if value == nil then return nil end

    local direct = value_text(value)
    if direct and not unusable_wrapper_text(direct) then
        return direct
    end

    local parts = {}
    for _, name in ipairs({ "A", "B", "C", "D" }) do
        local ok, part = pcall(function()
            return value[name]
        end)
        if not ok or part == nil then
            return nil
        end
        local partText = tostring(part)
        if unusable_wrapper_text(partText) then
            return nil
        end
        parts[#parts + 1] = partText
    end
    if #parts == 4 then
        return table.concat(parts, "-")
    end
    return nil
end

local function read_raw_field(container, fieldName)
    local ok, value = pcall(function()
        return container:GetPropertyValue(fieldName)
    end)
    if not ok or value == nil then
        ok, value = pcall(function()
            return container[fieldName]
        end)
    end
    if not ok or value == nil then return nil end
    return value
end

local function read_struct_field(container, fieldName, isGuid)
    local value = read_raw_field(container, fieldName)
    if value == nil then return nil end
    if isGuid then return normalize_guid(value) end
    return normalize_value(value)
end

local function read_field(container, fieldName)
    local ok, value = pcall(function()
        return container[fieldName]
    end)
    if not ok then return false, nil end
    return true, normalize_value(value)
end

local function contains_marker(value, markers)
    if type(value) ~= "string" then return false end
    local lower = string.lower(value)
    for _, marker in ipairs(markers) do
        if string.find(lower, marker, 1, true) then
            return true
        end
    end
    return false
end

-- The map can contain player or other individual records.  Only strong,
-- explicit markers are rejected; unknown classes remain visible to avoid
-- accidentally hiding a Pal variant introduced by a game update.
local PLAYER_MARKERS = {
    "playercharacter",
    "player",
}

local NON_PAL_MARKERS = {
    "humancharacter",
    "human",
    "npc",
    "enemy",
}

local function is_obvious_non_pal(individual, record)
    if record.is_player == true then
        return true
    end

    local individualClass = object_name(individual) or ""
    local characterClass = record.character_class or ""

    if contains_marker(individualClass, PLAYER_MARKERS)
        or contains_marker(characterClass, PLAYER_MARKERS) then
        return true
    end

    -- NPC/Enemy are only decisive when neither reflected class identifies the
    -- record as Pal.  This avoids rejecting a Pal variant named PalNPC, etc.
    local hasPalMarker = contains_marker(individualClass, { "pal" })
        or contains_marker(characterClass, { "pal" })
    return not hasPalMarker
        and (contains_marker(individualClass, NON_PAL_MARKERS)
            or contains_marker(characterClass, NON_PAL_MARKERS))
end

local function resolve_individual_id(manager, individual)
    if not manager or not individual then return nil end

    local handleOk, handle = pcall(function()
        return manager:GetIndividualHandleFromCharacterParameter(individual)
    end)
    if not handleOk or not is_valid_object(handle) then
        return nil
    end

    local idOk, individualId = pcall(function()
        return handle:GetIndividualID()
    end)
    if not idOk or individualId == nil then
        return nil
    end
    return unwrap(individualId)
end

local function normalize_record(mapKey, individual, manager)
    if not is_valid_object(individual) then
        return nil, "invalid individual parameter"
    end

    local saveParameterOk, saveParameter = pcall(function()
        return individual.SaveParameter
    end)
    if not saveParameterOk or saveParameter == nil then
        return nil, "SaveParameter unavailable"
    end

    local individualId = read_raw_field(individual, "IndividualId")
    if individualId ~= nil then
        individualId = unwrap(individualId)
    end
    if individualId == nil then
        individualId = resolve_individual_id(manager, individual)
    end

    local record = {
        map_key = normalize_value(unwrap(mapKey)),
        individual_class = object_name(individual),
        player_uid = read_struct_field(individualId, "PlayerUId", true),
        instance_id = read_struct_field(individualId, "InstanceId", true),
        debug_name = read_struct_field(individualId, "DebugName", false),
    }
    record.identity_key = record.instance_id
    record.identity_source = record.identity_key and "handle.instance_id" or nil

    local readable = 0
    for _, field in ipairs(VERIFIED_FIELDS) do
        local readableField, value = read_field(saveParameter, field.source)
        if readableField and value ~= nil then
            record[field.key] = value
            readable = readable + 1
        end
    end

    -- FGuid values need the struct-aware path.  Reading this through the
    -- generic Lua property wrapper produces an opaque UScriptStruct string,
    -- which is not useful for distinguishing owned records from world actors.
    record.owner_player_uid = read_struct_field(saveParameter, "OwnerPlayerUId", true)

    if readable == 0 then
        return nil, "no verified SaveParameter fields readable"
    end
    if is_obvious_non_pal(individual, record) then
        return nil, "explicit player/non-Pal marker"
    end

    return record, nil
end

local function unavailable_snapshot(message)
    return {
        ok = false,
        error = message,
        manager = nil,
        scanned = 0,
        included = 0,
        filtered = 0,
        records = {},
    }
end

-- Return a fresh snapshot.  No UObject or reflected container is returned;
-- callers can safely hand the result to a UI model and refresh it later.
function RuntimeReader.read()
    if type(FindFirstOf) ~= "function" then
        return unavailable_snapshot("FindFirstOf is unavailable")
    end

    local managerOk, manager = pcall(function()
        return FindFirstOf("PalCharacterManager")
    end)
    if not managerOk or not is_valid_object(manager) then
        return unavailable_snapshot("PalCharacterManager is unavailable")
    end

    local mapOk, individualMap = pcall(function()
        return manager.IndividualParameterMap
    end)
    if not mapOk or individualMap == nil then
        return unavailable_snapshot("PalCharacterManager.IndividualParameterMap is unavailable")
    end

    local hasForEachOk, hasForEach = pcall(function()
        return individualMap.ForEach
    end)
    if not hasForEachOk or not hasForEach then
        return unavailable_snapshot("IndividualParameterMap cannot be enumerated")
    end

    local snapshot = {
        ok = true,
        error = nil,
        manager = object_name(manager),
        scanned = 0,
        included = 0,
        filtered = 0,
        records = {},
    }

    local enumerateOk, enumerateError = pcall(function()
        individualMap:ForEach(function(mapKey, valueWrapper)
            snapshot.scanned = snapshot.scanned + 1

            local record, reason = normalize_record(mapKey, unwrap(valueWrapper), manager)
            if record then
                snapshot.included = snapshot.included + 1
                table.insert(snapshot.records, record)
            else
                snapshot.filtered = snapshot.filtered + 1
                snapshot.last_filter_reason = reason
            end
        end)
    end)

    if not enumerateOk then
        snapshot.ok = false
        snapshot.error = "IndividualParameterMap enumeration failed: " .. tostring(enumerateError)
    end

    return snapshot
end

-- Alias with a UI-friendly name for callers that do not want the snapshot
-- counters.  The second return value remains available for diagnostics.
function RuntimeReader.get_records()
    local snapshot = RuntimeReader.read()
    return snapshot.records, snapshot
end

-- Resolve a UI selection back to the current snapshot by canonical identity.
-- List position is deliberately not accepted as an edit/selection key.
function RuntimeReader.find_record(snapshot, identity_key)
    if type(snapshot) ~= "table" or identity_key == nil then return nil end
    for _, record in ipairs(snapshot.records or {}) do
        if record.identity_key == identity_key then
            return record
        end
    end
    return nil
end

local function output_line(output, line)
    if output and output.Log then
        output:Log(line .. "\n")
    else
        print(line .. "\n")
    end
end

local function console_value(value)
    if value == nil then return "nil" end
    if type(value) == "table" then
        return "[" .. tostring(#value) .. " items]"
    end
    return tostring(value)
end

local function register_console_command()
    if type(RegisterConsoleCommandHandler) ~= "function" then
        return false
    end
    if _G.PalAdminRuntimeReaderConsoleRegistered then
        return true
    end

    local ok = pcall(function()
        RegisterConsoleCommandHandler(RuntimeReader.COMMAND, function(_, _, output)
            local snapshot = RuntimeReader.read()
            output_line(output, string.format(
                "[Pal Admin] runtime reader ok=%s scanned=%d included=%d filtered=%d",
                tostring(snapshot.ok), snapshot.scanned, snapshot.included, snapshot.filtered
            ))
            if snapshot.error then
                output_line(output, "[Pal Admin] runtime reader error=" .. tostring(snapshot.error))
            end
            for index, record in ipairs(snapshot.records) do
                output_line(output, string.format(
                    "[Pal Admin] pal[%d] id=%s class=%s level=%s nickname=%s",
                    index,
                    console_value(record.character_id),
                    console_value(record.character_class),
                    console_value(record.level),
                    console_value(record.nickname)
                ))
            end
            return true
        end)
    end)

    if ok then
        _G.PalAdminRuntimeReaderConsoleRegistered = true
    end
    return ok
end

-- The global makes the module convenient for a later UI script while the
-- return value keeps normal require("runtime_reader") integration available.
_G.PalAdminRuntimeReader = RuntimeReader
register_console_command()

return RuntimeReader
