"""The tactical brain: in solo, the local model plays each SWAT teammate.

Every few seconds (and right after an order from Wal) it reads the game state, writes a short French picture of
the situation, and asks the model for ONE order per teammate from a closed list. The game's own behaviour does the
walking, the aiming and the doors; the model decides who does what. `garde_ordre()` decides in code: unknown
teammate or target, a shot at someone who must not be shot (unarmed, surrendered, civilian), anything out of
range becomes "garder" (keep doing what he does). How sharp the team is follows the game's difficulty.
"""
import json
import math
import re
import time
import urllib.request

from cerveau import OLLAMA
import style as style_mod

# what each difficulty of the game means for the AI team (Wal: "adaptée à notre niveau de difficulté").
# periode_s: how often the team re-thinks (reaction time); precision: the game's accuracy penalty multiplier
# for ordered shots (1 = the game's normal, higher = worse); rafale_s: length of an ordered burst;
# initiative: may the team act without an order from Wal (engage, take positions), or only defend and obey.
# reaction_s: when the situation changes (a suspect comes into view, a teammate is hit), the team re-thinks
# after this delay instead of waiting for the next period.
NIVEAUX = {
    'facile': {'periode_s': 1.5, 'reaction_s': 0.2, 'precision': 0.6, 'rafale_s': 2.0, 'initiative': True},
    'standard': {'periode_s': 2.5, 'reaction_s': 0.5, 'precision': 1.0, 'rafale_s': 1.5, 'initiative': True},
    'difficile': {'periode_s': 3.5, 'reaction_s': 1.0, 'precision': 1.6, 'rafale_s': 1.0, 'initiative': False},
}


def signature(etat):
    """What makes the team re-think at once: who is in view of a teammate, who surrendered, who is hurt."""
    equipe = [s for s in etat.get('swat', []) if 'Trailer' not in s.get('classe', '')]
    vus = frozenset(n for s in equipe for n in s.get('voit', []))
    rendus = frozenset((e['nom'], bool(e.get('menace'))) for e in etat.get('ia', [])
                       if e['rendu'] or e.get('obeit') or e['hors_combat'] or e.get('menace'))
    blesses = frozenset((s['nom'], s['hors_combat'], round(s['vie'] / 20)) for s in equipe)
    return vus, rendus, blesses
# the game's difficulty tags -> our levels; an unknown tag is logged and played as "standard"
TAGS = {'casual': 'facile', 'easy': 'facile', 'standard': 'standard', 'normal': 'standard',
        'hard': 'difficile', 'realistic': 'difficile', 'ironman': 'difficile'}


def niveau(tag):
    """(level name, its settings) for a game difficulty tag such as "Difficulty.Standard"."""
    cle = str(tag or '').rsplit('.', 1)[-1].lower()
    nom = TAGS.get(cle, 'standard')
    return nom, NIVEAUX[nom]


ACTIONS = ('garder', 'suivre', 'tenir', 'avancer', 'aller_vers', 'surveiller', 'couvrir', 'replier', 'replacer',
           'contourner', 'fouiller', 'tirer', 'tir_couverture', 'menotter')
PORTEE_TIR_M = 30.0
PORTEE_MENOTTES_M = 25.0
PRES_SUSPECT_M, PRES_CIVIL_M, PRES_PORTE_M = 30.0, 15.0, 12.0   # beyond this (and out of sight) the model is not told
COUVRIR_M = 2.0       # a cover man stands this far behind and to the side of the one he covers
REPLI_M = 6.0         # falling back: this far from the threat, on the leader's side
BLESSE_PV = 150       # a teammate below this (of 250) is badly hurt
DECALAGE_M = 2.5      # repositioning: a sidestep this long, across the line to the target (a new angle, same range)
CONTOURNER_M = 7.0    # flanking: this far from the target, a quarter turn around it from where the team is
COUVERTURE_DISPERSION = 2.5   # suppressive fire: the game's accuracy penalty times this (it sprays around him)
COUVERTURE_S = 2.5    # suppressive fire: burst length
LIGNE_DE_TIR_M = 1.2  # no friend, civilian or surrendered person this close to the line of fire

SYSTEME = """Tu commandes les coéquipiers SWAT IA du joueur dans le jeu Ready or Not. Le joueur est le chef ; ses
ordres peuvent être en français ou en anglais.
Tu reçois la situation et tu donnes UN ordre à CHAQUE coéquipier (A, B, C, D...), comme un bon chef d'équipe :
- garder : il continue ce qu'il fait (le bon choix si son ordre en cours est toujours utile).
- suivre : il revient près du chef. tenir : il s'arrête là. avancer : il passe quelques mètres devant le chef.
- aller_vers : il va vers la cible (une personne, une porte ou une pièce).
- surveiller : il garde l'œil sur la cible (une personne, une porte ou une pièce), arme prête.
- couvrir : il se place près de la cible (CHEF ou un coéquipier) et surveille le danger pour lui.
- replier : il recule à l'abri, loin du danger (blessé, ou face à un suspect menaçant qu'il ne peut pas viser).
- replacer : il fait un pas de côté pour prendre un meilleur angle sur la cible (s'il ne la voit pas bien).
- contourner : il prend la cible (un suspect) par le côté, à quelques mètres, pendant que les autres la fixent.
- fouiller : il fouille et sécurise une pièce (cible R...), ou celle où il est (sans cible).
- tirer : il tire sur la cible. SEULEMENT un suspect armé, vu par ce coéquipier, qui ne s'est pas rendu.
- tir_couverture : il tire vers un suspect MENAÇANT (même caché) pour l'obliger à se mettre à l'abri, pendant qu'un
  coéquipier bouge (contourne, avance, se replie ou va menotter).
- menotter : il menotte la cible, une personne qui s'est rendue ou obéit.
aller_vers, surveiller, couvrir, replacer, contourner, tirer, tir_couverture et menotter demandent une cible.
Si le chef NOMME des coéquipiers (Alpha, Bravo, Charlie, Delta ou leur nom), son ordre est pour eux seuls :
donne-leur exactement ce qu'il demande, les autres continuent.
Règles : obéis d'abord au dernier ordre du chef. Ne tire jamais sur un civil, sur quelqu'un qui se rend,
ni sur un suspect qui tient un otage. Un suspect armé en vue est le danger : surveille-le, et tire s'il est MENAÇANT. Un suspect DISPARU : surveille ou
couvre l'endroit où il a été vu en dernier, ou va chercher un angle (replacer) ; ne l'oublie pas.
Face à un suspect MENAÇANT : un coéquipier le fixe (tir_couverture ou tirer), un autre le contourne ou se replace.
Quand un coéquipier avance, fouille ou va vers une porte, un autre le couvre. Le chef doit toujours avoir quelqu'un
qui le couvre. Les pièces pas nettoyées sont à fouiller. Un coéquipier très blessé se replie.
Répartis les rôles (tout le monde ne fait pas la même chose).
EN PLUS, un ordre pour TOUTE l'équipe (EQUIPE), avec son équipement :
- entrer : l'équipe entre par une porte fermée proche (cible P...) et nettoie la pièce. methode : ouvrir, enfoncer
  (porte verrouillée), fusil, belier ou c2 (porte solide, ou suspects derrière). objet : une grenade lancée avant
  d'entrer (flash, stinger, gaz, ninebang) ou aucun. Une pièce pas nettoyée avec des suspects : entrer avec une flash.
- grenade : une grenade (objet) sur un suspect, une pièce ou derrière une porte, pour qu'ils se rendent.
- non_letal : taser, beanbag, pepperball ou spray (objet) sur une personne qui n'obéit pas, plutôt que de la tuer.
- bouclier : un coéquipier sort le bouclier balistique (face à des tireurs, dans un couloir).
- aucune : pas d'ordre d'équipe (le plus souvent).
N'utilise que l'équipement que l'équipe a encore.
Suis le STYLE du chef donné dans la situation : en non-létal, on arrête les suspects vivants (non_letal, flash,
stinger) et on ne tire pour tuer qu'en légitime défense.
Réponds seulement en JSON : pour chaque coéquipier {"a": action, "c": cible}, et EQUIPE {"a": action d'équipe,
"c": cible, "m": methode, "o": objet}."""


