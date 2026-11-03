"""Talking to suspects and civilians (step 6): what was said, how hard he is pushed, what he may do, his answer.

The model judges the reaction of each person addressed, but the code decides what is POSSIBLE (Wal's rule: an
instruction is not a guard): with little pressure a calm armed suspect is never offered to surrender, a civilian
never shoots. How much pressure it takes follows the game's difficulty (Wal: "tout dépend de la difficulté").

- analyser(): the request (à terre, mains en l'air, viens, sors, ne bouge pas, où sont les autres...), the tone
  (threat, calm, promise, insult) and whether it is shouted at everyone ("tout le monde à terre").
- Echanges: what each person was told this mission (the 3rd warning weighs more than the 1st; a promise followed
  by a shot is never believed again).
- pression(): from the game's state (his stress, health, morale, police around him, aimed at, allies down nearby),
  the exchange and the tone.
- options(): the reactions the code allows for him now; reagir(): one model call for everyone addressed;
  garde(): anything else becomes no reaction.
"""
import json
import math
import re
import time
import urllib.request

from cerveau import OLLAMA

# ---- what was said ----
# each in French and in English (Wal, 03/11: an English version; a player may speak either)
DEMANDES = [   # first match wins
    ('info', re.compile(r"\b(o[uù] (sont|est|se trouvent?|se cachent?)|combien|qui d'autre|il y a d'autres|y a d'autres|"
                        r"il en reste|vous [eê]tes combien|t'es tout seul|tu es seul|d'autres (personnes|gars|mecs)|"
                        r"where (are|is) (the )?(others|rest|they|everyone|your friends)|how many|who else|"
                        r"any(one|body) else|are you alone|you alone)\b", re.I)),
    ('venir', re.compile(r"\b(viens?|venez|approchez?|avance[sz]? vers moi|par ici|vers moi|rampe[sz]? vers moi|"
                         r"come (here|to me|over( here)?|towards me)|walk (to|towards) me|this way|over here|"
                         r"crawl (to|towards) me)\b", re.I)),
    ('sortir', re.compile(r"\b(sors|sortez|dehors|sortie|[ée]vacuez?|get out|come out|leave( now)?|evacuate)\b", re.I)),
    ('arme', re.compile(r"\b(l[aâ]che[sz]?|pose[sz]?|jette[sz]?|laisse[sz]? tomber|d[ée]pose[sz]?)\b.{0,15}\b(armes?|flingues?|"
                        r"pistolets?|fusils?|couteaux?|ça|ca)\b"
                        r"|\b(drop|put down|lose|let go of|throw down|lay down)\b.{0,20}\b(weapons?|guns?|pistols?|rifles?|"
                        r"knife|knives|it|that)\b|\bput (it|that|the \w+|your \w+) down\b", re.I)),
    ('a_terre', re.compile(r"\b([àa] terre|au sol|par terre|couche[sz]?[- ]?(toi|vous)?|[àa] genoux|allonge[sz]?|"
                           r"get down|on the (ground|floor)|on your knees|lie down|get on the (ground|floor))\b", re.I)),
    ('mains', re.compile(r"\b(mains? (en l'air|en l air|sur la t[eê]te|derri[eè]re)|l[eè]ve[sz]? les mains|"
                         r"montre[sz]? (tes|vos|moi tes|moi vos) mains|hands up|put your hands up|"
                         r"(show me|let me see) your hands|hands (in the air|on your head|behind your (head|back)))\b", re.I)),
    ('immobile', re.compile(r"\b(ne bouge[sz]? (pas|plus)|bouge[sz]? plus|plus un geste|stop|arr[eê]te[sz]?[- ]?(toi|vous)?|"
                            r"pas un geste|freeze|don'?t (you )?move|do not move|stay (still|where you are)|hold it)\b", re.I)),
    ('tourner', re.compile(r"\b(tourne[sz]?[- ](toi|vous)|retourne[sz]?[- ](toi|vous)|dos [àa] moi|turn around|"
                           r"face the wall)\b", re.I)),
]
TONS = [
    ('menace', re.compile(r"\b(je (vais|te) (te )?(tuer|buter|descendre|tire|shooter)|je tire|on tire|sinon|t'es mort|"
                          r"tu es mort|je vais tirer|on va tirer|derni[eè]re? (fois|sommation|chance)|je te bute|"
                          r"i('ll| will) (shoot|kill|fire)|i'?m gonna (shoot|kill)|we('ll| will) (shoot|fire)|or i (shoot|fire)|"
                          r"last (warning|chance)|you('re| are) dead)\b", re.I)),
    ('promesse', re.compile(r"\b(on ne (te )?(tire|fera) (pas|rien)|personne ne (va )?te (fera|faire) (de )?mal|"
                            r"il ne (va )?t'arrivera? rien|il t'arrivera rien|promis|je te promets|tu ne risques rien|"
                            r"on veut juste|on ne veut pas te|(nobody|no one)('s| is)? (going to|gonna|will) (hurt|shoot) you|"
                            r"we (won'?t|will not) (hurt|shoot) you|you('re| are) safe|i promise|we just want)\b", re.I)),
    ('insulte', re.compile(r"\b(connard|connasse|enfoir[ée]|encul[ée]|fdp|fils de pute|salope?|batard|b[aâ]tard|"
                           r"ordure|merdeux|abruti|cr[eé]tin|trou du cul|asshole|motherfucker|fucker|bastard|bitch|"
                           r"piece of shit|dumbass|fuck you)\b", re.I)),
    ('calme', re.compile(r"\b(calme[sz]?[- ]?(toi|vous)?|du calme|tout va bien|doucement|tranquille|respire[sz]?|"
                         r"[çc]a va aller|on est l[àa] pour vous aider|on est la pour t'aider|calm down|stay calm|"
                         r"take it easy|relax|it'?s (okay|ok|alright)|breathe|we('re| are) here to help)\b", re.I)),
]
FOULE = re.compile(r"\b(tout le monde|vous tous|tous [àa] terre|tous au sol|personne ne bouge|que personne ne bouge|"
                   r"tout le monde au sol|everybody|everyone|all of you|(nobody|no one) (move|moves))\b", re.I)


