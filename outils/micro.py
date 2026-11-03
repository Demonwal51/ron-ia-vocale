"""Microphone helpers: list inputs, find the configured one by name, measure its level.

Device indices change between enumerations, so config.json stores the name and the host API, never an index.
Probing uses sd.rec (a blocking read() lies on DirectSound on this PC).
"""
import numpy as np
import sounddevice as sd

TAUX = 16000


def entrees():
    """[(index, name, host API)] for every input device, WDM-KS left out (it does not open here)."""
    apis = sd.query_hostapis()
    return [(i, d['name'], apis[d['hostapi']]['name']) for i, d in enumerate(sd.query_devices())
            if d['max_input_channels'] > 0 and 'WDM-KS' not in apis[d['hostapi']]['name']]


def trouver(micro):
    """config 'micro' -> current device index. None = Windows default; {'nom', 'api'} = that exact input."""
    if micro is None or isinstance(micro, int):
        return micro
    for i, nom, api in entrees():
        if nom == micro['nom'] and api == micro.get('api', api):
            return i
    raise SystemExit(f"micro introuvable : {micro['nom']} ({micro.get('api')}). Relance outils/essai_micro.py")


def sonder(index, duree=0.6):
    """RMS level of `duree` seconds from this input (first fifth dropped), or None if it does not open."""
    try:
        rec = sd.rec(int(TAUX * duree), samplerate=TAUX, channels=1, dtype='float32', device=index)
        sd.wait()
    except Exception:
        return None
    x = rec[len(rec) // 5:, 0]
    return float(np.sqrt(np.mean(x * x)))
