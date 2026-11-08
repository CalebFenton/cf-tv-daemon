# Reading captured frames

Updated 2026-10-24. Frames come from `bin/snap` and `bin/stream`
(`docs/02-capture/screen-capture.md`) as 720×404 JPEGs. The size is fixed.

## Read frames by eye, not with OCR

Tesseract 5.3.4 returned noise on these frames in every page-segmentation
mode tried (psm 3: nothing; psm 6 and 11: garbage strings). Cropped regions
upscaled with Otsu thresholding did no better (2026-10-22). Reading the same
frame by eye recovered subtitle text, a poster headline and a sign. Do not
build an OCR pipeline on 720×404 frames. Tesseract was a one-off local
install from `.deb` files in host `/tmp/tess` and is not part of the repo.

## What worked (2026-10-22, not re-run since)

These were one-off scripts and are not in the repo. `.venv` has no OpenCV.

- **Face detection:** OpenCV 4.10 `haarcascade_frontalface_default` +
  `profileface` found both people in a real frame. OpenCV 5 reportedly drops
  the `CascadeClassifier` Python binding (unverified), so pin 4.x.
- **Colour clusters:** k-means over a downscaled frame gives a usable palette.
- **Edge energy:** Sobel, block-averaged on a 16 px grid, gives a cheap
  detail-density map for picking regions.
- **JPEG dimensions without an image library:** parse the SOF marker. Code:
  `JpegSize` in `apps/capture-probe.cs`, `image_size` in `tv/cftv.py`.

## Scope

Tracks are numbered (`TRK-01`, `P-01`), never named. Detecting faces is in
scope; attaching identities to them is not.
