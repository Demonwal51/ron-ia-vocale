"""What the suspects and civilians overhear (Wal, 03/11): "s'il entend que je dis qu'on va le contourner, il s'adapte,
et peut transmettre l'info aux autres qui peuvent l'entendre".

- intentions(): what a police sentence gives away, from a closed list (the code reads it, never the model: an
  instruction is not a guard): a flank, an entry, a grenade, a search, the police holding;
- qui_entend(): every suspect and civilian within earshot of the speaker (closer through walls), as for a shout;
- Renseignements: what each one knows of the police's plans, heard himself or shouted to him by a friend, and the
  shouts still to pass on (a suspect who heard passes it to the friends within reach, once, at most two hops).

What they do about it is decided in cerveau/adversaire.py (reactions_ecoute) and by the suspects' model, which is told.
"""
import math
import re
import threading
import time

import interlocuteurs

# first the strongest: "entrez avec une flash" is an entry AND a grenade. French and English (Wal, 03/11)
INTENTIONS = [
    ('grenade', re.compile(r"\b(flash\w*|grenades?|aveuglant\w*|stinger|lacrymo\w*|gaz|fumig[eè]n\w*|"
                           r"nine ?bang|gas|cs gas|smoke)\b", re.I)),
    ('contourner', re.compile(r"\b(contourn\w*|[àa] revers|par (l'|l )?arri[eè]re|par derri[eè]re|"
                              r"(sur|par) (le |son |leur )?flanc|fai(tes|s) le tour|en tenaille|encercl\w*|"
                              r"des deux c[ôo]t[ée]s|par l'autre (porte|c[ôo]t[ée]|entr[ée]e)|"
                              r"flank\w*|go (a)?round|from (behind|the back)|(a)?round the back|the other (side|door|way)|"
                              r"surround\w*|both sides|pincer)\b", re.I)),
    ('entrer', re.compile(r"\b(on (y va|entre|rentre|enfonce|d[ée]fonce|force|ouvre|avance|pousse)|entrez|rentrez|"
                          r"enfoncez|d[ée]foncez|breach\w*|go go|en avant|avancez|ouvrez (la|cette) porte|c'est parti|"
                          r"(we('re| are)|i'?m) going in|going in|kick (it|the door)|move in|let'?s go|stack up|push in|"
                          r"open (the|that) door|on my (go|mark)|execute)\b", re.I)),
    ('fouiller', re.compile(r"\b(fouill\w*|s[ée]curis\w*|nettoy\w*|search\w*|sweep|clear (the|this|that) (room|area)|"
                            r"secure (the|this|that) (room|area))\b", re.I)),
    ('attendre', re.compile(r"\b(tenez (la )?position|on attend|attendez|restez (l[àa]|en place|ici)|on tient|on se pose|"
                            r"hold (the |your )?position|hold here|wait (here|there)?|stay (here|put)|stand by)\b", re.I)),
]
MOTS = {'grenade': 'qu\'elle va lancer une grenade', 'contourner': 'qu\'elle va le contourner (le prendre par un autre côté)',
        'entrer': 'qu\'elle va entrer', 'fouiller': 'qu\'elle va fouiller', 'attendre': 'qu\'elle attend sur place'}
VALABLE_S = 45.0        # a plan overheard counts this long
RELAIS_S = 1.5          # he shouts it to his friends this long after hearing it
RELAIS_FRAIS_S = 20.0   # ...but not news older than this
SAUTS_MAX = 2           # heard -> shouted on -> shouted on once more, no further
# the shout that passes it on: the first kind this voice has (52 suspect voices out of 52 have [TELL]Shooting)
LIGNES_RELAIS = {'grenade': ('[TELL]Grenades', '[SQUAD]CommandCover', '[TELL]Shooting'),
                 'contourner': ('[TELL]Cover', '[SQUAD]CommandCover', '[TELL]Shooting'),
                 'entrer': ('[TELL]HeardSwat', '[SQUAD]CommandCover', '[TELL]Shooting'),
                 'fouiller': ('[TELL]HeardSwat', '[SQUAD]Rally', '[TELL]Shooting'),
                 'attendre': ('[TELL]Waiting', '[SQUAD]Rally', '[TELL]Shooting')}


