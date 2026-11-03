"""Step 7, part 1: what the suspects, civilians and the SWAT say, with the game's own recorded lines.

The game plays the exact variant asked for: PlayRawVO("[BARK]Compliant_2") (in game, 03/11: 8 variants out of 8
recognised in the game's sound against the real files). The French subtitles of every voice (dumps/sous_titres,
outils/extraire_sous_titres.py) say what each variant says. The code decides WHICH KIND of line (the reaction the
guard of step 6 allowed); the model picks, among that person's own variants of that kind, the one whose words answer
Wal best ("Lâche ton arme !" -> "Jamais !" rather than "J'ai rien fait"). Without the model: a random variant, never
the same twice in a row for one person.
"""
import csv
import json
import pathlib
import random
import re
import time
import urllib.request

from cerveau import OLLAMA

DOSSIER = pathlib.Path(__file__).resolve().parent.parent / 'dumps' / 'sous_titres'
_CATALOGUE = {}            # voice -> {kind: [(key, text)]}
_DERNIERE = {}             # person -> last key said
VARIANTES_MAX = 8          # offered to the model per person (the question stays short)


def catalogue(voix):
    """{kind: [(key, French text)]} for one voice ({} when its subtitles are not there)."""
    if voix not in _CATALOGUE:
        lignes = {}
        f = DOSSIER / f'{voix}.csv'
        if f.exists():
            with open(f, encoding='utf-8-sig', newline='') as fic:
                for r in csv.DictReader(fic):
                    cle, texte = (r.get('Key') or '').strip(), (r.get('Dialogue') or '').strip()
                    m = re.fullmatch(r'(.+)_(\d+)', cle)
                    if m and texte:
                        lignes.setdefault(m.group(1), []).append((cle, texte))
        for v in lignes.values():
            v.sort(key=lambda kt: int(kt[0].rsplit('_', 1)[1]))
        _CATALOGUE[voix] = lignes
    return _CATALOGUE[voix]


def variantes(voix, ligne):
    """The variants of this kind of line for this voice: [(key, text)], [] when unknown."""
    return list(catalogue(voix).get(ligne, []))


def parlee(texte):
    """A line that is words, not a stage direction such as "(Rit avant d'aboyer)"."""
    return not re.fullmatch(r'\s*[\(\[].*[\)\]]\s*', texte)


def au_hasard(nom, voix, ligne, hasard=random):
    """(key, text) of a variant other than the last one this person said, or (ligne, None) when unknown."""
    v = variantes(voix, ligne)
    if not v:
        return ligne, None
    autres = [x for x in v if x[0] != _DERNIERE.get(nom)] or v
    cle, texte = hasard.choice(autres)
    _DERNIERE[nom] = cle
    return cle, texte


def offertes(nom, voix, ligne, hasard=random):
    """The variants the model may choose from for this person: spoken ones, not the last one he said, at most
    VARIANTES_MAX (a random sample when there are more)."""
    v = [x for x in variantes(voix, ligne) if parlee(x[1]) and x[0] != _DERNIERE.get(nom)]
    return hasard.sample(v, VARIANTES_MAX) if len(v) > VARIANTES_MAX else v


SYSTEME = """Tu choisis ce que répondent des personnages du jeu Ready or Not à un policier, parmi leurs répliques
enregistrées. Pour chaque personne, choisis la réplique dont les mots répondent le mieux à la phrase du policier et
à ce que la personne fait, de façon naturelle. Réponds seulement en JSON."""


def choisir(modele, phrase, demandes, timeout=4):
    """One model call for everyone: demandes = [(k, what he does, [(key, text)])] -> ({k: key}, seconds, error).
    Only keys offered for that person are kept (the schema allows only them, the code checks again)."""
    demandes = [(k, quoi, v) for k, quoi, v in demandes if v]
    if not demandes:
        return {}, 0.0, None
    texte = f"Le policier a dit : « {phrase} »\n" + '\n'.join(
        f"Personne {k} ({quoi}) : " + ' ; '.join(f'{cle} = « {t} »' for cle, t in v) for k, quoi, v in demandes)
    schema = {'type': 'object', 'properties': {k: {'type': 'string', 'enum': [c for c, _ in v]} for k, _, v in demandes},
              'required': [k for k, _, _ in demandes]}
    body = json.dumps({'model': modele, 'stream': False, 'think': False, 'keep_alive': '30m', 'format': schema,
                       'messages': [{'role': 'system', 'content': SYSTEME}, {'role': 'user', 'content': texte}],
                       'options': {'temperature': 0.7, 'num_ctx': 2048}}).encode()
    t = time.perf_counter()
    try:
        req = urllib.request.Request(OLLAMA, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rep = json.loads(json.load(r)['message']['content'])
    except Exception as e:   # slow, down, malformed: random variants instead
        return {}, time.perf_counter() - t, f'{type(e).__name__}: {e}'
    permis = {k: {c for c, _ in v} for k, _, v in demandes}
    return {k: c for k, c in rep.items() if k in permis and c in permis[k]}, time.perf_counter() - t, None


def retenir(nom, cle):
    _DERNIERE[nom] = cle


def texte_de(voix, cle):
    ligne = cle.rsplit('_', 1)[0]
    return next((t for c, t in variantes(voix, ligne) if c == cle), None)
