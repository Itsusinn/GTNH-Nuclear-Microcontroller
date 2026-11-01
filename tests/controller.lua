-- Component simulator: no Minecraft world or server is touched.
local now, signals, outputs = 0, {}, {[0]=0,0,0,0,0,0}
local inventories, transfers, writes = {}, {}, {}
local input, stopped, injected = 0, nil, false
local supplyReads, shortageSince, replenished = {}, nil, false
local outputCase, meOutputCase, coolantCase = scenario:match("^output_"), scenario:match("^me_output_"), scenario:match("^coolant_")
local prefix = "gregtech:gt."
local coolant = prefix.."neutroniumHeatCapacitor"
local function stack(name, damage)
    return {name=name, damage=damage or 0, size=64, tag="initial"}
end
local function inventory(name, size, items)
    return {name=name, size=size, items=items or {}}
end
local fuel = prefix.."rodNaquadah32"
local source = {stack(fuel), stack(coolant)}
if scenario == "third_fuel" then
    source[3], source[4] = stack(prefix.."rodExcitedUranium4"), stack(prefix.."rodExcitedPlutonium4")
elseif scenario == "hybrid" then
    source[3] = stack(prefix.."rodExcitedUranium4")
end
inventories[1] = inventory("chest", 8, source)
inventories[2] = inventory("chest", 8)
local coolantSlots = {[3]=1,[6]=1,[9]=1,[10]=1,[15]=1,[22]=1,[26]=1,[29]=1,[33]=1,[40]=1,[45]=1,[46]=1,[49]=1,[52]=1}
local horizontal = scenario:match("^mapping_")
local reactorSide = horizontal and 3 or 0
local rods = {}
for slot=1,54 do
    rods[slot] = stack(coolantSlots[slot] and coolant or
        (scenario == "hybrid" and slot % 2 == 0 and prefix.."rodExcitedUranium4" or fuel))
    rods[slot].size = 1
end
inventories[reactorSide] = inventory("blockReactorChamber", 54, rods)
if outputCase or meOutputCase or coolantCase then
    rods[26].damage = 100
end
if outputCase then
    for slot=1,8 do inventories[2].items[slot] = stack("junk") end
end
if meOutputCase then
    local items = {stack(fuel), stack(coolant)}
    for slot=3,9 do items[slot] = stack("junk") end
    inventories[5] = inventory("tile.appliedenergistics2.BlockInterface", 9, items)
end
if scenario == "mapping_multiple" then
    local other = {}
    for slot=1,54 do other[slot] = stack(rods[slot].name) end
    inventories[4] = inventory("blockReactorChamber", 54, other)
end
if scenario == "me_priority" or scenario == "me_stale_hybrid" then
    if scenario == "me_priority" then
        source[3] = stack(prefix.."rodThorium4") -- incompatible items outside the ME interface
    else
        source[3] = stack(prefix.."rodExcitedUranium4")
    end
    inventories[5] = inventory("tile.appliedenergistics2.BlockInterface", 9,
        {stack(prefix.."rodThorium4"), stack(coolant)})
    for slot=1,54 do
        if not coolantSlots[slot] then rods[slot] = stack(prefix.."rodThorium4") end
    end
end
local function clone(value)
    local result = {}
    for k,v in pairs(value or {}) do result[k] = v end
    return result
end
local transposer = {}
function transposer.getInventorySize(side)
    return inventories[side] and inventories[side].size
end
function transposer.getInventoryName(side)
    return inventories[side] and inventories[side].name
end
function transposer.getAllStacks(side)
    local inv, items, index = assert(inventories[side]), {}, 0
    if (coolantCase and side == 1) or (meOutputCase and side == 5) then
        supplyReads[#supplyReads+1] = {time=now, available=inv.items[2] ~= nil}
        if coolantCase and not inv.items[2] and #transfers > 0 and not shortageSince then shortageSince = now end
    end
    for i=1,inv.size do items[i] = clone(inv.items[i]) end
    return setmetatable({count=function() return inv.size end}, {__call=function()
        index = index + 1
        return items[index]
    end})
end
function transposer.getStackInSlot(side, slot)
    return clone(inventories[side].items[slot])
end
function transposer.transferItem(from, to, count, slot, target)
    transfers[#transfers+1] = {time=now, from=from, to=to, slot=slot}
    if target and target < 1 then error("invalid target slot") end
    if to == reactorSide and not target then error("missing target") end
    local src, dst = inventories[from].items, inventories[to].items
    if not src[slot] then return 0 end
    if not target then
        for i=1,inventories[to].size do if not dst[i] then target=i; break end end
    end
    if not target or dst[target] then return 0 end
    transfers[#transfers].moved = 1
    dst[target] = clone(src[slot])
    dst[target].size = 1
    src[slot].size = src[slot].size - 1
    if src[slot].size == 0 then src[slot] = nil end
    return 1
end
local redstone = {}
function redstone.getInput()
    return {[0]=0,input,0,0,0,0}
end
function redstone.setOutput(side, value)
    if type(side) == "table" then
        local previous = clone(outputs)
        for k,v in pairs(side) do
            if outputs[k] > 0 and v == 0 then stopped = now end
            outputs[k] = v
        end
        writes[#writes+1] = {time=now, value=0}
        return previous
    end
    local previous = outputs[side]
    outputs[side] = value
    if previous > 0 and value == 0 then stopped = now end
    writes[#writes+1] = {time=now, side=side, value=value}
    return previous
end
component = {
    list=function(kind) return function() return kind end end,
    proxy=function(address) return address == "transposer" and transposer or redstone end,
}
computer = {
    freeMemory=function() return 100000 end,
    uptime=function() return now end,
    pushSignal=function(signal) signals[#signals+1] = signal end,
    shutdown=function() error("UNEXPECTED_REBOOT") end,
    pullSignal=function(timeout)
        now = now + timeout
        if coolantCase and not injected then
            source[2], injected = nil, true
        end
        if coolantCase and scenario == "coolant_recover" and shortageSince and not replenished
            and now >= shortageSince + recovery_attempt then
            source[2], replenished = stack(coolant), true
        end
        if scenario == "output_recover" and not replenished then
            local failed, last = 0, nil
            for _, transfer in ipairs(transfers) do
                if transfer.from == reactorSide and not transfer.moved then failed, last = failed + 1, transfer.time end
            end
            if failed >= recovery_attempt and now >= last + 1 then
                inventories[2].items, replenished = {}, true
            end
        end
        if scenario == "me_output_recover" and now >= 4.05 and not replenished then
            inventories[5].items[9], replenished = nil, true
        end
        if horizontal and scenario ~= "mapping_failure" then
            for side=3,4 do
                if outputs[side] > 0 and inventories[side] then
                    inventories[side].items[1].tag = tostring(now)
                end
            end
        end
        if now > (scenario == "mapping_failure" and 20 or 8) then error("SIMULATION_DONE") end
        if scenario == "stop_replace" and now > 0.5 and outputs[0] > 0 and not injected then
            injected, input = true, 0
            rods[26].damage = 100
            return "redstone_changed"
        end
        return table.remove(signals, 1)
    end,
}
if scenario == "stop_replace" or horizontal or outputCase or meOutputCase or coolantCase then input = 1 end
local ok, err = pcall(assert(load(firmware)))
return {ok=ok, err=tostring(err), transfers=transfers, writes=writes, stopped=stopped,
    supplyReads=supplyReads, shortageSince=shortageSince, time=now}
