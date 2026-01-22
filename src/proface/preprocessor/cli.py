# SPDX-FileCopyrightText: 2025 ProFACE developers
#
# SPDX-License-Identifier: MIT

import json
import logging
import sys
import tomllib
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

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


def _versions(
    ctx: click.Context,
    _param: click.Parameter,
    value: bool,  # noqa: FBT001
) -> None:
    """print console script version and list available plugins"""

    if not value or ctx.resilient_parsing:
        return
    click.echo(f"{ctx.info_name}, version {__version__}")
    click.echo("\nAvailable FEA plugins:")
    eps = entry_points(group="proface.preprocessor")
    for i in eps:
        assert i.dist is not None
        click.echo(f"  {i.name:10}: {i.dist.name}, version {i.dist.version}")

    click.echo("\nAvailable “transforms” plugins:")
    eps = entry_points(group="proface.preprocessor.tools")
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
def main(toml: Path, log_level: str) -> None:
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

    logger.info("Reading %s", toml.resolve().as_uri())
    #
    # parse TOML job
    #
    try:
        with open(toml, "rb") as fp:
            job = tomllib.load(fp)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        _error(f"Error decoding JOB.TOML: {exc}")

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
    # open temporary h5 file in memory, to be modified in place
    #
    logger.debug("Opening temporary h5 in memory")
    h5tmp = h5py.File.in_memory()

    #
    # run FEA translator
    #
    logger.info("\N{BLACK RIGHT-POINTING TRIANGLE} FEA translation")
    try:
        fea_meta = _fea_translator(
            fea=fea, job=fea_config, job_path=toml.with_suffix(""), h5=h5tmp
        )
    except PreprocessorError as exc:
        _error(f"Translation failed: {exc}")

    #
    # create metadata
    #
    meta: dict[str, Any] = {
        "metadata-version": "0.1.1",
        "type": "FEA",
        "version": "1.0",
        "generator": {
            "name": __name__,
            "version": __version__,
            "plugin": fea_meta,
        },
    }

    #
    # apply transforms
    #
    for step in job.get("transforms") or []:
        logger.info(
            "\N{BLACK RIGHT-POINTING TRIANGLE} Transform %s", step["_plugin"]
        )
        try:
            transform_meta = _apply_transform(
                h5=h5tmp, job=step, job_path=toml.with_suffix("")
            )
        except PreprocessorError as exc:
            _error(f"Transformation failed: {exc}")
        meta.setdefault("transforms", []).append(transform_meta)

    #
    # write h5 on disk
    #
    h5pth = toml.with_suffix(".h5")
    logger.info("Writing %s", h5pth.resolve().as_uri())
    logger.debug("Metadata: %s", meta)
    try:
        with h5py.File(h5pth, mode="w") as h5:
            h5.attrs["__proface.meta__"] = json.dumps(meta)
            for g in h5tmp:
                h5.copy(h5tmp[g], g)
    except OSError as exc:
        _error(f"{exc}")

    # all done, OK
    click.echo(h5pth)
    sys.exit(0)


def _error(msg: str, *, retcode: int = 1, color: str = "red") -> None:
    click.secho(msg, fg=color, file=sys.stderr)
    sys.exit(retcode)


def _load_plugin(
    group: str, name: str
) -> tuple[Callable[..., None], dict[str, str]]:
    """load plugin at (group, name)"""

    logger.debug("Searching plugin %s-%s", group, name)

    # search entry points
    eps = entry_points(group=group, name=name)
    if len(eps) > 1:
        msg = f"More than one plugin registered: {eps}."
        raise RuntimeError(msg)
    if len(eps) == 0:
        msg = f"A plugin for '{name}' is not installed in '{group}'."
        raise RuntimeError(msg)
    (plugin,) = eps
    logger.debug("Found plugin: %s", plugin)

    # build metadata from distro info
    assert plugin.dist is not None
    meta: dict[str, str] = {
        "distribution-package": plugin.dist.name,
        "distribution-version": plugin.dist.version,
        "distribution-entry point": plugin.value,
    }
    logger.debug("Plugin metadata: %s", meta)

    # load plugin
    logger.debug(
        "Loading plugin '%s:%s'",
        plugin.module,
        plugin.attr,
    )
    translator = plugin.load()

    return translator, meta


def _fea_translator(
    *, fea: str, job: dict[str, Any], job_path: Path, h5: h5py.File
) -> dict[str, str]:
    #
    # search fea plugin
    #
    try:
        fea_translator, fea_meta = _load_plugin(
            group="proface.preprocessor", name=f"{fea.lower()}"
        )
    except RuntimeError as exc:
        raise ValueError(exc) from exc
        _error(str(exc), retcode=2)
    #
    # run FEA plugin
    #
    try:
        fea_translator(job=job, job_path=job_path, h5=h5)
    except PreprocessorError as exc:
        _error(f"Conversion failed: {exc}")

    return fea_meta


def _apply_transform(
    *, h5: h5py.File, job: dict[str, str], job_path: Path
) -> dict[str, str]:
    step_meta = {k: v for k, v in job.items() if k.startswith("_")}
    step_config = {k: v for k, v in job.items() if not k.startswith("_")}
    plug = step_meta["_plugin"]
    plug_main, plug_meta = _load_plugin(
        group="proface.preprocessor.tools", name=plug
    )
    plug_main(job=step_config, job_path=job_path, h5=h5)
    return plug_meta
