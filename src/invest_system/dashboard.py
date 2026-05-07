from __future__ import annotations

from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st


def _workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _list_prefixes(data_dir: Path) -> list[str]:
    files = sorted(data_dir.glob("*_equity.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[str] = []
    for f in files:
        name = f.name
        if name.endswith("_equity.csv"):
            out.append(name[: -len("_equity.csv")])
    pref = "live_intraday"
    if pref in out:
        out.remove(pref)
        out.insert(0, pref)
    return out


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _calc_max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min()) if not dd.empty else 0.0


def _calc_sharpe_ratio(equity: pd.Series) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    returns = equity.pct_change().dropna()
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * (252 ** 0.5))


def _assistant_day_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: p.name, reverse=True)


def _get_current_positions(tx_df: pd.DataFrame) -> pd.DataFrame:
    if tx_df.empty:
        return pd.DataFrame()
    
    positions = {}
    avg_cost = {}
    
    for _, row in tx_df.iterrows():
        sym = row.get("symbol", "")
        name = row.get("name", "")
        if not sym:
            continue
        shares = float(row.get("shares", 0))
        price = float(row.get("price", 0))
        
        if row.get("side") == "buy":
            old_shares = positions.get(sym, 0)
            old_cost = avg_cost.get(sym, 0)
            new_shares = old_shares + shares
            new_cost = (old_cost * old_shares + price * shares) / new_shares if new_shares > 0 else 0
            positions[sym] = new_shares
            avg_cost[sym] = new_cost
        else:
            old_shares = positions.get(sym, 0)
            new_shares = old_shares - shares
            if new_shares <= 0:
                positions.pop(sym, None)
                avg_cost.pop(sym, None)
            else:
                positions[sym] = new_shares
    
    result = []
    for sym, shares in positions.items():
        if shares > 0:
            result.append({
                "股票代码": sym,
                "股票名称": next((row.get("name", "") for _, row in tx_df.iterrows() if row.get("symbol") == sym), ""),
                "持仓数量": int(shares),
                "成本价": round(avg_cost.get(sym, 0), 2)
            })
    
    if not result:
        return pd.DataFrame()
    
    return pd.DataFrame(result).sort_values("持仓数量", ascending=False)


def _get_daily_summary(eq_df: pd.DataFrame) -> pd.DataFrame:
    if eq_df.empty or "date" not in eq_df.columns:
        return pd.DataFrame()
    
    summary = []
    for date in eq_df["date"].unique():
        day_df = eq_df[eq_df["date"] == date]
        if day_df.empty:
            continue
        
        first_row = day_df.iloc[0]
        last_row = day_df.iloc[-1]
        
        start_equity = float(first_row.get("equity", 0))
        end_equity = float(last_row.get("equity", 0))
        daily_return = ((end_equity / start_equity) - 1) * 100 if start_equity > 0 else 0
        
        summary.append({
            "日期": date,
            "开盘权益": round(start_equity, 2),
            "收盘权益": round(end_equity, 2),
            "日收益率(%)": round(daily_return, 2),
            "记录数": len(day_df)
        })
    
    return pd.DataFrame(summary)


def _render_articles(day_dir: Path) -> None:
    phase_files = sorted(day_dir.glob("*_article.md"))
    if not phase_files:
        st.info("该日期没有看盘文章")
        return
    
    phase_names = {
        "pre_open": "🌅 盘前分析 (9:20)",
        "open_5m": "⏰ 开盘5分钟 (9:35)",
        "midday": "☀️ 午间复盘 (11:30)",
        "close": "🌙 收盘总结 (15:05)"
    }
    
    for fp in phase_files:
        phase_key = fp.stem.replace("_article", "")
        phase_name = phase_names.get(phase_key, phase_key)
        with st.expander(phase_name, expanded=False):
            st.markdown(fp.read_text(encoding="utf-8"))


