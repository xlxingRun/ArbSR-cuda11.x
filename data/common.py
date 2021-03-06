import random
import numpy as np
import skimage.color as sc
import torch


def get_patch(*args, patch_size=96, scale_1=1, scale_2=1):
    ih, iw = args[0].shape[:2]  # LR image

    tp = int(round(scale_1 * patch_size))
    tp2 = int(round(scale_2 * patch_size))
    ip = patch_size

    if scale_1 == int(scale_1):
        step = 1
    elif (scale_1 * 2) == int(scale_1 * 2):
        step = 2
    elif (scale_1 * 5) == int(scale_1 * 5):
        step = 5
    else:
        step = 10
    if scale_2 == int(scale_2):
        step2 = 1
    elif (scale_2 * 2) == int(scale_2 * 2):
        step2 = 2
    elif (scale_2 * 5) == int(scale_2 * 5):
        step2 = 5
    else:
        step2 = 10

    iy = random.randrange(2, (ih - ip) // step - 2) * step
    ix = random.randrange(2, (iw - ip) // step2 - 2) * step2

    tx, ty = int(round(scale_2 * ix)), int(round(scale_1 * iy))

    ret = [
        args[0][iy:iy + ip, ix:ix + ip, :],
        *[a[ty:ty + tp, tx:tx + tp2, :] for a in args[1:]]
    ]

    return ret


def set_channel(*args, n_channels=3):
    def _set_channel(img):
        if img.ndim == 2:
            img = np.expand_dims(img, axis=2)

        c = img.shape[2]
        if n_channels == 1 and c == 3:
            img = np.expand_dims(sc.rgb2ycbcr(img)[:, :, 0], 2)
        elif n_channels == 3 and c == 1:
            img = np.concatenate([img] * n_channels, 2)

        return img

    return [_set_channel(a) for a in args]


def np2tensor(*args, rgb_range=255):
    def _np2tensor(img):
        np_transpose = np.ascontiguousarray(img.transpose((2, 0, 1)))
        tensor = torch.from_numpy(np_transpose).float()
        tensor.mul_(rgb_range / 255)

        return tensor

    return [_np2tensor(a) for a in args]


def augment(*args, hflip=True, rot=True):
    hflip = hflip and random.random() < 0.5
    vflip = rot and random.random() < 0.5
    rot90 = rot and random.random() < 0.5

    def _augment(img, rot=True):
        if hflip:
            img = img[:, ::-1, :]
        if vflip: 
            img = img[::-1, :, :]
        if rot:
            if rot90:
                img = img.transpose(1, 0, 2)

        return img

    out = []

    if args[1].shape[0] == args[1].shape[1]:
        out.append(_augment(args[0]))
        out.append(_augment(args[1]))
    else:
        out.append(_augment(args[0], rot=False))
        out.append(_augment(args[1], rot=False))

    return out
