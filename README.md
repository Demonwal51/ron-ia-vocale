# Ready or Not — IA vocale (bêta)

*[English below](#english)*

Tu parles au micro, **en français ou en anglais**, et une IA **100 % locale** (sur ta carte graphique, rien ne part
sur internet) comprend ce que tu dis :

- **ton équipe SWAT** t'obéit (« Suivez-moi », « Tenez la position », « Fouillez la pièce », « Menottez-le »,
  « Alpha, couvre la porte », « Entrez avec une flash ») et joue toute seule entre tes ordres, comme une vraie équipe ;
- **les suspects et les civils** ont chacun leur personnalité et réagissent à ce que tu leur cries (« Police ! Lâche
  ton arme ! », « À terre ! », « Où sont les autres ? ») : ils se rendent, hésitent, mentent, résistent ou fuient ;
- **les suspects sont joués par l'IA** : ils connaissent les lieux, tendent des embuscades, se replient, contournent,
  prennent des otages… et **entendent ce que tu prépares** (« on va le contourner », « flash ») et se préviennent ;
- **ils te répondent** avec leurs vraies voix du jeu, et à voix haute quand ils doivent dire un fait (« Ils sont
  trois. Surtout derrière toi. »).

C'est une **bêta** : merci de signaler ce qui ne marche pas (voir « Signaler un problème »).

## Ce qu'il faut

