import json
io_groups = [1, 2]
tiles_per_io = [i for i in range(8)]
chip_ids = [i for i in range(11, 112)]

names = []

for io_group in io_groups:
	for tile in tiles_per_io:
		net = {'network' : {}}
		net['network'][io_group] = {}
		io_channels = [tile*4 + i + 1 for i in range(4)]
		print(tile, io_channels)
		for io_channel in io_channels:
			net['network'][io_group][io_channel] = {}
			nodes = [ {"chip_id" : 'ext' }]
			for chip in chip_ids:
				nodes.append({'chip_id': chip})
			net['network'][io_group][io_channel]['nodes']=nodes


		jsonString = json.dumps(net, indent=4)
		names.append('io-{}-tile-{}-agnostic-hydra.json'.format(io_group, tile+1))
		jsonFile = open('io-{}-tile-{}-agnostic-hydra.json'.format(io_group, tile+1), "w")
		jsonFile.write(jsonString)
		jsonFile.close()

with open('agnostic-hydra.txt', 'w') as f:
	for name in names:
		f.write(name+'\n')






