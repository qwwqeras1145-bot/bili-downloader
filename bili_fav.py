# -*- coding: utf-8 -*-
r"""
===============================================================================
 Fairy III 型  ·  B 站收藏夹下载
===============================================================================

 这是 bili_dl.py 的配套模块，负责"很多个视频"的场景：
 列出账号收藏夹、浏览里面的视频、批量或挑选着下。

 为什么不并进 bili_dl.py
   bili_dl.py 管的是"一个视频怎么下好"，这个模块管的是"一批视频怎么组织"。
   两件事的复杂度不一样，混在一个文件里会让两边都难改。
   依赖方向是单向的：这个模块引入 bili_dl，反过来不引，
   所以不存在循环导入。

 用法（入口在 bili_dl.py）
   python bili_dl.py --fav                     交互式浏览与下载
   python bili_dl.py --fav list                只列出收藏夹
   python bili_dl.py --fav <收藏夹ID>           列出里面的视频
   python bili_dl.py --fav <收藏夹ID> --all     全部下载
   python bili_dl.py --fav <收藏夹ID> --pick 1,3,5-9
   python bili_dl.py --fav <收藏夹ID> --search 教程

 下载相关的参数（模板、字幕、弹幕、仅音频等）都能跟着一起用，例如
   python bili_dl.py --fav 2320398826 --all --all-extras -t "{up}/{title}"

===============================================================================
"""

import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import bili_dl as core
from bili_dl import (info, ok, warn, err, step, fmt_size, fmt_dur,
                     sanitize, C, API_FAV_FOLDERS, API_FAV_LIST,
                     safe_input, confirm)

# 收藏夹接口每页最多给 20 条。要多了会被截断，所以固定按 20 翻页
PAGE_SIZE = 20


# ============================================================================
#  收藏夹读取
# ============================================================================
def list_folders(bili):
    """
    列出当前账号的收藏夹，返回 (列表, 错误)。

    需要登录。未登录时接口返回 -101，这里翻译成人话再返回。

    每项含 id、title、media_count（视频数）。
    注意 id 和 fid 不是一个东西，取内容列表要用 id。
    """
    logged, name, _vip = bili.whoami()
    if not logged:
        return [], "未登录。收藏夹只属于账号，请先运行 --login 扫码登录。"

    nav = None
    try:
        nav = bili.s.get("https://api.bilibili.com/x/web-interface/nav",
                         timeout=20).json()
    except Exception as e:
        return [], "取账号信息失败：%s" % e

    mid = ((nav.get("data") or {}).get("mid"))
    if not mid:
        return [], "没能取到账号 mid"

    try:
        r = bili.s.get(API_FAV_FOLDERS, params={"up_mid": mid}, timeout=25).json()
    except Exception as e:
        return [], "请求失败：%s" % e

    if r.get("code") != 0:
        return [], r.get("message") or ("返回码 %s" % r.get("code"))

    raw = ((r.get("data") or {}).get("list")) or []
    out = []
    for f in raw:
        out.append({
            "id": f.get("id"),
            "title": f.get("title") or "未命名",
            "count": f.get("media_count") or 0,
            # attr 是个位域，实测见过 1 / 22 / 23 / 131 等值，
            # 具体哪一位代表"公开"没有可靠依据，所以不翻译成文字。
            # 与其显示一个可能是错的"公开/私密"，不如原样留着不展示
            "attr": f.get("attr"),
        })
    return out, ""


# 收藏夹里失效视频会留下的标题。实测这三种写法都出现过
DEAD_TITLES = ("已失效视频", "已失效稿件", "稿件不可见")


def is_dead_entry(media):
    """
    判断收藏夹里的一条是不是失效视频。

    两种失效形态都要认
      一、没有 bvid。接口直接不给编号
      二、有 bvid，但标题被替换成"已失效视频"之类

    第二种容易漏。实测拿这种 bvid 去查，接口回的是
    "稿件不可见"或"啥都木有"，也就是说这个编号留着但内容没了。
    不提前剔除的话，下载时会白跑一次请求再报失败，
    在几百个视频的批量里会攒出一堆假失败。
    """
    if not media.get("bvid"):
        return True
    title = (media.get("title") or "").strip()
    if title in DEAD_TITLES:
        return True
    return False


