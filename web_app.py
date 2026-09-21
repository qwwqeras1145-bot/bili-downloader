# -*- coding: utf-8 -*-
r"""
===============================================================================
 Fairy III 型  ·  B 站视频下载器    网页版
===============================================================================

 一个本机运行的小型 Web 服务。启动后在浏览器里粘贴链接就能下载，
 下载由本机服务完成，不经过任何第三方服务器。

 用法
   python web_app.py                 默认 127.0.0.1:8848，并自动打开浏览器
   python web_app.py --port 9000     换端口
   python web_app.py --no-browser    不自动开浏览器
   python web_app.py --out D:\视频    指定下载目录

 依赖
   只需要 requests。界面与 HTTP 服务都用 Python 标准库实现，
   不需要 Flask，也不需要装任何前端框架。

===============================================================================
 为什么做成"本机服务 + 网页界面"而不是纯前端网页
===============================================================================

 纯前端方案（一个 HTML 文件直接用浏览器打开）在这件事上走不通，原因有三个：

 1. 跨域限制
    B 站的播放地址接口不返回允许跨域的响应头，浏览器里的 fetch 会被拦下。
    这是浏览器的安全策略，前端代码绕不过去（也不该去绕）。

 2. 登录凭证放不下
    扫码登录拿到的 Cookie 必须存在某个地方。纯前端只能存 localStorage，
    一旦把页面分享出去或换台电脑，凭证就跟着走，很不安全。

 3. 大文件与断点续传
    浏览器下载动辄几百 MB 的文件，中途断了无法续传，也拿不到稳定的本地路径。

 所以这里让本机跑一个服务：请求由服务端发出（不受跨域限制），
 凭证存在服务端的用户目录里，下载由服务端完成并支持断点续传。
 浏览器只负责显示和触发。服务只监听 127.0.0.1，外部网络访问不到。

===============================================================================
 使用须知   与命令行版相同
===============================================================================

 1. 仅供个人学习、研究与备份你有权保存的内容使用。
 2. 请遵守哔哩哔哩用户协议与著作权法。不要用于传播、二次上传或商业用途。
 3. 本工具不下载付费番剧、会员专属内容，不绕过任何付费或权限校验。
 4. 请勿高频批量抓取。
 5. 下载他人作品后，版权仍归原作者所有。

===============================================================================
"""

import os
import sys
import json
import time
import uuid
import html
import shutil
import socket
import argparse
import threading
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 把同目录的核心模块引入进来。命令行版和网页版共用同一套下载逻辑，
# 不重复实现，修一处两边都好
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import bili_dl as core
except ImportError:
    print("错误。找不到 bili_dl.py，请把它和 web_app.py 放在同一个目录。")
    sys.exit(1)

APP_TITLE = "B站视频下载器 · 网页版"
APP_VER = "1.0"

# 下载任务默认存两个文件：视频轨与音频轨各自一个。
# 这里只接已合并的 MP4，DASH 分轨在网页版里提示用户改用命令行版
MAX_JOBS = 50               # 内存里最多保留多少个历史任务
JOB_TTL = 6 * 3600          # 任务记录保留多久（秒）


# ============================================================================
#  下载任务
# ============================================================================
class Job:
    """
    一次下载的状态。

    之所以要单独记这个：下载是后台线程跑的，网页需要不停问"好了没"。
    把状态放在这个对象里，HTTP 处理函数读它就行，不用去碰线程内部。
    """

    def __init__(self, url, title, outdir, qn=None):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.title = title
        self.outdir = outdir
        self.qn = qn
        self.state = "pending"      # pending / running / done / error / canceled
        self.message = "等待开始"
        self.done_bytes = 0
        self.total_bytes = 0
        self.speed = 0.0
        self.path = None
        self.error = None
        self.created = time.time()
        self.finished = None
        self._lock = threading.Lock()
        self._last_bytes = 0
        self._last_tick = time.time()

    # ---- 状态更新。加锁是因为后台线程在写，HTTP 线程在读 ----
    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def add_bytes(self, n):
        with self._lock:
            self.done_bytes += n

    def snapshot(self):
        """给网页用的状态快照"""
        with self._lock:
            return {
                "id": self.id,
                "title": self.title,
                "url": self.url,
                "state": self.state,
                "message": self.message,
                "done": self.done_bytes,
                "total": self.total_bytes,
                "speed": self.speed,
                "path": self.path,
                "error": self.error,
                "pct": round(self.done_bytes * 100.0 / self.total_bytes, 1)
                       if self.total_bytes else 0,
                "done_h": core.fmt_size(self.done_bytes),
                "total_h": core.fmt_size(self.total_bytes) if self.total_bytes else "--",
                "speed_h": (core.fmt_size(self.speed) + "/s") if self.speed else "--",
            }

    def refresh_speed(self):
        """按固定间隔估算速度，避免每次请求都算一次导致数字乱跳"""
        now = time.time()
        dt = now - self._last_tick
        if dt < 0.5:
            return
        with self._lock:
            self.speed = max(0.0, (self.done_bytes - self._last_bytes) / dt)
            self._last_bytes = self.done_bytes
            self._last_tick = now


