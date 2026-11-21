# Car select texts of the DLC cars for the PC dictionaries (finish_car.py writes them, build_pack.py checks them).
#
# TEXT_<ID>: the name, as the DLC's car select plate has it (FE_vehicleSelect.gfx, name sprite, the frame of the
# Xbox record ID: brand field + model field; finish_car.py checks it against the DLC file). Brand words are not
# translated in any PC dictionary, so the name is the same in every language.
#
# TEXT_CARDESCR_<ID>: the DLC's own description is not in the packages: it is in Title Update 4's built-in text
# table as TEXT_CARDESCR_Placeholder_NN (NN = the Xbox slot of the car's record, SLOT below), with the names as
# TEXT_PLACEHOLDER_NN (../mode/tu4text.py). With that table (GLIMPSE_TU4_TEXTS, a JSON a developer makes from
# their own TU4; never in the repository) description() returns the official text. Without it a short line is
# written from the record: the pack, then the three stats the car select shows (txt_speedNum, txt_driftNum,
# txt_strengthNum in its ActionScript) in words. Rule:
#   - band per stat: 9-10 EXCELLENT, 7-8 GOOD, 5-6 AVERAGE, 0-4 LOW;
#   - highest stat first; equal values in the car select's order speed, drift, strength;
#   - Accel and OverallStat are left out (not shown there).
# Musclecar_17 (Speed 7, Drift 10, Strength 4) -> "EXCELLENT DRIFT, GOOD SPEED, LOW STRENGTH", the line of the
# first build (CHECK below; it was installed and seen in the game).
# Upper case with the accents the PC dictionaries use (É, À, Ä; the PC car select font has them, the first build
# shows them).

NAMES = {'Musclecar_17': 'COBRETTI CENTAUR',          # Placeholder_09
         'Musclecar_18': 'RYBACK JAVELIN',            # Placeholder_10
         'Musclecar_19': 'RYBACK COYOTE SPECIAL',     # Placeholder_08
         'Truck_09': 'HANZO KANOBO',                  # Placeholder_11
         # "High Octane" Supercar Vehicle Pack
         'Supercar_17': 'RYBACK CYCLONE SPECIAL',     # Placeholder_01
         'Supercar_18': 'COBRETTI SEVERUS',           # Placeholder_02
         'Supercar_19': 'RYBACK VULCAN',              # Placeholder_03
         'Supercar_20': 'HANZO KATANA',               # Placeholder_04
         # "The Elite" Vehicle Livery Pack (new records with the base elite cars' handling Unique_01/02)
         'Unique_21': 'ELITE 440 BRN',                # Placeholder_05
         'Unique_22': 'ELITE S510 RP',                # Placeholder_06
         'Unique_23': 'ELITE S510 VX'}                # Placeholder_07

# the Xbox record each car replaces in the merged VehicleDatabase.xml (its ID there; build_pack.py checks it
# against the DLC file): the key of its TU4 texts
SLOT = {'Supercar_17': 'Placeholder_01', 'Supercar_18': 'Placeholder_02', 'Supercar_19': 'Placeholder_03',
        'Supercar_20': 'Placeholder_04', 'Unique_21': 'Placeholder_05', 'Unique_22': 'Placeholder_06',
        'Unique_23': 'Placeholder_07', 'Musclecar_19': 'Placeholder_08', 'Musclecar_17': 'Placeholder_09',
        'Musclecar_18': 'Placeholder_10', 'Truck_09': 'Placeholder_11'}

LANGS = ('ENGLISH_NTSC', 'GERMAN', 'FRENCH_NTSC', 'ITALIAN', 'SPANISH_NTSC')   # plus StringIDs (key -> lower-case key)
STATS = ('Speed', 'Drift', 'Strength')
BANDS = ((9, 'EXCELLENT'), (7, 'GOOD'), (5, 'AVERAGE'), (0, 'LOW'))
PACK = {'ENGLISH_NTSC': 'FROM THE DEADLINE PACK.', 'GERMAN': 'AUS DEM DEADLINE-PAKET.', 'FRENCH_NTSC': 'DU PACK DEADLINE.',
        'ITALIAN': 'DAL PACCHETTO DEADLINE.', 'SPANISH_NTSC': 'DEL PACK DEADLINE.'}
