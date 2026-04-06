"""
word_banks.py — Raw material for algorithmic and Markov-chain prompt generation.

Each list is a grab-bag of vivid, specific entries.  The generator samples from these;
the LLM polishes the result.  Add entries freely — unusual beats common for diversity.

Sampling helpers at the bottom:  subject(), action(), setting(), etc.
"""

import random

# ── Subjects ───────────────────────────────────────────────────────────────────

SUBJECTS_STEINBECK = [
    "a crop picker alone in a lettuce field at 5am",
    "a jalopy loaded with everything a family owns, springs showing",
    "an Okie mother counting coins on a diner counter",
    "a man with a dog watching freight trains pass",
    "a cannery worker hosing down a concrete floor at dawn",
    "a bunkhouse door swinging open on an empty room",
    "a migrant family eating beans from a single pot",
    "a young farmhand asleep against a tractor wheel",
    "an old ranch hand mending fence wire alone in the heat",
    "a woman in a faded house dress hanging laundry between two poplars",
    "a foreman's truck raising dust on a dirt road at 6am",
    "three pickers huddled under a lean-to during sudden rain",
    "a child holding a cardboard suitcase outside a Greyhound station",
    "a man reading a folded newspaper by a gas station sign",
    "a waitress refilling coffee cups in a diner nobody else is in",
    "a family stopped at a roadside peach stand in the Sierras",
    "a dog nosing through garbage at the edge of a labor camp",
    "an old couple sitting on a stoop watching traffic that isn't coming",
    "a man who has walked further than he intended",
    "a woman with big knuckles and perfect posture eating a sandwich alone",
]

SUBJECTS_PKD = [
    "an android watering a plastic houseplant with a real watering can",
    "a man watching commercials on a TV in an empty house",
    "a woman who is almost certain she is not a replicant",
    "a pay phone ringing in an empty lot at 3pm",
    "kipple spreading across a kitchen table — old batteries, receipts, nothing useful",
    "a man in a bathrobe standing in a perfect California front yard",
    "a technician calibrating an empathy box in a grey studio apartment",
    "a neighbor whose smile doesn't quite match what his eyes are doing",
    "a Voigt-Kampff machine on a folding table in an empty warehouse",
    "a bus stop where everyone is looking at a slightly different version of the same ad",
    "a repairman who fixes appliances that aren't broken yet",
    "a woman buying canned goods in a supermarket that smells faintly wrong",
    "a man who can't remember whether he drove to work or was driven",
    "a police cruiser idling outside a house where nothing is technically wrong",
    "a child with a toy that is identical to the device the adult is holding",
    "a man reading a memo he wrote but doesn't remember writing",
    "an apartment where everything is slightly too clean to have been lived in",
    "a woman watching her hands as if they might do something without her",
    "a news anchor reading the same story a second time, slightly differently",
    "a door in a 1963 California suburb that opens onto something it shouldn't",
]

SUBJECTS_BRAUTIGAN = [
    "a fisherman leaning a rod against a 1940s hotel front desk",
    "a jar of watermelon sugar catching afternoon light on a windowsill",
    "a man reading a paperback in a trout stream, shoes dry somehow",
    "a library full of books nobody wanted, lovingly organized",
    "a woman making pie at a commune that runs on kindness and optimism",
    "a child counting pine needles on a Big Sur trail, unhurried",
    "a dog sleeping on the porch of a cabin that needs painting",
    "a man writing poetry on a lunch bag at a picnic table",
    "two people splitting a bottle of wine on a fire escape in 1968",
    "a hitchhiker with too many books in a paper grocery bag",
    "a postcard arriving from a town absorbed into a reservoir in 1962",
    "a woman feeding ducks in a city park in a dress from 1965",
    "a guy who builds things out of whatever's available and doesn't explain them",
    "a girl who names every trout she catches and throws them back",
    "a very small band playing very quietly in a field for themselves",
]

SUBJECTS_BUTLER = [
    "a woman walking through a burning Los Angeles neighborhood with a notebook",
    "a girl discovering she feels everyone else's pain as her own",
    "a man who woke up on a plantation in 1815 and knows exactly what year it is",
    "a woman who can heal any wound by taking it into herself",
    "a child who hasn't slept in three days running toward something on fire",
    "an elder teaching survival skills to a circle of frightened teenagers",
    "a woman with marks on her skin that no one else can read correctly",
    "a community planting a garden inside a walled compound at dawn",
    "a young man who changes form when frightened and has learned not to be frightened",
    "a woman reading her own future in a stranger's posture",
    "a group moving through a burned-out block carrying everything that matters",
    "a healer whose gift costs her something every time she uses it",
    "a girl who can make plants grow but only when she's sad",
    "a woman who has outlived everyone she started with",
    "a man trying to explain something to people who won't survive if they don't understand it",
]

SUBJECTS_NOON = [
    "a ravegoer covered in yellow pollen stumbling out at dawn",
    "a robodog sniffing a Manchester canal towpath in the rain",
    "shadow people slipping between parked cars on Oldham Street",
    "a taxi driver handing a passenger a blue feather and driving away",
    "a DJ playing records with a robotic arm they can't fully control",
    "a clubber whose shadow stays behind on the dancefloor",
    "a woman who only exists between midnight and 5am",
    "a courier delivering a package that vibrates gently and smells of cinnamon",
    "a man growing feathers in a Salford bedsit with no explanation",
    "a detective who can only see crimes happening in reverse",
    "a fox trotting through the empty Haçienda at last call, tail lit neon green",
    "a girl who finds a door in a Manchester alley that goes to 1978",
    "a bouncer who has not moved since Tuesday, and knows it",
    "a woman with chlorophyll in her bloodstream",
    "a man who is slowly becoming a map of a city he's never visited",
]

SUBJECTS_ROBBINS = [
    "a hitchhiker with an impossibly large thumb on Route 5",
    "a can of Prince Albert tobacco spinning slowly in zero gravity",
    "a talking beet sitting upright in a folding chair",
    "a red-haired woman in a Winnebago writing equations on the windshield",
    "a philosopher who drives a school bus and argues with the mirrors",
    "a woman selling enlightenment from a converted Airstream",
    "a man who turned into a can opener at forty and made peace with it",
    "a roadside prophet with excellent posture and a sandwich board that says WHAT IF",
    "a woman with a pet hummingbird that functions as her subconscious",
    "a trucker hauling a cargo of unopened birthday presents across Nevada",
    "a retired shaman running a bait shop that smells of sage and WD-40",
    "a child who narrates everything happening to them in the third person, accurately",
    "a thumb so large it has its own weather",
    "a woman who has been writing the same letter since 1971 and is almost done",
    "a man whose vehicle is always exactly the right size for what he needs to carry",
]

