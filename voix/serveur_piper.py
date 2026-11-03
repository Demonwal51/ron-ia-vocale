"""The Piper worker for voix/parole.py: loads the French voice once, then for each JSON line on stdin
{"texte": ..., "sortie": "<wav path>"} writes the wav and answers one JSON line {"ok": true, "ms": ...}.
Runs with the Python that has piper-tts (setting piper_python).
usage: <python with piper> -B voix/serveur_piper.py <voice .onnx>
"""
import json
import sys
import time
import wave

from piper import PiperVoice

voix = PiperVoice.load(sys.argv[1])
print(json.dumps({'pret': True}), flush=True)
for ligne in sys.stdin:
    try:
        d = json.loads(ligne)
        t = time.perf_counter()
        with wave.open(d['sortie'], 'wb') as w:
            voix.synthesize_wav(d['texte'], w)
        print(json.dumps({'ok': True, 'ms': round((time.perf_counter() - t) * 1000)}), flush=True)
    except Exception as e:   # one bad request must not kill the worker
        print(json.dumps({'ok': False, 'erreur': f'{type(e).__name__}: {e}'}), flush=True)
