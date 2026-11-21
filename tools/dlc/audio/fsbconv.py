# Convert a Split/Second Xbox 360 FSB4 bank (XMA) into the PC layout (MPEG Layer II, or PCM16).
#
#   python -I fsbconv.py <xbox.fsb> <out_pc.fsb> [--codec mp2|pcm] [--stereo-kbps 160] [--mono-kbps 128]
#                        [--delay N] [--reuse XBOX_BASE.fsb PC_BASE.fsb ...] [--work DIR] [--keep]
#
# Per sample: vgmstream-cli decodes the XMA subsong to WAV (loop info ignored), ffmpeg encodes MPEG-1/2
# Layer II (the PC codec; frames concatenated as in the PC banks) or the PCM is stored as is. The sample
# order, names, channel count, rate, volume/pan/priority, 3D distances and mode flags (except the codec bits)
# are kept, so the bank's FEV (which addresses waves by index) works unchanged. Of the Xbox extra data the XMA
# seek table is dropped and the "SYNC" chunk (sync points) is kept, as in the PC banks. Non-XMA Xbox samples
# (big-endian PCM in Generic_LFE) take the same decode/encode path; rates Layer II lacks (500 Hz) are
# resampled to the next one it has (16 kHz, as the PC bank does). --reuse: a sample whose XMA data is byte-identical to one in an
# Xbox base bank is replaced by the PC encoding of that base sample (same name in the PC base bank).
import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fsb4 import FSB4, Sample, M, CODEC_BITS, FIELDS  # noqa: E402

