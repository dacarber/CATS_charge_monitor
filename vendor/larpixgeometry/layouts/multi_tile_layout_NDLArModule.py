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
"""

import json
import fire
import yaml
import larpixgeometry.pixelplane

LAYOUT_VERSION = '3.0.0'
FORMAT_VERSION = '3.0.40'
PIXEL_PITCH = 3.8

def generate_layout(tile_layout_file, network_config_file, n_tiles, pixel_pitch=PIXEL_PITCH):
    """
    Function that generates the multi-layout YAML file.

    Args:
        tile_layout_file (str): YAML file containing the tile layout
        network_config_file (str): JSON file containing the network configuration
            or txt file with a list of JSON files (one per tile)
        n_tiles (int): number of tiles
        pixel_pitch (float): value of pixel pitch, default is PIXEL_PITCH
    """

    with open(tile_layout_file, 'r') as pf:
        board = larpixgeometry.pixelplane.PixelPlane.fromDict(yaml.load(pf, Loader=yaml.FullLoader))

    with open(network_config_file, 'r') as nc:
        if '.txt' in network_config_file:
            network_configs = nc.read().splitlines()
        elif '.json' in network_config_file:
            network_configs = [network_config_file]*n_tiles
        else:
            raise ValueError("Network configuration file must have txt or json extension")

    chipids = list(board.chips.keys())
    print("chipids",chipids)
    io_channels_tile = {}
    io_group_tile = {}

    #print("network configs: ",network_configs)

    for it,network_config in enumerate(network_configs):

        with open(network_config, 'r') as nc:
            nc_json = json.load(nc)

        io_group = list(nc_json['network'].keys())[-1]
        print("tile: ",it," -- io_group = ",io_group)
        io_channels = nc_json['network'][io_group]
        io_channels_tile[it+1] = {}
        io_group_tile[it+1] = int(io_group)

        for io_channel in io_channels:
            nodes = io_channels[io_channel]['nodes']
            for node in nodes:
                chip_id = node['chip_id']
                if isinstance(chip_id, int):
                    if int(io_channel) in io_channels_tile[it+1]:
                        io_channels_tile[it+1][int(io_channel)].append(chip_id)
                    else:
                        io_channels_tile[it+1][int(io_channel)] = [chip_id]
    #print("io_channels_tile: ",io_channels_tile)
    #print("io_group_tile: ",io_group_tile)
    ## These positions comes from the GDML file. Numbers in mm
    ## The anode is on the yz plane with the pixels oriented
    ## towards the positive x axis

    ## tile_indices [amodule, anode, tileID]
    ##
    ## tileIDs:
    ##  ---------
    ##  | 2 | 1 |
    ##  ---------
    ##  | 4 | 3 |
    ##  ---------
    ##  | 6 | 5 |
    ##  ---------
    ##  | 8 | 7 |
    ##  ---------
    ##  |10 | 9 |
    ##  ---------
    ##  |12 | 11|
    ##  ---------
    ##  |14 | 13|
    ##  ---------
    ##  |16 | 15|
    ##  ---------
    ##  |18 | 17|
    ##  ---------
    ##  |20 | 19|
    ##  --------

    tile_indeces = dict()
    tile_positions = dict()
    tile_orientations = dict() # (z, y, x)

    for tpc in range(1,3):
        for tileNum in range(1,21):
            tile_indeces[20*(tpc-1)+tileNum] = [1,tpc,tileNum]
            if tpc == 1:
                x = -50.4
                #x = 0.
                z_ori = 1
            elif tpc == 2:
                x = 50.4
                #x = 0.
                z_ori = -1
            if tileNum%2==1:
                #z = 437.16
                z = -24.32
                x_ori = 1
                y_ori = 1
                #z_ori = 1
            else:
                #z = 487.56
                z = 24.32
                x_ori = 1
                y_ori = 1
                #z_ori = -1
            y_it = (tileNum-1)//2
            #y = 155.387-15.2*(y_it*2+1)
            y = 150.-15.2*(y_it*2+1)
            #print(20*(tpc-1)+tileNum,": ",x,y,z)
            tile_positions[20*(tpc-1)+tileNum] = [x*10.,y*10.,z*10.]
            tile_orientations[20*(tpc-1)+tileNum] = [z_ori,y_ori,x_ori]

    tile_chip_io_channel_io_group = {it:{} for it in range(1,n_tiles+1)}

    for it in tile_chip_io_channel_io_group:
        io_channels = io_channels_tile[it]
        #print("it: ",it,", io_channels_tile[it]: ",io_channels_tile[it])
        for io_channel in io_channels:
            for chip in io_channels[io_channel]:
                tile_chip_io_channel_io_group[it][chip] = io_group_tile[it]*1000 + io_channel

    chip_channel = {}

    xs = []
    ys = []
    for chip in chipids:
        for channel, pixel in enumerate(board.chips[chip].channel_connections):
            if pixel.x !=0 and pixel.y != 0:
                xs.append(pixel.x)
                ys.append(pixel.y)

    for chip in chipids:
        for channel, pixel in enumerate(board.chips[chip].channel_connections):
            if pixel.x !=0 and pixel.y != 0:
                key = chip*1000+channel
                chip_channel[key] = [round((pixel.x - min(xs))/pixel_pitch),
                                     round((pixel.y - min(ys))/pixel_pitch)]


    print("tile_chip_io_channel_io_group: ",tile_chip_io_channel_io_group)

    with open('multi_tile_layout-%s.yaml' % FORMAT_VERSION, 'w') as f:
        yaml.dump({'tile_layout_version': LAYOUT_VERSION,
                   'multitile_layout_version': FORMAT_VERSION,
                   'pixel_pitch': pixel_pitch,
                   'tile_positions': tile_positions,
                   'tile_orientations': tile_orientations,
                   'tile_chip_to_io': tile_chip_io_channel_io_group,
                   'tile_indeces': tile_indeces,
                   'chip_channel_to_position': chip_channel}, f)

if __name__ == "__main__":
    fire.Fire(generate_layout)
