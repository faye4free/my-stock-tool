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

# 尝试导入翻译库，如果没有也不报错
try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

import re
import html as _html


# ==============================================================================
# ⚠️ 关键修复：注释掉了之前的环境清洗代码
# 既然你使用了美国梯子 (VPN)，保留系统代理设置是必须的，否则 Python 无法联网
# ==============================================================================
# for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "all_proxy", "ALL_PROXY"]:
#     os.environ.pop(key, None)


@dataclass
class StockQuote:
    symbol: str
    last_price: float
    prev_close: float
    change: float
    change_pct: float
    as_of: datetime
    is_market_open: bool


def _is_us_market_open(now_utc: Optional[datetime] = None) -> Tuple[bool, str]:
    """粗略按美东时间判断是否为盘中"""
    if now_utc is None:
        now_utc = datetime.utcnow()
    est = now_utc.astimezone(ZoneInfo("America/New_York"))
    if est.weekday() >= 5:
        return False, "休市（周末）"
    if (est.hour > 9 or (est.hour == 9 and est.minute >= 30)) and est.hour < 16:
        return True, "美股交易时段"
    return False, "盘后/盘前时段"


def _color_for_change(change: float) -> str:
    """美股习惯：涨为绿、跌为红"""
    if change > 0:
        return "#00C805"  # 更鲜艳的绿色
    if change < 0:
        return "#FF333A"  # 更鲜艳的红色
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
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # 统一转为北京时区再计算相对时间
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
    """使用 yfinance 获取最新价"""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("股票代码不能为空")

    ticker = yf.Ticker(symbol)
    
    # 稍微增加一点延时，避免被接口限流
    time.sleep(0.5) 
    
    # 优先尝试获取历史数据
    hist_2d = ticker.history(period="2d")
    last_close = None
    prev_close = None

    if isinstance(hist_2d, pd.DataFrame) and not hist_2d.empty:
        closes = hist_2d["Close"].dropna()
        if len(closes) >= 2:
            prev_close = float(closes.iloc[-2])
            last_close = float(closes.iloc[-1])
        elif len(closes) == 1:
            last_close = float(closes.iloc[-1])

    # 兜底逻辑
    if last_close is None:
        info = getattr(ticker, "fast_info", None) or getattr(ticker, "info", {})
        if info:
            val = info.get("lastPrice") or info.get("regularMarketPrice") or info.get("currentPrice")
            if val:
                last_close = float(val)

    if last_close is None:
        raise ValueError("无法获取有效报价")
        
    # 如果没取到前收盘，就设为当前价，避免报错
    if prev_close is None:
        prev_close = last_close

    last_price = float(last_close)
    change = last_price - prev_close
    change_pct = change / prev_close if prev_close != 0 else 0

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
    """抓取新闻并处理翻译逻辑"""
    symbol = symbol.strip().upper()
    if not symbol:
        return []

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=7)

    def _strip_html(raw: str) -> str:
        text = _html.unescape(raw or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    # 1. 尝试 yfinance 源
    items = []
    try:
        ticker = yf.Ticker(symbol)
        news_items = getattr(ticker, "news", None) or []
        for item in news_items:
            ts = item.get("providerPublishTime") or 0
            try:
                dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except:
                continue
            if dt_utc < cutoff: continue
            
            items.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "publisher": item.get("publisher", "Yahoo"),
                "time": dt_utc,
                "summary": _strip_html(item.get("summary", ""))
            })
    except Exception:
        items = []

    # 2. 尝试 Google RSS 兜底 (你之前的代码逻辑)
    if not items:
        try:
            import requests
            import xml.etree.ElementTree as ET
            from email.utils import parsedate_to_datetime
            
            # 必须带 User-Agent 否则容易被 Google 拦截
            headers = {'User-Agent': 'Mozilla/5.0'}
            q = f"{symbol} stock"
            url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
            
            # 使用 session 可能会自动复用代理配置
            resp = requests.get(url, headers=headers, timeout=5)
            
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    pub_date = item.findtext("pubDate")
                    try:
                        dt_utc = parsedate_to_datetime(pub_date)
                        if dt_utc.tzinfo is None: dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                    except:
                        continue
                    if dt_utc < cutoff: continue
                    
                    items.append({
                        "title": _html.unescape(item.findtext("title") or ""),
                        "link": item.findtext("link"),
                        "publisher": item.findtext("source") or "Google News",
                        "time": dt_utc,
                        "summary": _strip_html(item.findtext("description"))
                    })
        except Exception:
            pass

    if len(items) > limit:
        items = items[:limit]

    # 3. 翻译处理 (方案 2: 失败则静默降级)
    translator = None
    if GoogleTranslator:
        try:
            translator = GoogleTranslator(source="auto", target="zh-CN")
        except:
            translator = None

    def _translate(text: str) -> Tuple[str, bool]:
        if not text or not translator:
            return text, False
        try:
            # 缩短超时时间，避免卡顿
            return translator.translate(text), True
        except:
            return text, False

    for item in items:
        title_en = item.get("title", "")
        summary_en = item.get("summary", "")

        # 尝试翻译
        title_zh, ok_title = _translate(title_en)
        summary_zh, ok_sum = _translate(summary_en) if summary_en else ("", False)

        # ⚠️ 核心修改：如果翻译失败，直接使用英文原标题，不再添加“(翻译暂不可用)”后缀
        if not ok_title:
            title_zh = title_en 
        
        if not ok_sum:
            summary_zh = summary_en

        item["title_zh"] = title_zh
        item["summary_zh"] = summary_zh

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

with st.spinner("正在调取华尔街实时数据..."):
    try:
        quote = fetch_stock_quote(query)
    except Exception:
        st.error("未找到该股票，请检查代码或网络连接。")
        st.stop()

    try:
        news_list = fetch_news(quote.symbol, limit=8)
    except Exception:
        news_list = []

# 显示逻辑
is_open, session_label = _is_us_market_open()
price_label = "盘中价格" if is_open else "盘后/收盘价"

st.subheader("股价信息 (Stock Info)")
st.markdown(f"<div style='font-size: 22px; font-weight: 700;'>{quote.symbol}</div>", unsafe_allow_html=True)
st.markdown(f"<div style='font-size: 32px; font-weight: 700;'>{quote.last_price:.2f} USD</div>", unsafe_allow_html=True)
st.markdown(_format_change_html(quote.change, quote.change_pct), unsafe_allow_html=True)
st.caption(f"{price_label} · 前收盘：{quote.prev_close:.2f} USD · {session_label}")

st.divider()
st.subheader("深度资讯 (News Analytics)")

if not news_list:
    st.info("该股票近 7 天暂无重大公开资讯。")
else:
    for item in news_list:
        # 优先使用处理过的 title_zh (可能是中文，也可能是纯英文)
        title = item.get("title_zh") or item.get("title")
        publisher = item.get("publisher") or "Unknown"
        rel_time = _format_relative_time(item.get("time"))
        link = item.get("link")
        summary = item.get("summary_zh") or item.get("summary")

        # 简单的卡片布局
        with st.container(border=True):
            st.markdown(f"### {title}")
            if summary:
                st.write(summary)
            st.caption(f"{publisher} · {rel_time}")
            if link:
                st.link_button("查看原文", link)