class JobManager:
    """任务的登记与查询。进程内存储，重启就清空，不落盘"""

    def __init__(self):
        self.jobs = {}
        self._lock = threading.Lock()

    def create(self, url, title, outdir, qn=None):
        job = Job(url, title, outdir, qn)
        with self._lock:
            self.jobs[job.id] = job
            # 超过上限就把最老的清掉，避免长时间运行后内存一直涨
            if len(self.jobs) > MAX_JOBS:
                for k in sorted(self.jobs, key=lambda x: self.jobs[x].created)[:10]:
                    if self.jobs[k].state in ("done", "error", "canceled"):
                        self.jobs.pop(k, None)
        return job

    def get(self, jid):
        return self.jobs.get(jid)

    def all(self):
        with self._lock:
            return [j.snapshot() for j in
                    sorted(self.jobs.values(), key=lambda x: x.created, reverse=True)]

    def cleanup(self):
        """清掉过期的历史任务"""
        now = time.time()
        with self._lock:
            for k in list(self.jobs):
                j = self.jobs[k]
                if j.finished and now - j.finished > JOB_TTL:
                    self.jobs.pop(k, None)


JOBS = JobManager()


# ============================================================================
#  下载执行
# ============================================================================
class CountingSession:
    """
    包一层 requests.Session，用来数下载了多少字节。

    为什么不改 download_file 去回调：那个函数是命令行版在用的，
    为了网页版改它的签名会牵连另一处。这里用装饰的方式，
    把 iter_content 换成一个会记账的版本就行，核心逻辑一行不动。
    """

    def __init__(self, session, job):
        self._s = session
        self._job = job
        self.cookies = session.cookies

    def get(self, url, headers=None, stream=False, timeout=None, **kw):
        resp = self._s.get(url, headers=headers, stream=stream, timeout=timeout, **kw)
        if stream and resp.status_code in (200, 206):
            # 从 Content-Length 推总量，接口给的 size 更准但未必有
            cl = resp.headers.get("Content-Length")
            base = 0
            rng = (headers or {}).get("Range")
            if rng and rng.startswith("bytes="):
                try:
                    base = int(rng.split("=")[1].split("-")[0])
                except Exception:
                    base = 0
            if cl and cl.isdigit():
                total = int(cl) + base
                if not self._job.total_bytes:
                    self._job.set(total_bytes=total)
            resp.iter_content = self._wrap_iter(resp.iter_content)
        return resp

    def _wrap_iter(self, original):
        def gen(chunk_size=1024):
            for chunk in original(chunk_size=chunk_size):
                if chunk:
                    self._job.add_bytes(len(chunk))
                    self._job.refresh_speed()
                yield chunk
        return gen

    def __getattr__(self, name):
        return getattr(self._s, name)


