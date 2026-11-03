"""Ready or Not voice AI: push-to-talk -> Whisper -> brain (local LLM) -> code guard -> WalPont bridge.

usage: ia_vocale.py                      live: talk on the game's voice chat (or hold the push-to-talk key), release
       ia_vocale.py --simulation s.tsv   test: everything live, but Wal's voice from banc/audio_grand recordings
       ia_vocale.py --sans-jeu           live, but nothing is sent to the game (voice test with the game closed)
       ia_vocale.py --wav f.wav [...]    test: feed recordings instead of the microphone
Settings in config.json next to this file. Every event is logged to logs/evenements.jsonl.
"""
import ctypes
import json
import math
import os
import pathlib
import queue
import random
import re
import sys
import threading
import time
import wave

ROOT = pathlib.Path(__file__).resolve().parent
VENV = pathlib.Path(sys.executable).parent.parent
for sub in ('cublas', 'cudnn'):
    d = VENV / 'Lib' / 'site-packages' / 'nvidia' / sub / 'bin'
    if d.exists():
        os.add_dll_directory(str(d))
        os.environ['PATH'] = str(d) + os.pathsep + os.environ['PATH']
sys.path.insert(0, str(ROOT / 'cerveau'))
sys.path.insert(0, str(ROOT / 'outils'))
sys.path.insert(0, str(ROOT / 'oreilles'))

import numpy as np  # noqa: E402

import cerveau  # noqa: E402
import pont  # noqa: E402

SANS_JEU = '--sans-jeu' in sys.argv
DEFAUT = {
    'touche_vk': 0x05,          # mouse side button (XBUTTON1); see Windows virtual-key codes
    'micro': None,              # {'nom', 'api'} set by outils/essai_micro.py; None = Windows default
    'whisper': 'large-v3-turbo',
    # 8-bit weights: 1.0 GB less video memory than float16, the same text on all 120 recordings (02/11)
    'whisper_calcul': 'int8_float16',
    # qwen3.5:4b without its image part (outils/modele_texte.py): 0.87 GB less, the same answers on the bench (02/11)
    'modele': 'qwen3.5:4b-texte',
    'vise_min': 0.9,            # cosine between the crosshair and the AI: 0.9 = within about 25 degrees
    'portee_m': 25.0,
    'cri_vise_min': 0.5,        # a shout with nobody aimed at reaches the closest AI within 60 degrees...
    'cri_portee_m': 12.0,       # ...and this close
    'chat_vocal': True,         # also listen while Wal talks on the game's voice chat (WalPont writes pont/parle.txt)
    'autres_joueurs': True,     # coop: hear the other players through the game's own sound while they talk
    'tactique': True,           # solo: the local model plays each SWAT teammate (cerveau/tacticien.py)
    # solo: the teammates speak, with the game's recorded lines (cerveau/annonces.py; PlayRawVO, heard in game 02/11)
    'annonces': True,
    'verifier_pieges': True,    # solo: before an entry, a look under a door nobody checked yet (the mirror)
    # the local model plays the suspects through the mission (cerveau/adversaire.py), the civilians by rules
    'adversaires': True,
    # the SWAT's scoring jobs (report, standalone traps): the game takes the orders, but no effect was proven in game
    # (02/11 at night: the game's AI already reports what it cuffs, a cuffed civilian was not reported) -> off
    'taches': False,
    # ...but the evidence (a dropped weapon) must be collected to finish the mission (Wal, 03/11): the team picks it
    # up on its own once the area is clear, and on "ramassez les armes"
    'preuves': True,
    # Wal (03/11): the suspects and civilians overhear the police's plans ("on va le contourner", "entrez avec une
    # flash"), adapt, and the suspects shout it on to the friends who can hear them
    'ecoute': True,
    # step 7: the model picks the recorded variant whose words answer Wal (else a random one, never twice in a row)
    'repliques_ia': True,
    'adv_exclure': [],          # suspects' actions never offered (e.g. ["surveiller"])
    # step 7: answers said out loud (voix/parole.py), by Piper: the project's own Python and voices by default
    'parole': True,
    'piper_python': sys.executable,   # the Python that has piper-tts (the project's own, put there by the installer)
    'piper_voix': str(ROOT / 'modeles' / 'piper' / 'fr_FR-siwis-medium.onnx'),
    # Wal (03/11): an English version. 'fr' (Whisper hears French), 'en' (English), 'auto' (Whisper tells each
    # sentence's language, and the people answer in it). The code's own rules understand both, whatever this says.
    'langue': 'fr',
    'piper_voix_en': str(ROOT / 'modeles' / 'piper' / 'en_US-lessac-medium.onnx'),   # English answers (download it)
}
ORDRE_CHEF = {'texte': None, 't': 0.0, 'membres': None}   # Wal's last order (membres: the teammates he named)
NOUVEL_ORDRE = threading.Event()         # an order naming teammates: the tactical AI thinks at once
APPEL = {'membres': None, 't': 0.0}      # "Alpha !" alone: the teammates Wal called, waiting for his order
# an order about a door or the team's gear: for the tactical AI (team order), not a plain team move
# (each of these in French and in English: Wal, 03/11, an English version)
ORDRE_D_EQUIPEMENT = re.compile(r"\b(entr\w*|portes?|flash\w*|grenades?|stinger|gaz|c2|fusil|b[ée]lier|enfonc\w*|"
                                r"d[ée]fonc\w*|taser|beanbag|pepperball|spray|bouclier|br[èe]che|crochet\w*|"
                                r"doors?|breach\w*|shotgun|ram|kick|gas|shield|lock ?pick\w*|pick the lock|go in|going in)\b",
                                re.IGNORECASE)
APPEL_S = 8.0                            # the next order to the team within this goes to them
GRENADE_DEMANDEE = re.compile(r"\b(flash\w*|grenades?|aveuglant\w*|stinger|gaz|ninebang|nine bang|lacrymo\w*|gas)\b", re.I)
# Wal's words for the team's scoring jobs (a door trap stays a door order: "porte" goes to the tactical AI first)
TACHES_VOIX = [
    ('pieges', re.compile(r"\bd[ée]samorc\w*\s+(?:le|les|ce|ces|la|cette)\s+(?:pi[èe]ges?|bombes?|explosifs?)\b"
                          r"|\b(?:disarm|defuse)\s+(?:the|that|this)\s+(?:traps?|bombs?|explosives?|devices?)\b", re.I)),
    ('rapporter', re.compile(r"\b(?:rapport(?:ez|e|er)|faites (?:le|un) rapport|signalez(?:-le)? au (?:qg|toc|central)"
                             r"|report (?:it|that|him|her|them|this|in|to toc)|call it in|tell toc)\b", re.I)),
    ('preuves', re.compile(r"\b(?:ramass\w*|r[ée]cup[ée]r\w*|s[ée]curis\w*)\s+(?:les|l'|la|le|cette|ces|son|ses)\s*"
                           r"(?:armes?|preuves?|flingues?|pistolets?|fusils?)\b"
                           r"|\b(?:pick up|collect|grab|secure|bag)\s+(?:the|that|this|his|her|their|those|these)\s+"
                           r"(?:weapons?|guns?|evidence|pistols?|rifles?)\b", re.I)),
]
MANOEUVRE = {'en_cours': None}           # a door entry under way (cerveau/manoeuvre.py), stepped by the tactical loop
PORTES_ECHOUEES = {}                     # door -> time an entry failed there: left out for a while
PORTES_VERIFIEES = set()                 # doors looked under (mirror) this mission
PORTE_ECARTEE_S = 180.0                  # in the big test (02/11) the model went back to the main door, where no stack-up ever starts
import style as _style  # noqa: E402  (cerveau/ is on the path)
STYLE = _style.Style()                   # how Wal plays, lethal or less-lethal, watched over the mission
DERNIERS = {}                            # teammate -> (last command, time, what he was told), shared by the loops
import memoire as _memoire  # noqa: E402
MEMOIRE = _memoire.Memoire()             # the team knows only what Wal or a teammate saw (and remembers who vanished)
import annonces as _annonces  # noqa: E402
ANNONCES = _annonces.Annonces()          # the teammates' spoken lines (the game's own recordings)
import personnalite as _personnalite  # noqa: E402
import interlocuteurs  # noqa: E402
import adversaire as _adversaire  # noqa: E402
import ecoute as _ecoute  # noqa: E402
import repliques as _repliques  # noqa: E402
sys.path.insert(0, str(ROOT / 'voix'))
import parole as _parole  # noqa: E402
PERSOS = _personnalite.Personnalites()   # who each suspect and civilian is, drawn once per mission
ECHANGES = interlocuteurs.Echanges()     # what each of them was told this mission
SAVOIR = _adversaire.SavoirPolice()      # what the suspects know of the police: seen, heard, told by a friend
MARCHEUR = _adversaire.Marcheur()        # their moves, renewed every second (in game they walk only so, 02/11)
RENS = _ecoute.Renseignements()          # the police's plans they overheard, or were shouted by a friend (03/11)
ADV_EN_COURS = {}                        # suspect -> (what he does, since when, his goal, action)
FAUX_FAITS = set()                       # suspects who already faked a surrender (once each)
CIVILS_DEJA = {}                         # civilian -> last time he reacted on his own
VOIX_GENS = {}                           # suspect or civilian -> last time he said a line
VOIX_GENS_S = 3.0


def annoncer(etat, ligne, membre=None, prioritaire=False, equipe_code=None):
    """A teammate says a line of the game (rate-limited, except an answer to Wal's order: prioritaire); nothing is
    sent in --sans-jeu mode, nothing at all when lines are off."""
    if not CONFIG.get('annonces', True):
        return None
    cmd = ANNONCES.dire(etat, ligne, membre, prioritaire=prioritaire, equipe_code=equipe_code)
    if cmd:
        r = f'non envoyé (sans jeu) : {cmd}' if SANS_JEU else pont.send(cmd.split())
        log({'type': 'annonce', 'commande': cmd, 'pont': r})
        equipe_entendue(cmd.split()[1], ligne)
    return cmd


def equipe_entendue(membre, ligne, maintenant=None):
    """A teammate's line that gives a plan away ("stacking up", "flashbang out") is heard by the suspects in earshot
    of him, as Wal's own words are (Wal, 03/11). From the full state: the team's own view (MEMOIRE) would leave out
    the suspects it has not seen, who hear all the same."""
    plans = _ecoute.plans_de_ligne(ligne)
    if not CONFIG['ecoute'] or not plans:
        return None
    try:
        etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    s = next((x for x in etat.get('swat', []) if x['nom'] == membre and 'pos' in x), None)
    ent = RENS.entendre(etat, '', s['pos'], membre, maintenant, plans=plans) if s else None
    if ent and ent['par']:
        log({'type': 'ecoute', 'source': 'equipe', 'membre': membre, 'ligne': ligne, **ent})
    return ent


