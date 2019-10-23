#@IgnoreInspection BashAddShebang
[[ $_ != $0 ]] || { echo "functions.sh is meant to be sourced, not executed"; exit 1; }

function setup_cmd_log {
    CMD_OUTPUT_FILENAME="$(mktemp)"
    exec 3>&1 2> ${CMD_OUTPUT_FILENAME} 1> ${CMD_OUTPUT_FILENAME}
}

function log {
    msg="[$(date '+%Y-%m-%dT%H:%M:%S%z')] INFO ["${CURRENT_SCRIPT}"] $*"
    echo -e "$msg" >&3
    echo -e "$msg"
}

function verify_environment_prereq {
    [ -z "${CALLING_UID}" ] && { log "Missing CALLING_UID"; /bin/false; }
    [ -z "${CALLING_GID}" ] && { log "Missing CALLING_GID"; /bin/false; }
    [ -z "${BASH_ON_FAIL}" ] || { log "BASH_ON_FAIL is set; will enter interactive shell if errors occur."; }
    [ -n "${ENABLE_SOURCE_OVERLAY}" ] && { log "Source overlay is unsupported"; /bin/false; }
    return 0
}

function set_variables_from_environment {
    RPMS_DIR="$(rpm --eval %\{_rpmdir\})"
    SRPMS_DIR="$(rpm --eval %\{_srcrpmdir\})"
    SOURCE_DIR="$(rpm --eval %\{_sourcedir\})"
    SPECS_DIR="$(rpm --eval %\{_specdir\})"
    ARCH="$(rpm --eval %\{_arch\})"
}

function setup_rpm_builddeps {
    log "Now downloading build dependencies, could take a while..."
    SPECS_DIR="$(rpm --eval %\{_specdir\})"
    SPEC="$(ls "${SPECS_DIR}"/*.spec | head -n 1)"
    yum makecache fast || dnf makecache
    yum-builddep -y --nogpgcheck "${SPEC}" || (dnf install -y dnf-plugins-core; dnf builddep -y --nogpgcheck "${SPEC}")
    log "Download of build dependencies succeeded"
}



function map_uid_gid_to_existing_users {
    #rpmbuild complains if it can't find a proper user for uid/gid of the source files;
    #we should add all uid/gids for source files.
    for gid in $(find ${TOMAP_DIR} -print0 | xargs -0 stat -c '%g' | sort | uniq); do
        groupadd -g "$gid" "group$gid" || /bin/true
    done

    for uid in $(find ${TOMAP_DIR} -print0 | xargs -0 stat -c '%u' | sort | uniq); do
        useradd -u "$uid" "user$uid"|| /bin/true
    done

}

function setup_user_macros {
    if [ -r "/rpmmacros" ]
    then
        log "Adding user macros to current build"
        cp /rpmmacros "${HOME}/.rpmmacros"
        echo -e "\n" >> "${HOME}/.rpmmacros"
    fi
}

function setup_rpm_signing_system {
    GPGBIN="$(command -v gpg || command -v gpg2)"
    ${GPGBIN} --import /private.key
    KEYNAME=$(${GPGBIN} --list-secret-keys --with-colons  | grep uid | cut -d ":" -f 10)
    [ -n "${KEYNAME}" ] || { log "could not find key for signing purpose"; exit 1; }
    echo -e "%_gpg_name ${KEYNAME}\n%_signature gpg" >> "${HOME}/.rpmmacros"
    ${GPGBIN} --armor --export "${KEYNAME}" > /tmp/public.gpg
    rpm --import /tmp/public.gpg
}

function sign_rpmbuild_srcrpm_files {
	files="$(grep 'Wrote:' <<< "${rpmbuild_out}" | cut -d ':' -f 2)"

	exitcode=0
    echo -e "\n" | setsid rpmsign --addsign ${files} || /bin/true
    rpm -K ${files} | grep -e "pgp" -e "OK" || { log "Signing failed." ; exitcode=1 ; }
    if [ "${exitcode}" -ne 0 ]; then
			if [ "bashonfail" == "${BASH_ON_FAIL}" ]; then
				# if the build is interactive, we can see what's printed in the current log, no need to reprint.
				log "Signing failed, spawning a shell. The build will terminate after such shell is closed."
				/bin/bash
			else
				log "rpmsign command failed:output is: -->\n${rpmbuild_out}\nrpmsign command output end\n\n."
			fi
		# don't accidentally retain unsigned files
		rm -f "${files}"
		exit "${exitcode}"
	fi
}

# requires rpmbuild_out input variable
function sign_rpmbuild_output_files {
	files="$(sed -n -e '/Checking for unpackaged file/,$p' <<< "${rpmbuild_out}" | grep 'Wrote:' | cut -d ':' -f 2)"

	exitcode=0
    echo -e "\n" | setsid rpmsign --addsign ${files} || /bin/true
    rpm -K ${files} | grep -e "pgp" -e "OK" || { log "Signing failed." ; exitcode=1 ; }
    if [ "${exitcode}" -ne 0 ]; then
			if [ "bashonfail" == "${BASH_ON_FAIL}" ]; then
				# if the build is interactive, we can see what's printed in the current log, no need to reprint.
				log "Signing failed, spawning a shell. The build will terminate after such shell is closed."
				/bin/bash
			else
				log "rpmsign command failed:output is: -->\n${rpmbuild_out}\nrpmsign command output end\n\n."
			fi
		# don't accidentally retain unsigned files
		rm -f "${files}"
		exit "${exitcode}"
	fi
}

function finish {
  chown -R "${CALLING_UID}":"${CALLING_GID}" "${RPMS_DIR}" /tmp || /bin/true
  umount -f "${SOURCE_DIR}" || /bin/true
  log "Finished. Outcome: ${EXIT_STATUS}"
  [ "${EXIT_STATUS}" != "SUCCESS" ] && { echo -e "\n**** FULL OUTPUT START ****" >&3 ; cat ${CMD_OUTPUT_FILENAME} >&3 ; echo -e "\n**** FULL OUTPUT END ****" >&3 ; }
  rm -f "${CMD_OUTPUT_FILENAME}"
}