INDICATIFS = {0: 'Alpha', 1: 'Bravo', 2: 'Charlie', 3: 'Delta'}   # the game's squad positions (SP_Beta = Bravo)
ALIAS_INDICATIF = {'alpha': 0, 'bravo': 1, 'beta': 1, 'charlie': 2, 'charly': 2, 'delta': 3}


def _sans_accents(t):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', t.lower()) if unicodedata.category(c) != 'Mn')


def nommes(texte, etat):
    """The AI teammates Wal names in a sentence (by call sign, first or last name), as the game's names, in order."""
    mots = re.findall(r"[a-z]+", _sans_accents(texte))
    equipe = [s for s in etat.get('swat', []) if 'Trailer' not in s.get('classe', '') and not s['hors_combat']]
    trouves = []
    for i, mot in enumerate(mots):
        for s in equipe:
            noms = {_sans_accents(n) for n in (s.get('prenom', ''), s.get('nom_perso', '')) if len(n) >= 3}
            # (a word that is no call sign gives None: it must not match a teammate whose position is unknown)
            indic = ALIAS_INDICATIF.get(mot)
            if ((indic is not None and indic == s.get('poste')) or mot in noms) and s['nom'] not in trouves:
                trouves.append(s['nom'])
    return trouves


# words that may come with a mere call ("Alpha !", "Hé Bravo, tu m'entends ?", "Charlie, écoute") and carry no order
MOTS_D_APPEL = {'he', 'eh', 'hey', 'oh', 'ho', 'ok', 'okay', 'oui', 'tu', 'm', 'me', 'entends', 'ecoute', 'ecoutez',
                'dis', 'toi', 'la', 'est', 'ce', 'que', 't', 'es', 'chef', 'equipe', 'radio', 'allo', 'repondez',
                'reponds', 'bien',
                # in English ("Alpha!", "Hey Bravo, you copy?", "Charlie, do you read me?")
                'you', 'copy', 'do', 'read', 'hear', 'listen', 'there', 'come', 'in', 'yo'}
# not "ici", "vas-y": "Alpha, ici !", "Alpha, vas-y" are orders; nor "here", "go"


def simple_appel(texte, etat):
    """True when the sentence only calls teammates by name, with no order in it."""
    equipe = [s for s in etat.get('swat', []) if 'Trailer' not in s.get('classe', '')]
    noms = set(ALIAS_INDICATIF)
    for s in equipe:
        noms |= {_sans_accents(n) for n in (s.get('prenom', ''), s.get('nom_perso', '')) if len(n) >= 3}
    reste = [m for m in re.findall(r"[a-z]+", _sans_accents(texte)) if m not in noms and m not in MOTS_D_APPEL]
    return not reste


def indicatif(s):
    """How a teammate is named to the model: "Alpha, Rodriguez, rouge"."""
    parts = [INDICATIFS.get(s.get('poste'), '')] + [s.get('nom_perso', '')] + \
            [{1: 'rouge', 2: 'bleu'}.get(s.get('equipe'), '')]
    return ', '.join(p for p in parts if p)


def _ids(liste, lettre):
    """Short ids for the model ("S1", "C2", "P3") -> the game's names."""
    return {f'{lettre}{i + 1}': e for i, e in enumerate(liste)}


def direction(chef, pos):
    """Where a point is for the leader: "devant", "à droite", "derrière", "à gauche" (from his aim)."""
    if not chef or 'lacet' not in chef:
        return ''
    dx, dy = pos[0] - chef['pos'][0], pos[1] - chef['pos'][1]
    if abs(dx) + abs(dy) < 0.5:
        return 'sur lui'
    angle = (math.degrees(math.atan2(dy, dx)) - chef['lacet'] + 180) % 360 - 180   # UE: + = to his right
    return 'devant' if abs(angle) <= 45 else 'derrière' if abs(angle) >= 135 else 'à droite' if angle > 0 else 'à gauche'


DANS_PIECE_M = 12.0   # farther than this from every room centre = outside (in game the leader stood outside, 47 m away)


def piece_de(pieces, pos):
    """The room a point is in: the closest room centre (the bridge gives centres in metres), or None outside."""
    r = min(pieces, key=lambda r: math.dist(r['pos'], pos), default=None)
    return r if r and math.dist(r['pos'], pos) <= DANS_PIECE_M else None


