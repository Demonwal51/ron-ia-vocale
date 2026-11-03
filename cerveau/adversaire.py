"""The suspects' and civilians' AI (step 6): the local model plays the suspects through the mission, like the SWAT.

Wal (02/11): the same care as for the SWAT, a little less sharp (the SWAT is an elite unit), but the suspects know
the place better. So:
- they know the map from the start: every room, which rooms connect, where their friends are;
- they know the police only from what one of them sees or hears (no wallhack, as cerveau/memoire.py for the SWAT),
  and a suspect who saw the police warns the friends near him (better at higher difficulty);
- slower and less coordinated than the SWAT, the gap narrowing with the difficulty (NIVEAUX).

The game's own AI keeps doing the fighting (aim, shots, cover animations); the model decides the manoeuvre: hold,
watch, ambush by a doorway, fall back, flank through another room, join a friend, warn the others, hide, fake a
surrender, give up. In game (02/11) the suspects walk only while a move request is renewed every second, and the
game's own activities (flee to an exit, take cover, take a hostage, rally) started by hand never moved anyone:
`Marcheur` renews the moves, and those activities are not used. The civilians of the barricade maps (hostages)
cannot be moved at all; a civilian who does not move is left alone (the game decides his part).

garde() decides in code: unknown suspect or target, a move away from a fight he is winning, an ambush without
initiative, a room that is not where he can go: all become "garder" (the game's AI goes on).
"""
import json
import math
import time
import urllib.request

from cerveau import OLLAMA
import ecoute
import interlocuteurs
import tacticien

# what each difficulty means for the suspects (compare tacticien.NIVEAUX for the SWAT): periode_s, how often they
# re-think; reaction_s, the delay after a change (a policeman in sight); partage_m, how far a warning reaches;
# initiative: may they ambush or flank (or only hold, fall back, hide); combien: suspects thought about per turn.
NIVEAUX = {
    'facile': {'periode_s': 6.0, 'reaction_s': 2.0, 'partage_m': 8.0, 'initiative': False, 'combien': 2},
    'standard': {'periode_s': 4.0, 'reaction_s': 1.2, 'partage_m': 18.0, 'initiative': True, 'combien': 3},
    'difficile': {'periode_s': 3.0, 'reaction_s': 0.7, 'partage_m': 30.0, 'initiative': True, 'combien': 4},
}

ACTIONS = ('garder', 'tenir', 'surveiller', 'embuscade', 'replier', 'contourner', 'rejoindre', 'alerter', 'cacher',
           'faux_rendre', 'otage', 'se_rendre')
MOTS = {'garder': 'continuer ce qu\'il fait', 'tenir': 'tenir sa position', 'surveiller': 'surveiller une pièce ou un policier',
        'embuscade': 'se poster en embuscade près du passage vers une pièce', 'replier': 'se replier dans une pièce',
        'contourner': 'contourner la police par une autre pièce', 'rejoindre': 'rejoindre un complice',
        'alerter': 'prévenir les complices en criant', 'cacher': 'aller se cacher loin de la police',
        'faux_rendre': 'faire semblant de se rendre', 'otage': 'prendre en otage le civil le plus proche',
        'se_rendre': 'se rendre'}
SAIT_S = 45.0          # a policeman seen is remembered this long
ENTENDU_M = 30.0       # an alerted suspect who sees nobody hears the police this close (direction, not the spot)
TENUE_S = 6.0          # a suspect keeps an order this long (no flip-flop between two plans)
ALERTE_S = 20.0        # one warning shout per suspect at most this often
FAUX_RENDRE_M = 10.0
# hostage taking goes through the game's own action (AIAction_TakeHostage_C): in game (house map, 02/11) it took
# the hostage 3 times out of 6 with the police in view, never with the police far; the game decides the rest
OTAGE_CIVIL_M = 7.0
OTAGE_POLICE_M = 15.0
OTAGE_REESSAI_S = 60.0
OTAGE_TYPES = ('fanatique', 'nerveux', 'drogue', 'fier', 'calme')   # not a coward, not a talker
OTAGES_TENTES = {}     # suspect -> when he last tried (names are new on every map)
BLESSE_PV = 150        # of 360: a suspect this low may break off a fight (fall back, hide)
PIECES_MAX = 6

SYSTEME = """Tu diriges les suspects armés du jeu Ready or Not contre une équipe du SWAT qui entre dans les lieux.
Les suspects connaissent bien les lieux (toutes les pièces et les passages), mais ne savent de la police que ce
qu'ils ont vu ou entendu. Pour CHAQUE suspect, choisis UNE action et sa cible, de façon crédible pour sa personnalité :
un fanatique tient ou tend une embuscade, un lâche se cache ou se rend, un rusé contourne ou fait semblant.
Le jeu fait les gestes et les tirs : toi tu décides la manœuvre. Garder = laisser le suspect continuer.
Un suspect qui a entendu ce que prépare la police s'y prépare : un contournement, il surveille l'autre passage ou se
replie ; une entrée, il s'embusque ou se cache ; une grenade, il quitte la pièce.
Réponds seulement en JSON."""