# the other packs: the pack name stays English (a product name, as Deadline), the words around it as in PACK
PACKS = {'Deadline': PACK,
         'High Octane': {'ENGLISH_NTSC': 'FROM THE HIGH OCTANE PACK.', 'GERMAN': 'AUS DEM HIGH-OCTANE-PAKET.',
                         'FRENCH_NTSC': 'DU PACK HIGH OCTANE.', 'ITALIAN': 'DAL PACCHETTO HIGH OCTANE.',
                         'SPANISH_NTSC': 'DEL PACK HIGH OCTANE.'},
         'Elite': {'ENGLISH_NTSC': 'FROM THE ELITE LIVERY PACK.', 'GERMAN': 'AUS DEM ELITE-LACKIERUNGSPAKET.',
                   'FRENCH_NTSC': 'DU PACK DE LIVRÉES ELITE.', 'ITALIAN': 'DAL PACCHETTO LIVREE ELITE.',
                   'SPANISH_NTSC': 'DEL PACK DE DECORADOS ELITE.'}}
PACK_OF = {'Supercar_17': 'High Octane', 'Supercar_18': 'High Octane', 'Supercar_19': 'High Octane',
           'Supercar_20': 'High Octane', 'Unique_21': 'Elite', 'Unique_22': 'Elite', 'Unique_23': 'Elite'}
# every other car: Deadline
# per language: stat -> band -> words. Adjective forms follow the noun: DE der Drift / die Geschwindigkeit, die
# Stärke; FR "excellent en dérapage" (said of the car, as in the first build) / la vitesse, la résistance; IT la
# derapata, la velocità, la resistenza; ES el derrape / la velocidad, la fuerza.
WORDS = {
    'ENGLISH_NTSC': {s: {b: '%s %s' % (b, s.upper()) for _, b in BANDS} for s in STATS},
    'GERMAN': {
        'Speed': {'EXCELLENT': 'HERVORRAGENDE GESCHWINDIGKEIT', 'GOOD': 'GUTE GESCHWINDIGKEIT',
                  'AVERAGE': 'MITTLERE GESCHWINDIGKEIT', 'LOW': 'GERINGE GESCHWINDIGKEIT'},
        'Drift': {'EXCELLENT': 'HERVORRAGENDER DRIFT', 'GOOD': 'GUTER DRIFT', 'AVERAGE': 'MITTLERER DRIFT',
                  'LOW': 'GERINGER DRIFT'},
        'Strength': {'EXCELLENT': 'HERVORRAGENDE STÄRKE', 'GOOD': 'GUTE STÄRKE', 'AVERAGE': 'MITTLERE STÄRKE',
                     'LOW': 'GERINGE STÄRKE'}},
    'FRENCH_NTSC': {
        'Speed': {'EXCELLENT': 'EXCELLENTE VITESSE', 'GOOD': 'BONNE VITESSE', 'AVERAGE': 'VITESSE MOYENNE',
                  'LOW': 'FAIBLE VITESSE'},
        'Drift': {'EXCELLENT': 'EXCELLENT EN DÉRAPAGE', 'GOOD': 'BON EN DÉRAPAGE', 'AVERAGE': 'MOYEN EN DÉRAPAGE',
                  'LOW': 'FAIBLE EN DÉRAPAGE'},
        'Strength': {'EXCELLENT': 'EXCELLENTE RÉSISTANCE', 'GOOD': 'BONNE RÉSISTANCE', 'AVERAGE': 'RÉSISTANCE MOYENNE',
                     'LOW': 'FAIBLE RÉSISTANCE'}},
    'ITALIAN': {
        'Speed': {'EXCELLENT': 'VELOCITÀ ECCELLENTE', 'GOOD': 'BUONA VELOCITÀ', 'AVERAGE': 'VELOCITÀ MEDIA',
                  'LOW': 'SCARSA VELOCITÀ'},
        'Drift': {'EXCELLENT': 'DERAPATA ECCELLENTE', 'GOOD': 'BUONA DERAPATA', 'AVERAGE': 'DERAPATA MEDIA',
                  'LOW': 'SCARSA DERAPATA'},
        'Strength': {'EXCELLENT': 'RESISTENZA ECCELLENTE', 'GOOD': 'BUONA RESISTENZA', 'AVERAGE': 'RESISTENZA MEDIA',
                     'LOW': 'SCARSA RESISTENZA'}},
    'SPANISH_NTSC': {
        'Speed': {'EXCELLENT': 'VELOCIDAD EXCELENTE', 'GOOD': 'BUENA VELOCIDAD', 'AVERAGE': 'VELOCIDAD MEDIA',
                  'LOW': 'POCA VELOCIDAD'},
        'Drift': {'EXCELLENT': 'DERRAPE EXCELENTE', 'GOOD': 'BUEN DERRAPE', 'AVERAGE': 'DERRAPE MEDIO',
                  'LOW': 'POCO DERRAPE'},
        # the Spanish car select labels the stat FUERZA (TEXT_STRENGTH in spanish_ntsc_win32.dct)
        'Strength': {'EXCELLENT': 'FUERZA EXCELENTE', 'GOOD': 'BUENA FUERZA', 'AVERAGE': 'FUERZA MEDIA',
                     'LOW': 'POCA FUERZA'}},
}
# the lines of the first build (Musclecar_17 alone, installed and seen in the game): the rule must give them back;
# Spanish since then says FUERZA, the word of the Spanish car select, instead of the first build's RESISTENCIA
CHECK = {'Musclecar_17': {
    'ENGLISH_NTSC': 'FROM THE DEADLINE PACK. EXCELLENT DRIFT, GOOD SPEED, LOW STRENGTH.',
    'GERMAN': 'AUS DEM DEADLINE-PAKET. HERVORRAGENDER DRIFT, GUTE GESCHWINDIGKEIT, GERINGE STÄRKE.',
    'FRENCH_NTSC': 'DU PACK DEADLINE. EXCELLENT EN DÉRAPAGE, BONNE VITESSE, FAIBLE RÉSISTANCE.',
    'ITALIAN': 'DAL PACCHETTO DEADLINE. DERAPATA ECCELLENTE, BUONA VELOCITÀ, SCARSA RESISTENZA.',
    'SPANISH_NTSC': 'DEL PACK DEADLINE. DERRAPE EXCELENTE, BUENA VELOCIDAD, POCA FUERZA.'}}


