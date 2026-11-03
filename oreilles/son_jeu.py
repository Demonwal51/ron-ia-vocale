"""Capture the audio ONE process plays (Windows 10 2004+ process loopback), 16 kHz mono float32.

Used to hear the other players: their voice chat comes out of the game, so we record the game's own sound
(and nothing else on the PC: no music, no Discord) while the game says one of them is talking.

    cap = CaptureProcessus(pid); cap.demarrer(); ...; audio = cap.depuis(t0); cap.arreter()
    python -I oreilles/son_jeu.py <pid> [seconds]     prints the RMS of what the process played
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time

import numpy as np

TAUX = 16000
GARDE_S = 30          # ring buffer length: enough for one sentence plus the voice-chat delay

ole32 = ctypes.OleDLL('ole32')
mmdevapi = ctypes.OleDLL('Mmdevapi')
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [('d1', ctypes.c_uint32), ('d2', ctypes.c_uint16), ('d3', ctypes.c_uint16), ('d4', ctypes.c_ubyte * 8)]

    @classmethod
    def de(cls, texte):
        g = cls()
        ole32.CLSIDFromString(ctypes.c_wchar_p('{' + texte + '}'), ctypes.byref(g))
        return g

    def __eq__(self, autre):
        return bytes(self) == bytes(autre)


IID_IUnknown = GUID.de('00000000-0000-0000-C000-000000000046')
IID_IAgileObject = GUID.de('94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90')
IID_Handler = GUID.de('41D949AB-9862-444A-80F6-C261334DA5EB')     # IActivateAudioInterfaceCompletionHandler
IID_IAudioClient = GUID.de('1CB9AD4C-DBFA-4C32-B178-C2F568A703B2')
IID_IAudioCaptureClient = GUID.de('C8ADBD64-E71E-48A0-A4DE-185C395CD317')


class PROCESS_LOOPBACK(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int),            # AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
                ('pid', wt.DWORD),
                ('mode', ctypes.c_int)]            # PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0


class PROPVARIANT_BLOB(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort), ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                ('taille', ctypes.c_ulong), ('donnees', ctypes.c_void_p)]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [('wFormatTag', wt.WORD), ('nChannels', wt.WORD), ('nSamplesPerSec', wt.DWORD),
                ('nAvgBytesPerSec', wt.DWORD), ('nBlockAlign', wt.WORD), ('wBitsPerSample', wt.WORD), ('cbSize', wt.WORD)]


def methode(obj, index, *argtypes):
    """COM method `index` of interface pointer `obj`, returning HRESULT (ctypes raises OSError on failure)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return lambda *args: ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])(obj, *args)


def liberer(obj):
    if obj:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)


_VIVANTS = []


class _Gestionnaire:
    """A minimal COM object implementing IActivateAudioInterfaceCompletionHandler (+ IAgileObject)."""
    QI = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    FINI = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self):
        self.fini = threading.Event()
        self.operation = None

        def qi(this, riid, ppv):
            if riid.contents in (IID_IUnknown, IID_IAgileObject, IID_Handler):
                ppv[0] = this
                return 0
            ppv[0] = None
            return -2147467262   # E_NOINTERFACE

        def fini(this, op):
            self.operation = op
            self.fini.set()
            return 0
        # keep every ctypes object alive as long as this handler
        self._fns = (self.QI(qi), self.REF(lambda this: 1), self.REF(lambda this: 1), self.FINI(fini))
        self._vtbl = (ctypes.c_void_p * 4)(*[ctypes.cast(f, ctypes.c_void_p) for f in self._fns])
        self._objet = ctypes.c_void_p(ctypes.addressof(self._vtbl))
        self.pointeur = ctypes.addressof(self._objet)


def client_audio(pid, delai=5.0):
    """An IAudioClient that captures what process `pid` (and its children) play."""
    params = PROCESS_LOOPBACK(1, pid, 0)
    pv = PROPVARIANT_BLOB(vt=65, taille=ctypes.sizeof(params), donnees=ctypes.addressof(params))   # VT_BLOB
    g = _Gestionnaire()
    _VIVANTS.append(g)   # Windows may call the handler (Release) after we return: never free it
    op = ctypes.c_void_p()
    mmdevapi.ActivateAudioInterfaceAsync(ctypes.c_wchar_p('VAD\\Process_Loopback'), ctypes.byref(IID_IAudioClient),
                                         ctypes.byref(pv), ctypes.c_void_p(g.pointeur), ctypes.byref(op))
    if not g.fini.wait(delai):
        liberer(op)
        raise TimeoutError('activation de la capture du processus sans reponse')
    hr, client = ctypes.HRESULT(), ctypes.c_void_p()
    methode(op, 3, ctypes.POINTER(ctypes.HRESULT), ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(hr), ctypes.byref(client))
    liberer(op)
    if hr.value < 0 or not client:
        raise OSError(f'capture du processus {pid} refusee (HRESULT 0x{hr.value & 0xFFFFFFFF:08X})')
    return client


