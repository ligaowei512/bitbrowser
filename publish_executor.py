#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自媒体多平台自动发稿执行器 (v1 完整部署版)
==========================================

【架构定位】
飞书多维表格(内容中枢) ──飞书自动化 Webhook──▶ 本执行器 ──▶ 各平台官方 API ──▶ 回写发布状态

【v1 支持的平台（有正规开放接口，真·自动）】
  - 百家号  : 官方内容 API (app_id + app_token)
  - 网易号  : 网易号媒体开放平台 (access_token + 签名)
  - 头条号  : 字节/头条开放平台 OAuth2 (access_token 仅 2h，需刷新)

【其余平台（无官方接口）】v1 不自动发，只把"需人工发布"写回回执，由你人工/草稿发布。

==========================================================
部署步骤（新手向）
----------------------------------------------------------
1) 安装依赖      : pip install -r requirements.txt
2) 配置密钥      : cp .env.example .env  然后逐项填好（密钥只放 .env / 云函数环境变量，绝不硬编码）
3) 启动 Webhook  : python publish_executor.py --serve      （Flask 监听 0.0.0.0:PORT）
   或定时跑一次  : python publish_executor.py --once       （扫描"已通过"记录并发布，可用云函数定时器/cron 触发）
4) 飞书联动      : 多维表格自动化「当 审核状态 = 已通过」→ 发送 Webhook → 填你的服务地址 + /publish

==========================================================
环境变量（见 .env.example）
  FEISHU_APP_ID / FEISHU_APP_SECRET            飞书应用凭证
  FEISHU_BITABLE_APP_TOKEN / FEISHU_TABLE_ID  多维表格位置
  BJH_APP_ID / BJH_TOKEN                      百家号
  WY_TOKEN / WY_CLASSIFY                      网易号
  TT_TOKEN / TT_REFRESH_*                     头条号（access_token 2h，生产需配刷新）
  FEISHU_ALERT_WEBHOOK                        飞书群机器人 Webhook（失败告警用，可选）
  FIELD_*                                      字段名映射（默认已按通用中文列名，可在 .env 覆盖）
