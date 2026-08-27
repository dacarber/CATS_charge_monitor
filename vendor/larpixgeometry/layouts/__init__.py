'''
Layouts specify the mapping from pixel locations to (chipID+channel).

Layouts are numbered as X.Y.Z, where:

- X refers to the LArPix ASIC version number supported by the physical
  PCB
- Y refers to the iteration of PCB layout (e.g. a prototype might be
  Y=0, then a small tile would be Y=1, and then a full tile would be
  Y=2)
- Z refers to the specific ASIC loading and configuration on a
  particular PCB (e.g. if it is only partially loaded).

'''
import yaml
import os

def load(filename):
    '''
    Load the specified layout file.

    The path is first searched relative to the "current directory".
    If no match is found, the path is searched relative to the "config"
    package directory (aka ``__file__`` directly in code).

    '''
    if os.path.isfile(filename):
        with open(filename, 'r') as f:
            return yaml.load(f, Loader=yaml.SafeLoader)
    elif os.path.isfile(os.path.join(os.path.dirname(__file__), filename)):
        with open(os.path.join(os.path.dirname(__file__), filename), 'r') as f:
            return yaml.load(f, Loader=yaml.SafeLoader)
    else:
        raise IOError('File not found: %s' % filename)
