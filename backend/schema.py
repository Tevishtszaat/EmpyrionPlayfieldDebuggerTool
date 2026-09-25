"""Property names Empyrion actually deserializes.

Planet random POIs follow RandomPoiData (group name, drones, near/avoid, biome).
Space POIs follow the orbit POI map (Pos/Rot/Mode/Compound/Probability) and often
live under POIs.Random as well. The union is what we treat as known. Anything
else is reported and left in the file unless you explicitly strip it.

Source for the planet/fixed field names: the stock playfield schema mirrored by
EgsLib PlayfieldYaml.RandomPoiData / FixedPoiData, checked against ExamplePlanet
(DronesMinMax, TroopTransport, SpawnPOINear, SpawnPOINearRange, POIDistance,
PlayerStart, DroneBaseSetup). Space compound fields: Compound.Name,
CountMinMax, Probability, DistanceMinMax, Rotate.
"""

RANDOM_POI_KEYS = {
    "groupname",
    "useeachgrouppoionlyonce",
    "dronebasesetup",
    "dronesetupid",
    "planetvesselbasesetup",
    "faction",
    "factionterritory",
    "territory",
    "avoidfactionterritory",
    "noshieldreload",
    "iscommandcenter",
    "type",
    "isimportant",
    "auxiliarypois",
    "isauxpoi",
    "isscalingcount",
    "countminmax",
    "droneprob",
    "trooptransport",
    "dronesminmax",
    "reservecount",
    "properties",
    "initpower",
    "levelmod",
    "playerstart",
    "spawnpoinear",
    "spawnpoiavoid",
    "spawnresource",
    "placeat",
    "spacedefenseoverridedefaults",
    "spacedefenseprobability",
    "spacedefensepriceminmax",
    "poidistance",
    "spawnpoineardistance",
    "spawnpoinearrange",
    "spawnpoiavoiddistance",
    "resourcedistance",
    "spawnresourcerange",
    "biome",
    "biomesexcluded",
    # Present on orbit entries that share the Random list.
    "prefab",
    "name",
    "names",
    "fieldname",
    "displayname",
    "position",
    "probability",
    "radiusminmax",
    "compound",
    "compoundpoi",
    "mode",
    "submode",
    "pos",
    "rot",
    "initresource",
}

FIXED_POI_KEYS = {
    "type",
    "prefab",
    "mode",
    "submode",
    "name",
    "names",
    "initpower",
    "levelmod",
    "faction",
    "factionterritory",
    "properties",
    "noshieldreload",
    "iscommandcenter",
    "pos",
    "rot",
    "spawnresource",
    "resourcedistance",
    "spawnresourcerange",
    "spacedefenseoverridedefaults",
    "spacedefenseprobability",
    "spacedefensepriceminmax",
    "groupname",
    "displayname",
    "probability",
    "countminmax",
    "compound",
    "compoundpoi",
    "biome",
    "initresource",
}

# Tokens that are not blueprint groups.
SPAWN_TOKENS = {"start", "any", "all", "none", "null", ""}

BIOME_TOKENS = {"any", "all", "global", "space", "none", ""}
