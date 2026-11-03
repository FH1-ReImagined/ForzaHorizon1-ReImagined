# The .carbin car model format

How Forza Horizon (2012) stores a car model, how a mesh's material leads to its textures, and
how the damage (deformation) data is laid out. Everything here was read from the game's
executable and checked against the game's files: `tools/carbin_dump.py` parses all 4,183
`.carbin` files of the game (3,766 in the 180 archives of `media/cars`, 297 in `media/wheels`,
120 in `media/brakes`) byte for byte to their end. Where something is a reading of the data
rather than of the code, it says so.

- [Conventions](#conventions)
- [Files](#files)
- [Layout](#layout)
- [Materials and textures](#materials-and-textures)
  - [1. How a mesh refers to its material and its textures](#1-how-a-mesh-refers-to-its-material-and-its-textures)
  - [2. How a texture's role is known](#2-how-a-textures-role-is-known)
  - [3. Where the material parameters are](#3-where-the-material-parameters-are)
  - [A new mesh or a new material](#a-new-mesh-or-a-new-material)
- [Samplers of the in-race shaders](#samplers-of-the-in-race-shaders)
- [Damage (deformation) data](#damage-deformation-data)
- [Still unknown](#still-unknown)
- [The tool](#the-tool)
- [Sources](#sources)

## Conventions

- Big-endian (Xbox 360). No padding or alignment anywhere: fields follow each other byte by byte.
- The file is the output of the game's serializer (`IOSys::CBinaryVersionedStream`). Every
  *group* starts with a `u32` version; the reader branches on it. All versions below are the
  ones in the shipped files.
- `string` = `u32` length, then that many bytes (no terminator).
- `array<T>` = `u32` version (1), `u32` count, then count × `T`.
- `vec3` = 3 × `f32`, `vec4` = 4 × `f32`.

## Files

A car archive (`media/cars/<CAR>.zip`, members LZX-compressed, zip method 21) holds:

| File | Root | What |
|---|---|---|
| `<CAR>.carbin` | 5 | Exterior, LOD1 to LOD5 (one vertex buffer per section shared by all five LODs) |
| `<CAR>_lod0.carbin` | 5 | Exterior, LOD0 (the close-up model) |
| `<CAR>_cockpit.carbin` | 5 | Cockpit view model, LOD0 |
| `<CAR>_caliper??_LOD0.carbin`, `_rotor??_LOD0.carbin` | 1 | Brakes of each corner |
| `stripped_<car>*.carbin` | same | The same file with every buffer's data left out (see [buffers](#buffers)) |

`media/wheels/<WHEEL>.zip` holds `<WHEEL>.carbin` (root 3) with its textures, and
`media/brakes/*.rmb.carbin` are generic brakes (root 1).

The first `u32` of the file is the version of the root object and tells the kind: 5 is a car
model (`CCarModelData` comes first), 3 a wheel, 1 a part.

## Layout

```
u32 root                       5 car model, 3 wheel, 1 part
if root == 5: CCarModelData    (below)
if root == 3: 0x44 bytes       wheel data (not decoded)
CBaseCarModelData              (below)
DrawLists                      (below)
array<SubModel> sections       the parts: "body", "hooda", "bumperFa", "glassLF", ...
u32 1, u32 0, u32 1, u32 0, u32 1, u32 0     three empty arrays, always
```

### CCarModelData (root 5 only)

| Type | Field |
|---|---|
| 256 bytes | four 64-byte wheel records (positions and sizes; not decoded) |
| `vec4`, 48 bytes, `vec4` | not decoded |
| `u32` count, count × 32 bytes | car lights: 3 × `f32` position, 3 × `u16`, 9 × `u8`, 3 × `u8`, 2 × `u8` (colours and flags; not decoded further) |

### CBaseCarModelData (version 17)

| Type | Field |
|---|---|
| `u32` | version (17) |
| `u32` | ShaderVersion (16) |
| `array` | Emitters: each `u32` version (2) + 136 bytes (probably the exhausts; not decoded) |
| `u32` version + bytes | DriverControls: version 1: 0x60 bytes, 2: 0xC0 + 16, 3: 0x100 (steering wheel and driver transforms) |
| `u32`, `u32` | StartLOD, EndLOD (1 and 5 in `<CAR>.carbin`, 0 and 0 in the LOD0 files) |
| 15 × 36 bytes | Gauges: `vec4`, `vec4`, 4 bytes each |
| 2 × 4 bytes | not decoded |
| `u32` | not decoded |
| `u32` version, `u32` count, count × `string` | BoneMappingTable (empty in all cars) |

### DrawLists

```
u32 4, u32 4                   always
u32 n                          LODs covered (5 in <CAR>.carbin, 1 elsewhere)
n × list                       alpha-blended meshes, one list per LOD (StartLOD first)
n × list                       opaque meshes, one list per LOD
list = u32 count, count × (u16 section index, u16 mesh index)
```

Every mesh of the file is in exactly one list, the one for its LOD (checked on all files).
The first set holds only the see-through materials (`window`, `glass`, `detail_glass_*`,
`lights_glass`, `blur_rim`); the second set holds everything else. **This is where
transparency is decided for the renderer: a mesh that is not in a list is not drawn.** The
order inside a list is the draw order.

### SubModel (a section, version 5)

A section is one part of the car, named like the game's parts (`bumperFa`, `bumperFrace`,
`hooda`, `glassLF`, `wheel`, `seatL`). Its data, in order:

**CCarPartData**

| Type | Field |
|---|---|
| `u32` | version (5) |
| `vec3` | center |
| `vec3` | bounds min, relative to the center (= −half extents) |
| `vec3` | bounds max, relative to the center (= half extents) |
| `f32` | always 1.0 |
| `vec3` | not decoded (small offsets) |
| `f32` | 0 in most sections; 1e-5, 0.2, 0.3 or 1.0 in some mirrors, wings, exhausts and skirts (not decoded) |
| `u32` 1, `array<16 bytes>`, `array<u16>` | GlassParticles: vertices and indices (only on glass) |
| `u8` | flag, set on glass, lights, wheels, some bumpers (not decoded) |
| `u32` | 0 |

**The model part**

| Type | Field |
|---|---|
| `u32` | version (1) |
| `string` | Name of the section |
| VertexBuffer | `VertexBuffer`: LOD1 to LOD5 vertices (empty in the LOD0 files) |
| `array<Mesh>` | Meshes |
| VertexBuffer | `VertexBuffer_LOD0`: LOD0 vertices (empty in `<CAR>.carbin`) |
| `u32` version, `u8` | MorphTargets: `HasMorphs` |
| VertexBuffer | `Morph`: damage offsets for `VertexBuffer` (see [damage](#damage-deformation-data)) |
| VertexBuffer | `Morph_LOD0`: damage offsets for `VertexBuffer_LOD0` |

### Mesh

Each mesh is a `CCarMaterialData` followed by a `CMesh`:

| Type | Field |
|---|---|
| `u32` | CCarMaterialData version (3) |
| `u8` | RemoveFromShadow (1 on about 1,000 of the game's 246,000 meshes) |
| `vec4` | UV1 factors: offset u, scale u, offset v, scale v (see below) |
| `vec4` | UV2 factors, same order |
| 8 × `f32` | almost always 0 (not decoded) |
| `u32` | CMesh version (2) |
| `string` | **material**: the name of the shader technique, for example `body`, `window`, `hidhead`, `tire` |
| `u32` | LOD (0 to 5) |
| `u32` | primitive type: 6 = triangle strip (all but 22 meshes), 4 = triangle list |
| `u32` | MaterialIndex (0 in all files) |
| `u32` 1, 3 × `vec4` | UnpackingData: position offset, position scale, UV offset/scale (always 0, 1, (0,0,1,1)) |
| IndexBuffer | below |
| `u32` | meshFlags (0 in all files) |

UV factors: the vertex UVs are 16-bit unsigned normalized (0 to 1); the mesh's UV is
`offset + raw × scale` per axis. This fits all the data (a `chrome` mesh has scale 0, so every
vertex samples one texel; a `tire` mesh tiles about 34 times around the tread) and matches the
shaders' `uv1CompressionFactors` and `uv2CompressionFactors` constants; the exact shader math
was not traced.

### Buffers

**VertexBuffer** (version 3; version 2 lacks `flags`):

| Type | Field |
|---|---|
| `u32` | version (3) |
| `u32` | count |
| `u32` | stride in bytes (28 for vertices, 4 for morphs) |
| `u32` | flags: bit 0 = a key follows; bit 1 = the data is not in this file |
| `u32` | key, only if flags bit 0 |
| count × stride | data, unless flags bit 1 |

**IndexBuffer** (version 4):

| Type | Field |
|---|---|
| `u32` | version (4) |
| `u32` | flags (same bits; 0 in all full files) |
| `u32` | key, only if flags bit 0 |
| `u32` | count |
| `u32` | format: 2 = 16-bit indices, 4 = 32-bit |
| count × 2 or 4 | indices, unless flags bit 1. Strips restart at `0xFFFF` (16-bit) or `0x00FFFFFF` (32-bit) |

The key identifies the buffer's contents: identical buffers share a key (the left and right
exhaust's morphs, for example), and the game keeps loaded buffers in a cache by key. The
`stripped_` files are the same files with flags bit 1 set on every buffer and no data; their
vertex and morph buffers keep the full file's keys (their index buffers have none). Neither
CRC-32 nor Adler-32 of the data gives the key, so make a new one when you change data (see
[editing](#editing-safely)).

**Vertex** (28 bytes). The formats come from the vertex fetches of the car shaders as the game
binds them; the names come from the shaders' input signature:

| Offset | Format | Input |
|---|---|---|
| 0 | 4 × s16, signed normalized | `scaledPosition`: x, y, z; w is not position (not decoded) |
| 8 | 2 × u16, unsigned normalized | `uv` (UV1, then the mesh's UV1 factors) |
| 12 | 2 × u16, unsigned normalized | UV2 (read by some shaders only) |
| 16 | 4 × s16, signed normalized | `TanFrameQuat`: the tangent frame as a quaternion (normal, tangent, binormal) |
| 24 | 4 × s8, signed normalized | `SH0`: baked lighting |

Position in the car's space is `center + xyz × half extents` with the section's CCarPartData
(checked against the car's size in `physics/MaxData.xml`).

## Materials and textures

### 1. How a mesh refers to its material and its textures

**Only by name.** The `.carbin` holds no texture names, hashes, ids or indices (no string in any
`.carbin` is a texture file name, and `MaterialIndex` is 0 everywhere). The chain is:

1. A mesh's **material** string (for example `hidhead`) is a **shader technique name**.
2. The technique is looked up in the car shader library. In a race that is
   `media/shaders/Cars/shaders_v16.fxobj` (variants: `shaders_slod_v16`, `shaders_16car_v16`,
   `shaders_splitscreen_v16`, `shaders_ui_v16`). In the cockpit model, a name also has a
   `cockpit_` version: `leather` in `_cockpit.carbin` can only be technique `cockpit_leather`
   (13 cockpit names exist only with the prefix, 25 only without, 13 both; the loader has the
   `cockpit_` string, but which one wins when both exist was not traced). In the garage and
   other menus, the 145 names listed in `media/shaders/Cars/CustomCarShaders.xml` (all marked
   `uiload="true" inraceload="false"`) load their own `<name>.fxobj` instead.
3. The technique's shaders declare **samplers by name** (`baseSampler`, `LightsSampler`,
   `CarbonFiberDiffuseSampler`, ...). The name is all the engine uses to pick a texture.
4. The engine binds each sampler name to a texture file:
   - by `textures.xml` in `media/cars/Shared.zip`: each `<XxxSampler>` lists one or more
     `<Texture value="file.xds" searchPath="%CARFOLDER%|%SHAREDCARFOLDER%|%WHEELFOLDER%"
     heap=... loadFlags=...>`. `loadFlags` picks the entry by model: `LOD15` (the LOD1-5
     model), `LOD0`, `Cockpit`, `SuperLOD`, `!SuperLOD`, combined with `&`;
   - by rules in the game's code for the samplers textures.xml does not list (the file names
     sit next to the sampler names in the executable).

So a material's textures are **per car and per sampler, not per mesh**: every mesh of a car
whose shader samples `LightsSampler` reads the same `lights.xds`, each through its own UVs.

Where the common ones come from (`%CARFOLDER%` is the car's archive):

| Sampler | Texture | From |
|---|---|---|
| `baseSampler` | `nodamage.xds` (LOD1-5), `nodamage_LOD0.xds` (LOD0): the car's diffuse atlas | code |
| `damageSampler` | `damage.xds`, `damage_LOD0.xds`: the damaged version of that atlas | code |
| `LightsSampler` | `lights.xds` (LOD15), `lights_LOD0.xds` (LOD0) | textures.xml |
| `DamageLightsSampler` | `damagelights.xds`, `damagelights_LOD0.xds` | textures.xml |
| `lightsNormalSampler` | `lights_nrm_s_LOD0.xds` | code |
| `CockpitInteriorHiSampler` | `Interior_LOD0.xds` (cockpit) | textures.xml |
| `CockpitGaugeEmissiveSampler` | `interior_emissive_LOD0.xds` | textures.xml |
| `CockpitWoodSampler` | `wood.xds` | textures.xml |
| `NumplateSampler` | `traffic_numplates_DXT1.xds` (shared) | textures.xml |
| `Grille1Sampler`, `Grille2Sampler` | `grille1_alpha.xds`, `grille2_alpha.xds` (shared) | textures.xml |
| `CarbonFiberDiffuseSampler`, `CarbonFiberSampler` | `carbonFiber.xds`, `carbonFiberNormal.xds` (shared) | textures.xml |
| `UndercarriageSampler`, `DirtSampler`, `DirtMaskSampler`, `DamageGlassSampler`, `BumperFrameSampler` | shared files | textures.xml |
| tyre samplers (`treadSampler`, `sidewallSampler`, ...) | `cars/Shared.zip` `tire<letter><digit>.xds` (`tireA0.xds` ...; the digit is likely the LOD) | code (`tire%c%d.xds`) |
| rim (wheel model) | the wheel archive's `wheel.xds`, `wheel<LOD>.xds`, `wheel_LOD0.xds` | code |
| brake rotor | `cars/Shared.zip` `rotor0.xds` ... `rotor4.xds` | code |
| `env*`, `*Cubemap*`, `trackLightmapSampler`, `s_*` | the environment, lighting and shadows | engine |

The livery is separate: `LiveryMasks/*.tga` and `Masks.xml` in the car archive describe where
decals go; they are not material textures.

### 2. How a texture's role is known

From the **sampler name in the shader**, nothing else. A shader's pixel code reads `baseSampler`
as its diffuse, `damageSampler` as the damaged diffuse it blends towards, `*NormalSampler` /
`*_NRM` as normal maps (textures.xml marks them `bias="true"`), `*AOSampler` / `*_OCC` as
ambient occlusion, and so on. What each channel of a texture means (for example the alpha of
`lights.xds`) is decided by that shader's code, not stored anywhere as data. The material
instance (the mesh) has no say: it picks the technique, and the technique fixes the samplers.

Each shader blob in an `.fxobj` carries its own constant table with the samplers it really
uses and their texture slots; `tools/carbin_dump.py --media` prints them for the garage
effects (`<name>.fxobj`), and for each material also the samplers of its in-race technique (marked `race`). For example `carbon_fiber.fxobj`'s pixel shader samples
`CarbonFiberASampler`, `envSampler`, `blurredCubemapSampler` and `trackLightmapSampler`;
`chrome.fxobj`'s samples no car texture at all (only the environment), which is why `chrome`
meshes collapse their UVs to one point. For the in-race library (`shaders_v16.fxobj`) the
technique-to-blob link is not a table that is decoded here, but the blobs are stored in technique
order and that order, checked against what the game draws, gives every technique its
samplers: see [Samplers of the in-race shaders](#samplers-of-the-in-race-shaders).

### 3. Where the material parameters are

| What | Where |
|---|---|
| UV transform of the mesh, RemoveFromShadow | `CCarMaterialData` in the `.carbin` (per mesh) |
| Transparency (alpha-blended or opaque) and draw order | the [DrawLists](#drawlists) in the `.carbin` |
| Shader constants' defaults (`FresnelScalar`, `PaintScale`, `SpecularAmount2`, `GlassColor`, ...) | the effect (`.fxobj`) parameters |
| Per-car overrides of those constants, by technique name | `ShaderSettings.xml` in the car archive (`<glass><GlassMaxOpacity value=.../>`) |
| Paint of the colour slots (`body`, `body_2`, `body_3`) | `ColorShaderSettings1-4.xml` in the car archive; paint types in `Shared.zip` `ShaderSettings/{Normal,Metallic,Matte,Slod,ui}.xml` and `ShaderSettings/Tire/*.xml` |
| Texture files and their LOD/heap rules | `textures.xml` in `Shared.zip` (global, all cars) |
| Render states (depth bias, blending), sampler states | the effect's techniques and passes |
| Damage strength | `GlobalCarAttribs.xml` in `Shared.zip` (`DamageAttribs`: `TextureDamageScale`, `MorphDamageScale`), and `CarAttribs.xml` `<Damage>` in the car archive |

### A new mesh or a new material

There is no material *instance* object with its own textures. To add geometry:

- Give each mesh a material name that exists as a technique (for the cockpit model, as
  `<name>` or `cockpit_<name>`). An unknown name has no shader.
- Put each new mesh in the right [draw list](#drawlists) for its LOD (alpha list for
  see-through materials, opaque list otherwise), or it is not drawn.
- Set `CCarMaterialData` (version 3, RemoveFromShadow 0 to cast shadows, UV1/UV2 factors; use offset 0,
  scale 1 to keep raw UVs) and `CMesh` (version 2, LOD, primitive 6 or 4, MaterialIndex 0,
  UnpackingData 0/1/(0,0,1,1), meshFlags 0).
- A mesh with "its own texture" has to use a sampler the car does not use elsewhere, or a
  free area of the atlas its technique samples (`nodamage.xds` and `damage.xds` for `body`-type
  materials, `lights.xds` for lights). For example a texture in the car's folder that only one
  technique samples (`badge2.xds` via `Badge2Sampler`, `wood.xds` via `CockpitWoodSampler`), or
  edit the shared `textures.xml`, which changes it for every car. A new sampler name needs a new
  shader. The game's own licence plates use `numplate` with the shared
  `traffic_numplates_DXT1.xds` and a per-plate UV constant (`psNumplateUV`).
- Keep `nodamage` and `damage` in the same layout: the damage blend reads both with the same UVs.

## Samplers of the in-race shaders

What each technique of `shaders_v16.fxobj` (the library the race, free roam and photo mode use)
samples, slot by slot. **Measured at run time**, not read from a table: a Title Update 4 build
logged, for every draw, the pixel shader's microcode hash and the texture in each fetch slot
(Forza Horizon 1: ReImagined, Vulkan, free roam, photo mode and the start of a race with the
Aventador and its CPU rivals; 3,500 distinct draw states). Each hash was found in the library's
shader blobs, whose constant table names the sampler of each register. The register number *is*
the texture fetch slot (the log's slot 0 held the car's `nodamage.xds` for the sampler
`baseSampler`, register 0, and the same held for every other sampler whose file was
identified). The base game's
`shaders_v16.fxobj` and the Title Update 4 one have identical sampler tables.

**Which texture file is in a slot** was found with the texture fingerprint of the game's texture
packs ([texture_packs.md](../texture_packs.md)), computed offline for the car's files: an `.xds` is
a 52-byte D3D texture header plus the texture's guest memory (tiled, byte-swapped as on the
console). The header's last 24 bytes are the GPU fetch constant: dword 0 bit 31 tiled and bits
22-30 the pitch in 32-texel units, dword 1 bits 0-5 the format and bits 6-7 the endianness, dword 2
bits 0-12 and 13-25 the width and the height minus 1. The fingerprint is
`XXH3_64(u32 le: 1, format, endianness, width, height, pitch, tiled, packed_mips = 1, u64 le: D)`
with `D = XXH3_64(the blocks of the base level)` (the tiled data as it is when the size in blocks
is a multiple of 32, else the blocks untiled to rows), and the format as the game loads it (the
`..._AS_16_16_16_16` DXT variants 51 and 53 load as DXT1 = 18 and DXT5 = 20). `nodamage.xds`,
`damage.xds`, `lights.xds`, `damageLights.xds` of the Aventador, the grille, dirt, glass, carbon
and tyre textures all matched exactly. A texture without a fingerprint is one the engine writes
(the scene copies, the environment cubes, the shadow mask).

**Which technique a shader belongs to**: the library stores the pixel shaders (240) in the order
of its techniques (254 names in its string table, between the effect's parameter names and
`D3DX9.1 Effect Compiler`, leaving out the pass names `p0`, `p1`, `Cleanup` that are in the same
list): one shader per
technique from `body_traffic` to `brake_badge`, except that `cockpit_ghostInterior` shares the
shader before it and `detail_glass_red`, `detail_glass_clear` and `detail_glass_amber` have two
passes (two shaders each); the last 39 shaders belong one each to `tire` to `missingBrakePink`;
the 31 shadow techniques between (`shadowMap` to `dropShadowBrake`) share 15 shaders in a way not
decoded (they are not drawn into the car view, so their samplers are not listed). This is
checked in two ways: the samplers fit the names all along (`numberplate` has `Numplate`,
`undercarriage` has `Undercarriage`, `badge2` has `Badge2`, the six gauge techniques have
`CockpitGaugeEmissive`, `blur_rim` has `blurRim1-4`, ...), and for each of 144 pairs of a car
and a lights shader seen drawing with that car's own `lights.xds` (32 techniques, 11 cars), the
car has the shader's technique as a material in its model (144 of 144). 109 of the 223 techniques
with a shader were seen drawing in the session. `tools/carbin_dump.py --media` applies the same
rule and prints the samplers of each material.

A technique has the same slots on every car. Slot 13 (`ShadowMaskSamp`) is the
engine's screen-space shadow mask in every technique that has it; `env*` are engine textures
(`envSampler` a 256x256 cube, `envStaticSampler` a 128x128 cube, `envBackgroundSampler` a
copy of the scene).

**body** (also `body_2`, `body_3`; the paint colour comes from constants, not a texture)

| slot | sampler | texture |
|---|---|---|
| 0 | `baseSampler` | the car's `nodamage.xds` (LOD0 model: `nodamage_LOD0.xds`) |
| 1 | `damageSampler` | `damage.xds` (`damage_LOD0.xds`) |
| 2 | `DirtSampler` | `Shared.zip` `dirt_pattern.xds` |
| 3 | `DirtMaskSampler` | `Shared.zip` `dirt_mask.xds` |
| 4 | `envBackgroundSampler` | engine (scene copy) |
| 5 | `envSampler` | engine (environment cube) |
| 6 | `envStaticSampler` | engine (static cube) |
| 13 | `ShadowMaskSamp` | engine |

**tire** (and `tire_V2`; the tyre is drawn from three pictures of the tread and sidewall)

| slot | sampler | texture |
|---|---|---|
| 1 | `blurTireSampler` | `Shared.zip` `tireA0.xds` (`tireB0.xds`, `tireC0.xds` for tyre types B, C) |
| 2 | `blurTireSampler2` | `tireA1.xds` (`tireB1`, ...) |
| 3 | `blurTireSampler3` | `tireA2.xds` (`tireB2`, ...) |
| 4 | `blurTireSampler4` | none (a 32x32 engine placeholder) |
| 13 | `ShadowMaskSamp` | engine |

`tread` samples `treadSampler` in slot 1 and `treadNormalSampler` in slot 2 (`treadA_CLR.xds`,
`treadA_NRM.xds` in `media/wheels/tirea.zip`, the B and C tyres in `TireB.zip`, `TireC.zip`);
`sidewall` and `scaling_text` sample `sidewallSampler` (slot 1, `sidewallA_CLR.xds`) and
`sidewallNormalSampler` (slot 2, `sidewallA_NRM.xds`; in `tirea.zip` and the `Tire?<car>.zip`
archives). `wheel_black` samples nothing.

**rim** (and `rim_V2`, `outer_rim`, `inner_rim`, `inner_rim_V2`, `blur_lip`, `blur_lip_V2`,
`wheel_emblem`, `wheel_emblem_V2`, `rim_bump_metal`, `rim_bump_metal2`)

| slot | sampler | texture |
|---|---|---|
| 0 | `baseSampler` | the wheel's `wheel.xds` (LOD0: `wheel_LOD0.xds`), in `media/wheels/<wheel>.zip` |
| 1 | `envSampler` | engine |
| 2 | `envStaticSampler` | engine |
| 13 | `ShadowMaskSamp` | engine |

`chrome_rim`, `chrome_rim_V2`, `chrome_blur_lip` and `chrome_blur_lip_V2` are the same without
slot 0 (no texture). `blur_rim`, `blur_rim_V2`, `chrome_blur_rim` and `chrome_blur_rim_V2` (the
spinning rim) have no slot 0 either, and slots 1 and 2 as above plus `blurRimSampler1`
(slot 3, `wheel0.xds`), `blurRimSampler2` (4, `wheel1.xds`), `blurRimSampler3` (5, `wheel3.xds`)
and `blurRimSampler4` (6, a 32x32 engine placeholder), all from the wheel's archive.

**hidhead** (and the other 50 lights techniques: `head_light`, `xenonhead`, `oldhead`, `fullbeam`,
`fogwhite`, `fogred`, `tail_light`, `tail_light_lod0`, `tail_light_on`, `tail_light2`, `taillightst`,
`taillight2S`, `brkreverse`, `linred`, `rinred`, `indicator_left`, `indicator_right`, the `drl*`
daytime lights, `numplate`, `brinl`, `brinr`, `lindtailside`, `rindtailside`, `slorange`,
`slinlorange`, `slinrorange`, `textured_reflector`, `reflector`, `lights_glass` and `lights_gls_*`)

| slot | sampler | texture |
|---|---|---|
| 1 | `LightsSampler` | the car's `lights.xds` (LOD0 model: `lights_LOD0.xds`) |
| 2 | `envSampler` | engine |
| 3 | `envStaticSampler` | engine |
| 4 | `DamageLightsSampler` | `damageLights.xds` (`damageLights_LOD0.xds`) |
| 13 | `ShadowMaskSamp` | engine |

`lights.xds` is one atlas for all the car's lamps: a lights mesh's UVs pick its lamp, and the
technique decides the colour and glow it reads from it. Only `reverse_light` and
`reverse_light_lod0` lack slot 4. `head_light2` is the exception among the head lamps: it samples
only `baseSampler` (0), `envSampler` (1) and `envStaticSampler` (2), like `white_brake`, `badge`
and the rim group. The three `detail_glass_*` techniques have two passes: the first samples
`LightsSampler` (1) and `DamageLightsSampler` (2) only, the second `envBackgroundSampler` (1),
`envSampler` (2), `envStaticSampler` (3) and `DamageLightsSampler` (4).

Other techniques, in groups with the same samplers (all of them are in the tool's output; slots
in brackets; every one that draws into the car view also has `ShadowMaskSamp` in slot 13, except
the interior-only gauge and mirror ones):

| techniques | samplers (slot) |
|---|---|
| `body_2`, `body_3`, `cockpit_body`, `lights_body`, `headlight_paint`, `stripe_badge` | as `body` |
| `misc`, `liveryBadge` | `base` 0, `damage` 1, `DirtMask` 2, `envBackground` 3, `env` 4, `envStatic` 5 |
| `race_kit_red`, `cage`, `weatherstrip`, `frame` | `base` 0, `damage` 1, `DirtMask` 2, `env` 3, `envStatic` 4 |
| `engine`, `plastic`, `plastic2`, `dullplastic`, `embossed_plastic*`, `matte`, `matte_colors*` | `base` 0, `damage` 1, `DirtMask` 2 |
| `interior`, `defrost_lines` | `base` 0, `damage` 1 |
| `interior_banner` | `base` 0, `damage` 1, `Dirt` 2, `DirtMask` 3 |
| `rubber_trim`, `rubber_trim_2` | `Dirt` 1, `DirtMask` 2 |
| `emblem` | `base` 0, `DirtMask` 1, `env` 2, `envStatic` 3 |
| `badge2` | as `emblem`, plus `Badge2Sampler` 4 (the car's `badge2.xds`) |
| `badge`, `white_brake`, `head_light2` | `base` 0, `env` 1, `envStatic` 2 |
| `mottled`, `cloth`, `cloth2`, `caliper`, `rotor`, `hub`, `brake_badge` | `base` 0 (a brake's own picture, for example `Shared.zip` `rotor0.xds`) |
| `bottom`, `bottom_2` | `damage` 1, `DirtMask` 2, `env` 3, `envStatic` 4 |
| `undercarriage` | `damage` 1, `DirtMask` 2, `UndercarriageSampler` 3 (`Shared.zip` `undercarriage.xds`), `env` 4, `envStatic` 5 |
| `body_traffic` | `base` 0, `env` 1, `NumplateSampler` 2 (`Shared.zip` `traffic_numplates_DXT1.xds`) |
| `numberplate` | `env` 1, `NumplateSampler` 2 |
| `window`, `window_2`, `frontwindow*`, `inside_window`, `glass`, `glass_2`, `cockpit_glass`, `red_glass` | `envBackground` 1, `env` 2, `envStatic` 3, `DamageGlassSampler` 4 (`Shared.zip` `glass_texture.xds`) |
| `window_traffic`, `window_traffic_dark` | `env` 1, `DamageGlass` 2 |
| `chrome`, `chrome_2`, `chrome_3`, `badge_chrome`, `black_glass` | `env` 1, `envStatic` 2 |
| `chrome_bump` | `ChromeNormal` 1, `env` 2, `envStatic` 3 |
| `lights_chrome` | `env` 1, `envStatic` 2, `lightsNormal` 3 |
| `black_glass_breakable` | `env` 1, `envStatic` 2, `DamageGlass` 3 |
| `carbon_fiber`, `carbon_fiber2`, `carbon_fiber3` | `CarbonFiberDiffuseSampler` 1 (`Shared.zip` `carbonFiber.xds`), `CarbonFiberSampler` 2 (`carbonFiberNormal.xds`), `envBackground` 3, `env` 4, `envStatic` 5 |
| `carbon_fiber_aniso` | `DirtMask` 1, `CarbonFiber` 2, `envBackground` 3, `env` 4, `envStatic` 5 |
| `grille1`, `grille1_alpha`; `grille2`, `grille2_alpha` | `Grille1Sampler` 1 (`grille1_alpha.xds`); `Grille2Sampler` 1 (`grille2_alpha.xds`) |
| `bumper_frame` | `BumperFrameSampler` 1 (`Shared.zip` `bumper_frame.xds`) |
| `ZOnly`, `ghost*`, `black`, `wheel_black`, `gauge_red`, `geoShadow*`, `missing*`, the shadow-pass techniques | none (the shadow ones are not listed) |

The `cockpit_*` and gauge techniques use `CockpitInteriorHiSampler` (the car's `interior_LOD0.xds`),
`CockpitGaugeEmissiveSampler` (`interior_emissive_LOD0.xds`), the normal maps of `textures.xml`,
`digitalGaugeSampler` (engine) and `mirrorSamplerDefault` (engine); the tool prints their slots.

## Damage (deformation) data

The shape of the damage lives in the `Morph` and `Morph_LOD0` buffers of each section that can
deform (`HasMorphs` = 1): the body panels, bumpers, hood, trunk, wings, skirts, lights, mirrors,
exhausts and glass. Seats, interior, wheels, brakes and the steering wheel have none.

- `Morph` has one entry per vertex of the section's `VertexBuffer` (LOD1-5), `Morph_LOD0` one
  per vertex of `VertexBuffer_LOD0`, in the same order; entry *i* belongs to vertex *i*. The
  count always equals the vertex count, and in car models the stride is 4. A section without
  morphs has `HasMorphs` = 0 and both buffers empty (count 0, stride 0). (Wheel models with
  morphs use 16-byte entries instead, a different format not decoded here.)
- Each entry is one big-endian `u32` in the Xbox 360 vertex format `FMT_10_11_11`, signed and
  normalized (that is how the game's shaders fetch it, at stream 4 bytes wide):

  ```
  bits  0-10  x   11-bit two's complement
  bits 11-21  y   11-bit two's complement
  bits 22-31  z   10-bit two's complement
  value = raw / 1023 for x and y, raw / 511 for z  (the most negative raw value is -1.0)
  ```

  The field boundaries were confirmed on the data too: in the non-zero entries of six cars,
  neighbouring bits agree almost always just below each boundary (sign extension) and the
  agreement drops exactly at bits 10|11 and 21|22.
- An entry is the direction and size of that vertex's dent in the car's axes (the same axes
  as the positions; the front of the car is at −z). Zero means the vertex does not move. The
  data is consistent with that: the front bumper's offsets point rearwards (+z) and the rear
  bumper's forwards (−z), and left and right parts mirror each other in x. Most values are small (a few tens of 1023); undercarriages and some
  body panels use large ones, up to the full range.
- At run time the vertex shader adds the offset to the vertex position, scaled by how damaged
  that spot of the car is: see [The morph formula](#the-morph-formula).
- The damaged textures are separate: `damage.xds` / `damagelights.xds` are the damaged
  versions of `nodamage.xds` / `lights.xds`, and `TextureDamageScale` scales that effect.

### The morph formula

Read from the car vertex shader (`body` and the other paint techniques; its microcode as the
game binds it, Xenos assembly) and **measured in the game** with a changed car.

```
D        = damage at the vertex, 0..1 (see below)
scale    = clamp(D, 0, 1) * MorphDamageScale        (6.0), clamped again to 0..1
offset   = 2 * decode(Morph entry)                  (decode as above: x, y /1023, z /511)
position = unpacked position + offset * scale       (model space, metres)
```

- The shader doubles the decoded offset: an entry of `x = +1.0` (`0x000003FF`) moves the vertex
  **2.0 m** at full damage, and the range of an entry is ±2 m.
- `MorphDamageScale` reaches the shader as the fourth component of a constant (`c58` = 0, 1, 1,
  6: the clamp bounds of `D`, `TextureDamageScale`, `MorphDamageScale`). The product is
  saturated, so **any damage of 1/6 or more moves the vertex by the full offset**, and below that
  the offset grows linearly with `D` (at `D` = 0.05 it is 0.3 of the offset). Raising
  `MorphDamageScale` makes a car dent fully from a lighter hit; it never exceeds the stored offset.
- `D` is read from a damage grid the game keeps for each car: 10 x 10 x 10 cells of two `vec4`
  (the eight corner values of the cell), over the car's bounding box (the shader constants hold
  its corner and the reciprocal of its size: 2.27 x 1.68 x 4.91 m in one draw's constants), and the shader
  interpolates between the corners (trilinear). So a section's offsets are not scaled by the
  section's own extents or by anything stored per section: only by the grid at the vertex's
  position. A section with no damage in its part of the grid does not move, whatever its `Morph`.
- `Morph` is only fetched when a constant (`c127.x`) is non-zero (it was 1 in the draws logged).
  The hood and cockpit views have their own scale in `GlobalCarAttribs.xml`
  (`HoodViewAndCockpitViewMorphDamageScale`, 6.0 as well); that one was not logged.

Measured with a copy of the Aventador in which every `Morph` entry of `bumperFa` (the front
bumper, 10,892 LOD0 vertices) is `0x000003FF`, the damage grid filled with one value, and the
game's photo mode (front view, 1280x720), with the bumper's position taken from the car in
the unchanged run:

| grid value `D` | `scale` | bumper shift (the emblem), predicted | measured |
|---|---|---|---|
| 0 | 0 | none | none (the car as shipped) |
| 0.05 | 0.3 | 0.6 m | about 180 px (the emblem; the bumper's tips 155 to 165 px) |
| 1.0 | 1.0 | 2.0 m | about 600 px, the whole bumper beside the car |

The pixel values are read off the screenshots. The bumper is about 1.95 m wide and 480 px wide
at that depth (246 px per metre), so 0.6 m is about 150 px (seen 155 to 180, with the
perspective) and the ratio of the two shifts is about 3.4 (predicted 1 / 0.3 = 3.33). With a factor of 1 instead of 2 the 0.05 case would be 75 px. The
bumper moved to the car's right (to the left of the picture, looking at the front), the
direction of `+x` in the car's axes.

### Editing safely

- Keep `count` of `Morph` equal to `VertexBuffer` and `Morph_LOD0` equal to `VertexBuffer_LOD0`.
  When you add vertices, add one entry each (0 for "does not deform"); when you remove or
  reorder vertices, do the same to the morphs.
- To encode `(x, y, z)` in −1..1:
  `(round(x*1023) & 0x7FF) | (round(y*1023) & 0x7FF) << 11 | (round(z*511) & 0x3FF) << 22`.
- To make a new part deformable: `HasMorphs` = 1, both morph buffers filled, stride 4. Leave
  `HasMorphs` = 0 and the buffers empty for parts that must stay rigid.
- A good starting point for a new panel is the offsets of the nearest vertex of the original
  panel (the dent directions follow the panel's position on the car).
- **Keys:** when you change a buffer (vertices, indices or morphs), give it a new key that no
  other buffer uses (any unused 32-bit value), or clear flags bit 0 and drop the key. Buffers
  that shared a key with another buffer must stop sharing it when one of them changes. Then
  update the matching `stripped_` file: same sections, meshes, counts, strides and keys, with
  flags bit 1 set and no data (when the game reads it is not known).
- The vertex count of a section has to fit its index format: 16-bit index buffers address up to
  65,535 vertices (indices 0 to `0xFFFE`; `0xFFFF` is the restart); use format 4 above that.

## Still unknown

- The 31 shadow techniques (`shadowMap` to `dropShadowBrake`) share 15 pixel shaders, and which
  technique uses which was not decoded. They are never drawn into the car's picture, only into
  the shadow maps, and the other techniques do not depend on them.
- A few material names match no technique: `Reserved`, `wingz_LOD0` to `wingz_LOD5`, and the
  brakes' `brake` and `drum` (probably given a prefix such as `brake_` by the brake loader).
- The axes of `y` and `z` in a morph entry were not measured separately (`x` was: it moves the
  vertex along the car's `+x`); the shader adds the entry to the position in the same space.
  How the game fills the damage grid from a crash (the 10 x 10 x 10 values) is not decoded.
- The 16-byte morph entries of wheel models.
- Unnamed fields: the wheel records, the 48 bytes and lights' details in `CCarModelData`, the
  emitters, the `vec3`, the `f32` and the flag in `CCarPartData`, the position's `w`, the last
  8 floats of `CCarMaterialData`, the wheel files' 0x44 bytes.
- How the buffer keys are made (not CRC-32 or Adler-32 of the data).
- When the game reads the `stripped_` files, and whether `SuperLOD` textures are ever used in
  this game (most cars ship none).

### How the runtime checks were made

Both were done on a Title Update 4 build of this project with the free-roam car of the test
save (the Aventador), in scratch folders (scratch data, config and save copies; the car file
changed through a [mod](../modding.md) folder, never in the game's files).

1. **Samplers.** `tools/carbin_runtime/reiglue-draw-textures.patch` is a small change to
   ReIGlue (the project's SDK fork, against the revision `tools/build_rexglue_sdk.sh` pinned
   when this was written). With `FORZA_DRAW_TEXTURES=<file>` the GPU plugin writes one line for
   each new combination of pixel shader and bound textures: `ps <hash> vs <hash>` and, for each
   fetch slot the pixel shader reads, `t<slot>=<fingerprint>:<w>x<h>:f<format>:d<dimension>`
   (the fingerprint is the texture pack's, so `texture_dump=true` must be on; it is 0 for a
   texture the engine writes). `FORZA_DRAW_VS_CONSTS=<vertex shader hash>` also writes that
   vertex shader's float constants for its first 16 draws. Both are off unless set. The hash is
   `tools/fxobj_shaders.py`'s (XXH3-64 of the big-endian microcode); `--dump_shaders=<folder>`
   writes the shader's Xenos assembly. Offline: each pixel shader hash to its blob in
   `shaders_v16.fxobj` (the Title Update 4 `media.zip` copy, which the game uses) and its
   constant table; each fingerprint to a file as described above.
2. **Morph scale.** A mod folder with the car's `LAM_LP7004_12_lod0.carbin` and
   `LAM_LP7004_12.carbin` in which every `Morph` and `Morph_LOD0` entry of `bumperFa` was set to
   `0x000003FF` (4 bytes each at the buffer's data offset, as `tools/carbin_dump.py` finds it),
   loaded through `--forza_mods_dir`. The damage grid of the car's draws was filled in the plugin
   with a fixed value (a local test aid, not in the patch; a game could not be damaged to the same
   amount twice by hand) and photo mode's picture compared with the unchanged run.

## The tool

`tools/carbin_dump.py` (Python 3, no extra packages) reads `.carbin` files or whole car
archives (the archives' LZX members need `tools/unxmem`, built from `tools/unxmem.c`):

```
tools/carbin_dump.py media/cars/AUD_R8GT_11.zip                 # every file: sections, counts
tools/carbin_dump.py --meshes X.carbin                          # meshes: material, LOD, list, UVs
tools/carbin_dump.py --morph 5 X.carbin                         # morph statistics, 5 entries decoded
tools/carbin_dump.py --media extracted/game/media --meshes X.zip  # material -> race samplers (slot) and garage effect -> textures
tools/carbin_dump.py --json X.carbin                            # all of it as JSON (no vertex data)
```

With `--media` each material also lists its in-race samplers with their texture slots
(`race <sampler> (sN)`) and the texture each one picks, from `shaders_v16.fxobj` as described in
[Samplers of the in-race shaders](#samplers-of-the-in-race-shaders). Nothing is listed for a
technique that is not placed there (the shadow techniques), or when the library does not have the
layout checked there. The tool prints "parsed to the end" when the whole file matched this
description. It reads only, and prints no vertex data.

## Sources

- The game's executable, decompiled with `tools/ghidra_decomp.sh`: the serializers of
  `CCarModelData`, `CBaseCarModelData`, `CCarPartData`, `CCarSubModel`, `CCarMesh` / `CMesh`,
  `CCarMaterialData` and the vertex and index buffers (names from the field names the serializer
  passes, and from the RTTI).
- The bound vertex shaders in the SDK's shader storage (vertex fetch formats), and the shader
  blobs' own constant tables in the `.fxobj` files.
- The game's `textures.xml`, `CustomCarShaders.xml`, `ShaderSettings.xml`, `CarAttribs.xml` and
  `GlobalCarAttribs.xml`.
- A run of the game with the draw log of
  [How the runtime checks were made](#how-the-runtime-checks-were-made): the pixel shaders and
  textures of every draw, and the car vertex shader's assembly and constants.
- Prior art: Forza Studio (the FM3/FM4-era `.carbin`/`.xds` viewer) has no public source or format
  notes that could be found; Doliman100's
  [ForzaTech-extraction-tools](https://github.com/Doliman100/ForzaTech-extraction-tools) covers the
  later ForzaTech format (Forza Motorsport 5 onwards), which is a different, little-endian
  layout. Nothing here was taken from either.
