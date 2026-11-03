"""Step 7, part 2: a suspect or a civilian ANSWERS Wal out loud, in French, when no recorded line can say it
("Ils sont trois. Il y en a deux derrière toi.").

- The words come from the code, from the facts it decided at step 6 (how many, where, true or a lie): the model
  never adds a fact. Room names are the game's internal ones ("Room_12"): never spoken; the direction is given from
  where Wal stands and looks (devant, derrière, à gauche, à droite, en haut, en bas).
- The voice: Piper, French (fr_FR-siwis-medium, on the CPU: no video memory taken from the game; 92 ms a sentence,
  0 word error on 9 sentences listened back by Whisper, 03/11). Chatterbox imitated each character's own voice but
  took 3.6 s a sentence and 3.2 GB of video memory, and dropped the fact itself twice out of six. Piper has one
  woman's voice: each character gets his own pitch (lower for a man), fixed by his game voice name.
- The sound plays through Windows, louder when he is close and on his side (left/right) of Wal's view.
Piper runs in a worker process with the Python that has it (setting piper_python; no bytecode written there). Each
character's pitch needs ffmpeg on the PATH; without it, the voice is said unchanged.
"""
import hashlib
import json
import math
import pathlib
import queue
import subprocess
import threading
import time
import wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOSSIER = ROOT / 'logs' / 'parole'
NOMBRES = ['zéro', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf', 'dix', 'onze', 'douze',
           'treize', 'quatorze', 'quinze', 'seize', 'dix-sept', 'dix-huit', 'dix-neuf', 'vingt']   # the house: 13
PRES_M, LOIN_M, ETAGE_M = 8.0, 35.0, 2.5


def en_lettres(n):
    return NOMBRES[n] if 0 <= n < len(NOMBRES) else str(n)


def direction(depuis, lacet, vers):
    """Where 'vers' is for someone at 'depuis' looking along 'lacet' (degrees): 'devant toi', 'derrière toi',
    'à ta gauche', 'à ta droite', or 'en haut' / 'en bas' when it is a floor away."""
    dz = vers[2] - depuis[2]
    if abs(dz) >= ETAGE_M:
        return 'en haut' if dz > 0 else 'en bas'
    angle = math.degrees(math.atan2(vers[1] - depuis[1], vers[0] - depuis[0])) - lacet
    angle = (angle + 180) % 360 - 180   # -180..180, 0 = straight ahead
    if abs(angle) <= 45:
        return 'devant toi'
    if abs(angle) >= 135:
        return 'derrière toi'
    # the game's yaw grows clockwise seen from above (Unreal: +Y to the right of +X)
    return 'à ta droite' if angle > 0 else 'à ta gauche'


SILENCES = ["J'ai rien à te dire.", 'Je dirai rien.', 'Va te faire voir.']
SEULS = ['Je suis tout seul !', "Y a personne d'autre !"]
# the English version (Wal, 03/11): the same facts in English words, said by an English Piper voice
NUMBERS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
           'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty']
DIRECTIONS_EN = {'devant toi': 'in front of you', 'derrière toi': 'behind you', 'à ta gauche': 'to your left',
                 'à ta droite': 'to your right', 'en haut': 'upstairs', 'en bas': 'downstairs'}
SILENCES_EN = ["I've got nothing to say.", "I'm not telling you anything.", 'Go to hell.']
SEULS_EN = ["I'm alone!", "There's nobody else!"]


def _phrase_en(reaction, indice, civil, joueur, hasard):
    if reaction == 'se_taire':
        return None if civil else hasard.choice(SILENCES_EN)
    if indice is None:
        return None
    n = indice['nombre']
    if n == 0:
        return "I think there's nobody else." if civil else hasard.choice(SEULS_EN)
    nombre = NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)
    if n == 1:
        texte = "I think there's one" if civil else "There's one"
    else:
        texte = f'I think there are {nombre}' if civil else f'There are {nombre}'
    if indice.get('pos') and joueur:
        d = math.dist(joueur['pos'][:2], indice['pos'][:2])
        ou = DIRECTIONS_EN[direction(joueur['pos'], joueur.get('lacet', 0.0), indice['pos'])]
        loin = ', close by' if d <= PRES_M else (', far away' if d >= LOIN_M else '')
        return f'{texte}, {ou}{loin}.' if n == 1 else f'{texte}. Mostly {ou}{loin}.'
    return texte + '.'


def phrase(reaction, indice, e, joueur, hasard, langue='fr'):
    """The words he says for his answer (dire_vrai / mentir / se_taire), from the facts only, in French or English
    (langue 'en'). None = nothing."""
    civil = not e.get('suspect')
    if langue == 'en':
        return _phrase_en(reaction, indice, civil, joueur, hasard)
    if reaction == 'se_taire':
        return None if civil else hasard.choice(SILENCES)
    if indice is None:
        return None
    if indice['nombre'] == 0:
        return "Je crois qu'il n'y a personne d'autre." if civil else hasard.choice(SEULS)
    n = indice['nombre']
    debut = "Je crois qu'ils sont" if civil else 'Ils sont'
    if n == 1:
        debut = "Je crois qu'il y en a un" if civil else "Il y en a un"
        texte = debut
    else:
        texte = f'{debut} {en_lettres(n)}'
    if indice.get('pos') and joueur:
        d = math.dist(joueur['pos'][:2], indice['pos'][:2])
        ou = direction(joueur['pos'], joueur.get('lacet', 0.0), indice['pos'])
        loin = ', pas loin' if d <= PRES_M else (', loin' if d >= LOIN_M else '')
        texte += f', {ou}{loin}.' if n == 1 else f'. Surtout {ou}{loin}.'
    else:
        texte += '.'
    return texte