class SavoirPolice:
    """What each suspect knows of the police: seen by him, heard by him, or told by a friend near him."""

    def __init__(self):
        self.carte, self.sait = None, {}   # suspect -> {police: (pos, t, how)}

    def mettre_a_jour(self, etat, niv, maintenant=None):
        t = time.time() if maintenant is None else maintenant
        if etat.get('carte') != self.carte:
            self.carte, self.sait = etat.get('carte'), {}
        pos_police = {'joueur': next((j['pos'] for j in etat.get('joueurs', []) if j.get('moi') and 'pos' in j), None)}
        for s in etat.get('swat', []):
            if not s['hors_combat'] and 'Trailer' not in s.get('classe', ''):
                pos_police[s['nom']] = s['pos']
        for j in etat.get('joueurs', []):
            if not j.get('moi') and 'pos' in j:
                pos_police['joueur:' + j['nom']] = j['pos']
        suspects = [e for e in etat.get('ia', []) if e.get('suspect') and not e['hors_combat'] and not e['arrete']]
        for e in suspects:
            k = self.sait.setdefault(e['nom'], {})
            for nom in e.get('voit_police', []):
                if pos_police.get(nom):
                    k[nom] = (pos_police[nom], t, 'vu')
            if e.get('eveil') == 2 and not e.get('voit_police'):
                # alerted but sees nobody: he heard them; he knows the direction, roughly (the closest policeman,
                # within earshot), not who it is
                proches = [(math.dist(p, e['pos']), n, p) for n, p in pos_police.items() if p]
                if proches:
                    d, n, p = min(proches)
                    if d <= ENTENDU_M and (n not in k or k[n][2] != 'vu' or t - k[n][1] > 5):
                        k[n] = (p, t, 'entendu')
        # a suspect who knows warns his friends near him (how far depends on the difficulty)
        for e in suspects:
            for x in suspects:
                if x is e or math.dist(x['pos'], e['pos']) > niv['partage_m']:
                    continue
                kx = self.sait.setdefault(x['nom'], {})
                for n, (p, ti, how) in self.sait.get(e['nom'], {}).items():
                    if how != 'dit' and (n not in kx or kx[n][1] < ti):
                        kx[n] = (p, ti, 'dit')
        for k in self.sait.values():
            for n in [n for n, (_, ti, _) in k.items() if t - ti > SAIT_S]:
                del k[n]

    def de(self, nom):
        return self.sait.get(nom, {})

    def voix(self, nom, police, pos, t, comment):
        """He heard the police speak from pos (or a friend shouted where): known, unless he knows fresher."""
        k = self.sait.setdefault(nom, {})
        if police not in k or k[police][1] < t:
            k[police] = (list(pos), t, 'entendu' if comment == 'entendu' else 'dit')


class Marcheur:
    """The suspects walk only while a move request is renewed every second (in game, 02/11): this renews each one
    until he gets there, gives up after a while, and remembers who cannot be moved at all."""
    ARRIVE_M = 1.2
    MAX_S = 20.0
    BLOQUE_S = 4.0
    BLOQUE_ENVOIS = 3      # in game (02/11) a suspect given up after ONE request (the loop was busy) never moved
    IMMOBILE_S = 60.0      # then he is left alone this long (the game may free him later: alerted, out of a pose)

    def __init__(self):
        self.buts, self.immobiles = {}, {}   # name -> {'but', 't0', 'regard', 'p0', 'raison', 'n'} ; name -> since

    def immobile(self, nom, maintenant=None):
        t = time.time() if maintenant is None else maintenant
        if nom in self.immobiles and t - self.immobiles[nom] > self.IMMOBILE_S:
            del self.immobiles[nom]
        return nom in self.immobiles

    MANOEUVRE_S = 4.0      # a combat move runs by itself (it reached its point within 5 s): renewed this rarely

    def aller(self, nom, but, regard=None, raison='', maintenant=None, manoeuvre=None):
        if self.immobile(nom, maintenant):
            return False
        t = time.time() if maintenant is None else maintenant
        self.buts[nom] = {'but': list(but), 't0': t, 'regard': regard, 'p0': None, 'raison': raison, 'n': 0,
                          'manoeuvre': manoeuvre, 't_envoi': -1e9}
        return True

    def arreter(self, nom):
        self.buts.pop(nom, None)

    def tour(self, etat, envoyer, maintenant=None):
        """Renew every move once (call it about once a second). Returns the commands sent."""
        t = time.time() if maintenant is None else maintenant
        par_nom = {e['nom']: e for e in etat.get('ia', [])}
        envoyes = []
        for nom, m in list(self.buts.items()):
            e = par_nom.get(nom)
            if not e or e['hors_combat'] or e['arrete'] or e['rendu'] or e.get('obeit') or e.get('otage'):
                del self.buts[nom]
                continue
            if m['p0'] is None:
                m['p0'] = list(e['pos'])
            if math.dist(e['pos'][:2], m['but'][:2]) <= self.ARRIVE_M:
                del self.buts[nom]
                if m['regard']:
                    envoyes.append(envoyer('adv_regarder ' + nom + ' ' + ' '.join(f'{v:.2f}' for v in m['regard']) + ' 8'))
                continue
            if t - m['t0'] > self.MAX_S:
                del self.buts[nom]
                continue
            if t - m['t0'] > self.BLOQUE_S and m['n'] >= self.BLOQUE_ENVOIS and math.dist(e['pos'][:2], m['p0'][:2]) < 0.3:
                # he never moved: the game holds him (a seated hostage, a scripted pose)
                del self.buts[nom]
                self.immobiles[nom] = t
                continue
            point = ' '.join(f'{v:.2f}' for v in m['but'])
            if m.get('manoeuvre'):
                if t - m['t_envoi'] < self.MANOEUVRE_S:
                    continue
                m['n'] += 1
                m['t_envoi'] = t
                r = envoyer(f"adv_manoeuvre {nom} {m['manoeuvre']} {point}")
                envoyes.append(r)
                if isinstance(r, str) and '|ECHEC|' in r:
                    if "activite de combat" in r or "pas de " in r:
                        m['manoeuvre'] = None   # no such combat move for him: plain walking from the next turn
                    else:
                        self.buts.pop(nom, None)
                continue
            m['n'] += 1
            r = envoyer('adv_aller ' + nom + ' ' + point)
            envoyes.append(r)
            if isinstance(r, str) and '|ECHEC|' in r:   # being cuffed, dying: the game refuses, stop asking
                self.buts.pop(nom, None)
        return envoyes