| | |
|---|---|
| Système | Windows 10 ou 11 |
| Carte graphique | **NVIDIA**, au moins **12 Go** de mémoire vidéo (testé sur une RTX 5080 16 Go : jeu + IA = 11,3 Go au plus en 2560x1440, 188 images par seconde en médiane) |
| Jeu | Ready or Not sur Steam (testé sur la version du 01/11/2026, build 25784851) |
| Logiciels | [Python 3.12](https://www.python.org/downloads/) et [Ollama](https://ollama.com/download) |
| Disque | environ 8 Go (paquets Python, modèle d'IA, reconnaissance vocale, voix) |
| Micro | n'importe lequel ; une touche (ou un bouton de souris) pour parler |

Optionnel : [ffmpeg](https://ffmpeg.org/) dans le PATH, pour que chaque personnage ait sa propre hauteur de voix.

## Installer

1. Installe **Python 3.12** (coche « Add python.exe to PATH ») et **Ollama**.
2. Télécharge ce dépôt (bouton vert « Code » → « Download ZIP ») et décompresse-le où tu veux.
3. Double-clique sur **`installer.bat`**. Il fait tout, étape par étape :
   - l'environnement Python du programme et ses paquets ;
   - le modèle d'IA `qwen3.5:4b` (3,3 Go), dans le dossier du mod, sur son propre serveur Ollama (il ne touche pas
     à ton Ollama habituel) ;
   - la reconnaissance vocale Whisper et les voix Piper (française et anglaise) ;
   - **UE4SS** (le chargeur de mods) et le mod, ajoutés au jeu : rien n'est écrasé, tout ce qui est ajouté est noté
     pour la désinstallation ;
   - la langue (`fr`, `en`, ou `auto` pour reconnaître la langue de chaque phrase).

   Si une étape échoue, il dit pourquoi : corrige et relance, les étapes déjà faites sont sautées.
4. Double-clique sur **`micro.bat`** : il trouve ton micro (parle quand il le demande) et la touche pour parler.

## Jouer

1. Lance Ready or Not normalement.
2. Double-clique sur **`lancer.bat`**. La fenêtre affiche « Prêt » au bout de quelques secondes.
3. Tiens ta touche, parle, relâche. Pour arrêter : ferme la fenêtre.

**En coop**, c'est l'**hôte** qui fait tourner le mod (une seule IA, sur son PC) ; les autres n'installent rien. Il
n'y a alors pas de SWAT IA : chacun peut parler aux suspects et aux civils.

## Ce que tu peux dire (exemples)

| Français | English | Effet |
|---|---|---|
| « Suivez-moi » | "Follow me" | l'équipe te suit |
| « Tenez la position » | "Hold position" | l'équipe s'arrête |
| « On y va » | "Let's go" | l'équipe passe devant |
| « Fouillez la pièce » | "Search the room" | elle fouille |
| « Menottez-le » | "Cuff him" | elle menotte celui que tu vises (ou le plus proche qui s'est rendu) |
| « Alpha, couvre la porte » | "Alpha, cover the door" | un ordre à un seul équipier |
| « Entrez avec une flash » | "Breach with a flashbang" | entrée complète par la porte devant toi |
| « Police ! Lâche ton arme ! » | "Police! Drop your weapon!" | le suspect visé réagit selon sa personnalité |
| « À terre ! Mains en l'air ! » | "Get down! Hands up!" | idem |
| « Où sont les autres ? » | "Where are the others?" | il répond : la vérité, un mensonge, ou rien |
| « Ramassez les armes » | "Pick up the weapons" | un équipier ramasse les armes au sol |

## Réglages

Dans `config.json` (créé par l'installateur) : `langue` (`fr`, `en`, `auto`), `tactique` (`false` : l'équipe ne fait
que tes ordres), `adversaires` (`false` : les suspects gardent l'IA du jeu), `ecoute` (`false` : ils n'entendent plus
tes plans), `annonces` (`false` : les équipiers se taisent), `parole` (`false` : pas de réponses à voix haute).

## Désinstaller

Double-clique sur **`desinstaller.bat`** : il retire du jeu exactement les fichiers ajoutés (UE4SS et le mod), le jeu
redevient d'origine. Supprime ensuite le dossier du mod.

## Limites connues (bêta)

- Le **ramassage des armes** par l'équipe n'est pas fiable (environ 1 arme sur 4) : ramasse-les toi-même au besoin.
  Celles des suspects menottés, le jeu les ramasse bien.
- En combat, les suspects bougent avec les manœuvres du jeu : « fuir » va où le jeu veut.
- Une prise d'otage réussit environ une fois sur deux (c'est le jeu qui décide).
- Le jeu a planté quelques fois sur la carte du fast-food pendant nos essais, cause non trouvée.
- Testé sur un seul PC : d'autres cartes, d'autres résolutions et la vraie coop n'ont pas encore été essayées.
- Les fenêtres du programme sont en français.

## Signaler un problème

Ouvre une « Issue » sur GitHub (ou contacte-moi sur Discord) avec : ce que tu as dit, ce qui s'est passé, et les
fichiers `logs\evenements.jsonl` et `pont\WalPont.log` du dossier du mod. Si le jeu a planté : le dossier le plus
récent de `%LOCALAPPDATA%\ReadyOrNot\Saved\Crashes`.

## Crédits

- [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) (licence MIT, fourni dans `tiers/`), le chargeur de mods Lua.
- [Ollama](https://ollama.com) et le modèle [Qwen3.5](https://huggingface.co/Qwen) (4B) pour comprendre ;
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper d'OpenAI) pour entendre ;
  [Piper](https://github.com/OHF-Voice/piper1-gpl) et ses voix `fr_FR-siwis-medium` et `en_US-lessac-medium` pour
  parler (téléchargées depuis [piper-voices](https://huggingface.co/rhasspy/piper-voices), selon leurs propres
  licences).
- Les voix des personnages sont celles du jeu, jouées par le jeu lui-même (rien n'est copié).
- **Fait avec une IA** : le code a été écrit avec Claude (Anthropic), dans Claude Code, sous la direction de
  Demonwal51, et essayé en jeu à chaque étape.

**Open source, licence MIT** (voir `LICENSE`) : tu peux l'utiliser, le modifier et le partager librement. Les
outils qu'il installe gardent leurs propres licences (UE4SS : MIT ; Piper, installé par pip : GPL-3.0). Ce mod n'est
pas affilié à VOID Interactive.

---

<a name="english"></a>
## English

You speak into your microphone, **in English or French**, and a **fully local** AI (on your graphics card, nothing
goes online) understands you: **your SWAT team** follows your orders ("Follow me", "Hold position", "Search the
room", "Cuff him", "Alpha, cover the door", "Breach with a flashbang") and plays on its own between them; **suspects
and civilians** each have a personality and react to what you shout ("Police! Drop your weapon!", "Get down!",
"Where are the others?"); **suspects are played by the AI**, know the place, ambush, flank, take hostages, **overhear
your plans** ("we're going to flank him") and warn each other; **they answer** with the game's own voices, and out
loud when they have to state a fact ("There are three. Mostly behind you.").

This is a **beta**: please report what does not work.

**Requirements**: Windows 10/11; an **NVIDIA** card with **12 GB** of video memory or more (tested on an RTX 5080);
Ready or Not on Steam (build of 01/11/2026); [Python 3.12](https://www.python.org/downloads/) and
[Ollama](https://ollama.com/download); about 8 GB of disk. Optional: ffmpeg on the PATH (each character's own pitch).

**Install**: install Python 3.12 (tick "Add python.exe to PATH") and Ollama; download this repository (green "Code"
button → "Download ZIP") and unzip it; run **`installer.bat`** (Python packages, the AI model on its own Ollama
server, Whisper, the Piper voices, UE4SS and the mod added to the game without overwriting anything, the language:
choose `en` or `auto`); run **`micro.bat`** to pick your microphone and push-to-talk key.

**Play**: start Ready or Not, run **`lancer.bat`**, hold your key, speak, release. In co-op, only the **host** runs
the mod.

**Uninstall**: **`desinstaller.bat`** removes exactly the files the install added to the game.

**Known limits**: the team's weapon pick-up is unreliable; in a fight, suspects move with the game's own combat
moves; a hostage taking works about half the time; a few crashes on the fast-food map during our tests; tested on
one PC only; the program's windows are in French.

**Report a problem**: open a GitHub issue with what you said, what happened, and the files `logs\evenements.jsonl`
and `pont\WalPont.log`.

**Credits**: UE4SS (MIT, included in `tiers/`), Ollama + Qwen3.5 4B, faster-whisper, Piper and its voices. The code
was written with an AI (Claude by Anthropic, in Claude Code), directed by Demonwal51 and tried in game at each step.
**Open source, MIT license** (see `LICENSE`); the tools it installs keep their own licenses (UE4SS: MIT; Piper,
installed by pip: GPL-3.0). Not affiliated with VOID Interactive.
