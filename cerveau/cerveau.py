"""The brain: turn what the player said (plus the game context) into ONE action from a closed list.

The local model (Ollama) only proposes; `garde()` decides. Anything outside the list, malformed, or
aimed at a target that does not exist becomes "rien". Each action maps to one WalPont bridge command.
"""
import json
import os
import re
import time
import urllib.request

# the project's own Ollama server (port 11435), not the default one (11434) another app may run, which unloads
# models it did not ask for
OLLAMA = os.environ.get('RON_OLLAMA', 'http://127.0.0.1:11435') + '/api/chat'

# action -> (who it is for, bridge command template)
ACTIONS = {
    'rien': ('aucun', None),
    'swat_suivre': ('swat', 'swat suivre {equipe}'),
    'swat_tenir': ('swat', 'swat tenir {equipe}'),
    'swat_reprendre': ('swat', 'swat reprendre {equipe}'),
    'swat_couvrir': ('swat', 'swat couvrir {equipe}'),
    'swat_aller': ('swat', 'swat aller {equipe}'),
    'swat_fouiller': ('swat', 'swat fouiller {equipe}'),
    'swat_menotter': ('swat', 'swat menotter {equipe} vise'),
    'ordre_reddition': ('suspect', None),   # the reaction is decided by cerveau/interlocuteurs.py
    'ordre_obeir': ('civil', None),         # same for civilians
}
EQUIPES = ('tous', 'rouge', 'bleu')

SCHEMA = {
    'type': 'object',
    'properties': {
        'action': {'type': 'string', 'enum': list(ACTIONS)},
        'equipe': {'type': 'string', 'enum': list(EQUIPES)},
    },
    'required': ['action', 'equipe'],
}

SYSTEME = """Tu es le module de compréhension d'un mod du jeu Ready or Not. Le joueur est un policier du SWAT.
Il parle à voix haute (texte transcrit, parfois avec des fautes), en français OU en anglais : même liste d'actions
("Follow me" = swat_suivre, "Drop your weapon!" = ordre_reddition...). Tu choisis UNE action dans la liste.

Actions pour ses coéquipiers SWAT :
- swat_suivre : l'équipe le suit / se regroupe sur lui.
- swat_tenir : l'équipe s'arrête et tient sa position.
- swat_reprendre : l'équipe reprend après un arrêt.
- swat_couvrir : l'équipe couvre / surveille la zone devant lui.
- swat_aller : l'équipe se déplace à l'endroit qu'il montre.
- swat_fouiller : l'équipe fouille et sécurise la pièce.
- swat_menotter : l'équipe menotte / arrête la personne visée.
Actions pour les personnes en face :
- ordre_reddition : il ordonne à un suspect de se rendre, de lâcher son arme, de ne plus bouger, de se mettre à terre.
- ordre_obeir : il s'adresse à un civil pour qu'il obéisse, se calme, lève les mains, se couche.
- rien : tout le reste (discussion entre joueurs, blague, phrase sans ordre, bruit).

equipe : "rouge" ou "bleu" seulement si le joueur nomme l'équipe rouge ou bleue (red / blue), sinon "tous".
Le contexte dit qui est visé par le joueur ; s'il crie un ordre de reddition vers un civil visé, c'est ordre_obeir.
Réponds seulement en JSON."""


# Orders so unambiguous that code decides them before the model. Wal's first live test (02/11): qwen3.5:4b turned
# "Suivez-moi." into swat_aller whenever he was aiming at someone.
REGLES = [
    # (and in English, Wal 03/11: the same orders, whatever language a player speaks)
    (re.compile(r"\b(suivez[- ]moi|suis[- ]moi|regroupez[- ]vous|restez derrière moi|reste derrière moi|"
                r"follow me|fall in|regroup|group up|stay (behind|with) me|stack on me)\b", re.IGNORECASE), 'swat_suivre'),
    # "On continue", "On repart": the team resumes after a hold (every model said swat_aller for "On continue, allez").
    # Before the "on y va" rule: "On repart, on y va" is a resume.
    (re.compile(r"(?<!['’])\b(on continue|on repart|on reprend|reprenez|c'est reparti|c’est reparti|"
                r"keep (going|moving)|carry on|resume|let'?s continue)\b(?!\s+(pas|jamais)\b)", re.IGNORECASE),
     'swat_reprendre'),
    # Wal (02/11): "On y va", "C'est parti", "On rentre", "En avant" = the team moves ahead of him (swat aller:
    # 5 m ahead on his view line). Not after an apostrophe ("est-ce qu'on y va ?"), not followed by "pas"/"jamais"
    # (in English: not after "don't" / "not")
    (re.compile(r"(?<!['’])\b(on y va|c'est parti|c’est parti|on rentre|en avant|avancez)\b(?!\s+(pas|jamais)\b)"
                r"|(?<!don't )(?<!not )\b(let'?s go|go go|move up|move in|move forward|push (up|forward)|go ahead)\b",
                re.IGNORECASE), 'swat_aller'),
    # 03/11, English model bench: "Clear the area" became swat_aller 3 times out of 3 (the French "Nettoyez la zone"
    # goes right through the model)
    (re.compile(r"\b(search|clear|sweep|secure) (the|this|that) (room|area|zone|place|floor)\b", re.IGNORECASE),
     'swat_fouiller'),
]   # not "sur moi" nor a bare "derrière moi": "il a tiré sur moi", "il est derrière moi" would call the team over
EQUIPE_NOMMEE = {'rouge': r'\b(rouge(s|es)?|red)\b', 'bleu': r'\b(bleu(e|s|es)?|blue)\b'}