def _ids(liste, lettre):
    return {f'{lettre}{i + 1}': x for i, x in enumerate(liste)}


def actifs(etat):
    # eveil -1: the bridge found no controller to ask. On the house map (02/11) a suspect who had just taken a
    # hostage stayed so, and got 6 move orders in a row that could do nothing: the game drives him, not us
    return [e for e in etat.get('ia', []) if e.get('suspect') and not e['hors_combat'] and not e['arrete']
            and not e['rendu'] and not e.get('obeit') and not e.get('otage') and e.get('eveil', 0) != -1]


def situation(etat, savoir, persos, niv, en_cours=None, maintenant=None, rens=None, noms=None):
    """(text for the model, ids) for the suspects who matter now: alerted, or knowing where the police is (or only
    those named in noms: the ones reacting to what they heard). rens: what they overheard of the police's plans."""
    t = time.time() if maintenant is None else maintenant
    en_cours = en_cours or {}
    pieces = etat.get('pieces', [])
    if noms is not None:
        candidats = [e for e in actifs(etat) if e['nom'] in noms]
    else:
        candidats = [e for e in actifs(etat) if e.get('eveil', 0) >= 1 or savoir.de(e['nom'])]
        candidats.sort(key=lambda e: min((math.dist(p, e['pos']) for p in interlocuteurs.police(etat)), default=99))
    sus = _ids(candidats[:niv['combien']] if noms is None else candidats, 'S')
    if not sus:
        return None, None
    # the rooms that matter: theirs, their neighbours, and where the police was last seen
    noms_pieces = []

    def ajouter(r):
        if r and r['nom'] not in noms_pieces:
            noms_pieces.append(r['nom'])
    connu = {}
    for e in sus.values():
        for n, (p, ti, how) in savoir.de(e['nom']).items():
            if n not in connu or connu[n][1] < ti:
                connu[n] = (p, ti, how)
    for e in sus.values():
        r = tacticien.piece_de(pieces, e['pos'])
        ajouter(r)
    for p, _, _ in connu.values():
        ajouter(tacticien.piece_de(pieces, p))
    for e in sus.values():
        r = tacticien.piece_de(pieces, e['pos'])
        for v in (r or {}).get('voisines', []):
            ajouter(next((x for x in pieces if x['nom'] == v), None))
    salles = _ids([next(x for x in pieces if x['nom'] == n) for n in noms_pieces[:PIECES_MAX]], 'R')
    flics = _ids(sorted(connu.items(), key=lambda kv: -kv[1][1]), 'P')   # id -> (name, (pos, t, how))
    allies = _ids([e for e in actifs(etat) if e['nom'] not in {x['nom'] for x in sus.values()}][:3], 'A')

    def salle_de(pos):
        r = tacticien.piece_de(pieces, pos)
        k = next((k for k, x in salles.items() if r and x['nom'] == r['nom']), None)
        return f'dans {k}' if k else ('dans une autre pièce' if r else 'dehors')
    lignes = []
    for k, e in sus.items():
        perso = persos[e['nom']]
        sait = savoir.de(e['nom'])
        vus = [kp for kp, (n, _) in flics.items() if n in e.get('voit_police', [])]
        connus = [f"{kp} ({'vu' if how == 'vu' else 'entendu' if how == 'entendu' else 'signalé par un complice'} "
                  f"il y a {t - ti:.0f} s, {salle_de(p)}, à {math.dist(p, e['pos']):.0f} m)"
                  for kp, (n, (p, ti, how)) in flics.items() if n in sait]
        fait = en_cours.get(e['nom'])
        plans = rens.de(e['nom'], t) if rens is not None else {}
        entendu = [f"{'a entendu la police dire' if i['comment'] == 'entendu' else 'un complice lui a crié'} "
                   f"{ecoute.MOTS[q]} (il y a {t - i['t']:.0f} s)" for q, i in plans.items()]
        lignes.append(f"Suspect {k} : {perso['mots']}, santé {e['vie']:.0f}, {'armé' if e.get('arme') else 'sans arme'}, "
                      f"{salle_de(e['pos'])}" + (f", VOIT {', '.join(vus)}" if vus else '')
                      + (f" ; sait : {', '.join(connus)}" if connus else ' ; ne sait pas où est la police')
                      + (f" ; {' ; '.join(entendu)}" if entendu else '')
                      + (f" ; en train de : {fait[0]} (depuis {t - fait[1]:.0f} s)" if fait else '') + '.')
    for k, e in allies.items():
        lignes.append(f"Complice {k} : {salle_de(e['pos'])}, santé {e['vie']:.0f}.")
    for k, r in salles.items():
        voisines = [kk for kk, x in salles.items() if x['nom'] in r.get('voisines', [])]
        police_la = [kp for kp, (n, (p, _, _)) in flics.items() if tacticien.piece_de(pieces, p) is r]
        lignes.append(f"Pièce {k} ({r['nom']}) : communique avec {', '.join(voisines) if voisines else 'des pièces lointaines'}"
                      + (f" ; police ({', '.join(police_la)}) là" if police_la else '') + '.')
    if not niv['initiative']:
        lignes.append('Niveau facile : pas d\'embuscade ni de contournement, les suspects tiennent, se replient ou se cachent.')
    return '\n'.join(lignes), {'S': sus, 'R': salles, 'P': flics, 'A': allies}


