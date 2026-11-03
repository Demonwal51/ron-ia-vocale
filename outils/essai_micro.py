"""Interactive setup with the player: pick the microphone by measurement, then the push-to-talk key. Writes
config.json (micro.bat runs it).

usage: python -I outils/essai_micro.py
"""
import ctypes
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import micro  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config.json'
user32 = ctypes.windll.user32
NOMS_SOURIS = {2: 'clic droit', 4: 'clic molette', 5: 'bouton latéral de la souris (arrière)',
               6: 'bouton latéral de la souris (avant)'}
ORDRE_API = {'MME': 0, 'Windows DirectSound': 1, 'Windows WASAPI': 2}   # WASAPI gives silence on the PD100X


def nom_touche(vk):
    if vk in NOMS_SOURIS:
        return NOMS_SOURIS[vk]
    buf = ctypes.create_unicode_buffer(64)
    scan = user32.MapVirtualKeyW(vk, 0)
    if scan and user32.GetKeyNameTextW(scan << 16, buf, 64):
        return buf.value
    return f'touche {vk}'


def choisir_micro():
    liste = micro.entrees()
    print(f"\n=== 1. LE MICRO ({len(liste)} entrées à écouter, environ {len(liste) * 0.7:.0f} s) ===")
    input("Quand tu appuies sur Entrée, PARLE SANS T'ARRÊTER (compte à voix haute) jusqu'à ce que je dise STOP. Entrée...")
    print("Parle !")
    mesures = []
    for i, nom, api in liste:
        mesures.append((micro.sonder(i), i, nom, api))
    print("STOP, merci.\n")
    # Windows' default-device aliases and virtual cables (they can carry music or Discord) are listed, never chosen
    vivants = [m for m in mesures if m[0] is not None and m[0] > 1e-3
               and not any(x in m[2] for x in ('Mappeur', 'Pilote de capture', 'CABLE'))]
    for rms, i, nom, api in sorted(mesures, key=lambda m: -(m[0] or 0)):
        niveau = "n'ouvre pas" if rms is None else f"{rms:.4f}" + ("  <- t'entend" if rms > 1e-3 else "")
        print(f"  {nom[:45]:45} [{api}]  {niveau}")
    if not vivants:
        print("\nAucune entrée ne t'a entendu. Vérifie que le micro est branché et non coupé, puis relance.")
        sys.exit(1)
    # the loudest device name wins; among its host APIs, prefer MME
    meilleur_nom = max(vivants, key=lambda m: m[0])[2]
    rms, i, nom, api = min((m for m in vivants if m[2] == meilleur_nom), key=lambda m: ORDRE_API.get(m[3], 9))
    print(f"\nMicro choisi : {nom} [{api}], niveau {rms:.4f}")
    return {'nom': nom, 'api': api}


def choisir_touche():
    print("\n=== 2. LA TOUCHE POUR PARLER ===")
    print("Appuie UNE FOIS sur la touche (ou le bouton de souris) que tu veux tenir pour parler en jeu.")
    print("Pas le clic gauche. Choisis une touche que Ready or Not n'utilise pas.")
    time.sleep(0.5)
    deja = {vk for vk in range(1, 255) if user32.GetAsyncKeyState(vk) & 0x8000}
    while True:
        for vk in range(2, 255):
            if vk in (0x10, 0x11, 0x12, 0x0D):      # generic Shift/Ctrl/Alt (the left/right codes stay) and Enter
                continue
            appuye = user32.GetAsyncKeyState(vk) & 0x8000
            if appuye and vk not in deja:
                print(f"Touche choisie : {nom_touche(vk)} (code {vk})")
                return vk
            if not appuye:
                deja.discard(vk)
        time.sleep(0.01)


def main():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    cfg['micro'] = choisir_micro()
    cfg['touche_vk'] = choisir_touche()
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    relu = json.loads(CONFIG.read_text(encoding='utf-8'))
    if relu['micro'] != cfg['micro'] or relu['touche_vk'] != cfg['touche_vk']:
        sys.exit("ÉCHEC : config.json relu ne contient pas le nouveau réglage")
    print(f"\nC'est enregistré dans config.json. Indice actuel du micro : {micro.trouver(relu['micro'])}")


if __name__ == '__main__':
    main()
