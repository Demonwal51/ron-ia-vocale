"""Copy the game's French subtitles of every voice (VO_PC/<voice>/sub_fr.csv) out of its .pak files into
dumps/sous_titres/<voice>.csv, read-only on the game (repak list, then repak get). Then the AI can know what each
recorded line says (the installer runs it on the player's own copy of the game; nothing of the game is shipped).
usage: python -I outils/extraire_sous_titres.py <the game's Content\\Paks folder> <repak.exe> [<destination folder>]
"""
import pathlib
import subprocess
import sys

PAKS, REPAK = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
DEST = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else pathlib.Path(__file__).resolve().parent.parent / 'dumps' / 'sous_titres'
SANS_FENETRE = subprocess.CREATE_NO_WINDOW

DEST.mkdir(parents=True, exist_ok=True)
faits, rates, vus = 0, [], 0
for pak in sorted(PAKS.glob('*.pak')):
    r = subprocess.run([str(REPAK), 'list', str(pak)], capture_output=True, text=True, encoding='utf-8', errors='replace',
                       creationflags=SANS_FENETRE, timeout=300)
    for chemin in (l.strip() for l in r.stdout.splitlines()):
        if not chemin.endswith('/sub_fr.csv') or '/VO_PC/' not in chemin:
            continue
        vus += 1
        voix = chemin.split('/VO_PC/')[1].split('/')[0]
        g = subprocess.run([str(REPAK), 'get', str(pak), chemin], capture_output=True, creationflags=SANS_FENETRE, timeout=60)
        if g.returncode != 0 or not g.stdout.lstrip(b'\xef\xbb\xbf').startswith(b'Key,'):   # the SWAT files have a BOM
            rates.append(voix)
            continue
        (DEST / f'{voix}.csv').write_bytes(g.stdout)   # bytes as stored: UTF-8 text
        faits += 1
relus = sum(1 for f in DEST.glob('*.csv') if f.read_bytes().lstrip(b'\xef\xbb\xbf').startswith(b'Key,'))
print(f'{vus} fichiers de sous-titres vus, {faits} voix extraites, {relus} fichiers valides dans {DEST}, '
      f'{len(rates)} ratees {rates[:5]}')
sys.exit(0 if faits and relus >= faits else 1)
