'''
Generate a YAML file with the pixel layout and chip connections for the
original 4-chip sensor plane, with no focusing grids or extended pads.

'''
import yaml
import patterngenerator as pg

pixels = []
subgrid_width = 12
pixels.extend(pg.pixels_triangle_grid(subgrid_width, 4, 1, 76, 88,
    len(pixels)))
pixels.extend(pg.pixels_plain_grid(3, 4, 1, 77.5, 113.5, len(pixels)))
x = 76
y = 88
width = 48
height = 36

pixelids = {
        # Bool value is argument to right_side_up
        # ranges are pixel id ranges for 0-15, 16-31
        245: (False, 'triangle', range(1*16, 2*16), range(0*16, 1*16)),
        252: (True, 'triangle', range(2*16, 3*16), range(3*16, 4*16)),
        246: (False, 'plain', range(5*16, 6*16), range(4*16, 5*16)),
        243: (True, 'plain', range(6*16, 7*16), range(7*16, 8*16))
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


with open('layout-1.0.0.yaml', 'w') as f:
    yaml.dump({'pixels': pixels, 'chips': chips, 'x': x, 'y': y,
        'width': width, 'height': height}, f)
