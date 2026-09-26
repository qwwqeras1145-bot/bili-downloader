# -*- coding: utf-8 -*-
r"""
===============================================================================
 Fairy III 型  ·  B 站合集与系列下载
===============================================================================

 收藏夹（bili_fav.py）解决的是"我存了什么"，
 这个模块解决的是"某个 UP 把一批视频编成了一组"。

 两种分组形式，接口和字段都不一样，但对使用者是同一件事

   合集（season）
     UP 主整理的付费/免费合集，可以分成多个分节（section）。
     接口是 seasons_archives_list，翻页参数叫 page_num / page_size，
     总数在 data.page.total。
     典型场景：一套连载教程、一部连载动画。

   系列（series）
     UP 主用旧版"系列"功能编的分组，没有分节。
     接口是 series/archives，翻页参数叫 pn / ps，总数在 data.page.total。
     典型场景：直播回放、某个话题的合集。

 两套接口的字段名不一样，所以下面各写一个取数函数，
 但都归一成同一种结构再交给上层，上层就不用管是哪种了。

 另一个入口是"从这个视频找合集"。`web-interface/view` 返回的
 ugc_season 字段里带着这个视频所属的整个合集（含每个分节的每一集），
 一次请求就能拿到全集，不用自己拼。
 对用户来说这是最顺手的用法：手上有链接，想下整套。

 依赖方向
   本模块 -> bili_fav -> bili_dl

   bili_fav 里那些跟"收藏夹"无关的东西（视频列表打印、序号表达式解析、
   并发批量下载、体积解析）其实是通用的批处理工具，这里直接复用，
   不再抄一份。抄一份的代价是以后行为会分叉 ——
   比如收藏夹那边的多分P 处理和这边不一致，用户就会遇到
   "同一个视频从收藏夹下和从合集下结果不同"这种莫名其妙的问题。

 用法（入口在 bili_dl.py）
   python bili_dl.py --season                     交互式浏览与下载
   python bili_dl.py --season <视频链接>           看这个视频属于哪个合集
   python bili_dl.py --season <视频链接> --all     下整个合集
   python bili_dl.py --season <视频链接> --pick 1,3-5
   python bili_dl.py --season <视频链接> --section 2   只要第 2 个分节
   python bili_dl.py --up <mid或空间链接>          列出该 UP 的全部合集与系列
   python bili_dl.py --up <mid> --season-id 13794 --all
   python bili_dl.py --up <mid> --series-id 2229877 --all

 下载相关的参数（模板、字幕、弹幕、仅音频等）都能跟着一起用。

===============================================================================
"""

import os
import re
import sys
import time

import bili_dl as core
from bili_dl import (info, ok, warn, err, step, fmt_dur, sanitize,
                     safe_input, confirm, parse_link)

# 这两个接口都放在 bili_dl.py 的接口常量区，跟其它接口地址集中在一起
API_SEASONS_SERIES_LIST = ("https://api.bilibili.com/x/polymer/web-space"
                           "/seasons_series_list")
API_SEASONS_ARCHIVES = ("https://api.bilibili.com/x/polymer/web-space"
                        "/seasons_archives_list")
API_SERIES_ARCHIVES = "https://api.bilibili.com/x/series/archives"

# 合集接口一页最多给 30 条（给多了会被截断）。
# 系列接口一页也是 30。两边一致，用同一个常量
PAGE_SIZE = 30

# 翻页上限。理论上一个合集能有几千集，但请求太密容易被限，
# 而且真有几千集的话用户也不会想一次全下。
# 30 * 400 = 一万两千集，够用了，超过就停下来告诉你
MAX_PAGES = 400