SUBJECTS_KING = [
    # The Shining
    "two identical girls at the end of a hotel corridor",
    "a room in a hotel that the cleaning staff refuses to enter",
    "a man who writes the same sentence in his sleep every night",
    "a hedge maze in hard winter light, footprints leading in but not out",
    # The Dark Tower
    "a gunslinger crossing a desert that has no end, following a figure ahead",
    "a door standing alone on a beach with no building around it",
    "a boy who speaks in a voice that belongs to someone long dead",
    "a tower on the horizon visible from everywhere, belonging to no landscape",
    # ESP / Firestarter / Carrie
    "a child who sets things on fire without touching them, standing very still",
    "a girl who moved something with her mind and hasn't told anyone",
    "a government vehicle parked outside a school, same spot, three days running",
    "a child pressing both palms flat on a table, eyes closed, concentrating",
    # General King dread
    "a mailbox that keeps filling with yesterday's newspaper",
    "a pet cemetery tilting toward the tree line in Maine",
    "a car that started itself in a locked garage",
    "a telephone ringing in a house where everyone is outside",
    "a woman who has started keeping a list of things that have moved on their own",
    "a crow that keeps calling in a voice the neighbors half-recognize",
    "a library book returned 40 years late with a note written in someone else's blood",
    "a man who knows the exact date of his death and keeps rescheduling things anyway",
    "a dog that won't come out of the fog no matter what you call",
]

SUBJECTS_KAFKA = [
    "a man who arrived for an appointment no one will explain",
    "a clerk stamping forms in an office with no windows and no apparent exit",
    "a figure in a grey suit waiting in a queue that hasn't moved since morning",
    "a man who woke up as a large insect, lying on his back, legs in the air",
    "an office worker holding a memo nobody sent, addressed to a name close to his",
    "a bureaucrat whose desk acquires new stacks of paper overnight",
    "a man who cannot find the department that handles his particular problem",
    "a woman being processed for something she hasn't done and cannot contest",
    "a guard who cannot say what he's guarding or why",
    "a petitioner who has filled out the same form for six years",
    "a man who received a summons for a trial scheduled before he was born",
    "an inspector inspecting inspectors who are inspecting other inspectors",
    "a man whose identity documents are all correct but describe someone slightly different",
    "a woman trying to leave a building that keeps acquiring new hallways",
]

SUBJECTS_GENERAL = [
    # Human figures
    "a lone astronaut",
    "a samurai standing at the edge of a rice paddy",
    "a child with a red umbrella",
    "an old fisherman",
    "a dancer in silk",
    "a street musician",
    "a deep-sea diver",
    "a monk in orange robes",
    "a knight in rusted armor",
    "twin sisters",
    "a figure in a long coat",
    "a small girl in a yellow raincoat",
    "a tall man in a hat who is not quite there",
    "a surgeon in scrubs sitting on a curb in the rain",
    "a cosmonaut eating borscht in a weightless cabin",
    "a skeleton in a tuxedo at a grand piano, playing something slow",
    "a marching band in a wheat field with no audience",
    "a pair of hands making origami in darkness with one lamp",
    "an old woman on a porch watching a thunderstorm roll in, not moving",
    "a line of schoolchildren walking into the ocean and not stopping",
    "a tired waitress counting tips at 2am beside a spinning pie rack",
    "a man in a diving suit in a parking lot",
    "a woman with a suitcase full of clocks, all set to different times",
    "a child conducting an orchestra that isn't there",
    # Animals — mammals
    "a red fox",
    "a wolf in deep snow",
    "a bear emerging from fog",
    "a very old horse standing in an empty field in the rain",
    "a polar bear in a shopping mall at closing time",
    "a stag standing in a lit intersection, breathing steam",
    "a coyote crossing a highway overpass at 3am",
    "a raccoon carefully washing something bright in a city fountain",
    "a family of river otters sleeping curled in the current",
    "a tiger walking through a flooded temple courtyard",
    "a lone elephant at a watering hole as the sun drops below the acacia line",
    "a gorilla on a park bench turning the pages of a newspaper",
    "a giraffe peering through the glass wall of a fourth-floor office",
    "a capybara in a thermal spring, surrounded by smaller animals keeping warm",
    "a manatee drifting past a porthole, eye level with the observer",
    "a narwhal at the ice edge, horn disappearing into fog",
    # Animals — birds
    "a crow on a telephone wire",
    "a mechanical owl with one eye that doesn't track",
    "a great horned owl on a fence post in hard snow, perfectly still",
    "a hawk perched on a fire hydrant, watching something in the gutter",
    "a flamingo standing alone in a strip-mall parking lot at noon",
    "a peacock on a crumbling marble staircase",
    "a crane standing in a flooded rice paddy at first light",
    "a colony of bats exploding from a cave mouth at dusk, seen from below",
    "a pelican skimming six inches above a flat grey sea",
    "a child in a swan paddle-boat in a city fountain",
    # Animals — reptiles, sea creatures, insects
    "a rat in a train ditch with a giant pizza slice",
    "a cat on a rooftop watching pigeons it has decided not to chase",
    "an octopus reaching one arm through an open porthole into a lit cabin",
    "an alligator in the slow lane of a highway at first light",
    "a whale breaching at the edge of sunset, silhouette against orange",
    "a school of jellyfish drifting upward through a column of blue light",
    "a praying mantis on the rim of a coffee cup, studying the steam",
]

SUBJECTS = (
    SUBJECTS_STEINBECK + SUBJECTS_PKD + SUBJECTS_BRAUTIGAN +
    SUBJECTS_BUTLER + SUBJECTS_NOON + SUBJECTS_ROBBINS +
    SUBJECTS_KING + SUBJECTS_KAFKA + SUBJECTS_GENERAL
)

# ── Actions ────────────────────────────────────────────────────────────────────

ACTIONS_TRAJECTORY = [
    "walks slowly left to right across the frame",
    "a door swings slowly open with no one touching it",
    "a hand reaches into frame from the left",
    "turns slowly to face the camera",
    "a single leaf falls straight down through still air",
    "steam rises from a sidewalk grate",
    "runs directly away from camera into fog",
    "a truck passes left to right through a static frame",
    "a figure crests a hill and stops",
    "a window light switches off",
    "a hand sets something heavy down on a table",
    "walks directly toward camera and does not stop",
    "turns and walks away without looking back",
    "climbs a fire escape one rung at a time",
    "descends stairs into darkness",
    "crosses a street without looking both ways",
    "emerges from water slowly",
    "falls backward into tall grass",
    "steps into a doorway and pauses",
    "wheels a bicycle across a gravel lot",
    "kneels and does not get up",
    "stands at a window watching rain",
    "opens a refrigerator and stares into it",
    "sits down heavily on a curb",
    "walks along a chain-link fence, trailing one hand",
    "pushes through a bead curtain and stops",
    "crosses a frozen lake in a straight line",
    "moves through a crowded room without touching anyone",
    "climbs onto a car roof and lies down",
    "walks backward into a building",
]

