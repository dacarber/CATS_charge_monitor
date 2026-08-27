"""
This script produces a YAML file containing one variable and three dictionaries,
describing the physical structures (e.g. pixel pads and anode tiles) and
software/electrical associations (e.g. IO group, IO channel, chip ID) for
a multi-tile LArPix anode

- tile_layout_version: version of the LArPix single-tile layout
- multitile_layout_version: version of the multi-tile layout in the X.Y.Z. format
    where X represents the ASIC version, Y represents the single-tile version
    (incremented from 0), and Z represents the number of tiles in the layout.
- pixel_pitch: pixel pitch value in mm
- tile_positions: dictionary where the key is tile ID of type integer
    and the value is the position vector of the tile center
- tile_orientations: dictionary where the key is tile ID of type integer
    and the value is the direction vector of the tile, with respect to
    the reference frame in larpix-geometry
- tile_chip_to_io: nested dictionary where the first key is tile ID
    and the second key is chip ID and the value is (IO channel, IO group).
    Tile ID, chip ID, IO channel, and IO group are all type integers
- chip_channel_to_position: dictionary where the key is (channel ID, chip ID)
    and the value is (x-position, y-position), stored as multiples of the pixel pitch
    of type integer
- tile_indeces: dictionary where the key is the tile ID and the value is a tuple in
    the format (module ID, anode ID, tile ID within the anode)
- tpc_centers: a dictionary of the offset to apply to each tpc
"""

import json
import fire
import yaml
import larpixgeometry.pixelplane

LAYOUT_VERSION = '2.5.1'
FORMAT_VERSION = '2.5.16'
#PIXEL_PITCH = 4.434
PIXEL_PITCH = 3.8

USE_SINGLE_NETWORK_FILE = False

