cmake_minimum_required(VERSION 3.25)
project(forza_linux_host_check LANGUAGES CXX)

# Compile the game's host sources (forza-recomp/src) against an installed ReIGlue without game
# data, generated code, linking or launching. CI uses it for ARM64 Linux
# (.github/workflows/arm64-linux.yml, docs/arm64_linux.md); it works on any Linux CPU.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "This compile check targets Linux (macOS has tools/cmake/macos-host-check)")
endif()
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(FORZA_TITLE_UPDATE 0 CACHE STRING "Game version: 0 or 4")
get_filename_component(FORZA_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
include("${FORZA_ROOT}/forza-recomp/cmake/NativePause.cmake")
find_package(rexglue 0.10.0 REQUIRED CONFIG)

file(GLOB_RECURSE HOST_SOURCES CONFIGURE_DEPENDS "${FORZA_ROOT}/forza-recomp/src/*.cpp")
list(FILTER HOST_SOURCES EXCLUDE REGEX "/main\\.cpp$")
add_library(forza_host_check OBJECT ${HOST_SOURCES}
    "${FORZA_ROOT}/tools/cmake/macos-host-check/app_header_check.cpp")
target_include_directories(forza_host_check PRIVATE
    "${FORZA_ROOT}/forza-recomp" "${FORZA_ROOT}/forza-recomp/src"
    "${FORZA_ROOT}/forza-recomp/thirdparty/discord-rpc/include"
    "${FORZA_ROOT}/forza-recomp/thirdparty/discord-rpc/src"
    "${FORZA_ROOT}/forza-recomp/thirdparty/openxr/include")
# As in forza-recomp/CMakeLists.txt: the Vulkan hooks and OpenXR see the WSI stub headers.
set_source_files_properties("${FORZA_ROOT}/forza-recomp/src/platform/vulkan_hooks.cpp"
    "${FORZA_ROOT}/forza-recomp/src/platform/xr.cpp"
    PROPERTIES INCLUDE_DIRECTORIES "${FORZA_ROOT}/forza-recomp/src/platform/wsi_stubs")
target_link_libraries(forza_host_check PRIVATE rex::runtime)
if(TARGET rex::renderdoc)
    target_link_libraries(forza_host_check PRIVATE rex::renderdoc)
endif()
target_compile_definitions(forza_host_check PRIVATE FORZA_TITLE_UPDATE=${FORZA_TITLE_UPDATE})
forza_apply_native_pause(forza_host_check)
