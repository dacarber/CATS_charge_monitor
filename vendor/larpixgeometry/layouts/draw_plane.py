'''
Generate a PDF of a pixel plane YAML file.

'''
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch, cm
import yaml
from larpixgeometry.pixelplane import PixelPlane
from larpixgeometry.layouts import load
import  numpy as np
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('layoutversion')
parser.add_argument('--pixelside', action='store_true')
args = parser.parse_args()
version = args.layoutversion
pixelside = args.pixelside
if pixelside:
    sidename = 'pixel'
else:
    sidename = 'chip'

pixelplane = PixelPlane.fromDict(load('layout-' + version + '.yaml'))
dimensions = pixelplane.dimensions
x_orig = dimensions['x']
y_orig = dimensions['y']
width_orig = dimensions['width']
height_orig = dimensions['height']

colors = np.array([[228, 26, 28], [55, 126, 184], [77, 175, 74], [152,
    78, 163], [255, 127, 0],[0, 255, 229]])/256.0
colors = np.tile(colors, (100,1))
print('colors',len(colors))

makelandscape = False
if version.startswith('3.'):
    makelandscape = True;

if makelandscape:
    canvas_width, canvas_height = landscape(letter)
else:
    canvas_width, canvas_height = letter

#c = canvas.Canvas('layout-' + version + '-' + sidename + 'side.pdf', pagesize=letter)
if makelandscape:
    c = canvas.Canvas('layout-' + version + '-' + sidename + 'side.pdf', pagesize=landscape(letter))
else:
    c = canvas.Canvas('layout-' + version + '-' + sidename + 'side.pdf', pagesize=letter)


margin = 1*inch
page_center_x = canvas_width/2
page_center_y = canvas_height/2

remaining_width = canvas_width - 2*margin
remaining_height = canvas_height - 2*margin
width_scalefactor = remaining_width/width_orig
height_scalefactor = remaining_height/height_orig
scalefactor = min(width_scalefactor, height_scalefactor)
x_scaled = x_orig * scalefactor
y_scaled = y_orig * scalefactor
width_final = width_orig * scalefactor
height_final = height_orig * scalefactor
center_scaled_x = x_scaled + width_final/2
center_scaled_y = y_scaled + height_final/2
translation_x = page_center_x - center_scaled_x
translation_y = page_center_y - center_scaled_y
x_final = x_scaled + translation_x
y_final = y_scaled + translation_y
c.rect(x_final, y_final, width_final, height_final, fill=0, stroke=1)

def transform_x(xcoord):
    result = xcoord * scalefactor + translation_x
    if pixelside:
        result = canvas_width - result
    return result

def transform_y(ycoord):
    return (ycoord * scalefactor + translation_y)

minor_font = 3
major_font = 20

c.setFont('Helvetica', major_font)
c.drawString(canvas_width/3., canvas_height*0.92, 'Layout %s (%d chips)' % (version,
    len(pixelplane.chips)))
c.drawString(canvas_width/3., canvas_height*0.87, '(view from %s side)' % sidename)
c.setFont('Courier', minor_font)
colorkey = []
for pixel in pixelplane.pixels.values():
    c.circle(transform_x(pixel.x), transform_y(pixel.y), 0.4)
    c.drawCentredString(transform_x(pixel.x), transform_y(pixel.y),
            str(pixel.pixelid))
c.circle(transform_x(0), transform_y(0), 1.0)
p = c.beginPath()
p.moveTo(transform_x(0),transform_y(0)); p.lineTo(transform_x(0),transform_y(height_orig/100))
c.drawPath(p)
c.drawCentredString(transform_x(0), transform_y(1.5*height_orig/100), 'Y')
p = c.beginPath()
p.moveTo(transform_x(0),transform_y(0)); p.lineTo(transform_x(width_orig/100),transform_y(0))
c.drawPath(p)
c.drawCentredString(transform_x(1.5*height_orig/100), transform_y(0), 'X')
for chip, color in zip(pixelplane.chips.values(), colors):
    c.setFont('Courier-Bold', minor_font)
    c.setFillColorRGB(*color, alpha=1)
    colorkey.append(['Chip %d' % chip.chipid, color])
    x_sum = 0
    y_sum = 0
    count = 0
    for channel, pixel in enumerate(chip.channel_connections):
        if pixel != pixelplane.unconnected_pixel:
            c.drawCentredString(transform_x(pixel.x),
                    transform_y(pixel.y)-minor_font,
                    str(channel))
            x_sum += transform_x(pixel.x)
            y_sum += transform_y(pixel.y) - 5
            count += 1
    x_avg = x_sum/float(count)
    y_avg = y_sum/float(count)
    font = ('Helvetica', major_font)
    c.setFont(*font)
    c.setFillColorRGB(*color, alpha=0.45)
    width = c.stringWidth(str(chip.chipid), *font)
    c.drawCentredString(x_avg, y_avg-major_font/2, str(chip.chipid))
c.showPage()
c.save()
