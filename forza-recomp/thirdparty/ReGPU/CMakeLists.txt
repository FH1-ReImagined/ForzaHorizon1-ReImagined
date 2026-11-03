cmake_minimum_required(VERSION 3.25)
project(ReGPU CXX)

# ReGPU: the Xbox 360 GPU for XenonRecomp ports (include/regpu/regpu.h). A static library the
# game links; the game's own CMake sets the paths below (or add_subdirectory with them set).
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    message(FATAL_ERROR "ReGPU needs clang-cl, like XenonRecomp's output")
endif()

# xenonrecomp: ReGPU provides the Vd* exports (a XenonRecomp port). rexglue: the host's kernel
# provides them and calls regpu::driver (include/regpu/driver.h); no game headers needed.
set(REGPU_GUEST_ABI "xenonrecomp" CACHE STRING "Guest ABI: xenonrecomp or rexglue")
set_property(CACHE REGPU_GUEST_ABI PROPERTY STRINGS xenonrecomp rexglue)
set(REGPU_XENONRECOMP "" CACHE PATH "XenonRecomp checkout (for XenonUtils/ppc_context.h)")
set(REGPU_PPC_DIR "" CACHE PATH "The game's XenonRecomp output folder (its ppc_config.h)")
set(REGPU_REXGLUE_DIR "" CACHE PATH "ReXGlue SDK source or install (its include/rex/ppc/context.h), with REGPU_GUEST_ABI=rexglue")
set(REGPU_FFX "" CACHE PATH "AMD FidelityFX SDK's Kits/FidelityFX folder (FSR1 shader headers; FSR3 runtime with REGPU_FSR3)")
option(REGPU_FSR3 "AMD FSR 3 upscaling (needs the FidelityFX SDK's signed DLLs)" OFF)
option(REGPU_DLSS "NVIDIA DLSS (needs the DLSS SDK)" OFF)
set(REGPU_DLSS_DIR "" CACHE PATH "NVIDIA DLSS SDK folder (with REGPU_DLSS)")
set(REGPU_D3DCOMPILER "C:/Program Files (x86)/Windows Kits/10/Redist/D3D/x64/d3dcompiler_47.dll" CACHE FILEPATH
    "Redistributable d3dcompiler_47.dll, to ship beside the game executable (src/gpu/d3dcompiler.cpp)")

if(REGPU_GUEST_ABI STREQUAL "xenonrecomp")
    if(NOT EXISTS "${REGPU_XENONRECOMP}/XenonUtils/ppc_context.h")
        message(FATAL_ERROR "Set REGPU_XENONRECOMP to a XenonRecomp checkout")
    endif()
    if(NOT EXISTS "${REGPU_PPC_DIR}/ppc_config.h")
        message(FATAL_ERROR "Set REGPU_PPC_DIR to the game's XenonRecomp output (with ppc_config.h)")
    endif()
    set(REGPU_GUEST_INCLUDES "${REGPU_PPC_DIR}" "${REGPU_XENONRECOMP}/XenonUtils" "${REGPU_XENONRECOMP}/thirdparty/simde")
    set(REGPU_GUEST_DEFINES REGPU_GUEST_EXPORTS=1 REGPU_REXGLUE=0)
elseif(REGPU_GUEST_ABI STREQUAL "rexglue")
    if(NOT EXISTS "${REGPU_REXGLUE_DIR}/include/rex/ppc/context.h")
        message(FATAL_ERROR "Set REGPU_REXGLUE_DIR to a ReXGlue SDK (with include/rex/ppc/context.h)")
    endif()
    # ReXGlue's headers are C++23 and use SIMDe (its thirdparty/simde submodule).
    set(CMAKE_CXX_STANDARD 23)
    set(REGPU_GUEST_INCLUDES "${REGPU_REXGLUE_DIR}/include" "${REGPU_REXGLUE_DIR}/thirdparty/simde")
    set(REGPU_GUEST_DEFINES REGPU_GUEST_EXPORTS=0 REGPU_REXGLUE=1)
