# -*- coding: utf-8 -*-
r"""
===============================================================================
 Fairy III 型  ·  B 站视频下载器   命令行版
===============================================================================

 用法
   python bili_dl.py                    交互模式，粘贴链接即可
   python bili_dl.py <链接>              直接下载
   python bili_dl.py <链接> -p 3         下载第 3 个分P
   python bili_dl.py --login             扫码登录
   python bili_dl.py --login-sms         手机号短信登录
   python bili_dl.py --whoami            查看当前登录状态
   python bili_dl.py --logout            退出登录

 依赖
   仅需 requests 一个库。二维码在终端内直接绘制，不需要任何额外依赖，
   也不需要安装 ffmpeg（默认走 B 站已合并的 MP4 流）。

===============================================================================
 使用须知   请先读完再用
===============================================================================

 1. 仅供个人学习、研究与备份你有权保存的内容使用。
 2. 请遵守哔哩哔哩用户协议与著作权法。不要用于传播、二次上传或任何商业用途。
 3. 本工具不下载付费番剧、会员专属内容，不绕过任何付费或权限校验。
 4. 请勿高频批量抓取，那会给对方服务器造成负担，也可能导致你的账号被限制。
 5. 下载他人作品后，版权仍归原作者所有。

 登录只是为了取到更高清晰度。登录凭证只保存在本机当前用户目录下的
 .bili_cookies.json，不会上传到任何地方。

===============================================================================
"""

import os
import re
import sys
import json
import time
import shutil
from urllib.parse import urlparse, parse_qs

try:
    import requests
except ImportError:
    print("缺少 requests 库。请先执行：  python -m pip install requests")
    sys.exit(1)


APP_NAME = "Fairy III 型 · B 站视频下载器"
APP_VER = "1.1"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

COOKIE_FILE = os.path.join(os.path.expanduser("~"), ".bili_cookies.json")
DEFAULT_OUTDIR = os.path.join(os.path.expanduser("~"), "Downloads", "BiliVideo")

# 用到的接口。全部是 B 站网页端自己在用的公开接口，没有逆向私有协议
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_QR_GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
API_QR_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
API_SMS_SEND = "https://passport.bilibili.com/x/passport-login/web/sms/send"
API_SMS_LOGIN = "https://passport.bilibili.com/x/passport-login/web/login/sms"

# qn 参数与画质的对应关系。接口返回的 quality 字段就是这里的键
QUALITY_NAME = {
    127: "8K 超高清", 126: "杜比视界", 125: "HDR 真彩",
    120: "4K 超清", 116: "1080P60", 112: "1080P 高码率",
    100: "智能修复", 80: "1080P 高清", 74: "720P60",
    64: "720P 高清", 32: "480P 清晰", 16: "360P 流畅", 6: "240P 极速",
}

# 扫码轮询接口返回的子状态码。外层 code 一直是 0，真正表示进度的是 data.code
#   86101 二维码已生成，还没人扫
#   86090 扫了，等手机上点确认
#   0     确认完毕，凭证在 data.url 里
#   86038 二维码超时作废
QR_POLL_OK = 0
QR_POLL_EXPIRED = 86038
QR_POLL_WAIT = 86101
QR_POLL_SCANNED = 86090


# ============================================================================
#  终端输出
# ============================================================================
class C:
    """
    ANSI 颜色。

    非 TTY 时全部退化成空串。原因是把输出重定向到文件时，
    这些转义序列会原样写进去，日志里全是乱码。
    """
    ON = sys.stdout.isatty()
    R = "\033[0m" if ON else ""
    B = "\033[1m" if ON else ""
    D = "\033[2m" if ON else ""
    RED = "\033[31m" if ON else ""
    GRN = "\033[32m" if ON else ""
    YEL = "\033[33m" if ON else ""
    BLU = "\033[36m" if ON else ""
    MAG = "\033[35m" if ON else ""


# 下面五个是输出函数。都带 flush，因为进度和二维码必须在终端里立刻出现，
# 不能攒在缓冲区里等着一起吐。用 pythonw 或重定向时这个差别很明显
def info(msg):
    """普通信息"""
    print("  " + msg, flush=True)


def ok(msg):
    """成功"""
    print(C.GRN + "  [完成] " + msg + C.R, flush=True)


def warn(msg):
    """警告，不致命但需要留意"""
    print(C.YEL + "  [注意] " + msg + C.R, flush=True)


def err(msg):
    """错误"""
    print(C.RED + "  [错误] " + msg + C.R, flush=True)


def step(msg):
    """当前正在做什么"""
    print(C.BLU + "  ▶ " + msg + C.R, flush=True)


def fmt_size(n):
    """
    把字节数变成人看的大小，保留两位小数。

    传入非数字时返回 "--" 而不是抛异常。调用点都在打印路径上，
    为了显示一个大小把整个下载搞崩不值当。
    """
    try:
        n = float(n)
    except Exception:
        return "--"
    for unit, div in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return "%.2f %s" % (n / div, unit)
    return "%d B" % n


def fmt_dur(sec):
    """
    秒数转成 分:秒 或 时:分:秒。

    不足一小时不显示小时位，视频列表里短分P 看着干净些。
    """
    try:
        sec = int(sec)
    except Exception:
        return "--"
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%d:%02d" % (m, s))


def sanitize(name, limit=100):
    """
    把视频标题清理成能落盘的文件名。

    要替换的是 Windows 不允许出现在文件名里的九个字符，顺带把换行制表符
    也换掉——B 站有些标题里真的带换行。
    末尾的 strip(".") 是因为 Windows 不允许文件名以点结尾。

    非字符串一律先转成字符串。标题字段偶尔会是数字或 None，
    直接拿去做正则替换会抛类型错误
    """
    if name is None:
        return "video"
    if not isinstance(name, str):
        name = str(name)
    if not name.strip():
        return "video"
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > limit:
        name = name[:limit].rstrip()
    return name or "video"


