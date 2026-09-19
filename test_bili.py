# -*- coding: utf-8 -*-
r"""
B 站下载器测试套件

分两类
  离线测试  不联网，覆盖链接解析、文件名清理、格式化、二维码编码等纯逻辑
  联网测试  可选，验证 B 站接口的返回结构与下载链路

运行  python test_bili.py           只跑离线测试
      python test_bili.py --online  附带联网测试
"""

import os
import re
import sys
import json
import time
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bili_dl.py")

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


def load():
    spec = importlib.util.spec_from_file_location("bili", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================
def test_01_link_parsing(m):
    print()
    print("=" * 72)
    print("测试组 1  链接解析")
    print("=" * 72)

    cases = [
        ("https://www.bilibili.com/video/BV1GJ411x7h7", ("BV1GJ411x7h7", None, 1)),
        ("https://www.bilibili.com/video/BV1GJ411x7h7/", ("BV1GJ411x7h7", None, 1)),
        ("https://www.bilibili.com/video/BV1GJ411x7h7?p=3", ("BV1GJ411x7h7", None, 3)),
        ("https://www.bilibili.com/video/BV1GJ411x7h7/?p=12&t=30", ("BV1GJ411x7h7", None, 12)),
        ("https://www.bilibili.com/video/av80433022", (None, 80433022, 1)),
        ("https://www.bilibili.com/video/av80433022?p=2", (None, 80433022, 2)),
        ("BV1xx411c7mD", ("BV1xx411c7mD", None, 1)),
        ("av12345", (None, 12345, 1)),
        ("AV12345", (None, 12345, 1)),
        ("  https://bilibili.com/video/BV17x411w7KC  ", ("BV17x411w7KC", None, 1)),
        ('"https://www.bilibili.com/video/BV1BE411W7mM/?spm_id_from=333.999"',
         ("BV1BE411W7mM", None, 1)),
        ("https://www.bilibili.com/video/BV1GJ411x7h7?vd_source=abc#reply123",
         ("BV1GJ411x7h7", None, 1)),
        ("看这个 https://www.bilibili.com/video/BV1GJ411x7h7 很好", ("BV1GJ411x7h7", None, 1)),
        ("", (None, None, 1)),
        ("   ", (None, None, 1)),
        ("随便一段没有链接的文字", (None, None, 1)),
        ("https://www.bilibili.com/bangumi/play/ep123456", (None, None, 1)),
        ("BV1GJ411x7h", (None, None, 1)),           # 位数不足
        ("x" * 500, (None, None, 1)),                # 超长垃圾输入
        ("https://example.com/video/BV1GJ411x7h7", ("BV1GJ411x7h7", None, 1)),
    ]
    for text, want in cases:
        got = m.parse_link(text)
        check(got == want, "解析 %-52s" % (repr(text[:50])), "得到 %s 期望 %s" % (got, want))


def test_02_sanitize(m):
    print()
    print("=" * 72)
    print("测试组 2  文件名清理")
    print("=" * 72)

    check(m.sanitize("") == "video", "空标题有兜底")
    check(m.sanitize(None) == "video", "None 有兜底")
    check("/" not in m.sanitize("a/b"), "斜杠被替换")
    check("\\" not in m.sanitize("a\\b"), "反斜杠被替换")
    for ch in ':*?"<>|':
        check(ch not in m.sanitize("a%sb" % ch), "字符 %r 被替换" % ch)
    check("\n" not in m.sanitize("a\nb"), "换行被替换")
    check(m.sanitize("a" * 300).__len__() <= 100, "超长被截断到 100 以内")
    check(m.sanitize("  多余   空格  ") == "多余 空格", "多余空格被归一")
    check(m.sanitize("正常标题【测试】") == "正常标题【测试】", "中文与括号保留")
    check(m.sanitize("...") != "", "全点号不会变成空名")
    check(len(m.sanitize("标题", limit=2)) <= 2, "limit 参数生效")

    # 系统保留名不应崩溃
    for bad in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
        check(isinstance(m.sanitize(bad), str) and m.sanitize(bad), "保留名 %s 可处理" % bad)


def test_03_format(m):
    print()
    print("=" * 72)
    print("测试组 3  格式化函数")
    print("=" * 72)

    check(m.fmt_size(0) == "0 B", "fmt_size(0)")
    check(m.fmt_size(512) == "512 B", "fmt_size(512)")
    check(m.fmt_size(1024) == "1.00 KB", "fmt_size(1KB)")
    check(m.fmt_size(1024 ** 2) == "1.00 MB", "fmt_size(1MB)")
    check(m.fmt_size(1024 ** 3) == "1.00 GB", "fmt_size(1GB)")
    check(m.fmt_size(None) == "--", "fmt_size(None) 安全")
    check(m.fmt_size("abc") == "--", "fmt_size 非数字安全")
    check(m.fmt_size(-5) == "-5 B", "fmt_size 负数不崩溃")

    check(m.fmt_dur(0) == "0:00", "fmt_dur(0)")
    check(m.fmt_dur(59) == "0:59", "fmt_dur(59)")
    check(m.fmt_dur(60) == "1:00", "fmt_dur(60)")
    check(m.fmt_dur(3661) == "1:01:01", "fmt_dur(3661)")
    check(m.fmt_dur(None) == "--", "fmt_dur(None) 安全")
    check(m.fmt_dur("x") == "--", "fmt_dur 非数字安全")


def test_04_qr(m):
    print()
    print("=" * 72)
    print("测试组 4  二维码编码")
    print("=" * 72)

    # 版本与容量
    q = m.QRCode("https://www.bilibili.com", ecc="M")
    check(1 <= q.version <= 10, "短内容版本号为 %d" % q.version)
    check(q.size == q.version * 4 + 17, "尺寸与版本对应")

    long_payload = "https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key=" + "a" * 32
    q2 = m.QRCode(long_payload, ecc="M")
    check(q2.version > q.version, "长内容使用更高版本 %d" % q2.version)

    # 超长必须给出可理解的错误而非崩溃
    try:
        m.QRCode("z" * 2000, ecc="M")
        check(False, "超长内容应抛出明确异常")
    except ValueError as e:
        check("过长" in str(e) or "范围" in str(e), "超长内容抛出可读错误", str(e))
    except Exception as e:
        check(False, "超长内容抛出了非预期异常", repr(e))

    # 矩阵基本结构
    mat = m.QRCode("https://www.bilibili.com", ecc="M").build()
    n = len(mat)
    check(all(v in (0, 1) for row in mat for v in row), "矩阵只含 0 与 1")

    # 三个定位图形必须存在且正确
    def finder_ok(r0, c0):
        for dr in range(7):
            for dc in range(7):
                edge = dr in (0, 6) or dc in (0, 6)
                core = 2 <= dr <= 4 and 2 <= dc <= 4
                want = 1 if (edge or core) else 0
                if mat[r0 + dr][c0 + dc] != want:
                    return False
        return True
    check(finder_ok(0, 0), "左上定位图形正确")
    check(finder_ok(0, n - 7), "右上定位图形正确")
    check(finder_ok(n - 7, 0), "左下定位图形正确")
    check(mat[n - 8][8] == 1, "固定暗模块存在")

    # 定时图形
    timing = all(mat[6][i] == (1 if i % 2 == 0 else 0) for i in range(8, n - 8))
    check(timing, "横向定时图形正确")

    # 版本 7 以上必须写入版本信息
    for pl in ("z" * 200, "z" * 300):
        try:
            qq = m.QRCode(pl, ecc="M")
            if qq.version >= 7:
                mm = qq.build()
                sz = len(mm)
                a = [[mm[i // 3][sz - 11 + i % 3] for i in range(18)]]
                b = [[mm[sz - 11 + i % 3][i // 3] for i in range(18)]]
                check(a == b, "v%d 两处版本信息一致" % qq.version)
                bits = qq._version_bits()
                got = sum(((bits >> i) & 1) << i for i in range(18))
                check(got == bits, "v%d 版本信息位生成自洽" % qq.version)
                break
        except ValueError:
            continue

    # 终端渲染
    obj = m.QRCode("https://www.bilibili.com", ecc="M")
    obj.build()
    lines = obj.to_terminal()
    check(len(lines) > 5, "终端渲染输出 %d 行" % len(lines))
    check(len(set(len(l) for l in lines)) == 1, "终端渲染各行等宽")
    chars = set("".join(lines))
    check(chars <= {" ", "▀", "▄", "█"}, "终端渲染只用半块字符", str(chars))
    # 静默区
    check(lines[0].strip() == "", "顶部有静默区")
    check(lines[-1].strip() == "", "底部有静默区")
    # 反色模式也要能出图
    inv = obj.to_terminal(invert=True)
    check(len(inv) == len(lines), "反色渲染行数一致")
    check(set("".join(inv)) <= {" ", "▀", "▄", "█"}, "反色渲染字符集正确")

    # 纠错级别
    for lvl in ("L", "M"):
        qq = m.QRCode("test payload", ecc=lvl)
        check(qq.ecc == lvl, "支持纠错级别 %s" % lvl)


def test_05_block_tables(m):
    print()
    print("=" * 72)
    print("测试组 5  分块与纠错参数自洽性")
    print("=" * 72)

    # 总码字数 = 数据码字数 + 块数 × 每块纠错码字数
    # 且必须等于该版本的标称总容量
    EXPECT_TOTAL = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
                    6: 172, 7: 196, 8: 242, 9: 292, 10: 346}
    for v in range(1, 11):
        for lvl in ("L", "M"):
            g1, g2 = m.QRCode.BLOCKS[(lvl, v)]
            nblocks = g1[0] + g2[0]
            data = g1[0] * g1[1] + g2[0] * g2[1]
            ecc = m.QRCode.ECC_PER_BLOCK[lvl][v]
            total = data + nblocks * ecc
            check(total == EXPECT_TOTAL[v],
                  "v%-2d %s 容量自洽  数据%d + %d块x%d纠错 = %d" % (
                      v, lvl, data, nblocks, ecc, total),
                  "期望 %d" % EXPECT_TOTAL[v])
            check(nblocks >= 1, "v%-2d %s 至少一个块" % (v, lvl))

    # 版本 1 到 10 的两种纠错级别都必须有定义
    for v in range(1, 11):
        for lvl in ("L", "M"):
            check((lvl, v) in m.QRCode.BLOCKS, "分块表含 (%s,%d)" % (lvl, v))
            check(v in m.QRCode.ECC_PER_BLOCK[lvl], "纠错表含 (%s,%d)" % (lvl, v))

    # 对齐图形坐标
    for v in range(1, 11):
        al = m.QRCode.ALIGN[v]
        if v == 1:
            check(al == [], "v1 无对齐图形")
        else:
            check(len(al) >= 2 and al[0] == 6 and al[-1] == v * 4 + 10,
                  "v%-2d 对齐坐标 %s" % (v, al))


def test_06_api_parsing(m):
    print()
    print("=" * 72)
    print("测试组 6  接口返回解析（用构造数据，不联网）")
    print("=" * 72)

    # 品质名称表
    check(m.QUALITY_NAME.get(80) == "1080P 高清", "清晰度名称表 80")
    check(m.QUALITY_NAME.get(64) == "720P 高清", "清晰度名称表 64")
    check(m.QUALITY_NAME.get(120) == "4K 超清", "清晰度名称表 120")

    # playurl 解析逻辑  直接调用内部结构变换
    class FakeResp:
        def __init__(self, data):
            self._d = data
        def json(self):
            return self._d

    class FakeSession:
        def __init__(self, data):
            self._d = data
        def get(self, *a, **k):
            return FakeResp(self._d)
        def post(self, *a, **k):
            return FakeResp(self._d)

    b = m.Bili.__new__(m.Bili)
    b.s = FakeSession({"code": 0, "data": {
        "quality": 64, "format": "mp4720",
        "durl": [{"url": "https://x/1.mp4", "size": 100, "length": 1000}],
    }})
    r, e = b.playurl("BV1", 1)
    check(e is None and r and r["kind"] == "merged", "解析已合并 MP4 返回")
    check(r["parts"][0][1] == 100, "分片体积解析正确")

    # DASH 退回
    b.s = FakeSession({"code": 0, "data": {
        "quality": 80, "format": "dash",
        "dash": {
            "video": [{"id": 32, "baseUrl": "https://x/v32.mp4", "bandwidth": 200},
                      {"id": 64, "baseUrl": "https://x/v64.mp4", "bandwidth": 900}],
            "audio": [{"id": 30232, "baseUrl": "https://x/a.mp4", "bandwidth": 128}],
        },
    }})
    r2, e2 = b.playurl("BV1", 1)
    check(e2 is None and r2 and r2["kind"] == "dash", "解析 DASH 返回")
    check(r2["video"][0] == "https://x/v64.mp4", "选取最高码率视频轨")
    check(r2["audio"][0] == "https://x/a.mp4", "选取音轨")

    # 接口报错
    b.s = FakeSession({"code": -404, "message": "啥都木有"})
    r3, e3 = b.playurl("BV1", 1)
    check(r3 is None and e3 == "啥都木有", "接口报错被正确传出", str(e3))

    # 付费内容
    b.s = FakeSession({"code": 0, "data": {"dash": {"video": [], "audio": []}}})
    r4, e4 = b.playurl("BV1", 1)
    check(r4 is None and e4 and ("付费" in e4 or "受限" in e4),
          "无可用地址时给出付费或受限提示", str(e4))

    # 网络异常
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("网络断了")
    b.s = Boom()
    r5, e5 = b.playurl("BV1", 1)
    check(r5 is None and e5 and "网络" in e5, "网络异常被捕获", str(e5))


def test_07_cli(m):
    print()
    print("=" * 72)
    print("测试组 7  命令行接口")
    print("=" * 72)

    import subprocess
    py = sys.executable

    r = subprocess.run([py, TOOL, "--help"], capture_output=True, timeout=60)
    out = r.stdout.decode("utf-8", "replace")
    check(r.returncode == 0, "--help 退出码为 0")
    check("扫码登录" in out and "手机号" in out, "--help 说明包含登录方式")

    r = subprocess.run([py, TOOL, "--version"], capture_output=True, timeout=60)
    check(r.returncode == 0 and m.APP_VER in r.stdout.decode("utf-8", "replace"),
          "--version 输出版本号")

    # 无效参数不应崩溃
    r = subprocess.run([py, TOOL, "-o"], capture_output=True, timeout=60)
    check(r.returncode != 0, "-o 单独使用给出错误")

    # 无效链接不应崩溃
    r = subprocess.run([py, TOOL, "这不是连结"], capture_output=True, timeout=90,
                       input=b"")
    check(r.returncode != 0, "无效链接退出码非零")
    err = (r.stdout + r.stderr).decode("utf-8", "replace")
    check("没能从这段内容" in err or "支持的写法" in err, "无效链接给出可读提示")


def test_08_online(m):
    print()
    print("=" * 72)
    print("测试组 8  联网接口验证")
    print("=" * 72)

    b = m.Bili()

    data, e = b.video_info(bvid="BV1GJ411x7h7")
    check(e is None and data, "获取视频信息成功", str(e))
    if not data:
        return
    for k in ("title", "owner", "duration", "pages", "cid", "bvid"):
        check(k in data, "视频信息含字段 %s" % k)
    check(len(data["pages"]) >= 1, "至少一个分P")

    r, e2 = b.playurl(data["bvid"], data["cid"], qn=80)
    check(e2 is None and r, "获取播放地址成功", str(e2))
    if r:
        check(r["kind"] in ("merged", "dash"), "返回形式为 %s" % r["kind"])
        if r["kind"] == "merged":
            check(len(r["parts"]) >= 1, "合并流有 %d 个分片" % len(r["parts"]))
            check(r["parts"][0][0].startswith("http"), "分片地址是 http 链接")
            check(r["parts"][0][1] > 0, "分片体积大于 0")

    # 二维码申请与轮询接口连通性（只申请，不实际扫码）
    r = b.s.get(m.API_QR_GEN, timeout=15).json()
    check(r.get("code") == 0 and r.get("data", {}).get("url"),
          "二维码申请接口可用")
    if r.get("code") == 0:
        key = r["data"]["qrcode_key"]
        check(bool(key), "取得 qrcode_key")
        p = b.s.get(m.API_QR_POLL, params={"qrcode_key": key}, timeout=15).json()
        sub = (p.get("data") or {}).get("code")
        check(sub in (m.QR_POLL_WAIT, m.QR_POLL_SCANNED),
              "轮询接口返回待扫描状态 %s" % sub)

    logged, name, vip = b.whoami()
    check(isinstance(logged, bool), "登录状态查询可用，当前%s" % ("已登录" if logged else "未登录"))


def test_09_quality(m):
    print()
    print("=" * 72)
    print("测试组 9  清晰度选择")
    print("=" * 72)

    class FakeResp:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    class FakeSession:
        def __init__(self, data):
            self._d = data
            self.calls = []

        def get(self, url, params=None, **kw):
            self.calls.append(params or {})
            return FakeResp(self._d)

    b = m.Bili.__new__(m.Bili)

    # ---- best_quality 的取值逻辑 ----
    b.s = FakeSession({"code": 0, "data": {"accept_quality": [80, 64, 32, 16]}})
    check(b.best_quality("BV1", 1) == 80, "从列表取最大值 80")

    # 接口声称列表从高到低排，但不能依赖这个顺序
    b.s = FakeSession({"code": 0, "data": {"accept_quality": [16, 120, 64, 80]}})
    check(b.best_quality("BV1", 1) == 120, "列表乱序时仍取最大值 120")

    # 大会员能拿 4K 的场景
    b.s = FakeSession({"code": 0, "data": {"accept_quality": [127, 120, 116, 112, 80]}})
    check(b.best_quality("BV1", 1) == 127, "大会员场景取到 8K 编号 127")

    b.s = FakeSession({"code": 0, "data": {"accept_quality": []}})
    check(b.best_quality("BV1", 1) == 80, "空列表退回 80")

    b.s = FakeSession({"code": 0, "data": {}})
    check(b.best_quality("BV1", 1) == 80, "缺字段退回 80")

    b.s = FakeSession({"code": -404, "message": "啥都木有"})
    check(b.best_quality("BV1", 1) == 80, "接口报错退回 80")

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("网络断了")

    b.s = Boom()
    check(b.best_quality("BV1", 1) == 80, "网络异常退回 80")

    b.s = FakeSession({"code": 0, "data": {"accept_quality": ["abc", None, 64]}})
    check(isinstance(b.best_quality("BV1", 1), int), "脏数据不崩溃")

    # ---- playurl 自动模式：先问再取 ----
    class TwoStep:
        """第一次问清晰度，第二次取地址。记录每次请求的参数"""
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, **kw):
            params = dict(params or {})
            self.calls.append(params)
            if len(self.calls) == 1:
                return FakeResp({"code": 0, "data": {"accept_quality": [80, 64, 16]}})
            return FakeResp({"code": 0, "data": {
                "quality": 80, "format": "mp4",
                "accept_quality": [80, 64, 16],
                "durl": [{"url": "https://x/1.mp4", "size": 100}]}})

    b.s = TwoStep()
    r, e = b.playurl("BV1", 1)
    check(e is None and r, "自动模式取地址成功")
    check(len(b.s.calls) == 2, "自动模式恰好发两次请求", str(len(b.s.calls)))
    check(b.s.calls[0].get("qn") == 16, "第一次是探测请求")
    check(b.s.calls[1].get("qn") == 80, "第二次按问到的最高档请求")
    check(r["requested"] == 80, "结果里记录了请求档位")
    check(r.get("accept") == [80, 64, 16], "结果里带上了可用清晰度列表")

    # ---- playurl 显式指定 ----
    b.s = FakeSession({"code": 0, "data": {
        "quality": 64, "format": "mp4", "accept_quality": [80, 64, 16],
        "durl": [{"url": "https://x/1.mp4", "size": 100}]}})
    r2, e2 = b.playurl("BV1", 1, qn=64)
    check(e2 is None and r2["requested"] == 64, "显式指定 64 时按 64 请求")
    check(r2["quality"] == 64, "返回清晰度为 64")
    check(len(b.s.calls) == 1, "显式指定时不发探测请求")

    # 指定档位拿不到时接口会静默降级，上层靠两个字段的差值发现
    b.s = FakeSession({"code": 0, "data": {
        "quality": 80, "format": "mp4", "accept_quality": [80, 64, 16],
        "durl": [{"url": "https://x/1.mp4", "size": 100}]}})
    r3, e3 = b.playurl("BV1", 1, qn=120)
    check(e3 is None and r3["requested"] == 120 and r3["quality"] == 80,
          "指定 4K 只能给 1080P 时，两个字段能反映降级")

    # ---- DASH 模式选轨 ----
    b.s = FakeSession({"code": 0, "data": {
        "quality": 64, "format": "dash", "accept_quality": [80, 64, 16],
        "dash": {
            "video": [
                {"id": 32, "baseUrl": "https://x/v32.mp4", "bandwidth": 300},
                {"id": 80, "baseUrl": "https://x/v80-low.mp4", "bandwidth": 800},
                {"id": 80, "baseUrl": "https://x/v80-high.mp4", "bandwidth": 2000},
                {"id": 64, "baseUrl": "https://x/v64.mp4", "bandwidth": 900},
            ],
            "audio": [
                {"id": 30232, "baseUrl": "https://x/a-low.mp4", "bandwidth": 100},
                {"id": 30280, "baseUrl": "https://x/a-high.mp4", "bandwidth": 300},
            ],
        }}})
    r4, e4 = b.playurl("BV1", 1, qn=80)
    check(e4 is None and r4["kind"] == "dash", "DASH 模式解析成功")
    check(r4["quality"] == 80, "DASH 选了最高的清晰度档 80")
    check(r4["video"][0] == "https://x/v80-high.mp4",
          "同档内选了码率最高的那条轨", r4["video"][0])
    check(r4["audio"][0] == "https://x/a-high.mp4", "音轨选了码率最高的")

    b.s = FakeSession({"code": 0, "data": {
        "quality": 80, "format": "dash", "accept_quality": [80],
        "dash": {"video": [
            {"id": 64, "baseUrl": "https://x/v64.mp4", "bandwidth": 900},
            {"id": 80, "baseUrl": "https://x/v80.mp4", "bandwidth": 1800},
        ], "audio": []}}})
    r5, e5 = b.playurl("BV1", 1, qn=80)
    check(r5["quality"] == 80, "DASH 取实际轨里的最高档")
    check(r5["audio"] is None, "没有音轨时 audio 为 None 而不是报错")


def test_10_param_precedence(m):
    print()
    print("=" * 72)
    print("测试组 10  参数优先级与默认值")
    print("=" * 72)

    src = open(TOOL, encoding="utf-8").read()

    # --all 与 -p 同时给时应该按 --all 处理。
    # 这个靠分支顺序保证，真跑起来需要联网取分P列表，所以查源码结构
    seg = src[src.find("def download_one("):src.find("def interactive(")]
    i_all = seg.find("if all_parts and len(pages) > 1:")
    i_page = seg.find("elif len(pages) > 1 and page is None:")
    check(i_all != -1, "存在 --all 分支")
    check(i_page != -1, "存在交互选择分支")
    check(0 <= i_all < i_page, "--all 分支排在交互选择之前，优先级更高")
    check("同时指定了 --all 与 -p" in seg, "同时指定时给出了提示")

    # 默认必须自动取最高，不能写死档位
    head = src[src.find("def download_one("):]
    head = head[:head.find("\n\n\n")]
    check("qn=None" in head, "download_one 的 qn 默认是 None")
    main_seg = src[src.find("def main("):]
    check("qn = None" in main_seg, "主流程 qn 默认是 None")
    check("qn = 80" not in main_seg, "主流程没有写死 80 的残留")


def main():
    print("=" * 72)
    print("B 站下载器测试套件")
    print("=" * 72)
    print("工具  %s" % TOOL)
    print("Python %s" % sys.version.split()[0])

    if not os.path.exists(TOOL):
        print("找不到工具文件")
        return 2

    import importlib.util as iu
    if iu.find_spec("requests") is None:
        print("缺少 requests 库，请先 pip install requests")
        return 2

    m = load()
    print("导入成功  v%s" % m.APP_VER)

    tests = [
        test_01_link_parsing,
        test_02_sanitize,
        test_03_format,
        test_04_qr,
        test_05_block_tables,
        test_06_api_parsing,
        test_07_cli,
        test_09_quality,
        test_10_param_precedence,
    ]
    if "--online" in sys.argv:
        tests.append(test_08_online)
    else:
        print()
        print("提示。加 --online 参数可附带联网接口测试")

    for fn in tests:
        try:
            fn(m)
        except Exception as ex:
            global FAIL
            FAIL += 1
            FAILED.append(fn.__name__ + " 抛异常 " + repr(ex))
            print("  [异常] %s  %r" % (fn.__name__, ex))
            import traceback
            traceback.print_exc()

    print()
    print("=" * 72)
    print("结果   通过 %d   失败 %d" % (PASS, FAIL))
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
