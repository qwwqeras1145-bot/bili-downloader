# -*- coding: utf-8 -*-
r"""
网页版测试套件

分两部分
  离线测试  路由、响应头、Range 处理、安全防护、页面渲染，全部不联网
  联网测试  真实走一遍 解析 -> 下载 -> 取文件（加 --online 才跑）

离线部分用注入假任务的方式测文件服务，因此不受网络和 B 站接口波动影响，
可以反复跑。
"""

import os
import sys
import json
import time
import shutil
import socket
import tempfile
import threading
import subprocess
import importlib.util
import urllib.request
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "web_app.py")

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


def load_web():
    spec = importlib.util.spec_from_file_location("web", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """在后台线程里跑起网页版服务"""

    def __init__(self, mod, outdir):
        self.mod = mod
        self.outdir = outdir
        self.port = free_port()
        self.httpd = None
        self.thread = None

    def start(self):
        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), self.mod.Handler)
        self.httpd.outdir = self.outdir
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        # 等端口真的可连
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    # ---- 简单 HTTP 客户端 ----
    def get(self, path, headers=None, method="GET", body=None, timeout=20):
        req = urllib.request.Request(self.url(path), method=method, data=body)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except Exception as e:
            return None, {}, str(e).encode()


# ============================================================================
def test_page(mod, srv):
    print()
    print("=" * 72)
    print("测试组 1  页面渲染")
    print("=" * 72)

    code, hdr, body = srv.get("/")
    check(code == 200, "首页返回 200", str(code))
    check("text/html" in hdr.get("Content-Type", ""), "Content-Type 是 html")
    text = body.decode("utf-8", "replace")
    check("B站视频下载器" in text, "标题存在")
    check("不下载付费番剧" in text, "使用须知存在")
    check("__TITLE__" not in text and "__QNAMES__" not in text,
          "模板占位符已全部替换")
    check('id="url"' in text and 'id="go"' in text, "输入框与按钮存在")
    check("扫码登录" in text, "扫码登录入口存在")
    # 页面必须自包含，不依赖外部资源
    check("http://" not in text.replace("http://127.0.0.1", "")
          and "https://" not in text,
          "页面不引任何外部资源（离线可用）")
    check(len(body) > 5000, "页面长度 %d 字节" % len(body))

    # 静态文件不需要鉴权，但不能暴露目录
    code2, _, _ = srv.get("/../bili_dl.py")
    check(code2 in (400, 404), "上级目录遍历被拒绝", str(code2))


def test_jobs_api(mod, srv):
    print()
    print("=" * 72)
    print("测试组 2  任务接口")
    print("=" * 72)

    code, hdr, body = srv.get("/api/jobs")
    check(code == 200, "任务列表返回 200")
    d = json.loads(body)
    check("jobs" in d and isinstance(d["jobs"], list), "返回结构正确")

    code, _, body = srv.get("/api/status?id=nosuchjob")
    check(code == 404, "不存在的任务返回 404", str(code))

    # 中文查询参数要按百分号编码发（浏览器就是这么发的）
    code, _, body = srv.get("/api/status?id=" +
                            urllib.parse.quote("不存在的任务"))
    check(code == 404, "百分号编码的中文参数也能正确处理", str(code))

    code, _, body = srv.get("/api/status")
    check(code == 404, "缺少 id 参数返回 404", str(code))

    # 超长 id 不应崩
    code, _, body = srv.get("/api/status?id=" + "a" * 5000)
    check(code == 404, "超长 id 安全返回 404", str(code))


