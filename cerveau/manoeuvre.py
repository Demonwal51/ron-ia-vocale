"""A door entry, step by step: each step is checked in the game before the next one is sent.

Learnt in game (02/11): an entry waits until every member of the team is in his stack-up spot, and one teammate
sometimes never gets there (the player standing on his spot, or an unreachable spot); the grenade given with the
game's "breach and clear" was never thrown; "breach and clear" does nothing on a door already open. So:
  1. the entry team (2 men) stacks up; if it is not in place in time, the other team takes over;
  2. no grenade: breach and clear in one order (open, kick, shotgun, ram or C2);
     with a grenade: breach only, the other team throws the grenade past the doorway, then the entry team
     clears the room (search and secure behind the door);
  3. a known trap is disarmed first; a locked door is not "opened": it is kicked (or C2 / shotgun if asked);
  4. a closed door nobody has checked yet is looked under first (the mirror), when the team has one: a trap
     found there is disarmed before the stack-up. Each door is checked once (the caller keeps the set).
"""
import time

ATTENTE_VERIF_S = 15.0       # the look under the door: then go on, trap seen or not
ATTENTE_PLACE_S = 12.0
ATTENTE_PORTE_S = 15.0
ATTENTE_GRENADE_S = 3.0
ATTENTE_NETTOYAGE_S = 30.0   # then the room is marked cleared (a chemlight in the doorway) even if they still search
EQUIPES = {'rouge': 1, 'bleu': 2}
AUTRE = {'rouge': 'bleu', 'bleu': 'rouge'}


def membres(etat, equipe):
    return [s for s in etat.get('swat', []) if 'Trailer' not in s.get('classe', '') and not s['hors_combat']
            and s.get('equipe') == EQUIPES[equipe]]


def a_l_objet(etat, equipe, objet):
    return sum(s.get('equipement', {}).get(objet, 0) for s in membres(etat, equipe))