CRIS_ENTENDUS = {}       # suspect -> when his last shout became a hint for the team
CIVILS_INFORMES = set()  # civilians who already called out to the police on their own this mission


def cris_entendus(etat, noms, maintenant=None):
    """The suspects who just shouted (noms): the ones the player or a teammate hears without seeing them become a
    hint for the team, where they shouted from (Wal, 03/11: the other way round from the suspects overhearing the
    police). Returns the names heard."""
    import tacticien
    t = time.time() if maintenant is None else maintenant
    if not CONFIG['ecoute']:
        return []
    par_nom = {e['nom']: e for e in etat.get('ia', [])}
    entendus = []
    for n in noms:
        e = par_nom.get(n)
        if not e or not e.get('suspect') or t - CRIS_ENTENDUS.get(n, -1e9) < _ecoute.CRI_REDIT_S \
                or not _ecoute.police_entend(etat, e):
            continue
        CRIS_ENTENDUS[n] = t
        r = tacticien.piece_de(etat.get('pieces', []), e['pos'])
        MEMOIRE.signaler({'nombre': 1, 'pos': list(e['pos']), 'piece': r['nom'] if r else None, 'vrai': True}, 'cri', t)
        entendus.append(n)
    return entendus


def infos_civils(etat, parleur_pos, maintenant=None):
    """A civilian who hears the police close by may call out where the suspects are, on his own, once (Wal, 03/11):
    the closest one of a willing kind (ecoute.INFORMATEURS), not held hostage. The truth from the game's state (a
    civilian may mix up the room, as when questioned); a hint for the team, said out loud. Returns the event, or None."""
    import tacticien
    t = time.time() if maintenant is None else maintenant
    if not CONFIG['ecoute']:
        return None
    niveau = tacticien.niveau(etat.get('difficulte'))[0]
    hasard = random.Random()
    for e, _ in sorted(_ecoute.qui_entend(etat, parleur_pos), key=lambda x: x[1]):
        if e.get('suspect') or e.get('otage') or e['nom'] in CIVILS_INFORMES:
            continue
        perso = PERSOS.de(e, etat.get('carte'), niveau)
        if perso['type'] not in _ecoute.INFORMATEURS:
            continue
        phrase, indice = interlocuteurs.renseignement(e, 'dire_vrai', etat, etat.get('pieces', []), hasard)
        if not indice or not indice['nombre']:
            continue
        CIVILS_INFORMES.add(e['nom'])
        MEMOIRE.signaler(indice, 'civil', t)
        ev = {'type': 'civil_informe', 'nom': e['nom'], 'dit': phrase, 'vrai': indice['vrai']}
        moi = next((j for j in etat.get('joueurs', []) if j.get('moi')), None)
        langue = langue_reponse()
        parle = _parole.phrase('dire_vrai', indice, e, moi, hasard, langue)
        if parle and CONFIG['parole']:
            ev['parle'] = parle
            dire_a_voix_haute(e, parle, moi, langue)
        return ev
    return None
VERROU_ORDRES = threading.Lock()
VERROU = threading.Lock()
VOIX_EN_COURS = threading.Event()        # a sentence is being decided: the tactical AI waits (voice first)
VERROU_ADV = threading.Lock()            # what the suspects know, read and written by two loops
ORDRE_CONTEXTE_S = 120                   # how long Wal's last order stays the team's intent
PAROLE_MAX_S = 20               # a "1" older than this is stale (game closed or crashed mid-sentence)
AVANT_S, APRES_S = 0.3, 0.6     # another player's voice reaches us a little after his push-to-talk signal
# Whisper hint: on Wal's first live test it heard "Pousse ton arme" and "personne ne mange"
VOCABULAIRE = ("Police ! LSPD ! Pose ton arme ! Lâche ton arme ! Personne ne bouge ! À terre ! Mains en l'air ! "
               "Suivez-moi. Tenez la position. Fouillez la pièce. Menottez-le.")
VOCABULAIRE_EN = ("Police! LSPD! Drop your weapon! Drop the gun! Nobody move! Get down! Hands up! "
                  "Follow me. Hold position. Search the room. Cuff him. Flashbang.")
LANGUE_DETECTEE = {'l': 'fr'}   # the language Whisper heard last ('auto' setting): the people answer in it
CONFIG = {**DEFAUT, **(json.loads((ROOT / 'config.json').read_text(encoding='utf-8')) if (ROOT / 'config.json').exists() else {})}
# a test may switch settings for one run without touching config.json (e.g. RON_CONFIG='{"adversaires": false}')
CONFIG.update(json.loads(os.environ.get('RON_CONFIG') or '{}'))
LOG = ROOT / 'logs' / 'evenements.jsonl'


def log(ev):
    LOG.parent.mkdir(exist_ok=True)
    ev['heure'] = time.strftime('%H:%M:%S')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    if ev['type'] == 'phrase':
        qui = f", {ev['vise']} {'visé' if ev['cible_visee'] else 'devant'}" if ev['cible'] else ''
        de = f" ({ev['joueur']})" if ev.get('joueur') else ''
        print(f"\nEntendu{de} : « {ev['texte']} »  ({ev['stt_ms']} ms{qui})\n  -> {ev['action']}"
              f"  ({'règle' if ev['source'] == 'regle' else 'IA'} : {ev['brut'] and ev['brut'].get('action')},"
              f" {ev['llm_ms']} ms, garde : {ev['garde']})"
              + (''.join(f"\n  -> {r['nom'][-6:]} ({r['type']}, pression {r['pression']:.2f}) : {r['reaction']}"
                         + (f" ({r['dit']})" if r.get('dit') else '') for r in ev['reactions']) if ev.get('reactions') else '')
              + (f"\n  -> jeu : {ev['pont']}" if 'pont' in ev else '')
              + (f"\n  ERREUR : {ev['erreur']}" if 'erreur' in ev else ''), flush=True)
    elif ev['type'] == 'parole':
        print(f"  {ev['nom'][-6:]} dit : « {ev['texte']} »"
              + (f" ({ev['ms']} ms)" if ev.get('ms') is not None else f" (pas de voix : {ev.get('erreur')})"), flush=True)
    elif ev['type'] == 'replique' and ev.get('texte'):
        print(f"  {ev['nom'][-6:]} : « {ev['texte']} »{' (choisie par l IA)' if ev.get('choisie') else ''}", flush=True)
    else:
        print(json.dumps(ev, ensure_ascii=False), flush=True)


def lire_wav(path):
    with wave.open(str(path), 'rb') as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def vue_de(etat, joueur):
    """The AI list as seen by another player: distance and aim recomputed from his position and his aim.
    None for Wal himself (the bridge already computed it from his view) or a player with no body (dead, spectating)."""
    j = next((j for j in etat.get('joueurs', []) if j['nom'] == joueur and not j['moi']), None)
    if not j or 'pos' not in j:
        return None
    lacet, tangage = math.radians(j['lacet']), math.radians(j['tangage'])
    regard = np.array([math.cos(tangage) * math.cos(lacet), math.cos(tangage) * math.sin(lacet), math.sin(tangage)])
    ici = np.array(j['pos'])
    vue = []
    for e in etat.get('ia', []):
        if 'pos' not in e:
            continue
        d = np.array(e['pos']) - ici
        n = float(np.linalg.norm(d))
        vue.append({**e, 'distance_m': round(n, 1), 'vise': round(float(regard @ d) / n, 2) if n > 0.01 else 1.0})
    return vue


def equipiers_ia(etat):
    """True when SWAT AI teammates are there to take orders (solo). In coop there are none: only players."""
    return any(not s['hors_combat'] for s in etat.get('swat', []))


def cible_visee(etat):
    """The AI the player is aiming at (closest to the crosshair, in range, still active), or None."""
    best = None
    for e in etat.get('ia', []):
        if e['hors_combat'] or e['arrete'] or e['distance_m'] > CONFIG['portee_m'] or e['vise'] < CONFIG['vise_min']:
            continue
        if best is None or e['vise'] > best['vise']:
            best = e
    return best


def cible_criee(etat):
    """Who a shouted order reaches: the aimed AI, else the closest active one roughly in front. (AI, aimed?)"""
    best = cible_visee(etat)
    if best:
        return best, True
    proches = [e for e in etat.get('ia', []) if not (e['hors_combat'] or e['arrete'] or e['rendu'])
               and e['distance_m'] <= CONFIG['cri_portee_m'] and e['vise'] >= CONFIG['cri_vise_min']]
    return min(proches, key=lambda e: e['distance_m'], default=None), False


MENOTTER_M = 10.0


def a_menotter(etat, cible):
    """Who "Menottez-le" is for: the person the player aims at or faces, else the closest one within MENOTTER_M who
    surrendered or complies and is not cuffed yet (03/11, big test: nobody under the crosshair, the bridge found
    "aucune cible" and nothing happened). None when there is nobody to cuff."""
    if cible and not (cible.get('arrete') or cible.get('hors_combat')):
        return cible
    prets = [e for e in etat.get('ia', []) if (e.get('rendu') or e.get('obeit')) and not e.get('arrete')
             and not e.get('hors_combat') and e.get('distance_m', 1e9) <= MENOTTER_M]
    return min(prets, key=lambda e: e['distance_m'], default=None)


def ecrire_wav(path, audio):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def envoyer(cmd):
    """One bridge command (nothing is sent in --sans-jeu mode)."""
    return f'1|ok|non envoyé (sans jeu) : {cmd}' if SANS_JEU else pont.send(cmd.split())


def peut_parler(nom, ligne, maintenant=None):
    """One line per person per VOIX_GENS_S at most (and none when the lines are switched off). Books the slot."""
    if not ligne or not CONFIG.get('annonces', True):
        return False
    t = time.time() if maintenant is None else maintenant
    if t - VOIX_GENS.get(nom, -1e9) < VOIX_GENS_S:
        return False
    VOIX_GENS[nom] = t
    return True


def dire_variante(nom, voix, cle, texte, choisie=False, ms=None):
    """Play one exact recorded variant ("[BARK]Compliant_2") and show what it says."""
    r = envoyer(f'adv_parler {nom} {cle}')
    log({'type': 'replique', 'nom': nom, 'voix': voix, 'cle': cle, 'texte': texte, 'choisie': choisie, 'ms': ms,
         'pont': r})
    return r