def intentions(texte):
    """The police plans a sentence gives away, strongest first (['entrer', 'grenade'] ...), [] for none."""
    return [q for q, rx in INTENTIONS if rx.search(texte or '')]


def qui_entend(etat, parleur_pos):
    """[(person, distance)] who hear the speaker: suspects and civilians not out of action, closer through walls."""
    if not parleur_pos:
        return []
    gens = []
    for e in etat.get('ia', []):
        if e['hors_combat'] or e['arrete'] or 'pos' not in e:
            continue
        d = math.dist(e['pos'], parleur_pos)
        if d <= (interlocuteurs.CRI_VU_M if e.get('vu_chef') else interlocuteurs.CRI_MUR_M):
            gens.append((e, d))
    return gens


# the teammates' own lines (cerveau/annonces.py) give plans away too: what the suspects make of each one (Wal, 03/11)
PLANS_DES_LIGNES = {'[RESPONSE]StackUp': ['entrer'], '[RESPONSE]OpenClear': ['entrer'], '[RESPONSE]BreachKick': ['entrer'],
                    '[RESPONSE]BreachShotgun': ['entrer'], '[RESPONSE]BreachC2': ['entrer'], '[CALL]BreachDone': ['entrer'],
                    '[RESPONSE]DeployFlashbang': ['grenade'], '[RESPONSE]DeployStinger': ['grenade']}


def plans_de_ligne(ligne):
    """The plans a teammate's line gives away ([] for "Roger", "Moving"...)."""
    return PLANS_DES_LIGNES.get(ligne, [])


# ---- the other way: the police hears the suspects (Wal, 03/11) ----
CRI_REDIT_S = 10.0   # one hint per shouting suspect this often
CRI_ENTENDU_M = 12.0   # a shout carries farther through a wall than talk (8 m)


def police_entend(etat, crieur):
    """True when the player or a teammate hears this suspect shout and does not see him (a hint, not a sighting)."""
    vus = {n for s in etat.get('swat', []) for n in s.get('voit', [])}
    if crieur.get('vu_chef') or crieur['nom'] in vus:
        return False
    oreilles = [j['pos'] for j in etat.get('joueurs', []) if j.get('moi') and 'pos' in j]
    oreilles += [s['pos'] for s in etat.get('swat', []) if not s['hors_combat'] and 'Trailer' not in s.get('classe', '')]
    return any(math.dist(p, crieur['pos']) <= CRI_ENTENDU_M for p in oreilles)


# a civilian who hears the police close by may call out where the suspects are, on his own, once (his type)
INFORMATEURS = ('cooperatif', 'calme', 'panique')


def ligne_relais(quoi, sortes):
    """The kind of line he shouts for that news, among the kinds his voice has (sortes; empty = his subtitles are not
    there: the one every suspect voice has, the others exist for few voices)."""
    return next((l for l in LIGNES_RELAIS[quoi] if l in sortes), '[TELL]Shooting')