def analyser(texte):
    """{'demande', 'tons', 'foule'} for a sentence aimed at people."""
    demande = next((d for d, rx in DEMANDES if rx.search(texte)), 'rendre')
    return {'demande': demande, 'tons': [t for t, rx in TONS if rx.search(texte)], 'foule': bool(FOULE.search(texte))}


def est_question_info(texte):
    return DEMANDES[0][1].search(texte) is not None


# orders that can only be for the person in front ("à terre", "mains en l'air", "lâche ton arme", "ne bouge plus"):
# in game (02/11) the model made "À terre ! Mains en l'air !" a SWAT handcuffing order 3 times out of 3
POUR_LA_PERSONNE = ('arme', 'a_terre', 'mains', 'immobile', 'tourner')


def ordre_a_la_personne(texte):
    # not when it speaks to the team: "Menottez-le", "Équipe, stop", "Stop, on bouge plus" (a SWAT hold)
    # (in English: "Cuff him", "Team, hold", "Stop, we hold here")
    return analyser(texte)['demande'] in POUR_LA_PERSONNE and not re.search(
        r"\b(menottez|menotte[sz]?|[ée]quipe|suivez|couvrez|fouillez|tenez|restez|arr[eê]tez[- ]le|stop|on|"
        r"cuff\w*|restrain|team|squad|follow|cover|search|hold (the |your )?position)\b", texte, re.I)


MOTS_DEMANDE = {'info': 'te demande où sont les autres', 'venir': 'te dit de venir vers lui', 'sortir': 'te dit de sortir',
                'arme': 'te dit de lâcher ton arme', 'a_terre': 'te dit de te mettre à terre',
                'mains': 'te dit de lever les mains', 'immobile': 'te dit de ne plus bouger',
                'tourner': 'te dit de te retourner', 'rendre': 'te somme de te rendre'}
MOTS_TON = {'menace': 'il te menace', 'promesse': 'il te promet de ne pas te faire de mal', 'insulte': 'il t\'insulte',
            'calme': 'il parle calmement pour te rassurer'}


# ---- what each person was told this mission ----
SOMMATION_S = 60.0     # warnings this recent count together
PROMESSE_S = 90.0      # a promise is watched this long: a shot at him or next to him breaks it
TRAHISON_M = 6.0


