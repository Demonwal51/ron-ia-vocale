"""The teammates speak, with the game's own recorded lines (names from the game files, VO_PC/SWAT*/).

Only at moments that tell Wal something: they acknowledge his orders, say how they enter, call the grenade and
the chemlight, and say when the gear he asked for is missing. Never more than one line every ESPACE_S, and a
given teammate at most every ESPACE_MEMBRE_S, so they do not chatter.
"""
import math
import time

ESPACE_S, ESPACE_MEMBRE_S = 2.0, 5.0
# Lines are file names of the game (VO_PC/<voice>/[RESPONSE]RogerGeneric_03.ogg) without the number, said through
# the character's PlayRawVO (WalPont "parler ... raw"). In game (02/11), 3 runs, the game's sound before/after:
# PlayRawVO x1.5 (a made-up name x1.04), the bark function x1.0 (nothing); then 19 lines, 2 runs each: all heard.
ACCUSE = '[RESPONSE]RogerGeneric'
APPEL = '[RESPONSE]Command'        # "Alpha !" alone: he answers, waiting for the order
# Wal's orders get an answer that says what they do (Wal, 02/11: "quand ils ont reçu l'ordre ils doivent dire un truc")
PAR_ORDRE = {'swat_suivre': '[RESPONSE]Fallin', 'swat_tenir': '[RESPONSE]Hold', 'swat_reprendre': '[RESPONSE]RogerGeneric',
             'swat_couvrir': '[RESPONSE]Cover', 'swat_aller': '[RESPONSE]MoveTo', 'swat_fouiller': '[RESPONSE]RogerGeneric',
             'swat_menotter': '[CALL]ArrestingSuspect'}
EMPILER = '[RESPONSE]StackUp'
# the game's less-lethal lines exist for few voices (4 files for beanbag, pepper, spray): in game (02/11) taser was
# heard 2 times in 6, the others never; "Roger" every time. So a less-lethal order is answered "Roger".
NON_LETAL = {'taser': ACCUSE, 'beanbag': ACCUSE, 'pepperball': ACCUSE, 'spray': ACCUSE}
PAR_ACTION = {'avancer': '[RESPONSE]MoveTo', 'aller_vers': '[RESPONSE]MoveTo', 'contourner': '[RESPONSE]MoveTo',
              'replacer': '[RESPONSE]MoveTo', 'couvrir': '[RESPONSE]Cover', 'surveiller': '[RESPONSE]Cover',
              'menotter': '[CALL]ArrestingSuspect'}
ENTREE = {'ouvrir': '[RESPONSE]OpenClear', 'enfoncer': '[RESPONSE]BreachKick', 'fusil': '[RESPONSE]BreachShotgun',
          'c2': '[RESPONSE]BreachC2', 'belier': '[RESPONSE]BreachKick'}
GRENADE = {'flash': '[RESPONSE]DeployFlashbang', 'stinger': '[RESPONSE]DeployStinger', 'ninebang': '[RESPONSE]DeployFlashbang'}
PORTE_FORCEE = '[CALL]BreachDone'
BATON = '[RESPONSE]DeployChemlight'
CONTACT = '[CALL]SpottedSuspect1'
MANQUE = {'c2': '[RESPONSE]NegativeNoC2', 'fusil_breche': '[RESPONSE]NegativeNoShotgun',
          'flash': '[RESPONSE]NegativeNoFlashbang', 'stinger': '[RESPONSE]NegativeNoStinger',
          'chemlight': '[RESPONSE]NegativeNoChemlight'}


class Annonces:
    def __init__(self):
        self.dernier, self.par_membre = float('-inf'), {}   # nothing said yet (0 would read "just now" at time 0)

    def dire(self, etat, ligne, membre=None, maintenant=None, prioritaire=False, equipe_code=None):
        """The bridge command for a teammate (his game name, else the one closest to the leader, in team
        equipe_code if given) to say a line, or None when it is too soon, or nobody can speak.
        prioritaire: the answer to an order of Wal's always goes (in game, 02/11 design: after "Alpha !" -> "Roger",
        the answer to the order that followed was cut by the 5 s spacing)."""
        maintenant = time.time() if maintenant is None else maintenant
        if not ligne or (not prioritaire and maintenant - self.dernier < ESPACE_S):
            return None
        equipe = [s for s in etat.get('swat', []) if 'Trailer' not in s.get('classe', '') and not s['hors_combat']]
        if equipe_code in (1, 2) and any(s.get('equipe') == equipe_code for s in equipe):
            equipe = [s for s in equipe if s.get('equipe') == equipe_code]
        if membre is None:
            chef = next((j for j in etat.get('joueurs', []) if j['moi'] and 'pos' in j), None)
            libres = [s for s in equipe if prioritaire
                      or maintenant - self.par_membre.get(s['nom'], float('-inf')) >= ESPACE_MEMBRE_S]
            if not libres:
                return None
            s = min(libres, key=lambda s: math.dist(s['pos'], chef['pos']) if chef else 0)
        else:
            s = next((x for x in equipe if x['nom'] == membre), None)
            if s is None or (not prioritaire
                             and maintenant - self.par_membre.get(s['nom'], float('-inf')) < ESPACE_MEMBRE_S):
                return None
        self.dernier = self.par_membre[s['nom']] = maintenant
        return f"parler {s['nom']} {ligne} raw"


def manque_dans(raison):
    """The missing gear named by a refusal of the guard ("personne n'a de fusils de brèche"), as a stock key."""
    for cle, mots in (('c2', 'C2'), ('fusil_breche', 'fusils de br'), ('flash', 'aveuglantes'),
                      ('stinger', 'stinger'), ('chemlight', 'lumineux')):
        if mots in (raison or ''):
            return cle
    return None