def run_download(job, bvid, cid, title):
    """
    后台线程里跑的下载流程。

    整个过程包在 try 里。这里抛异常不会有人接，
    线程会静默死掉、任务永远停在"下载中"，网页就一直转圈。
    所以无论出什么事都要把任务置成终态
    """
    try:
        bili = core.Bili()
        job.set(state="running", message="正在获取下载地址")

        play, err = bili.playurl(bvid, cid, qn=job.qn)
        if err:
            job.set(state="error", error=err, message="获取地址失败", finished=time.time())
            return
        if not play:
            job.set(state="error", error="没有可用的下载地址", finished=time.time())
            return

        # DASH 分轨需要 ffmpeg 合并，网页版不做这件事，
        # 直接说明并让用户改用命令行版。硬要在网页里拼轨只会做出个半成品
        if play.get("kind") != "merged":
            msg = ("该视频只提供 DASH 分轨，网页版不支持合并音视频。"
                   "请改用命令行版：python bili_dl.py <链接>")
            job.set(state="error", error=msg, message="需要命令行版处理",
                    finished=time.time())
            return

        parts = play.get("parts") or []
        if not parts:
            job.set(state="error", error="下载地址为空", finished=time.time())
            return

        q = play.get("quality")
        qname = core.QUALITY_NAME.get(q, str(q))
        job.set(message="清晰度 %s" % qname)

        total = sum(sz for _u, sz in parts)
        job.set(total_bytes=total)

        os.makedirs(job.outdir, exist_ok=True)

        # 单分片就直接下，多分片先下到临时目录再拼
        session = CountingSession(bili.s, job)
        if len(parts) == 1:
            url, _size = parts[0]
            out = os.path.join(job.outdir, core.sanitize(title) + ".mp4")
            ok, _got = core.download_file(session, url, out, "下载中", total)
            if not ok:
                job.set(state="error", error="下载未完成，可重新点击继续",
                        message="已中断", finished=time.time())
                return
            job.set(state="done", path=out, message="下载完成", finished=time.time())
            return

        tmpdir = os.path.join(job.outdir, ".parts_%s" % job.id)
        os.makedirs(tmpdir, exist_ok=True)
        got_files = []
        try:
            for i, (url, size) in enumerate(parts, 1):
                p = os.path.join(tmpdir, "p%03d" % i)
                job.set(message="分片 %d/%d" % (i, len(parts)))
                before = job.done_bytes
                ok, _g = core.download_file(session, url, p, "分片%d" % i, size)
                if not ok:
                    job.set(state="error", error="分片 %d 下载失败" % i,
                            message="已中断", finished=time.time())
                    return
                got_files.append(p)
            out = os.path.join(job.outdir, core.sanitize(title) + ".mp4")
            with open(out, "wb") as of:
                for p in got_files:
                    with open(p, "rb") as f:
                        shutil.copyfileobj(f, of, 1024 * 1024)
            job.set(state="done", path=out, message="下载完成", finished=time.time())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    except Exception as e:
        import traceback
        job.set(state="error", error="%s: %s" % (type(e).__name__, e),
                message="出现异常", finished=time.time())
        traceback.print_exc()