def situation(etat, ordre_chef=None, depuis_s=None, niv=NIVEAUX['standard'], en_cours=None, style=None):
    """(text for the model, ids) from the bridge's state. ids maps every short id to the game's entity.
    en_cours: teammate's game name -> (what he was told, seconds ago), so the model sees the plan it is running.
    style: the leader's way of playing (cerveau/style.py: letal, non_letal, mixte); None = lethal rules."""
    en_cours = en_cours or {}
    chef = next((j for j in etat.get('joueurs', []) if j['moi'] and 'pos' in j), None)
    ici = chef['pos'] if chef else [0.0, 0.0, 0.0]
    pieces = etat.get('pieces', [])
    sa_piece = piece_de(pieces, ici) if chef else None
    if sa_piece:
        voisines = [r for r in pieces if r['nom'] in sa_piece.get('voisines', []) and not r['nettoyee']]
        if not sa_piece['nettoyee']:
            voisines.insert(0, sa_piece)
    else:   # outside, or between rooms: the uncleared rooms close to him
        voisines = [r for r in pieces if chef and not r['nettoyee'] and math.dist(r['pos'], ici) <= PRES_SUSPECT_M]
    salles = _ids(sorted(voisines, key=lambda r: math.dist(r['pos'], ici))[:3], 'R')
    equipe = [s for s in etat.get('swat', []) if not s['hors_combat'] and 'Trailer' not in s.get('classe', '')]
    eq = {chr(ord('A') + i): s for i, s in enumerate(equipe)}
    # only what matters now: in game (02/11) the full picture took 2.5 to 3.9 s per decision, and the model
    # kept "watching" a calm civilian 50 m away
    vus = {n for s in equipe for n in s.get('voit', [])}
    actifs = [e for e in etat.get('ia', []) if not e['hors_combat'] and not e['arrete']]
    sus = _ids(sorted([e for e in actifs if e['suspect'] and (e['nom'] in vus or e['distance_m'] <= PRES_SUSPECT_M)],
                      key=lambda e: e['distance_m'])[:4], 'S')
    civ = _ids(sorted([e for e in actifs if e['civil'] and (e['nom'] in vus or e['distance_m'] <= PRES_CIVIL_M)
                       and not (e['rendu'] and e['arrete'])], key=lambda e: e['distance_m'])[:3], 'C')
    portes = _ids(sorted([p for p in etat.get('portes', []) if p['distance_m'] <= PRES_PORTE_M],
                         key=lambda p: p['distance_m'])[:3], 'P')

    def ou(pos):
        d = direction(chef, pos)
        return f"à {math.dist(pos, ici):.0f} m du chef" + (f", {d}" if d and d != 'sur lui' else '')
    lignes = [f"Chef : {'en mission' if chef else 'position inconnue'}"
              + (f", dans une pièce {'déjà nettoyée' if sa_piece['nettoyee'] else 'pas encore nettoyée'}" if sa_piece
                 else ', dehors ou entre deux pièces' if chef else '') + '.']
    for k, s in eq.items():
        fait = en_cours.get(s['nom'])
        nomme = indicatif(s)
        lignes.append(f"Coéquipier {k}{' (' + nomme + ')' if nomme else ''} : {ou(s['pos'])}, vie {s['vie']:.0f}"
                      + (' (TRÈS BLESSÉ)' if s['vie'] < BLESSE_PV else '')
                      + (f", en train de : {fait[0]} (depuis {fait[1]:.0f} s)" if fait else '') + '.')
    for k, e in sus.items():
        etat_s = ('rendu' if e['rendu'] else 'obéit' if e.get('obeit')
                  else 'armé et MENAÇANT (arme levée ou a tiré sur l\'équipe)' if e.get('arme') and e.get('menace')
                  else 'armé' if e.get('arme') else 'sans arme visible')
        if e.get('otage'):
            etat_s += ', TIENT UN OTAGE (ne pas tirer)'
        vu = ', '.join(x for x, s in eq.items() if e['nom'] in s.get('voit', []))
        if e.get('disparu_s') is not None:   # cerveau/memoire.py: where he was last seen
            lignes.append(f"Suspect {k} : {etat_s}, DISPARU de la vue il y a {e['disparu_s']} s, dernière position "
                          f"connue {ou(e['pos'])}.")
        else:
            lignes.append(f"Suspect {k} : {etat_s}, {ou(e['pos'])}"
                          + (f", vu par {vu}" if vu else ', vu par le chef' if e.get('vu_chef') else ', pas en vue') + '.')
    for k, e in civ.items():
        lignes.append(f"Civil {k} : {'obéit' if e.get('obeit') or e['rendu'] else 'pas encore sous contrôle'}, {ou(e['pos'])}.")
    for k, p in portes.items():
        details = [d for d, oui in (('PIÉGÉE (piège repéré)', p.get('piege')), ('calée', p.get('calee')),
                                    ('cassée', p.get('cassee'))) if oui]
        lignes.append(f"Porte {k} : {'ouverte' if p['ouverte'] else 'verrouillée' if p['verrouillee'] else 'fermée'}"
                      + (', ' + ', '.join(details) if details else '') + f", {ou(p['pos'])}.")
    stock = {}
    for s in equipe:
        for objet, n in s.get('equipement', {}).items():
            stock[objet] = stock.get(objet, 0) + n
    if stock:
        lignes.append('Équipement de l\'équipe : ' + ', '.join(f"{n} {NOMS_OBJETS.get(o, o)}" for o, n in sorted(stock.items())
                                                              if o in NOMS_OBJETS) + '.')
    for k, r in salles.items():
        lignes.append(f"Pièce {k} pas encore nettoyée : {'celle du chef' if r is sa_piece else ou(r['pos'])}.")
    if style:
        lignes.append(style_mod.description(style, etat.get('style')))
    for s in etat.get('signalements', [])[-2:]:   # what a suspect or a civilian said when questioned (step 6)
        qui = {'suspect': 'un suspect arrêté (il peut mentir)', 'cri': 'un suspect entendu crier (sa voix, pas vu)'}.get(
            s['source'], 'un civil (il peut se tromper)')
        quoi = ('personne d\'autre' if not s['nombre'] else f"{s['nombre']} suspect{'s' if s['nombre'] > 1 else ''}"
                + (f" {ou(s['pos'])}" if s.get('pos') else ''))
        lignes.append(f"Renseignement NON VÉRIFIÉ donné par {qui} il y a {s['il_y_a_s']} s : {quoi}.")
    if ordre_chef:
        lignes.append(f"Dernier ordre du chef (il y a {depuis_s or 0:.0f} s) : « {ordre_chef} ».")
    else:
        lignes.append("Le chef n'a pas donné d'ordre récent.")
    if not niv['initiative']:
        lignes.append("Niveau difficile : sans ordre du chef, l'équipe ne fait que se défendre (garder, surveiller, tirer).")
    tous_civils = [e for e in actifs if e['civil']]   # every one of them, for the line-of-fire check
    # for the grenade guard: the rooms, the leader's, and how many suspects are still free anywhere
    libres = sum(1 for e in actifs if e['suspect'] and not e['rendu'] and not e.get('obeit'))
    return '\n'.join(lignes), {'equipiers': eq, 'S': sus, 'C': civ, 'P': portes, 'R': salles, 'chef': chef,
                               'tous_civils': tous_civils, 'stock': stock, 'style': style, 'pieces': pieces,
                               'piece_chef': sa_piece, 'libres': libres, 'vitres': etat.get('vitres', [])}


