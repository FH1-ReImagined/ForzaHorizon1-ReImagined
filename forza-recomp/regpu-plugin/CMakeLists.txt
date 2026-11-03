# rexgpu-regpu: ReGPU (thirdparty/ReGPU, Direct3D 12) as a ReXGlue GPU plugin, loaded by
# --gpu_plugin=regpu in place of the SDK's xenos renderer. Windows only. Built beside forza.exe by
# forza-recomp/CMakeLists.txt (FORZA_REGPU_PLUGIN), with the same compiler as the SDK (clang).

# The SDK install holds include/rex/ppc/context.h and SIMDe, which ReGPU's guest ABI needs.
get_filename_component(_regpu_sdk "${rexglue_DIR}/../../.." ABSOLUTE)
set(_regpu_src "${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/ReGPU")

# Profile-guided build: pgo/drive.profdata is a replayed free-roam drive, 70-130 s (2026-11-02), worth
# about 6% (99.3 -> 105.5 fps). FORZA_REGPU_PGO=generate builds the counting DLL (it writes its profile
# itself, see regpu_graphics_system.cpp; LLVM_PROFILE_FILE names the file), a .profdata path uses that
# one, OFF an ordinary build. Profiles from older sources still apply where they match.
set(FORZA_REGPU_PGO "${CMAKE_CURRENT_SOURCE_DIR}/pgo/drive.profdata" CACHE STRING
    "ReGPU plugin profile-guided build: generate, a .profdata path, or OFF")
set(_regpu_pgo_compile "")
set(_regpu_pgo_link "")
if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
elseif(FORZA_REGPU_PGO STREQUAL "generate")
    set(_regpu_pgo_compile -fprofile-instr-generate)
    set(_regpu_pgo_link -fprofile-instr-generate)
elseif(FORZA_REGPU_PGO AND EXISTS "${FORZA_REGPU_PGO}")
    set(_regpu_pgo_compile "-fprofile-instr-use=${FORZA_REGPU_PGO}" -Wno-profile-instr-unprofiled -Wno-profile-instr-out-of-date)
endif()

# ReGPU without its Vd* exports, against ReXGlue's PPCContext (regpu::driver).
set(REGPU_GUEST_ABI rexglue CACHE STRING "" FORCE)
set(REGPU_REXGLUE_DIR "${_regpu_sdk}" CACHE PATH "" FORCE)
set(REGPU_FFX "${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/fidelityfx-fsr1" CACHE PATH "" FORCE) # FSR 1 shader headers
add_subdirectory("${_regpu_src}" regpu)
target_compile_options(regpu PRIVATE ${_regpu_pgo_compile})

# The shipped pipeline list (Host::pipelineSeed): seed/pipelines.manifest compiled in as text.
set(_regpu_seed "${CMAKE_CURRENT_SOURCE_DIR}/seed/pipelines.manifest")
set(_regpu_seed_cpp "${CMAKE_CURRENT_BINARY_DIR}/fh1_pipeline_seed.cpp")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_regpu_seed}")
file(READ "${_regpu_seed}" _seed_hex HEX)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," _seed_hex "${_seed_hex}")
file(WRITE "${_regpu_seed_cpp}.tmp"
    "// Generated from regpu-plugin/seed/pipelines.manifest.\nextern const char kFh1PipelineSeed[] = {${_seed_hex}0};\n")
file(COPY_FILE "${_regpu_seed_cpp}.tmp" "${_regpu_seed_cpp}" ONLY_IF_DIFFERENT)

# ReXGlue's loader looks for rexgpu-<name> beside the executable.
add_library(rexgpu-regpu SHARED
    src/fh1_boot_screen.cpp
    src/fh1_shaders.cpp
    src/plugin_main.cpp
    src/regpu_graphics_system.cpp
    src/stack_dump.cpp
    "${_regpu_seed_cpp}")
target_compile_features(rexgpu-regpu PRIVATE cxx_std_23)
target_compile_definitions(rexgpu-regpu PRIVATE _CRT_SECURE_NO_WARNINGS)
target_compile_options(rexgpu-regpu PRIVATE ${_regpu_pgo_compile})
target_link_options(rexgpu-regpu PRIVATE ${_regpu_pgo_link})
if(FORZA_REGPU_PGO STREQUAL "generate" AND CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_definitions(rexgpu-regpu PRIVATE GLUE2GPU_PGO_GENERATE)
endif()
target_link_libraries(rexgpu-regpu PRIVATE regpu rex::runtime dbghelp shell32)
# ReGPU's microcode and texture decoders (the shader search, the boot screen's art).
target_include_directories(rexgpu-regpu PRIVATE "${_regpu_src}/src")
set_target_properties(rexgpu-regpu PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
add_custom_command(TARGET rexgpu-regpu POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_BINARY_DIR}/licenses"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${_regpu_src}/LICENSE" "${CMAKE_BINARY_DIR}/licenses/ReGPU.txt"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/fidelityfx-fsr1/LICENSE.txt"
        "${CMAKE_BINARY_DIR}/licenses/AMD-FidelityFX-FSR1.txt")
