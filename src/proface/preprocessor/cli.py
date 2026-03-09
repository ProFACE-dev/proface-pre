# SPDX-FileCopyrightText: 2025 ProFACE developers
#
# SPDX-License-Identifier: MIT

import json
import logging
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import click
import h5py  # type: ignore[import-untyped]

from proface.preprocessor import PreprocessorError, __version__


class SchemaError(PreprocessorError):
    """
    Raised when the job configuration doesn't adhere to the expected schema.
    """


# Configure logging
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

logger = logging.getLogger(__name__)


@dataclass
class FEA:
    software: str
    data: dict[str, Any]


@dataclass
class Transform:
    meta: dict[str, Any]
    data: dict[str, Any]

    @classmethod
    def from_job(cls, config: dict[str, Any]) -> "Transform":
        meta: dict[str, Any] = {}
        data: dict[str, Any] = {}
        for k, v in config.items():
            (meta if k.startswith("_") else data)[k] = v
        return cls(meta=meta, data=data)


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

    #
    # read/decode, parse TOML job
    #
    logger.info("Reading %s", toml.resolve().as_uri())
    try:
        with open(toml, "rb") as fp:
            job = tomllib.load(fp)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        _error(f"Error decoding JOB.TOML: {exc}")

    try:
        fea, transforms = _parse_job(job)
    except SchemaError as exc:
        _error(f"Invalid JOB.TOML: {exc}")

    #
    # open temporary h5 file in memory, to be modified in place
    #
    logger.debug("Opening temporary h5 in memory")
    h5tmp = h5py.File.in_memory()

    #
    # run FEA translator
    #
    logger.info(
        "\N{BLACK RIGHT-POINTING TRIANGLE} FEA translation for '%s'", fea
    )
    try:
        fea_meta = _fea_translator(
            fea=fea, job_path=toml.with_suffix(""), h5=h5tmp
        )
    except (RuntimeError, OSError) as exc:
        _error(f"{exc}")
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
    for step in transforms:
        logger.info(
            "\N{BLACK RIGHT-POINTING TRIANGLE} Transform '%s'",
            step.meta["_plugin"],
        )
        try:
            transform_meta = _apply_transform(
                h5=h5tmp, transform=step, job_path=toml.with_suffix("")
            )
        except (RuntimeError, PreprocessorError) as exc:
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


def _parse_job(
    job: dict[str, Any],
) -> tuple[FEA, list[Transform]]:
    #
    # fea_software spec
    #
    try:
        fea_software = job["fea_software"]
    except KeyError as exc:
        msg = f"missing {exc} key."
        raise SchemaError(msg) from exc

    if not isinstance(fea_software, str):
        msg = "'fea_software' has not a string value"
        raise SchemaError(msg)

    fea_config = job.get(fea_software, {})
    if not isinstance(fea_config, dict):
        msg = f"'{fea_software}' is not a table."
        raise SchemaError(msg)

    fea = FEA(software=fea_software, data=fea_config)

    #
    # transforms spec
    #
    transforms_raw = job.get("transforms")
    if transforms_raw is None:
        # missing transforms array is valid and no-op
        return fea, []

    if not isinstance(transforms_raw, list) or any(
        not isinstance(i, dict) for i in transforms_raw
    ):
        msg = "'transforms' is not array of tables (i.e. [[transforms]])"
        raise SchemaError(msg)

    # map to Transform class
    transforms = [Transform.from_job(config) for config in transforms_raw]

    # check if mandatory _plugin meta is defined and string
    for t in transforms:
        if "_plugin" not in t.meta or not isinstance(t.meta["_plugin"], str):
            msg = "'_plugin' must be a valid name in each transform."
            raise SchemaError(msg)

    return fea, transforms


def _fea_translator(
    *, fea: FEA, job_path: Path, h5: h5py.File
) -> dict[str, str]:
    #
    # search fea plugin
    #
    fea_translator, fea_meta = _load_plugin(
        group="proface.preprocessor", name=f"{fea.software.lower()}"
    )
    #
    # run FEA plugin
    #
    fea_translator(job=fea.data, job_path=job_path, h5=h5)

    return fea_meta


def _apply_transform(
    *, h5: h5py.File, transform: Transform, job_path: Path
) -> dict[str, str]:
    plug = transform.meta["_plugin"]
    plug_main, plug_meta = _load_plugin(
        group="proface.preprocessor.tools", name=plug
    )
    plug_main(job=transform.data, job_path=job_path, h5=h5)
    return plug_meta