ACTIONS_CHARACTER = [
    "counts change on a counter very slowly",
    "tapes a handwritten note to a lamppost",
    "watches a test pattern on a television at midnight",
    "opens a door that shouldn't exist",
    "walks the length of a freight train",
    "drives through the same intersection three times",
    "stares into the middle distance",
    "dances alone with eyes closed",
    "sits watching the horizon not change",
    "writes a word in the condensation on a window",
    "tears a photograph in half very carefully",
    "holds a phone that isn't ringing",
    "fills a glass of water and doesn't drink it",
    "reads a letter and folds it back up",
    "packs a bag slowly and then unpacks it",
    "draws a circle on a piece of paper over and over",
    "holds both hands near a candle flame",
    "buttons a coat one button at a time",
    "waits at a bus stop as three buses go past without stopping",
    "waters a plant that is clearly already dead",
    "writes an address on an envelope and then crosses it out",
    "holds a pocket watch open watching the seconds hand",
    "stirs a cup of coffee until it is cold",
    "peels an orange very precisely into one unbroken spiral",
    "writes the same word on different pieces of paper",
    "assembles something carefully from a pile of parts",
    "stands in front of a mirror and does nothing",
    "puts on a record and sits down before it starts",
    "rolls up a map and puts it away without looking at it",
    "writes in a notebook in the dark by feel",
]

ACTIONS_WEIRD = [
    "ATARI video games from the 1970s blinking to life on a television",
    "pages of a notebook turning in wind with no wind",
    "a crow drops something bright and red onto wet pavement",
    "the same man passes the same corner three times in one shot",
    "a woman writes in a notebook while everything around her burns gently",
    "a television changes channels by itself",
    "shadows on a wall move opposite to the light source",
    "a balloon drifts up through a room with closed windows",
    "a clock runs backwards at exactly the right speed",
    "a cup slides across a table with no one touching it",
    "a door opens to reveal another door, identical",
    "rain falls upward outside a single window",
    "the hands of a stopped clock begin to move",
    "a dog walks through a wall and comes out the other side confused",
    "a figure in a mirror is slightly out of sync",
    "an empty rocking chair moves in a room with no breeze",
    "a newspaper headline changes between one reading and the next",
    "a child's drawing hangs on a wall but the child in it is now facing away",
    "all the clocks in a house stop at different times",
    "a snowglobe shakes itself",
]

ACTIONS = ACTIONS_TRAJECTORY + ACTIONS_CHARACTER + ACTIONS_WEIRD

# ── Settings ───────────────────────────────────────────────────────────────────

SETTINGS_AMERICAN_REALISM = [
    "a Route 66 diner at 3am with one waitress and no customers",
    "a Dust Bowl farmhouse with one window lit, flat land to every horizon",
    "a Salinas Valley lettuce field in morning fog, irrigation ditches running silver",
    "a migrant labor camp at sunrise, a single laundry line, a man eating alone",
    "a Greyhound bus interior at night, headlights of oncoming trucks",
    "an empty boxcar sliding through Nebraska",
    "an Iowa gas station at dawn, pumps still showing 1979 prices",
    "a cotton field in August, no shade for three miles",
    "a roadside motel with a burnt-out vacancy sign, two cars in the lot",
    "a railroad switching yard in the dark, signal lamps blinking",
    "a peach orchard in the Central Valley, trees perfect in their rows",
    "the parking lot of a closed Woolworths on a Sunday morning",
    "a county fair midway at 9pm, half the lights out, four people left",
    "an unemployment office in 1933, men in hats in a queue out the door",
    "a grain elevator standing alone in a flat Kansas prairie",
    "a sharecropper's porch looking out over 40 acres he doesn't own",
    "a truck stop outside Barstow, 2am, three semis idling",
    "a strawberry flat in Watsonville, pickers bent double in a row",
    "a highway rest stop at 4am where a family sleeps in their car",
    "a Salvation Army store in Bakersfield, everything fifty cents",
]

SETTINGS_SUBURBAN_UNEASE = [
    "a 1960s California suburb where the hedges are too perfect",
    "a garage filled with kipple — broken appliances, old Sears catalogs, nothing useful",
    "a living room where the TV plays commercials and nobody is watching",
    "an apartment building hallway that goes on slightly longer than it should",
    "a cul-de-sac at 11am where every car is in every driveway",
    "a backyard in Pomona in July, above-ground pool, no one in it",
    "a tract house kitchen in 1963, every appliance harvest gold",
    "a strip mall parking lot at 2pm — three cars, one person, no destination evident",
    "a master bedroom in 1975, avocado green, a TV tray with a single glass",
    "a neighbor's house where the lights are always on and no one ever comes outside",
    "a subdivision under construction — finished streets, no houses, signs for nowhere",
    "a school gymnasium on a Saturday, lights flickering at the far end",
    "a church parking lot, empty, one shopping cart orbiting slowly",
    "a park in Anaheim where no child is playing",
    "a dentist's waiting room with a fish tank and magazines from four years ago",
    "a backyard barbecue that everyone left an hour ago",
    "a community pool at noon in August, one kid, no lifeguard",
    "a supermarket at 7am when the shelves are fully stocked and utterly silent",
    "a car wash in Pomona, Sunday, a man who has been there since morning",
]

SETTINGS_GENTLE_ELSEWHERE = [
    "a Big Sur campfire with fog coming in from the ocean",
    "a 1970s Winnebago parked in a field of sunflowers, engine off, door open",
    "the inside of a bait shop in a town that doesn't exist on the map",
    "a commune dining table set for twelve with no one sitting yet",
    "a fire lookout tower in Oregon, August, smoke on three horizons",
    "a Berkeley co-op kitchen at midnight, three people making soup",
    "a VW van parked on a cliff above the Pacific, curtains open",
    "a public library in a small Vermont town, nobody under 60",
    "a cabin porch in the Cascades, rain on the metal roof",
    "a bookstore in Portland with cats and no organization system",
    "a kitchen garden in Bolinas, tomatoes staked with driftwood",
    "a narrow boat on a canal in England at dawn, tea steaming on the deck",
    "a used record store in Eugene, everything in the wrong bin",
    "a hot spring in the mountains at dusk, no one else there",
    "a food co-op in 1974, a hand-lettered sign about wheat berries",
]