class Echanges:
    def __init__(self):
        self.par_nom = {}      # name -> {'dits': [(t, demande, tons)], 'promesse': t or None, 'trahi': bool}
        self.vie = {}          # name -> highest health seen (his full health) and last health
        self.carte = None

    def _de(self, nom):
        return self.par_nom.setdefault(nom, {'dits': [], 'promesse': None, 'trahi': False})

    def noter(self, nom, analyse, maintenant=None):
        t = time.time() if maintenant is None else maintenant
        x = self._de(nom)
        x['dits'].append((t, analyse['demande'], tuple(analyse['tons'])))
        x['dits'] = x['dits'][-12:]
        if 'promesse' in analyse['tons']:
            x['promesse'] = t

    def sommations(self, nom, maintenant=None):
        t = time.time() if maintenant is None else maintenant
        return sum(1 for (ti, _, _) in self.par_nom.get(nom, {}).get('dits', []) if t - ti <= SOMMATION_S)

    def observer(self, etat, maintenant=None):
        """Watch health: a person hurt, or someone hurt next to him, while a promise holds = the promise is broken.
        A new map clears everything."""
        t = time.time() if maintenant is None else maintenant
        if etat.get('carte') != self.carte:
            self.carte, self.par_nom, self.vie = etat.get('carte'), {}, {}
        blesses = []
        for e in etat.get('ia', []):
            haut, avant = self.vie.get(e['nom'], (e['vie'], e['vie']))
            if e['vie'] < avant - 1 or (e['hors_combat'] and avant > 0):
                blesses.append(e)
            self.vie[e['nom']] = (max(haut, e['vie']), 0 if e['hors_combat'] else e['vie'])
        for nom, x in self.par_nom.items():
            if x['promesse'] is None or t - x['promesse'] > PROMESSE_S or x['trahi']:
                continue
            lui = next((e for e in etat.get('ia', []) if e['nom'] == nom), None)
            if lui and any(b['nom'] == nom or math.dist(b['pos'], lui['pos']) <= TRAHISON_M for b in blesses):
                x['trahi'] = True

    def vie_max(self, e):
        return self.vie.get(e['nom'], (e['vie'], e['vie']))[0] or 1.0

    def resume(self, nom, maintenant=None):
        """French words for the model: how often he was warned, promises, broken trust."""
        x = self.par_nom.get(nom)
        if not x:
            return 'premier contact avec la police'
        n = self.sommations(nom, maintenant)
        bouts = [f"on t'a déjà crié dessus {n - 1} fois juste avant" if n > 1 else 'premier ordre de ce policier']
        if x['trahi']:
            bouts.append('la police a promis de ne pas tirer puis a tiré : tu ne la crois plus')
        elif x['promesse']:
            bouts.append('la police t\'a promis de ne pas te faire de mal')
        return ', '.join(bouts)


# ---- how hard he is pushed ----
# pressure it takes to give in, by the game's difficulty (Wal: it all depends on the difficulty), moved by courage
SEUILS = {'facile': 0.25, 'standard': 0.38, 'difficile': 0.52}
PROCHE_M = 10.0
AMI_TOMBE_M = 12.0


def police(etat):
    """Positions of the police: the players and the active SWAT teammates (not the reinforcements far away)."""
    p = [j['pos'] for j in etat.get('joueurs', []) if 'pos' in j]
    p += [s['pos'] for s in etat.get('swat', []) if not s['hors_combat'] and 'Trailer' not in s.get('classe', '')]
    return p


def pression(e, etat, perso, echanges, analyse, letal=True, maintenant=None):
    """0 (calm, safe) to about 1 (cornered, hurt, outnumbered, warned again and again). With the details."""
    d = {}
    stress = max(0.0, e.get('stress', 0) or 0)
    d['stress'] = 0.25 * min(stress, 1.5) / 1.5
    d['blessure'] = 0.2 * (1 - min(1.0, e['vie'] / echanges.vie_max(e)))
    n = sum(1 for p in police(etat) if math.dist(p, e['pos']) <= PROCHE_M)
    d['nombre'] = 0.15 * min(n, 4) / 4
    d['vise'] = 0.1 if e.get('vise', 0) >= 0.9 and e.get('distance_m', 99) <= 25 else 0.0
    d['sommations'] = 0.15 * min(max(echanges.sommations(e['nom'], maintenant) - 1, 0), 3) / 3
    moral = e.get('moral', -1)
    d['moral'] = 0.15 * (1 - moral) if moral is not None and 0 <= moral <= 1 else 0.07
    tombes = sum(1 for x in etat.get('ia', []) if x['nom'] != e['nom'] and x.get('suspect') == e.get('suspect')
                 and (x['hors_combat'] or x['arrete']) and math.dist(x['pos'], e['pos']) <= AMI_TOMBE_M)
    d['amis_tombes'] = 0.1 * min(tombes, 2) / 2
    d['equipe_letale'] = 0.05 if letal and e.get('suspect') else 0.0
    tons = analyse.get('tons', [])
    fier = perso['type'] in ('fier', 'fanatique', 'tetu')
    d['ton'] = ((0.08 if not fier else -0.05) if 'menace' in tons else 0) \
        + (0.05 if 'promesse' in tons or 'calme' in tons else 0) + ((-0.05 if fier else 0.02) if 'insulte' in tons else 0)
    if echanges.par_nom.get(e['nom'], {}).get('trahi'):
        d['confiance'] = -0.1
    return max(0.0, sum(d.values())), d