def hauteur(voix, femme):
    """His own pitch factor, fixed by his game voice: a man 0.74-0.86 of Piper's woman's voice, a woman 0.96-1.08."""
    h = int(hashlib.sha256((voix or '?').encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (0.96 + 0.12 * h) if femme else (0.74 + 0.12 * h)


def panoramique(joueur, pos):
    """(left gain, right gain) for a voice at 'pos' heard by Wal: louder when close, on its side of his view."""
    if not joueur or not pos:
        return 0.8, 0.8
    d = math.dist(joueur['pos'][:2], pos[:2])
    volume = max(0.25, min(1.0, 1.0 - d / 30.0))
    angle = math.radians(math.degrees(math.atan2(pos[1] - joueur['pos'][1], pos[0] - joueur['pos'][0]))
                         - joueur.get('lacet', 0.0))
    cote = 0.8 * math.sin(angle)        # +1 = to his right (see direction()); never one ear silent
    return volume * (1.0 - cote) ** 0.5 * 0.7, volume * (1.0 + cote) ** 0.5 * 0.7


def changer_hauteur(son, taux, facteur):
    """Lower or raise the pitch, keeping the speed (ffmpeg: resample then time-stretch back)."""
    if abs(facteur - 1.0) < 0.01:
        return son
    filtre = f'asetrate={int(taux * facteur)},aresample={taux},atempo={1 / facteur:.4f}'
    try:
        r = subprocess.run(['ffmpeg', '-v', 'error', '-f', 's16le', '-ar', str(taux), '-ac', '1', '-i', 'pipe:0', '-af', filtre,
                            '-f', 's16le', '-ac', '1', '-ar', str(taux), 'pipe:1'],
                           input=(np.clip(son, -1, 1) * 32767).astype(np.int16).tobytes(), capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):   # no ffmpeg on this PC: his voice, unchanged
        return son
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0 if r.returncode == 0 else son


class Parole:
    """The Piper worker and the playback. dire() returns at once; the sentence plays in its own thread, one at a
    time (two answers never talk over each other)."""

    def __init__(self, python, modele, jouer=None):
        self.python, self.modele = python, modele
        self.jouer = jouer or self._jouer_windows
        self.proc, self.verrou, self.n = None, threading.Lock(), 0
        self.file = queue.Queue()
        threading.Thread(target=self._boucle, name='parole', daemon=True).start()

    def _demarrer(self):
        if self.proc and self.proc.poll() is None:
            return True
        if not pathlib.Path(self.python).exists() or not pathlib.Path(self.modele).exists():
            return False
        self.proc = subprocess.Popen([self.python, '-B', str(ROOT / 'voix' / 'serveur_piper.py'), self.modele],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW,
                                     env={**__import__('os').environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        return json.loads(self.proc.stdout.readline() or '{}').get('pret', False)

    def synthetiser(self, texte):
        """(mono float32, rate) from the worker, or (None, None)."""
        with self.verrou:
            if not self._demarrer():
                return None, None
            self.n += 1
            DOSSIER.mkdir(parents=True, exist_ok=True)
            f = DOSSIER / f'phrase_{self.n % 20}.wav'
            self.proc.stdin.write(json.dumps({'texte': texte, 'sortie': str(f)}, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()
            rep = json.loads(self.proc.stdout.readline() or '{}')
        if not rep.get('ok'):
            return None, None
        with wave.open(str(f), 'rb') as w:
            taux = w.getframerate()
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0, taux

    def prechauffer(self):
        """Load Piper now (about 2 s), not on the first answer. Nothing is played."""
        threading.Thread(target=self.synthetiser, args=('Prêt.',), name='parole-prechauffe', daemon=True).start()

    def dire(self, texte, voix, femme, pos=None, joueur=None, rapport=None):
        self.file.put((texte, voix, femme, pos, joueur, rapport))

    def _boucle(self):
        while True:
            texte, voix, femme, pos, joueur, rapport = self.file.get()
            t = time.perf_counter()
            try:
                son, taux = self.synthetiser(texte)
                if son is not None:
                    son = changer_hauteur(son, taux, hauteur(voix, femme))
                    g, d = panoramique(joueur, pos)
                    if rapport:
                        rapport(round((time.perf_counter() - t) * 1000), None)
                    self.jouer(np.stack([son * g, son * d], axis=1), taux)
                elif rapport:
                    rapport(None, 'synthese indisponible')
            except Exception as e:   # the voice must never stop the program
                if rapport:
                    rapport(None, f'{type(e).__name__}: {e}')

    @staticmethod
    def _jouer_windows(stereo, taux):
        import sounddevice as sd
        sd.play(stereo.astype(np.float32), taux, blocking=True)