SETTINGS_DREAD = [
    "an Overlook Hotel ballroom — chairs arranged perfectly, chandeliers lit, no one home",
    "a Derry storm drain at the end of a dead-end street, late October",
    "a pet cemetery at the edge of the woods in Maine, markers tilting",
    "an elementary school gymnasium on a Saturday, lights flickering",
    "a motel room where the clock radio turns on at 3am every night",
    "a hospital corridor at 4am, nurses' station empty, call light blinking",
    "a small-town police station, one deputy, a CB radio, 2am",
    "a fairground after close — Ferris wheel still turning, no operator",
    "a basement with a pull-chain lightbulb and something in the corner",
    "a Maine fog-bank at the edge of a field, treeline invisible",
    "an attic full of someone else's childhood in labeled boxes",
    "the end of a dock over black water, one boat missing",
    "a playground at midnight, swing moving gently, no wind",
    "an empty mall food court at 8pm, one pretzel stand still open",
    "a farmhouse with every curtain drawn and one light on in the back",
    "a carnival ride operating in the rain with no attendant",
    "a children's section of a library where the books are all wrong",
    "a house where someone has recently moved out, everything echoing",
    "the basement of a church at 3am, a single folding chair in the center",
    "a road that is taking longer than it should",
]

SETTINGS_SF = [
    "an empathy box in a studio apartment, grey morning light",
    "a Manchester underground rave at 6am, strobe lights and pollen dust",
    "a replicant's apartment — too sparse, one fake plant, someone else's family photo",
    "a Blade Runner rooftop in rain, neon reflecting in standing water",
    "an android repair shop, bodies stacked like appliances awaiting service",
    "a Martian colony canteen, everyone eating in silence",
    "a VR parlor where the headsets are all the same model and nobody is moving",
    "a precinct room where all the officers are slightly different versions of the same person",
    "a memory parlor — you sit in a chair and rent someone else's past for an hour",
    "a generation ship's common room, year 200, original destination forgotten",
    "a Manchester basement coated in yellow pollen, bass frequencies visible in the dust",
    "a genetics lab, very clean, a baby in a jar no one looks at directly",
    "an interplanetary customs office, orange plastic chairs, fluorescent light",
    "a sleep clinic where everyone is dreaming the same thing",
    "a retrofitted freighter hauling unspecified cargo through an unnamed system",
]

SETTINGS_KAFKA = [
    "an office corridor that goes on slightly longer than any building could contain",
    "a waiting room where everyone holds a number but no number is ever called",
    "a trial in a courtroom where no one knows the charge, the judge eating lunch",
    "a door marked EXIT that opens onto another waiting room, one chair moved",
    "a government ministry where every desk is stacked to the ceiling with folders",
    "a processing center where the queue feeds back into itself",
    "a checkpoint where the correct form is never the form you have",
    "an archive room where the files are organized by a system no one will explain",
    "a hearing room where the petitioner is allowed to speak only during adjournment",
    "a border crossing that is never open and never officially closed",
    "a registry office where the registrar is being registered",
    "a form with a hundred pages, the last page always blank",
    "a building where every staircase goes to a different floor than labeled",
    "an office where the in-box is screwed to the ceiling",
]

SETTINGS_RETRO_TV = [
    "a test pattern on a color TV from 1974, the static between channels at 2am",
    "a public access show with a cheap green curtain and one phone-in caller",
    "a 1960s game show set with giant foam letters and a hostess in white gloves",
    "an 8mm home movie of a birthday party playing on a white wall in a dark room",
    "Saturday morning cartoons reflected in a bowl of cereal going soggy",
    "a local news anchor reading tomorrow's weather, 1987",
    "a UHF station sign-off — the national anthem, a minute of static, then nothing",
    "a Betamax playing a recording of a recording of a recording of a news broadcast",
    "a broadcast control room at a local affiliate at 3am, one engineer",
    "a TV repair shop window, 1969 — twelve sets all showing the moon landing",
    "the Today show, 1973, a segment about something no one worries about anymore",
    "a children's show set made of cardboard and absolute sincerity",
    "a drive-in movie screen showing a film nobody can quite identify",
    "a living room in 1979, a wood-paneled TV, a game of Pong reflected in eyeglasses",
    "a control room watching everything that isn't happening",
    "a morning news desk on a set that hasn't been updated since 1984",
]

SETTINGS_SYNTHS = [
    "a Moog Minimoog on a kitchen table, patch cables trailing off the edge",
    "a Roland TR-808 drum machine in a dark room, one red LED blinking",
    "a wall of modular synth — hundreds of patch cables, knobs, oscilloscope sine waves",
    "a Buchla synthesizer glowing orange in an empty concert hall",
    "a Roland TB-303 bubbling acid basslines in a Manchester basement",
    "a Mellotron with a stuck key, tape loops spilling out like ribbon",
    "a bank of VCOs and filters in a brutalist recording studio, reel-to-reel spinning",
    "a Theremin on a stand, a hand approaching but not touching",
    "a cramped bedroom studio in 1983 — two synths, a four-track, carpet on the walls",
    "a Prophet-5 in a freight elevator, unplugged, going somewhere",
    "a Korg MS-20 on a milk crate beside a sleeping bag",
    "a DX7 in a church hall, someone playing a patch called BRASS 1 at low volume",
    "a home studio in 1987 — MIDI cables everywhere, an 8-bit screen showing a waveform",
    "an Oberheim Matrix-6 in a pawnshop window, everything else already sold",
    "a row of vintage drum machines all powered on, none in sync",
    "a Sequential Circuits Prophet-VS in a recording studio, dust on the keys",
    "a Yamaha CS-80 in a warehouse being played with both hands and both knees",
    "a Roland Juno-106 on a milk crate in a damp rehearsal space, 1986",
    "a wall of patch cables connecting twelve synthesizers no one is currently playing",
    "a Fairlight CMI in a recording studio, an enormous disk drive, 1982",
]

SETTINGS_BROKEN_ELECTRONICS = [
    "a CRT monitor with a burnt-in ghost image of a Windows 95 desktop",
    "a VHS deck eating a tape slowly, the ribbon unspooling in real time",
    "a Walkman with a warped cassette, speed fluctuating visibly",
    "a keyboard with three missing keys and a cracked LCD, still trying",
    "a boombox with a broken antenna held together with a rubber band",
    "a television with a bowed screen showing someone's living room from 1987",
    "a dead pixel grid spreading across a monitor like frost, edges still lit",
    "a reel-to-reel machine with a snapped tape flapping on every revolution",
    "a sparking circuit board on a concrete floor, one capacitor still glowing",
    "a dot matrix printer printing something nobody asked for, at 3am",
    "a Game Boy with a cracked screen still running Tetris, battery low",
    "a smoke detector beeping once every 40 seconds for the past three weeks",
    "a pager on a nightstand receiving a message in 2024",
    "a landline off the hook, busy tone going since Tuesday",
    "a digital clock blinking 12:00 since a power cut eight months ago",
    "a battery-operated toy slowing down as the battery fails",
    "a malfunctioning ATM dispensing the same receipt over and over",
    "a monitor where the image has collapsed to a horizontal line",
    "a printer jam that has been escalating for six days",
    "a hard drive making a clicking sound that everyone has decided to ignore",
    "a vending machine that accepts money and considers it",
    "a fax machine receiving a transmission from a number that was disconnected in 1997",
]

