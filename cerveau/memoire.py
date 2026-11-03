"""What the AI team knows about people: only what the leader or a teammate sees, plus a memory of who vanished.

Before (02/11) the model got the true position of every suspect within 30 m, even behind walls: a wallhack.
Now each suspect or civilian is either:
- seen now (by the leader or a teammate): as he is, "vu": True;
- seen less than GARDE_S ago: as he was LAST SEEN (position, armed, menacing, surrendered...), with how long ago
  ("disparu_s"): the team watches where he went out of sight;
- never seen, or forgotten: left out.
"""
import math
import time

GARDE_S = 60.0
SIGNAL_S = 120.0   # a hint given by a suspect or a civilian ("there are two of them in the kitchen") lasts this long


class Memoire:
    def __init__(self):
        self.vus = {}   # name -> (snapshot of the entity when last seen, time)
        self.signales = []   # hints heard from the people questioned (step 6): never a sighting, maybe a lie

    def signaler(self, indice, source, maintenant=None):
        """A hint from a suspect or a civilian: {'nombre', 'pos', 'piece'} (pos None = "nobody else")."""
        t = time.time() if maintenant is None else maintenant
        self.signales.append({**indice, 'source': source, 't': t})
        self.signales = self.signales[-6:]

    def filtrer(self, etat, maintenant=None):
        """A copy of the state whose 'ia' list holds only what the team knows."""
        maintenant = time.time() if maintenant is None else maintenant
        equipe = [s for s in etat.get('swat', []) if 'Trailer' not in s.get('classe', '') and not s['hors_combat']]
        vus = {n for s in equipe for n in s.get('voit', [])}
        chef = next((j for j in etat.get('joueurs', []) if j['moi'] and 'pos' in j), None)
        connus = []
        for e in etat.get('ia', []):
            if e['nom'] in vus or e.get('vu_chef'):
                self.vus[e['nom']] = (dict(e), maintenant)
                connus.append({**e, 'vu': True})
            elif e['nom'] in self.vus and maintenant - self.vus[e['nom']][1] <= GARDE_S:
                vu, t = self.vus[e['nom']]
                d = math.dist(vu['pos'], chef['pos']) if chef else vu['distance_m']
                connus.append({**vu, 'vu': False, 'disparu_s': round(maintenant - t), 'distance_m': round(d, 1)})
        for nom in [n for n, (_, t) in self.vus.items() if maintenant - t > GARDE_S]:
            del self.vus[nom]
        self.signales = [s for s in self.signales if maintenant - s['t'] <= SIGNAL_S]
        # what the team was told (not what it saw), without the truth flag the game gave us
        dits = [{k: v for k, v in s.items() if k != 'vrai'} | {'il_y_a_s': round(maintenant - s['t'])} for s in self.signales]
        return {**etat, 'ia': connus, 'signalements': dits}
