cmake_minimum_required(VERSION 3.25)
project(forza_macos_host_check LANGUAGES CXX)

if(NOT APPLE)
    message(FATAL_ERROR "This compile check targets macOS")
endif()
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(FORZA_TITLE_UPDATE 0 CACHE STRING "Game version: 0 or 4")
get_filename_component(FORZA_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
include("${FORZA_ROOT}/forza-recomp/cmake/NativePause.cmake")
find_package(rexglue 0.10.0 REQUIRED CONFIG)

# Compile host integration without game data, generated code, linking or launching.
file(GLOB_RECURSE HOST_SOURCES CONFIGURE_DEPENDS "${FORZA_ROOT}/forza-recomp/src/*.cpp")
list(FILTER HOST_SOURCES EXCLUDE REGEX "/(main|fiber_trace)\\.cpp$")
add_library(forza_host_check OBJECT ${HOST_SOURCES} app_header_check.cpp)
target_include_directories(forza_host_check PRIVATE
    "${FORZA_ROOT}/forza-recomp" "${FORZA_ROOT}/forza-recomp/src"
    "${FORZA_ROOT}/forza-recomp/thirdparty/discord-rpc/include"
    "${FORZA_ROOT}/forza-recomp/thirdparty/discord-rpc/src"
    "${FORZA_ROOT}/forza-recomp/thirdparty/openxr/include")
target_link_libraries(forza_host_check PRIVATE rex::runtime rex::renderdoc)
target_compile_definitions(forza_host_check PRIVATE FORZA_TITLE_UPDATE=${FORZA_TITLE_UPDATE})
forza_apply_native_pause(forza_host_check)

# Exercise the installed plugin ABI and SDK Vulkan device creation without a game.
add_executable(forza_macos_gpu_check gpu_check.cpp)
target_link_libraries(forza_macos_gpu_check PRIVATE rex::runtime rex::renderdoc)
set_target_properties(forza_macos_gpu_check PROPERTIES
    BUILD_WITH_INSTALL_RPATH ON INSTALL_RPATH "@executable_path")
foreach(lib rex::runtime rex::gpu-xenos)
    add_custom_command(TARGET forza_macos_gpu_check POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_FILE:${lib}> $<TARGET_FILE_DIR:forza_macos_gpu_check>
        VERBATIM)
endforeach()