def choix(ids, persos, niv, etat, echanges=None, deja_faux=None, maintenant=None):
    """For each suspect, only the (action, target) pairs the guard accepts, as "action" or "action:target".
    With free actions and targets (02/11, real model) every order of a turn was refused: an ambush "on" a
    policeman, a flank through a room that does not open onto the police, a surrender with no pressure."""
    autres = list(ids['A'])
    menus = {}
    for k in ids['S']:
        cibles = {'tenir': [''], 'alerter': [''], 'faux_rendre': [''], 'se_rendre': [''], 'otage': [''],
                  'surveiller': list(ids['P']) + list(ids['R']), 'embuscade': list(ids['R']), 'replier': list(ids['R']),
                  'contourner': list(ids['R']), 'cacher': list(ids['R']),
                  'rejoindre': autres + [s for s in ids['S'] if s != k]}
        opts = ['garder']
        for a in ACTIONS[1:]:
            for c in cibles[a]:
                fait, _ = garde({'suspect': k, 'action': a, 'cible': c}, ids, persos, niv, etat, echanges, deja_faux, maintenant)
                if fait == a:
                    opts.append(f'{a}:{c}' if c else a)
        menus[k] = opts
    return menus


DEPLACEMENTS = ('embuscade', 'replier', 'contourner', 'rejoindre', 'cacher')


def sans_deplacement(menus, ids, marcheur, maintenant=None):
    """A suspect the game holds in place is offered no move (the model kept ordering one, 02/11)."""
    return {k: [o for o in opts if not (o.partition(':')[0] in DEPLACEMENTS and marcheur.immobile(ids['S'][k]['nom'], maintenant))]
            for k, opts in menus.items()}


def texte_des_choix(menus, ids):
    """The meaning of each action once, then each suspect's choices. In game (02/11) the meaning repeated after every
    choice made the question too long: the answer came back cut twice ("Unterminated string")."""
    utiles = sorted({o.partition(':')[0] for opts in menus.values() for o in opts}, key=ACTIONS.index)
    return ('Actions : ' + ' ; '.join(f'{a} = {MOTS[a]}' for a in utiles) + ' (action:cible).\n'
            + '\n'.join(f"Choix pour {k} : " + ', '.join(opts) + '.' for k, opts in menus.items()))


def schema(menus):
    return {'type': 'object', 'properties': {k: {'type': 'string', 'enum': opts} for k, opts in menus.items()},
            'required': list(menus)}


def demander(modele, texte, ids, menus, timeout=30):
    """One model call for every suspect of the turn: each picks one of his own choices."""
    body = json.dumps({'model': modele, 'stream': False, 'think': False, 'keep_alive': '30m', 'format': schema(menus),
                       'messages': [{'role': 'system', 'content': SYSTEME},
                                    {'role': 'user', 'content': texte + '\n' + texte_des_choix(menus, ids)}],
                       'options': {'temperature': 0.3, 'num_ctx': 2048}}).encode()
    t = time.perf_counter()
    try:
        req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rep = json.loads(json.load(r)['message']['content'])
        ordres = []
        for k, v in rep.items():
            if isinstance(v, str):
                a, _, c = v.partition(':')
                ordres.append({'suspect': k, 'action': a, 'cible': c})
        return ordres, time.perf_counter() - t, None
    except Exception as e:   # malformed, timeout, server down: nothing changes, the game's AI goes on
        return None, time.perf_counter() - t, f'{type(e).__name__}: {e}'


