import json
import math
import os
import random
import sqlite3
import time
from typing import Optional

from dotenv import load_dotenv

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, join_room, leave_room, emit, disconnect
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency path
    OpenAI = None

try:
    import openai as openai_module
except ImportError:  # pragma: no cover - optional dependency path
    openai_module = None

# --- Basic Flask setup ---
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-prod")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

DB_PATH = "game.db"

# --- Multi-zone world definition (grid based maps) ---
DEFAULT_ZONE = "village"
VILLAGE_START = (2, 2)
DUNGEON_1_START = (3, 0)
DUNGEON_2_START = (4, 0)

MAX_CHARACTERS_PER_ACCOUNT = 3

VILLAGE_MAP = [
    [
        {
            "name": "Northern Gate",
            "description": "Timber palisades creak while a sentry nods beneath the gate arch. Beyond the arch the trade road stretches north into the frontier, while the village lane bends east toward Merchant Row and south toward the Thatched Cottages; the palisade crowds close along the western wall.",
        },
        {
            "name": "Merchant Row",
            "description": "Stalls line the street with bolts of cloth and fresh produce neatly displayed. A narrow cart track returns west to the Northern Gate, the shrine's stone steps rise to the east, and a footpath drops south into the Herbalist Garden; the palisade seals away any path to the north.",
        },
        {
            "name": "Shrine Steps",
            "description": "Stone steps lead to a modest shrine where votive candles gutter in the breeze. The plaza spills west onto Merchant Row, a shadowed watchtower yard waits to the east, and the main thoroughfare continues south toward the Town Hall; the cliff of the palisade blocks any climb northward.",
        },
        {
            "name": "Watchtower Shadow",
            "description": "The shadow of the wooden watchtower stretches across crates of supplies. Patrol stairs descend west toward the shrine, a service lane leads east toward the crumbling wall, and a ramp descends south into the Storage Barn's yard; the timber barricade hugs the northern edge.",
        },
        {
            "name": "Crumbling Wall",
            "description": "Weathered masonry from an older fortification lies half-buried in the sod. The watchtower yard lies to the west and the wagon yard spreads to the south, while shattered stone and the outer ditch block passage further north or east.",
        },
    ],
    [
        {
            "name": "Thatched Cottages",
            "description": "Smoke from cookfires drifts lazily above tidy thatched cottages. A muddy lane heads north to the Northern Gate, the herb garden blooms eastward, and baker's apprentices dash south down Baker's Lane; pastures hem in the cottages to the west.",
        },
        {
            "name": "Herbalist Garden",
            "description": "Raised beds of fragrant herbs attract bees and the occasional wandering villager. Wicker gates swing west toward the thatched cottages, the Town Hall stands east beyond tidy hedges, merchant stalls bustle to the south, and the market road climbs north toward Merchant Row.",
        },
        {
            "name": "Town Hall",
            "description": "The timbered town hall smells of parchment and warm lamp oil. Stone steps climb north toward the shrine, civic notices point south toward the Village Square, and the public lane angles west into the Herbalist Garden. A thick oak service door with iron bands stands in the east wall, linking the hall to the Storage Barn beyond.",
        },
        {
            "name": "Storage Barn",
            "description": "Barrels of grain and bundled hay crowd the floor and loft. The watchtower yard opens northward, the wagon yard sprawls to the east, and a work path slopes south toward the Blacksmith Forge. A sturdy service door in the west wall leads back into the Town Hall, its hinges groaning when opened.",
        },
        {
            "name": "Wagon Yard",
            "description": "Wooden wagons await repair beside piles of seasoned lumber. The crumbling wall looms to the north, the mine entrance waits to the south, and teamsters can retreat west into the Storage Barn while the outer bank blocks travel further east.",
        },
    ],
    [
        {
            "name": "Baker's Lane",
            "description": "Warm smells of bread drift from an open oven while apprentices hurry by. The cottage lane climbs north toward the homesteads, market stalls bustle to the east, and the forest path winds south beneath the trees; stacked wood piles close off the western fence.",
        },
        {
            "name": "Market Stalls",
            "description": "Vendors haggle over coppers as curious travelers browse their wares. Merchant Row beckons north, the Village Square thrums to the east, the Ancient Well lies down a shaded path to the south, and Baker's Lane curls back to the west.",
        },
        {
            "name": "Village Square",
            "description": "A cobbled square with a communal well and a notice board layered with fresh ink. The Town Hall towers to the north, the forge blazes to the east, Lakeside Dock extends south, and market stalls throng to the west.",
        },
        {
            "name": "Blacksmith Forge",
            "description": "Sparks fly from the anvil as the smith hammers glowing iron. The Storage Barn offers supplies to the north, the Old Mine Entrance yawns to the east, Fisher's Hut nestles south along the water, and the Village Square rests to the west.",
        },
        {
            "name": "Old Mine Entrance",
            "description": "Timber supports frame the old mine entrance where a rune-cut warp stone hums beside the cart tracks. Wagon ruts return north to the yard, the lakefront trail bends south toward an abandoned shed, and the forge's sparks dance to the west while sheer rock prevents travel further east.",
            "warp_description": "A waist-high warp stone thrums with stored power, waiting for a willing traveler.",
            "travel_to": {"zone": "dungeon_1", "start": DUNGEON_1_START},
        },
    ],
    [
        {
            "name": "Forest Path",
            "description": "A narrow track winds beneath dark pines east of the village. Baker's Lane lies to the north, the Ancient Well waits to the east, the South Field stretches southward, and the treeline thickens impassably to the west.",
        },
        {
            "name": "Ancient Well",
            "description": "An ivy-wrapped stone well whispers with echoes from below. Market Stalls bustle to the north, Lakeside Dock creaks to the east, the Hayloft ladder rises to the south, and the forest path circles back west.",
            "search": {
                "dc": 11,
                "ability": "wis",
                "success_text": "You notice a loose capstone and uncover a forgotten brooch tucked inside.",
                "failure_text": "Cold water splashes your hands but nothing else reveals itself.",
                "loot": ["forgotten_brooch"],
            },
        },
        {
            "name": "Lakeside Dock",
            "description": "Wooden planks creak as small fishing boats bob against the pilings. The Village Square bustles to the north, Fisher's Hut leans east along the shore, the Sunken Stair descends southward, and the well path loops back west.",
        },
        {
            "name": "Fisher's Hut",
            "description": "Nets dry on racks beside a cottage that smells strongly of smoked trout. The forge blazes to the north, the abandoned shed lists to the east, Miller's Bridge crosses the millrace to the south, and Lakeside Dock lies to the west.",
        },
        {
            "name": "Abandoned Shed",
            "description": "Broken tools litter a leaning shed reclaimed by moss. The mine tracks return north, Riverside Grove murmurs to the south, and Fisher's Hut stands to the west while thick bramble chokes the eastern approach.",
        },
    ],
    [
        {
            "name": "South Field",
            "description": "Rows of freshly turned soil promise a hearty autumn harvest. The forest path returns north, the Hayloft beckons east beside the barn, and hedgerows fence off the farmland to the south and west.",
        },
        {
            "name": "Hayloft",
            "description": "Stacks of hay tower above the barn floor, a favorite hideout for village children. The Ancient Well rests to the north, the Sunken Stair descends east toward the cellar, and the South Field lies to the west while corrals close the way south.",
        },
        {
            "name": "Sunken Stair",
            "description": "Stone steps descend into a cellar where a crystal-veined warp stone casts steady, cold light. Lakeside Dock rests to the north, Miller's Bridge arches east, the Hayloft is tucked to the west, and packed earth walls bar any southern exit.",
            "warp_description": "An embedded warp stone glows between the lowest steps, promising passage for those who touch it.",
            "travel_to": {"zone": "dungeon_2", "start": DUNGEON_2_START},
        },
        {
            "name": "Miller's Bridge",
            "description": "A wooden bridge spans the millrace, slick with fine spray. Fisher's Hut perches to the north, Riverside Grove rustles to the east, and the Sunken Stair is only a few steps west while foaming water bars travel south.",
        },
        {
            "name": "Riverside Grove",
            "description": "Tall willows shade a quiet bend in the river where frogs croak at dusk. The abandoned shed leans to the north, Miller's Bridge sits to the west, and the deep river curls around to block any southern or eastern approach.",
        },
    ],
]

DUNGEON_1_MAP = [
    [
        {
            "name": "Sealed Tunnel",
            "description": "Rockfalls here leave only a narrow crawlspace. A jagged crawl squeezes east toward the Dusty Landing and a cracked slope drops south into the Twisting Hall, while collapsed stone seals the way north and west.",
        },
        {
            "name": "Dusty Landing",
            "description": "Loose gravel litters the floor where miners once gathered. The crawlspace leads back west to the Sealed Tunnel, stalagmites knife up to the east, and a greasy ramp slides south toward the Rat Warren; the ceiling presses too low to continue north.",
            "mobs": ["giant_rat"],
        },
        {
            "name": "Stalagmite Cluster",
            "description": "Jagged pillars create cramped lanes through the cavern. Lantern light glimmers on the Collapsed Entrance to the east, the Dusty Landing lies to the west, and a damp pit yawns south toward the Sunken Den; fallen spires choke off the northern crawl.",
            "search": {
                "dc": 12,
                "ability": "int",
                "success_text": "You pry apart two stalagmites and find a miner's scribbled map fragment.",
                "failure_text": "Every crevice looks the same in the dim torchlight.",
                "loot": ["ancient_coin"],
            },
        },
        {
            "name": "Collapsed Entrance",
            "description": "Splintered beams circle a collapsed shaft where a cracked warp stone glows amid the rubble. The rubble slope leads west toward the stalagmite cluster, a narrow chute dives east, and the Broken Cart chamber lies directly south. The warp stone hums with power for those returning to the village above.",
            "warp_description": "A chipped warp stone flickers here, still strong enough to return travelers to the village.",
            "travel_to": {"zone": "village", "start": VILLAGE_START},
        },
        {
            "name": "Rubble Chute",
            "description": "Fresh stones tumble occasionally from the sloped ceiling. The Collapsed Entrance rests to the west, the Echoing Gallery opens eastward, and pungent fungal growth spreads south into the grotto while unstable debris blocks any path north.",
        },
        {
            "name": "Echoing Gallery",
            "description": "Footsteps clap back in a chorus of hollow echoes. The rubble chute narrows to the west, the Dripping Alcove glistens to the east, and a cold underground brook courses south; the ceiling squeezes low to the north.",
        },
        {
            "name": "Dripping Alcove",
            "description": "Mineral-heavy droplets patter into a shallow basin. Echoing passages return west, slick stairs spiral south toward the lower levels, and the alcove dead-ends against sheer rock to the east and north.",
        },
    ],
    [
        {
            "name": "Twisting Hall",
            "description": "Passages coil like a knot, worn smooth by decades of traffic. The Sealed Tunnel lies to the north, vermin tunnels wriggle east toward the Rat Warren, and a rubble slide drops south into a collapsed shaft while the western wall remains sealed.",
            "mobs": ["giant_rat"],
        },
        {
            "name": "Rat Warren",
            "description": "Nests of frayed rope and cloth rustle with vermin. The Dusty Landing opens to the north, the Sunken Den reeks to the east, and a whispering junction lies south; gnawed stone limits movement back west to the twisting hall.",
            "mobs": ["giant_rat", "giant_rat"],
        },
        {
            "name": "Sunken Den",
            "description": "Moisture collects in a low pit surrounded by gnawed bones. The stalagmite cluster looms to the north, a broken ore cart lies to the east, goblin sentries muster to the south, and the rat warrens open to the west.",
        },
        {
            "name": "Broken Cart",
            "description": "A splintered ore cart lies on its side, spilling rusted tools. The collapsed entrance glows to the north, fungal terraces shimmer to the east, goblin fields extend south, and the sunken den rests to the west.",
        },
        {
            "name": "Fungus Grotto",
            "description": "Bioluminescent caps cast a ghostly glow over the cavern. The rubble chute feeds spores from the north, an underground brook courses east, a guard post waits to the south, and the broken cart chamber lies to the west.",
            "mobs": ["giant_rat"],
        },
        {
            "name": "Underground Brook",
            "description": "An icy stream carves a shallow trench through the stone. Echoes tumble from the gallery to the north, slick steps descend east, abandoned barracks huddle to the south, and the fungus grotto glows to the west.",
        },
        {
            "name": "Slick Steps",
            "description": "A carved stairway descends deeper, slick with condensation. The dripping alcove is just north, the underground brook winds to the west, and a shimmering pool lies directly south while crumbling rock blocks any path eastward.",
        },
    ],
    [
        {
            "name": "Collapsed Shaft",
            "description": "The remnants of a vertical shaft are choked with rubble. Twisting passages climb north, a whispering fork opens east, and a bone-strewn pit falls away to the south while the western wall remains buried.",
        },
        {
            "name": "Whispering Fork",
            "description": "Whispers ride the draft, hinting at unseen side passages. Rat tunnels emerge from the north, a goblin outpost stands to the east, collapsed dormitories stretch south, and the shaft rubble presses close to the west.",
        },
        {
            "name": "Goblin Outpost",
            "description": "Makeshift barricades guard a cluster of stolen crates. The sunken den growls to the north, fungal farms expand eastward, a makeshift shrine flickers to the south, and the whispering fork provides a western retreat.",
            "mobs": ["goblin", "goblin"],
        },
        {
            "name": "Fungal Farms",
            "description": "Rows of edible fungus grow in carefully scraped troughs. Goblin outposts bustle to the west, a guard post holds watch to the east, the overseer chamber looms south, and the broken cart room is only a stair north.",
            "mobs": ["goblin"],
        },
        {
            "name": "Guard Post",
            "description": "A pair of overturned barrels serve as a crude lookout. Spore fields lie to the west, abandoned barracks slump to the east, goblin farmers labor northward, and drills echo from the hall to the south.",
            "mobs": ["goblin"],
        },
        {
            "name": "Abandoned Barracks",
            "description": "Rotten bedrolls and broken spears lie scattered across the floor. The underground brook murmurs to the north, a shimmering pool gleams to the east, the crystal vein tunnels run south, and the guard post watches from the west.",
            "search": {
                "dc": 13,
                "ability": "wis",
                "success_text": "Beneath a cot you discover a goblin bugle wrapped in cloth.",
                "failure_text": "Only moldy blankets and stale air greet your search.",
                "loot": ["goblin_bugle"],
            },
        },
        {
            "name": "Shimmering Pool",
            "description": "Still water reflects the cavern roof like polished glass. Slick steps climb north, a vent shaft plunges south, and abandoned barracks lie to the west while crystal-studded walls block the eastern edge.",
        },
    ],
    [
        {
            "name": "Bone Pile",
            "description": "Heap of cracked bones crunch underfoot. Rubble passages climb north to the collapsed shaft, a ruined dormitory slumps east, and molten heat rises from a lava fissure to the south while the western face is sealed.",
            "mobs": ["kobold"],
        },
        {
            "name": "Collapsed Dormitory",
            "description": "Splintered bunks sag beneath a partial ceiling collapse. Whispering passages return north, a makeshift shrine glows to the east, a chasm edge yawns south, and the bone pile lies to the west.",
        },
        {
            "name": "Makeshift Shrine",
            "description": "Charred sigils mark a shrine to a forgotten subterranean spirit. Goblin sentries watch from the north, the overseer chamber stands eastward, a fortified guardroom waits to the south, and collapsed dormitories slump to the west.",
            "search": {
                "dc": 14,
                "ability": "wis",
                "success_text": "Behind the altar you uncover a pouch of ancient coins.",
                "failure_text": "The shrine offers only dust and unsettling whispers.",
                "loot": ["ancient_coin"],
            },
        },
        {
            "name": "Overseer Chamber",
            "description": "A carved desk and ledger hint at the mine's orderly past. Fungal farms line the northern balcony, the drill hall roars to the east, supply depots extend south, and the shrine's altar stands to the west.",
            "mobs": ["goblin"],
        },
        {
            "name": "Drill Hall",
            "description": "Rusted drills and chains litter this wide chamber. Guard posts hold the northern arch, a crystal vein sparkles to the east, a trophy chamber stands to the south, and the overseer chamber commands the west.",
            "mobs": ["kobold"],
        },
        {
            "name": "Crystal Vein",
            "description": "Chunks of quartz jut from the walls, catching stray light. Abandoned barracks loom to the north, a vent shaft gusts eastward, hidden workshops wait to the south, and the drill hall rattles to the west.",
        },
        {
            "name": "Vent Shaft",
            "description": "A narrow chimney breathes cool air from unseen depths. The shimmering pool lies to the north, the lower spiral plunges south, and crystal veins glitter to the west while the eastern wall remains sheer.",
        },
    ],
    [
        {
            "name": "Lava Fissure",
            "description": "A faint red glow issues from a crack radiating gentle heat. The bone pile rises to the north, a chasm edge stretches east, and molten rock bars any passage further south or west.",
        },
        {
            "name": "Chasm Edge",
            "description": "A sheer drop-off disappears into rumbling darkness. Collapsed dormitories lie to the north, a deep guardroom holds the eastern ledge, and the lava fissure glows to the west while the abyss blocks the southern path.",
        },
        {
            "name": "Deep Guardroom",
            "description": "Barricades of scavenged timber block an advance deeper inside. The makeshift shrine flickers northward, the supply depot stacks to the east, and the chasm edge yawns to the west while darkness falls away to the south.",
            "mobs": ["kobold", "kobold"],
        },
        {
            "name": "Supply Depot",
            "description": "Shelves of pilfered supplies are kept in meticulous order. Overseer chambers rise to the north, trophy racks gleam to the east, deep guardrooms stand to the west, and the floor drops sharply to the south.",
            "mobs": ["kobold"],
        },
        {
            "name": "Trophy Chamber",
            "description": "Tattered banners and trophies from surface raids hang proudly. Drill halls ring just north, a hidden workshop clatters to the east, and the supply depot borders to the west while the cavern wall seals the southern edge.",
            "search": {
                "dc": 15,
                "ability": "int",
                "success_text": "You catalogue the trophies and spot a gleaming ceremonial dagger hidden away.",
                "failure_text": "Your fingers brush dusty trophies but no hidden prizes.",
                "loot": ["kobold_sling"],
            },
        },
        {
            "name": "Hidden Workshop",
            "description": "Tools for trap making lie scattered across stone benches. Crystal veins shimmer to the north, the lower spiral descends eastward, and the trophy chamber sits to the west while the southern rock wall is unyielding.",
            "mobs": ["kobold"],
        },
        {
            "name": "Lower Spiral",
            "description": "A tight spiral stair descends into silent blackness. The vent shaft exhales cool air from the north, the hidden workshop adjoins to the west, and sheer stone seals the south and east.",
        },
    ],
]

