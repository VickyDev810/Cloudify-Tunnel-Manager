#!/usr/bin/env bash
# Cloudify safety check: shows everything on this machine that is reachable through Cloudflare (temporary
# tunnels, SSH sharing, persistent tunnels, the dashboard) and can stop it, or remove Cloudify and cloudflared
# completely. It needs only bash and standard tools, so it works whether Cloudify is installed or not.
# Linux and macOS (Windows: inside WSL).
#
#   bash safety-check.sh                    list everything (changes nothing)
#   bash safety-check.sh --kill             stop temporary tunnels and SSH sharing sessions
#   bash safety-check.sh --kill-all         also stop persistent tunnels and the dashboard
#   bash safety-check.sh --remove           stop everything, remove auto-start, uninstall Cloudify and cloudflared
#   bash safety-check.sh --remove --purge   ...and delete ~/.cloudflared (certificates, tunnel credentials)
#
# Without downloading it first:
#   curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/safety-check.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/safety-check.sh | bash -s -- --kill

# shellcheck disable=SC2088  # "~/..." in messages is meant literally

usage() {
    cat <<'EOF'
Cloudify safety check (Linux, macOS)

Lists everything on this machine that is reachable through Cloudflare, and can stop it.
Works whether Cloudify is installed or not. Without options it only lists and changes nothing.

Usage: safety-check.sh [options]

  --kill        stop temporary tunnels (trycloudflare.com addresses) and SSH sharing sessions
  --kill-all    like --kill, and also stop persistent tunnels and the Cloudify dashboard
  --remove      stop everything, remove auto-start entries, uninstall Cloudify and cloudflared
  --purge       with --remove: also delete ~/.cloudflared (Cloudflare certificates, tunnel
                credentials, Cloudify accounts). This cannot be undone.
  -y, --yes     don't ask for confirmation
  -h, --help    show this help

Only your own processes are stopped; run with sudo to stop other users' processes too.
EOF
}

KILL=false
KILL_ALL=false
REMOVE=false
PURGE=false
YES=false
FAILED=0

# ------------------------------------------------------------------ output

setup_output() {
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
        BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
    else
        BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
    fi
}
section() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
item()    { printf '  %s\n' "$1"; }
detail()  { printf '      %s%s%s\n' "$DIM" "$1" "$RESET"; }
alert()   { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
ok()      { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
fail()    { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; FAILED=1; }
none()    { printf '  %snone%s\n' "$DIM" "$RESET"; }
have()    { command -v "$1" >/dev/null 2>&1; }
pretty()  { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }

# ------------------------------------------------------------------ processes

P_PID=(); P_PPID=(); P_UID=(); P_USER=(); P_ETIME=(); P_ARGS=()

process_list() {  # one line per process: PID PPID UID USER ELAPSED ARGS
    local out d pid ppid uid args
    if have ps; then
        # -ww: never cut long command lines (ps limits them to the terminal width otherwise)
        out="$(ps -ww -A -o pid= -o ppid= -o uid= -o user= -o etime= -o args= 2>/dev/null)"
        if [ -n "$out" ]; then printf '%s\n' "$out"; return; fi
    fi
    [ -r /proc/self/stat ] || return  # Linux without a full ps (BusyBox, minimal containers): read /proc
    for d in /proc/[0-9]*; do
        pid="${d#/proc/}"
        args="$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)"
        [ -n "$args" ] || continue
        ppid="$(sed -n 's/^PPid:[[:space:]]*//p' "$d/status" 2>/dev/null)"
        uid="$(sed -n 's/^Uid:[[:space:]]*\([0-9]*\).*/\1/p' "$d/status" 2>/dev/null)"
        printf '%s %s %s %s %s %s\n' "$pid" "${ppid:-0}" "${uid:-0}" "$(user_name "${uid:-0}")" "-" "$args"
    done
}

user_name() {  # UID -> user name
    local n
    n="$(awk -F: -v u="$1" '$3 == u { print $1; exit }' /etc/passwd 2>/dev/null)"
    printf '%s' "${n:-$1}"
}

load_processes() {
    P_PID=(); P_PPID=(); P_UID=(); P_USER=(); P_ETIME=(); P_ARGS=()
    local pid ppid uid user etime args
    while read -r pid ppid uid user etime args; do
        [ -n "$args" ] || continue
        [ "$pid" = "$$" ] && continue
        P_PID+=("$pid"); P_PPID+=("$ppid"); P_UID+=("$uid"); P_USER+=("$user"); P_ETIME+=("$etime")
        P_ARGS+=("$args")
    done < <(process_list)
}

alive() {  # alive PID: running and not a zombie
    local state
    kill -0 "$1" 2>/dev/null || return 1
    if [ -r "/proc/$1/stat" ]; then
        state="$(sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -d' ' -f1)"
    else
        state="$(ps -o stat= -p "$1" 2>/dev/null)"
    fi
    case "$state" in Z*) return 1 ;; esac
    return 0
}

name_of() { local s="${1%/}"; printf '%s' "${s##*/}"; }

cloudflared_args() {  # ARGS -> the arguments after the cloudflared binary; fails for other programs
    local args="$1" first rest second
    first="${args%% *}"
    rest=""
    [ "$first" = "$args" ] || rest="${args#* }"
    case "$(name_of "$first")" in
        cloudflared|cloudflared.exe) printf '%s' "$rest"; return 0 ;;
        python|python3|python3.*)  # a script called cloudflared (e.g. a test stand-in)
            second="${rest%% *}"
            if [ "$(name_of "$second")" = cloudflared ]; then
                if [ "$second" = "$rest" ]; then rest=""; else rest="${rest#* }"; fi
                printf '%s' "$rest"
                return 0
            fi ;;
    esac
    return 1
}

# Parsed cloudflared command line (set by parse_cloudflared; values are never printed for secrets)
CF_KIND=""; CF_URL=""; CF_NAME=""; CF_CONFIG=""; CF_METRICS=""; CF_HOST=""; CF_TOKEN=false