# ---- the guard: what each order really becomes ----
def _proche_police(e, ids):
    return min((math.dist(p, e['pos']) for _, (p, _, _) in ids['P'].values()), default=None)


def _vers(a, b, frac):
    return [a[i] + (b[i] - a[i]) * frac for i in range(3)]


def _de_cote(a, b, metres):
    """A point beside the line a -> b (to the left), this far."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return [-dy / n * metres, dx / n * metres]


# in a fight he sees, a move is one of the game's own combat moves, run by its fight logic (in game, house map,
# 02/11: a flank to a point ended 0.4 m from it, cover at a point 0.2 m, a reposition 5 m toward it in 5 s, while
# our plain walking orders lost to that logic). "tenir" there means taking cover where the game finds it.
COMBAT = {'contourner': 'FlankingCombatMove', 'replier': 'RepositionCombatMove', 'rejoindre': 'RepositionCombatMove',
          'cacher': 'RepositionCombatMove', 'tenir': 'HardCoverCombatMove'}


def garde(o, ids, persos, niv, etat, echanges=None, deja_faux=None, maintenant=None):
    """One raw order -> (action, details) where details holds the point to walk to, the point to look at, the
    friends to warn, and in a fight the game's combat move to use; or ('garder', reason) when it cannot stand."""
    action, d = _garde(o, ids, persos, niv, etat, echanges, deja_faux, maintenant)
    sus = ids['S'].get(o.get('suspect'))
    if sus is not None and action in COMBAT and sus.get('voit_police') and sus.get('arme'):
        d = {**d, 'manoeuvre': COMBAT[action]}
    return action, d


