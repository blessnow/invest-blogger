from __future__ import annotations

from pathlib import Path

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


def _assistant_day_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: p.name, reverse=True)


def _render_articles(day_dir: Path) -> None:
    st.subheader(f"看盘文章: {day_dir.name}")
    bundle = day_dir / "day_bundle.md"
    if bundle.is_file():
        st.markdown(bundle.read_text(encoding="utf-8"))
        return
    phase_files = sorted(day_dir.glob("*_article.md"))
    if not phase_files:
        st.info("该日期目录没有文章文件。")
        return
    for fp in phase_files:
        st.markdown(f"### {fp.name}")
        st.markdown(fp.read_text(encoding="utf-8"))


def main() -> None:
    st.set_page_config(page_title="Invest Dashboard", layout="wide")
    root = _workspace_root()
    data_dir = root / "data"
    article_root = root / "artifacts" / "assistant"

    st.title("投资模拟看板")
    st.caption("查看收益、交易明细、看盘文章")

    prefixes = _list_prefixes(data_dir)
    if not prefixes:
        st.warning("未找到任何回测结果。先运行一次 invest-sim。")
        return

    c1, c2 = st.columns([3, 2])
    with c1:
        prefix = st.selectbox("选择结果前缀", options=prefixes, index=0)
    with c2:
        auto_refresh = st.checkbox("自动刷新(30秒)", value=False)

    if auto_refresh:
        st.autorefresh(interval=30_000, key="autorefresh")

    eq_path = data_dir / f"{prefix}_equity.csv"
    tx_path = data_dir / f"{prefix}_transactions.csv"
    png_path = data_dir / f"{prefix}_equity_curve.png"

    eq_df = _safe_read_csv(eq_path)
    tx_df = _safe_read_csv(tx_path)

    if eq_df.empty:
        st.error(f"未读取到权益数据: {eq_path}")
        return

    for col in ("equity", "cash", "return_pct"):
        if col in eq_df.columns:
            eq_df[col] = pd.to_numeric(eq_df[col], errors="coerce")

    latest_equity = float(eq_df["equity"].dropna().iloc[-1]) if "equity" in eq_df.columns else 0.0
    latest_return = float(eq_df["return_pct"].dropna().iloc[-1]) if "return_pct" in eq_df.columns else 0.0
    max_dd = _calc_max_drawdown(eq_df["equity"].dropna()) if "equity" in eq_df.columns else 0.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("当前权益", f"{latest_equity:,.2f}")
    k2.metric("累计收益率", f"{latest_return:.2f}%")
    k3.metric("最大回撤", f"{max_dd * 100:.2f}%")
    k4.metric("交易笔数", f"{len(tx_df)}")

    tab1, tab2, tab3, tab4 = st.tabs(["收益", "交易", "文章", "文件"])

    with tab1:
        st.subheader("权益曲线")
        if "equity" in eq_df.columns:
            plot_df = eq_df.copy()
            if "datetime" in plot_df.columns:
                plot_df["_x"] = pd.to_datetime(plot_df["datetime"], errors="coerce")
            elif "date" in plot_df.columns:
                plot_df["_x"] = pd.to_datetime(plot_df["date"], errors="coerce")
            else:
                plot_df["_x"] = pd.RangeIndex(len(plot_df))
            st.line_chart(plot_df.set_index("_x")["equity"])
        if png_path.is_file():
            st.image(str(png_path), caption=png_path.name)
        st.dataframe(eq_df, use_container_width=True)

    with tab2:
        st.subheader("交易明细")
        if tx_df.empty:
            st.info("暂无交易。")
        else:
            st.dataframe(tx_df, use_container_width=True)

    with tab3:
        dirs = _assistant_day_dirs(article_root)
        if not dirs:
            st.info("未找到看盘文章目录。")
        else:
            day = st.selectbox("选择文章日期", options=[d.name for d in dirs], index=0)
            _render_articles(article_root / day)

    with tab4:
        st.code(str(eq_path))
        st.code(str(tx_path))
        if png_path.is_file():
            st.code(str(png_path))


if __name__ == "__main__":
    main()