parse_cloudflared() {  # parse_cloudflared "ARGS AFTER THE BINARY"
    local -a w
    local i n word sub="" run=false hello=false access="" login=false last=""
    CF_KIND=""; CF_URL=""; CF_NAME=""; CF_CONFIG=""; CF_METRICS=""; CF_HOST=""; CF_TOKEN=false
    set -f
    read -r -a w <<< "$1"
    set +f
    n=${#w[@]}
    i=0
    while [ "$i" -lt "$n" ]; do
        word="${w[$i]}"
        case "$word" in
            --url) CF_URL="${w[$((i + 1))]:-}"; i=$((i + 1)) ;;
            --url=*) CF_URL="${word#--url=}" ;;
            --hello-world) hello=true ;;
            --config) CF_CONFIG="${w[$((i + 1))]:-}"; i=$((i + 1)) ;;
            --config=*) CF_CONFIG="${word#--config=}" ;;
            --metrics) CF_METRICS="${w[$((i + 1))]:-}"; i=$((i + 1)) ;;
            --metrics=*) CF_METRICS="${word#--metrics=}" ;;
            --token|--credentials-contents) CF_TOKEN=true; i=$((i + 1)) ;;
            --token=*|--credentials-contents=*) CF_TOKEN=true ;;
            --hostname|-hostname) CF_HOST="${w[$((i + 1))]:-}"; i=$((i + 1)) ;;
            --hostname=*) CF_HOST="${word#--hostname=}" ;;
            --credentials-file|--origincert|--loglevel|--logfile|--log-directory|--pidfile|--protocol|\
            --edge-ip-version|--region|--grace-period|--retries|--destination|--listener|--name|--label|--tag|\
            --proxy-dns-port|--ha-connections|--edge-bind-address|--service-op-ip|--origin-ca-pool|\
            --autoupdate-freq|--metrics-update-freq|--url-path)
                i=$((i + 1)) ;;
            -*) ;;
            *)
                if [ -z "$sub" ]; then
                    sub="$word"
                    [ "$word" = access ] && access="access"
                elif [ "$sub" = access ] && [ "$access" = access ]; then
                    access="$word"                       # ssh, tcp, rdp, ...
                elif [ "$sub" = tunnel ] && [ "$word" = run ] && ! $run; then
                    run=true
                elif [ "$sub" = tunnel ] && [ "$word" = login ] && ! $run; then
                    login=true
                elif $run; then
                    last="$word"                         # the tunnel's name or ID comes last
                fi ;;
        esac
        i=$((i + 1))
    done
    if [ -n "$access" ]; then
        CF_KIND="access"
    elif $login; then
        CF_KIND="login"
    elif $run || $CF_TOKEN; then
        CF_KIND="persistent"
        CF_NAME="$last"
    elif [ -n "$CF_URL" ] || $hello; then
        case "$CF_URL" in ssh://*) CF_KIND="ssh-tunnel" ;; *) CF_KIND="quick" ;; esac
        [ -n "$CF_URL" ] || CF_URL="(hello world test page)"
    else
        CF_KIND="other"
    fi
}

cloudify_kind() {  # ARGS -> ssh-host | ssh-remote | serve | temp-cli; fails for other programs
    local a=" $1 "
    case "$a" in
        */cloudify\ *|*\ cloudify\ *|*"from cloudify.cli import cli"*) ;;  # .../cloudify, -m cloudify, the remote's restart
        *) return 1 ;;
    esac
    case "$a" in
        *" -ssh host "*|*" --ssh host "*|*" --ssh=host "*|*" ssh host "*) echo ssh-host ;;
        *" -ssh remote "*|*" --ssh remote "*|*" --ssh=remote "*|*" ssh remote "*) echo ssh-remote ;;
        *" serve "*) echo serve ;;
        *" -p "[0-9]*|*" --port "[0-9]*|*" --port="[0-9]*|*" temp create "*) echo temp-cli ;;
        *) return 1 ;;
    esac
}

# Indexes into the process arrays, by kind
QUICK=(); SSHT=(); ACCESS=(); PERSISTENT=(); LOGIN=(); CF_OTHER=(); SSH_HOST=(); SSH_REMOTE=(); SERVE=(); TEMP_CLI=()

classify_processes() {
    QUICK=(); SSHT=(); ACCESS=(); PERSISTENT=(); LOGIN=(); CF_OTHER=(); SSH_HOST=(); SSH_REMOTE=(); SERVE=(); TEMP_CLI=()
    local i n cfargs kind
    n=${#P_PID[@]}
    i=0
    while [ "$i" -lt "$n" ]; do
        if cfargs="$(cloudflared_args "${P_ARGS[$i]}")"; then
            parse_cloudflared "$cfargs"
            case "$CF_KIND" in
                quick) QUICK+=("$i") ;;
                ssh-tunnel) SSHT+=("$i") ;;
                access) ACCESS+=("$i") ;;
                persistent) PERSISTENT+=("$i") ;;
                login) LOGIN+=("$i") ;;
                *) CF_OTHER+=("$i") ;;
            esac
        elif kind="$(cloudify_kind "${P_ARGS[$i]}")"; then
            case "$kind" in
                ssh-host) SSH_HOST+=("$i") ;;
                ssh-remote) SSH_REMOTE+=("$i") ;;
                serve) SERVE+=("$i") ;;
                temp-cli) TEMP_CLI+=("$i") ;;
            esac
        fi
        i=$((i + 1))
    done
}

orphan_ssh_tunnels() {  # number of SSH tunnels that don't belong to a running `cloudify -ssh host`
    local t h n=0 found
    for t in ${SSHT[@]+"${SSHT[@]}"}; do
        found=false
        for h in ${SSH_HOST[@]+"${SSH_HOST[@]}"}; do [ "${P_PID[$h]}" = "${P_PPID[$t]}" ] && found=true; done
        $found || n=$((n + 1))
    done
    printf '%s' "$n"
}