def _garde(o, ids, persos, niv, etat, echanges=None, deja_faux=None, maintenant=None):
    sus = ids['S'].get(o.get('suspect'))
    if sus is None:
        return 'garder', {'raison': f"suspect inconnu {o.get('suspect')!r}"}
    a, c = o.get('action'), o.get('cible') or ''
    if a not in ACTIONS:
        return 'garder', {'raison': f'action hors liste {a!r}'}
    perso = persos[sus['nom']]
    pieces = etat.get('pieces', [])
    sa_piece = tacticien.piece_de(pieces, sus['pos'])
    proche = _proche_police(sus, ids)
    voit = bool(sus.get('voit_police'))
    # in a fight he can see, our walking orders fought the game's AI (02/11): he moves through the game's own combat
    # moves instead (see COMBAT). Not the moment to lie in wait or watch a door; he hides only when badly hurt
    if voit and sus.get('arme'):
        if a in ('embuscade', 'surveiller'):
            return 'garder', {'raison': 'en plein combat : pas le moment'}
        if a == 'cacher' and sus['vie'] > BLESSE_PV:
            return 'garder', {'raison': 'en plein combat, il ne se cache que blesse'}
    if a in ('embuscade', 'contourner') and not niv['initiative']:
        return 'garder', {'raison': 'pas d\'initiative a ce niveau'}
    if a in ('embuscade', 'contourner') and perso['type'] == 'lache':
        return 'garder', {'raison': 'un lache ne tend pas d\'embuscade'}
    if a == 'garder':
        return 'garder', {'raison': 'ok'}
    if a == 'tenir':
        return 'tenir', {}
    if a == 'surveiller':
        if c in ids['P']:
            return 'surveiller', {'regard': ids['P'][c][1][0]}
        if c in ids['R']:
            return 'surveiller', {'regard': ids['R'][c]['pos']}
        return 'garder', {'raison': 'rien a surveiller'}
    if a == 'alerter':
        amis = [e for e in actifs(etat) if e['nom'] != sus['nom'] and math.dist(e['pos'], sus['pos']) <= max(niv['partage_m'], 15)]
        if not amis or not ids['P']:
            return 'garder', {'raison': 'personne a prevenir ou rien a dire'}
        return 'alerter', {'amis': [e['nom'] for e in amis]}
    if a == 'otage':
        t = time.time() if maintenant is None else maintenant
        if not sus.get('arme') or perso['type'] not in OTAGE_TYPES:
            return 'garder', {'raison': 'pas arme, ou pas le genre a prendre un otage'}
        if t - OTAGES_TENTES.get(sus['nom'], -1e9) < OTAGE_REESSAI_S:
            return 'garder', {'raison': 'deja essaye il y a peu'}
        civils_pres = [e for e in etat.get('ia', []) if e.get('civil') and not e['hors_combat'] and not e['arrete']
                       and math.dist(e['pos'], sus['pos']) <= OTAGE_CIVIL_M]
        if not civils_pres:
            return 'garder', {'raison': 'aucun civil assez pres'}
        if proche is None or proche > OTAGE_POLICE_M:
            return 'garder', {'raison': 'la police est loin : le jeu ne le fait pas'}
        return 'otage', {}
    if a == 'faux_rendre':
        if not sus.get('arme') or perso['ruse'] < 0.45 or (deja_faux is not None and sus['nom'] in deja_faux):
            return 'garder', {'raison': 'pas rusé, pas armé, ou déjà fait'}
        if not voit or proche is None or proche > FAUX_RENDRE_M:
            return 'garder', {'raison': 'personne assez pres pour y croire'}
        return 'faux_rendre', {}
    if a == 'se_rendre':
        if echanges is None:
            return 'garder', {'raison': 'pas de mesure de pression'}
        p, _ = interlocuteurs.pression(sus, etat, perso, echanges, {'tons': []}, maintenant=maintenant)
        if p < interlocuteurs.seuil(perso, etat_niveau(etat)) + 0.15 or perso['type'] == 'fanatique':
            return 'garder', {'raison': f'pas assez de pression pour se rendre seul ({p:.2f})'}
        return 'se_rendre', {}
    if a == 'rejoindre':
        ami = ids['A'].get(c) or ids['S'].get(c)
        if ami is None or ami is sus:
            return 'garder', {'raison': 'complice inconnu'}
        # next to him, 1.5 m on the side away from the police when it is known
        flics = [p for _, (p, _, _) in ids['P'].values()]
        if flics:
            f = min(flics, key=lambda p: math.dist(p, ami['pos']))
            dx, dy = ami['pos'][0] - f[0], ami['pos'][1] - f[1]
            n = math.hypot(dx, dy) or 1.0
            cote = [1.5 * dx / n, 1.5 * dy / n]
        else:
            cote = [1.5, 0.0]
        return 'rejoindre', {'but': [ami['pos'][0] + cote[0], ami['pos'][1] + cote[1], ami['pos'][2]]}
    # the rest go to a room
    r = ids['R'].get(c)
    if r is None:
        return 'garder', {'raison': 'piece inconnue'}
    police_pos = [p for _, (p, _, _) in ids['P'].values()]

    def loin_police(pos):
        return min((math.dist(pos, p) for p in police_pos), default=99.0)
    if a in ('replier', 'cacher'):
        if police_pos and loin_police(r['pos']) <= loin_police(sus['pos']):
            return 'garder', {'raison': 'cette piece n\'est pas plus loin de la police'}
        if a == 'replier' and sa_piece and r['nom'] not in sa_piece.get('voisines', []) and r is not sa_piece:
            return 'garder', {'raison': 'pas une piece voisine'}
        regard = min(police_pos, key=lambda p: math.dist(p, r['pos'])) if police_pos else None
        return a, {'but': r['pos'], 'regard': regard}
    if a == 'embuscade':
        if not sa_piece or (r is not sa_piece and r['nom'] not in sa_piece.get('voisines', [])):
            return 'garder', {'raison': 'embuscade loin de sa piece'}
        if r is sa_piece:   # wait inside his own room, beside the way the police will come in
            entree = police_pos[0] if police_pos else sus['pos']
            p = _vers(sa_piece['pos'], entree, 0.35)
            cote = _de_cote(sa_piece['pos'], entree, 2.0)
            return 'embuscade', {'but': [p[0] + cote[0], p[1] + cote[1], p[2]], 'regard': _vers(sa_piece['pos'], entree, 0.8)}
        # beside the passage from his room to the neighbouring room, looking at it
        p = _vers(sa_piece['pos'], r['pos'], 0.35)
        cote = _de_cote(sa_piece['pos'], r['pos'], 1.8)
        return 'embuscade', {'but': [p[0] + cote[0], p[1] + cote[1], p[2]], 'regard': r['pos']}
    if a == 'contourner':
        if not police_pos:
            return 'garder', {'raison': 'police inconnue'}
        cible_police = min(police_pos, key=lambda p: math.dist(p, sus['pos']))
        piece_police = tacticien.piece_de(pieces, cible_police)
        if piece_police is not None and r['nom'] == piece_police['nom']:
            return 'garder', {'raison': 'contourner, pas foncer dans la piece de la police'}
        if piece_police is not None and r['nom'] not in piece_police.get('voisines', []):
            return 'garder', {'raison': 'cette piece ne donne pas sur la police'}
        if math.dist(r['pos'], cible_police) > 1.5 * math.dist(sus['pos'], cible_police) + 5:
            return 'garder', {'raison': 'ce detour l\'eloigne de la police'}
        return 'contourner', {'but': r['pos'], 'regard': cible_police}
    return 'garder', {'raison': 'action sans traduction'}


def etat_niveau(etat):
    return tacticien.niveau(etat.get('difficulte'))[0]


# a suspect's line when he does it, his own recorded voice: only lines heard in game for a suspect (02/11,
# GasSuspect_01, 3 runs out of 3); [SQUAD]Cover, [BARK]Flanking and [SQUAD]Rally do not exist for every voice
LIGNES = {'alerter': '[TELL]Shooting', 'cacher': '[TELL]Fleeing', 'replier': '[TELL]Fleeing',
          'faux_rendre': '[BARK]Compliant', 'se_rendre': '[BARK]Compliant'}


