#!/bin/sh
# Tests or builds a package of this repo inside a throwaway Checkmk Raw container, because the plugin code imports
# modules that only exist in a Checkmk site (cmk.agent_based, cmk.utils.password_store, the mkp tool). The container uses
# the same edition (Raw) and Python as the production site. The image is large (about 3 GB) and is pulled on first use.
#
# Usage: scripts/mkp.sh test  <package>   runs the package's unit tests with the site's Python
#        scripts/mkp.sh build <package>   builds dist/<package>-<version>.mkp from packages/<package>/<package>.manifest
#
# Needs only docker. CMK_IMAGE overrides the image (default: checkmk/check-mk-raw:2.4.0-latest).
set -eu

IMAGE="${CMK_IMAGE:-checkmk/check-mk-raw:2.4.0-latest}"
SITE=cmk
SITE_HOME="/omd/sites/${SITE}"
NAME="mkp-$$"
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

usage() {
    echo "Usage: $(basename "$0") {test|build} <package>" >&2
    exit 1
}

# Removes the throwaway container, also when the script fails half-way.
cleanup() {
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
}

# Starts the container and waits until the site's mkp tool answers (the site is created on first start, which takes a
# little while).
start_site() {
    docker run -d --name "${NAME}" -e CMK_PASSWORD=throwaway -e CMK_SITE_ID="${SITE}" \
        --tmpfs "/opt/omd/sites/${SITE}/tmp:uid=1000,gid=1000" "${IMAGE}" >/dev/null
    trap cleanup EXIT
    i=0
    until docker exec "${NAME}" su - "${SITE}" -c 'mkp list' >/dev/null 2>&1; do
        i=$((i + 1))
        if [ "${i}" -gt 60 ]; then
            echo "the Checkmk site did not come up in time" >&2
            exit 1
        fi
        sleep 3
    done
}

# Copies the package's plugin tree into the site, where a Checkmk plugin has to live to be importable and packageable.
install_plugin() {
    dest="${SITE_HOME}/local/lib/python3/cmk_addons/plugins"
    docker exec -u root "${NAME}" mkdir -p "${dest}"
    docker cp "${REPO_ROOT}/packages/${PACKAGE}/src/cmk_addons/plugins/${PACKAGE}" "${NAME}:${dest}/${PACKAGE}"
    docker exec -u root "${NAME}" chown -R "${SITE}:${SITE}" "${SITE_HOME}/local"
}

# Runs the package's unit tests with the site's Python.
run_tests() {
    docker exec -u root "${NAME}" mkdir -p /tmp/pkg
    docker cp "${REPO_ROOT}/packages/${PACKAGE}/tests" "${NAME}:/tmp/pkg/tests"
    docker exec -u root "${NAME}" chown -R "${SITE}:${SITE}" /tmp/pkg
    docker exec "${NAME}" su - "${SITE}" -c 'cd /tmp/pkg && python3 -m unittest discover -s tests -t . -v'
}

# Builds the .mkp with the site's mkp tool and copies it to dist/.
build_mkp() {
    manifest="${REPO_ROOT}/packages/${PACKAGE}/${PACKAGE}.manifest"
    [ -f "${manifest}" ] || { echo "no manifest at ${manifest}" >&2; exit 1; }
    docker cp "${manifest}" "${NAME}:/tmp/${PACKAGE}.manifest"
    docker exec -u root "${NAME}" chown "${SITE}:${SITE}" "/tmp/${PACKAGE}.manifest"
    docker exec "${NAME}" su - "${SITE}" -c "mkp package /tmp/${PACKAGE}.manifest"
    mkdir -p "${REPO_ROOT}/dist"
    file=$(docker exec "${NAME}" sh -c "ls ${SITE_HOME}/var/check_mk/packages_local/${PACKAGE}-*.mkp")
    docker cp "${NAME}:${file}" "${REPO_ROOT}/dist/"
    echo "built dist/$(basename "${file}")"
}

[ $# -eq 2 ] || usage
ACTION="$1"
PACKAGE="$2"
[ -d "${REPO_ROOT}/packages/${PACKAGE}" ] || { echo "unknown package: ${PACKAGE}" >&2; exit 1; }

start_site
install_plugin
case "${ACTION}" in
    test) run_tests ;;
    build) build_mkp ;;
    *) usage ;;
esac