class Manoeuvre:
    def __init__(self, porte, methode='ouvrir', grenade='aucune', equipe='rouge', maintenant=None, verifiees=None):
        """verifiees: the doors already looked under this mission (shared set, updated here); None = no check."""
        self.porte, self.methode, self.grenade, self.equipe = porte, methode, grenade, equipe
        self.verifiees, self.verif_vue = verifiees, False
        self.etape, self.t, self.relais = 'debut', time.time() if maintenant is None else maintenant, False
        self.fini, self.echec, self.journal = False, None, []
        self.fouille_vue, self.relance = False, False   # clearing seen started; clearing order sent a second time

    def _porte(self, etat):
        return next((p for p in etat.get('portes', []) if p['nom'] == self.porte), None)

    def _envoyer(self, envoyer, *mots):
        r = envoyer(' '.join(mots))
        self.journal.append(f'{" ".join(mots[:2])} -> {r}')
        if '|ECHEC|' in r:
            self.echec = r.split('|', 2)[-1]
        return r

    def _passer(self, etape, maintenant):
        self.etape, self.t = etape, maintenant

    def avancer(self, etat, envoyer, maintenant=None):
        """One step if its condition is met. envoyer(command) -> the bridge's answer. Returns the current step."""
        maintenant = time.time() if maintenant is None else maintenant
        if self.fini or self.echec:
            return self.etape
        p = self._porte(etat)
        if p is None:
            self.echec = 'porte plus en vue'
            return self.etape
        attente = maintenant - self.t
        place = [s.get('activite') or '' for s in membres(etat, self.equipe)]
        if self.etape == 'debut':
            verifieur = next((e for e in (self.equipe, AUTRE[self.equipe]) if a_l_objet(etat, e, 'optiwand')), None)
            if p['ouverte'] or p['cassee']:   # already open: no breach, straight to the grenade or the clearing
                self._grenade_ou_nettoyer(etat, envoyer, maintenant)
            elif p.get('piege'):
                self._envoyer(envoyer, 'porte', 'desamorcer', self.equipe, self.porte)
                self._passer('desamorcer', maintenant)
            elif self.verifiees is not None and self.porte not in self.verifiees and verifieur:
                self._envoyer(envoyer, 'porte', 'pieges', verifieur, self.porte)   # a look under the door first
                self.verifieur = verifieur
                self._passer('verifier', maintenant)
            else:
                self._empiler(p, envoyer, maintenant)
        elif self.etape == 'verifier':
            occupe = any(any(m in (s.get('activite') or '') for m in ('Mirror', 'Scan', 'Trap', 'Check'))
                         for s in membres(etat, self.verifieur))
            self.verif_vue = self.verif_vue or occupe
            if p.get('piege') or (self.verif_vue and not occupe and attente > 2.0) or attente > ATTENTE_VERIF_S:
                self.verifiees.add(self.porte)
                if p.get('piege'):   # found one: disarm it before anything else
                    self._envoyer(envoyer, 'porte', 'desamorcer', self.equipe, self.porte)
                    self._passer('desamorcer', maintenant)
                else:
                    self._empiler(p, envoyer, maintenant)
        elif self.etape == 'desamorcer':
            if not p.get('piege'):
                self._empiler(p, envoyer, maintenant)
            elif attente > 25:
                self.echec = 'piege pas desamorce'
        elif self.etape == 'empiler':
            if place and all('Stacked' in a for a in place):
                if self.grenade == 'aucune':
                    self._envoyer(envoyer, 'porte', 'entrer', self.equipe, self.porte, self.methode, 'aucune')
                    self._passer('entrer', maintenant)
                else:
                    self._envoyer(envoyer, 'porte', 'entrer', self.equipe, self.porte, self.methode, 'aucune',
                                  'sans_nettoyage')
                    self._passer('forcer', maintenant)
            elif attente > ATTENTE_PLACE_S:
                if self.relais:
                    self.echec = "aucune equipe n'arrive en place"
                else:   # one teammate never reaches his spot: the other team takes over
                    self.relais, self.equipe = True, AUTRE[self.equipe]
                    self._envoyer(envoyer, 'swat', 'suivre', AUTRE[self.equipe])
                    self._empiler(p, envoyer, maintenant)
        elif self.etape == 'entrer':
            if any(':Clear' in a for a in place) or p['ouverte'] or p['cassee']:
                self._passer('nettoyer', maintenant)
            elif attente > ATTENTE_PORTE_S:
                self.echec = 'porte pas ouverte'
        elif self.etape == 'forcer':
            if p['ouverte'] or p['cassee']:
                # in game (02/11) the entry team went in as the flashbang went off: it holds at the door first
                self._envoyer(envoyer, 'swat', 'tenir', self.equipe)
                self._grenade_ou_nettoyer(etat, envoyer, maintenant)
            elif attente > ATTENTE_PORTE_S:
                self.echec = 'porte pas forcee'
        elif self.etape == 'grenade':
            if attente > ATTENTE_GRENADE_S:   # the flashbang has gone off: in they go (the bridge lifts the hold)
                self._envoyer(envoyer, 'porte', 'nettoyer', self.equipe, self.porte)
                self._passer('nettoyer', maintenant)
        elif self.etape == 'nettoyer':
            occupes = any(('Clear' in a or 'Search' in a) for a in place)
            self.fouille_vue = self.fouille_vue or occupes
            if not self.fouille_vue and attente > 5.0 and not self.relance:
                # in game (02/11) the clearing order right after the hold did not take: once more
                self.relance = True
                self._envoyer(envoyer, 'porte', 'nettoyer', self.equipe, self.porte)
                self.t = maintenant
                return self.etape
            # the room is clear once the entry team has searched and stopped (or after a while): mark it
            if (self.fouille_vue and not occupes and attente > 3.0) or attente > ATTENTE_NETTOYAGE_S:
                if a_l_objet(etat, self.equipe, 'chemlight') or a_l_objet(etat, AUTRE[self.equipe], 'chemlight'):
                    qui = self.equipe if a_l_objet(etat, self.equipe, 'chemlight') else AUTRE[self.equipe]
                    self._envoyer(envoyer, 'porte', 'baton', qui, self.porte)
                self.fini = True
        return self.etape

    def _empiler(self, p, envoyer, maintenant):
        if p['verrouillee'] and self.methode == 'ouvrir':
            self.methode = 'enfoncer'   # a locked door does not open: kick it
        self._envoyer(envoyer, 'porte', 'empiler', self.equipe, self.porte)
        self._passer('empiler', maintenant)

    def _grenade_ou_nettoyer(self, etat, envoyer, maintenant):
        if self.grenade == 'aucune':
            self._envoyer(envoyer, 'porte', 'nettoyer', self.equipe, self.porte)
            self._passer('nettoyer', maintenant)
            return
        # the other team throws (the entry team stays in its spots), unless only the entry team has one left
        lanceur = AUTRE[self.equipe] if a_l_objet(etat, AUTRE[self.equipe], self.grenade) else self.equipe
        self._envoyer(envoyer, 'porte', 'grenade', lanceur, self.porte, self.grenade)
        self._passer('grenade', maintenant)
