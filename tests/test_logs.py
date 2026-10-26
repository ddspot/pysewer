# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""``pysewer.show_progress`` and ``pysewer.capture_log``."""

import logging
import shutil
import warnings
from pathlib import Path

import pytest

import pysewer

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)
LOGGER = logging.getLogger("pysewer")


@pytest.fixture(autouse=True)
def _reset():
    state = (LOGGER.level, LOGGER.propagate, list(LOGGER.handlers))
    yield
    pysewer.show_progress(False)
    LOGGER.setLevel(state[0])
    LOGGER.propagate = state[1]
    LOGGER.handlers[:] = state[2]


@pytest.fixture(scope="module")
def dem(tmp_path_factory):
    path = tmp_path_factory.mktemp("logs") / "dem.tif"
    shutil.copy(TEST_DATA / "dem.tif", path)
    return path


def _run(dem, extra_building=None):
    md = pysewer.ModelDomain(
        dem, TEST_DATA / "roads_clipped.shp", TEST_DATA / "buildings_clipped.shp"
    )
    md.add_sink(SINK)
    G = md.generate_connection_graph()
    if extra_building:
        G.add_node(extra_building, node_type="building")  # no road link
    tree = pysewer.rsph_tree(G, [SINK])
    pysewer.estimate_peakflow(tree, inhabitants_dwelling=3, daily_wastewater_person=0.15)
    pysewer.calculate_hydraulic_parameters(tree, sinks=[SINK])
    return tree


# ----------------------------------------------------------- show_progress
def test_quiet_by_default(dem, capsys):
    _run(dem)
    assert capsys.readouterr().out == ""


def test_show_progress_prints_a_few_readable_lines(dem, capsys):
    pysewer.show_progress()
    _run(dem)
    out = capsys.readouterr().out.splitlines()
    assert out, "nothing printed"
    assert all(line.startswith("pysewer · ") for line in out)
    assert any("Sewer graph:" in line for line in out)
    # progress, not every pipe or profile segment
    assert len(out) < 20


def test_show_progress_debug_shows_details(dem, capsys):
    pysewer.show_progress("debug")
    _run(dem)
    out = capsys.readouterr().out
    assert "needs pump" in out or "gravity flow" in out


def test_show_progress_off_again(dem, capsys):
    pysewer.show_progress()
    pysewer.show_progress(False)
    _run(dem)
    assert capsys.readouterr().out == ""
    assert LOGGER.propagate is True


def test_show_progress_rejects_unknown_level():
    with pytest.raises(ValueError, match="level must be one of"):
        pysewer.show_progress("loud")


# ------------------------------------------------------------- capture_log
def test_capture_log_collects_instead_of_printing(dem, capsys):
    with pysewer.capture_log() as log:
        _run(dem)
    assert capsys.readouterr().out == ""
    assert any("Sewer graph:" in line for line in log.lines)
    assert log.warnings == []
    assert "Sewer graph:" in log.text
    assert len(log.tail(2).splitlines()) == 2
    assert repr(log).startswith(f"pysewer log: {len(log)} lines, 0 warnings")


def test_capture_log_reports_problems(dem, capsys):
    with pysewer.capture_log() as log:
        _run(dem, extra_building=(0, 0))
    assert len(log.warnings) == 1
    assert "no path to a sink" in log.warnings[0]
    assert log.warnings[0] in log.lines
    # one line so the problem is not silently hidden
    assert capsys.readouterr().out == "pysewer: 1 warning, see log.warnings\n"


def test_capture_log_report_off(dem, capsys):
    with pysewer.capture_log(report=False) as log:
        _run(dem, extra_building=(0, 0))
    assert log.warnings
    assert capsys.readouterr().out == ""


def test_capture_log_catches_a_repeated_warning():
    # the default filter shows a warning once per place; capture_log always collects it
    for _ in range(2):
        with pysewer.capture_log(report=False) as log:
            warnings.warn("same place", UserWarning, stacklevel=1)
        assert log.warnings == ["pysewer · WARNING · same place"]


def test_capture_log_save(tmp_path, dem):
    with pysewer.capture_log() as log:
        _run(dem)
    path = log.save(tmp_path / "run.log")
    assert path.read_text(encoding="utf-8").splitlines() == log.lines


def test_capture_log_restores_logger_and_progress(dem, capsys):
    pysewer.show_progress()
    level, propagate = LOGGER.level, LOGGER.propagate
    with pysewer.capture_log("debug") as log:
        _run(dem)
    assert capsys.readouterr().out == ""  # progress paused while capturing
    assert any("needs pump" in line or "gravity flow" in line for line in log.lines)
    assert (LOGGER.level, LOGGER.propagate) == (level, propagate)
    _run(dem)
    assert "Sewer graph:" in capsys.readouterr().out  # and back afterwards


def test_capture_log_restores_after_an_error():
    level = LOGGER.level
    with pytest.raises(RuntimeError), pysewer.capture_log():
        raise RuntimeError("boom")
    assert LOGGER.level == level
    assert not any(type(h).__name__ == "_CaptureHandler" for h in LOGGER.handlers)


def test_building_input_raises_no_warning(dem):
    # every run used to warn "Geometry column does not contain geometry"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        pysewer.Buildings(
            TEST_DATA / "buildings_clipped.shp",
            roads_obj=pysewer.Roads(TEST_DATA / "roads_clipped.shp"),
        )