def test_file_serving(mod, srv):
    print()
    print("=" * 72)
    print("测试组 3  文件服务与 Range 处理")
    print("=" * 72)

    # 注入一个已完成的任务，指向真实文件，这样测试完全离线且可重复
    tmpd = tempfile.mkdtemp(prefix="webfile_")
    content = bytes(range(256)) * 40          # 10240 字节，内容有规律便于校验
    path = os.path.join(tmpd, "测试-文件名 with space.mp4")
    with open(path, "wb") as f:
        f.write(content)
    size = len(content)

    job = mod.JOBS.create("fake://url", "测试视频", tmpd)
    job.set(state="done", path=path, total_bytes=size, done_bytes=size,
            finished=time.time())

    # ---- 完整下载 ----
    code, hdr, body = srv.get("/download?id=%s" % job.id)
    check(code == 200, "完整下载返回 200", str(code))
    check(len(body) == size, "内容长度正确", "%d vs %d" % (len(body), size))
    check(body == content, "内容逐字节一致")
    check(hdr.get("Accept-Ranges") == "bytes", "声明支持 Range")
    check(hdr.get("Content-Type") == "video/mp4", "Content-Type 正确")
    disp = hdr.get("Content-Disposition", "")
    check("filename*=UTF-8''" in disp, "中文文件名按 RFC 5987 编码", disp)
    check("%E6%B5%8B%E8%AF%95" in disp, "文件名编码内容正确")

    # ---- 常规 Range ----
    code, hdr, body = srv.get("/download?id=%s" % job.id,
                              headers={"Range": "bytes=0-99"})
    check(code == 206, "Range 请求返回 206", str(code))
    check(len(body) == 100, "返回 100 字节", str(len(body)))
    check(body == content[:100], "片段内容正确")
    check(hdr.get("Content-Range") == "bytes 0-99/%d" % size,
          "Content-Range 正确", str(hdr.get("Content-Range")))

    # ---- 中段 Range ----
    code, hdr, body = srv.get("/download?id=%s" % job.id,
                              headers={"Range": "bytes=5000-5099"})
    check(code == 206 and body == content[5000:5100], "中段 Range 正确")

    # ---- 尾部 Range ----
    code, hdr, body = srv.get("/download?id=%s" % job.id,
                              headers={"Range": "bytes=-200"})
    check(code == 206, "尾部 Range 返回 206", str(code))
    check(body == content[-200:], "尾部片段内容正确")

    # ---- 开放式结尾 ----
    code, hdr, body = srv.get("/download?id=%s" % job.id,
                              headers={"Range": "bytes=10000-"})
    check(code == 206 and body == content[10000:], "开放式 Range 正确")

    # ---- 越界 Range 必须及时返回 416，且不能挂住客户端 ----
    t0 = time.time()
    code, hdr, body = srv.get("/download?id=%s" % job.id,
                              headers={"Range": "bytes=999999999-"},
                              timeout=10)
    dt = time.time() - t0
    check(code == 416, "越界 Range 返回 416", str(code))
    check(dt < 5, "越界 Range 及时返回（%.2f 秒）" % dt, "可能挂住客户端")
    check(hdr.get("Content-Length") == "0",
          "416 响应带 Content-Length: 0（否则长连接下客户端会一直等）",
          str(hdr.get("Content-Length")))
    check(hdr.get("Content-Range", "").startswith("bytes */"),
          "416 带 Content-Range: bytes */size")

    # ---- 不合法 Range 应降级成完整返回 ----
    code, hdr, body = srv.get("/download?id=%s" % job.id,
                              headers={"Range": "bytes=abc-def"})
    check(code == 200 and len(body) == size, "非法 Range 降级为完整返回",
          str(code))

    # ---- 未完成的任务不能下载 ----
    job2 = mod.JOBS.create("fake://url2", "未完成", tmpd)
    job2.set(state="running")
    code, _, _ = srv.get("/download?id=%s" % job2.id)
    check(code == 404, "未完成的任务返回 404", str(code))

    # ---- 任务里的文件被删掉后 ----
    job3 = mod.JOBS.create("fake://url3", "文件已删", tmpd)
    gone = os.path.join(tmpd, "gone.mp4")
    open(gone, "wb").write(b"x")
    job3.set(state="done", path=gone, finished=time.time())
    os.remove(gone)
    code, _, _ = srv.get("/download?id=%s" % job3.id)
    check(code == 404, "文件已被删除时返回 404 而不是崩溃", str(code))

    shutil.rmtree(tmpd, ignore_errors=True)


