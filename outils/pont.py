"""Talk to the WalPont mod: send an order and wait for its result, or print the game state.

usage: pont.py ordre <commande> [args...]   (prints the result line, exit 1 on ECHEC or timeout)
       pont.py etat                          (short summary of pont/etat.json)
"""
import json
import pathlib
import sys
import time

DIR = pathlib.Path(__file__).resolve().parent.parent / 'pont'   # the project's bridge folder (the mod writes there)
ORDERS, RESULTS, STATE = DIR / 'ordres.txt', DIR / 'resultats.txt', DIR / 'etat.json'


def send(words, timeout=5.0):
    DIR.mkdir(parents=True, exist_ok=True)
    oid = int(time.time() * 1000)
    with open(ORDERS, 'a', encoding='utf-8', newline='\n') as f:
        f.write(f'{oid} {" ".join(words)}\n')
    end = time.time() + timeout
    while time.time() < end:
        if RESULTS.exists():
            for line in RESULTS.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith(f'{oid}|'):
                    return line
        time.sleep(0.05)
    return f'{oid}|ECHEC|pas de reponse en {timeout:.0f} s (mod absent ou jeu fige)'


def state():
    if not STATE.exists():
        return 'pas de etat.json'
    age = time.time() - STATE.stat().st_mtime
    d = json.loads(STATE.read_text(encoding='utf-8'))
    ia = d.get('ia', [])
    lines = [f'carte {d.get("carte")}, joueur {d.get("joueur")}, fps {d.get("fps", -1):.0f}, {len(ia)} IA, etat vieux de {age:.1f} s']
    swat = d.get('swat', [])
    lines.append('  SWAT : ' + ', '.join(f'{s["nom"][-10:]} {s["distance_m"]:.1f} m' for s in swat))
    for e in ia[:10]:
        kind = 'suspect' if e['suspect'] else 'civil' if e['civil'] else '?'
        flags = [k for k in ('hors_combat', 'rendu', 'arrete', 'obeit', 'otage') if e.get(k)]
        lines.append(f'  {e["nom"][:38]:38s} {kind:7s} {e["distance_m"]:6.1f} m vise {e["vise"]:+.2f} vie {e["vie"]:.0f} {" ".join(flags)}')
    return '\n'.join(lines)


if __name__ == '__main__':
    if sys.argv[1] == 'ordre':
        r = send(sys.argv[2:])
        print(r)
        sys.exit(0 if '|ok|' in r else 1)
    print(state())