def faire_dire(nom, ligne, maintenant=None, voix=None):
    """A suspect or a civilian says a line of his own recorded voice: a variant of that kind, never the same twice in
    a row (step 7: the exact variant is played, and its subtitle shown)."""
    if not peut_parler(nom, ligne, maintenant):
        return None
    cle, texte = _repliques.au_hasard(nom, voix, ligne) if voix else (ligne, None)
    return dire_variante(nom, voix, cle, texte)


PAROLE = {'moteur': None}   # voix/parole.Parole, started at the first answer (its worker loads Piper)


def langue_reponse():
    """The language the people answer in: the setting, or with 'auto' the one Whisper heard last."""
    return CONFIG['langue'] if CONFIG['langue'] in ('fr', 'en') else LANGUE_DETECTEE['l']


def dire_a_voix_haute(e, texte, joueur, langue='fr'):
    """A synthesised answer in his own pitch, from his side of Wal's view (voix/parole.py), by the French voice or
    the English one (langue 'en'; nothing said, and logged, while that voice is not on the PC)."""
    cle, modele = ('moteur_en', CONFIG['piper_voix_en']) if langue == 'en' else ('moteur', CONFIG['piper_voix'])
    nom = e['nom']
    if langue == 'en' and not pathlib.Path(modele).exists():
        log({'type': 'parole', 'nom': nom, 'texte': texte, 'ms': None, 'erreur': 'voix anglaise absente (piper_voix_en)'})
        return
    if PAROLE.get(cle) is None:
        PAROLE[cle] = _parole.Parole(CONFIG['piper_python'], modele)

    def rapport(ms, err):
        log({'type': 'parole', 'nom': nom, 'texte': texte, 'ms': ms, **({'erreur': err} if err else {})})
    PAROLE[cle].dire(texte, e.get('voix'), e.get('femme'), e.get('pos'), joueur, rapport)


# what a reacting person does, for the model choosing his words
FAIT_MOTS = {'se_rendre': 'se rend', 'obeir': 'obéit', 'faux_rendre': 'fait semblant de se rendre', 'hesiter': 'hésite',
             'resister': 'refuse et garde son arme', 'fuir': 's\'enfuit', 'paniquer': 'panique', 'venir': 'vient vers lui',
             'refuser': 'refuse'}


def repondre(texte, a_dire):
    """Everyone who reacted says a line: the model picks, for each, the variant whose words answer Wal's sentence
    (one call for all); a random one when it cannot. a_dire = [(person, kind of line, reaction)]."""
    demandes = [(str(i + 1), FAIT_MOTS.get(r, r), _repliques.offertes(e['nom'], e.get('voix'), ligne))
                for i, (e, ligne, r) in enumerate(a_dire)]
    choix, sec, err = _repliques.choisir(CONFIG['modele'], texte, demandes) if CONFIG['repliques_ia'] else ({}, 0.0, None)
    if err:
        log({'type': 'erreur', 'source': 'repliques', 'erreur': err})
    for (k, _, _), (e, ligne, _) in zip(demandes, a_dire):
        cle = choix.get(k)
        if cle:
            _repliques.retenir(e['nom'], cle)
            dire_variante(e['nom'], e.get('voix'), cle, _repliques.texte_de(e.get('voix'), cle), True, round(sec * 1000))
        else:
            cle, t = _repliques.au_hasard(e['nom'], e.get('voix'), ligne) if e.get('voix') else (ligne, None)
            dire_variante(e['nom'], e.get('voix'), cle, t)


def position_de(etat, joueur):
    """Where the speaker stands: Wal (joueur None) or another player by name."""
    j = next((j for j in etat.get('joueurs', []) if 'pos' in j and (j['moi'] if joueur is None else j['nom'] == joueur)), None)
    return j['pos'] if j else None


def point_de_fuite(e, depuis, pieces):
    """Where someone running from the speaker goes: the neighbouring room farthest from him."""
    import tacticien
    sa = tacticien.piece_de(pieces, e['pos'])
    voisines = [r for r in pieces if sa and r['nom'] in sa.get('voisines', [])]
    if not voisines or depuis is None:
        return None
    r = max(voisines, key=lambda r: math.dist(r['pos'], depuis))
    return r['pos'] if math.dist(r['pos'], depuis) > math.dist(e['pos'], depuis) else None


def reactions_des_gens(texte, analyse, etat, cible, parleur_pos, maintenant=None):
    """Everyone a sentence reaches (the one aimed at; everyone in earshot for "tout le monde à terre") reacts:
    personality, pressure and what the code allows -> one model call -> guard -> the game. Returns (for the log,
    seconds, error)."""
    import tacticien
    t = time.time() if maintenant is None else maintenant
    ECHANGES.observer(etat, t)
    niveau = tacticien.niveau(etat.get('difficulte'))[0]
    letal = STYLE.mode != _style.NON_LETAL
    personnes, gens = [], {}
    for i, e in enumerate(interlocuteurs.auditeurs(etat, cible, analyse, parleur_pos)):
        perso = PERSOS.de(e, etat.get('carte'), niveau)
        ECHANGES.noter(e['nom'], analyse, t)
        p, d = interlocuteurs.pression(e, etat, perso, ECHANGES, analyse, letal, t)
        opts = interlocuteurs.options(e, perso, p, niveau, analyse['demande'])
        k = str(i + 1)
        gens[k] = (e, perso, p, opts)
        if opts:
            personnes.append((k, interlocuteurs.decrire(e, perso, p, d, ECHANGES, analyse, etat, t), opts))
    brut, sec, err = interlocuteurs.reagir(CONFIG['modele'], texte, analyse, personnes)
    gardees = interlocuteurs.garde(brut, personnes)
    hasard = random.Random()
    sorties, a_dire = [], []
    for k, (e, perso, p, opts) in gens.items():
        r = gardees.get(k)
        sortie = {'nom': e['nom'], 'type': perso['type'], 'pression': round(p, 2), 'offertes': opts, 'reaction': r}
        sorties.append(sortie)
        if r is None:
            continue
        cmds, ligne = interlocuteurs.commandes(r, e['nom'], analyse)
        if e.get('otage'):
            # holding a hostage, he ignores Surrender (in game, 02/11): Wal's words reach him the game's own way, as
            # a yell his hostage activity hears (it let the hostage go once in two tries: the game decides)
            cmds = [f"adv_crier {e['nom']}"] + [c for c in cmds if not c.startswith(('rendre ', 'faux_rendre '))]
        sortie['pont'] = [envoyer(c) for c in cmds]
        if r == 'fuir':
            but = point_de_fuite(e, parleur_pos, etat.get('pieces', []))
            if but:
                MARCHEUR.aller(e['nom'], but, raison='fuir', maintenant=t)
        elif r == 'venir' and parleur_pos:   # stops 1.5 m in front of the speaker
            dx, dy = e['pos'][0] - parleur_pos[0], e['pos'][1] - parleur_pos[1]
            n = math.hypot(dx, dy) or 1.0
            MARCHEUR.aller(e['nom'], [parleur_pos[0] + 1.5 * dx / n, parleur_pos[1] + 1.5 * dy / n, e['pos'][2]],
                           raison='venir', maintenant=t)
        elif r in ('se_rendre', 'obeir', 'faux_rendre'):
            MARCHEUR.arreter(e['nom'])
        if r in ('dire_vrai', 'mentir', 'se_taire'):
            phrase, indice = interlocuteurs.renseignement(e, r, etat, etat.get('pieces', []), hasard)
            sortie['dit'] = phrase
            if indice is not None:   # the SWAT hears it too, as a hint to check, never as a sighting
                MEMOIRE.signaler(indice, 'suspect' if e.get('suspect') else 'civil', t)
                sortie['vrai'] = indice['vrai']
            # step 7: he says it out loud, words built by the code from the same facts (no fact added)
            moi = next((j for j in etat.get('joueurs', []) if j.get('moi')), None)
            langue = langue_reponse()   # in the language he was spoken to
            parle = _parole.phrase(r, indice, e, moi, hasard, langue)
            if parle and CONFIG['parole']:
                sortie['parle'] = parle
                dire_a_voix_haute(e, parle, moi, langue)
        if peut_parler(e['nom'], ligne, t):
            sortie['voix'] = ligne
            a_dire.append((e, ligne, r))
    if a_dire:   # their words come a moment after the reaction, which is sent at once
        if SANS_JEU:
            repondre(texte, a_dire)
        else:
            threading.Thread(target=repondre, args=(texte, a_dire), name='repliques', daemon=True).start()
    return sorties, sec, err


def tour_adverse(etat, maintenant=None, urgent=False):
    """One turn of the suspects' AI: what they know -> picture -> model -> guard -> the game; and the civilians'
    own reactions (rules). Returns the logged event, or None when nothing was decided."""
    import tacticien
    t = time.time() if maintenant is None else maintenant
    niveau = tacticien.niveau(etat.get('difficulte'))[0]
    niv = _adversaire.NIVEAUX[niveau]
    with VERROU_ADV:   # shared with the overhearing loop (tour_ecoute); not held while the model thinks
        SAVOIR.mettre_a_jour(etat, niv, t)
        ECHANGES.observer(etat, t)
        for e in etat.get('ia', []):
            PERSOS.de(e, etat.get('carte'), niveau)
        persos = PERSOS.connues
        ev = {'type': 'adverse', 'niveau': niveau, 'suspects': [], 'civils': []}
        for c, quoi, d in _adversaire.civils(etat, persos, MARCHEUR, CIVILS_DEJA, t):
            if quoi == 'cacher':
                MARCHEUR.aller(c['nom'], d['but'], raison='cacher', maintenant=t)
                faire_dire(c['nom'], '[TELL]Fleeing', t, c.get('voix'))
            elif quoi == 'se_figer':
                envoyer(f"stress {c['nom']} 0.3")
            ev['civils'].append({'nom': c['nom'], 'quoi': quoi})
        texte, ids = _adversaire.situation(etat, SAVOIR, persos, niv, ADV_EN_COURS, t, RENS if CONFIG['ecoute'] else None)
        if ids is None:
            return ev if ev['civils'] else None
        menus = _adversaire.sans_deplacement(_adversaire.choix(ids, persos, niv, etat, ECHANGES, FAUX_FAITS, t), ids, MARCHEUR, t)
        if CONFIG['adv_exclure']:   # actions taken out of the suspects' choices (a test, or one that misbehaves)
            menus = {k: [o for o in m if o.partition(':')[0] not in CONFIG['adv_exclure']] or ['garder'] for k, m in menus.items()}
        if all(m == ['garder'] for m in menus.values()):   # nothing they could do but go on: no question
            return ev if ev['civils'] else None
    brut, sec, err = _adversaire.demander(CONFIG['modele'], texte, ids, menus)
    ev['ms'] = round(sec * 1000)
    if err:
        ev['erreur'] = err
    for o in brut or []:
        sus = ids['S'].get(o.get('suspect'))
        a, d = _adversaire.garde(o, ids, persos, niv, etat, ECHANGES, FAUX_FAITS, t)
        fait = {'suspect': o.get('suspect'), 'nom': sus['nom'] if sus else None, 'demande': o.get('action'),
                'cible': o.get('cible'), 'action': a}
        ev['suspects'].append(fait)
        if a == 'garder' or sus is None:
            fait['raison'] = d.get('raison')
            continue
        avant = ADV_EN_COURS.get(sus['nom'])
        if avant and avant[3] != a and t - avant[1] < _adversaire.TENUE_S and not urgent:
            fait['raison'] = 'garde son ordre precedent'   # no flip-flop between two plans
            continue
        if avant and avant[3] == a and avant[2] == d.get('but') and t - avant[1] < 20:
            continue   # the same order, already under way
        if a in _adversaire.DEPLACEMENTS and sus['nom'] in MARCHEUR.buts:
            # on his way: in game (02/11) five goals in 22 s, and he never got anywhere
            fait['raison'] = 'deja en route'
            continue
        fait['pont'] = _adversaire.executer(a, d, sus, MARCHEUR, envoyer, t)
        if a in _adversaire.LIGNES and cris_entendus(etat, [sus['nom']], t):   # his shout heard by the police
            fait['police_entend'] = True
        if d.get('manoeuvre'):
            fait['manoeuvre'] = d['manoeuvre']
        if d.get('but'):   # for the test's check that he really went there
            fait.update(but=[round(v, 2) for v in d['but']], t=round(t, 1))
        if a == 'faux_rendre':
            FAUX_FAITS.add(sus['nom'])
        ADV_EN_COURS[sus['nom']] = (_adversaire.MOTS[a], t, d.get('but'), a)
    return ev