index_of_pid() {  # PID -> index in the process arrays
    local i=0 n=${#P_PID[@]}
    while [ "$i" -lt "$n" ]; do
        [ "${P_PID[$i]}" = "$1" ] && { echo "$i"; return 0; }
        i=$((i + 1))
    done
    return 1
}

children_of() {  # PID -> indexes of its child processes
    local i=0 n=${#P_PID[@]}
    while [ "$i" -lt "$n" ]; do
        [ "${P_PPID[$i]}" = "$1" ] && echo "$i"
        i=$((i + 1))
    done
}

is_mine() { [ "$MY_UID" = 0 ] || [ "${P_UID[$1]}" = "$MY_UID" ]; }

owner_text() {  # index -> "you" or the user name
    if [ "${P_UID[$1]}" = "$MY_UID" ]; then printf 'you'; else printf '%s' "${P_USER[$1]}"; fi
}

started_text() {  # index -> "running for 01:02:03"
    local e="${P_ETIME[$1]}"
    if [ -z "$e" ] || [ "$e" = "-" ]; then printf 'running'; else printf 'running for %s' "$e"; fi
}

# ------------------------------------------------------------------ what a process is connected to

listen_addresses() {  # PID -> its listening TCP addresses (one per line); empty if they can't be seen
    local pid="$1"
    if [ "$OS" = linux ] && have ss; then
        ss -ltnpH 2>/dev/null | awk -v p="pid=$pid," 'index($0, p) { print $4 }'
    elif have lsof; then
        lsof -nP -a -p "$pid" -iTCP -sTCP:LISTEN -Fn 2>/dev/null | sed -n 's/^n//p'
    fi
}

local_port() { printf '%s' "${1##*:}"; }

quick_hostname() {  # INDEX -> the tunnel's trycloudflare.com address, asked from cloudflared itself
    local idx="$1" addr port json
    have curl || return 1
    parse_cloudflared "$(cloudflared_args "${P_ARGS[$idx]}")"
    {
        [ -n "$CF_METRICS" ] && printf '%s\n' "$CF_METRICS"
        listen_addresses "${P_PID[$idx]}"
    } | while read -r addr; do
        port="$(local_port "$addr")"
        case "$port" in ''|*[!0-9]*) continue ;; esac
        json="$(curl -s --max-time 2 "http://127.0.0.1:$port/quicktunnel" 2>/dev/null </dev/null)"
        json="$(printf '%s' "$json" | sed -n 's/.*"hostname"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
        if [ -n "$json" ]; then printf 'https://%s\n' "$json"; break; fi
    done
}

ready_connections() {  # INDEX -> number of Cloudflare edge connections of a persistent tunnel
    local idx="$1" addr port json
    have curl || return 1
    parse_cloudflared "$(cloudflared_args "${P_ARGS[$idx]}")"
    {
        [ -n "$CF_METRICS" ] && printf '%s\n' "$CF_METRICS"
        listen_addresses "${P_PID[$idx]}"
    } | while read -r addr; do
        port="$(local_port "$addr")"
        case "$port" in ''|*[!0-9]*) continue ;; esac
        json="$(curl -s --max-time 2 "http://127.0.0.1:$port/ready" 2>/dev/null </dev/null)"
        json="$(printf '%s' "$json" | sed -n 's/.*"readyConnections"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')"
        if [ -n "$json" ]; then printf '%s\n' "$json"; break; fi
    done
}

# How a persistent process is kept running: "systemd-user UNIT", "systemd UNIT", "launchd LABEL",
# "container ID", "cron" or "manual"
MANAGER=""
detect_manager() {  # INDEX
    local idx="$1" pid cg unit label
    pid="${P_PID[$idx]}"
    MANAGER="manual"
    if [ -r "/proc/$pid/cgroup" ]; then
        cg="$(cat "/proc/$pid/cgroup" 2>/dev/null)"
        case "$cg" in
            *docker*|*containerd*|*kubepods*|*libpod*|*lxc.payload*)
                MANAGER="container $(printf '%s' "$cg" | grep -oE '[0-9a-f]{64}' | head -1 | cut -c1-12)"
                return ;;
        esac
        unit="$(printf '%s\n' "$cg" | sed -n 's#.*/\([^/]*\.service\)$#\1#p' | tail -1)"
        case "$unit" in
            ''|user@*.service) ;;
            cron.service|crond.service|cronie.service) MANAGER="cron" ;;
            *)
                case "$cg" in
                    *user@*) MANAGER="systemd-user $unit" ;;
                    *) MANAGER="systemd $unit" ;;
                esac ;;
        esac
    elif [ "$OS" = macos ] && have launchctl; then
        label="$(launchctl list 2>/dev/null </dev/null | awk -v p="$pid" '$1 == p { print $3; exit }')"
        [ -n "$label" ] && MANAGER="launchd $label"
    fi
}

manager_text() {
    case "$1" in
        systemd-user\ *) printf 'kept running by the systemd user service %s' "${1#systemd-user }" ;;
        systemd\ *) printf 'kept running by the system service %s' "${1#systemd }" ;;
        launchd\ *) printf 'kept running by launchd (%s)' "${1#launchd }" ;;
        container\ *) printf 'inside a container (%s)' "${1#container }" ;;
        cron) printf 'started by cron' ;;
        *) printf 'started by hand' ;;
    esac
}

container_name() {  # short container ID -> name (if docker can be asked)
    have docker || return 1
    docker ps --format '{{.ID}} {{.Names}}' 2>/dev/null </dev/null | awk -v id="$1" 'index($1, id) == 1 { print $2; exit }'
}

# ------------------------------------------------------------------ listing

list_quick() {
    local idx parent pidx url origin
    section "Temporary tunnels (public trycloudflare.com addresses)"
    if [ ${#QUICK[@]} -eq 0 ]; then none; return; fi
    for idx in "${QUICK[@]}"; do
        parse_cloudflared "$(cloudflared_args "${P_ARGS[$idx]}")"
        origin="$CF_URL"
        url="$(quick_hostname "$idx")"
        alert "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): ${url:-a trycloudflare.com address} -> $origin"
        [ -n "$url" ] || detail "public address: not visible to this user"
        parent="${P_PPID[$idx]}"
        if pidx="$(index_of_pid "$parent")"; then
            case " ${TEMP_CLI[*]:-} " in *" $pidx "*) detail "started by: cloudify -p (PID $parent)" ;; esac
            case " ${SERVE[*]:-} " in *" $pidx "*) detail "started by: the Cloudify dashboard (PID $parent)" ;; esac
        fi
        detect_manager "$idx"
        case "$MANAGER" in container*) detail "$(manager_text "$MANAGER")" ;; esac
    done
}

