"""Streamlit 看板登录闸（不依赖第三方包）。

配置（环境变量）：
- ``DASHBOARD_AUTH_ENABLED``：默认 false（本机），上线请置 true。
- ``DASHBOARD_USERS``：``user1:pass1,user2:pass2`` 多账号；或单账号 ``user:pass``。
"""

from __future__ import annotations

import hmac

import streamlit as st

from invest_system.config import Settings


def _parse_users(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        u, p = part.split(":", 1)
        out.append((u.strip(), p.strip()))
    return out


def _check(settings: Settings, user: str, pw: str) -> bool:
    pairs = _parse_users(settings.dashboard_users)
    if not pairs:
        return False
    for u, p in pairs:
        if hmac.compare_digest(u, user) and hmac.compare_digest(p, pw):
            return True
    return False


def require_login(settings: Settings) -> None:
    """放在 dashboard.main() 顶部；未通过则 st.stop()。"""
    if not settings.dashboard_auth_enabled:
        return

    if not _parse_users(settings.dashboard_users):
        st.error(
            "DASHBOARD_AUTH_ENABLED=true 但未配置 DASHBOARD_USERS（格式：user:pass[,user:pass]）。"
        )
        st.stop()

    if st.session_state.get("auth_user"):
        st.sidebar.success(f"👤 {st.session_state['auth_user']}")
        if st.sidebar.button("🚪 退出登录", key="_logout_btn"):
            st.session_state.pop("auth_user", None)
            st.rerun()
        return

    st.title("🔒 投资模拟看板 · 登录")
    with st.form("login_form"):
        u = st.text_input("用户名", key="_login_user")
        p = st.text_input("密码", type="password", key="_login_pw")
        ok = st.form_submit_button("登录")
    if ok:
        if _check(settings, u, p):
            st.session_state["auth_user"] = u
            st.rerun()
        else:
            st.error("用户名或密码错误")
    st.stop()
