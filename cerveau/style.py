"""How Wal plays: lethal or less-lethal. The AI team follows his style (Wal, 02/11: "l'IA doit regarder comment je
joue et quel est mon équipement pour savoir si on joue en tuant les gens ou en non-létal").

Two clues, from the bridge's "style" field:
- his loadout: only less-lethal weapons -> clearly less-lethal; only lethal ones -> lethal; both -> no clue;
- what he actually fires: the share of his shots made with less-lethal weapons (weighs more as shots add up).
The mode changes only on a clear trend (hysteresis), so one shot of the other kind does not swap the team's rules.
"""

LETAL, NON_LETAL, MIXTE = 'letal', 'non_letal', 'mixte'
SEUIL_NON_LETAL, SEUIL_LETAL = 0.65, 0.35   # score >= -> less-lethal ; <= -> lethal ; between: keep the mode
TIRS_PLEINS = 10                           # from this many shots, the shots weigh twice the loadout


def indice_equipement(s):
    """0..1 from the loadout (1 = only less-lethal weapons), or None with no weapon seen."""
    nl, le = len(s.get('non_letaux', [])), len(s.get('letaux', []))
    if nl and not le:
        return 0.9
    if le and not nl:
        return 0.15
    if nl and le:
        return 0.5
    return None


def score(s):
    """0..1, the less-lethal share of his play: the loadout, then his shots as they add up."""
    poids, total = [], []
    eq = indice_equipement(s)
    if eq is not None:
        poids.append(1.0)
        total.append(eq)
    nl, le = s.get('tirs_non_letaux', 0), s.get('tirs_letaux', 0)
    if nl + le:
        w = 2.0 * min(1.0, (nl + le) / TIRS_PLEINS)
        poids.append(w)
        total.append(nl / (nl + le))
    if not poids:
        return 0.5
    return sum(p * t for p, t in zip(poids, total)) / sum(poids)


class Style:
    def __init__(self):
        self.mode, self.dernier = None, None

    def mise_a_jour(self, etat):
        """The team's mode for this game state; keeps it when the evidence is in between, or missing (in game,
        02/11, the player down or between lives: no weapon seen, and the mode fell to "mixed" for no reason)."""
        s = etat.get('style') or {}
        indices = indice_equipement(s) is not None or s.get('tirs_non_letaux', 0) + s.get('tirs_letaux', 0) > 0
        if not indices:
            return self.mode or MIXTE
        self.dernier = round(score(s), 2)
        if self.mode is None:   # first look: from the loadout and what he already fired
            self.mode = NON_LETAL if self.dernier >= 0.6 else LETAL if self.dernier <= 0.4 else MIXTE
        elif self.dernier >= SEUIL_NON_LETAL:
            self.mode = NON_LETAL
        elif self.dernier <= SEUIL_LETAL:
            self.mode = LETAL
        elif self.mode != MIXTE and abs(self.dernier - 0.5) < 0.05:
            self.mode = MIXTE
        return self.mode


def description(mode, s):
    """One line for the model about the leader's style."""
    s = s or {}
    nl, le = s.get('tirs_non_letaux', 0), s.get('tirs_letaux', 0)
    tirs = f" ({nl} tirs non létaux, {le} tirs mortels)" if nl + le else ''
    if mode == NON_LETAL:
        return ("Le chef joue en NON-LÉTAL" + tirs + " : arrête les suspects vivants (crier, non-létal, grenades "
                "aveuglantes ou à billes) ; tir mortel SEULEMENT si un suspect menace (arme levée ou a tiré).")
    if mode == LETAL:
        return "Le chef joue avec des armes mortelles" + tirs + " : tir permis sur un suspect armé qui ne se rend pas."
    return "Le chef mélange armes mortelles et non létales" + tirs + " : non-létal d'abord quand c'est possible."
