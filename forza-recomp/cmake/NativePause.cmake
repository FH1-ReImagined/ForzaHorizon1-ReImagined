include_guard(GLOBAL)

# This builds original menu/controller code. It never adds private translation
# include paths. Runtime activation is controlled by forza_native_pause.
option(FORZA_NATIVE_PAUSE "Build the original FH1 native pause menu" ON)

function(forza_validate_native_pause)
    if(FORZA_FH2_NAVIGATION_DIR OR FORZA_FH2_GRID_EVENTS_DIR)
        message(FATAL_ERROR
            "The native menu uses no FH2 inputs. Remove FORZA_FH2_NAVIGATION_DIR "
            "and FORZA_FH2_GRID_EVENTS_DIR from the CMake cache; use FORZA_NATIVE_PAUSE.")
    endif()
endfunction()

function(forza_apply_native_pause target)
    # Native screen layouts and addresses are validated only for TU4. The disc
    # build retains its stock pause menu even when this option has its default.
    if(FORZA_NATIVE_PAUSE AND FORZA_TITLE_UPDATE STREQUAL "4")
        target_compile_definitions(${target} PRIVATE FORZA_NATIVE_PAUSE=1)
    endif()
endfunction()
