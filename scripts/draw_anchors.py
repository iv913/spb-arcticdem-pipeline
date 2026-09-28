#!/usr/bin/env python3
"""Рисует перекрестия-якоря на плане для визуальной проверки.

usage: draw_anchors.py <img> <out> x1,y1 x2,y2 ...
"""
import sys

import cv2

img, out, *anchors = sys.argv[1:]
im = cv2.imread(img)
H, W = im.shape[:2]
for a in anchors:
    x, y = map(int, a.split(","))
    cv2.line(im, (x - 40, y), (x + 40, y), (0, 0, 255), 2)
    cv2.line(im, (x, y - 40), (x, y + 40), (0, 0, 255), 2)
    cv2.putText(im, f"{x},{y}", (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 255), 2)
cv2.imwrite(out, im)
print("saved", out)
