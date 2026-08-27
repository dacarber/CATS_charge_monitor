'''
Generate a YAML file with the pixel layout and chip connections for the
packaged 4-chip sensor plane, (first 3 chips only), with no focusing
grids or extended pads.

'''
import yaml
import patterngenerator as pg

pixels = []
subgrid_width = 16
chip_ids = (14,13,12,24,23,22,34,33,32)
width = 8*4*3
height = 8*4*3
for chip in chip_ids:
    x = (chip // 10 - 1) * 4 * 4 * 2 + 2 - width/2
    y = (4 - chip % 10) * 4 * 4 * 2 + 2 - height/2
    pixels.extend(pg.pixels_plain_grid(4, 2, 2, x, y, len(pixels)))

pixelids = dict()
for chip_idx, chip in enumerate(chip_ids):
    # Bool value is argument to right_side_up
    # ranges are pixel id ranges for 0-15, 16-31, 32-47, 48-63
    pixels_0 = range(chip_idx*64, chip_idx*64 + 16) # quad 0
    pixels_3 = range(chip_idx*64 + 16, chip_idx*64 + 32) # quad 3
    pixels_1 = range(chip_idx*64 + 32, chip_idx*64 + 48) # quad 1
    pixels_2 = range(chip_idx*64 + 48, chip_idx*64 + 64) # quad 2
    pixelids[chip] = (True, 'plain', pixels_0, pixels_1, pixels_2, pixels_3)

chips = []
for chipid, (right_side_up, shape, ids_0, ids_1, ids_2, ids_3) in pixelids.items():
    assignment_0 = pg.grid_4x4_assignments_0_16_v2_2
    assignment_1 = pg.grid_4x4_assignments_16_31_v2_2
    assignment_2 = pg.grid_4x4_assignments_31_46_v2_2
    assignment_3 = pg.grid_4x4_assignments_46_64_v2_2
    channels_0 = pg.assign_pixels(ids_0, assignment_0, right_side_up)
    channels_1 = pg.assign_pixels(ids_1, assignment_1, right_side_up)
    channels_2 = pg.assign_pixels(ids_2, assignment_2, right_side_up)
    channels_3 = pg.assign_pixels(ids_3, assignment_3, right_side_up)
    chips.append([chipid, channels_0 + channels_1 + channels_2 + channels_3])

with open('layout-2.2.0.yaml', 'w') as f:
    yaml.dump({'pixels': pixels, 'chips': chips, 'x': -width/2, 'y': -height/2,
        'width': width, 'height': height}, f)