DUNGEON_2_MAP = [
    [
        {
            "name": "Iridescent Approach",
            "description": "Rainbow motes drift through the humid entrance hall. Glittering runoff flows to the east, the Singing Cavern hums to the south, and jagged crystal walls prevent any retreat to the north or west.",
        },
        {
            "name": "Glittering Runoff",
            "description": "Streams of mineral-laden water shimmer like liquid glass. The approach opens west, shattered columns topple to the east, and a stair spirals south toward Echo Falls while crystal-crusted walls block the northern face.",
            "mobs": ["kobold"],
        },
        {
            "name": "Shattered Column",
            "description": "Crystal shards jut from a toppled pillar. Glittering runoff pools to the west, the Crystal Gate rises to the east, and the Kobold Watchpost waits down a ramp to the south while the ceiling presses low overhead.",
        },
        {
            "name": "Crystal Gate",
            "description": "A lattice of quartz bars blocks the path and glows softly. Shattered columns lie to the west, the prismatic vestibule beams to the east, and the gate itself seals the northern archway while the Crystal Gate Lattice bars passage south toward the Shimmer Forge.",
            "mobs": ["kobold"],
        },
        {
            "name": "Prismatic Vestibule",
            "description": "Spectral light spills from a pedestal where a faceted warp stone rotates slowly in mid-air. The Crystal Gate gleams to the west, a cliffside overlook stretches east, and gemcutters labor to the south while the cavern roof seals the north.",
            "warp_description": "The suspended warp stone pulses invitingly, ready to fold space back to Greyford Village.",
            "travel_to": {"zone": "village", "start": VILLAGE_START},
        },
        {
            "name": "Facet Overlook",
            "description": "A ledge overlooks a crystalline canyon humming with resonance. The vestibule shimmers to the west, luminous cradles glow to the east, and a maze of mirrors coils south while sheer drops guard the north.",
            "search": {
                "dc": 14,
                "ability": "int",
                "success_text": "You chip free a flawless crystal teardrop wedged in the rock.",
                "failure_text": "Your tools slip, sending shards tinkling into the abyss.",
                "loot": ["crystal_teardrop"],
            },
        },
        {
            "name": "Luminous Cradle",
            "description": "Nestled geodes emit a gentle violet glow. The overlook lies to the west, a fractured ramp angles eastward, and glowing barracks bustle to the south while the ceiling vaults high above.",
            "mobs": ["kobold"],
        },
        {
            "name": "Fractured Ramp",
            "description": "A sloping ramp splits, descending toward resonant caverns. Luminous nests lie to the west, the rune circle glows below to the south, and splintered stone denies travel north or east.",
        },
    ],
    [
        {
            "name": "Singing Cavern",
            "description": "Every step sets the crystals humming in delicate harmony. The iridescent approach opens north, Echo Falls roars to the east, and azure hollows widen to the south while the western wall remains untouched.",
            "search": {
                "dc": 13,
                "ability": "wis",
                "success_text": "Listening carefully, you find a hidden alcove containing a crystal teardrop.",
                "failure_text": "The echoes overwhelm your senses, masking any secrets.",
                "loot": ["crystal_teardrop"],
            },
        },
        {
            "name": "Echo Falls",
            "description": "A waterfall cascades through crystalline prisms, scattering light. Glittering runoff pours from the north, the Kobold Watchpost crouches to the east, and chittering warrens stretch south while the cascade forms a barrier to the west.",
        },
        {
            "name": "Kobold Watchpost",
            "description": "Kobolds have carved slits into the wall for hidden crossbows. Shattered columns rise to the north, the Shimmer Forge blazes to the east, moonstone chambers lie to the south, and Echo Falls thunders to the west.",
            "mobs": ["kobold", "kobold"],
        },
        {
            "name": "Shimmer Forge",
            "description": "Forges burn with blue fire, shaping crystal arrowheads. The Kobold Watchpost guards the western arch, Gemcutter's Bench hums to the east, icy crevasses sink to the south, and the Crystal Gate Lattice looms along the northern doorway, usually sealed against intruders.",
            "mobs": ["kobold"],
        },
        {
            "name": "Gemcutter's Bench",
            "description": "Polishing wheels spin, leaving glittering dust across the stone. The prismatic vestibule hovers to the north, the crystal maze reflects to the east, guardian constructs muster to the south, and the forge's sparks fly to the west.",
            "search": {
                "dc": 15,
                "ability": "int",
                "success_text": "You collect a pouch of finely cut shards before the dust settles.",
                "failure_text": "Your hands come away empty but sparkling with grit.",
                "loot": ["crystal_teardrop"],
            },
        },
        {
            "name": "Crystal Maze",
            "description": "Mirrored walls create bewildering reflections of yourself. The overlook beckons north, glowing barracks lie to the east, resonant vaults hum to the south, and gemcutters labor to the west.",
        },
        {
            "name": "Glowing Barracks",
            "description": "Sleeping pallets surround lanterns filled with glowing moss. Luminous cradles rest to the north, the rune circle shimmers to the east, crystal nurseries lie to the south, and the maze's reflections are to the west.",
            "mobs": ["kobold"],
        },
        {
            "name": "Rune Circle",
            "description": "A circle of runes thrums with latent power. The fractured ramp descends from the north, the veiled passage winds south, and glowing barracks anchor the western edge while cracked stone blocks the eastern cliff.",
        },
    ],
    [
        {
            "name": "Azure Hollow",
            "description": "Blue quartz formations twist like frozen waves. Singing Cavern harmonics spill from the north, chittering warrens coil to the east, and violet depths plunge south while the western wall gleams unbroken.",
        },
        {
            "name": "Chittering Warrens",
            "description": "Narrow burrows ring with the chatter of unseen kobolds. Echo Falls echoes to the north, Moonstone Chamber glows to the east, Hoard Gallery stretches south, and Azure Hollow opens west.",
            "mobs": ["kobold", "kobold"],
        },
        {
            "name": "Moonstone Chamber",
            "description": "Soft white light spills from polished moonstones embedded in the floor. The Kobold Watchpost stands to the north, an icy crevasse chills the east, Geode Sanctum gleams to the south, and the warrens chatter to the west.",
            "search": {
                "dc": 14,
                "ability": "wis",
                "success_text": "You locate a concealed niche holding an untouched moonstone shard.",
                "failure_text": "Reflections play tricks on your eyes, hiding any clues.",
                "loot": ["crystal_teardrop"],
            },
        },
        {
            "name": "Icy Crevasse",
            "description": "Cold vapors billow from a deep crack rimed with frost. The Shimmer Forge smolders to the north, guardian constructs await to the east, the Glinting Archive lies south, and moonstones glow to the west.",
        },
        {
            "name": "Guardian Nexus",
            "description": "Crystal sentries loom over a dais carved with warding sigils. Gemcutter's Bench is to the north, the resonant vault hums eastward, the Crystal Throne commands the south, and icy fissures chill the west.",
            "mobs": ["kobold"],
        },
        {
            "name": "Resonant Vault",
            "description": "The air vibrates with a constant low hum that prickles your teeth. The crystal maze reflects to the north, crystal nurseries brood to the east, the ritual pool lies to the south, and guardian sentries stand to the west.",
            "mobs": ["goblin"],
        },
        {
            "name": "Crystal Nursery",
            "description": "Small geodes cradle faintly glowing eggs. Glowing barracks bustle to the north, the veiled passage drifts east, darkened faults rumble south, and the resonant vault hums to the west.",
            "mobs": ["kobold"],
        },
        {
            "name": "Veiled Passage",
            "description": "Veils of hanging crystals sway gently in the draft. The rune circle is to the north, the collapsed escape slumps south, and crystal nurseries shimmer to the west while an abyss blocks the eastern rim.",
        },
    ],
    [
        {
            "name": "Violet Depth",
            "description": "Deep amethyst crystals pulse with a slow, steady light. Azure hollow corridors rise to the north, hoarded spoils lie to the east, and a sheer drop bars travel further south or west.",
        },
        {
            "name": "Hoard Gallery",
            "description": "Neat piles of sorted gemstones testify to recent raids. Chittering warrens open to the north, the Geode Sanctum glitters to the east, and violet depths border the west while the floor falls away to the south.",
            "mobs": ["kobold"],
        },
        {
            "name": "Geode Sanctum",
            "description": "A titanic geode splits open, revealing a hollow filled with riches. Moonstone chambers gleam to the north, the Glinting Archive stores records to the east, and the Hoard Gallery rests to the west while the southern wall remains sealed.",
            "search": {
                "dc": 16,
                "ability": "int",
                "success_text": "You pry loose a rare heartstone from the geode's core.",
                "failure_text": "The crystalline lattice refuses to part under your efforts.",
                "loot": ["crystal_heartstone"],
            },
        },
        {
            "name": "Glinting Archive",
            "description": "Shelves of crystal tablets refract the light into rainbow sigils. The Icy Crevasse chills the north, the Crystal Throne commands the east, and the Geode Sanctum lines the west while silence hangs over the southern descent.",
        },
        {
            "name": "Crystal Throne",
            "description": "An ornate seat of quartz watches over the chamber like a judge. Guardian sentries stand to the north, the ritual pool mirrors to the east, the Glinting Archive holds records to the west, and basalt walls bar any southern route.",
            "mobs": ["kobold", "kobold"],
        },
        {
            "name": "Ritual Pool",
            "description": "A still pool mirrors the ceiling perfectly despite the cavern's breeze. The resonant vault hums to the north, the darkened fault rumbles to the east, and the Crystal Throne gleams to the west while underground currents seal the south.",
        },
        {
            "name": "Darkened Fault",
            "description": "Shadowed cracks hint at deeper tunnels still unexplored. Crystal nurseries line the north, the collapsed escape slumps to the east, and the ritual pool shines to the west while the ground fractures into impassable darkness southward.",
            "mobs": ["goblin"],
        },
        {
            "name": "Collapsed Escape",
            "description": "A former exit lies sealed by a recent cave-in. The veiled passage whispers to the north, the darkened fault borders west, and shattered rock walls block all hope of moving south or east.",
        },
    ],
]


def make_world(name, tile_map, start):
    height = len(tile_map)
    width = len(tile_map[0]) if height else 0
    return {"name": name, "map": tile_map, "start": start, "width": width, "height": height}


WORLDS = {
    "village": make_world("Greyford Village", VILLAGE_MAP, VILLAGE_START),
    "dungeon_1": make_world("Old Mine", DUNGEON_1_MAP, DUNGEON_1_START),
    "dungeon_2": make_world("Crystal Depths", DUNGEON_2_MAP, DUNGEON_2_START),
}

DIRECTION_VECTORS = {
    "north": (0, -1),
    "south": (0, 1),
    "west": (-1, 0),
    "east": (1, 0),
}

DOOR_DEFINITIONS = {
    "village_town_hall_service": {
        "name": "Town Hall Service Door",
        "description": "Thick oak panels banded with iron link the town hall to the storage barn.",
        "initial_state": "closed",
        "endpoints": [
            {"zone": "village", "coords": (2, 1), "direction": "east"},
            {"zone": "village", "coords": (3, 1), "direction": "west"},
        ],
    },
    "crystal_gate_lattice": {
        "name": "Crystal Gate Lattice",
        "description": "A ribbed lattice of crystal bars can seal the passage between the gate and the watchpost.",
        "initial_state": "closed",
        "endpoints": [
            {"zone": "dungeon_2", "coords": (3, 0), "direction": "south"},
            {"zone": "dungeon_2", "coords": (3, 1), "direction": "north"},
        ],
    },
}

DOORS = {}
DOOR_ENDPOINT_LOOKUP = {}


def initialize_doors():
    for door_id, spec in DOOR_DEFINITIONS.items():
        endpoints = []
        for endpoint in spec.get("endpoints", []):
            coords = tuple(endpoint["coords"])
            record = {
                "zone": endpoint["zone"],
                "coords": coords,
                "direction": endpoint["direction"],
            }
            endpoints.append(record)
            DOOR_ENDPOINT_LOOKUP[(record["zone"], coords[0], coords[1], record["direction"])] = door_id
        DOORS[door_id] = {
            "id": door_id,
            "name": spec["name"],
            "description": spec["description"],
            "state": spec.get("initial_state", "closed"),
            "endpoints": endpoints,
        }


initialize_doors()

ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")
DEFAULT_RACE = "Human"
DEFAULT_CLASS = "Fighter"
DEFAULT_WEAPON_KEY = "unarmed"
PROFICIENCY_BONUS = 2  # SRD level 1 characters

# --- Global action timing ---
BASE_ACTION_COOLDOWN = 1.0  # baseline delay between rate-limited actions
MIN_ACTION_MULTIPLIER = 0.75
MAX_ACTION_MULTIPLIER = 1.25

WEAPONS = {
    "unarmed": {"name": "Unarmed Strike", "dice": (1, 1), "ability": "str", "damage_type": "bludgeoning"},
    "longsword": {"name": "Longsword", "dice": (1, 8), "ability": "str", "damage_type": "slashing"},
    "battleaxe": {"name": "Battleaxe", "dice": (1, 8), "ability": "str", "damage_type": "slashing"},
    "spear": {"name": "Spear", "dice": (1, 6), "ability": "str", "damage_type": "piercing"},
    "shortsword": {"name": "Shortsword", "dice": (1, 6), "ability": "dex", "damage_type": "piercing"},
    "dagger": {"name": "Dagger", "dice": (1, 4), "ability": "dex", "damage_type": "piercing"},
    "shortbow": {"name": "Shortbow", "dice": (1, 6), "ability": "dex", "damage_type": "piercing"},
    "mace": {"name": "Mace", "dice": (1, 6), "ability": "str", "damage_type": "bludgeoning"},
    "warhammer": {"name": "Warhammer", "dice": (1, 8), "ability": "str", "damage_type": "bludgeoning"},
    "arcane_bolt": {"name": "Arcane Bolt", "dice": (1, 8), "ability": "int", "damage_type": "force"},
    "sacred_flame": {"name": "Sacred Flame", "dice": (1, 8), "ability": "wis", "damage_type": "radiant"},
}