def schema(ids):
    """One required entry per teammate (with a free list, the model only ordered the first one, 02/11), plus one
    order for the whole team (EQUIPE): an entry through a door, a grenade, a less-lethal shot."""
    cibles = ([''] + list(ids['S']) + list(ids['C']) + list(ids['P']) + list(ids.get('R', {}))
              + (['CHEF'] if ids.get('chef') else []) + list(ids['equipiers']))
    # one-letter keys (a = action, c = cible, m = methode, o = objet): the answer is what costs time (02/11)
    # only the actions that can apply now: in the big test (02/11) the model asked 22 times to flank with no
    # suspect known, and the guard refused each time
    avec_suspect, avec_personne = bool(ids['S']), bool(ids['S'] or ids['C'])
    avec_cible = bool(ids['S'] or ids['C'] or ids['P'] or ids.get('R'))
    actions = [a for a in ACTIONS if not ((a in ('tirer', 'tir_couverture', 'contourner') and not avec_suspect)
                                          or (a == 'menotter' and not avec_personne)
                                          or (a in ('aller_vers', 'surveiller', 'replacer') and not avec_cible))]
    ordre = {'type': 'object', 'properties': {'a': {'type': 'string', 'enum': actions},
                                              'c': {'type': 'string', 'enum': cibles}},
             'required': ['a', 'c']}
    # only what the team still carries: with the full lists the model asked for a breaching shotgun nobody had
    stock = ids.get('stock', {})
    methodes = [m for m in METHODES if m not in OUTIL_DE_METHODE or stock.get(OUTIL_DE_METHODE[m], 0) > 0]
    objets = ['aucun'] + [o for o in OBJETS[1:] if stock.get(o, 0) > 0]
    actions_equipe = [a for a in ACTIONS_EQUIPE if not ((a == 'entrer' and not ids['P'])
                                                        or (a == 'grenade' and not (ids['S'] or ids['P'] or ids.get('R')))
                                                        or (a == 'non_letal' and not (avec_personne and any(
                                                            stock.get(o, 0) > 0 for o in NON_LETAUX)))
                                                        or (a == 'bouclier' and stock.get('bouclier', 0) <= 0))]
    equipe = {'type': 'object', 'properties': {
        'a': {'type': 'string', 'enum': actions_equipe},
        'c': {'type': 'string', 'enum': [''] + list(ids['S']) + list(ids['C']) + list(ids['P']) + list(ids.get('R', {}))},
        'm': {'type': 'string', 'enum': methodes},
        'o': {'type': 'string', 'enum': objets}},
        'required': ['a', 'c', 'm', 'o']}
    return {'type': 'object', 'properties': {**{k: ordre for k in ids['equipiers']}, 'EQUIPE': equipe},
            'required': list(ids['equipiers']) + ['EQUIPE']}


# ---- orders for the whole team: doors, grenades, less-lethal (the game gives these to a team, not a man) ----
ACTIONS_EQUIPE = ('aucune', 'entrer', 'grenade', 'non_letal', 'bouclier')
METHODES = ('ouvrir', 'enfoncer', 'fusil', 'belier', 'c2')
OBJETS = ('aucun', 'flash', 'stinger', 'gaz', 'ninebang', 'taser', 'beanbag', 'pepperball', 'spray')
GRENADES = ('flash', 'stinger', 'gaz', 'ninebang')
NON_LETAUX = ('taser', 'beanbag', 'pepperball', 'spray')
OUTIL_DE_METHODE = {'fusil': 'fusil_breche', 'belier': 'belier', 'c2': 'c2'}
NOMS_OBJETS = {'flash': 'grenades aveuglantes', 'stinger': 'grenades à billes (stinger)', 'gaz': 'grenades à gaz',
               'ninebang': 'grenades à 9 détonations', 'taser': 'tasers', 'beanbag': 'cartouches beanbag',
               'pepperball': 'lanceurs pepperball', 'spray': 'sprays au poivre', 'c2': 'charges C2',
               'fusil_breche': 'fusils de brèche', 'belier': 'béliers', 'cale': 'cales de porte',
               'optiwand': 'caméras sous porte', 'bouclier': 'boucliers', 'chemlight': 'bâtons lumineux'}
PORTE_MANOEUVRE_M = 8.0     # in game (02/11) a stack-up on a door 22 m from the leader never started; 3.6 m worked
GRENADE_AMI_M = 4.0         # never a grenade this close to the leader or a teammate
GRENADE_CIVIL_M = 3.0       # no stinger or gas this close to a civilian (a flashbang is harmless)
NON_LETAL_M = 15.0
GRENADE_RAISON_M = 10.0     # a free suspect known this close to the door or the point is a reason to throw one
# Wal (03/11): they tried to shoot through windows. LineOfSightTo sees through glass, and the game's line trace
# called through UE4SS crashed the game twice (07:44, 07:52): so the panes' centres (bridge: sonde_vitres, 70 on the
# gas station, 2.7 m apart) and a line of fire passing this close to one is through it
VITRE_R_M = 1.35
VITRE_DZ_M = 1.5
VITRE_GARDE_S = 5   # how long he keeps watching a suspect he must not shoot through glass (renewed while it lasts)


def vitre_entre(a, b, vitres):
    """The pane (x, y, z) the line from a to b goes through, or None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    for g in vitres:
        t = 0.0 if n == 0 else max(0.0, min(1.0, ((g[0] - a[0]) * dx + (g[1] - a[1]) * dy) / n))
        px, py, pz = a[0] + t * dx, a[1] + t * dy, a[2] + t * (b[2] - a[2])
        if math.hypot(px - g[0], py - g[1]) <= VITRE_R_M and abs(pz - g[2]) <= VITRE_DZ_M:
            return g
    return None


# Wal (03/11, again): "every window is bulletproof": windows are there to see through, never to shoot through. So a
# line where a teammate's rounds were seen doing nothing (a pane the map does not list, like the gas station's till
# glass) is kept for the whole team until the mission ends: any shot from near one end at near the other is refused
LIGNE_BLINDEE_M = 2.5


def vitre_sur_ligne(a, b, ids):
    """True when the line from a to b goes through glass: one of the map's panes, or a line learnt bulletproof."""
    if vitre_entre(a, b, ids.get('vitres', [])):
        return True
    r = LIGNE_BLINDEE_M
    return any((math.dist(a, p) <= r and math.dist(b, q) <= r) or (math.dist(a, q) <= r and math.dist(b, p) <= r)
               for p, q in ids.get('lignes_blindees', ()))


def piece_derriere(ids, porte):
    """The room on the far side of a door from the leader: the closest room centre to the door that is not his."""
    autres = [r for r in ids.get('pieces', []) if r is not ids.get('piece_chef')]
    r = min(autres, key=lambda r: math.dist(r['pos'], porte['pos']), default=None)
    return r if r and math.dist(r['pos'], porte['pos']) <= DANS_PIECE_M else None


def refus_grenade(ids, porte=None, piece=None, vers=None):
    """Why no grenade may go there (None = it may). Wal (03/11): a flashbang was thrown where the team already was.
    Never with no suspect left free, never into a room cleared or where the leader or a teammate stands; behind a
    door, only for a free suspect known near it (outside the leader's room) or a room not cleared yet. A grenade Wal
    asked for himself always goes (ids['grenade_demandee'], set by the caller from his order)."""
    if ids.get('grenade_demandee'):
        return None
    if not ids.get('libres', 1):
        return 'plus aucun suspect libre'
    if porte is not None:
        piece = piece_derriere(ids, porte)
    pieces = ids.get('pieces', [])
    if piece is not None:
        if piece.get('nettoyee'):
            return 'piece deja nettoyee'
        nous = ([ids['chef']] if ids.get('chef') else []) + list(ids.get('equipiers', {}).values())
        if any('pos' in a and piece_de(pieces, a['pos']) is piece for a in nous):
            return "le chef ou un coequipier est dans la piece"
    point = porte['pos'] if porte is not None else vers or (piece or {}).get('pos')
    if point is None:
        return None
    pres = any(not e['rendu'] and not e.get('obeit') and math.dist(e['pos'], point) <= GRENADE_RAISON_M
               and (ids.get('piece_chef') is None or piece_de(pieces, e['pos']) is not ids['piece_chef'])
               for e in ids.get('S', {}).values())
    if not pres and piece is None:
        return 'aucun suspect connu par la'
    return None


