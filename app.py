# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import os

# ---------- 設定 ----------
DATA_DIR = "data"
STUDENT_FILE = os.path.join(DATA_DIR, "students.csv")
LESSON_FILE = os.path.join(DATA_DIR, "lessons.csv")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title="家教時間管理", page_icon="📚", layout="wide")

# ---------- 資料讀取 ----------
def load_csv(path, columns):
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame(columns=columns)

def save_csv(df, path):
    df.to_csv(path, index=False)

students = load_csv(STUDENT_FILE, ["id", "name", "subject", "hourly_rate", "note"])
lessons = load_csv(LESSON_FILE, ["id", "student_id", "date", "start", "end", "type", "status", "note"])

# ---------- 側邊欄 ----------
st.sidebar.title("📚 家教管理系統")
menu = st.sidebar.radio("功能選單", ["🏠 首頁總覽", "👤 學生管理", "📅 課程排程", "🔄 補課管理", "📊 時數統計"])

# ---------- 首頁 ----------
if menu == "🏠 首頁總覽":
    st.title("🏠 首頁總覽")
    today = date.today()
    upcoming = lessons.copy()
    if not upcoming.empty:
        upcoming["date"] = pd.to_datetime(upcoming["date"]).dt.date
        upcoming = upcoming[upcoming["date"] >= today].sort_values(["date", "start"])

    col1, col2, col3 = st.columns(3)
    col1.metric("學生總數", len(students))
    col2.metric("本週課程", len(upcoming[upcoming["date"] <= today + timedelta(days=7)]) if not upcoming.empty else 0)
    col3.metric("待補課", len(lessons[lessons["status"] == "待補課"]) if not lessons.empty else 0)

    st.subheader("📌 即將到來的課程")
    if upcoming.empty:
        st.info("目前沒有安排課程")
    else:
        show = upcoming.head(10).copy()
        show["學生"] = show["student_id"].map(students.set_index("id")["name"] if not students.empty else {})
        st.dataframe(show[["date", "start", "end", "學生", "type", "status"]], use_container_width=True)

# ---------- 學生管理 ----------
elif menu == "👤 學生管理":
    st.title("👤 學生管理")

    with st.expander("➕ 新增學生", expanded=False):
        with st.form("add_student"):
            name = st.text_input("姓名")
            subject = st.text_input("科目")
            rate = st.number_input("時薪", min_value=0, step=50)
            note = st.text_area("備註")
            if st.form_submit_button("新增"):
                new_id = int(students["id"].max() + 1) if not students.empty else 1
                students.loc[len(students)] = [new_id, name, subject, rate, note]
                save_csv(students, STUDENT_FILE)
                st.success(f"已新增學生 {name}")
                st.rerun()

    st.subheader("學生列表")
    if students.empty:
        st.info("尚無學生資料")
    else:
        st.dataframe(students, use_container_width=True)
        del_id = st.selectbox("刪除學生 ID", students["id"])
        if st.button("🗑️ 刪除"):
            students = students[students["id"] != del_id]
            save_csv(students, STUDENT_FILE)
            st.rerun()

# ---------- 課程排程 ----------
elif menu == "📅 課程排程":
    st.title("📅 課程排程")

    if students.empty:
        st.warning("請先新增學生")
    else:
        with st.form("add_lesson"):
            student = st.selectbox("學生", students["name"])
            sid = int(students[students["name"] == student]["id"].values[0])
            d = st.date_input("日期", value=date.today())
            c1, c2 = st.columns(2)
            start = c1.time_input("開始時間", value=datetime.strptime("19:00", "%H:%M").time())
            end = c2.time_input("結束時間", value=datetime.strptime("20:00", "%H:%M").time())
            ltype = st.selectbox("類型", ["正課", "補課", "試聽"])
            note = st.text_input("備註")
            if st.form_submit_button("新增課程"):
                new_id = int(lessons["id"].max() + 1) if not lessons.empty else 1
                lessons.loc[len(lessons)] = [new_id, sid, d, start.strftime("%H:%M"), end.strftime("%H:%M"), ltype, "已排定", note]
                save_csv(lessons, LESSON_FILE)
                st.success("課程已新增")
                st.rerun()

        st.subheader("所有課程")
        if lessons.empty:
            st.info("尚無課程")
        else:
            view = lessons.copy()
            view["學生"] = view["student_id"].map(students.set_index("id")["name"])
            st.dataframe(view[["id", "date", "start", "end", "學生", "type", "status", "note"]], use_container_width=True)

