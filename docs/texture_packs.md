# Texture packs (#384)

Higher-resolution textures without touching the game's files: the renderer recognises a
texture the game loads by a fingerprint of its contents, and draws a file named after that
fingerprint instead. The game itself never sees the file; it keeps its own texture, in its own
memory, and only the graphics card's copy changes.

A **texture pack** is a folder of such files. Packs come from two places, and the project hosts
neither (they are derived from the game's art):

- **Make HD pack**: the launcher upscales the textures *you* dumped, on your PC, with an AI
  upscaler (Real-ESRGAN), and compresses them. Nothing of the game is downloaded or shared.
- **Community packs**: packs other players made by hand, added with **Add pack…**.

## The Texture packs page

The launcher's **Texture packs** page (also **Settings > Display > Texture packs > Texture packs
page…**):

| Launcher | cvar | What it does |
| --- | --- | --- |
| Use texture packs | `texture_replace` | Draws the ticked packs' textures (Vulkan) |
| Dump textures | `texture_dump` | Saves each texture the game loads, once, to `textures/dump/` |
| The list of packs | `texture_packs` | The packs to use, in order (the launcher passes it) |
| Open textures folder | | Opens `textures/` in the game's data folder |

Both switches are off by default, are the same settings as in Settings > Display, and apply from
the next start. The folder is `textures` in the game's data folder
(`~/.local/share/forza/textures` on Linux, `%LOCALAPPDATA%\ForzaReImagined\textures` on
Windows): the launcher passes it as `texture_pack_root`; without the launcher it is
`<user_data_root>/textures`. Other cvars, for `forza.toml` or the command line:

- `texture_dump_png` (on): with `texture_dump`, also write a `.png` of the largest level.
- `texture_replace_preload` (off): read every pack file at start instead of when its texture
  first loads, so no texture is shown at the original resolution first (more memory).
- `texture_pack_root`: another folder (the launcher then passes no `texture_packs`).

### The list

Each folder in `textures/` is a pack (not `dump/`, nor folders starting with a dot). The list
shows its name, author and version (from its `pack.toml`), its textures and how much graphics
memory they take when all are in use; tick a pack to use it (Return, Enter or a controller's A on
the selected row too). **Ticked packs are used from top to bottom: when two packs replace the
same texture, the one lower in the list wins** (as on the Mods page). The details under the list
name the textures a pack shares with others and whose copy is used. **Move up**, **Move down**,
**Remove** (asks first; a linked folder loses only the link) and **Refresh** work as on the Mods
page. The pack Make HD pack writes, "HD pack (made on this PC)" (`hd-pack`), starts at the top, so
hand-made packs below it win over it.

launcher.json keeps the order and the packs turned off (`"texture_packs": {"order": [...],
"disabled": [...]}`). With Use texture packs on, every start passes
`--texture_packs=<pack>|<pack>` with the ticked packs in order (`|` alone: none), unless
**Extra command-line flags** name `texture_packs` or `texture_pack_root`. The game reads only
those folders (SDK patch 0310); without the option (the game started without the launcher) it
reads every folder in `textures/` but `dump/`, the first path in alphabetical order winning.

Texture files directly in `textures/` (where milestone 1 told you to put them) keep working: they
are listed as **Loose files (textures folder)**, always first, so every pack wins over them for the
same texture. Untick them to turn them off; they cannot be moved or removed on the page (move or
delete the files in the folder). The launcher passes them as `.` in `texture_packs` (the files in
the folder itself, not in its folders).

### Add pack…