def fetch_folder(bili, media_id, limit=0, on_page=None, cancel=None):
    """
    取收藏夹里的视频列表，自动翻页。

    返回 (视频列表, 错误)。

    几个要处理的情况
      翻页  用 has_more 判断，不要靠"本页不足 20 条"来猜，
            因为失效视频会被过滤掉，条数会少于 20 但后面还有
      失效  见 is_dead_entry 的说明，两种形态都要剔除
      limit 大收藏夹可能有几千条，limit 用来限制最多取多少，
            0 表示全取
    """
    videos = []
    pn = 1
    skipped = 0

    while True:
        if cancel is not None and cancel.is_set():
            break
        try:
            r = bili.s.get(API_FAV_LIST,
                           params={"media_id": media_id, "ps": PAGE_SIZE,
                                   "pn": pn, "platform": "web"},
                           timeout=25).json()
        except Exception as e:
            if videos:
                # 已经拿到一部分了，网络抖一下就返回现有的，别整个失败
                warn("翻页中断：%s（已取到 %d 条）" % (e, len(videos)))
                break
            return [], "请求失败：%s" % e

        if r.get("code") != 0:
            if videos:
                warn("翻页失败：%s（已取到 %d 条）" % (r.get("message"), len(videos)))
                break
            return [], r.get("message") or ("返回码 %s" % r.get("code"))

        d = r.get("data") or {}
        medias = d.get("medias") or []

        for x in medias:
            if is_dead_entry(x):
                skipped += 1
                continue
            videos.append({
                "bvid": x.get("bvid"),
                "aid": x.get("id"),
                "title": x.get("title") or "",
                "cover": x.get("cover") or "",
                "duration": x.get("duration") or 0,
                "up": ((x.get("upper") or {}).get("name")) or "",
                "up_mid": ((x.get("upper") or {}).get("mid")),
                # 这个字段在收藏夹接口里叫 page，含义是"分P 总数"，不是"第几个分P"。
                # 实测 BV15J41187T2 在这里返回 2，视频信息接口给出的 pages 也正好 2 个。
                # 早先按"第几P"理解，于是所有多P 视频都只下了最后一个分P。
                # 改名成 parts，免得再被当下标用。
                "parts": x.get("page") or 1,
                "fav_time": x.get("fav_time") or 0,
                "intro": x.get("intro") or "",
            })

        if on_page:
            on_page(pn, len(videos))

        if limit and len(videos) >= limit:
            videos = videos[:limit]
            break

        if not d.get("has_more"):
            break

        pn += 1
        if pn > 500:            # 一万条上限，防呆
            warn("翻页超过 500 页，停止取用")
            break
        # 稍微歇一下。收藏夹几百页时连续请求容易被限
        time.sleep(0.25)

    if skipped:
        info("已跳过 %d 个失效视频（原视频已被删除）" % skipped)
    return videos, ""


