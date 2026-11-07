# Notice

**Forza Horizon 1: ReImagined**: the game hooks, the launcher, the tools and the documentation
of this repository.

Copyright (c) 2026 Dyotson and Forza Horizon 1: ReImagined contributors

SPDX-License-Identifier: GPL-3.0-or-later
(plus the additional permission below; SPDX has no identifier for it)

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License in [`LICENSE`](LICENSE) for
more details.

The additional permission below (GNU GPL version 3, section 7) applies to this program.

## Earlier versions were MIT

Releases and commits before the commit that added this notice were published under the MIT
License, reproduced below. Contributions made under the MIT License stay available under the
MIT License's terms in the versions in which they were published. From that commit on, the
project as a whole is distributed under the GNU General Public License, version 3 or (at your
option) any later version, with the additional permission below. The MIT License allows that,
on the condition that its copyright and permission notice is kept, which is what this section
does.

```text
MIT License

Copyright (c) 2026 Dyotson and Forza Horizon 1: ReImagined contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Additional permission under GNU GPL version 3 section 7

This permission is granted by the copyright holders named above, for the whole of this program.

**Proprietary Components.** In this section, a "Proprietary Component" is software that someone
other than the licensors of this program wrote and distributes under its own terms, and that is
one of the following, as its vendor released it:

1. NVIDIA's DLSS, NGX and Streamline runtime libraries (for example `nvngx_dlss.dll`), and the
   headers and loader libraries that NVIDIA's SDKs supply for them;
2. graphics drivers: the Vulkan, Direct3D, OpenGL or other graphics-API implementations and
   their user-space components, from NVIDIA, AMD, Intel, Apple or any other GPU vendor;
3. OpenXR runtimes and their API layers, such as SteamVR, the Meta (Oculus) runtime and the
   Virtual Desktop streamer;
4. the Discord desktop client, which the game reaches through Discord's local IPC;
5. platform system libraries: the libraries of the operating system the program runs on
   (Windows, macOS or Linux), and Microsoft's C and C++ runtime libraries and Windows SDK
   import libraries.

**The permission.** You may link this program, or a work based on it, with a Proprietary
Component, load a Proprietary Component at run time from it (including one that the user
installed, that the operating system or driver supplies, or that the launcher downloaded from
its vendor), and otherwise combine it with a Proprietary Component, and you may convey the
resulting combination, without the GNU General Public License requiring the Proprietary
Component, or its source code, to be licensed under the GNU General Public License or to be part
of the Corresponding Source.

**What stays the same.**

- The permission is limited to the Proprietary Components listed above. Software does not
  become a Proprietary Component merely because it is loaded at run time, renders graphics or
  runs on a GPU.
- Everything else stays under the GNU General Public License: this program, your changes to it,
  and any code you write to connect it to a Proprietary Component (a wrapper, shim, plugin or
  adapter). You must convey all of it under the GNU General Public License, with its
  Corresponding Source.
- The permission does not license a Proprietary Component. It stays under its vendor's own
  terms, and you must follow them when you distribute it.
- If you modify this program, you may extend this permission to your version, but you are not
  obliged to. If you do not want to, delete this section from your version.

## Third-party components

The launcher Steam shortcut and Deck clock-check approaches in
launcher/forza_launcher/steam_shortcuts.py and deck_clock.py are inspired by Pinyon Shift's
tools/pinyon_steam.py and tools/pinyon_deck.py (BSD-3-Clause). Its notice is retained in
forza-recomp/LICENSE-Pinyon-Shift.

Files and directories of other holders keep their own licenses and notices; the relicensing of
this project does not change them.

| Where | What | License |
| --- | --- | --- |
| `forza-recomp/thirdparty/` | Discord RPC, RapidJSON, bc7enc_rdo, OpenXR headers (details in its `README.md` and the license files beside each) | MIT; MIT; MIT or public domain; Apache-2.0 or MIT |
| `forza-recomp/thirdparty/ReGPU/` | ReGPU (zer0ducksgiven), the Direct3D 12 renderer of the optional ReGPU plugin (`forza-recomp/regpu-plugin/`, `rexgpu-regpu.dll`); a git submodule of github.com/FH1-ReImagined/ReGPU | MIT |
| `forza-recomp/thirdparty/fidelityfx-fsr1/` | FidelityFX SDK FSR 1 shader headers (Advanced Micro Devices) | MIT |
| `forza-recomp/LICENSE-Pinyon-Shift` | notice for code adapted from Pinyon Shift (the ideas ReIGlue takes from it, such as the present step on the submission thread and the thread placement by name, are credited in ReIGlue's `NOTICE`) | BSD-3-Clause |
| `launcher/forza_launcher/haptics/vendor/` | HorizonHaptics (Haritha Rathnayake) | MIT |
| `launcher/forza_launcher/ui/controller_art/` | Gamepad Viewer artwork (Michaël Ferrand) | MIT |
| `launcher/forza_launcher/ui/fonts/` | Bitter ExtraBold (The Bitter Project Authors; the weight-800 instance of Google Fonts' variable Bitter) and Saira Semi Condensed SemiBold and Bold (The Saira Project Authors), from github.com/google/fonts, with their `OFL-*.txt` | SIL Open Font License 1.1 |
| `tools/native_menu/icons/` | Font Awesome Free icons (Fonticons, Inc.) | CC BY 4.0 |
| `patches/rexglue-sdk/` | the old patch series for the ReXGlue SDK (kept for reference, no longer applied) | BSD-3-Clause for the parts derived from the SDK, with its notices |

The ReXGlue SDK (Tom Clay and contributors, with code derived from Xenia) and ReIGlue, this
project's fork of it, are separate projects under their own license (BSD-3-Clause) and are not
covered by this notice. The binaries that the launcher builds and runs include them, and the
packages carry their license under `licenses/`. Downloaded tools (LLVM, CMake, Ninja, xwin,
Real-ESRGAN) and the Microsoft C++ runtime and Windows SDK keep their own licenses too.

Screenshots in `docs/images/` and `screenshots/` show the game, which is (c) Microsoft. They
illustrate the documentation; this license grants no rights to the game's content.
