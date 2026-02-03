# My Stock Tool (美股实时行情与 AI 资讯)

这是一个使用 Streamlit 开发的简单美股查询工具，支持查看实时股价和相关新闻资讯。

## 功能特点
- 实时获取美股最新价、涨跌幅。
- 自动判断美股交易时段（盘中、盘前、盘后）。
- 抓取最近 7 天的相关新闻，并支持自动翻译为中文。

## 本地运行
1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 启动应用：
   ```bash
   streamlit run app.py
   ```

## 部署
本项目已适配 Vercel 部署。