ECOUTE_CIVIL_S = 0.5   # a civilian's reaction to what he overheard (no thinking: he ducks or runs)


def tour_ecoute(etat, maintenant=None):
    """What the suspects and civilians overheard of the police's plans (Wal, 03/11), without the model: the suspects
    shout it on to the friends who can hear them, where the police spoke from becomes known, then each one reacts
    once his reaction time has passed. Returns the logged event, or None when nothing happened."""
    import tacticien
    t = time.time() if maintenant is None else maintenant
    if not CONFIG['ecoute'] or not RENS.en_attente(t):
        return None
    niveau = tacticien.niveau(etat.get('difficulte'))[0]
    niv = _adversaire.NIVEAUX[niveau]
    ev = {'type': 'ecoute_gens', 'relais': [], 'suspects': [], 'civils': []}
    par_nom = {e['nom']: e for e in etat.get('ia', [])}
    for nom, quoi, amis in RENS.relais(etat, max(niv['partage_m'], interlocuteurs.CRI_MUR_M), t):
        crieur = par_nom[nom]
        voix = crieur.get('voix')
        ligne = _ecoute.ligne_relais(quoi, set(_repliques.catalogue(voix)) if voix else set())
        ev['relais'].append({'nom': nom, 'quoi': quoi, 'amis': amis, 'ligne': ligne,
                             'pont': [faire_dire(nom, ligne, t, voix)]
                             + [envoyer(f'adv_eveil {a} 2') for a in amis if par_nom[a].get('eveil', 0) < 2]})
    for nom, police, pos, ti, comment in RENS.connus(t):   # they know where the voice came from
        SAVOIR.voix(nom, police, pos, ti, comment)
    for e in etat.get('ia', []):
        PERSOS.de(e, etat.get('carte'), niveau)
    persos = PERSOS.connues
    for sus, quoi, a, d in _adversaire.reactions_ecoute(etat, SAVOIR, RENS, persos, niv, ADV_EN_COURS, ECHANGES,
                                                         FAUX_FAITS, t, MARCHEUR):
        fait = {'nom': sus['nom'], 'quoi': quoi, 'action': a, 'pont': _adversaire.executer(a, d, sus, MARCHEUR, envoyer, t)}
        if d.get('but'):
            fait['but'] = [round(v, 2) for v in d['but']]
        ADV_EN_COURS[sus['nom']] = (_adversaire.MOTS[a], t, d.get('but'), a)
        ev['suspects'].append(fait)
    civils = sorted(e['nom'] for e in etat.get('ia', []) if e.get('civil'))
    for nom, quoi, _ in RENS.a_reagir(civils, ECOUTE_CIVIL_S, t):
        RENS.marquer_reagi(nom, quoi)
        c = par_nom[nom]
        r = _adversaire.reaction_civil(c, quoi, persos.get(nom), etat)
        if r is None or t - CIVILS_DEJA.get(nom, -1e9) < 3.0:
            continue
        CIVILS_DEJA[nom] = t
        if r[0] == 'cacher':
            MARCHEUR.aller(nom, r[1]['but'], raison='cacher', maintenant=t)
            faire_dire(nom, '[TELL]Fleeing', t, c.get('voix'))
        else:
            envoyer(f'stress {nom} 0.3')
        ev['civils'].append({'nom': nom, 'quoi': quoi, 'reaction': r[0]})
    # their shouts give them away in turn: the police hears where they are (a hint for the team, not a sighting)
    crieurs = [r['nom'] for r in ev['relais']] + [s['nom'] for s in ev['suspects'] if s['action'] in _adversaire.LIGNES]
    entendus = cris_entendus(etat, crieurs, t)
    if entendus:
        ev['police_entend'] = entendus
    return ev if ev['relais'] or ev['suspects'] or ev['civils'] else None


def boucle_ecoute():
    """What they overheard, ten times a second in its own loop: in game (03/11, house map) inside the suspects' loop it
    waited behind their model call, and the reactions came 9 to 11 s after Wal's sentence instead of 1.5 s."""
    while True:
        time.sleep(0.1)
        try:
            if time.time() - pont.STATE.stat().st_mtime > 5:
                continue
            etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not etat.get('hote', True):   # coop client: the host's game decides the AI
            continue
        try:
            with VERROU_ADV:
                ev = tour_ecoute(etat)
            if ev:
                log(ev)
        except Exception as e:   # never stop the voice AI
            log({'type': 'erreur', 'source': 'ecoute', 'erreur': repr(e)})


def boucle_marcheur():
    """The suspects' and civilians' moves, renewed every second in their own loop: in game (02/11) renewing them
    only between two model calls left a suspect with one request, and he never moved."""
    while True:
        time.sleep(1.0)
        try:
            if time.time() - pont.STATE.stat().st_mtime > 5:
                continue
            etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        try:
            MARCHEUR.tour(etat, envoyer)
        except Exception as e:   # never stop the voice AI
            log({'type': 'erreur', 'source': 'marcheur', 'erreur': repr(e)})


def boucle_adverse():
    """The suspects' AI every few seconds (the difficulty sets how often), sooner when a suspect sees the police
    (after the level's reaction time); the walker renews their moves every second. Voice first."""
    import tacticien
    prochain, vue, carte, urgent = 0.0, None, None, False
    while True:
        time.sleep(0.1)
        if VOIX_EN_COURS.is_set():
            continue
        try:
            if time.time() - pont.STATE.stat().st_mtime > 5:
                continue
            etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not etat.get('hote', True):   # coop client: the host's game decides the AI
            continue
        if etat.get('carte') != carte:   # a new mission: what they knew of the last one goes
            carte = etat.get('carte')
            for memoire_de_mission in (ADV_EN_COURS, FAUX_FAITS, CIVILS_DEJA, VOIX_GENS, MARCHEUR.buts, CRIS_ENTENDUS,
                                       CIVILS_INFORMES):
                memoire_de_mission.clear()
            MARCHEUR.immobiles.clear()
        try:
            maintenant = time.time()
            niv = _adversaire.NIVEAUX[tacticien.niveau(etat.get('difficulte'))[0]]
            sig = frozenset((e['nom'], tuple(e.get('voit_police', []))) for e in _adversaire.actifs(etat))
            if vue is not None and sig != vue:
                prochain = min(prochain, maintenant + niv['reaction_s'])
                urgent = True
            vue = sig
            if maintenant < prochain:
                continue
            prochain = maintenant + niv['periode_s']
            ev = tour_adverse(etat, maintenant, urgent)
            urgent = False
            if ev and (ev['civils'] or 'ms' in ev):   # every turn the model was asked, even one that changed nothing
                log(ev)
        except Exception as e:   # the suspects' AI must never stop the voice AI
            log({'type': 'erreur', 'source': 'adversaire', 'erreur': repr(e)})


