-- Pal Admin Runtime snapshot bridge
--
-- Writes only a local, atomic JSON observation for the desktop Pal Admin UI.
-- This file is never sent to the game and no game object is mutated.

local SnapshotWriter = {
    VERSION = 1,
    FILE_NAME = "runtime_snapshot.json",
}

local function local_app_data()
    if not os or not os.getenv then return nil end
    local ok, value = pcall(function()
        return os.getenv("LOCALAPPDATA")
    end)
    if not ok or value == nil or value == "" then return nil end
    return value
end

function SnapshotWriter.path()
    local root = local_app_data()
    if not root then return nil end
    return root .. "\\PalAdmin\\" .. SnapshotWriter.FILE_NAME
end

local function escape_string(value)
    local text = tostring(value)
    text = string.gsub(text, "\\", "\\\\")
    text = string.gsub(text, '"', '\\"')
    text = string.gsub(text, "\b", "\\b")
    text = string.gsub(text, "\f", "\\f")
    text = string.gsub(text, "\n", "\\n")
    text = string.gsub(text, "\r", "\\r")
    text = string.gsub(text, "\t", "\\t")
    return '"' .. text .. '"'
end

local function is_array(value)
    local count = 0
    local maxIndex = 0
    for key, _ in pairs(value) do
        if type(key) ~= "number" or key < 1 or key % 1 ~= 0 then
            return false, 0
        end
        count = count + 1
        if key > maxIndex then maxIndex = key end
    end
    return maxIndex == count, maxIndex
end

local function encode(value)
    local valueType = type(value)
    if value == nil then return "null" end
    if valueType == "boolean" then return value and "true" or "false" end
    if valueType == "number" then return tostring(value) end
    if valueType == "string" then return escape_string(value) end
    if valueType ~= "table" then return escape_string(value) end

    local array, size = is_array(value)
    local parts = {}
    if array then
        for index = 1, size do
            parts[#parts + 1] = encode(value[index])
        end
        return "[" .. table.concat(parts, ",") .. "]"
    end

    local keys = {}
    for key, _ in pairs(value) do
        keys[#keys + 1] = tostring(key)
    end
    table.sort(keys)
    for _, key in ipairs(keys) do
        parts[#parts + 1] = escape_string(key) .. ":" .. encode(value[key])
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

local function ensure_directory()
    local path = SnapshotWriter.path()
    if not path or not io then return false, "LOCALAPPDATA or io unavailable" end
    local probe = io.open(path, "ab")
    if probe then
        probe:close()
        return true, nil
    end
    local parent = string.match(path, "^(.*)\\[^\\]+$")
    if not parent then return false, "snapshot parent unavailable" end
    local marker = io.open(parent .. "\\.paladmin_runtime_probe", "ab")
    if marker then
        marker:close()
        os.remove(parent .. "\\.paladmin_runtime_probe")
        return true, nil
    end
    return false, "snapshot directory unavailable: " .. parent
end

function SnapshotWriter.write(snapshot)
    local path = SnapshotWriter.path()
    if not path then return false, "snapshot path unavailable" end
    local directoryOk, directoryError = ensure_directory()
    if not directoryOk then return false, directoryError end
    if not io or not os then return false, "file APIs unavailable" end

    local payload = {
        schema = "paladmin-runtime-snapshot",
        schema_version = SnapshotWriter.VERSION,
        written_at = os.time and os.time() or nil,
        snapshot = snapshot,
    }
    local temporary = path .. ".tmp"
    local file, openError = io.open(temporary, "wb")
    if not file then return false, tostring(openError) end
    local writeOk, writeError = pcall(function()
        file:write(encode(payload))
        file:flush()
        file:close()
    end)
    if not writeOk then
        pcall(function() file:close() end)
        pcall(function() os.remove(temporary) end)
        return false, tostring(writeError)
    end

    local renamed, renameError = os.rename(temporary, path)
    if not renamed then
        pcall(function() os.remove(path) end)
        renamed, renameError = os.rename(temporary, path)
    end
    if not renamed then
        pcall(function() os.remove(temporary) end)
        return false, tostring(renameError)
    end
    return true, path
end

SnapshotWriter.encode = encode

return SnapshotWriter
