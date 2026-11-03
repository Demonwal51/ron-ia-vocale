"""Make a text-only twin of an Ollama model in the project's folder: the same manifest without the image layer
(application/vnd.ollama.image.projector, 644 MB for qwen3.5:4b). The program only ever sends text, and Ollama loads
the image part on the GPU with the model. Nothing is copied or deleted: both manifests point to the same blobs, so
removing the twin is deleting its manifest file.

usage: python -B outils/modele_texte.py qwen3.5:4b qwen3.5:4b-texte
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIBLIO = ROOT / 'modeles' / 'manifests' / 'registry.ollama.ai' / 'library'
IMAGE = 'application/vnd.ollama.image.projector'


def chemin(nom):
    famille, etiquette = nom.split(':')
    return BIBLIO / famille / etiquette


def main(source, cible):
    m = json.loads(chemin(source).read_text(encoding='utf-8'))
    garde = [c for c in m['layers'] if c['mediaType'] != IMAGE]
    if len(garde) == len(m['layers']):
        sys.exit(f'{source} n a pas de couche image : rien a faire')
    if not any(c['mediaType'] == 'application/vnd.ollama.image.model' for c in garde):
        sys.exit('pas de couche modele : refus')
    m['layers'] = garde
    f = chemin(cible)
    if f.exists():
        sys.exit(f'{f} existe deja : refus')
    f.write_text(json.dumps(m, separators=(',', ':')), encoding='utf-8')
    relu = json.loads(f.read_text(encoding='utf-8'))
    assert all(c['mediaType'] != IMAGE for c in relu['layers']) and len(relu['layers']) == len(garde)
    for c in relu['layers']:
        assert (ROOT / 'modeles' / 'blobs' / c['digest'].replace(':', '-')).exists(), c['digest']
    print(f'{cible} cree : {len(garde)} couches, sans la couche image')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