# ============================================================================
#  入口参数解析
# ============================================================================
def parse_mid(text):
    """
    从各种写法里取出 UP 的 mid。

    认得这几种
        1234567                                  直接是 mid
        https://space.bilibili.com/1234567       空间链接
        https://space.bilibili.com/1234567/video 带子路径
        https://www.bilibili.com/video/BVxxx     视频链接（要再查一次才知道 mid）
        BV1xx411c7mD                             视频编号

    返回 (mid, 错误)。视频链接那种要联网才能拿到 mid，
    所以这里只做能离线判断的部分，返回的 mid 为 None 时由调用方去查。

    一个例外：b23.tv 短链要先跟一次跳转才知道是不是视频，
    这是 parse_link 内部做的，所以传短链时这里会有一次网络请求。
    请求失败不影响判断，只是会走到"认不出来"那条分支。
    """
    s = str(text or "").strip()
    if not s:
        return None, "没有给内容。可以贴 UP 的 mid、空间链接，或者一个视频链接。"

    # 纯数字
    if s.isdigit():
        return int(s), ""

    # 空间链接
    m = re.search(r"space\.bilibili\.com/(\d+)", s)
    if m:
        return int(m.group(1)), ""

    # 视频链接或 BV 号：mid 得联网查
    bvid, aid, _pg = parse_link(s)
    if bvid or aid:
        return None, ""

    # 有些人会贴 UP 的名字。名字查不到 mid（没有公开的按名搜索接口），
    # 与其瞎猜不如直接说清楚
    return None, ("没能从「%s」里认出 mid。可以贴 UP 空间链接，"
                  "或者直接贴这个 UP 任意一个视频的链接。" % s[:60])


def mid_of_video(bili, text):
    """
    给一个视频链接/BV 号，查它作者的 mid。

    返回 (mid, 错误)。
    """
    bvid, aid, _pg = parse_link(text)
    if not bvid and not aid:
        return None, "没能认出视频编号"
    try:
        data, e = bili.video_info(bvid=bvid, aid=aid)
    except Exception as e:
        return None, "请求失败：%s" % e
    if e or not data:
        return None, e or "没取到视频信息"
    mid = ((data.get("owner") or {}).get("mid"))
    if not mid:
        return None, "这个视频没有作者信息"
    return int(mid), ""


# ============================================================================
#  合集 / 系列 列表
# ============================================================================
def _norm_meta(meta, kind):
    """
    把两种接口的 meta 归一成同一种结构。

    合集用 season_id，系列用 series_id，其余字段名倒是差不多。
    归一是为了让上层（列表打印、按序号选择）不用分情况写两遍
    """
    if kind == "season":
        cid = meta.get("season_id")
    else:
        cid = meta.get("series_id")
    try:
        total = int(meta.get("total") or 0)
    except Exception:
        total = 0
    return {
        "kind": kind,
        "id": cid,
        "title": meta.get("name") or "未命名",
        # meta 里的 total 是"包含多少集"。实测偶尔是字符串，转一下
        "count": total,
        "cover": meta.get("cover") or "",
        "intro": (meta.get("description") or "").strip(),
    }


def list_collections(bili, mid, page_size=20):
    """
    列出某个 UP 的全部合集与系列，返回 (列表, 错误)。

    返回的每一项含 kind('season'/'series')、id、title、count。
    kind 必须带着 —— 下内容时要用它决定调哪个接口，
    只给一个 id 是不够的。

    这个接口不需要登录。
    """
    out = []
    page = 1
    while True:
        try:
            r = bili.s.get(API_SEASONS_SERIES_LIST,
                           params={"mid": mid, "page_num": page,
                                   "page_size": page_size},
                           timeout=25).json()
        except Exception as e:
            if out:
                warn("翻页中断：%s（已取到 %d 项）" % (e, len(out)))
                break
            return [], "请求失败：%s" % e

        if r.get("code") != 0:
            if out:
                break
            code = r.get("code")
            msg = r.get("message") or ("返回码 %s" % code)
            # -404 是"这个 mid 没有合集或系列"，对新 UP 来说很正常，
            # 不该显示成错误
            if code == -404:
                return [], ""
            return [], msg

        it = ((r.get("data") or {}).get("items_lists")) or {}
        seasons = it.get("seasons_list") or []
        series = it.get("series_list") or []

        for x in seasons:
            out.append(_norm_meta(x.get("meta") or {}, "season"))
        for x in series:
            out.append(_norm_meta(x.get("meta") or {}, "series"))

        # 两串都空了就说明到底了。不能只看一边，
        # 有的 UP 只有系列没有合集
        if not seasons and not series:
            break

        # 翻页条件看 items_lists.page
        pg = it.get("page") or {}
        try:
            total = int(pg.get("total") or 0)
        except Exception:
            total = 0
        if page * page_size >= total or not total:
            break
        page += 1
        if page > 50:
            warn("合集列表翻页超过 50 页，停止取用")
            break
        time.sleep(0.2)

    return out, ""