def traiter(audio, transcrire, audio_path=None, joueur=None):
    """One sentence, from Wal (joueur None) or from another player (his name): text -> action -> game."""
    t0 = time.perf_counter()
    texte = transcrire(audio)
    t_stt = time.perf_counter() - t0
    if not texte.strip():
        log({'type': 'vide', 'joueur': joueur, 'stt_ms': round(t_stt * 1000), 'audio': audio_path and audio_path.name})
        return
    try:
        etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
    except Exception:
        etat = {'ia': [], 'swat': []}
    if joueur is not None:
        vue = vue_de(etat, joueur)
        if vue is None:   # unknown or bodiless speaker: never judge his words from Wal's view
            log({'type': 'ignore', 'joueur': joueur, 'texte': texte, 'raison': 'joueur sans position'})
            return
        etat = {**etat, 'ia': vue}
    cible, visee = cible_criee(etat)
    if CONFIG['ecoute']:
        # whoever is in earshot hears what the police says, to anyone (Wal, 03/11): its plans are given away
        entendu = RENS.entendre(etat, texte, position_de(etat, joueur), 'joueur' if joueur is None else 'joueur:' + joueur)
        if entendu and entendu['par']:
            log({'type': 'ecoute', 'joueur': joueur, 'texte': texte, **entendu})
        informe = infos_civils(etat, position_de(etat, joueur))   # a civilian close by may call out to the police
        if informe:
            log(informe)
    vise = 'suspect' if cible and cible['suspect'] else 'civil' if cible and cible['civil'] else 'aucun'
    if vise == 'aucun':
        ctx = 'le joueur ne vise personne'
    elif visee:
        ctx = {'suspect': 'le joueur vise un suspect armé', 'civil': 'le joueur vise un civil'}[vise]
    else:
        ctx = f"un {'suspect armé' if vise == 'suspect' else 'civil'} est devant le joueur, à {cible['distance_m']:.0f} m"
    equipiers = equipiers_ia(etat)
    if not equipiers:
        ctx += " ; pas de coéquipiers IA (partie entre joueurs)"
    def pour_eux(noms, source):
        """The order goes to these named teammates, through the tactical AI (it thinks at once)."""
        with VERROU:
            ORDRE_CHEF.update(texte=texte, t=time.time(), membres=noms, equipement=False)
            APPEL.update(membres=None, t=0.0)
        NOUVEL_ORDRE.set()
        annoncer(etat, _annonces.ACCUSE, noms[0], prioritaire=True)   # the first one named answers "Roger", always
        log({'type': 'phrase', 'joueur': None, 'texte': texte, 'vise': vise, 'cible': cible['nom'] if cible else None,
             'cible_visee': visee, 'source': source, 'audio': audio_path and audio_path.name, 'brut': None,
             'action': 'ordre_nominatif', 'garde': 'pour ' + ', '.join(n[-4:] for n in noms),
             'stt_ms': round(t_stt * 1000), 'llm_ms': 0})

    if equipiers and joueur is None:
        import tacticien
        noms = tacticien.nommes(texte, etat)
        if noms and tacticien.simple_appel(texte, etat):
            # "Alpha !": he answers, and Wal's next order to the team (within APPEL_S) is for him
            with VERROU:
                APPEL.update(membres=noms, t=time.time())
            annoncer(etat, _annonces.APPEL, noms[0], prioritaire=True)   # he answers, waiting for the order
            log({'type': 'phrase', 'joueur': None, 'texte': texte, 'vise': vise, 'cible': None, 'cible_visee': False,
                 'source': 'noms', 'audio': audio_path and audio_path.name, 'brut': None, 'action': 'appel',
                 'garde': 'appel de ' + ', '.join(n[-4:] for n in noms), 'stt_ms': round(t_stt * 1000), 'llm_ms': 0})
            return
        if noms:   # "Alpha, couvre la porte ; Bravo, avec moi"
            pour_eux(noms, 'noms')
            return
        with VERROU:
            appele_tot = APPEL['membres'] if APPEL['membres'] and time.time() - APPEL['t'] < APPEL_S else None
        if appele_tot and ORDRE_D_EQUIPEMENT.search(texte):
            # "Alpha !" ... "couvre la porte": a pending call comes first (the named one may use the team's gear)
            pour_eux(appele_tot, 'appel')
            return
        if ORDRE_D_EQUIPEMENT.search(texte):
            # "entrez avec une flash", "enfoncez la porte": a door entry or the team's gear, which only the tactical
            # AI can order (in the big test, 02/11, this became a plain "move there" and the entry came much later)
            with VERROU:
                ORDRE_CHEF.update(texte=texte, t=time.time(), membres=None, equipement=True)
                APPEL.update(membres=None, t=0.0)
            NOUVEL_ORDRE.set()
            annoncer(etat, _annonces.ACCUSE, prioritaire=True)
            log({'type': 'phrase', 'joueur': None, 'texte': texte, 'vise': vise, 'cible': cible['nom'] if cible else None,
                 'cible_visee': visee, 'source': 'equipement', 'audio': audio_path and audio_path.name, 'brut': None,
                 'action': 'ordre_equipe', 'garde': 'pour le tacticien', 'stt_ms': round(t_stt * 1000), 'llm_ms': 0})
            return
        tache = next((q for q, motif in TACHES_VOIX if q in taches_actives() and motif.search(texte)), None)
        if tache:   # "rapportez", "ramassez les armes", "désamorcez le piège": the team's own job, at once
            r = envoyer(f'swat_tache {tache} rouge {TACHES_M}')
            if '|ECHEC|' not in r:
                annoncer(etat, _annonces.ACCUSE, prioritaire=True)
                if tache == 'preuves':   # the tactical AI leaves the team alone meanwhile
                    TACHES_ETAT['tenue'] = time.time() + TACHES_TENUE_S
            log({'type': 'phrase', 'joueur': None, 'texte': texte, 'vise': vise, 'cible': None, 'cible_visee': False,
                 'source': 'tache', 'audio': audio_path and audio_path.name, 'brut': None, 'action': 'swat_' + tache,
                 'garde': 'tache de l equipe', 'pont': r, 'stt_ms': round(t_stt * 1000), 'llm_ms': 0})
            return
    regle = cerveau.regle_directe(texte)
    if regle:
        brut, t_llm, err = regle, 0.0, None
    else:
        brut, t_llm, err = cerveau.appeler(CONFIG['modele'], texte, ctx)
    action, equipe, cmd, raison = cerveau.garde(regle or cerveau.corriger(texte, brut), vise)
    analyse = interlocuteurs.analyser(texte)
    if not cible and not regle and action.startswith('swat_') and interlocuteurs.ordre_a_la_personne(texte):
        # "Tout le monde à terre !" with nobody near (house map, 02/11): the model made it a SWAT "hold". An order
        # only a person can carry out never goes to the team (nor, in coop, vanishes as talk between players):
        # a shout reaches whoever hears it, else nothing
        action, cmd = ('ordre_foule', None) if analyse['foule'] else ('rien', None)
        raison = 'cri a tout le monde' if analyse['foule'] else 'ordre pour une personne, personne en face'
    if action.startswith('swat_') and not equipiers:
        # coop: the other SWAT officers are players; an order to them is just talk between players
        action, cmd, raison = 'rien', None, "pas d'equipiers IA"
    with VERROU:
        appele = APPEL['membres'] if APPEL['membres'] and time.time() - APPEL['t'] < APPEL_S else None
    if appele and joueur is None and action.startswith('swat_'):
        # Wal called a teammate a moment ago ("Alpha !" ... "couvre la porte"): this team order is for him.
        # An order to a suspect or a civilian ("lâche ton arme") stays one, and the call waits.
        vivants = {s['nom'] for s in etat.get('swat', []) if not s['hors_combat']}
        appele = [n for n in appele if n in vivants]
        if appele:
            pour_eux(appele, 'appel')
            return
    if cible and not regle and (interlocuteurs.est_question_info(texte) or interlocuteurs.ordre_a_la_personne(texte)):
        # "Où sont les autres ?" to the person in front of him: a question for him, not talk between players nor an
        # order to the team (in game, 02/11, the model made it "swat_suivre")
        action, raison = ('ordre_reddition' if cible['suspect'] else 'ordre_obeir'), 'pour la personne visee'
        cmd = None   # never the team order the model had made of it
    elif not cible and interlocuteurs.est_question_info(texte) and not regle and action.startswith('swat_'):
        action, cmd, raison = 'rien', None, 'question sans personne en face'   # 02/11: it became "suivez-moi"
    # "Tout le monde à terre !" with nobody in front: still a shout at everyone in earshot
    foule = analyse['foule'] and isinstance(brut, dict) and brut.get('action') in ('ordre_reddition', 'ordre_obeir')
    if foule and action == 'rien':
        action, raison = 'ordre_foule', 'cri a tout le monde'
    ev = {'type': 'phrase', 'joueur': joueur, 'texte': texte, 'vise': vise, 'cible': cible['nom'] if cible else None,
          'cible_visee': visee, 'source': 'regle' if regle else 'ia', 'audio': audio_path and audio_path.name,
          'brut': brut, 'action': action, 'garde': raison, 'stt_ms': round(t_stt * 1000), 'llm_ms': round(t_llm * 1000)}
    if err:
        ev['erreur'] = err
    if (action in ('ordre_reddition', 'ordre_obeir') and cible) or action == 'ordre_foule':
        sorties, t_rea, err2 = reactions_des_gens(texte, analyse, etat, cible, position_de(etat, joueur))
        ev.update({'demande': analyse['demande'], 'tons': analyse['tons'], 'reactions': sorties,
                   'reaction': sorties[0]['reaction'] if sorties else None, 'reaction_ms': round(t_rea * 1000)})
        if err2:
            ev['erreur_reaction'] = err2
    if action.startswith('swat_') and joueur is None:
        with VERROU:   # the tactical AI carries this order out, and may not override it for a while
            ORDRE_CHEF.update(texte=texte, t=time.time(), membres=None, equipement=False)
    if action == 'swat_menotter' and cmd and cmd.endswith(' vise'):
        qui = a_menotter(etat, cible)   # by name: not only the one under the crosshair
        if qui:
            cmd, ev['menotte'] = cmd[:-len('vise')] + qui['nom'], qui['nom']
    if cmd and SANS_JEU:
        ev['pont'] = f'non envoyé (sans jeu) : {cmd}'
    elif cmd:
        t1 = time.perf_counter()
        ev['pont'] = pont.send(cmd.split())
        ev['pont_ms'] = round((time.perf_counter() - t1) * 1000)
    if cmd and action.startswith('swat_') and '|ECHEC|' not in ev.get('pont', ''):
        # a teammate (of the team addressed) answers with what they do: "falling in", "holding", "moving"...
        annoncer(etat, _annonces.PAR_ORDRE.get(action, _annonces.ACCUSE), prioritaire=True,
                 equipe_code={'rouge': 1, 'bleu': 2}.get(equipe))
    ev['total_ms'] = round((time.perf_counter() - t0) * 1000)
    log(ev)


def transcrire_avec(model, audio):
    """Whisper invents text on silence and noise ("Sous-titrage ST' 501", "Merci.", and with the hint even
    "Fouillez la pièce." on a noise burst), which would be a real order. So: keep only what the voice
    detector calls speech, and drop segments Whisper itself thinks are not speech. The language: the 'langue'
    setting (with 'auto', Whisper tells it, and it is kept for the answers)."""
    langue, indice = whisper_langue()
    segs, info = model.transcribe(audio, language=langue, beam_size=1, condition_on_previous_text=False,
                                  initial_prompt=indice, vad_filter=True,
                                  vad_parameters={'min_silence_duration_ms': 300})
    texte = ' '.join(s.text.strip() for s in segs if s.no_speech_prob < 0.6)
    if texte and getattr(info, 'language', None) in ('fr', 'en'):
        LANGUE_DETECTEE['l'] = info.language
    return texte


def whisper_langue():
    """(the language Whisper is told, or None to let it tell; its hint of words) for the 'langue' setting."""
    langue = CONFIG.get('langue', 'fr')
    if langue == 'en':
        return 'en', VOCABULAIRE_EN
    if langue == 'auto':
        return None, VOCABULAIRE + ' ' + VOCABULAIRE_EN
    return 'fr', VOCABULAIRE


