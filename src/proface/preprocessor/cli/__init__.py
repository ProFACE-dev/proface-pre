# SPDX-FileCopyrightText: 2025 ProFACE developers
#
# SPDX-License-Identifier: MIT

import logging
import sys
import tomllib
from importlib.metadata import entry_points
from pathlib import Path

import click
import h5py

from proface.preprocessor import PreprocessorError

# Configure logging
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


@click.command()
@click.option(
    "--log-level",
    type=click.Choice(list(LOG_LEVELS), case_sensitive=False),
    default="info",
    help="Set the logging level.",
)
@click.argument(
    "toml",
    metavar="JOB.TOML",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    nargs=1,
)
def main(toml, log_level):
    with open(toml, "rb") as fp:
        try:
            job = tomllib.load(fp)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            _error(f"Error decoding JOB.TOML: {exc}")

    #
    # hdf5 output path
    #
    h5pth = toml.parent / toml.with_suffix(".h5").name

    #
    # read and check JOB.TOML 'preamble'
    #
    if "fea_software" not in job:
        _error("Invalid JOB.TOML: missing 'fea_software' key.")
    fea = job["fea_software"]

    if fea not in job:
        _error(f"Invalid JOB.TOML: missing '{fea}' table.")
    fea_config = job[fea]
    if not isinstance(fea_config, dict):
        _error(f"Invalid JOB.TOML: '{fea}' is not a table.")

    #
    # search fea plugin
    #
    eps = entry_points(group=f"proface.{fea.lower()}", name="preprocessor")
    if len(eps) > 1:
        _error(f"More than one plugin registered: {eps}.", retcode=2)
    if len(eps) == 0:
        _error(
            f"A preprocessor plugin for {fea} FEA is not installed.",
            retcode=2,
            color="blue",
        )

    #
    # load fea plugin
    #
    try:
        preproc = eps["preprocessor"].load()
    except ImportError as exc:
        _error(f"Unalble to load plugin '{exc.name}'.")

    #
    # setup logging
    #
    logging.basicConfig(
        level=LOG_LEVELS[log_level],
        format="%(levelname)s: %(message)s"
        if LOG_LEVELS[log_level] >= logging.INFO
        else "%(name)s-%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    #
    # run preprocessor plugin
    #
    with h5py.File(h5pth, mode="w") as h5:
        try:
            preproc(job=fea_config, job_dir=toml.parent, h5=h5)
        except PreprocessorError as exc:
            _error(f"Conversion failed: {exc}")

    # all done, OK
    click.echo(h5pth)
    sys.exit(0)


def _error(msg: str, *, retcode: int = 1, color: str = "red") -> None:
    click.secho(msg, fg=color, file=sys.stderr)
    sys.exit(retcode)
