"""Install (or remove) UE4SS + the project's mods into the game's Win64 folder, adding files only. The bridge
mod's copy is told where this project's bridge folder is (pont/).

usage: installer_ue4ss.py install <ue4ss extract dir> <Win64 dir> <mods dir> <manifest.json>
       installer_ue4ss.py remove  <manifest.json>

install refuses to overwrite any existing game file, writes the list of every file it added to the
manifest, then re-reads the result. remove deletes exactly the files of the manifest, nothing else.
"""
import json
import pathlib
import re
import shutil
import sys


def install(src, win64, mods, manifest):
    added = []
    plan = [(src / 'dwmapi.dll', win64 / 'dwmapi.dll')]
    for p in (src / 'ue4ss').rglob('*'):
        if p.is_file():
            plan.append((p, win64 / 'ue4ss' / p.relative_to(src / 'ue4ss')))
    for p in mods.rglob('*'):
        if p.is_file():
            plan.append((p, win64 / 'ue4ss' / 'Mods' / p.relative_to(mods)))
    clash = [str(d) for _, d in plan if d.exists()]
    if clash:
        sys.exit(f'REFUS : fichiers deja presents, rien copie : {clash[:5]}')
    for s, d in plan:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        added.append(str(d))
    manifest.write_text(json.dumps(added, indent=1), encoding='utf-8')

    # the bridge folder, wherever this project was installed (the mod's own copy in the game gets it)
    pont = (pathlib.Path(__file__).resolve().parent.parent / 'pont')
    pont.mkdir(exist_ok=True)
    lua = win64 / 'ue4ss' / 'Mods' / 'WalPont' / 'Scripts' / 'main.lua'
    if lua.exists():
        lua.write_text(lua.read_text(encoding='utf-8').replace('__DOSSIER_PONT__', pont.as_posix() + '/'), encoding='utf-8')

    ini = win64 / 'ue4ss' / 'UE4SS-settings.ini'
    t = ini.read_text(encoding='utf-8')
    t = re.sub(r'(?m)^MajorVersion = *$', 'MajorVersion = 5', t)
    t = re.sub(r'(?m)^MinorVersion = *$', 'MinorVersion = 3', t)
    ini.write_text(t, encoding='utf-8')

    mt = win64 / 'ue4ss' / 'Mods' / 'mods.txt'
    m = mt.read_text(encoding='utf-8')
    names = sorted({p.relative_to(mods).parts[0] for p in mods.rglob('*') if p.is_file()})
    lines = ''.join(f'{n} : 1\n' for n in names)
    m = m.replace('; Built-in keybinds', lines + '\n; Built-in keybinds', 1)
    mt.write_text(m, encoding='utf-8')

    # verification by re-reading
    t2 = ini.read_text(encoding='utf-8')
    m2 = mt.read_text(encoding='utf-8')
    bad = []
    if not re.search(r'(?m)^MajorVersion = 5$', t2) or not re.search(r'(?m)^MinorVersion = 3$', t2):
        bad.append('version moteur non ecrite')
    if not re.search(r'(?m)^ConsoleEnabled = 0$', t2) or not re.search(r'(?m)^GuiConsoleEnabled = 0$', t2):
        bad.append('console non coupee (fenetre possible)')
    for n in names:
        if f'{n} : 1' not in m2:
            bad.append(f'{n} absent de mods.txt')
    if lua.exists() and '__DOSSIER_PONT__' in lua.read_text(encoding='utf-8'):
        bad.append('dossier du pont non ecrit dans le mod')
    missing = [a for a in added if not pathlib.Path(a).exists()]
    if missing:
        bad.append(f'{len(missing)} fichiers manquants')
    print(f'{len(added)} fichiers ajoutes ; mods actives : {names} ; ' + ('ECHEC : ' + ', '.join(bad) if bad else 'verification OK'))
    if bad:
        sys.exit(1)


def remove(manifest):
    added = json.loads(manifest.read_text(encoding='utf-8'))
    n = 0
    for a in added:
        p = pathlib.Path(a)
        if p.exists():
            p.unlink()
            n += 1
    # remove now-empty folders created by the install, deepest first
    dirs = sorted({pathlib.Path(a).parent for a in added}, key=lambda d: len(d.parts), reverse=True)
    for d in dirs:
        while d.exists() and d.name != 'Win64' and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    left = [a for a in added if pathlib.Path(a).exists()]
    print(f'{n} fichiers retires ; ' + (f'ECHEC : {len(left)} restent' if left else 'verification OK'))


if sys.argv[1] == 'install':
    install(*(pathlib.Path(a) for a in sys.argv[2:6]))
elif sys.argv[1] == 'remove':
    remove(pathlib.Path(sys.argv[2]))