GENERAL_ITEMS = {
    "rat_tail": {
        "name": "Rat Tail Token",
        "description": "A grisly token proving your victory over a giant rat.",
        "rarity": "common",
    },
    "goblin_bugle": {
        "name": "Goblin Bugle",
        "description": "A dented horn used to rally goblins. It no longer sounds quite right.",
        "rarity": "common",
    },
    "kobold_sling": {
        "name": "Kobold Sling",
        "description": "A worn leather sling sized for small hands. Still functional.",
        "rarity": "common",
    },
    "forgotten_brooch": {
        "name": "Forgotten Brooch",
        "description": "A tarnished family brooch depicting the village crest.",
        "rarity": "common",
    },
    "ancient_coin": {
        "name": "Ancient Mine Coin",
        "description": "An old silver coin stamped with a miner's pick emblem.",
        "rarity": "uncommon",
    },
    "crystal_teardrop": {
        "name": "Crystal Teardrop",
        "description": "A flawless droplet of crystal that glows faintly when held.",
        "rarity": "uncommon",
    },
    "crystal_heartstone": {
        "name": "Crystal Heartstone",
        "description": "A rare heartstone that pulses with inner light from the crystal depths.",
        "rarity": "rare",
    },
}

MOB_TEMPLATES = {
    "giant_rat": {
        "name": "Giant Rat",
        "ac": 12,
        "hp": 7,
        "hp_dice": "2d6",
        "speed": 30,
        "abilities": {"str": 7, "dex": 15, "con": 11, "int": 2, "wis": 10, "cha": 4},
        "attack_bonus": 4,
        "damage": {"dice": (1, 4), "bonus": 2, "type": "piercing"},
        "attack_interval": 2.5,
        "initiative": 12,
        "behaviour_type": "aggressive",
        "xp": 25,
        "initial_spawns": 2,
        "gold_range": (1, 6),
        "loot": [("rat_tail", 0.6)],
        "description": "A sewer-dwelling rat the size of a hound, eyes gleaming with hunger.",
    },
    "goblin": {
        "name": "Goblin",
        "ac": 15,
        "hp": 7,
        "hp_dice": "2d6",
        "speed": 30,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "attack_bonus": 4,
        "damage": {"dice": (1, 6), "bonus": 2, "type": "slashing"},
        "attack_interval": 3.0,
        "initiative": 11,
        "behaviour_type": "defensive",
        "xp": 50,
        "initial_spawns": 2,
        "gold_range": (2, 12),
        "loot": [("goblin_bugle", 0.4), ("dagger", 0.2)],
        "description": "A wiry goblin clutching rusted blades and muttering in guttural tones.",
    },
    "kobold": {
        "name": "Kobold",
        "ac": 12,
        "hp": 5,
        "hp_dice": "2d6-2",
        "speed": 30,
        "abilities": {"str": 7, "dex": 15, "con": 9, "int": 8, "wis": 7, "cha": 8},
        "attack_bonus": 4,
        "damage": {"dice": (1, 4), "bonus": 2, "type": "piercing"},
        "attack_interval": 2.8,
        "initiative": 13,
        "behaviour_type": "aggressive",
        "xp": 25,
        "initial_spawns": 2,
        "gold_range": (1, 8),
        "loot": [("kobold_sling", 0.5)],
        "description": "A scaly kobold scouting the area with wary, darting eyes.",
    },
    "npc_elder_mara": {
        "name": "Elder Mara",
        "ac": 13,
        "hp": 24,
        "speed": 25,
        "abilities": {"str": 9, "dex": 11, "con": 12, "int": 14, "wis": 15, "cha": 16},
        "attack_bonus": 4,
        "damage": {"dice": (1, 6), "bonus": 2, "type": "bludgeoning"},
        "attack_interval": 3.6,
        "initiative": 10,
        "behaviour_type": "defensive",
        "xp": 0,
        "gold_range": (0, 0),
        "loot": [],
        "description": "The village elder, leaning on a rune-carved staff yet keen-eyed and alert.",
    },
}

NPC_TEMPLATES = {
    "elder_mara": {
        "name": "Elder Mara",
        "mob_template": "npc_elder_mara",
        "zone": "village",
        "coords": (2, 2),
        "bio": (
            "A seasoned rune-keeper who shepherds Dawnfell Village and remembers the warpstone routes of old."
        ),
        "personality": "warm, patient, and quietly amused by youthful bravado",
        "facts": [
            "Dawnfell Village was rebuilt atop an abandoned teleport nexus, and the warp stones reawakened only a generation ago.",
            "Merchants and adventurers gather in the Village Square before venturing toward the mines or river routes.",
            "The town hall's clerks can mark safe paths to the Sunken Stair if you ask respectfully.",
            "Warp stones require calm focus—touch the rune and picture the destination to travel.",
            "The Old Mine Entrance houses the nearest warp stone leading to the first dungeon."
        ],
        "secret_fact": (
            "A hidden warp stone shard rests beneath the shrine steps; six sincere visits awaken it as a shortcut to the Sunken Stair."
        ),
        "aliases": ["mara", "elder", "elder mara", "elder_mara"],
    }
}

NPC_SECRET_THRESHOLD = 5
NPC_MODEL_NAME = os.environ.get("OPENAI_NPC_MODEL", "gpt-4o-mini")

SPELLS = {
    "magic_missile": {
        "name": "Magic Missile",
        "classes": ["Wizard"],
        "type": "attack",
        "description": "Launch three darts of force that automatically strike a creature for 3d4 + 3 force damage.",
        "ability": "int",
        "target": "enemy",
        "damage": {"dice": (3, 4), "bonus": 3, "damage_type": "force", "auto_hit": True},
        "cooldown": 8,
    },
    "burning_hands": {
        "name": "Burning Hands",
        "classes": ["Wizard"],
        "type": "attack",
        "description": "A sheet of flame erupts for 3d6 fire damage to a creature in front of you.",
        "ability": "int",
        "target": "enemy",
        "damage": {"dice": (3, 6), "damage_type": "fire"},
        "cooldown": 10,
    },
    "enhance_agility": {
        "name": "Enhance Ability (Cat's Grace)",
        "classes": ["Wizard", "Cleric"],
        "type": "buff",
        "description": "Bestow feline agility, granting +2 DEX modifier for 2 minutes.",
        "ability": "int",
        "target": "ally",
        "effect": {
            "key": "enhance_agility",
            "modifiers": {"ability_mods": {"dex": 2}},
            "duration": 120,
            "description": "+2 to Dexterity-based checks and defenses.",
        },
        "cooldown": 30,
    },
    "cure_wounds": {
        "name": "Cure Wounds",
        "classes": ["Cleric"],
        "type": "heal",
        "description": "Channel healing energy to restore 1d8 + WIS modifier hit points.",
        "ability": "wis",
        "target": "ally",
        "heal": {"dice": (1, 8), "add_ability_mod": True},
        "cooldown": 10,
    },
    "shield_of_faith": {
        "name": "Shield of Faith",
        "classes": ["Cleric"],
        "type": "buff",
        "description": "A shimmering field surrounds a creature, granting +2 AC for 2 minutes.",
        "ability": "wis",
        "target": "ally",
        "effect": {
            "key": "shield_of_faith",
            "modifiers": {"ac": 2},
            "duration": 120,
            "description": "+2 AC from radiant warding.",
        },
        "cooldown": 30,
    },
    "bless": {
        "name": "Bless",
        "classes": ["Cleric"],
        "type": "buff",
        "description": "You bless a creature, adding 1d4 to its attack rolls for 2 minutes.",
        "ability": "wis",
        "target": "ally",
        "effect": {
            "key": "bless",
            "modifiers": {
                "attack_roll_bonus": {"dice": (1, 4), "label": "Bless"}
            },
            "duration": 120,
            "description": "+1d4 on attack rolls.",
        },
        "cooldown": 30,
    },
    "second_wind": {
        "name": "Second Wind",
        "classes": ["Fighter"],
        "type": "heal",
        "description": "Draw on stamina to heal 1d10 + your level hit points.",
        "ability": "con",
        "target": "self",
        "heal": {"dice": (1, 10), "add_level": True},
        "cooldown": 60,
    },
    "shadow_veil": {
        "name": "Shadow Veil",
        "classes": ["Rogue"],
        "type": "buff",
        "description": "Wrap yourself in shadows, gaining +1 AC and +1 DEX modifier for 1 minute.",
        "ability": "dex",
        "target": "self",
        "effect": {
            "key": "shadow_veil",
            "modifiers": {"ac": 1, "ability_mods": {"dex": 1}},
            "duration": 60,
            "description": "Shrouded in shadow, harder to hit and quicker.",
        },
        "cooldown": 45,
    },
    "keen_eye": {
        "name": "Keen Eye",
        "classes": ["Rogue"],
        "type": "utility",
        "description": "Survey nearby paths to learn who lurks just beyond sight.",
        "ability": "wis",
        "target": "none",
        "cooldown": 30,
    },
}

CLASS_SPELLS = {
    "Wizard": ["magic_missile", "burning_hands", "enhance_agility"],
    "Cleric": ["cure_wounds", "shield_of_faith", "bless", "enhance_agility"],
    "Fighter": ["second_wind"],
    "Rogue": ["shadow_veil", "keen_eye"],
}

RACES = {
    "Human": {"modifiers": {ability: 1 for ability in ABILITY_KEYS}},
    "Elf": {"modifiers": {"dex": 2}},
    "Dwarf": {"modifiers": {"con": 2}},
    "Halfling": {"modifiers": {"dex": 2}},
}

CLASSES = {
    "Fighter": {
        "hit_die": 10,
        "primary_ability": "str",
        "armor_bonus": 2,
        "starting_weapons": ["longsword", "battleaxe", "spear", "dagger"],
    },
    "Rogue": {
        "hit_die": 8,
        "primary_ability": "dex",
        "armor_bonus": 1,
        "starting_weapons": ["shortsword", "dagger", "shortbow"],
    },
    "Wizard": {
        "hit_die": 6,
        "primary_ability": "int",
        "armor_bonus": 0,
        "starting_weapons": ["arcane_bolt", "dagger"],
    },
    "Cleric": {
        "hit_die": 8,
        "primary_ability": "wis",
        "armor_bonus": 1,
        "starting_weapons": ["mace", "warhammer", "sacred_flame"],
    },
}

RACE_OPTIONS = list(RACES.keys())
CLASS_OPTIONS = list(CLASSES.keys())


def normalize_choice(value, valid, default_value):
    if not value:
        return default_value
    value = value.strip()
    for key in valid.keys():
        if key.lower() == value.lower():
            return key
    return default_value


def get_weapon(key):
    if not key:
        return WEAPONS[DEFAULT_WEAPON_KEY]
    return WEAPONS.get(key, WEAPONS[DEFAULT_WEAPON_KEY])


def format_weapon_payload(key):
    weapon = get_weapon(key)
    dice = weapon.get("dice") or (1, 1)
    return {
        "key": key or DEFAULT_WEAPON_KEY,
        "name": weapon["name"],
        "dice": dice,
        "dice_label": format_dice(dice),
        "ability": weapon.get("ability", "str"),
        "damage_type": weapon.get("damage_type", "physical"),
    }


def get_spell(key):
    if not key:
        return None
    return SPELLS.get(key)


def get_spells_for_class(class_name):
    canonical = normalize_choice(class_name, CLASSES, DEFAULT_CLASS)
    return list(dict.fromkeys(CLASS_SPELLS.get(canonical, [])))


def default_inventory_for_class(class_name):
    char_class = normalize_choice(class_name, CLASSES, DEFAULT_CLASS)
    return list(dict.fromkeys(CLASSES[char_class].get("starting_weapons", []) + [DEFAULT_WEAPON_KEY]))


def serialize_inventory(inventory):
    return json.dumps(inventory or [])


def deserialize_inventory(payload):
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    try:
        data = json.loads(payload)
        if isinstance(data, list):
            return [item for item in data if item in WEAPONS]
    except (json.JSONDecodeError, TypeError):
        pass
    return [part.strip() for part in str(payload).split(",") if part.strip() in WEAPONS]


def serialize_items(items):
    return json.dumps(items or [])


def deserialize_items(payload):
    if not payload:
        return []
    if isinstance(payload, list):
        return [item for item in payload if item in GENERAL_ITEMS]
    try:
        data = json.loads(payload)
        if isinstance(data, list):
            return [item for item in data if item in GENERAL_ITEMS]
    except (json.JSONDecodeError, TypeError):
        pass
    return [part.strip() for part in str(payload).split(",") if part.strip() in GENERAL_ITEMS]


def format_item_payload(key):
    item = GENERAL_ITEMS.get(key)
    if not item:
        return None
    return {
        "key": key,
        "name": item.get("name", key.title()),
        "description": item.get("description", ""),
        "rarity": item.get("rarity", "common"),
    }


def ensure_equipped_weapon(equipped_key, inventory):
    if equipped_key in inventory:
        return equipped_key
    if inventory:
        return inventory[0]
    return DEFAULT_WEAPON_KEY


def roll_4d6_drop_lowest():
    rolls = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
    return sum(rolls[:3])


def generate_base_scores():
    return {ability: roll_4d6_drop_lowest() for ability in ABILITY_KEYS}


def apply_race_modifiers(scores, race_name):
    race = RACES.get(race_name, RACES[DEFAULT_RACE])
    mods = race.get("modifiers", {})
    modified = dict(scores)
    for ability, bonus in mods.items():
        modified[ability] = modified.get(ability, 10) + bonus
    return modified


def ability_modifier(score):
    return (score - 10) // 2


def format_dice(dice):
    return f"{dice[0]}d{dice[1]}"


def build_character_sheet(race_choice, class_choice, base_scores=None):
    race = normalize_choice(race_choice, RACES, DEFAULT_RACE)
    char_class = normalize_choice(class_choice, CLASSES, DEFAULT_CLASS)
    if base_scores:
        base_scores = {ability: int(base_scores.get(ability, 10)) for ability in ABILITY_KEYS}
    else:
        base_scores = generate_base_scores()
    ability_scores = apply_race_modifiers(base_scores, race)
    ability_mods = {ability: ability_modifier(score) for ability, score in ability_scores.items()}
    class_data = CLASSES[char_class]
    inventory = default_inventory_for_class(char_class)
    equipped_weapon = inventory[0] if inventory else DEFAULT_WEAPON_KEY
    weapon_payload = format_weapon_payload(equipped_weapon)
    attack_ability = weapon_payload["ability"] or class_data["primary_ability"]
    proficiency = PROFICIENCY_BONUS
    max_hp = max(class_data["hit_die"] + ability_mods["con"], 1)
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    attack_bonus = ability_mods[attack_ability] + proficiency
    return {
        "race": race,
        "char_class": char_class,
        "level": 1,
        "abilities": ability_scores,
        "ability_mods": ability_mods,
        "max_hp": max_hp,
        "current_hp": max_hp,
        "ac": ac,
        "proficiency": proficiency,
        "weapon": weapon_payload,
        "attack_bonus": attack_bonus,
        "attack_ability": attack_ability,
        "inventory": inventory,
        "equipped_weapon": equipped_weapon,
    }


def derive_character_from_record(record):
    race = normalize_choice(record.get("race"), RACES, DEFAULT_RACE)
    char_class = normalize_choice(record.get("char_class"), CLASSES, DEFAULT_CLASS)
    class_data = CLASSES[char_class]
    abilities = {ability: record.get(f"{ability}_score") or 10 for ability in ABILITY_KEYS}
    ability_mods = {ability: ability_modifier(score) for ability, score in abilities.items()}
    proficiency = PROFICIENCY_BONUS
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    max_hp = record.get("hp") or max(class_data["hit_die"] + ability_mods["con"], 1)
    inventory = deserialize_inventory(record.get("weapon_inventory"))
    if not inventory:
        inventory = default_inventory_for_class(char_class)
    equipped_key = ensure_equipped_weapon(record.get("equipped_weapon"), inventory)
    weapon = format_weapon_payload(equipped_key)
    attack_ability = weapon["ability"] or class_data["primary_ability"]
    attack_bonus = ability_mods[attack_ability] + proficiency
    items = deserialize_items(record.get("item_inventory"))
    return {
        "race": race,
        "char_class": char_class,
        "level": record.get("level") or 1,
        "abilities": abilities,
        "ability_mods": ability_mods,
        "max_hp": max_hp,
        "ac": ac,
        "proficiency": proficiency,
        "weapon": weapon,
        "attack_bonus": attack_bonus,
        "attack_ability": attack_ability,
        "inventory": inventory,
        "equipped_weapon": weapon["key"],
        "xp": record.get("xp") or 0,
        "gold": record.get("gold") or 0,
        "items": items,
    }


