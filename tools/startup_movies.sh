# Normalize this one tool option before passing it to the SDK. CLI11 sums repeated boolean
# flags, so appending an explicit true after an injected false would otherwise stay false.
# Preserve other arguments; the last startup option wins over FORZA_SKIP_MOVIES.
forza_startup_movie_args() {
    local value="${FORZA_SKIP_MOVIES:-true}" negate=0 neutral=0 arg number
    STARTUP_MOVIE_ARGS=()
    while [ "$#" -gt 0 ]; do
        arg="$1"; shift
        case "$arg" in
            --) STARTUP_MOVIE_ARGS+=(-- "$@"); break ;;
            --skip_startup_movies) value=true; negate=0 ;;
            --no-skip_startup_movies) value=true; negate=1 ;;
            --skip_startup_movies=*) value="${arg#*=}"; negate=0 ;;
            --no-skip_startup_movies=*) value="${arg#*=}"; negate=1 ;;
            *) STARTUP_MOVIE_ARGS+=("$arg") ;;
        esac
    done
    value="${value,,}"
    case "$value" in
        true|t|yes|y|on|enable|+) STARTUP_SKIP_MOVIES=true ;;
        false|f|no|n|off|disable|-|0) STARTUP_SKIP_MOVIES=false ;;
        *)
            # CLI11 accepts signed integers (C-style decimal/octal/hex) as flag values.
            if [[ "$value" =~ ^[+-]?(0x[0-9a-f]+|0[0-7]*|[1-9][0-9]*)$ ]]; then
                number="${value#[-+]}"
                if [[ "$number" =~ ^(0x)?0+$ ]]; then
                    # Numeric zero has a neutral CLI11 count; negating it still gives zero.
                    STARTUP_SKIP_MOVIES=false; neutral=1
                elif [[ "$value" == -* ]]; then
                    STARTUP_SKIP_MOVIES=false
                else
                    STARTUP_SKIP_MOVIES=true
                fi
            else
                echo "invalid skip_startup_movies value: $value" >&2; return 2
            fi ;;
    esac
    if [ "$negate" = 1 ] && [ "$neutral" = 0 ]; then
        if [ "$STARTUP_SKIP_MOVIES" = true ]; then STARTUP_SKIP_MOVIES=false; else STARTUP_SKIP_MOVIES=true; fi
    fi
    STARTUP_MOVIE_ARGS=(--skip_startup_movies="$STARTUP_SKIP_MOVIES" "${STARTUP_MOVIE_ARGS[@]}")
}