# ============================================================================
#  合集 / 系列 里的视频
# ============================================================================
def fetch_season(bili, mid, season_id, limit=0, section=None, on_page=None):
    """
    取一个合集里的视频，返回 (列表, 错误)。

    section 只保留第 N 个分节（从 1 开始），None 表示全部。
    指定 section 时 limit 会被忽略：分节本身就决定了要哪些，
    再叠一个数量上限只会让人搞不清到底下了什么。
    """
    if section is not None:
        return _fetch_season_section(bili, mid, season_id, section)
    videos, _total = _walk(bili, mid, season_id, "season", limit, on_page)
    return videos, ""


def _fetch_season_section(bili, mid, season_id, section):
    """
    取合集的某个分节。

    分节信息不在 seasons_archives_list 里。实测那个接口的 meta 只有
    category/cover/description/mid/name/ptime/season_id/title/total，
    没有分节。分节只有另一个接口有：web-interface/view 的 ugc_season 字段。

    所以要拿分节，得先取一页列表拿到任意一集的 bvid，
    再用那一集去查它所属的合集的完整分节结构。
    多一次请求，换来 --section 在"从视频进来"和"从 --season-id 进来"
    两条路上行为一致。
    """
    probe, _total = _walk(bili, mid, season_id, "season", 1, None)
    if not probe:
        return [], "这个合集是空的"

    sinfo, vids, e = season_of_video(bili, probe[0]["bvid"])
    if e:
        return [], e
    if not sinfo or not vids:
        return [], "拿不到这个合集的分节信息"

    # 极少数情况下第一集已经不属于这个合集了（合集被改过），
    # 这时分节边界是错的，宁可不做也不要切错
    if str(sinfo.get("id")) != str(season_id):
        return [], ("第一集和合集 %s 对不上（可能合集改过），"
                    "请改用 --pick 按序号挑" % season_id)

    secs = sinfo.get("sections") or []
    got, e = slice_section(vids, secs, section)
    if e:
        return [], e
    return got, ""


def slice_section(videos, sections, n):
    """
    从整份视频列表里切出第 n 个分节，返回 (列表, 错误)。

    分节是按顺序排列的，每节带自己的集数，所以按前缀和切段就行 ——
    不需要再请求一次，也不用相信两个接口的排序一致，
    因为这里的 videos 和 sections 来自同一次 ugc_season 响应。

    抽成独立的纯函数是为了能离线测边界。真实的多分节合集不好找，
    而"切错一节"这种问题只有在有多个分节时才会暴露出来，
    靠碰运气找样本测不牢靠。
    """
    if not sections:
        return [], "这个合集没有分节信息"
    try:
        n = int(n)
    except Exception:
        return [], "分节序号要是数字"
    if n < 1 or n > len(sections):
        return [], ("这个合集只有 %d 个分节，没有第 %d 个"
                    % (len(sections), n))

    base = sum(s.get("count") or 0 for s in sections[:n - 1])
    cnt = sections[n - 1].get("count") or 0
    return videos[base:base + cnt], ""


def fetch_series(bili, mid, series_id, limit=0, on_page=None):
    """取一个系列里的视频，返回 (列表, 错误)"""
    videos, _total = _walk(bili, mid, series_id, "series", limit, on_page)
    return videos, ""


