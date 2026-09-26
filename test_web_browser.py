# -*- coding: utf-8 -*-
"""
用真实浏览器驱动网页版，验证"点一下就能用"

做法是启动 Edge 的远程调试端口，通过 CDP 协议操作页面：
点击按钮、读取界面上的文字，看它有没有按预期反应。

为什么值得单独做这一步
  接口测试通过只说明后端没问题，JS 写错了照样点不动。
  DOM 导出能证明脚本执行过，但证明不了点击事件绑定正确。
  只有真的点一下才知道。
"""
import json
import time
import subprocess
import urllib.request
import sys
import os

try:
    import websockets.sync.client as ws_client
except ImportError:
    try:
        import websockets as _ws
        ws_client = None
    except ImportError:
        print("需要 websockets 库： python -m pip install websockets")
        sys.exit(2)

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9222
PAGE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899/"

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
        print("  [失败] %s%s" % (label, ("   " + detail) if detail else ""))


def http_json(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP:
    def __init__(self, ws_url):
        # legacy=True 是为了拿一个可以直接用的连接对象。
        # 新版 websockets 推荐用 with 上下文管理器，但这里连接要跨越多个方法，
        # 手动 close 更顺手
        self.ws = ws_client.connect(ws_url, max_size=None, legacy=True)
        self.id = 0

    def send(self, method, params=None):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        deadline = time.time() + 30
        while time.time() < deadline:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == mid:
                return msg
        return {}

    def js(self, expr, timeout=30):
        """执行 JS 并返回值"""
        r = self.send("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True,
            "timeout": timeout * 1000,
        })
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return None
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main():
    print("=" * 72)
    print("浏览器端到端验证")
    print("=" * 72)

    if not os.path.exists(EDGE):
        print("找不到 Edge: %s" % EDGE)
        return 2

    tmpdir = os.path.join(os.environ.get("TEMP", "."), "edge_cdp_profile")
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu",
        "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=%s" % tmpdir,
        "--no-first-run", "--no-default-browser-check",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # 等调试端口起来
        ok = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                http_json("/json/version")
                ok = True
                break
            except Exception:
                continue
        if not ok:
            print("Edge 调试端口未能启动")
            return 2
        print("Edge 已就绪（远程调试端口 %d）" % PORT)

        # 拿现有标签页来用。
        # 不用 /json/new 是因为它在新版浏览器里要求 PUT，各版本行为不一致；
        # 直接复用启动时的 about:blank，再用 Page.navigate 跳转，最稳
        targets = http_json("/json/list")
        page_tgt = None
        for t in targets:
            if t.get("type") == "page":
                page_tgt = t
                break
        if not page_tgt:
            print("找不到可用的页面标签")
            return 2
        ws_url = page_tgt.get("webSocketDebuggerUrl")
        if not ws_url:
            print("拿不到页面调试地址")
            return 2

        cdp = CDP(ws_url)
        cdp.send("Runtime.enable")
        cdp.send("Page.enable")
        cdp.send("Page.navigate", {"url": PAGE})

        # 等页面加载完
        for _ in range(40):
            time.sleep(0.5)
            if cdp.js("document.readyState") == "complete":
                break
        time.sleep(2)

        # 装上错误收集器。必须赶在下面这些点击之前装，
        # 否则"页面没有 JS 报错"这句其实什么都没验证
        cdp.js(
            "window.__errs=[];"
            "window.addEventListener('error',function(e){"
            "window.__errs.push('error: '+(e.message||'')+' @'+(e.filename||'')+':'+(e.lineno||0))});"
            "window.addEventListener('unhandledrejection',function(e){"
            "window.__errs.push('reject: '+String(e.reason))});"
            "'installed'")

        print()
        print("一、页面加载与元素就位")
        print("=" * 72)
        check(cdp.js("document.title") is not None, "页面标题可读")
        check(cdp.js("!!document.getElementById('url')"), "链接输入框存在")
        check(cdp.js("!!document.getElementById('go')"), "下载按钮存在")
        check(cdp.js("!!document.getElementById('jobs')"), "下载记录区存在")
        check(cdp.js("!!document.getElementById('qrart')"), "二维码容器存在")

        print()
        print("二、脚本初始化（这些内容由 JS 填充）")
        print("=" * 72)
        opts = cdp.js("document.getElementById('qn').options.length")
        check(isinstance(opts, int) and opts > 0,
              "清晰度下拉已填充 %s 个选项" % opts)
        first = cdp.js("document.getElementById('qn').options[0].textContent")
        check("自动" in (first or ""), "首项是自动模式", str(first))
        login = cdp.js("document.getElementById('loginstate').textContent") or ""
        check("登录" in login, "登录状态已显示: %s" % login.strip())

        print()
        print("三、点击下载按钮，观察界面反应")
        print("=" * 72)
        # 填入链接再点，走的是和用户完全一样的路径
        cdp.js("document.getElementById('url').value='BV17x411w7KC'")
        val = cdp.js("document.getElementById('url').value")
        check(val == "BV17x411w7KC", "链接已填入输入框", str(val))

        cdp.js("document.getElementById('go').click()")

        # 按钮应立刻被禁用，防止重复提交。
        #
        # 这里不能用"睡 1.5 秒再看一眼"的写法：如果下载目录里已经有
        # 上一次留下的成品，整个流程可能一秒内就跑完，按钮已经恢复了，
        # 于是明明正确也会报失败。改成在短窗口内轮询"有没有被禁用过"
        ever_disabled = False
        for _ in range(20):
            time.sleep(0.05)
            if cdp.js("document.getElementById('go').disabled") is True:
                ever_disabled = True
                break
        check(ever_disabled, "点击后按钮立即禁用（防重复提交）")

        msg = cdp.js("document.getElementById('msg').textContent") or ""
        check(bool(msg.strip()), "界面给出了状态提示: %s" % msg.strip()[:40])

        # 等解析结果
        info = ""
        for _ in range(30):
            time.sleep(1)
            info = cdp.js("document.getElementById('info').textContent") or ""
            if info.strip():
                break
        check(bool(info.strip()), "视频信息已显示到界面")
        if info.strip():
            print("      界面显示: %s" % " ".join(info.split())[:100])

        opts2 = cdp.js("document.getElementById('qn').options.length")
        check(isinstance(opts2, int) and opts2 >= 1,
              "解析后清晰度选项已按本视频刷新（%s 项）" % opts2)

        # 进度条区域应出现
        check(cdp.js("document.getElementById('prog').style.display") == "block",
              "进度区域已显示")

        print()
        print("四、等待下载完成，检查进度与记录更新")
        print("=" * 72)
        state_txt = ""
        done = False
        for i in range(90):
            time.sleep(2)
            state_txt = cdp.js("document.getElementById('sright').textContent") or ""
            msg = cdp.js("document.getElementById('msg').textContent") or ""
            jobs = cdp.js("document.getElementById('jobs').textContent") or ""
            if "下载完成" in msg or "完成" in msg:
                done = True
                break
            if "失败" in msg or "错误" in msg:
                break
            if i % 5 == 0 and state_txt:
                print("      进度: %s" % " ".join(state_txt.split())[:70])

        check(done, "界面显示下载完成", (msg or "")[:60])

        # 回来后按钮应恢复可点
        for _ in range(20):
            if cdp.js("document.getElementById('go').disabled") is False:
                break
            time.sleep(0.5)
        check(cdp.js("document.getElementById('go').disabled") is False,
              "下载结束后按钮恢复可用")

        # 进度条应到 100%
        w = cdp.js("document.getElementById('barfill').style.width")
        check(w == "100%", "进度条走到 100%%", str(w))

        # 下载记录里应出现可点的下载链接
        jobs_html = cdp.js("document.getElementById('jobs').innerHTML") or ""
        check("/download?id=" in jobs_html, "下载记录里出现了取文件链接")

        print()
        print("五、检查页面是否有 JS 报错")
        print("=" * 72)
        # 用 Performance 里的资源条目确认外部资源都是本机
        ext = cdp.js(
            "(function(){var a=performance.getEntriesByType('resource')"
            ".filter(function(r){return r.name.indexOf('http://127.0.0.1')!==0"
            " && r.name.indexOf('http://localhost')!==0});"
            "return a.map(function(r){return r.name}).slice(0,5)})()")
        check(not ext, "页面没有加载任何外部资源（离线可用）", str(ext))

        print()
        print("六、收藏夹：载入、勾选、批量下载")
        print("=" * 72)
        check(cdp.js("!!document.getElementById('folder')"), "收藏夹下拉框存在")
        check(cdp.js("!!document.getElementById('favlist')"), "视频列表容器存在")

        # 附加选项应能被 JS 读到。这组开关是网页版和命令行版共用的
        opts = cdp.js("JSON.stringify(getOpts())")
        check(isinstance(opts, str) and "template" in opts,
              "getOpts() 能读出一组附加选项: %s" % (opts or "")[:60])

        # 点「载入收藏夹」，走的是用户真实路径
        cdp.js("document.getElementById('fload').click()")
        n = 0
        for _ in range(40):
            time.sleep(0.5)
            n = cdp.js("document.getElementById('folder').options.length") or 0
            # 注意是 >1：下拉框里本来就有 1 个占位项，
            # 用 >0 判断会在请求还没回来时就跳出循环
            if n > 1:
                break
        check(n > 1, "收藏夹下拉框已填充 %s 个收藏夹" % n,
              cdp.js("document.getElementById('favstate').textContent"))

        if n > 1:
            # 挑视频数最少的那个，别让测试去拉几千条
            idx = cdp.js(
                "(function(){var s=document.getElementById('folder');"
                "var best=0,bn=1e9;"
                "for(var i=0;i<s.options.length;i++){"
                "var m=/（(\\d+) 个）/.exec(s.options[i].textContent);"
                "var v=m?parseInt(m[1],10):1e9;"
                "if(v<bn){bn=v;best=i}}"
                "s.selectedIndex=best;"
                "return s.options[best].textContent})()")
            print("      选中: %s" % idx)

            cdp.js("document.getElementById('folder').onchange()")
            cnt = 0
            for _ in range(60):
                time.sleep(0.5)
                cnt = cdp.js("document.querySelectorAll('#favlist .fav').length") or 0
                if cnt > 0:
                    break
            check(cnt > 0, "视频列表已渲染 %s 行" % cnt)

            if cnt > 0:
                rows = cdp.js(
                    "Array.prototype.map.call(document.querySelectorAll('#favlist .fav'),"
                    "function(r){return r.dataset.i}).join(',')")
                check(bool(rows), "每行都带下标: %s" % (rows or "")[:40])

                # 单行「下载」按钮必须先于全选验证，否则选中状态会干扰判断
                check(cdp.js("!!document.querySelector('#favlist .fav .dl')"),
                      "每行都有单独下载按钮")

                # 全选 -> 按钮文字应带上数量
                cdp.js("document.getElementById('fall').click()")
                txt = cdp.js("document.getElementById('fdl').textContent") or ""
                picked = cdp.js("document.querySelectorAll('#favlist .ck:checked').length")
                check(picked == cnt, "全选后勾选了 %s / %s 行" % (picked, cnt))
                check(("（%d）" % cnt) in txt, "按钮上显示了选中数量: %s" % txt)

                # 清空 -> 再点批量下载应给出提示而不是提交
                cdp.js("document.getElementById('fnone').click()")
                check(cdp.js("document.querySelectorAll('#favlist .ck:checked').length") == 0,
                      "清空后没有勾选")
                cdp.js("document.getElementById('fdl').click()")
                time.sleep(0.6)
                m2 = cdp.js("document.getElementById('msg').textContent") or ""
                check("勾选" in m2 or "选中" in m2,
                      "一个都没选时给出提示: %s" % m2.strip()[:30])

                # 反选 -> 应重新全勾上
                cdp.js("document.getElementById('finv').click()")
                check(cdp.js("document.querySelectorAll('#favlist .ck:checked').length") == cnt,
                      "反选后勾选数回到 %s" % cnt)

                # 真的走一次批量下载，选一个小收藏夹不该太久
                cdp.js("document.getElementById('fdl').click()")
                bt = ""
                for _ in range(90):
                    time.sleep(1)
                    bt = cdp.js("document.getElementById('btbox').textContent") or ""
                    if "全部结束" in bt:
                        break
                check("全部结束" in bt, "批量进度面板跑到结束")
                if bt:
                    print("      批量面板: %s" % " ".join(bt.split())[:110])
                check(cdp.js("!!document.querySelector('#btbox .bar')"), "批量面板有进度条")
                check(cdp.js("document.querySelectorAll('#btbox .it').length") == cnt,
                      "批量面板列出的条数与选中数一致")

        print()
        print("七、页面无 JS 报错")
        print("=" * 72)
        errs = cdp.js("JSON.stringify(window.__errs||[])") or "[]"
        check(errs == "[]", "整个流程没有 JS 报错或未处理的 Promise 拒绝", errs[:200])

        cdp.close()

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print()
    print("=" * 72)
    print("结果   通过 %d   失败 %d" % (PASS, FAIL))
    for b in BAD:
        print("  - " + b)
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