# ============================================================================
#  界面
# ============================================================================
def page_html():
    """
    整个界面就是这一张页面。

    内联了 CSS 和 JS，不引任何外部资源 —— 包括字体和图标库。
    原因是这个服务只在本机跑，没必要为了一份界面去连 CDN，
    而且离线环境下外部资源加载失败会让页面变得很难看
    """
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#12161c; --bg2:#1a2028; --bg3:#232b35; --fg:#d8e0ea;
    --dim:#7b8794; --acc:#4ec9b0; --warn:#e2b341; --err:#e06c75; --ok:#98c379;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.6 "Microsoft YaHei UI","Microsoft YaHei",系统-ui,sans-serif}
  .wrap{max-width:900px;margin:0 auto;padding:24px 20px 60px}
  h1{font-size:20px;margin:0 0 4px;color:var(--acc);font-weight:600}
  .sub{color:var(--dim);font-size:12px;margin-bottom:18px}
  .card{background:var(--bg2);border-radius:10px;padding:18px;margin-bottom:16px}
  .notice{background:var(--bg2);border-left:3px solid var(--warn);border-radius:6px;
          padding:12px 16px;margin-bottom:18px;color:var(--dim);font-size:12px}
  .notice b{color:var(--warn)}
  label{display:block;color:var(--dim);font-size:12px;margin-bottom:6px}
  input[type=text],select{width:100%;padding:10px 12px;border-radius:7px;
    border:1px solid #2b3542;background:var(--bg3);color:var(--fg);font-size:14px;
    font-family:inherit;outline:none}
  input[type=text]:focus,select:focus{border-color:var(--acc)}
  .row{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
  .row>div{flex:1;min-width:150px}
  button{padding:10px 20px;border:0;border-radius:7px;background:var(--bg3);
    color:var(--fg);font-size:14px;font-family:inherit;cursor:pointer;transition:.15s}
  button:hover{background:#3a4756}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.primary{background:var(--acc);color:#0d1117;font-weight:600}
  button.primary:hover{background:#63d6bd}
  .info{margin-top:16px;padding-top:16px;border-top:1px solid #2b3542}
  .info .t{font-size:16px;font-weight:600;margin-bottom:8px;word-break:break-all}
  .meta{color:var(--dim);font-size:12px}
  .meta span{margin-right:16px;display:inline-block}
  .bar{height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;margin:10px 0 6px}
  .bar>i{display:block;height:100%;background:var(--acc);width:0;transition:width .25s}
  .stat{color:var(--dim);font-size:12px;display:flex;justify-content:space-between}
  .msg{padding:10px 12px;border-radius:7px;margin-top:12px;font-size:13px;display:none}
  .msg.show{display:block}
  .msg.err{background:rgba(224,108,117,.12);color:var(--err)}
  .msg.ok{background:rgba(152,195,121,.12);color:var(--ok)}
  .msg.warn{background:rgba(226,179,65,.12);color:var(--warn)}
  .joblist{margin-top:6px}
  .job{padding:10px 0;border-bottom:1px solid #232b35;font-size:13px}
  .job:last-child{border-bottom:0}
  .job .n{display:flex;justify-content:space-between;gap:12px}
  .job .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .job .st{color:var(--dim);font-size:12px;flex-shrink:0}
  .job a{color:var(--acc);text-decoration:none}
  .job a:hover{text-decoration:underline}
  .qr{display:none;text-align:center;margin-top:14px}
  .qr.show{display:block}
  .qr pre{display:inline-block;background:#000;color:#fff;padding:12px;border-radius:8px;
          line-height:1;font-size:6px;letter-spacing:0;margin:0}
  .qr .tip{color:var(--dim);font-size:12px;margin-top:10px}
  .hint{color:var(--dim);font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">

  <h1>__TITLE__</h1>
  <div class="sub">本机运行 · 下载由本机完成 · 不经过第三方服务器</div>

  <div class="notice">
    <b>使用须知</b>　仅供个人学习与备份你有权保存的内容。请遵守哔哩哔哩用户协议与著作权法，
    不要用于传播、二次上传或商业用途。本工具不下载付费番剧与会员专属内容，
    也不绕过任何付费或权限校验。请勿高频批量抓取。
  </div>

  <div class="card">
    <label>视频链接</label>
    <input type="text" id="url" placeholder="粘贴链接，或直接贴 BV 号 / av 号 / b23.tv 短链" autocomplete="off">
    <div class="row">
      <div>
        <label>清晰度</label>
        <select id="qn"><option value="">自动（取账号可用的最高档）</option></select>
      </div>
      <div style="display:flex;align-items:flex-end">
        <button class="primary" id="go" style="width:100%">解析并下载</button>
      </div>
    </div>
    <div class="hint" id="loginstate"></div>
    <div id="msg" class="msg"></div>
    <div id="info" class="info" style="display:none"></div>
    <div id="prog" style="display:none">
      <div class="bar"><i id="barfill"></i></div>
      <div class="stat"><span id="sleft"></span><span id="sright"></span></div>
    </div>
    <div class="qr" id="qrbox">
      <div class="tip" id="qrtip"></div>
      <pre id="qrart"></pre>
      <div class="tip">用哔哩哔哩手机客户端扫码</div>
    </div>
  </div>

  <div class="card">
    <label style="margin-bottom:10px">下载记录</label>
    <div class="joblist" id="jobs"><div class="meta">还没有下载记录。</div></div>
    <div class="row" style="margin-top:12px">
      <div><button id="refresh" style="width:100%">刷新</button></div>
      <div><button id="login" style="width:100%">扫码登录</button></div>
      <div><button id="logout" style="width:100%">退出登录</button></div>
    </div>
  </div>

</div>

<script>
const $ = id => document.getElementById(id);
let curJob = null, timer = null, qrTimer = null;

function showMsg(text, kind){
  const m = $('msg');
  m.className = 'msg show' + (kind ? ' ' + kind : '');
  m.textContent = text;
}
function hideMsg(){ $('msg').className = 'msg'; }

function fmtDur(sec){
  sec = parseInt(sec || 0, 10);
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  const p = n => String(n).padStart(2,'0');
  return h ? (h + ':' + p(m) + ':' + p(s)) : (m + ':' + p(s));
}

async function jget(path){
  const r = await fetch(path);
  return await r.json();
}

// ---- 登录状态 ----
async function loadLogin(){
  try{
    const d = await jget('/api/login');
    $('loginstate').textContent = d.logged
      ? ('已登录：' + d.name + (d.vip ? '（大会员）' : ''))
      : '未登录。游客清晰度上限通常是 1080P，登录后可取更高。';
  }catch(e){ $('loginstate').textContent = ''; }
}

// ---- 清晰度下拉：按本视频可选档位填充 ----
function fillQuality(accept){
  const sel = $('qn');
  sel.innerHTML = '<option value="">自动（取账号可用的最高档）</option>';
  (accept || []).forEach(q => {
    const names = __QNAMES__;
    const o = document.createElement('option');
    o.value = q;
    o.textContent = (names[q] || ('编号 ' + q));
    sel.appendChild(o);
  });
}

// ---- 下载记录 ----
async function loadJobs(){
  try{
    const d = await jget('/api/jobs');
    const box = $('jobs');
    if(!d.jobs.length){ box.innerHTML = '<div class="meta">还没有下载记录。</div>'; return; }
    box.innerHTML = d.jobs.map(j => {
      let action = '';
      if(j.state === 'done' && j.path){
        const nm = j.path.split(/[\\\\/]/).pop();
        action = ' <a href="/download?id=' + j.id + '">下载文件</a>';
      }
      let st = j.state;
      if(j.state === 'running') st = '下载中 ' + j.pct + '%';
      else if(j.state === 'done') st = '已完成';
      else if(j.state === 'error') st = '失败';
      const err = (j.state === 'error' && j.error)
        ? '<div class="st" style="color:var(--err)">' + j.error + '</div>' : '';
      return '<div class="job"><div class="n"><span class="name">' +
        j.title + '</span><span class="st">' + st + action + '</span></div>' + err + '</div>';
    }).join('');
  }catch(e){}
}

// ---- 进度轮询 ----
function startPoll(jobId){
  curJob = jobId;
  $('prog').style.display = 'block';
  if(timer) clearInterval(timer);
  timer = setInterval(async () => {
    try{
      const j = await jget('/api/status?id=' + jobId);
      $('barfill').style.width = (j.pct || 0) + '%';
      $('sleft').textContent = j.message || '';
      $('sright').textContent = j.total
        ? (j.done_h + ' / ' + j.total_h + '　' + j.speed_h)
        : (j.done_h + '　' + j.speed_h);
      if(j.state === 'done'){
        clearInterval(timer); timer = null;
        $('barfill').style.width = '100%';
        showMsg('下载完成。点下方下载记录里的链接保存文件。', 'ok');
        loadJobs();
        $('go').disabled = false;
      } else if(j.state === 'error'){
        clearInterval(timer); timer = null;
        showMsg(j.error || '下载失败', 'err');
        loadJobs();
        $('go').disabled = false;
      }
    }catch(e){}
  }, 800);
}

// ---- 主流程 ----
$('go').onclick = async () => {
  const url = $('url').value.trim();
  if(!url){ showMsg('请先粘贴链接', 'warn'); return; }
  $('go').disabled = true;
  hideMsg();
  $('info').style.display = 'none';
  $('prog').style.display = 'none';
  showMsg('正在解析…');

  let d;
  try{
    const r = await fetch('/api/prepare', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url: url, qn: $('qn').value || null})
    });
    d = await r.json();
  }catch(e){
    showMsg('请求失败：' + e, 'err'); $('go').disabled = false; return;
  }

  if(d.error){ showMsg(d.error, 'err'); $('go').disabled = false; return; }

  fillQuality(d.accept);
  $('info').style.display = 'block';
  $('info').innerHTML =
    '<div class="t">' + d.title + '</div>' +
    '<div class="meta"><span>作者　' + d.owner + '</span>' +
    '<span>时长　' + fmtDur(d.duration) + '</span>' +
    '<span>清晰度　' + d.quality + '</span>' +
    '<span>' + d.kind + '</span></div>';

  if(d.warn) showMsg(d.warn, 'warn'); else showMsg('开始下载…', 'ok');
  startPoll(d.job);
  loadJobs();
};

// ---- 扫码登录 ----
$('login').onclick = async () => {
  hideMsg();
  let d;
  try{ d = await jget('/api/qr/start'); }
  catch(e){ showMsg('申请二维码失败：' + e, 'err'); return; }
  if(d.error){ showMsg(d.error, 'err'); return; }

  $('qrart').textContent = d.art;
  $('qrbox').className = 'qr show';
  $('qrtip').textContent = '等待扫码…';

  if(qrTimer) clearInterval(qrTimer);
  qrTimer = setInterval(async () => {
    try{
      const s = await jget('/api/qr/poll');
      if(s.state === 'ok'){
        clearInterval(qrTimer); qrTimer = null;
        $('qrbox').className = 'qr';
        showMsg('登录成功：' + (s.name || ''), 'ok');
        loadLogin();
      } else if(s.state === 'scanned'){
        $('qrtip').textContent = '已扫码，请在手机上确认';
      } else if(s.state === 'expired'){
        clearInterval(qrTimer); qrTimer = null;
        $('qrbox').className = 'qr';
        showMsg('二维码已过期，请重新点击扫码登录', 'warn');
      }
    }catch(e){}
  }, 1500);
};

$('logout').onclick = async () => {
  await fetch('/api/logout', {method:'POST'});
  showMsg('已清除本机保存的登录凭证', 'ok');
  loadLogin();
};

$('refresh').onclick = loadJobs;
$('url').addEventListener('keydown', e => { if(e.key === 'Enter') $('go').click(); });

fillQuality(__ACCEPT__);
loadLogin();
loadJobs();
</script>
</body>
</html>
"""


def render_page(accept_qualities):
    """把动态部分填进模板。用简单替换而不是格式化，避免和 CSS 里的花括号冲突"""
    qnames = {str(k): v for k, v in core.QUALITY_NAME.items()}
    return (page_html()
            .replace("__TITLE__", html.escape(APP_TITLE))
            .replace("__QNAMES__", json.dumps(qnames, ensure_ascii=False))
            .replace("__ACCEPT__", json.dumps(sorted(accept_qualities, reverse=True))))


# ============================================================================
#  HTTP 服务
# ============================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "BiliWeb/" + APP_VER
    protocol_version = "HTTP/1.1"

    # 登录流程的临时状态，挂在类上就够了，单用户本机服务不需要更复杂的东西
    qr_key = None
    qr_state = "idle"       # idle / waiting / scanned / ok / expired
    qr_name = ""

    def log_message(self, fmt, *a):
        """
        日志过滤。

        默认实现会把每个请求都打到终端，包括轮询状态这种每秒一次的，
        一屏全是 "GET /api/status 200"，真正有用的错误反而被淹掉。
        这里只保留出错的和非 GET 的请求。
        """
        try:
            msg = fmt % a
        except Exception:
            msg = str(fmt)
        # 成功响应不记
        if '" 2' in msg or '" 3' in msg:
            if self.command == "GET":
                return
        sys.stderr.write("  %s\n" % msg)

    # ---- 工具 ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # 请求体上限。这个服务只接受一个链接，正常请求几百字节，
    # 给到 64KB 已经很宽松
    MAX_BODY = 64 * 1024

    def _read_json(self):
        """
        读请求体并解析成 JSON。

        这里有个必须处理的细节：不管请求体多大，都要把它从 socket 里读干净。
        HTTP/1.1 默认长连接，只读一部分就返回的话，剩下的字节会被当成
        下一个请求的开头，导致后续请求解析错乱 —— 表现为连接莫名卡住。
        超过上限的照读不误，只是读完丢掉，并按 413 明确拒绝。

        返回 (数据, 是否超限)
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            return {}, False

        if n <= 0:
            return {}, False

        if n > self.MAX_BODY:
            # 读完并丢弃，保住连接的一致性
            left = n
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
            return {}, True

        try:
            raw = self.rfile.read(n)
            if not raw:
                return {}, False
            return json.loads(raw.decode("utf-8")), False
        except Exception:
            return {}, False

    def _same_origin(self):
        """
        只接受来自本机的请求。

        这个服务跑在 127.0.0.1 上，外部本来就访问不到。
        但浏览器里的其他网页可以向本机地址发请求，
        所以要挡一下 Origin 不是本机的跨站请求。
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            host = urlparse(origin).hostname or ""
        except Exception:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    # ---- 路由 ----
    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._html(render_page(list(core.QUALITY_NAME.keys())))
            return

        if path == "/api/login":
            try:
                b = core.Bili()
                logged, name, vip = b.whoami()
            except Exception as e:
                self._json({"logged": False, "name": "", "vip": 0, "error": str(e)})
                return
            self._json({"logged": bool(logged), "name": name, "vip": vip})
            return

        if path == "/api/jobs":
            JOBS.cleanup()
            self._json({"jobs": JOBS.all()})
            return

        if path == "/api/status":
            q = parse_qs(urlparse(self.path).query)
            job = JOBS.get((q.get("id") or [""])[0])
            if not job:
                self._json({"error": "任务不存在"}, 404)
                return
            self._json(job.snapshot())
            return

        if path == "/api/qr/start":
            self._qr_start()
            return

        if path == "/api/qr/poll":
            self._qr_poll()
            return

        if path == "/download":
            self._serve_file()
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if not self._same_origin():
            self._json({"error": "拒绝跨站请求"}, 403)
            return

        if path == "/api/prepare":
            self._prepare()
            return

        if path == "/api/logout":
            try:
                core.Bili().clear_cookies()
            except Exception:
                pass
            self._json({"ok": True})
            return

        self._json({"error": "not found"}, 404)

    # ---- 各接口实现 ----
    def _prepare(self):
        """
        解析链接并启动下载任务。

        这里只负责"看懂链接 + 建任务 + 起线程"，拿到地址的活儿交给后台线程，
        否则解析和取地址都要等网络，浏览器会一直转圈
        """
        data = self._read_json()
        if isinstance(data, tuple):
            data, too_big = data
            if too_big:
                self._json({"error": "请求体过大。这个接口只接受一个链接，"
                                     "正常请求不会超过 64KB。"}, 413)
                return
        url = (data.get("url") or "").strip()
        qn = data.get("qn")
        try:
            qn = int(qn) if qn not in (None, "", "null") else None
        except Exception:
            qn = None

        if not url:
            self._json({"error": "请先粘贴链接"})
            return

        bvid, aid, pg = core.parse_link(url)
        if not bvid and not aid:
            self._json({"error": "没能从这段内容里找到视频标识。"
                                 "支持完整链接、BV 号、av 号、b23.tv 短链。"})
            return

        try:
            b = core.Bili()
            info, err = b.video_info(bvid=bvid, aid=aid)
        except Exception as e:
            self._json({"error": "请求失败：%s" % e})
            return

        if err or not info:
            self._json({"error": "获取视频信息失败：%s" % (err or "未知原因")})
            return

        pages = info.get("pages") or []
        if not pages:
            self._json({"error": "该视频没有可下载的分P"})
            return

        # 分P 处理：默认第 1 个，链接里带 ?p= 就按它走。
        # 网页上做一整套分P 选择意义不大，需要精细控制请用命令行版
        idx = min(max(1, pg), len(pages)) - 1
        p = pages[idx]
        cid = p.get("cid")

        title = info.get("title") or "video"
        if len(pages) > 1:
            part = p.get("part") or ""
            title = "%s [P%d]%s" % (title, idx + 1, (" " + part) if part else "")

        # 先问一次清晰度，既能拿到可选档位填下拉框，也能让用户看到实际会下哪一档
        try:
            accept = []
            r = b.s.get(core.API_PLAYURL,
                        params={"bvid": info.get("bvid") or bvid, "cid": cid,
                                "qn": 16, "fnval": 1, "fnver": 0,
                                "fourk": 1, "platform": "html5"},
                        timeout=20).json()
            if r.get("code") == 0:
                for x in ((r.get("data") or {}).get("accept_quality") or []):
                    try:
                        accept.append(int(x))
                    except Exception:
                        continue
        except Exception:
            accept = []

        logged, uname, vip = b.whoami()
        best = max(accept) if accept else 80
        want = qn if qn else best
        qname = core.QUALITY_NAME.get(want, str(want))

        warn = None
        if qn and qn not in accept and accept:
            warn = ("你指定的是 %s，但本账号对该视频最高可到 %s。"
                    "实际会按可用的最高档下载。" % (
                        core.QUALITY_NAME.get(qn, str(qn)),
                        core.QUALITY_NAME.get(best, str(best))))
        elif not logged:
            warn = "未登录，清晰度上限通常是 1080P。点下方「扫码登录」可解锁更高档。"

        job = JOBS.create(url, title, self.server.outdir, qn)
        job.set(message="准备中", total_bytes=0)

        t = threading.Thread(target=run_download,
                             args=(job, info.get("bvid") or bvid, cid, title),
                             daemon=True)
        t.start()

        self._json({
            "job": job.id,
            "title": title,
            "owner": (info.get("owner") or {}).get("name") or "未知",
            "duration": p.get("duration") or info.get("duration") or 0,
            "quality": qname,
            "kind": "已合并 MP4",
            "accept": sorted(accept, reverse=True),
            "warn": warn,
        })

    def _qr_start(self):
        """申请登录二维码，并在服务端画成字符画返回给网页"""
        try:
            b = core.Bili()
            r = b.s.get(core.API_QR_GEN, timeout=15).json()
            if r.get("code") != 0:
                self._json({"error": "申请二维码失败：%s" % r.get("message")})
                return
            url = r["data"]["url"]
            key = r["data"]["qrcode_key"]

            Handler.qr_key = key
            Handler.qr_state = "waiting"

            qr = core.QRCode(url, ecc="M")
            qr.build()
            art = "\n".join(qr.to_terminal(quiet_zone=1))
            self._json({"art": art, "state": "waiting"})
        except Exception as e:
            self._json({"error": "申请二维码失败：%s" % e})

    def _qr_poll(self):
        """查一次扫码状态。网页每 1.5 秒问一次"""
        if not Handler.qr_key:
            self._json({"state": "idle"})
            return
        try:
            b = core.Bili()
            p = b.s.get(core.API_QR_POLL,
                        params={"qrcode_key": Handler.qr_key}, timeout=15).json()
            data = p.get("data") or {}
            sub = data.get("code")

            if sub == core.QR_POLL_OK:
                # 复用命令行版的收尾逻辑，凭证处理和它保持一致
                if b._finish_qr(data):
                    Handler.qr_state = "ok"
                    Handler.qr_key = None
                    _, name, _vip = b.whoami()
                    Handler.qr_name = name
                    self._json({"state": "ok", "name": name})
                else:
                    Handler.qr_state = "expired"
                    Handler.qr_key = None
                    self._json({"state": "expired"})
                return

            if sub == core.QR_POLL_EXPIRED:
                Handler.qr_state = "expired"
                Handler.qr_key = None
                self._json({"state": "expired"})
                return

            if sub == core.QR_POLL_SCANNED:
                Handler.qr_state = "scanned"
                self._json({"state": "scanned"})
                return

            self._json({"state": "waiting"})
        except Exception as e:
            self._json({"state": "waiting", "error": str(e)})

    def _serve_file(self):
        """
        把下载好的文件发给浏览器。

        支持 Range 请求，这样浏览器拖动进度条时不用把整个文件重下一次。
        路径只从任务表里取，不接受请求方传路径 ——
        否则就是一个任意文件读取漏洞
        """
        q = parse_qs(urlparse(self.path).query)
        job = JOBS.get((q.get("id") or [""])[0])

        if not job or job.state != "done" or not job.path:
            self._json({"error": "文件不存在或尚未下载完成"}, 404)
            return

        path = job.path
        if not os.path.isfile(path):
            self._json({"error": "文件已被移动或删除"}, 404)
            return

        size = os.path.getsize(path)
        name = os.path.basename(path)
        # 文件名里可能有中文和特殊字符，按 RFC 5987 编码，浏览器才能正确显示
        from urllib.parse import quote
        disp = "attachment; filename*=UTF-8''%s" % quote(name)

        start, end = 0, size - 1
        rng = self.headers.get("Range")
        partial = False
        if rng and rng.startswith("bytes="):
            try:
                spec = rng[6:].split(",")[0].strip()
                s, _, e = spec.partition("-")
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                elif e:
                    start = max(0, size - int(e))
                if start > end or start >= size:
                    # 范围不合法，按标准回 416。
                    #
                    # 这里必须带上 Content-Length: 0。HTTP/1.1 默认长连接，
                    # 不发 Content-Length 也不发 chunked，客户端会一直等一个
                    # 永远不来的响应体，表现为下载工具卡死 —— 比直接报错更糟
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    return
                end = min(end, size - 1)
                partial = True
            except Exception:
                start, end, partial = 0, size - 1, False

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", disp)
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()

        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # 浏览器取消下载或关掉页面，属于正常情况，不用报错
            pass


# ============================================================================
#  启动
# ============================================================================
def pick_port(preferred):
    """
    找一个能用的端口。

    默认端口被占用时自动往后试几个。不这样做的话用户会遇到
    "Address already in use" 然后不知道怎么办
    """
    for p in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def main():
    ap = argparse.ArgumentParser(
        description="B站视频下载器 网页版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="启动后浏览器会自动打开。按 Ctrl+C 停止服务。")
    ap.add_argument("--port", type=int, default=8848, help="监听端口，默认 8848")
    ap.add_argument("--out", default=None, help="下载目录，默认与命令行版相同")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址，默认只监听本机。改成 0.0.0.0 会让同网段其他设备也能访问，"
                         "请确认你了解风险后再改")
    args = ap.parse_args()

    outdir = args.out or core.DEFAULT_OUTDIR
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        print("无法创建下载目录 %s：%s" % (outdir, e))
        return 2

    port = pick_port(args.port)
    if port is None:
        print("端口 %d 起连续 20 个都被占用，请用 --port 指定其他端口。" % args.port)
        return 2

    httpd = ThreadingHTTPServer((args.host, port), Handler)
    httpd.outdir = outdir
    url = "http://127.0.0.1:%d/" % port

    print()
    print("=" * 70)
    print("  %s v%s" % (APP_TITLE, APP_VER))
    print("=" * 70)
    print("  地址      %s" % url)
    print("  下载目录  %s" % outdir)
    if args.port != port:
        print("  注意      端口 %d 被占用，已改用 %d" % (args.port, port))
    if args.host != "127.0.0.1":
        print("  注意      正在监听 %s，同网段的其他设备可以访问本服务" % args.host)
    print()
    print("  按 Ctrl+C 停止服务")
    print("=" * 70)
    print()

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        print("  正在停止…")
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        print("  已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
