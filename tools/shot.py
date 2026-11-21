# grab the screen into shots/<name>.png at half size
import os
import sys
from PIL import ImageGrab
# shots/ in the project folder, next to tools/
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'shots')
os.makedirs(SHOTS, exist_ok=True)
im = ImageGrab.grab(all_screens=True)
im.resize((im.size[0] // 2, im.size[1] // 2)).save(os.path.join(SHOTS, '%s.png' % sys.argv[1]))
print(im.size)