# ============================================================================
#  纯 Python 二维码编码器
#
#  为什么自己写一个
#   B 站扫码登录必须把那条链接画成二维码，而工具定位是"只装 requests 就能跑"。
#   引 qrcode 库就多一个依赖，引 Pillow 只想在终端里显示又太重。
#   所以这里内嵌一份实现，覆盖版本 1 到 10、L 与 M 两级纠错。
#   登录链接一百来字节，版本 8 就到顶了，够用。
#
#  正确性怎么保证
#   不跟参考库比矩阵——不同实现可以选不同掩码，两种都合法，比了也没意义。
#   做法是把生成的码画成像素图，交给 OpenCV 的独立解码器读回来对内容。
#   见 test_qr.py，12 项用例全过。
# ============================================================================
class QRCode:
    """
    极简 QR 编码器。

    只实现字节模式（UTF-8 编码后的任意数据都走这条），
    不实现数字模式、字母数字模式、汉字模式的压缩优化。
    同样的内容用字节模式会比官方实现多占一点容量、版本号可能高一档，
    但对登录链接这种规模完全无所谓。
    """

    # 对齐图形的中心坐标。版本 1 没有对齐图形，其余版本按标准给出
    ALIGN = {
        1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
        6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
    }

    # 每块的纠错码字数。
    #
    # 这里踩过坑，记下来
    #   标准表里给的是"每块总码字数"和"每块数据码字数"，两者相减才是每块纠错字数。
    #   我一开始把某张表里那个较大的数当成了纠错字数，结果单块版本碰巧对得上，
    #   多块版本全错。单块时"总纠错"和"每块纠错"数值相同，所以这个错误
    #   在版本 1、2 上完全看不出来，直到版本 3 才暴露。
    ECC_PER_BLOCK = {
        "L": {1: 7, 2: 10, 3: 15, 4: 20, 5: 26, 6: 18, 7: 20, 8: 24, 9: 30, 10: 18},
        "M": {1: 10, 2: 16, 3: 26, 4: 18, 5: 24, 6: 16, 7: 18, 8: 22, 9: 22, 10: 26},
    }

    # 数据块划分，格式为 第一组(块数, 每块数据码字数), 第二组(块数, 每块数据码字数)。
    #
    # 版本 8 以上会出现两种块长，第二组的块比第一组多一个码字。
    # 交错时按列取，短块取完就跳过，这一点写错了整张码就废了。
    #
    # 版本 3 的 M 级是单块 44 字，不是两块各 22 字。这两个的总数据量一样，
    # 但纠错块数不同，画出来的码完全不一样。
    BLOCKS = {
        ("L", 1): ((1, 19), (0, 0)),   ("M", 1): ((1, 16), (0, 0)),
        ("L", 2): ((1, 34), (0, 0)),   ("M", 2): ((1, 28), (0, 0)),
        ("L", 3): ((1, 55), (0, 0)),   ("M", 3): ((1, 44), (0, 0)),
        ("L", 4): ((1, 80), (0, 0)),   ("M", 4): ((2, 32), (0, 0)),
        ("L", 5): ((1, 108), (0, 0)),  ("M", 5): ((2, 43), (0, 0)),
        ("L", 6): ((2, 68), (0, 0)),   ("M", 6): ((4, 27), (0, 0)),
        ("L", 7): ((2, 78), (0, 0)),   ("M", 7): ((4, 31), (0, 0)),
        ("L", 8): ((2, 97), (0, 0)),   ("M", 8): ((2, 38), (2, 39)),
        ("L", 9): ((2, 116), (0, 0)),  ("M", 9): ((3, 36), (2, 37)),
        ("L", 10): ((2, 68), (2, 69)), ("M", 10): ((4, 43), (1, 44)),
    }

    # 纠错级别在格式信息里的两位编码。顺序看着别扭但不是笔误，
    # L 是 01、M 是 00，这是标准规定的值
    ECC_BITS = {"L": 0b01, "M": 0b00}

    def __init__(self, data, ecc="M"):
        """
        data 传字符串或字节都行，字符串会按 UTF-8 编码。
        ecc 是纠错级别，默认 M。级别越高越耐污损，同样内容占的版本也越高。
        登录链接这种屏幕显示的码用 M 足够，没必要上 Q 或 H。
        """
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.data = data
        self.ecc = ecc
        self.version, self.data_codewords = self._pick_version()
        # 边长 = 版本号 x 4 + 17。版本 1 是 21x21，之后每升一版加 4
        self.size = self.version * 4 + 17
        self.modules = [[None] * self.size for _ in range(self.size)]
        # 标记哪些格子已经被功能图形占用，数据不能再往里填，掩码也不能翻转它们
        self._reserved = [[False] * self.size for _ in range(self.size)]

    def _pick_version(self):
        """
        选最小的够用版本。

        容量算法  4 位模式指示符 + 字符计数指示符 + 数据位数。
        字符计数指示符的宽度随版本变：版本 9 及以下是 8 位，版本 10 起是 16 位。
        位数不够就换下一个版本。
        """
        for v in range(1, 11):
            g1, g2 = self.BLOCKS[(self.ecc, v)]
            total = g1[0] * g1[1] + g2[0] * g2[1]
            count_bits = 8 if v <= 9 else 16
            bits = 4 + count_bits + len(self.data) * 8
            if bits <= total * 8:
                return v, total
        raise ValueError("内容过长，超出本编码器支持范围（版本 1 到 10）")

    # ---- 伽罗华域 GF(256) ----
    @staticmethod
    def _gf_tables():
        """
        造 GF(256) 的指数表与对数表。

        QR 用的伽罗华域由本原多项式 x^8+x^4+x^3+x^2+1 生成，二进制就是 0x11D。
        做法是不断乘 2，一旦第 9 位冒出来就拿 0x11D 约掉。
        这就是 a^8 = a^4+a^3+a^2+1 = 0x1D 的由来，可以拿来验证表对不对。

        exp 开到 512 长是为了省掉取模：两个小于 255 的下标相加最多 508，
        直接查表就行，不用每次 % 255。
        """
        exp = [0] * 512
        log = [0] * 256
        x = 1
        for i in range(255):
            exp[i] = x
            log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= 0x11D
        for i in range(255, 512):
            exp[i] = exp[i - 255]
        return exp, log

    @classmethod
    def _rs_generator(cls, degree):
        """
        生成多项式 g(x) = (x-a^0)(x-a^1)...(x-a^(degree-1))，展开后 degree+1 项。

        这里也踩过坑。曾经在循环里先对 poly 做了一次原地追加，又调用 _poly_mul，
        等于乘了两遍，长度直接翻倍。结果是度 7 的多项式出来 15 项，
        编码时下标越界。生成多项式长度不对，整个纠错就是废的。
        """
        exp, _ = cls._gf_tables()
        poly = [1]
        for i in range(degree):
            poly = cls._poly_mul(poly, [1, exp[i]])
        return poly

    @staticmethod
    def _poly_mul(a, b):
        """
        多项式乘法，系数在 GF(256) 上运算。

        乘法变成"对数相加再查指数表"，加法就是异或。
        系数为 0 的项直接跳过——0 没有对数，查表会炸。
        """
        exp, log = QRCode._gf_tables()
        out = [0] * (len(a) + len(b) - 1)
        for i, av in enumerate(a):
            if av == 0:
                continue
            for j, bv in enumerate(b):
                if bv == 0:
                    continue
                out[i + j] ^= exp[log[av] + log[bv]]
        return out

    @staticmethod
    def _rs_encode(data, ec_len):
        """
        给一块数据算纠错码字，返回 ec_len 个字节。

        标准的多项式长除法：把数据左移 ec_len 位，然后不断用生成多项式
        去消掉最高次项。res 前 len(data) 轮跑完就被消成 0，剩下的尾部就是纠错码。

        实际实现用的是免除法版本——不真的做减法，而是把当前最高项系数
        直接反馈进后面的位置。数学上等价，少一层循环。
        """
        exp, log = QRCode._gf_tables()
        gen = QRCode._rs_generator(ec_len)
        res = list(data) + [0] * ec_len
        for i in range(len(data)):
            coef = res[i]
            if coef == 0:
                continue
            for j in range(1, len(gen)):
                if gen[j]:
                    res[i + j] ^= exp[log[gen[j]] + log[coef]]
        return res[len(data):]

    def _bitstream(self):
        """
        把数据编成码字序列。

        顺序是  模式指示符 -> 字符计数 -> 数据 -> 终止符 -> 补零到字节 -> 填充字节。

        几个细节
          模式指示符取 0100，表示 8 位字节模式，任意 UTF-8 数据都走这条。
          字符计数指示符宽度随版本变，版本 9 及以下 8 位，版本 10 起 16 位。
          终止符最多 4 位，但如果剩余空间不足 4 位就只补能放下的那么多。
          补零到字节边界后若还有空位，交替填 0xEC 和 0x11，这是标准指定的填充值。
        """
        bits = []

        def put(val, n):
            """把一个数按高位在前写进位流"""
            for i in range(n - 1, -1, -1):
                bits.append((val >> i) & 1)

        put(0b0100, 4)                      # 模式指示符，0100 即字节模式
        put(len(self.data), 8 if self.version <= 9 else 16)
        for b in self.data:
            put(b, 8)

        cap = self.data_codewords * 8
        put(0, min(4, cap - len(bits)))     # 终止符，空间不够就少补几位
        while len(bits) % 8:                # 补齐到字节边界
            bits.append(0)
        pad = (0xEC, 0x11)                  # 标准规定的两个填充字节，轮流用
        i = 0
        while len(bits) < cap:
            put(pad[i % 2], 8)
            i += 1
        return [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, cap, 8)]

    def _codewords(self):
        """
        分块、算纠错、再交错，返回最终要铺进矩阵的码字序列。

        为什么要分块
          数据太多时纠错能力会下降，所以标准把数据切成若干块，每块单独算纠错。
          这样局部损坏只影响一块，整体仍然可恢复。

        交错规则
          数据部分按列取：第 0 列取每块的第 0 个字节，第 1 列取每块的第 1 个……
          版本 8 以上会出现两种块长，短块取完就跳过，所以要判 i < len(b)。
          纠错部分同样按列取。
          顺序不能反，反了画出来的码扫不出来。
        """
        data = self._bitstream()
        g1, g2 = self.BLOCKS[(self.ecc, self.version)]
        ec_len = self.ECC_PER_BLOCK[self.ecc][self.version]

        # 按分组表把数据切成块
        blocks, idx = [], 0
        for count, size in (g1, g2):
            for _ in range(count):
                blocks.append(data[idx:idx + size])
                idx += size

        ecs = [self._rs_encode(b, ec_len) for b in blocks]

        # 交错输出：先数据，后纠错，都是按列取
        out = []
        for i in range(max(len(b) for b in blocks)):
            for b in blocks:
                if i < len(b):
                    out.append(b[i])
        for i in range(ec_len):
            for e in ecs:
                out.append(e[i])
        return out

    # ---- 矩阵构建 ----
    def _place_function_patterns(self):
        """
        先把所有功能图形铺好，并标记成"已占用"。

        功能图形是解码器用来定位和校正的参照物，不承载数据，掩码也不能翻转它们。
        顺序上必须先铺这些、再填数据，否则预留区会被数据覆盖掉。

        铺的东西有
          三个定位图形  左上、右上、左下，7x7 的同心方框，用于找码定位
          定时图形      第 6 行与第 6 列交替黑白，用于推算模块尺寸
          对齐图形      版本 2 起才有，用于校正形变，坐标见 ALIGN
          格式信息区    15 位，记录纠错级别与掩码编号
          版本信息区    18 位，版本 7 起才有
          固定暗模块    右下角一个恒为黑的点
        """
        s = self.size
        for r in range(s):
            for c in range(s):
                self._reserved[r][c] = False

        def finder(r0, c0):
            """
            画一个 7x7 定位图形。

            外框一圈是黑、往里一圈是白、中心 3x3 是黑。
            循环从 -1 起是为了把图形外面那圈分隔带也刷成白——
            不留这圈白，定位图形会跟旁边的数据糊在一起，解码器认不出来。
            """
            for dr in range(-1, 8):
                for dc in range(-1, 8):
                    r, c = r0 + dr, c0 + dc
                    if 0 <= r < s and 0 <= c < s:
                        inside = 0 <= dr <= 6 and 0 <= dc <= 6
                        border = dr in (0, 6) or dc in (0, 6)
                        core = 2 <= dr <= 4 and 2 <= dc <= 4
                        self.modules[r][c] = 1 if (inside and (border or core)) else 0
                        self._reserved[r][c] = True

        finder(0, 0)
        finder(0, s - 7)
        finder(s - 7, 0)

        # 定时图形。从第 8 格起，因为前 8 格已经被定位图形和格式信息占了
        for i in range(8, s - 8):
            v = 1 if i % 2 == 0 else 0
            self.modules[6][i] = v
            self._reserved[6][i] = True
            self.modules[i][6] = v
            self._reserved[i][6] = True

        # 对齐图形。中心为黑，往外一圈白一圈黑，共 5x5
        for cr in self.ALIGN[self.version]:
            for cc in self.ALIGN[self.version]:
                # 落在三个定位图形范围内的位置要跳过，那里已经有参照物了
                if (cr <= 8 and cc <= 8) or (cr <= 8 and cc >= s - 9) or (cr >= s - 9 and cc <= 8):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        r, c = cr + dr, cc + dc
                        if 0 <= r < s and 0 <= c < s:
                            edge = max(abs(dr), abs(dc))
                            self.modules[r][c] = 1 if edge != 1 else 0
                            self._reserved[r][c] = True

        # 预留格式信息区。真实内容由 _place_format 逐个掩码试算后再填，
        # 这里只是先把位置占住，免得数据铺进来。
        # 已定时图形的格子不用重复标记
        for i in range(9):
            if not self._reserved[8][i]:
                self._reserved[8][i] = True
            if not self._reserved[i][8]:
                self._reserved[i][8] = True
        for i in range(8):
            self._reserved[8][s - 1 - i] = True
            self._reserved[s - 1 - i][8] = True

        # 版本 7 起必须放版本信息。少了这两块，解码器连版本都判不出来，
        # 整张码直接扫不出来 —— 实测版本 7 到 10 全部无法识别
        if self.version >= 7:
            for i in range(18):
                self._reserved[s - 11 + i % 3][i // 3] = True
                self._reserved[i // 3][s - 11 + i % 3] = True

        self.modules[s - 8][8] = 1          # 固定暗模块，标准要求恒为黑
        self._reserved[s - 8][8] = True

    def _version_bits(self):
        """
        版本信息 18 位 = 6 位版本号 + 12 位 BCH 纠错。

        生成多项式 G18 = x^12+x^11+x^10+x^9+x^8+x^5+x^2+1，二进制 1111100100101。
        做法是把版本号左移 12 位，然后反复用 G18 消掉高位，剩下的余数拼回去。

        算法正确性用标准里的确定值验过
          版本 7 -> 0x07C94   版本 8 -> 0x085BC
          版本 9 -> 0x09A99   版本 10 -> 0x0A4D3
        """
        G18 = 0b1111100100101
        rem = self.version << 12
        for _ in range(6):
            if rem.bit_length() >= 13:
                rem ^= G18 << (rem.bit_length() - 13)
        return (self.version << 12) | rem

    def _place_version(self):
        """
        把版本信息写进两处对称位置。

        位序是从低位开始放，右下角那块用 (i//3, s-11+i%3)，
        左下角那块用 (s-11+i%3, i//3)，两者内容完全一样。
        放两块是冗余设计，一处被挡住另一处还能用。
        """
        if self.version < 7:
            return
        s = self.size
        bits = self._version_bits()
        for i in range(18):
            b = (bits >> i) & 1
            self.modules[i // 3][s - 11 + i % 3] = b   # 右上角那块
            self.modules[s - 11 + i % 3][i // 3] = b   # 左下角那块

    def _place_data(self):
        """
        把码字铺进矩阵。QR 的填充路径是固定的，必须严格遵守。

        走法是蛇形
          从右下角开始，一次取两列，从下往上走
          走到顶后向左移两列，改成从上往下走
          如此往复，直到铺满

        两个坑
          第 6 列是定时图形，直接整列跳过。所以 col 走到 6 时要多减一列。
          每两列内部先取右边那列再取左边那列，顺序反了整张码就废。
        """
        bits = []
        for cw in self._codewords():
            for i in range(7, -1, -1):
                bits.append((cw >> i) & 1)

        s = self.size
        idx, up = 0, True
        col = s - 1
        while col > 0:
            if col == 6:                   # 跳过定时图形所在列
                col -= 1
            rows = range(s - 1, -1, -1) if up else range(s)
            for r in rows:
                for c in (col, col - 1):
                    if not self._reserved[r][c]:
                        bit = bits[idx] if idx < len(bits) else 0
                        self.modules[r][c] = bit
                        idx += 1
            up = not up                    # 换方向
            col -= 2                       # 左移两列

    @staticmethod
    def _mask_fn(k, r, c):
        """
        八个掩码公式，标准里逐个列出的，没什么可推的，照抄即可。

        掩码的作用是把数据区里大片同色的区域打散。
        大片同色会让解码器难以分辨模块边界，也容易被误认成定位图形。
        """
        if k == 0: return (r + c) % 2 == 0
        if k == 1: return r % 2 == 0
        if k == 2: return c % 3 == 0
        if k == 3: return (r + c) % 3 == 0
        if k == 4: return (r // 2 + c // 3) % 2 == 0
        if k == 5: return (r * c) % 2 + (r * c) % 3 == 0
        if k == 6: return ((r * c) % 2 + (r * c) % 3) % 2 == 0
        return ((r + c) % 2 + (r * c) % 3) % 2 == 0

    def _apply_mask(self, k):
        """
        按掩码 k 翻转数据区。

        只翻未被占用的格子。功能图形要是被翻了，定位和定时全废，码就扫不出来了。
        """
        for r in range(self.size):
            for c in range(self.size):
                if self._reserved[r][c]:
                    continue
                if self._mask_fn(k, r, c):
                    self.modules[r][c] ^= 1

    def _format_bits(self, mask):
        """
        格式信息 15 位 = 5 位数据 + 10 位 BCH 纠错，最后整体异或 0x5412。

        5 位数据是 2 位纠错级别加 3 位掩码编号。
        BCH 生成多项式是 0x537，即 x^10+x^8+x^5+x^4+x^2+x+1。
        那个 0x5412 的异或是标准规定的掩码，不加的话解码器认不出来。
        """
        data = (self.ECC_BITS[self.ecc] << 3) | mask
        rem = data << 10
        for _ in range(5):
            if rem.bit_length() >= 11:
                rem ^= 0x537 << (rem.bit_length() - 11)
        return ((data << 10) | rem) ^ 0x5412

    def _place_format(self, mask):
        """
        把格式信息写到两处。

        第一处绕着左上角那个定位图形，第二处分成两段，一段在左下、一段在右上。
        15 位的分布位置标准里写得比较绕，尤其 i=6、7、8 那几位要跳开定时图形，
        所以这里逐个列出来而不是用公式算。
        末尾那个 modules[s-8][8] = 1 是固定暗模块，顺手兜一下，
        免得它前面被格式信息写到。
        """
        s = self.size
        fmt = self._format_bits(mask)
        for i in range(15):
            bit = (fmt >> i) & 1
            # 第一处，绕左上角
            if i < 6:
                self.modules[i][8] = bit
            elif i == 6:
                self.modules[7][8] = bit        # 跳过第 6 行，那是定时图形
            elif i == 7:
                self.modules[8][8] = bit
            elif i == 8:
                self.modules[8][7] = bit        # 跳过第 6 列，同上
            else:
                self.modules[8][14 - i] = bit
            # 第二处，左下与右上各一半
            if i < 8:
                self.modules[8][s - 1 - i] = bit
            else:
                self.modules[s - 15 + i][8] = bit
        self.modules[s - 8][8] = 1

    def _penalty(self):
        """
        给当前矩阵打分，分越低越好。八个掩码里挑分数最低的那个用。

        四条规则都是标准定死的，照着实现就行
          规则 1  连续 5 个以上同色，每多一个加一分，基础 3 分
          规则 2  出现 2x2 同色方块，每处 3 分
          规则 3  出现类似定位图形的 1:1:3:1:1 图案，每处 40 分
          规则 4  黑色占比偏离 50%，每偏 5% 加 10 分

        规则 3 的两个图案是两个方向，横着和竖着都要查，
        所以下面把矩阵和它的转置一起遍历。
        """
        s = self.size
        m = self.modules
        score = 0

        # 把每一行和每一列都拉成列表，行与列用同一套逻辑处理
        lines = list(m) + [[m[r][c] for r in range(s)] for c in range(s)]

        # 规则 1  连续同色
        for line in lines:
            run, prev = 1, line[0]
            for v in line[1:]:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run, prev = 1, v
            if run >= 5:
                score += 3 + (run - 5)

        # 规则 2  2x2 同色块
        for r in range(s - 1):
            for c in range(s - 1):
                if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                    score += 3

        # 规则 3  长得像定位图形的图案
        p1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
        p2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
        for line in lines:
            for i in range(s - 10):
                seg = line[i:i + 11]
                if seg == p1 or seg == p2:
                    score += 40

        # 规则 4  黑色比例
        dark = sum(sum(row) for row in m)
        ratio = dark * 100 // (s * s)
        score += abs(ratio - 50) // 5 * 10
        return score

    def build(self):
        """
        生成最终矩阵，返回 [[0/1, ...], ...]。

        流程是  铺功能图形 -> 填数据 -> 八个掩码各试一遍，取罚分最低的。

        每次试掩码都要从涂完数据的原始状态复制一份出来重来，
        不能在上一轮的结果上接着翻——那样就叠了两次掩码，出来的码是错的。
        """
        self._place_function_patterns()
        self._place_data()
        best, best_score, best_matrix = 0, None, None
        base = [row[:] for row in self.modules]
        for k in range(8):
            self.modules = [row[:] for row in base]
            self._apply_mask(k)
            self._place_format(k)
            self._place_version()
            sc = self._penalty()
            if best_score is None or sc < best_score:
                best, best_score, best_matrix = k, sc, [row[:] for row in self.modules]
        self.modules = best_matrix
        self.mask = best
        return self.modules

    # ---- 终端渲染 ----
    def to_terminal(self, quiet_zone=2, invert=False):
        """
        把矩阵渲染成能在终端显示的字符行。

        用半块字符 ▀ ▄ █ 的原因
          终端字符格是竖长条，大约 1:2。一个字符如果只表示一个模块，
          画出来的码会被纵向拉长，手机很难扫。用半块字符让一格装上下两个模块，
          比例就回到接近 1:1，扫码识别率最高。

        quiet_zone 是四周留白。标准要求至少 4 个模块，这里默认给 2，
        是为了让二维码别占满屏幕宽度。手机扫不出来时可以把参数调大。

        invert 用于浅色背景的终端。这时候是反过来的，
        亮色代表模块，所以字符映射要整体翻转。
        """
        m = self.modules
        n = len(m)
        q = quiet_zone
        size = n + q * 2

        def cell(r, c):
            """取含留白后的某格，留白区恒为浅色"""
            if r < q or c < q or r >= n + q or c >= n + q:
                return 0
            return m[r - q][c - q]

        lines = []
        # 一次处理两行，合成一行字符
        for r in range(0, size, 2):
            row = []
            for c in range(size):
                top = cell(r, c)
                bot = cell(r + 1, c) if r + 1 < size else 0
                if invert:
                    # 浅色终端，模块用亮色表示
                    if top and bot: row.append(" ")
                    elif top: row.append("▄")
                    elif bot: row.append("▀")
                    else: row.append("█")
                else:
                    # 深色终端，模块用暗色表示
                    if top and bot: row.append("█")
                    elif top: row.append("▀")
                    elif bot: row.append("▄")
                    else: row.append(" ")
            lines.append("".join(row))
        return lines


# ============================================================================
#  HTTP 会话与登录
# ============================================================================
class Bili:
    """
    一个会话对象管住所有请求。

    保持同一个 Session 是必须的，不是图省事
      Cookie 要跨请求保持，扫码登录拿到的凭证就存在这里面
      连接复用能省掉每次握手的开销，下载时更明显
      Referer 头不带上，B 站的播放地址接口会直接拒
    """

    def __init__(self):
        """建会话，装好请求头，再把上次存的登录凭证读回来"""
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            # 这两个头是硬要求。少了 Referer，取播放地址会返回 403
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        self.load_cookies()

    # ---- 登录凭证 ----
    def load_cookies(self):
        if not os.path.exists(COOKIE_FILE):
            return
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                self.s.cookies.set(k, v, domain=".bilibili.com")
        except Exception as e:
            warn("读取登录凭证失败：%s" % e)

    def save_cookies(self):
        """
        把登录凭证写进本机文件。

        只挑 bilibili 域名下的 Cookie 存，别的域的一律不落盘。
        文件权限设成 600，只让当前用户能读——这里面的 SESSDATA
        等同于账号身份，被别人拿到就相当于把号交出去了。
        Windows 上 chmod 基本无效，加了失败也照常继续。
        """
        try:
            data = {c.name: c.value for c in self.s.cookies
                    if c.domain and "bilibili" in c.domain}
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(COOKIE_FILE, 0o600)
            except Exception:
                pass
            return True
        except Exception as e:
            err("保存登录凭证失败：%s" % e)
            return False

    def clear_cookies(self):
        """退出登录。删掉凭证文件并清空会话里的 Cookie"""
        try:
            if os.path.exists(COOKIE_FILE):
                os.remove(COOKIE_FILE)
        except Exception:
            pass
        self.s.cookies.clear()

    def whoami(self):
        """
        查当前登录状态，返回 (是否登录, 用户名, 会员状态)。

        未登录时这个接口返回 code=-101，那是正常情况不是错误，
        所以这里不报错，只当成"没登录"处理。
        """
        try:
            r = self.s.get(API_NAV, timeout=15).json()
        except Exception as e:
            return False, "网络异常 %s" % e, 0
        d = r.get("data") or {}
        if r.get("code") == 0 and d.get("isLogin"):
            return True, d.get("uname", ""), d.get("vipStatus", 0)
        return False, "", 0

    def qr_login(self, timeout=180):
        """
        扫码登录，整个流程照官方网页端走一遍。

        三步
          1. 调 generate 拿到一个二维码链接和一个 qrcode_key
          2. 把链接画成二维码显示出来，让用户拿手机扫
          3. 每两秒调 poll 问一次状态，扫完确认后凭证就在返回的 url 里

        注意外层 code 一直是 0，真正表示进度的是 data.code。
        一开始我盯着外层 code 看，怎么都是 0，还以为接口没反应。
        """
        step("正在向 B 站申请登录二维码")
        try:
            r = self.s.get(API_QR_GEN, timeout=15).json()
        except Exception as e:
            err("申请二维码失败：%s" % e)
            return False
        if r.get("code") != 0:
            err("申请二维码失败：%s" % r.get("message"))
            return False

        url = r["data"]["url"]
        key = r["data"]["qrcode_key"]

        print()
        try:
            qr = QRCode(url, ecc="M")
            # build() 返回的是矩阵列表，不是 self。
            # 写成 QRCode(url).build().to_terminal() 会炸，
            # 因为列表上没有 to_terminal 方法
            qr.build()
            lines = qr.to_terminal()
            print(C.B + "  ── 请用哔哩哔哩手机客户端扫码 ──" + C.R)
            print()
            for ln in lines:
                print("   " + ln, flush=True)
            print()
        except Exception as e:
            # 画不出来不算失败，下面还有链接可以手工打开
            warn("二维码绘制失败（%s），请改用下方链接" % e)

        print(C.D + "  二维码内容：" + url + C.R)
        print(C.D + "  也可以把上面这行链接发到手机浏览器打开" + C.R)
        print()

        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                p = self.s.get(API_QR_POLL, params={"qrcode_key": key}, timeout=15).json()
            except Exception:
                # 单次轮询失败不致命，等一下再问
                time.sleep(2)
                continue

            data = p.get("data") or {}
            sub = data.get("code")
            # 状态没变就不重复刷屏，不然两秒一条"等待扫码"看着很吵
            if sub != last:
                last = sub
                if sub == QR_POLL_WAIT:
                    print(C.D + "  等待扫码…" + C.R)
                elif sub == QR_POLL_SCANNED:
                    print(C.YEL + "  已扫码，请在手机上确认登录" + C.R)
                elif sub == QR_POLL_EXPIRED:
                    err("二维码已过期，请重新运行")
                    return False
                elif sub == QR_POLL_OK:
                    if self._finish_qr(data):
                        return True
                    return False
                else:
                    print(C.D + "  状态 %s  %s" % (sub, data.get("message", "")) + C.R)
            time.sleep(2)

        err("等待超时（%d 秒），请重新运行" % timeout)
        return False

    def _finish_qr(self, data):
        """扫码确认后，从返回地址里取出凭证"""
        target = data.get("url") or ""
        try:
            q = parse_qs(urlparse(target).query)
            got = 0
            for k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
                if k in q:
                    self.s.cookies.set(k, q[k][0], domain=".bilibili.com")
                    got += 1
            # 有些情况下凭证只在响应头里
            if got == 0:
                for c in self.s.cookies:
                    if c.name in ("SESSDATA", "bili_jct", "DedeUserID"):
                        got += 1
            if got == 0:
                err("登录成功但未取到凭证，请重试")
                return False
            self.save_cookies()
            logged, name, vip = self.whoami()
            print()
            ok("登录成功" + ("：" + name if name else "") +
               ("（大会员）" if vip else ""))
            info("凭证已保存到 " + COOKIE_FILE)
            return True
        except Exception as e:
            err("处理登录结果失败：%s" % e)
            return False

    # ---- 短信登录 ----
    def sms_login(self):
        """
        手机号短信登录。

        坦白说这条路现在基本走不通。B 站的登录接口要求先过极验人机验证，
        请求里得带上 geetest 发下来的 token 与 challenge 才认。
        本工具不会去绕那套验证，所以这里只能走官方流程，
        被要求验证时把话说明白，让用户改走扫码。

        少数情况下接口不要求验证，那这条就能走通，所以功能保留着。

        返回码 -105 表示"验证码错误"，但在没有极验参数的情况下，
        这个码实际含义是"缺人机验证凭据"。看到它就该换方式了。
        """
        print()
        warn("B 站登录接口需要人机验证，本工具不会绕过。")
        info("建议优先使用 --login 扫码登录，更简单也更安全。")
        print()
        cid = input("  国家代码（中国大陆直接回车，默认 86）：").strip() or "86"
        tel = input("  手机号：").strip()
        if not re.match(r"^\d{6,15}$", tel):
            err("手机号格式不对")
            return False

        payload = {
            "cid": cid, "tel": tel, "source": "main-fe-header",
            # 这四个是人机验证的字段，故意留空。
            # 填入伪造值或许能骗过服务端，但那属于绕过验证，不做
            "token": "", "challenge": "", "validate": "", "seccode": "",
        }
        step("正在请求发送验证码")
        try:
            r = self.s.post(API_SMS_SEND, data=payload, timeout=20).json()
        except Exception as e:
            err("请求失败：%s" % e)
            return False

        code = r.get("code")
        if code == 0:
            info("验证码已发送，请查看手机短信")
        elif code == -105:
            # 这条分支是常态，直接把替代方案摆出来，别让用户干等短信
            print()
            warn("服务器要求先完成人机验证（返回码 -105）")
            info("请在浏览器里打开下面这个地址，完成验证后把结果按提示贴回来：")
            print()
            print("   " + C.BLU + "https://www.bilibili.com/" + C.R)
            print()
            info("登录后按 F12 打开开发者工具，切到 Application 或 存储 标签页，")
            info("在 Cookies 里找到 bilibili.com，复制这三个值：")
            print("   " + C.YEL + "SESSDATA" + C.R + "   " +
                  C.YEL + "bili_jct" + C.R + "   " + C.YEL + "DedeUserID" + C.R)
            print()
            info("然后运行： python %s --set-cookie" % os.path.basename(__file__))
            return False
        else:
            err("发送失败：%s（返回码 %s）" % (r.get("message"), code))
            print()
            info("若是提示验证码错误，说明同样需要人机验证。")
            info("请改用扫码登录： python %s --login" % os.path.basename(__file__))
            return False

        sms = input("  短信验证码：").strip()
        if not sms:
            err("未输入验证码")
            return False

        payload.update({"code": sms})
        step("正在提交验证码")
        try:
            r2 = self.s.post(API_SMS_LOGIN, data=payload, timeout=20).json()
        except Exception as e:
            err("提交失败：%s" % e)
            return False

        if r2.get("code") != 0:
            err("登录失败：%s" % r2.get("message"))
            return False

        self.save_cookies()
        logged, name, vip = self.whoami()
        print()
        ok("登录成功" + ("：" + name if name else ""))
        return True

    def set_cookie_manual(self):
        """
        手工粘贴 Cookie 登录。

        给"不想扫码，浏览器里已经登录着"的人用。
        三个值缺一不可：SESSDATA 是身份凭据，bili_jct 用于写操作，
        DedeUserID 是用户编号。少了任何一个，接口都会当未登录。
        """
        print()
        info("从浏览器开发者工具里复制 Cookie 值。三个都要，缺一不可。")
        print()
        sess = input("  SESSDATA        ：").strip()
        jct = input("  bili_jct        ：").strip()
        uid = input("  DedeUserID      ：").strip()
        if not (sess and jct and uid):
            err("三项都必须填写")
            return False
        for k, v in (("SESSDATA", sess), ("bili_jct", jct), ("DedeUserID", uid)):
            self.s.cookies.set(k, v, domain=".bilibili.com")
        if self.save_cookies():
            # 存完再验一次。粘贴少了字符或已经过期，这里能当场发现，
            # 不用等到下载时才发现拿不到高清
            logged, name, vip = self.whoami()
            if logged:
                ok("登录成功" + ("：" + name if name else ""))
                return True
            warn("凭证已保存，但校验未通过。可能已过期或复制不完整。")
            return False
        return False

    # ---- 取视频信息 ----
    def video_info(self, bvid=None, aid=None):
        """
        取视频基本信息。返回 (数据, 错误)，出错时数据为 None。

        返回里的 pages 是分P列表，单P视频也有一个元素。
        每个元素带自己的 cid，取播放地址时必须用对应的 cid，
        用错了会下到别的分P去。
        """
        params = {"bvid": bvid} if bvid else {"aid": aid}
        try:
            r = self.s.get(API_VIEW, params=params, timeout=20).json()
        except Exception as e:
            return None, "网络异常：%s" % e
        if r.get("code") != 0:
            return None, r.get("message") or ("返回码 %s" % r.get("code"))
        return r["data"], None

    def best_quality(self, bvid, cid):
        """
        问出这个视频在本账号下能拿到的最高清晰度编号。

        做法是先用一个极小的 qn 试一次，只为读回 accept_quality 列表。
        那个列表就是本账号对这个视频可用的全部清晰度，从高到低排好，
        取里面的最大值即可。

        为什么先问一次，而不是直接传个大数让服务端降级
          实测服务端确实会自动降到账号能拿的最高档，传 127 也不会报错。
          但那样程序自己不知道最终给的是哪一档，用户要 4K 却只拿到 1080P 时
          说不清原因。先问再按实际可用的档位请求，就能明确告诉用户拿到了什么。

        拿不到列表时退回 80（1080P），这是个安全的默认值。
        """
        try:
            r = self.s.get(API_PLAYURL,
                           params={"bvid": bvid, "cid": cid, "qn": 16,
                                   "fnval": 1, "fnver": 0, "fourk": 1,
                                   "platform": "html5"},
                           timeout=20).json()
        except Exception:
            return 80
        if r.get("code") != 0:
            return 80
        accept = (r.get("data") or {}).get("accept_quality") or []
        # 接口说它是从高到低排的，但这里自己再取一次最大值，不依赖接口顺序。
        # 要做类型转换是因为接口偶尔返回字符串形式的编号，
        # 混着非数字元素时直接 max 会抛类型错误
        try:
            nums = [int(x) for x in accept]
            return max(nums) if nums else 80
        except Exception:
            return 80

    def playurl(self, bvid, cid, qn=None):
        """
        取播放地址。这是整个工具最要紧的一步。

        关于 fnval，它决定返回什么形式
          fnval=1      返回 durl，音视频已经合并进同一个文件
          fnval=4048   返回 dash，视频轨和音频轨分开

        这里故意用 fnval=1。合并流下载下来就是能直接播放的完整文件，
        不需要再拿 ffmpeg 拼一遍，工具也因此不用依赖 ffmpeg。
        某个视频没有合并流时，下面会退回 DASH 处理。

        关于清晰度
          qn 传 None 就是自动取最高，内部会先调 best_quality 问一次。

          上限由账号决定，不是工具能决定的
            游客    通常 1080P 及以下
            登录    账号本身允许的最高档
            大会员  视频本身有的话，能到 4K、HDR、杜比视界

          工具不绕过任何权限。接口给什么就是什么，不会伪造参数去骗更高的档。
        """
        if qn is None:
            qn = self.best_quality(bvid, cid)

        params = {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 1,
                  "fnver": 0, "fourk": 1, "platform": "html5"}
        try:
            r = self.s.get(API_PLAYURL, params=params, timeout=20).json()
        except Exception as e:
            return None, "网络异常：%s" % e
        if r.get("code") != 0:
            return None, r.get("message") or ("返回码 %s" % r.get("code"))

        d = r["data"]
        # 接口会顺带列出本账号对该视频可用的清晰度，带上给上层做提示用
        accept = d.get("accept_quality") or []

        # 先看有没有合并流
        durl = d.get("durl") or []
        if durl and durl[0].get("url"):
            return {
                "kind": "merged",
                "quality": d.get("quality"),
                "requested": qn,
                "accept": accept,
                "format": d.get("format"),
                "parts": [(x["url"], x.get("size", 0)) for x in durl],
                # 接口给的 length 是毫秒，换算成秒
                "length": sum(x.get("length", 0) for x in durl) / 1000.0,
                "backup": [u for x in durl for u in (x.get("backup") or [])],
            }, None

        # 没有合并流，退回 DASH
        dash = d.get("dash") or {}
        videos = dash.get("video") or []
        audios = dash.get("audio") or []
        if not videos:
            # 走到这里通常意味着内容受限，不是网络问题
            return None, "该视频没有可用的下载地址（可能是付费或受限内容）"

        # 先锁定最高的清晰度档，再在同一档里挑码率最高的一条。
        # 同一档会有多种编码（avc / hevc / av1），排列顺序不保证，
        # 挑码率最高的那条才能保证画质最好
        top_q = max(x.get("id", 0) for x in videos)
        same_q = [x for x in videos if x.get("id", 0) == top_q]
        v = max(same_q, key=lambda x: x.get("bandwidth", 0))
        a = max(audios, key=lambda x: x.get("bandwidth", 0)) if audios else None
        return {
            "kind": "dash",
            "quality": v.get("id"),
            "requested": qn,
            "accept": accept,
            "format": "dash",
            "video": (v["baseUrl"], v.get("bandwidth", 0)),
            "audio": (a["baseUrl"], a.get("bandwidth", 0)) if a else None,
            "parts": [],
            "length": 0,
            "backup": [],
        }, None
# ============================================================================
#  下载
# ============================================================================
def http_get_stream(session, url, headers=None, retries=3):
    """
    带重试的流式请求。

    拿到非 200/206 的响应时要显式关掉它再重试。
    不关的话这条连接会一直挂在连接池里，重试几次就积几条，
    下载大文件时连接池被占满，后面的请求会莫名其妙卡住
    """
    last = None
    for i in range(retries):
        r = None
        try:
            r = session.get(url, headers=headers, stream=True, timeout=(15, 60))
            if r.status_code in (200, 206):
                return r
            last = "HTTP %s" % r.status_code
            try:
                r.close()
            except Exception:
                pass
        except Exception as e:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
            last = str(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(last or "请求失败")


def download_file(session, url, path, desc, expect_size=0):
    """
    把一个地址流式下载到文件，带进度条和断点续传。
    返回 (是否成功, 实际拿到的字节数)。

    几个设计取舍

    先写 .part 再改名
      下载过程中的文件叫 xxx.mp4.part，全部拿完才改名为 xxx.mp4。
      这样中途断了不会留下一个看起来正常、其实残缺的文件。

    续传靠 Range 头
      发现 .part 已存在就把它的长度发过去，让服务器从那里接着传。

    服务器不认 Range 时重头来
      如果带着 Range 却收到 200 而不是 206，说明服务器忽略了范围请求，
      返回的是完整文件。这时候必须把已下的部分丢掉重来，
      否则会把整个文件追加到半截文件后面，得到一个坏文件。

    读流出错必须接住
      网络抖动、连接被重置、读超时都会在读取过程中抛出来。
      以前这里没接，异常会一路冒到顶把程序整个带崩 ——
      下大文件时中途断网是常事，工具不该因此退出。
      现在接住之后保留 .part，下次运行能接着下。

    进度条刷新限流
      每 0.25 秒最多重绘一次。不限流的话每收一个数据块就刷一次屏，
      终端会卡住，反而看不清进度。
    """
    headers = {"Referer": "https://www.bilibili.com/", "User-Agent": UA}
    tmp = path + ".part"
    done = os.path.getsize(tmp) if os.path.exists(tmp) else 0

    if done:
        if expect_size and done == expect_size:
            # 上次其实已经下完了，只是没来得及改名
            os.replace(tmp, path)
            return True, done
        if expect_size and done > expect_size:
            # 半截文件比接口上报的还大，两者对不上，不能信。
            # 以前这里直接把它当成品改名了，那会留下一个来路不明的文件。
            # 稳妥做法是丢掉重下
            warn("%s 已存在的临时文件比预期大（%s / %s），丢弃重下"
                 % (desc, fmt_size(done), fmt_size(expect_size)))
            try:
                os.remove(tmp)
            except Exception:
                pass
            done = 0

    if done:
        headers["Range"] = "bytes=%d-" % done

    try:
        r = http_get_stream(session, url, headers)
    except Exception as e:
        err("%s 下载失败：%s" % (desc, e))
        return False, done

    # 服务器返回 200 表示它没理 Range，得从头下载
    if done and r.status_code == 200:
        done = 0
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

    # 总长度优先信接口给的 size，拿不到就退回读响应头
    total = expect_size
    if not total:
        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit():
            total = int(cl) + done

    got = done
    t0 = time.time()
    last_draw = 0.0
    read_error = None
    try:
        with open(tmp, "ab") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last_draw > 0.25:
                    last_draw = now
                    speed = (got - done) / max(0.001, now - t0)
                    if total:
                        pct = got * 100.0 / total
                        # 进度条固定 33 格宽，免得长度跳动导致整行闪烁
                        bar = "█" * int(pct / 3) + "░" * (33 - int(pct / 3))
                        line = "  %s %s %5.1f%%  %s / %s  %s/s" % (
                            desc, bar, pct, fmt_size(got), fmt_size(total), fmt_size(speed))
                    else:
                        line = "  %s  %s  %s/s" % (desc, fmt_size(got), fmt_size(speed))
                    # 用 \r 回到行首覆盖重绘，不换行
                    sys.stdout.write("\r" + line[:150].ljust(min(150, len(line))))
                    sys.stdout.flush()
    except Exception as e:
        # 网络中断、磁盘写满、连接重置都落这里。
        # 不往外抛，保住已经下到的那部分
        read_error = e
    finally:
        try:
            r.close()
        except Exception:
            pass
    # 用空格盖掉残留的进度条，再回到行首
    sys.stdout.write("\r" + " " * 150 + "\r")

    if read_error is not None:
        err("%s 下载中断：%s" % (desc, read_error))
        info("已下载 %s，再次运行会从这里继续。" % fmt_size(got))
        return False, got

    # 长度对不上就判定失败，保留 .part 供下次续传
    if total and got < total:
        err("%s 未下完（%s / %s）" % (desc, fmt_size(got), fmt_size(total)))
        return False, got

    os.replace(tmp, path)
    return True, got


def try_merged(session, info, outdir):
    """
    下载已合并的 MP4。

    大多数情况就一个分片，直接下。
    偶尔会遇到切片的视频，这时逐个下到临时目录再拼起来。
    拼接用二进制追加即可——同一视频的分片格式相同，
    MP4 允许这样简单连接，不需要重新封装。
    """
    title = sanitize(info["title"])
    path = os.path.join(outdir, title + ".mp4")

    # 同名文件已存在就跳过，避免重复下载
    if os.path.exists(path) and os.path.getsize(path) > 0:
        info["skipped"] = True
        return path

    parts = info["parts"]
    if len(parts) == 1:
        url, size = parts[0]
        good, got = download_file(session, url, path, "下载中", size)
        return path if good else None

    # 多分片  逐个下载后拼接
    tmpdir = os.path.join(outdir, ".parts")
    os.makedirs(tmpdir, exist_ok=True)
    files = []
    try:
        for i, (url, size) in enumerate(parts, 1):
            p = os.path.join(tmpdir, "p%03d" % i)
            good, _ = download_file(session, url, p, "分片 %d/%d" % (i, len(parts)), size)
            if not good:
                return None
            files.append(p)
        with open(path, "wb") as out:
            for p in files:
                with open(p, "rb") as f:
                    shutil.copyfileobj(f, out, 1024 * 1024)
        return path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def try_dash(session, info, outdir):
    """
    处理 DASH 分轨的兜底路径。

    视频轨和音频轨各下一个文件，能合并就合并。
    合并失败也不是错误，两个文件都在，用播放器分别加载照样能看，
    只是麻烦一点。所以这里不返回失败，只把情况说清楚。
    """
    title = sanitize(info["title"])
    vurl, _ = info["video"]
    vpath = os.path.join(outdir, title + ".video.mp4")
    good, _ = download_file(session, vurl, vpath, "视频轨")
    if not good:
        return None
    result = vpath

    if info.get("audio"):
        aurl, _ = info["audio"]
        apath = os.path.join(outdir, title + ".audio.mp4")
        good2, _ = download_file(session, aurl, apath, "音频轨")
        if good2:
            merged = merge_with_ffmpeg(vpath, apath, os.path.join(outdir, title + ".mp4"))
            if merged:
                # 合并成功就把两个中间文件清掉，只留成品
                try:
                    os.remove(vpath)
                    os.remove(apath)
                except Exception:
                    pass
                result = merged
            else:
                print()
                warn("没有找到 ffmpeg，音视频已分开保存。")
                info("安装 ffmpeg 后重新运行即可自动合并，或用播放器分别加载。")
                info("视频文件：" + vpath)
                info("音频文件：" + apath)
    return result


def find_ffmpeg():
    """
    找 ffmpeg。

    PATH 里没有的话再看几个常见安装位置。Windows 上多半是 winget 装的，
    落在 LOCALAPPDATA 下的 Links 目录；也有很多人手工解压到 C 或 D 盘根目录。
    """
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in (
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe", r"D:\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
    ):
        if os.path.exists(c):
            return c
    return None


def merge_with_ffmpeg(vpath, apath, outpath):
    """
    调 ffmpeg 把两条轨拼成一个文件。

    用 -c copy 做流复制，不重新编码。好处是快且不损失画质——
    几秒就能完成，而不是把整个视频重压一遍。
    也正因为不重编码，两条轨的编码格式必须能被同一个容器装下，
    MP4 装 H.264 加 AAC 没问题。
    """
    exe = find_ffmpeg()
    if not exe:
        return None
    import subprocess
    step("正在用 ffmpeg 合并音视频")
    try:
        r = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", vpath, "-i", apath,
             "-c", "copy", outpath],
            capture_output=True, timeout=1800)
        if r.returncode == 0 and os.path.exists(outpath):
            ok("合并完成")
            return outpath
        err("合并失败：" + (r.stderr or b"").decode("utf-8", "replace")[:200])
    except Exception as e:
        err("调用 ffmpeg 失败：%s" % e)
    return None


# ============================================================================
#  链接解析
# ============================================================================
def parse_link(text):
    """
    从用户粘贴的内容里挑出视频编号。

    为什么处理得这么宽松
      用户复制链接时经常连前后文字一起粘进来，也可能只粘 BV 号，
      还可能从手机 App 分享出短链。与其要求格式正确后报错，
      不如直接在里面搜一遍，找到什么用什么。

    返回 (bvid, aid, page)，三个值里 bvid 和 aid 只会有一个非空。

    传进来不是字符串时先转成字符串。命令行参数理论上都是字符串，
    但这个函数也可能被当成库调用，收到数字不该直接抛异常
    """
    if text is None:
        return None, None, 1
    if not isinstance(text, str):
        text = str(text)
    text = text.strip().strip('"').strip("'")
    if not text:
        return None, None, 1

    # 短链得先跟一次跳转才知道真实地址
    if "b23.tv" in text or "bili2233.cn" in text:
        m = re.search(r"https?://[^\s]+", text)
        if m:
            step("正在解析短链")
            try:
                r = requests.get(m.group(0), headers={"User-Agent": UA},
                                 allow_redirects=True, timeout=15)
                text = r.url
                info("还原为 " + text[:90])
            except Exception as e:
                # 短链跟不了也没关系，下面照样能从头解析
                warn("短链解析失败：%s" % e)

    # 分P 参数的两种写法，标准的是 ?p=3，手机分享出来的偶尔是 /p3 或 _p3
    page = 1
    m = re.search(r"[?&]p=(\d+)", text) or re.search(r"[/_]p(\d+)", text)
    if m:
        page = max(1, int(m.group(1)))

    m = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if m:
        return m.group(1), None, page

    m = re.search(r"av(\d+)", text, re.I)
    if m:
        return None, int(m.group(1)), page

    return None, None, page


# ============================================================================
#  主流程
# ============================================================================
BANNER = r"""
  ______ _           _    ___   __     ____  _
 |  ____(_)         (_)  |__ \ |  |   |  _ \| |
 | |__   _  __ _ ___ _      ) ||  |   | | | | |     B 站视频下载器
 |  __| | |/ _` / __| |    / / |  |   | | | | |     命令行版 v%s
 | |    | | (_| \__ \ |   / /_ |  |   | |_| | |____
 |_|    |_|\__,_|___/_|  |____||__|  |____/|______|
""" % APP_VER

NOTICE = """\
 ─────────────────────────────────────────────────────────────────────────────
  使用须知
   仅供个人学习与备份你有权保存的内容。请遵守哔哩哔哩用户协议与著作权法，
   不要用于传播、二次上传或商业用途。本工具不下载付费番剧与会员专属内容，
   也不绕过任何付费或权限校验。请勿高频批量抓取。
 ─────────────────────────────────────────────────────────────────────────────
"""


def get_outdir(argv):
    """取下载目录。命令行给了 -o 就用它，没给就用默认目录，都确保目录存在"""
    for i, a in enumerate(argv):
        if a in ("-o", "--out") and i + 1 < len(argv):
            d = argv[i + 1]
            os.makedirs(d, exist_ok=True)
            return d
    os.makedirs(DEFAULT_OUTDIR, exist_ok=True)
    return DEFAULT_OUTDIR


def download_one(bili, link, page=None, outdir=None, qn=None, all_parts=False):
    """
    下载一个链接。

    page 是命令行 -p 指定的分P，为 None 时看情况：
    单P视频直接用，多P视频会问用户要哪一个。
    all_parts 为真则不分P，全部下。
    """
    bvid, aid, pg = parse_link(link)
    if not bvid and not aid:
        # 解析不出来时把能用的写法列给对方，比只说"链接无效"有用
        err("没能从这段内容里找到视频标识。")
        info("支持的写法：")
        info("  https://www.bilibili.com/video/BV1xx411c7mD")
        info("  https://b23.tv/xxxxxxx          短链")
        info("  BV1xx411c7mD                    直接贴 BV 号")
        info("  av12345                         直接贴 av 号")
        return False
    if page:
        pg = page

    step("正在获取视频信息")
    data, e = bili.video_info(bvid=bvid, aid=aid)
    if e:
        err("获取失败：%s" % e)
        if "不存在" in str(e) or "-404" in str(e):
            info("请确认链接正确，或该视频已被删除、设为私密。")
        return False

    pages = data.get("pages") or []
    if not pages:
        err("该视频没有可下载的分P")
        return False

    # 分P 选择。--all 优先，其次是 -p，都没有才问
    #
    # 这里原来有个顺序错误：--all 的判断放在最后，
    # 导致 `--all -p 3` 这种组合会先被 -p 分支吃掉，只下一个分P，
    # 跟用户明确要求"全部"的意图相反。改成先判断 --all
    if all_parts and len(pages) > 1:
        if page is not None:
            warn("同时指定了 --all 与 -p，按 --all 处理，下载全部分P。")
        targets = list(range(len(pages)))
    elif len(pages) > 1 and page is None:
        print()
        print(C.B + "  这是一个多分P视频，共 %d 个分P：" % len(pages) + C.R)
        for i, p in enumerate(pages[:30], 1):
            print("    %2d. %s  (%s)" % (i, sanitize(p.get("part", ""), 46),
                                         fmt_dur(p.get("duration", 0))))
        if len(pages) > 30:
            print("    ... 其余 %d 个" % (len(pages) - 30))
        print()

        # 非交互环境（管道、重定向、计划任务）里 stdin 没有输入源，
        # 直接调 input() 会抛 EOFError 把程序顶掉。所以先判断再问
        if not sys.stdin or not sys.stdin.isatty():
            warn("当前非交互环境，自动选择第 1 个分P。")
            info("要下载指定分P请加 -p 序号，要全部下载请加 --all")
            targets = [0]
        else:
            try:
                sel = input("  请输入分P序号（回车默认第 1 个，输入 all 下载全部）：").strip().lower()
            except (EOFError, KeyboardInterrupt):
                # 用户按了 Ctrl+C 或输入流断了，当成默认选择，不算错误
                print()
                warn("未收到输入，默认选择第 1 个分P。")
                sel = ""
            if sel == "all":
                targets = list(range(len(pages)))
            elif sel.isdigit() and 1 <= int(sel) <= len(pages):
                targets = [int(sel) - 1]
            else:
                targets = [0]
    else:
        # 越界就夹到合法范围，不报错。用户给 -p 99 时下最后一个更合常理
        idx = min(max(1, pg), len(pages)) - 1
        targets = [idx]

    logged, uname, vip = bili.whoami()
    if logged:
        info("已登录：%s%s" % (uname, "（大会员）" if vip else ""))
    else:
        info("未登录，将取到游客清晰度。想拿更高清晰度请先运行 --login 扫码登录。")

    all_ok = True
    for n, idx in enumerate(targets, 1):
        p = pages[idx]
        cid = p.get("cid")          # 取播放地址必须用这个分P自己的 cid
        part = p.get("part") or ""
        # 标题缺字段时给个兜底，不然拼文件名会直接崩
        title = data.get("title") or "video"
        # 多P视频给每个分P单独起名，否则会互相覆盖
        if len(pages) > 1:
            title = "%s [P%d]%s" % (title, idx + 1, (" " + part) if part else "")

        print()
        print(C.B + "  " + "─" * 70 + C.R)
        info("标题   " + data.get("title", ""))
        if len(pages) > 1:
            info("分P    P%d / %d  %s" % (idx + 1, len(pages), part))
        # 用 get 逐层取，接口偶尔少字段，不能让显示信息把程序带崩
        info("作者   " + ((data.get("owner") or {}).get("name") or "未知"))
        info("时长   " + fmt_dur(p.get("duration") or data.get("duration")))
        real_bvid = data.get("bvid") or bvid or ""
        real_aid = data.get("aid") or aid or ""
        info("编号   " + ("BV" + real_bvid[2:] if real_bvid.startswith("BV")
                          else (real_bvid or ("av%s" % real_aid))))
        print()

        step("正在获取下载地址")
        info_play, e = bili.playurl(data.get("bvid") or bvid, cid, qn=qn)
        if e:
            err("获取失败：%s" % e)
            if "付费" in str(e) or "会员" in str(e) or "1080" in str(e):
                info("该清晰度或该视频需要大会员权限。本工具不下载付费内容。")
            all_ok = False
            continue

        q = info_play.get("quality")
        qname = QUALITY_NAME.get(q, str(q))
        kind = "已合并 MP4" if info_play["kind"] == "merged" else "DASH 分轨"
        info("清晰度 " + qname + "   形式 " + kind)

        # 说明清晰度是怎么定的，以及为什么没能更高。
        # 用户最常问的就是"我登录了怎么还是 1080P"，这里直接答清楚
        #
        # accept 里的元素统一转成整数再比较。接口偶尔会返回字符串编号，
        # 或者混进 None，直接 max 会抛类型错误把下载中断
        accept = []
        for _x in (info_play.get("accept") or []):
            try:
                accept.append(int(_x))
            except Exception:
                continue

        if qn is None:
            if len(accept) > 1:
                top = QUALITY_NAME.get(max(accept), str(max(accept)))
                if top == qname:
                    info("已自动选取本账号可用的最高清晰度。")
                else:
                    info("本账号对该视频最高可到 %s，实际取到 %s。" % (top, qname))
            elif not logged:
                info("未登录时的清晰度上限通常是 1080P。登录后可取更高，"
                     "大会员可到 4K 及以上（前提是视频本身有）。")
        else:
            # 用户明确指定了档位。接口拿不到那一档时会自动降级，
            # 这种静默降级必须讲出来，否则用户以为下的是自己要的档
            asked = max(accept) if accept else None
            if q != qn:
                warn("你指定的是 %s，但接口实际给出 %s。"
                     % (QUALITY_NAME.get(qn, str(qn)), qname))
                if asked:
                    info("本账号对该视频最高可到 "
                         + QUALITY_NAME.get(asked, str(asked))
                         + "。更高档需要对应权限，工具不会绕过。")
                else:
                    info("可能是该档位需要更高权限，或视频本身没有这一档。")

        if info_play["kind"] == "dash":
            warn("该视频只有 DASH 分轨，需要一个 ffmpeg 来合并音视频")

        item = {"title": title, "skipped": False}
        item.update(info_play)
        item["parts"] = info_play.get("parts") or []

        step("开始下载")
        if info_play["kind"] == "merged":
            path = try_merged(bili.s, item, outdir)
        else:
            path = try_dash(bili.s, item, outdir)

        if item.get("skipped"):
            warn("文件已存在，跳过：" + path)
        elif path and os.path.exists(path):
            ok("已保存 " + path + "  （" + fmt_size(os.path.getsize(path)) + "）")
        else:
            err("下载未完成")
            all_ok = False

    return all_ok


def interactive(bili):
    """
    交互模式。启动时不带任何参数就走这里。

    做成循环而不是"一次一个链接就退出"，是因为实际用起来经常要连着下好几个。
    每次输入都单独兜住异常，一个链接出错不该把整个会话结束掉。
    """
    outdir = DEFAULT_OUTDIR
    print(BANNER)
    print(NOTICE)

    logged, uname, vip = bili.whoami()
    if logged:
        ok("已登录：%s%s" % (uname, "（大会员）" if vip else ""))
    else:
        warn("当前未登录。游客可下载 720P 及以下，登录后可取更高清晰度。")
        ans = input("  现在扫码登录吗？[y/N] ").strip().lower()
        if ans == "y":
            bili.qr_login()
            print()

    info("下载目录：" + outdir)
    info("直接粘贴视频链接后回车。输入 q 退出，输入 dir 换目录。")
    print()

    while True:
        try:
            line = input(C.B + "  链接> " + C.R).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("q", "quit", "exit"):
            break
        if line.lower() == "dir":
            d = input("  新目录：").strip().strip('"')
            if d:
                try:
                    os.makedirs(d, exist_ok=True)
                    outdir = d
                    ok("下载目录已改为 " + outdir)
                except Exception as e:
                    err("无法创建目录：%s" % e)
            continue
        print()
        try:
            download_one(bili, line, outdir=outdir)
        except KeyboardInterrupt:
            print()
            warn("已取消")
        except Exception as e:
            err("出现异常：%s" % e)
        print()
    print()
    info("再见。")


def main():
    """
    入口。有参数走命令行，没参数进交互模式。

    退出码约定  0 成功，1 业务失败（下载没成、登录没成），2 参数用法不对。
    这样写在脚本或计划任务里能直接判 returncode。
    """
    argv = sys.argv[1:]

    if not argv:
        interactive(Bili())
        return 0

    if argv[0] in ("-h", "--help", "/?"):
        print(__doc__)
        return 0
    if argv[0] in ("-v", "--version"):
        print("%s v%s" % (APP_NAME, APP_VER))
        return 0

    bili = Bili()

    cmd = argv[0]
    if cmd in ("--login", "-l"):
        print(BANNER)
        return 0 if bili.qr_login() else 1
    if cmd == "--login-sms":
        print(BANNER)
        return 0 if bili.sms_login() else 1
    if cmd == "--set-cookie":
        print(BANNER)
        return 0 if bili.set_cookie_manual() else 1
    if cmd == "--whoami":
        logged, name, vip = bili.whoami()
        if logged:
            ok("已登录：%s%s" % (name, "（大会员）" if vip else ""))
            return 0
        warn("未登录")
        return 1
    if cmd == "--logout":
        bili.clear_cookies()
        ok("已清除本机保存的登录凭证")
        return 0

    # 下载模式
    if cmd in ("-o", "--out"):
        err("参数顺序不对。请把链接放在最前面。")
        return 2

    page = None
    # qn 默认 None，表示不指定档位，由 playurl 自动问到本账号可用的最高档。
    # 用 -q 显式指定时才按指定值请求
    qn = None
    all_parts = False
    outdir = get_outdir(argv)
    for i, a in enumerate(argv):
        if a in ("-p", "--page") and i + 1 < len(argv) and argv[i + 1].isdigit():
            page = int(argv[i + 1])
        if a in ("-q", "--quality") and i + 1 < len(argv) and argv[i + 1].isdigit():
            qn = int(argv[i + 1])
        if a in ("-a", "--all"):
            all_parts = True

    print(BANNER)
    print(NOTICE)
    okd = download_one(bili, cmd, page=page, outdir=outdir, qn=qn,
                       all_parts=all_parts)
    print()
    return 0 if okd else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        info("已中断。")
        sys.exit(130)
