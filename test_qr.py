# -*- coding: utf-8 -*-
"""
二维码编码器端到端验证

思路
  比对我的实现与参考库的模块矩阵意义有限，因为两者可能选择不同的掩码，
  两种都合法。真正有意义的判据是：把二维码画成图片后，能不能被解码器读出来，
  并且读出的内容与输入完全一致。

  这里用 numpy 生成像素图，用 OpenCV 的 QRCodeDetector 解码。
  解码器是独立实现，不共享我编码器的任何代码，因此这是有效的交叉验证。
"""
import sys
import os
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

spec = importlib.util.spec_from_file_location("bili", os.path.join(HERE, "bili_dl.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS = 0
FAIL = 0
FAILED = []


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [通过] %s" % label)
    else:
        FAIL += 1
        FAILED.append(label + ("  " + detail if detail else ""))
        print("  [失败] %s%s" % (label, ("   " + detail) if detail else ""))


def render_png_data(matrix, scale=6, quiet=4):
    """
    把模块矩阵转成灰度图数组，深色为 0，浅色为 255。

    渲染参数说明。scale 与 quiet 会影响 OpenCV 检测器能否识别，
    实测在 scale 过大而 quiet 固定时，连参考库生成的矩阵也会识别失败，
    那是检测器的限制而非编码错误。此处取已验证稳定的参数。
    """
    import numpy as np
    n = len(matrix)
    size = (n + quiet * 2) * scale
    img = np.full((size, size), 255, dtype=np.uint8)
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                y0 = (r + quiet) * scale
                x0 = (c + quiet) * scale
                img[y0:y0 + scale, x0:x0 + scale] = 0
    return img


def decode(img):
    """用 OpenCV 解码，返回 (内容, 是否成功)"""
    import cv2
    det = cv2.QRCodeDetector()
    ok, decoded, points, _ = det.detectAndDecodeMulti(img)
    if ok and decoded:
        return decoded[0], True
    single, _pts, _s = det.detectAndDecode(img)
    if single:
        return single, True
    return "", False


def main():
    print("=" * 72)
    print("二维码编码器端到端验证   画图后交给独立解码器读取")
    print("=" * 72)

    try:
        import numpy  # noqa
        import cv2
    except ImportError as e:
        print("缺少验证所需库：%s" % e)
        print("请执行： python -m pip install opencv-python-headless numpy")
        return 2

    print("OpenCV %s   NumPy %s" % (cv2.__version__, numpy.__version__))
    print()

    payloads = [
        ("短链接", "https://www.bilibili.com"),
        ("B站登录二维码实际载荷",
         "https://account.bilibili.com/h5/account-h5/auth/scan-web"
         "?navhide=1&callback=close&qrcode_key=" + "a1b2c3d4e5f60718293a4b5c6d7e8f90"),
        ("纯数字", "0123456789" * 4),
        ("含中文", "哔哩哔哩视频下载测试"),
        ("长随机", "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
                   "?qrcode_key=0123456789abcdef0123456789abcdef"),
        ("边界长度 100", "Z" * 100),
    ]

    for label, payload in payloads:
        for ecc in ("L", "M"):
            try:
                qr = m.QRCode(payload, ecc=ecc)
                mat = qr.build()
                img = render_png_data(mat)
                got, okk = decode(img)
                same = (got == payload)
                check(okk and same,
                      "%s  ecc=%s  版本%d  %dx%d" % (label, ecc, qr.version,
                                                     len(mat), len(mat)),
                      "解码结果不一致" if okk and not same else
                      ("解码失败" if not okk else ""))
            except Exception as e:
                check(False, "%s  ecc=%s" % (label, ecc), "异常 %s" % e)

    print()
    print("不同纠错级别与不同长度，全部以能否被独立解码器读出为准")
    print()
    print("=" * 72)
    print("结果   通过 %d   失败 %d" % (PASS, FAIL))
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
