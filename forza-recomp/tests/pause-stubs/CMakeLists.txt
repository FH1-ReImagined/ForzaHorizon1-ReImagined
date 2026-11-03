cmake_minimum_required(VERSION 3.25)
project(pause_stubs_checks LANGUAGES CXX)
enable_testing()

# These builds intentionally have no SDK, translations, game files or opt-in defines.
foreach(tu IN ITEMS 0 4)
    set(target pause_stubs_tu${tu})
    add_executable(${target}
        ../pause_stubs_test.cpp
        ../../src/platform/native_quit.cpp
        ../../src/hooks/pause_menu.cpp
        ../../src/hooks/native_pause_scene.cpp
        ../../src/hooks/native_pause_edges.cpp
        ../../src/hooks/fh1_map_preview.cpp)
    target_include_directories(${target} PRIVATE ../../src)
    target_compile_features(${target} PRIVATE cxx_std_23)
    target_compile_definitions(${target} PRIVATE FORZA_TITLE_UPDATE=${tu})
    if(MSVC)
        target_compile_options(${target} PRIVATE /UNDEBUG)
    else()
        target_compile_options(${target} PRIVATE -UNDEBUG)
    endif()
    add_test(NAME ${target} COMMAND ${target})
endforeach()