"""

import os
import sys
import json
import time
import logging
import traceback
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------------------
# 日志（同时输出到控制台与本地文件，便于排障）
# ----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("publish_executor.log", encoding="utf-8")],
)
log = logging.getLogger("publisher")

# ----------------------------------------------------------------------------
# 配置（来自环境变量）
# ----------------------------------------------------------------------------
FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
APP_TOKEN = os.environ["FEISHU_BITABLE_APP_TOKEN"]
TABLE_ID = os.environ["FEISHU_TABLE_ID"]
ALERT_WEBHOOK = os.environ.get("FEISHU_ALERT_WEBHOOK", "")

# 字段名映射（如你的列名不同，在 .env 里覆盖即可，无需改代码）
FIELD_TITLE = os.environ.get("FIELD_TITLE", "选题/标题")
FIELD_CONTENT = os.environ.get("FIELD_CONTENT", "初稿")
FIELD_PLATFORMS = os.environ.get("FIELD_PLATFORMS", "目标平台")
FIELD_STATUS = os.environ.get("FIELD_STATUS", "审核状态")
FIELD_RECEIPT = os.environ.get("FIELD_RECEIPT", "发布回执")
FIELD_PUBTIME = os.environ.get("FIELD_PUBTIME", "发布时间")

STATUS_PENDING = "已通过"   # 人工确认后的发布触发状态
STATUS_DONE = "已发布"
STATUS_FAILED = "失败"

# ----------------------------------------------------------------------------
# 通用工具：指数退避重试（外网调用必备）
# ----------------------------------------------------------------------------
def with_retry(func, max_attempts=3, base_delay=1.0):
    """最多重试 max_attempts 次，间隔按 2^i 秒指数退避。"""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                log.warning("调用失败(第%d次)，%ss 后重试: %s", attempt + 1, delay, exc)
                time.sleep(delay)
    raise last_exc


# ----------------------------------------------------------------------------
# 飞书多维表格客户端（鉴权 + 读写）
# ----------------------------------------------------------------------------
class FeishuBitable:
    BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._expire_at = 0

    def _ensure_token(self):
        if self._token and time.time() < self._expire_at - 60:
            return
        resp = requests.post(
            f"{self.BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        ).json()
        if resp.get("code") != 0:
            raise RuntimeError(f"飞书鉴权失败: {resp}")
        self._token = resp["data"]["tenant_access_token"]
        self._expire_at = time.time() + resp["data"]["expire"] - 60
        log.info("飞书 tenant_access_token 已刷新")

    @property
    def _headers(self):
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def get_records(self, page_size=100):
        """分页拉取数据表全部记录（小批量内容场景足够；大数据量按需加过滤/游标）。"""
        records, page_token = [], None
        while True:
            url = f"{self.BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records?page_size={page_size}"
            if page_token:
                url += f"&page_token={page_token}"
            resp = requests.get(url, headers=self._headers, timeout=10).json()
            if resp.get("code") != 0:
                raise RuntimeError(f"读取多维表格失败: {resp}")
            records.extend(resp["data"].get("items", []))
            if not resp["data"].get("has_more"):
                break
            page_token = resp["data"].get("page_token")
        return records

    def update_record(self, record_id, fields):
        url = f"{self.BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{record_id}"
        resp = requests.put(url, headers=self._headers, json={"fields": fields}, timeout=10).json()
        if resp.get("code") != 0:
            raise RuntimeError(f"回写多维表格失败: {resp}")
        return resp

    def filter_pending(self, records, now=None):
        """筛出"已通过"且（若设了发布时间）已到点的记录。"""
        now = now or datetime.now()
        result = []
        for r in records:
            f = r.get("fields", {})
            if f.get(FIELD_STATUS) != STATUS_PENDING:
                continue
            pub_time = f.get(FIELD_PUBTIME)
            if pub_time:  # 飞书日期字段形如 "2026-09-24" 或带时间
                try:
                    pt = self._parse_feishu_time(pub_time)
                    if pt and pt > now:
                        continue  # 未到发布时间，跳过
                except Exception:
                    log.warning("发布时间解析失败，按立即发布处理: %s", pub_time)
            result.append(r)
        return result

    @staticmethod
    def _parse_feishu_time(s):
        s = str(s).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None


# ----------------------------------------------------------------------------
# 多平台发布器（v1：百家号 / 网易号 / 头条号 官方 API）
# ----------------------------------------------------------------------------
class Publisher:
    def publish(self, platform, title, content, origin_url=""):
        p = (platform or "").strip()
        if "百家" in p:
            return self.baijiahao(title, content, origin_url)
        if "网易" in p:
            return self.wangyi(title, content)
        if "头条" in p:
            return self.toutiao(title, content)
        raise ValueError(f"v1 未支持平台「{p}」：无官方接口，请在后台人工/草稿发布")

    def baijiahao(self, title, content, origin_url=""):
        # 官方图文发布接口；is_original 视账号资质而定
        payload = {
            "app_id": os.environ["BJH_APP_ID"],
            "app_token": os.environ["BJH_TOKEN"],
            "title": title,
            "content": content,
            "origin_url": origin_url,
            "is_original": os.environ.get("BJH_IS_ORIGINAL", "1"),
        }
        resp = with_retry(lambda: requests.post(
            "https://baijiahao.baidu.com/builderinner/open/resource/article/publish",
            json=payload, timeout=15,
        ))
        data = resp.json()
        if data.get("errno", data.get("code", -1)) not in (0, None) and not data.get("msg", "").startswith("成功"):
            raise RuntimeError(f"百家号返回: {data}")
        return {"platform": "百家号", "raw": data}

    def wangyi(self, title, content):
        # 网易号媒体开放平台：access_token + 签名已封装在 SDK；此处用最简 FORM 调用
        payload = {
            "title": title,
            "content": content,  # HTML 富文本
            "user_classify": os.environ.get("WY_CLASSIFY", "科技"),
            "original": os.environ.get("WY_ORIGINAL", "false"),
            "access_token": os.environ["WY_TOKEN"],
        }
        resp = with_retry(lambda: requests.post(
            "http://mp.163.com/wemedia/article/status/api/oauth2/publish.do",
            data=payload, timeout=15,
        ))
        data = resp.json()
        if data.get("code") != 1:
            raise RuntimeError(f"网易号返回: {data}")
        return {"platform": "网易号", "doc_id": data.get("data", {}).get("docId"), "raw": data}

    def toutiao(self, title, content):
        # 头条开放平台 OAuth2：access_token 仅 2h，生产环境务必接刷新（见 get_toutiao_token）
        token = self.get_toutiao_token()
        payload = {
            "title": title[:30],        # 头条标题限 30 字
            "content": content,
            "cover_type": int(os.environ.get("TT_COVER_TYPE", "0")),
            "status": 1,                # 1=发布, 0=存草稿
        }
        resp = with_retry(lambda: requests.post(
            "https://developer.toutiao.com/api/v2/article",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=15,
        ))
        data = resp.json()
        if data.get("code") not in (0, None) or data.get("message") not in (None, "", "success"):
            raise RuntimeError(f"头条号返回: {data}")
        return {"platform": "头条号", "raw": data}

    @staticmethod
    def get_toutiao_token():
        """头条 access_token 有效期 2 小时。
        - 最简：直接读环境变量 TT_TOKEN（适合测试，过期需手动换）。
        - 生产：在下方填入你的 OAuth 刷新逻辑（client_id/secret + refresh_token 换新 token）。
        当前未配置刷新时，返回环境变量值并在日志提示。
        """
        token = os.environ.get("TT_TOKEN", "")
        if not token:
            raise RuntimeError("缺少 TT_TOKEN（头条号 access_token）")
        # TODO(生产): 在此调用刷新接口，例如：
        #   r = requests.post("https://open.toutiao.com/oauth2/refresh_token", ...)
        #   token = r.json()["access_token"]
        return token


# ----------------------------------------------------------------------------
# 失败告警（飞书群机器人 Webhook；可选）
# ----------------------------------------------------------------------------
def alert(title, detail=""):
    if not ALERT_WEBHOOK:
        log.warning("[告警未发送] 未配置 FEISHU_ALERT_WEBHOOK。%s | %s", title, detail)
        return
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": f"发稿异常：{title}"}, "template": "red"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": detail or "请检查执行器日志"}}],
        },
    }
    try:
        requests.post(ALERT_WEBHOOK, json=card, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.error("告警发送失败: %s", exc)


# ----------------------------------------------------------------------------
# 单条记录处理流程：读取 → 遍历平台发布 → 回写状态/回执
# ----------------------------------------------------------------------------
def process_record(fs: FeishuBitable, record: dict):
    rid = record["record_id"]
    f = record.get("fields", {})
    title = (f.get(FIELD_TITLE) or "").strip()
    content = f.get(FIELD_CONTENT) or ""
    platforms = f.get(FIELD_PLATFORMS) or []
    if isinstance(platforms, str):
        platforms = [platforms]

    if not title or not content:
        raise ValueError("缺少标题或初稿内容，无法发布")

    pub = Publisher()
    lines = []
    any_success = False
    for p in platforms:
        try:
            res = pub.publish(p, title, content, origin_url="")
            lines.append(f"OK {p}：成功 {res.get('doc_id') or res.get('platform')}")
            any_success = True
            log.info("发布成功 | %s | %s", p, title)
        except Exception as exc:  # noqa: BLE001
            msg = f"FAIL {p}：{exc}"
            lines.append(msg)
            log.error("发布失败 | %s | %s | %s", p, title, exc)
            alert(f"{p} 发布失败", f"标题：{title}\n错误：{exc}\n{traceback.format_exc()[-500:]}")

    status = STATUS_DONE if any_success else STATUS_FAILED
    fs.update_record(rid, {FIELD_STATUS: status, FIELD_RECEIPT: "\n".join(lines)})
    return status


# ----------------------------------------------------------------------------
# 主流程：扫描并发布所有"已通过"记录
# ----------------------------------------------------------------------------
def run_once():
    fs = FeishuBitable(FEISHU_APP_ID, FEISHU_APP_SECRET)
    records = fs.get_records()
    pending = fs.filter_pending(records)
    log.info("扫描到待发布记录 %d 条", len(pending))
    if not pending:
        return {"scanned": len(records), "published": 0}
    published = 0
    for r in pending:
        try:
            st = process_record(fs, r)
            if st == STATUS_DONE:
                published += 1
        except Exception as exc:  # noqa: BLE001
            log.error("记录处理异常 record_id=%s: %s", r.get("record_id"), exc)
            alert("记录处理异常", f"record_id={r.get('record_id')}\n错误：{exc}")
    log.info("本次发布完成：成功 %d / 待处理 %d", published, len(pending))
    return {"scanned": len(records), "published": published}


# ----------------------------------------------------------------------------
# Webhook 入口（Flask）：飞书自动化「状态=已通过 → 发 Webhook」打到 /publish
# ----------------------------------------------------------------------------
def create_app():
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        raise RuntimeError("缺少 flask，请先 pip install flask")
    app = Flask(__name__)

    @app.route("/publish", methods=["POST"])
    def publish_webhook():
        try:
            body = request.get_json(silent=True) or {}
            rid = body.get("record_id")
            if rid:
                fs = FeishuBitable(FEISHU_APP_ID, FEISHU_APP_SECRET)
                rec = next((r for r in fs.get_records() if r["record_id"] == rid), None)
                if rec:
                    process_record(fs, rec)
                else:
                    return jsonify({"ok": False, "error": "record not found"}), 404
            else:
                run_once()
            return jsonify({"ok": True})
        except Exception as exc:  # noqa: BLE001
            alert("Webhook 处理异常", str(exc))
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"ok": True, "service": "publisher", "time": datetime.now().isoformat()})

    return app


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if mode == "once":
        run_once()
    elif mode == "serve":
        port = int(os.environ.get("PORT", "5000"))
        create_app().run(host="0.0.0.0", port=port)
    else:
        print("用法: python publish_executor.py [serve|once]")
        sys.exit(1)
