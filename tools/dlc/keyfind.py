# Do the PC archives already hold files of the DLC? The archives keep only a key per file:
# h = h * 31 + c over the lower-case path with '/' (the game's 0x571080). Compare the keys of the DLC's
# paths with every PC index (indexes only, nothing is unpacked).
import glob
import os
import struct
import sys

# GAME_DIR: the Split/Second folder
GAME = os.environ.get('GAME_DIR', '')
PC = os.path.join(GAME, 'data').replace('\\', '/')
if not GAME or not os.path.isdir(PC):
    sys.exit('keyfind: no game data folder at %s (set GAME_DIR to the Split/Second folder)' % (PC if GAME else '?'))
DLC = sys.argv[1:]


def key(path, seed=0):
    h = seed
    for c in path.lower().replace('\\', '/').encode('latin-1'):
        h = (h * 31 + c) & 0xFFFFFFFF
    return h


pc = {}
for fn in glob.glob(PC + '/**/*.ark', recursive=True):
    with open(fn, 'rb') as f:
        head = f.read(24)
        ver, hsize, cA, offA, cB, offB = struct.unpack_from('<6I', head, 0)
        f.seek(8 + offA)
        idx = f.read(20 * cA)
    for k in range(cA):
        pc.setdefault(struct.unpack_from('<I', idx, 20 * k + 16)[0], os.path.relpath(fn, PC))
print('PC keys', len(pc))

# check the hash on the DLC's own archive: its keys should match its loose file paths
for root in DLC:
    paths = []
    for dp, dn, fns in os.walk(root):
        for n in fns:
            rel = os.path.relpath(os.path.join(dp, n), root).replace('\\', '/')
            paths.append(rel)
    hit = [(p, pc[key(p)]) for p in paths if key(p) in pc]
    print(root, 'files', len(paths), 'found in PC archives', len(hit))
    for p, a in hit[:15]:
        print('   ', p, '->', a)