class CaptureProcessus:
    """Records process `pid` continuously into a ring buffer; `depuis(t)` returns the audio from time t on."""

    def __init__(self, pid):
        self.pid = pid
        self.tampon = np.zeros(TAUX * GARDE_S, dtype=np.float32)
        self.ecrits = 0                       # samples written since start
        self.t_debut = None
        self.verrou = threading.Lock()
        self.erreur = None
        self._stop = threading.Event()
        self._pret = threading.Event()
        self._fil = None

    def demarrer(self, delai=10.0):
        self._fil = threading.Thread(target=self._boucle, name='son_jeu', daemon=True)
        self._fil.start()
        if not self._pret.wait(delai):
            raise TimeoutError('capture du jeu pas prete')
        if self.erreur:
            raise self.erreur

    def arreter(self):
        self._stop.set()
        if self._fil:
            self._fil.join(3)

    def vivant(self):
        return self._fil is not None and self._fil.is_alive() and not self.erreur

    def _boucle(self):
        client = capture = None
        evenement = kernel32.CreateEventW(None, False, False, None)
        try:
            ole32.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED
            client = client_audio(self.pid)
            fmt = WAVEFORMATEX(1, 1, TAUX, TAUX * 2, 2, 16, 0)   # 16 kHz mono 16-bit PCM, converted by Windows
            flags = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000   # LOOPBACK, EVENTCALLBACK, AUTOCONVERTPCM, SRC_DEFAULT_QUALITY
            methode(client, 3, ctypes.c_int, wt.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                    ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p)(0, flags, 2_000_000, 0, ctypes.byref(fmt), None)
            methode(client, 13, wt.HANDLE)(evenement)
            capture = ctypes.c_void_p()
            methode(client, 14, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(capture))
            methode(client, 10)()   # Start
            self.t_debut = time.time()
            self._pret.set()
            prendre = methode(capture, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p, ctypes.c_void_p)
            rendre = methode(capture, 4, ctypes.c_uint32)
            taille = methode(capture, 5, ctypes.POINTER(ctypes.c_uint32))
            n, donnees, nb, drapeaux = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_uint32(), wt.DWORD()
            while not self._stop.is_set():
                kernel32.WaitForSingleObject(evenement, 200)
                # the process may play nothing for a while: no packets, so time is filled with silence
                attendu = int((time.time() - self.t_debut) * TAUX)
                taille(ctypes.byref(n))
                while n.value:
                    prendre(ctypes.byref(donnees), ctypes.byref(nb), ctypes.byref(drapeaux), None, None)
                    if drapeaux.value & 0x2 or not donnees.value:   # AUDCLNT_BUFFERFLAGS_SILENT
                        bloc = np.zeros(nb.value, dtype=np.float32)
                    else:
                        bloc = np.ctypeslib.as_array(ctypes.cast(donnees, ctypes.POINTER(ctypes.c_int16)),
                                                     (nb.value,)).astype(np.float32) / 32768.0
                    rendre(nb.value)
                    self._ajouter(bloc)
                    taille(ctypes.byref(n))
                if self.ecrits < attendu - TAUX // 5:   # more than 0.2 s behind the clock: the process was silent
                    self._ajouter(np.zeros(attendu - self.ecrits, dtype=np.float32))
            methode(client, 11)()   # Stop
        except Exception as e:      # reported by demarrer() / vivant(); the voice AI keeps working without it
            self.erreur = e
            self._pret.set()
        finally:
            liberer(capture)
            liberer(client)
            kernel32.CloseHandle(evenement)

    def _ajouter(self, bloc):
        with self.verrou:
            bloc = bloc[-len(self.tampon):]
            i = self.ecrits % len(self.tampon)
            fin = min(len(bloc), len(self.tampon) - i)
            self.tampon[i:i + fin] = bloc[:fin]
            self.tampon[:len(bloc) - fin] = bloc[fin:]
            self.ecrits += len(bloc)

    def depuis(self, t, jusqua=None):
        """Audio between wall-clock times t and jusqua (default: now), clipped to what the ring buffer still holds."""
        with self.verrou:
            debut = max(int((t - self.t_debut) * TAUX), self.ecrits - len(self.tampon), 0)
            fin = self.ecrits if jusqua is None else min(int((jusqua - self.t_debut) * TAUX), self.ecrits)
            if fin <= debut:
                return np.zeros(0, dtype=np.float32)
            idx = np.arange(debut, fin) % len(self.tampon)
            return self.tampon[idx].copy()


def pid_du_jeu(exe='ReadyOrNotSteam-Win64-Shipping.exe'):
    out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {exe}', '/FO', 'CSV', '/NH'], capture_output=True,
                         text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    for ligne in out.splitlines():
        champs = [c.strip('"') for c in ligne.split('","')]
        if len(champs) > 1 and champs[0].strip('"').lower() == exe.lower():
            return int(champs[1])
    return None


if __name__ == '__main__':
    pid, duree = int(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    cap = CaptureProcessus(pid)
    t0 = time.time()
    cap.demarrer()
    time.sleep(duree)
    a = cap.depuis(t0)
    cap.arreter()
    spectre = np.abs(np.fft.rfft(a)) if len(a) else np.zeros(1)
    pic = np.argmax(spectre) * TAUX / max(len(a), 1)
    print(f'{len(a) / TAUX:.2f} s captees, RMS {float(np.sqrt(np.mean(a ** 2))) if len(a) else 0:.4f}, pic {pic:.0f} Hz')