def _walk(bili, mid, cid, kind, limit, on_page):
    """
    两种接口的翻页循环。返回 (视频列表, 总数)。

    分开写是因为字段名真的不一样，硬凑成一个函数会变成一堆 if；
    但两边的"怎么翻、什么时候停、出错怎么办"逻辑是一致的，
    改的时候两边都要看一眼。
    """
    videos = []
    total = 0
    page = 1

    if kind == "season":
        url = API_SEASONS_ARCHIVES
    else:
        url = API_SERIES_ARCHIVES

    while True:
        if kind == "season":
            params = {"mid": mid, "season_id": cid, "sort_reverse": "false",
                      "page_num": page, "page_size": PAGE_SIZE}
        else:
            params = {"mid": mid, "series_id": cid, "only_normal": "true",
                      "sort": "desc", "pn": page, "ps": PAGE_SIZE}
        try:
            r = bili.s.get(url, params=params, timeout=25).json()
        except Exception as e:
            if videos:
                warn("翻页中断：%s（已取到 %d 条）" % (e, len(videos)))
                break
            return [], 0

        if r.get("code") != 0:
            if videos:
                warn("翻页失败：%s（已取到 %d 条）"
                     % (r.get("message"), len(videos)))
                break
            return [], 0

        d = r.get("data") or {}

        for a in (d.get("archives") or []):
            if not a.get("bvid"):
                # 和下架视频同一种情况：编号都没了，留着只会白跑一次请求
                continue
            videos.append({
                "bvid": a.get("bvid"),
                "aid": a.get("aid"),
                "title": a.get("title") or "",
                "cover": a.get("pic") or "",
                "duration": a.get("duration") or 0,
                "up": "",
                "up_mid": a.get("upMid") or mid,
                # 合集接口不返回分P 数。这里给 1 是保守取值：
                # 显示上不会谎报"共几P"，下载时按第 1 个分P 走
                "parts": 1,
                "pubdate": a.get("pubdate") or 0,
            })

        pg = d.get("page") or {}
        try:
            # 合集是 total，系列也是 total。page_num/pn 不用管，
            # 自己数的页码更可靠
            total = int(pg.get("total") or 0)
        except Exception:
            total = 0

        if on_page:
            on_page(page, len(videos), total)

        if limit and len(videos) >= limit:
            videos = videos[:limit]
            break

        if not d.get("archives"):
            break
        if total and page * PAGE_SIZE >= total:
            break
        page += 1
        if page > MAX_PAGES:
            warn("翻页超过 %d 页，停止取用" % MAX_PAGES)
            break
        # 合集几百集时连续请求容易被限，喘一下
        time.sleep(0.2)

    return videos, total


# ============================================================================
#  从一个视频找它所属的合集
# ============================================================================
def season_of_video(bili, text):
    """
    给一个视频链接，返回它所属的合集，返回 (合集信息, 视频列表, 错误)。

    走的是 web-interface/view 的 ugc_season 字段，一次请求拿到全集，
    不用按合集 ID 再翻一遍页。合集信息里带 sections，每节有 title 和 count。

    视频不在任何合集里时返回 (None, [], "")，这不是错误 ——
    大量视频本来就没有合集，报错反而让人以为出问题了。
    """
    bvid, aid, _pg = parse_link(text)
    if not bvid and not aid:
        return None, [], "没能认出视频编号"

    try:
        data, e = bili.video_info(bvid=bvid, aid=aid)
    except Exception as ex:
        return None, [], "请求失败：%s" % ex
    if e or not data:
        return None, [], e or "没取到视频信息"

    us = data.get("ugc_season")
    if not us:
        return None, [], ""

    sections = []
    videos = []
    for idx, s in enumerate(us.get("sections") or [], 1):
        eps = s.get("episodes") or []
        sections.append({"title": s.get("title") or ("第 %d 节" % idx),
                         "count": len(eps)})
        for e in eps:
            if not e.get("bvid"):
                continue
            videos.append({
                "bvid": e.get("bvid"),
                "aid": e.get("aid"),
                "title": e.get("title") or "",
                "cover": (e.get("arc") or {}).get("pic") or "",
                "duration": e.get("arc", {}).get("duration") or 0,
                "up": (data.get("owner") or {}).get("name") or "",
                "up_mid": (data.get("owner") or {}).get("mid"),
                "parts": 1,
                "pubdate": (e.get("arc") or {}).get("pubdate") or 0,
            })

    sinfo = {
        "kind": "season",
        "id": us.get("id"),
        "mid": (data.get("owner") or {}).get("mid"),
        "title": us.get("title") or "未命名合集",
        "count": len(videos),
        "cover": us.get("cover") or "",
        "intro": "",
        "sections": sections,
        # 从哪个视频找过来的。下载时提示里带上它更有用
        "from_bvid": data.get("bvid") or bvid,
    }
    return sinfo, videos, ""


# ============================================================================
#  显示
# ============================================================================
def show_collections(items):
    """打印合集/系列列表，带序号方便挑选"""
    print()
    print(core.C.B + "  共 %d 个合集或系列" % len(items) + core.C.R)
    print("  " + "─" * 68)
    for i, c in enumerate(items, 1):
        tag = "合集" if c["kind"] == "season" else "系列"
        print("   %3d. [%s] %s  （%d 集）"
              % (i, tag, sanitize(c["title"], 44), c["count"]))
        print("        %s id=%s" % (core.C.D, c["id"]))
        print("        %s" % core.C.R, end="")
    print("  " + "─" * 68)


