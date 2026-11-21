# HLSL -> D3D9 bytecode with a legacy D3DCompiler DLL through ctypes (no fxc.exe needed).
# D3DCompiler_41.dll is the March 2009 SDK compiler, the same build (9.26.952.2844) the PC shaders of the game
# were made with. The Windows 10 SDK fxc.exe (d3dcompiler_47) produces wrong code for some ps_3_0 shaders
# (component order bugs in its legacy-target optimiser, seen in validation), so it is only a fallback.
import ctypes
import os

S_OK = 0
_dll_cache = {}


class Blob:
    def __init__(self, p):
        self.p = p

    def _vt(self, i, restype, *argtypes):
        vt = ctypes.cast(ctypes.cast(self.p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vt[i])

    def data(self):
        ptr = self._vt(3, ctypes.c_void_p)(self.p)
        n = self._vt(4, ctypes.c_size_t)(self.p)
        return ctypes.string_at(ptr, n)

    def release(self):
        self._vt(2, ctypes.c_ulong)(self.p)


def load(name):
    if name not in _dll_cache:
        dll = ctypes.WinDLL(os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', name))
        f = dll.D3DCompile
        f.restype = ctypes.c_long
        f.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                      ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        _dll_cache[name] = f
    return _dll_cache[name]


def compile(src, profile, entry='main', dll='D3DCompiler_41.dll', flags=0):
    """returns (bytecode or None, messages)"""
    f = load(dll)
    b = src.encode('latin-1')
    code = ctypes.c_void_p()
    err = ctypes.c_void_p()
    hr = f(b, len(b), b'shader', None, None, entry.encode(), profile.encode(), flags, 0, ctypes.byref(code),
           ctypes.byref(err))
    msg = ''
    if err.value:
        e = Blob(err.value)
        msg = e.data().decode('latin-1', 'replace').rstrip('\0')
        e.release()
    if hr != S_OK or not code.value:
        return None, msg or ('hr %08x' % (hr & 0xFFFFFFFF))
    c = Blob(code.value)
    d = c.data()
    c.release()
    return d, msg