list_ssh() {
    local idx t tidx url kids shells c
    section "SSH sharing (this machine's shell, reachable through Cloudflare)"
    if [ ${#SSH_HOST[@]} -eq 0 ] && [ ${#SSHT[@]} -eq 0 ]; then none; return; fi
    for idx in ${SSH_HOST[@]+"${SSH_HOST[@]}"}; do
        url=""; shells=0
        kids="$(children_of "${P_PID[$idx]}")"
        for c in $kids; do
            case " ${SSHT[*]:-} " in
                *" $c "*) url="$(quick_hostname "$c")" ;;
                *) shells=$((shells + 1)) ;;
            esac
        done
        alert "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): cloudify -ssh host, shell of ${P_USER[$idx]}"
        detail "public address: ${url:-not visible to this user}; programs started by remotes: $shells"
    done
    for t in ${SSHT[@]+"${SSHT[@]}"}; do  # SSH tunnels whose host process is gone or isn't Cloudify's
        tidx=""
        for idx in ${SSH_HOST[@]+"${SSH_HOST[@]}"}; do [ "${P_PID[$idx]}" = "${P_PPID[$t]}" ] && tidx="$idx"; done
        [ -n "$tidx" ] && continue
        parse_cloudflared "$(cloudflared_args "${P_ARGS[$t]}")"
        url="$(quick_hostname "$t")"
        alert "PID ${P_PID[$t]} ($(owner_text "$t"), $(started_text "$t")): SSH tunnel${url:+ $url} -> $CF_URL"
    done
}

