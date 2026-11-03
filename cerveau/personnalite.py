"""Who each suspect and civilian is: a personality drawn once per mission and kept to the end (Wal, step 6).

Drawn from the game's own data on him (his archetype, "Standard Suspect" / "Hostage Civilian", and his voice,
"RigSuspectCoward", "GasSuspect_01"...), so a coward stays a coward, then from a fixed draw on his name and the map:
the same suspect stays coherent, two suspects do not act alike. The game's difficulty shifts the suspects: harder =
braver, sneakier (Wal: "la difficulté de convaincre dépend de la difficulté").

Traits, 0 to 1:
- courage: how much pressure he takes before giving in;
- agressivite: shoots, ambushes, flanks rather than hides;
- ruse: fakes a surrender, lies;
- panique: runs, freezes, does something stupid under pressure;
- honnetete: tells the truth when asked;
- loyaute: helps an ally, warns the others.
"""
import hashlib
import random

# type -> (traits, French words for the model); the weights below say how often each type is drawn
SUSPECTS = {
    'calme': ({'courage': 0.7, 'agressivite': 0.5, 'ruse': 0.5, 'panique': 0.15, 'honnetete': 0.3, 'loyaute': 0.6},
              'calme et méthodique'),
    'nerveux': ({'courage': 0.35, 'agressivite': 0.45, 'ruse': 0.3, 'panique': 0.7, 'honnetete': 0.4, 'loyaute': 0.4},
                'nerveux, imprévisible'),
    'fanatique': ({'courage': 0.95, 'agressivite': 0.9, 'ruse': 0.4, 'panique': 0.05, 'honnetete': 0.1, 'loyaute': 0.9},
                  'fanatique, prêt à mourir'),
    'drogue': ({'courage': 0.6, 'agressivite': 0.75, 'ruse': 0.15, 'panique': 0.5, 'honnetete': 0.4, 'loyaute': 0.2},
               'drogué, confus et agressif'),
    'lache': ({'courage': 0.15, 'agressivite': 0.15, 'ruse': 0.35, 'panique': 0.8, 'honnetete': 0.6, 'loyaute': 0.15},
              'lâche, il pense à sauver sa peau'),
    'fier': ({'courage': 0.75, 'agressivite': 0.6, 'ruse': 0.55, 'panique': 0.2, 'honnetete': 0.2, 'loyaute': 0.5},
             'fier, il refuse de perdre la face'),
    'bavard': ({'courage': 0.4, 'agressivite': 0.3, 'ruse': 0.6, 'panique': 0.4, 'honnetete': 0.5, 'loyaute': 0.3},
               'bavard, il parle volontiers et négocie'),
}
CIVILS = {
    'cooperatif': ({'courage': 0.3, 'agressivite': 0.0, 'ruse': 0.0, 'panique': 0.3, 'honnetete': 0.9, 'loyaute': 0.5},
                   'coopératif'),
    'panique': ({'courage': 0.1, 'agressivite': 0.0, 'ruse': 0.0, 'panique': 0.9, 'honnetete': 0.8, 'loyaute': 0.3},
                'paniqué'),
    'choque': ({'courage': 0.2, 'agressivite': 0.0, 'ruse': 0.0, 'panique': 0.6, 'honnetete': 0.8, 'loyaute': 0.3},
               'en état de choc, il comprend mal'),
    'tetu': ({'courage': 0.6, 'agressivite': 0.05, 'ruse': 0.1, 'panique': 0.2, 'honnetete': 0.7, 'loyaute': 0.4},
             'têtu, il n\'aime pas la police'),
    'calme': ({'courage': 0.5, 'agressivite': 0.0, 'ruse': 0.0, 'panique': 0.15, 'honnetete': 0.9, 'loyaute': 0.6},
              'calme'),
}
POIDS_SUSPECTS = {'calme': 3, 'nerveux': 3, 'fanatique': 1, 'drogue': 2, 'lache': 2, 'fier': 2, 'bavard': 2}
POIDS_CIVILS = {'cooperatif': 4, 'panique': 3, 'choque': 2, 'tetu': 1, 'calme': 2}
# words of the game's archetype or voice name that set the type outright (game data beats the draw)
INDICES = [('coward', 'lache'), ('lache', 'lache'), ('fanatic', 'fanatique'), ('sentinel', 'fanatique'),
           ('heavy', 'fanatique'), ('meth', 'drogue'), ('drug', 'drogue'), ('junk', 'drogue'), ('rioter', 'nerveux'),
           ('accomplice', 'nerveux'), ('robber', 'calme'), ('gangster', 'fier'), ('dealer', 'bavard')]
# the game's difficulty moves the suspects' courage and cunning (civilians do not change)
DIFFICULTE = {'facile': -0.15, 'standard': 0.0, 'difficile': 0.12}


def tirer(nom, carte, suspect, archetype='', voix='', niveau='standard'):
    """The personality of one character: {'type', 'mots', traits...}. Fixed for (name, map)."""
    graine = int(hashlib.sha256(f'{carte}|{nom}'.encode()).hexdigest()[:12], 16)
    hasard = random.Random(graine)
    table, poids = (SUSPECTS, POIDS_SUSPECTS) if suspect else (CIVILS, POIDS_CIVILS)
    indice = f'{archetype} {voix}'.lower()
    type_ = next((t for mot, t in INDICES if suspect and mot in indice), None)
    if type_ is None:
        type_ = hasard.choices(list(poids), weights=list(poids.values()))[0]
    traits, mots = table[type_]
    # a little spread around the type, so two "calm" suspects are not twins
    p = {k: min(1.0, max(0.0, v + hasard.uniform(-0.1, 0.1))) for k, v in traits.items()}
    if not suspect:   # a civilian never attacks, whatever the draw
        p['agressivite'] = 0.0
    if suspect:
        d = DIFFICULTE.get(niveau, 0.0)
        for k in ('courage', 'ruse', 'agressivite'):
            p[k] = min(1.0, max(0.0, p[k] + d))
        p['panique'] = min(1.0, max(0.0, p['panique'] - d))
    return {'type': type_, 'mots': mots, **p}


class Personnalites:
    """Every character's personality for the current mission (cleared when the map changes)."""

    def __init__(self):
        self.carte, self.connues = None, {}

    def de(self, e, carte, niveau='standard'):
        if carte != self.carte:
            self.carte, self.connues = carte, {}
        if e['nom'] not in self.connues:
            self.connues[e['nom']] = tirer(e['nom'], carte, bool(e.get('suspect')), e.get('archetype', ''),
                                           e.get('voix', ''), niveau)
        return self.connues[e['nom']]
