'''
Generate pixel plane patterns.

'''

from itertools import product
import numpy as np

def pixels_plain_grid(pixel_pitch, nblocksx, nblocksy, startx, starty, start_index, batch_size=4, pixels_per_grid=16):
    '''
    A plain grid of no-pad no-focus pixels, numbered in batches of batch_size x batch_size.

    '''
    pixels = []
    repetition_period = batch_size * pixel_pitch
    x, y, = np.meshgrid(pixel_pitch*np.arange(batch_size),
            pixel_pitch*np.arange(batch_size))
    subgrid = np.array(list(zip(x.reshape(-1), y.reshape(-1))))
    # This line forms the list of blocks going along rows. Without this
    # nonsense (just using product(range, range) goes down columns).
    blocklist = np.array(list(product(range(nblocksy), range(nblocksx))))[:, ::-1]
    for block_index, (xblock, yblock) in enumerate(blocklist):
        pixelids = range(block_index*pixels_per_grid + start_index,
                (block_index+1)*pixels_per_grid + start_index)
        offset = np.array([xblock*repetition_period + startx,
            yblock*repetition_period + starty])
        pixel_locations = subgrid + offset
        for pixelid, (x, y) in zip(pixelids, pixel_locations):
            pixels.append([pixelid, float(x), float(y), [], []])
    return pixels

def pixels_triangle_grid(repetition_period, nblocksx, nblocksy, startx,
        starty, start_index):
    '''
    A grid of triangle pixels specific to the LArPix sensor board.

    '''
    pixels = []
    unit = repetition_period / 8.0
    subgrid = np.array(  # Laid out here in the orientation on the board
              [[2*unit, unit],                [6*unit, unit],
        [unit, 2*unit], [3*unit, 2*unit], [5*unit, 2*unit], [7*unit, 2*unit],
               [2*unit, 10/3.*unit],          [6*unit, 10/3.*unit],
               [2*unit, 14/3.*unit],          [6*unit, 14/3.*unit],
        [unit, 6*unit], [3*unit, 6*unit], [5*unit, 6*unit], [7*unit, 6*unit],
               [2*unit, 7*unit],              [6*unit, 7*unit]])
    pixels_per_grid = len(subgrid)
    # This line forms the list of blocks going along rows. Without this
    # nonsense (just using product(range, range) goes down columns).
    blocklist = np.array(list(product(range(nblocksy), range(nblocksx))))[:, ::-1]
    for block_index, (xblock, yblock) in enumerate(blocklist):
        pixelids = range(block_index*pixels_per_grid + start_index,
                (block_index+1)*pixels_per_grid + start_index)
        offset = np.array([xblock*repetition_period + startx,
            yblock*repetition_period + starty])
        pixel_locations = subgrid + offset
        for pixelid, (x, y) in zip(pixelids, pixel_locations):
            pixels.append([pixelid, float(x), float(y), [], []])
    return pixels

grid_4x4_assignments_v1 = [14, 13, 15, 12, 10, 9, 11, 8, 7, 4, 6, 5, 3, 0, 2, 1]
'''
Assignments list maps channel to geometrical position in 4x4 grid (v1.0 and
v1.1).

I.e. assignments[2] gives the location of channel 2, where the locations
are numbered according to the following grid::

    0  1  2  3
    4  5  6  7
    8  9  10 11
    12 13 14 15

Channel assignments (right side up) (from the chip side)::

    13 15 14 12     29 31 30 28
    9  11 10 8  or  25 27 26 24
    7  5  4  6      23 21 20 22
    3  1  0  2      19 17 16 18

'''
grid_4x4_assignments_0_15_v1_2 = [14, 13, 12, 15, 10, 9, 11, 8, 7, 4, 5, 6, 0, 3, 1, 2]
grid_4x4_assignments_16_31_v1_2 = [13, 14, 15, 12, 9, 10, 8, 11, 4, 7, 6, 5, 3, 0, 2, 1]
'''
Assignments list maps channel to geometrical position in 4x4 grid (v1.2).

I.e. assignments[2] gives the location of channel 2, where the locations
are numbered according to the following grid::

0  1  2  3
4  5  6  7
8  9  10 11
12 13 14 15

Channel assignments (right side up) (from the chip side)::

12 14 15 13     29 31 30 28
9  10 11 8  or  24 27 26 25
7  5  4  6      22 20 21 23
2  1  0  3      19 16 17 18

'''

triangle_assignments_v1 = [15, 14, 12, 11, 13, 10, 9, 8, 7, 6, 5, 2, 4, 3, 1, 0]
'''
Assignments list maps channel to geometrical position in triangle grid
(v1.0 and v1.1).

I.e. assignments[2] gives the location of channel 2, where the locations
are numbered according to the following grid::

   0           1
2     3     4     5
   6           7
   8           9
10    11    12    13
   14          15

Channel assignments (right-side up) (from the chip side)::

   15          14              31          30
11    13    12    10        27    29    28    26
   9           8       or      25          24
   7           6               23          22
5     3     2     4         21    19    18    20
   1           0               17          16

'''

