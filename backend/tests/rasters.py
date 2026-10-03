"""Synthetic rasters for the pipeline tests (needs rasterio).

The orthoimage is a painted scene with exact, known geometry:

    0.05 m pixels, 700 x 600 px (35 m x 30 m), EPSG:32643, RGB + alpha

    green   field     cols  20..420, rows  20..320   300 m2 (280 after the hole)
    red     building  cols 100..200, rows 100..180    20 m2, inside the field
    grey    road      cols   0..700, rows 360..420   105 m2, 2 m south of the field
    blue    water     cols 500..600, rows  50..150    25 m2
    yellow  unsure    cols 500..600, rows 180..240    15 m2
    alpha 0           rows 500..600                  NoData strip

``ColourModel`` stands in for the trained network *in tests only*: it maps
those colours to class logits, with deliberately ambiguous logits for
yellow, so that the pipeline's handling of confidence and entropy can be
checked against exact expected values.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.tests import support

PIXEL = 0.05
WIDTH, HEIGHT = 700, 600

FIELD = (20, 20, 420, 320)       # col0, row0, col1, row1
BUILDING = (100, 100, 200, 180)
ROAD = (0, 360, 700, 420)
WATER = (500, 50, 600, 150)
UNSURE = (500, 180, 600, 240)
NODATA_ROWS = (500, 600)

GREEN = (40, 200, 40)
RED = (200, 40, 40)
GREY = (128, 128, 128)
BLUE = (40, 40, 200)
YELLOW = (220, 220, 40)
WHITE = (255, 255, 255)


def paint() -> tuple[np.ndarray, np.ndarray]:
    rgb = np.empty((3, HEIGHT, WIDTH), dtype=np.uint8)
    for band in range(3):
        rgb[band] = WHITE[band]

    def fill(box, colour):
        col0, row0, col1, row1 = box
        for band in range(3):
            rgb[band, row0:row1, col0:col1] = colour[band]

    fill(FIELD, GREEN)
    fill(BUILDING, RED)
    fill(ROAD, GREY)
    fill(WATER, BLUE)
    fill(UNSURE, YELLOW)

    alpha = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    alpha[NODATA_ROWS[0] : NODATA_ROWS[1], :] = 0
    rgb[:, NODATA_ROWS[0] : NODATA_ROWS[1], :] = 0
    return rgb, alpha


def origin() -> tuple[float, float]:
    """Upper-left corner of the scene in EPSG:32643."""

    easting, northing = support.origin_utm()
    return easting, northing + HEIGHT * PIXEL


def write_orthoimage(path: Path, *, crs: str | None = "EPSG:32643", with_alpha: bool = True) -> Path:
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin

    rgb, alpha = paint()
    west, north = origin()
    count = 4 if with_alpha else 3
    profile = dict(
        driver="GTiff", width=WIDTH, height=HEIGHT, count=count, dtype="uint8",
        transform=from_origin(west, north, PIXEL, PIXEL), photometric="RGB",
    )
    if crs:
        profile["crs"] = crs
    if with_alpha:
        profile["alpha"] = "YES"
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb, [1, 2, 3])
        interpretation = [ColorInterp.red, ColorInterp.green, ColorInterp.blue]
        if with_alpha:
            dst.write(alpha, 4)
            interpretation.append(ColorInterp.alpha)
        dst.colorinterp = interpretation
    return path


def write_elevation(path: Path, raised: dict[tuple[float, float, float, float], float], base: float = 200.0) -> Path:
    """A 0.5 m elevation raster over the fixture layers.

    ``raised`` maps ``(x, y, width, height)`` rectangles (metres from the
    fixture origin, the same convention as ``support.rect``) to elevations.
    """

    import rasterio
    from rasterio.transform import from_origin

    cell = 0.5
    easting, northing = support.origin_utm()
    west, south = easting - 20.0, northing - 20.0
    cols, rows = 300, 560  # 150 m x 280 m
    north = south + rows * cell
    data = np.full((rows, cols), base, dtype=np.float32)
    for (x, y, width, height), elevation in raised.items():
        col0 = int(round((easting + x - west) / cell))
        col1 = int(round((easting + x + width - west) / cell))
        row0 = int(round((north - (northing + y + height)) / cell))
        row1 = int(round((north - (northing + y)) / cell))
        data[row0:row1, col0:col1] = elevation
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=cols, height=rows, count=1, dtype="float32",
        crs="EPSG:32643", transform=from_origin(west, north, cell, cell), nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    return path


def colour_model():
    """A deterministic stand-in network for tests: colour -> class logits."""

    import torch

    class ColourModel(torch.nn.Module):
        def forward(self, x):  # x: [N, 3, H, W], values 0..1
            r, g, b = x[:, 0], x[:, 1], x[:, 2]
            logits = torch.zeros((x.shape[0], 6, x.shape[2], x.shape[3]), dtype=torch.float32)

            field = (g > 0.6) & (r < 0.4) & (b < 0.4)
            building = (r > 0.6) & (g < 0.4) & (b < 0.4)
            water = (b > 0.6) & (r < 0.4) & (g < 0.4)
            road = (r > 0.4) & (r < 0.6) & (g > 0.4) & (g < 0.6) & (b > 0.4) & (b < 0.6)
            unsure = (r > 0.6) & (g > 0.6) & (b < 0.4)
            background = ~(field | building | water | road | unsure)

            logits[:, 0][background] = 8.0
            logits[:, 1][field] = 8.0
            logits[:, 2][building] = 8.0
            logits[:, 3][road] = 8.0
            logits[:, 4][water] = 8.0
            # Ambiguous: Field barely ahead of Other, everything else close.
            logits[:, 1][unsure] = 1.1
            logits[:, 5][unsure] = 1.0
            return logits

    return ColourModel()


def loader(_path):
    import torch

    return colour_model(), torch.device("cpu")
