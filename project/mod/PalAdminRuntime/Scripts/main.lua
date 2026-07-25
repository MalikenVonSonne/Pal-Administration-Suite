local RuntimeReader = require("runtime_reader")
local SnapshotWriter = require("snapshot_writer")
local previous_snapshot = nil

local function usable_identity(value)
    if value == nil then return nil end
    local text = tostring(value)
    local lower = string.lower(text)
    if text == ""
        or lower == "none"
        or lower == "nil"
        or lower == "null"
        or string.find(lower, "trivialobject:", 1, true)
        or string.find(lower, "uscriptstruct:", 1, true)
        or string.find(lower, "remoteunrealparam", 1, true)
        or string.find(lower, "table:", 1, true) then
        return nil
    end
    return text
end

local function identity_value(record)
    local identity_key = usable_identity(record.identity_key)
    if identity_key then
        return identity_key, record.identity_source or "identity_key"
    end
    local instance_id = usable_identity(record.instance_id)
    if instance_id then
        return instance_id, "handle.instance_id"
    end
    local unique_npc_id = usable_identity(record.unique_npc_id)
    if unique_npc_id then
        return unique_npc_id, "unique_npc_id"
    end
    local map_key = usable_identity(record.map_key)
    if map_key then
        return map_key, "map_key"
    end
    return nil, "none"
end

local function identity_counts(snapshot)
    local counts = {}
    local unidentified = 0
    for _, record in ipairs(snapshot.records or {}) do
        local identity = identity_value(record)
        if identity then
            counts[identity] = (counts[identity] or 0) + 1
        else
            unidentified = unidentified + 1
        end
    end
    return counts, unidentified
end

local function identity_delta(previous, current)
    local added = 0
    local removed = 0
    for identity, count in pairs(current) do
        local old_count = previous[identity] or 0
        if count > old_count then
            added = added + (count - old_count)
        end
    end
    for identity, count in pairs(previous) do
        local new_count = current[identity] or 0
        if count > new_count then
            removed = removed + (count - new_count)
        end
    end
    return added, removed
end

local function console_value(value)
    if value == nil then return "nil" end
    if type(value) == "table" then
        return "[" .. tostring(#value) .. " items]"
    end
    return tostring(value)
end

local function log_snapshot(snapshot)
    print(string.format(
        "[Pal Admin] runtime entrypoint ok=%s scanned=%d included=%d filtered=%d\n",
        tostring(snapshot.ok), snapshot.scanned, snapshot.included, snapshot.filtered
    ))
    if snapshot.error then
        print("[Pal Admin] runtime entrypoint error=" .. tostring(snapshot.error) .. "\n")
    end

    local current_ids, unidentified = identity_counts(snapshot)
    for index, record in ipairs(snapshot.records or {}) do
        local candidate, candidate_source = identity_value(record)
        print(string.format(
            "[Pal Admin] identity pal[%d] source=%s candidate=%s instance_id=%s player_uid=%s debug_name=%s unique_npc_id=%s map_key=%s character_id=%s level=%s\n",
            index,
            candidate_source,
            console_value(candidate),
            console_value(record.instance_id),
            console_value(record.player_uid),
            console_value(record.debug_name),
            console_value(record.unique_npc_id),
            console_value(record.map_key),
            console_value(record.character_id),
            console_value(record.level)
        ))
    end

    if previous_snapshot then
        local previous_ids, previous_unidentified = identity_counts(previous_snapshot)
        local added, removed = identity_delta(previous_ids, current_ids)
        local stable = added == 0 and removed == 0
            and unidentified == 0
            and previous_unidentified == 0
        print(string.format(
            "[Pal Admin] identity comparison stable=%s added=%d removed=%d unidentified=%d\n",
            tostring(stable),
            added,
            removed,
            unidentified
        ))
    else
        print(string.format(
            "[Pal Admin] identity baseline candidates=%d unidentified=%d\n",
            snapshot.included or 0,
            unidentified
        ))
    end
    previous_snapshot = snapshot
end

local function write_snapshot(snapshot)
    local ok, result = SnapshotWriter.write(snapshot)
    if ok then
        print("[Pal Admin] runtime snapshot bridge wrote " .. tostring(result) .. "\n")
    else
        print("[Pal Admin] runtime snapshot bridge unavailable: " .. tostring(result) .. "\n")
    end
end

RegisterKeyBind(Key.F9, function()
    local snapshot = RuntimeReader.read()
    log_snapshot(snapshot)
    write_snapshot(snapshot)
end)

local initial_snapshot = RuntimeReader.read()
log_snapshot(initial_snapshot)
write_snapshot(initial_snapshot)
print("[Pal Admin] read-only runtime entrypoint loaded; F9 refreshes the live roster snapshot\n")
