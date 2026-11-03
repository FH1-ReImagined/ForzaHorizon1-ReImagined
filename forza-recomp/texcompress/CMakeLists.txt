# forza-texcompress (#413, texcompress.h): texture pack pictures to BC1/BC7 DDS files with mips,
# for the launcher's Make HD pack and Add pack. Built with the game (the launcher stages it next to
# forza) and buildable alone, with its test:
#   cmake -S forza-recomp/texcompress -B build && cmake --build build && ctest --test-dir build
cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(forza_texcompress LANGUAGES CXX)
    include(CTest)
endif()

set(_bc7enc "${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/bc7enc_rdo")
add_library(forza-texcompress-core STATIC
    texcompress.cpp
    "${_bc7enc}/bc7enc.cpp"
    "${_bc7enc}/bc7decomp.cpp"
    "${_bc7enc}/rgbcx.cpp")
target_include_directories(forza-texcompress-core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}"
                           PRIVATE "${_bc7enc}")
target_compile_features(forza-texcompress-core PUBLIC cxx_std_20)
# rgbcx's 969x32 ordering table instead of the 969x128 one: the same results up to its level 14.
target_compile_definitions(forza-texcompress-core PRIVATE RGBCX_USE_SMALLER_TABLES=1)
if(MSVC)
    target_compile_definitions(forza-texcompress-core PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

find_package(Threads REQUIRED)
add_executable(forza-texcompress main.cpp)
target_link_libraries(forza-texcompress PRIVATE forza-texcompress-core Threads::Threads)
set(_texcompress_targets forza-texcompress forza-texcompress-core)
# Next to the game, where the launcher's build copies it from (the generator expression keeps
# multi-config generators from adding a Release/ folder).
set_target_properties(forza-texcompress PROPERTIES RUNTIME_OUTPUT_DIRECTORY "$<1:${CMAKE_BINARY_DIR}>")
add_custom_command(TARGET forza-texcompress POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_BINARY_DIR}/licenses"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${_bc7enc}/LICENSE"
        "${CMAKE_BINARY_DIR}/licenses/bc7enc_rdo.txt"
    VERBATIM)

if(BUILD_TESTING AND CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    add_executable(texcompress_test texcompress_test.cpp)
    target_link_libraries(texcompress_test PRIVATE forza-texcompress-core)
    list(APPEND _texcompress_targets texcompress_test)
    add_test(NAME texcompress COMMAND texcompress_test)
endif()

# Every consumer of the static core must use the same CRT, including the standalone test.
# Keep the runtime linked in so the tool needs no DLL besides the system's.
set_property(TARGET ${_texcompress_targets} PROPERTY
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