def executer(action, d, sus, marcheur, envoyer, maintenant=None):
    """The bridge commands for one guarded order. Moves go to the walker, which renews them."""
    nom = sus['nom']
    cmds = []
    # (no "eveil" = the bridge did not read it, beyond the 20 closest: unknown, so alert him too. With "known alert"
    # as the default, 5 orders on the house map, 02/11 at night, never moved anyone)
    if sus.get('eveil', 0) < 2 and action not in ('garder', 'se_rendre'):
        # he acts on what he knows (a friend warned him): the game must know he is alert too. In game (02/11) an
        # unalerted suspect busy robbing the till never walked a step
        cmds.append(envoyer(f'adv_eveil {nom} 2'))
    if action == 'tenir':
        marcheur.arreter(nom)
        if d.get('manoeuvre'):
            cmds.append(envoyer(f"adv_manoeuvre {nom} {d['manoeuvre']}"))
        else:
            cmds.append(envoyer(f'adv_tenir {nom}'))
    elif action == 'surveiller':
        cmds.append(envoyer(f'adv_regarder {nom} ' + ' '.join(f'{v:.2f}' for v in d['regard']) + ' 8'))
    elif action == 'alerter':
        for ami in d['amis']:
            cmds.append(envoyer(f'adv_eveil {ami} 2'))
    elif action == 'faux_rendre':
        marcheur.arreter(nom)
        cmds.append(envoyer(f'faux_rendre {nom}'))
    elif action == 'otage':
        marcheur.arreter(nom)
        OTAGES_TENTES[nom] = time.time() if maintenant is None else maintenant
        cmds.append(envoyer(f'adv_action {nom} TakeHostage'))
    elif action == 'se_rendre':
        marcheur.arreter(nom)
        cmds.append(envoyer(f'rendre {nom}'))
    elif 'but' in d:
        marcheur.aller(nom, d['but'], d.get('regard'), action, maintenant, manoeuvre=d.get('manoeuvre'))
    ligne = LIGNES.get(action)
    if ligne:
        cmds.append(envoyer(f'adv_parler {nom} {ligne}'))
    return cmds


# ---- what they do about a police plan they overheard (Wal, 03/11), at once, without the model ----
def _preferences(quoi, perso, sus, ids, etat):
    """The orders to try, best first, for a suspect who just learnt this plan; each still goes through garde()."""
    pieces = etat.get('pieces', [])
    police = [p for _, (p, _, _) in ids['P'].values()]
    sa = tacticien.piece_de(pieces, sus['pos'])
    k_sa = next((k for k, r in ids['R'].items() if sa and r['nom'] == sa['nom']), None)
    piece_police = tacticien.piece_de(pieces, police[0]) if police else None

    def loin(k):
        return min((math.dist(ids['R'][k]['pos'], p) for p in police), default=0.0)
    salles = sorted(ids['R'], key=loin, reverse=True)   # farthest from the police first
    fuir = [('replier', k) for k in salles] + [('cacher', k) for k in salles]
    craintif = perso['type'] == 'lache' or (perso['type'] == 'nerveux' and quoi != 'contourner')
    if quoi == 'grenade':   # out of that room before it goes off
        return fuir + [('tenir', '')]
    if craintif or not sus.get('arme'):   # nowhere farther to go: he stays where he is, hidden
        return [('cacher', k) for k in salles] + [('replier', k) for k in salles] + [('tenir', '')]
    if quoi == 'entrer':    # they are coming in: wait for them by the way in
        return ([('embuscade', k_sa)] if k_sa else []) + [('surveiller', k) for k in ids['P']] + fuir
    if quoi == 'contourner':
        # someone will come from another side: watch the passages that are not the police's, those that also open
        # onto the police's room first (the way round)
        voisines = [k for k in ids['R'] if sa and ids['R'][k]['nom'] in sa.get('voisines', [])
                    and not (piece_police and ids['R'][k]['nom'] == piece_police['nom'])]
        voisines.sort(key=lambda k: not (piece_police and ids['R'][k]['nom'] in piece_police.get('voisines', [])))
        return ([('embuscade', k) for k in voisines] + [('surveiller', k) for k in voisines]
                + [('rejoindre', k) for k in ids['S'] if ids['S'][k] is not sus] + fuir)
    if quoi == 'fouiller':
        return ([('embuscade', k_sa)] if k_sa else []) + [('cacher', k) for k in salles]
    return []   # 'attendre': only something to know (the model is told)


