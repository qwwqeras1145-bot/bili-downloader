# -*- coding: utf-8 -*-
"""
验证：服务停止时不会等在跑的请求结束

为什么值得单独测

  ThreadingHTTPServer 默认 block_on_close = True，server_close() 会 join
  每一个请求线程。请求线程里可能正跑着一个几百 MB 的下载，
  于是用户按 Ctrl+C 之后进程要等它下完才退 —— 表现是"按了没反应"。

  这类问题在手工测试里很难碰到（要正好在一个大下载进行中按 Ctrl+C），
  但用户天天遇到。所以这里用一个卡住的请求把它固定下来。
"""
import os
import sys
import json
import time
import socket
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import web_app as web

FAILED = []


def check(cond, label, detail=""):
    if cond:
        print("  [通过] %s" % label)
    else:
        FAILED.append(label)
        print("  [失败] %s%s" % (label, ("   " + str(detail)) if detail else ""))


class SlowHandler(web.Handler):
    """收到请求就睡 30 秒，用来冒充"正在下载大文件"的请求线程"""

    def do_GET(self):
        time.sleep(30)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    print("=" * 72)
    print("停止服务时不等在跑的请求")
    print("=" * 72)

    print()
    print("一、默认行为应当是会等（说明这个开关确实起作用）")
    print("=" * 72)
    check(web.ThreadingHTTPServer.block_on_close is True,
          "标准库默认 block_on_close=True，会被我们覆盖掉")
    check(web.Server.block_on_close is False,
          "Server 关掉了 block_on_close")

    print()
    print("二、实测：一个请求卡住时，关闭服务要多久")
    print("=" * 72)
    port = free_port()
    srv = web.Server(("127.0.0.1", port), SlowHandler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)

    # 发一个请求出去，让它卡在 handler 里
    def fire():
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/x" % port, timeout=40).read()
        except Exception:
            pass

    threading.Thread(target=fire, daemon=True).start()
    time.sleep(0.8)          # 等请求确实进到 handler

    t0 = time.time()
    srv.shutdown()
    srv.server_close()
    dt = time.time() - t0

    check(dt < 3.0,
          "有请求卡住时关闭耗时 %.2f 秒（没有等那 30 秒）" % dt,
          "%.2f 秒" % dt)
    check(not t.is_alive(), "serve_forever 线程已退出")

    print()
    print("三、端口应当已经释放（不然重开服务会撞端口）")
    print("=" * 72)
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            check(True, "原端口可以立刻重新绑定")
        except OSError as e:
            check(False, "原端口可以立刻重新绑定", e)

    print()
    print("四、客户端主动断开不应被当成程序错误")
    print("=" * 72)
    # handle_error 里过滤掉的异常类型
    import inspect
    src = inspect.getsource(web.Server.handle_error)
    for name in ("ConnectionResetError", "BrokenPipeError",
                 "ConnectionAbortedError", "TimeoutError"):
        check(name in src, "handle_error 放过 %s" % name)

    print()
    print("=" * 72)
    if FAILED:
        print("结果   失败 %d" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
    else:
        print("结果   全部通过")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