def seuil(perso, niveau):
    return SEUILS.get(niveau, SEUILS['standard']) + (perso['courage'] - 0.5) * 0.3


# ---- what he may do ----
# reaction -> words for the model
SENS = {
    'se_rendre': 'se rendre (lâcher son arme, mains en l\'air, à genoux)',
    'faux_rendre': 'faire semblant de se rendre pour attaquer ensuite',
    'hesiter': 'hésiter, gagner du temps',
    'resister': 'refuser et lever son arme',
    'fuir': 'fuir vers une autre pièce',
    'obeir': 'obéir (mains en l\'air, à genoux)',
    'venir': 'obéir et venir vers le policier',
    'paniquer': 'paniquer, crier',
    'se_figer': 'se figer sans rien faire',
    'refuser': 'refuser d\'obéir',
    'dire_vrai': 'dire la vérité',
    'mentir': 'mentir',
    'se_taire': 'ne rien dire',
}


def options(e, perso, p, niveau, demande):
    """The reactions the code allows for him now (the model picks among them)."""
    s = seuil(perso, niveau)
    # questioned: only a tough one (or one under little pressure) keeps quiet; the others talk, true or not.
    # With silence always offered the model chose it 6 times out of 6, even for a "talkative" suspect (02/11)
    taire = ['se_taire'] if perso['courage'] >= 0.5 or p < 0.3 else []
    if e.get('suspect'):
        if e.get('rendu') or e.get('obeit'):
            return (['dire_vrai', 'mentir'] + taire) if demande == 'info' else []
        if demande == 'info':   # an armed suspect still standing does not chat
            return ['se_taire'] + (['mentir'] if perso['ruse'] >= 0.5 else [])
        armee = bool(e.get('arme'))
        if p >= s:
            choix = ['se_rendre', 'hesiter'] + (['faux_rendre'] if armee and perso['ruse'] >= 0.45 else [])
        elif p >= s - 0.15:
            choix = ['hesiter', 'resister'] + (['se_rendre'] if perso['panique'] >= 0.6 else []) \
                + (['faux_rendre'] if armee and perso['ruse'] >= 0.45 else []) + (['fuir'] if perso['panique'] >= 0.4 else [])
        else:
            choix = ['resister', 'hesiter'] + (['fuir'] if perso['panique'] >= 0.4 or perso['agressivite'] < 0.4 else []) \
                + (['faux_rendre'] if armee and perso['ruse'] >= 0.6 else [])
        if not armee:
            choix = [c for c in choix if c != 'resister'] or ['hesiter']
        return choix
    # civilians: never shoot, never lie on purpose (they can be wrong: the code may blur what they say)
    if demande == 'info':
        return ['dire_vrai'] + taire
    if e.get('rendu') or e.get('obeit'):
        return ['venir'] if demande == 'venir' else []
    s_civ = s - 0.2
    choix = ['obeir'] if p >= s_civ or perso['type'] != 'tetu' else []
    if demande == 'venir' and choix:
        choix.append('venir')
    choix += ['paniquer'] if perso['panique'] >= 0.4 else []
    choix += ['se_figer'] if perso['type'] in ('choque', 'panique') else []
    choix += ['refuser'] if perso['type'] == 'tetu' or p < s_civ - 0.15 else []
    choix += ['fuir'] if perso['panique'] >= 0.7 and p < s_civ else []
    return choix or ['obeir']


