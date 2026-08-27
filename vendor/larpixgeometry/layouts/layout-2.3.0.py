'''
Generate a YAML file with the pixel layout and chip connections for the
packaged 3x3-rev2 pixel tile

'''
import yaml
import patterngenerator as pg

pixels = []
subgrid_width = 16
chip_ids = list(range(11,20)) \
    + list(range(21,30)) \
    + list(range(31,40)) \
    + list(range(41,50)) \
    + list(range(51,60)) \
    + list(range(61,70)) \
    + list(range(71,80)) \
    + list(range(81,90)) \
    + list(range(91,100)) \
    + list(range(101,110)) \
    + list(range(110,201,10))

print(chip_ids)
print('chips',len(chip_ids))
pixel_pitch = 4.434
width = pixel_pitch*69 + pixel_pitch
height = pixel_pitch*69 + pixel_pitch

two_digit_xy = lambda x: (((x%10-1)), 9-(x//10-1))
last_column_xy = lambda x: (9, 9-((x-100)//10-1))
last_row_xy = lambda x: (((x-100)%10-1), 0)

for chip in chip_ids:
    if chip < 100:
        x,y = two_digit_xy(chip)
    elif chip < 110:
        x,y = last_row_xy(chip)
    elif chip < 200:
        x,y = last_column_xy(chip)
    else:
        x,y = (9,0)
    x = x * 7 * pixel_pitch + pixel_pitch/2 - width/2
    y = y * 7 * pixel_pitch + pixel_pitch/2 - height/2
    pixels.extend(pg.pixels_plain_grid(pixel_pitch, 1, 1, x, y, len(pixels), batch_size=7, pixels_per_grid=49))

pixelids = dict()
for chip_idx, chip in enumerate(chip_ids):
    # Bool value is argument to right_side_up
    chip_pixels = list(range(chip_idx*49, chip_idx*49 + 49))
    pixelids[chip] = (True, 'plain', chip_pixels)

chips = []
for chipid, (right_side_up, shape, ids) in pixelids.items():
    assignment = pg.grid_7x7_assignments_0_64_v2_2_1
    channels = pg.assign_pixels(ids, assignment, right_side_up, range(64))
    chips.append([chipid, channels])
print('chips',len(chips))

with open('layout-2.3.0.yaml', 'w') as f:
    yaml.dump({'pixels': pixels, 'chips': chips, 'x': -width/2, 'y': -height/2,
        'width': width, 'height': height}, f)