grid_4x4_assignments_0_16_v2_2 = [3, 7, 2, 1, 0, 4, 6, 5, 10, 9, 11, 8, 14, 13, 15, 12]
grid_4x4_assignments_16_31_v2_2 = [3, 1, 2, 0, 7, 4, 6, 5, 10, 9, 8, 12, 13, 14, 11, 15]
grid_4x4_assignments_31_46_v2_2 = [12, 8, 13, 14, 15, 11, 10, 9, 5, 6, 4, 7, 0, 2, 1, 3]
grid_4x4_assignments_46_64_v2_2 = [12, 15, 13, 14, 8, 11, 9, 10, 5, 6, 7, 3, 2, 1, 4, 0]
'''
Assignments list maps channel to geometrical position in 4x4 grid (v2.2).

I.e. assignments[2] gives the location of channel 2, where the locations
are numbered according to the following grid::

0  1  2  3
4  5  6  7
8  9  10 11
12 13 14 15

'''

grid_7x7_assignments_0_64_v2_2_1 = [
    # 10,     2,      1,  0,  9,  8,  None,   None,
    38,     44,     43, 42, 37, 36,  None,   None,
    # None,   None,   7,  16, 15, 14, 24,     23,
    None,   None,   35, 30, 29, 28, 24,     23,
    # 22,     21,     30, 35, 28, 29, None,   None,
    22,     21,     16, 7, 14, 15, None,   None,
    # None,   None,   36, 37, 42, 43, 44,     45,
    None,   None,   8, 9, 0, 1, 2,     3,
    # 38,     46,     47, 48, 39, 40, None,   None,
    10,     4,     5, 6, 11, 12, None,   None,
    # None,   41,     31, 32, 33, 34, 25,     26,
    None,   13,     17, 18, 19, 20, 25,     26,
    # 27,     17,     18, 19, 13, 20, None,   None,
    27,     31,     32, 33, 41, 34, None,   None,
    # None,   None,   12, 11, 6,  5,  4,      3,
    None,   None,   40, 39, 48,  47,  46,      45,
    ]
'''

Assignments list maps channel to geometrical position in 7x7 grid.

I.e. assigments[2] gives the location of channel 2, where the locations
are numbered according to the following grid::

0   1   2   3   4   5   6
7   8   9   10  11  12  13
14  15  16  17  18  19  20
21  22  23  24  25  26  27
28  29  30  31  32  33  34
35  36  37  38  39  40  41
42  43  44  45  46  47  48

42  43  44  45  46  47  48
35  36  37  38  39  40  41
28  29  30  31  32  33  34
21  22  23  24  25  26  27
14  15  16  17  18  19  20
7   8   9   10  11  12  13
0   1   2   3   4   5   6
'''

grid_8x8_assignments_0_64_v2_0 = [
        60, 52, 61, 62, 63, 55, 53, 54,
        45, 46, 44, 47, 37, 38, 36, 39,
        28, 30, 29, 31, 20, 23, 21, 22,
        13, 14, 15,  7,  6,  5, 12,  4,
         3, 11,  2,  1,  0,  8,  9, 10,
        18, 17, 19, 16, 27, 25, 26, 24,
        35, 32, 34, 33, 43, 40, 42, 41,
        50, 49, 48, 56, 57, 58, 51, 59]

grid_8x8_assignments_0_64_v2_1 = [
	59, 51, 58, 57, 56, 48, 50, 49,
	42, 41, 43, 40, 34, 33, 35, 32,
	27, 25, 26, 24, 19, 16, 18, 17,
	10,  9,  8,  0,  1,  2, 11,  3,
	4,  12,  5,  6,  7, 15, 14, 13,
	21, 22, 20, 23, 28, 30, 29, 31,
	36, 39, 37, 38, 44, 47, 45, 46,
	53, 54, 55, 63, 62, 61, 52, 60]


'''
Assignments list maps channel to geometrical position in 8x8 grid.

I.e. assigments[2] gives the location of channel 2
'''


def assign_pixels(pixelids, assignments, right_side_up=True, channel_ids=None):
    '''
    Return a list assigning the given pixelids to a chip's channels
    using the provided assignment array.

    The right-side up configuration (``right_side_up=True``) of the chip
    is (viewed from above), channels 0-15 on the left and channels 16-31
    on the right. The upside-down configuration is reversed: channels
    16-31 on the left, and channels 0-15 on the right.

    Since the convention is that channel id is given by position in the
    list, this function always returns assignments to channels 0-15.
    If the given grid should be assigned to channels 16-31, just
    concatenate it with the assignments for 0-15.

    ``assignments[i]`` gives the index in the final list of
    ``pixelids[i]``.

    '''
    if not right_side_up:
        assignments = assignments[::-1]
    channel_connections = []
    if channel_ids is None:
        channel_ids = pixelids
    for channel_id, pixelid in enumerate(channel_ids):
        if assignments[channel_id] is None:
            channel_connections.append(None)
        else:
            channel_connections.append(pixelids[assignments[channel_id]])
    return channel_connections