class Renseignements:
    """name -> {plan: {'t', 'pos' (where the police spoke from), 'comment' ('entendu' | 'dit'), 'par', 'sauts',
    'relaye', 'reagi'}}. Shared by the voice thread (entendre) and the suspects' loop: one lock."""

    def __init__(self):
        self.verrou = threading.Lock()
        self.carte, self.sait = None, {}

    def _carte(self, carte):
        if carte != self.carte:
            self.carte, self.sait = carte, {}

    def entendre(self, etat, texte, parleur_pos, qui='joueur', maintenant=None, plans=None):
        """A police sentence said by qui (the suspects' name for that policeman): who heard which plan. Returns
        {'quoi': [...], 'par': [names]} or None. plans: the plans themselves (a teammate's recorded line, no text)."""
        t = time.time() if maintenant is None else maintenant
        quoi = intentions(texte) if plans is None else list(plans)
        if not quoi:
            return None
        auditeurs = qui_entend(etat, parleur_pos)
        with self.verrou:
            self._carte(etat.get('carte'))
            for e, _ in auditeurs:
                k = self.sait.setdefault(e['nom'], {})
                for q in quoi:
                    # a civilian keeps it to himself (only the suspects pass it on)
                    k[q] = {'t': t, 'pos': list(parleur_pos), 'qui': qui, 'comment': 'entendu', 'par': None, 'sauts': 0,
                            'relaye': not e.get('suspect'), 'reagi': False}
        return {'quoi': quoi, 'par': [e['nom'] for e, _ in auditeurs]}

    def en_attente(self, maintenant=None):
        """True while some news is still to be shouted on or acted on (else the loop has nothing to do)."""
        t = time.time() if maintenant is None else maintenant
        with self.verrou:
            return any(not (i['reagi'] and (i['relaye'] or i['sauts'] >= SAUTS_MAX)) and t - i['t'] <= VALABLE_S
                       for plans in self.sait.values() for i in plans.values())

    def connus(self, maintenant=None):
        """[(name, police, where it spoke from, when, how)] of every fresh plan: what the suspects know of the voice."""
        t = time.time() if maintenant is None else maintenant
        with self.verrou:
            return [(n, i.get('qui', 'joueur'), i['pos'], i['t'], i['comment']) for n, plans in self.sait.items()
                    for i in plans.values() if t - i['t'] <= VALABLE_S]

    def de(self, nom, maintenant=None):
        """{plan: info} still valid for him."""
        t = time.time() if maintenant is None else maintenant
        with self.verrou:
            return {q: dict(i) for q, i in self.sait.get(nom, {}).items() if t - i['t'] <= VALABLE_S}

    def a_reagir(self, noms, delai, maintenant=None):
        """[(name, plan, info)] heard or told at least delai ago (his reaction time) and not acted on yet."""
        t = time.time() if maintenant is None else maintenant
        with self.verrou:
            return [(n, q, dict(i)) for n in noms for q, i in self.sait.get(n, {}).items()
                    if not i['reagi'] and delai <= t - i['t'] <= VALABLE_S]

    def marquer_reagi(self, nom, quoi):
        with self.verrou:
            if quoi in self.sait.get(nom, {}):
                self.sait[nom][quoi]['reagi'] = True

    def relais(self, etat, portee_m, maintenant=None):
        """The shouts due now: [(shouter, plan, [friends told])]. A suspect still free who heard (or was told) a fresh
        plan RELAIS_S ago passes it once to the free suspects within portee_m who did not know it."""
        t = time.time() if maintenant is None else maintenant
        libres = {e['nom']: e for e in etat.get('ia', []) if e.get('suspect') and not e['hors_combat']
                  and not e['arrete'] and not e['rendu'] and not e.get('obeit')}
        cris = []
        with self.verrou:
            self._carte(etat.get('carte'))
            for nom, plans in list(self.sait.items()):   # the friends told are added on the way
                crieur = libres.get(nom)
                if crieur is None:
                    continue
                for q, i in list(plans.items()):
                    if i['relaye'] or i['sauts'] >= SAUTS_MAX or not RELAIS_S <= t - i['t'] <= RELAIS_FRAIS_S:
                        continue
                    i['relaye'] = True
                    # only those who do not know it yet (not back to who told him, nor to who heard it himself)
                    amis = [x for n, x in libres.items() if n != nom and math.dist(x['pos'], crieur['pos']) <= portee_m
                            and (q not in self.sait.get(n, {}) or t - self.sait[n][q]['t'] > VALABLE_S)]
                    for x in amis:
                        self.sait.setdefault(x['nom'], {})[q] = {'t': t, 'pos': i['pos'], 'qui': i.get('qui', 'joueur'),
                                                               'comment': 'dit', 'par': nom, 'sauts': i['sauts'] + 1,
                                                               'relaye': False, 'reagi': False}
                    if amis:
                        cris.append((nom, q, [x['nom'] for x in amis]))
        return cris
