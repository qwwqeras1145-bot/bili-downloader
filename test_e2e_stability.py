# -*- coding: utf-8 -*-
"""
端到端稳定性验证

两个重点
  一、接口返回脏数据时，走完整的 download_one 流程会不会崩
  二、下载中断后再次运行，是不是真的只补剩余部分（续传的核心承诺）
"""
import os
import sys
import shutil
import tempfile
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("b", os.path.join(HERE, "bili_dl.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BAD = []


def check(cond, label, detail=""):
    print("  [%s] %s%s" % ("通过" if cond else "失败", label,
                           ("   " + detail) if detail and not cond else ""))
    if not cond:
        BAD.append(label)


print("=" * 72)
print("端到端稳定性验证")
print("=" * 72)

tmp = tempfile.mkdtemp(prefix="e2e_")

# ============================================================================
print()
print("一、接口返回脏数据，跑完整 download_one")
print("=" * 72)


class Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


class Sess:
    """按 URL 返回预置响应"""
    def __init__(self, mapping):
        self.mapping = mapping
        self.cookies = []

    def get(self, url, params=None, **kw):
        for key, val in self.mapping.items():
            if key in url:
                return Resp(val)
        return Resp({"code": -404, "message": "未预置"})

    def post(self, *a, **k):
        return Resp({"code": 0, "data": {}})


class FakeBili(m.Bili):
    def __init__(self, mapping):
        self.s = Sess(mapping)

    def whoami(self):
        return False, "", 0


# 场景：owner 缺失、title 缺失、accept 含脏数据，全部同时出现
dirty_view = {"code": 0, "data": {
    "bvid": "BV1xx411c7mD", "aid": 1,
    "pages": [{"cid": 111, "part": "第一集", "duration": 60}],
    "duration": 60,
    # 故意不给 title，也不给 owner
}}
dirty_play = {"code": 0, "data": {
    "quality": 80, "format": "mp4",
    "accept_quality": ["80", None, 64],
    "durl": [{"url": "https://fake/video.mp4", "size": 300, "length": 60000}],
}}
bili = FakeBili({"web-interface/view": dirty_view, "player/playurl": dirty_play})


class TinyStream:
    """提供一个刚好 300 字节的响应，配 fake 地址用"""
    status_code = 200
    headers = {"Content-Length": "300"}

    def iter_content(self, chunk_size=1024):
        yield b"V" * 300

    def close(self):
        pass


class TinySess(Sess):
    def get(self, url, params=None, **kw):
        if "fake/video" in url:
            return TinyStream()
        return super().get(url, params=params, **kw)


bili.s = TinySess({"web-interface/view": dirty_view, "player/playurl": dirty_play})

try:
    okd = m.download_one(bili, "BV1xx411c7mD", outdir=tmp)
    check(True, "脏数据下 download_one 未抛异常", "返回 %s" % okd)
    check(okd is True, "脏数据下仍能完成下载")
except Exception as e:
    import traceback
    check(False, "脏数据下 download_one 未抛异常", "%s: %s" % (type(e).__name__, e))
    traceback.print_exc()

files = [f for f in os.listdir(tmp) if os.path.isfile(os.path.join(tmp, f))]
check(len(files) >= 1, "确实产出了文件", str(files))
if files:
    p = os.path.join(tmp, files[0])
    check(os.path.getsize(p) == 300, "产物大小正确", "%d" % os.path.getsize(p))
    print("      文件名: %s" % files[0])

# ============================================================================
print()
print("二、中断后能否真正续传")
print("=" * 72)

tmp2 = tempfile.mkdtemp(prefix="resume_")
target = os.path.join(tmp2, "resume.mp4")
FULL = b"X" * 1000

# 第一次：下到 400 字节断掉
class DropStream:
    status_code = 200
    headers = {"Content-Length": "1000"}

    def iter_content(self, chunk_size=1024):
        yield FULL[:400]
        raise __import__("requests").exceptions.ConnectionError("断了")

    def close(self):
        pass


class OneShot:
    def __init__(self, resp):
        self.resp = resp
        self.seen = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.seen.append(dict(headers or {}))
        return self.resp


s1 = OneShot(DropStream())
ok1, got1 = m.download_file(s1, "http://x/v", target, "第一次", 1000)
check(ok1 is False, "第一次下载判定为失败")
check(got1 == 400, "第一次拿到 400 字节", str(got1))
check(not os.path.exists(target), "没有产出残缺的成品")
check(os.path.exists(target + ".part"), "保留了 .part 供续传")
check(os.path.getsize(target + ".part") == 400, "残留大小正确")

# 第二次：模拟服务器按 Range 只回剩下的 600 字节
class ResumeStream:
    status_code = 206
    headers = {"Content-Length": "600"}

    def iter_content(self, chunk_size=1024):
        yield FULL[400:]

    def close(self):
        pass


class Sess2:
    def __init__(self):
        self.range_header = None

    def get(self, url, headers=None, stream=False, timeout=None):
        self.range_header = (headers or {}).get("Range")
        return ResumeStream()


s2 = Sess2()
ok2, got2 = m.download_file(s2, "http://x/v", target, "第二次", 1000)
check(s2.range_header == "bytes=400-", "第二次带上了正确的 Range 头",
      str(s2.range_header))
check(ok2 is True, "第二次下载成功")
check(os.path.exists(target), "产出了成品")
if os.path.exists(target):
    size = os.path.getsize(target)
    check(size == 1000, "成品大小完整 1000 字节", str(size))
    with open(target, "rb") as f:
        data = f.read()
    check(data == FULL, "内容与预期完全一致（没有接错位置）")
    check(data[:400] == b"X" * 400 and data[400:] == b"X" * 600,
          "前后两段拼接正确")
check(not os.path.exists(target + ".part"), ".part 已被清理")

# ============================================================================
print()
print("三、接口不提供 size 时会不会误判成功")
print("=" * 72)

tmp3 = tempfile.mkdtemp(prefix="nosize_")
t3 = os.path.join(tmp3, "nosize.mp4")


class NoSizeDrop:
    """不给 Content-Length，且中途断掉"""
    status_code = 200
    headers = {}

    def iter_content(self, chunk_size=1024):
        yield b"Y" * 100
        raise __import__("requests").exceptions.ChunkedEncodingError("断了")

    def close(self):
        pass


class SessNS:
    def get(self, url, headers=None, stream=False, timeout=None):
        return NoSizeDrop()


ok3, got3 = m.download_file(SessNS(), "http://x/v", t3, "无长度", 0)
check(ok3 is False, "不给长度且中断时判定为失败")
check(not os.path.exists(t3), "没有产出残缺成品")
check(os.path.exists(t3 + ".part"), "保留 .part")

# ============================================================================
print()
print("=" * 72)
if BAD:
    print("失败 %d 项：" % len(BAD))
    for x in BAD:
        print("  - " + x)
else:
    print("全部通过")
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(tmp2, ignore_errors=True)
shutil.rmtree(tmp3, ignore_errors=True)