def reactions_ecoute(etat, savoir, rens, persos, niv, en_cours=None, echanges=None, deja_faux=None, maintenant=None,
                     marcheur=None):
    """[(suspect, plan, action, details)] for the free suspects whose reaction time has passed since they heard (or
    were told) a police plan: the first of their preferences the guard accepts. Each plan once per suspect. One the
    game holds in place (marcheur.immobile; in game, 03/11, two of four sent to an ambush never took a step) gets
    the first one that needs no walking."""
    t = time.time() if maintenant is None else maintenant
    libres = {e['nom'] for e in actifs(etat)}
    dus = [(n, q, i) for n, q, i in rens.a_reagir(sorted(libres), niv['reaction_s'], t)]
    if not dus:
        return []
    _, ids = situation(etat, savoir, persos, niv, en_cours, t, rens, noms={n for n, _, _ in dus})
    if ids is None:
        return []
    par_nom = {s['nom']: k for k, s in ids['S'].items()}
    ordre = [q for q, _ in ecoute.INTENTIONS]
    faits, sorties = set(), []
    for n, q, i in sorted(dus, key=lambda x: ordre.index(x[1])):   # the strongest plan first
        rens.marquer_reagi(n, q)
        k = par_nom.get(n)
        if k is None or n in faits:
            continue
        prefs = _preferences(q, persos[n], ids['S'][k], ids, etat)
        if marcheur is not None and marcheur.immobile(n, t):
            prefs = [(a, c) for a, c in prefs if a not in DEPLACEMENTS]
            prefs += [] if any(a in ('surveiller', 'tenir') for a, _ in prefs) else \
                [('surveiller', kp) for kp in ids['P']] + [('tenir', '')]
        for a, c in prefs:
            fait, d = garde({'suspect': k, 'action': a, 'cible': c}, ids, persos, niv, etat, echanges, deja_faux, t)
            if fait == a:
                sorties.append((ids['S'][k], q, a, d))
                faits.add(n)
                break
    return sorties


CIVIL_ECOUTE = ('grenade', 'entrer')   # what a civilian reacts to: something is about to happen right here


def reaction_civil(c, quoi, perso, etat):
    """(what, details) for a civilian who overheard a police plan, or None: a panicked one gets away from where the
    police spoke (to the neighbouring room farthest from it), the others freeze where they are."""
    if quoi not in CIVIL_ECOUTE or c['hors_combat'] or c['arrete'] or c['rendu'] or c.get('obeit') or perso is None:
        return None
    if perso['type'] == 'choque':
        return None
    pieces = etat.get('pieces', [])
    police = interlocuteurs.police(etat)
    sa = tacticien.piece_de(pieces, c['pos'])
    if perso['panique'] >= 0.5 and pieces and police:
        proches = [r for r in pieces if sa is None or r['nom'] in sa.get('voisines', []) or r is sa]
        r = max(proches, key=lambda r: min(math.dist(r['pos'], p) for p in police), default=None)
        if r is not None and r is not sa:
            return 'cacher', {'but': r['pos']}
    return 'se_figer', {}


# ---- civilians: simple rules (there may be 15 of them; the model is kept for the suspects) ----
CIVIL_REPENSE_S = 15.0
CIVIL_MENACE_M = 6.0
CIVIL_COMBAT_M = 12.0


def civils(etat, persos, marcheur, deja, maintenant=None):
    """[(civilian, what, details)] for the civilians who react on their own this turn: a panicked one runs away from
    the shooting to a room far from the suspects and the police (if the game lets him walk), one with an armed
    suspect raising his weapon next to him freezes; the others are left to the game."""
    t = time.time() if maintenant is None else maintenant
    pieces = etat.get('pieces', [])
    menaces = [e for e in etat.get('ia', []) if e.get('suspect') and e.get('menace') and not e['hors_combat']]
    danger = [e['pos'] for e in menaces] + interlocuteurs.police(etat)
    sorties = []
    for c in etat.get('ia', []):
        if not c.get('civil') or c['hors_combat'] or c['arrete'] or c['rendu'] or c.get('obeit'):
            continue
        if t - deja.get(c['nom'], -1e9) < CIVIL_REPENSE_S or marcheur.immobile(c['nom'], t):
            continue
        perso = persos.get(c['nom'])
        if perso is None:
            continue
        pres = [m for m in menaces if math.dist(m['pos'], c['pos']) <= CIVIL_MENACE_M]
        if pres:
            deja[c['nom']] = t
            sorties.append((c, 'se_figer', {}))
            continue
        # a fight close by (a suspect threatening or shooting it out with the police, not right next to him: then
        # he freezes, above): he gets away from it, police in view or not, unless he is calm or stubborn (low panic)
        # or in shock (he stays put, lost)
        combat = [e for e in etat.get('ia', []) if e.get('suspect') and not e['hors_combat'] and not e['arrete']
                  and (e.get('menace') or e.get('voit_police')) and math.dist(e['pos'], c['pos']) <= CIVIL_COMBAT_M]
        fuit_combat = bool(combat) and perso['type'] != 'choque' and perso['panique'] >= 0.3
        if c.get('eveil') == 2 and pieces and (fuit_combat or (perso['panique'] >= 0.7 and not c.get('voit_police'))):
            if combat:
                danger = danger + [e['pos'] for e in combat]
            sa = tacticien.piece_de(pieces, c['pos'])
            proches = [r for r in pieces if sa is None or r['nom'] in sa.get('voisines', []) or r is sa]
            if not proches:
                continue
            r = max(proches, key=lambda r: min((math.dist(r['pos'], p) for p in danger), default=0))
            if r is sa:
                continue
            deja[c['nom']] = t
            sorties.append((c, 'cacher', {'but': r['pos']}))
    return sorties