def decrire(e, perso, p, d, echanges, analyse, etat, maintenant=None):
    """One person, in French, for the model."""
    n = sum(1 for x in police(etat) if math.dist(x, e['pos']) <= PROCHE_M)
    etat_mots = [f"santé {100 * e['vie'] / echanges.vie_max(e):.0f} %",
                 'armé' if e.get('arme') else 'sans arme',
                 f"{n} policier{'s' if n > 1 else ''} à moins de 10 m" if n else 'aucun policier tout près',
                 'un policier le met en joue' if d.get('vise') else '',
                 'très stressé' if (e.get('stress') or 0) >= 1 else 'stressé' if (e.get('stress') or 0) >= 0.4 else 'pas encore stressé',
                 'des complices tombés à côté de lui' if d.get('amis_tombes') else '',
                 'déjà rendu' if e.get('rendu') else '', 'tient un otage' if e.get('otage') else '']
    return (f"{'suspect' if e.get('suspect') else 'civil'}, {perso['mots']} ; " + ', '.join(m for m in etat_mots if m)
            + f" ; {echanges.resume(e['nom'], maintenant)} ; pression {'forte' if p >= 0.55 else 'moyenne' if p >= 0.3 else 'faible'}")


SYSTEME = """Tu joues des personnages du jeu Ready or Not (suspects et civils) face à un policier du SWAT qui vient de
parler. Pour chaque personnage tu choisis SA réaction, crédible et humaine, parmi celles proposées pour lui, selon
sa personnalité, son état, la pression qu'il subit et ce que le policier dit et comment.
Un suspect lâche, blessé ou encerclé cède plus vite ; un fanatique ou un fier résiste à la menace ; un rusé fait
semblant. Un civil obéit le plus souvent, mais un civil paniqué ou en état de choc peut ne pas comprendre.
Pour une question (« où sont les autres ? ») : dire_vrai, mentir ou se_taire selon sa personnalité.
Le policier peut parler français ou anglais : juge ce qu'il dit, dans l'une ou l'autre langue.
Réponds seulement en JSON."""


def reagir(modele, texte, analyse, personnes, timeout=30):
    """personnes: [(id, description, options)]. One model call for all. Returns ({id: reaction}, seconds, error)."""
    if not personnes:
        return {}, 0.0, None
    props = {i: {'type': 'string', 'enum': opts} for i, _, opts in personnes}
    schema = {'type': 'object', 'properties': props, 'required': list(props)}
    dit = f"Le policier dit : « {texte} » ({MOTS_DEMANDE[analyse['demande']]}" \
        + (', ' + ', '.join(MOTS_TON[t] for t in analyse['tons']) if analyse['tons'] else '') + ').'
    lignes = [dit] + [f"Personnage {i} : {desc}. Réactions possibles : "
                      + ', '.join(f'{o} ({SENS[o]})' for o in opts) + '.' for i, desc, opts in personnes]
    body = json.dumps({
        'model': modele, 'stream': False, 'think': False, 'keep_alive': '30m', 'format': schema,
        'messages': [{'role': 'system', 'content': SYSTEME}, {'role': 'user', 'content': '\n'.join(lignes)}],
        'options': {'temperature': 0.7, 'num_ctx': 2048},   # some variety: two suspects should not act alike
    }).encode()
    t = time.perf_counter()
    try:
        req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rep = json.loads(json.load(r)['message']['content'])
        return (rep if isinstance(rep, dict) else {}), time.perf_counter() - t, None
    except Exception as e:   # malformed, timeout, server down: the guard turns it into no reaction
        return {}, time.perf_counter() - t, f'{type(e).__name__}: {e}'


def garde(reponse, personnes):
    """{id: reaction} kept only when it was offered to that person; anything else is dropped."""
    offert = {i: opts for i, _, opts in personnes}
    return {i: r for i, r in (reponse or {}).items() if i in offert and r in offert[i]}


# ---- what each reaction does in the game ----
# Stress in the game's own units: in game (02/11) it read 0 to 3 (a hostage 0.25, a scared civilian 1 to 3); the
# first version added 30, far off the scale.
# reaction -> (bridge command template or None, the person's own voice line); "fuir" and "venir" are moves, done
# by the caller (cerveau/adversaire.py walks him there)
EFFETS = {
    'se_rendre': ('rendre {nom}', '[BARK]Compliant'),
    'faux_rendre': ('faux_rendre {nom}', '[BARK]Compliant'),
    'hesiter': ('stress {nom} 0.5', '[BARK]Hesitating'),
    'resister': ('degainer {nom}', '[BARK]NonCompliant'),
    'fuir': (None, '[TELL]Fleeing'),
    'obeir': ('rendre {nom}', '[BARK]Compliant'),
    'venir': (None, '[BARK]Compliant'),
    'paniquer': ('stress {nom} 0.8', '[BARK]Hesitating'),
    'se_figer': ('stress {nom} 0.3', None),
    'refuser': (None, '[BARK]NonCompliant'),
    'dire_vrai': (None, None),
    'mentir': (None, None),
    'se_taire': (None, None),
}
# every warning heard lowers his morale in the game's own model too (the game decides surrenders with it)
MORAL_SOMMATION = -0.05
MORAL_MENACE = -0.1


