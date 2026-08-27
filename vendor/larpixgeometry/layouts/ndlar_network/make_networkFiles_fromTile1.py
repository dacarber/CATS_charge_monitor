import json
import fire
import os

def make_ndlar_network_files(tile1_network_file):

    network_path_exists = os.path.isdir('ndlar_network_configs')
    if not network_path_exists:
        os.makedirs('ndlar_network_configs')

    for tile in range(1,41):

        # get the base network configuration
        with open(tile1_network_file, 'r') as t1nc:
            t1nc_json = json.load(t1nc)

        # copy the base configuration for this tile
        ttnc_json = t1nc_json

        # change io_group
        io_group = (tile-1)//10+1
        ttnc_json['network'][str(io_group)] = ttnc_json['network'].pop('1')

        # change the io_channel
        for base_io_chan in list(t1nc_json['network'][str(io_group)].keys()):
            io_channel = ((tile-1)%10)*4 + int(base_io_chan)
            ttnc_json['network'][str(io_group)][str(io_channel)] = ttnc_json['network'][str(io_group)].pop(base_io_chan)

        # write the new network configuration file
        filename = "ndlar_network_configs/network_ndlarmodule_tile"+str(tile)+".json"
        with open(filename, 'w') as outfile:
            json.dump(ttnc_json, outfile, ensure_ascii=False, indent=4)

    #print(t1nc_json)

if __name__ == "__main__":
    fire.Fire(make_ndlar_network_files)