SETTINGS_RETRO_OBJECTS = [
    "a rotary dial phone ringing in an empty kitchen",
    "a ViewMaster reel of Yellowstone with one slide broken",
    "a Betamax tape with a label written in red marker",
    "a Sears Wishbook open to the bicycle page",
    "a transistor radio with a broken antenna picking up a signal anyway",
    "a Lite-Brite glowing on a shag carpet",
    "a Speak & Spell spelling out something wrong and insisting it's right",
    "a Polaroid camera on a hospital bed tray",
    "a lunch box with Evel Knievel on it",
    "a Fisher-Price record player with one record that plays one song",
    "a Magic 8-Ball giving the same answer five times in a row",
    "a Simon game with all four lights on at once",
    "a mood ring stuck on dark blue for years",
    "a Spirograph on a kitchen table, half-finished",
    "an Etch A Sketch drawing that hasn't been erased",
    "a slide rule in a leather case",
    "an 8-track tape in a car with no 8-track player",
    "a View-Master with a reel nobody can identify",
    "a Commodore 64 loading something from a cassette, cursor blinking",
    "a ceramic piggy bank with coins going in but none ever coming out",
]

SETTINGS_CARTOON = [
    "flat painted desert, a tunnel painted on a cliff wall (Looney Tunes)",
    "a character runs off a cliff and hangs in air before looking down",
    "a door that opens onto a brick wall",
    "rubber-hose arms, pie-eyed expression, an anvil from nowhere",
    "Hannah-Barbera background panning — same potted plant, same painting, same lamp",
    "a Hanna-Barbera chase sequence, the same background looping endlessly",
    "a Looney Tunes character holding a sign that says HELP in someone else's handwriting",
    "an ACME warehouse, shelves full of devices of inexplicable purpose",
    "a cartoon interior where everything is bolted down except the character",
    "a black-and-white Fleischer Studios cartoon — the buildings bending and dancing",
    "a Disney rotoscoped forest, everything slightly too fluid",
    "a Tom and Jerry kitchen — pristine except for one cat-shaped hole in a wall",
    "a Wile E. Coyote canyon, everything flat, shadows only where they're supposed to be",
    "a Merrie Melodies background — rolling hills, a mailbox, a white picket fence, no logic",
    "a cartoon sky with three clouds, each one the same cloud",
]

SETTINGS_SESAME_MUPPETS = [
    "a bright urban brownstone with giant friendly monsters hanging from windows",
    "the number 14, enormous, being carried down a street by two Anything Muppets",
    "Big Bird standing alone in fog, enormous and entirely calm, looking left",
    "backstage at the Muppet Show — controlled chaos, a penguin on fire, Kermit running",
    "felt puppets with ping-pong eyes arguing about something unimportant, extreme close-up",
    "a Muppet in a tuxedo performing at the edge of a spotlight, everything else dark",
    "Oscar the Grouch's trash can in a rainstorm, lid clattering open and shut",
    "the Sesame Street stoop — Maria's Fix-it Shop, Hooper's Store, a perfect summer afternoon",
    "a two-headed monster arguing with itself in a public library",
    "the Sesame Street counting bat, hanging upside down in a number-filled lab",
    "a Muppet band in a cramped green room, instruments two sizes too large",
    "Animal behind a full drum kit, sticks raised, the moment before the beat drops",
    "Gonzo doing something inadvisable with a cannon, utterly serene about it",
    "the Muppet Newsman delivering a story that immediately affects him personally",
    "a giant letter falling from the sky into a quiet neighborhood",
    "Cookie Monster confronting a plate of vegetables, very seriously",
    "Statler and Waldorf in a box at the opera, both asleep, both snoring",
]

SETTINGS_STOP_MOTION = [
    "Harryhausen skeletons rising from the ground in choppy 12fps motion",
    "Rankin/Bass claymation reindeer crossing a snowy field, breath fake and perfect",
    "a Nick Park character — clay face enormous and expressive, one eyebrow raised",
    "a puppet with visible strings, lit from above, operating itself",
    "8 frames per second stop-motion: every movement a commitment",
    "a Laika Studios set — micro-props, rigging holes in the floor, beautiful and wrong",
    "a Švankmajer kitchen — meat that moves, hands operating without bodies",
    "a children's BBC puppet show set, everything slightly too large for the scale",
    "a Ray Harryhausen cyclops, clumsy and terrifying in the same motion",
    "a Gumby and Pokey diorama on a kitchen floor, 1962",
    "a Wallace and Gromit garage full of impossible inventions made of ordinary things",
    "a stop-motion map being drawn one frame at a time — BBC documentary, 1974",
    "a Tim Burton-style graveyard, headstones all slightly too tall",
    "a Winton's Miracle Maker texture — plasticine faces, Edwardian light, deep grief",
    "a stop-motion spider made of pipe cleaners walking across a physics textbook",
    "an Aardman Animations chicken in a coop that is clearly a prison",
]

SETTINGS_PSYCHEDELIA = [
    "Peter Max colors — flat magenta, electric blue, lime green, a face fractured into layers",
    "a lava lamp blob drifting upward in slow motion, amber through orange wax",
    "Yellow Submarine's sea of holes, the Blue Meanies advancing in formation",
    "swirling paisley dissolving into a tunnel of color, concert poster typography",
    "a fractal zooming inward forever, organic branching at every scale, day-glo palette",
    "Fillmore Auditorium poster art — letters dripping, faces melting into flowers",
    "a black light poster in a dorm room, 1971 — a wizard, a tiger, too much purple",
    "a Grateful Dead concert — liquid light show on a scrim, the band barely visible through it",
    "a Fender guitar neck with the strings melting into the wood slowly",
    "a cloud of oil-slick colors rotating in slow motion like a soap bubble",
    "a mandala unfolding one geometric layer at a time",
    "a Day-Glo mural on a warehouse wall, a figure walking past, colors bleeding into them",
    "a Kenney-Malone geometry, symmetrical and alive and infinite",
    "a 1960s film acid-trip sequence — stock footage of cells, endless zoom in",
    "a concert poster where the band's faces have become the letters of their name",
    "a Merry Pranksters bus driving through a color field that has no road",
    "a room where everything is covered in aluminum foil, 1967, San Francisco",
]