def garde_equipe(o, ids, a_un_ordre, ordre_frais, niv):
    """The team order -> ('manoeuvre', (door, method, grenade)) | ('cmd', bridge command) | (None, reason)."""
    if not isinstance(o, dict) or o.get('action') not in ACTIONS_EQUIPE or o['action'] == 'aucune':
        return None, 'aucun ordre d equipe'
    if ordre_frais:
        return None, "ordre du chef en cours : pas d'ordre d'equipe"
    if not niv['initiative'] and not a_un_ordre:
        return None, "difficile : pas d'ordre d'equipe sans ordre du chef"
    action, k, stock = o['action'], o.get('cible') or '', ids.get('stock', {})
    objet = o.get('objet') if o.get('objet') in OBJETS else 'aucun'
    chef = ids.get('chef')
    if action == 'entrer':
        porte = ids['P'].get(k)
        if not porte:
            return None, 'entrer : il faut une porte'
        # with the model (02/11) the team started a door entry with a menacing suspect hidden 11 m away: the threat
        # comes first (an entry ties up the whole team)
        if chef and any(e.get('menace') and e.get('arme') and not e['rendu'] and not e.get('obeit')
                        and math.dist(e['pos'], chef['pos']) <= PORTEE_TIR_M for e in ids['S'].values()):
            return None, "entrer refuse : une menace d'abord"
        if chef and math.dist(porte['pos'], chef['pos']) > PORTE_MANOEUVRE_M:
            return None, 'entrer : porte trop loin du chef'
        methode = o.get('methode') if o.get('methode') in METHODES else 'ouvrir'
        outil = OUTIL_DE_METHODE.get(methode)
        if outil and stock.get(outil, 0) <= 0:
            return None, f"entrer : personne n'a de {NOMS_OBJETS[outil]}"
        if porte.get('calee') and methode in ('ouvrir', 'enfoncer'):
            return None, 'entrer : porte calee, il faut le C2 ou le fusil de breche'
        grenade = objet if objet in GRENADES else 'aucune'
        if grenade != 'aucune' and stock.get(grenade, 0) <= 0:
            return None, f"entrer : plus de {NOMS_OBJETS[grenade]}"
        if grenade != 'aucune' and refus_grenade(ids, porte=porte):
            grenade = 'aucune'   # the entry still goes, without a pointless grenade
        return 'manoeuvre', (porte['nom'], methode, grenade)
    if action == 'grenade':
        if objet not in GRENADES or stock.get(objet, 0) <= 0:
            return None, f"grenade : il n'y en a pas ({objet})"
        if k in ids['P']:   # behind a door: the team throws it past the doorway
            porte = ids['P'][k]
            if chef and math.dist(porte['pos'], chef['pos']) > PORTE_MANOEUVRE_M:
                return None, 'grenade : porte trop loin du chef'
            refus = refus_grenade(ids, porte=porte)
            if refus:
                return None, f'grenade refusee : {refus}'
            return 'cmd', f"porte grenade tous {porte['nom']} {objet}"
        vers = (ids['S'].get(k) or ids.get('R', {}).get(k) or {}).get('pos')
        if not vers:
            return None, 'grenade : il faut un suspect, une piece ou une porte'
        if k in ids['S'] and (ids['S'][k]['rendu'] or ids['S'][k].get('obeit')):
            return None, 'grenade refusee : le suspect obeit deja'
        refus = refus_grenade(ids, piece=ids.get('R', {}).get(k), vers=vers)
        if refus:
            return None, f'grenade refusee : {refus}'
        amis = ([chef] if chef else []) + list(ids['equipiers'].values())
        if any('pos' in a and math.dist(a['pos'], vers) < GRENADE_AMI_M for a in amis):
            return None, 'grenade refusee : trop pres du chef ou d un coequipier'
        if objet != 'flash' and any(math.dist(c['pos'], vers) < GRENADE_CIVIL_M for c in ids.get('tous_civils', [])):
            return None, f'grenade refusee : un civil trop pres ({objet})'
        # thrown at a window it would bounce back onto the team (every window is bulletproof): from the closest one
        lanceur = min(ids['equipiers'].values(), key=lambda s: math.dist(s['pos'], vers), default=None)
        if lanceur and vitre_sur_ligne(lanceur['pos'], vers, ids):
            return None, 'grenade refusee : une vitre sur le trajet'
        x, y, z = vers
        return 'cmd', f'grenade tous {objet} {x:.2f} {y:.2f} {z:.2f}'
    if action == 'non_letal':
        cible = ids['S'].get(k) or ids['C'].get(k)
        if not cible:
            return None, 'non letal : il faut une personne'
        if cible['rendu'] or cible.get('obeit'):
            return None, 'non letal refuse : la cible obeit deja'
        if objet not in NON_LETAUX or stock.get(objet, 0) <= 0:
            return None, f"non letal : personne n'en a ({objet})"
        proches = [s for s in ids['equipiers'].values() if math.dist(s['pos'], cible['pos']) <= NON_LETAL_M]
        if not proches:
            return None, 'non letal : trop loin'
        # the game picks which teammate fires: none in range may have a window on his line (all are bulletproof)
        if any(vitre_sur_ligne(s['pos'], cible['pos'], ids) for s in proches):
            return None, 'non letal refuse : une vitre entre un equipier et la cible'
        return 'cmd', f"non_letal tous {cible['nom']} {objet}"
    if action == 'bouclier':
        if stock.get('bouclier', 0) <= 0:
            return None, "bouclier : personne n'en a"
        return 'cmd', 'bouclier tous'
    return None, f'ordre d equipe non gere : {action}'


def demander(modele, texte, ids, timeout=30):
    """Ask the model. Returns (list of raw orders or None, seconds, error)."""
    body = json.dumps({'model': modele, 'stream': False, 'think': False, 'keep_alive': '30m', 'format': schema(ids),
                       'messages': [{'role': 'system', 'content': SYSTEME}, {'role': 'user', 'content': texte}],
                       'options': {'temperature': 0, 'num_ctx': 2048}}).encode()
    t = time.perf_counter()
    try:
        req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rep = json.loads(json.load(r)['message']['content'])
        # back to full names; the team order comes back as equipier "EQUIPE", the caller takes it apart
        longs = {'a': 'action', 'c': 'cible', 'm': 'methode', 'o': 'objet'}
        ordres = [{'equipier': k, **{longs.get(c, c): x for c, x in v.items()}} for k, v in rep.items() if isinstance(v, dict)]
        return ordres, time.perf_counter() - t, None
    except Exception as e:  # malformed, timeout, server down: the guard turns it into nothing
        return None, time.perf_counter() - t, f'{type(e).__name__}: {e}'


# In game (02/11) the model swapped two plans every turn (watch S2 / come back, door P3 / door P2): a teammate
# keeps an order at least this long, unless he must shoot or the situation changed.
TENUE_S = 6.0