def charger_whisper(essais=3, attente_s=10):
    """Whisper on the GPU. With the video memory full for a moment (02/11: another app's model loaded while the game
    opened a map), CUDA fails with "out of memory" or "cublas64_12.dll ... cannot be loaded": try again a few times."""
    from faster_whisper import WhisperModel
    for essai in range(1, essais + 1):
        try:
            model = WhisperModel(CONFIG['whisper'], device='cuda', compute_type=CONFIG['whisper_calcul'])

            def transcrire(audio, model=model):
                return transcrire_avec(model, audio)
            # warm-up both: the voice filter skips Whisper on silence, so Whisper needs its own warm-up call
            transcrire(np.zeros(16000, dtype=np.float32))
            list(model.transcribe(np.zeros(16000, dtype=np.float32), language='fr', beam_size=1)[0])
            return transcrire
        except RuntimeError as e:
            # 02/11 at night also "mkl_malloc: failed to allocate memory": the PC's own memory full for a moment
            if essai == essais or not any(m in str(e) for m in ('out of memory', 'cublas', 'CUDA', 'allocate memory')):
                raise
            print(f"Mémoire vidéo pleine au chargement de Whisper ({e}) : nouvel essai dans {attente_s} s.", flush=True)
            time.sleep(attente_s)


def qui_parle(fichier=None, maintenant=None):
    """{player name: is it Wal} for every player the game says is talking on its voice chat right now.
    pont/parle.txt lines: "<1|0> <time> <moi|autre> <name>"; a "1" older than PAROLE_MAX_S is stale."""
    try:
        lignes = (fichier or pont.DIR / 'parle.txt').read_text(encoding='utf-8').splitlines()
    except OSError:   # no file yet, or being replaced: nobody
        return {}
    parlent = {}
    for ligne in lignes:
        champs = ligne.split(' ', 3)
        if len(champs) == 4 and champs[0] == '1' and champs[1].isdigit() and champs[2] in ('moi', 'autre') \
                and 0 <= (time.time() if maintenant is None else maintenant) - int(champs[1]) < PAROLE_MAX_S:
            parlent[champs[3]] = champs[2] == 'moi'
    return parlent


def chat_vocal_actif(fichier=None, maintenant=None):
    """True while the game says Wal himself is talking on its voice chat."""
    return any(qui_parle(fichier, maintenant).values())


def nom_wav(joueur=None):
    suffixe = '' if joueur is None else '_' + ''.join(c if c.isalnum() else '_' for c in joueur)[:30]
    return ROOT / 'logs' / 'audio' / f"{time.strftime('%Y%m%d_%H%M%S')}{suffixe}.wav"   # kept to replay later


def boucle_micro(file_phrases):
    """Wal's own voice: his microphone, while he holds the key or talks on the game's voice chat."""
    import sounddevice as sd
    import micro
    user32 = ctypes.windll.user32
    vk = CONFIG['touche_vk']
    entree = micro.trouver(CONFIG['micro'])

    def parle():
        return bool(user32.GetAsyncKeyState(vk) & 0x8000) or (CONFIG['chat_vocal'] and chat_vocal_actif())
    while True:
        if parle():
            morceaux = []
            with sd.InputStream(samplerate=16000, channels=1, dtype='float32', device=entree,
                                callback=lambda data, n, t, s: morceaux.append(data.copy())):
                while parle():
                    time.sleep(0.01)
            audio = np.concatenate(morceaux)[:, 0] if morceaux else np.zeros(0, dtype=np.float32)
            if len(audio) > 16000 * 0.3:
                file_phrases.put((audio, None))
        time.sleep(0.02)


def boucle_autres(file_phrases):
    """The other players (coop): their voice comes out of the game, so record the game's own sound (nothing else
    on the PC) between the moment the game says one of them talks and the moment he stops, plus a margin."""
    import son_jeu
    cap, essai = None, 0.0
    debuts = {}     # other player -> wall-clock time he started talking
    a_couper = []   # (player, start, end): cut once the end (his last words, still on their way) is reached
    while True:
        if cap is None or not cap.vivant():
            if cap is not None:
                log({'type': 'son_jeu', 'etat': 'perdu', 'erreur': repr(cap.erreur)})
                cap, debuts, a_couper = None, {}, []
            if time.time() - essai > 5:
                essai = time.time()
                pid = son_jeu.pid_du_jeu()
                if pid:
                    try:
                        cap = son_jeu.CaptureProcessus(pid)
                        cap.demarrer()
                        log({'type': 'son_jeu', 'etat': 'capte', 'pid': pid})
                    except Exception as e:
                        log({'type': 'son_jeu', 'etat': 'echec', 'pid': pid, 'erreur': repr(e)})
                        cap = None
            time.sleep(0.5)
            continue
        maintenant = time.time()
        autres = {n for n, moi in qui_parle(maintenant=maintenant).items() if not moi}
        for nom in autres - debuts.keys():
            debuts[nom] = maintenant
        for nom in list(debuts.keys() - autres):
            a_couper.append((nom, debuts.pop(nom) - AVANT_S, maintenant + APRES_S))
        for item in [i for i in a_couper if i[2] <= maintenant]:
            a_couper.remove(item)
            audio = cap.depuis(item[1], item[2])
            if len(audio) > 16000 * 0.3:
                file_phrases.put((audio, item[0]))
        time.sleep(0.05)


def tour_tactique(etat, derniers, maintenant=None, urgent=False):
    """One turn of the tactical AI: picture -> model -> guard -> commands. Returns the logged event, or None
    when there is no AI team to play (coop, menu, team down). derniers: member -> (last command, time), so the
    same command is not resent every turn, and a fresh order is not swapped for another (tacticien.TENUE_S)
    unless urgent (the situation changed) or it is a shot."""
    import tacticien
    maintenant = time.time() if maintenant is None else maintenant
    if not etat.get('joueur') or not equipiers_ia(etat):
        return None
    etat = MEMOIRE.filtrer(etat, maintenant)   # no wallhack: only what is or was seen
    ecartees = {p for p, t in PORTES_ECHOUEES.items() if maintenant - t < PORTE_ECARTEE_S}
    if ecartees:   # a door where an entry just failed is not offered again for a while
        etat = {**etat, 'portes': [p for p in etat.get('portes', []) if p['nom'] not in ecartees]}
    nom_niv, niv = tacticien.niveau(etat.get('difficulte'))
    with VERROU:
        ordre, t_ordre, nommes = ORDRE_CHEF['texte'], ORDRE_CHEF['t'], ORDRE_CHEF.get('membres') or []
        equipement = ORDRE_CHEF.get('equipement', False)
    depuis = maintenant - t_ordre
    recent = ordre is not None and depuis < ORDRE_CONTEXTE_S
    frais = recent and depuis < tacticien.RESPECT_ORDRE_S
    en_cours = {nom: (v[2], maintenant - v[1]) for nom, v in derniers.items() if len(v) > 2}
    mode = STYLE.mise_a_jour(etat)   # lethal or less-lethal: the team follows how Wal plays
    texte, ids = tacticien.situation(avec_vitres(etat), ordre if recent else None, depuis, niv, en_cours, style=mode)
    ids['tirs_bloques'] = tirs_bloques(maintenant)
    ids['lignes_blindees'] = LIGNES_BLINDEES
    equipiers = ids['equipiers']
    lettres = [k for k, s in equipiers.items() if s['nom'] in nommes]
    if recent and lettres:   # an order naming teammates: the model carries it out for them (and only them)
        pour = ', '.join(f"{k} ({tacticien.indicatif(equipiers[k])})" for k in lettres)
        texte += f"\nCet ordre du chef est pour {pour} : donne-leur exactement ce qu'il demande."
    if recent and equipement and depuis < tacticien.RESPECT_ORDRE_S:
        texte += "\nCet ordre du chef demande une action d'équipe (porte, grenade, équipement) : donne l'ordre EQUIPE qui le fait."
    if not ids['equipiers']:
        return None
    ordres, duree, err = tacticien.demander(CONFIG['modele'], texte, ids)
    ev = {'type': 'tactique', 'niveau': nom_niv, 'style': mode, 'style_score': STYLE.dernier,
          'ordre_chef': ordre if recent else None, 'ordre_frais': frais, 'ms': round(duree * 1000), 'decisions': []}
    if err:
        ev['erreur'] = err
    ordres = [o for o in ordres or [] if isinstance(o, dict)]
    equipe_o = next((o for o in ordres if o.get('equipier') == 'EQUIPE'), None)
    ordres = [o for o in ordres if o.get('equipier') != 'EQUIPE']
    # a grenade Wal asked for himself is thrown even where the tactical AI would not throw one on its own
    ids['grenade_demandee'] = bool(recent and equipement and depuis < tacticien.RESPECT_ORDRE_S and ordre
                                   and GRENADE_DEMANDEE.search(ordre))
    if MANOEUVRE['en_cours'] is None:
        # an order naming teammates may need the team's gear ("Alpha, Bravo, entrez avec une flash")
        genre, quoi = tacticien.garde_equipe(equipe_o, ids, recent, frais and not lettres and not equipement, niv)
        ev['equipe'] = {'brut': equipe_o, 'garde': quoi if genre is None else 'ok'}
        if genre == 'manoeuvre':
            import manoeuvre
            MANOEUVRE['en_cours'] = manoeuvre.Manoeuvre(
                *quoi, equipe='rouge', verifiees=PORTES_VERIFIEES if CONFIG['verifier_pieges'] else None)
            ev['equipe']['manoeuvre'] = list(quoi)
            annoncer(etat, _annonces.EMPILER, prioritaire=lettres != [] or frais)   # "stacking up"
        elif genre == 'cmd':
            ev['equipe']['pont'] = f'non envoyé (sans jeu) : {quoi}' if SANS_JEU else pont.send(quoi.split())
            if quoi.startswith('non_letal'):
                annoncer(etat, _annonces.NON_LETAL.get(quoi.split()[-1]))   # "taser!"
        elif recent and _annonces.manque_dans(quoi):
            # what Wal asked for needs gear the team no longer has: they say so
            annoncer(etat, _annonces.MANQUE[_annonces.manque_dans(quoi)])
    if MANOEUVRE['en_cours'] is not None:
        # the whole team is busy with the entry: individual orders wait, except the shooting reflex below
        ordres = [{'equipier': k, 'action': 'garder', 'cible': ''} for k in ids['equipiers']]
    # reflex: a teammate who sees a menacing armed suspect shoots now, whatever the model planned for him
    for k, sid in tacticien.reflexes(ids).items():
        ordres = [o for o in ordres if o.get('equipier') != k] + [{'equipier': k, 'action': 'tirer', 'cible': sid,
                                                                   'reflexe': True}]
    if MANOEUVRE['en_cours'] is None:
        ordres = tacticien.doctrine(ordres, ids)   # fire and manoeuvre, the leader always covered
    for o in ordres:
        # the named teammates carry out the fresh order; the others may only defend themselves meanwhile
        cmd, raison = tacticien.garde_ordre(o, ids, niv, recent, frais and o.get('equipier') not in lettres)
        d = {'brut': o, 'garde': raison}
        membre = ids['equipiers'].get(o.get('equipier'))
        if cmd and membre:
            avant = derniers.get(membre['nom'])
            tir = o.get('action') == 'tirer'
            # same plan, new coordinates (he covers the leader, who walked on): an update, not a change of mind
            meme_plan = avant is not None and len(avant) > 2 and avant[2] == tacticien.decrire(o, ids)
            ecart = maintenant - avant[1] if avant else 1e9
            if avant and avant[0] == cmd and ecart < (niv['rafale_s'] if tir else 8):
                d['pont'] = 'deja en cours'
            elif meme_plan and not tir and ecart < 2.0:
                d['pont'] = 'deja en cours'
            elif avant and not meme_plan and ecart < tacticien.TENUE_S and not urgent and not tir:
                d['pont'] = 'ordre precedent tenu'
            else:
                d['pont'] = f'non envoyé (sans jeu) : {cmd}' if SANS_JEU else pont.send(cmd.split())
                derniers[membre['nom']] = (cmd, maintenant, tacticien.decrire(o, ids))
                if not meme_plan and o.get('action') in ('couvrir', 'menotter'):
                    annoncer(etat, _annonces.PAR_ACTION[o['action']], membre['nom'])   # "covering", "restraining"
        ev['decisions'].append(d)
    return ev