SETTINGS_MUSIC_VIDEO = [
    "a band in a white void warehouse, one light source, dry ice on the floor",
    "a single performer on a stark stage, suit and shadow, Talking Heads energy",
    "synchronized swimming shot from directly above, kaleidoscope edit",
    "MTV 1984 — a VJ in a cardigan, static-edged frame, a wind machine behind the band",
    "a slow-motion shot of someone walking toward camera in an empty parking structure",
    "a stock footage montage: space shuttle, cheetah, crowd of commuters, repeat",
    "a New Order music video — grey industrial building, single figure, no affect",
    "a Peter Gabriel video — a surreal suburban house, a woman in white, a giant suit",
    "a Beastie Boys video: fast cuts, fish-eye lens, no color grading, everything slightly wrong",
    "a Kate Bush video — dance, white dress, wide open field, fog machine cranked full",
    "a Depeche Mode performance — fog, leather, a single red light from behind",
    "a Laurie Anderson performance — neon text scrolling, one microphone, minimal",
    "an A-ha video — a hand reaches from a comic panel into a real diner booth",
    "a Devo performance — energy domes, matching suits, mechanical precision",
    "a Prince video — one dancer, one light, everything purple",
    "a Dead Kennedys show — a photographer in the pit, everything moving too fast",
    "a Talking Heads Stop Making Sense opening — one person, one guitar, one spotlight",
    "a Bowie Ziggy Stardust era stage — glitter, a platform boot, a lightning bolt face",
    "a Joy Division performance — Ian Curtis moving like he doesn't control his own body",
    "a Kraftwerk concert — four men at keyboards, identical, barely moving",
]

SETTINGS_NOSTALGIA = [
    "the back seat of a car at night, highway lights strobing the ceiling",
    "a pool at 7pm in August, nobody in it, a lawn chair tipped sideways",
    "a summer that never ended and then suddenly did",
    "a hallway you've walked before in a dream, slightly wrong dimensions",
    "a kitchen where someone who is no longer alive used to cook",
    "a sandbox in a yard that has since been paved over",
    "a Sears portrait studio backdrop, blue and grey, a family arranged and frozen",
    "a videotape of a Christmas morning that nobody labeled",
    "a public swimming pool at 8am opening, everything damp and echoey",
    "a child's bedroom with a nightlight and a mobile that no longer spins",
    "a school library with a card catalog, 1988",
    "a tire swing over a creek that may not exist anymore",
    "a birthday party in a backyard in 1983, a polaroid camera, party hats, cake",
    "a summer camp dining hall on the last night, everyone pretending not to be sad",
    "a motel pool at dusk, the neon sign reflecting in the water, nobody swimming",
    "a first apartment with secondhand furniture and a single working lamp",
    "a playground that used to be bigger",
    "a basement rec room, 1977 — wood paneling, beanbag chairs, a turntable",
    "a 7-Eleven at 1am in a suburb you grew up in",
    "a drive-through at closing time, two cars, the menu lights going off",
]

SETTINGS_IMPOSSIBLE = [
    "an Escher staircase that loops forever, figures walking both up and down simultaneously",
    "a grid floor extending to the horizon with no vanishing point, figures casting no shadows",
    "a sphere that doesn't reflect the room it's in",
    "a room where the ceiling and floor are mirrors facing each other, a figure multiplied",
    "tessellating penguins filling a white plane, colors shifting at the seam",
    "a Klein bottle sitting on a kitchen table like it's nothing",
    "a cube with too many corners",
    "a door that is smaller on the outside than inside",
    "a corridor that curves the wrong direction",
    "a window looking into a room that is not behind the wall",
    "a shadow cast in the wrong direction from every light source simultaneously",
    "a staircase descending to a point below the floor",
    "a room that is larger inside than outside",
    "a hall of mirrors where one reflection isn't doing what the others are",
]

SETTINGS_GENERAL = [
    "a rain-soaked Tokyo alley",
    "a vast salt flat at dusk",
    "a moss-covered temple courtyard",
    "the deck of a storm-battered ship",
    "a sunlit wheat field",
    "a brutalist rooftop at sunset",
    "an underground mushroom forest",
    "a frozen tundra with one dead tree",
    "a cathedral of glass and light",
    "a flooded ancient city",
    "a cliffside path above clouds",
    "the inside of a lighthouse",
    "a velvet-black void",
    "the surface of Jupiter",
    "a walk-in closet stretching impossibly deep",
    "a cramped cyberpunk apartment",
    "an open-air market in Marrakech",
    "a gondola in a canal going nowhere",
    "an Antarctic research station in a total whiteout",
    "a 1970s airport departure gate, everyone smoking",
    "a public telephone booth in a forest with no road",
    "a laundromat at 4am with one machine running",
    "an Amtrak sleeper car at night, small towns going past",
    "a bodega at 3am lit entirely by beer signs",
    "a piano bar where the pianist plays to an empty room",
    "a half-demolished theater still showing a movie on a damaged screen",
    "a rooftop water tower in New York, winter, one pigeon",
    "the inside of a very old elevator, mahogany and brass",
    "an empty aquarium after closing, the fish still going",
    "a greenhouse in January, everything humid and green",
    "a salt mine three hundred feet underground",
    "a glass-bottom boat over a coral reef at night",
]

SETTINGS = (
    SETTINGS_AMERICAN_REALISM + SETTINGS_SUBURBAN_UNEASE + SETTINGS_GENTLE_ELSEWHERE +
    SETTINGS_DREAD + SETTINGS_SF + SETTINGS_KAFKA + SETTINGS_RETRO_TV +
    SETTINGS_SYNTHS + SETTINGS_BROKEN_ELECTRONICS + SETTINGS_RETRO_OBJECTS +
    SETTINGS_CARTOON + SETTINGS_SESAME_MUPPETS + SETTINGS_STOP_MOTION +
    SETTINGS_PSYCHEDELIA + SETTINGS_MUSIC_VIDEO + SETTINGS_NOSTALGIA +
    SETTINGS_IMPOSSIBLE + SETTINGS_GENERAL
)

# ── Time & Weather ─────────────────────────────────────────────────────────────

TIME_WEATHER = [
    "at golden hour",
    "in the dead of night",
    "under a blood-orange sunset",
    "at blue hour",
    "in a blizzard",
    "under heavy monsoon rain",
    "on a foggy morning",
    "during a solar eclipse",
    "at high noon, no shade",
    "under the northern lights",
    "in the hour before dawn",
    "in Bakersfield summer heat",
    "the kind of October afternoon that smells like something ending",
    "the hour after a thunderstorm, everything dripping, the air gone green",
    "in the grey flat light of an overcast February morning",
    "on a clear night with too many stars",
    "in a heat shimmer at 2pm",
    "just before a tornado, when everything goes yellow-green",
    "in the five minutes between when the rain stops and the birds start again",
    "in driving sleet on a road with no shoulders",
    "the morning after the first hard freeze",
    "at 4am when the only lights are trucks and diners",
    "in the exact middle of the night",
    "at the moment the sun clears the horizon",
    "in a dense coastal fog that erases everything beyond 20 feet",
    "during a controlled burn, everything orange and bitter",
    "in a snowglobe stillness after a two-foot storm",
    "on a windless August afternoon that refuses to end",
    "in the last good light before the power goes out",
    "in the flat white of an overcast November noon",
    "at the moment a storm breaks",
    "in the amber middle hour of a long Alaskan summer day",
    "at dusk in a city where dusk lasts two hours",
    "under a supermoon that makes everything strange",
]