# ---------- 補課管理 ----------
elif menu == "🔄 補課管理":
    st.title("🔄 補課管理")

    st.subheader("標記請假課程")
    if lessons.empty:
        st.info("尚無課程可操作")
    else:
        pending = lessons[lessons["status"] == "已排定"]
        if not pending.empty:
            lid = st.selectbox("選擇要請假的課程", pending["id"])
            if st.button("標記為待補課"):
                lessons.loc[lessons["id"] == lid, "status"] = "待補課"
                save_csv(lessons, LESSON_FILE)
                st.success("已標記，請安排補課時間")
                st.rerun()

    st.subheader("待補課清單")
    todo = lessons[lessons["status"] == "待補課"]
    if todo.empty:
        st.success("沒有待補課項目 🎉")
    else:
        todo_view = todo.copy()
        todo_view["學生"] = todo_view["student_id"].map(students.set_index("id")["name"])
        st.dataframe(todo_view[["id", "date", "start", "end", "學生", "note"]], use_container_width=True)

        st.markdown("### 安排補課")
        with st.form("makeup"):
            lid = st.selectbox("補課課程 ID", todo["id"])
            new_date = st.date_input("補課日期")
            c1, c2 = st.columns(2)
            ns = c1.time_input("開始", value=datetime.strptime("19:00", "%H:%M").time())
            ne = c2.time_input("結束", value=datetime.strptime("20:00", "%H:%M").time())
            if st.form_submit_button("建立補課"):
                sid = int(lessons[lessons["id"] == lid]["student_id"].values[0])
                new_id = int(lessons["id"].max() + 1)
                lessons.loc[len(lessons)] = [new_id, sid, new_date, ns.strftime("%H:%M"), ne.strftime("%H:%M"), "補課", "已排定", f"補原課程 {lid}"]
                lessons.loc[lessons["id"] == lid, "status"] = "已補課"
                save_csv(lessons, LESSON_FILE)
                st.success("補課已建立")
                st.rerun()

# ---------- 時數統計 ----------
elif menu == "📊 時數統計":
    st.title("📊 時數統計")

    if lessons.empty or students.empty:
        st.info("資料不足")
    else:
        df = lessons.copy()
        df["date"] = pd.to_datetime(df["date"])
        df["start_dt"] = pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + df["start"])
        df["end_dt"] = pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + df["end"])
        df["hours"] = (df["end_dt"] - df["start_dt"]).dt.total_seconds() / 3600
        df["學生"] = df["student_id"].map(students.set_index("id")["name"])

        months = sorted(df["date"].dt.strftime("%Y-%m").unique(), reverse=True)
        month = st.selectbox("選擇月份", ["全部"] + list(months))
        if month != "全部":
            df = df[df["date"].dt.strftime("%Y-%m") == month]

        summary = df.groupby("學生")["hours"].sum().reset_index()
        summary.columns = ["學生", "總時數"]

        rate_map = students.set_index("name")["hourly_rate"].to_dict()
        summary["預估收入"] = summary.apply(lambda r: r["總時數"] * rate_map.get(r["學生"], 0), axis=1)
        st.metric("總收入", f"NT$ {int(summary['預估收入'].sum()):,}")
        st.dataframe(summary, use_container_width=True)

        import plotly.express as px
        fig = px.bar(summary, x="學生", y="總時數", title="各學生上課時數")
        st.plotly_chart(fig, use_container_width=True)