def band(v):
    return next(b for lo, b in BANDS if v >= lo)


def describe(lang, stats, model=None):
    """the description line from {'Speed': n, 'Drift': n, 'Strength': n}; model picks the pack (PACK_OF; default
    Deadline)"""
    order = sorted(STATS, key=lambda s: (-stats[s], STATS.index(s)))
    return '%s %s.' % (PACKS[PACK_OF.get(model, 'Deadline')][lang], ', '.join(WORDS[lang][s][band(stats[s])] for s in order))


def stats_of(record):
    """{'Speed': n, 'Drift': n, 'Strength': n} of a <Car .../> record"""
    import re
    return {s: int(re.search(r' %s="(\d+)"' % s, record).group(1)) for s in STATS}


def check_rule():
    """the rule against the first build's lines; returns the differences"""
    return ['%s %s' % (m, l) for m, by in CHECK.items() for l, t in by.items() if describe(l, CHECK_STATS[m]) != t]


CHECK_STATS = {'Musclecar_17': {'Speed': 7, 'Drift': 10, 'Strength': 4}}   # its DLC record (Placeholder_09)


# ------------------------------------------------------------------------------- the official texts (TU4)
def _tu4():
    import os
    import sys
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mode')
    if d not in sys.path:
        sys.path.insert(0, d)
    import tu4text
    return tu4text


def tu4_table(path=None):
    """the TU4 text table (../mode/tu4text.py: path, else GLIMPSE_TU4_TEXTS) or None"""
    return _tu4().load(path)


def official_keys(model):
    """(description key, name key) of the car in the TU4 table"""
    n = SLOT[model].split('_')[1]
    return 'TEXT_CARDESCR_Placeholder_%s' % n, 'TEXT_PLACEHOLDER_%s' % n


def pc_text(t):
    """the official text as a PC dictionary line: a no-break space (Spanish Placeholder_05..07; no PC text uses
    one, so the PC font may lack it) becomes a space"""
    return t.replace('\xa0', ' ')


def description(lang, stats, model, table=None):
    """(text, source): the official description from the TU4 table when it has the car in every language,
    else the generated line (describe); source 'TU4 <key>' or 'generated'"""
    if table is not None and model in SLOT:
        k = official_keys(model)[0]
        if table.has(k, LANGS):
            return pc_text(table.get(k, lang)), 'TU4 ' + k
    return describe(lang, stats, model), 'generated'