def generate_layout(tile_layout_file, network_config_file, n_tiles=1, pixel_pitch=PIXEL_PITCH, single_network_file=USE_SINGLE_NETWORK_FILE):
    """
    Function that generates the multi-layout YAML file.

    Args:
        tile_layout_file (str): YAML file containing the tile layout
        network_config_file (str): JSON file containing the network configuration
            or txt file with a list of JSON files (one per tile)
        n_tiles (int): number of tiles
        pixel_pitch (float): value of pixel pitch, default is PIXEL_PITCH
        single_network_file (bool): flag to use a single network file for the global configuration (default=False), to use, you also need to specify the number of tiles
    """

    with open(tile_layout_file, 'r', encoding='utf-8') as pf_file:
        board = larpixgeometry.pixelplane.PixelPlane.fromDict(yaml.load(pf_file, Loader=yaml.FullLoader))

    with open(network_config_file, 'r', encoding='utf-8') as nc_file:
        if '.txt' in network_config_file:
            network_configs = nc_file.read().splitlines()
            n_tiles = len(network_configs)
        elif '.json' in network_config_file:
            network_configs = [network_config_file]*n_tiles
        else:
            raise ValueError("Network configuration file must have txt or json extension")

    chipids = list(board.chips.keys())

    io_channels_tile = {}
    io_group_tile = {}

    for it, network_config in enumerate(network_configs):

        with open(network_config, 'r', encoding='utf-8') as nc_file:
            nc_json = json.load(nc_file)

        io_group = list(nc_json['network'].keys())[0]
        io_channels = nc_json['network'][io_group]
        io_channels_tile[it+1] = {}
        io_group_tile[it+1] = int(io_group)

        for io_channel in io_channels:
            # P. Madigan 11/17 - Hack to allow for specifying geometry from a single network file
            # assumes that io channel 1,2,3,4 -> tile number 1; io channel 5,6,7,8 -> tile number 2, ...
            if single_network_file != False:
                n_tiles_per_io_group = n_tiles // (len(nc_json['network'])-3)
                io_group_tile[it+1] = int(it // n_tiles_per_io_group + 1)
                if (int(io_channel)-1) // 4 + (io_group_tile[it+1]-1) * n_tiles_per_io_group != it:
                    continue
            
            nodes = io_channels[io_channel]['nodes']
            for node in nodes:
                chip_id = node['chip_id']
                if isinstance(chip_id, int):
                    if int(io_channel) in io_channels_tile[it+1]:
                        io_channels_tile[it+1][int(io_channel)].append(chip_id)
                    else:
                        io_channels_tile[it+1][int(io_channel)] = [chip_id]

    ## These positions comes from the GDML file.
    ## The numbers are in mm and were provided by Patrick Koller.
    ## The anode is on the yz plane with the pixels oriented
    ## towards the positive x axis
    tile_indeces = {1:  [1,1],
                    2:  [1,2],
                    3:  [1,3],
                    4:  [1,4],
                    5:  [1,5],
                    6:  [1,6],
                    7:  [1,7],
                    8:  [1,8],
                    9:  [2,1],
                    10: [2,2],
                    11: [2,3],
                    12: [2,4],
                    13: [2,5],
                    14: [2,6],
                    15: [2,7],
                    16: [2,8]}

    tile_positions = {1:  [-304.31, 465.57,-155.19],
                      2:  [-304.31, 465.57, 155.19],
                      3:  [-304.31, 155.19,-155.19],
                      4:  [-304.31, 155.19, 155.19],
                      5:  [-304.31,-155.19,-155.19],
                      6:  [-304.31,-155.19, 155.19],
                      7:  [-304.31,-465.57,-155.19],
                      8:  [-304.31,-465.57, 155.19],
                      9:  [ 304.31, 465.57, 155.19],
                      10: [ 304.31, 465.57, -155.19],
                      11: [ 304.31, 155.19, 155.19],
                      12: [ 304.31, 155.19, -155.19],
                      13: [ 304.31,-155.19, 155.19],
                      14: [ 304.31,-155.19, -155.19],
                      15: [ 304.31,-465.57, 155.19],
                      16: [ 304.31,-465.57, -155.19]}

                             # z  y  x
    tile_orientations = {1:  [ 1,-1, 1],
                         2:  [ 1, 1,-1],
                         3:  [ 1,-1, 1],
                         4:  [ 1, 1,-1],
                         5:  [ 1,-1, 1],
                         6:  [ 1, 1,-1],
                         7:  [ 1,-1, 1],
                         8:  [ 1, 1,-1],
                         9:  [-1,-1,-1],
                         10: [-1, 1, 1],
                         11: [-1,-1,-1],
                         12: [-1, 1, 1],
                         13: [-1,-1,-1],
                         14: [-1, 1, 1],
                         15: [-1,-1,-1],
                         16: [-1, 1, 1]}

    tpc_centers = {1: [0,0,0], 2: [0,0,0]}

    tile_chip_io_channel_io_group = {it:{} for it in range(1,n_tiles+1)}

    for it in tile_chip_io_channel_io_group:
        io_channels = io_channels_tile[it]
        for io_channel in io_channels:
            for chip in io_channels[io_channel]:
                tile_chip_io_channel_io_group[it][chip] = io_group_tile[it]*1000 + io_channel

    chip_channel = {}

    xs = []
    ys = []
    for chip in chipids:
        for channel, pixel in enumerate(board.chips[chip].channel_connections):
            if pixel.x != 0 and pixel.y != 0:
                xs.append(pixel.x)
                ys.append(pixel.y)

    for chip in chipids:
        for channel, pixel in enumerate(board.chips[chip].channel_connections):
            if pixel.x != 0 and pixel.y != 0:
                key = chip*1000+channel
                chip_channel[key] = [round((pixel.x - min(xs))/pixel_pitch),
                                     round((pixel.y - min(ys))/pixel_pitch)]

    with open(f'multi_tile_layout-{FORMAT_VERSION}.yaml', 'w', encoding='utf-8') as multi_tile_file:
        yaml.dump({'tile_layout_version': LAYOUT_VERSION,
                   'multitile_layout_version': FORMAT_VERSION,
                   'pixel_pitch': pixel_pitch,
                   'tile_positions': tile_positions,
                   'tile_orientations': tile_orientations,
                   'tile_chip_to_io': tile_chip_io_channel_io_group,
                   'tile_indeces': tile_indeces,
                   'tpc_centers': tpc_centers,
                   'chip_channel_to_position': chip_channel}, multi_tile_file)

if __name__ == "__main__":
    fire.Fire(generate_layout)