# ── Camera Moves ──────────────────────────────────────────────────────────────

CAMERA_MOVES = [
    "slow dolly in",
    "long tracking shot",
    "low-angle push",
    "overhead crane shot",
    "handheld shaky",
    "smooth orbit around the subject",
    "static wide",
    "slow pan left",
    "locked-off static — nothing moves but one thing",
    "rack focus from foreground to background",
    "slow push into a window from outside",
    "pull-back revealing the full scene",
    "close-up on hands",
    "extreme wide — figure barely a pixel",
    "dutch angle — 15 degrees",
    "bird's-eye — straight down",
    "worm's-eye — ground level looking up",
    "whip pan",
    "slow arc around the subject",
    "zoom in from wide, slightly too slow",
    "two-shot, one character facing away",
    "over-the-shoulder toward an empty room",
    "crash zoom",
    "steady-cam through a crowd",
    "push into a window from inside",
    "tilt up from feet to face",
    "tilt down from sky to subject",
    "360-degree pan, camera staying still",
]

# ── Lighting ──────────────────────────────────────────────────────────────────

LIGHTING = [
    "golden hour backlight",
    "flickering neon reflection on wet pavement",
    "single candle, everything else shadow",
    "harsh overhead fluorescent",
    "diffuse overcast, no shadows at all",
    "god rays through smoke",
    "moonlight on water",
    "lightning flash freezing the frame",
    "deep chiaroscuro",
    "bioluminescent glow",
    "a single 60-watt bulb in a large room",
    "TV light blue on a sleeping face",
    "sodium vapor streetlight orange",
    "headlights sweeping a bedroom ceiling at 2am",
    "grey-green light before a tornado",
    "flat white overcast winter noon",
    "the warm band of a single desk lamp",
    "blacklight ultraviolet, whites glowing",
    "strobe at 5fps",
    "fire from below, upward shadows",
    "hospital fluorescent through frosted glass",
    "the pale blue of a phone screen in total darkness",
    "amber from a kerosene lamp",
    "the pink wash of a neon sign in rain",
    "direct noon sun, no relief",
    "cool blue of pre-dawn",
    "stage footlights — upward shadows, theatrical",
    "the flat dead light of an overcast November, shadows nonexistent",
    "red emergency light",
    "sodium yellow of a freeway overpass at night",
    "morning light through venetian blinds, bars across the floor",
    "a single match lit and immediately extinguished",
]

# ── Mood & Atmosphere ─────────────────────────────────────────────────────────

MOOD = [
    "melancholy and quiet",
    "tense and breathless",
    "eerie and still",
    "intimate and warm",
    "joyful and kinetic",
    "epic and sweeping",
    "surreal and unsettling",
    "triumphant",
    "the specific dread of a familiar place at an unfamiliar hour",
    "tender and slightly broken",
    "darkly funny",
    "flat and declarative",
    "feverishly alive for no reason",
    "quietly apocalyptic",
    "nostalgic for something that may not have happened",
    "matter-of-fact and strange",
    "exhausted but luminous",
    "resigned with dignity",
    "absurdist and warm",
    "catastrophically hopeful",
    "bureaucratically depressed",
    "buzzing with low-level wrongness",
    "innocent and slightly doomed",
    "like a joke whose punchline is grief",
    "profoundly ordinary",
    "the calm before something",
    "the moment after something, before you understand it",
    "reverently mundane",
    "half-awake in a good way",
    "unbearably tender",
    "ominous in a friendly way",
    "relentlessly sincere",
    "formally correct and deeply wrong",
    "like a memory of a dream of a film you haven't seen",
    "operating on its own internal logic",
    "very still and very full",
]

# ── Cinematic Director Styles ─────────────────────────────────────────────────
# Each entry names a director's signature visual language in 4-8 words.
# Sampled by _algo_video() (1-in-3 chance) and included in STYLE below.

CINEMATIC_DIRECTORS = [
    # Suspense / noir
    "Hitchcock — voyeuristic high-angle thriller, chiaroscuro",
    "Carol Reed — wet cobblestones, canted angle, moral fog",
    "Billy Wilder — venetian blinds, cynical wit, cigarette smoke",
    "Douglas Sirk — Technicolor melodrama hiding social critique",
    "Roman Polanski — paranoia in close quarters, clipped corners",
    # European art cinema
    "Fellini — carnival dreamscape, baroque crowd, memory dissolve",
    "Tarkovsky — slow-burn long take, transcendent water and fire",
    "Bergman — faces in extreme close-up, death as quiet presence",
    "Antonioni — alienated figure in stark modern architecture",
    "Godard — jump cut, primary color wall, direct address",
    "Truffaut — warm spontaneous New Wave, freeze frame",
    "Bresson — non-actor gesture, fragmented body, spiritual ellipsis",
    "Rohmer — naturalistic summer light, moral hesitation on a terrace",
    "Fassbinder — frontal staging, alienated melodrama, gauze curtain",
    "Wenders — drifter, rock music on an empty road, longing",
    "Herzog — obsession dwarfed by impossible landscape",
    "Pasolini — sacred poverty, non-professional faces, cracked earth",
    "Visconti — operatic aristocratic decay, gilded surface, ruin",
    "Cassavetes — improvised handheld, raw emotional exposure",
    "Akerman — static duration, domestic space, feminist minimalism",
    # Asian masters
    "Kurosawa — widescreen epic in driving rain, weather as emotion",
    "Ozu — tatami-level static, family at table, pillow shot",
    "Mizoguchi — long scroll takes, female suffering in lantern light",
    "Wong Kar-wai — neon overexposure, slow-motion missed connection",
    "Hou Hsiao-hsien — long take, natural window light, elliptical cut",
    "Zhang Yimou — saturated color field, folk opera sweep",
    "Kiarostami — road and windshield, reflexivity, Iranian plateau",
    # British realism / poetic
    "David Lean — panoramic golden epic, lone figure in vastness",
    "Powell & Pressburger — color as emotion, dance, Technicolor myth",
    "Ken Loach — documentary handheld, working-class dignity",
    "Mike Leigh — improvised kitchen-sink intimacy",
    # American auteurs
    "Orson Welles — deep focus, low angle, expressionist shadow",
    "John Huston — adventure and moral complexity, wide weathered faces",
    "Coppola — operatic family tragedy, amber candlelight",
    "Spielberg — golden-hour backlit silhouette, kinetic wonder, child's-eye rack-focus reveal",
    "Scorsese — kinetic guilt, Catholic imagery, freeze-frame",
    "Malick — grass, whispered voiceover, light through trees",
    "Lynch — uncanny suburban dread, velvet curtain, distorted sound",
    "Altman — overlapping dialogue, zoom lens, ensemble drift",
    "PT Anderson — operatic tracking shot, American grotesque",
    "Wes Anderson — symmetry, pastel, nostalgia rendered as grief",
    "Fincher — dark precision, obsessive negative-fill lighting",
    "Spike Lee — double-dolly glide, jazz cut, direct address",
    "Jarmusch — deadpan cool, black and white, coffee and cigarettes",
    "Van Sant — dreamlike youth, Satie-paced drift",
    "Sofia Coppola — luxury melancholy, feminine interior silence",
    "Penny Marshall — warm ensemble Americana, naturalistic ensemble blocking, working-class tenderness",
    "Roger Corman — garish B-movie color, Gothic camp excess, drive-in spectacle on a shoestring",
    "Mel Brooks — vaudevillian sight gag, wide parody staging, anachronistic wink at the camera",
    # Contemporary / global
    "Haneke — long take, violence withheld, clinical Austrian interior",
    "von Trier — Dogme rawness, handheld, washed-out palette",
    "Claire Denis — bodies, texture, elliptical West African sun",
    "Dardenne Brothers — tight handheld behind neck, moral urgency",
    "Nolan — fractured timeline, IMAX grain, rain on concrete",
    "De Palma — split diopter, operatic Hitchcock homage, slow zoom",
    # Global voices, gender diversity, human-centered depiction
    "Agnes Varda — found-object tenderness, essay film, women's daily life",
    "Satyajit Ray — humanist Bengali realism, natural light, quiet dignity",
    "Ousmane Sembene — postcolonial West African labor, Wolof faces, village square",
    "Djibril Diop Mambety — Dakar surrealist fable, trickster figure, crumbling colonial wall",
    "Apichatpong Weerasethakul — Thai spirit cinema, jungle dissolve, dream double",
    "Lucrecia Martel — Argentine bourgeois unease, peripheral sound, bodies in heat",
    "Pedro Almodovar — queer Iberian melodrama, saturated red, female solidarity",
    "Kelly Reichardt — quiet Pacific Northwest, working-class women, long empty pause",
    "Celine Sciamma — queer portrait, soft historical light, female gaze and interiority",
    "Jia Zhangke — post-industrial Chinese displacement, DV texture, karaoke grief",
    "Larisa Shepitko — Soviet spiritual intensity, white light, female heroism in snow",
    "Carlos Reygadas — Mexican slow cinema, indigenous non-actor faces, open sky",
]

