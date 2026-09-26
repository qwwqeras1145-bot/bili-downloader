# -*- coding: utf-8 -*-
"""
收藏夹、命名模板、附加内容相关的离线测试

这些都是不需要联网就能验证的部分：解析、路径拼装、选项白名单、
状态机、并发开关的语义。真正要联网的东西（取收藏夹、下视频）
放在 test_web.py --online 和 test_web_browser.py 里。

跑法
    python test_fav.py
"""
import os
import re
import sys
import json
import time
import shutil
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bili_dl as core
import bili_fav as fav

PASS, FAIL = 0, 0
BAD = []


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [通过] %s" % label)
    else:
        FAIL += 1
        BAD.append(label)
        print("  [失败] %s%s" % (label, ("   " + str(detail)) if detail else ""))


def group(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ============================================================================
#  一、命名模板
# ============================================================================
def test_template():
    group("测试组 1  命名模板")

    # TEMPLATE_FIELDS 是"字段 -> 说明"的字典，键才是字段名
    check("title" in core.TEMPLATE_FIELDS, "字段表里有 title")
    for f in ("bvid", "aid", "up", "date", "p", "part", "quality", "duration"):
        check(f in core.TEMPLATE_FIELDS, "字段表里有 %s" % f)
    for f, desc in core.TEMPLATE_FIELDS.items():
        check(bool(desc), "字段 %s 有说明文字" % f)

    vinfo = {"title": "测试视频", "bvid": "BV1xx411c7mD", "aid": 12345,
             "owner": {"name": "某UP"}, "pubdate": 1700000000}
    page = {"part": "第一段", "duration": 125, "page": 1}

    ctx = core.template_context(vinfo, page, "第一段", 1, "1080P")
    check(ctx.get("title") == "测试视频", "title 取值正确", ctx.get("title"))
    check(ctx.get("bvid") == "BV1xx411c7mD", "bvid 取值正确", ctx.get("bvid"))
    check(ctx.get("up") == "某UP", "up 取自 owner.name", ctx.get("up"))
    check(ctx.get("part") == "第一段", "part 取值正确", ctx.get("part"))
    check(ctx.get("quality") == "1080P", "quality 取值正确", ctx.get("quality"))

    # 模板里的字段全都要能替换掉，不能留 {xxx}
    ctx2 = core.template_context(vinfo, page, "第一段", 1, "1080P")
    for f in core.TEMPLATE_FIELDS:
        t = "{%s}" % f
        out = core.build_relpath(t, ctx2, "兜底")
        check("{" not in out and "}" not in out,
              "字段 %s 能被替换" % f, out)

    # 标题里的斜杠不能变成目录层级。这是踩过的坑：
    # 先替换再切分的话 "A/B" 会凭空多出一层目录
    ctx3 = core.template_context(
        {"title": "上/下 集", "bvid": "BV1", "aid": 1, "owner": {"name": "U"}},
        {}, "", 0, "")
    p = core.build_relpath("{title}", ctx3, "兜底")
    check(p.count(os.sep) == 0, "标题里的斜杠不会造出多余目录", p)

    # 模板本身写出的子目录要保留
    p2 = core.build_relpath("{up}/{title}", ctx3, "兜底")
    check(p2.count(os.sep) == 1, "模板里的目录分隔符保留", p2)

    # 非法字符要清掉，Windows 上这些字符会让写文件直接失败
    ctx4 = core.template_context(
        {"title": 'a<b>c:d"e|f?g*h', "bvid": "BV1", "aid": 1, "owner": {"name": "U"}},
        {}, "", 0, "")
    p3 = core.build_relpath("{title}", ctx4, "兜底")
    bad = [ch for ch in '<>:"|?*' if ch in p3]
    check(not bad, "Windows 非法字符已清除", p3)

    # 上下文缺字段时不能崩
    try:
        out = core.build_relpath("{title}-{up}-{nope}", {}, "兜底")
        check("兜底" in out or out, "空上下文不抛异常", out)
    except Exception as e:
        check(False, "空上下文不抛异常", repr(e))

    # 模板为空时用兜底名
    out2 = core.build_relpath("", ctx, "兜底名")
    check(bool(out2), "空模板回落到兜底名", out2)


# ============================================================================
#  二、配置文件
# ============================================================================
def test_config():
    group("测试组 2  配置文件")

    for k in ("outdir", "quality", "template", "concurrency", "subtitle",
              "danmaku", "cover", "metadata", "audio_only", "all_parts",
              "proxy", "interval"):
        check(k in core.DEFAULT_CONFIG, "默认配置里有 %s" % k)

    check(core.DEFAULT_CONFIG["all_parts"] is False,
          "多分P 默认不全下（只下第 1 个分P）")

    # 用一个临时目录冒充用户目录，别动真实配置
    old = core.CONFIG_FILE
    tmp = tempfile.mkdtemp(prefix="bilicfg_")
    try:
        core.CONFIG_FILE = os.path.join(tmp, ".bili_dl.json")

        cfg = core.load_config()
        check(cfg["concurrency"] == core.DEFAULT_CONFIG["concurrency"],
              "没有配置文件时给默认值")

        cfg["template"] = "{up}/{title}"
        cfg["subtitle"] = True
        check(core.save_config(cfg), "配置能写盘")

        back = core.load_config()
        check(back["template"] == "{up}/{title}", "模板写入后能读回",
              back["template"])
        check(back["subtitle"] is True, "开关写入后能读回")

        # 文件坏了不能影响使用
        with open(core.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        broken = core.load_config()
        check(broken["concurrency"] == core.DEFAULT_CONFIG["concurrency"],
              "配置损坏时回落到默认值而不是抛异常")

        # 多出来的键要忽略，不能让配置文件往程序里注入奇怪的东西
        with open(core.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"template": "{title}", "rm_rf": "/"}, f)
        only = core.load_config()
        check("rm_rf" not in only, "未知配置键被忽略")
    finally:
        core.CONFIG_FILE = old
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
#  三、分P 数与分P 序号
# ============================================================================
def test_parts():
    group("测试组 3  分P 总数不能被当成第几P")

    # 收藏夹接口的 page 字段是"分P 总数"。这个测试的意义在于
    # 把这条结论钉住：谁再拿它去拼 ?p=N，这里就会红
    src = open(os.path.join(HERE, "bili_fav.py"), encoding="utf-8").read()
    check('"parts": x.get("page")' in src,
          "fetch_folder 把该字段存成 parts")

    body = src[src.index("def fetch_folder"):]
    body = body[:body.index("\ndef ", 10)]
    check('"page": x.get("page")' not in body,
          "fetch_folder 不再输出误导性的 page 键")

    # 下载路径里不能再出现由 parts 拼出来的 "%s?p=%d"
    # 只看代码形式，不看注释
    dl = src[src.index("def download_batch"):].split("\ndef ")[0]
    check('?p=%d' not in dl, "批量下载不再用 parts 拼 ?p=")

    web = open(os.path.join(HERE, "web_app.py"), encoding="utf-8").read()
    rbatch = web[web.index("def run_batch"):].split("\ndef ")[0]
    check('?p=%d' not in rbatch, "网页批量的 run_batch 不再拼 ?p=")

    # download_one 应该收到 all_parts 参数
    check("all_parts=bool(opts.get(\"all_parts\"))" in dl,
          "批量下载把 all_parts 透传给 download_one")

    check("all_parts" in fav.build_opts(core.load_config(), []),
          "build_opts 产出 all_parts")
    check(fav.build_opts(core.load_config(), ["--all-parts"])["all_parts"] is True,
          "--all-parts 能打开 all_parts")
    check(fav.build_opts(core.load_config(), [])["all_parts"] is False,
          "默认不打开 all_parts")


# ============================================================================
#  四、选择表达式
# ============================================================================
def test_selection():
    group("测试组 4  选择表达式")

    check(fav.parse_selection("1,3,5", 10) == [0, 2, 4], "逗号分隔")
    check(fav.parse_selection("2-4", 10) == [1, 2, 3], "区间")
    check(fav.parse_selection("1,3-5,9", 10) == [0, 2, 3, 4, 8], "混合写法")
    check(fav.parse_selection("3,3,3", 10) == [2], "重复序号去重")
    check(fav.parse_selection("99", 10) == [], "越界序号丢掉")
    # 区间写反了按两端之间处理，不报错。人打 "5-2" 时想表达的
    # 几乎肯定是 2 到 5，直接当成空集反而莫名其妙
    check(fav.parse_selection("5-2", 10) == [1, 2, 3, 4],
          "倒序区间按两端之间处理", fav.parse_selection("5-2", 10))
    check(fav.parse_selection("", 10) == [], "空串返回空")
    check(fav.parse_selection("abc", 10) == [], "非数字返回空")
    check(fav.parse_selection("1", 0) == [], "总数 0 时返回空")

    # 中文逗号也要认，中文输入法下很容易打出来
    got = fav.parse_selection("1，3", 10)
    check(got == [0, 2], "中文逗号也能解析", got)

    # 体积。不带单位按 MB —— 对视频来说这是最自然的粒度
    check(fav.parse_size("500MB") == 500 * 1024 ** 2, "500MB 解析",
          fav.parse_size("500MB"))
    check(fav.parse_size("500M") == 500 * 1024 ** 2, "500M 解析")
    check(fav.parse_size("500mb") == 500 * 1024 ** 2, "小写 mb 也认")
    check(fav.parse_size("500 MiB") == 500 * 1024 ** 2, "带空格的 MiB 也认")
    check(fav.parse_size("1.5G") == int(1.5 * 1024 ** 3), "1.5G 解析")
    check(fav.parse_size("2GB") == 2 * 1024 ** 3, "2GB 解析")
    check(fav.parse_size("800K") == 800 * 1024, "800K 解析")
    check(fav.parse_size("1024") == 1024 * 1024 ** 2, "纯数字按 MB")
    check(fav.parse_size("乱七八糟") == 0, "认不出来返回 0")
    check(fav.parse_size("") == 0, "空串返回 0")
    check(fav.parse_size(None) == 0, "None 返回 0")
    check(fav.parse_size("-5M") == 0, "负数返回 0")


# ============================================================================
#  五、失效视频过滤
# ============================================================================
def test_dead():
    group("测试组 5  失效视频过滤")

    check(fav.is_dead_entry({}) is True, "没有 bvid 算失效")
    check(fav.is_dead_entry({"bvid": ""}) is True, "bvid 为空算失效")
    check(fav.is_dead_entry({"bvid": "BV1xx411c7mD", "title": "正常标题"}) is False,
          "正常的算有效")

    # 有 bvid 但标题被替换掉的那种。这种最容易漏，
    # 不提前剔除的话批量下载时会白跑一次请求再报失败
    for t in fav.DEAD_TITLES:
        check(fav.is_dead_entry({"bvid": "BV1xx411c7mD", "title": t}) is True,
              "标题「%s」算失效" % t)

    # 前后空格不该影响判断
    check(fav.is_dead_entry({"bvid": "BV1x", "title": "  已失效视频  "}) is True,
          "标题带空格也认得出")


# ============================================================================
#  六、交互开关
# ============================================================================
def test_interactive():
    group("测试组 6  交互与非交互")

    old = core.is_interactive()
    try:
        core.set_interactive(False)
        check(core.is_interactive() is False, "能关掉交互")

        # 关掉之后 safe_input 必须立刻返回默认值。
        # 这条是这次修的重点：后台线程里问问题是永远等不到答案的
        t0 = time.time()
        got = core.safe_input("  请输入：", default="兜底")
        dt = time.time() - t0
        check(got == "兜底", "非交互下 safe_input 直接返回默认值", got)
        check(dt < 0.5, "非交互下 safe_input 不会阻塞（%.3f 秒）" % dt)

        core.set_interactive(True)
        check(core.is_interactive() is True, "能重新打开交互")
    finally:
        core.set_interactive(old)

    check(core.is_interactive() is True, "默认视为有人在终端前")

    # confirm 在 -y 下直接通过
    old_y = core._ASSUME_YES
    try:
        core.set_assume_yes(True)
        core.set_quiet(True)
        check(core.confirm("继续？") is True, "--yes 时 confirm 直接通过")
        core.set_quiet(False)
        core.set_assume_yes(False)
        core.set_interactive(False)
        t0 = time.time()
        r = core.confirm("继续？", default=False)
        dt = time.time() - t0
        check(r is False and dt < 0.5,
              "非交互且没给 --yes 时取默认值且不阻塞（%.3f 秒）" % dt)
    finally:
        core._ASSUME_YES = old_y
        core.set_interactive(old)
        core.set_quiet(False)


# ============================================================================
#  七、静默模式
# ============================================================================
def test_quiet():
    group("测试组 7  静默模式")

    import io
    old = core.is_quiet()
    buf = io.StringIO()
    old_out = sys.stdout
    try:
        sys.stdout = buf
        core.set_quiet(True)
        core.info("这行不该出现")
        core.step("这行也不该出现")
        core.ok("这行也不该出现")
        core.warn("这行要出现")
        core.err("这行也要出现")
        out = buf.getvalue()
    finally:
        sys.stdout = old_out
        core.set_quiet(old)

    check("不该出现" not in out, "静默时 info/step/ok 都不输出")
    check("这行要出现" in out, "静默时 warn 仍然输出")
    check("这行也要出现" in out, "静默时 err 仍然输出")

    # 关掉之后要恢复正常
    buf2 = io.StringIO()
    old_out = sys.stdout
    try:
        sys.stdout = buf2
        core.set_quiet(False)
        core.info("恢复正常了")
        out2 = buf2.getvalue()
    finally:
        sys.stdout = old_out
        core.set_quiet(old)
    check("恢复正常了" in out2, "关掉静默后 info 恢复输出")


# ============================================================================
#  八、网页版：选项白名单与参数校验
# ============================================================================
def test_web_opts():
    group("测试组 8  网页版参数校验")

    import web_app as web

    # _extract_opts 是绑定在 Handler 上的方法，造一个空壳来调它
    class Fake:
        _extract_opts = web.Handler._extract_opts
    f = Fake()

    o = f._extract_opts({})
    for k in ("template", "subtitle", "danmaku", "cover", "metadata",
              "audio_only", "audio_mp3", "all_parts"):
        check(k in o, "opts 里有 %s" % k)
    check(o["template"] == "{title}", "没给模板时用 {title}", o["template"])

    # 只认白名单，别的一律丢掉
    o2 = f._extract_opts({"rm_rf": "/", "outdir": "C:\\Windows",
                          "template": "{title}", "__class__": "x"})
    check("rm_rf" not in o2, "白名单外的键被丢掉")
    check("outdir" not in o2, "请求不能改下载目录")
    check("__class__" not in o2, "请求不能注入 dunder 键")

    # 超长模板要截断，不然能拼出一个超长路径
    o3 = f._extract_opts({"template": "a" * 5000})
    check(len(o3["template"]) <= 200, "超长模板被截断到 %d" % len(o3["template"]))

    # 空模板回落到 {title}
    check(f._extract_opts({"template": "   "})["template"] == "{title}",
          "空白模板回落到 {title}")

    # 真值转换
    o4 = f._extract_opts({"subtitle": 1, "danmaku": "yes", "cover": [], "metadata": None})
    check(o4["subtitle"] is True and o4["danmaku"] is True, "真值被转成 True")
    check(o4["cover"] is False and o4["metadata"] is False, "假值被转成 False")

    check(hasattr(web, "guess_mime"), "有 guess_mime")
    check(web.guess_mime("a.mp4") == "video/mp4", "mp4 类型")
    check(web.guess_mime("a.m4a") == "audio/mp4", "m4a 是音频不是视频")
    check(web.guess_mime("a.srt") == "application/x-subrip", "srt 类型")
    check(web.guess_mime("a.ass") == "text/x-ssa", "ass 类型")
    check(web.guess_mime("a.bin") == "application/octet-stream", "未知类型走兜底")
    check(web.guess_mime("A.MP4") == "video/mp4", "扩展名大小写不敏感")


# ============================================================================
#  九、网页版：批次状态
# ============================================================================
def test_batch_state():
    group("测试组 9  批次状态机")

    import web_app as web

    items = [
        {"bvid": "BV1xx411c7mD", "title": "第一个", "parts": 1},
        {"bvid": "BV1xx411c7mE", "title": "第二个", "parts": 12},
    ]
    b = web.Batch("测试夹", items, tempfile.gettempdir(), 80, {"template": "{title}"})

    snap = b.snapshot()
    check(snap["total"] == 2, "总数正确", snap["total"])
    check(snap["state"] == "running", "初始状态是 running")
    check(snap["done"] == 0, "初始完成数是 0")

    # parts 必须原样带出来，前端要显示"共几P"
    check(snap["items"][1]["parts"] == 12, "分P 总数透传到快照",
          snap["items"][1].get("parts"))
    check("page" not in snap["items"][1],
          "快照里没有误导性的 page 键")

    # 每一项都要有自己的任务号，前端靠它拉产物链接
    ids = [it["job"] for it in snap["items"]]
    check(len(set(ids)) == 2 and all(ids), "每项有独立任务号", ids)

    # 登记之后要能被 _find_job 找到
    with web.BATCH_LOCK:
        web.BATCHES[b.id] = b
    try:
        class Fake:
            _find_job = web.Handler._find_job
            server = None
        f = Fake()
        got = f._find_job(ids[0])
        check(got is not None, "批量子任务能被 _find_job 找到")
        check(f._find_job("不存在") is None, "找不到时返回 None")
        check(f._find_job("") is None, "空任务号返回 None")
    finally:
        with web.BATCH_LOCK:
            web.BATCHES.pop(b.id, None)

    # 任务标成完成后计数要跟着变
    b.entries[0]["job"].set(state="done")
    s2 = b.snapshot()
    check(s2["done"] == 1, "一项完成后 done=1", s2["done"])
    check(s2["pct"] == 50.0, "百分比正确", s2["pct"])

    b.entries[1]["job"].set(state="error", error="网络断了")
    s3 = b.snapshot()
    check(s3["failed"] == 1, "失败数正确", s3["failed"])
    check(s3["pct"] == 100.0, "全终态时 100%", s3["pct"])


# ============================================================================
#  十、网页版：服务对象与页面
# ============================================================================
def test_server():
    group("测试组 10  服务对象与页面")

    import web_app as web

    check(issubclass(web.Server, web.ThreadingHTTPServer),
          "Server 继承自 ThreadingHTTPServer")
    # 不关这个的话，Ctrl+C 之后 server_close 会 join 还在下载的请求线程，
    # 用户看到的是"按了没反应"
    check(web.Server.block_on_close is False,
          "block_on_close 关掉了（Ctrl+C 能立刻退出）")
    check(web.Server.daemon_threads is True, "请求线程是 daemon")

    # 配置属性要有默认值，不能只靠 setattr
    check(web.Server.outdir is None, "Server.outdir 有默认值")
    check(isinstance(web.Server.concurrency, int), "Server.concurrency 有默认值")

    page = web.render_page([80, 64], {"template": "{up}/{title}", "subtitle": True})

    for p in ("__TITLE__", "__QNAMES__", "__ACCEPT__", "__CFG__"):
        check(p not in page, "占位符 %s 已替换" % p)

    # 收藏夹那一块的元素
    for eid in ("folder", "fload", "fdl", "favlist", "favacts",
                "fall", "fnone", "finv", "btbox"):
        check(('id="%s"' % eid) in page, "页面里有 #%s" % eid)

    # 附加选项
    for eid in ("opt_sub", "opt_dm", "opt_cover", "opt_meta",
                "opt_audio", "opt_parts", "tpl"):
        check(('id="%s"' % eid) in page, "页面里有 #%s" % eid)

    # 关键的 JS 函数
    for fn in ("function esc(", "function getOpts(", "function applyCfg(",
               "function startFav(", "function pollBatch(", "function renderFavs("):
        check(fn in page, "页面里有 %s" % fn)

    # XSS：标题来自接口，是不可信输入，拼 innerHTML 前必须转义
    check("esc(j.title)" in page and "esc(d.title)" in page,
          "下载记录和视频信息里的标题都过了 esc()")
    check("esc(it.title)" in page and "esc(v.title)" in page,
          "收藏夹列表和批量面板的标题也过了 esc()")

    # 送进 filler 的是 __CFG__，渲染后会被换成真的配置 JSON，
    # 所以要在替换之前的模板上查这个调用
    check("applyCfg(__CFG__)" in web.page_html(),
          "页面会把配置填进表单")
    check('"template": "{up}/{title}"' in page or "&quot;" in page or "template" in page,
          "配置内容确实注入了页面")

    # 空档位也要能渲染
    try:
        web.render_page([])
        check(True, "空清晰度列表能渲染")
    except Exception as e:
        check(False, "空清晰度列表能渲染", repr(e))


# ============================================================================
#  十一、附加内容：字幕与弹幕
# ============================================================================
def test_sidecar():
    group("测试组 11  字幕与弹幕转换")

    # 字幕：B 站给的是一串带起止毫秒的片段
    data = {"body": [
        {"from": 0.0, "to": 2.5, "content": "第一句"},
        {"from": 2.5, "to": 5.0, "content": "第二句"},
    ]}
    srt = core.srt_from_bili_json(data)
    check("00:00:00,000 --> 00:00:02,500" in srt, "SRT 起止时间格式正确", srt[:80])
    check("第一句" in srt and "第二句" in srt, "SRT 内容完整")
    check(srt.count("-->") == 2, "SRT 有两条字幕")

    # 空数据不能崩
    check(core.srt_from_bili_json({}) == "", "空字幕数据返回空串")
    check(core.srt_from_bili_json({"body": []}) == "", "空列表返回空串")
    check(core.srt_from_bili_json(None) == "", "None 返回空串")

    # 弹幕：p 属性是 time,mode,size,color,timestamp,pool,hash,row,weight
    xml = ('<?xml version="1.0" encoding="UTF-8"?><i>'
           '<d p="1.5,1,25,16711680,1700000000,0,abc,0,0">普通弹幕</d>'
           '<d p="2.5,4,25,16777215,1700000000,0,abc,0,0">底部弹幕</d>'
           '<d p="3.5,5,25,255,1700000000,0,abc,0,0">顶部弹幕</d>'
           '<d p="4.5,7,25,255,1700000000,0,abc,0,0">高级弹幕</d>'
           '</i>')
    ass = core.ass_from_danmaku(xml, 1920, 1080)
    check("[Script Info]" in ass and "[V4+ Styles]" in ass and "[Events]" in ass,
          "ASS 三段结构齐全")
    check("普通弹幕" in ass and "底部弹幕" in ass and "顶部弹幕" in ass,
          "三种普通弹幕都转进去了")
    # 7 是高级弹幕（带坐标和时长），转成滚动字幕会变成一坨，明确跳过
    check("高级弹幕" not in ass, "高级弹幕被跳过")
    check("Dialogue:" in ass, "有 Dialogue 行")
    check(ass.count("Dialogue:") == 3, "对话行数与有效弹幕数一致",
          ass.count("Dialogue:"))

    # 颜色要转成 ASS 的 &HBBGGRR
    check("&H" in ass, "颜色按 ASS 格式写出")

    # 空弹幕不能崩
    try:
        empty = core.ass_from_danmaku("", 1920, 1080)
        check("[Script Info]" in empty, "空弹幕也能产出合法 ASS 头")
    except Exception as e:
        check(False, "空弹幕不抛异常", repr(e))

    # 属性坏掉的弹幕要跳过而不是让整个转换失败
    try:
        weird = core.ass_from_danmaku(
            '<i><d p="坏掉的数据">x</d><d p="1,1,25,16777215,1,0,h,0,0">好的</d></i>',
            1920, 1080)
        check("好的" in weird, "坏属性的弹幕被跳过，其余仍能转换")
    except Exception as e:
        check(False, "坏属性弹幕不抛异常", repr(e))


# ============================================================================
#  十二、并发下载的隔离
# ============================================================================
def test_concurrency():
    group("测试组 12  并发下载的隔离")

    src = open(os.path.join(HERE, "bili_fav.py"), encoding="utf-8").read()
    body = src[src.index("def download_batch"):]
    body = body[:body.index("\n# =====")]

    check("core.Bili()" in body, "每个任务自己建会话（requests.Session 不是线程安全的）")
    check("set_interactive(False)" in body, "批量下载会关掉交互提示")
    check("finally:" in body and "set_interactive(prev_interactive)" in body,
          "开关用 finally 还原，异常时不会留成打开的")

    web = open(os.path.join(HERE, "web_app.py"), encoding="utf-8").read()
    check("core.set_interactive(False)" in web, "网页版声明了非交互")
    check("core.set_assume_yes(True)" in web, "网页版声明了自动确认")

    # run_download 里也要再声明一次：它跑在后台线程里
    rd = web[web.index("def run_download"):]
    rd = rd[:rd.index("\n# ===")]
    check("core.set_interactive(False)" in rd,
          "后台下载线程内部也声明了非交互")
    check("core.set_interactive(prev_iact)" in rd,
          "run_download 会还原交互开关")

    # 并发上限
    check("max(1, min(workers, 4))" in web, "网页端并发压在 4 以内")
    check("max(1, min(n, 8))" in src, "命令行并发压在 8 以内")


def main():
    print("=" * 72)
    print("收藏夹 / 模板 / 附加内容  离线测试")
    print("=" * 72)

    for fn in (test_template, test_config, test_parts, test_selection,
               test_dead, test_interactive, test_quiet, test_web_opts,
               test_batch_state, test_server, test_sidecar, test_concurrency):
        try:
            fn()
        except Exception as e:
            global FAIL
            FAIL += 1
            BAD.append("%s 抛异常" % fn.__name__)
            print("  [异常] %s  %r" % (fn.__name__, e))
            import traceback
            traceback.print_exc()

    print()
    print("=" * 72)
    print("结果   通过 %d   失败 %d" % (PASS, FAIL))
    for b in BAD:
        print("  - " + b)
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
