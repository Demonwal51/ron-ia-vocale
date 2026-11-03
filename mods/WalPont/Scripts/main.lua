-- WalPont: bridge between the game and the voice AI.
-- Orders come in by appending lines "<id> <command> [args]" to pont/ordres.txt (never truncated by the mod:
-- it remembers the last id it ran). Each result is appended to pont/resultats.txt as "<id>|ok|msg" or
-- "<id>|ECHEC|msg". The game state is written to pont/etat.json every second.
-- the program's bridge folder: written in by outils/installer_ue4ss.py when it copies the mod into the game
local DIR = "__DOSSIER_PONT__"
local ORDERS = DIR .. "ordres.txt"
local RESULTS = DIR .. "resultats.txt"
local STATE = DIR .. "etat.json"

local lastId = 0
local tick = 0

local function append(path, line)
    local f = io.open(path, "a")
    if f then
        f:write(line .. "\n")
        f:close()
    end
end

local function log(msg)
    print("[WalPont] " .. msg .. "\n")
    append(DIR .. "WalPont.log", os.date("%H:%M:%S") .. " " .. msg)
end

local function valid(o)
    return o ~= nil and o:IsValid()
end

local function playerPawn()
    local pc = FindFirstOf("PlayerController")
    if not valid(pc) then return nil, nil end
    local pawn = pc.Pawn
    if not valid(pawn) then return pc, nil end
    return pc, pawn
end

local function vec(v)
    return { X = v.X, Y = v.Y, Z = v.Z }
end

local function dist(a, b)
    local dx, dy, dz = a.X - b.X, a.Y - b.Y, a.Z - b.Z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

-- cosine between the player's view and the direction to a point (1 = dead centre)
local function viewCos(pc, from, to)
    local r = pc:GetControlRotation()
    local yaw, pitch = math.rad(r.Yaw), math.rad(r.Pitch)
    local fx, fy, fz = math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)
    local dx, dy, dz = to.X - from.X, to.Y - from.Y, to.Z - from.Z
    local n = math.sqrt(dx * dx + dy * dy + dz * dz)
    if n < 1 then return 1 end
    return (fx * dx + fy * dy + fz * dz) / n
end

local function safe(fn, default)
    local ok, v = pcall(fn)
    if ok then return v end
    return default
end

local function eachOf(arr, fn)
    local ok = pcall(function() arr:ForEach(function(_, el) fn(el:get()) end) end)
    if not ok then for i = 1, #arr do fn(arr[i]) end end
end

local function nameOf(o)
    return tostring(safe(function() return o:GetClass():GetFName():ToString() end, "?"))
end