def main() -> None:
    st.set_page_config(
        page_title="投资模拟看板",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    root = _workspace_root()
    data_dir = root / "data"
    article_root = root / "artifacts" / "assistant"

    st.sidebar.title("📋 控制面板")
    
    prefixes = _list_prefixes(data_dir)
    if not prefixes:
        st.warning("未找到任何回测结果。先运行一次 invest-sim。")
        return

    prefix = st.sidebar.selectbox("选择数据源", options=prefixes, index=0)
    auto_refresh = st.sidebar.checkbox("🔄 自动刷新(30秒)", value=False)
    
    if auto_refresh:
        st.autorefresh(interval=30_000, key="autorefresh")

    eq_path = data_dir / f"{prefix}_equity.csv"
    tx_path = data_dir / f"{prefix}_transactions.csv"

    eq_df = _safe_read_csv(eq_path)
    tx_df = _safe_read_csv(tx_path)

    if eq_df.empty:
        st.error(f"未读取到权益数据")
        return

    for col in ("equity", "cash", "return_pct"):
        if col in eq_df.columns:
            eq_df[col] = pd.to_numeric(eq_df[col], errors="coerce")

    latest_equity = float(eq_df["equity"].dropna().iloc[-1]) if "equity" in eq_df.columns else 0.0
    latest_return = float(eq_df["return_pct"].dropna().iloc[-1]) if "return_pct" in eq_df.columns else 0.0
    max_dd = _calc_max_drawdown(eq_df["equity"].dropna()) if "equity" in eq_df.columns else 0.0
    sharpe = _calc_sharpe_ratio(eq_df["equity"].dropna()) if "equity" in eq_df.columns else 0.0

    st.title("📈 投资模拟看板")
    st.caption(f"数据源: {prefix} | 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    st.markdown("---")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("💰 总权益", f"{latest_equity:,.2f}")
    with col2:
        delta_color = "normal" if latest_return >= 0 else "inverse"
        st.metric("📊 累计收益率", f"{latest_return:.2f}%")
    with col3:
        st.metric("📉 最大回撤", f"{max_dd * 100:.2f}%")
    with col4:
        st.metric("📐 夏普比率", f"{sharpe:.2f}")
    with col5:
        st.metric("🔢 交易笔数", f"{len(tx_df)}")

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 权益曲线",
        "💼 当前持仓",
        "📝 交易记录",
        "📅 每日汇总",
        "📰 看盘文章"
    ])

    with tab1:
        st.subheader("权益变化趋势")
        
        if "equity" in eq_df.columns:
            plot_df = eq_df.copy()
            if "datetime" in plot_df.columns:
                plot_df["时间"] = pd.to_datetime(plot_df["datetime"], errors="coerce")
            elif "date" in plot_df.columns:
                plot_df["时间"] = pd.to_datetime(plot_df["date"], errors="coerce")
            else:
                plot_df["时间"] = pd.RangeIndex(len(plot_df))
            
            st.line_chart(plot_df.set_index("时间")["equity"])
        
        with st.expander("📋 查看详细数据", expanded=False):
            display_df = eq_df.copy()
            if "datetime" in display_df.columns:
                display_df["时间"] = display_df["datetime"]
            if "date" in display_df.columns:
                display_df["日期"] = display_df["date"]
            if "phase" in display_df.columns:
                display_df["阶段"] = display_df["phase"]
            if "equity" in display_df.columns:
                display_df["权益"] = display_df["equity"]
            if "cash" in display_df.columns:
                display_df["现金"] = display_df["cash"]
            if "return_pct" in display_df.columns:
                display_df["收益率(%)"] = display_df["return_pct"]
            
            cols_to_show = [col for col in ["时间", "日期", "阶段", "权益", "现金", "收益率(%)"] if col in display_df.columns]
            st.dataframe(display_df[cols_to_show], width="stretch")

    with tab2:
        st.subheader("当前持仓明细")
        
        positions_df = _get_current_positions(tx_df)
        
        if positions_df.empty:
            st.info("当前无持仓")
        else:
            st.dataframe(positions_df, width="stretch")
            
            total_positions = positions_df["持仓数量"].sum()
            st.markdown(f"**持仓股票数**: {len(positions_df)} | **总持仓股数**: {total_positions}")

    with tab3:
        st.subheader("交易历史")
        
        if tx_df.empty:
            st.info("暂无交易记录")
        else:
            display_tx = tx_df.copy()
            if "date" in display_tx.columns:
                display_tx["日期"] = display_tx["date"]
            if "symbol" in display_tx.columns:
                display_tx["股票代码"] = display_tx["symbol"]
            if "name" in display_tx.columns:
                display_tx["股票名称"] = display_tx["name"]
            if "side" in display_tx.columns:
                display_tx["交易类型"] = display_tx["side"].apply(lambda x: "🟢 买入" if x == "buy" else "🔴 卖出")
            if "shares" in display_tx.columns:
                display_tx["股数"] = display_tx["shares"]
            if "price" in display_tx.columns:
                display_tx["价格"] = display_tx["price"]
            if "fee" in display_tx.columns:
                display_tx["手续费"] = display_tx["fee"]
            if "cash_after" in display_tx.columns:
                display_tx["交易后现金"] = display_tx["cash_after"]
            
            cols_to_show = [col for col in ["日期", "股票代码", "股票名称", "交易类型", "股数", "价格", "手续费", "交易后现金"] if col in display_tx.columns]
            
            if "日期" in display_tx.columns:
                display_tx = display_tx.sort_values("日期", ascending=False)
            
            st.dataframe(display_tx[cols_to_show], width="stretch")
            
            buy_count = len(tx_df[tx_df["side"] == "buy"]) if "side" in tx_df.columns else 0
            sell_count = len(tx_df[tx_df["side"] == "sell"]) if "side" in tx_df.columns else 0
            total_fee = tx_df["fee"].sum() if "fee" in tx_df.columns else 0
            st.markdown(f"**买入**: {buy_count} 笔 | **卖出**: {sell_count} 笔 | **总手续费**: {total_fee:.2f}")

    with tab4:
        st.subheader("每日收益汇总")
        
        daily_summary = _get_daily_summary(eq_df)
        
        if daily_summary.empty:
            st.info("暂无每日汇总数据")
        else:
            st.dataframe(daily_summary, width="stretch")
            
            avg_return = daily_summary["日收益率(%)"].mean()
            positive_days = len(daily_summary[daily_summary["日收益率(%)"] > 0])
            negative_days = len(daily_summary[daily_summary["日收益率(%)"] < 0])
            
            st.markdown(
                f"**平均日收益**: {avg_return:.2f}% | "
                f"**盈利天数**: {positive_days} | "
                f"**亏损天数**: {negative_days}"
            )

    with tab5:
        st.subheader("看盘助手文章")
        
        dirs = _assistant_day_dirs(article_root)
        if not dirs:
            st.info("暂无看盘文章")
        else:
            selected_day = st.selectbox(
                "选择日期",
                options=[d.name for d in dirs],
                index=0,
                key="article_day_selector"
            )
            
            selected_dir = article_root / selected_day
            
            st.markdown(f"**日期**: {selected_day}")
            _render_articles(selected_dir)


if __name__ == "__main__":
    main()