def clamp_hp(value, max_hp):
    if value is None:
        return max_hp
    return max(0, min(int(value), max_hp))


def roll_weapon_damage(weapon, ability_mod, crit=False, bonus_damage=0):
    dice_count, dice_size = weapon["dice"]
    total_dice = dice_count * (2 if crit else 1)
    total = sum(random.randint(1, dice_size) for _ in range(total_dice)) + ability_mod + bonus_damage
    return max(1, total)


def roll_dice(dice):
    if not dice:
        return 0
    count, size = dice
    return sum(random.randint(1, size) for _ in range(max(0, count)))

# --- DB helpers ---

def _table_exists(cursor, table):
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def _column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            name TEXT UNIQUE NOT NULL,
            race TEXT,
            char_class TEXT,
            level INTEGER DEFAULT 1,
            str_score INTEGER,
            dex_score INTEGER,
            con_score INTEGER,
            int_score INTEGER,
            wis_score INTEGER,
            cha_score INTEGER,
            hp INTEGER,
            current_hp INTEGER,
            equipped_weapon TEXT,
            weapon_inventory TEXT,
            xp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 0,
            item_inventory TEXT,
            bio TEXT,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
        """
    )

    for column, default in (
        ("bio", ""),
        ("description", ""),
    ):
        if not _column_exists(c, "characters", column):
            default_literal = "''" if default == "" else f"'{default}'"
            c.execute(
                f"ALTER TABLE characters ADD COLUMN {column} TEXT DEFAULT {default_literal}"
            )

    if _table_exists(c, "users"):
        c.execute("SELECT COUNT(*) FROM accounts")
        if (c.fetchone() or [0])[0] == 0:
            c.execute("SELECT * FROM users")
            legacy_rows = c.fetchall()
            for row in legacy_rows:
                username = row["username"]
                password_hash = row["password_hash"]
                c.execute(
                    "INSERT OR IGNORE INTO accounts (username, password_hash) VALUES (?, ?)",
                    (username, password_hash),
                )
                c.execute("SELECT id FROM accounts WHERE username = ?", (username,))
                account_row = c.fetchone()
                if not account_row:
                    continue
                account_id = account_row["id"]
                char_name = username
                inventory = deserialize_inventory(row["weapon_inventory"])
                if not inventory:
                    inventory = default_inventory_for_class(row["char_class"] or DEFAULT_CLASS)
                equipped_weapon = ensure_equipped_weapon(row["equipped_weapon"], inventory)
                items = deserialize_items(row["item_inventory"])
                c.execute(
                    """
                    INSERT OR IGNORE INTO characters (
                        account_id, name, race, char_class, level,
                        str_score, dex_score, con_score, int_score, wis_score, cha_score,
                        hp, current_hp, equipped_weapon, weapon_inventory,
                        xp, gold, item_inventory, bio, description
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        char_name,
                        row["race"] or DEFAULT_RACE,
                        row["char_class"] or DEFAULT_CLASS,
                        row["level"] or 1,
                        row["str_score"] or 10,
                        row["dex_score"] or 10,
                        row["con_score"] or 10,
                        row["int_score"] or 10,
                        row["wis_score"] or 10,
                        row["cha_score"] or 10,
                        row["hp"] or 10,
                        row["current_hp"] or row["hp"] or 10,
                        equipped_weapon,
                        serialize_inventory(inventory),
                        row["xp"] or 0,
                        row["gold"] or 0,
                        serialize_items(items),
                        "",
                        "",
                    ),
                )

    conn.commit()
    conn.close()
    if not mobs:
        spawn_initial_mobs()