# defaults: the environment variables VGMSTREAM / FFMPEG, else PATH (build_all.py sets both from its own search,
# --vgmstream / --ffmpeg override them)
VGMSTREAM = os.environ.get('VGMSTREAM') or shutil.which('vgmstream-cli') or 'vgmstream-cli.exe'
FFMPEG = os.environ.get('FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg.exe'
L2_RATES = (16000, 22050, 24000, 32000, 44100, 48000)
L2_KBPS_V1 = (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384)
L2_KBPS_V2 = (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
# Layer II decoder delay (synthesis filterbank) in samples: FMOD's decode of the ffmpeg frames lags the source
# by exactly this much (measured with fmodcheck.exe against the vgmstream XMA decode). Partial loop points
# are shifted by it, and samples with a partial loop get that many samples of padding so the loop end stays
# inside the encoded data.
DEFAULT_DELAY = 481


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError('%s failed (%d): %s' % (os.path.basename(cmd[0]), r.returncode,
                                                   r.stdout.decode('latin-1')[-800:]))
    return r.stdout.decode('latin-1')


def read_wav(fn):
    w = wave.open(fn)
    ch, rate, width, n = w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()
    data = w.readframes(n)
    w.close()
    if width != 2:
        raise RuntimeError('%s: %d-byte samples' % (fn, width))
    return ch, rate, n, data


def mpeg_frames(b):
    """Split a raw MPEG audio stream into frames; returns (list of frames, header info of the first)."""
    V1 = {1: L2_KBPS_V1}
    rates = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
    p, out, info = 0, [], None
    # skip a leading ID3v2 tag if the muxer wrote one
    if b[:3] == b'ID3':
        p = 10 + ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14 | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
    while p + 4 <= len(b):
        h = struct.unpack_from('>I', b, p)[0]
        if h >> 21 != 0x7FF:
            if b[p:p + 3] == b'TAG' or not b[p:].strip(b'\0'):
                break
            raise RuntimeError('lost MPEG sync at %d' % p)
        ver, layer = (h >> 19) & 3, (h >> 17) & 3
        if layer != 2:
            raise RuntimeError('not Layer II')
        bri, sri, pad = (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1
        kb = (L2_KBPS_V1 if ver == 3 else L2_KBPS_V2)[bri - 1]
        sr = rates[ver][sri]
        n = 144000 * kb // sr + pad
        out.append(b[p:p + n])
        info = info or dict(version=ver, kbps=kb, rate=sr, chmode=(h >> 6) & 3)
        p += n
    return out, info


def pick_rate(r):
    for x in L2_RATES:
        if x >= r:
            return x
    return 48000


def pick_kbps(want, rate, ch):
    tab = L2_KBPS_V1 if rate >= 32000 else L2_KBPS_V2
    if rate >= 32000 and ch == 1:
        tab = [k for k in tab if k <= 192]
    return min(tab, key=lambda k: (abs(k - want), -k))


class Reuse:
    """XMA data hash -> (PC sample) from pairs of Xbox/PC base banks."""

    def __init__(self, pairs):
        self.map = {}
        for xf, pf in pairs:
            x = FSB4(open(xf, 'rb').read())
            p = FSB4(open(pf, 'rb').read())
            byname = {s.sname.lower(): s for s in p.samples}
            for s in x.samples:
                t = byname.get(s.sname.lower())
                if t is not None and t.channels == s.channels:
                    self.map[hashlib.md5(s.data).hexdigest()] = t

    def get(self, s):
        return self.map.get(hashlib.md5(s.data).hexdigest())


def convert_sample(src_fsb, k, s, args, work, log):
    wav = os.path.join(work, '%03d.wav' % k)
    out = run([args.vgmstream, '-i', '-s', str(k + 1), '-o', wav, src_fsb])
    nm = [l.split(':', 1)[1].strip() for l in out.splitlines() if l.startswith('stream name:')]
    # (vgmstream may take the name from a .fev next to the bank, with the wave's source folder in front;
    # FSB header names are cut at 30 characters)
    if nm and not nm[0].replace('\\', '/').split('/')[-1].lower().startswith(s.sname.lower()):
        raise RuntimeError('subsong %d is %r, header says %r' % (k + 1, nm[0], s.sname))
    ch, rate, n, pcm = read_wav(wav)
    if ch != s.channels or rate != s.freq:
        raise RuntimeError('%s: decoded %d ch %d Hz, header %d ch %d Hz' % (s.sname, ch, rate, s.channels, s.freq))
    if n != s.samples:
        log.append('  note %s: decoded %d samples, header %d' % (s.sname, n, s.samples))
    t = Sample(**{f: getattr(s, f) for f in FIELDS})
    t.extra = b''
    base = s.mode & ~CODEC_BITS
    if args.codec == 'pcm':
        t.data, t.samples, t.freq = pcm, n, rate
        t.mode = base | M['BITS16'] | M['SIGNED']
        delay, ratio = 0, 1.0
    else:
        r2 = pick_rate(rate)
        kb = pick_kbps(args.stereo_kbps if ch > 1 else args.mono_kbps, r2, ch)
        mp2 = os.path.join(work, '%03d.mp2' % k)
        partial = not (s.loopstart == 0 and s.loopend >= s.samples - 2)
        cmd = [args.ffmpeg, '-v', 'error', '-y', '-i', wav]
        if partial and args.delay > 0:
            cmd += ['-af', 'apad=pad_len=%d' % args.delay]
        if r2 != rate:
            cmd += ['-ar', str(r2)]
        run(cmd + ['-c:a', 'mp2', '-b:a', '%dk' % kb, '-f', 'mp2', mp2])
        frames, info = mpeg_frames(open(mp2, 'rb').read())
        t.data = b''.join(frames)
        t.samples, t.freq = len(frames) * 1152, r2
        t.mode = base | M['MPEG'] | M['MPEG_L2']
        delay, ratio = args.delay, r2 / rate
        if not args.keep:
            os.remove(mp2)
    if not args.keep:
        os.remove(wav)
    t.csize = len(t.data)
    pos = lambda v: min(int(round(v * ratio)) + delay, t.samples - 1)
    # loop points: whole-sample loops stay whole-sample (as in the PC banks), others are mapped
    if s.loopstart == 0 and s.loopend >= s.samples - 2:
        t.loopstart, t.loopend = 0, t.samples - 1
    else:
        t.loopstart = pos(s.loopstart)
        t.loopend = min(int(round((s.loopend + 1) * ratio)) + delay, t.samples) - 1
        log.append('  loop %s: %d-%d/%d -> %d-%d/%d' % (s.sname, s.loopstart, s.loopend, s.samples,
                                                         t.loopstart, t.loopend, t.samples))
    # sync points: the PC banks keep them as the same "SYNC" chunk, alone in the header extra
    sync = parse_sync(s.extra)
    if sync and not args.no_sync:
        named = s.mode & M['SYNCPOINTS_NONAME'] == 0
        ent = bytearray()
        for p, name in sync:
            ent += struct.pack('<I', pos(p)) + (name if named else b'')
        t.extra = b'SYNC' + struct.pack('<I', len(sync)) + bytes(ent)
        log.append('  sync %s: %s -> %s' % (s.sname, [p for p, _ in sync],
                                            [struct.unpack_from('<I', ent, i * (260 if named else 4))[0]
                                             for i in range(len(sync))]))
    elif s.mode & M['SYNCPOINTS'] and not sync:
        log.append('  note %s: SYNCPOINTS flag without SYNC data' % s.sname)
    return t


def parse_sync(extra):
    """Sync points of a sample's extra data: list of (position, 256-byte name or b'')."""
    i = extra.rfind(b'SYNC')
    while i >= 0:
        n = struct.unpack_from('<I', extra, i + 4)[0] if i + 8 <= len(extra) else -1
        rest = len(extra) - i - 8
        for size in (4, 260):
            if n > 0 and rest == n * size:
                return [(struct.unpack_from('<I', extra, i + 8 + k * size)[0],
                         extra[i + 12 + k * size:i + 8 + (k + 1) * size]) for k in range(n)]
        i = extra.rfind(b'SYNC', 0, i)
    return []


def convert(src_fsb, dst_fsb, args):
    x = FSB4(open(src_fsb, 'rb').read())
    reuse = Reuse(args.reuse) if args.reuse else None
    out = FSB4()
    # header flags: drop BASICHEADERS (0x2, full headers are written) and BIGENDIANPCM (0x8, Xbox PCM banks)
    out.version, out.flags, out.zero, out.hash = x.version, x.flags & ~0xA, x.zero, x.hash
    work = args.work or tempfile.mkdtemp(prefix='fsbconv_')
    os.makedirs(work, exist_ok=True)
    log, reused = [], 0
    try:
        for k, s in enumerate(x.samples):
            if s.mode & M['MPEG']:
                t = s                       # already the PC codec: keep
            elif reuse and reuse.get(s) is not None:
                t = reuse.get(s)
                reused += 1
            else:
                t = convert_sample(src_fsb, k, s, args, work, log)
            out.samples.append(t)
            print('%3d/%d %-30s %d ch %d Hz %d -> %d samples, %d -> %d bytes%s' % (
                k + 1, len(x.samples), s.sname, t.channels, t.freq, s.samples, t.samples, s.csize, t.csize,
                ' (PC base sample)' if t is not s and reuse and reuse.get(s) is t else ''))
            sys.stdout.flush()
    finally:
        if not args.work and not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    blob = out.build(32)
    os.makedirs(os.path.dirname(os.path.abspath(dst_fsb)), exist_ok=True)
    open(dst_fsb, 'wb').write(blob)
    for l in log:
        print(l)
    print('wrote', dst_fsb, len(blob), 'bytes,', len(out.samples), 'samples,', reused, 'reused from PC base')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--codec', choices=('mp2', 'pcm'), default='mp2')
    ap.add_argument('--stereo-kbps', type=int, default=160)
    ap.add_argument('--mono-kbps', type=int, default=128)
    ap.add_argument('--delay', type=int, default=DEFAULT_DELAY, help='shift for partial loop points (mp2)')
    ap.add_argument('--reuse', nargs=2, action='append', metavar=('XBOX_BASE', 'PC_BASE'))
    ap.add_argument('--no-sync', action='store_true', help='drop sync points (the PC music bank has none)')
    ap.add_argument('--work')
    ap.add_argument('--keep', action='store_true', help='keep the intermediate wav/mp2 files')
    ap.add_argument('--vgmstream', default=VGMSTREAM)
    ap.add_argument('--ffmpeg', default=FFMPEG)
    args = ap.parse_args(argv)
    convert(args.src, args.dst, args)


if __name__ == '__main__':
    main()