# ── Artistic Style ─────────────────────────────────────────────────────────────

STYLE = CINEMATIC_DIRECTORS + [
    "35mm film grain",
    "photorealistic",
    "painterly impressionist",
    "ink wash",
    "Studio Ghibli-inspired",
    "brutalist graphic",
    "neon noir",
    "oil painting",
    "hyperrealistic",
    "vintage VHS texture",
    "matte painting",
    "ukiyo-e woodblock",
    "mid-century paperback cover",
    "pulp science fiction",
    "Edward Hopper stillness",
    "WPA mural style",
    "Dorothea Lange documentary black and white",
    "1970s Kodachrome",
    "Peter Max psychedelia",
    "Yellow Submarine flat color",
    "stop motion claymation",
    "Harryhausen skeletal",
    "Rankin/Bass holiday special",
    "Escher lithograph",
    "MTV 1984 video aesthetic",
    "one-light warehouse photography",
    "Sesame Street primary color urban",
    "Cinema verite 16mm",
    "Soviet propaganda poster",
    "EC Comics horror illustration",
    "Robert Crumb underground comix",
    "Norman Rockwell Saturday Evening Post",
    "Saul Bass title card geometry",
    "Chris Ware architectural grid",
    "Moebius ligne claire",
    "Topps trading card photography 1978",
    "Ansel Adams zone system",
    "Diane Arbus square format portrait",
    "William Eggleston dye transfer color",
    "George Tice documentary",
]

# ── Quality Tags (image) ──────────────────────────────────────────────────────

QUALITY_TAGS = [
    "ultra-detailed",
    "8K",
    "sharp focus",
    "shallow depth of field",
    "bokeh",
    "masterpiece",
    "photorealistic",
    "35mm film grain",
    "high dynamic range",
    "cinematic color grading",
    "professional photography",
    "award-winning",
    "wide aperture",
    "long exposure",
    "medium format",
]

# ── Sampling helpers ──────────────────────────────────────────────────────────

_SUBJECT_REGISTERS = {
    "steinbeck": SUBJECTS_STEINBECK,
    "pkd": SUBJECTS_PKD,
    "brautigan": SUBJECTS_BRAUTIGAN,
    "butler": SUBJECTS_BUTLER,
    "noon": SUBJECTS_NOON,
    "robbins": SUBJECTS_ROBBINS,
    "king": SUBJECTS_KING,
    "kafka": SUBJECTS_KAFKA,
    "general": SUBJECTS_GENERAL,
}

_SETTING_REGISTERS = {
    "american_realism": SETTINGS_AMERICAN_REALISM,
    "suburban_unease": SETTINGS_SUBURBAN_UNEASE,
    "gentle_elsewhere": SETTINGS_GENTLE_ELSEWHERE,
    "dread": SETTINGS_DREAD,
    "sf": SETTINGS_SF,
    "kafka": SETTINGS_KAFKA,
    "retro_tv": SETTINGS_RETRO_TV,
    "synths": SETTINGS_SYNTHS,
    "broken_electronics": SETTINGS_BROKEN_ELECTRONICS,
    "retro_objects": SETTINGS_RETRO_OBJECTS,
    "cartoon": SETTINGS_CARTOON,
    "sesame_muppets": SETTINGS_SESAME_MUPPETS,
    "stop_motion": SETTINGS_STOP_MOTION,
    "psychedelia": SETTINGS_PSYCHEDELIA,
    "music_video": SETTINGS_MUSIC_VIDEO,
    "nostalgia": SETTINGS_NOSTALGIA,
    "impossible": SETTINGS_IMPOSSIBLE,
    "general": SETTINGS_GENERAL,
}


def pick(lst: list) -> str:
    """Return a random item from a list."""
    return random.choice(lst)


def pick_register(register_dict: dict) -> str:
    """Pick a random register, then a random item within it."""
    reg = random.choice(list(register_dict.values()))
    return random.choice(reg)


def subject() -> str:
    return pick_register(_SUBJECT_REGISTERS)


def action() -> str:
    return pick(ACTIONS)


def setting() -> str:
    return pick_register(_SETTING_REGISTERS)


def time_weather() -> str:
    return pick(TIME_WEATHER)


def camera() -> str:
    return pick(CAMERA_MOVES)


def lighting() -> str:
    return pick(LIGHTING)


def mood() -> str:
    return pick(MOOD)


def style() -> str:
    return pick(STYLE)


def quality_tags(n: int = 2) -> str:
    return ", ".join(random.sample(QUALITY_TAGS, n))


def director_style() -> str:
    """Return a random director-inspired cinematic style string."""
    return pick(CINEMATIC_DIRECTORS)
