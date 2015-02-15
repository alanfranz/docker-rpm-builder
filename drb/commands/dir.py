from __future__ import unicode_literals

import os
import codecs
import glob
import logging
import click

from drb.spectemplate import DoubleDelimiterTemplate
from drb.which import which
from drb.spawn import sp
from drb.path import getpath
from drb.pull import pull
from drb.bash import serialize, provide_encoded_signature, spawn_interactive
from drb.downloadsources import downloadsources

_HELP = """Builds a binary RPM from a directory. Uses `docker run` under the hood.

    IMAGE should be a docker image id or a repository:tag,
    e.g something like a682b68bbaba or alanfranz/drb-epel-6-x86-64:latest ;
    anything that can be passed to `docker run` as an IMAGE parameter will do.

    SOURCE_DIRECTORY should be a directory containing the .spec or the
    .spectemplate file and all the source files and patches referenced
    in such spec. If using a .spectemplate the directory should be writeable,
    since a .spec file will be written there (and then removed).

    TARGET_DIRECTORY is where the RPMS will be written. Anything inside
    may be overwritten during the build phase.

    ADDITIONAL_DOCKER_OPTIONS whatever is passed will be forwarded
    straight to the 'docker run' command. PLEASE REMEMBER to insert a double dash (--)
    before the first additional options, otherwise it will be mistaken
    for a docker-rpm-builder option.

    Options:

    --download-sources: if enabled, SourceX and PatchX from the spec
    that contain an URL will be downloaded from the internet. Such
    files will be placed in SOURCE_DIRECTORY, so it should be writeable.
    Such files won't be deleted afterwards and will be cached for future
    builds.
    WARNING: if this option is used and a file already exists, it may be
    overwritten according to wget --timestamping caching policy.

    --bash-on-failure: if enabled, the tool will drop in an interactive
    shell inside the container if the build fails.

    --sign-with <PATH>: if passed, the chosen GPG key file is used to sign the package.
    Currently, such file MUST be a readable, password-free, ascii-armored
    GPG private key file.

    --always-pull: if passed, a `docker pull` for the latest
    image version from Docker Hub (or other configured endpoint) is performed. Please note that
    any error that may arise from the operation is currently ignored.
    
    --uid: if passed uses the given value as the uid when creating a local user
    to satisfy rpmbuild's need for a valid uid; defaults to calling user's uid.
    
    --gid: if passed uses the given value as the gid when creating a local group
    to satisfy rpmbuild's need for a valid gid; defaults to calling user's gid.
    
    Examples:

    - in this scenario we use no option of ours but we add an option to be forwarded to docker:

    docker-rpm-builder dir a682b68bbaba . /tmp/rpms -- --dns=192.168.1.1

    - in this scenario we use a repository:tag as an image, and we ask drb to download the sources from the internet for us:

    docker-rpm-builder dir alanfranz/drb-epel-6-x86-64:latest /home/user/sourcedir/myproject /tmp/rpms --download-sources

    """

_logger = logging.getLogger("drb.commands.dir")

@click.command(help=_HELP)
@click.argument("image", type=click.STRING)
@click.argument("source_directory", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("target_directory", type=click.Path(file_okay=False, resolve_path=True))
@click.argument("additional_docker_options", type=click.STRING, nargs=-1)
@click.option("--download-sources", is_flag=True)
@click.option("--bash-on-failure", is_flag=True)
@click.option("--sign-with", nargs=1, type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.option("--always-pull", is_flag=True)
@click.option("--uid", type=int, help="uid of calling user", default=os.getuid())
@click.option("--gid", type=int, help="gid of calling user", default=os.getgid())
def dir(image, source_directory, target_directory, additional_docker_options, download_sources=False,
        bash_on_failure=False, sign_with=None, always_pull=False, uid=-1, gid=-1):


    # TODO: let spectemplate and/or spec be optional parameters
    # TODO: let the user choose $-delimited templates
    deletespec = False
    spectemplates = [os.path.join(source_directory, fn) for fn in glob.glob1(source_directory, "*.spectemplate")]
    specfiles = [os.path.join(source_directory, fn) for fn in glob.glob1(source_directory, "*.spec")]
    if len(spectemplates) > 1:
        raise ValueError("More than one spectemplate found in source directory")

    if not os.path.exists(target_directory):
        os.mkdir(target_directory)

    if spectemplates:
        if specfiles:
            raise ValueError("Found both .spec and .spectemplate in source directory.")
        spectemplate = spectemplates[0]
        template = DoubleDelimiterTemplate(codecs.open(spectemplate, "rb", "utf-8").read())
        with_substitutions = template.substitute(os.environ)
        finalspec = os.path.splitext(spectemplate)[0] + ".spec"
        with codecs.open(finalspec, "wb", "utf-8") as f:
            f.write(with_substitutions)
        specfiles.append(finalspec)
        deletespec = True

    if not specfiles or len(specfiles) > 1:
        raise ValueError("No specfiles or more than one specfile in source directory")

    specfile = specfiles[0]

    if download_sources:
        downloadsources(source_directory, specfile)

    _logger.info("Now building project from %s on image %s", source_directory, image)
    dockerexec = which("docker")

    bashonfail = "dontspawn"
    bashonfail_options = ""
    spawn_func = sp
    if bash_on_failure:
        bashonfail = "bashonfail"
        bashonfail_options = "-i -t"
        spawn_func = spawn_interactive

    sign_with_encoded = provide_encoded_signature(sign_with)

    if always_pull:
        pull(dockerexec, image)

    serialized_options = serialize({"CALLING_UID": uid, "CALLING_GID": gid, "BASH_ON_FAIL":bashonfail, "GPG_PRIVATE_KEY": sign_with_encoded})

    try:
        additional_docker_options = " ".join(additional_docker_options)
        dockerscripts = getpath("drb/dockerscripts")
        rpms_inner_dir = sp("{dockerexec} run --rm {image} rpm --eval %{{_rpmdir}}", **locals()).strip()
        sources_inner_dir = sp("{dockerexec} run --rm {image} rpm --eval %{{_sourcedir}}", **locals()).strip()
        spawn_func("{dockerexec} run {additional_docker_options} -v {dockerscripts}:/dockerscripts -v {source_directory}:{sources_inner_dir} -v {target_directory}:{rpms_inner_dir} {bashonfail_options} -w /dockerscripts {image}  ./rpmbuild-dir-in-docker.sh {serialized_options}", **locals())
    finally:
        if deletespec:
            os.unlink(specfile)





