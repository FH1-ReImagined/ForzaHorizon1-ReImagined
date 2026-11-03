option(FORZA_GUEST_FMA "Use hardware FMA for generated guest math (Linux x86-64 experiment)" OFF)

function(forza_configure_guest_fma host)
    if(NOT FORZA_GUEST_FMA)
        return()
    endif()
    if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR
       NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$" OR
       NOT CMAKE_CXX_COMPILER_ID MATCHES "^(Clang|GNU)$")
        message(FATAL_ERROR "FORZA_GUEST_FMA requires Linux x86-64 with Clang or GCC")
    endif()
    if(NOT TARGET ${host}_recomp)
        message(FATAL_ERROR "FORZA_GUEST_FMA requires the SDK's separate generated-code target")
    endif()
    # Includes each target's PCH. Do not put these flags on the host or SDK:
    # the host's ELF preinitializer runs the CPU/OS guard before SDK shared
    # library initializers or generated guest functions can execute.
    foreach(guest ${host}_recomp ${host}_XMediaFacade_default ${host}_SpeechFacade_default)
        if(TARGET ${guest})
            target_compile_options(${guest} PRIVATE -mfma -ffp-contract=off)
        endif()
    endforeach()
    target_compile_definitions(${host} PRIVATE FORZA_GUEST_FMA=1)
    message(STATUS "Guest FMA enabled; CPU and OS AVX/FMA support required at runtime")
endfunction()
