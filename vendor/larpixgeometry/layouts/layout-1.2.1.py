'''
Generate a YAML file with the pixel layout and chip connections for the
packaged 4-chip sensor plane, (first 3 chips only), with no focusing
grids or extended pads.

'''
import yaml
import patterngenerator as pg

pixels = []
subgrid_width = 16
pixels.extend(pg.pixels_plain_grid(4, 2, 1, 53.5, 65.5, len(pixels)))
pixels.extend(pg.pixels_plain_grid(4, 2, 1, 97.5, 65.5, len(pixels)))
pixels.extend(pg.pixels_plain_grid(4, 2, 1, 53.5, 109.5, len(pixels)))
x = 52
y = 64
width = 76
height = 60

pixelids = {
        # Bool value is argument to right_side_up
        # ranges are pixel id ranges for 0-15, 16-31
        3: (True, 'plain', range(0*16, 1*16), range(1*16, 2*16)),
        5: (True, 'plain', range(2*16, 3*16), range(3*16, 4*16)),
        10: (True, 'plain', range(4*16, 5*16), range(5*16, 6*16)),
}

chips = []
for chipid, (right_side_up, shape, first_ids, second_ids) in pixelids.items():
    assignment_1 = pg.grid_4x4_assignments_0_15_v1_2
    assignment_2 = pg.grid_4x4_assignments_16_31_v1_2
    first_channels = pg.assign_pixels(first_ids, assignment_1, right_side_up)
    second_channels = pg.assign_pixels(second_ids, assignment_2, right_side_up)
    chips.append([chipid, first_channels + second_channels])


with open('layout-1.2.1.yaml', 'w') as f:
    yaml.dump({'pixels': pixels, 'chips': chips, 'x': x, 'y': y,
        'width': width, 'height': height}, f)