-- FindAllOf matches the exact class only, so ask the engine for every actor deriving from the class
local function allOfClass(pc, path)
    local cls = StaticFindObject(path)
    local gs = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local res = {}
    if not valid(cls) or not valid(gs) then return res end
    local outp = {}
    gs:GetAllActorsOfClass(pc, cls, outp)
    local arr = outp.OutActors or outp
    if type(arr) == "table" then
        for _, a in ipairs(arr) do
            local o = (type(a) == "userdata" and a.get) and a:get() or a
            if valid(o) then res[#res + 1] = o end
        end
    elseif arr and arr.ForEach then
        arr:ForEach(function(_, elem)
            local o = elem:get()
            if valid(o) then res[#res + 1] = o end
        end)
    end
    return res
end

local function isSwat(c)
    return safe(function() return c:IsOnSWATTeam() end, false)
end

-- every AI character (suspects and civilians), with what the voice AI needs to know
local function listAI(pc, pawn, wantSwat)
    local out = {}
    local all = {}
    for _, c in ipairs(allOfClass(pc, "/Script/ReadyOrNot.CyberneticCharacter")) do
        if isSwat(c) == (wantSwat == true) then all[#all + 1] = c end
    end
    local here = pawn and vec(pawn:K2_GetActorLocation()) or { X = 0, Y = 0, Z = 0 }
    for _, c in ipairs(all) do
        if valid(c) then
            local loc = vec(c:K2_GetActorLocation())
            local e = {
                obj = c,
                nom = c:GetFName():ToString(),
                classe = c:GetClass():GetFName():ToString(),
                suspect = safe(function() return c:IsSuspect() end, false),
                civil = safe(function() return c:IsCivilian() end, false),
                hors_combat = safe(function() return c:IsDeadOrUnconscious() end, false),
                rendu = safe(function() return c:IsSurrendered() end, false),
                arrete = safe(function() return c:IsArrested() end, false),
                obeit = safe(function() return c:IsComplying() end, false),
                otage = safe(function() return c:IsTakingHostage() end, false),
                vie = safe(function() return c:GetCurrentHealth() end, -1),
                distance_m = math.floor(dist(here, loc) / 10 + 0.5) / 10,
                vise = pc and pawn and viewCos(pc, here, loc) or 0,
                pos = loc,
                arme = safe(function() return valid(c:GetEquippedWeapon()) end, false),
                -- a threat: raising his weapon, or has already hurt the SWAT
                menace = safe(function() return c:IsRaisingWeapon() == true end, false)
                    or safe(function() return c.bHasDamagedSWATTeam == true end, false),
                stress = safe(function() return c.Stress end, -1),
            }
            table.insert(out, e)
        end
    end
    table.sort(out, function(a, b) return a.distance_m < b.distance_m end)
    return out
end

-- target words: "proche" (closest active AI), "vise" (the one closest to the crosshair), or an actor name
local function pickTarget(pc, pawn, word)
    local ais = listAI(pc, pawn)
    local best, bestScore = nil, nil
    for _, e in ipairs(ais) do
        local active = not e.hors_combat and not e.arrete
        if word == "proche" and active then
            return e
        elseif word == "vise" and active and e.vise > 0.9 and e.distance_m < 40 then
            if not bestScore or e.vise > bestScore then best, bestScore = e, e.vise end
        elseif e.nom == word then
            return e
        end
    end
    return best
end

local function jsonStr(s)
    return '"' .. tostring(s):gsub('[%c"\\]', function(ch) return string.format("\\u%04x", ch:byte()) end) .. '"'
end

-- frame-time samples (one per state write); the average over the last 5 s gives an FPS estimate
local deltas = {}
local function sampleFps(pc)
    local gs = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local d = safe(function() return gs:GetWorldDeltaSeconds(pc) end, nil)
    if type(d) == "number" and d > 0 then
        table.insert(deltas, d)
        if #deltas > 5 then table.remove(deltas, 1) end
    end
    local sum = 0
    for _, v in ipairs(deltas) do sum = sum + v end
    if sum <= 0 then return -1 end
    return #deltas / sum
end

local function difficultyName()
    local d = FindFirstOf("DifficultySubsystem")
    if not valid(d) then return "?" end
    return safe(function() return d:GetCurrentDifficulty().TagName:ToString() end, "?")
end

local function isOwnState(ps)
    local pc = FindFirstOf("PlayerController")
    if not valid(pc) or not valid(ps) then return false end
    return safe(function() return pc.PlayerState:GetFullName() == ps:GetFullName() end, false)
end

local function playerName(ps)
    return safe(function() return ps:GetPlayerName():ToString() end, nil)
        or safe(function() return ps:GetFName():ToString() end, "?")
end

-- the human players: name, me or not, where they stand and where they aim (for coop, the AI judges from the
-- speaker's own view). GetBaseAimRotation works for remote players too.
local function listPlayers(pc)
    local out = {}
    for _, ps in ipairs(allOfClass(pc, "/Script/ReadyOrNot.ReadyOrNotPlayerState")) do
        local p = safe(function() return ps.PawnPrivate end, nil)
        local e = { nom = playerName(ps), moi = isOwnState(ps) }
        if valid(p) then
            e.pos = vec(p:K2_GetActorLocation())
            e.rot = safe(function() return p:GetBaseAimRotation() end, nil)
        end
        out[#out + 1] = e
    end
    return out
end

-- in coop only the host's game decides what the AI does: a client must not try
local function isHost(pawn)
    return pawn ~= nil and safe(function() return pawn:HasAuthority() end, false) == true
end

-- ---- how the player plays: lethal or less-lethal (the AI team follows his style) ----
-- an item is less-lethal by its class or name (taser, pepperball, beanbag, less-lethal, OC spray), lethal if it is
-- any other firearm, else something else (grenade, gadget)
local MAG_WEAPON = nil
local function itemType(it)
    if not valid(it) then return "autre", "" end
    local cls = safe(function() return it:GetClass():GetFName():ToString() end, "")
    local nom = safe(function() return it.ItemName:ToString() end, "")
    local s = (cls .. " " .. nom):lower()
    for _, mot in ipairs({ "taser", "pepper", "beanbag", "bean bag", "lesslethal", "less lethal", "less_lethal",
                           "ocspray", "oc spray", "pava" }) do
        if s:find(mot, 1, true) then return "non_letal", nom ~= "" and nom or cls end
    end
    MAG_WEAPON = MAG_WEAPON or StaticFindObject("/Script/ReadyOrNot.BaseMagazineWeapon")
    if valid(MAG_WEAPON) and safe(function() return it:IsA(MAG_WEAPON) end, false) then
        return "letal", nom ~= "" and nom or cls
    end
    return "autre", nom ~= "" and nom or cls
end

local tirsJoueur = { letal = 0, non_letal = 0, autre = 0 }

-- the player's shots, counted by kind from the ammo of the item in his hands: in game (02/11) three real shots
-- (20 -> 17 rounds) were seen by neither the character's fire hook nor the weapon's, so the count is the drop of
-- his rounds between two state writes (a reload, a rise, counts nothing; another item resets the reference)
local munitionsVues = { nom = nil, n = nil }
local function compterTirs(main)
    if not valid(main) then munitionsVues.nom = nil return end
    local nom = main:GetFullName()
    local n = tonumber(safe(function() return main:GetAmmo() end, nil))
    if not n then munitionsVues.nom = nil return end
    if munitionsVues.nom == nom and munitionsVues.n and n < munitionsVues.n then
        local kind = itemType(main)
        tirsJoueur[kind] = (tirsJoueur[kind] or 0) + math.floor(munitionsVues.n - n + 0.5)
    end
    munitionsVues.nom, munitionsVues.n = nom, n
end

-- what the player carries and holds, and how many shots of each kind he fired (JSON object). The loadout is read
-- slot by slot (primary, secondary, taser, beanbag, pepperball, spray): the whole inventory list came back empty.
local SLOTS = { 1, 2, 36, 35, 37, 24 }
local function styleJoueur(pawn)
    local nl, le, vus = {}, {}, {}
    for _, cat in ipairs(SLOTS) do
        local it = safe(function() return pawn.InventoryComp:GetInventoryItemOfType(cat) end, nil)
        if valid(it) and not vus[it:GetFullName()] then
            vus[it:GetFullName()] = true
            local kind, nom = itemType(it)
            if kind == "non_letal" then nl[#nl + 1] = jsonStr(nom)
            elseif kind == "letal" then le[#le + 1] = jsonStr(nom) end
        end
    end
    local main = safe(function() return pawn:GetEquippedItem() end, nil)
    compterTirs(main)
    local enMain, nomMain = itemType(main)
    local mun = safe(function() return main:GetAmmo() end, -1)
    return string.format('{"en_main":%s,"type_en_main":"%s","munitions_en_main":%d,"non_letaux":[%s],"letaux":[%s],"tirs_letaux":%d,"tirs_non_letaux":%d}',
        jsonStr(nomMain), enMain, tonumber(mun) or -1, table.concat(nl, ","), table.concat(le, ","),
        tirsJoueur.letal, tirsJoueur.non_letal)
end

-- equipment counted in the state for the tactical AI (EItemCategory)
local EQUIP_ETAT = { flash = 30, ninebang = 31, gaz = 32, stinger = 33, optiwand = 34, beanbag = 35, taser = 36,
                     pepperball = 37, c2 = 38, cale = 40, belier = 41, fusil_breche = 42, chemlight = 28,
                     bouclier = 29, spray = 24 }

local function writeState()
    local pc, pawn = playerPawn()
    local fps = pc and sampleFps(pc) or -1
    local parts = {}
    local ais = (pc and pawn) and listAI(pc, pawn) or {}
    local swatList = (pc and pawn) and listAI(pc, pawn, true) or {}
    -- the police an AI can see: the player, the other players, the SWAT teammates (not the reinforcements)
    local police = {}
    if pawn then police[#police + 1] = { nom = "joueur", obj = pawn, pos = vec(pawn:K2_GetActorLocation()) } end
    for _, s in ipairs(swatList) do
        if not s.classe:find("Trailer") and not s.hors_combat then police[#police + 1] = { nom = s.nom, obj = s.obj, pos = s.pos } end
    end
    -- two handles on the same object are not equal in Lua: compare the names (in game, 02/11, the player came twice)
    local moiNom = pawn and safe(function() return pawn:GetFullName() end, "") or ""
    for _, ps in ipairs(pc and allOfClass(pc, "/Script/ReadyOrNot.ReadyOrNotPlayerState") or {}) do
        local p = safe(function() return ps.PawnPrivate end, nil)
        if valid(p) and safe(function() return p:GetFullName() end, "") ~= moiNom then
            police[#police + 1] = { nom = "joueur:" .. playerName(ps), obj = p, pos = vec(p:K2_GetActorLocation()) }
        end
    end
    for i, e in ipairs(ais) do
        if i > 40 then break end
        -- does the player himself see him? (the AI team knows only what he or a teammate sees)
        local vuChef = i <= 15 and e.distance_m < 40
            and safe(function() return pc:LineOfSightTo(e.obj, { X = 0, Y = 0, Z = 0 }, false) end, false) == true
        -- what the suspects' and civilians' AI needs about him (the closest 20, still active): how alert, his game
        -- archetype, his voice, what he does, his morale, whether he heard the police, which police he sees
        local adv = ""
        if i <= 20 and not e.hors_combat and not e.arrete then
            local ctrl = safe(function() return e.obj.Controller end, nil)
            local voitP = {}
            if valid(ctrl) then
                for _, p in ipairs(police) do
                    if dist(e.pos, p.pos) < 4000
                        and safe(function() return ctrl:LineOfSightTo(p.obj, { X = 0, Y = 0, Z = 0 }, false) end, false) == true then
                        voitP[#voitP + 1] = jsonStr(p.nom)
                    end
                end
            end
            local eveil = valid(ctrl) and tonumber(safe(function() return ctrl:GetAwarenessState() end, -1)) or -1
            local act = valid(ctrl) and safe(function()
                local a = ctrl.CurrentActivity
                return valid(a) and a:GetClass():GetFName():ToString() or ""
            end, "") or ""
            local arch = safe(function() return e.obj.Archetype.Name:ToString() end, "")
            local moral = valid(ctrl) and tonumber(safe(function() return ctrl.MoraleComponent:GetMorale() end, -1)) or -1
            local entendu = valid(ctrl) and safe(function() return ctrl.bHasEverHeardSwat == true end, false) or false
            local fuit = safe(function() return e.obj.bIsFleeing == true or e.obj.bIsFleeing == 1 end, false)
            local lacet = tonumber(safe(function() return e.obj:GetBaseAimRotation().Yaw end, 0)) or 0
            adv = string.format(',"eveil":%d,"activite":%s,"archetype":%s,"moral":%.2f,"entendu":%s,"fuit":%s,"lacet":%.1f,"voit_police":[%s]',
                eveil or -1, jsonStr(act or ""), jsonStr(arch or ""), moral or -1,
                tostring(entendu == true), tostring(fuit == true), lacet, table.concat(voitP, ","))
        end
        -- his voice and whether she is a woman, for everyone: a cuffed suspect is the one Wal questions (step 7)
        if i <= 30 then
            local voix = safe(function() return e.obj.SpeechCharacterName:ToString() end, "")
            local femme = safe(function() return e.obj.bFemale == true end, false)
            adv = adv .. string.format(',"voix":%s,"femme":%s', jsonStr(voix or ""), tostring(femme == true))
        end
        parts[#parts + 1] = string.format(
            '{"nom":%s,"classe":%s,"suspect":%s,"civil":%s,"hors_combat":%s,"rendu":%s,"arrete":%s,"obeit":%s,"otage":%s,"arme":%s,"menace":%s,"vu_chef":%s,"stress":%.2f,"vie":%.0f,"distance_m":%.1f,"vise":%.2f,"pos":[%.2f,%.2f,%.2f]%s}',
            jsonStr(e.nom), jsonStr(e.classe), tostring(e.suspect), tostring(e.civil), tostring(e.hors_combat),
            tostring(e.rendu), tostring(e.arrete), tostring(e.obeit), tostring(e.otage), tostring(e.arme),
            tostring(e.menace), tostring(vuChef), tonumber(e.stress) or -1,
            tonumber(e.vie) or -1, e.distance_m, e.vise, e.pos.X / 100, e.pos.Y / 100, e.pos.Z / 100, adv)
    end
    local players = {}
    for _, j in ipairs(pc and listPlayers(pc) or {}) do
        local extra = ""
        if j.pos and j.rot then
            extra = string.format(',"pos":[%.2f,%.2f,%.2f],"lacet":%.1f,"tangage":%.1f',
                j.pos.X / 100, j.pos.Y / 100, j.pos.Z / 100, j.rot.Yaw, j.rot.Pitch)
        end
        players[#players + 1] = string.format('{"nom":%s,"moi":%s%s}', jsonStr(j.nom), tostring(j.moi), extra)
    end
    local team = {}
    for _, e in ipairs(swatList) do
        -- what he is doing, and which suspects/civilians within 40 m he has in line of sight
        local ctrl = safe(function() return e.obj.Controller end, nil)
        local act = valid(ctrl) and safe(function()
            local a = ctrl.CurrentActivity
            if not valid(a) then return "" end
            -- the activity, and the step of its state machine (to see where an order waits)
            local etape = safe(function() return a:GetActiveStateName():ToString() end, "")
            return a:GetClass():GetFName():ToString() .. (etape ~= "" and (":" .. etape) or "")
        end, "") or ""
        local voit = {}
        if valid(ctrl) and not e.classe:find("Trailer") then
            for i, a in ipairs(ais) do
                if i > 12 then break end
                if not a.hors_combat and dist(e.pos, a.pos) < 4000
                    and safe(function() return ctrl:LineOfSightTo(a.obj, { X = 0, Y = 0, Z = 0 }, false) end, false) then
                    voit[#voit + 1] = jsonStr(a.nom)
                end
            end
        end
        local lacet = safe(function() return e.obj:GetBaseAimRotation().Yaw end, 0.0)
        -- his squad team (1 red, 2 blue) and what he still carries
        local equip = {}
        if not e.classe:find("Trailer") then
            for nom, cat in pairs(EQUIP_ETAT) do
                local n = safe(function() return e.obj.InventoryComp:CountInventoryItemType(cat) end, 0)
                if type(n) == "number" and n > 0 then equip[#equip + 1] = string.format('"%s":%d', nom, n) end
            end
        end
        local eqp = tonumber(safe(function() return e.obj:GetTeam() end, 0)) or 0
        -- what Wal calls him by: his squad position (0 Alpha, 1 Beta, 2 Charlie, 3 Delta) and his character's name
        local poste = tonumber(safe(function() return e.obj.SquadPosition end, -1)) or -1
        local prenom = safe(function() return e.obj.RosterCharacter.FirstName:ToString() end, "")
        local nomPerso = safe(function() return e.obj.RosterCharacter.LastName:ToString() end, "")
        team[#team + 1] = string.format('{"nom":%s,"classe":%s,"hors_combat":%s,"vie":%.0f,"distance_m":%.1f,"pos":[%.2f,%.2f,%.2f],"lacet":%.1f,"activite":%s,"voit":[%s],"equipe":%d,"poste":%d,"prenom":%s,"nom_perso":%s,"equipement":{%s}}',
            jsonStr(e.nom), jsonStr(e.classe), tostring(e.hors_combat), tonumber(e.vie) or -1, e.distance_m,
            e.pos.X / 100, e.pos.Y / 100, e.pos.Z / 100, tonumber(lacet) or 0.0, jsonStr(act), table.concat(voit, ","),
            eqp, poste, jsonStr(prenom or ""), jsonStr(nomPerso or ""), table.concat(equip, ","))
    end
    local doors = {}
    if pc and pawn then
        local here = vec(pawn:K2_GetActorLocation())
        local near = {}
        for _, d in ipairs(allOfClass(pc, "/Script/ReadyOrNot.Door")) do
            local loc = vec(d:K2_GetActorLocation())
            near[#near + 1] = { d = d, loc = loc, m = dist(here, loc) / 100 }
        end
        table.sort(near, function(a, b) return a.m < b.m end)
        for i, n in ipairs(near) do
            if i > 6 then break end
            -- a trap counts only once the SWAT knows about it (checked under the door), as for a player
            doors[#doors + 1] = string.format('{"nom":%s,"pos":[%.2f,%.2f,%.2f],"distance_m":%.1f,"ouverte":%s,"verrouillee":%s,"piege":%s,"calee":%s,"cassee":%s}',
                jsonStr(n.d:GetFName():ToString()), n.loc.X / 100, n.loc.Y / 100, n.loc.Z / 100, n.m,
                tostring(safe(function() return n.d:IsOpen() end, false) == true),
                tostring(safe(function() return n.d:IsLocked() end, false) == true),
                tostring(safe(function() return n.d:HasTrapAndSWATKnowsTrap() end, false) == true),
                tostring(safe(function() return n.d:IsJammed() end, false) == true),
                tostring(safe(function() return n.d:IsDoorBroken() end, false) == true))
        end
    end
    local world = pc and safe(function() return pc:GetWorld():GetFName():ToString() end, "?") or "?"
    -- the map's rooms (centre in metres, cleared by the SWAT or not, neighbours), from the game's own room graph
    local rooms = {}
    local gen = FindFirstOf("WorldDataGenerator")
    if valid(gen) then
        safe(function()
            gen.RoomData.Rooms:ForEach(function(_, elem)
                local r = elem:get()
                local next_to = {}
                r.ConnectingRooms:ForEach(function(_, n) next_to[#next_to + 1] = jsonStr(n:get():ToString()) end)
                rooms[#rooms + 1] = string.format('{"nom":%s,"pos":[%.2f,%.2f,%.2f],"nettoyee":%s,"voisines":[%s]}',
                    jsonStr(r.Name:ToString()), r.Location.X / 100, r.Location.Y / 100, r.Location.Z / 100,
                    tostring(r.bClearedBySwat == true), table.concat(next_to, ","))
            end)
        end)
    end
    local txt = string.format('{"t":%d,"carte":%s,"joueur":%s,"hote":%s,"fps":%.1f,"difficulte":%s,"style":%s,"ia":[%s],"swat":[%s],"joueurs":[%s],"portes":[%s],"pieces":[%s]}',
        tick, jsonStr(world), tostring(pawn ~= nil), tostring(isHost(pawn)), fps, jsonStr(difficultyName()),
        pawn and styleJoueur(pawn) or "null",
        table.concat(parts, ","), table.concat(team, ","), table.concat(players, ","), table.concat(doors, ","),
        table.concat(rooms, ","))
    local tmp = STATE .. ".tmp"
    local f = io.open(tmp, "w")
    if f then
        f:write(txt)
        f:close()
        os.remove(STATE)
        os.rename(tmp, STATE)
    end
end

local TEAMS = { rouge = 1, bleu = 2, tous = 5 }

local function swatManager()
    local m = FindFirstOf("SWATManager")
    if valid(m) then return m end
    return nil
end

local COMMANDS = {}

COMMANDS.ping = function() return "pong" end

-- spawn the mission's suspects and civilians the way the coop game mode does
COMMANDS.apparition = function(pc, pawn, args)
    local gm = FindFirstOf("CoopGM")
    if not valid(gm) then error("pas de CoopGM") end
    gm:SpawnSuspectsAndHostages()
    return gm:GetClass():GetFName():ToString()
end

-- dev spawner of the player controller: spawn_ia <data table row> <count>
COMMANDS.spawn_ia = function(pc, pawn, args)
    pc:SpawnAI(args[1] or "", tonumber(args[2] or "1") or 1)
    return (args[1] or "") .. " x" .. (args[2] or "1")
end

-- diagnostic: how many characters each lookup finds (FindAllOf = exact class, allOfClass = with subclasses)
COMMANDS.compter = function(pc, pawn, args)
    local parts = {}
    for _, n in ipairs({ "CyberneticCharacter", "SWATCharacter", "SuspectCharacter", "CivilianCharacter", "CyberneticsSwat_V2_C" }) do
        local fa = FindAllOf(n)
        parts[#parts + 1] = n .. " exact=" .. (fa and #fa or 0)
    end
    for _, p in ipairs({ "/Script/ReadyOrNot.CyberneticCharacter", "/Script/ReadyOrNot.SuspectCharacter", "/Script/ReadyOrNot.CivilianCharacter", "/Script/ReadyOrNot.SWATCharacter" }) do
        parts[#parts + 1] = p:match("%.(%w+)$") .. " derives=" .. #allOfClass(pc, p)
    end
    return table.concat(parts, ", ")
end

COMMANDS.console = function(pc, pawn, args)
    local ksl = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    ksl:ExecuteConsoleCommand(pc, table.concat(args, " "), pc)
    return "envoye"
end

-- the game's character functions (Surrender, IncreaseStress...) read his controller; a cuffed or dead character has
-- none. On the fast-food map (02/11) the game crashed reading address 0x888 (the controller's CurrentActivity) two
-- seconds after a "stress" to a civilian: refuse, as aiByName does for the adv_* commands
local function avecControleur(e)
    if not e then error("aucune cible") end
    if not valid(safe(function() return e.obj.Controller end, nil)) then
        error("sans controleur (arrete ou hors de combat) : " .. e.nom)
    end
    return e
end

COMMANDS.rendre = function(pc, pawn, args)
    local e = avecControleur(pickTarget(pc, pawn, args[1] or "vise"))
    e.obj:Surrender()
    return e.nom
end

COMMANDS.faux_rendre = function(pc, pawn, args)
    local e = avecControleur(pickTarget(pc, pawn, args[1] or "vise"))
    e.obj:FakeSurrender(false)
    return e.nom
end

-- test only: put the player 3 m from an AI and aim at it, so reactions can be tested without a human
COMMANDS.face_a = function(pc, pawn, args)
    local e = pickTarget(pc, pawn, args[1] or "proche")
    if not e then error("aucune cible") end
    local t = vec(e.obj:K2_GetActorLocation())
    local yaw = math.rad((tonumber(args[2] or "0") or 0))
    local p = { X = t.X + 300 * math.cos(yaw), Y = t.Y + 300 * math.sin(yaw), Z = t.Z + 20 }
    pawn:K2_SetActorLocation(p, false, {}, true)
    local look = math.deg(math.atan(t.Y - p.Y, t.X - p.X))
    pc:SetControlRotation({ Pitch = 0, Yaw = look, Roll = 0 })
    return e.nom
end

COMMANDS.degainer = function(pc, pawn, args)
    local e = avecControleur(pickTarget(pc, pawn, args[1] or "vise"))
    e.obj:DrawWeapon()
    return e.nom
end

COMMANDS.stress = function(pc, pawn, args)
    local e = avecControleur(pickTarget(pc, pawn, args[1] or "vise"))
    local amount = tonumber(args[2] or "0") or 0
    if amount >= 0 then e.obj:IncreaseStress(amount) else e.obj:DecreaseStress(-amount) end
    return e.nom .. " " .. amount
end

COMMANDS.replique = function(pc, pawn, args)
    local e = pickTarget(pc, pawn, args[1] or "vise")
    if not e then error("aucune cible") end
    e.obj:PlayBarkOrStartConversation(args[2] or "", false, 0.0)
    return e.nom .. " " .. (args[2] or "")
end

-- what the team could still do for the score, near the player (metres): people to report to TOC (arrested or down,
-- not reported yet), evidence to pick up (a dropped weapon, an evidence item), traps not on a door
local function aFaire(pc, pawn, maxM)
    local here, out = vec(pawn:K2_GetActorLocation()), { rapports = {}, preuves = {}, pieges = {} }
    local function loin(a)
        local p = safe(function() return vec(a:K2_GetActorLocation()) end, nil)
        if not p then return nil end
        local d = math.sqrt((p.X - here.X) ^ 2 + (p.Y - here.Y) ^ 2 + (p.Z - here.Z) ^ 2) / 100
        return d <= maxM and d or nil
    end
    for _, e in ipairs(listAI(pc, pawn)) do
        if (e.arrete or e.hors_combat) and not safe(function() return e.obj.bHasBeenReported == true end, true) then
            local d = loin(e.obj)
            if d then out.rapports[#out.rapports + 1] = { obj = e.obj, nom = e.nom, d = d } end
        end
    end
    for _, cls in ipairs({ "EvidenceActor", "BaseWeapon" }) do
        for _, a in ipairs(allOfClass(pc, "/Script/ReadyOrNot." .. cls)) do
            if valid(a) and not valid(safe(function() return a:GetOwner() end, nil)) then
                local c = safe(function() return a.EvidenceComponent end, nil)
                if valid(c) and safe(function() return c:CanBeCollected() end, false) == true then
                    local d = loin(a)
                    if d then out.preuves[#out.preuves + 1] = { obj = a, nom = nameOf(a), d = d } end
                end
            end
        end
    end
    for _, t in ipairs(allOfClass(pc, "/Script/ReadyOrNot.TrapActor")) do
        if not valid(safe(function() return t.AttachedToDoor end, nil)) then
            local d = loin(t)
            if d then
                out.pieges[#out.pieges + 1] = { obj = t, nom = nameOf(t), d = d,
                    etat = tostring(safe(function() return t.TrapStatus end, "?")) }
            end
        end
    end
    for _, l in pairs(out) do table.sort(l, function(a, b) return a.d < b.d end) end
    return out
end

COMMANDS.sonde_rapports = function(pc, pawn, args)
    local o, parts = aFaire(pc, pawn, tonumber(args[1]) or 200), {}
    for k, l in pairs(o) do
        local s = {}
        for i = 1, math.min(#l, 5) do s[#s + 1] = string.format("%s %.0fm%s", l[i].nom, l[i].d, l[i].etat and (" etat " .. l[i].etat) or "") end
        parts[#parts + 1] = string.format("%s %d [%s]", k, #l, table.concat(s, ", "))
    end
    return table.concat(parts, " ; ")
end

-- the game's own evidence count (the score screen's: collected / total), and each evidence item left near the player
-- with its state (0 unclaimed, 1 collected, 2 extraction, 3 dropped), to prove a pick-up by the game itself
COMMANDS.sonde_preuves = function(pc, pawn, args)
    local sm, compte = FindFirstOf("ScoringManager"), "?"
    if valid(sm) then
        local a, b = {}, {}
        local ok, err = pcall(function() sm:GetEvidenceCount(a, b) end)
        compte = ok and string.format("%s/%s", tostring(a.EvidenceCollected), tostring(b.TotalEvidence)) or tostring(err)
    end
    local here, l = vec(pawn:K2_GetActorLocation()), {}
    for _, cls in ipairs({ "EvidenceActor", "BaseWeapon" }) do
        for _, a in ipairs(allOfClass(pc, "/Script/ReadyOrNot." .. cls)) do
            local c = safe(function() return a.EvidenceComponent end, nil)
            if valid(c) and not valid(safe(function() return a:GetOwner() end, nil)) then
                local p = safe(function() return vec(a:K2_GetActorLocation()) end, nil)
                local d = p and math.sqrt((p.X - here.X) ^ 2 + (p.Y - here.Y) ^ 2 + (p.Z - here.Z) ^ 2) / 100 or -1
                if d >= 0 and d <= (tonumber(args[1]) or 30) then
                    l[#l + 1] = string.format("%s %.0fm etat %s%s%s", nameOf(a), d,
                        tostring(safe(function() return c.EvidenceState end, "?")),
                        safe(function() return c:CanBeCollected() end, false) == true and " libre" or "",
                        safe(function() return c.bIsBeingCollected end, false) == true and " en_cours" or "")
                end
            end
        end
    end
    return string.format("compte %s ; objets %d [%s]", compte, #l, table.concat(l, ", "))
end

-- the team's own scoring jobs: report the closest person to TOC, pick up the closest evidence, disarm the closest
-- standalone trap (args: team, max metres)
COMMANDS.swat_tache = function(pc, pawn, args)
    local m = swatManager()
    if not m then error("pas de SWATManager (pas en mission ?)") end
    -- evidence: the team command did nothing (03/11, 0 out of 3), so the teammate's own activity or the game's
    -- search and secure (args[4], default "activite"; each picked the item up once out of two)
    if args[1] == "preuves" then
        return "preuves " .. COMMANDS.swat_ramasser(pc, pawn, { args[4] or "activite", args[2], args[3], args[5] })
    end
    local quoi, team, o = args[1], TEAMS[args[2] or "tous"] or 5, aFaire(pc, pawn, tonumber(args[3]) or 20)
    local l = ({ rapporter = o.rapports, pieges = o.pieges })[quoi]
    if not l then error("tache inconnue : " .. tostring(quoi)) end
    if #l == 0 then error("rien a faire (" .. quoi .. ")") end
    local t = l[1]
    if quoi == "rapporter" then m:GiveReportTargetCommand(t.obj, team)
    else m:GiveDisarmStandaloneTrapCommand(t.obj, team) end
    return string.format("%s : %s a %.0f m", quoi, t.nom, t.d)
end

-- whether a shot is clear from a to b (03/11: the team shot at a suspect behind the gas station's bulletproof glass;
-- LineOfSightTo sees through glass). The game's own checks, called with plain actors only (07:44 a LineTraceSingle
-- called through UE4SS with an actor array and a hit result crashed the game inside UE4SS.dll)
local function vuesDuJeu(pc, a, b)
    local lib = StaticFindObject("/Script/ReadyOrNot.Default__BpGameplayHelperLib")
    local r = {}
    r.los = safe(function() return pc:LineOfSightTo(b, { X = 0, Y = 0, Z = 0 }, false) end, "?")
    r.ctrl = safe(function() return a.Controller:LineOfSightTo(b, { X = 0, Y = 0, Z = 0 }, false) end, "?")
    r.perso = safe(function() return a:HasLineOfSightToCharacter(b) end, "?")
    r.lib = valid(lib) and safe(function() return lib:HasLineOfSight(a, b) end, "?") or "pas de lib"
    r.lieu = safe(function() return a:HasLineOfSightTo(vec(b:K2_GetActorLocation())) end, "?")
    return r
end

-- the window panes of the map (their centres, in metres), for the line-of-fire guard. 03/11: a line trace
-- (KismetSystemLibrary.LineTraceSingle) called through UE4SS crashed the game twice inside UE4SS.dll, at
-- the same address (07:44 with an actor array, 07:52 after 6 calls without): never call it
-- 03/11 11:48 and 11:49: walking every StaticMeshActor for its material and physical material (to find the
-- bulletproof till glass, a static mesh) crashed the game twice as well, GetActorBounds with its out vectors among
-- the calls: not done; the bulletproof glass is left to the "3 s of fire with no effect" guard (ia_vocale.tir_sans_effet)
COMMANDS.sonde_vitres = function(pc, pawn, args)
    local parts = {}
    for _, cls in ipairs({ "BreakableGlass", "GlassActor" }) do
        for _, g in ipairs(allOfClass(pc, "/Script/ReadyOrNot." .. cls)) do
            local p = vec(g:K2_GetActorLocation())
            parts[#parts + 1] = string.format("%s (%.1f, %.1f, %.1f)", nameOf(g), p.X / 100, p.Y / 100, p.Z / 100)
        end
    end
    return #parts .. " vitres : " .. table.concat(parts, ", ")
end

COMMANDS.sonde_tir = function(pc, pawn, args)
    -- args: shooter name ("joueur" = the player), target name
    local tous = {}
    for _, e in ipairs(listAI(pc, pawn)) do tous[e.nom] = e.obj end
    for _, e in ipairs(listAI(pc, pawn, true)) do tous[e.nom] = e.obj end
    local a = args[1] == "joueur" and pawn or tous[args[1] or ""]
    local b = tous[args[2] or ""]
    if not valid(a) or not valid(b) then error("tireur ou cible introuvable") end
    local r = vuesDuJeu(pc, a, b)
    return string.format("vue_joueur %s ctrl %s perso %s lib %s lieu %s a %.1f m", tostring(r.los), tostring(r.ctrl),
        tostring(r.perso), tostring(r.lib), tostring(r.lieu),
        dist(vec(a:K2_GetActorLocation()), vec(b:K2_GetActorLocation())) / 100)
end

-- each teammate's activity queue (the game's own text), to see whether an order queued behind another job
COMMANDS.sonde_swat_file = function(pc, pawn, args)
    local parts = {}
    for _, s in ipairs(listAI(pc, pawn, true)) do
        if not s.classe:find("Trailer") then
            local ctrl = safe(function() return s.obj.Controller end, nil)
            parts[#parts + 1] = valid(ctrl) and tostring(safe(function() return ctrl:GetActivityQueueAsString():ToString() end, "?"))
                or "pas de controleur"
        end
    end
    return table.concat(parts, " | ")
end

-- the class name of a controller's current activity ("" when none)
local function activiteDe(ctrl)
    local a = valid(ctrl) and safe(function() return ctrl.CurrentActivity end, nil) or nil
    return valid(a) and safe(function() return a:GetClass():GetFName():ToString() end, "") or ""
end

-- true when a controller's current activity is an arrest whose target is gone, dead, or already cuffed (an
-- unconscious suspect is still to be cuffed: his arrest stays)
local function arrestationMorte(ctrl)
    if activiteDe(ctrl) ~= "ArrestTargetActivity" then return false end
    local cible = safe(function() return ctrl.CurrentActivity.ArrestTarget end, nil)
    return not valid(cible) or safe(function() return cible:IsDeadNotUnconscious() end, false) == true
        or safe(function() return cible:IsArrested() end, false) == true
end

-- the closest evidence picked up four ways (args: route, team, max metres): "ordre" the team command
-- (GiveCollectEvidenceCommand), "fouille" the game's search and secure where the item lies, "activite" the closest
-- free teammate given his own CollectEvidenceActivity aimed at the item, "individuelle" the same teammate given the
-- manager's own search and secure order for him alone (03/11: a raw activity was taken back by the team's fall-in)
COMMANDS.swat_ramasser = function(pc, pawn, args)
    local m = swatManager()
    if not m then error("pas de SWATManager (pas en mission ?)") end
    local route, team, o = args[1], TEAMS[args[2] or "tous"] or 5, aFaire(pc, pawn, tonumber(args[3]) or 20)
    -- args[4]: which item, closest first, wrapping (03/11: the map's own Glock was never picked up, and the closest
    -- item every time kept the others waiting)
    local t = #o.preuves > 0 and o.preuves[((tonumber(args[4]) or 1) - 1) % #o.preuves + 1] or nil
    if not t then error("rien a faire (preuves)") end
    -- 03/11 in game: the teammate given the pick-up sat 100 s in an ArrestTargetActivity ("Move To") aimed at a
    -- suspect already dead, and nothing could take its place: such an arrest is finished first
    local liberes = 0
    for _, s in ipairs(listAI(pc, pawn, true)) do
        if not s.classe:find("Trailer") and arrestationMorte(s.obj.Controller) then
            safe(function() s.obj.Controller:FinishActivity(s.obj.Controller.CurrentActivity, false, true) end, nil)
            liberes = liberes + 1
        end
    end
    if route == "ordre" then
        m:GiveCollectEvidenceCommand(t.obj, team)
    elseif route == "fouille" then
        m:GiveSearchAndSecureCommand(team, vec(t.obj:K2_GetActorLocation()), true)
    elseif route == "activite" or route == "individuelle" then
        local p, best, bd = vec(t.obj:K2_GetActorLocation()), nil, 1e12
        for _, s in ipairs(listAI(pc, pawn, true)) do
            -- not one busy cuffing someone alive (his arrest outranks the pick-up)
            if not s.classe:find("Trailer") and not s.hors_combat and activiteDe(s.obj.Controller) ~= "ArrestTargetActivity" then
                local d = dist(s.pos, p)
                if d < bd then best, bd = s, d end
            end
        end
        if not best then error("aucun equipier libre") end
        local ctrl = best.obj.Controller
        if not valid(ctrl) then error("pas de controleur") end
        if route == "individuelle" then
            m:GiveSearchAndSecureCommand_Individual(best.obj, p, false)
            return string.format("individuelle : %s vers %s a %.0f m (arrestations mortes terminees %d, il fait %s)", best.nom,
                t.nom, bd / 100, liberes, activiteDe(ctrl))
        end
        local act = ctrl:GetCollectEvidenceActivity()
        if not valid(act) then error("pas de CollectEvidenceActivity") end
        act.EvidenceItem = t.obj
        ctrl:AddActivity(act, true)
        return string.format("activite : %s vers %s a %.0f m (arrestations mortes terminees %d, il fait %s)", best.nom, t.nom,
            bd / 100, liberes, activiteDe(ctrl))
    else
        error("route inconnue : " .. tostring(route))
    end
    return string.format("%s : %s a %.0f m (arrestations mortes terminees %d)", route, t.nom, t.d, liberes)
end

COMMANDS.swat = function(pc, pawn, args)
    local m = swatManager()
    if not m then error("pas de SWATManager (pas en mission ?)") end
    local what, team = args[1], TEAMS[args[2] or "tous"] or 5
    if what == "suivre" then
        m:GiveFallInCommand(team, 0)
    elseif what == "tenir" then
        m:GiveHoldCommand(team)
    elseif what == "reprendre" then
        m:RemoveHoldCommand(team)
    elseif what == "couvrir" or what == "aller" or what == "fouiller" then
        -- the point the player looks at, 5 m ahead on the view line (good enough for a first test)
        local here = vec(pawn:K2_GetActorLocation())
        local r = pc:GetControlRotation()
        local yaw = math.rad(r.Yaw)
        local p = { X = here.X + 500 * math.cos(yaw), Y = here.Y + 500 * math.sin(yaw), Z = here.Z }
        if what == "couvrir" then m:GiveCoverAreaCommand(team, p)
        elseif what == "aller" then m:GiveMoveCommand(team, p)
        else m:GiveSearchAndSecureCommand(team, p, true) end
    elseif what == "menotter" then
        local e = pickTarget(pc, pawn, args[3] or "vise")
        if not e then error("aucune cible") end
        m:GiveRestrainCommand(e.obj, team, vec(e.obj:K2_GetActorLocation()))
    elseif what == "voix" then
        m:PlaySwatCommandVoiceLine(args[3] or "", "", true)
    else
        error("ordre swat inconnu : " .. tostring(what))
    end
    return what .. " " .. (args[2] or "tous")
end

-- ---- one SWAT teammate at a time (the tactical AI drives each one) ----

local function swatByName(pc, pawn, nom)
    for _, e in ipairs(listAI(pc, pawn, true)) do
        if e.nom == nom then return e end
    end
    error("equipier inconnu : " .. tostring(nom))
end

-- one teammate's activity now (03/11: why a pick-up does not happen): his current activity and its step, his own
-- CollectEvidenceActivity (status, time, item, abort flags) and his queue
COMMANDS.sonde_activite = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local ctrl = e.obj.Controller
    if not valid(ctrl) then error("pas de controleur") end
    local function decrit(a)
        if not valid(a) then return "aucune" end
        local etape = safe(function() return a:GetActiveStateName():ToString() end, "")
        return a:GetClass():GetFName():ToString() .. (etape ~= "" and (":" .. etape) or "")
    end
    local c = safe(function() return ctrl.CollectEvidenceActivity end, nil)
    local col = "aucune"
    if valid(c) then
        col = string.format("statut %s temps %.1f fini %s objet %s abandonSiEnnemi %s abandonSiImmobile %s max %.0f peut %s",
            tostring(safe(function() return c.ActivityStatus end, "?")),
            tonumber(safe(function() return c.ElapsedActivityTime end, -1)) or -1,
            tostring(safe(function() return c:IsActivityComplete() end, "?")),
            valid(safe(function() return c.EvidenceItem end, nil)) and nameOf(c.EvidenceItem) or "aucun",
            tostring(safe(function() return c.bAbortIfTrackingEnemy end, "?")),
            tostring(safe(function() return c.bAbortIfNotMovingForAWhile end, "?")),
            tonumber(safe(function() return c.MaxActivityTime end, -1)) or -1,
            tostring(safe(function() return c:CanCollectEvidence() end, "?")))
    end
    local cur = safe(function() return ctrl.CurrentActivity end, nil)
    local arr = ""
    local cible = valid(cur) and safe(function() return cur.ArrestTarget end, nil) or nil
    if valid(cible) then
        arr = string.format(" (arrete %s mort %s inconscient %s menotte %s)", nameOf(cible),
            tostring(safe(function() return cible:IsDeadNotUnconscious() end, "?")),
            tostring(safe(function() return cible:IsUnconsciousNotDead() end, "?")),
            tostring(safe(function() return cible:IsArrested() end, "?")))
    end
    -- what may pause it: the enemy he tracks, a scripted look, a combat move; and whether he gets closer to the item
    local ca = safe(function() return ctrl.CombatActivity end, nil)
    local combat = "pas de combat"
    if valid(ca) then
        local suivi = safe(function() return ca.LastTrackedEnemy end, nil)
        combat = string.format("suit %s regard %.1f s mouvement %s",
            valid(suivi) and nameOf(suivi) or "personne",
            tonumber(safe(function() return ca.CurrentScriptedLookAt.TimeRemaining end, -1)) or -1,
            decrit(safe(function() return ca.CombatMoveActivity end, nil)))
    end
    local objet = valid(c) and safe(function() return c.EvidenceItem end, nil) or nil
    local d = valid(objet) and dist(vec(e.obj:K2_GetActorLocation()), vec(objet:K2_GetActorLocation())) / 100 or -1
    return string.format("courante %s%s ; collecte %s ; a %.1f m de l'objet ; %s ; file %s", decrit(cur), arr,
        col, d, combat, tostring(safe(function() return ctrl:GetActivityQueueAsString():ToString() end, "?")))
end

local function numbers(args, from)
    local x, y, z = tonumber(args[from]), tonumber(args[from + 1]), tonumber(args[from + 2])
    if not (x and y and z) then error("position attendue : x y z (en metres)") end
    return { X = x * 100, Y = y * 100, Z = z * 100 }
end

-- probe: what the tactical AI can read and drive, written to the log
COMMANDS.sonde_tactique = function(pc, pawn, args)
    log("difficulte : " .. difficultyName())
    for _, e in ipairs(listAI(pc, pawn, true)) do
        local ctrl = safe(function() return e.obj.Controller end, nil)
        local queue = ctrl and safe(function() return ctrl:GetActivityQueueAsString():ToString() end, "?") or "?"
        local combat = ctrl and safe(function() return valid(ctrl.CombatActivity) end, false)
        local cible = ctrl and safe(function()
            local t = ctrl:GetTrackedTarget()
            return valid(t) and t:GetFName():ToString() or "aucune"
        end, "?") or "?"
        local aware = ctrl and safe(function() return tostring(ctrl:GetAwarenessState()) end, "?") or "?"
        log(string.format("equipier %s classe %s ctrl %s a %.1f m : activites [%s] combat %s cible %s eveil %s",
            e.nom, e.classe, ctrl and ctrl:GetClass():GetFName():ToString() or "aucun", e.distance_m, queue,
            tostring(combat), cible, aware))
    end
    local gen = FindFirstOf("WorldDataGenerator")
    if valid(gen) then
        local n = 0
        safe(function()
            gen.RoomData.Rooms:ForEach(function(_, elem)
                n = n + 1
                if n <= 8 then
                    local r = elem:get()
                    log(string.format("piece %s a (%d,%d,%d) nettoyee %s voisines %d", r.Name:ToString(),
                        r.Location.X, r.Location.Y, r.Location.Z, tostring(r.bClearedBySwat), r.ConnectingRooms:GetArrayNum()))
                end
            end)
        end)
        log("pieces : " .. n)
    else
        log("pas de WorldDataGenerator")
    end
    local here = vec(pawn:K2_GetActorLocation())
    local doors = allOfClass(pc, "/Script/ReadyOrNot.Door")
    log("portes : " .. #doors)
    for i, d in ipairs(doors) do
        if i > 6 then break end
        local loc = vec(d:K2_GetActorLocation())
        log(string.format("porte %s a %.1f m ouverte %s verrouillee %s cassee %s", d:GetFName():ToString(),
            dist(here, loc) / 100, tostring(safe(function() return d:IsOpen() end, "?")),
            tostring(safe(function() return d:IsLocked() end, "?")), tostring(safe(function() return d:IsDoorBroken() end, "?"))))
    end
    return "sonde ecrite dans le journal"
end

-- move one teammate to a point (metres, world coordinates), or N metres ahead of the player with "devant N"
COMMANDS.membre_aller = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local p
    if args[2] == "chef" then   -- back next to the player, 1.5 m behind him
        local here, yaw = vec(pawn:K2_GetActorLocation()), math.rad(pc:GetControlRotation().Yaw)
        p = { X = here.X - 150 * math.cos(yaw), Y = here.Y - 150 * math.sin(yaw), Z = here.Z }
    elseif args[2] == "devant" then
        local here, yaw = vec(pawn:K2_GetActorLocation()), math.rad(pc:GetControlRotation().Yaw)
        local d = (tonumber(args[3]) or 5) * 100
        p = { X = here.X + d * math.cos(yaw), Y = here.Y + d * math.sin(yaw), Z = here.Z }
    else
        p = numbers(args, 2)
    end
    e.obj.Controller:RequestMoveAsync(p, true, 50.0)
    return string.format("%s va a (%.1f, %.1f, %.1f)", e.nom, p.X / 100, p.Y / 100, p.Z / 100)
end

-- one teammate looks at, or fires at, an AI for N seconds (accuracy penalty: 1 = normal, higher = worse)
local function combatOf(e)
    local ca = safe(function() return e.obj.Controller.CombatActivity end, nil)
    if not valid(ca) then ca = safe(function() return e.obj.Controller:GetCombatActivity() end, nil) end
    if not valid(ca) then error("pas d'activite de combat pour " .. e.nom) end
    return ca
end

COMMANDS.membre_regarder = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local t = pickTarget(pc, pawn, args[2] or "proche")
    if not t then error("aucune cible") end
    local ca = combatOf(e)
    -- a look is also "hold fire": a burst we ordered a moment ago (through a window, Wal 03/11) stops now
    safe(function() ca:StopScriptedFire() end, nil)
    ca:ScriptedLookAtActor(t.obj, tonumber(args[3]) or 3.0)
    return e.nom .. " regarde " .. t.nom
end

COMMANDS.membre_tirer = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local t = pickTarget(pc, pawn, args[2] or "proche")
    if not t then error("aucune cible") end
    combatOf(e):ScriptedFireAtActor(t.obj, tonumber(args[3]) or 2.0, true, tonumber(args[4]) or 1.0, false)
    local vue = safe(function() return e.obj.Controller:LineOfSightTo(t.obj, { X = 0, Y = 0, Z = 0 }, false) end, "?")
    return e.nom .. " tire sur " .. t.nom .. " (vue " .. tostring(vue) .. ")"
end

-- fire at a point instead of an AI: "loin" = 3 m from him, straight away from the player (test without a target)
COMMANDS.membre_tirer_point = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local me, him = vec(pawn:K2_GetActorLocation()), vec(e.obj:K2_GetActorLocation())
    local dx, dy = him.X - me.X, him.Y - me.Y
    local n = math.max(math.sqrt(dx * dx + dy * dy), 1)
    local p = { X = him.X + 300 * dx / n, Y = him.Y + 300 * dy / n, Z = him.Z - 80 }
    combatOf(e):ScriptedFireAtLocation(p, tonumber(args[2]) or 1.0, true, 1.0, false)
    return e.nom .. " tire au sol, loin du joueur"
end

-- fire at an AI's chest as a point (no target tracking)
COMMANDS.membre_tirer_vers = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local t = pickTarget(pc, pawn, args[2] or "proche")
    if not t then error("aucune cible") end
    local p = vec(t.obj:K2_GetActorLocation())
    p.Z = p.Z + 30
    combatOf(e):ScriptedFireAtLocation(p, tonumber(args[3]) or 2.0, true, tonumber(args[4]) or 1.0, false)
    local vue = safe(function() return e.obj.Controller:LineOfSightTo(t.obj, { X = 0, Y = 0, Z = 0 }, false) end, "?")
    return e.nom .. " tire vers " .. t.nom .. " (vue " .. tostring(vue) .. ")"
end

COMMANDS.membre_munitions = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local w = e.obj:GetEquippedWeapon()
    if not valid(w) then error("pas d'arme en main") end
    return tostring(w:GetAmmo())
end

-- one teammate stops where he is (his own hold activity when the game gave him one, else just stop walking)
COMMANDS.membre_tenir = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local ctrl = e.obj.Controller
    local hold = safe(function() return ctrl.HoldActivity end, nil)
    if valid(hold) then
        ctrl:AddActivity(hold, true)
        return e.nom .. " tient (activite de maintien)"
    end
    ctrl:AbortMove(false)
    return e.nom .. " s'arrete"
end

COMMANDS.membre_regarder_porte = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    for _, d in ipairs(allOfClass(pc, "/Script/ReadyOrNot.Door")) do
        if d:GetFName():ToString() == args[2] then
            combatOf(e):ScriptedLookAtLocation(vec(d:K2_GetActorLocation()), tonumber(args[3]) or 5.0)
            return e.nom .. " surveille " .. args[2]
        end
    end
    error("porte inconnue : " .. tostring(args[2]))
end

-- cover: walk to a spot (x y z) and watch a point (x y z), all in metres. The look is held for 8 s; the tactical
-- AI renews it while the cover order lasts.
COMMANDS.membre_couvrir = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local place, regard = numbers(args, 2), numbers(args, 5)
    e.obj.Controller:RequestMoveAsync(place, true, 50.0)
    combatOf(e):ScriptedLookAtLocation(regard, 8.0)
    return string.format("%s couvre depuis (%.1f, %.1f)", e.nom, place.X / 100, place.Y / 100)
end

-- suppressive fire: shoot at a point (x y z, metres) for N seconds with a wide spread (accuracy penalty multiplier),
-- even without seeing anyone there: it pins a suspect down while a teammate moves
COMMANDS.membre_suppression = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    combatOf(e):ScriptedFireAtLocation(numbers(args, 2), tonumber(args[5]) or 2.5, true, tonumber(args[6]) or 2.5, false)
    return e.nom .. " tir de couverture"
end

COMMANDS.membre_regarder_point = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    combatOf(e):ScriptedLookAtLocation(numbers(args, 2), tonumber(args[5]) or 5.0)
    return e.nom .. " surveille un point"
end

-- ---- the suspects' and civilians' AI (step 6): one character at a time, by actor name ----

-- by default he must still have his AI controller: an arrested (or dying) character has none (in game, 02/11,
-- "UObject instance is nullptr" on moves sent to a suspect being cuffed)
local function aiByName(pc, pawn, nom, sansControleur)
    for _, e in ipairs(listAI(pc, pawn)) do
        if e.nom == nom then
            if not sansControleur and not valid(safe(function() return e.obj.Controller end, nil)) then
                error("sans controleur (arrete ou hors de combat) : " .. nom)
            end
            return e
        end
    end
    error("personnage inconnu : " .. tostring(nom))
end

-- any character by name: "joueur" (the player), a SWAT teammate, a suspect or a civilian
local function anyByName(pc, pawn, nom)
    if nom == "joueur" then return { nom = "joueur", obj = pawn } end
    for _, e in ipairs(listAI(pc, pawn, true)) do if e.nom == nom then return e end end
    for _, e in ipairs(listAI(pc, pawn)) do if e.nom == nom then return e end end
    error("personnage inconnu : " .. tostring(nom))
end

-- an activity of the given class for this controller: the one he already holds, else a new one
local function activityFor(ctrl, name)
    local cls = StaticFindObject("/Script/ReadyOrNot." .. name)
    if not valid(cls) then error("classe introuvable : " .. name) end
    local a = safe(function() return ctrl:GetActivity(cls) end, nil)
    if valid(a) then return a, "existante" end
    local ok, b = pcall(function() return StaticConstructObject(cls, ctrl) end)
    if not ok or not valid(b) then error("impossible de creer " .. name .. " : " .. tostring(b)) end
    return b, "creee"
end

COMMANDS.adv_aller = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local p = numbers(args, 2)
    e.obj.Controller:RequestMoveAsync(p, true, 50.0)
    return string.format("%s va a (%.1f, %.1f, %.1f)", e.nom, p.X / 100, p.Y / 100, p.Z / 100)
end

-- probe: three ways to make him walk (the game's async request, the engine's MoveToLocation, the simple move)
COMMANDS.adv_bouger = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local facon, p, ctrl = args[2] or "moteur", numbers(args, 3), e.obj.Controller
    if facon == "async" then ctrl:RequestMoveAsync(p, true, 50.0)
    elseif facon == "moteur" then ctrl:MoveToLocation(p, 50.0, false, true, true, false, nil, true)
    elseif facon == "simple" then
        StaticFindObject("/Script/AIModule.Default__AIBlueprintHelperLibrary"):SimpleMoveToLocation(ctrl, p)
    else error("facon inconnue : " .. facon) end
    return string.format("%s bouge (%s), statut %s", e.nom, facon, tostring(safe(function() return ctrl:GetMoveStatus() end, "?")))
end

-- probe: what may hold him in place
COMMANDS.adv_info = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1], true)
    local c, ctrl = e.obj, e.obj.Controller
    local mv = safe(function() return c.CharacterMovement end, nil)
    return string.format("roam %s vitesse %s mode %s entree_ignoree %s statut %s activites [%s]",
        tostring(safe(function() return c.bCanRoam end, "?")),
        tostring(valid(mv) and safe(function() return mv.MaxWalkSpeed end, "?") or "?"),
        tostring(valid(mv) and safe(function() return mv.MovementMode end, "?") or "?"),
        tostring(safe(function() return ctrl:IsMoveInputIgnored() end, "?")),
        tostring(safe(function() return ctrl:GetMoveStatus() end, "?")),
        tostring(safe(function() return ctrl:GetActivityQueueAsString():ToString() end, "?")))
end

COMMANDS.adv_tenir = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    e.obj.Controller:AbortMove(false)
    return e.nom .. " s'arrete"
end

COMMANDS.adv_regarder = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    combatOf(e):ScriptedLookAtLocation(numbers(args, 2), tonumber(args[5]) or 5.0)
    return e.nom .. " surveille un point"
end

-- fire at the police: only with a line of sight (the game's scripted fire shoots through walls)
COMMANDS.adv_tirer = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local t = anyByName(pc, pawn, args[2] or "joueur")
    local vue = safe(function() return e.obj.Controller:LineOfSightTo(t.obj, { X = 0, Y = 0, Z = 0 }, false) end, false)
    if vue ~= true then error(e.nom .. " ne voit pas " .. t.nom) end
    combatOf(e):ScriptedFireAtActor(t.obj, tonumber(args[3]) or 1.5, true, tonumber(args[4]) or 1.0, false)
    return e.nom .. " tire sur " .. t.nom
end

-- run for an exit, the game's own way (his controller's move-to-exit activity)
COMMANDS.adv_fuir = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local ctrl = e.obj.Controller
    local a = safe(function() return ctrl:GetMoveToExitActivity() end, nil)
    if not valid(a) then a = safe(function() return ctrl.MoveToExitActivity end, nil) end
    if not valid(a) then error("pas d'activite de fuite pour " .. e.nom) end
    ctrl:AddActivity(a, true)
    return e.nom .. " fuit vers une sortie"
end

-- take cover from a threat: a police character by name (default the player); the activity needs to know whom from
COMMANDS.adv_couvert = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local t = anyByName(pc, pawn, args[2] or "joueur")
    local a, d = activityFor(e.obj.Controller, "TakeCoverActivity")
    local loc = vec(t.obj:K2_GetActorLocation())
    local ok, err = pcall(function()
        a.InstigatorStimulus = { InstigatorCharacter = t.obj, ThreatTransform = {
            Rotation = { X = 0, Y = 0, Z = 0, W = 1 }, Translation = loc, Scale3D = { X = 1, Y = 1, Z = 1 } } }
    end)
    e.obj.Controller:AddActivity(a, true)
    return e.nom .. " se met a couvert de " .. t.nom .. " (" .. d .. (ok and "" or ", menace non posee : " .. tostring(err)) .. ")"
end

-- go and look at a point, the game's own way (as if he had heard something there): his controller's
-- investigate-stimulus activity with the stimulus moved to the point (metres)
COMMANDS.adv_enqueter = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local p, ctrl = numbers(args, 2), e.obj.Controller
    local a = safe(function() return ctrl.InvestigateStimulusActivity end, nil)
    local d = "existante"
    if not valid(a) then a, d = activityFor(ctrl, "InvestigateStimulusActivity") end
    local ok, err = pcall(function()
        a.Stimulus = { Age = 0.0, ExpirationAge = 30.0, Strength = 1.0, StimulusLocation = p,
                       ReceiverLocation = vec(e.obj:K2_GetActorLocation()), Tag = FName("Noise"), bSuccessfullySensed = true }
        a.Instigator = pawn
    end)
    if not ok then error("stimulus non pose : " .. tostring(err)) end
    ctrl:AddActivity(a, true)
    return string.format("%s va voir (%.1f, %.1f) (%s)", e.nom, p.X / 100, p.Y / 100, d)
end

-- join an ally (the game's rally activity: he walks to him and finds a line of sight from there)
COMMANDS.adv_rallier = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local allie = aiByName(pc, pawn, args[2])
    local a, d = activityFor(e.obj.Controller, "RallyToActivity")
    a.Target = allie.obj
    e.obj.Controller:AddActivity(a, true)
    return e.nom .. " rejoint " .. allie.nom .. " (" .. d .. ")"
end

COMMANDS.adv_otage = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local c = aiByName(pc, pawn, args[2])
    if not c.civil then error(c.nom .. " n'est pas un civil") end
    local a, d = activityFor(e.obj.Controller, "TakeHostageActivity")
    a.Hostage = c.obj
    e.obj.Controller:AddActivity(a, true)
    return e.nom .. " prend " .. c.nom .. " en otage (" .. d .. ")"
end

-- say a line of his own voice; the answer gives the file the game finds for it ("" = no such line for him)
COMMANDS.adv_parler = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1], true)
    local voix = safe(function() return e.obj.SpeechCharacterName:ToString() end, "?")
    local chemin = safe(function()
        local r = StaticFindObject("/Script/ReadyOrNot.Default__BpGameplayHelperLib"):GetDialoguePath(voix, args[2] or "")
        return type(r) == "string" and r or r:ToString()
    end, "?")
    e.obj:PlayRawVO(args[2] or "", "", false)
    return e.nom .. " (" .. voix .. ") dit " .. (args[2] or "") .. " fichier " .. tostring(chemin)
end

-- morale: a negative change lowers it (pressure), a positive one raises it
COMMANDS.adv_moral = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local v = tonumber(args[2]) or 0
    local m = e.obj.Controller.MoraleComponent
    if not valid(m) then error("pas de moral pour " .. e.nom) end
    if v < 0 then m:LowerMoraleOnCharacter(e.obj, -v, FName("WalPont")) else m:IncreaseMoraleOnCharacter(e.obj, v, FName("WalPont")) end
    return string.format("%s moral %.2f", e.nom, tonumber(safe(function() return m:GetMorale() end, -1)) or -1)
end

-- awareness: 0 unalerted, 1 suspicious, 2 alerted (an ally's shout puts him on alert)
COMMANDS.adv_eveil = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    e.obj.Controller:SetAwarenessState(tonumber(args[2]) or 2)
    return e.nom .. " eveil " .. tostring(safe(function() return e.obj.Controller:GetAwarenessState() end, "?"))
end

-- probe: what the suspects' and civilians' AI can read and drive, written to pont/adverses.txt
COMMANDS.adv_sonde = function(pc, pawn, args)
    local f = io.open(DIR .. "adverses.txt", "w")
    if not f then error("fichier adverses.txt") end
    local n = 0
    for _, e in ipairs(listAI(pc, pawn)) do
        n = n + 1
        if n > 20 then break end
        local ctrl = safe(function() return e.obj.Controller end, nil)
        local function has(name)
            local cls = StaticFindObject("/Script/ReadyOrNot." .. name)
            return valid(ctrl) and valid(cls) and valid(safe(function() return ctrl:GetActivity(cls) end, nil))
        end
        f:write(string.format("== %s classe %s suspect %s civil %s a %.1f m hors_combat %s rendu %s\n", e.nom, e.classe,
            tostring(e.suspect), tostring(e.civil), e.distance_m, tostring(e.hors_combat), tostring(e.rendu)))
        if valid(ctrl) then
            f:write("   ctrl " .. ctrl:GetClass():GetFName():ToString() .. " eveil "
                .. tostring(safe(function() return ctrl:GetAwarenessState() end, "?")) .. " entendu_swat "
                .. tostring(safe(function() return ctrl.bHasEverHeardSwat end, "?")) .. "\n")
            f:write("   activites [" .. tostring(safe(function() return ctrl:GetActivityQueueAsString():ToString() end, "?")) .. "]\n")
            f:write("   moral " .. tostring(safe(function() return ctrl.MoraleComponent:GetMorale() end, "?"))
                .. " fuite " .. tostring(valid(safe(function() return ctrl:GetMoveToExitActivity() end, nil)))
                .. " couvert " .. tostring(has("TakeCoverActivity")) .. " otage " .. tostring(has("TakeHostageActivity"))
                .. " obeir " .. tostring(has("CompliantActivity")) .. " mort " .. tostring(has("PlayDeadActivity")) .. "\n")
        else
            f:write("   pas de controleur\n")
        end
        f:write("   archetype " .. tostring(safe(function() return e.obj.Archetype.Name:ToString() end, "?"))
            .. " voix " .. tostring(safe(function() return e.obj.SpeechCharacterName:ToString() end, "?"))
            .. " stress " .. tostring(e.stress) .. " vie " .. tostring(e.vie) .. " arme " .. tostring(e.arme) .. "\n")
    end
    f:close()
    return "adverses.txt ecrit (" .. n .. ")"
end

-- the game's own decisions of one character: its action objects (CustomActions: AIAction_TakeHostage_C,
-- AIAction_FallbackToZone_C...), its current activity, and the combat moves its combat activity holds
local COMBAT_MOVES = { "HardCoverCombatMove", "DuelingCombatMove", "FlankingCombatMove", "SuppressionCombatMove",
    "PushCombatMove", "ChargeCombatMove", "FleeingCombatMove", "RepositionCombatMove" }

COMMANDS.adv_actions = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local ctrl, noms = e.obj.Controller, {}
    eachOf(ctrl.CustomActions, function(a) if valid(a) then noms[#noms + 1] = nameOf(a) end end)
    local cur = safe(function() return ctrl.CurrentActivity end, nil)
    local cmb = safe(function() return ctrl.CombatActivity end, nil)
    local moves = {}
    if valid(cmb) then
        for _, m in ipairs(COMBAT_MOVES) do
            if valid(safe(function() return cmb[m] end, nil)) then moves[#moves + 1] = m end
        end
        local run = safe(function() return cmb.CombatMoveActivity end, nil)
        moves[#moves + 1] = "en_cours=" .. (valid(run) and nameOf(run) or "aucune")
    end
    return string.format("%s actions [%s] activite %s combat %s [%s] otage_en_cours %s", e.nom, table.concat(noms, ","),
        valid(cur) and nameOf(cur) or "aucune", valid(cmb) and nameOf(cmb) or "aucune", table.concat(moves, ","),
        tostring(safe(function() return e.obj:IsTakingHostage() end, "?")))
end

-- start one of his own game actions (class name containing the pattern) the way the game's decision does
COMMANDS.adv_action = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local motif, trouve = args[2] or "", nil
    eachOf(e.obj.Controller.CustomActions, function(a)
        if not trouve and valid(a) and string.find(nameOf(a), motif, 1, true) then trouve = a end
    end)
    if not trouve then error("pas d'action " .. motif .. " pour " .. e.nom) end
    local ok, err = pcall(function() trouve:BeginAction_Blueprint() end)
    if not ok then error("BeginAction refuse : " .. tostring(err)) end
    return e.nom .. " lance " .. nameOf(trouve)
end

-- run one of the combat moves of his combat activity (the game's own fight logic), optionally to a point (metres)
COMMANDS.adv_manoeuvre = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local cmb = safe(function() return e.obj.Controller.CombatActivity end, nil)
    if not valid(cmb) then error(e.nom .. " n'a pas d'activite de combat") end
    local m = safe(function() return cmb[args[2]] end, nil)
    if not valid(m) then error("pas de " .. tostring(args[2]) .. " pour " .. e.nom) end
    local lieu = ""
    if args[3] then
        local p = numbers(args, 3)
        local ok, err = pcall(function() m:SetLocation(p, true, { X = 50.0, Y = 50.0, Z = 100.0 }) end)
        lieu = ok and string.format(" vers (%.1f, %.1f)", p.X / 100, p.Y / 100) or (" lieu refuse : " .. tostring(err))
    end
    cmb:StartRunningCombatMove(m)
    return e.nom .. " manoeuvre " .. args[2] .. lieu
end

-- the player's shout reaches a hostage taker the game's own way: his hostage activity "heard a yell" (Surrender
-- does nothing while he holds one, in game 02/11)
COMMANDS.adv_crier = function(pc, pawn, args)
    local e = aiByName(pc, pawn, args[1])
    local ctrl = e.obj.Controller
    local cls = StaticFindObject("/Script/ReadyOrNot.TakeHostageActivity")
    local a = valid(cls) and safe(function() return ctrl:GetActivity(cls) end, nil) or nil
    if not valid(a) then
        local cur = safe(function() return ctrl.CurrentActivity end, nil)
        if valid(cur) and nameOf(cur) == "TakeHostageActivity" then a = cur end
    end
    if not valid(a) then error(e.nom .. " ne tient pas d'otage") end
    a:OnHeardYell(pawn, true)
    return e.nom .. " entend le cri (otage)"
end

-- probe: the game's own "every suspect takes a hostage" (a debug function of the player controller)
COMMANDS.adv_otages_force = function(pc, pawn, args)
    pc:ForceAllTakeHostages(tonumber(args[1]) or 15.0)
    return "ForceAllTakeHostages envoye"
end

-- TEST ONLY: put the player and his AI team a few metres from a suspect, at a spot from which the player sees him
-- (the engine's TeleportTo refuses spots inside walls). Used to stage a real fight for the tactical AI.
COMMANDS.test_approcher = function(pc, pawn, args)
    -- args: a suspect name, or "suspect" for any armed active suspect; then the farthest distance to try (m)
    local cibles = {}
    for _, e in ipairs(listAI(pc, pawn)) do
        if (args[1] == "suspect" and e.suspect and e.arme and not e.hors_combat and not e.rendu) or e.nom == args[1] then
            cibles[#cibles + 1] = e
        end
    end
    if #cibles == 0 then error("aucune cible") end
    local teleports, vues = 0, 0
    for _, t in ipairs(cibles) do
    for _, metres in ipairs({ 3, 5, tonumber(args[2]) or 7 }) do
    local d = metres * 100
    for i = 0, 15 do
        local a = i * math.pi / 8
        local p = { X = t.pos.X + d * math.cos(a), Y = t.pos.Y + d * math.sin(a), Z = t.pos.Z + 20 }
        local yaw = math.deg(math.atan(t.pos.Y - p.Y, t.pos.X - p.X))
        if pawn:K2_TeleportTo(p, { Pitch = 0, Yaw = yaw, Roll = 0 }) then
            teleports = teleports + 1
            pc:SetControlRotation({ Pitch = 0, Yaw = yaw, Roll = 0 })
            if safe(function() return pc:LineOfSightTo(t.obj, { X = 0, Y = 0, Z = 0 }, false) end, false) then
                vues = vues + 1
                if args[3] == "seul" then   -- the team is not moved: it walks to him on its own (test of door entries)
                    return string.format("joueur seul a %.0f m de %s, en vue", d / 100, t.nom)
                end
                local n = 0
                for _, s in ipairs(listAI(pc, pawn, true)) do
                    if not s.classe:find("Trailer") then
                        n = n + 1
                        local q = { X = p.X - 150 * math.cos(math.rad(yaw)) + 80 * (n - 2.5) * math.sin(math.rad(yaw)),
                                    Y = p.Y - 150 * math.sin(math.rad(yaw)) - 80 * (n - 2.5) * math.cos(math.rad(yaw)), Z = p.Z }
                        s.obj:K2_TeleportTo(q, { Pitch = 0, Yaw = yaw, Roll = 0 })
                    end
                end
                return string.format("joueur et %d equipiers a %.0f m de %s, en vue", n, d / 100, t.nom)
            end
        end
    end
    end
    end
    error(string.format("aucun endroit libre d'ou l'on voit un des %d suspects (%d teleportations reussies, %d en vue)",
        #cibles, teleports, vues))
end

-- TEST ONLY: the player's view yaw and the game's own "right" vector of his view (to prove left/right, step 7)
COMMANDS.test_axes = function(pc, pawn, args)
    local r = pc:GetControlRotation()
    local d = StaticFindObject("/Script/Engine.Default__KismetMathLibrary"):GetRightVector(r)
    local p = vec(pawn:K2_GetActorLocation())
    return string.format("lacet %.2f droite %.4f %.4f %.4f pos %.2f %.2f %.2f", r.Yaw, d.X, d.Y, d.Z, p.X / 100, p.Y / 100, p.Z / 100)
end

COMMANDS.test_tourner = function(pc, pawn, args)
    pc:SetControlRotation({ Pitch = 0, Yaw = tonumber(args[1]) or 0, Roll = 0 })
    return "vue tournee a " .. tostring(args[1])
end

-- TEST ONLY: the player takes no damage (1) or does again (0), so a staged fight can last
-- test only: the player at a point (metres), facing another, the team in a row 1.5 m behind him (03/11: to put the
-- team across a window from a suspect)
COMMANDS.test_placer = function(pc, pawn, args)
    local p = { X = tonumber(args[1]) * 100, Y = tonumber(args[2]) * 100, Z = tonumber(args[3]) * 100 + 20 }
    local yaw = math.deg(math.atan((tonumber(args[5]) or 0) * 100 - p.Y, (tonumber(args[4]) or 0) * 100 - p.X))
    if not pawn:K2_TeleportTo(p, { Pitch = 0, Yaw = yaw, Roll = 0 }) then error("place prise") end
    pc:SetControlRotation({ Pitch = 0, Yaw = yaw, Roll = 0 })
    -- args[6] "seul": the team stays where it is (03/11: brought behind him, it dealt with the suspect he was put
    -- next to before the test could say a word)
    if args[6] == "seul" then return "joueur place seul" end
    local n = 0
    for _, s in ipairs(listAI(pc, pawn, true)) do
        if not s.classe:find("Trailer") then
            n = n + 1
            local q = { X = p.X - 150 * math.cos(math.rad(yaw)) + 80 * (n - 2.5) * math.sin(math.rad(yaw)),
                        Y = p.Y - 150 * math.sin(math.rad(yaw)) - 80 * (n - 2.5) * math.cos(math.rad(yaw)), Z = p.Z }
            s.obj:K2_TeleportTo(q, { Pitch = 0, Yaw = yaw, Roll = 0 })
        end
    end
    return string.format("joueur et %d equipiers places", n)
end

-- test only: a suspect killed outright, so his weapon stays on the floor with nobody to cuff (the team's evidence job)
COMMANDS.test_tuer = function(pc, pawn, args)
    for _, e in ipairs(listAI(pc, pawn)) do
        if e.nom == args[1] then
            e.obj:Kill()
            return e.nom
        end
    end
    error("aucune cible")
end

COMMANDS.test_invulnerable = function(pc, pawn, args)
    -- a property in this engine version, not a function (02/11: "attempt to call a TrivialObject value")
    pawn.bCanBeDamaged = (args[1] == "0")
    return "joueur peut etre blesse : " .. tostring(safe(function() return pawn.bCanBeDamaged end, "?"))
end

COMMANDS.membre_fouiller = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local p = args[2] and numbers(args, 2) or vec(e.obj:K2_GetActorLocation())
    swatManager():GiveSearchAndSecureCommand_Individual(e.obj, p, true)
    return e.nom .. " fouille"
end

-- ---- doors and equipment (the game's squad orders: by team, "rouge" / "bleu" / "tous") ----

-- item categories the AI team may use (EItemCategory)
local ITEMS = { flash = 30, ninebang = 31, gaz = 32, stinger = 33, optiwand = 34, beanbag = 35, taser = 36,
                pepperball = 37, c2 = 38, cale = 40, belier = 41, fusil_breche = 42, chemlight = 28, bouclier = 29,
                spray = 24 }
local BREACH = { ouvrir = 1, enfoncer = 3, fusil = 4, belier = 5, c2 = 6 }   -- EDoorBreachType
local BREACH_ITEM = { fusil = "fusil_breche", belier = "belier", c2 = "c2" }

local function doorByName(pc, nom)
    for _, d in ipairs(allOfClass(pc, "/Script/ReadyOrNot.Door")) do
        if d:GetFName():ToString() == nom then return d end
    end
    error("porte inconnue : " .. tostring(nom))
end

-- where an order on a door is given from: the doorway's centre and its face on the player's side.
-- In game (02/11) the actor's origin (its hinge) and forward vector sent the team nowhere: all four stood still
-- in "Stack Up". The game puts a threat marker on each side of every door: the centre is between them, and the
-- face is the marker on the player's side.
local function doorSide(pawn, d)
    local me = vec(pawn:K2_GetActorLocation())
    local fr = safe(function() return vec(d.FrontThreat:K2_GetActorLocation()) end, nil)
    local bk = safe(function() return vec(d.BackThreat:K2_GetActorLocation()) end, nil)
    if fr and bk then
        local c = { X = (fr.X + bk.X) / 2, Y = (fr.Y + bk.Y) / 2, Z = (fr.Z + bk.Z) / 2 }
        local cote = dist(me, fr) <= dist(me, bk) and fr or bk
        local nx, ny = cote.X - c.X, cote.Y - c.Y
        local n = math.max(math.sqrt(nx * nx + ny * ny), 1)
        local f = { X = nx / n, Y = ny / n, Z = 0 }
        return { X = c.X + 20 * f.X, Y = c.Y + 20 * f.Y, Z = c.Z }, f
    end
    local loc = vec(d:K2_GetActorLocation())
    local f = vec(d:GetActorForwardVector())
    if (me.X - loc.X) * f.X + (me.Y - loc.Y) * f.Y < 0 then f = { X = -f.X, Y = -f.Y, Z = -f.Z } end
    return { X = loc.X + 40 * f.X, Y = loc.Y + 40 * f.Y, Z = loc.Z }, f
end

COMMANDS.sonde_porte = function(pc, pawn, args)
    local d = doorByName(pc, args[1])
    local loc, f = doorSide(pawn, d)
    local o = vec(d:K2_GetActorLocation())
    local fr = safe(function() return vec(d.FrontThreat:K2_GetActorLocation()) end, nil)
    return string.format("origine (%.1f,%.1f) commande (%.1f,%.1f) normale (%.2f,%.2f) reperes %s ; desactive %s, ordres avant %s arriere %s",
        o.X / 100, o.Y / 100, loc.X / 100, loc.Y / 100, f.X, f.Y, tostring(fr ~= nil),
        tostring(safe(function() return d:IsStackUpDisabled(loc) end, "?")),
        tostring(safe(function() return d.bCanIssueOrdersOnFrontSide end, "?")),
        tostring(safe(function() return d.bCanIssueOrdersOnBackSide end, "?")))
end

-- the class of an item a teammate carries (the game's commands want the class: which flashbang, which ram)
local function itemClass(pc, pawn, cat)
    for _, e in ipairs(listAI(pc, pawn, true)) do
        if not e.classe:find("Trailer") then
            local it = safe(function() return e.obj.InventoryComp:GetInventoryItemOfType(cat) end, nil)
            if valid(it) then return it:GetClass() end
        end
    end
    return nil
end

local function count(e, cat)
    return safe(function() return e.obj.InventoryComp:CountInventoryItemType(cat) end, -1)
end

COMMANDS.sonde_equipement = function(pc, pawn, args)
    for _, e in ipairs(listAI(pc, pawn, true)) do
        if not e.classe:find("Trailer") then
            local parts = {}
            for nom, cat in pairs(ITEMS) do
                local n = count(e, cat)
                if n ~= 0 then parts[#parts + 1] = nom .. "=" .. tostring(n) end
            end
            table.sort(parts)
            log(string.format("equipier %s poste %s equipe %s : %s", e.nom,
                tostring(safe(function() return e.obj.SquadPosition end, "?")),
                tostring(safe(function() return e.obj:GetTeam() end, "?")), table.concat(parts, " ")))
        end
    end
    local here = vec(pawn:K2_GetActorLocation())
    local best, bd = nil, nil
    for _, d in ipairs(allOfClass(pc, "/Script/ReadyOrNot.Door")) do
        local m = dist(here, vec(d:K2_GetActorLocation()))
        if not bd or m < bd then best, bd = d, m end
    end
    if best then
        local function q(f) return tostring(safe(function() return best[f](best) end, "?")) end
        log(string.format("porte la plus proche %s a %.1f m : ouverte %s verrouillee %s piege %s calee %s c2 %s cassee %s",
            best:GetFName():ToString(), bd / 100, q("IsOpen"), q("IsLocked"), q("IsTrapLive"), q("IsJammed"),
            q("IsC2Placed"), q("IsDoorBroken")))
    end
    return "sonde ecrite dans le journal"
end

-- porte <action> <equipe> <porte> [methode] [grenade]
--   actions: empiler, pieges, contacts, ouvrir, fermer, crocheter, caler, decaler, desamorcer, entrer
--   entrer: methode = ouvrir | enfoncer | fusil | belier | c2 ; grenade = flash | stinger | gaz | ninebang | aucune
-- the stack-up side: with the player close to the door, on the other side of the doorway from him (in game, 02/11,
-- the player standing on a stack-up spot left two teammates waiting); else the game picks (Auto)
local function stackStyle(pawn, d, loc)
    local me = vec(pawn:K2_GetActorLocation())
    if dist(me, loc) > 350 then return 0 end
    local droite = safe(function() return d:IsPointRightOfDoorway(me) end, nil)
    if droite == nil then return 0 end
    return droite and 2 or 3   -- EStackUpStyle: Left = 2, Right = 3
end

COMMANDS.porte = function(pc, pawn, args)
    local action, team, d = args[1], TEAMS[args[2] or "tous"] or 5, doorByName(pc, args[3])
    local m = swatManager()
    if not m then error("pas de SWATManager") end
    local loc, n = doorSide(pawn, d)
    local style = stackStyle(pawn, d, loc)
    if action == "empiler" then
        m:GiveStackUpCommand(d, team, loc, n, true, style)
        return "empiler " .. d:GetFName():ToString() .. ({ [0] = " (auto)", [2] = " (a gauche)", [3] = " (a droite)" })[style]
    elseif action == "baton" then
        -- mark the room as cleared: a chemlight in the doorway
        m:GiveDropChemlightAtLocation(team, loc)
        return "baton lumineux a " .. d:GetFName():ToString()
    elseif action == "pieges" then m:GiveCheckForTrapsCommand(d, team, loc, n)
    elseif action == "contacts" then m:GiveCheckForContactsCommand(d, team, loc, n)
    elseif action == "ouvrir" then m:GiveOpenDoorCommand(d, team, loc)
    elseif action == "fermer" then m:GiveCloseDoorCommand(d, team, loc)
    elseif action == "crocheter" then m:GivePickLockCommand(d, team, loc)
    elseif action == "caler" then m:GiveWedgeDoorCommand(d, team, loc, n)
    elseif action == "decaler" then m:GiveRemoveWedgeCommand(d, team, loc, n)
    elseif action == "desamorcer" then m:GiveDisarmTrapOnDoorCommand(d, team, loc)
    elseif action == "grenade" then
        -- into the room behind the door: 3 m past the doorway, on the far side from the player. (The grenade
        -- given with "entrer" was never thrown in game, 02/11, so entries throw it as a step of their own.)
        local g = args[4] or "flash"
        local cat = ITEMS[g]
        if not cat or cat < 30 or cat > 33 then error("grenade inconnue : " .. tostring(g)) end
        local cls = itemClass(pc, pawn, cat)
        if not cls then error("personne n'a de " .. g) end
        local p = { X = loc.X - 320 * n.X, Y = loc.Y - 320 * n.Y, Z = loc.Z }
        m:GiveDeployGrenadeAtLocation(team, p, cls)
        return string.format("%s derriere %s", g, d:GetFName():ToString())
    elseif action == "entrer" then
        local methode = args[4] or "ouvrir"
        local bt = BREACH[methode]
        if not bt then error("methode inconnue : " .. methode) end
        local breachItem = BREACH_ITEM[methode] and itemClass(pc, pawn, ITEMS[BREACH_ITEM[methode]]) or nil
        if BREACH_ITEM[methode] and not breachItem then error("personne n'a de " .. BREACH_ITEM[methode]) end
        local g = args[5] or "aucune"
        local useItem = (g ~= "aucune" and ITEMS[g]) and itemClass(pc, pawn, ITEMS[g]) or nil
        if g ~= "aucune" and not useItem then error("personne n'a de " .. g) end
        -- "sans_nettoyage": breach only (the door is opened or forced, nobody goes in yet: a grenade comes first)
        local nettoyer = args[6] ~= "sans_nettoyage"
        m:GiveBreachAndClearCommand(d, bt, team, loc, breachItem, useItem, false, false, nettoyer, nettoyer, style, true)
        return string.format("entrer par %s (%s, grenade %s%s)", d:GetFName():ToString(), methode, g,
            nettoyer and "" or ", sans nettoyage")
    elseif action == "nettoyer" then
        -- clear the room behind the door, 3 m past the doorway on the far side: the hold is lifted, then each
        -- member of the team gets his own search order (in game, 02/11, the team order given right after the
        -- hold did not take; the individual one sent two reds in, and they cuffed someone)
        m:RemoveHoldCommand(team)
        local p = { X = loc.X - 320 * n.X, Y = loc.Y - 320 * n.Y, Z = loc.Z }
        local k = 0
        for _, s in ipairs(listAI(pc, pawn, true)) do
            local t = tonumber(safe(function() return s.obj:GetTeam() end, 0)) or 0
            if not s.classe:find("Trailer") and not s.hors_combat and (team == 5 or t == team) then
                m:GiveSearchAndSecureCommand_Individual(s.obj, p, true)
                k = k + 1
            end
        end
        return string.format("nettoyer %s (%d equipiers)", d:GetFName():ToString(), k)
    else
        error("action de porte inconnue : " .. tostring(action))
    end
    return action .. " " .. d:GetFName():ToString()
end

-- grenade <equipe> <type> x y z : throw a grenade (flash, stinger, gaz, ninebang) at a point, in metres
COMMANDS.grenade = function(pc, pawn, args)
    local team, cat = TEAMS[args[1] or "tous"] or 5, ITEMS[args[2] or ""]
    if not cat or cat < 30 or cat > 33 then error("grenade inconnue : " .. tostring(args[2])) end
    local cls = itemClass(pc, pawn, cat)
    if not cls then error("personne n'a de " .. args[2]) end
    swatManager():GiveDeployGrenadeAtLocation(team, numbers(args, 3), cls)
    return args[2] .. " lancee"
end

-- non_letal <equipe> <cible> <type> : taser, beanbag, pepperball, spray on a suspect or civilian
COMMANDS.non_letal = function(pc, pawn, args)
    local team, t, cat = TEAMS[args[1] or "tous"] or 5, pickTarget(pc, pawn, args[2] or "vise"), ITEMS[args[3] or ""]
    if not t then error("aucune cible") end
    if not cat then error("objet inconnu : " .. tostring(args[3])) end
    swatManager():GiveDeployNonLethalItemAtTargetCommand(t.obj, team, cat)
    return args[3] .. " sur " .. t.nom
end

COMMANDS.bouclier = function(pc, pawn, args)
    swatManager():GiveDeployShield(TEAMS[args[1] or "tous"] or 5)
    return "bouclier"
end

COMMANDS.chemlight = function(pc, pawn, args)
    swatManager():GiveDropChemlightAtLocation(TEAMS[args[1] or "tous"] or 5, numbers(args, 2))
    return "baton lumineux"
end

-- probe: the game's spoken lines (data tables whose name speaks of speech, VO, barks, dialogue), row names
-- written to pont/repliques.txt, to pick the teammates' call-outs
COMMANDS.sonde_repliques = function(pc, pawn, args)
    local lib = StaticFindObject("/Script/Engine.Default__DataTableFunctionLibrary")
    local f = io.open(DIR .. "repliques.txt", "w")
    if not f then error("fichier repliques.txt") end
    local tables, lignes = 0, 0
    local all = FindAllOf("DataTable") or {}
    for _, dt in ipairs(all) do
        local nom = safe(function() return dt:GetFullName() end, "")
        local bas = nom:lower()
        if bas:find("speech") or bas:find("vo_") or bas:find("bark") or bas:find("dialog") or bas:find("voice") then
            tables = tables + 1
            f:write("== " .. nom .. "\n")
            local out = {}
            safe(function() lib:GetDataTableRowNames(dt, out) end)
            local noms = out.OutRowNames or out
            if type(noms) == "table" then
                for _, n in ipairs(noms) do
                    local s = safe(function() return (type(n) == "userdata" and n.get) and n:get():ToString() or n:ToString() end, "?")
                    f:write(s .. "\n")
                    lignes = lignes + 1
                end
            elseif noms and noms.ForEach then
                noms:ForEach(function(_, n)
                    f:write(safe(function() return n:get():ToString() end, "?") .. "\n")
                    lignes = lignes + 1
                end)
            end
        end
    end
    f:close()
    return string.format("%d tables, %d repliques -> repliques.txt", tables, lignes)
end

-- ---- the teammates' voices ----
-- the game's "this character speaks" event, counted per speaker (to know whether a line really played)
local voixEntendues = {}
local okVoix, errVoix = pcall(RegisterHook, "/Script/ReadyOrNot.ReadyOrNotCharacter:VoiceAudioEvent", function(ctx, speaker)
    local s = safe(function() return speaker:get():GetFName():ToString() end, nil)
        or safe(function() return ctx:get():GetFName():ToString() end, "?")
    voixEntendues[s] = (voixEntendues[s] or 0) + 1
end)
log("accroche VoiceAudioEvent : " .. (okVoix and "ok" or ("ECHEC " .. tostring(errVoix))))

COMMANDS.voix_entendues = function(pc, pawn, args)
    local parts = {}
    for s, n in pairs(voixEntendues) do parts[#parts + 1] = s .. "=" .. n end
    return table.concat(parts, " ")
end

local HELPER = nil
local function helper()
    HELPER = HELPER or StaticFindObject("/Script/ReadyOrNot.Default__BpGameplayHelperLib")
    return HELPER
end

-- probe: each teammate's voice name, the rows of his speech table, and the file the game finds for a line,
-- written to pont/voix.txt
COMMANDS.sonde_voix = function(pc, pawn, args)
    local f = io.open(DIR .. "voix.txt", "w")
    if not f then error("fichier voix.txt") end
    local lib = StaticFindObject("/Script/Engine.Default__DataTableFunctionLibrary")
    local essais = { "RogerGeneric", "[RESPONSE]RogerGeneric", "Cover", "[RESPONSE]Cover" }
    for _, e in ipairs(listAI(pc, pawn, true)) do
        if not e.classe:find("Trailer") then
            local voix = safe(function() return e.obj.SpeechCharacterName:ToString() end, "?")
            f:write(string.format("== %s voix %s\n", e.nom, tostring(voix)))
            for _, b in ipairs(essais) do
                local chemin = safe(function()
                    local r = helper():GetDialoguePath(voix, b)
                    return type(r) == "string" and r or r:ToString()
                end, "?")
                f:write(string.format("   chemin(%s) = %s\n", b, tostring(chemin)))
            end
            local dt = safe(function() return helper():GetSpeechLookupDataTable(voix) end, nil)
            if valid(dt) then
                f:write("   table " .. dt:GetFullName() .. "\n")
                local out = {}
                safe(function() lib:GetDataTableRowNames(dt, out) end)
                local noms, n = out.OutRowNames or out, 0
                local function ecrire(s) n = n + 1; if n <= 400 then f:write("   " .. s .. "\n") end end
                if type(noms) == "table" then
                    for _, x in ipairs(noms) do ecrire(safe(function() return x:ToString() end, "?")) end
                elseif noms and noms.ForEach then
                    noms:ForEach(function(_, x) ecrire(safe(function() return x:get():ToString() end, "?")) end)
                end
                f:write("   (" .. n .. " lignes)\n")
            else
                f:write("   pas de table de repliques\n")
            end
        end
    end
    f:close()
    return "voix.txt ecrit"
end

-- one teammate says a line of the game, by one of the game's four ways (to find the one that works):
-- raw = PlayRawVO, bark = PlayBarkOrStartConversation, serveur = Server_VoiceLine, multi = Multicast_PlayRawVO
COMMANDS.parler = function(pc, pawn, args)
    local e = swatByName(pc, pawn, args[1])
    local ligne, facon = args[2] or "", args[3] or "raw"
    local voix = safe(function() return e.obj.SpeechCharacterName:ToString() end, "")
    if facon == "raw" then e.obj:PlayRawVO(ligne, "", false)
    elseif facon == "bark" then e.obj:PlayBarkOrStartConversation(ligne, false, 0.0)
    elseif facon == "serveur" then e.obj:Server_VoiceLine(ligne, voix, false)
    elseif facon == "multi" then e.obj:Multicast_PlayRawVO(ligne, "", false)
    else error("facon inconnue : " .. facon) end
    return string.format("%s (%s) dit %s par %s", e.nom, tostring(voix), ligne, facon)
end

-- TEST ONLY: turn the player away from his whole team (to fire test shots at nobody)
COMMANDS.test_dos_equipe = function(pc, pawn, args)
    local me, sx, sy, n = vec(pawn:K2_GetActorLocation()), 0, 0, 0
    for _, s in ipairs(listAI(pc, pawn, true)) do
        if not s.classe:find("Trailer") then
            sx, sy, n = sx + s.pos.X - me.X, sy + s.pos.Y - me.Y, n + 1
        end
    end
    local yaw = n > 0 and math.deg(math.atan(-sy, -sx)) or 0
    pc:SetControlRotation({ Pitch = -10, Yaw = yaw, Roll = 0 })
    return string.format("joueur tourne vers %.0f degres (dos a %d equipiers)", yaw, n)
end

-- TEST ONLY: put the player 2.5 m in front of a door, facing it (his team is not moved: it walks to him)
COMMANDS.test_devant_porte = function(pc, pawn, args)
    local d = doorByName(pc, args[1])
    local loc, f = vec(d:K2_GetActorLocation()), vec(d:GetActorForwardVector())
    local m = (tonumber(args[2]) or 2.5) * 100   -- 2.5 m stood on a stack-up spot in game (02/11): two teammates waited
    for _, s in ipairs({ 1, -1 }) do
        local p = { X = loc.X + s * m * f.X, Y = loc.Y + s * m * f.Y, Z = loc.Z + 20 }
        local yaw = math.deg(math.atan(loc.Y - p.Y, loc.X - p.X))
        if pawn:K2_TeleportTo(p, { Pitch = 0, Yaw = yaw, Roll = 0 }) then
            pc:SetControlRotation({ Pitch = 0, Yaw = yaw, Roll = 0 })
            return "joueur devant " .. args[1]
        end
    end
    error("pas de place devant " .. args[1])
end

-- test only: press/release the game's own push-to-talk, as the voice chat key does
COMMANDS.parole = function(pc, pawn, args)
    local ps = pc.PlayerState
    if not valid(ps) then error("pas de PlayerState") end
    ps:Server_PushToTalk(args[1] == "1")
    return "Server_PushToTalk " .. tostring(args[1] == "1")
end

-- commands that do not touch the AI, allowed on a coop client too
local ANYWHERE = { ping = true, console = true, parole = true }

local function runLine(id, cmd, args)
    local pc, pawn = playerPawn()
    local fn = COMMANDS[cmd]
    if not fn then
        append(RESULTS, id .. "|ECHEC|commande inconnue " .. tostring(cmd))
        return
    end
    if not pc then
        append(RESULTS, id .. "|ECHEC|pas de PlayerController")
        return
    end
    if cmd ~= "ping" and cmd ~= "console" and not pawn then
        append(RESULTS, id .. "|ECHEC|pas de personnage joueur")
        return
    end
    if not ANYWHERE[cmd] and not isHost(pawn) then
        append(RESULTS, id .. "|ECHEC|pas l'hote : en coop, seul le jeu de l'hote decide de l'IA")
        return
    end
    local ok, msg = pcall(fn, pc, pawn, args)
    append(RESULTS, id .. "|" .. (ok and "ok" or "ECHEC") .. "|" .. tostring(msg))
    log(string.format("%d %s -> %s %s", id, cmd, ok and "ok" or "ECHEC", tostring(msg)))
end

local function readOrders()
    local f = io.open(ORDERS, "r")
    if not f then return {} end
    local todo = {}
    for line in f:lines() do
        local id, rest = line:match("^(%d+)%s+(.+)$")
        id = tonumber(id)
        if id and id > lastId then
            local words = {}
            for w in rest:gmatch("%S+") do words[#words + 1] = w end
            table.insert(todo, { id = id, cmd = table.remove(words, 1), args = words })
        end
    end
    f:close()
    return todo
end

-- Game voice chat: the AI listens while a player talks on it, whatever key he bound in the game.
-- Server_PushToTalk runs where the server is (solo, host: for every player); Multicast_PushToTalk reaches every
-- machine, for every talker. pont/parle.txt has one line per player seen talking, rewritten on each change:
-- "<1|0> <os.time()> <moi|autre> <player name>" (the name last: it may contain spaces).
local TALK = DIR .. "parle.txt"
local talkers = {}   -- player name -> { on = bool, t = os.time(), moi = bool }

local function writeTalkers()
    local f = io.open(TALK .. ".tmp", "w")
    if not f then return end
    for nom, s in pairs(talkers) do
        f:write(string.format("%s %d %s %s\n", s.on and "1" or "0", s.t, s.moi and "moi" or "autre", nom))
    end
    f:close()
    os.remove(TALK)
    os.rename(TALK .. ".tmp", TALK)
end

local function setTalking(ps, on, src)
    local nom, moi = playerName(ps), isOwnState(ps)
    local s = talkers[nom]
    if s and s.on == on then return end
    talkers[nom] = { on = on, t = os.time(), moi = moi }
    writeTalkers()
    log("chat vocal " .. (on and "debut" or "fin") .. " : " .. nom .. (moi and " (moi)" or "") .. " (" .. src .. ")")
end

writeTalkers()   -- never leave a "1" from a previous run
for _, fn in ipairs({ "Server_PushToTalk", "Multicast_PushToTalk" }) do
    local ok, err = pcall(RegisterHook, "/Script/ReadyOrNot.ReadyOrNotPlayerState:" .. fn, function(ctx, push)
        local ps = ctx:get()
        if valid(ps) then setTalking(ps, push:get() == true, fn) end
    end)
    log("accroche " .. fn .. " : " .. (ok and "ok" or ("ECHEC " .. tostring(err))))
end

-- skip the orders already present when the game starts: only new lines run
for _, o in ipairs(readOrders()) do
    if o.id > lastId then lastId = o.id end
end
log("mod charge, dernier ordre deja present : " .. lastId)

LoopAsync(250, function()
    tick = tick + 1
    local todo = readOrders()
    for _, o in ipairs(todo) do
        lastId = math.max(lastId, o.id)
    end
    if #todo > 0 or tick % 4 == 0 then
        ExecuteInGameThread(function()
            for _, o in ipairs(todo) do
                runLine(o.id, o.cmd, o.args)
            end
            if tick % 4 == 0 then
                local ok, err = pcall(writeState)
                if not ok then log("etat ECHEC " .. tostring(err)) end
            end
        end)
    end
    return false
end)