def show_sections(cinfo, videos):
    """
    打印合集的分节。

    分节是合集独有的：一个合集可以分成好几节，
    比如"前期""中期""后期"。用户想只下其中一节时不用自己数序号

    参数叫 cinfo 不叫 info：模块里 info() 是个输出函数，
    用同名参数会把它盖掉，调用时就成了"拿字典当函数调"。
    这类错误只在真跑到那一行时才炸，很难提前发现
    """
    secs = cinfo.get("sections") or []
    if len(secs) <= 1:
        return
    print()
    print(core.C.B + "  这个合集有 %d 个分节：" % len(secs) + core.C.R)
    base = 1
    for i, s in enumerate(secs, 1):
        print("    %2d. %s  （%d 集，序号 %d-%d）"
              % (i, sanitize(s["title"], 40), s["count"],
                 base, base + s["count"] - 1))
        base += s["count"]
    print()
    info("要只下某一节，加 --section <分节序号>")


# ============================================================================
#  交互模式
# ============================================================================
def interactive(bili, cfg, opts):
    """
    交互式浏览合集。

    第一步问"从哪儿找"：给视频链接就顺着视频找它所属的合集，
    给 UP 的 mid 或空间链接就把这个 UP 的合集全列出来。
    两种入口合并成一个问题，比让用户先选模式少一步操作。
    """
    import bili_fav as fav

    print()
    info("可以贴这些东西：")
    info("   视频链接或 BV 号    找出这个视频所属的合集")
    info("   UP 的 mid          列出这个 UP 的全部合集与系列")
    info("   UP 空间链接        https://space.bilibili.com/1234567")
    print()
    try:
        text = safe_input("  粘贴（回车退出）：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not text:
        return 0

    mid, e = parse_mid(text)
    if e:
        err(e)
        return 2

    if mid is None:
        # 是视频链接，先试着直接从它找合集。
        # 这条路的请求更少，而且能拿到分节结构
        step("正在查这个视频所属的合集")
        sinfo, videos, e2 = season_of_video(bili, text)
        if e2:
            err(e2)
            return 1
        if sinfo and videos:
            return _pick_and_download(bili, cfg, opts, sinfo, videos, None)
        if sinfo:
            warn("这个视频在合集「%s」里，但合集没有可取的内容" % sinfo["title"])
            return 1
        # 不在合集里就退一步：列作者的其它合集，让用户自己挑
        info("这个视频不在任何合集里。看一下它作者有哪些合集。")
        mid, e2 = mid_of_video(bili, text)
        if e2:
            err(e2)
            return 1

    return _browse_up(bili, cfg, opts, mid)


def _browse_up(bili, cfg, opts, mid):
    """列出某个 UP 的合集，选一个，再挑视频"""
    import bili_fav as fav

    step("正在读取这个 UP 的合集与系列")
    items, e = list_collections(bili, mid)
    if e:
        err(e)
        return 1
    if not items:
        warn("这个 UP 没有公开的合集或系列。")
        info("有些 UP 用的是「频道」或「播放列表」，那是另一套接口，本工具暂时不支持。")
        return 1

    show_collections(items)
    print()
    try:
        sel = safe_input("  选择序号（回车退出）：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not sel:
        return 0

    idx = fav.parse_selection(sel, len(items))
    if not idx:
        err("序号不对")
        return 2
    c = items[idx[0]]

    step("正在读取「%s」的内容" % c["title"])
    videos, e = _fetch(bili, mid, c)
    if e:
        err(e)
        return 1
    if not videos:
        warn("这个%s是空的" % ("合集" if c["kind"] == "season" else "系列"))
        return 1

    return _pick_and_download(bili, cfg, opts, c, videos, mid)


def _fetch(bili, mid, c):
    """按合集类型取视频"""
    if c.get("kind") == "season":
        return fetch_season(bili, mid, c["id"],
                            on_page=lambda pn, n, t: print(
                                "   第 %d 页，已取 %d/%s 条"
                                % (pn, n, t or "?"), end="\r"))
    return fetch_series(bili, mid, c["id"],
                        on_page=lambda pn, n, t: print(
                            "   第 %d 页，已取 %d/%s 条"
                            % (pn, n, t or "?"), end="\r"))


def _pick_and_download(bili, cfg, opts, info_obj, videos, mid):
    """选定合集之后：显示、挑视频、确认、下载"""
    import bili_fav as fav

    print()
    print(core.C.B + "  「%s」共 %d 集" % (info_obj["title"], len(videos))
          + core.C.R)

    # 分节只在"从视频找到合集"那条路上有（那边的接口带 sections）
    show_sections(info_obj, videos)

    fav.show_videos(videos)
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

    picked = _parse_pick(sel, videos)
    if picked is None:
        return 2
    if not picked:
        return 1

    chosen = [videos[i] for i in picked]
    total_dur = sum(v.get("duration") or 0 for v in chosen)
    print()
    info("准备下载 %d 个视频，合计时长约 %s" % (len(chosen), fmt_dur(total_dur)))
    if not opts.get("all_parts"):
        info("合集接口不返回分P 数，多P 视频只会下第 1 个分P。")
    if not confirm("  确认开始？[y/N] "):
        info("已取消")
        return 0

    core.set_template(opts["template"])
    outdir = core.get_outdir([], cfg)
    res = fav.download_batch(chosen, cfg, opts, outdir)
    return fav.report(res)


def _parse_pick(sel, videos):
    """
    解析交互里的选择表达式。

    返回下标列表；解析不出来返回 None（跟"筛完是空的"区分开，
    前者是用法错误，后者是没匹配到）
    """
    import bili_fav as fav

    s = sel.strip()
    if s.lower() in ("all", "*", "全部"):
        return list(range(len(videos)))
    if s.startswith("/"):
        kw = s[1:].strip()
        got = [i for i, v in enumerate(videos) if kw and kw in v["title"]]
        if not got:
            warn("没有标题包含「%s」的视频" % kw)
        return got
    got = fav.parse_selection(s, len(videos))
    if not got:
        err("没能解析出要下哪些")
        return None
    return got


# ============================================================================
#  命令行模式
# ============================================================================
def main(bili, cfg, argv):
    """
    合集功能的入口。argv 是 --season 或 --up 之后剩下的参数。

    不带参数进交互模式；带了就按参数走命令式流程，
    参数风格跟 --fav 保持一致（同一个 --all / --pick / --search）。
    """
    import bili_fav as fav

    opts = fav.build_opts(cfg, argv)

    # ---- --up / --season 后面跟的东西 ----
    # --up <mid>            列这个 UP 的合集
    # --season <视频链接>    找这个视频所属的合集
    src = None
    for a in argv:
        if a.startswith("-"):
            continue
        src = a
        break

    if not argv or argv[0] in ("-h", "--help"):
        if argv and argv[0] in ("-h", "--help"):
            print(__doc__)
            return 0
        core.set_template(opts["template"])
        return interactive(bili, cfg, opts)

    if not src:
        err("请给出视频链接或 UP 的 mid。加 -h 看用法。")
        return 2

    # 显式指定合集/系列 ID 时不用再去找
    season_id = _opt_int(argv, "--season-id", "--sid")
    series_id = _opt_int(argv, "--series-id", "--rid")

    mid, e = parse_mid(src)
    if e:
        err(e)
        return 2

    if season_id or series_id:
        if mid is None:
            mid, e = mid_of_video(bili, src)
            if e:
                err(e)
                return 1
        kind = "season" if season_id else "series"
        cid = season_id or series_id
        info_obj = {"kind": kind, "id": cid,
                    "title": "%s %s" % ("合集" if kind == "season" else "系列",
                                        cid),
                    "count": 0, "sections": []}
        step("正在读取%s内容" % ("合集" if kind == "season" else "系列"))
        videos, e = (fetch_season(bili, mid, cid,
                                  section=_opt_int(argv, "--section"))
                     if kind == "season"
                     else fetch_series(bili, mid, cid))
        if e:
            err(e)
            return 1
        if not videos:
            warn("里面没有可取的内容")
            return 1
        # 拿到真实标题再显示
        full, _e2 = list_collections(bili, mid)
        for c in full:
            if str(c["id"]) == str(cid) and c["kind"] == kind:
                info_obj["title"] = c["title"]
                info_obj["count"] = c["count"]
                break
        return _finish(bili, cfg, opts, argv, info_obj, videos, mid)

    # 没给 ID：从视频反查，或者列出 UP 的合集让用户挑
    if mid is None:
        step("正在查这个视频所属的合集")
        sinfo, videos, e2 = season_of_video(bili, src)
        if e2:
            err(e2)
            return 1
        if sinfo and videos:
            if _opt_int(argv, "--section") is not None:
                n = _opt_int(argv, "--section")
                secs = sinfo.get("sections") or []
                if n < 1 or n > len(secs):
                    err("这个合集只有 %d 个分节，没有第 %d 个" % (len(secs), n))
                    return 2
                base = sum(s["count"] for s in secs[:n - 1])
                videos = videos[base:base + secs[n - 1]["count"]]
                info_obj = dict(sinfo)
                info_obj["title"] = "%s · %s" % (sinfo["title"],
                                                 secs[n - 1]["title"])
                return _finish(bili, cfg, opts, argv, info_obj, videos, mid)
            return _finish(bili, cfg, opts, argv, sinfo, videos, mid)
        info("这个视频不在任何合集里。")
        mid, e2 = mid_of_video(bili, src)
        if e2:
            err(e2)
            return 1
        info("看一下它作者（mid=%s）有哪些合集。" % mid)
    else:
        step("正在读取这个 UP 的合集与系列")

    items, e = list_collections(bili, mid)
    if e:
        err(e)
        return 1
    if not items:
        warn("这个 UP 没有公开的合集或系列")
        return 1

    # 只列不下
    show_collections(items)
    print()
    info("要继续的话，加上 --season-id 或 --series-id 指定一个，例如：")
    info("  python bili_dl.py --up %s --season-id %s --all"
         % (mid, items[0]["id"]))
    info("  python bili_dl.py --up %s --season-id %s --pick 1,3-5"
         % (mid, items[0]["id"]))
    return 0


def _opt_int(argv, *names):
    """从 argv 里取一个整数选项，取不到返回 None"""
    for i, a in enumerate(argv):
        if a in names and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except Exception:
                continue
    return None


def _finish(bili, cfg, opts, argv, info_obj, videos, mid):
    """选定视频集合之后：筛选、列清单、确认、下载"""
    import bili_fav as fav

    # 关键词筛选，和 --fav 一致
    kw = None
    for i, a in enumerate(argv):
        if a in ("--search", "-s") and i + 1 < len(argv):
            kw = argv[i + 1]
    if kw:
        before = len(videos)
        videos = [v for v in videos if kw in v["title"]]
        info("标题包含「%s」的：%d -> %d 个" % (kw, before, len(videos)))
        if not videos:
            warn("筛选之后没有剩下视频")
            return 1

    pick = None
    for i, a in enumerate(argv):
        if a in ("--pick", "-p") and i + 1 < len(argv):
            pick = argv[i + 1]

    want_all = any(a in ("--all", "-a") for a in argv)
    list_only = any(a in ("--list-only", "--dry", "-n") for a in argv)

    print()
    print(core.C.B + "  「%s」共 %d 集" % (info_obj["title"], len(videos))
          + core.C.R)
    if info_obj.get("sections") and len(info_obj["sections"]) > 1:
        show_sections(info_obj, videos)

    if pick is not None:
        idx = fav.parse_selection(pick, len(videos))
        if not idx:
            err("--pick 没解析出有效序号")
            return 2
        chosen = [videos[i] for i in idx]
    elif want_all:
        chosen = videos
    else:
        fav.show_videos(videos)
        print()
        info("要继续的话，加上 --all 下全部，或 --pick 1,3,5-9 挑着下")
        info("例如： python bili_dl.py --season <链接> --all")
        return 0

    if list_only:
        fav.show_videos(chosen)
        info("--dry 只列不下，共 %d 个" % len(chosen))
        return 0

    total_dur = sum(v.get("duration") or 0 for v in chosen)
    print()
    info("准备下载 %d 个视频，合计时长约 %s" % (len(chosen), fmt_dur(total_dur)))
    if opts["audio_only"]:
        info("模式：仅音频")
    if opts["template"] != "{title}":
        info("命名模板：" + opts["template"])
    if not confirm("  确认开始？[y/N] "):
        info("已取消")
        return 0

    core.set_template(opts["template"])
    outdir = core.get_outdir(argv, cfg)
    res = fav.download_batch(chosen, cfg, opts, outdir)
    return fav.report(res)