**Add pack…** takes a **Zip file…** or a **Folder…** (dropping either on the page does the same).
The pack is the folder that has `pack.toml` or texture files at its top, the zip's or folder's top
or the one folder inside it; it is named after that folder (a zip with its files at the top: the
zip's name). Only files named `<fingerprint>.dds` or `.png` and the pack's text files (`.txt`,
`.md`, `.toml`: read-me, licence, `pack.toml`) are copied; anything else in the zip is left out.
The files are unpacked into a hidden `.adding-*` folder in `textures/`, checked as the game reads
them, and moved into place only when the pack is usable; a pack of the same name is replaced
only after you say yes (never while the game runs).

Refused, in words under the list: a zip with an absolute path, `..` or a link (zip slip), more
than 200,000 files or 32 GB unpacked, a texture over 2 GB, a texture dump (a folder with
`index.tsv`: the game's own textures), and a pack without a single texture the game can use. A
pack named `hd-pack` or `dump` is added as `hd-pack (added)` (those folders are Make HD pack's
and the dump's). Kept, with a note: files the game cannot use (a cube map, a format it does not
read, a size over 16384, a file shorter than its header), files not named by a fingerprint, a
texture in a pack twice, and files that are byte for byte the game's own dumped textures (they
change nothing, and a pack must not contain them).

### Compress PNGs

A PNG becomes RGBA8 in graphics memory (4 bytes a texel, a third more for the mips the game makes),
a BC7 DDS file 1 byte a texel. **Compress PNGs** (under the list) turns the selected pack's PNGs into
DDS files with their mip levels, with the compressor Make HD pack uses ([Compression](#compression)):
about a quarter of the graphics memory. It runs in the background with a progress bar and
**Cancel**; each PNG is deleted only once its DDS file is written, so a stopped run leaves a pack
that works, part compressed. The pack's details say how many of its textures are PNGs and how much
graphics memory they take. **Compress the PNG textures of packs I add** (on by default,
`"texture_packs_compress"` in launcher.json) does the same right after **Add pack…**. The change
applies from the next start of the game. A pack's PNG with a `.dds` of the same name beside it is
left alone (the game uses the `.dds`).

## Make HD pack

**Make HD pack** upscales your dump into the pack "HD pack (made on this PC)" (`textures/hd-pack`),
at **2x** (default) or **4x**, and compresses it. Each choice shows how much graphics memory its
pack takes. 4x is offered only on a graphics card with at least 6 GB of its own memory (10 GB when
the pack is written as PNGs, see below; the launcher's GPU detection, as for the recommended
settings); otherwise it is greyed out and the page says why (the card's memory, integrated
graphics, or memory the launcher could not read). Turn on **Dump textures**, play where you want sharper textures (a
race from the festival dumps about 1,300), then press **Make HD pack**. It runs in the background
(the rest of the launcher and Play work meanwhile; the game can run, more slowly while both use
the graphics card), with a progress bar, the time left and **Cancel**. What is made stays: the next
**Make HD pack** (then named **Continue HD pack**) goes on from there. A pack made at another scale
is replaced after you say yes. Before it starts, the page says how many textures it will upscale,
how big the pack will be on disk and in graphics memory, and how long it takes on a mid-range card;
it warns on an integrated or small graphics card, and when the pack would take more than 40% of the
card's memory.

What it upscales, from the dump's `index.tsv`:

- **Colour textures only.** DXT5A/BC4 and 8-bit textures (one channel: masks), DXN/BC5 and 8_8
  textures (two channels: normal maps) are left as they are: an RGB upscaler would ruin their
  data. So are colour textures that look like normal maps (red and green around the middle, blue
  high) or data maps (channels used as masks: most values at 0 or 255 and many texels pure red,
  green or yellow, like the game's blend maps).
- **Not small ones**: a side under 32 or under 64x64 in all (UI bits, solid colours, ramps).
- **Sizes**: 2x or 4x, same aspect ratio; a side is never made longer than 4096 (a 2048 texture
  at 4x becomes 4096, at 2x stays out).
- **Alpha** is kept: the colour under transparent black texels (BC1 cut-outs: foliage, fences) is
  filled from the texels around them before upscaling, so edges do not turn dark; the alpha
  channel is scaled smoothly on its own. Opaque textures are written without alpha.
- **Colours**: the model shifts colours a little (darker, less saturated); each texture's mean and
  contrast per channel are matched back to the original's over the texels it shows (not the filled
  cut-out holes), so upscaled and original textures still match side by side. New detail stays.
- **Files**: `hd-pack/<fingerprint>.dds`, compressed with its mip levels ([Compression](#compression)),
  and a `pack.toml` with the scale and `format = "dds"`. Each is written to a temporary file and
  renamed, so a cancelled or interrupted run leaves no half-written texture. A build of the game
  from before the compressor has none: then the textures are written as `<fingerprint>.png` (the
  game makes the mips), take 4 times the graphics memory, and the page says to build the game
  again; the next Make HD pack after that compresses the PNGs first (named **Compress HD pack**
  when nothing is left to upscale).

### The upscaler

| | Chosen: Real-ESRGAN ncnn-Vulkan | Also considered |
| --- | --- | --- |
| What | `realesrgan-ncnn-vulkan` 20220424 with the `realesrgan-x4plus` model (general photos, 4x) | waifu2x-ncnn-vulkan (MIT, models MIT): fast, but keeps the DXT block artefacts and its models are for drawings; Real-CUGAN ncnn (MIT): anime only; Real-ESRGAN's `realesr-animevideov3`: anime, smooths textures to cartoons; upscayl-ncnn: AGPL-3.0 and community models with unclear or non-commercial licences (not used); chaiNNer and the PyTorch models: need Python or CUDA |
| Runs on | any Vulkan GPU (AMD, NVIDIA, Intel; the CPU's llvmpipe as a last resort), Windows, Linux (Flatpak too: its runtime has `libvulkan.so.1` and `libgomp.so.1`), macOS; no Python, CUDA or install | |
| Program licence | MIT (Copyright (c) 2021 Xintao Wang, and (c) 2019 nihui for realsr-ncnn-vulkan), [Real-ESRGAN-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/blob/master/LICENSE); it is built with ncnn (BSD-3-Clause) | |
| Model licence | BSD-3-Clause, [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE) (Copyright (c) 2021 Xintao Wang): the weights are release files of that repository, with no other terms | |
| From | the official release [Real-ESRGAN v0.2.5.0](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0): `realesrgan-ncnn-vulkan-20220424-{ubuntu,windows,macos}.zip` (45-50 MB) | |

The page names both licences with links. The launcher downloads the archive for your system the
first time you press Make HD pack (after asking), checks its SHA-256 against the one pinned in `launcher/forza_launcher/pins.py` (the release
predates GitHub's asset checksums; the pinned hashes match nixpkgs' package of the same archive and
other projects' pins), keeps only the program and its model (plus `vcomp140.dll`, Microsoft's OpenMP
runtime, on Windows) in `<data folder>/tools/realesrgan-ncnn-vulkan-20220424` with a `SOURCE.txt`
naming the source and licences, and deletes the archive. Before every run each file is checked
against the SHA-256 recorded when it was unpacked; a changed file is not run. The project's
repository and releases never contain the upscaler.

### Compression

Make HD pack and Compress PNGs write DDS files: **BC7**, or **BC1** where BC1 keeps a texture nearly
as well, each with all its mip levels down to 1x1. The game uploads them as they are (SDK patch 0280
reads BC1-5 and BC7), so a texture takes a quarter (BC7) or an eighth (BC1) of the graphics memory of
the same PNG, loads faster (no PNG to decode, no mips to make, a quarter of the data to upload at
the 32 MiB a frame) and is drawn the same way.

| | Chosen: bc7enc_rdo, built with the game | Also considered |
| --- | --- | --- |
| What | `forza-texcompress` (`forza-recomp/texcompress`), a small program built next to the game, with Rich Geldreich's encoders from [bc7enc_rdo](https://github.com/richgel999/bc7enc_rdo): `bc7enc.cpp` for BC7 (modes 1 and 6 for opaque blocks, 5, 6 and 7 with alpha), `rgbcx` for BC1 | Compressonator CLI (MIT): a prebuilt program per system to download, pin and check, far larger than the two encoders; Intel's ISPC Texture Compressor (MIT) and `bc7e.ispc`: need the ISPC compiler, which the launcher's toolchain does not have, and run on x86 only; basis_universal's scalar port of bc7e: Apache-2.0 |
| Runs on | wherever the game builds (Windows, Linux, the Flatpak): the launcher's build compiles it with the same toolchain, so nothing is downloaded, and it needs no Python, CUDA or graphics card | |
| Licence | MIT or public domain (Unlicense), whichever the user prefers: [LICENSE](https://github.com/richgel999/bc7enc_rdo/blob/master/LICENSE), copied to the game's `licenses/bc7enc_rdo.txt` | |
| From | vendored, unchanged, in `forza-recomp/thirdparty/bc7enc_rdo` at commit `b9438627` (archive SHA-256 in `forza-recomp/thirdparty/README.md`): only the BC7 and BC1 encoders and the BC7 decoder; not `bc7e.ispc` (Apache-2.0) nor LodePNG | |

How each texture is compressed:

- **Mip levels**: the game's own box filter (2x2, the same rounding as it uses for a PNG), so a
  compressed texture differs from its PNG only by the compression.
- **Alpha** (cut-outs, glass, decals): BC7, with alpha weighted twice as much as luma, so the
  edge an alpha test cuts at stays where it was.
- **Opaque**: BC1 when its largest level keeps at least 40 dB (RGB PSNR), no 4x4 block has an
  RMS error over 12, and at most 2% of its smooth blocks (every channel within 12 levels:
  gradients, sky, flat paint) are off by 4 levels or more; otherwise BC7. BC1 has two 5:6:5
  colours and four levels a block: it is near lossless on flat paint and signs, but bands smooth
  areas and smears the fine grain the upscaler adds.
- **Threads**: up to 8, below normal priority (the game can run meanwhile); while the graphics card
  upscales a batch, the processor compresses the one before.

Measured on the 2x pack of the festival race's dump (738 textures, 320 million texels in their
largest levels), each texture compared with its uncompressed upscale:

| | BC7 as chosen | BC7, alpha weighted as luma | BC1 for every opaque texture |
| --- | --- | --- | --- |
| RGB PSNR, median of the opaque / alpha textures | 49.5 / 46.8 dB | 49.5 / 47.4 dB | 40.0 dB (opaque) |
| Smooth blocks off by 4 levels or more (opaque) | 0.9% | 0.9% | 14.1% |
| Texels across half alpha (of the 155 million in the alpha textures) | 299k (0.19%), 72 of them 32 or more from half | 370k, 314 | |

The texels that cross half alpha are next to an edge (its alpha ramp), so an alpha test's cut moves
by less than a texel; no hole opens. bc7enc's slower settings (uber levels 1 and 2) gained under
0.1 dB at 1.5 and 3 times the time. As chosen, 60 of the 520 opaque textures are BC1 (flat colours,
signs: median 67.8 dB), the rest BC7 (median 48.6 dB over all of them): BC1 for every opaque
texture would save another quarter of the memory, at the cost above.

### Measured

Ryzen 7 7800X3D with a Radeon RX 9060 XT (16 GB, Linux, RADV), the dump of a festival race (1,228
textures): 738 upscaled, 490 left as they are (423 small, 25 masks, 22 normal maps, 20 data maps:
the red and green blend maps). Compressed, 678 of the 738 are BC7 and 60 BC1. The game runs are
`tools/perf_scenario.sh`'s race (3 minutes from the start to 26 s of racing) and free roam (90 s of
driving) inputs, 720p, 30 fps cap, a warm shader cache, one each, back to back with nothing else
running (the desktop held 1.0 GB of the card before each):

| | HD pack 2x, PNG | HD pack 2x, compressed | HD pack 4x, PNG | HD pack 4x, compressed |
| --- | --- | --- | --- | --- |
| Making the pack | 9 min 4 s | 8 min 36 s | 9 min 50 s | 8 min 41 s |
| Compressing a PNG pack that is there (8 threads) | | 29 s | | 1 min 56 s |
| On disk | 369 MB | 423 MB | 1.3 GB | 1.7 GB |
| Graphics memory of the whole pack | 1.6 GB | 0.4 GB | 6.4 GB | 1.6 GB |
| Race: textures drawn from the pack, their graphics memory | 705, 1,580 MiB | 709, 408 MiB | 705, 6,118 MiB | 709, 1,552 MiB |
| Race: textures read in the first two minutes | 512 | 513 | 412 | 516 |
| Race: fps, p99 frame (`PERF` race line) | 30.0, 34.1 ms | 30.0, 34.1 ms | 30.0, 34.1 ms | 30.0, 34.2 ms |
| Race, minute 3: fps, 1% low, worst frame | 30.0, 29.6, 34 ms | 30.0, 29.6, 34 ms | 30.0, 29.6, 34 ms | 30.0, 29.5, 34 ms |
| Free roam: textures drawn from the pack, their graphics memory | 274, 645 MiB | 264, 157 MiB | 245, 2,586 MiB | 274, 618 MiB |
| Free roam: fps, p99 frame | 29.9, 34.1 ms | 29.8, 34.2 ms | 29.9, 34.0 ms | 29.9, 34.1 ms |
| The card's used memory at its peak (all programs), race / free roam | 3.7 / 2.7 GB | 2.5 / 2.2 GB | 8.1 / 4.5 GB | 3.5 / 2.7 GB |

Compressed, a pack takes a quarter of the graphics memory (a compressed 4x pack as much as a PNG 2x
one), and its textures load faster: a PNG 4x pack had read 412 textures two minutes into the race
(245 in free roam, against 274 compressed), a compressed one 516. The frame rate held the game's
30 fps in every run: the packs add no work per frame. Making the pack takes no longer, as the
processor compresses one batch while the graphics card upscales the next (the PNG times are from
before the data maps were left out: 758 textures). The compressed pack is a little larger on disk
than the PNGs: a DDS file holds its texture as the card does, and the mip levels.
What changes with the pack: tiled ground textures (road, gravel, dirt) lose their DXT blocks and
gain fine grit, rock faces get sharper cracks, bark and foliage cards show more detail; from the
chase camera at 1440p the difference is clear on the near road and foliage, small on distant
terrain. Compressed and PNG packs look the same in the game: the shots at the spawn point differ
only where something moved between runs (foliage in the wind, traffic's tyre tracks on the dirt).

## Making and sharing a pack

A pack is a folder:

```
Rocks HD/
  pack.toml            name = "Rocks HD", version = "1.0", author = "you", description = "..."
  README.md            optional: what it changes, your licence
  b5fcb5a5a9d78d71.dds the replacement of the dumped texture b5fcb5a5a9d78d71
  terrain/1808870a1806a9d5.png   folders inside are fine
```

1. Turn on **Dump textures** and play where the textures you want to change are. Each texture is
   in `textures/dump/` as `<fingerprint>.dds` and `.png` (to look at), listed in `index.tsv`.
2. Make your replacement from the dumped file (paint it, redraw it, make it larger) and save it
   with **the same file name** in your pack folder, as described below (same aspect ratio; DDS
   BC1/BC3/BC7 with mips is smallest in graphics memory, PNG works too, and **Compress PNGs**
   makes BC7 DDS files of PNGs).
3. Put the folder in `textures/` (or **Add pack…**), tick it, turn on **Use texture packs** and play.
4. To share it, zip the folder. Name the files by fingerprint only; the fingerprint is the same on
   every PC and in every run.

**Sharing is your responsibility.** A pack holds changed game art, which belongs to the game's
publisher; the project does not host packs and cannot vouch for any. Share only your own work:
never the game's textures unchanged, the dumped files, files only re-saved or converted from them,
or a dump; and keep the pack Make HD pack made on your PC to yourself (it is the game's art run
through a program, not your work). The launcher notes files of an added pack that are the game's
own textures unchanged.

## Dumping

With **Dump textures** on, drive where the textures you want to replace are. Each texture is
written once (also across runs: the files already in `dump/` are skipped) as

- `textures/dump/<fingerprint>.dds`: the texture as the game has it, with all its mip levels,
  in the matching DDS format (DXT1/3/5, ATI1/ATI2 for BC4/BC5, RGBA8, R8 or RG8);
- `textures/dump/<fingerprint>.png`: its largest level, decoded, to browse;
- a line in `textures/dump/index.tsv`: fingerprint, the game's format, size, mip levels, file
  format.

The fingerprint is 16 hexadecimal digits, for example `b5fcb5a5a9d78d71`. It is a hash
(xxHash3) of the texture's largest level as the game wrote it to memory (tiled, big-endian;
only the texels' blocks, not the padding around them, which holds whatever the memory held
before) plus its format, size, pitch, tiling and endianness. Where the texture is in memory is
not part of it, so it is the same in every run and on every PC; nor are its smaller mip levels
(the game binds some textures with different mip counts).

What is dumped: 2D textures in the formats DXT1, DXT2/3, DXT4/5, DXN, DXT5A, 8, 8_8 and
8_8_8_8, from 8x8 up. Skipped, and counted in the log: render targets the GPU drew (shadows,
reflections, the screen), screen-sized textures (1280x720 and its halves: render targets the
game reads back), textures the game rewrites as it runs (reloaded three times, each within a
second: videos, gauges), cube maps, 3D textures and arrays, and the other formats (5_6_5,
4_4_4_4, CTX1 and the float and depth formats) for now.

A race from the festival (3 minutes from the start) dumps about 1,300 textures, 190 MB (90 MB
of DDS files, 100 MB of PNGs); a free-roam drive from there adds about 700. The files are
untiled and written on a worker thread. If the game loads textures faster than they are
written (4,096 or 256 MiB waiting), the rest are not dumped this time and the log says how
many: they are dumped the next time they load.

## Making a replacement

Put a file named `<fingerprint>.dds` or `<fingerprint>.png` in a pack folder in `textures/`
(folders inside it are fine), or directly in `textures/` (the loose files). Keep the name of the
dumped texture you started from.

- **Size**: any, as long as the aspect ratio is the same (a 512x256 texture can become
  1024x512 or 4096x2048, within a texel). Up to 16384.
- **Mip levels**: optional. A `.png`, or an uncompressed DDS without mips, gets them made
  (box filter). A block-compressed DDS (BC1-BC7) is used with the levels it has: include
  them, or the texture shimmers in the distance.
- **Channels**: as in the dumped file. Single-channel textures (8, DXT5A) are read from red;
  two-channel ones (8_8, DXN: normal maps) from red and green. Alpha comes from the file.
- **Formats**: DDS with BC1 (DXT1), BC2 (DXT3), BC3 (DXT5), BC4 (ATI1, BC4U), BC5 (ATI2, BC5U),
  BC7 (DX10 header), RGBA8 or BGRA8 (32-bit RGB masks, or DXGI R8G8B8A8/B8G8R8A8), R8
  (luminance, or DXGI R8) and RG8 (DXGI R8G8, or luminance + alpha); sRGB variants are read as
  the plain ones, as the game applies its own gamma. PNG: 8 bits per channel (16-bit images are
  reduced), any colour type. Not supported: cube maps, arrays, volume textures, other DXGI
  formats.
- **Row padding** (uncompressed DDS): rows are read packed unless the header's pitch
  (`DDSD_PITCH`) says otherwise. Rows padded to 4 bytes are read in every mip level (as
  Direct3D 9 tools write them); other padding only in a file without mips. A header whose pitch
  is shorter than a row, or a file shorter than its header says, is not used (the log says
  why).
- When both a `.dds` and a `.png` exist in a pack, the `.dds` is used; the same name twice in a
  pack, the first path in alphabetical order; in two packs, the later pack's (see the list).

The replacement shows from the frame after it is read (a worker reads and decodes it the first
time its texture loads, and the frame start uploads it): the original is drawn until then,
without a stall. The log names the first replacements it uses, and every file it cannot use
and why (a wrong aspect ratio, an unsupported format).

## Limits

- Replacing needs the Vulkan renderer; Direct3D 12 dumps only.
- A texture the game samples as signed data keeps its original (the replacement is only bound
  where the game reads it unsigned, which is how it reads colour textures).
- The game's own texture still loads (its memory and its copy on the graphics card), so a
  replacement adds graphics memory; replacements stay loaded until the game exits. The log's
  summary line counts them (`MiB of GPU memory`).
- The mip level the GPU picks follows the replacement's size, so the distance where a texture
  turns blurry moves out, as it should; the game's mip clamps (`mip_min_level`) are levels of
  the new image, a little sharper than before.
- Only what the list above dumps can be replaced: no cube maps (sky, reflections), 3D textures
  or other formats yet.

## The log

At most once a minute while something happens; the counts are since the start:

```
Texture packs: command processor time 6.4 ms (1.70 ms since the last summary; hashing 5.8 ms,
dump copies 0.0 ms; at most 924 us for a load); 1902 loads fingerprinted (107.0 MiB); dumped 0
(0.0 MiB written, 0 queued, 0 dropped, 0 failed); replacements for 22 textures (22 files read,
0 not usable, 22 uploaded, 66.8 MiB of GPU memory); textures skipped: 3 format, 6
cube/3D/array/scaled, 27 screen-sized, 51 dynamic, 0 mips only, 59 tiny; 164 GPU-written loads;
formats not handled: k_32_FLOAT 1, k_24_8 1, k_2_10_10_10 1
```

The command processor time is what fingerprinting cost the thread that feeds the GPU (every
frame waits for it): it runs when a texture's data loads (created, or reloaded after the game
wrote its memory), never per draw. With both options off it does not run at all.

## Cost

Measured on a Ryzen 7 7800X3D with a Radeon RX 9060 XT (Linux), TU4, a copy of the Xenia
save, warm shader cache, alone on the machine: one run of each from boot to the festival race
at the spawn and 26 s of racing (`tools/perf_scenario.sh race` inputs; about 3 minutes, 5,400
frames). "Texture pack time" is the summary line's command processor time; "per draw" the
`GPU minute` line's command processor CPU time per draw in minutes 2 (loading, free roam, the
race's start) and 3 (racing), which evens out the runs' different draw counts:

| | Texture pack time | A frame on average | Longest load | CPU per draw, minute 2 / 3 | Race p99 frame |
| --- | --- | --- | --- | --- | --- |
| Both off | not run (one test per texture load) | 0 | | 1.71 / 1.57 us | 34.3 ms |
| Use texture packs (28 files, 22 used) | 6.4 ms (hashing 5.8 ms) | 1.2 us | 0.9 ms | 1.67 / 1.58 us | 36.3 ms |
| Dump textures (empty folder, 1,226 dumped) | 36.1 ms (hashing 5.8 ms, copies 28.3 ms) | 6.7 us | 3.1 ms | 1.73 / 1.61 us | 36.5 ms |

Hashing costs about 3 us per texture load (1,900 loads, 107 MiB in the run); dumping adds a
copy of each new texture's data, once (up to 3 ms for one load). The frame rate held 30 fps in
all three; the differences per draw are noise.

## Code

SDK patch 0280 (`docs/sdk_patches.md`): `src/graphics/pipeline/texture/pack.{h,cpp}` (cvars,
fingerprint, the dump writer with its untiling, DDS and PNG encoders, the replacement reader and
its two worker threads), the hook in `TextureCache::CommitPreparedTextureLoad`, and the Vulkan
texture cache's replacement images (`VulkanTextureCache::UploadTexturePackReplacement`,
`GetTexturePackView`). SDK patch 0310: `texture_packs`, the packs to read in order, `.` for the loose files
(`ParsePackOrder` in `pack_format.cpp`, tested by `tools/test_texture_pack_dds.cpp`). The game
sets `texture_pack_root` (`src/forza_app.h`); the launcher's options are in
`launcher/forza_launcher/schema.py`, the packs in `texture_packs.py` (tests:
`launcher/test_texture_packs.py`), Make HD pack in `hd_pack.py` (tests: `launcher/test_hd_pack.py`,
with a stand-in for the upscaler), the page in `ui/texture_packs_page.py` (its list is the Mods
page's, `ui/mods_page.py` `FolderListPage`). The compressor (#413) is `forza-recomp/texcompress`
(`texcompress.{h,cpp}`: mips, encoding, DDS; `main.cpp`: the program and its job list; built by the
game's CMake and staged next to the game by `builder.stage_runtime`), tested by
`texcompress_test.cpp` (`cmake -S forza-recomp/texcompress -B build && cmake --build build && ctest
--test-dir build`: the files decode back within set errors, cut-outs keep their edges, gradients do
not band); the launcher runs it through `texture_compress.py` (tests: `launcher/test_texture_compress.py`,
which builds it with the system's C++ compiler, or takes `FORZA_TEXCOMPRESS`, and decodes its files
back with its `--decode`).