list_outgoing() {
    local idx
    section "Outgoing connections (this machine logged in to another one)"
    if [ ${#SSH_REMOTE[@]} -eq 0 ] && [ ${#ACCESS[@]} -eq 0 ]; then none; return; fi
    for idx in ${SSH_REMOTE[@]+"${SSH_REMOTE[@]}"}; do
        item "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): cloudify -ssh remote"
    done
    for idx in ${ACCESS[@]+"${ACCESS[@]}"}; do
        parse_cloudflared "$(cloudflared_args "${P_ARGS[$idx]}")"
        item "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): cloudflared access ${CF_HOST:+to $CF_HOST}"
    done
}

list_persistent() {
    local idx conns name
    section "Persistent tunnels (named tunnels with your own hostnames)"
    if [ ${#PERSISTENT[@]} -eq 0 ]; then none; return; fi
    for idx in "${PERSISTENT[@]}"; do
        parse_cloudflared "$(cloudflared_args "${P_ARGS[$idx]}")"
        name="${CF_NAME:-}"
        if [ -z "$name" ]; then
            if $CF_TOKEN; then name="(started with a token)"; else name="(named in its config file)"; fi
        fi
        conns="$(ready_connections "$idx")"
        item "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): tunnel $name${conns:+, $conns Cloudflare connections}"
        [ -n "$CF_CONFIG" ] && detail "config: $CF_CONFIG"
        detect_manager "$idx"
        detail "$(manager_text "$MANAGER")"
    done
}

list_dashboard() {
    local idx addrs a open where
    section "Cloudify dashboard"
    if [ ${#SERVE[@]} -eq 0 ] && [ ${#LOGIN[@]} -eq 0 ]; then none; return; fi
    for idx in ${SERVE[@]+"${SERVE[@]}"}; do
        addrs="$(listen_addresses "${P_PID[$idx]}" | sort -u | tr '\n' ' ')"
        open=false
        for a in $addrs; do
            case "$a" in 127.*|\[::1\]:*|::1:*|localhost:*) ;; *) open=true ;; esac
        done
        if $open; then
            alert "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): listening on ${addrs% } - reachable from the network"
            case " ${P_ARGS[$idx]} " in
                *" --ssl-certfile"*) ;;
                *) detail "plain HTTP: passwords and logins cross the network unencrypted" ;;
            esac
            detail "to keep it on this machine only, restart it with --secure (Docker: CLOUDIFY_SECURE=1)"
        else
            if [ -n "$addrs" ]; then where="listening on ${addrs% }"; else where="listening address not visible to this user"; fi
            item "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): $where"
        fi
        detect_manager "$idx"
        detail "$(manager_text "$MANAGER")"
    done
    for idx in ${LOGIN[@]+"${LOGIN[@]}"}; do
        item "PID ${P_PID[$idx]} ($(owner_text "$idx")): a Cloudflare login is waiting in the browser"
    done
}

list_other() {
    local idx
    [ ${#CF_OTHER[@]} -eq 0 ] && return
    section "Other cloudflared processes"
    for idx in "${CF_OTHER[@]}"; do
        item "PID ${P_PID[$idx]} ($(owner_text "$idx"), $(started_text "$idx")): cloudflared $(cloudflared_args "${P_ARGS[$idx]}" | awk '{print $1}')"
    done
}

# Auto-start entries: what starts tunnels or the dashboard at boot or login
AUTOSTART_FILES=()
CRON_LINES=""
SYSTEM_SERVICE=""

find_autostart() {
    local f
    AUTOSTART_FILES=()
    for f in "$HOME"/.config/systemd/user/cloudflared-tunnel-*.service "$HOME"/.config/systemd/user/cloudify-dashboard.service \
             "$HOME"/Library/LaunchAgents/com.user.cloudflare.tunnel.*.plist "$HOME"/Library/LaunchAgents/com.cloudify.dashboard.plist \
             "$HOME"/Library/LaunchAgents/com.cloudflare.cloudflared.plist; do
        [ -e "$f" ] && AUTOSTART_FILES+=("$f")
    done
    CRON_LINES=""
    if have crontab; then
        CRON_LINES="$(crontab -l 2>/dev/null </dev/null | grep -E 'start-tunnel-|cloudflared' | grep -v '^[[:space:]]*#')"
    fi
    SYSTEM_SERVICE=""
    for f in /etc/systemd/system/cloudflared.service /etc/systemd/system/cloudflared-update.timer \
             /Library/LaunchDaemons/com.cloudflare.cloudflared.plist; do
        [ -e "$f" ] && SYSTEM_SERVICE="$SYSTEM_SERVICE $f"
    done
    SYSTEM_SERVICE="${SYSTEM_SERVICE# }"
}

list_autostart() {
    local f line
    section "Starts automatically (at boot or login)"
    find_autostart
    if [ ${#AUTOSTART_FILES[@]} -eq 0 ] && [ -z "$CRON_LINES" ] && [ -z "$SYSTEM_SERVICE" ]; then none; return; fi
    for f in ${AUTOSTART_FILES[@]+"${AUTOSTART_FILES[@]}"}; do item "$(pretty "$f")"; done
    if [ -n "$CRON_LINES" ]; then
        while IFS= read -r line; do item "crontab: $line"; done <<< "$CRON_LINES"
    fi
    for f in $SYSTEM_SERVICE; do item "$f (system-wide cloudflared service)"; done
}

# Installed programs and data
CLOUDIFY_BIN=""
CLOUDFLARED_BINS=()

find_installed() {
    local p
    CLOUDIFY_BIN="$(command -v cloudify 2>/dev/null)"
    [ -z "$CLOUDIFY_BIN" ] && [ -e "$HOME/.local/bin/cloudify" ] && CLOUDIFY_BIN="$HOME/.local/bin/cloudify"
    CLOUDFLARED_BINS=()
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        case " ${CLOUDFLARED_BINS[*]:-} " in *" $p "*) continue ;; esac
        CLOUDFLARED_BINS+=("$p")
    done < <({ type -ap cloudflared 2>/dev/null
               for p in /usr/local/bin/cloudflared "$HOME/.local/bin/cloudflared" /opt/homebrew/bin/cloudflared /usr/bin/cloudflared; do
                   [ -x "$p" ] && printf '%s\n' "$p"
               done; })
}

list_installed() {
    local p v creds
    section "Installed"
    find_installed
    if [ -n "$CLOUDIFY_BIN" ]; then item "cloudify: $CLOUDIFY_BIN"; else item "cloudify: not installed"; fi
    if [ ${#CLOUDFLARED_BINS[@]} -eq 0 ]; then
        item "cloudflared: not installed"
    else
        for p in "${CLOUDFLARED_BINS[@]}"; do
            v="$("$p" --version 2>/dev/null </dev/null | sed -n 's/.*version \([^ ]*\).*/\1/p' | head -1)"
            item "cloudflared ${v:-?}: $p"
        done
    fi
    if [ -d "$HOME/.cloudflared" ]; then
        creds="$(find "$HOME/.cloudflared" -maxdepth 1 -name '*-*-*-*-*.json' 2>/dev/null | wc -l | tr -d ' ')"
        item "~/.cloudflared: $( [ -e "$HOME/.cloudflared/cert.pem" ] && printf 'Cloudflare certificate (cert.pem), ')$creds tunnel credential file(s)$( [ -d "$HOME/.cloudflared/cloudify" ] && printf ', Cloudify settings and accounts')"
    fi
    if have docker; then
        docker ps -a --format '{{.Names}} ({{.Image}}, {{.Status}})' 2>/dev/null </dev/null | grep -iE 'cloudify|cloudflared' | while IFS= read -r p; do
            item "Docker container: $p"
        done
    fi
}

# ------------------------------------------------------------------ stopping

STOPPED=0
NOT_MINE=0

stop_pid() {  # PID LABEL: TERM, wait, then KILL
    local pid="$1" label="$2" n=0
    alive "$pid" || return 0
    if ! kill -TERM "$pid" 2>/dev/null; then
        fail "could not stop $label (PID $pid)"
        return 1
    fi
    while alive "$pid" && [ "$n" -lt 40 ]; do sleep 0.2; n=$((n + 1)); done
    if alive "$pid"; then
        kill -KILL "$pid" 2>/dev/null
        sleep 0.3
    fi
    if alive "$pid"; then fail "could not stop $label (PID $pid)"; return 1; fi
    ok "stopped $label (PID $pid)"
    STOPPED=$((STOPPED + 1))
    return 0
}

stop_index() {  # INDEX LABEL: stop a process, through its service manager if it has one
    local idx="$1" label="$2" pid unit
    pid="${P_PID[$idx]}"
    alive "$pid" || return 0
    if ! is_mine "$idx"; then
        alert "$label (PID $pid) belongs to ${P_USER[$idx]}: run this script with sudo to stop it"
        NOT_MINE=$((NOT_MINE + 1))
        return 1
    fi
    detect_manager "$idx"
    case "$MANAGER" in
        systemd-user\ *)
            unit="${MANAGER#systemd-user }"
            if [ "${P_UID[$idx]}" = "$MY_UID" ] && systemctl --user stop "$unit" </dev/null 2>/dev/null; then
                sleep 0.5
                if ! alive "$pid"; then ok "stopped $label (systemd user service $unit)"; STOPPED=$((STOPPED + 1)); return 0; fi
            fi ;;
        systemd\ *)
            unit="${MANAGER#systemd }"
            if [ "$MY_UID" = 0 ] && systemctl stop "$unit" </dev/null 2>/dev/null; then
                sleep 0.5
                if ! alive "$pid"; then ok "stopped $label (system service $unit)"; STOPPED=$((STOPPED + 1)); return 0; fi
            elif [ "$MY_UID" != 0 ]; then
                alert "$label (PID $pid) is the system service $unit: stop it with  sudo systemctl stop $unit"
                return 1
            fi ;;
        launchd\ *)
            unit="${MANAGER#launchd }"
            if launchctl bootout "gui/$MY_UID/$unit" </dev/null 2>/dev/null || launchctl remove "$unit" </dev/null 2>/dev/null; then
                sleep 0.5
                if ! alive "$pid"; then ok "stopped $label (launchd $unit)"; STOPPED=$((STOPPED + 1)); return 0; fi
            fi ;;
        container\ *)
            unit="${MANAGER#container }"
            alert "$label (PID $pid) runs inside the container $(container_name "$unit" || printf '%s' "$unit"): stop it with  docker stop <container>"
            return 1 ;;
    esac
    stop_pid "$pid" "$label"
}

stop_temporary() {
    local idx kid kids
    section "Stopping temporary tunnels and SSH sessions"
    # Cloudify's own processes first: they stop their tunnels and disconnect their remotes cleanly
    for idx in ${TEMP_CLI[@]+"${TEMP_CLI[@]}"}; do stop_index "$idx" "cloudify -p"; done
    for idx in ${SSH_HOST[@]+"${SSH_HOST[@]}"}; do
        kids="$(children_of "${P_PID[$idx]}")"
        stop_index "$idx" "SSH sharing (cloudify -ssh host)"
        for kid in $kids; do alive "${P_PID[$kid]}" && stop_index "$kid" "process left by SSH sharing"; done
    done
    for idx in ${SSH_REMOTE[@]+"${SSH_REMOTE[@]}"}; do stop_index "$idx" "outgoing SSH session (cloudify -ssh remote)"; done
    for idx in ${QUICK[@]+"${QUICK[@]}"}; do stop_index "$idx" "temporary tunnel"; done
    for idx in ${SSHT[@]+"${SSHT[@]}"}; do stop_index "$idx" "SSH tunnel"; done
    for idx in ${ACCESS[@]+"${ACCESS[@]}"}; do stop_index "$idx" "cloudflared access connection"; done
    [ "$STOPPED" -gt 0 ] || [ "$NOT_MINE" -gt 0 ] || [ "$FAILED" -gt 0 ] || none
}

stop_persistent() {
    local idx name before="$STOPPED"
    section "Stopping persistent tunnels and the dashboard"
    for idx in ${PERSISTENT[@]+"${PERSISTENT[@]}"}; do
        parse_cloudflared "$(cloudflared_args "${P_ARGS[$idx]}")"
        name="${CF_NAME:-}"
        stop_index "$idx" "persistent tunnel${name:+ $name}"
    done
    for idx in ${SERVE[@]+"${SERVE[@]}"}; do stop_index "$idx" "Cloudify dashboard"; done
    for idx in ${LOGIN[@]+"${LOGIN[@]}"}; do stop_index "$idx" "Cloudflare login"; done
    [ "$STOPPED" -gt "$before" ] || [ "$NOT_MINE" -gt 0 ] || [ "$FAILED" -gt 0 ] || none
    if [ ${#PERSISTENT[@]} -gt 0 ] && ! $REMOVE; then
        detail "Auto-start entries were kept, so these tunnels start again at the next boot (--remove deletes them)."
    fi
}

# ------------------------------------------------------------------ removing

SUDO=""

setup_sudo() {
    if [ "$MY_UID" = 0 ]; then SUDO=""
    elif have sudo; then
        if [ -r /dev/tty ]; then SUDO="sudo"; else SUDO="sudo -n"; fi
    else SUDO="false"
    fi
}

as_root() {  # run a command as root (sudo asks for the password if needed)
    if [ -z "$SUDO" ]; then "$@"; else $SUDO "$@"; fi
}

remove_autostart() {
    local f unit label line kept
    section "Removing auto-start entries"
    find_autostart
    for f in ${AUTOSTART_FILES[@]+"${AUTOSTART_FILES[@]}"}; do
        case "$f" in
            *.service)
                unit="$(name_of "$f")"
                systemctl --user disable --now "$unit" </dev/null >/dev/null 2>&1
                rm -f "$f" && ok "removed $(pretty "$f")" ;;
            *.plist)
                label="$(name_of "$f")"; label="${label%.plist}"
                launchctl bootout "gui/$MY_UID/$label" </dev/null >/dev/null 2>&1 || launchctl unload -w "$f" </dev/null >/dev/null 2>&1
                rm -f "$f" && ok "removed $(pretty "$f")" ;;
        esac
    done
    if have systemctl && [ "$OS" = linux ]; then systemctl --user daemon-reload </dev/null >/dev/null 2>&1; fi
    if [ -n "$CRON_LINES" ]; then
        kept="$(crontab -l 2>/dev/null </dev/null | grep -vE 'start-tunnel-|cloudflared')"
        if [ -n "$kept" ]; then printf '%s\n' "$kept" | crontab -; else crontab -r </dev/null 2>/dev/null || printf '' | crontab -; fi
        while IFS= read -r line; do ok "removed from crontab: $line"; done <<< "$CRON_LINES"
    fi
    for f in "$HOME"/.local/bin/start-tunnel-*.sh; do
        [ -e "$f" ] && rm -f "$f" && ok "removed $(pretty "$f")"
    done
    if [ -n "$SYSTEM_SERVICE" ]; then
        local cf
        cf="$(command -v cloudflared 2>/dev/null)"
        if [ -n "$cf" ] && as_root "$cf" service uninstall </dev/null >/dev/null 2>&1; then
            ok "removed the system-wide cloudflared service"
        elif [ "$OS" = linux ] && as_root systemctl disable --now cloudflared.service cloudflared-update.timer </dev/null >/dev/null 2>&1; then
            as_root rm -f /etc/systemd/system/cloudflared.service /etc/systemd/system/cloudflared-update.service \
                /etc/systemd/system/cloudflared-update.timer
            as_root systemctl daemon-reload </dev/null >/dev/null 2>&1
            ok "removed the system-wide cloudflared service"
        else
            fail "could not remove the system-wide cloudflared service ($SYSTEM_SERVICE): run  sudo cloudflared service uninstall"
        fi
    fi
}

remove_user_site_install() {  # DIST_INFO: what `pip uninstall` would do for a 1.x copy installed with pip --user
    local py
    py="$(command -v python3 2>/dev/null)" || return 1
    "$py" -I - "$1" <<'PY'
import csv, os, shutil, sys
dist = sys.argv[1]
site = os.path.realpath(os.path.dirname(dist))
inside = lambda p: p == site or p.startswith(site + os.sep)
try:
    with open(os.path.join(dist, "RECORD"), newline="") as f:
        rows = [r[0] for r in csv.reader(f) if r]
except OSError:
    rows = []
tops = set()
for rel in rows:
    path = os.path.normpath(os.path.join(site, rel))
    parent = os.path.realpath(os.path.dirname(path))
    if not inside(parent):
        continue
    target = os.path.join(parent, os.path.basename(path))
    if os.path.islink(target) or os.path.isfile(target):
        os.unlink(target)
    top = rel.split("/")[0]
    if top and top != "..":
        tops.add(top)
frontend = os.path.join(site, "cli", "frontend")
if "cli" in tops and os.path.isdir(frontend) and not os.path.islink(frontend):
    for name in ("node_modules", ".next"):
        if os.path.isdir(os.path.join(frontend, name)):
            shutil.rmtree(os.path.join(frontend, name), ignore_errors=True)
for top in sorted(tops):
    root = os.path.join(site, top)
    if os.path.islink(root) or not os.path.isdir(root):
        continue
    left = [os.path.join(d, n) for d, _, files in os.walk(root) for n in files if not n.endswith(".pyc")]
    if not left:
        shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(dist, ignore_errors=True)
PY
}

remove_cloudify() {
    local d venv found=false
    section "Removing Cloudify"
    for d in "$HOME/.local/share/pipx/venvs/cloudify-cli" "$HOME/.local/pipx/venvs/cloudify-cli" \
             "$HOME/Library/Application Support/pipx/venvs/cloudify-cli" "${PIPX_HOME:-/nonexistent}/venvs/cloudify-cli"; do
        [ -d "$d" ] || continue
        found=true
        if have pipx && pipx uninstall cloudify-cli </dev/null >/dev/null 2>&1; then ok "removed Cloudify (pipx)"
        else rm -rf "$d" && ok "removed $(pretty "$d")"
        fi
        break
    done
    venv="${XDG_DATA_HOME:-$HOME/.local/share}/cloudify"
    if [ -d "$venv/venv" ]; then
        rm -rf "$venv/venv" && ok "removed $(pretty "$venv")/venv"; found=true
        rmdir "$venv" 2>/dev/null
    fi
    for d in "$HOME"/.local/lib/python3*/site-packages/cloudify_cli-*.dist-info \
             "$HOME"/Library/Python/3*/lib/python/site-packages/cloudify_cli-*.dist-info; do
        [ -d "$d" ] || continue
        found=true
        if remove_user_site_install "$d"; then ok "removed the 1.x copy in $(dirname "$(pretty "$d")")"
        else fail "could not remove the 1.x copy in $(dirname "$d") (python3 is needed)"
        fi
    done
    d="$HOME/.local/bin/cloudify"
    if [ -L "$d" ] && [ ! -e "$d" ]; then rm -f "$d"   # link into an environment that is gone now
    elif [ -f "$d" ] && grep -qE 'cloudify\.cli|cli\.cli' "$d" 2>/dev/null; then rm -f "$d"
    fi
    hash -r 2>/dev/null
    d="$(command -v cloudify 2>/dev/null)"
    if [ -n "$d" ]; then
        alert "another cloudify command is still on the PATH: $d (installed some other way; remove it yourself if it is this Cloudify)"
    elif ! $found; then
        none
    fi
}

remove_cloudflared() {
    local p owner
    section "Removing cloudflared"
    find_installed
    if [ ${#CLOUDFLARED_BINS[@]} -eq 0 ]; then none; return; fi
    if have brew && brew list cloudflared </dev/null >/dev/null 2>&1; then
        if brew uninstall cloudflared </dev/null >/dev/null 2>&1; then ok "removed cloudflared (Homebrew)"
        else fail "brew uninstall cloudflared failed"
        fi
    fi
    for p in "${CLOUDFLARED_BINS[@]}"; do
        [ -e "$p" ] || continue
        owner=""
        if have dpkg && dpkg -S "$p" >/dev/null 2>&1; then owner="apt-get remove -y cloudflared"
        elif have rpm && rpm -qf "$p" >/dev/null 2>&1; then
            if have dnf; then owner="dnf remove -y cloudflared"
            elif have zypper; then owner="zypper --non-interactive remove cloudflared"
            else owner="yum remove -y cloudflared"; fi
        elif have pacman && pacman -Qo "$p" >/dev/null 2>&1; then owner="pacman -R --noconfirm cloudflared"
        elif have apk && apk info --who-owns "$p" 2>/dev/null | grep -q 'owned by'; then owner="apk del cloudflared"
        fi
        if [ -n "$owner" ]; then
            # shellcheck disable=SC2086  # the package manager command is split on purpose
            if as_root $owner </dev/null >/dev/null 2>&1; then ok "removed cloudflared (package: ${owner%% *})"
            else fail "could not remove the cloudflared package: run  sudo $owner"
            fi
            continue
        fi
        if ! "$p" --version 2>/dev/null </dev/null | grep -q "cloudflared version"; then
            alert "$p doesn't look like cloudflared; left in place"
            continue
        fi
        if [ -w "$(dirname "$p")" ]; then rm -f "$p"; else as_root rm -f "$p"; fi
        if [ -e "$p" ]; then fail "could not remove $p: run  sudo rm $p"; else ok "removed $p"; fi
    done
}

purge_data() {
    local d
    section "Deleting Cloudflare and Cloudify data"
    for d in "$HOME/.cloudflared" "${XDG_DATA_HOME:-$HOME/.local/share}/cloudify"; do
        [ -e "$d" ] || continue
        rm -rf "$d" && ok "deleted $(pretty "$d")"
    done
    for d in "$HOME"/.cache/cloudify-install.*; do [ -e "$d" ] && rm -rf "$d"; done
    if [ -d /etc/cloudflared ]; then
        if as_root rm -rf /etc/cloudflared </dev/null; then ok "deleted /etc/cloudflared"
        else fail "could not delete /etc/cloudflared: run  sudo rm -rf /etc/cloudflared"
        fi
    fi
}

# ------------------------------------------------------------------ confirmation

confirm() {  # confirm QUESTION EXPECTED-ANSWER
    local answer
    $YES && return 0
    if ! { true < /dev/tty; } 2>/dev/null; then
        printf '\n%s\nNo terminal to ask in: run again with --yes to confirm.\n' "$1" >&2
        return 1
    fi
    printf '\n%s ' "$1" > /dev/tty
    IFS= read -r answer < /dev/tty || return 1
    case "$answer" in
        "$2"|"$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')") return 0 ;;
    esac
    [ "$2" = y ] && case "$answer" in y|Y|yes|YES|Yes) return 0 ;; esac
    return 1
}

# ------------------------------------------------------------------ main

main() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --kill) KILL=true ;;
            --kill-all) KILL=true; KILL_ALL=true ;;
            --remove|--uninstall) REMOVE=true; KILL=true; KILL_ALL=true ;;
            --purge) PURGE=true ;;
            -y|--yes) YES=true ;;
            -h|--help) usage; return 0 ;;
            *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; return 2 ;;
        esac
        shift
    done
    if $PURGE && ! $REMOVE; then printf -- '--purge only works together with --remove\n' >&2; return 2; fi

    case "$(uname -s)" in Darwin) OS=macos ;; Linux) OS=linux ;; *) OS=other ;; esac
    MY_UID="$(id -u)"
    setup_output
    printf '%sCloudify safety check%s  %s(%s, user %s%s)%s\n' "$BOLD" "$RESET" "$DIM" "$OS" "$(id -un)" \
        "$([ "$MY_UID" = 0 ] && printf ', all users')" "$RESET"
    if [ "$MY_UID" = 0 ] && [ -n "${SUDO_USER:-}" ] && $REMOVE; then
        printf '\nRunning with sudo: --remove would remove the installation of root, not of %s.\n' "$SUDO_USER" >&2
        printf 'Run it without sudo (it asks for your password only for system-wide parts).\n' >&2
        return 2
    fi

    load_processes
    classify_processes
    list_quick
    list_ssh
    list_outgoing
    list_persistent
    list_dashboard
    list_other
    list_autostart
    list_installed

    local temporary=$(( ${#QUICK[@]} + ${#SSHT[@]} + ${#SSH_HOST[@]} + ${#SSH_REMOTE[@]} + ${#ACCESS[@]} + ${#TEMP_CLI[@]} ))
    local persistent=$(( ${#PERSISTENT[@]} + ${#SERVE[@]} + ${#LOGIN[@]} ))

    if ! $KILL; then
        section "Summary"
        item "$(( ${#QUICK[@]} )) temporary tunnel(s), $(( ${#SSH_HOST[@]} + $(orphan_ssh_tunnels) )) SSH sharing session(s), $(( ${#SSH_REMOTE[@]} + ${#ACCESS[@]} )) outgoing connection(s)"
        item "$(( ${#PERSISTENT[@]} )) persistent tunnel(s), $(( ${#SERVE[@]} )) dashboard(s)"
        printf '\n'
        [ "$temporary" -gt 0 ] && item "Stop temporary tunnels and SSH sharing:        safety-check.sh --kill"
        [ "$persistent" -gt 0 ] && item "Also stop persistent tunnels and dashboard:    safety-check.sh --kill-all"
        item "Remove Cloudify and cloudflared completely:   safety-check.sh --remove  (add --purge to delete ~/.cloudflared)"
        return 0
    fi

    if $REMOVE; then
        local plan="This stops everything listed above, removes the auto-start entries, and uninstalls Cloudify and cloudflared."
        $PURGE && plan="$plan It also DELETES ~/.cloudflared (Cloudflare certificates, tunnel credentials, Cloudify accounts) and /etc/cloudflared if it exists. Tunnels and DNS records in your Cloudflare account are not deleted."
        printf '\n%s\n' "$plan"
        if $PURGE; then
            confirm "Type 'delete' to continue:" delete || { printf 'Cancelled.\n'; return 1; }
        else
            confirm "Continue? [y/N]" y || { printf 'Cancelled.\n'; return 1; }
        fi
        setup_sudo
    elif $KILL_ALL && [ "$persistent" -gt 0 ]; then
        confirm "Stop the persistent tunnels and the dashboard too? Websites served by them go offline. [y/N]" y \
            || { printf 'Cancelled.\n'; return 1; }
    fi

    stop_temporary
    if $KILL_ALL; then stop_persistent; fi
    if $REMOVE; then
        remove_autostart
        remove_cloudify
        remove_cloudflared
        if $PURGE; then purge_data; fi
    fi

    # What is left?
    load_processes
    classify_processes
    section "Result"
    temporary=$(( ${#QUICK[@]} + ${#SSHT[@]} + ${#SSH_HOST[@]} + ${#SSH_REMOTE[@]} + ${#ACCESS[@]} ))
    persistent=$(( ${#PERSISTENT[@]} + ${#SERVE[@]} ))
    if [ "$temporary" -eq 0 ]; then ok "no temporary tunnels or SSH sessions are running"
    else alert "$temporary temporary tunnel(s) or SSH session(s) still running (see above)"; FAILED=1
    fi
    if $KILL_ALL; then
        if [ "$persistent" -eq 0 ]; then ok "no persistent tunnels or dashboards are running"
        else alert "$persistent persistent tunnel(s) or dashboard(s) still running (see above)"; FAILED=1
        fi
    elif [ "$persistent" -gt 0 ]; then
        item "$persistent persistent tunnel(s) or dashboard(s) left running (stop them with --kill-all)"
    fi
    if $REMOVE; then
        hash -r 2>/dev/null
        if have cloudify; then alert "cloudify is still installed: $(command -v cloudify)"; else ok "Cloudify is not installed"; fi
        if have cloudflared; then alert "cloudflared is still installed: $(command -v cloudflared)"; else ok "cloudflared is not installed"; fi
        if ! $PURGE && [ -d "$HOME/.cloudflared" ]; then
            item "~/.cloudflared was kept (certificates and tunnel credentials): --remove --purge deletes it"
        fi
        item "Lines the installer added to ~/.profile, ~/.bashrc or ~/.zshrc (PATH) were left; they are harmless."
    fi
    return "$FAILED"
}

main "$@"