# Wal (02/11): "fais quand même qu'ils écoutent mes ordres". After an order from him, the team carries it out:
# for this long the tactical AI may not override it, only defend (shoot an armed threat).
RESPECT_ORDRE_S = 15.0


def _danger(pres_de, ids):
    """The armed, active, not surrendered suspect closest to a point (within shooting range), or None."""
    armes = [e for e in ids['S'].values() if e.get('arme') and not e['rendu'] and not e.get('obeit')
             and math.dist(e['pos'], pres_de) <= PORTEE_TIR_M]
    return min(armes, key=lambda e: (not e.get('menace'), math.dist(e['pos'], pres_de)), default=None)


def _devant(qui, metres):
    lacet = math.radians(qui.get('lacet', 0.0))
    x, y, z = qui['pos']
    return [x + metres * math.cos(lacet), y + metres * math.sin(lacet), z]


def place_de_couverture(protege, couvreur, ids):
    """Where a cover man stands and what he watches. He stands COUVRIR_M behind the one he covers and a little to
    the side (left or right by name, so two cover men do not stand in the same spot), and watches the danger: the
    closest armed suspect, else the nearest uncleared room or door ahead, else straight ahead of the covered one."""
    danger = _danger(protege['pos'], ids)
    vers = danger['pos'] if danger else None
    if not vers:
        lieux = [r['pos'] for r in list(ids.get('R', {}).values()) + list(ids['P'].values())
                 if direction(protege, r['pos']) in ('devant', 'à gauche', 'à droite')]
        vers = min(lieux, key=lambda p: math.dist(p, protege['pos'])) if lieux else _devant(protege, 10.0)
    dx, dy = vers[0] - protege['pos'][0], vers[1] - protege['pos'][1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n                       # from the covered one towards the danger
    cote = 1.0 if sum(map(ord, couvreur['nom'])) % 2 else -1.0
    x, y, z = protege['pos']
    place = [x - COUVRIR_M * ux - cote * uy, y - COUVRIR_M * uy + cote * ux, z]
    return place, [vers[0], vers[1], vers[2] + 1.0]


def point_de_repli(s, ids):
    """Falling back: away from the closest danger, on the leader's side; with no danger, behind the leader."""
    chef = ids.get('chef')
    danger = _danger(s['pos'], ids)
    base = chef['pos'] if chef else s['pos']
    if not danger:
        return _devant(chef, -COUVRIR_M) if chef else s['pos']
    dx, dy = base[0] - danger['pos'][0], base[1] - danger['pos'][1]
    n = math.hypot(dx, dy) or 1.0
    recul = max(n + COUVRIR_M, REPLI_M)
    return [danger['pos'][0] + recul * dx / n, danger['pos'][1] + recul * dy / n, base[2]]


def reflexes(ids):
    """{teammate letter: suspect id} for every teammate who sees a MENACING armed suspect in range: he shoots
    at once, whatever the model said (a reflex, not a plan). Never at a suspect holding a hostage."""
    tirs = {}
    for k, s in ids['equipiers'].items():
        menaces = [(sid, e) for sid, e in ids['S'].items() if e.get('menace') and e.get('arme') and not e['rendu']
                   and not e.get('obeit') and not e.get('otage') and e['nom'] in s.get('voit', [])
                   and math.dist(e['pos'], s['pos']) <= PORTEE_TIR_M]
        if menaces:
            tirs[k] = min(menaces, key=lambda m: math.dist(m[1]['pos'], s['pos']))[0]
    return tirs


LIBRES = ('garder', 'suivre', 'tenir', 'surveiller')   # orders the doctrine may take a teammate away from


def _menaces(ids):
    return [(sid, e) for sid, e in ids['S'].items() if e.get('menace') and e.get('arme') and not e['rendu']
            and not e.get('obeit') and not e.get('otage')]


def doctrine(ordres, ids):
    """Team habits added in code on top of the model's plan (each marked 'doctrine'):
    - fire and manoeuvre: when a teammate moves near a menacing suspect (flanks, closes in, falls back, goes to
      cuff), a free teammate in range lays suppressive fire on that suspect, unless someone already shoots at him;
    - the leader is never left alone: with an armed suspect near him and nobody covering him, the free teammate
      closest to him covers him."""
    ordres = [dict(o) for o in ordres]
    par = {o['equipier']: o for o in ordres if o.get('equipier') in ids['equipiers']}
    for k in ids['equipiers']:
        par.setdefault(k, {'equipier': k, 'action': 'garder', 'cible': ''})
    # less-lethal style: no suppressive fire (bullets sprayed around a man kill him)
    menaces = _menaces(ids) if ids.get('style') != style_mod.NON_LETAL else []
    for k, o in list(par.items()):
        if o['action'] not in ('contourner', 'aller_vers', 'avancer', 'replier', 'menotter') or not menaces:
            continue
        s = ids['equipiers'][k]
        sid, m = min(menaces, key=lambda x: math.dist(x[1]['pos'], s['pos']))
        if math.dist(m['pos'], s['pos']) > PORTEE_TIR_M:
            continue
        if any(p['action'] in ('tirer', 'tir_couverture') and p.get('cible') == sid for p in par.values()):
            continue
        libres = [(j, ids['equipiers'][j]) for j, p in par.items() if j != k and p['action'] in LIBRES
                  and math.dist(ids['equipiers'][j]['pos'], m['pos']) <= PORTEE_TIR_M
                  and not ligne_libre(ids['equipiers'][j], m['pos'], ids)]
        if libres:
            j = min(libres, key=lambda x: math.dist(x[1]['pos'], m['pos']))[0]
            par[j] = {'equipier': j, 'action': 'tir_couverture', 'cible': sid, 'doctrine': 'feu et mouvement'}
    # a menacing suspect nobody sees (with the model, 02/11, the team just watched the wall): the free teammate
    # closest to him steps aside for an angle on him (in every style: it is about seeing him)
    for sid, m in _menaces(ids):
        if any(m['nom'] in s.get('voit', []) for s in ids['equipiers'].values()):
            continue
        if any(p.get('cible') == sid and p['action'] in ('tirer', 'tir_couverture', 'replacer', 'contourner')
               for p in par.values()):
            continue
        libres = [(j, ids['equipiers'][j]) for j, p in par.items() if p['action'] in LIBRES
                  and math.dist(ids['equipiers'][j]['pos'], m['pos']) <= PORTEE_TIR_M]
        if libres:
            j = min(libres, key=lambda x: math.dist(x[1]['pos'], m['pos']))[0]
            par[j] = {'equipier': j, 'action': 'replacer', 'cible': sid, 'doctrine': "chercher l'angle"}
    chef = ids.get('chef')
    armes_pres = chef and any(e.get('arme') and not e['rendu'] and not e.get('obeit')
                              and math.dist(e['pos'], chef['pos']) <= PRES_SUSPECT_M for e in ids['S'].values())
    if armes_pres and not any(p['action'] == 'couvrir' and p.get('cible') == 'CHEF' for p in par.values()):
        libres = [(j, ids['equipiers'][j]) for j, p in par.items() if p['action'] in LIBRES]
        if libres:
            j = min(libres, key=lambda x: math.dist(x[1]['pos'], chef['pos']))[0]
            par[j] = {'equipier': j, 'action': 'couvrir', 'cible': 'CHEF', 'doctrine': 'chef couvert'}
    return list(par.values())


MOTS = {'suivre': 'suivre le chef', 'tenir': 'tenir sa position', 'avancer': 'avancer devant le chef',
        'aller_vers': 'aller vers', 'surveiller': 'surveiller', 'couvrir': 'couvrir', 'replier': 'se replier',
        'replacer': 'se replacer face à', 'contourner': 'contourner', 'fouiller': 'fouiller', 'tirer': 'tirer sur',
        'tir_couverture': 'faire un tir de couverture sur', 'menotter': 'menotter'}


def _distance_au_segment(p, a, b):
    """Horizontal distance from p to the segment a-b, and whether p lies between a and b along it."""
    ax, ay, bx, by, px, py = a[0], a[1], b[0], b[1], p[0], p[1]
    dx, dy = bx - ax, by - ay
    long2 = dx * dx + dy * dy or 1e-9
    t = ((px - ax) * dx + (py - ay) * dy) / long2
    tc = min(1.0, max(0.0, t))
    return math.hypot(px - (ax + tc * dx), py - (ay + tc * dy)), 0.0 < t < 1.0


def bloqueur(tireur, vers, ids):
    """(who, entity) for the first one who must not be hit standing on the line of fire (the leader, a teammate,
    a civilian, a surrendered suspect), or None. Checked for every ordered shot, aimed or suppressive."""
    amis = ([('le chef', ids['chef'])] if ids.get('chef') else []) + \
           [(f'le coéquipier {k}', s) for k, s in ids['equipiers'].items() if s is not tireur]
    neutres = [('un civil', e) for e in ids.get('tous_civils', ids['C'].values())] + \
              [('un suspect rendu', e) for e in ids['S'].values() if e['rendu'] or e.get('obeit')]
    for qui, e in amis + neutres:
        if 'pos' not in e or math.dist(e['pos'], vers) < 0.3:
            continue
        d, entre = _distance_au_segment(e['pos'], tireur['pos'], vers)
        if entre and d < LIGNE_DE_TIR_M:
            return qui, e
    return None


def ligne_libre(tireur, vers, ids):
    """None if the line of fire is clear, else who stands on it."""
    b = bloqueur(tireur, vers, ids)
    return b[0] if b else None


def point_replacement(s, vers, gene=None):
    """A sidestep across the line from him to the target. With someone on the line (gene), away from him and
    long enough to clear him: the line pivots on the target, so a blocker at fraction t of the way moves by
    (1 - t) of the step (in tests a 2.5 m step left a civilian halfway still on the line). Else DECALAGE_M, to
    the side picked by his name."""
    dx, dy = vers[0] - s['pos'][0], vers[1] - s['pos'][1]
    n = math.hypot(dx, dy) or 1.0
    px, py = -dy / n, dx / n                      # unit vector across the line
    cote, pas = (1.0 if sum(map(ord, s['nom'])) % 2 else -1.0), DECALAGE_M
    if gene and 'pos' in gene:
        gx, gy = gene['pos'][0] - s['pos'][0], gene['pos'][1] - s['pos'][1]
        cote = -1.0 if gx * px + gy * py > 0 else 1.0
        t = min(0.9, max(0.0, (gx * dx + gy * dy) / (n * n)))
        pas = min(5.0, max(DECALAGE_M, (LIGNE_DE_TIR_M + 0.6) / (1.0 - t)))
    return [s['pos'][0] + cote * pas * px, s['pos'][1] + cote * pas * py, s['pos'][2]]


def point_contournement(s, cible, ids):
    """A quarter turn around the target from where the team holds it (the leader, else this teammate),
    CONTOURNER_M from the target, on the side picked by his name."""
    base = ids['chef']['pos'] if ids.get('chef') else s['pos']
    dx, dy = base[0] - cible['pos'][0], base[1] - cible['pos'][1]
    n = math.hypot(dx, dy) or 1.0
    cote = 1.0 if sum(map(ord, s['nom'])) % 2 else -1.0
    return [cible['pos'][0] - cote * CONTOURNER_M * dy / n, cible['pos'][1] + cote * CONTOURNER_M * dx / n, s['pos'][2]]


def decrire(o, ids):
    """What a teammate was told, in words that stay true next turn (the short ids are renumbered every turn)."""
    action, k = o.get('action'), o.get('cible') or ''
    quoi = ('le chef' if k == 'CHEF' else f'le coéquipier {k}' if k in ids['equipiers']
            else 'un suspect' if k in ids['S'] else 'un civil' if k in ids['C'] else 'une porte' if k in ids['P']
            else 'une pièce' if k in ids.get('R', {}) else '')
    return f"{MOTS.get(action, action)} {quoi}".strip()


def refus_de_style(cible, ids):
    """Why a lethal shot is not allowed by the leader's style, or None. Less-lethal: only at a suspect who menaces
    (weapon raised or has shot at the team), as self-defence. Mixed: at a suspect who does not menace, the team's
    less-lethal first while it has some."""
    style = ids.get('style')
    if style == style_mod.NON_LETAL and not cible.get('menace'):
        return 'tir refuse : le chef joue en non-letal, le suspect ne menace pas'
    if style == style_mod.MIXTE and not cible.get('menace') and any(ids.get('stock', {}).get(o, 0) > 0 for o in NON_LETAUX):
        return "tir refuse : non-letal d'abord (le suspect ne menace pas)"
    return None


def garde_ordre(o, ids, niv, a_un_ordre, ordre_frais=False):
    """One raw order -> (bridge command or None, reason). Code decides; the model only proposes.
    ordre_frais: Wal gave an order less than RESPECT_ORDRE_S ago; only self-defence may change what he ordered."""
    if not isinstance(o, dict) or o.get('equipier') not in ids['equipiers'] or o.get('action') not in ACTIONS:
        return None, f'ordre illisible : {o!r}'
    s, action, k = ids['equipiers'][o['equipier']], o['action'], o.get('cible') or ''
    if ordre_frais and action not in ('garder', 'tirer', 'tir_couverture'):
        return None, "ordre du chef en cours : pas d'autre ordre que se defendre"
    cible = ids['S'].get(k) or ids['C'].get(k)
    porte = ids['P'].get(k) or ids.get('R', {}).get(k)   # a room is a place to go to, like a door
    nom = s['nom']
    if action == 'garder':
        return None, 'garder'
    if not niv['initiative'] and not a_un_ordre and action in ('avancer', 'aller_vers', 'fouiller', 'menotter', 'contourner'):
        return None, 'difficile : pas sans ordre du chef'
    if action == 'suivre':
        return f'membre_aller {nom} chef', 'ok'
    if action == 'tenir':
        return f'membre_tenir {nom}', 'ok'
    if action == 'avancer':
        return f'membre_aller {nom} devant 6', 'ok'
    if action == 'fouiller':
        salle = ids.get('R', {}).get(k)
        if salle:
            x, y, z = salle['pos']
            return f'membre_fouiller {nom} {x:.2f} {y:.2f} {z:.2f}', 'ok'
        return f'membre_fouiller {nom}', 'ok'
    if action == 'surveiller' and (k == 'CHEF' or k in ids['equipiers']):
        action = 'couvrir'   # "watch the leader / a teammate" (the model's words, 02/11) is covering him
    if action in ('aller_vers', 'surveiller'):
        if not (cible or porte):
            return None, f'{action} sans cible'
        if action == 'surveiller':
            if cible:
                return f'membre_regarder {nom} {cible["nom"]} 5', 'ok'
            if k in ids['P']:
                return f'membre_regarder_porte {nom} {porte["nom"]}', 'ok'
            x, y, z = porte['pos']
            return f'membre_regarder_point {nom} {x:.2f} {y:.2f} {z:.2f}', 'ok'
        x, y, z = (cible or porte)['pos']
        return f'membre_aller {nom} {x:.2f} {y:.2f} {z:.2f}', 'ok'
    if action == 'tirer':
        # rules of engagement, in code: never a civilian, never someone surrendered or complying, only armed
        if not cible or k not in ids['S']:
            return None, 'tir refuse : pas un suspect'
        if cible['rendu'] or cible.get('obeit') or not cible.get('arme'):
            return None, 'tir refuse : suspect rendu, qui obeit ou sans arme'
        if math.dist(cible['pos'], s['pos']) > PORTEE_TIR_M:
            return None, 'tir refuse : trop loin'
        if cible.get('otage'):
            return None, 'tir refuse : il tient un otage'
        refus = refus_de_style(cible, ids)
        if refus:
            return None, refus
        if cible['nom'] not in s.get('voit', []):
            # in game (02/11) a teammate told to fire at a suspect behind a wall emptied 12 rounds into the wall;
            # instead he steps aside for an angle on him
            x, y, z = point_replacement(s, cible['pos'])
            return f'membre_aller {nom} {x:.2f} {y:.2f} {z:.2f}', 'pas en vue : il se replace'
        # Wal (03/11): they may see through a window, never shoot through it: he keeps his eyes on the suspect, where
        # he stands, and holds fire (the model may still order him round)
        if vitre_sur_ligne(s['pos'], cible['pos'], ids):
            return f'membre_regarder {nom} {cible["nom"]} {VITRE_GARDE_S}', 'tir refuse : une vitre dans la ligne de tir, il le garde en vue'
        if (nom, cible['nom']) in ids.get('tirs_bloques', ()):   # the caller saw his rounds do nothing
            return f'membre_regarder {nom} {cible["nom"]} {VITRE_GARDE_S}', 'tir refuse : ses balles ne passent pas, il le garde en vue'
        gene = bloqueur(s, cible['pos'], ids)
        if gene:   # with the model (02/11) the leader stood between a teammate and the threat: step aside, then shoot
            x, y, z = point_replacement(s, cible['pos'], gene[1])
            return f'membre_aller {nom} {x:.2f} {y:.2f} {z:.2f}', f'tir refuse : {gene[0]} dans la ligne de tir, il se decale'
        return f'membre_tirer {nom} {cible["nom"]} {niv["rafale_s"]} {niv["precision"]}', 'ok'
    if action == 'tir_couverture':
        # suppressive fire may go where he cannot see (that is the point), but only at a MENACING armed suspect,
        # never with a friend, civilian or surrendered person on the line, never at a hostage taker
        if not cible or k not in ids['S']:
            return None, 'couverture refusee : pas un suspect'
        if not (cible.get('arme') and cible.get('menace')) or cible['rendu'] or cible.get('obeit'):
            return None, 'couverture refusee : suspect pas menacant'
        if ids.get('style') == style_mod.NON_LETAL:
            return None, 'couverture refusee : le chef joue en non-letal'
        if cible.get('otage'):
            return None, 'couverture refusee : il tient un otage'
        if math.dist(cible['pos'], s['pos']) > PORTEE_TIR_M:
            return None, 'couverture refusee : trop loin'
        if vitre_sur_ligne(s['pos'], cible['pos'], ids):
            return None, 'couverture refusee : une vitre dans la ligne de tir'
        if (nom, cible['nom']) in ids.get('tirs_bloques', ()):
            return None, 'couverture refusee : ses balles ne passent pas'
        gene = bloqueur(s, cible['pos'], ids)
        if gene:
            x, y, z = point_replacement(s, cible['pos'], gene[1])
            return f'membre_aller {nom} {x:.2f} {y:.2f} {z:.2f}', f'couverture refusee : {gene[0]} dans la ligne de tir, il se decale'
        x, y, z = cible['pos']
        return (f'membre_suppression {nom} {x:.2f} {y:.2f} {z + 1.0:.2f} {COUVERTURE_S} '
                f'{niv["precision"] * COUVERTURE_DISPERSION:.2f}'), 'ok'
    if action in ('replacer', 'contourner'):
        vers = (cible or porte or {}).get('pos')
        if not vers:
            return None, f'{action} sans cible'
        if action == 'contourner':
            if not cible or k not in ids['S']:
                return None, 'contourner : il faut un suspect'
            x, y, z = point_contournement(s, cible, ids)
        else:
            x, y, z = point_replacement(s, vers)
        return f'membre_aller {nom} {x:.2f} {y:.2f} {z:.2f}', 'ok'
    if action == 'couvrir':
        protege = ids.get('chef') if k == 'CHEF' else ids['equipiers'].get(k)
        if not protege or protege is s or 'pos' not in protege:
            return None, 'couvrir : il faut le chef ou un autre coequipier'
        place, regard = place_de_couverture(protege, s, ids)
        return (f'membre_couvrir {nom} {place[0]:.2f} {place[1]:.2f} {place[2]:.2f} '
                f'{regard[0]:.2f} {regard[1]:.2f} {regard[2]:.2f}'), 'ok'
    if action == 'replier':
        # the model fell back in calm scenes too (02/11, with the real model): only when hurt or under threat
        menaces = [e for e in ids['S'].values() if e.get('menace') and e.get('arme') and not e['rendu']
                   and math.dist(e['pos'], s['pos']) <= PORTEE_TIR_M]
        if s['vie'] >= BLESSE_PV and not menaces:
            return None, 'repli refuse : ni blesse ni menace'
        if s['vie'] >= BLESSE_PV and any(e['nom'] in s.get('voit', []) for e in menaces):
            # in game (02/11) a teammate who saw the threat backed off and the suspect lived on: he fights back
            return None, 'repli refuse : il voit la menace, il riposte'
        x, y, z = point_de_repli(s, ids)
        return f'membre_aller {nom} {x:.2f} {y:.2f} {z:.2f}', 'ok'
    if action == 'menotter':
        if not cible or not (cible['rendu'] or cible.get('obeit')):
            return None, 'menottes refusees : la cible ne se rend pas'
        if math.dist(cible['pos'], s['pos']) > PORTEE_MENOTTES_M:
            return None, 'menottes refusees : trop loin'
        return f'swat menotter tous {cible["nom"]}', 'ok'
    return None, f'action non geree : {action}'