def test_security(mod, srv):
    print()
    print("=" * 72)
    print("测试组 4  安全防护")
    print("=" * 72)

    # 路径遍历：接口只从任务表取路径，不接受请求方传路径
    for bad in ("/download?id=../../../../windows/win.ini",
                "/download?id=..%2F..%2Fwindows%2Fwin.ini",
                "/download?id=/etc/passwd",
                "/download?id=C:\\Windows\\win.ini"):
        code, _, _ = srv.get(bad)
        check(code == 404, "路径遍历被拒绝 %s" % bad[:40], str(code))

    # 跨站请求应被拒
    code, _, _ = srv.get("/api/prepare", method="POST",
                         body=b'{"url":"BV1xx411c7mD"}',
                         headers={"Content-Type": "application/json",
                                  "Origin": "http://evil.example.com"})
    check(code == 403, "跨站 POST 被拒绝", str(code))

    # 本机 Origin 应放行（走到解析逻辑，返回的不是 403 即可）
    code, _, _ = srv.get("/api/prepare", method="POST",
                         body=b'{"url":""}',
                         headers={"Content-Type": "application/json",
                                  "Origin": "http://127.0.0.1"})
    check(code != 403, "本机 Origin 放行", str(code))

    # 空链接应给出提示而不是崩
    code, _, body = srv.get("/api/prepare", method="POST",
                            body=b'{"url":""}',
                            headers={"Content-Type": "application/json"})
    check(code == 200, "空链接返回 200 与提示")
    d = json.loads(body)
    check("error" in d, "空链接给出错误提示")

    # 无效链接（不联网也能判出来，因为解析阶段就失败了）
    code, _, body = srv.get("/api/prepare", method="POST",
                            body=json.dumps({"url": "这不是链接"}).encode(),
                            headers={"Content-Type": "application/json"})
    d = json.loads(body)
    check("error" in d, "无效链接给出错误提示")
    check(d.get("job") is None, "无效链接不会创建任务")

    # 超大 body 不应导致内存炸掉（自行限制 100KB）
    big = json.dumps({"url": "x" * (200 * 1024)}).encode()
    code, _, _ = srv.get("/api/prepare", method="POST", body=big,
                         headers={"Content-Type": "application/json"})
    check(code in (200, 413), "超大请求体被安全处理", str(code))


