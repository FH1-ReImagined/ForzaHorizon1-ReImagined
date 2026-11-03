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
effects (`<name>.fxobj`). For example `carbon_fiber.fxobj`'s pixel shader samples
`CarbonFiberASampler`, `envSampler`, `blurredCubemapSampler` and `trackLightmapSampler`;
`chrome.fxobj`'s samples no car texture at all (only the environment), which is why `chrome`
meshes collapse their UVs to one point. For the in-race library (`shaders_v16.fxobj`) the link
from a technique to its shader blobs is inside the effect's binary tables, which are not decoded
yet (see [still unknown](#still-unknown)).

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
- At run time the game scales the offsets by how damaged that area is and by
  `MorphDamageScale` (6.0 in `GlobalCarAttribs.xml`; the same for hood and cockpit views).
  The exact formula (whether the section's half extents also scale it, and how the damage amount
  per vertex is found) is in the vertex shader and was not traced (see below).
- The damaged textures are separate: `damage.xds` / `damagelights.xds` are the damaged
  versions of `nodamage.xds` / `lights.xds`, and `TextureDamageScale` scales that effect.

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

- **Technique → samplers for the in-race library** (`shaders_v16.fxobj` and its variants). The
  effect format links techniques and passes to shader blobs through tables not decoded here;
  the garage `<name>.fxobj` files are decoded (one technique each). How to settle it at run time
  is in the runtime check below.
- A few material names match no technique: `Reserved`, `wingz_LOD0` to `wingz_LOD5`, and the
  brakes' `brake` and `drum` (probably given a prefix such as `brake_` by the brake loader).
- **The morph formula**: what multiplies the decoded offset besides `MorphDamageScale`
  (the section's half extents or not), and how the per-vertex damage amount is computed.
- The 16-byte morph entries of wheel models.
- Unnamed fields: the wheel records, the 48 bytes and lights' details in `CCarModelData`, the
  emitters, the `vec3`, the `f32` and the flag in `CCarPartData`, the position's `w`, the last
  8 floats of `CCarMaterialData`, the wheel files' 0x44 bytes.
- How the buffer keys are made (not CRC-32 or Adler-32 of the data).
- When the game reads the `stripped_` files, and whether `SuperLOD` textures are ever used in
  this game (most cars ship none).

### Runtime checks that would settle them

1. **Samplers per technique** (garage or photo mode, one car): log, for each draw, the pixel
   shader's microcode hash and, for each texture fetch slot it uses, the bound texture's
   fingerprint (the same xxHash3 fingerprint as `texture_dump`). With `texture_dump` on, each
   fingerprint maps to a dumped texture, which you can match to the car's `.xds` files. Offline,
   each pixel shader hash maps to a blob in `shaders_v16.fxobj` (`tools/fxobj_shaders.py` hashes
   them the same way), and that blob's constant table gives the sampler name of each slot. To
   name the technique of each blob, also log the technique name the game looks up when it
   loads the car shaders (the code that reads `CustomCarShaders.xml`, `CCarShaderManager::Init`).
2. **Morph scale**: in a scratch copy of a car, set every `Morph` entry of one section (for
   example `hooda`) to `x = +1.0` (`0x000003FF`), damage the car, and capture a frame
   (RenderDoc on the Vulkan build): the vertex shader's output position minus the undamaged one,
   divided by 1.0, is the scale for that damage amount.

## The tool

`tools/carbin_dump.py` (Python 3, no extra packages) reads `.carbin` files or whole car
archives (the archives' LZX members need `tools/unxmem`, built from `tools/unxmem.c`):

```
tools/carbin_dump.py media/cars/AUD_R8GT_11.zip                 # every file: sections, counts
tools/carbin_dump.py --meshes X.carbin                          # meshes: material, LOD, list, UVs
tools/carbin_dump.py --morph 5 X.carbin                         # morph statistics, 5 entries decoded
tools/carbin_dump.py --media extracted/game/media --meshes X.zip  # material -> effect -> samplers -> textures
tools/carbin_dump.py --json X.carbin                            # all of it as JSON (no vertex data)
```

It prints "parsed to the end" when the whole file matched this description. It reads only, and
prints no vertex data.

## Sources

- The game's executable, decompiled with `tools/ghidra_decomp.sh`: the serializers of
  `CCarModelData`, `CBaseCarModelData`, `CCarPartData`, `CCarSubModel`, `CCarMesh` / `CMesh`,
  `CCarMaterialData` and the vertex and index buffers (names from the field names the serializer
  passes, and from the RTTI).
- The bound vertex shaders in the SDK's shader storage (vertex fetch formats), and the shader
  blobs' own constant tables in the `.fxobj` files.
- The game's `textures.xml`, `CustomCarShaders.xml`, `ShaderSettings.xml`, `CarAttribs.xml` and
  `GlobalCarAttribs.xml`.
- Prior art: Forza Studio (the FM3/FM4-era `.carbin`/`.xds` viewer) has no public source or format
  notes that could be found; Doliman100's
  [ForzaTech-extraction-tools](https://github.com/Doliman100/ForzaTech-extraction-tools) covers the
  later ForzaTech format (Forza Motorsport 5 onwards), which is a different, little-endian
  layout. Nothing here was taken from either.
