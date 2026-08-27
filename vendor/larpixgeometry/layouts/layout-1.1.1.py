'''
Generate a YAML file with the pixel layout and chip connections for the
28-chip sensor plane, with no focusing grids or extended pads, for 8
chips in one column.

'''
import yaml
import patterngenerator as pg

pixels = []
subgrid_width = 12
pixels.extend(pg.pixels_triangle_grid(subgrid_width, 4, 1, 76, 52, 0))
pixels.extend(pg.pixels_triangle_grid(subgrid_width, 6, 1, 64, 64,
    len(pixels)))
pixels.extend(pg.pixels_triangle_grid(subgrid_width, 8, 2, 52, 76,
    len(pixels)))
pixels.extend(pg.pixels_plain_grid(3, 8, 2, 53.5, 101.5, len(pixels)))
pixels.extend(pg.pixels_plain_grid(3, 6, 1, 65.5, 125.5, len(pixels)))
pixels.extend(pg.pixels_plain_grid(3, 4, 1, 77.5, 137.5, len(pixels)))
x = 52
y = 52
width = 96
height = 96

pixelids = {
        # Bool value is argument to right_side_up
        # ranges are pixel id ranges for 0-15, 16-31
        48: (False, 'triangle', range(1*16, 2*16), range(0*16, 1*16)),
        51: (False, 'triangle', range(6*16, 7*16), range(5*16, 6*16)),
        53: (False, 'triangle', range(13*16, 14*16), range(12*16, 13*16)),
        57: (False, 'triangle', range(21*16, 22*16), range(20*16, 21*16)),
        54: (False, 'plain', range(29*16, 30*16), range(28*16, 29*16)),
        58: (False, 'plain', range(37*16, 38*16), range(36*16, 37*16)),
        60: (False, 'plain', range(44*16, 45*16), range(43*16, 44*16)),
        63: (False, 'plain', range(49*16, 50*16), range(48*16, 49*16)),

}

chips = []
for chipid, (right_side_up, shape, first_ids, second_ids) in pixelids.items():
    if shape == 'plain':
        assignment = pg.grid_4x4_assignments_v1
    else:
        assignment = pg.triangle_assignments_v1
    first_channels = pg.assign_pixels(first_ids, assignment, right_side_up)
    second_channels = pg.assign_pixels(second_ids, assignment, right_side_up)
    chips.append([chipid, first_channels + second_channels])


with open('layout-1.1.1.yaml', 'w') as f:
    yaml.dump({'pixels': pixels, 'chips': chips, 'x': x, 'y': y,
        'width': width, 'height': height}, f)
