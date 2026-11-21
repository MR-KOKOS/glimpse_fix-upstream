# Structural comparison of a converted bank with the PC original of the same bank.
# usage: python -I cmpfsb.py <converted.fsb> <pc.fsb>
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fsb4 import FSB4, modestr, codec  # noqa: E402
from fsbconv import parse_sync  # noqa: E402


def compare(a_fn, b_fn, quiet=False):
    da, db = open(a_fn, 'rb').read(), open(b_fn, 'rb').read()
    a, b = FSB4(da), FSB4(db)
    problems = []
    if (a.version, a.flags) != (b.version, b.flags):
        problems.append('header version/flags %X/%X vs %X/%X' % (a.version, a.flags, b.version, b.flags))
    if (0x30 + a.raw_shsize) % 32 or a.build(32) != da:
        problems.append('converted bank not in PC layout (32-byte alignment)')
    if len(a.samples) != len(b.samples):
        problems.append('sample count %d vs %d' % (len(a.samples), len(b.samples)))
    stats = dict(same_len=0, len_within_frame=0)
    for k, (x, y) in enumerate(zip(a.samples, b.samples)):
        tag = '%d %s' % (k, x.sname)
        if x.sname != y.sname:
            problems.append('%s: name differs from PC %r' % (tag, y.sname))
        for f in ('channels', 'vol', 'pan', 'pri', 'mindist', 'maxdist', 'varvol', 'varpan', 'size', 'size2'):
            if getattr(x, f) != getattr(y, f):
                problems.append('%s: %s %r vs PC %r' % (tag, f, getattr(x, f), getattr(y, f)))
        if x.freq != y.freq:
            problems.append('%s: freq %d vs PC %d' % (tag, x.freq, y.freq))
        if x.mode != y.mode:
            problems.append('%s: mode %s vs PC %s' % (tag, modestr(x.mode), modestr(y.mode)))
        sx, sy = parse_sync(x.extra), parse_sync(y.extra)
        if len(sx) != len(sy) or any(a[1] != b[1] or abs(a[0] - b[0]) > 1152 for a, b in zip(sx, sy)):
            problems.append('%s: sync points %s vs PC %s' % (tag, [a[0] for a in sx], [b[0] for b in sy]))
        elif (x.extra and not sx) or (y.extra and not sy):
            problems.append('%s: extra data %d / %d' % (tag, len(x.extra), len(y.extra)))
        if x.samples == y.samples:
            stats['same_len'] += 1
        elif abs(x.samples - y.samples) <= 1152:
            stats['len_within_frame'] += 1
        else:
            problems.append('%s: length %d vs PC %d' % (tag, x.samples, y.samples))
        fa = x.loopstart == 0 and x.loopend == x.samples - 1
        fb = y.loopstart == 0 and y.loopend == y.samples - 1
        if fa != fb:
            problems.append('%s: loop type %d-%d vs PC %d-%d' % (tag, x.loopstart, x.loopend, y.loopstart, y.loopend))
        elif not fa and (abs(x.loopstart - y.loopstart) > 1152 or abs(x.loopend - y.loopend) > 1152):
            problems.append('%s: loop %d-%d vs PC %d-%d' % (tag, x.loopstart, x.loopend, y.loopstart, y.loopend))
    if not quiet:
        print('%s: %d samples (%s), PC %d samples (%s); equal lengths %d, within one frame %d; %d problems' % (
            os.path.basename(a_fn), len(a.samples), codec(a.samples[0].mode), len(b.samples),
            codec(b.samples[0].mode), stats['same_len'], stats['len_within_frame'], len(problems)))
        for p in problems:
            print('  ', p)
    return problems


if __name__ == '__main__':
    sys.exit(1 if compare(sys.argv[1], sys.argv[2]) else 0)