# ============================================================================
#  选择表达式
# ============================================================================
def parse_selection(text, max_n):
    """
    解析 "1,3,5-9" 这种选择表达式，返回下标列表（从 0 开始）。

    支持逗号分隔的单个序号和区间，序号按人类习惯从 1 开始。
    越界的自动丢掉，重复的去掉，最后按原顺序返回 ——
    用户指定顺序不影响下载顺序，只是挑哪些。
    """
    picked = set()
    for piece in str(text or "").replace("，", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            a, _, b = piece.partition("-")
            try:
                lo, hi = int(a), int(b)
            except Exception:
                continue
            if lo > hi:
                lo, hi = hi, lo
            for i in range(lo, hi + 1):
                if 1 <= i <= max_n:
                    picked.add(i - 1)
        else:
            try:
                i = int(piece)
            except Exception:
                continue
            if 1 <= i <= max_n:
                picked.add(i - 1)
    return sorted(picked)


def parse_size(text):
    """
    解析 "300M" / "500MB" / "1.5G" / "800K" 这类体积写法，返回字节数。

    不带单位时按 MB 算 —— 这是最常用的粒度。

    后缀兼容这几种写法（大小写和空格都不敏感）
        500M   500MB   500MiB   500m
    早先只认单字母的 M/G/K，于是 "500MB" 会被判成认不出来返回 0，
    而调用方把 0 当成"没设上限"，结果是筛选静默失效 ——
    用户以为自己限了体积，其实一个都没滤掉。
    """
    s = str(text or "").strip().upper().replace(" ", "")
    if not s:
        return 0

    # 后缀按从长到短匹配，不然 "500MB" 会先被 "B" 吃掉
    mult = 1024 ** 2                      # 不带单位按 MB
    for suffix, m in (("TIB", 1024 ** 4), ("TB", 1024 ** 4), ("T", 1024 ** 4),
                      ("GIB", 1024 ** 3), ("GB", 1024 ** 3), ("G", 1024 ** 3),
                      ("MIB", 1024 ** 2), ("MB", 1024 ** 2), ("M", 1024 ** 2),
                      ("KIB", 1024), ("KB", 1024), ("K", 1024),
                      ("B", 1)):
        if s.endswith(suffix) and len(s) > len(suffix):
            mult, s = m, s[:-len(suffix)]
            break

    try:
        n = int(float(s) * mult)
    except Exception:
        return 0
    return n if n > 0 else 0


# ============================================================================
#  展示
# ============================================================================
def show_folders(folders):
    """打印收藏夹列表"""
    print()
    print(C.B + "  收藏夹" + C.R)
    print("  " + "─" * 68)
    for i, f in enumerate(folders, 1):
        print("   %2d. %-30s %6d 个视频" % (i, f["title"][:28], f["count"]))
        print("       %sID: %s%s" % (C.D, f["id"], C.R))
    print("  " + "─" * 68)
    info("下载用 ID，例如： python bili_dl.py --fav %s --all" % folders[0]["id"])


def show_videos(videos, start=1):
    """打印视频列表，带序号方便挑选"""
    print()
    print(C.B + "  共 %d 个视频" % len(videos) + C.R)
    print("  " + "─" * 68)
    for i, v in enumerate(videos, start):
        mark = ""
        n = v.get("parts") or 1
        if n > 1:
            # 是"共几P"，不是"第几P"。默认只下第 1 个分P
            mark = " [共%dP]" % n
        print("   %3d. %s" % (i, sanitize(v["title"], 46) + mark))
        print("        %s %s  %s%s" % (
            C.D, v["bvid"], fmt_dur(v["duration"]), C.R))
    print("  " + "─" * 68)


# ============================================================================
#  批量下载
# ============================================================================
class BatchResult:
    """批量下载的统计。加锁是因为多个线程会同时累加"""

    def __init__(self, total):
        self.total = total
        self.done = 0
        self.failed = []
        self._lock = threading.Lock()

    def mark(self, bvid, title, okflag, reason=""):
        with self._lock:
            self.done += 1
            if not okflag:
                self.failed.append({"bvid": bvid, "title": title, "reason": reason})
            return self.done


def download_batch(videos, cfg, opts, outdir, workers=None):
    """
    并发下载一批视频。

    并发方面有两个要留意的点

    一、每个线程用自己的会话
        requests 的 Session 不是线程安全的。共用的话在高并发下
       偶发连接错乱，表现是随机的 SSL 错误或响应串包。
       所以每个任务自己 new 一个 Bili，代价是几次握手，换来的是稳。

    二、输出要静默
       多个线程同时画进度条会把终端刷花。这里统一切到静默模式，
       只报"开始"和"结果"两行，最后给一份汇总。
    """
    n = workers or int(cfg.get("concurrency") or 1)
    n = max(1, min(n, 8))          # 上限 8，再多对 B 站不礼貌

    core.set_quiet(True)
    # 并发下载绝不能弹交互提示：多P 视频会问"下哪个分P"，
    # 而这里没人在回答，线程会永远停住
    prev_interactive = core.is_interactive()
    core.set_interactive(False)
    res = BatchResult(len(videos))
    interval = float(cfg.get("interval") or 0)

    print()
    info("开始批量下载：%d 个视频，%d 个并发" % (len(videos), n))
    print("  " + "─" * 68)

    def one(item):
        idx, v = item
        # 每个线程自带会话，见上面说明
        bili = core.Bili()
        link = v["bvid"]
        # 这里原来会把 parts 当成"第几P"拼成 ?p=N，于是多P 视频只下了最后一个分P。
        # 现在不拼分P：默认下第 1 个，要全下由 opts 里的 all_parts 决定。
        try:
            got = core.download_one(bili, link, outdir=outdir, qn=opts.get("qn"),
                                    all_parts=bool(opts.get("all_parts")),
                                    opts=opts)
        except Exception as e:
            got = False
            return v, False, "%s: %s" % (type(e).__name__, e)
        if interval:
            time.sleep(interval)
        return v, bool(got), ""

    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {pool.submit(one, (i, v)): v for i, v in enumerate(videos, 1)}
            for fut in as_completed(futures):
                v = futures[fut]
                try:
                    vv, flag, why = fut.result()
                except Exception as e:
                    vv, flag, why = v, False, str(e)
                done = res.mark(vv["bvid"], vv["title"], flag, why)
                tag = C.GRN + "完成" + C.R if flag else C.RED + "失败" + C.R
                print("  [%3d/%3d] %s  %s" % (done, res.total, tag,
                                              sanitize(vv["title"], 40)))
                if not flag and why:
                    print("         %s%s%s" % (C.D, why[:100], C.R))
    finally:
        # 用 finally 还原：中途出异常时不能把这两个开关留在打开状态，
        # 否则同一进程后面的交互全变成"自动取默认值"，很隐蔽
        core.set_quiet(False)
        core.set_interactive(prev_interactive)
    return res


# ============================================================================
#  交互模式
# ============================================================================
def interactive(bili, cfg, opts):
    """
    交互式浏览收藏夹。

    做成"选一次收藏夹 → 选一批视频 → 下载"的流程。
    选视频支持三种方式：序号区间、关键词筛选、直接全选。
    """
    folders, e = list_folders(bili)
    if e:
        err(e)
        if "登录" in e:
            info("扫码登录： python bili_dl.py --login")
        return 1
    if not folders:
        warn("这个账号还没有收藏夹")
        return 1

    show_folders(folders)
    print()
    try:
        sel = safe_input("  选择收藏夹序号（回车退出）：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not sel:
        return 0

    idx = parse_selection(sel, len(folders))
    if not idx:
        err("序号不对")
        return 2
    folder = folders[idx[0]]

    print()
    step("正在读取「%s」的内容" % folder["title"])
    videos, e = fetch_folder(bili, folder["id"],
                             on_page=lambda pn, n: print("   第 %d 页，已取 %d 条"
                                                         % (pn, n), end="\r"))
    print()
    if e:
        err(e)
        return 1
    if not videos:
        warn("这个收藏夹是空的")
        return 1

    show_videos(videos)
    print()
    info("选择要下载的视频。可以输入：")
    info("   序号区间   例如 1,3,5-9")
    info("   关键词     例如 /教程        只下标题里带这两个字的")
    info("   全部       all")
    print()
    try:
        sel = safe_input("  选择（回车退出）：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not sel:
        return 0

    if sel.lower() in ("all", "*", "全部"):
        picked = list(range(len(videos)))
    elif sel.startswith("/"):
        kw = sel[1:].strip()
        picked = [i for i, v in enumerate(videos) if kw and kw in v["title"]]
        if not picked:
            warn("没有标题包含「%s」的视频" % kw)
            return 1
        info("匹配到 %d 个" % len(picked))
    else:
        picked = parse_selection(sel, len(videos))
        if not picked:
            err("没能解析出要下哪些")
            return 2

    chosen = [videos[i] for i in picked]
    multi = sum(1 for v in chosen if (v.get("parts") or 1) > 1)
    total_dur = sum(v.get("duration") or 0 for v in chosen)
    print()
    info("准备下载 %d 个视频，合计时长约 %s" % (len(chosen), fmt_dur(total_dur)))
    if multi and not opts["all_parts"]:
        info("其中 %d 个是多分P 视频，只会下第 1 个分P。" % multi)
        info("要每个分P 都下，加上 --all-parts")
    if not confirm("  确认开始？[y/N] "):
        info("已取消")
        return 0

    outdir = core.get_outdir([], cfg)
    res = download_batch(chosen, cfg, opts, outdir)
    return report(res)


def report(res):
    """打印批量下载的汇总"""
    print()
    print("  " + "─" * 68)
    okn = res.total - len(res.failed)
    if res.failed:
        warn("完成 %d 个，失败 %d 个" % (okn, len(res.failed)))
        print()
        info("失败的这些可以单独重试：")
        for f in res.failed[:15]:
            print("   %s  %s" % (f["bvid"], sanitize(f["title"], 40)))
            if f["reason"]:
                print("      %s%s%s" % (C.D, f["reason"][:90], C.R))
        if len(res.failed) > 15:
            info("  ... 其余 %d 个" % (len(res.failed) - 15))
    else:
        ok("全部完成：%d 个视频" % okn)
    print("  " + "─" * 68)
    return 0 if not res.failed else 1


# ============================================================================
#  命令行入口
# ============================================================================
def build_opts(cfg, argv):
    """把命令行里的下载选项整理成 opts，跟 bili_dl 的用法保持一致"""
    opts = {
        "template": cfg.get("template") or "{title}",
        "subtitle": bool(cfg.get("subtitle")),
        "danmaku": bool(cfg.get("danmaku")),
        "cover": bool(cfg.get("cover")),
        "metadata": bool(cfg.get("metadata")),
        "audio_only": bool(cfg.get("audio_only")),
        "audio_mp3": False,
        "qn": cfg.get("quality"),
        # 收藏夹里的多P 视频默认只下第 1 个分P。--all-parts 才会把每个分P 都下。
        # 不用 -p 指定分P 是因为收藏夹接口不给"第几P"，只给分P 总数。
        "all_parts": bool(cfg.get("all_parts")),
    }
    for i, a in enumerate(argv):
        if a in ("-t", "--template") and i + 1 < len(argv):
            opts["template"] = argv[i + 1]
        if a in ("-q", "--quality") and i + 1 < len(argv) and argv[i + 1].isdigit():
            opts["qn"] = int(argv[i + 1])
        if a in ("--subtitle", "--sub"):
            opts["subtitle"] = True
        if a in ("--danmaku", "--dm"):
            opts["danmaku"] = True
        if a == "--cover":
            opts["cover"] = True
        if a in ("--metadata", "--meta"):
            opts["metadata"] = True
        if a in ("--all-extras", "--extras"):
            opts["subtitle"] = opts["danmaku"] = True
            opts["cover"] = opts["metadata"] = True
        if a in ("--audio-only", "--audio"):
            opts["audio_only"] = True
        if a in ("--all-parts", "--parts"):
            opts["all_parts"] = True
        if a == "--mp3":
            opts["audio_mp3"] = True
            opts["audio_only"] = True
    return opts


def main(bili, cfg, argv):
    """
    收藏夹功能的入口。argv 是 --fav 之后剩下的参数。

    不带参数就进交互模式，带了就按参数走命令式流程。
    """
    opts = build_opts(cfg, argv)

    # ---- 没有子命令：交互 ----
    if not argv or argv[0] in ("-h", "--help"):
        if argv and argv[0] in ("-h", "--help"):
            print(__doc__)
            return 0
        core.set_template(opts["template"])
        return interactive(bili, cfg, opts)

    # ---- --fav list：只列收藏夹 ----
    if argv[0] in ("list", "ls"):
        folders, e = list_folders(bili)
        if e:
            err(e)
            return 1
        if not folders:
            warn("这个账号还没有收藏夹")
            return 1
        show_folders(folders)
        return 0

    # ---- --fav <id> ...：针对某个收藏夹 ----
    media_id = argv[0]
    if not str(media_id).isdigit():
        err("收藏夹 ID 应该是数字。用 --fav list 看有哪些")
        return 2

    limit = 0
    for i, a in enumerate(argv):
        if a in ("--limit",) and i + 1 < len(argv) and argv[i + 1].isdigit():
            limit = int(argv[i + 1])

    print()
    step("正在读取收藏夹内容")
    videos, e = fetch_folder(
        bili, int(media_id), limit=limit,
        on_page=lambda pn, n: print("   第 %d 页，已取 %d 条" % (pn, n), end="\r"))
    print()
    if e:
        err(e)
        return 1
    if not videos:
        warn("这个收藏夹是空的，或者里面的视频都已失效")
        return 1

    # ---- 关键词筛选 ----
    kw = None
    for i, a in enumerate(argv):
        if a in ("--search", "-s") and i + 1 < len(argv):
            kw = argv[i + 1]
    if kw:
        videos = [v for v in videos if kw in v["title"]]
        info("标题包含「%s」的有 %d 个" % (kw, len(videos)))
        if not videos:
            return 1

    # ---- 体积上限筛选 ----
    for i, a in enumerate(argv):
        if a in ("--max-size",) and i + 1 < len(argv):
            raw_cap = argv[i + 1]
            cap = parse_size(raw_cap)
            if cap:
                before = len(videos)
                # 用时长和常见码率粗估体积，避免为了筛掉大文件先下再说
                videos = [v for v in videos
                          if (v.get("duration") or 0) * 250 * 1024 // 8 <= cap]
                info("按体积上限过滤：%d -> %d 个" % (before, len(videos)))
            else:
                # 认不出来就说一声。静默跳过的话用户会以为筛选起作用了
                warn("--max-size 的值「%s」认不出来，体积筛选没有生效。" % raw_cap)
                info("写法示例： 500MB   1.5G   800K   不带单位按 MB 算")

    if not videos:
        warn("筛选之后没有剩下视频")
        return 1

    # ---- 决定下哪些 ----
    pick = None
    for i, a in enumerate(argv):
        if a in ("--pick", "-p") and i + 1 < len(argv):
            pick = argv[i + 1]

    want_all = any(a in ("--all", "-a") for a in argv)
    list_only = any(a in ("--list-only", "--dry", "-n") for a in argv)

    if pick is not None:
        idx = parse_selection(pick, len(videos))
        if not idx:
            err("--pick 没解析出有效序号")
            return 2
        chosen = [videos[i] for i in idx]
    elif want_all:
        chosen = videos
    else:
        # 既没 --all 也没 --pick，就列出来让用户看着办
        show_videos(videos)
        print()
        info("要继续的话，加上 --all 下全部，或 --pick 1,3,5-9 挑着下")
        info("例如： python bili_dl.py --fav %s --all" % media_id)
        return 0

    if list_only:
        show_videos(chosen)
        info("--dry 只列不下，共 %d 个" % len(chosen))
        return 0

    total_dur = sum(v.get("duration") or 0 for v in chosen)
    print()
    info("准备下载 %d 个视频，合计时长约 %s" % (len(chosen), fmt_dur(total_dur)))
    multi = sum(1 for v in chosen if (v.get("parts") or 1) > 1)
    if multi and not opts["all_parts"]:
        info("其中 %d 个是多分P 视频，只会下第 1 个分P。" % multi)
        info("要每个分P 都下，加上 --all-parts")
    if opts["audio_only"]:
        info("模式：仅音频")
    if opts["template"] != "{title}":
        info("命名模板：" + opts["template"])
    if not confirm("  确认开始？[y/N] "):
        info("已取消")
        return 0

    core.set_template(opts["template"])
    outdir = core.get_outdir(argv, cfg)
    res = download_batch(chosen, cfg, opts, outdir)
    return report(res)
