# SPDX-FileCopyrightText: 2025 ProFACE developers
#
# SPDX-License-Identifier: MIT

import json
import logging
import sys
import tomllib
from importlib.metadata import entry_points
from pathlib import Path

import click
import h5py  # type: ignore[import-untyped]

from proface.preprocessor import PreprocessorError, __version__

# Configure logging
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

logger = logging.getLogger(__name__)


def _versions(ctx, _param, value) -> None:
    """print console script version and list available plugins"""

    if not value or ctx.resilient_parsing:
        return
    click.echo(f"{ctx.info_name}, version {__version__}")
    click.echo("\nAvailable plugins:")
    eps = entry_points(group="proface.preprocessor")
    for i in eps:
        assert i.dist is not None
        click.echo(f"  {i.name:10}: {i.dist.name}, version {i.dist.version}")
    ctx.exit()


@click.command
@click.option(
    "--version",
    is_flag=True,
    callback=_versions,
    expose_value=False,
    is_eager=True,
)
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
    # parse TOML job
    #
    try:
        with open(toml, "rb") as fp:
            job = tomllib.load(fp)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        _error(f"Error decoding JOB.TOML: {exc}")

    #
    # hdf5 output path
    #
    h5pth = toml.with_suffix(".h5")

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
    eps = entry_points(group="proface.preprocessor", name=f"{fea.lower()}")
    if len(eps) > 1:
        _error(f"More than one plugin registered: {eps}.", retcode=2)
    if len(eps) == 0:
        _error(
            f"A preprocessor plugin for {fea} FEA is not installed.",
            retcode=2,
            color="blue",
        )
    (plugin,) = eps

    #
    # create metadata
    #
    meta = {
        "metadata-version": "0.1",
        "type": "FEA",
        "version": "1.0",
        "generator": {
            "name": __name__,
            "version": __version__,
            "plugin": {
                "distribution-package": plugin.dist.name,
                "distribution-version": plugin.dist.version,
                "distribution-entry point": plugin.value,
            },
        },
    }
    logger.debug("Metadata: %s", meta)

    #
    # load fea plugin
    #
    logger.debug(
        "Loading plugin '%s:%s'",
        plugin.module,
        plugin.attr,
    )
    preproc = plugin.load()

    #
    # run preprocessor plugin
    #
    logger.debug("Opening h5 '%s'", h5pth)
    try:
        with h5py.File(h5pth, mode="w") as h5:
            h5.attrs["__proface.meta__"] = json.dumps(meta)
            preproc(job=fea_config, job_path=toml.with_suffix(""), h5=h5)
    except OSError as exc:
        _error(f"{exc}")
    except PreprocessorError as exc:
        _error(f"Conversion failed: {exc}")

    # all done, OK
    click.echo(h5pth)
    sys.exit(0)


def _error(msg: str, *, retcode: int = 1, color: str = "red") -> None:
    click.secho(msg, fg=color, file=sys.stderr)
    sys.exit(retcode)