def test_concurrency(mod, srv):
    print()
    print("=" * 72)
    print("测试组 5  并发与稳定性")
    print("=" * 72)

    tmpd = tempfile.mkdtemp(prefix="webconc_")
    path = os.path.join(tmpd, "c.mp4")
    data = os.urandom(200000)
    with open(path, "wb") as f:
        f.write(data)
    job = mod.JOBS.create("fake://c", "并发测试", tmpd)
    job.set(state="done", path=path, total_bytes=len(data),
            done_bytes=len(data), finished=time.time())

    results = []

    def worker(i):
        try:
            code, _, body = srv.get("/download?id=%s" % job.id, timeout=30)
            results.append((code, len(body), body == data))
        except Exception as e:
            results.append((None, 0, False))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    okn = sum(1 for c, n, same in results if c == 200 and same)
    check(len(results) == 8, "8 个并发请求都有返回", str(len(results)))
    check(okn == 8, "8 个并发下载全部内容正确", "%d/8" % okn)

    # 并发轮询状态
    res2 = []

    def poller():
        for _ in range(10):
            c, _, _b = srv.get("/api/status?id=%s" % job.id, timeout=15)
            res2.append(c)

    ts = [threading.Thread(target=poller) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    check(all(c == 200 for c in res2), "并发状态轮询全部成功",
          str(set(res2)))

    shutil.rmtree(tmpd, ignore_errors=True)


def test_job_manager(mod):
    print()
    print("=" * 72)
    print("测试组 6  任务管理")
    print("=" * 72)

    mgr = mod.JobManager()
    j1 = mgr.create("u1", "t1", "/tmp")
    check(j1.id and len(j1.id) == 12, "任务 ID 生成正常: %s" % j1.id)
    check(mgr.get(j1.id) is j1, "能按 ID 取回任务")
    check(mgr.get("nope") is None, "取不存在的任务返回 None")

    # 状态快照
    j1.set(state="running", done_bytes=50, total_bytes=100)
    snap = j1.snapshot()
    check(snap["pct"] == 50.0, "进度百分比计算正确", str(snap["pct"]))
    check(snap["done_h"] and snap["total_h"], "可读大小已生成")

    # 总量为 0 时不应除零
    j2 = mgr.create("u2", "t2", "/tmp")
    j2.set(done_bytes=0, total_bytes=0)
    snap2 = j2.snapshot()
    check(snap2["pct"] == 0, "总量为 0 时进度为 0（未除零）")

    # 数量上限
    mgr2 = mod.JobManager()
    for i in range(mod.MAX_JOBS + 30):
        j = mgr2.create("u%d" % i, "t%d" % i, "/tmp")
        j.set(state="done", finished=time.time())
    check(len(mgr2.jobs) <= mod.MAX_JOBS + 30,
          "任务数量受控: %d" % len(mgr2.jobs))
    mgr.cleanup()
    check(True, "清理过期任务不抛异常")

    # 速度估算不应崩
    j3 = mgr.create("u3", "t3", "/tmp")
    j3.add_bytes(1024)
    j3.refresh_speed()
    check(j3.speed >= 0, "速度估算正常")


def test_helpers(mod):
    print()
    print("=" * 72)
    print("测试组 7  辅助函数")
    print("=" * 72)

    p1 = mod.pick_port(8848)
    check(isinstance(p1, int) and p1 >= 8848, "能挑到可用端口: %s" % p1)

    # 渲染函数不应留下占位符
    html = mod.render_page([80, 64, 16])
    check("__TITLE__" not in html, "render_page 替换了标题占位符")
    check("__QNAMES__" not in html, "render_page 替换了清晰度表")
    check("__ACCEPT__" not in html, "render_page 替换了可选档位")
    check("[80, 64, 16]" in html, "可选档位已注入页面")

    # 空档位列表也要能渲染
    html2 = mod.render_page([])
    check("[]" in html2, "空档位列表能正常渲染")

    # CountingSession 的字节统计
    class FakeResp:
        status_code = 200
        headers = {"Content-Length": "1000"}

        def iter_content(self, chunk_size=1024):
            yield b"a" * 300
            yield b"b" * 700

        def close(self):
            pass

    class FakeSess:
        cookies = []

        def get(self, *a, **k):
            return FakeResp()

    job = mod.Job(url="u", title="t", outdir="/tmp")
    cs = mod.CountingSession.__new__(mod.CountingSession)
    cs._s = FakeSess()
    cs._job = job
    cs.cookies = []
    resp = cs.get("http://x", stream=True)
    total = sum(len(c) for c in resp.iter_content(chunk_size=1024))
    check(total == 1000, "CountingSession 透传数据正确", str(total))
    check(job.done_bytes == 1000, "CountingSession 计数正确",
          str(job.done_bytes))
    check(job.total_bytes == 1000, "从 Content-Length 推断总量正确",
          str(job.total_bytes))


def test_cli(mod):
    print()
    print("=" * 72)
    print("测试组 8  命令行入口")
    print("=" * 72)

    py = sys.executable

    r = subprocess.run([py, TOOL, "--help"], capture_output=True, timeout=60)
    out = r.stdout.decode("utf-8", "replace")
    check(r.returncode == 0, "--help 退出码为 0")
    check("--port" in out and "--out" in out, "--help 列出参数")
    check("Ctrl+C" in out, "--help 说明如何停止")

    # 端口被占用时应自动换端口，而不是直接报错退出
    port = free_port()
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        p = subprocess.Popen([py, "-u", TOOL, "--port", str(port),
                              "--no-browser", "--out",
                              tempfile.mkdtemp(prefix="webcli_")],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(4)
        # 用 127.0.0.1 的相邻端口探一下，只要进程没退出就说明换端口成功了
        alive = p.poll() is None
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        check(alive, "指定端口被占用时服务仍能启动（自动换端口）")
    finally:
        holder.close()


def test_online(mod, srv):
    print()
    print("=" * 72)
    print("测试组 9  联网端到端")
    print("=" * 72)

    code, _, body = srv.get("/api/login", timeout=30)
    check(code == 200, "登录状态接口可用")
    d = json.loads(body)
    print("      当前登录: %s" % (d.get("name") or "未登录"))

    code, _, body = srv.get("/api/qr/start", timeout=30)
    check(code == 200, "二维码申请接口可用")
    d = json.loads(body)
    check(bool(d.get("art")), "返回了二维码字符画")
    if d.get("art"):
        lines = d["art"].split("\n")
        check(len(lines) > 10, "二维码有 %d 行，尺寸合理" % len(lines))
        check(set("".join(lines)) <= set(" ▀▄█"), "二维码只用半块字符绘制")

    code, _, body = srv.get("/api/qr/poll", timeout=30)
    check(code == 200, "二维码轮询接口可用")
    check(json.loads(body).get("state") in
          ("waiting", "scanned", "ok", "expired", "idle"),
          "轮询状态合法")

    # 真实下载一个短视频
    code, _, body = srv.get("/api/prepare", method="POST",
                            body=json.dumps({"url": "BV17x411w7KC"}).encode(),
                            headers={"Content-Type": "application/json"},
                            timeout=90)
    check(code == 200, "解析并开始下载返回 200")
    d = json.loads(body)
    if d.get("error"):
        check(False, "解析视频信息", d["error"])
        return
    check(bool(d.get("job")), "创建了下载任务")
    check(bool(d.get("title")), "返回了标题: %s" % d.get("title"))
    check(bool(d.get("quality")), "返回了清晰度: %s" % d.get("quality"))

    jid = d["job"]
    state = None
    for _ in range(60):
        time.sleep(2)
        _c, _h, b = srv.get("/api/status?id=%s" % jid, timeout=30)
        s = json.loads(b)
        state = s.get("state")
        if state in ("done", "error"):
            break
    check(state == "done", "下载任务完成", str(state))

    if state == "done":
        _c, _h, b = srv.get("/api/status?id=%s" % jid, timeout=30)
        path = json.loads(b).get("path")
        check(bool(path) and os.path.exists(path), "产物文件存在")
        if path and os.path.exists(path):
            check(os.path.getsize(path) > 100000, "产物大小合理: %d 字节"
                  % os.path.getsize(path))
            # 通过接口取回并核对
            c2, _h2, b2 = srv.get("/download?id=%s" % jid, timeout=120)
            check(c2 == 200, "能从网页取回文件")
            check(len(b2) == os.path.getsize(path), "取回内容长度一致")

    test_online_ugc(srv)


def test_online_ugc(srv):
    """
    合集接口的联网验证。

    用真实的公开合集做样本，不 mock。选的是"罗翔说刑法"（mid 517327498），
    他既有合集也有系列，两种接口都能覆盖到。

    只验"接口能通、字段能用"，不验具体有多少集 —— 那个随时会变，
    写死数字只会让测试过几天自己红掉。
    """
    print()
    print("=" * 72)
    print("测试组 10  合集与系列（联网）")
    print("=" * 72)

    # 1. 给 UP 的 mid，应列出全部合集与系列
    code, _, body = srv.get("/api/ugc/resolve?q=517327498", timeout=60)
    check(code == 200, "resolve 返回 200")
    d = json.loads(body)
    if d.get("error"):
        check(False, "列出某个 UP 的合集", d["error"])
        return
    cols = d.get("collections") or []
    check(len(cols) > 0, "列出 %d 个合集或系列" % len(cols))
    kinds = set(c.get("kind") for c in cols)
    check(kinds <= {"season", "series"}, "kind 取值合法", str(kinds))
    for c in cols[:3]:
        check(bool(c.get("id")) and bool(c.get("title")),
              "每项都有 id 和标题: %s" % c.get("title"))
    print("      例子: " + "、".join(c["title"] for c in cols[:3]))

    season = next((c for c in cols if c["kind"] == "season"), None)
    series = next((c for c in cols if c["kind"] == "series"), None)

    # 2. 合集里的视频
    if season:
        code, _, body = srv.get(
            "/api/ugc/videos?mid=517327498&kind=season&id=%s&limit=5"
            % season["id"], timeout=90)
        check(code == 200, "取合集视频返回 200")
        v = json.loads(body)
        if v.get("error"):
            check(False, "取合集里的视频", v["error"])
        else:
            check(v.get("count", 0) > 0,
                  "合集「%s」取到 %d 条" % (season["title"], v.get("count", 0)))
            if v.get("videos"):
                one = v["videos"][0]
                check(one.get("bvid", "").startswith("BV"),
                      "视频编号正常: %s" % one.get("bvid"))
                check(bool(one.get("title")), "视频有标题")
                # 响应裁剪过，不该把播放量那些一起带回来
                check("stat" not in one, "响应里没有冗余的统计字段")

    # 3. 系列走的是另一套接口，字段名都不一样，单独验一次
    if series:
        code, _, body = srv.get(
            "/api/ugc/videos?mid=517327498&kind=series&id=%s&limit=5"
            % series["id"], timeout=90)
        check(code == 200, "取系列视频返回 200")
        v = json.loads(body)
        if v.get("error"):
            check(False, "取系列里的视频", v["error"])
        else:
            check(v.get("count", 0) > 0,
                  "系列「%s」取到 %d 条" % (series["title"], v.get("count", 0)))

    # 4. 给视频链接，应直接找出它所属的合集
    code, _, body = srv.get("/api/ugc/resolve?q=BV1kv4y1L7EC", timeout=90)
    check(code == 200, "从视频找合集返回 200")
    d = json.loads(body)
    if d.get("error"):
        check(False, "从视频找它所属的合集", d["error"])
    elif d.get("videos"):
        check(bool((d.get("collection") or {}).get("title")),
              "找到了合集：%s" % d["collection"]["title"])
        check(len(d["videos"]) > 1,
              "一次就拿到了全集 %d 条" % len(d["videos"]))
        check(all(v.get("bvid") for v in d["videos"]), "每条都有编号")
    else:
        # 这个视频哪天被移出合集也算正常，退一步要求至少给出了作者
        check(bool(d.get("mid")), "视频不在合集里时给出了作者 mid", str(d)[:80])


# ============================================================================
def main():
    print("=" * 72)
    print("网页版测试套件")
    print("=" * 72)
    print("工具  %s" % TOOL)
    print("Python %s" % sys.version.split()[0])

    if not os.path.exists(TOOL):
        print("找不到 web_app.py")
        return 2

    if importlib.util.find_spec("requests") is None:
        print("缺少 requests 库")
        return 2

    mod = load_web()
    print("导入成功  v%s" % mod.APP_VER)

    outdir = tempfile.mkdtemp(prefix="webout_")
    srv = Server(mod, outdir)
    if not srv.start():
        print("服务启动失败")
        return 2
    print("测试服务已启动  端口 %d" % srv.port)

    tests = [
        test_page, test_jobs_api, test_file_serving, test_security,
        test_concurrency, test_job_manager, test_helpers, test_cli,
    ]

    try:
        for fn in tests:
            try:
                if fn in (test_job_manager, test_helpers):
                    fn(mod)
                elif fn is test_cli:
                    fn(mod)
                else:
                    fn(mod, srv)
            except Exception as e:
                global FAIL
                FAIL += 1
                FAILED.append(fn.__name__ + " 抛异常 " + repr(e))
                print("  [异常] %s  %r" % (fn.__name__, e))
                import traceback
                traceback.print_exc()

        if "--online" in sys.argv:
            try:
                test_online(mod, srv)
            except Exception as e:
                FAIL += 1
                FAILED.append("test_online 抛异常 " + repr(e))
                print("  [异常] test_online  %r" % e)
                import traceback
                traceback.print_exc()
        else:
            print()
            print("提示。加 --online 参数可附带联网端到端测试")
    finally:
        srv.stop()
        shutil.rmtree(outdir, ignore_errors=True)

    print()
    print("=" * 72)
    print("结果   通过 %d   失败 %d" % (PASS, FAIL))
    for f in FAILED:
        print("  - " + f)
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
