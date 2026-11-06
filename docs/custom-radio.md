# Custom radio

The launcher's Radio page edits the three original station slots. Choose a name,
add local music files or a folder, select **Add** or **Replace**, and optionally
choose a PNG logo. **Build stations** creates and enables the Custom radio mod.
Close the game before building and restart it afterwards. Changes to a playlist
do not take effect until its next successful build.

FFmpeg with libmp3lame is required. Choose its executable with **Locate FFmpeg**
or make it available on PATH. The launcher does not download an encoder or music.
Songs are converted locally to 48 kHz stereo MP3 at 160 kb/s. Each song is limited
to 30 minutes; each station accepts at most 200 songs, subject to the game's 2 GB
bank limit. Logos fit without cropping in the native logo slot, with transparent
space below the artwork to keep track titles readable.

**Export original logos…** saves the game's own station logos, read from your
game files, as PNGs in a folder you choose, with `stock-logos.json` listing each
one's size, texture format, transparency and visible area. The station logos
(`RS_BassArena`, `RS_Pulse`, `RS_2Eleven`) are 210x100 DXT5 textures with 8-bit
alpha; the small station icons (`Radio1` to `Radio3`) are 64x32 DXT1 textures;
`RS_Off` is the 32x32 radio-off icon. A custom PNG replaces both of its station's
textures: it is scaled without cropping and centred in the top 210x72 of the
logo (the HUD writes the song's title and artist over the bottom 28 pixels), and
fitted to the 64x32 icon. A PNG with a transparent background, about 210x72 or
larger in that shape, matches the stock logos best. The exported files are game
assets: nothing is uploaded, and they are for your own reference.

The in-game radio shows each song's title and artist. By default the title is
the file name and the artist is the station name. Select a song and use **Edit
artist and title…** (or double-click it) to change them; the fields start with
the file's own artist and title tags when FFprobe (part of FFmpeg, found next to
the chosen FFmpeg or on PATH) can read them. Leave a field empty to use the
default. The names, up to 100 characters each, are saved in launcher.json and
used at the next **Build stations**; the music file itself is not changed.

**Level songs to the game's loudness** (on by default) measures each song first
(EBU R128, FFmpeg's ebur128 filter) and applies one fixed gain while encoding,
toward -14.5 LUFS, the median of the stock station songs. The gain never pushes
a song's true peak above -3.5 dBTP, so a quiet song with loud peaks is not
raised into clipping. Nothing is compressed or limited. Without it, a current
release mastered near -7 LUFS plays about 7 dB louder than the game's own songs
and can clip when the radio volume is high, because the game mixes the radio
with the car and world sounds without a limiter (#662). Stations built before
this option, or with the other setting, need one more **Build stations**; the
Radio page says so.

Settings stay in launcher.json and generated assets stay in
`<data>/mods/reimagined-radio/`, outside the versioned installation. Updates do
not require adding songs again. The original game bank and selected music files
are read-only. A failed or cancelled build leaves the previously installed mod
in place. The original playlists, bank indexes and compressed audio are retained;
Replace changes the selected station's playlist, rather than deleting stock
samples used elsewhere in the game. DJ dialogue remains the original dialogue.

**Restore this station on next build** removes its customization. When no custom
stations remain, Build disables the generated mod. It can also be disabled on the
Mods page. Other mods that replace the same files may conflict; the normal mod
load order applies. Generated banks and string tables contain game assets and
must stay local.

## Implementation provenance and validation

This feature was independently authored for this repository. No Pinyon Shift
implementation was copied or adapted. Its user-facing feature description was
consulted before implementation; it was not used as source code or a patch.

Format inputs:

- Private inspection of the player's original `Radio_Music.fsb`,
  `RadioSystem.xml`, `RadioSoundbankInfo_Music.xml` and LSB2 string tables.
  The bank contains 127 full FSB4 sample headers and three named SYNC points per
  inspected header. Original sample indexes are preserved by appending samples.
- Public FSB field/flag descriptions in the
  [vgmstream format reader](https://github.com/vgmstream/vgmstream/blob/master/src/meta/fsb.c).
  Format facts informed the new writer; its implementation was not copied.
- This repository's existing `extract_map_points.py` archive reader and
  `xdc_decode.py` Xbox tile helper. FFmpeg runs as an external encoder.

Focused tests cover preservation of original bank bytes, empty-build round trips,
malformed bank rejection, cancellation, MP3 framing, Unicode string rebuilding,
configuration restart, restore behavior and logo tile round trips. Logo export
and the song names (#678) are tested with generated textures and tones: DXT1 and
DXT5 block decoding through the Xbox tiling, the exported PNGs and notes, tag
reading with FFprobe, and the artist and title written to `RadioSystem.xml`.
The stock logo formats were read from TU0 `Horizon.zip`; a TU4 `media.zip` copy
of a logo, if present, takes precedence as for the other radio files.

Windows/Vulkan TU4 in-game checks completed on 2026-10-28 using copied test saves
and two generated test tones. Replacement playback was measured from the audio
output at 523 Hz and 1046 Hz. Add mode retained stock songs; station switching,
custom HUD logos and song/artist labels, pause/resume, and restart were exercised.
The custom station name appears as the artist for imported songs. The localized
station-option strings are covered by file-format tests; the stock Audio page
does not expose a station picker.

The first runtime check exposed a texture-pitch mismatch and artwork overlapping
track labels. The descriptor now matches the tiled row pitch, and large artwork
leaves the native text area transparent. Both fixes were checked in-game.
Restoring all stations through the build backend and restarting the same copied
save restored stock Pulse/Bass Arena logos and music; output measurement detected
neither custom test tone. Source game/profile files were unchanged. Test sessions
ended with bounded supervisor stops, not a qualification of normal game shutdown.

Coverage is Windows/Vulkan with TU4. TU0, Linux, macOS, other audio devices and
long-playlist soak testing remain untested. Original DJ dialogue was observed
over custom playback; these short checks do not qualify every DJ transition.
Generated assets, recordings and the encoder remain private test inputs.
