# -*- coding:utf-8 -*-

from PIL import Image, ImageDraw, ImageFont
import math

def text(text, epd, font):
    image = Image.new('1', (epd.height, epd.width), 255)
    draw = ImageDraw.Draw(image)
    draw.text((0, 0), text, font=font, fill=0)
    upside_down(image, epd)

def upside_down(image, epd):
    image = image.rotate(180)
    epd.display(epd.getbuffer(image))

def scale_image_letterboxed(image, new_width, new_height):
    # first compute the scale factor based on the height
    scaleFactor = new_height / image.height

    # if the height-based scale factor would result in a width that is still too
    # large, update the scale factor to be based on the width
    if math.trunc(image.width * scaleFactor) > new_width:
        scaleFactor = new_width / image.width

    image = image.resize((
        math.trunc(image.width * scaleFactor),
        math.trunc(image.height * scaleFactor)
    ))

    # if we ended up with an image the exact desired dimensions, just return it.
    # otherwise, we need to paste the scaled image into the center of a new
    # blank image
    if image.width == new_width and image.height == new_height:
        return image

    newImage = Image.new('1', (new_width, new_height), 255)

    newImage.paste(image, (
        math.trunc((new_width / 2) - (image.width / 2)),
        math.trunc((new_height / 2) - (image.height / 2))
    ))

    return newImage

# scale, rotate, and put put the image on the screen
def image_full(image, epd):
    if image.height != 122 or image.width != 250:
            image = scale_image_letterboxed(image, 250, 122)
    epd.init()
    upside_down(image, epd)
    epd.sleep()

def image_from_bytes(bytes, epd):
    image_full(Image.open(io.BytesIO(bytes)), epd)