VITRES = {'carte': None, 'liste': [], 't': 0.0}   # the map's window panes, asked once per map (sonde_vitres)


def avec_vitres(etat, envoyer_cmd=None):
    """The state with the map's window panes ('vitres': [(x, y, z) in m]) for the line-of-fire guard. Asked of the
    bridge once per map; a failed answer is asked again 30 s later."""
    carte = etat.get('carte')
    if VITRES['carte'] != carte and time.time() - VITRES['t'] > 30 and not SANS_JEU:
        VITRES['t'] = time.time()
        r = (envoyer_cmd or (lambda c: pont.send(c.split())))('sonde_vitres')
        if '|ok|' in r:
            VITRES.update(carte=carte, liste=[tuple(float(v) for v in m) for m in
                                              re.findall(r'\((-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\)', r)])
    return {**etat, 'vitres': VITRES['liste'] if VITRES['carte'] == carte else []}


# Wal (03/11): they tried to shoot through windows. The gas station's bulletproof glass is not one of the map's panes
# (sonde_vitres lists breakable glass only) and no sight check sees it: so a teammate who has fired this long at a
# suspect whose health has not moved stops, and finds another angle, for a while
TIR_SANS_EFFET_S = 3.0
TIR_BLOQUE_S = 15.0
TIRS_SUIVIS = {}    # (teammate, suspect) -> (time his firing began, the suspect's health then, last look)
TIR_TROU_S = 2.0    # a longer gap between two looks: his firing is a new one
TIRS_BLOQUES = {}   # (teammate, suspect) -> until when he must not fire at him
LIGNES_BLINDEES = []   # (shooter's place, target's place) where rounds did nothing: glass for the whole team, all mission


def tirs_bloques(maintenant):
    return {c for c, fin in TIRS_BLOQUES.items() if fin > maintenant}


def tir_sans_effet(membre, cible, maintenant):
    """True when the teammate's firing at this suspect has done nothing for TIR_SANS_EFFET_S (and blocks the pair)."""
    cle, vie = (membre, cible['nom']), cible.get('vie', -1)
    t0, vie0, vu = TIRS_SUIVIS.get(cle, (maintenant, vie, maintenant))
    if vie < vie0 or maintenant - vu > TIR_TROU_S:   # a hit, or a new burst after a pause: count again from now
        t0, vie0 = maintenant, vie
    TIRS_SUIVIS[cle] = (t0, vie0, maintenant)
    if maintenant - t0 >= TIR_SANS_EFFET_S:
        TIRS_BLOQUES[cle] = maintenant + TIR_BLOQUE_S
        del TIRS_SUIVIS[cle]
        return True
    return False


def tour_reflexes(etat, vus_depuis, maintenant=None):
    """The shooting reflex, without waiting for the model (a model turn takes 1 to 3 s in game): a teammate who
    sees a menacing armed suspect shoots after the level's reaction time, through the same guard as any order
    (line of fire, the leader's style, hostages). vus_depuis: (teammate, suspect) -> first time seen. Returns the
    commands sent."""
    import tacticien
    maintenant = time.time() if maintenant is None else maintenant
    if not etat.get('joueur') or not equipiers_ia(etat):
        vus_depuis.clear()
        return []
    nom_niv, niv = tacticien.niveau(etat.get('difficulte'))
    _, ids = tacticien.situation(avec_vitres(etat), niv=niv, style=STYLE.mode)
    ids['tirs_bloques'] = tirs_bloques(maintenant)
    ids['lignes_blindees'] = LIGNES_BLINDEES
    tirs = tacticien.reflexes(ids)
    for cle in [c for c in TIRS_SUIVIS if c not in {(ids['equipiers'][k]['nom'], ids['S'][s]['nom']) for k, s in tirs.items()}]:
        del TIRS_SUIVIS[cle]
    for cle in [c for c in vus_depuis if (c[0], c[1]) not in {(ids['equipiers'][k]['nom'], ids['S'][s]['nom'])
                                                               for k, s in tirs.items()}]:
        del vus_depuis[cle]
    envoyes = []
    for k, sid in tirs.items():
        membre, cible = ids['equipiers'][k], ids['S'][sid]
        debut = vus_depuis.setdefault((membre['nom'], cible['nom']), maintenant)
        if maintenant - debut < niv['reaction_s']:
            continue
        cmd, raison = tacticien.garde_ordre({'equipier': k, 'action': 'tirer', 'cible': sid}, ids, niv, True)
        if not cmd:
            continue
        if cmd.startswith('membre_tirer') and tir_sans_effet(membre['nom'], cible, maintenant):
            # his rounds do not get through (bulletproof glass, armour, cover): he stops and finds another angle
            log({'type': 'tir_sans_effet', 'membre': membre['nom'], 'cible': cible['nom'], 'vie': cible.get('vie')})
            LIGNES_BLINDEES.append((tuple(membre['pos']), tuple(cible['pos'])))   # no one shoots along it again
            ids['tirs_bloques'] = tirs_bloques(maintenant)
            cmd, raison = tacticien.garde_ordre({'equipier': k, 'action': 'tirer', 'cible': sid}, ids, niv, True)
            if not cmd:
                continue
        with VERROU_ORDRES:
            avant = DERNIERS.get(membre['nom'])
            if avant and avant[0] == cmd and maintenant - avant[1] < niv['rafale_s']:
                continue
            DERNIERS[membre['nom']] = (cmd, maintenant, 'tirer sur un suspect')
        r = f'non envoyé (sans jeu) : {cmd}' if SANS_JEU else pont.send(cmd.split())
        envoyes.append((cmd, r, raison))
        if cmd.startswith('membre_tirer') and cible['nom'] not in CONTACTS_ANNONCES:
            CONTACTS_ANNONCES.add(cible['nom'])   # "contact!" once per suspect, by the one who opens fire
            annoncer(etat, _annonces.CONTACT, membre['nom'])
    return envoyes


CONTACTS_ANNONCES = set()   # suspects already called out by the reflex

# the team's scoring jobs, done on its own when things are calm (the game's own commands: GiveReportTargetCommand,
# GiveCollectEvidenceCommand, GiveDisarmStandaloneTrapCommand): a trap first, then a report, then evidence
TACHES = ('pieges', 'rapporter', 'preuves')
TACHES_CLE = {'pieges': 'pieges', 'rapporter': 'rapports', 'preuves': 'preuves'}
TACHES_S = 12.0          # one job asked at most this often
TACHES_CALME_S = 10.0    # ...and only this long after the last threat seen or order from Wal
TACHES_PAUSE_S = 60.0    # a job that did not get done twice in a row is left alone this long
TACHES_M = 20
# 03/11 in game: a teammate's raw pick-up activity was taken back by the team's fall-in; the manager's own search and
# secure order for that teammate alone held (2 weapons in 3 s): that one first, the raw activity after a miss
PREUVES_ROUTES = ('individuelle', 'activite')
ZONE_CLAIRE_M = 20      # the area is clear: no suspect still free (surrendered but not cuffed included) this close
# 03/11 in game: with the whole program on, the tactical AI re-ordered the team every 6 to 12 s and no weapon was picked
# up (0 out of 4 in 100 s; alone, the same pick-up took 2 to 3 s): while one is under way, the team is left alone
TACHES_TENUE_S = 20.0
TACHES_ETAT = {'t': 0.0, 'menace': 0.0, 'ratees': {}, 'pause': {}, 'avant': None, 'tenue': 0.0, 'rang': 0}


def tactique_en_pause(maintenant=None):
    """True while the team picks evidence up and Wal has given no order since (the reflexes still defend)."""
    t = time.time() if maintenant is None else maintenant
    with VERROU:
        dernier_ordre = ORDRE_CHEF['t']
    return t < TACHES_ETAT['tenue'] and dernier_ordre < TACHES_ETAT['tenue'] - TACHES_TENUE_S


def taches_actives():
    """The jobs switched on: all of them with the 'taches' setting, else the evidence alone (needed to finish)."""
    return TACHES if CONFIG['taches'] else ('preuves',) if CONFIG['preuves'] else ()


def compter_taches(reponse):
    """{'rapports': n, 'preuves': n, 'pieges': n} from the bridge's sonde_rapports answer ({} when it failed)."""
    return {k: int(n) for k, n in re.findall(r'(rapports|preuves|pieges) (\d+)', reponse or '')} \
        if '|ok|' in (reponse or '') else {}


