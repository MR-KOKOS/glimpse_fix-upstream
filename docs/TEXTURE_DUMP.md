# Texture dump

Settings program, page *Debug*: *Dump textures on the next start*. At the next start a small window
says "glimpse_fix: dumping the game's textures, please wait" while the fix reads the game's
archives, then the switch turns itself off and the game closes. Start it again to play.

* Result: the folder `glimpse_textures` in the game folder, one subfolder per archive, every texture
  as a DDS file with an `index.txt` (size, format, mip levels). On the Steam version: 551 textures,
  573 MB, in about 10 seconds.
* The archives keep only a hash of each file name, so the files are named by that key
  (`0A1B2C3D.dds`); the key is also what a texture pack will use to say which texture it replaces.
* Nothing in the game folder is changed apart from the new folder and the switch in the INI, and
  no game code is involved.

This is the first step towards texture packs: a pack needs the original textures to start from.
Loading replaced textures in the game is the next step and not part of this version. That is also
why the large address aware flag (`setlaa.exe`) stays: high-resolution textures need address space
on top of the 512 MB streaming pool.