def regle_directe(phrase):
    """A {'action', 'equipe'} decided by code for an unambiguous order, or None to ask the model."""
    for motif, action in REGLES:
        if motif.search(phrase):
            equipe = next((e for e, rx in EQUIPE_NOMMEE.items() if re.search(rx, phrase, re.IGNORECASE)), 'tous')
            return {'action': action, 'equipe': equipe}
    return None


# A sentence that only reports something ("Je crois qu'il y a un mec dans la cuisine") is not an order, but the
# model sends the team there. Only when it opens like a report AND holds no order verb (-ez: "fouillez", "allez-y").
INFORMATION = re.compile(r"^\W*(je crois|je pense|j'ai l'impression|j’ai l’impression|il y a|y a|j'entends|j’entends"
                         r"|j'ai vu|j’ai vu|j'ai entendu|j’ai entendu|on dirait"
                         r"|i think|i believe|i guess|there'?s|there is|there are|i hear|i heard|i saw|i see|looks like"
                         r"|seems like|sounds like)\b", re.IGNORECASE)
VERBE_ORDRE = re.compile(r"\b(?!(chez|assez|rez|nez)\b)\w+ez\b", re.IGNORECASE)   # "chez lui", "au rez-de-chaussée"
# English orders have no "-ez": the team verbs themselves ("there's a guy in there, go check")
VERBE_ORDRE_EN = re.compile(r"\b(go|move|search|clear|check|cover|follow|hold|cuff|restrain|stack|breach|open|kick|"
                            r"throw|deploy|watch|get in|come)\b", re.IGNORECASE)


def corriger(phrase, brut):
    """The model's proposal, except a team order drawn from a mere report, which becomes "rien"."""
    if isinstance(brut, dict) and str(brut.get('action', '')).startswith('swat_') \
            and INFORMATION.search(phrase) and not VERBE_ORDRE.search(phrase) and not VERBE_ORDRE_EN.search(phrase):
        return {**brut, 'action': 'rien'}
    return brut


def appeler(modele, phrase, contexte, timeout=30):
    """Ask the model. Returns (raw dict or None, seconds, error)."""
    msg = f'Contexte : {contexte}\nLe joueur dit : « {phrase} »'
    body = json.dumps({
        'model': modele,
        'messages': [{'role': 'system', 'content': SYSTEME}, {'role': 'user', 'content': msg}],
        'format': SCHEMA,
        'stream': False,
        'think': False,
        'keep_alive': '30m',
        'options': {'temperature': 0, 'num_ctx': 2048},
    }).encode()
    t = time.perf_counter()
    try:
        req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = json.load(r)['message']['content']
        return json.loads(content), time.perf_counter() - t, None
    except Exception as e:  # malformed JSON, timeout, server down: the guard turns it into "rien"
        return None, time.perf_counter() - t, f'{type(e).__name__}: {e}'


def garde(brut, contexte_vise):
    """Validate the model's proposal in code. Returns (action, equipe, bridge command or None, reason)."""
    if not isinstance(brut, dict):
        return 'rien', 'tous', None, 'reponse illisible'
    action, equipe = brut.get('action'), brut.get('equipe', 'tous')
    if action not in ACTIONS:
        return 'rien', 'tous', None, f'action hors liste : {action!r}'
    if equipe not in EQUIPES:
        equipe = 'tous'
    pour, modele_cmd = ACTIONS[action]
    if pour in ('suspect', 'civil') and contexte_vise != pour:
        # an order for a suspect while aiming at a civilian (or nobody) must not reach the wrong person
        if contexte_vise in ('suspect', 'civil'):
            action = 'ordre_reddition' if contexte_vise == 'suspect' else 'ordre_obeir'
        else:
            return 'rien', equipe, None, f'{action} sans personne visee'
    cmd = modele_cmd.format(equipe=equipe) if modele_cmd else None
    return action, equipe, cmd, 'ok'
