# Ready or Not — IA vocale (bêta)

*[English below](#english)*

**Page Nexus Mods** : https://www.nexusmods.com/readyornot/mods/9149 · **Discord** : demonwal51

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

Ouvre une « Issue » sur GitHub, ou écris dans l'onglet Bugs de la page Nexus (ou à demonwal51 sur Discord), avec : ce que tu as dit, ce qui s'est passé, et les
fichiers `logs\evenements.jsonl` et `pont\WalPont.log` du dossier du mod. Si le jeu a planté : le dossier le plus
récent de `%LOCALAPPDATA%\ReadyOrNot\Saved\Crashes`.

## On cherche des voix !

Tu fais du doublage, du théâtre, ou tu as simplement une voix qui en jette ? On cherche des doubleurs et doubleuses,
en français et en anglais, pour donner une vraie voix aux suspects, aux civils et à l'équipe. Avec ton accord, l'IA
apprend ta voix à partir de quelques enregistrements, et les personnages parlent ensuite avec elle en jeu. Ta voix
sert uniquement à ce mod, ton nom est crédité ici, et tu peux te retirer quand tu veux. Intéressé ? Écris-moi sur
Discord (**demonwal51**) ou en message privé sur Nexus.

## Crédits et outils utilisés

Ce mod tient debout grâce au travail de toutes ces personnes. Merci à elles. Chaque outil garde sa propre licence.

**Le jeu**
- *Ready or Not*, de VOID Interactive. L'équipe parle avec les voix enregistrées du jeu, et le mod lit les
  sous-titres depuis ta propre copie : rien du jeu n'est copié dans le mod. Merci aux comédiens qui ont prêté leur voix.

**Pour faire marcher le mod dans le jeu**
- [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS), par l'équipe UE4SS-RE (créé par Narknon) — MIT, fourni dans `tiers/`.
- [repak](https://github.com/trumank/repak), par trumank et spuds — MIT / Apache 2.0 — pour lire les sous-titres du jeu.

**Pour comprendre ta voix**
- [Whisper](https://github.com/openai/whisper), par OpenAI (modèle `large-v3-turbo`) — MIT.
- La version CTranslate2 de ce modèle, préparée par [Mobius Labs](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo) — MIT.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper), par SYSTRAN (Guillaume Klein) — MIT.
- [CTranslate2](https://github.com/OpenNMT/CTranslate2), par OpenNMT — MIT.
- Les bibliothèques CUDA de NVIDIA (cuBLAS, cuDNN), pour la carte graphique — licence NVIDIA.
- [sounddevice](https://github.com/spatialaudio/python-sounddevice), par Matthias Geier, avec PortAudio — MIT — pour le micro.

**Pour réfléchir**
- [Qwen3.5 4B](https://github.com/QwenLM), par l'équipe Qwen d'Alibaba Cloud — Apache 2.0.
- [Ollama](https://ollama.com), qui fait tourner le modèle sur ton PC — MIT.

**Pour répondre à voix haute**
- [Piper](https://github.com/OHF-Voice/piper1-gpl), créé par Michael Hansen, aujourd'hui entretenu par l'Open Home
  Foundation — GPL-3.0 (installé par pip, pas fourni dans le dépôt).
- espeak-ng (GPL-3.0) et ONNX Runtime de Microsoft (MIT), utilisés par Piper.
- La voix française `fr_FR-siwis-medium`, entraînée sur la base SIWIS de Pierre-Edouard Honnet, Alexandros Lazaridis,
  Philip N. Garner et Junichi Yamagishi (Université d'Édimbourg et Idiap) — CC BY 4.0.
- La voix anglaise `en_US-lessac-medium`, entraînée sur les enregistrements de Voice Factory et Lessac Technologies
  (Blizzard Challenge 2013, Université d'Édimbourg) — usage non commercial.
- Les deux voix sont téléchargées depuis [piper-voices](https://huggingface.co/rhasspy/piper-voices).
- [FFmpeg](https://ffmpeg.org/), pour varier le ton des voix, seulement s'il est déjà sur ton PC.

**Et aussi**
- Python (Python Software Foundation), NumPy, et Hugging Face qui héberge les modèles et les voix.
- **Fait avec une IA** : le code a été écrit avec Claude (Anthropic), dans Claude Code, sous la direction de
  Demonwal51, et essayé en jeu à chaque étape.

**Open source, licence MIT** (voir `LICENSE`) : tu peux l'utiliser, le modifier et le partager librement en citant
l'auteur. Mod gratuit, sans but commercial, non affilié à VOID Interactive ni aux auteurs cités.

---

<a name="english"></a>
## English

**Nexus Mods page**: https://www.nexusmods.com/readyornot/mods/9149 · **Discord**: demonwal51

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

**Report a problem**: open a GitHub issue, or post in the Bugs tab of the Nexus page (or message demonwal51 on
Discord), with what you said, what happened, and the files `logs\evenements.jsonl` and `pont\WalPont.log`.

### We're looking for voices!

Do you do voice acting, theatre, or just have a voice that stands out? We're looking for voice actors and actresses,
in English and French, to give a real voice to the suspects, the civilians and the team. With your permission, the AI
learns your voice from a few recordings, and the characters then speak with it in game. Your voice is used only for
this mod, your name is credited here, and you can step out whenever you want. Interested? Message me on Discord
(**demonwal51**) or privately on Nexus.

### Credits and tools used

This mod stands on the work of all these people. Thank you. Each tool keeps its own license.

- **The game**: *Ready or Not*, by VOID Interactive. The team speaks with the game's recorded voices, and the mod reads
  the subtitles from your own copy: nothing from the game is copied into the mod. Thanks to the actors who lent their voices.
- **Running inside the game**: [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS), by the UE4SS-RE team (created by
  Narknon) — MIT, included in `tiers/`; [repak](https://github.com/trumank/repak), by trumank and spuds — MIT /
  Apache 2.0 — to read the game's subtitles.
- **Understanding your voice**: [Whisper](https://github.com/openai/whisper), by OpenAI (`large-v3-turbo`) — MIT; its
  CTranslate2 version by [Mobius Labs](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo) — MIT;
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper), by SYSTRAN (Guillaume Klein) — MIT;
  [CTranslate2](https://github.com/OpenNMT/CTranslate2), by OpenNMT — MIT; NVIDIA's CUDA libraries (cuBLAS, cuDNN) —
  NVIDIA license; [sounddevice](https://github.com/spatialaudio/python-sounddevice), by Matthias Geier, with
  PortAudio — MIT.
- **Thinking**: [Qwen3.5 4B](https://github.com/QwenLM), by Alibaba Cloud's Qwen team — Apache 2.0;
  [Ollama](https://ollama.com) — MIT.
- **Answering out loud**: [Piper](https://github.com/OHF-Voice/piper1-gpl), created by Michael Hansen, now maintained
  by the Open Home Foundation — GPL-3.0 (installed by pip, not shipped in this repository), with espeak-ng (GPL-3.0)
  and Microsoft's ONNX Runtime (MIT); the French voice `fr_FR-siwis-medium`, trained on the SIWIS database by
  Pierre-Edouard Honnet, Alexandros Lazaridis, Philip N. Garner and Junichi Yamagishi (University of Edinburgh and
  Idiap) — CC BY 4.0; the English voice `en_US-lessac-medium`, trained on recordings by Voice Factory and Lessac
  Technologies (Blizzard Challenge 2013, University of Edinburgh) — non-commercial use; both downloaded from
  [piper-voices](https://huggingface.co/rhasspy/piper-voices); [FFmpeg](https://ffmpeg.org/), to vary the pitch of
  the voices, only if it is already on your PC.
- **And also**: Python (Python Software Foundation), NumPy, and Hugging Face, which hosts the models and voices.
- **Made with an AI**: the code was written with Claude (Anthropic), in Claude Code, directed by Demonwal51 and tried
  in game at each step.

**Open source, MIT license** (see `LICENSE`): use, change and share it freely as long as you credit the author. Free
mod, non-commercial, not affiliated with VOID Interactive or any of the authors above.
