# mqtt commands
## update qr code
- topic: `epaper/cmnd/update-qr`
- payload: bmp formatted image data containing the qr code

## add drawing
- topic: `epaper/cmnd/image/add/<n>`
- parameters:
    - `n`: numerals representing a unique id for the image
- payload: bmp formatted image data containing the drawing

## remove drawing
- topic: `epaper/cmnd/image/remove`
- payload: ASCII numerals of the ID number of the image to remove

## blank screen now
- topic: `epaper/cmnd/blank`
- payload: `true`

## unblank screen
- topic: `epaper/cmnd/blank`
- payload: `false`