def taches_du_swat(etat, envoyer_cmd, maintenant=None):
    """One step of the team's own jobs. Returns the logged event, or None. Never during a door entry, a fight
    (a threat seen by the team or the player) or right after an order of Wal's."""
    t = time.time() if maintenant is None else maintenant
    s = TACHES_ETAT
    actives = taches_actives()
    if not actives or not equipiers_ia(etat) or MANOEUVRE['en_cours'] is not None:
        return None
    libres = [e for e in etat.get('ia', []) if e.get('suspect') and not e['hors_combat'] and not e['arrete']]
    if any((not e['rendu'] and not e.get('obeit') and (e.get('menace') or e.get('voit_police')))
           or e.get('distance_m', 1e9) <= ZONE_CLAIRE_M for e in libres):
        s['menace'] = t
    with VERROU:
        dernier_ordre = ORDRE_CHEF['t']
    if t - max(s['menace'], dernier_ordre) < TACHES_CALME_S or t - s['t'] < TACHES_S or t < s.get('tenue', 0.0):
        return None
    s['t'] = t
    compte = compter_taches(envoyer_cmd(f'sonde_rapports {TACHES_M}'))
    if s['avant']:   # the last job: done if what was left of it went down
        quoi, n = s['avant']
        if compte.get(TACHES_CLE[quoi], 0) < n:
            s['ratees'][quoi] = 0
            s['rang'] = 0
        else:
            s['ratees'][quoi] = s['ratees'].get(quoi, 0) + 1
            if s['ratees'][quoi] >= 2:
                s['ratees'][quoi] = 0
                # evidence: that item twice missed, the next one in line straight away (03/11 in game, the closest
                # weapon lay out of reach and the 60 s pause left the others on the floor); the pause once all tried
                if quoi == 'preuves' and s.get('rang', 0) + 1 < compte.get('preuves', 0):
                    s['rang'] = s.get('rang', 0) + 1
                else:
                    s['pause'][quoi] = t
                    if quoi == 'preuves':
                        s['rang'] = 0
        s['avant'] = None
    for quoi in actives:
        n = compte.get(TACHES_CLE[quoi], 0)
        if n and t - s['pause'].get(quoi, -1e9) >= TACHES_PAUSE_S:
            # evidence (03/11, in game): one teammate's search and secure first, his raw pick-up activity after a miss
            route = (f" {PREUVES_ROUTES[s['ratees'].get(quoi, 0) % 2]} {s.get('rang', 0) + 1}" if quoi == 'preuves'
                     else '')
            r = envoyer_cmd(f'swat_tache {quoi} rouge {TACHES_M}{route}')
            s['avant'] = (quoi, n)
            if quoi == 'preuves' and '|ok|' in r:
                s['tenue'] = t + TACHES_TENUE_S
            return {'type': 'tache', 'quoi': quoi, 'restant': compte, 'pont': r}
    return None


def boucle_reflexes():
    """Solo: the reflex, five times a second, apart from the model's turns."""
    vus_depuis = {}
    while True:
        time.sleep(0.2)
        try:
            if time.time() - pont.STATE.stat().st_mtime > 5:
                continue
            etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
            for cmd, r, _ in tour_reflexes(etat, vus_depuis):
                log({'type': 'reflexe', 'commande': cmd, 'pont': r})
        except (OSError, ValueError):   # the state is being rewritten
            continue
        except Exception as e:   # never stop the voice AI
            log({'type': 'erreur', 'source': 'reflexes', 'erreur': repr(e)})


def boucle_tactique():
    """Solo: re-think the team every few seconds (the game's difficulty sets how often), and soon after the
    situation changes (a suspect in view, a teammate hit: after the level's reaction time). Voice first."""
    import tacticien
    derniers, prochain, vue, urgent, carte = DERNIERS, 0.0, None, False, None
    while True:
        time.sleep(0.1)
        if VOIX_EN_COURS.is_set():
            continue
        try:
            etat = json.loads(pont.STATE.read_text(encoding='utf-8'))
            if time.time() - pont.STATE.stat().st_mtime > 5:   # game closed or loading: nothing to play
                continue
        except (OSError, ValueError):   # being rewritten by the bridge
            continue
        if etat.get('carte') != carte:   # a new mission: what the team knew of the last one goes
            carte = etat.get('carte')
            for memoire_de_mission in (PORTES_VERIFIEES, PORTES_ECHOUEES, MEMOIRE.vus, MEMOIRE.signales,
                                       CONTACTS_ANNONCES, DERNIERS, TIRS_SUIVIS, TIRS_BLOQUES, LIGNES_BLINDEES):
                memoire_de_mission.clear()
            MANOEUVRE['en_cours'] = None
            TACHES_ETAT.update(t=0.0, menace=0.0, ratees={}, pause={}, avant=None, tenue=0.0, rang=0)
        niv =tacticien.niveau(etat.get('difficulte'))[1]
        m = MANOEUVRE['en_cours']
        if m is not None:   # a door entry: one step at a time, each checked in the game
            avant = m.etape
            m.avancer(etat, (lambda cmd: f'1|ok|non envoyé (sans jeu) : {cmd}') if SANS_JEU else
                      (lambda cmd: pont.send(cmd.split())))
            if m.etape != avant or m.fini or m.echec:
                log({'type': 'manoeuvre', 'porte': m.porte, 'etape': m.etape, 'fini': m.fini, 'echec': m.echec,
                     'journal': m.journal[-2:]})
                if m.etape in ('forcer', 'entrer') and avant == 'empiler':
                    annoncer(etat, _annonces.ENTREE.get(m.methode))   # "breaching with the shotgun", "kicking"...
                elif m.etape == 'grenade' and avant != 'grenade':
                    annoncer(etat, _annonces.GRENADE.get(m.grenade))   # "flashbang out"
                elif m.fini and any('porte baton' in j for j in m.journal[-1:]):
                    annoncer(etat, _annonces.BATON)
            if m.fini or m.echec:
                if m.echec:
                    PORTES_ECHOUEES[m.porte] = time.time()
                MANOEUVRE['en_cours'] = None
        try:
            ev_t = taches_du_swat(etat, envoyer)
            if ev_t:
                log(ev_t)
        except Exception as e:   # never stop the tactical AI
            log({'type': 'erreur', 'source': 'taches', 'erreur': repr(e)})
        sig = tacticien.signature(MEMOIRE.filtrer(etat))   # what the team can know changing, not what is hidden
        if vue is not None and sig != vue:
            prochain = min(prochain, time.time() + niv['reaction_s'])
            urgent = True
        if NOUVEL_ORDRE.is_set():   # Wal named teammates: think now
            NOUVEL_ORDRE.clear()
            prochain, urgent = 0.0, True
        vue = sig
        if time.time() < prochain or tactique_en_pause():
            continue
        prochain = time.time() + 2.0
        try:
            ev = tour_tactique(etat, derniers, urgent=urgent)
            urgent = False
        except Exception as e:   # the tactical AI must never stop the voice AI
            log({'type': 'erreur', 'source': 'tactique', 'erreur': repr(e)})
            continue
        if ev:
            prochain = time.time() + niv['periode_s']
            if any('pont' in d for d in ev['decisions']) or 'erreur' in ev:
                log(ev)


def boucle_simulation(file_phrases, scenario, fini):
    """Test: Wal's voice from recordings instead of the microphone, on a schedule (a TSV: id, phrase, wait in s
    after it; the recordings are banc/audio_grand/<id>.wav). Everything else runs as in a real game."""
    lignes = [l.split('\t') for l in pathlib.Path(scenario).read_text(encoding='utf-8').splitlines()[1:] if l.strip()]
    dossier = ROOT / 'banc' / 'audio_grand'
    time.sleep(10)   # the tactical AI and the reflexes start first
    for ident, phrase, attente in lignes:
        log({'type': 'simulation', 'id': ident, 'phrase': phrase})
        file_phrases.put((lire_wav(dossier / f'{ident}.wav'), None))
        time.sleep(float(attente))
    time.sleep(20)
    fini.set()


def ecouter(transcrire):
    """Capture runs in threads (so a sentence is never missed while the previous one is being decided);
    sentences are decided one at a time, in order, here."""
    file_phrases = queue.Queue()
    fini = threading.Event()
    if '--simulation' in sys.argv:
        scenario = sys.argv[sys.argv.index('--simulation') + 1]
        threading.Thread(target=boucle_simulation, args=(file_phrases, scenario, fini), name='simulation',
                         daemon=True).start()
    else:
        threading.Thread(target=boucle_micro, args=(file_phrases,), name='micro', daemon=True).start()
    if CONFIG['autres_joueurs']:
        threading.Thread(target=boucle_autres, args=(file_phrases,), name='autres', daemon=True).start()
    if CONFIG['tactique']:
        threading.Thread(target=boucle_tactique, name='tactique', daemon=True).start()
        threading.Thread(target=boucle_reflexes, name='reflexes', daemon=True).start()
    if CONFIG['adversaires']:
        threading.Thread(target=boucle_adverse, name='adversaire', daemon=True).start()
        threading.Thread(target=boucle_marcheur, name='marcheur', daemon=True).start()
        threading.Thread(target=boucle_ecoute, name='ecoute', daemon=True).start()
    log({'type': 'pret', 'touche_vk': CONFIG['touche_vk'], 'chat_vocal': CONFIG['chat_vocal'],
         'autres_joueurs': CONFIG['autres_joueurs'], 'micro': CONFIG['micro'], 'modele': CONFIG['modele'],
         'sans_jeu': SANS_JEU})
    print("\nPrêt. Parle au chat vocal du jeu (ou tiens ta touche), puis relâche. Ctrl+C pour arrêter.", flush=True)
    while not (fini.is_set() and file_phrases.empty()):
        try:
            audio, joueur = file_phrases.get(timeout=1)
        except queue.Empty:
            continue
        chemin = nom_wav(joueur)
        ecrire_wav(chemin, audio)
        VOIX_EN_COURS.set()
        try:
            traiter(audio, transcrire, chemin, joueur)
        except Exception as e:   # one bad sentence must not stop the listening
            log({'type': 'erreur', 'joueur': joueur, 'audio': chemin.name, 'erreur': repr(e)})
        finally:
            VOIX_EN_COURS.clear()


if __name__ == '__main__':
    transcrire = charger_whisper()
    cerveau.appeler(CONFIG['modele'], 'test', 'le joueur ne vise personne')   # load the model now, not on the first phrase
    if CONFIG['parole'] and CONFIG['langue'] != 'en':   # and the voice of the answers (step 7), French
        PAROLE['moteur'] = _parole.Parole(CONFIG['piper_python'], CONFIG['piper_voix'])
        PAROLE['moteur'].prechauffer()
    if CONFIG['parole'] and CONFIG['langue'] != 'fr' and pathlib.Path(CONFIG['piper_voix_en']).exists():   # English
        PAROLE['moteur_en'] = _parole.Parole(CONFIG['piper_python'], CONFIG['piper_voix_en'])
        PAROLE['moteur_en'].prechauffer()
    if '--wav' in sys.argv:
        for f in sys.argv[sys.argv.index('--wav') + 1:]:
            traiter(lire_wav(f), transcrire, pathlib.Path(f))
    else:
        ecouter(transcrire)