def get_account(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_account_by_id(account_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def create_account(username, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    password_hash = generate_password_hash(password)
    c.execute(
        "INSERT INTO accounts (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    conn.commit()
    conn.close()


def count_account_characters(account_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM characters WHERE account_id = ?", (account_id,))
    count = c.fetchone()[0]
    conn.close()
    return count


def get_account_characters(account_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT * FROM characters WHERE account_id = ? ORDER BY created_at",
        (account_id,),
    )
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def get_character_by_id(character_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM characters WHERE id = ?", (character_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_character_by_name(name):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM characters WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def create_character(
    account_id,
    name,
    race_choice,
    class_choice,
    ability_scores,
    bio="",
    description="",
):
    sheet = build_character_sheet(race_choice, class_choice, base_scores=ability_scores)
    ability_values = sheet["abilities"]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO characters (
            account_id, name, race, char_class, level,
            str_score, dex_score, con_score, int_score, wis_score, cha_score,
            hp, current_hp, equipped_weapon, weapon_inventory,
            xp, gold, item_inventory, bio, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            name,
            sheet["race"],
            sheet["char_class"],
            sheet["level"],
            ability_values["str"],
            ability_values["dex"],
            ability_values["con"],
            ability_values["int"],
            ability_values["wis"],
            ability_values["cha"],
            sheet["max_hp"],
            sheet["current_hp"],
            sheet["equipped_weapon"],
            serialize_inventory(sheet["inventory"]),
            0,
            0,
            serialize_items([]),
            bio or "",
            description or "",
        ),
    )
    conn.commit()
    conn.close()


def delete_character(account_id, character_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "DELETE FROM characters WHERE id = ? AND account_id = ?",
        (character_id, account_id),
    )
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def update_character_current_hp(character_id, hp):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE characters SET current_hp = ? WHERE id = ?",
        (hp, character_id),
    )
    conn.commit()
    conn.close()


def update_character_equipped_weapon(character_id, weapon_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE characters SET equipped_weapon = ? WHERE id = ?",
        (weapon_key, character_id),
    )
    conn.commit()
    conn.close()


def update_character_weapon_inventory(character_id, inventory):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE characters SET weapon_inventory = ? WHERE id = ?",
        (serialize_inventory(inventory), character_id),
    )
    conn.commit()
    conn.close()


def update_character_gold(character_id, gold):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE characters SET gold = ? WHERE id = ?", (gold, character_id))
    conn.commit()
    conn.close()


def update_character_xp(character_id, xp):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE characters SET xp = ? WHERE id = ?", (xp, character_id))
    conn.commit()
    conn.close()


def update_character_items(character_id, items):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE characters SET item_inventory = ? WHERE id = ?",
        (serialize_items(items), character_id),
    )
    conn.commit()
    conn.close()
# --- In-memory game state (per container, MVP only) ---
# players[character_name] = {
#     "sid": socket_id,
#     "character_id": int,
#     "account_id": int,
#     "name": str,
#     "x": int,
#     "y": int,
#     "hp": int,
#     "max_hp": int,
#     "ac": int,
#     "race": str,
#     "char_class": str,
#     "level": int,
#     "abilities": dict,
#     "ability_mods": dict,
#     "weapon": dict,
#     "attack_bonus": int,
#     "attack_ability": str,
#     "proficiency": int,
#     "inventory": list[str],
#     "equipped_weapon": str,
# }
players = {}
mobs = {}
npcs = {}
npc_lookup_by_id = {}
npc_conversations = {}
_openai_client: Optional[object] = None
_openai_mode: Optional[str] = None


def compute_action_multiplier(initiative):
    """Convert initiative into a small speed boost/penalty."""
    try:
        value = float(initiative)
    except (TypeError, ValueError):
        value = 10.0
    # Clamp initiative influence to keep multipliers within the desired range.
    delta = max(-5.0, min(5.0, value - 10.0))
    # Each 5 points away from 10 shifts the multiplier by ~0.25.
    adjustment = (delta / 5.0) * 0.25
    multiplier = 1.0 + adjustment
    return max(MIN_ACTION_MULTIPLIER, min(MAX_ACTION_MULTIPLIER, multiplier))


def update_player_action_timing(player):
    """Refresh derived initiative and cooldown values for the player."""
    base_initiative = player.get("base_initiative", 10)
    dex_bonus = player.get("ability_mods", {}).get("dex", 0)
    total_initiative = max(1.0, base_initiative + dex_bonus)
    multiplier = compute_action_multiplier(total_initiative)
    player["initiative"] = total_initiative
    player["action_cooldown"] = BASE_ACTION_COOLDOWN / multiplier
    player.setdefault("last_action_ts", 0)


def get_player_action_cooldown_remaining(player):
    cooldown = player.get("action_cooldown", BASE_ACTION_COOLDOWN)
    last_ts = player.get("last_action_ts", 0)
    remaining = cooldown - (time.time() - last_ts)
    return max(0.0, remaining)


def send_action_denied(username, player, remaining):
    payload = {"reason": "cooldown", "remaining": round(remaining, 2)}
    socketio.emit("action_denied", payload, to=player["sid"])
    notify_player(username, f"You must wait {remaining:.1f}s before acting again.")


def check_player_action_gate(username):
    """Server-side guard that enforces the global action cooldown per player."""
    player = players.get(username)
    if not player:
        return False
    recalculate_player_stats(player)
    remaining = get_player_action_cooldown_remaining(player)
    if remaining > 0:
        send_action_denied(username, player, remaining)
        return False
    return True


def mark_player_action(player):
    player["last_action_ts"] = time.time()
room_loot = {}
_mob_counter = 0
_loot_counter = 0


def get_world(zone):
    return WORLDS.get(zone, WORLDS[DEFAULT_ZONE])


def get_world_dimensions(zone):
    world = get_world(zone)
    return world["width"], world["height"]


def get_world_map(zone):
    return get_world(zone)["map"]


def get_world_start(zone):
    return get_world(zone)["start"]


def get_door_id(zone, x, y, direction):
    return DOOR_ENDPOINT_LOOKUP.get((zone, x, y, direction))


def is_door_open(door_id):
    door = DOORS.get(door_id)
    if not door:
        return True
    return door.get("state") == "open"


def format_door_payload(door_id, facing_direction, zone, x, y):
    door = DOORS.get(door_id)
    if not door:
        return None
    other_side = None
    for endpoint in door.get("endpoints", []):
        coords = endpoint["coords"]
        if endpoint["zone"] == zone and coords == (x, y) and endpoint["direction"] == facing_direction:
            continue
        other_room = get_room_info(endpoint["zone"], coords[0], coords[1])
        other_side = {
            "zone": endpoint["zone"],
            "direction": endpoint["direction"],
            "coords": {"x": coords[0], "y": coords[1]},
            "room_name": other_room.get("name") if other_room else None,
        }
        break
    return {
        "id": door_id,
        "name": door["name"],
        "description": door["description"],
        "state": door.get("state", "closed"),
        "is_open": door.get("state") == "open",
        "facing": facing_direction,
        "other_side": other_side,
    }


def get_room_door_payload(zone, x, y):
    seen = set()
    doors_here = []
    for direction in DIRECTION_VECTORS:
        door_id = get_door_id(zone, x, y, direction)
        if not door_id or door_id in seen:
            continue
        seen.add(door_id)
        payload = format_door_payload(door_id, direction, zone, x, y)
        if payload:
            doors_here.append(payload)
    return doors_here


def build_exit_payload(zone, x, y):
    width, height = get_world_dimensions(zone)
    exits = {}
    for direction, (dx, dy) in DIRECTION_VECTORS.items():
        nx, ny = x + dx, y + dy
        in_bounds = 0 <= nx < width and 0 <= ny < height
        reason = None
        door_id = get_door_id(zone, x, y, direction)
        door_payload = format_door_payload(door_id, direction, zone, x, y) if door_id else None
        can_travel = in_bounds
        if not in_bounds:
            reason = "No path in that direction."
            can_travel = False
        elif door_payload and not door_payload["is_open"]:
            reason = f"{door_payload['name']} is closed."
            can_travel = False
        exits[direction] = {
            "available": can_travel,
            "reason": reason,
            "door": door_payload,
            "target": {"zone": zone, "x": nx, "y": ny} if in_bounds else None,
        }
    return exits


def room_name(zone, x, y):
    return f"room_{zone}_{x}_{y}"


def get_room_info(zone, x, y):
    width, height = get_world_dimensions(zone)
    if 0 <= x < width and 0 <= y < height:
        return get_world_map(zone)[y][x]
    return {"name": "Unknown void", "description": "You should not be here."}


def get_players_in_room(zone, x, y):
    return [u for u, p in players.items() if p.get("zone", DEFAULT_ZONE) == zone and p["x"] == x and p["y"] == y]


def random_world_position(zone, exclude=None):
    exclude = set(exclude or [])
    width, height = get_world_dimensions(zone)
    if width == 0 or height == 0:
        return 0, 0
    attempts = 0
    while attempts < 50:
        x = random.randrange(width)
        y = random.randrange(height)
        if (x, y) not in exclude:
            return x, y
        attempts += 1
    return random.randrange(width), random.randrange(height)


def roll_hit_points_from_notation(notation, fallback):
    if not notation:
        return max(1, int(fallback or 1))
    cleaned = notation.lower().replace(" ", "")
    if "d" not in cleaned:
        try:
            return max(1, int(cleaned))
        except ValueError:
            return max(1, int(fallback or 1))
    num_part, rest = cleaned.split("d", 1)
    try:
        count = int(num_part) if num_part else 1
    except ValueError:
        count = 1
    modifier = 0
    size_part = rest
    if "+" in rest:
        size_part, mod_part = rest.split("+", 1)
        try:
            modifier = int(mod_part)
        except ValueError:
            modifier = 0
    elif "-" in rest:
        size_part, mod_part = rest.split("-", 1)
        try:
            modifier = -int(mod_part)
        except ValueError:
            modifier = 0
    try:
        size = int(size_part)
    except ValueError:
        size = max(1, int(fallback or 1))
    total = sum(random.randint(1, max(1, size)) for _ in range(max(1, count))) + modifier
    return max(1, total)


def spawn_mob(template_key, x=None, y=None, zone=None):
    template = MOB_TEMPLATES.get(template_key)
    if not template:
        return None
    global _mob_counter
    zone = zone or DEFAULT_ZONE
    if x is None or y is None:
        x, y = random_world_position(zone)
    _mob_counter += 1
    hp = roll_hit_points_from_notation(template.get("hp_dice"), template.get("hp", 1))
    mob_id = f"{template_key}-{_mob_counter}"
    mob = {
        "id": mob_id,
        "template": template_key,
        "name": template.get("name", template_key.title()),
        "zone": zone,
        "x": x,
        "y": y,
        "ac": template.get("ac", 10),
        "hp": hp,
        "max_hp": hp,
        "attack_interval": template.get("attack_interval", 3.0),
        "last_attack_ts": 0,
        "initiative": template.get("initiative", 10),
        "behaviour_type": template.get("behaviour_type", "defensive"),
        "xp": template.get("xp", 0),
        "description": template.get("description", ""),
        "abilities": template.get("abilities", {}),
        "gold_range": template.get("gold_range", (0, 0)),
        "loot": list(template.get("loot", [])),
        "contributions": {},
        "alive": True,
        "in_combat": False,
        "combat_targets": set(),
        "combat_task": None,
    }
    mobs[mob_id] = mob
    return mob


def spawn_npc_instance(npc_key):
    info = NPC_TEMPLATES.get(npc_key)
    if not info:
        return None
    existing_id = npcs.get(npc_key)
    if existing_id:
        existing = mobs.get(existing_id)
        if existing and existing.get("alive"):
            return existing
    template_key = info.get("mob_template")
    coords = info.get("coords", (0, 0))
    zone = info.get("zone", DEFAULT_ZONE)
    mob = spawn_mob(template_key, coords[0], coords[1], zone)
    if not mob:
        return None
    mob["is_npc"] = True
    mob["npc_key"] = npc_key
    mob["npc_bio"] = info.get("bio", "")
    mob["npc_personality"] = info.get("personality", "")
    mob["npc_facts"] = list(info.get("facts", []))
    mob["npc_secret_fact"] = info.get("secret_fact")
    mob["npc_aliases"] = list(info.get("aliases", []))
    npcs[npc_key] = mob["id"]
    npc_lookup_by_id[mob["id"]] = npc_key
    return mob


def spawn_initial_npcs():
    npcs.clear()
    npc_lookup_by_id.clear()
    for npc_key in NPC_TEMPLATES.keys():
        npc_conversations.setdefault(npc_key, {})
        mob = spawn_npc_instance(npc_key)
        if not mob:
            continue


def respawn_npc_after_delay(npc_key, delay=60):
    socketio.sleep(delay)
    mob = spawn_npc_instance(npc_key)
    if not mob:
        return
    zone = mob.get("zone", DEFAULT_ZONE)
    x, y = mob.get("x"), mob.get("y")
    broadcast_room_state(zone, x, y)


def spawn_initial_mobs():
    mobs.clear()
    for zone, world in WORLDS.items():
        tile_map = world["map"]
        for y, row in enumerate(tile_map):
            for x, tile in enumerate(row):
                for template_key in tile.get("mobs", []):
                    spawn_mob(template_key, x, y, zone)
    spawn_initial_npcs()


def get_mobs_in_room(zone, x, y):
    return [
        mob
        for mob in mobs.values()
        if mob["alive"] and mob.get("zone", DEFAULT_ZONE) == zone and mob["x"] == x and mob["y"] == y
    ]


def get_npcs_in_room(zone, x, y):
    return [mob for mob in get_mobs_in_room(zone, x, y) if mob.get("is_npc")]


def format_mob_payload(mob):
    return {
        "id": mob["id"],
        "name": mob["name"],
        "hp": mob["hp"],
        "max_hp": mob["max_hp"],
        "ac": mob["ac"],
        "xp": mob.get("xp", 0),
        "description": mob.get("description", ""),
        "behaviour": mob.get("behaviour_type", "defensive"),
        "is_npc": mob.get("is_npc", False),
    }


def format_npc_payload(mob, viewer=None):
    npc_key = mob.get("npc_key")
    counts = npc_conversations.get(npc_key, {}) if npc_key else {}
    handle = npc_key or mob.get("id")
    return {
        "id": mob["id"],
        "name": mob.get("name"),
        "ac": mob.get("ac"),
        "hp": mob.get("hp"),
        "max_hp": mob.get("max_hp"),
        "description": mob.get("description", ""),
        "bio": mob.get("npc_bio", ""),
        "handle": handle,
        "conversation_count": counts.get(viewer, 0) if viewer else 0,
    }


def ensure_openai_client():
    global _openai_client, _openai_mode
    if _openai_mode == "disabled":
        return None, "disabled"
    if _openai_client is not None and _openai_mode:
        return _openai_client, _openai_mode
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _openai_mode = "disabled"
        return None, "disabled"
    if OpenAI is not None:
        try:
            _openai_client = OpenAI(api_key=api_key)
            _openai_mode = "client"
            return _openai_client, _openai_mode
        except Exception:
            _openai_client = None
            _openai_mode = None
    if openai_module is not None:
        openai_module.api_key = api_key
        _openai_client = openai_module
        _openai_mode = "legacy"
        return _openai_client, _openai_mode
    _openai_mode = "disabled"
    return None, "disabled"


def build_npc_knowledge(mob, conversation_count):
    knowledge = list(mob.get("npc_facts", []))
    secret = mob.get("npc_secret_fact")
    if secret and conversation_count > NPC_SECRET_THRESHOLD:
        knowledge.append(secret)
    return knowledge


def generate_npc_response(mob, player, message, conversation_count):
    client, mode = ensure_openai_client()
    if not client or mode == "disabled":
        return None, "AI conversations are not configured. Set OPENAI_API_KEY on the server."

    npc_name = mob.get("name", "The villager")
    persona = mob.get("npc_personality", "friendly")
    bio = mob.get("npc_bio", "a notable resident of Dawnfell Village")
    knowledge = build_npc_knowledge(mob, conversation_count)
    knowledge_text = "\n".join(f"- {fact}" for fact in knowledge) if knowledge else "- (No stored facts.)"
    player_name = player.get("name") or "An adventurer"
    race = player.get("race") or ""
    char_class = player.get("char_class") or ""
    level = player.get("level", 1)
    lineage = " ".join(part for part in [race, char_class] if part)
    summary_bits = [player_name]
    if lineage:
        summary_bits.append(lineage)
    summary_bits.append(f"Level {level}")
    player_summary = " • ".join(summary_bits)
    player_bio = player.get("bio") or "No personal biography provided."
    player_description = player.get("description") or ""
    conversation_line = f"You have spoken with this adventurer {conversation_count} times."
    instructions = (
        f"You are {npc_name}, {bio}. Speak in a {persona} tone. Stay in character and use the knowledge provided. "
        "Offer guidance about Dawnfell Village and warp stones when it fits the conversation. If a question exceeds your knowledge, admit uncertainty."
    )
    player_context_lines = [
        f"Adventurer summary: {player_summary}.",
        f"Adventurer bio: {player_bio}",
    ]
    if player_description:
        player_context_lines.append(f"Adventurer appearance: {player_description}")
    player_context = "\n".join(player_context_lines)
    messages = [
        {"role": "system", "content": instructions},
        {"role": "system", "content": conversation_line},
        {"role": "system", "content": "Knowledge available to you:\n" + knowledge_text},
        {"role": "system", "content": "Respond in 1-3 short paragraphs."},
        {"role": "user", "content": f"{player_context}\nPlayer says: {message}"},
    ]
    try:
        if mode == "client" and hasattr(client, "chat"):
            response = client.chat.completions.create(
                model=NPC_MODEL_NAME,
                messages=messages,
                temperature=0.6,
                max_tokens=220,
            )
            reply = response.choices[0].message.content.strip()
        else:
            response = client.ChatCompletion.create(
                model=NPC_MODEL_NAME,
                messages=messages,
                temperature=0.6,
                max_tokens=220,
            )
            reply = response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # pragma: no cover - network dependency
        return None, str(exc)
    return reply, None


def find_mob_in_room(identifier, zone, x, y):
    if not identifier:
        return None
    lookup = identifier.strip().lower()
    for mob in get_mobs_in_room(zone, x, y):
        if mob["id"].lower() == lookup or mob["name"].lower() == lookup:
            return mob
    return None


def find_npc_in_room(identifier, zone, x, y):
    if not identifier:
        return None
    lookup = identifier.strip().lower()
    for npc in get_npcs_in_room(zone, x, y):
        if npc["id"].lower() == lookup:
            return npc
        key = npc.get("npc_key")
        if key and key.lower() == lookup:
            return npc
        name = npc.get("name")
        if name and name.lower() == lookup:
            return npc
        if key and key.replace("_", " ").lower() == lookup:
            return npc
        for alias in npc.get("npc_aliases", []):
            if alias.lower() == lookup:
                return npc
    return None


def parse_talk_target(player, raw_args):
    if not player:
        return None, None
    text = (raw_args or "").strip()
    if not text:
        return None, None
    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    if text[0] in ('"', "'"):
        quote = text[0]
        closing = text.find(quote, 1)
        if closing != -1:
            identifier = text[1:closing].strip()
            remainder = text[closing + 1 :].strip()
            npc = find_npc_in_room(identifier, zone, x, y)
            if npc and remainder:
                return npc, remainder
    parts = text.split(None, 1)
    identifier = parts[0]
    remainder = parts[1].strip() if len(parts) > 1 else ""
    npc = find_npc_in_room(identifier, zone, x, y)
    if npc and remainder:
        return npc, remainder
    for candidate in get_npcs_in_room(zone, x, y):
        lowered = text.lower()
        aliases = [candidate.get("name", ""), candidate.get("npc_key", "")]
        aliases.extend(candidate.get("npc_aliases", []))
        for alias in aliases:
            alias = (alias or "").strip()
            if not alias:
                continue
            alias_lower = alias.lower()
            if lowered.startswith(alias_lower):
                remainder = text[len(alias) :].strip()
                if remainder:
                    return candidate, remainder
            compact = alias_lower.replace(" ", "_")
            if compact != alias_lower and lowered.startswith(compact):
                remainder = text[len(compact) :].strip()
                if remainder:
                    return candidate, remainder
    return None, None


def handle_talk_command(username, raw_args):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    npc, message = parse_talk_target(player, raw_args)
    if not npc or not message:
        return False, "Usage: /talk <npc> <message>"
    if not npc.get("alive") or npc.get("hp", 0) <= 0:
        return False, f"{npc.get('name', 'That NPC')} cannot respond right now."
    npc_key = npc.get("npc_key")
    if not npc_key:
        return False, "That creature does not respond to conversation."
    counts = npc_conversations.setdefault(npc_key, {}) if npc_key else {}
    previous = counts.get(username, 0)
    counts[username] = previous + 1
    conversation_total = counts[username]
    reply, error = generate_npc_response(npc, player, message, conversation_total)
    if error:
        counts[username] = previous
        return False, f"{npc.get('name', 'The NPC')} hesitates: {error}"

    zone = player.get("zone", DEFAULT_ZONE)
    room = room_name(zone, player["x"], player["y"])
    socketio.emit(
        "chat_message",
        {"from": username, "text": f"(to {npc['name']}) {message}"},
        room=room,
    )
    socketio.emit(
        "chat_message",
        {"from": npc["name"], "text": reply},
        room=room,
    )
    send_room_state(username)
    return True, reply


def stop_mob_combat(mob):
    mob["in_combat"] = False
    mob["combat_targets"] = set()


def mob_combat_loop(mob_id):
    """Background loop that lets mobs retaliate on their own timer."""
    while True:
        socketio.sleep(0.25)
        mob = mobs.get(mob_id)
        if not mob or not mob.get("alive"):
            break
        if not mob.get("in_combat"):
            break

        targets = mob.setdefault("combat_targets", set())
        engaged = []
        for username in list(targets):
            player = players.get(username)
            if not player or player["hp"] <= 0:
                targets.discard(username)
                continue
            if (
                player.get("zone", DEFAULT_ZONE) != mob.get("zone", DEFAULT_ZONE)
                or (player["x"], player["y"]) != (mob["x"], mob["y"])
            ):
                targets.discard(username)
                continue
            engaged.append((username, player))

        if not engaged:
            stop_mob_combat(mob)
            break

        now = time.time()
        interval = mob.get("attack_interval", 3.0)
        if now - mob.get("last_attack_ts", 0) < interval:
            continue

        username, target = random.choice(engaged)
        damage_info = mob.get("damage", {})
        damage = roll_dice(damage_info.get("dice")) + damage_info.get("bonus", 0)
        damage = max(1, damage)
        mob["last_attack_ts"] = now

        target["hp"] = clamp_hp(target["hp"] - damage, target["max_hp"])
        update_character_current_hp(target["character_id"], target["hp"])
        room = room_name(mob.get("zone", DEFAULT_ZONE), mob["x"], mob["y"])
        dmg_type = damage_info.get("type")
        suffix = f" {dmg_type} damage" if dmg_type else " damage"
        socketio.emit(
            "system_message",
            {"text": f"{mob['name']} strikes {username} for {damage}{suffix}!"},
            room=room,
        )
        send_room_state(username)
        broadcast_room_state(mob.get("zone", DEFAULT_ZONE), mob["x"], mob["y"])

        if target["hp"] == 0:
            socketio.emit(
                "system_message",
                {"text": f"{username} is felled by {mob['name']}!"},
                room=room,
            )
            targets.discard(username)
            respawn_player(username)

    mob = mobs.get(mob_id)
    if mob:
        mob["combat_task"] = None


def engage_mob_with_player(mob, username, auto=False):
    """Ensure the mob is locked in combat with a player, starting timers if needed."""
    if not mob or not mob.get("alive"):
        return
    player = players.get(username)
    if not player or player["hp"] <= 0:
        return
    if (
        player.get("zone", DEFAULT_ZONE) != mob.get("zone", DEFAULT_ZONE)
        or (player["x"], player["y"]) != (mob["x"], mob["y"])
    ):
        return

    targets = mob.setdefault("combat_targets", set())
    if username not in targets:
        targets.add(username)
        room = room_name(mob.get("zone", DEFAULT_ZONE), mob["x"], mob["y"])
        if auto:
            socketio.emit(
                "system_message",
                {"text": f"{mob['name']} lunges at {username}!"},
                room=room,
            )
        else:
            socketio.emit(
                "system_message",
                {"text": f"{mob['name']} turns to fight {username}!"},
                room=room,
            )

    if not mob.get("in_combat"):
        mob["in_combat"] = True
        mob["last_attack_ts"] = time.time()
        if not mob.get("combat_task"):
            mob["combat_task"] = socketio.start_background_task(mob_combat_loop, mob["id"])
    elif not mob.get("combat_task"):
        mob["combat_task"] = socketio.start_background_task(mob_combat_loop, mob["id"])


def disengage_player_from_room_mobs(username, x, y):
    player = players.get(username)
    zone = player.get("zone", DEFAULT_ZONE) if player else DEFAULT_ZONE
    for mob in get_mobs_in_room(zone, x, y):
        targets = mob.setdefault("combat_targets", set())
        if username in targets:
            targets.discard(username)
            if not targets:
                stop_mob_combat(mob)


def trigger_aggressive_mobs_for_player(username, x, y):
    """Aggressive mobs attack as soon as a fresh player enters their room."""
    player = players.get(username)
    zone = player.get("zone", DEFAULT_ZONE) if player else DEFAULT_ZONE
    for mob in get_mobs_in_room(zone, x, y):
        if mob.get("behaviour_type") == "aggressive" and mob.get("alive"):
            engage_mob_with_player(mob, username, auto=True)


def get_loot_in_room(zone, x, y):
    return list(room_loot.get((zone, x, y), []))


def add_loot_to_room(zone, x, y, loot_entry):
    room_loot.setdefault((zone, x, y), []).append(loot_entry)


def generate_loot_entry_gold(amount):
    global _loot_counter
    _loot_counter += 1
    return {
        "id": f"loot-{_loot_counter}",
        "type": "gold",
        "amount": amount,
        "name": f"{amount} gold coins",
        "description": "A small pile of coins dropped by a defeated foe.",
    }


def generate_loot_entry_item(item_key):
    global _loot_counter
    _loot_counter += 1
    item = GENERAL_ITEMS.get(item_key) or WEAPONS.get(item_key)
    name = item.get("name", item_key.title()) if item else item_key.title()
    description = item.get("description", "") if item else "An unidentified item."
    return {
        "id": f"loot-{_loot_counter}",
        "type": "item",
        "item_key": item_key,
        "name": name,
        "description": description,
    }


def format_loot_payload(entries):
    payload = []
    for entry in entries:
        payload.append(
            {
                "id": entry["id"],
                "type": entry.get("type", "item"),
                "name": entry.get("name", "Mysterious loot"),
                "amount": entry.get("amount"),
                "description": entry.get("description", ""),
            }
        )
    return payload


def resolve_spell_key_from_input(player, identifier):
    if not player or not identifier:
        return None
    lookup = identifier.strip().lower()
    for key in player.get("spells", []):
        spell = get_spell(key)
        if not spell:
            continue
        if key.lower() == lookup or spell["name"].lower() == lookup:
            return key
    return None


def get_spell_cooldown_remaining(player, spell_key):
    if not player:
        return 0
    ready_at = (player.get("cooldowns") or {}).get(spell_key)
    if not ready_at:
        return 0
    remaining = ready_at - time.time()
    if remaining <= 0:
        return 0
    return int(math.ceil(remaining))


def recalculate_player_stats(player):
    if not player:
        return
    base_mods = dict(player.get("base_ability_mods") or player.get("ability_mods") or {})
    if "base_ability_mods" not in player:
        player["base_ability_mods"] = dict(base_mods)
    ability_mods = dict(base_mods)
    base_ac = player.get("base_ac", player.get("ac", 10))
    if "base_ac" not in player:
        player["base_ac"] = base_ac
    proficiency = player.get("proficiency", 0)
    attack_ability = player.get("attack_ability")
    extra_attack_bonus = 0
    ac_bonus = 0
    attack_roll_bonus = []
    damage_bonus = 0
    now = time.time()
    active_effects = []
    for effect in player.get("active_effects", []) or []:
        expires_at = effect.get("expires_at")
        if expires_at and expires_at <= now:
            continue
        active_effects.append(effect)
        modifiers = effect.get("modifiers") or {}
        for ability, delta in (modifiers.get("ability_mods") or {}).items():
            ability_mods[ability] = ability_mods.get(ability, 0) + delta
        ac_bonus += modifiers.get("ac", 0)
        extra_attack_bonus += modifiers.get("attack_bonus", 0)
        attack_bonus_mod = modifiers.get("attack_roll_bonus")
        if attack_bonus_mod:
            attack_roll_bonus.append(dict(attack_bonus_mod))
        damage_bonus += modifiers.get("damage_bonus", 0)
    player["active_effects"] = active_effects
    player["ability_mods"] = ability_mods
    dex_delta = ability_mods.get("dex", 0) - base_mods.get("dex", 0)
    player["ac"] = base_ac + dex_delta + ac_bonus
    attack_mod = ability_mods.get(attack_ability, 0) if attack_ability else 0
    player["attack_bonus"] = proficiency + attack_mod + extra_attack_bonus
    player["attack_roll_bonus_dice"] = attack_roll_bonus
    player["damage_bonus"] = damage_bonus
    player.setdefault("cooldowns", {})
    player.setdefault("active_effects", [])
    update_player_action_timing(player)


def apply_effect_to_player(target, effect_template):
    if not target or not effect_template:
        return None
    effect = {
        "key": effect_template.get("key") or effect_template.get("name"),
        "name": effect_template.get("name"),
        "description": effect_template.get("description", ""),
        "modifiers": effect_template.get("modifiers", {}),
        "expires_at": None,
    }
    duration = effect_template.get("duration")
    if duration:
        effect["expires_at"] = time.time() + duration
    stackable = effect_template.get("stackable", False)
    effects = target.setdefault("active_effects", [])
    replaced = False
    if not stackable and effect["key"]:
        for idx, existing in enumerate(effects):
            if existing.get("key") == effect["key"]:
                effects[idx] = effect
                replaced = True
                break
    if not replaced:
        effects.append(effect)
    recalculate_player_stats(target)
    return effect


def format_spell_list(player):
    payload = []
    if not player:
        return payload
    for key in sorted(player.get("spells", []), key=lambda k: get_spell(k)["name"] if get_spell(k) else k):
        spell = get_spell(key)
        if not spell:
            continue
        payload.append(
            {
                "key": key,
                "name": spell["name"],
                "type": spell.get("type", "").title(),
                "description": spell.get("description", ""),
                "cooldown": spell.get("cooldown", 0),
                "cooldown_remaining": get_spell_cooldown_remaining(player, key),
                "target": spell.get("target", "self"),
            }
        )
    return payload


def format_effect_list(player):
    payload = []
    if not player:
        return payload
    now = time.time()
    for effect in player.get("active_effects", []):
        expires_at = effect.get("expires_at")
        remaining = None
        if expires_at:
            remaining = max(0, int(math.ceil(expires_at - now)))
        payload.append(
            {
                "key": effect.get("key"),
                "name": effect.get("name"),
                "description": effect.get("description", ""),
                "expires_in": remaining,
            }
        )
    return payload


def build_player_state(user_record, sid):
    derived = derive_character_from_record(user_record)
    start_x, start_y = get_world_start(DEFAULT_ZONE)
    state = {
        "sid": sid,
        "zone": DEFAULT_ZONE,
        "x": start_x,
        "y": start_y,
        "character_id": user_record.get("id"),
        "account_id": user_record.get("account_id"),
        "name": user_record.get("name"),
        "bio": user_record.get("bio") or "",
        "description": user_record.get("description") or "",
    }
    state.update(derived)
    state["inventory"] = list(state.get("inventory", []))
    state["items"] = list(state.get("items", []))
    state["gold"] = int(derived.get("gold", 0))
    state["xp"] = int(derived.get("xp", 0))
    state["hp"] = clamp_hp(user_record.get("current_hp"), derived["max_hp"])
    state["base_ability_mods"] = dict(state.get("ability_mods", {}))
    state["base_ac"] = state.get("ac", 10)
    state["base_initiative"] = 10
    state["initiative"] = 10
    state["action_cooldown"] = BASE_ACTION_COOLDOWN
    state["last_action_ts"] = 0
    state["active_effects"] = []
    state["cooldowns"] = {}
    state["spells"] = get_spells_for_class(state.get("char_class"))
    state["attack_roll_bonus_dice"] = []
    state["damage_bonus"] = 0
    state["searched_rooms"] = set()
    apply_weapon_to_player_state(state, state.get("equipped_weapon"))
    recalculate_player_stats(state)
    return state


def apply_weapon_to_player_state(player, weapon_key=None):
    inventory = player.get("inventory") or [DEFAULT_WEAPON_KEY]
    weapon_key = ensure_equipped_weapon(weapon_key, inventory)
    player["equipped_weapon"] = weapon_key
    weapon_payload = format_weapon_payload(weapon_key)
    player["weapon"] = weapon_payload
    class_name = normalize_choice(player.get("char_class"), CLASSES, DEFAULT_CLASS)
    class_data = CLASSES[class_name]
    attack_ability = weapon_payload.get("ability") or class_data["primary_ability"]
    player["attack_ability"] = attack_ability
    recalculate_player_stats(player)
    return weapon_payload


def resolve_weapon_key_from_input(player, identifier):
    if not identifier:
        return None
    target = identifier.strip().lower()
    for key in player.get("inventory", []):
        weapon = get_weapon(key)
        if key.lower() == target or weapon["name"].lower() == target:
            return key
    return None


def equip_weapon_for_player(username, weapon_identifier):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not weapon_identifier:
        return False, "Select a weapon to equip."

    weapon_key = resolve_weapon_key_from_input(player, weapon_identifier)
    if not weapon_key:
        return False, "You do not possess that weapon."
    if weapon_key == player.get("equipped_weapon"):
        return False, f"{get_weapon(weapon_key)['name']} is already equipped."

    apply_weapon_to_player_state(player, weapon_key)
    update_character_equipped_weapon(player["character_id"], weapon_key)
    send_room_state(username)

    zone = player.get("zone", DEFAULT_ZONE)
    room = room_name(zone, player["x"], player["y"])
    message = f"{username} equips {player['weapon']['name']}."
    socketio.emit("system_message", {"text": message}, room=room)
    return True, message


def send_room_state(username):
    player = players.get(username)
    if not player:
        return
    recalculate_player_stats(player)
    x, y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    room = get_room_info(zone, x, y)
    occupants = get_players_in_room(zone, x, y)
    weapon = player.get("weapon", {})
    inventory_payload = []
    for key in player.get("inventory", []):
        info = format_weapon_payload(key)
        inventory_payload.append(
            {
                "key": info["key"],
                "name": info["name"],
                "dice": info["dice_label"],
                "damage_type": info["damage_type"],
                "equipped": info["key"] == player.get("equipped_weapon"),
            }
        )
    item_payload = []
    for key in player.get("items", []):
        info = format_item_payload(key)
        if not info:
            continue
        item_payload.append(info)
    mobs_here = [format_mob_payload(mob) for mob in get_mobs_in_room(zone, x, y)]
    npcs_here = [format_npc_payload(npc, viewer=username) for npc in get_npcs_in_room(zone, x, y)]
    loot_here = format_loot_payload(get_loot_in_room(zone, x, y))
    doors_here = get_room_door_payload(zone, x, y)
    exits = build_exit_payload(zone, x, y)
    warp_info = None
    if room.get("travel_to"):
        warp_info = {
            "label": room.get("warp_label", "Warp Stone"),
            "description": room.get("warp_description")
            or "A rune-carved warp stone hums softly, awaiting activation.",
        }
    payload = {
        "zone": zone,
        "world_name": get_world(zone)["name"],
        "x": x,
        "y": y,
        "room_name": room["name"],
        "description": room["description"],
        "players": occupants,
        "mobs": mobs_here,
        "npcs": npcs_here,
        "loot": loot_here,
        "doors": doors_here,
        "exits": exits,
        "warp_stone": warp_info,
        "character": {
            "id": player.get("character_id"),
            "name": player.get("name"),
            "bio": player.get("bio", ""),
            "description": player.get("description", ""),
            "race": player["race"],
            "char_class": player["char_class"],
            "level": player.get("level", 1),
            "hp": player["hp"],
            "max_hp": player["max_hp"],
            "ac": player["ac"],
            "proficiency": player["proficiency"],
            "weapon": {
                "key": weapon.get("key", DEFAULT_WEAPON_KEY),
                "name": weapon.get("name", "Unarmed"),
                "dice": weapon.get("dice_label", "-"),
                "damage_type": weapon.get("damage_type", ""),
            },
            "attack_bonus": player["attack_bonus"],
            "attack_ability": player["attack_ability"],
            "abilities": player["abilities"],
            "ability_mods": player["ability_mods"],
            "weapon_inventory": inventory_payload,
            "items": item_payload,
            "gold": player.get("gold", 0),
            "xp": player.get("xp", 0),
            "spells": format_spell_list(player),
            "effects": format_effect_list(player),
        },
    }
    socketio.emit("room_state", payload, to=player["sid"])


def broadcast_room_state(zone, x, y):
    for occupant in get_players_in_room(zone, x, y):
        send_room_state(occupant)


def describe_adjacent_players(player):
    directions = [
        ("north", (0, -1)),
        ("south", (0, 1)),
        ("west", (-1, 0)),
        ("east", (1, 0)),
    ]
    lines = []
    zone = player.get("zone", DEFAULT_ZONE)
    width, height = get_world_dimensions(zone)
    for label, (dx, dy) in directions:
        nx, ny = player["x"] + dx, player["y"] + dy
        if not (0 <= nx < width and 0 <= ny < height):
            continue
        occupants = get_players_in_room(zone, nx, ny)
        room = get_room_info(zone, nx, ny)
        if occupants:
            lines.append(f"{label.title()} ({room['name']}): {', '.join(occupants)}")
        else:
            lines.append(f"{label.title()} ({room['name']}): No one in sight.")
    if not lines:
        return "You sense nothing nearby."
    return "Nearby presences:\n" + "\n".join(lines)


def extract_spell_and_target(player, text):
    cleaned = (text or "").strip()
    if not cleaned:
        return None, None
    lower = cleaned.lower()
    for key in player.get("spells", []):
        spell = get_spell(key)
        if not spell:
            continue
        for candidate in (key.lower(), spell["name"].lower()):
            if lower.startswith(candidate):
                remainder = cleaned[len(candidate) :].strip()
                return key, (remainder or None)
    parts = cleaned.split(None, 1)
    if not parts:
        return None, None
    key_guess = resolve_spell_key_from_input(player, parts[0])
    if key_guess:
        remainder = parts[1].strip() if len(parts) > 1 else None
        return key_guess, (remainder or None)
    return None, None


def cast_spell_for_player(username, spell_identifier, target_identifier=None):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not check_player_action_gate(username):
        return False, None
    if not spell_identifier:
        return False, "Choose a spell or ability to use."

    recalculate_player_stats(player)
    spells_known = player.get("spells", [])
    if spell_identifier in spells_known:
        spell_key = spell_identifier
    else:
        spell_key = resolve_spell_key_from_input(player, spell_identifier)
    if not spell_key:
        return False, "You do not know that spell or ability."

    spell = get_spell(spell_key)
    if not spell:
        return False, "That magic is unknown to this realm."

    remaining = get_spell_cooldown_remaining(player, spell_key)
    if remaining > 0:
        return False, f"{spell['name']} will be ready in {remaining} seconds."

    target_requirement = spell.get("target", "self")
    identifier = (target_identifier or "").strip()
    target_name = None
    if target_requirement == "none":
        target_name = None
    elif target_requirement == "self":
        target_name = username
    elif target_requirement in ("ally", "self_or_ally", "ally_or_self"):
        target_name = identifier or username
    elif target_requirement == "enemy":
        if not identifier:
            return False, f"Choose a target for {spell['name']}."
        target_name = identifier
    else:
        target_name = identifier or username

    target_player = None
    if target_name:
        if target_name == username:
            target_player = player
        else:
            target_player = players.get(target_name)
        if not target_player:
            return False, f"{target_name} is not present."
        if target_requirement == "enemy" and target_name == username:
            return False, "You cannot target yourself with that."
        if target_requirement != "none":
            if player.get("zone", DEFAULT_ZONE) != target_player.get("zone", DEFAULT_ZONE):
                return False, f"{target_name} is not in the same room."
            if (player["x"], player["y"]) != (target_player["x"], target_player["y"]):
                return False, f"{target_name} is not in the same room."
        recalculate_player_stats(target_player)

    success, feedback = execute_spell(username, player, spell_key, spell, target_player, target_name)
    if not success:
        return False, feedback

    mark_player_action(player)
    cooldown = spell.get("cooldown", 0)
    if cooldown:
        player.setdefault("cooldowns", {})[spell_key] = time.time() + cooldown

    send_room_state(username)
    if target_player and target_name and target_name != username:
        send_room_state(target_name)

    return True, feedback


def execute_spell(caster_name, caster, spell_key, spell, target_player, target_name):
    zone = caster.get("zone", DEFAULT_ZONE)
    room = room_name(zone, caster["x"], caster["y"])
    ability_mod = caster.get("ability_mods", {}).get(spell.get("ability"), 0)
    spell_type = spell.get("type")

    if spell_type == "attack":
        if not target_player or not target_name:
            return False, "No valid target."
        damage_info = spell.get("damage", {})
        damage = roll_dice(damage_info.get("dice")) + damage_info.get("bonus", 0)
        if damage_info.get("add_ability_mod"):
            damage += ability_mod
        damage += caster.get("damage_bonus", 0)
        damage = max(1, damage)
        target_player["hp"] = clamp_hp(target_player["hp"] - damage, target_player["max_hp"])
        update_character_current_hp(target_player["character_id"], target_player["hp"])
        damage_type = damage_info.get("damage_type")
        dmg_suffix = f" {damage_type} damage" if damage_type else " damage"
        message = f"{caster_name} casts {spell['name']} at {target_name}, dealing {damage}{dmg_suffix}!"
        socketio.emit("system_message", {"text": message}, room=room)
        if target_player["hp"] == 0:
            socketio.emit(
                "system_message",
                {"text": f"{target_name} collapses under the assault!"},
                room=room,
            )
            respawn_player(target_name)
        return True, message

    if spell_type == "heal":
        target = target_player or caster
        target_label = target_name or caster_name
        heal_info = spell.get("heal", {})
        amount = 0
        if heal_info.get("dice"):
            amount += roll_dice(heal_info.get("dice"))
        if heal_info.get("add_ability_mod"):
            amount += ability_mod
        if heal_info.get("add_level"):
            amount += caster.get("level", 1)
        amount += heal_info.get("bonus", 0)
        amount = max(1, amount)
        before = target["hp"]
        target["hp"] = clamp_hp(target["hp"] + amount, target["max_hp"])
        restored = target["hp"] - before
        update_character_current_hp(target["character_id"], target["hp"])
        if restored <= 0:
            message = f"{spell['name']} has no effect on {target_label}."
        else:
            message = f"{caster_name} casts {spell['name']} and restores {restored} HP to {target_label}."
        socketio.emit("system_message", {"text": message}, room=room)
        return True, message

    if spell_type == "buff":
        target = target_player or caster
        target_label = target_name or caster_name
        effect_template = dict(spell.get("effect") or {})
        if not effect_template:
            return False, "No effect defined for this magic."
        effect_template.setdefault("name", spell.get("name"))
        apply_effect_to_player(target, effect_template)
        description = effect_template.get("description")
        if target_label == caster_name:
            message = f"{caster_name} is wreathed in {spell['name']}."
        else:
            message = f"{caster_name} casts {spell['name']} on {target_label}."
        if description:
            message += f" ({description})"
        socketio.emit("system_message", {"text": message}, room=room)
        return True, message

    if spell_type == "utility":
        if spell_key == "keen_eye":
            socketio.emit(
                "system_message",
                {"text": f"{caster_name} narrows their eyes, surveying the surrounding paths."},
                room=room,
            )
            report = describe_adjacent_players(caster)
            notify_player(caster_name, report)
            return True, report
        message = f"{caster_name} invokes {spell['name']}, but its effect is subtle."
        socketio.emit("system_message", {"text": message}, room=room)
        return True, message

    message = f"{caster_name} channels {spell['name']}, but nothing notable happens."
    socketio.emit("system_message", {"text": message}, room=room)
    return True, message


def notify_player(username, text):
    player = players.get(username)
    if not player:
        return
    socketio.emit("system_message", {"text": text}, to=player["sid"])


def respawn_player(username):
    player = players.get(username)
    if not player:
        return

    zone = player.get("zone", DEFAULT_ZONE)
    old_room = room_name(zone, player["x"], player["y"])
    disengage_player_from_room_mobs(username, player["x"], player["y"])
    leave_room(old_room, sid=player["sid"])
    socketio.emit(
        "system_message",
        {"text": f"{username} collapses and vanishes in a swirl of grey mist."},
        room=old_room,
    )

    player["zone"] = DEFAULT_ZONE
    start_x, start_y = get_world_start(DEFAULT_ZONE)
    player["x"], player["y"] = start_x, start_y
    player["hp"] = player["max_hp"]
    update_character_current_hp(player["character_id"], player["hp"])
    player["active_effects"] = []
    recalculate_player_stats(player)

    new_room = room_name(player["zone"], player["x"], player["y"])
    join_room(new_room, sid=player["sid"])
    socketio.emit(
        "system_message",
        {"text": f"{username} staggers back into the area, looking dazed."},
        room=new_room,
        include_self=False,
    )
    notify_player(username, "You have been defeated and return to the village square.")
    send_room_state(username)
    trigger_aggressive_mobs_for_player(username, player["x"], player["y"])


def handle_command(username, command_text):
    command_text = (command_text or "").strip()
    if not command_text:
        return False

    parts = command_text.split()
    cmd = parts[0].lower()
    if cmd in ("attack", "fight"):
        if len(parts) < 2:
            notify_player(username, "Usage: /attack <target>")
            return True
        target_name = parts[1]
        resolve_attack(username, target_name)
        return True
    if cmd in ("equip", "wield"):
        if len(parts) < 2:
            notify_player(username, "Usage: /equip <weapon_name>")
            return True
        weapon_name = " ".join(parts[1:])
        success, message = equip_weapon_for_player(username, weapon_name)
        if not success:
            notify_player(username, message)
        return True
    if cmd in ("search", "investigate"):
        success, message = perform_search_action(username)
        if not success and message:
            notify_player(username, message)
        return True
    if cmd == "cast":
        player = players.get(username)
        if not player:
            notify_player(username, "You are not in the game.")
            return True
        remainder = command_text[len(parts[0]):].strip()
        if not remainder:
            notify_player(username, "Usage: /cast <spell_name> [target]")
            return True
        spell_key, target_text = extract_spell_and_target(player, remainder)
        if not spell_key:
            notify_player(username, "You do not know that spell or ability.")
            return True
        success, message = cast_spell_for_player(username, spell_key, target_text)
        if not success and message:
            notify_player(username, message)
        return True
    if cmd in ("spells", "abilities"):
        player = players.get(username)
        if not player:
            notify_player(username, "You are not in the game.")
            return True
        recalculate_player_stats(player)
        known = format_spell_list(player)
        if not known:
            notify_player(username, "You have no spells or class abilities.")
            return True
        lines = []
        for spell in known:
            cooldown = spell.get("cooldown_remaining", 0)
            base_cd = spell.get("cooldown", 0)
            if cooldown:
                cooldown_text = f" (recharges in {cooldown}s)"
            elif base_cd:
                cooldown_text = f" ({base_cd}s cooldown)"
            else:
                cooldown_text = ""
            spell_type = spell.get("type") or ""
            type_label = f"[{spell_type}] " if spell_type else ""
            lines.append(f"- {type_label}{spell['name']}: {spell['description']}{cooldown_text}")
        notify_player(username, "Known spells & abilities:\n" + "\n".join(lines))
        return True
    if cmd in ("loot", "take", "pickup"):
        if len(parts) < 2:
            notify_player(username, "Usage: /loot <loot-id>")
            return True
        loot_id = parts[1]
        success, message = pickup_loot(username, loot_id)
        if not success and message:
            notify_player(username, message)
        return True
    if cmd == "talk":
        remainder = command_text[len(parts[0]) :].strip()
        success, message = handle_talk_command(username, remainder)
        if not success and message:
            notify_player(username, message)
        return True

    notify_player(username, f"Unknown command: {cmd}")
    return True

def attack_roll_success(roll, total_attack, target_ac):
    if roll == 1:
        return False
    if roll == 20:
        return True
    return total_attack >= target_ac


def distribute_xp(contributions, total_xp):
    awards = {}
    if not total_xp or total_xp <= 0:
        return awards
    filtered = {player: max(0, int(damage)) for player, damage in contributions.items() if damage > 0}
    if not filtered:
        return awards
    total_damage = sum(filtered.values())
    if total_damage <= 0:
        return awards
    remaining = total_xp
    ordered = sorted(filtered.items(), key=lambda item: item[1], reverse=True)
    for username, damage in ordered:
        share = int(total_xp * damage / total_damage)
        if share > remaining:
            share = remaining
        awards[username] = share
        remaining -= share
    idx = 0
    while remaining > 0 and ordered:
        username = ordered[idx % len(ordered)][0]
        awards[username] = awards.get(username, 0) + 1
        remaining -= 1
        idx += 1
    return {user: amount for user, amount in awards.items() if amount > 0}


def award_xp(username, amount):
    if not amount or amount <= 0:
        return
    player = players.get(username)
    if player:
        player["xp"] = player.get("xp", 0) + amount
        update_character_xp(player["character_id"], player["xp"])
        notify_player(username, f"You gain {amount} XP.")
    else:
        record = get_character_by_name(username)
        if record is None:
            return
        new_total = (record.get("xp") or 0) + amount
        update_character_xp(record["id"], new_total)


def collect_item_for_player(player, username, item_key):
    if not item_key:
        return None
    if item_key in GENERAL_ITEMS:
        items = player.setdefault("items", [])
        items.append(item_key)
        update_character_items(player["character_id"], items)
        return GENERAL_ITEMS[item_key]["name"]
    if item_key in WEAPONS:
        inventory = player.setdefault("inventory", [])
        if item_key not in inventory:
            inventory.append(item_key)
            update_character_weapon_inventory(player["character_id"], inventory)
        return WEAPONS[item_key]["name"]
    items = player.setdefault("items", [])
    items.append(item_key)
    update_character_items(player["character_id"], items)
    return item_key.replace("_", " ").title()


def handle_mob_defeat(mob, killer_name=None):
    if not mob or not mob.get("alive"):
        return
    mob["alive"] = False
    stop_mob_combat(mob)
    x, y = mob["x"], mob["y"]
    zone = mob.get("zone", DEFAULT_ZONE)
    room = room_name(zone, x, y)
    socketio.emit(
        "system_message",
        {"text": f"{mob['name']} is slain!"},
        room=room,
    )
    contributions = mob.get("contributions", {})
    xp_total = mob.get("xp", 0)
    awards = distribute_xp(contributions, xp_total)
    if awards:
        for username, amount in awards.items():
            award_xp(username, amount)
    gold_min, gold_max = mob.get("gold_range", (0, 0))
    drops = []
    if gold_max and gold_max >= gold_min and gold_max > 0:
        gold_amount = random.randint(gold_min, gold_max)
        if gold_amount > 0:
            gold_entry = generate_loot_entry_gold(gold_amount)
            add_loot_to_room(zone, x, y, gold_entry)
            drops.append(gold_entry)
    for entry in mob.get("loot", []):
        if isinstance(entry, (list, tuple)) and entry:
            item_key = entry[0]
            chance = entry[1] if len(entry) > 1 else 1.0
        else:
            item_key = entry
            chance = 1.0
        if random.random() <= chance:
            loot_entry = generate_loot_entry_item(item_key)
            add_loot_to_room(zone, x, y, loot_entry)
            drops.append(loot_entry)
    if drops:
        names = ", ".join(drop["name"] for drop in drops)
        socketio.emit(
            "system_message",
            {"text": f"Treasure spills onto the ground: {names}."},
            room=room,
        )
    mobs.pop(mob["id"], None)
    if mob.get("is_npc"):
        npc_key = npc_lookup_by_id.pop(mob["id"], None)
        if npc_key:
            npcs.pop(npc_key, None)
            socketio.start_background_task(respawn_npc_after_delay, npc_key)
    broadcast_room_state(zone, x, y)


def resolve_attack_against_mob(attacker_name, attacker, mob):
    engage_mob_with_player(mob, attacker_name)
    recalculate_player_stats(attacker)
    roll = random.randint(1, 20)
    crit = roll == 20
    attack_bonus = attacker["attack_bonus"]
    bonus_rolls = []
    bonus_total = 0
    for bonus in attacker.get("attack_roll_bonus_dice", []):
        extra = roll_dice(bonus.get("dice"))
        bonus_total += extra
        label = bonus.get("label") or format_dice(bonus.get("dice"))
        bonus_rolls.append((label, extra))
    total_attack = roll + attack_bonus + bonus_total
    zone = attacker.get("zone", DEFAULT_ZONE)
    room = room_name(zone, attacker["x"], attacker["y"])
    if not attack_roll_success(roll, total_attack, mob["ac"]):
        bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
        socketio.emit(
            "system_message",
            {
                "text": f"{attacker_name} strikes at {mob['name']} but misses (roll {roll} + {attack_bonus}{bonus_text} = {total_attack} vs AC {mob['ac']}).",
            },
            room=room,
        )
        return
    ability_key = attacker.get("attack_ability", "str")
    ability_mod = attacker["ability_mods"].get(ability_key, 0)
    damage = roll_weapon_damage(
        attacker["weapon"], ability_mod, crit=crit, bonus_damage=attacker.get("damage_bonus", 0)
    )
    mob["hp"] = max(0, mob["hp"] - damage)
    contributions = mob.setdefault("contributions", {})
    contributions[attacker_name] = contributions.get(attacker_name, 0) + damage
    bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
    attack_detail = f"roll {roll}{' - critical!' if crit else ''} + {attack_bonus}{bonus_text} = {total_attack}"
    socketio.emit(
        "system_message",
        {
            "text": f"{attacker_name} hits {mob['name']} with {attacker['weapon']['name']} for {damage} damage ({attack_detail}, AC {mob['ac']}).",
        },
        room=room,
    )
    if mob["hp"] <= 0:
        handle_mob_defeat(mob, killer_name=attacker_name)
    else:
        broadcast_room_state(attacker["x"], attacker["y"])


def pickup_loot(username, loot_identifier):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not loot_identifier:
        return False, "Specify which loot to take."
    loot_identifier = loot_identifier.strip().lower()
    x, y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    entries = room_loot.get((zone, x, y), [])
    match = None
    for entry in entries:
        if entry["id"].lower() == loot_identifier:
            match = entry
            break
    if not match:
        return False, "No such loot lies here."
    room_loot[(zone, x, y)].remove(match)
    if not room_loot[(zone, x, y)]:
        room_loot.pop((zone, x, y), None)
    room = room_name(zone, x, y)
    if match.get("type") == "gold":
        amount = int(match.get("amount") or 0)
        player["gold"] = player.get("gold", 0) + amount
        update_character_gold(player["character_id"], player["gold"])
        message = f"{username} scoops up {amount} gold coins."
    else:
        item_key = match.get("item_key")
        if item_key in GENERAL_ITEMS:
            items = player.setdefault("items", [])
            items.append(item_key)
            update_character_items(player["character_id"], items)
            item_name = GENERAL_ITEMS[item_key]["name"]
            message = f"{username} picks up {item_name}."
        elif item_key in WEAPONS:
            inventory = player.setdefault("inventory", [])
            if item_key not in inventory:
                inventory.append(item_key)
                update_character_weapon_inventory(player["character_id"], inventory)
            item_name = WEAPONS[item_key]["name"]
            message = f"{username} claims {item_name}."
        else:
            items = player.setdefault("items", [])
            items.append(item_key)
            update_character_items(player["character_id"], items)
            item_name = match.get("name", "an item")
            message = f"{username} picks up {item_name}."
    socketio.emit("system_message", {"text": message}, room=room)
    broadcast_room_state(zone, x, y)
    return True, message


def perform_search_action(username):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not check_player_action_gate(username):
        return False, None

    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    room = get_room_info(zone, x, y)
    search_meta = room.get("search")

    mark_player_action(player)
    searched_rooms = player.setdefault("searched_rooms", set())
    location_key = (zone, x, y)

    if not search_meta:
        notify_player(username, "You search around but find nothing unusual.")
        return True, None

    ability_key = (search_meta.get("ability") or "wis").lower()
    ability_mod = player.get("ability_mods", {}).get(ability_key, 0)
    try:
        dc = int(search_meta.get("dc", 10))
    except (TypeError, ValueError):
        dc = 10
    roll = random.randint(1, 20)
    total = roll + ability_mod
    detail = f" (Roll {total} vs DC {dc})"

    if total >= dc:
        success_text = search_meta.get("success_text") or "You uncover something hidden."
        notify_player(username, success_text + detail)
        already_cleared = location_key in searched_rooms
        if already_cleared:
            loot_keys = search_meta.get("loot") or []
            if loot_keys:
                notify_player(username, "You have already recovered the valuables hidden here.")
            return True, None
        searched_rooms.add(location_key)
        loot_keys = search_meta.get("loot") or []
        if loot_keys:
            awarded = []
            for item_key in loot_keys:
                item_name = collect_item_for_player(player, username, item_key)
                if item_name:
                    awarded.append(item_name)
            if awarded:
                notify_player(username, "You obtain " + ", ".join(awarded) + ".")
        return True, None

    failure_text = search_meta.get("failure_text") or "You search around but find nothing unusual."
    notify_player(username, failure_text + detail)
    return True, None


def resolve_attack(attacker_name, target_name):
    attacker = players.get(attacker_name)
    if not attacker:
        return
    if not check_player_action_gate(attacker_name):
        return
    if not target_name:
        notify_player(attacker_name, "Choose a target to attack.")
        return

    target_name = target_name.strip()
    if attacker_name == target_name:
        notify_player(attacker_name, "You cannot attack yourself.")
        return

    target = players.get(target_name)
    attacker_zone = attacker.get("zone", DEFAULT_ZONE)
    if not target:
        mob = find_mob_in_room(target_name, attacker_zone, attacker["x"], attacker["y"])
        if mob:
            mark_player_action(attacker)
            resolve_attack_against_mob(attacker_name, attacker, mob)
            return
        notify_player(attacker_name, f"{target_name} is nowhere to be found.")
        return

    if attacker_zone != target.get("zone", DEFAULT_ZONE) or attacker["x"] != target["x"] or attacker["y"] != target["y"]:
        notify_player(attacker_name, f"{target_name} is not in the same room.")
        return

    recalculate_player_stats(attacker)
    recalculate_player_stats(target)
    mark_player_action(attacker)

    roll = random.randint(1, 20)
    crit = roll == 20
    attack_bonus = attacker["attack_bonus"]
    bonus_rolls = []
    bonus_total = 0
    for bonus in attacker.get("attack_roll_bonus_dice", []):
        extra = roll_dice(bonus.get("dice"))
        bonus_total += extra
        label = bonus.get("label") or format_dice(bonus.get("dice"))
        bonus_rolls.append((label, extra))
    total_attack = roll + attack_bonus + bonus_total
    target_ac = target["ac"]
    room = room_name(attacker_zone, attacker["x"], attacker["y"])

    if not attack_roll_success(roll, total_attack, target_ac):
        bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
        socketio.emit(
            "system_message",
            {
                "text": f"{attacker_name} attacks {target_name} but misses "
                f"(roll {roll} + {attack_bonus}{bonus_text} = {total_attack} vs AC {target_ac})."
            },
            room=room,
        )
        return

    ability_key = attacker["weapon"].get("ability") or attacker["attack_ability"]
    ability_mod = attacker["ability_mods"].get(ability_key, 0)
    damage = roll_weapon_damage(
        attacker["weapon"], ability_mod, crit=crit, bonus_damage=attacker.get("damage_bonus", 0)
    )
    target["hp"] = clamp_hp(target["hp"] - damage, target["max_hp"])
    update_character_current_hp(target["character_id"], target["hp"])

    bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
    attack_detail = (
        f"roll {roll}{' - critical!' if crit else ''} + {attack_bonus}{bonus_text} = {total_attack}"
    )

    socketio.emit(
        "system_message",
        {
            "text": f"{attacker_name} hits {target_name} with {attacker['weapon']['name']} "
            f"for {damage} damage ({attack_detail}, AC {target_ac})."
        },
        room=room,
    )

    send_room_state(attacker_name)
    send_room_state(target_name)

    if target["hp"] == 0:
        socketio.emit(
            "system_message",
            {"text": f"{target_name} collapses from their wounds!"},
            room=room,
        )
        respawn_player(target_name)


# --- Routes ---
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password required.")
            return redirect(url_for("login"))

        if action == "register":
            if get_account(username):
                flash("Username already taken.")
                return redirect(url_for("login"))
            create_account(username, password)
            flash("Account created. You can now log in.")
            return redirect(url_for("login"))
        elif action == "login":
            account = get_account(username)
            if not account or not check_password_hash(account["password_hash"], password):
                flash("Invalid username or password.")
                return redirect(url_for("login"))
            session.clear()
            session["account_id"] = account["id"]
            session["account_username"] = account["username"]
            return redirect(url_for("character_select"))

        flash("Invalid action.")
        return redirect(url_for("login"))

    if session.get("account_id"):
        return redirect(url_for("character_select"))
    return render_template("login.html")


@app.route("/characters")
def character_select():
    if "account_id" not in session:
        return redirect(url_for("login"))
    account_id = session["account_id"]
    characters = get_account_characters(account_id)
    return render_template(
        "characters.html",
        account_username=session.get("account_username"),
        characters=characters,
        max_characters=MAX_CHARACTERS_PER_ACCOUNT,
    )


@app.route("/characters/new", methods=["GET", "POST"])
def new_character():
    if "account_id" not in session:
        return redirect(url_for("login"))
    account_id = session["account_id"]
    character_count = count_account_characters(account_id)
    if character_count >= MAX_CHARACTERS_PER_ACCOUNT and request.method == "GET":
        flash("You already have the maximum number of characters.")
        return redirect(url_for("character_select"))

    rolls = session.get("rolled_scores")
    if not rolls:
        rolls = generate_base_scores()
    rolls = {ability: int((rolls or {}).get(ability, 10)) for ability in ABILITY_KEYS}
    session["rolled_scores"] = rolls

    if request.method == "POST":
        action = request.form.get("action")
        if action == "roll":
            session["rolled_scores"] = generate_base_scores()
            return redirect(url_for("new_character"))
        elif action == "create":
            if count_account_characters(account_id) >= MAX_CHARACTERS_PER_ACCOUNT:
                flash("You already have the maximum number of characters.")
                return redirect(url_for("character_select"))
            name = request.form.get("name", "").strip()
            race_choice = request.form.get("race")
            class_choice = request.form.get("char_class")
            bio = (request.form.get("bio") or "").strip()
            description = (request.form.get("description") or "").strip()
            if not name:
                flash("Character name is required.")
                return redirect(url_for("new_character"))
            if len(name) > 40:
                flash("Character names must be 40 characters or fewer.")
                return redirect(url_for("new_character"))
            if get_character_by_name(name):
                flash("Character name already taken.")
                return redirect(url_for("new_character"))
            race_choice = normalize_choice(race_choice, RACES, None)
            class_choice = normalize_choice(class_choice, CLASSES, None)
            if not race_choice or not class_choice:
                flash("Select a valid race and class.")
                return redirect(url_for("new_character"))
            ability_scores = {}
            try:
                for ability in ABILITY_KEYS:
                    raw = request.form.get(f"ability_{ability}")
                    if raw is None or raw.strip() == "":
                        raw = rolls.get(ability)
                    value = int(raw)
                    ability_scores[ability] = max(1, min(value, 30))
            except (TypeError, ValueError):
                flash("Ability scores must be numbers.")
                return redirect(url_for("new_character"))
            if len(bio) > 500 or len(description) > 1000:
                flash("Bio or description is too long.")
                return redirect(url_for("new_character"))
            create_character(account_id, name, race_choice, class_choice, ability_scores, bio, description)
            session.pop("rolled_scores", None)
            flash(f"{name} has been created.")
            return redirect(url_for("character_select"))

    return render_template(
        "new_character.html",
        account_username=session.get("account_username"),
        rolls=rolls,
        race_options=RACE_OPTIONS,
        class_options=CLASS_OPTIONS,
        ability_keys=ABILITY_KEYS,
        max_characters=MAX_CHARACTERS_PER_ACCOUNT,
    )


@app.route("/characters/play/<int:character_id>", methods=["POST"])
def play_character(character_id):
    if "account_id" not in session:
        return redirect(url_for("login"))
    record = get_character_by_id(character_id)
    if not record or record["account_id"] != session["account_id"]:
        flash("Character not found.")
        return redirect(url_for("character_select"))
    session["character_id"] = record["id"]
    session["character_name"] = record["name"]
    session.pop("rolled_scores", None)
    existing = players.get(record["name"])
    if existing:
        update_character_current_hp(existing["character_id"], existing["hp"])
        players.pop(record["name"], None)
    return redirect(url_for("game"))


@app.route("/characters/delete/<int:character_id>", methods=["POST"])
def delete_character_route(character_id):
    if "account_id" not in session:
        return redirect(url_for("login"))
    record = get_character_by_id(character_id)
    if not record or record["account_id"] != session["account_id"]:
        flash("Character not found.")
        return redirect(url_for("character_select"))
    players.pop(record["name"], None)
    if session.get("character_id") == character_id:
        session.pop("character_id", None)
        session.pop("character_name", None)
    if delete_character(session["account_id"], character_id):
        flash(f"{record['name']} was deleted.")
    else:
        flash("Unable to delete character.")
    return redirect(url_for("character_select"))


@app.route("/game")
def game():
    if "account_id" not in session:
        return redirect(url_for("login"))
    if "character_id" not in session:
        return redirect(url_for("character_select"))
    return render_template(
        "game.html",
        account_username=session.get("account_username"),
        character_name=session.get("character_name"),
    )


@app.route("/logout")
def logout():
    character_name = session.get("character_name")
    if character_name and character_name in players:
        update_character_current_hp(players[character_name]["character_id"], players[character_name]["hp"])
        players.pop(character_name, None)
    session.clear()
    return redirect(url_for("login"))


# --- Socket.IO events ---

@socketio.on("connect")
def on_connect():
    if "account_id" not in session or "character_id" not in session or "character_name" not in session:
        disconnect()
        return
    emit("connected", {"message": "Connected to game server."})


@socketio.on("join_game")
def on_join_game():
    account_id = session.get("account_id")
    character_id = session.get("character_id")
    character_name = session.get("character_name")
    if not account_id or not character_id or not character_name:
        emit("system_message", {"text": "You are not logged in. Please reconnect."})
        disconnect()
        return

    record = get_character_by_id(character_id)
    if not record or record.get("account_id") != account_id or record.get("name") != character_name:
        emit("system_message", {"text": "Unable to load your character. Please log in again."})
        disconnect()
        return

    if character_name not in players:
        state = build_player_state(record, request.sid)
    else:
        existing = players[character_name]
        preserved_zone = existing.get("zone", DEFAULT_ZONE)
        start_x, start_y = get_world_start(preserved_zone)
        preserved_position = (existing.get("x", start_x), existing.get("y", start_y))
        preserved_hp = clamp_hp(existing.get("hp"), existing.get("max_hp", 1))
        preserved_effects = list(existing.get("active_effects", []))
        preserved_cooldowns = dict(existing.get("cooldowns", {}))
        state = build_player_state(record, request.sid)
        state["zone"] = preserved_zone
        state["x"], state["y"] = preserved_position
        state["hp"] = preserved_hp
        state["active_effects"] = preserved_effects
        state["cooldowns"] = preserved_cooldowns
        state["last_action_ts"] = existing.get("last_action_ts", 0)
        state["searched_rooms"] = set(existing.get("searched_rooms", set()))
        recalculate_player_stats(state)

    state["character_id"] = record["id"]
    state["account_id"] = account_id
    state["name"] = record["name"]
    players[character_name] = state

    x = state["x"]
    y = state["y"]
    zone = state.get("zone", DEFAULT_ZONE)
    rname = room_name(zone, x, y)

    join_room(rname)

    emit("system_message", {"text": f"{character_name} has entered the room."}, room=rname, include_self=False)

    send_room_state(character_name)
    trigger_aggressive_mobs_for_player(character_name, x, y)


def handle_travel_portal(username):
    player = players.get(username)
    if not player:
        return False
    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    room = get_room_info(zone, x, y)
    travel = room.get("travel_to")
    if not travel:
        return False
    target_zone = travel.get("zone")
    if not target_zone or target_zone not in WORLDS:
        return False
    target_world = get_world(target_zone)
    destination = travel.get("start")
    if isinstance(destination, (list, tuple)) and len(destination) == 2:
        tx, ty = destination
    else:
        tx, ty = target_world["start"]
    width, height = target_world["width"], target_world["height"]
    if not (0 <= tx < width and 0 <= ty < height):
        tx, ty = target_world["start"]

    origin_zone = zone
    source_room = room_name(origin_zone, x, y)
    leave_room(source_room)
    socketio.emit(
        "system_message",
        {"text": f"{username} presses the warp stone and vanishes in a burst of light."},
        room=source_room,
    )
    broadcast_room_state(origin_zone, x, y)

    player["zone"] = target_zone
    player["x"], player["y"] = tx, ty
    destination_room = room_name(target_zone, tx, ty)
    join_room(destination_room)
    socketio.emit(
        "system_message",
        {"text": f"{username} coalesces beside the warp stone in a shimmer of light."},
        room=destination_room,
        include_self=False,
    )
    world_name = target_world.get("name", target_zone.title())
    dest_info = get_room_info(target_zone, tx, ty)
    notify_player(username, f"The warp stone pulls you to {world_name}: {dest_info['name']}.")
    send_room_state(username)
    trigger_aggressive_mobs_for_player(username, tx, ty)
    broadcast_room_state(target_zone, tx, ty)
    return True


@socketio.on("move")
def on_move(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    if not check_player_action_gate(username):
        return

    direction = (data.get("direction") or "").lower()
    player = players[username]
    old_x, old_y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    width, height = get_world_dimensions(zone)
    if direction not in DIRECTION_VECTORS:
        return
    dx, dy = DIRECTION_VECTORS[direction]
    new_x, new_y = old_x + dx, old_y + dy

    # Bounds check
    if not (0 <= new_x < width and 0 <= new_y < height):
        emit("system_message", {"text": "You cannot go that way."})
        return

    door_id = get_door_id(zone, old_x, old_y, direction)
    if door_id and not is_door_open(door_id):
        door = DOORS.get(door_id)
        door_name = door.get("name") if door else "The door"
        notify_player(username, f"{door_name} is closed.")
        send_room_state(username)
        return

    old_room = room_name(zone, old_x, old_y)
    new_room = room_name(zone, new_x, new_y)

    if (new_x, new_y) == (old_x, old_y):
        # no move
        return

    # Update player position
    disengage_player_from_room_mobs(username, old_x, old_y)
    player["x"], player["y"] = new_x, new_y

    # Leave old room, notify others
    leave_room(old_room)
    emit("system_message", {"text": f"{username} has left the room."}, room=old_room)

    # Join new room, notify others
    join_room(new_room)
    emit("system_message", {"text": f"{username} has entered the room."}, room=new_room, include_self=False)

    # Send new room state to moving player
    mark_player_action(player)
    send_room_state(username)
    trigger_aggressive_mobs_for_player(username, player["x"], player["y"])


@socketio.on("activate_warp")
def on_activate_warp():
    username = session.get("character_name")
    if not username or username not in players:
        return
    if not check_player_action_gate(username):
        return

    player = players[username]
    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    room = get_room_info(zone, x, y)
    if not room.get("travel_to"):
        notify_player(username, "No warp stone responds in this room.")
        send_room_state(username)
        return

    mark_player_action(player)
    if not handle_travel_portal(username):
        notify_player(username, "The warp stone flickers but does not take hold.")
        send_room_state(username)


@socketio.on("door_action")
def on_door_action(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    if not check_player_action_gate(username):
        return

    payload = data or {}
    door_id = payload.get("door_id")
    action = (payload.get("action") or "").lower()
    door = DOORS.get(door_id)
    if not door:
        notify_player(username, "That door does not seem to exist.")
        return

    player = players[username]
    zone = player.get("zone", DEFAULT_ZONE)
    coords = (player["x"], player["y"])
    facing = None
    for endpoint in door.get("endpoints", []):
        if endpoint["zone"] == zone and endpoint["coords"] == coords:
            facing = endpoint["direction"]
            break
    if not facing:
        notify_player(username, "You are not close enough to that door.")
        send_room_state(username)
        return

    if action == "open":
        if door.get("state") == "open":
            notify_player(username, f"The {door['name']} is already open.")
            return
        door["state"] = "open"
        verb = "opens"
        feedback = f"You swing the {door['name']} open."
    elif action == "close":
        if door.get("state") == "closed":
            notify_player(username, f"The {door['name']} is already closed.")
            return
        door["state"] = "closed"
        verb = "closes"
        feedback = f"You pull the {door['name']} closed."
    else:
        notify_player(username, "You must choose to open or close the door.")
        return

    mark_player_action(player)
    notify_player(username, feedback)

    touched_rooms = set()
    for endpoint in door.get("endpoints", []):
        z = endpoint["zone"]
        ex, ey = endpoint["coords"]
        room_key = (z, ex, ey)
        if room_key in touched_rooms:
            continue
        touched_rooms.add(room_key)
        room_channel = room_name(z, ex, ey)
        include_self = not (z == zone and (ex, ey) == coords)
        socketio.emit(
            "system_message",
            {"text": f"{username} {verb} the {door['name']}."},
            room=room_channel,
            include_self=include_self,
        )
        broadcast_room_state(z, ex, ey)


@socketio.on("equip_weapon")
def on_equip_weapon(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    weapon_key = (data or {}).get("weapon") or (data or {}).get("weapon_key")
    success, message = equip_weapon_for_player(username, weapon_key)
    if not success:
        notify_player(username, message)


@socketio.on("cast_spell")
def on_cast_spell(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    payload = data or {}
    spell_identifier = payload.get("spell") or payload.get("spell_key") or payload.get("name")
    target = payload.get("target") or payload.get("target_name")
    success, message = cast_spell_for_player(username, spell_identifier, target)
    if not success and message:
        notify_player(username, message)


@socketio.on("pickup_loot")
def on_pickup_loot(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    payload = data or {}
    loot_id = payload.get("loot_id") or payload.get("id") or payload.get("loot")
    success, message = pickup_loot(username, loot_id)
    if not success and message:
        notify_player(username, message)


@socketio.on("search")
def on_search_event(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    success, message = perform_search_action(username)
    if not success and message:
        notify_player(username, message)


@socketio.on("chat")
def on_chat(data):
    username = session.get("character_name")
    if not username or username not in players:
        return

    text = (data.get("text") or "").strip()
    if not text:
        return

    if text.startswith("/"):
        handled = handle_command(username, text[1:])
        if handled:
            return

    player = players[username]
    x, y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    rname = room_name(zone, x, y)

    emit("chat_message", {"from": username, "text": text}, room=rname)


@socketio.on("disconnect")
def on_disconnect():
    # We can try to identify the user by sid
    username = None
    for u, p in list(players.items()):
        if p["sid"] == request.sid:
            username = u
            break

    if username:
        player = players.get(username)
        if player:
            x, y = player["x"], player["y"]
            zone = player.get("zone", DEFAULT_ZONE)
            rname = room_name(zone, x, y)
        else:
            rname = None
            x = y = 0
        disengage_player_from_room_mobs(username, x, y)
        # Notify others
        if rname:
            emit("system_message", {"text": f"{username} has disconnected."}, room=rname)
        update_character_current_hp(players[username]["character_id"], players[username]["hp"])
        # Remove from players (MVP: no persistent positions)
        players.pop(username, None)


if not mobs:
    spawn_initial_mobs()


if __name__ == "__main__":
    init_db()
    # Bind to 0.0.0.0 for container use
    socketio.run(app, host="0.0.0.0", port=5000)