def commandes(reaction, nom, analyse):
    """The bridge commands for one reaction (moves excluded), the morale drop of being warned, and his line."""
    cmd, ligne = EFFETS.get(reaction, (None, None))
    cmds = [cmd.format(nom=nom)] if cmd else []
    baisse = MORAL_MENACE if 'menace' in analyse.get('tons', []) else MORAL_SOMMATION
    if reaction not in ('se_rendre', 'obeir', 'dire_vrai', 'mentir', 'se_taire'):
        cmds.append(f'adv_moral {nom} {baisse:.2f}')
    return cmds, ligne


# ---- who hears a shout ----
CRI_VU_M = 20.0        # in sight of the speaker
CRI_MUR_M = 8.0        # through a wall or a door
FOULE_MAX = 6


def auditeurs(etat, cible, analyse, parleur_pos):
    """Who reacts: the person aimed at; for "tout le monde à terre", everyone in earshot (closer through walls)."""
    if not analyse['foule']:
        return [cible] if cible else []
    gens = []
    for e in etat.get('ia', []):
        if e['hors_combat'] or e['arrete']:
            continue
        d = math.dist(e['pos'], parleur_pos) if parleur_pos else e['distance_m']
        if d <= (CRI_VU_M if e.get('vu_chef') else CRI_MUR_M):
            gens.append((d, e))
    gens.sort(key=lambda x: x[0])
    tous = [e for _, e in gens[:FOULE_MAX]]
    if cible and cible not in tous:
        tous.insert(0, cible)
    return tous[:FOULE_MAX]


# ---- what he says when asked where the others are ----
def renseignement(e, reaction, etat, pieces, hasard):
    """(French sentence, the hint for the SWAT or None). The truth from the game's state (the suspects know where
    their friends are); a lie points at a room with nobody; a civilian may be wrong about the room."""
    import tacticien
    autres = [x for x in etat.get('ia', []) if x.get('suspect') and x['nom'] != e['nom'] and not x['hors_combat']
              and not x['arrete'] and not x['rendu'] and math.dist(x['pos'], e['pos']) <= 60]
    if reaction == 'se_taire':
        return 'il ne dit rien', None
    if reaction == 'mentir':
        vides = [r for r in pieces if not any(math.dist(r['pos'], x['pos']) <= 5 for x in autres)]
        if not vides or hasard.random() < 0.3:
            return 'il dit qu\'il est tout seul', {'nombre': 0, 'pos': None, 'piece': None, 'vrai': not autres}
        r = hasard.choice(vides)
        n = hasard.randint(1, 3)
        return f"il dit qu'il y en a {n} vers la pièce {r['nom']}", {'nombre': n, 'pos': r['pos'], 'piece': r['nom'], 'vrai': False}
    if not autres:
        return 'il dit qu\'il n\'y a personne d\'autre', {'nombre': 0, 'pos': None, 'piece': None, 'vrai': True}
    # the true answer: how many, and the room most of them are in
    ou = {}
    for x in autres:
        r = tacticien.piece_de(pieces, x['pos'])
        ou.setdefault(r['nom'] if r else None, []).append(x)
    piece, qui = max(ou.items(), key=lambda kv: len(kv[1]))
    pos = [sum(x['pos'][i] for x in qui) / len(qui) for i in range(3)]
    vrai = True
    if not e.get('suspect') and pieces and hasard.random() < 0.15:   # a civilian may mix up the rooms
        r = hasard.choice(pieces)
        piece, pos, vrai = r['nom'], r['pos'], False
    if piece is None:   # no room known there: he still knows which way they are (said out loud at step 7)
        return f"il dit qu'il y en a {len(autres)}", {'nombre': len(autres), 'pos': pos, 'piece': None, 'vrai': vrai}
    return (f"il dit qu'il y en a {len(autres)}, dont {len(qui)} vers la pièce {piece}",
            {'nombre': len(autres), 'pos': pos, 'piece': piece, 'vrai': vrai})
