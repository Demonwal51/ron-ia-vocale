"""The installer for players (run by installer.bat with Python 3.12, standard library only).

Each step is skipped when already done, so it can be run again after fixing something:
1. checks: Windows, an NVIDIA card and its video memory;
2. the program's Python environment (.venv) and its packages (requirements.txt);
3. Ollama (installed by the player) and the AI model, in the project's own folder (modeles/), on the project's own
   server (port 11435): qwen3.5:4b, then its text-only twin (outils/modele_texte.py);
4. Whisper (speech to text) and the two Piper voices (French, English), downloaded once;
5. Ready or Not found through Steam, then UE4SS + the bridge mod added to it (files added only, listed in
   manifeste_ue4ss.json, removed by desinstaller.bat);
6. config.json: the language.

usage: python outils/installer.py [--auto] [--jeu <Win64 folder>] [--langue fr|en|auto] [--sans-telechargement]
  --auto: no question (the defaults); --sans-telechargement: skip the model and voice downloads (a test).
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENV_PY = ROOT / '.venv' / 'Scripts' / 'python.exe'
MODELE, MODELE_TEXTE = 'qwen3.5:4b', 'qwen3.5:4b-texte'
SERVEUR = 'http://127.0.0.1:11435'
UE4SS_ZIP = ROOT / 'tiers' / 'UE4SS_v3.0.1-1195-g1ceefac8.zip'
MANIFESTE = ROOT / 'manifeste_ue4ss.json'
VOIX = {'fr_FR-siwis-medium': 'fr/fr_FR/siwis/medium', 'en_US-lessac-medium': 'en/en_US/lessac/medium'}
DEPOT_VOIX = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/'
REPAK_URL = 'https://github.com/trumank/repak/releases/download/v0.2.3/repak_cli-x86_64-pc-windows-msvc.zip'
JEU_RELATIF = pathlib.Path('steamapps') / 'common' / 'Ready Or Not' / 'ReadyOrNot' / 'Binaries' / 'Win64'
SANS_FENETRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

args = sys.argv[1:]
AUTO = '--auto' in args
SANS_TELECHARGEMENT = '--sans-telechargement' in args


def option(nom, defaut=None):
    return args[args.index(nom) + 1] if nom in args and args.index(nom) + 1 < len(args) else defaut


def etape(titre):
    print(f'\n=== {titre}', flush=True)


def demander(question, defaut):
    if AUTO:
        return defaut
    r = input(f'{question} [{defaut}] : ').strip()
    return r or defaut


def arreter(message):
    print(f'\nARRÊT : {message}\nCorrige ce point puis relance installer.bat (les étapes déjà faites sont sautées).')
    sys.exit(1)


def lancer(cmd, **kw):
    """Run a command, its output shown; True when it worked."""
    return subprocess.run(cmd, creationflags=SANS_FENETRE, **kw).returncode == 0


def telecharger(url, cible):
    """Download to a .part file, then rename (a cut download never looks finished)."""
    cible.parent.mkdir(parents=True, exist_ok=True)
    part = cible.with_name(cible.name + '.part')
    with urllib.request.urlopen(url, timeout=60) as r, open(part, 'wb') as f:
        total, fait, dernier = int(r.headers.get('Content-Length') or 0), 0, 0
        while True:
            bloc = r.read(1 << 20)
            if not bloc:
                break
            f.write(bloc)
            fait += len(bloc)
            if total and fait - dernier > total / 10:
                dernier = fait
                print(f'   {cible.name} : {fait * 100 // total} %', flush=True)
    part.replace(cible)


# ---- 1. the PC ----
etape('1. Le PC')
if os.name != 'nt':
    arreter('ce mod ne marche que sous Windows.')
try:
    out = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                         capture_output=True, text=True, creationflags=SANS_FENETRE).stdout.strip().splitlines()
    nom, mio = [x.strip() for x in out[0].split(',')]
    print(f'   carte graphique : {nom}, {int(mio) / 1024:.1f} Go de mémoire vidéo')
    if int(mio) < 12 * 1024:
        print('   ATTENTION : moins de 12 Go. Le jeu + l\'IA ont pris jusqu\'à 11,3 Go en 2560x1440 (mesuré sur une RTX 5080) :'
              '\n   baisse la résolution ou les textures du jeu si ça rame.')
except (OSError, IndexError, ValueError):
    arreter('pas de carte NVIDIA trouvée (nvidia-smi). Il faut une carte NVIDIA avec ses pilotes à jour.')
if sys.version_info[:2] != (3, 12):
    arreter(f'il faut Python 3.12 (celui-ci est {sys.version.split()[0]}) : https://www.python.org/downloads/')

# ---- 2. the program's Python ----
etape('2. L\'environnement Python du programme (.venv)')
if not VENV_PY.exists():
    print('   création de .venv')
    if not lancer([sys.executable, '-m', 'venv', str(ROOT / '.venv')]):
        arreter('impossible de créer .venv.')
print('   installation des paquets (requirements.txt, environ 2 Go la première fois)')
if not lancer([str(VENV_PY), '-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', str(ROOT / 'requirements.txt')]):
    arreter('pip n\'a pas pu installer les paquets (connexion internet ?).')
print('   ok')

# ---- 3. Ollama and the model ----
etape('3. Ollama et le modèle d\'IA (qwen3.5:4b, 3,3 Go)')
ollama = option('--ollama')
if not ollama:
    trouve = subprocess.run(['where', 'ollama.exe'], capture_output=True, text=True, creationflags=SANS_FENETRE).stdout.split()
    defaut = pathlib.Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe'
    ollama = trouve[0] if trouve else (str(defaut) if defaut.exists() else None)
if not ollama or not pathlib.Path(ollama).exists():
    arreter('Ollama n\'est pas installé. Installe-le depuis https://ollama.com/download (il n\'a pas besoin de tourner).')
print(f'   Ollama : {ollama}')
config_f = ROOT / 'config.json'
config = json.loads(config_f.read_text(encoding='utf-8')) if config_f.exists() else {}
config['ollama_exe'] = ollama
config_f.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
if not lancer(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File',
               str(ROOT / 'cerveau' / 'ollama_projet.ps1')]):
    arreter('le serveur d\'IA du projet n\'a pas démarré (voir cerveau/ollama_projet.log).')


def modeles_presents():
    with urllib.request.urlopen(SERVEUR + '/api/tags', timeout=10) as r:
        return {m['name'] for m in json.load(r).get('models', [])}


if SANS_TELECHARGEMENT:
    print('   (téléchargement sauté : --sans-telechargement)')
elif MODELE_TEXTE in modeles_presents():
    print(f'   {MODELE_TEXTE} déjà là')
else:
    if MODELE not in modeles_presents():
        print(f'   téléchargement de {MODELE}...')
        req = urllib.request.Request(SERVEUR + '/api/pull', data=json.dumps({'model': MODELE, 'stream': True}).encode(),
                                     headers={'Content-Type': 'application/json'})
        dernier = ''
        with urllib.request.urlopen(req, timeout=3600) as r:
            for ligne in r:
                e = json.loads(ligne or b'{}')
                if e.get('error'):
                    arreter(f'Ollama : {e["error"]}')
                etat = e.get('status', '')
                if e.get('total') and e.get('completed'):
                    etat += f" {e['completed'] * 100 // e['total']} %"
                if etat != dernier and (etat.endswith('0 %') or '%' not in etat):
                    print(f'   {etat}', flush=True)
                    dernier = etat
    # the same model without its image part (never used here): 0.87 GB less video memory
    if not lancer([str(VENV_PY), '-B', str(ROOT / 'outils' / 'modele_texte.py'), MODELE, MODELE_TEXTE]):
        print(f'   (version texte impossible : on garde {MODELE})')
        config['modele'] = MODELE
    if MODELE_TEXTE not in modeles_presents() and config.get('modele') != MODELE:
        config['modele'] = MODELE
print('   ok')

# ---- 4. Whisper and the voices ----
etape('4. Whisper (la reconnaissance de la voix, 1,6 Go) et les voix Piper (2 x 60 Mo)')
if SANS_TELECHARGEMENT:
    print('   (téléchargements sautés : --sans-telechargement)')
else:
    if not lancer([str(VENV_PY), '-c', "from faster_whisper import download_model; download_model('large-v3-turbo')"]):
        arreter('Whisper n\'a pas pu être téléchargé.')
    for nom, chemin in VOIX.items():
        for ext in ('.onnx', '.onnx.json'):
            f = ROOT / 'modeles' / 'piper' / (nom + ext)
            if not f.exists():
                telecharger(f'{DEPOT_VOIX}{chemin}/{nom}{ext}', f)
print('   ok')

# ---- 5. the game ----
etape('5. Ready or Not : UE4SS et le mod')


def jeux_steam():
    """The Win64 folders of Ready or Not in every Steam library (registry, then libraryfolders.vdf)."""
    racines = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            racines.append(pathlib.Path(winreg.QueryValueEx(k, 'SteamPath')[0]))
    except OSError:
        pass
    racines.append(pathlib.Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Steam')
    biblios = []
    for r in racines:
        vdf = r / 'steamapps' / 'libraryfolders.vdf'
        if vdf.exists():
            biblios += [pathlib.Path(p.replace('\\\\', '\\')) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding='utf-8', errors='replace'))]
        biblios.append(r)
    return [b / JEU_RELATIF for b in dict.fromkeys(biblios) if (b / JEU_RELATIF).exists()]


win64 = option('--jeu')
if not win64:
    trouves = jeux_steam()
    defaut = str(trouves[0]) if trouves else ''
    if trouves:
        print(f'   jeu trouvé : {defaut}')
    win64 = demander('Dossier Win64 du jeu (ReadyOrNot\\Binaries\\Win64)', defaut)
win64 = pathlib.Path(win64)
if not (win64 / 'ReadyOrNotSteam-Win64-Shipping.exe').exists() and not (win64 / 'ReadyOrNot-Win64-Shipping.exe').exists() \
        and '--jeu' not in args:
    arreter(f'je ne trouve pas le jeu dans {win64}.')
if MANIFESTE.exists():   # our own earlier install (in this game or another folder): taken out first
    print('   le mod était déjà installé : réinstallé à jour')
    lancer([str(VENV_PY), '-I', str(ROOT / 'outils' / 'installer_ue4ss.py'), 'remove', str(MANIFESTE)])
    MANIFESTE.unlink()
if (win64 / 'dwmapi.dll').exists() or (win64 / 'ue4ss').exists():   # then anything left there is not ours
    arreter('un autre UE4SS est déjà installé dans le jeu (dwmapi.dll / ue4ss) : enlève-le d\'abord, ce mod pose le sien.')
extrait = ROOT / 'tiers' / 'UE4SS'
if not (extrait / 'dwmapi.dll').exists():
    with zipfile.ZipFile(UE4SS_ZIP) as z:
        z.extractall(extrait)
if not lancer([str(VENV_PY), '-I', str(ROOT / 'outils' / 'installer_ue4ss.py'), 'install', str(extrait), str(win64),
               str(ROOT / 'mods'), str(MANIFESTE)]):
    arreter('l\'installation dans le jeu a échoué (rien n\'a été écrasé).')

# the game's French subtitles of its recorded lines, from the player's own copy (read-only, repak): the AI then
# knows what each line says and picks the one that answers. Without them the mod works, the game picks the line
sous_titres = ROOT / 'dumps' / 'sous_titres'
paks = win64.parent.parent / 'Content' / 'Paks'   # ReadyOrNot\Binaries\Win64 -> ReadyOrNot\Content\Paks
if any(sous_titres.glob('*.csv')):
    print('   sous-titres du jeu déjà extraits')
elif SANS_TELECHARGEMENT or not paks.exists():
    print('   (sous-titres du jeu sautés)')
else:
    repak = ROOT / 'tiers' / 'repak' / 'repak.exe'
    if not repak.exists():
        print('   téléchargement de repak (2 Mo, github.com/trumank/repak)')
        z = ROOT / 'tiers' / 'repak.zip'
        telecharger(REPAK_URL, z)
        with zipfile.ZipFile(z) as f:
            f.extractall(repak.parent)
        z.unlink()
    print('   extraction des sous-titres du jeu (environ 2 min)')
    if not lancer([str(VENV_PY), '-I', str(ROOT / 'outils' / 'extraire_sous_titres.py'), str(paks), str(repak)]):
        print('   (sous-titres impossibles : le mod marche quand même, le jeu choisira ses répliques)')

# ---- 6. settings ----
etape('6. Réglages')
langue = option('--langue') or demander('Langue de ta voix : fr, en, ou auto (reconnaît chaque phrase)', config.get('langue', 'fr'))
config['langue'] = langue if langue in ('fr', 'en', 'auto') else 'fr'
config.setdefault('modele', MODELE_TEXTE)
config_f.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
relu = json.loads(config_f.read_text(encoding='utf-8'))
if relu.get('langue') != config['langue'] or relu.get('ollama_exe') != ollama:
    arreter('config.json n\'a pas été écrit correctement.')
print(f"   langue : {config['langue']} ; modèle : {config['modele']}")

print('\nInstallation terminée.')
print('Ensuite : 1) micro.bat pour choisir ton micro et ta touche pour parler ;'
      '\n          2) lance Ready or Not, puis lancer.bat ; parle en tenant ta touche.')