else()
    message(FATAL_ERROR "REGPU_GUEST_ABI must be xenonrecomp or rexglue")
endif()
if(NOT EXISTS "${REGPU_FFX}/upscalers/fsr3/include/gpu/fsr1/ffx_fsr1.h")
    message(FATAL_ERROR "Set REGPU_FFX to the FidelityFX SDK's Kits/FidelityFX folder (FSR1 shaders)")
endif()

file(GLOB_RECURSE REGPU_SOURCES CONFIGURE_DEPENDS src/*.cpp)
if(NOT REGPU_FSR3)
    list(REMOVE_ITEM REGPU_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/gpu/fsr3.cpp")
endif()
if(NOT REGPU_DLSS)
    list(REMOVE_ITEM REGPU_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/gpu/dlss.cpp")
endif()

# The FSR1, depth copy, gamma and enhance compute shaders, compiled into a header at build time.
set(REGPU_GENERATED "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${REGPU_GENERATED}")
add_custom_command(OUTPUT "${REGPU_GENERATED}/fsr1_cs.h"
    COMMAND py -3 -I "${CMAKE_CURRENT_SOURCE_DIR}/tools/embed_hlsl.py" "${CMAKE_CURRENT_SOURCE_DIR}/src/gpu/fsr1.hlsl" "${REGPU_GENERATED}/fsr1_cs.h"
            EasuMain,RcasMain,DepthCopyMain,GammaMain,EnhanceMain "${REGPU_FFX}/api/internal/gpu" "${REGPU_FFX}/upscalers/fsr3/include/gpu/fsr1"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/gpu/fsr1.hlsl" "${CMAKE_CURRENT_SOURCE_DIR}/tools/embed_hlsl.py")
list(APPEND REGPU_SOURCES "${REGPU_GENERATED}/fsr1_cs.h")

add_library(regpu STATIC ${REGPU_SOURCES})
target_include_directories(regpu PUBLIC include ${REGPU_GUEST_INCLUDES})
target_compile_definitions(regpu PUBLIC ${REGPU_GUEST_DEFINES})
target_include_directories(regpu PRIVATE src "${REGPU_GENERATED}")
target_compile_definitions(regpu PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN _CRT_SECURE_NO_WARNINGS)
target_compile_options(regpu PRIVATE -msse4.1 -fno-strict-aliasing -gline-tables-only)
target_link_libraries(regpu PUBLIC d3d12 dxgi ws2_32 winmm synchronization onecore)
if(REGPU_FSR3)
    target_compile_definitions(regpu PRIVATE REGPU_FSR3=1)
    target_include_directories(regpu PRIVATE "${REGPU_FFX}/api/include" "${REGPU_FFX}/upscalers/include")
    target_link_libraries(regpu PUBLIC "${REGPU_FFX}/signedbin/amd_fidelityfx_loader_dx12.lib")
endif()
if(REGPU_DLSS)
    target_compile_definitions(regpu PRIVATE REGPU_DLSS=1)
    target_include_directories(regpu PRIVATE "${REGPU_DLSS_DIR}/include")
    target_link_libraries(regpu PUBLIC "${REGPU_DLSS_DIR}/lib/Windows_x86_64/x64/nvsdk_ngx_s.lib")
endif()

# Build check only: ReGPU linked with nothing else (no unresolved symbols from a game).
add_executable(regpu_linkcheck EXCLUDE_FROM_ALL tests/linkcheck.cpp)
target_link_libraries(regpu_linkcheck PRIVATE regpu)

# Xenos shader disassembler / translator check (xenos_dis --hlsl <ucode files>).
add_executable(regpu_dis EXCLUDE_FROM_ALL tools/xenos_dis.cpp)
target_include_directories(regpu_dis PRIVATE src)
target_link_libraries(regpu_dis PRIVATE regpu d3dcompiler)
