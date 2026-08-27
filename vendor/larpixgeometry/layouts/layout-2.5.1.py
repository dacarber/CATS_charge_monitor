'''
Generate a YAML file with the pixel layout and chip connections for the
packaged 10x10-chip, 6400-pixel tile

'''
import yaml
import patterngenerator as pg

pixels = []
subgrid_width = 16
chip_ids = list(range(11,111))

pixel_pitch = 3.8 
width = pixel_pitch*80
height = pixel_pitch*80

two_digit_xy = lambda x: ((x%10-1), 9-(x//10-1))
last_column_xy = lambda x: (9, 9-(x//10-2))
last_row_xy = lambda x: ((x%100-1), 0)


for chip in chip_ids:
    if chip < 100:
        x,y = two_digit_xy(chip)
    elif chip>=100:
        x,y = last_row_xy(chip)

    if chip%10==0:
        x,y = last_column_xy(chip)

    x = x * 8 * pixel_pitch + pixel_pitch/2 - width/2
    y = y * 8 * pixel_pitch + pixel_pitch/2 - height/2
    pixels.extend(pg.pixels_plain_grid(pixel_pitch, 1, 1, x, y, len(pixels), batch_size=8, pixels_per_grid=64))

pixelids = dict()
for chip_idx, chip in enumerate(chip_ids):
    # Bool value is argument to right_side_up
    chip_pixels = list(range(chip_idx*64, chip_idx*64 + 64))
    pixelids[chip] = (True, 'plain', chip_pixels)

chips = []
for chipid, (right_side_up, shape, ids) in pixelids.items():
    assignment = pg.grid_8x8_assignments_0_64_v2_1
    channels = pg.assign_pixels(ids, assignment, right_side_up, range(64))
    chips.append([chipid, channels])

with open('layout-2.5.1.yaml', 'w') as f:
    yaml.dump({'pixels': pixels, 'chips': chips, 'x': -width/2, 'y': -height/2,
        'width': width, 'height': height}, f)
