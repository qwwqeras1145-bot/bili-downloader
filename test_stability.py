# -*- coding: utf-8 -*-
"""
稳定性压测：专找会在真实使用中让工具崩掉或产生错误结果的问题

这里刻意不用 mock，而是用构造出来的真实数据流去跑，
因为 mock 会掩盖参数不匹配、类型错误这类问题。
"""
import os
import sys
import json
import shutil
import tempfile
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("b", os.path.join(HERE, "bili_dl.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FOUND = []


def probe(label, fn, expect_ok=True):
    """跑一个用例，记录是否抛异常"""
    try:
        r = fn()
        if expect_ok:
            print("  [正常] %s" % label)
        else:
            print("  [正常] %s  →  %r" % (label, r))
        return r
    except Exception as e:
        print("  [崩溃] %s" % label)
        print("         %s: %s" % (type(e).__name__, e))
        FOUND.append("%s  (%s)" % (label, type(e).__name__))
        return None


print("=" * 72)
print("稳定性压测")
print("=" * 72)

# ============================================================================
print()
print("一、接口返回脏数据时会不会崩")
print("=" * 72)


class R:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


class S:
    def __init__(self, d):
        self._d = d
        self.cookies = []

    def get(self, *a, **k):
        return R(self._d)


b = m.Bili.__new__(m.Bili)
b.s = S({"code": 0, "data": {"quality": 80, "format": "mp4",
                             "accept_quality": [80, 64, 16],
                             "durl": [{"url": "u", "size": 1}]}})

# accept_quality 里混入非数字
b.s = S({"code": 0, "data": {"quality": 80, "format": "mp4",
                             "accept_quality": ["80", None, 64],
                             "durl": [{"url": "u", "size": 1}]}})
probe("best_quality 遇到字符串/None 混入的清晰度列表",
      lambda: b.best_quality("BV1", 1))

# playurl 里 accept 含非数字，download_one 会把它转成整数再比较
b.s = S({"code": 0, "data": {"quality": 80, "format": "mp4",
                             "accept_quality": ["80", None],
                             "durl": [{"url": "u", "size": 1}]}})
r = probe("playurl 返回脏 accept 列表", lambda: b.playurl("BV1", 1, qn=80))
if r and r[0]:
    # 复现 download_one 里的处理方式：逐个转整数，转不动的丢掉
    def norm_accept(lst):
        out = []
        for x in lst:
            try:
                out.append(int(x))
            except Exception:
                continue
        return out

    probe("按 download_one 的方式归一 accept",
          lambda: norm_accept(r[0]["accept"]), expect_ok=False)

# owner 字段缺失
print()
b.s = S({"code": 0, "data": {"bvid": "BV1", "aid": 1, "title": "t",
                             "pages": [{"cid": 1, "part": "p"}],
                             "duration": 10}})
vi = probe("video_info 返回缺 owner 字段", lambda: b.video_info(bvid="BV1"))
if vi and vi[0]:
    # 复现 download_one 的取法：(data.get("owner") or {}).get("name") or "未知"
    probe("按 download_one 的方式取作者名",
          lambda: ((vi[0].get("owner") or {}).get("name") or "未知"),
          expect_ok=False)

print()
print("=" * 72)
print("二、下载中断时的行为")
print("=" * 72)

tmp = tempfile.mkdtemp(prefix="stab_")


class BrokenStream:
    """模拟下载到一半连接断掉"""

    def __init__(self, first_chunk, exc):
        self.first_chunk = first_chunk
        self.exc = exc
        self.status_code = 200
        self.headers = {"Content-Length": "1000"}

    def iter_content(self, chunk_size=1024):
        yield self.first_chunk
        raise self.exc

    def close(self):
        pass


class SessBroken:
    def __init__(self, resp):
        self.resp = resp

    def get(self, *a, **k):
        return self.resp


for exc_name, exc in (
    ("ConnectionError", __import__("requests").exceptions.ConnectionError("连接被重置")),
    ("ChunkedEncodingError", __import__("requests").exceptions.ChunkedEncodingError("传输中断")),
    ("Timeout", __import__("requests").exceptions.Timeout("读超时")),
):
    path = os.path.join(tmp, "brk_%s.mp4" % exc_name)
    sess = SessBroken(BrokenStream(b"A" * 400, exc))
    probe("下载中途抛 %s" % exc_name,
          (lambda p=path, s=sess: m.download_file(s, "http://x", p, "测试", 1000)),
          expect_ok=False)

print()
print("-- 下载到一半中断后，残留了什么 --")
for f in sorted(os.listdir(tmp)):
    p = os.path.join(tmp, f)
    print("  %-40s %d 字节" % (f, os.path.getsize(p)))

print()
print("=" * 72)
print("三、非交互环境下的输入处理")
print("=" * 72)

# 模拟 stdin 立即 EOF
import io
old_stdin = sys.stdin
try:
    sys.stdin = io.StringIO("")
    sys.stdin.isatty = lambda: False

    b2 = m.Bili.__new__(m.Bili)
    b2.s = S({"code": 0, "data": {"bvid": "BV1", "aid": 1, "title": "t",
                                  "owner": {"name": "a"},
                                  "pages": [{"cid": 1, "part": "p1"}, {"cid": 2, "part": "p2"}],
                                  "duration": 10}})
    b2.whoami = lambda: (False, "", 0)
    b2.playurl = lambda *a, **k: (None, "不实际下载")

    probe("download_one 多分P + stdin 为 EOF",
          lambda: m.download_one(b2, "BV1xx411c7mD", outdir=tmp))
finally:
    sys.stdin = old_stdin

print()
print("=" * 72)
print("四、输出目录参数解析的副作用")
print("=" * 72)

import subprocess
py = sys.executable
tool = os.path.join(HERE, "bili_dl.py")

# --whoami 是只读操作，不该创建目录
testdir = os.path.join(tmp, "should_not_exist_%d" % os.getpid())
if os.path.exists(testdir):
    shutil.rmtree(testdir, ignore_errors=True)
subprocess.run([py, tool, "--whoami", "-o", testdir],
               capture_output=True, timeout=120, input=b"")
print("  执行 %s --whoami -o <新目录>" % os.path.basename(tool))
if os.path.isdir(testdir):
    print("  [问题] 只读命令也创建了目录：%s" % testdir)
    FOUND.append("只读命令 --whoami 会创建输出目录")
else:
    print("  [正常] 没有创建目录")

print()
print("=" * 72)
print("五、异常数据的其它边界")
print("=" * 72)

# accept 为空列表时 max() 会怎样
probe("空 accept 列表取 max（有 if 保护）",
      lambda: max([]) if [] else None, expect_ok=False)

# sanitize 收到非字符串
probe("sanitize 收到 None", lambda: m.sanitize(None))
probe("sanitize 收到数字", lambda: m.sanitize(12345), expect_ok=False)

# fmt_dur / fmt_size 收到怪值
probe("fmt_dur 收到列表", lambda: m.fmt_dur([]), expect_ok=False)
probe("fmt_size 收到对象", lambda: m.fmt_size(object()), expect_ok=False)

# parse_link 收到非字符串
probe("parse_link 收到 None", lambda: m.parse_link(None))
probe("parse_link 收到数字", lambda: m.parse_link(12345))

print()
print("=" * 72)
print("压测结论")
print("=" * 72)
if FOUND:
    print("发现 %d 处会崩溃或行为异常：" % len(FOUND))
    for f in FOUND:
        print("  - " + f)
else:
    print("未发现崩溃点")
shutil.rmtree(tmp, ignore_errors=True)
