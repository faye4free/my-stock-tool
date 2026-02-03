import os
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from deep_translator import GoogleTranslator
except Exception:  # pragma: no cover - optional dependency
    GoogleTranslator = None  # type: ignore

import re
import html as _html


# 最小环境净化：避免被系统代理干扰直连华尔街数据源
for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "all_proxy", "ALL_PROXY"]:
    os.environ.pop(key, None)


@dataclass
class StockQuote:
    symbol: str
    last_price: float
    prev_close: float
    change: float
    change_pct: float  # decimal, e.g. 0.0123 for +1.23%
    as_of: datetime
    is_market_open: bool


def _is_us_market_open(now_utc: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    粗略按美东时间判断是否为盘中。
    """
    if now_utc is None:
        now_utc = datetime.utcnow()
    est = now_utc.astimezone(ZoneInfo("America/New_York"))
    if est.weekday() >= 5:
        return False, "休市（周末）"
    # 9:30 ~ 16:00 视为盘中
    if (est.hour > 9 or (est.hour == 9 and est.minute >= 30)) and est.hour < 16:
        return True, "美股交易时段"
    return False, "盘后/盘前时段"


def _color_for_change(change: float) -> str:
    """
    美股习惯：涨为绿、跌为红。
    """
    if change > 0:
        return "#00FF00"  # green
    if change < 0:
        return "#FF0000"  # red
    return "#666666"


def _format_change_html(change: float, pct: float) -> str:
    pct_100 = pct * 100
    sign = "+" if change > 0 else ""
    color = _color_for_change(change)
    return (
        f"<span style='color:{color}; font-weight:700;'>"
        f"{sign}{change:.2f} ({sign}{pct_100:.2f}%)"
        "</span>"
    )


def _format_relative_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "时间未知"
    # 统一转为北京时区再计算相对时间
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    dt_local = dt.astimezone(ZoneInfo("Asia/Shanghai"))
    diff = now - dt_local
    seconds = diff.total_seconds()
    if seconds < 60:
        return "刚刚"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} 分钟前"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} 小时前"
    days = hours / 24
    return f"{int(days)} 天前"


def fetch_stock_quote(symbol: str) -> StockQuote:
    """
    使用 yfinance 获取最新价和前收盘，支持盘中/收盘/盘前盘后。
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("股票代码不能为空")

    ticker = yf.Ticker(symbol)

    # 先用 2 日收盘价精确确定“前收盘价”
    time.sleep(1)
    hist_2d = ticker.history(period="2d")

    last_close: Optional[float] = None
    prev_close: Optional[float] = None

    if isinstance(hist_2d, pd.DataFrame) and not hist_2d.empty:
        closes = hist_2d["Close"].dropna()
        if len(closes) >= 2:
            prev_close = float(closes.iloc[-2])
            last_close = float(closes.iloc[-1])
        elif len(closes) == 1:
            last_close = float(closes.iloc[-1])

    # 回退到 fast_info/info 获取当前价
    info = getattr(ticker, "fast_info", None) or getattr(ticker, "info", {})
    current_price: Optional[float] = None
    if info:
        # yfinance 新版本 basic_info.lastPrice 更准确，这里兼容处理
        basic_info = getattr(ticker, "basic_info", None)
        if isinstance(basic_info, dict):
            current_price = basic_info.get("lastPrice") or basic_info.get("regularMarketPrice")
        if current_price is None:
            current_price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("last_price")

    # 若未能拿到 last_close，则用 current_price 兜底
    if current_price is not None and last_close is None:
        last_close = float(current_price)

    if last_close is None or prev_close is None or prev_close == 0:
        raise ValueError("无法获取该股票的有效价格数据")

    last_price = float(last_close)
    change = last_price - prev_close
    change_pct = change / prev_close

    is_open, _ = _is_us_market_open()
    return StockQuote(
        symbol=symbol,
        last_price=last_price,
        prev_close=prev_close,
        change=change,
        change_pct=change_pct,
        as_of=datetime.utcnow(),
        is_market_open=is_open,
    )


def fetch_news(symbol: str, limit: int = 8):
    """
    使用 yfinance 的 news 接口抓取最近新闻。
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return []

    # 过去 7 天时间窗
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=7)

    def _strip_html(raw: str) -> str:
        """Remove HTML tags and unescape entities to avoid <a href=...> 等原始片段."""
        text = _html.unescape(raw or "")
        # 简单去标签，避免把内容整段去掉
        text = re.sub(r"<[^>]+>", " ", text)
        # 折叠多余空白
        return re.sub(r"\s+", " ", text).strip()

    def _from_yfinance() -> List[dict]:
        ticker = yf.Ticker(symbol)
        time.sleep(1)
        news_items = getattr(ticker, "news", None) or []
        items: List[dict] = []
        for item in news_items:
            ts = item.get("providerPublishTime") or item.get("providerPublishTimeEpoch") or item.get("time") or 0
            try:
                dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                continue
            if dt_utc < cutoff:
                continue
            title = item.get("title") or ""
            link = item.get("link") or ""
            publisher = item.get("publisher") or item.get("source") or "Unknown"
            # 优先使用 summary/content 作为摘要，并去掉 HTML
            summary_raw = item.get("summary") or item.get("content") or ""
            summary = _strip_html(summary_raw)
            items.append(
                {
                    "title": title,
                    "link": link,
                    "publisher": publisher,
                    "time": dt_utc,
                    "summary": summary,
                }
            )
        return items

    def _from_google_rss() -> List[dict]:
        try:
            import requests
            import xml.etree.ElementTree as ET
            from email.utils import parsedate_to_datetime
        except Exception:
            return []

        try:
            time.sleep(1)
            q = f"{symbol} stock"
            url = (
                "https://news.google.com/rss/search"
                f"?q={q}&hl=en-US&gl=US&ceid=US:en"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            text = resp.text
        except Exception:
            return []

        items: List[dict] = []
        try:
            root = ET.fromstring(text)
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                desc_raw = item.findtext("description") or ""
                pub = item.findtext("source") or "Google News"
                pub_date = item.findtext("pubDate") or ""
                try:
                    dt_utc = parsedate_to_datetime(pub_date)
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if dt_utc < cutoff:
                    continue
                summary = _strip_html(desc_raw)

                items.append(
                    {
                        "title": _html.unescape(title),
                        "link": link,
                        "publisher": pub,
                        "time": dt_utc,
                        "summary": summary,
                    }
                )
        except Exception:
            return []
        return items

    # 1) 先尝试 yfinance
    items = []
    try:
        items = _from_yfinance()
    except Exception:
        items = []

    # 2) 若 7 天内为空，尝试 Google News RSS 降级
    if not items:
        try:
            items = _from_google_rss()
        except Exception:
            items = []

    # 截断为最多 8 条
    if len(items) > limit:
        items = items[:limit]

    # 3) 汉化 title 与 summary
    translator = None
    if GoogleTranslator is not None:
        try:
            translator = GoogleTranslator(source="auto", target="zh-CN")
        except Exception:
            translator = None

    def _translate(text: str) -> Tuple[str, bool]:
        text = (text or "").strip()
        if not text or translator is None:
            return text, False
        try:
            return translator.translate(text), True
        except Exception:
            return text, False

    for item in items:
        title_en = item.get("title") or ""
        summary_en = item.get("summary") or ""

        title_zh, ok_title = _translate(title_en)
        summary_zh, ok_sum = _translate(summary_en) if summary_en else ("", False)

        if not ok_title and not ok_sum and (title_en or summary_en):
            # 标注翻译失败
            title_zh = title_en + "（翻译暂不可用）"
            summary_zh = summary_en

        item["title_zh"] = title_zh or title_en
        item["summary_zh"] = summary_zh or summary_en

    return items


# ---------------------- Streamlit 页面 ---------------------- #

st.set_page_config(page_title="美股实时行情与 AI 资讯", layout="centered")
st.title("美股个股实时行情与 AI 资讯")
st.caption("输入美股代码（如 AAPL / TSLA / NVDA），查看实时价格与深度新闻。")

query = st.text_input("输入美股代码", value="AAPL", max_chars=10)
btn = st.button("开始查询", type="primary")

if not btn:
    st.info("请输入美股代码并点击“开始查询”。")
    st.stop()

with st.spinner("正在调取华尔街实时数据，并汇总全球财经动态..."):
    try:
        quote = fetch_stock_quote(query)
    except Exception:
        st.error("未找到该股票，请检查代码是否正确。")
        st.stop()

    # 盘前/盘后价格补充展示
    ticker = yf.Ticker(quote.symbol)
    info = getattr(ticker, "fast_info", None) or getattr(ticker, "info", {}) or {}
    pre_price = info.get("preMarketPrice")
    post_price = info.get("postMarketPrice")

    try:
        news_list = fetch_news(quote.symbol, limit=8)
    except Exception:
        news_list = []

is_open, session_label = _is_us_market_open()
price_label = "盘中价格" if is_open else "盘后/收盘价"

st.subheader("股价信息 (Stock Info)")

st.markdown(
    f"<div style='font-size: 22px; font-weight: 700;'>{quote.symbol}</div>",
    unsafe_allow_html=True,
)

st.markdown(
    f"<div style='font-size: 32px; font-weight: 700;'>{quote.last_price:.2f} USD</div>",
    unsafe_allow_html=True,
)

st.markdown(_format_change_html(quote.change, quote.change_pct), unsafe_allow_html=True)
st.caption(f"{price_label} · 前收盘：{quote.prev_close:.2f} USD · {session_label}")

if pre_price:
    pre_chg = float(pre_price) - quote.prev_close
    pre_pct = pre_chg / quote.prev_close if quote.prev_close else 0.0
    color = _color_for_change(pre_chg)
    sign = "+" if pre_chg > 0 else ""
    st.markdown(
        f"<span style='font-size: 13px; color:{color};'>盘前 {float(pre_price):.2f} "
        f"({sign}{pre_pct*100:.2f}%)</span>",
        unsafe_allow_html=True,
    )
elif post_price:
    post_chg = float(post_price) - quote.prev_close
    post_pct = post_chg / quote.prev_close if quote.prev_close else 0.0
    color = _color_for_change(post_chg)
    sign = "+" if post_chg > 0 else ""
    st.markdown(
        f"<span style='font-size: 13px; color:{color};'>盘后 {float(post_price):.2f} "
        f"({sign}{post_pct*100:.2f}%)</span>",
        unsafe_allow_html=True,
    )

st.divider()
st.subheader("深度资讯 (News Analytics)")

if not news_list:
    st.info("该股票近 7 天暂无重大公开资讯。")
else:
    for item in news_list:
        title = item.get("title_zh") or item.get("title") or "(无标题)"
        publisher = item.get("publisher") or "Unknown"
        t = item.get("time")
        rel_time = _format_relative_time(t)
        link = item.get("link") or ""
        summary = (item.get("summary_zh") or item.get("summary") or "").strip()

        # 控制摘要长度，避免在手机端过长
        if summary:
            summary_lines = summary.splitlines()
            summary = "\n".join(summary_lines[:2])

        with st.container(border=True):
            # 标题（中文），字体加粗加大
            st.markdown(f"### {title}")

            # 简介（摘要，中文）
            if summary:
                st.write(summary)

            # 来源与时间
            st.caption(f"{publisher} · {rel_time}")

            # 查看原文按钮
            if link:
                st.link_button("查看原文", link)

